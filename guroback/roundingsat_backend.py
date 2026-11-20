"""Backend de RoundingBack (RoundingSat modificado) para la extracción de backbones.

RoundingBack es un ejecutable C++ (``roundingsat/``) que calcula backbones con
*assumptions* sobre su propio solver de pseudo-booleanos, sin reiniciar la
búsqueda ni la base de restricciones aprendidas en cada sondeo. Para que
participe en ParaBack con el mismo contrato que Gurobi y SCIP, el ejecutable
incorpora un **modo servidor** (``--backbone-server=1``, ver
``roundingsat/src/BackboneServer.hpp``) y este módulo lo envuelve en un
:class:`guroback.backend.SolverBackend`:

* La instancia se lee una sola vez por proceso. En problemas de optimización
  el óptimo se calcula con una primera ejecución normal del solver (la misma
  que hace ``extract_backbone.sh``) y después se arranca el servidor con
  ``--already-solved=1 --optimum-value=z*``, cuya base de restricciones sólo
  contiene la fórmula original y las dos cotas ``objetivo == z*``. Es la única
  "recreación" del modelo y ocurre una vez, no por sondeo.
* ``exclude_value`` se materializa como una *assumption* (``assume -lit``) y
  ``require_any_flip`` como la restricción ``sum ¬l_i + aux >= 1`` con la
  assumption ``¬aux``; ambas viven sólo durante el siguiente ``solve`` y el
  servidor las neutraliza después (``aux := 1``), de modo que todo lo aprendido
  sigue siendo consecuencia de la fórmula original.
* ``fix_variable`` es una unidad permanente (``fix lit``).
* ``maximize_flips`` (``chunks=maxflip``, ``supports_max_flips``) es la orden ``maxflip``:
  el servidor hace una búsqueda lineal SAT-UNSAT sobre "cuántos candidatos cambian
  respecto de x*" (la cota ``sum ¬l_i + (k+1)·aux >= k+1`` se reifica y neutraliza como
  las de ``flip``) y emite cada solución al momento; el backend la entrega al oyente de
  incumbentes y ``r optimum k`` cierra la llamada (``k = 0``: nada puede cambiar).
* ``interrupt`` envía ``SIGINT`` al proceso: RoundingSat aborta el ``solve`` en
  curso y responde ``unknown`` sin perder el estado.
* ``add_live_fixes`` escribe las unidades en un *pipe* heredado por el servidor
  (orden ``live fd``); RoundingSat lo lee en cada reinicio **dentro** del
  ``solve`` en curso, añade las unidades en el nivel 0 y sigue buscando con
  todo lo aprendido. El canal de órdenes (stdin) sólo se lee entre ``solve``.

Los ``VarId`` son las posiciones (0..n-1) de los índices ``x<k>`` que aparecen
en el archivo, en orden creciente de ``k``; ``variable_name`` devuelve ``x<k>``.
"""
from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import time
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .backend import SolveResult, SolveStatus, SolverCrashed, VarId
from .line_server import LineServer, LineServerError, find_executable, is_crash_exit
from .opb import HEADER_PREFIX, plain_copy, scan, scan_opb  # noqa: F401 - scan_opb se reexporta
from .pb_output import OPTIMUM_FOUND
from .server_backend import LineServerBackend

#: Variable de entorno con la ruta del ejecutable; si no está, se busca en
#: ``<repo>/roundingsat/build/roundingsat`` y después en ``PATH``.
ROUNDINGSAT_BIN_ENV = "ROUNDINGSAT_BIN"

#: Opciones nativas aplicadas antes de las del usuario (las de ``extract_backbone.sh``).
DEFAULT_OPTIONS: Mapping[str, str] = {
    "cg-encoding": "reified",
    "cg-resprop": "1",
    "lp-cut-gomory": "0",
    "verbosity": "0",
}

UNSUPPORTED_ALIASES: Mapping[str, str] = {
    "MIPGap": "RoundingSat demuestra optimalidad exacta; no hay gap",
    "MIPFocus": "sin equivalente; el extractor ya trabaja en modo factibilidad",
    "Seed": "RoundingSat no expone una semilla; use opciones nativas como --luby-mult o --vsids-var",
    "NodeLimit": "sin equivalente; use TimeLimit",
    "FeasibilityTol": "RoundingSat es exacto (aritmética entera); no hay tolerancias",
    "IntFeasTol": "RoundingSat es exacto (aritmética entera); no hay tolerancias",
    "OptimalityTol": "RoundingSat es exacto (aritmética entera); no hay tolerancias",
}


def find_binary() -> Optional[str]:
    """Ruta del ejecutable de RoundingSat, o ``None`` si no se encuentra."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    return find_executable(ROUNDINGSAT_BIN_ENV, [str(repo / "roundingsat" / "build" / "roundingsat")], ["roundingsat"])


class RoundingSatError(LineServerError):
    """El proceso de RoundingSat falló o respondió algo inesperado."""


class RoundingSatCrashed(RoundingSatError, SolverCrashed):
    """RoundingSat murió (señal, ``OutOfMemoryException``): el trabajador se retira (ver :class:`SolverCrashed`)."""


class RoundingSatBackend(LineServerBackend):
    """Backend basado en el ejecutable de RoundingBack en modo servidor."""

    name = "roundingsat"
    #: RoundingSat lee los coeficientes como enteros y razona sin tolerancias.
    exact_arithmetic = True
    #: Orden ``maxflip`` del servidor (``chunks=maxflip[:T]``).
    supports_max_flips = True
    UNSUPPORTED_ALIASES = UNSUPPORTED_ALIASES
    LABEL = "RoundingSat"

    @classmethod
    def is_available(cls) -> Tuple[bool, str]:
        binary = find_binary()
        if binary is None:
            return False, (
                f"ejecutable de RoundingSat no encontrado (compile roundingsat/ con `make roundingsat` "
                f"o indique {ROUNDINGSAT_BIN_ENV})"
            )
        try:
            out = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"no se pudo ejecutar {binary}: {exc}"
        if "backbone-server" not in out.stdout:
            return False, f"{binary} no incluye el modo servidor (--backbone-server); recompile roundingsat/"
        return True, ""

    def __init__(self, instance_path: str, params: Optional[Mapping[str, str]] = None) -> None:
        super().__init__(instance_path, params)
        self._temp_instance: Optional[str] = None
        self._options: Dict[str, str] = dict(DEFAULT_OPTIONS)
        self._var_by_index: Optional[Dict[int, VarId]] = None
        binary = find_binary()
        if binary is None:
            raise RoundingSatError("ejecutable de RoundingSat no encontrado")
        self._binary = binary
        scanned = scan(instance_path)
        self._has_objective, self._indices = scanned.has_objective, scanned.indices
        self._solver_path = instance_path
        if not scanned.header_ok or instance_path.endswith((".gz", ".xz")):
            num_vars = self._indices[-1] if self._indices else 0
            # El número de restricciones son las sentencias ";" (no las líneas: una restricción
            # puede ocupar varias, o una línea tener varias).
            header = f"{HEADER_PREFIX}{num_vars} #constraint= {scanned.num_constraints}"
            self._temp_instance = plain_copy(instance_path, prefix="guroback_rs_", header=header)
            self._solver_path = self._temp_instance
        for name, value in self.params.items():
            self.set_parameter(name, value)

    # ------------------------------------------------------------------ LineServerBackend
    def _literal(self, var: VarId, value: bool) -> int:
        index = self._indices[var]
        return index if value else -index

    def _base_argv(self, without: Sequence[str] = ()) -> List[str]:
        argv = [self._binary, self._solver_path]
        argv.extend(f"--{name}={value}" for name, value in self._options.items() if name not in without)
        return argv

    def _spawn_server(self) -> LineServer:
        argv = self._base_argv() + ["--backbone-server=1"]
        if self._has_objective:
            assert self._optimum is not None
            argv += ["--already-solved=1", f"--optimum-value={self._optimum}"]
        return LineServer(argv, label="RoundingSat", error=RoundingSatError, crashed=RoundingSatCrashed, memory_limit_mb=self._memory_limit_mb)

    def _server_ready(self) -> bool:
        return True

    def _server_error(self, message: str) -> Exception:
        return RoundingSatError(message)

    def _kill_subproc(self, proc: "subprocess.Popen[str]") -> None:
        proc.send_signal(signal.SIGINT)

    def _decode_solution(self, model: Mapping[int, bool]) -> Dict[VarId, bool]:
        return {var: model.get(index, False) for var, index in enumerate(self._indices)}

    def _var_of(self, literal: int) -> Optional[VarId]:
        if self._var_by_index is None:
            self._var_by_index = {index: var for var, index in enumerate(self._indices)}
        return self._var_by_index.get(abs(literal))

    def _on_new_optimum(self) -> None:
        # El servidor se arranca ya con --optimum-value=z*: es la única "recreación" del
        # modelo, una vez y no por sondeo.
        self._drop_server()
        self._start_server()
        server = self._server
        if self._pguide and self._solution and server is not None:
            # RB-PG: x* salió de otro proceso (el optimizador); es la primera solución
            # que cuenta en los potenciales, como en el extractor secuencial.
            server.command("phase " + " ".join(str(self._literal(v, b)) for v, b in sorted(self._solution.items())))

    def _set_output(self, enabled: bool) -> None:
        self._options["verbosity"] = "1" if enabled else "0"

    def _set_native_parameter(self, name: str, value: str) -> None:
        if "-" not in name and not name.islower():
            raise ValueError(f"parámetro desconocido {name!r}; las opciones nativas de RoundingSat son minúsculas (p. ej. opt-mode)")
        if self._server is not None:
            raise RuntimeError("las opciones nativas de RoundingSat sólo pueden fijarse antes de arrancar el solver")
        # Opción nativa: se valida cuando arranca el proceso (RoundingSat rechaza nombres desconocidos).
        self._options[name] = str(value)

    def _cleanup_files(self) -> None:
        if self._temp_instance is not None:
            try:
                os.unlink(self._temp_instance)
            except OSError:
                pass
            self._temp_instance = None

    # ------------------------------------------------------------------ introspección
    def binary_variables(self) -> Sequence[VarId]:
        return range(len(self._indices))

    def variable_name(self, var: VarId) -> str:
        return f"x{self._indices[var]}"

    # ------------------------------------------------------------------ optimización
    def _run_optimizer(self) -> SolveResult:
        """Primera resolución de un problema de optimización: proceso independiente.

        Sin ``--pguide``: RB-PG es una heurística de los sondeos, y en RoundingBack x* sale de un
        RoundingSat normal. Con ella, el optimizador anotaba cada solución mejorada en los
        potenciales y decidía en su contra, lejos de las buenas: bnn_mnist_back_image_73 (norm_1),
        óptimo en 90 s sin ella y ninguno en 300 s con ella (local, 2026-11-04; en mirador, ninguno
        en las dos pruebas). Las rondas ev1–ev5 no lo notaron: partían de la x* de ``--solutions``.
        """
        argv = self._base_argv(without=("pguide",)) + ["--print-sol=1"]
        if self._time_limit is not None:
            argv.append(f"--timeout={self._time_limit}")
        output, parsed, interrupted, returncode = self._external_optimizer(argv)
        self._solution = None
        if parsed.errors:
            raise RoundingSatError(f"RoundingSat: {parsed.errors[0]}")
        if interrupted and parsed.status is None:
            # SIGINT antes de que RoundingSat instale su manejador (p. ej. durante la
            # lectura de la instancia): sale con código 1 sin línea ``s``. Es una
            # interrupción pedida, no un fallo del solver.
            return SolveResult(SolveStatus.INTERRUPTED, None, raw_status="INTERRUPTED")
        # Muerto por una señal, aunque antes llegara a escribir su respuesta: lo que dijo no vale.
        # Con --cg-resprop=1 RoundingSat imprime "o z*" y "s OPTIMUM FOUND" y muere al deshacer la
        # búsqueda core-guided (Solver::dropExternal) con la línea "v" aún en el búfer de printf
        # (aim-200-3_4-yes1-2 y par8-2-c en la Ronda 1 del histórico, 2026-11-09): antes era un
        # RoundingSatError fatal ("omite N variables") y, con "s UNSATISFIABLE", se tomaba por
        # una infactibilidad. Ahora el trabajador se retira y los demás buscan la base.
        if returncode not in (0, None) and (parsed.status is None or is_crash_exit(returncode, output)):
            error = RoundingSatCrashed if is_crash_exit(returncode, output) else RoundingSatError
            raise error(f"RoundingSat terminó con código {returncode}:\n{output[-2000:]}")
        if parsed.status == OPTIMUM_FOUND and parsed.objective is not None:
            # Una línea ``v`` ausente o recortada no debe convertirse en ceros implícitos:
            # el extractor tomaría una asignación falsa como solución óptima base.
            missing = [k for k in self._indices if f"x{k}" not in parsed.values]
            if missing:
                raise RoundingSatError(
                    f"RoundingSat declaró óptimo pero su solución omite {len(missing)} variables (p. ej. x{missing[0]})"
                )
            self._solution = {var: parsed.values[f"x{k}"] for var, k in enumerate(self._indices)}
            return SolveResult(SolveStatus.OPTIMAL, float(parsed.objective), raw_status=parsed.status)
        return self._non_optimal(parsed, interrupted, output)

    # ------------------------------------------------------------------ RB-PG / RB-WP
    @property
    def _pguide(self) -> bool:
        """``pguide=1``: la fase de decisión va contra la polaridad mayoritaria (RB-PG, ``--pguide``)."""
        return str(self._options.get("pguide", "0")).strip().lower() in ("1", "true")

    def propagation_scores(self) -> Optional[Dict[VarId, float]]:
        """RB-WP: puntúa la actividad de propagación durante una resolución sin sondeo.

        Es la "fase de la solución inicial" del artículo con el óptimo ya conocido
        (así se evaluó RB-WP): un ``solve`` con ``objetivo = z*`` y sin
        assumptions, con la puntuación activa. Se hace aparte de la base porque
        ésta viene de otro proceso (el optimizador) o es la verificación de un x*
        ajeno (pura propagación, sin información). ``None`` si se interrumpe o no
        termina en el límite vigente.
        """
        server = self._ensure_server()
        assert server is not None
        start = time.perf_counter()
        self._mark_solving()
        try:
            with server.io_lock:
                server.command("wp start")
                answer = server.command("solve")
                if answer == "sat":
                    server.read_solution()  # no sustituye a la solución base, que el extractor ya leyó
                server.command("wp stop")
        finally:
            self._unmark_solving()
        self._accumulate_stats(server)
        self._stats["solving_time"] += time.perf_counter() - start
        if answer != "sat":
            return None
        values = [float(x) for x in server.query("wp get", "w").split()]
        return {var: values[index - 1] if index - 1 < len(values) else 0.0 for var, index in enumerate(self._indices)}


__all__ = [
    "DEFAULT_OPTIONS",
    "ROUNDINGSAT_BIN_ENV",
    "RoundingSatBackend",
    "RoundingSatError",
    "UNSUPPORTED_ALIASES",
    "find_binary",
    "scan_opb",
]

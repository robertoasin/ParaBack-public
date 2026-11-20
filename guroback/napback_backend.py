"""Backend de NapBack: backbones sobre la CNF de Naps con un servidor CaDiCaL.

``docs/PLAN_NAPBACK.md`` §2–§4. NapBack traduce la instancia PB a CNF con Naps
**una vez** (tras conocer ``z*``) y resuelve los sondeos con
``napback-server`` (``napback_server/napback_server.cpp``), que habla el mismo
protocolo de líneas que el modo servidor de RoundingBack
(:mod:`guroback.line_server`):

* **Base propia** (optimización sin ``z*``): Naps como optimizador PB. Su
  solución se valida con aritmética entera (:class:`guroback.exact.ExactChecker`)
  antes de darla por óptima. En ParaBack lo normal es recibir ``z*`` y ``x*``
  de otro trabajador (``BASE_HINT``).
* **Traducción** (primer ``solve`` tras ``fix_objective_value``, o el primero en
  instancias de factibilidad): ``dec.opb`` = restricciones + ``objetivo = z*``
  (:func:`~guroback.napback_translation.write_decision_instance`), Naps con
  ``-GM`` y mapa ``cv`` verificado (:class:`~guroback.napback_translation.VarMap`).
  Ocurre dentro de ``solve`` para que ``interrupt`` pueda abortarla.
* **Comprobación obligatoria al arrancar** (§4.2, punto 5): los literales que
  la propagación unitaria PB fuerza en la instancia original (con ``z*``)
  tienen que estar implicados por la CNF; el servidor lo comprueba con
  ``flip`` antes de aplicar ninguna fijación. Si no, :class:`NapBackError`.
* Sondeos: ``exclude_value`` → ``assume``; ``require_any_flip`` → ``flip``
  (``constrain`` de CaDiCaL); ``fix_variable`` → ``fix``; ``interrupt`` →
  ``SIGINT`` (o matar a Naps si está traduciendo u optimizando);
  ``add_live_fixes`` → *pipe* de unidades vivas.

``exact_arithmetic`` es ``False`` a propósito: la cadena Naps → CNF → mapa no
se da por exacta, así que ParaBack valida con ``ExactChecker`` toda solución
que refute algo (§3.1). Los ``VarId`` son los de ``scan_opb`` (posiciones de
los ``x<k>`` presentes en orden de ``k``), como en RoundingBack.
"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
import subprocess
import tempfile
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .backend import Assignment, SolveResult, SolveStatus, VarId
from .exact import ExactChecker
from .line_server import LineServer, find_executable, is_crash_exit
from .napback_translation import (

    NAPS_TESTED_VERSION,
    NapBackCrashed,
    NapBackError,
    PropagationConflict,
    Translation,
    TranslationCancelled,
    find_naps,
    forced_literals,
    instance_names,
    naps_version,
    pb_clauses,
    translate,
    write_decision_instance,
)
from .opb import OPBInstance, ParseCancelled, parse_opb, plain_copy, scan_opb
from .pb_output import OPTIMUM_FOUND
from .server_backend import LineServerBackend, ServerInterrupted

log = logging.getLogger(__name__)

#: Ruta del servidor; si no está se busca en ``<repo>/napback/build/napback-server``.
NAPBACK_SERVER_BIN_ENV = "NAPBACK_SERVER_BIN"

UNSUPPORTED_ALIASES: Mapping[str, str] = {
    "MIPGap": "NapBack demuestra optimalidad exacta; no hay gap",
    "MIPFocus": "sin equivalente; el extractor ya trabaja en modo factibilidad",
    "NodeLimit": "sin equivalente; use TimeLimit",
    "FeasibilityTol": "NapBack trabaja sobre una CNF: no hay tolerancias",
    "IntFeasTol": "NapBack trabaja sobre una CNF: no hay tolerancias",
    "OptimalityTol": "NapBack trabaja sobre una CNF: no hay tolerancias",
}


def find_server() -> Optional[str]:
    """Ruta de ``napback-server`` (``make napback``), o ``None``."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    return find_executable(NAPBACK_SERVER_BIN_ENV, [str(repo / "napback" / "build" / "napback-server")], ["napback-server"])


#: Interrupción durante la traducción o la comprobación inicial (la de la base común).
_Interrupted = ServerInterrupted


class NapBackBackend(LineServerBackend):
    """Backend basado en la traducción de Naps y en ``napback-server`` (CaDiCaL)."""

    name = "napback"
    #: Deliberadamente ``False``: toda solución que refute se comprueba contra el OPB original.
    exact_arithmetic = False
    UNSUPPORTED_ALIASES = UNSUPPORTED_ALIASES
    LABEL = "NapBack"

    @classmethod
    def is_available(cls) -> Tuple[bool, str]:
        naps, server = find_naps(), find_server()
        if naps is None:
            return False, "Naps no encontrado (compile con `make napback` o indique NAPS_BIN)"
        if server is None:
            return False, f"napback-server no encontrado (compile con `make napback` o indique {NAPBACK_SERVER_BIN_ENV})"
        try:
            version = naps_version(naps)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"no se pudo ejecutar {naps}: {exc}"
        if version != NAPS_TESTED_VERSION:
            return False, f"Naps {version} no es la versión probada con NapBack ({NAPS_TESTED_VERSION})"
        return True, ""

    def __init__(self, instance_path: str, params: Optional[Mapping[str, str]] = None) -> None:
        super().__init__(instance_path, params)
        self._workdir_path: Optional[str] = None  # se crea al escribir el primer archivo
        naps, server = find_naps(), find_server()
        if naps is None or server is None:
            raise NapBackError("Naps o napback-server no encontrados (make napback)")
        self._naps, self._server_bin = naps, server
        self._has_objective, self._indices, _ = scan_opb(instance_path)
        self._names = instance_names(self._indices)
        self._plain: Optional[str] = None

        self._cadical_options: Dict[str, str] = {}
        self._max_cnf_vars: Optional[int] = None
        #: ``max_nodes``: tope de nodos de fórmula de Naps; sin él, el que sale del límite de memoria
        #: (:func:`~guroback.napback_translation.naps_max_nodes`).
        self._max_nodes: Optional[int] = None
        self._check_limit = 300.0
        #: Cuántas cláusulas PB se comprueban al arrancar (§4.2, punto 5b).
        self._max_clause_checks = 10000
        #: Motivo por el que NapBack no sirve para esta instancia (CNF demasiado grande): se retira.
        self._unsuitable: Optional[str] = None
        self._translation: Optional[Translation] = None
        self._forced: Dict[str, bool] = {}
        self._clauses: List[List[Tuple[str, bool]]] = []
        #: La instancia leída en Python, una sola vez (:meth:`instance`): la comparten las
        #: comprobaciones de la traducción, el comprobador de la base y el del trabajador.
        self.parsed_instance: Optional[OPBInstance] = None
        self._sat: List[int] = []  # VarId -> variable SAT
        self._var_by_sat: Optional[Dict[int, VarId]] = None
        self._root_unsat = False
        self._verified = False
        self._checker: Optional[ExactChecker] = None
        self._stats.update(
            {"translation_time": 0.0, "cnf_vars": 0.0, "cnf_clauses": 0.0, "fenv_nodes": 0.0, "forced_checked": 0.0,
             "clauses_checked": 0.0}
        )
        for name, value in self.params.items():
            self.set_parameter(name, value)

    # ------------------------------------------------------------------ utilidades
    @property
    def _workdir(self) -> str:
        """Directorio de trabajo (``dec.opb``, CNF, copia descomprimida); lo borra ``close()``."""
        if self._workdir_path is None:
            self._workdir_path = tempfile.mkdtemp(prefix="guroback_nb_")
        return self._workdir_path

    def _literal(self, var: VarId, value: bool) -> int:
        sat = self._sat[var]
        return sat if value else -sat

    def _plain_instance(self) -> str:
        """El OPB sin comprimir (Naps no lee ``.gz``/``.xz``); la copia se borra con el directorio de trabajo."""
        if not self.instance_path.endswith((".gz", ".xz")):
            return self.instance_path
        if self._plain is None:
            self._plain = plain_copy(self.instance_path, directory=self._workdir, prefix="instance_")
        return self._plain

    def instance(self) -> OPBInstance:
        """La instancia leída en Python (se lee la primera vez; ver :meth:`SolverBackend.instance`).

        Consulta ``interrupt`` mientras lee (:class:`~guroback.opb.ParseCancelled`).
        """
        if self.parsed_instance is None:
            self.parsed_instance = parse_opb(self.instance_path, self._interrupted)
        return self.parsed_instance

    def _release_checks(self) -> None:
        """Suelta lo que sólo hacía falta para validar la base y comprobar la traducción.

        La instancia (``parsed_instance``) se conserva: la comparte el comprobador exacto
        del trabajador, así que soltarla aquí no liberaría nada y obligaría a leerla de nuevo.
        """
        self._checker = None
        self._forced = {}
        self._clauses = []

    # ------------------------------------------------------------------ traducción y servidor
    def _translate(self) -> None:
        """Traduce con Naps (``-GM``) y prepara la comprobación 5. Puede lanzar :class:`_Interrupted`."""
        try:
            self._prepare_checks()
        except (ParseCancelled, TranslationCancelled):
            raise _Interrupted() from None
        if self._root_unsat:
            return
        source = self._plain_instance()
        optimum = self._optimum if self._has_objective else None
        if optimum is not None:
            source = write_decision_instance(source, optimum, os.path.join(self._workdir, "dec.opb"))
        try:
            translation = translate(
                source, os.path.join(self._workdir, "instance.cnf"), self._names, naps=self._naps,
                on_start=self._register_subproc, memory_limit_mb=self._memory_limit_mb, max_nodes=self._max_nodes,
            )
        except NapBackError:
            if self._interrupted():
                raise _Interrupted() from None
            raise
        with self._lock:
            self._subproc = None
        self._stats["translation_time"] += translation.seconds
        self._stats["fenv_nodes"] = float(translation.fenv_nodes or 0)
        if translation.trivially_unsat:
            self._root_unsat = True
            return
        varmap = translation.varmap
        assert varmap is not None
        self._stats["cnf_vars"] = float(varmap.num_sat_vars)
        self._stats["cnf_clauses"] = float(varmap.num_clauses)
        if self._max_cnf_vars is not None and varmap.num_sat_vars > self._max_cnf_vars:
            # No es un error de la traducción: NapBack no es útil aquí y se retira sin
            # tumbar la ejecución (los solve responden LIMIT_REACHED; ver _run_server_solve).
            self._unsuitable = f"la CNF tiene {varmap.num_sat_vars} variables, más que max_cnf_vars={self._max_cnf_vars}"
            log.warning("NapBack se retira: %s", self._unsuitable)
            return
        self._sat = [varmap.sat_of[name] for name in self._names]
        self._translation = translation

    def _prepare_checks(self) -> None:
        """Lee la instancia y calcula los literales forzados y las cláusulas PB (§4.2, 5 y 5b).

        Es Python puro y en instancias grandes tarda segundos (haplotipo de 21 756
        variables: ~14 s), así que consulta ``interrupt`` periódicamente: un STOP
        no debe esperar a que termine.
        """
        should_stop = self._interrupted
        instance = self.instance()
        optimum = self._optimum if self._has_objective else None
        try:
            self._forced = forced_literals(instance, optimum, should_stop)
        except PropagationConflict:
            self._root_unsat = True  # p. ej. z* por debajo del óptimo: infactible, como en RoundingBack
            return
        self._clauses = pb_clauses(instance, self._max_clause_checks, should_stop)

    def _verify(self, server: LineServer) -> None:
        """§4.2, punto 5: ``CNF ∧ (∨ ¬f)`` tiene que ser UNSAT. Antes de cualquier ``fix``."""
        translation = self._translation
        assert translation is not None and translation.varmap is not None
        varmap = translation.varmap
        lits = [varmap.literal(name, value) for name, value in self._forced.items()]
        if lits:
            server.command(f"limit {self._check_limit}")
            server.command("flip " + " ".join(map(str, lits)))
            answer = server.command("solve")
            model = server.read_solution() if answer == "sat" else {}
            server.command(f"limit {-1 if self._time_limit is None else self._time_limit}")
            if answer == "sat":
                lost = sorted((n for n, v in self._forced.items() if model.get(varmap.sat_of[n]) != v), key=lambda n: int(n[1:]))
                raise NapBackError(
                    f"la CNF de Naps no implica los {len(lits)} literales que la propagación PB fuerza: admite un "
                    f"modelo que viola {len(lost)} (p. ej. {', '.join(f'{n}={int(self._forced[n])}' for n in lost[:5])}); "
                    "no es equisatisfacible con la instancia"
                )
            if answer != "unsat":
                if self._interrupted():
                    raise _Interrupted()
                raise NapBackError(f"no se pudo comprobar en {self._check_limit} s que la CNF implica los literales forzados")
        self._stats["forced_checked"] = float(len(lits))
        # 5b: cada cláusula PB tiene que estar implicada: CNF ∧ ¬C es UNSAT (assumptions).
        for clause in self._clauses:
            server.command("assume " + " ".join(str(-varmap.literal(name, value)) for name, value in clause))
            answer = server.command("solve")
            if answer == "sat":
                server.read_solution()
                shown = " ∨ ".join(f"{'' if value else '¬'}{name}" for name, value in clause[:6]) + (" ∨ …" if len(clause) > 6 else "")
                raise NapBackError(
                    f"la CNF de Naps no implica la cláusula PB ({shown}): el mapa 'cv' o la traducción no corresponden a la instancia"
                )
            if answer != "unsat":
                if self._interrupted():
                    raise _Interrupted()
                raise NapBackError(f"no se pudo comprobar la cláusula PB ({answer})")
        self._stats["clauses_checked"] = float(len(self._clauses))
        self._verified = True
        self._release_checks()

    # ------------------------------------------------------------------ LineServerBackend
    def _spawn_server(self) -> LineServer:
        translation = self._translation
        assert translation is not None and translation.cnf_path is not None and translation.varmap is not None
        argv = [self._server_bin, translation.cnf_path, str(translation.varmap.num_pb_vars)]
        argv += [f"--{name}={value}" for name, value in self._cadical_options.items()]
        return LineServer(argv, label="napback-server", error=NapBackError, crashed=NapBackCrashed, memory_limit_mb=self._memory_limit_mb)

    def _prepare_server(self, server: LineServer) -> None:
        if not self._verified:
            self._verify(server)

    def _server_ready(self) -> bool:
        """Traduce si hace falta (puede lanzar :class:`_Interrupted`); ``False`` si es infactible o no sirve."""
        if self._translation is None and not self._root_unsat and self._unsuitable is None:
            self._translate()
        return not self._root_unsat and self._unsuitable is None

    def _no_server_answer(self) -> str:
        # Infactible en la traducción o por propagación; o NapBack no sirve para esta instancia.
        return "unsuitable" if self._unsuitable is not None else "unsat"

    def _raw_status(self, answer: str) -> str:
        return self._unsuitable if answer == "unsuitable" and self._unsuitable is not None else answer

    def _kill_subproc(self, proc: "subprocess.Popen[str]") -> None:
        # Naps (traducción u optimización): tras una interrupción su salida no sirve,
        # así que SIGKILL, que no depende de cómo trate SIGTERM.
        proc.kill()

    def _decode_solution(self, model: Mapping[int, bool]) -> Dict[VarId, bool]:
        return {v: model[sat] for v, sat in enumerate(self._sat)}

    def _var_of(self, literal: int) -> Optional[VarId]:
        sat = self._sat
        if self._var_by_sat is None or len(self._var_by_sat) != len(sat):
            self._var_by_sat = {s: v for v, s in enumerate(sat)}
        return self._var_by_sat.get(abs(literal))

    def _live_literals(self, assignment: Sequence[Assignment]) -> List[int]:
        sat = self._sat  # lista asignada de una vez tras la traducción: lectura segura desde otro hilo
        if not sat:
            return []
        return [sat[var] if value else -sat[var] for var, value in assignment]

    def _on_new_optimum(self) -> None:
        # Un z* nuevo exige otra instancia de decisión: se traduce de nuevo en el próximo solve.
        self._drop_server()
        self._translation, self._forced, self._clauses, self._sat = None, {}, [], []
        self._var_by_sat = None
        self._unsuitable = None
        self._root_unsat, self._verified = False, False

    def _set_native_parameter(self, name: str, value: str) -> None:
        if name == "max_cnf_vars":
            self._max_cnf_vars = int(value)
        elif name == "max_nodes":
            self._max_nodes = int(value)
        elif name == "check_limit":
            self._check_limit = float(value)
        elif name == "max_clause_checks":
            self._max_clause_checks = int(value)
        elif name.islower() or "-" in name:
            if self._server is not None:
                raise RuntimeError("las opciones de CaDiCaL sólo pueden fijarse antes de arrancar el servidor")
            self._cadical_options[name] = str(value)  # la valida napback-server al arrancar
        else:
            raise ValueError(f"parámetro desconocido {name!r}; las opciones de CaDiCaL son minúsculas (p. ej. phase)")

    def _cleanup_files(self) -> None:
        if self._workdir_path is not None:
            shutil.rmtree(self._workdir_path, ignore_errors=True)
            self._workdir_path = None

    # ------------------------------------------------------------------ para el trabajador Cadiback nativo
    def prepare_cnf(self) -> Tuple[str, Optional[Translation]]:
        """Traduce y hace todas las comprobaciones de §4 (con ``z*`` ya fijado); devuelve ``(estado, traducción)``.

        ``estado``: ``"ok"`` (CNF verificada), ``"infeasible"`` (Naps o la
        propagación PB demuestran que no hay solución), ``"unsuitable"`` (la CNF
        supera ``max_cnf_vars``) o ``"interrupted"``. Lo usa
        :mod:`paraback.cadiback_worker`, que después ejecuta Cadiback sobre la
        misma CNF; ``interrupt`` puede abortarlo (traducción o comprobación).
        """
        self._mark_solving()
        try:
            server = self._ensure_server()
        except _Interrupted:
            return "interrupted", None
        finally:
            self._unmark_solving()
        if server is None:
            return ("unsuitable" if self._unsuitable is not None else "infeasible"), None
        return "ok", self._translation

    def release_server(self) -> None:
        """Termina ``napback-server`` (la CNF y el directorio de trabajo siguen hasta ``close``)."""
        self._drop_server()

    @property
    def unsuitable_reason(self) -> Optional[str]:
        return self._unsuitable

    # ------------------------------------------------------------------ introspección
    def binary_variables(self) -> Sequence[VarId]:
        return range(len(self._indices))

    def variable_name(self, var: VarId) -> str:
        return self._names[var]

    # ------------------------------------------------------------------ optimización
    def _run_optimizer(self) -> SolveResult:
        """Base propia: Naps como optimizador PB; la solución se valida con enteros."""
        argv = [self._naps, self._plain_instance()]
        if self._time_limit is not None:
            argv.append(f"-given-time={self._time_limit}")
        output, parsed, interrupted, returncode = self._external_optimizer(argv)
        self._solution = None
        if interrupted and returncode is not None and returncode < 0:
            # Lo matamos nosotros (interrupt): su salida puede estar cortada a mitad de
            # la línea "v" y no vale nada. Si terminó por sí solo (código 10/20/30),
            # su respuesta se usa aunque se pidiera la interrupción.
            return SolveResult(SolveStatus.INTERRUPTED, None, raw_status="INTERRUPTED")
        if parsed.status is None and is_crash_exit(returncode, output):
            raise NapBackCrashed(f"Naps terminó con código {returncode}:\n{output[-2000:]}")
        if parsed.status == OPTIMUM_FOUND and parsed.objective is not None:
            values = parsed.values
            missing = [n for n in self._names if n not in values]
            if missing:
                raise NapBackError(f"Naps declaró óptimo pero su solución omite {len(missing)} variables (p. ej. {missing[0]})")
            if self._checker is None:
                self._checker = ExactChecker(self.instance_path, self.instance())
            why = self._checker.violation(values, optimum=parsed.objective)
            if why is not None:
                raise NapBackError(f"la solución óptima de Naps no es exactamente válida: {why}")
            self._solution = {v: values[n] for v, n in enumerate(self._names)}
            return SolveResult(SolveStatus.OPTIMAL, float(parsed.objective), raw_status=parsed.status)
        return self._non_optimal(parsed, interrupted, output)


__all__ = ["NAPBACK_SERVER_BIN_ENV", "NapBackBackend", "find_server"]

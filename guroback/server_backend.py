"""Base común de los backends que resuelven con un servidor de líneas (RoundingBack y NapBack).

Los dos backends hablan con un proceso servidor (:class:`guroback.line_server.LineServer`)
y, para la base propia de una instancia de optimización, con un optimizador externo que
escribe la salida de la competición PB. :class:`LineServerBackend` reúne lo que antes
estaba copiado casi línea por línea en los dos:

* **Guardia del solve**: cerrojo, ``_solving``, interrupción pedida y subproceso en curso
  (optimizador o traductor). ``interrupt`` sólo actúa con un solve en curso; el solve se
  marca *antes* de enviar sus órdenes al servidor (el hilo de escucha reinsiste en cada tic
  por si el servidor limpió su bandera al empezar el ``solve``).
* **Sondeos** con *assumptions* (``exclude_value``) y grupos ``flip``
  (``require_any_flip``), guardados como ``VarId`` y traducidos a literales al resolver;
  no se pueden anidar. Con ``supports_max_flips`` (RoundingBack), ``maximize_flips`` es la
  orden ``maxflip``: el servidor hace la búsqueda lineal SAT-UNSAT y emite cada solución
  como línea ``v`` antes de su respuesta ``r optimum k``; cada una va al oyente de
  incumbentes desde el hilo del solve, y un hilo auxiliar consulta ``should_stop`` (el
  estancamiento ``T``) y manda ``SIGINT`` al servidor si pide parar.
* **Estado base**: ``fix_objective_value`` (normalizado, entero, el servidor sólo se
  reinicia si z* cambia), fijaciones permanentes que se reaplican si el servidor se
  reinicia, límite de tiempo, fijaciones vivas por el *pipe* del servidor.
* Parámetros comunes (``TimeLimit``, ``OutputFlag``, ``Threads``, alias sin equivalente),
  estadísticas del servidor por diferencia, cierre, ``server_pid``.

Cada backend aporta cómo arranca su servidor, cómo traduce un ``VarId`` a literal, cómo
ejecuta su optimizador y cómo interrumpe un subproceso.
"""
from __future__ import annotations

import abc
import logging
import subprocess
import threading
from typing import ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from .backend import (
    Assignment,
    IncumbentListener,
    ProbeScope,
    SolveResult,
    SolveStatus,
    SolverBackend,
    VarId,
    normalize_objective_value,
)
from .line_server import LineServer, LineServerError, popen_die_with_parent
from .pb_output import UNSATISFIABLE, PBOutput, parse_pb_output

log = logging.getLogger("guroback.server")

#: Contadores que publican los dos servidores (orden ``stats``).
SERVER_STATS_KEYS: Tuple[str, ...] = ("conflicts", "decisions", "propagations")
#: Contador de unidades vivas aplicadas (sólo con ``enable_live_fixes``).
LIVE_STATS_KEY = "live_units"
#: Veces que llegaron unidades vivas con RoundingSat fuera del nivel de decisión 0 (tras un
#: *inprocessing*): antes de 2026-10-08 el servidor las añadía ahí y una unidad verdadera pero
#: falsa en la rama explorada convertía el sondeo en "unsat" (backbones falsos).
LIVE_NONROOT_KEY = "live_nonroot"
#: Solves cortados y reanudados para incorporar unidades vivas (NapBack: cada lote que llega
#: durante un solve lo interrumpe; RoundingSat las toma en sus reinicios y no lo informa).
LIVE_RESUMES_KEY = "live_resumes"
#: Soluciones emitidas por los solves ``maxflip`` (sólo RoundingBack).
MAXFLIP_STATS_KEY = "maxflip_solutions"
#: Cada cuánto consulta el hilo auxiliar ``IncumbentListener.should_stop`` durante un solve ``maxflip``.
STALL_POLL_SECONDS = 0.05


class AssumptionScope(ProbeScope):
    """Sondeo por *assumptions* y grupos ``flip`` que el servidor aplica en el próximo ``solve``."""

    def __init__(self, backend: "LineServerBackend") -> None:
        self._backend = backend
        self.assumptions: List[Assignment] = []
        self.groups: List[List[Assignment]] = []
        #: Asignación cuyos cambios maximiza el próximo ``solve`` (orden ``maxflip``), o ``None``.
        self.maximize: Optional[List[Assignment]] = None

    def exclude_value(self, var: VarId, value: bool) -> None:
        # x != value  <=>  x == not value: assumption del literal contrario.
        self.assumptions.append((var, not value))

    def require_any_flip(self, assignment: Sequence[Assignment]) -> None:
        self.groups.append(list(assignment))

    def maximize_flips(self, assignment: Sequence[Assignment]) -> None:
        if not self._backend.supports_max_flips:
            raise NotImplementedError(f"{self._backend.LABEL} no admite maximize_flips")
        if not assignment:
            raise ValueError("maximize_flips exige al menos una variable")
        self.maximize = list(assignment)

    def _restore(self) -> None:
        self.assumptions.clear()
        self.groups.clear()
        self.maximize = None
        self._backend._scope = None


class LineServerBackend(SolverBackend):
    """:class:`SolverBackend` sobre un :class:`LineServer` y un optimizador PB externo."""

    supports_interrupt = True
    supports_time_limit = True
    supports_live_fix = True
    supports_fixed = True

    #: Parámetros de Gurobi sin equivalente en este solver → motivo.
    UNSUPPORTED_ALIASES: ClassVar[Mapping[str, str]] = {}
    #: Nombre del solver en los mensajes de error.
    LABEL: ClassVar[str] = "el servidor"

    def __init__(self, instance_path: str, params: Optional[Mapping[str, str]] = None) -> None:
        super().__init__(instance_path, params)
        # Lo primero: si algo falla más abajo, close() (o __del__) ya tiene lo que necesita.
        self._lock = threading.Lock()
        self._solving = False
        self._interrupt_requested = False
        #: Subproceso en curso que no es el servidor (optimizador, traductor).
        self._subproc: Optional["subprocess.Popen[str]"] = None
        self._server: Optional[LineServer] = None
        self._time_limit: Optional[float] = None
        #: ``MemoryLimit`` (MB): ``RLIMIT_AS`` del servidor y de los optimizadores y traductores que lanza.
        self._memory_limit_mb: Optional[float] = None
        self._optimum: Optional[int] = None
        self._has_objective = False
        self._permanent_fixes: List[Assignment] = []
        self._scope: Optional[AssumptionScope] = None
        self._solution: Optional[Dict[VarId, bool]] = None
        self._stats: Dict[str, float] = {key: 0.0 for key in SERVER_STATS_KEYS}
        self._stats["solving_time"] = 0.0
        self._server_snapshot: Dict[str, float] = {}
        self._live_enabled = False
        self._fixed_supported = True
        #: El oyente de incumbentes pidió parar el solve ``maxflip`` en curso (estancamiento).
        self._listener_stopped = False

    # ------------------------------------------------------------------ lo que aporta cada backend
    @abc.abstractmethod
    def _literal(self, var: VarId, value: bool) -> int:
        """Literal del servidor para ``x_var = value``."""

    @abc.abstractmethod
    def _spawn_server(self) -> LineServer:
        """Arranca el proceso servidor (sin límite, fijaciones ni unidades vivas: los pone la base)."""

    @abc.abstractmethod
    def _server_ready(self) -> bool:
        """``False`` si no se puede arrancar el servidor (la instancia es infactible o no sirve)."""

    @abc.abstractmethod
    def _run_optimizer(self) -> SolveResult:
        """Base propia de una instancia de optimización (z* aún sin fijar)."""

    @abc.abstractmethod
    def _kill_subproc(self, proc: "subprocess.Popen[str]") -> None:
        """Interrumpe el optimizador o el traductor en curso."""

    @abc.abstractmethod
    def _decode_solution(self, model: Mapping[int, bool]) -> Dict[VarId, bool]:
        """Solución por ``VarId`` a partir de la línea ``v`` del servidor."""

    @abc.abstractmethod
    def _var_of(self, literal: int) -> Optional[VarId]:
        """``VarId`` de la variable de un literal del servidor (``None`` si no es de la instancia)."""

    def _no_server_answer(self) -> str:
        """Respuesta de un solve sin servidor (``_server_ready`` falso): por defecto ``unsat``."""
        return "unsat"

    def _on_new_optimum(self) -> None:
        """z* cambió: el servidor actual ya no sirve (por defecto se cierra y se arranca otro al resolver)."""
        self._drop_server()

    def _set_native_parameter(self, name: str, value: str) -> None:
        """Parámetro propio del solver (por defecto, desconocido)."""
        raise ValueError(f"parámetro desconocido {name!r}")

    def _set_output(self, enabled: bool) -> None:
        """``OutputFlag``; por defecto los servidores ya son silenciosos."""

    # ------------------------------------------------------------------ guardia del solve
    def _mark_solving(self, proc: Optional["subprocess.Popen[str]"] = None) -> None:
        with self._lock:
            self._solving = True
            self._interrupt_requested = False
            self._subproc = proc

    def _register_subproc(self, proc: "subprocess.Popen[str]") -> None:
        """Subproceso lanzado dentro de un solve ya marcado: ``interrupt`` lo alcanza."""
        with self._lock:
            self._subproc = proc
            # Sólo cuenta una interrupción del solve en curso: fuera de un solve (p. ej. la
            # traducción de NapBack que dispara fix_variable) interrupt() no hace nada.
            interrupted = self._solving and self._interrupt_requested
        if interrupted and proc.poll() is None:
            # La interrupción llegó entre el arranque del solve y el del subproceso.
            self._kill_subproc(proc)

    def _unmark_solving(self) -> bool:
        """Termina el solve; devuelve si se pidió interrumpirlo."""
        with self._lock:
            self._solving = False
            self._subproc = None
            requested, self._interrupt_requested = self._interrupt_requested, False
            return requested

    def _interrupted(self) -> bool:
        with self._lock:
            return self._solving and self._interrupt_requested

    def interrupt(self) -> None:
        with self._lock:
            if not self._solving:
                return
            self._interrupt_requested = True
            proc = self._subproc
            server = self._server
        if proc is not None:
            if proc.poll() is None:
                self._kill_subproc(proc)
        elif server is not None:
            server.interrupt()

    # ------------------------------------------------------------------ optimizador externo
    def _external_optimizer(self, argv: Sequence[str]) -> Tuple[str, PBOutput, bool, Optional[int]]:
        """Ejecuta un optimizador PB como solve interrumpible: ``(salida, salida leída, interrumpido, código)``."""
        self._mark_solving()
        try:
            proc = popen_die_with_parent(argv, self._memory_limit_mb, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self._register_subproc(proc)
            output, _ = proc.communicate()
        finally:
            interrupted = self._unmark_solving()
        return output, parse_pb_output(output.splitlines()), interrupted, proc.returncode

    @staticmethod
    def _non_optimal(parsed: PBOutput, interrupted: bool, output: str) -> SolveResult:
        """Resultado de un optimizador que no demostró un óptimo."""
        status_line = parsed.status or ""
        if status_line == UNSATISFIABLE:
            return SolveResult(SolveStatus.INFEASIBLE, None, raw_status=status_line)
        if interrupted:
            return SolveResult(SolveStatus.INTERRUPTED, None, raw_status=status_line or "INTERRUPTED")
        if status_line in ("TIMELIMIT", "UNKNOWN", "SATISFIABLE"):
            return SolveResult(SolveStatus.LIMIT_REACHED, None, raw_status=status_line)
        return SolveResult(SolveStatus.OTHER, None, raw_status=status_line or output[-500:])

    # ------------------------------------------------------------------ servidor
    def _start_server(self) -> LineServer:
        server = self._spawn_server()
        self._server_snapshot = {}
        with self._lock:
            self._server = server  # visible ya para interrupt() durante la preparación
        try:
            self._prepare_server(server)
            if self._time_limit is not None:
                server.command(f"limit {self._time_limit}")
            for var, value in self._permanent_fixes:
                server.command(f"fix {self._literal(var, value)}")
            if self._live_enabled:
                self._attach_live(server)
        except BaseException:
            # Un servidor a medio preparar (sin comprobar o sin sus fijaciones) nunca se reutiliza.
            self._drop_server()
            raise
        return server

    def _prepare_server(self, server: LineServer) -> None:
        """Antes de las fijaciones (NapBack: la comprobación de la traducción)."""

    def _attach_live(self, server: LineServer) -> None:
        try:
            server.command(f"live {server.live_read}")
        except LineServerError as exc:
            # Un servidor sin la orden ``live`` (compilado antes de las fijaciones vivas):
            # se sigue sin ellas, el resultado no cambia. Un servidor muerto falla en la orden siguiente.
            log.warning("%s no admite fijaciones vivas: %s", self.LABEL, exc)
            self._live_enabled = False

    def _ensure_server(self) -> Optional[LineServer]:
        """Servidor listo, o ``None`` si ``_server_ready`` dice que no puede haberlo."""
        server = self._server
        if server is not None and server.alive():
            return server
        if self._has_objective and self._optimum is None:
            raise RuntimeError("en instancias de optimización hay que fijar el óptimo (fix_objective_value) primero")
        if not self._server_ready():
            return None
        return self._start_server()

    def _drop_server(self) -> None:
        with self._lock:
            server, self._server = self._server, None
        if server is not None:
            server.close()

    # ------------------------------------------------------------------ ciclo de vida
    def _close(self) -> None:
        self._solution = None
        lock = getattr(self, "_lock", None)
        if lock is None:  # el constructor falló antes de crear el estado
            return
        with lock:
            proc = self._subproc
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        self._drop_server()
        self._cleanup_files()

    def _cleanup_files(self) -> None:
        """Borra copias temporales y directorios de trabajo."""

    def __del__(self) -> None:  # red de seguridad contra procesos huérfanos
        try:
            self.close()
        except Exception:  # noqa: BLE001 - nunca propagar desde un finalizador
            pass

    # ------------------------------------------------------------------ parámetros
    def set_parameter(self, name: str, value: str) -> None:
        if name == "TimeLimit":
            self.set_time_limit(float(value))
        elif name == "MemoryLimit":
            # Lo pone ``--memory-limit`` (``paraback.config.with_memory_limit``): límite de espacio de
            # direcciones de los subprocesos, no un parámetro del solver.
            limit = float(value)
            if limit <= 0:
                raise ValueError("MemoryLimit debe ser positivo (MB)")
            self._memory_limit_mb = limit
        elif name == "OutputFlag":
            self._set_output(str(value).strip() not in ("0", "false", "False"))
        elif name == "Threads":
            if str(value).strip() != "1":
                raise ValueError(f"{self.LABEL} es secuencial: Threads sólo admite 1")
        elif name in self.UNSUPPORTED_ALIASES:
            raise ValueError(f"parámetro {name!r} sin equivalente en {self.LABEL}: {self.UNSUPPORTED_ALIASES[name]}")
        else:
            self._set_native_parameter(name, value)

    def set_feasibility_focus(self) -> None:
        """Los servidores resuelven problemas de satisfacibilidad: ya trabajan en modo factibilidad."""

    def set_time_limit(self, seconds: Optional[float]) -> None:
        self._time_limit = None if seconds is None else float(seconds)
        server = self._server
        if server is not None and server.alive():
            server.command(f"limit {-1 if self._time_limit is None else self._time_limit}")

    # ------------------------------------------------------------------ estado base
    def has_objective(self) -> bool:
        return self._has_objective

    def fix_objective_value(self, value: float) -> None:
        target = normalize_objective_value(value)
        if not self._has_objective:
            if abs(target) > 1e-9:
                raise ValueError(f"la instancia no tiene función objetivo; no puede valer {target}")
            return
        if target != int(target):
            raise ValueError(f"{self.LABEL} requiere un óptimo entero; recibido {value}")
        if self._optimum == int(target):
            return
        self._optimum = int(target)
        # La solución de la resolución base se conserva: el extractor la consulta justo después.
        self._on_new_optimum()

    def fix_variable(self, var: VarId, value: bool) -> None:
        self._permanent_fixes.append((var, value))
        server = self._ensure_server()
        if server is not None:
            server.command(f"fix {self._literal(var, value)}")

    # ------------------------------------------------------------------ optimización
    def _solve(self) -> SolveResult:
        if self._has_objective and self._optimum is None:
            return self._run_optimizer()
        return self._run_server_solve()

    def _account_solve(self, elapsed: float) -> None:
        self._stats["solving_time"] += elapsed

    def _run_server_solve(self) -> SolveResult:
        self._solution = None
        # Antes de las órdenes: un interrupt mientras se envían alcanza al servidor (SIGINT). Los
        # servidores limpian su bandera al empezar "solve", así que ése sí puede perderse; lo
        # cubre el hilo de escucha, que reinsiste en cada tic mientras el sondeo esté decidido.
        self._mark_solving()
        answer = ""
        server: Optional[LineServer] = None
        try:
            try:
                server = self._ensure_server()
            except _Interrupted:
                answer = "unknown"
            if server is not None and answer == "":
                with server.io_lock:
                    scope = self._scope
                    maximize = None
                    if scope is not None:
                        if scope.assumptions:
                            server.command("assume " + " ".join(str(self._literal(v, b)) for v, b in scope.assumptions))
                        for group in scope.groups:
                            server.command("flip " + " ".join(str(self._literal(v, b)) for v, b in group))
                        maximize = scope.maximize
                    if maximize is None:
                        answer = server.command("solve")
                        if answer == "sat":
                            self._solution = self._decode_solution(server.read_solution())
                    else:
                        self._stats.setdefault(MAXFLIP_STATS_KEY, 0.0)
                        server.command("maxflip " + " ".join(str(self._literal(v, b)) for v, b in maximize))
                        answer = self._maxflip_solve(server)
            elif server is None and answer == "":
                answer = self._no_server_answer()
        finally:
            interrupted = self._unmark_solving()
            stopped, self._listener_stopped = self._listener_stopped, False
        objective: Optional[float] = None
        if answer == "sat":
            status, objective = SolveStatus.OPTIMAL, 0.0
        elif answer.startswith("optimum "):
            # maxflip: k = máximo de cambios. Con k = 0 no hay solución nueva (sólo la base, que
            # no cambia nada): solution_value no es válido, y el extractor no lo consulta.
            status, objective = SolveStatus.OPTIMAL, float(int(answer.split()[1]))
        elif answer == "unsat":
            status = SolveStatus.INFEASIBLE
        elif answer == "unknown":
            status = SolveStatus.INTERRUPTED if interrupted or stopped else SolveStatus.LIMIT_REACHED
        elif answer == "unsuitable":
            status = SolveStatus.LIMIT_REACHED
        else:
            status = SolveStatus.OTHER
        if server is not None:
            self._accumulate_stats(server)
        return SolveResult(status, objective, raw_status=self._raw_status(answer))

    def _raw_status(self, answer: str) -> str:
        return answer

    # ------------------------------------------------------------------ maxflip
    def _maxflip_solve(self, server: LineServer) -> str:
        """El ``solve`` de un sondeo ``maxflip``: cada línea ``v`` va al oyente de incumbentes.

        Devuelve la respuesta final del servidor (``optimum k``, ``unknown`` o ``unsat``) y
        deja en ``_solution`` la última solución emitida. Si el oyente falla, se interrumpe
        al servidor, se consume su respuesta (para no desincronizar el protocolo) y la
        excepción se propaga desde ``solve``, como en el backend de Gurobi.
        """
        listener = self._incumbent_listener
        variables = list(self.binary_variables())
        stop = threading.Event()
        watcher: Optional[threading.Thread] = None
        if listener is not None:
            watcher = threading.Thread(
                target=self._watch_stall, args=(server, listener, stop), name=f"{self.LABEL}-maxflip-stall", daemon=True
            )
        with server.io_lock:
            server.send("solve")
            try:
                if watcher is not None:
                    watcher.start()
                while True:
                    tag, rest = server.read_tagged(("v", "r "))
                    if tag == "r ":
                        if rest.startswith("error"):
                            raise self._server_error(f"{self.LABEL} rechazó el solve maxflip: {rest}")
                        return rest
                    model = self._decode_solution(LineServer.parse_model(rest))
                    self._solution = model
                    if listener is not None:
                        listener.on_incumbent([1.0 if model.get(v, False) else 0.0 for v in variables])
                        if listener.should_stop():  # sin esperar al siguiente tic del hilo auxiliar
                            self._listener_stopped = True
                            server.interrupt()
            except BaseException:
                server.interrupt()
                try:
                    server.read_tagged(("r ",))  # las líneas "v" que queden se descartan
                except Exception:  # noqa: BLE001 - el servidor murió: ya lo dirá la orden siguiente
                    pass
                raise
            finally:
                stop.set()
                if watcher is not None and watcher.is_alive():
                    watcher.join()

    def _watch_stall(self, server: LineServer, listener: IncumbentListener, stop: threading.Event) -> None:
        """Consulta ``should_stop`` mientras dura el solve y, si pide parar, interrumpe al servidor."""
        while not stop.wait(STALL_POLL_SECONDS):
            if listener.should_stop():
                self._listener_stopped = True
                server.interrupt()
                return

    def _server_error(self, message: str) -> Exception:
        """Error del servidor con la clase propia del backend."""
        return LineServerError(message)

    def _accumulate_stats(self, server: LineServer) -> None:
        current = server.stats()
        keys = SERVER_STATS_KEYS + ((LIVE_STATS_KEY, LIVE_NONROOT_KEY, LIVE_RESUMES_KEY) if self._live_enabled else ())
        if MAXFLIP_STATS_KEY in self._stats:  # sólo tras el primer sondeo maxflip
            keys += (MAXFLIP_STATS_KEY,)
        for key in keys:
            value = current.get(key, 0.0)
            self._stats[key] = self._stats.get(key, 0.0) + value - self._server_snapshot.get(key, 0.0)
            self._server_snapshot[key] = value

    def solution_value(self, var: VarId) -> float:
        if self._solution is None:
            raise RuntimeError("no hay solución disponible: la última llamada a solve() no fue óptima")
        return 1.0 if self._solution.get(var, False) else 0.0

    # ------------------------------------------------------------------ sondeos
    def _open_scope(self) -> ProbeScope:
        if self._scope is not None:
            raise RuntimeError("los sondeos no se pueden anidar")
        self._scope = AssumptionScope(self)
        return self._scope

    # ------------------------------------------------------------------ fijaciones vivas
    def enable_live_fixes(self) -> bool:
        self._live_enabled = True
        self._stats.setdefault(LIVE_STATS_KEY, 0.0)
        with self._lock:
            server = self._server
        if server is not None and server.alive():
            self._attach_live(server)
        # Si aún no hay servidor, _start_server lo conecta al arrancarlo.
        return self._live_enabled

    def _live_literals(self, assignment: Sequence[Assignment]) -> List[int]:
        """Literales de las fijaciones vivas (``[]`` si aún no se pueden traducir)."""
        return [self._literal(var, value) for var, value in assignment]

    def add_live_fixes(self, assignment: Sequence[Assignment]) -> None:
        if not self._live_enabled:
            return
        with self._lock:
            server = self._server
        if server is None:
            return
        literals = self._live_literals(assignment)
        if literals:
            server.write_live(literals)

    # ------------------------------------------------------------------ literales fijados en la raíz
    def fixed_literals(self) -> Optional[Mapping[VarId, bool]]:
        if not self._fixed_supported:
            return None
        server = self._server
        if server is None or not server.alive():
            return {}
        try:
            reply = server.command("fixed")
        except LineServerError as exc:
            if not server.alive():
                raise  # el servidor murió: es un fallo, no una orden que no conoce
            # Un servidor compilado antes de la orden "fixed": se sigue sin ella.
            log.warning("%s no informa de literales fijados: %s", self.LABEL, exc)
            self._fixed_supported = False
            return None
        out: Dict[VarId, bool] = {}
        for token in reply.split()[1:]:
            lit = int(token)
            var = self._var_of(lit)
            if var is not None:
                out[var] = lit > 0
        return out

    # ------------------------------------------------------------------ estadísticas
    @property
    def native_stats(self) -> Mapping[str, float]:
        return dict(self._stats)

    @property
    def server_pid(self) -> Optional[int]:
        """PID del proceso servidor (para comprobar en tests que no se recrea ni queda huérfano)."""
        server = self._server
        return None if server is None else server.pid


class ServerInterrupted(Exception):
    """Se pidió ``interrupt`` mientras se preparaba el servidor (traducción, comprobación)."""


_Interrupted = ServerInterrupted

__all__ = [
    "AssumptionScope",
    "LIVE_NONROOT_KEY",
    "LIVE_RESUMES_KEY",
    "LIVE_STATS_KEY",
    "MAXFLIP_STATS_KEY",
    "LineServerBackend",
    "SERVER_STATS_KEYS",
    "ServerInterrupted",
]

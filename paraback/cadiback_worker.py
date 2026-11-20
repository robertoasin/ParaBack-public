"""Trabajador nativo de Cadiback: el algoritmo completo de Cadiback, no el extractor de ParaBack.

El trabajador ``napback`` usa el :class:`~guroback.extractor.BackboneExtractor`
de ParaBack sobre la CNF de Naps. Éste, ``cadiback``, ejecuta **Cadiback** con su
propio algoritmo (filtrado por modelos, *flippable*, ``constrain`` sobre todas
las candidatas restantes tras cada UNSAT, unidades del nivel 0, núcleos
opcionales) en su modo ``--paraback=<n>`` (``patches/cadiback/``), y hace de
puente con el protocolo de ParaBack:

1. **Base**: el z* de ``config.optimum``, el relevado por otro trabajador
   (``BASE_HINT``, con su x*) o el propio, con Naps como optimizador; exactamente
   como NapBack (:class:`guroback.napback_backend.NapBackBackend`).
2. **Traducción y comprobaciones**: las de NapBack (``-GM``, mapa ``cv``
   estricto, numeración independiente, literales forzados, cláusulas PB;
   ``docs/PLAN_NAPBACK.md`` §4) con :meth:`NapBackBackend.prepare_cnf`. Un x* ajeno se
   verifica sobre la CNF antes de arrancar Cadiback (si no la satisface, la
   traducción es demasiado fuerte: ``InconsistentKnowledge``).
3. **Cadiback** sobre la CNF verificada, sólo con las variables PB como
   candidatas y, si hay x*, con x* como primer modelo. Sus eventos se traducen
   a ``BASE`` y ``FACTS``:

   * ``m …``: un modelo; el primero es la base. Toda refutación posterior lo
     tiene como testigo.
   * ``d l``: refutada porque el modelo la falsa; ``f l``: porque el modelo con
     ``l`` volteado también lo es (Cadiback congela las candidatas para que lo sea: con
     una variable PB eliminada por CaDiCaL, el volteo podía obligar a cambiarla). **Cada testigo se valida con enteros**
     (:class:`guroback.exact.ExactModel`, el volteo re-evaluando sólo las
     restricciones de esa variable) antes de publicarse; uno inválido no refuta
     nada y se cuenta en ``invalid_solutions``.
   * ``b l``: backbone (UNSAT o unidad del nivel 0). Sin certificado, como en
     cualquier trabajador SAT/MIP.
   * ``.``: fin de lote (antes de cada llamada al solver): todo lo anterior va
     en un único ``FACTS`` (aim100: los 200 backbones en un mensaje).
4. **Hechos ajenos**: el hilo de escucha traduce cada ``UPDATE`` a ``fix <lit>`` /
   ``drop <var>`` (literales CNF) y los escribe en la entrada de Cadiback, que los
   absorbe entre dos llamadas al solver.
   ``STOP`` → ``SIGINT`` (Cadiback termina ``s interrupted``).
"""
from __future__ import annotations

import logging
import queue
import pathlib
import tempfile
import signal
import subprocess
import threading
from typing import IO, Dict, List, Mapping, Optional, Sequence, Tuple

from guroback.backend import Incompatible, SolveStatus
from guroback.exact import ExactChecker, ExactModel
from guroback.extractor import InconsistentKnowledge, RunStatus
from guroback.line_server import find_executable, is_crash_exit, popen_die_with_parent
from guroback.napback_backend import NapBackBackend
from guroback.napback_translation import NapBackCrashed, NapBackError, VarMap

from . import protocol
from .communicator import Communicator
from .config import ParaBackConfig, WorkerSpec
from .coordination import FactPublisher
from .protocol import BaseStatus
from .stats import Stopwatch
from .worker_base import BaseWorker, WorkerUnavailable

log = logging.getLogger("paraback.cadiback")

#: Ruta de Cadiback con el modo ``--paraback``; si no, ``<repo>/napback/build/cadiback/cadiback``.
CADIBACK_BIN_ENV = "CADIBACK_BIN"

#: Opciones de Cadiback admitidas como parámetros del ``WorkerSpec`` (``cadiback:cores=1``).
CADIBACK_FLAGS = ("cores", "chunking", "one-by-one", "no-constrain", "no-filter", "no-fixed", "no-flip", "no-inprocessing", "set-phase")
#: Parámetros con valor que van a Cadiback tal cual (``seed=3`` → ``--seed=3``: semilla y barajado de CaDiCaL).
CADIBACK_VALUES = ("seed",)

#: Los hechos ajenos se absorben entre dos llamadas al solver, no dentro de la que está en curso.
CAPABILITIES = {"interrupt": True, "time_limit": False, "solution_hint": False, "live_fix": False}


def find_cadiback() -> Optional[str]:
    repo = pathlib.Path(__file__).resolve().parent.parent
    return find_executable(CADIBACK_BIN_ENV, [str(repo / "napback" / "build" / "cadiback" / "cadiback")], ["cadiback"])


def is_available() -> Tuple[bool, str]:
    ok, reason = NapBackBackend.is_available()
    if not ok:
        return False, reason
    binary = find_cadiback()
    if binary is None:
        return False, "Cadiback no encontrado (make napback)"
    try:
        out = subprocess.run([binary, "-h"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"no se pudo ejecutar {binary}: {exc}"
    if "--paraback" not in out.stdout:
        return False, f"{binary} no tiene el modo --paraback (patches/cadiback/, make napback)"
    return True, ""


class CadibackWorker(BaseWorker):
    """Trabajador de ParaBack que ejecuta Cadiback (mismo protocolo que :class:`paraback.worker.Worker`).

    Del :class:`~paraback.worker_base.BaseWorker` hereda ``HELLO``/``ERROR``/``DONE``, el hilo
    de escucha y la elección de la base; los hechos ajenos le llegan siempre por el hilo de
    escucha (Cadiback los absorbe entre dos llamadas a su solver) y ``STOP`` lo interrumpe
    con ``SIGINT`` salvo con ``interrupt_on_stop=False``. No tiene sondeos propios que
    trazar ni rebanadas de tiempo: ``sync_every``, ``probe_time_slice``, ``share_solutions``
    y ``share_hints`` no le afectan.
    """

    def __init__(self, comm: Communicator, config: ParaBackConfig, spec: WorkerSpec) -> None:
        super().__init__(comm, config, spec)
        #: Sólo hechos y llegadas de UPDATE: los sondeos viven dentro de Cadiback (paraback/trace.py).
        self.backend: Optional[NapBackBackend] = None
        self.publisher = FactPublisher(comm, comm.rank, self.tracer)
        #: Tiempo del hilo principal enviando ``FACTS`` (``WorkerStats.send_time``).
        self.send_watch = Stopwatch()
        self._lock = threading.Lock()
        self._proc: Optional["subprocess.Popen[str]"] = None
        #: Se le mandó SIGINT: una salida por señal es entonces una interrupción, no una caída.
        self._interrupt_sent = False
        self._varmap: Optional[VarMap] = None
        #: Hechos ajenos recibidos antes de que Cadiback arrancara (se le envían tras ``start``).
        self._queued: List[protocol.Facts] = []
        #: Lo que se escribe en la entrada de Cadiback lo escribe sólo ``_writer``: Cadiback
        #: no lee su entrada durante una llamada al solver, y un ``write`` bloqueado en el
        #: hilo principal (que tiene que seguir leyendo su salida) o en el de escucha (que
        #: tiene que poder entregar STOP) interbloquearía las dos tuberías.
        self._outbox: "queue.Queue[Optional[str]]" = queue.Queue()
        self._writer: Optional[threading.Thread] = None
        self._stderr: Optional[IO[str]] = None

    # ------------------------------------------------------------------ BaseWorker
    def capabilities(self) -> Mapping[str, bool]:
        ok, reason = is_available()
        if not ok:
            raise WorkerUnavailable(reason)
        return CAPABILITIES

    def interrupt(self) -> None:
        with self._lock:
            proc, backend = self._proc, self.backend
        if proc is not None:
            if proc.poll() is None:
                self._interrupt_sent = True
                proc.send_signal(signal.SIGINT)
        elif backend is not None:
            backend.interrupt()

    def interrupt_base(self) -> None:
        backend = self.backend
        if backend is not None:
            backend.interrupt()  # Naps optimizando: la base ya la tiene otro

    def on_update(self, update: protocol.Facts) -> None:
        self.stats.updates_received += 1
        with self._lock:
            if self._varmap is None or self._proc is None:
                self._queued.append(update)
                return
            self._send_facts([update])

    def execute(self) -> RunStatus:
        params = {k: v for k, v in self.spec.params.items() if k not in CADIBACK_FLAGS and k not in CADIBACK_VALUES}
        backend = NapBackBackend(self.config.instance, params)
        with backend:
            with self._lock:
                self.backend = backend
            # Los UPDATE sólo se usan al llegar (on_update): no se guardan en el búfer.
            self.start_listener(base_hints=self.config.share_base_optimum, buffer_updates=False)
            try:
                return self._extract(backend)
            finally:
                self.stop_listener()
                self._close_cadiback()
                with self._lock:
                    self.backend = None
                self.stats.native = dict(backend.native_stats)
                self.stats.facts_sent = self.publisher.facts_sent
                self.stats.facts_count = self.publisher.facts_count
                self.stats.send_time = self.send_watch.total

    # ------------------------------------------------------------------ hechos ajenos
    def _send_facts(self, updates: Sequence[protocol.Facts]) -> None:
        """Con ``_lock`` tomado: traduce los hechos ajenos a ``fix <lit>`` / ``drop <var>`` CNF y los encola."""
        varmap = self._varmap
        assert varmap is not None
        sat_of = varmap.sat_of
        out = []
        for update in updates:
            for name, value in update.backbone:
                sat = sat_of.get(name)
                if sat is not None:
                    out.append(f"fix {sat if value else -sat}")
            for name in update.refuted:
                sat = sat_of.get(name)
                if sat is not None:
                    out.append(f"drop {sat}")
        if not out:
            return
        self.stats.external_facts += len(out)
        self._outbox.put("\n".join(out) + "\n")

    def _write_loop(self, stdin: IO[str]) -> None:
        """Hilo escritor: vacía ``_outbox`` en la entrada de Cadiback (puede bloquearse sin riesgo)."""
        while True:
            chunk = self._outbox.get()
            if chunk is None:
                return
            try:
                stdin.write(chunk)
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return  # Cadiback ya terminó: los hechos ajenos ya no hacen falta

    # ------------------------------------------------------------------ base
    def _base(self, backend: NapBackBackend) -> Tuple[Optional[RunStatus], Optional[float], Optional[Dict[str, bool]]]:
        """``(estado si no hay base, z*, x* por nombre)``; la x* (propia o ajena) es el primer modelo."""
        choice = self.initial_base()
        if choice is not None and choice.source == "file":
            return None, choice.optimum, choice.values
        if not backend.has_objective():
            return None, None, None
        if choice is not None:
            return None, choice.optimum, choice.values
        while True:
            if self.hint.stop_requested:
                return RunStatus.INTERRUPTED, None, None
            result = backend.solve()  # Naps como optimizador (la solución se valida con enteros)
            if result.status is SolveStatus.OPTIMAL:
                assert result.objective is not None
                xs = {backend.variable_name(v): backend.solution_value(v) > 0.5 for v in backend.binary_variables()}
                return None, result.objective, xs
            if result.status is SolveStatus.INFEASIBLE:
                return RunStatus.INFEASIBLE, None, None
            retry = self.take_hint()
            if retry is None:
                return RunStatus.INTERRUPTED, None, None
            return None, retry.optimum, retry.values

    def _extract(self, backend: NapBackBackend) -> RunStatus:
        missing, optimum, x_star = self._base(backend)
        if missing is not None:
            self.hint.base_solved = True
            self.send(protocol.base(self.rank, BaseStatus(str(missing)), None, {}))
            return missing
        if optimum is not None:
            backend.fix_objective_value(optimum)
        self.hint.base_solved = True
        # Si Naps no traduce la instancia, prepare_cnf lanza NapsCannotTranslate (Incompatible):
        # con --substitute, el hueco sigue con el sustituto (paraback/worker_base.py).
        state, translation = backend.prepare_cnf()
        if state != "ok":
            if state == "infeasible" and x_star is not None:
                raise InconsistentKnowledge(f"la CNF de Naps es infactible con z* = {optimum}, pero hay un x* de ese coste")
            if state == "unsuitable":
                raise Incompatible(f"NapBack no es útil aquí: {backend.unsuitable_reason}")
            report = RunStatus.INFEASIBLE if state == "infeasible" else RunStatus.INTERRUPTED
            self.send(protocol.base(self.rank, BaseStatus(str(report)), None, {}))
            return report
        assert translation is not None and translation.varmap is not None and translation.cnf_path is not None
        varmap = translation.varmap
        if x_star is not None:
            self._verify_x_star(backend, x_star)
        backend.release_server()
        if self.hint.stop_requested:
            self.send(protocol.base(self.rank, BaseStatus.INTERRUPTED, None, {}))
            return RunStatus.INTERRUPTED
        # La instancia ya leída para las comprobaciones de NapBack: no se vuelve a leer.
        checker = ExactChecker(self.config.instance, backend.instance()) if self.config.exact_check else None
        return self._run_cadiback(translation.cnf_path, varmap, optimum, x_star, checker)

    def _verify_x_star(self, backend: NapBackBackend, x_star: Mapping[str, bool]) -> None:
        """x* (propio o ajeno) asumido sobre la CNF tiene que ser SAT (§4.2, punto 6)."""
        values = {v: x_star[backend.variable_name(v)] for v in backend.binary_variables() if backend.variable_name(v) in x_star}
        with backend.probe() as scope:
            for var, value in values.items():
                scope.exclude_value(var, not value)
            result = backend.solve()
        if result.status is SolveStatus.INTERRUPTED:
            return  # STOP: Cadiback no llegará a arrancar
        if not result.is_optimal:
            raise InconsistentKnowledge(f"la CNF de Naps rechaza el x* de la base ({result.status.value}): traducción demasiado fuerte")

    def _run_cadiback(
        self, cnf: str, varmap: VarMap, optimum: Optional[float], x_star: Optional[Mapping[str, bool]], checker: Optional[ExactChecker]
    ) -> RunStatus:
        if self.hint.stop_requested:
            self.send(protocol.base(self.rank, BaseStatus.INTERRUPTED, None, {}))
            return RunStatus.INTERRUPTED
        binary = find_cadiback()
        assert binary is not None
        flags = [f"--{k}" for k, v in self.spec.params.items() if k in CADIBACK_FLAGS and str(v).strip().lower() in ("1", "true")]
        flags += [f"--{k}={str(v).strip()}" for k, v in self.spec.params.items() if k in CADIBACK_VALUES]
        if self.spec.order != "file":
            flags.append(f"--order={self.spec.order}")  # el orden de sondeo de las candidatas (patches/cadiback/0002)
        argv = [binary, f"--paraback={varmap.num_pb_vars}", *flags, cnf]
        # stderr a un archivo, no a un pipe que nadie vacía: un Cadiback que escriba mucho en
        # stderr se bloquearía. Sólo se lee si no arranca.
        self._stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        memory_limit = self.spec.params.get("MemoryLimit")  # --memory-limit: RLIMIT_AS de Cadiback (y de Naps, vía el backend)
        proc = popen_die_with_parent(
            argv, None if memory_limit is None else float(memory_limit),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, bufsize=1,
        )
        assert proc.stdin is not None and proc.stdout is not None
        ready = proc.stdout.readline().strip()
        if ready != f"r ready {varmap.num_pb_vars}":
            proc.kill()
            proc.wait()
            self._stderr.seek(0)
            diagnostics = self._stderr.read()[-2000:]
            self._stderr.close()
            self._stderr = None
            raise NapBackError(f"Cadiback no arrancó en modo --paraback: {ready!r} {diagnostics}")
        start = "start" + ("".join(f" {varmap.literal(n, v)}" for n, v in x_star.items() if n in varmap.sat_of) if x_star else "")
        # 'start' se escribe aquí: Cadiback lo lee antes de producir ninguna salida.
        proc.stdin.write(start + "\n")
        proc.stdin.flush()
        self._writer = threading.Thread(target=self._write_loop, args=(proc.stdin,), name=f"paraback-cadiback-writer-{self.rank}", daemon=True)
        self._writer.start()
        with self._lock:
            self._proc, self._varmap = proc, varmap
            queued, self._queued = self._queued, []
            if queued:
                self._send_facts(queued)
        if self.hint.stop_requested:
            self.interrupt()
        return self._events(proc, proc.stdout, varmap, optimum, checker)

    def _events(self, proc: "subprocess.Popen[str]", out: IO[str], varmap: VarMap, optimum: Optional[float], checker: Optional[ExactChecker]) -> RunStatus:
        name_of = varmap.name_of
        model: Optional[Dict[str, bool]] = None
        evaluated: Optional[ExactModel] = None
        backbone: List[Tuple[str, bool]] = []
        refuted: List[str] = []
        sent_base = False

        def flush() -> None:
            if backbone or refuted:
                with self.send_watch:
                    self.publisher.facts(list(backbone), list(refuted))
                backbone.clear()
                refuted.clear()

        def witness() -> Optional[ExactModel]:
            nonlocal evaluated
            if checker is None or model is None:
                return None
            if evaluated is None:
                evaluated = checker.model(model, optimum)
            return evaluated

        status = RunStatus.INTERRUPTED
        withdrawn = False
        for line in out:
            tag, _, rest = line.strip().partition(" ")
            if withdrawn:
                continue  # base no válida: ya nada de este trabajador es de fiar
            if tag == "m":
                flush()
                model = {name_of[abs(int(t))]: int(t) > 0 for t in rest.split()}
                evaluated = None
                self.stats.models += 1
                if not sent_base:
                    sent_base = True
                    if checker is not None and checker.violation(model, optimum) is not None:
                        # La base de Cadiback no es exactamente válida: se retira como Gurobi/SCIP.
                        reason = checker.violation(model, optimum)
                        self.send(protocol.base(self.rank, BaseStatus.INVALID, None, {}))
                        self.stats.base_invalid = reason
                        withdrawn = True
                        self.interrupt()
                        continue
                    self.send(protocol.base(self.rank, BaseStatus.FEASIBLE, optimum, model))
            elif tag in ("d", "f"):
                lit = int(rest)
                name = name_of[abs(lit)]
                w = witness()
                reason = None
                if w is not None:
                    reason = w.violation if tag == "d" else w.flip_violation(name)
                if reason is not None:
                    self.stats.invalid_solutions += 1
                    log.info("trabajador %d (cadiback): refutación de %s rechazada (%s)", self.rank, name, reason)
                    continue
                refuted.append(name)
                if tag == "d":
                    self.stats.refuted_by_model += 1
                else:
                    self.stats.refuted_by_flip += 1
            elif tag == "b":
                lit = int(rest)
                if lit == 0:
                    continue
                backbone.append((name_of[abs(lit)], lit > 0))
                self.stats.backbone_found += 1
            elif tag == ".":
                flush()  # fin de un lote: todo lo aprendido desde la llamada anterior al solver
            elif tag == "z":
                raise InconsistentKnowledge(f"Cadiback: el complementario de la candidata {rest} está fijado en la raíz")
            elif tag == "e":
                raise InconsistentKnowledge(f"Cadiback rechaza un hecho ajeno ({rest}): contradice lo que demostró")
            elif tag == "s":
                status = {"SATISFIABLE": RunStatus.COMPLETE, "UNSATISFIABLE": RunStatus.INFEASIBLE}.get(rest, RunStatus.INTERRUPTED)
        flush()
        proc.wait()
        if not self._interrupt_sent and is_crash_exit(proc.returncode):
            # Murió (SIGKILL del sistema, abort por memoria con RLIMIT_AS): retirada, no un resultado.
            raise NapBackCrashed(f"Cadiback terminó con código {proc.returncode} (señal o memoria agotada)")
        if status is RunStatus.INFEASIBLE and not sent_base:
            self.send(protocol.base(self.rank, BaseStatus.INFEASIBLE, None, {}))
        if not sent_base and status is not RunStatus.INFEASIBLE:
            self.send(protocol.base(self.rank, BaseStatus.INTERRUPTED, None, {}))
        return status

    def _close_cadiback(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        self._outbox.put(None)
        if self._writer is not None:
            self._writer.join(timeout=5)  # el kill cierra la tubería: un write pendiente falla y sale
            self._writer = None
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


__all__ = ["CADIBACK_BIN_ENV", "CADIBACK_FLAGS", "CadibackWorker", "find_cadiback", "is_available"]

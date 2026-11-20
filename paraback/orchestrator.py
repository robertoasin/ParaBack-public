"""Orquestador de ParaBack: conocimiento global, fusión de hechos y ciclo de vida.

El orquestador ocupa el rango :data:`paraback.config.ORCHESTRATOR_RANK` y no
resuelve nada: recibe los hechos de los trabajadores (:mod:`paraback.protocol`),
los fusiona en un :class:`GlobalKnowledge` keyed por nombre de variable, los
redistribuye a los demás trabajadores y decide cuándo terminar. Es una clase
abstracta: lanzar y esperar a los procesos heterogéneos depende del transporte
(:class:`LocalOrchestrator` en :mod:`paraback.local` usa hilos; el
``MPIOrchestrator`` de la Fase 2 se apoya en ``mpirun``).

Etapas del orquestador
----------------------

::

    INIT ──launch_workers()──▶ HELLO/BASE ──primer BASE──▶ PROBING ──completo/límite──▶ STOPPING ──todos DONE──▶ DONE
                                                                                              │
                                                              ERROR de un trabajador ─────────┴──▶ FAILED

* **HELLO/BASE**: cada trabajador anuncia su especificación y, tras resolver la
  instancia base, su ``z*`` y su solución. El primer ``BASE`` fija los valores
  candidatos globales; cada ``BASE`` posterior debe traer el mismo ``z*``
  (normalizado) o la ejecución se aborta con ``FAILED`` (discrepancia por
  tolerancias: nunca se resuelve en silencio). Una solución base distinta
  refuta inmediatamente las variables en las que difiere.
* **PROBING**: cada ``FACTS`` se fusiona; los hechos nuevos se reenvían a los
  demás trabajadores (nunca al emisor) en un ``UPDATE``: en cuanto llegan, cada
  ``share_interval`` s acumulados, o nunca (``share_facts=False``: trabajadores
  aislados, el orquestador sólo une lo que demuestran).
* **STOPPING**: cuando el conocimiento global está completo (o se agota
  ``time_limit``) se envía ``STOP`` a todos y se espera su ``DONE``.
"""
from __future__ import annotations

import abc
import json
import logging
import os
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, TextIO, Tuple

from guroback.backend import normalize_objective_value
from guroback.extractor import BackboneKnowledge, InconsistentKnowledge, RunStatus, VarStatus, literal_label

from . import protocol
from .communicator import Communicator
from .config import FLOAT_SOLVERS, ORCHESTRATOR_RANK, ParaBackConfig, PASSIVE_WORKERS
from .protocol import BaseStatus, StopReason
from .stats import WorkerRecord, WorkerStats, max_rss_mb
from .trace import Tracer, now

log = logging.getLogger("paraback.orchestrator")


#: Segundos entre dos instantáneas de ``stats_json`` durante la ejecución (:meth:`Orchestrator.snapshot`).
SNAPSHOT_INTERVAL = 10.0


class OrchestratorState(Enum):
    INIT = "init"
    WAITING_BASE = "waiting_base"
    PROBING = "probing"
    STOPPING = "stopping"
    DONE = "done"
    FAILED = "failed"


class GlobalKnowledge:
    """Conocimiento global keyed por nombre, con procedencia y registro incremental.

    Envuelve un :class:`guroback.extractor.BackboneKnowledge` cuyos "VarId" son
    los nombres de variable. Cada hecho nuevo se anota en un registro
    (``log``) con el rango que lo aportó; :meth:`pending_for` devuelve los
    hechos que un trabajador aún no ha recibido, excluyendo los suyos.
    """

    def __init__(self, values: Mapping[str, bool], optimum: Optional[float]) -> None:
        self.knowledge: BackboneKnowledge[str] = BackboneKnowledge(values=dict(values), optimum=optimum)
        #: (kind, name, value, source) en orden de llegada; kind ∈ {"backbone", "refuted"}.
        self.log: List[Tuple[str, str, bool, int]] = []
        self._cursor: Dict[int, int] = {}
        #: Desde qué entrada de ``log`` los hechos de un rango son de su trabajador actual (:meth:`restart`).
        self._own_from: Dict[int, int] = {}
        self.provenance: Dict[str, int] = {}
        #: Chunk que cada trabajador está sondeando ahora (``announce_intents``).
        self.intents: Dict[int, FrozenSet[str]] = {}

    # ------------------------------------------------------------------ consultas
    @property
    def optimum(self) -> Optional[float]:
        return self.knowledge.optimum

    def is_complete(self) -> bool:
        return self.knowledge.is_complete()

    def counts(self) -> Dict[str, int]:
        return self.knowledge.counts()

    def backbone(self) -> List[Tuple[str, bool]]:
        return [(name, value) for name, value in self.knowledge.backbone()]

    def refuted(self) -> List[str]:
        return list(self.knowledge.refuted())

    def unknown(self) -> List[str]:
        return list(self.knowledge.unknown())

    # ------------------------------------------------------------------ fusión
    def add_variables(self, values: Mapping[str, bool]) -> List[str]:
        """Variables que un trabajador conoce y el conocimiento global aún no (unión de nombres)."""
        added = []
        for name, value in values.items():
            if name not in self.knowledge.values:
                self.knowledge.values[name] = value
                self.knowledge.status[name] = VarStatus.UNKNOWN
                added.append(name)
        return added

    def merge(
        self, source: int, backbone: Sequence[Tuple[str, bool]], refuted: Sequence[str], solution: Optional[Mapping[str, bool]]
    ) -> Tuple[List[str], List[str]]:
        """Incorpora hechos; devuelve ``(backbone nuevos, refutados nuevos)`` por nombre."""
        known = self.knowledge.values
        new_backbone = self.knowledge.mark_backbone([(n, v) for n, v in backbone if n in known])
        new_refuted = self.knowledge.mark_not_backbone([n for n in refuted if n in known])
        if solution:
            new_refuted += self.knowledge.absorb_solution({n: v for n, v in solution.items() if n in known})
        for name in new_backbone:
            self.log.append(("backbone", name, known[name], source))
            self.provenance[name] = source
        for name in new_refuted:
            self.log.append(("refuted", name, False, source))
            self.provenance[name] = source
        return new_backbone, new_refuted

    # ------------------------------------------------------------------ intenciones
    def note_intent(self, rank: int, chunk: Sequence[str]) -> int:
        """``rank`` empieza a sondear ``chunk``; devuelve con cuántos otros sondeos en curso se solapa."""
        names = frozenset(chunk)
        self.intents[rank] = names
        return sum(1 for other, theirs in self.intents.items() if other != rank and not names.isdisjoint(theirs))

    def clear_intent(self, rank: int) -> None:
        """El sondeo anunciado por ``rank`` terminó (llegaron sus hechos o el trabajador acabó)."""
        self.intents.pop(rank, None)

    def probing_except(self, rank: int) -> List[str]:
        """Lo que los demás trabajadores están sondeando ahora (para ``UPDATE.probing_elsewhere``)."""
        out: Set[str] = set()
        for other, names in self.intents.items():
            if other != rank:
                out |= names
        return sorted(out)

    def restart(self, rank: int) -> None:
        """En ``rank`` empieza otro trabajador (sustitución en el sitio): no sabe nada, ni lo de su antecesor."""
        self._cursor[rank] = 0
        self._own_from[rank] = len(self.log)
        self.clear_intent(rank)

    def pending_for(self, rank: int) -> Tuple[List[Tuple[str, bool]], List[str]]:
        """Hechos que ``rank`` no ha recibido todavía y no aportó él mismo (los de su antecesor, sí)."""
        start = self._cursor.get(rank, 0)
        own_from = self._own_from.get(rank, 0)
        backbone: List[Tuple[str, bool]] = []
        refuted: List[str] = []
        for index, (kind, name, value, source) in enumerate(self.log[start:], start):
            if source == rank and index >= own_from:
                continue
            if kind == "backbone":
                backbone.append((name, value))
            else:
                refuted.append(name)
        self._cursor[rank] = len(self.log)
        return backbone, refuted


@dataclass
class ParaBackResult:
    """Resultado agregado de una ejecución de ParaBack."""

    #: ``complete``, ``infeasible``, ``interrupted`` (límite global) o ``failed``.
    status: RunStatus
    is_optimization: bool
    optimum: Optional[float]
    backbone: List[Tuple[str, bool]] = field(default_factory=list)
    refuted: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)
    workers: Dict[int, WorkerRecord] = field(default_factory=dict)
    wall_time: float = 0.0
    #: Segundos desde el arranque del orquestador hasta que el backbone quedó completo (``None`` si no
    #: lo quedó). ``wall_time`` sigue hasta que terminan todos: tras STOP, hasta ``stop_grace`` s más.
    complete_time: Optional[float] = None
    messages: int = 0
    error: Optional[str] = None
    #: Trabajadores que no terminaron ``stop_grace`` segundos después de STOP: el resultado se
    #: escribió sin ellos y el transporte debe matarlos (``MPI_Abort``).
    abandoned: List[int] = field(default_factory=list)
    #: Trabajadores que se retiraron (``ERROR`` no fatal: su solver murió o se quedó sin memoria; o
    #: su infactibilidad en coma flotante la desmintió una solución comprobada); la ejecución siguió
    #: con los demás. Su motivo está en ``workers[r].error``.
    retired: List[int] = field(default_factory=list)
    #: Pico de memoria residente del proceso del orquestador (MB).
    max_rss_mb: float = 0.0
    #: Con ``announce_intents``: sondeos anunciados y cuántos se solapaban con otro en curso.
    intents: int = 0
    intent_overlaps: int = 0
    #: ``UPDATE`` enviados a los trabajadores y hechos que llevaban.
    updates_sent: int = 0
    update_facts: int = 0
    #: Segundos procesando mensajes y reenviando hechos (el resto, esperando mensajes).
    busy_time: float = 0.0
    #: Rango del trabajador cuya base fijó z* y x* (``None`` si no hubo base).
    base_worker: Optional[int] = None
    #: La base x* por nombre (vacía sin base). No va en ``to_dict``: se escribe con :meth:`write_solution`.
    base_values: Dict[str, bool] = field(default_factory=dict)

    @property
    def solve_count(self) -> int:
        return sum(w.stats.solve_count for w in self.workers.values())

    @property
    def total_solve_time(self) -> float:
        return sum(w.stats.total_solve_time for w in self.workers.values())

    @property
    def backbone_literals(self) -> List[str]:
        return [("" if value else "-") + literal_label(name) for name, value in self.backbone]

    def to_dict(self) -> Dict[str, Any]:
        """Métricas serializables a JSON (``--stats-json``; se leen con :class:`paraback.stats.RunSummary`)."""
        return {
            "status": str(self.status),
            "is_optimization": self.is_optimization,
            "optimum": self.optimum,
            "backbone": self.backbone_literals,
            "num_backbone": len(self.backbone),
            "num_refuted": len(self.refuted),
            "num_unknown": len(self.unknown),
            "wall_time": self.wall_time,
            "complete_time": self.complete_time,
            "messages": self.messages,
            "solve_count": self.solve_count,
            "total_solve_time": self.total_solve_time,
            "error": self.error,
            "abandoned": list(self.abandoned),
            "retired": list(self.retired),
            "max_rss_mb": self.max_rss_mb,
            "intents": self.intents,
            "intent_overlaps": self.intent_overlaps,
            "updates_sent": self.updates_sent,
            "update_facts": self.update_facts,
            "base_worker": self.base_worker,
            "busy_time": self.busy_time,
            "workers": {str(rank): w.to_dict() for rank, w in sorted(self.workers.items())},
        }

    def write_solution(self, out: TextIO, per_line: int = 1000) -> None:
        """La base x* en el formato de la competición PB (lo que lee ``--base-solution``).

        ``s OPTIMUM FOUND`` con objetivo (la optimalidad es la que afirmó el solver que resolvió
        la base; la factibilidad y el coste se comprobaron con enteros), ``s SATISFIABLE`` sin él y
        ``s UNSATISFIABLE`` si la instancia es infactible (antes ``s UNKNOWN``: las DEC infactibles del
        histórico no se distinguían de las que agotan el límite).
        """
        if self.status is RunStatus.INFEASIBLE:
            out.write("s UNSATISFIABLE\n")
            return
        if not self.base_values:
            out.write("s UNKNOWN\n")
            return
        if self.is_optimization and self.optimum is not None:
            out.write("s OPTIMUM FOUND\n")
            out.write(f"o {int(round(self.optimum))}\n")
        else:
            out.write("s SATISFIABLE\n")
        literals = [("" if value else "-") + name for name, value in self.base_values.items()]
        for start in range(0, len(literals), per_line):
            out.write("v " + " ".join(literals[start : start + per_line]) + "\n")

    def write(self, out: TextIO) -> None:
        """Escribe el archivo de backbone en el formato de GuroBack (``b 0`` sólo si está completo)."""
        for lit in self.backbone_literals:
            out.write(f"b {lit}\n")
        if self.status is RunStatus.COMPLETE:
            out.write("b 0\n")
        out.flush()


class Orchestrator(abc.ABC):
    """Bucle de eventos del rango 0. Las subclases aportan el lanzamiento de los trabajadores."""

    def __init__(self, config: ParaBackConfig, comm: Communicator) -> None:
        if comm.rank != ORCHESTRATOR_RANK:
            raise ValueError(f"el orquestador debe ser el rango {ORCHESTRATOR_RANK}")
        if comm.size != config.mpi_size:
            raise ValueError(f"se esperaban {config.mpi_size} rangos (orquestador + trabajadores) y hay {comm.size}")
        self.config = config
        self.comm = comm
        self.state = OrchestratorState.INIT
        self.workers: Dict[int, WorkerRecord] = {rank: WorkerRecord(rank) for rank in range(1, comm.size)}
        self.knowledge: Optional[GlobalKnowledge] = None
        self.is_optimization: Optional[bool] = None
        self._stop_sent = False
        self._stop_reason: str = ""
        self._stop_sent_at = 0.0
        self.abandoned: List[int] = []
        self.retired: List[int] = []
        self._error: Optional[str] = None
        self._messages = 0
        self._start = 0.0
        #: Cuándo quedó completo el backbone (``time.perf_counter()``; ``None`` si aún no).
        self._complete_at: Optional[float] = None
        #: Rangos cuya base es ``infeasible`` y que nadie ha desmentido (ver :meth:`_on_infeasible_base`).
        self._infeasible_claims: List[int] = []
        self._base_worker: Optional[int] = None
        self._intents = 0
        self._intent_overlaps = 0
        self._updates_sent = 0
        self._update_facts = 0
        self._busy_time = 0.0
        #: Reenvío periódico (``share_interval > 0``): próximo instante de reenvío
        #: (``time.perf_counter()``), si hay hechos sin reenviar y la última pista recibida.
        self._next_share = 0.0
        self._unshared = False
        self._pending_hint: Optional[Tuple[int, Dict[str, bool]]] = None
        self.tracer = Tracer(config.trace_dir, comm.rank)
        #: Última instantánea de ``stats_json`` (``time.perf_counter()``); ver :meth:`snapshot`.
        self._snapshot_at = 0.0

    # ------------------------------------------------------------------ ganchos de transporte
    @abc.abstractmethod
    def launch_workers(self) -> None:
        """Arranca los trabajadores (hilos, procesos MPI ya lanzados por ``mpirun``, ...)."""

    @abc.abstractmethod
    def wait_workers(self) -> None:
        """Espera a que todos los trabajadores hayan terminado y libera sus recursos."""

    # ------------------------------------------------------------------ ciclo de vida
    def run(self) -> ParaBackResult:
        self._start = time.perf_counter()
        self._next_share = self._start + self.config.share_interval
        self.state = OrchestratorState.WAITING_BASE
        hard_exit = False
        try:
            self.launch_workers()
            try:
                self._event_loop()
            except (KeyboardInterrupt, SystemExit) as exc:
                # SIGTERM (exit_on_sigterm) o Ctrl-C: como un límite de tiempo. Los trabajadores
                # interrumpen sus solves y cierran sus backends; se les espera stop_grace como
                # mucho y el resultado parcial se escribe (``interrupted``). Una segunda señal
                # durante la espera termina en seco, sin esperar a nadie.
                log.warning("señal recibida (%s): parando a los trabajadores", type(exc).__name__)
                self._send_stop(StopReason.SIGNAL)
                try:
                    self._event_loop()
                except (KeyboardInterrupt, SystemExit):
                    hard_exit = True
                    raise
        finally:
            if not hard_exit:
                self.wait_workers()
            self.tracer.close()
        return self.result()

    def _deadline_reached(self) -> bool:
        if self.config.time_limit is None:
            return False
        if self.config.start_time is not None:
            # Desde el arranque en el lanzador: incluye lo previo a mpirun.
            return time.time() - self.config.start_time >= self.config.time_limit
        return (time.perf_counter() - self._start) >= self.config.time_limit

    def _abandon_unresponsive(self) -> bool:
        """Tras STOP + ``stop_grace`` s, deja de esperar a quien no terminó. ``True`` si abandonó a alguno."""
        if not self._stop_sent or time.perf_counter() - self._stop_sent_at < self.config.stop_grace:
            return False
        if self.abandoned:
            return True
        for rank, worker in self.workers.items():
            if not worker.finished:
                worker.error = f"abandonado: no terminó {self.config.stop_grace:g} s después de STOP ({self._stop_reason})"
                self.abandoned.append(rank)
                log.warning("trabajador %d (%s) %s", rank, worker.spec, worker.error)
        return bool(self.abandoned)

    def _all_finished(self) -> bool:
        return all(w.finished for w in self.workers.values())

    def _event_loop(self) -> None:
        while not self._all_finished():
            message = self.comm.wait(timeout=self._wait_timeout())
            if message is not None:
                self._messages += 1
                started = time.perf_counter()
                self.handle_safely(message)
                self._busy_time += time.perf_counter() - started
            self._share_if_due()
            if not self._stop_sent and self._deadline_reached():
                self._send_stop(StopReason.TIME_LIMIT)
            if self._abandon_unresponsive():
                break
            self.snapshot()
        self.state = OrchestratorState.FAILED if self._error else OrchestratorState.DONE

    # ------------------------------------------------------------------ reenvío periódico
    def _periodic(self) -> bool:
        return self.config.share_facts and self.config.share_interval > 0

    def _wait_timeout(self) -> float:
        """Espera máxima por un mensaje: ``poll_interval``, o menos si antes toca un reenvío periódico."""
        timeout = self.config.poll_interval
        if self._periodic():
            timeout = min(timeout, max(0.0, self._next_share - time.perf_counter()))
        return timeout

    def _share_if_due(self) -> None:
        """Con ``share_interval > 0``: cada ``share_interval`` s, reenvía lo acumulado desde la vez anterior."""
        if not self._periodic():
            return
        started = time.perf_counter()
        if started < self._next_share:
            return
        self._next_share = started + self.config.share_interval
        if not self._unshared or self._stop_sent or self.knowledge is None:
            return
        self._unshared = False
        source, hint = self._pending_hint if self._pending_hint is not None else (None, None)
        self._pending_hint = None
        self._forward(source, hint)
        self._busy_time += time.perf_counter() - started

    def snapshot(self, force: bool = False) -> bool:
        """Instantánea de ``stats_json`` con ``status = "running"`` cada :data:`SNAPSHOT_INTERVAL` s.

        Si el trabajo muere (un rango sin memoria o con un fallo de segmentación tumba todo
        el trabajo MPI; un ``scancel``), el archivo final no llega a escribirse: la instantánea
        deja al menos qué trabajadores había, qué base tenían y cuántos hechos llevaban. La
        sobrescribe el resultado final (``paraback.cli.report``). ``True`` si escribió.
        """
        path = self.config.stats_json
        if path is None:
            return False
        now = time.perf_counter()
        if not force and now - self._snapshot_at < SNAPSHOT_INTERVAL:
            return False
        self._snapshot_at = now
        data = self.result().to_dict()
        data["status"] = "running"
        data["config"] = self.config.to_dict()
        try:
            # Escritura atómica (archivo temporal + rename): quien lea la instantánea mientras se
            # escribe (failures.py en el cluster, un test) nunca ve un JSON a medias.
            temp = f"{path}.tmp"
            with open(temp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(temp, path)
        except OSError as exc:
            log.warning("no se pudo escribir la instantánea de %s: %s", path, exc)
            return False
        return True

    def _send_stop(self, reason: StopReason) -> None:
        if self._stop_sent:
            return
        self._stop_sent = True
        self._stop_reason = reason
        self._stop_sent_at = time.perf_counter()
        self.state = OrchestratorState.STOPPING
        log.info("STOP a todos los trabajadores: %s", reason)
        self.tracer.stop(reason)
        self.comm.broadcast(protocol.stop(self.comm.rank, reason))
        self.snapshot(force=True)  # lo que se sabía al parar, por si el cierre no termina

    def _retire(self, worker: WorkerRecord) -> None:
        """``ERROR`` no fatal: el solver del trabajador murió (señal, memoria); o su infactibilidad en coma
        flotante resultó falsa (:meth:`_reject_float_infeasibility`). Se sigue sin él (motivo en ``worker.error``).

        Nada de lo que demostró queda en duda (sus hechos pasaron la comprobación exacta o son
        de un solver exacto): sólo deja de aportar. Si no queda ningún trabajador, la ejecución
        falla con todos los motivos: una ejecución sin nadie no es un resultado.
        """
        if self.knowledge is not None:
            self.knowledge.clear_intent(worker.rank)
        self.retired.append(worker.rank)
        log.warning("trabajador %d (%s) se retira: %s", worker.rank, worker.spec, worker.error)
        if all(w.error is not None for w in self.workers.values()):
            reasons = "; ".join(f"{rank}: {w.error}" for rank, w in sorted(self.workers.items()))
            self._fail(f"todos los trabajadores se retiraron: {reasons}")

    def _fail(self, message: str) -> None:
        log.error("ParaBack falló: %s", message)
        if self._error is None:
            self._error = message
        self._send_stop(StopReason.ERROR)

    # ------------------------------------------------------------------ mensajes
    def handle_safely(self, message: protocol.Message) -> None:
        """:meth:`handle` que convierte un error interno en un ``FAILED`` con la traza.

        Una excepción del rango 0 mataría el proceso sin escribir resultados y ``mpirun``
        abortaría el trabajo entero sin dejar rastro del porqué; así los trabajadores
        reciben ``STOP``, el resultado parcial se escribe y ``stats.json`` lleva el error.
        """
        try:
            self.handle(message)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 - cualquier fallo interno debe quedar registrado
            self._fail(f"error interno del orquestador con un mensaje {message.kind.value} del rango {message.sender}: "
                       f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")

    def handle(self, message: protocol.Message) -> None:
        worker = self.workers.get(message.sender)
        if worker is None:
            self._fail(f"mensaje de un rango desconocido: {message.sender}")
            return
        kind = message.kind
        if kind is protocol.MessageKind.HELLO:
            self._on_hello(worker, protocol.read_hello(message))
        elif kind is protocol.MessageKind.BASE:
            self._on_base(worker, protocol.read_base(message))
        elif kind is protocol.MessageKind.FACTS:
            if self.knowledge is not None:
                self.knowledge.clear_intent(worker.rank)
            self._on_facts(worker, protocol.read_facts(message))
        elif kind is protocol.MessageKind.INTENT:
            self._on_intent(worker, protocol.read_intent(message))
        elif kind is protocol.MessageKind.DONE:
            if self.knowledge is not None:
                self.knowledge.clear_intent(worker.rank)
            done = protocol.read_done(message)
            worker.done_status = done.status
            worker.stats = WorkerStats.from_dict(done.stats)
        elif kind is protocol.MessageKind.ERROR:
            worker.error = protocol.read_error(message)
            if protocol.is_fatal_error(message):
                self._fail(f"trabajador {worker.rank} ({worker.spec}): {worker.error}")
            else:
                self._retire(worker)
        else:
            self._fail(f"mensaje inesperado {kind.value} del trabajador {worker.rank}")

    def _on_hello(self, worker: WorkerRecord, hello: protocol.Hello) -> None:
        worker.spec = hello.spec
        worker.solver = hello.solver
        worker.capabilities = dict(hello.capabilities)
        log.info("trabajador %d: %s", worker.rank, worker.spec)
        if hello.replaces_spec is not None:
            self._on_replacement(worker, hello.replaces_spec, hello.replaces_reason or "")

    def _on_replacement(self, worker: WorkerRecord, previous: str, reason: str) -> None:
        """Sustitución en el sitio: el nuevo trabajador del rango recibe todo lo que ya se sabe.

        Su antecesor no admitía la instancia (:class:`~guroback.backend.Incompatible`) y no se
        retiró: el rango sigue con ``worker.spec``. Se le vuelve a dar el z* y la x* (si ya se
        conocen y su antecesor no había resuelto la base) y todos los hechos.
        """
        worker.replaced = f"{previous}: {reason}"
        log.warning("trabajador %d: %s no admite la instancia (%s); sigue como %s", worker.rank, previous, reason, worker.spec)
        knowledge = self.knowledge
        if knowledge is None or self._stop_sent:
            return
        knowledge.restart(worker.rank)
        if self.config.share_base_optimum and worker.base_status is None and knowledge.optimum is not None:
            self.comm.send(worker.rank, protocol.base_hint(self.comm.rank, knowledge.optimum, knowledge.knowledge.values))
        if self.config.share_facts:
            if self._periodic():
                self._unshared = True
            else:
                self._forward(None, None)

    def _on_base(self, worker: WorkerRecord, base: protocol.Base) -> None:
        status = base.status
        worker.base_status = status
        worker.optimum = None if base.optimum is None else normalize_objective_value(base.optimum)
        values = base.values
        if status != BaseStatus.FEASIBLE:
            # Un trabajador no pudo resolver la base (infactible/límite): no aporta hechos.
            if status == BaseStatus.INFEASIBLE:
                self._on_infeasible_base(worker)
            return
        if not self._settle_infeasible_claims(worker):
            return
        if self.is_optimization is None:
            self.is_optimization = worker.optimum is not None
        if self.knowledge is None:
            self.knowledge = GlobalKnowledge(values, worker.optimum)
            self.state = OrchestratorState.PROBING
            self._base_worker = worker.rank
            log.info("valores base fijados por el trabajador %d (z* = %s, %d variables)", worker.rank, worker.optimum, len(values))
            if self.config.base_only:
                # Sólo se quería la base: los demás interrumpen su búsqueda (o su verificación) y terminan.
                self._send_stop(StopReason.BASE)
                return
            if self.config.share_base_optimum and worker.optimum is not None:
                for rank, other in self.workers.items():
                    if rank != worker.rank and other.base_status is None:
                        # Con x*: el receptor sólo tiene que verificarla, no buscar otra de coste z*.
                        self.comm.send(rank, protocol.base_hint(self.comm.rank, worker.optimum, values))
            return
        if worker.optimum != self.knowledge.optimum:
            self._fail(
                f"óptimos distintos: trabajador {worker.rank} ({worker.spec}) informa z*={worker.optimum} "
                f"y el conocimiento global usa z*={self.knowledge.optimum}; revise las tolerancias"
            )
            return
        self.knowledge.add_variables(values)
        # Otra solución óptima: toda variable en la que difiere queda refutada.
        try:
            _, refuted = self.knowledge.merge(worker.rank, [], [], values)
        except InconsistentKnowledge as exc:
            self._fail(str(exc))
            return
        worker.refuted_contributed += len(refuted)
        self.tracer.facts(worker.rank, None, True, [], refuted)
        self._after_new_facts(worker.rank)

    # ------------------------------------------------------------------ infactibilidad de la base
    def _solver_of(self, worker: WorkerRecord) -> Optional[str]:
        """El solver del rango: el de su ``HELLO`` (con la sustitución en el sitio) o el de su especificación."""
        if worker.solver:
            return worker.solver
        specs = self.config.workers
        return specs[worker.rank - 1].solver if 0 < worker.rank <= len(specs) else None

    def _proves_infeasibility(self, worker: WorkerRecord) -> bool:
        """Su ``INFEASIBLE`` es una prueba: un solver exacto (no en coma flotante ni pasivo)."""
        solver = self._solver_of(worker)
        return solver is not None and solver not in FLOAT_SOLVERS and solver not in PASSIVE_WORKERS

    def _can_prove_infeasibility(self) -> bool:
        """¿Hay en el portfolio algún solver cuyo ``INFEASIBLE`` sea una prueba?"""
        return any(self._proves_infeasibility(w) for w in self.workers.values())

    def _settles_feasibility(self, worker: WorkerRecord) -> bool:
        """Su base factible es cierta: la de un solver exacto, o la de uno en coma flotante comprobada
        con enteros antes de enviarla (``exact_check``: si no lo era, la base llega ``invalid``)."""
        solver = self._solver_of(worker)
        return self.config.exact_check or (solver is not None and solver not in FLOAT_SOLVERS)

    def _infeasibility_proven(self) -> bool:
        """La ejecución termina ``infeasible``: lo demostró un solver exacto o, sin ninguno en el
        portfolio (GuroBack solo), lo dijo uno en coma flotante y nadie lo desmintió."""
        claims = [self.workers[rank] for rank in self._infeasible_claims]
        return any(self._proves_infeasibility(w) for w in claims) or (bool(claims) and not self._can_prove_infeasibility())

    def _on_infeasible_base(self, worker: WorkerRecord) -> None:
        """Base ``infeasible`` de ``worker``.

        La de un solver exacto para a todos: no hay backbone que extraer (sin esto esperaban al
        límite de tiempo: base25, 9 ejecuciones de 3600 s por una infactibilidad demostrada en
        segundos; y cada DEC infactible, porque Cadiback no demuestra la de even-colouring). La de
        Gurobi, SCIP o ``lp`` no es una prueba (Ronda 1 del histórico: 3 falsas en mirador-node02 y
        1 153 "infactibles" que nadie confirmó en 300 s): espera a que un exacto la confirme, y una
        solución comprobada la desmiente y retira a su trabajador (:meth:`_reject_float_infeasibility`).
        Sólo sin ningún exacto en el portfolio basta la suya.
        """
        if self.knowledge is not None:
            # Ya hay una base factible.
            base = self.workers.get(self._base_worker) if self._base_worker is not None else None
            if not self._proves_infeasibility(worker) and base is not None and self._settles_feasibility(base):
                self._reject_float_infeasibility(worker, base)
            else:
                self._fail_base_contradiction(worker)
            return
        self._infeasible_claims.append(worker.rank)
        if self._proves_infeasibility(worker) or not self._can_prove_infeasibility():
            self._send_stop(StopReason.INFEASIBLE)

    def _settle_infeasible_claims(self, worker: WorkerRecord) -> bool:
        """Antes de aceptar la base factible de ``worker``, las infactibilidades anteriores: las de
        solvers en coma flotante quedan desmentidas si esta base es cierta; las de un exacto (o frente
        a una base sin comprobar) son una contradicción. ``False`` si la ejecución falla."""
        if not self._infeasible_claims:
            return True
        claims = [self.workers[rank] for rank in self._infeasible_claims]
        if any(self._proves_infeasibility(w) for w in claims) or not self._settles_feasibility(worker):
            self._fail_base_contradiction(worker)
            return False
        self._infeasible_claims.clear()
        for claim in claims:
            self._reject_float_infeasibility(claim, worker)
        return True

    def _reject_float_infeasibility(self, worker: WorkerRecord, witness: WorkerRecord) -> None:
        """La infactibilidad en coma flotante de ``worker`` es falsa: ``witness`` tiene una solución cierta."""
        worker.error = (f"su infactibilidad ({self._solver_of(worker)}, en coma flotante) es falsa: el trabajador "
                        f"{witness.rank} ({witness.spec}) tiene una solución comprobada")
        self._retire(worker)

    def _fail_base_contradiction(self, worker: WorkerRecord) -> None:
        """Un trabajador demostró infactibilidad y otro encontró solución: no se elige ganador.

        Con la base comprobada, sólo pasa si un solver exacto declara infactible una instancia que
        no lo es (o sin ``exact_check``: un solver acepta una solución que viola una restricción
        dentro de su tolerancia, como la comprobación relativa de SCIP con coeficientes grandes,
        ``REPORTE_SCIP.md`` §7.2); tomar esa base daría un backbone falso.
        """
        infeasible = [w.rank for w in self.workers.values() if w.base_status == BaseStatus.INFEASIBLE]
        feasible = [w.rank for w in self.workers.values() if w.base_status == BaseStatus.FEASIBLE]
        self._fail(
            f"estado base contradictorio: los trabajadores {infeasible} demuestran infactibilidad y "
            f"los trabajadores {feasible} encuentran solución (último: {worker.rank}, {worker.spec}); "
            "revise las tolerancias de factibilidad (FeasibilityTol)"
        )

    def _on_facts(self, worker: WorkerRecord, facts: protocol.Facts) -> None:
        if self.knowledge is None:
            self._fail(f"FACTS del trabajador {worker.rank} antes de cualquier BASE")
            return
        if self.config.base_only:
            return  # sólo se quería la base: lo que un trabajador sondee antes de recibir STOP no cuenta
        worker.facts_received += 1
        reported_backbone = facts.backbone
        reported_refuted = facts.refuted
        try:
            new_backbone, new_refuted = self.knowledge.merge(
                worker.rank,
                reported_backbone,
                reported_refuted,
                facts.solution if self.config.share_solutions else None,
            )
        except InconsistentKnowledge as exc:
            self._fail(f"trabajador {worker.rank} ({worker.spec}): {exc}")
            return
        worker.backbone_contributed += len(new_backbone)
        worker.refuted_contributed += len(new_refuted)
        if self.tracer.enabled:
            # Repetidos: hechos que el trabajador demostró y otro ya había aportado (trabajo duplicado).
            known, fresh = self.knowledge.knowledge.values, set(new_backbone) | set(new_refuted)
            self.tracer.facts(
                worker.rank, facts.sent_at, False, new_backbone, new_refuted,
                dup_backbone=sum(1 for n, _ in reported_backbone if n in known and n not in fresh),
                dup_refuted=sum(1 for n in reported_refuted if n in known and n not in fresh),
            )
        hint = facts.solution if self.config.share_hints else None
        self._after_new_facts(worker.rank, hint)

    def _on_intent(self, worker: WorkerRecord, chunk: List[str]) -> None:
        """Un trabajador empieza a sondear ``chunk``: se cuenta el solapamiento y se avisa a los demás."""
        if self.knowledge is None:
            return
        overlaps = self.knowledge.note_intent(worker.rank, chunk)
        self._intents += 1
        self._intent_overlaps += 1 if overlaps else 0
        self.tracer.intent(worker.rank, chunk, overlaps)
        for rank, other in self.workers.items():
            if rank != worker.rank and not other.finished:
                elsewhere = self.knowledge.probing_except(rank)
                self._send_update(rank, protocol.update(self.comm.rank, probing_elsewhere=elsewhere), 0)

    def _after_new_facts(self, source: int, hint: Optional[Mapping[str, bool]] = None) -> None:
        assert self.knowledge is not None
        if self.config.share_facts:
            if self._periodic():
                # Se reenvían en el próximo tic de _share_if_due (con la última pista recibida).
                self._unshared = True
                if hint:
                    self._pending_hint = (source, dict(hint))
            else:
                self._forward(source, hint)
        if self._complete_at is None and self.knowledge.is_complete():
            # ``complete_time``: el reloj de ``wall_time`` sigue hasta que terminan todos los
            # trabajadores, que tras STOP pueden tardar hasta ``stop_grace`` s más.
            self._complete_at = time.perf_counter()
            if self.config.stop_when_complete:
                self._send_stop(StopReason.COMPLETE)

    def _forward(self, source: Optional[int], hint: Optional[Mapping[str, bool]]) -> None:
        """Envía a cada trabajador activo los hechos que aún no conoce (y la pista, salvo a quien la aportó)."""
        assert self.knowledge is not None
        for rank, worker in self.workers.items():
            if worker.finished:
                continue
            backbone, refuted = self.knowledge.pending_for(rank)
            if backbone or refuted or (hint and rank != source):
                sent_at = now() if self.tracer.enabled else None
                # Con intenciones, cada UPDATE lleva además lo que los demás sondean ahora
                # (un FACTS acaba de cerrar el sondeo anunciado por ``source``).
                elsewhere = self.knowledge.probing_except(rank) if self.config.announce_intents else None
                count = len(backbone) + len(refuted)
                self._send_update(
                    rank, protocol.update(self.comm.rank, backbone, refuted, hint if rank != source else None, sent_at, elsewhere), count
                )
                self.tracer.update_sent(rank, count)

    def _send_update(self, rank: int, message: protocol.Message, facts: int) -> None:
        self._updates_sent += 1
        self._update_facts += facts
        self.comm.send(rank, message)

    # ------------------------------------------------------------------ resultado
    def result(self) -> ParaBackResult:
        wall = time.perf_counter() - self._start
        status: RunStatus
        if self._error is not None:
            status = RunStatus.FAILED
        elif self.knowledge is None:
            # Basta una demostración de infactibilidad: si otro trabajador hubiera encontrado
            # solución, ``_fail_base_contradiction`` ya habría fallado. La de un solver en coma
            # flotante sin confirmar no basta (:meth:`_on_infeasible_base`).
            status = RunStatus.INFEASIBLE if self._infeasibility_proven() else RunStatus.INTERRUPTED
        elif self.knowledge.is_complete() or self.config.base_only:
            status = RunStatus.COMPLETE  # con --base-only la tarea era la base
        else:
            status = RunStatus.INTERRUPTED
        knowledge = self.knowledge
        complete_time = None if self._complete_at is None else self._complete_at - self._start
        if complete_time is None and knowledge is not None and knowledge.is_complete():
            complete_time = wall  # completo sin hechos nuevos (una instancia sin variables)
        return ParaBackResult(
            status=status,
            is_optimization=bool(self.is_optimization),
            optimum=None if knowledge is None else knowledge.optimum,
            backbone=[] if knowledge is None else knowledge.backbone(),
            refuted=[] if knowledge is None else knowledge.refuted(),
            unknown=[] if knowledge is None else knowledge.unknown(),
            workers=dict(self.workers),
            wall_time=wall,
            complete_time=complete_time,
            messages=self._messages,
            error=self._error,
            abandoned=list(self.abandoned),
            retired=list(self.retired),
            max_rss_mb=max_rss_mb(),
            intents=self._intents,
            intent_overlaps=self._intent_overlaps,
            updates_sent=self._updates_sent,
            update_facts=self._update_facts,
            busy_time=self._busy_time,
            base_worker=self._base_worker,
            base_values={} if knowledge is None else dict(knowledge.knowledge.values),
        )


__all__ = ["GlobalKnowledge", "Orchestrator", "OrchestratorState", "ParaBackResult", "WorkerRecord"]

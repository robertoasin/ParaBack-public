"""Trabajador de ParaBack: un extractor de backbones que publica y absorbe hechos.

Un :class:`Worker` construye el backend de su :class:`~paraback.config.WorkerSpec`
y ejecuta :class:`guroback.extractor.BackboneExtractor` con un
:class:`WorkerCoordinator`, que implementa los puntos de enganche del extractor
sobre un :class:`~paraback.communicator.Communicator`:

* ``on_base_solution`` → ``BASE`` (``z*`` y la solución base, por nombre).
* ``on_probe_result`` → ``FACTS`` (backbones demostrados, candidatos refutados
  y, opcionalmente, la solución óptima del sondeo).
* ``before_probe`` → procesa los ``UPDATE`` recibidos (fusiona en el
  :class:`~guroback.extractor.BackboneKnowledge` local, que el extractor
  traslada al backend fijando los backbones ajenos) y obedece ``STOP``.
* ``on_inconclusive`` → con sondeos por rebanadas de tiempo, sincroniza y
  reintenta (el extractor aplaza el chunk) salvo que haya llegado ``STOP``.

El coordinador compone las piezas de :mod:`paraback.coordination` (nombres, z*
relevado, sondeo en curso, comprobación exacta y mensajes salientes); la traducción
``VarId ↔ nombre`` es necesaria porque cada backend numera sus variables a su manera y
en el cable sólo viajan nombres. :class:`Worker` hereda el protocolo de
:class:`~paraback.worker_base.BaseWorker`, que comparte con el trabajador Cadiback.

Con ``config.interrupt_on_stop`` y un backend con ``supports_interrupt``, el
trabajador envuelve su canal en un :class:`ListeningCommunicator`: un hilo
recibe los mensajes durante los sondeos y, si llega ``STOP``, llama a
``backend.interrupt()`` (``docs/PLAN_PARABACK.md`` §7.3). Los ``UPDATE`` quedan en
búfer y los fusiona el hilo principal en ``before_probe``, de modo que el
:class:`~guroback.extractor.BackboneKnowledge` nunca cambia a mitad de un sondeo.

Con ``config.live_updates`` el mismo hilo usa además cada ``UPDATE`` **en el
sondeo en curso**, sin esperar al siguiente (``docs/PLAN_PARABACK.md`` §7.4):

* los backbones ajenos se pasan a ``backend.add_live_fixes`` (si el backend lo
  soporta), que los impone dentro del ``solve`` en marcha;
* si los hechos ajenos ya deciden el chunk que se está sondeando (todo él
  backbone, o alguna variable refutada), el sondeo se interrumpe: su
  resultado ya se conoce y el extractor sigue con el siguiente chunk.
"""
from __future__ import annotations

import importlib
import logging
from typing import AbstractSet, Callable, FrozenSet, Mapping, Optional, Sequence, Tuple, Type

from guroback import get_backend_class
from guroback.backend import SolveResult, SolverBackend, VarId
from guroback.extractor import BackboneExtractor, BackboneKnowledge, BackboneResult, Coordinator, RunStatus
from guroback.opb import OPBInstance
from guroback.rotation import ModelRotation, make_rotation

from . import protocol
from .communicator import Communicator
from .config import NATIVE_WORKERS, ParaBackConfig, WorkerSpec
from .coordination import BaseHintState, ExactValidation, FactPublisher, Hint, LiveProbeGuard, NameMap
from .listener import ListeningCommunicator, trace_update
from .protocol import BaseStatus
from .stats import Stopwatch
from .trace import Outcome, Tracer, now
from .worker_base import BaseWorker

log = logging.getLogger("paraback.worker")

#: Clase de backend de un trabajador a partir de su especificación. Por defecto se busca
#: ``spec.solver`` en el registro (:func:`default_backend_factory`); los tests inyectan
#: dobles sin tocar el registro global.
BackendFactory = Callable[[WorkerSpec], Type[SolverBackend]]


def default_backend_factory(spec: WorkerSpec) -> Type[SolverBackend]:
    """La clase registrada para ``spec.solver`` (:data:`guroback.BACKENDS`)."""
    return get_backend_class(spec.solver)


class WorkerCoordinator(Coordinator):
    """Los puntos de enganche del extractor sobre el protocolo de ParaBack.

    Compone :class:`~paraback.coordination.NameMap`, :class:`~paraback.coordination.BaseHintState`,
    :class:`~paraback.coordination.LiveProbeGuard`, :class:`~paraback.coordination.ExactValidation`
    y :class:`~paraback.coordination.FactPublisher`. Sus contadores y banderas se exponen
    como atributos (``stop_requested``, ``live_fixes``…) para quien los leía antes.
    """

    def __init__(
        self,
        comm: Communicator,
        config: ParaBackConfig,
        spec: WorkerSpec,
        tracer: Optional[Tracer] = None,
        hint: Optional[BaseHintState] = None,
    ) -> None:
        self.config = config
        self.spec = spec
        self.rank = comm.rank
        self.tracer = tracer if tracer is not None else Tracer(None, comm.rank)
        #: ``True`` si un hilo de escucha ya traza la llegada de los ``UPDATE``.
        self.listener_traces = False
        self.hint = hint if hint is not None else BaseHintState()
        self.guard = LiveProbeGuard()
        self.validation = ExactValidation()
        self.publisher = FactPublisher(comm, comm.rank, self.tracer)
        #: Nombres de las variables del backend (desde la base propia).
        self.names: Optional[NameMap] = None
        #: Lo que otros trabajadores sondean ahora, según el último ``UPDATE`` (``announce_intents``).
        self.probing_elsewhere: FrozenSet[str] = frozenset()
        self.updates_received = 0
        #: Coste de la comunicación en el hilo principal (``WorkerStats.send_time``/``absorb_time``).
        self.send_watch = Stopwatch()
        self.absorb_watch = Stopwatch()
        self._comm = comm
        self._probes_since_sync = 0
        self._backend: Optional[SolverBackend] = None
        self._probe_id = 0
        self._probe_started = 0.0

    # ------------------------------------------------------------------ canal
    @property
    def comm(self) -> Communicator:
        return self._comm

    @comm.setter
    def comm(self, comm: Communicator) -> None:
        """El trabajador lo cambia por su hilo de escucha al arrancarlo."""
        self._comm = comm
        self.publisher.comm = comm

    # ------------------------------------------------------------------ estado expuesto
    @property
    def stop_requested(self) -> bool:
        return self.hint.stop_requested

    @stop_requested.setter
    def stop_requested(self, value: bool) -> None:
        self.hint.stop_requested = value

    @property
    def base_solved(self) -> bool:
        return self.hint.base_solved

    @property
    def pending_base_hint(self) -> Optional[Hint]:
        return self.hint.pending

    @property
    def live_fix_enabled(self) -> bool:
        return self.guard.live_fix_enabled

    @property
    def live_fixes(self) -> int:
        return self.guard.live_fixes

    @property
    def probes_decided(self) -> int:
        return self.guard.probes_decided

    @property
    def invalid_solutions(self) -> int:
        return self.validation.invalid_solutions

    @property
    def base_invalid(self) -> Optional[str]:
        return self.validation.base_invalid

    @property
    def facts_sent(self) -> int:
        return self.publisher.facts_sent

    @property
    def facts_count(self) -> int:
        return self.publisher.facts_count

    # ------------------------------------------------------------------ mensajes entrantes
    def _absorb(self, knowledge: BackboneKnowledge, backend: SolverBackend) -> None:
        names = self.names
        assert names is not None, "sólo se absorben hechos tras la base propia"
        with self.absorb_watch:
            self._absorb_messages(knowledge, backend, names)

    def _absorb_messages(self, knowledge: BackboneKnowledge, backend: SolverBackend, names: NameMap) -> None:
        for message in self._comm.poll():
            if message.kind is protocol.MessageKind.STOP:
                self.hint.stop_requested = True
            elif message.kind is protocol.MessageKind.UPDATE:
                self.updates_received += 1
                update = protocol.read_facts(message)
                if not self.listener_traces:
                    trace_update(self.tracer, update)
                knowledge.merge(names.pairs_by_id(update.backbone), names.known_ids(update.refuted))
                if update.probing_elsewhere is not None:
                    self.probing_elsewhere = frozenset(update.probing_elsewhere)
                if update.solution and backend.supports_solution_hint:
                    backend.add_solution_hint(names.by_id(update.solution))
            elif message.kind is protocol.MessageKind.BASE_HINT:
                pass  # tardío: esta base ya se resolvió (o llegó tras STOP); no aporta nada
            else:
                log.warning("trabajador %d: mensaje inesperado %s", self.rank, message.kind.value)

    # ------------------------------------------------------------------ z* ajeno (base)
    def note_base_hint(self, optimum: float, values: Optional[Mapping[str, bool]] = None) -> bool:
        """Registra un z* ajeno (y su x*); devuelve si conviene interrumpir el solve en curso."""
        return self.hint.note(optimum, values)

    def take_pending_hint(self) -> Optional[Hint]:
        """Consume ``(z*, x*)`` ajenos pendientes, o ``None`` si no hay o si hay ``STOP`` pedido."""
        return self.hint.take()

    # ------------------------------------------------------------------ hechos ajenos en el sondeo en curso
    def note_live_update(self, update: protocol.Facts, backend: SolverBackend) -> None:
        """Usa un ``UPDATE`` recién llegado en el sondeo en curso (hilo de escucha).

        No toca el :class:`BackboneKnowledge` (eso sigue en ``before_probe``).
        """
        self.guard.note_update(update, backend)

    def enforce_decided(self, backend: SolverBackend) -> None:
        """Tic del hilo de escucha: reinsiste en interrumpir un sondeo ya decidido."""
        self.guard.enforce(backend)

    def busy_elsewhere(self, knowledge: BackboneKnowledge) -> AbstractSet[VarId]:
        if not self.probing_elsewhere or self.names is None:
            return frozenset()
        return frozenset(self.names.known_ids(self.probing_elsewhere))

    def on_probe_start(self, chunk: Sequence[VarId], whole: bool = False) -> None:
        assert self.names is not None
        names = self.names.names(chunk)
        if self.config.announce_intents:
            self.publisher.intent(names)
        self.guard.start(names, whole)
        self._probe_id += 1
        self._probe_started = now()

    def on_probe_end(self, chunk: Sequence[VarId]) -> None:
        decided = self.guard.end()
        if self.tracer.enabled and self.names is not None:
            self.tracer.probe(self._probe_id, self.names.names(chunk), self._probe_started, now(), decided)

    # ------------------------------------------------------------------ ganchos del extractor
    def on_base_solution(self, knowledge: BackboneKnowledge, backend: SolverBackend) -> None:
        self._backend = backend
        names = NameMap.of(backend, knowledge.variables)
        self.names = names
        self.guard.set_names(names.ids())
        self.hint.base_solved = True
        values = names.by_name(knowledge.values)
        self.validation.prepare(self.config, backend, self.rank)
        reason = self.validation.check_base(values, knowledge.optimum)
        if reason is not None:
            # La solución base es la referencia con la que se refuta todo: si no es
            # exactamente válida, este trabajador no aporta nada fiable y se retira
            # (el orquestador ignora una base no "feasible"; otro puede aportar la suya).
            log.warning("trabajador %d: solución base no válida en aritmética exacta: %s", self.rank, reason)
            self.publisher.base(BaseStatus.INVALID)
            return
        if self.config.live_updates and backend.supports_live_fix:
            self.guard.live_fix_enabled = backend.enable_live_fixes()
        if self.config.probe_time_slice is not None and backend.supports_time_limit:
            backend.set_time_limit(self.config.probe_time_slice)
        self.publisher.base(BaseStatus.FEASIBLE, knowledge.optimum, values)

    def base_accepted(self) -> bool:
        return self.validation.base_invalid is None

    def shared_instance(self) -> Optional[OPBInstance]:
        """La instancia del comprobador exacto (la del backend si la leyó en Python): la rotación la reutiliza."""
        checker = self.validation.checker
        return None if checker is None else checker.instance

    def on_derived(self, knowledge: BackboneKnowledge, backbone: Sequence[VarId], refuted: Sequence[VarId]) -> None:
        """Hechos sin sondeo (rotación, literales fijados): un FACTS como el de un sondeo."""
        names = self.names
        if names is None or self.validation.base_invalid is not None or not (backbone or refuted):
            return
        with self.send_watch:
            self.publisher.facts([(names.name(v), knowledge.values[v]) for v in backbone], names.names(refuted))

    def validate_solution(self, backend: SolverBackend) -> bool:
        if self.validation.checker is None:
            return True
        return self.validate_assignment({v: backend.solution_value(v) > 0.5 for v in backend.binary_variables()})

    def validate_assignment(self, values: Mapping[VarId, bool]) -> bool:
        if self.validation.checker is None:
            return True
        assert self.names is not None
        return self.validation.accept(self.names.by_name(values), self.rank)

    def on_incumbent(self, knowledge: BackboneKnowledge, refuted: Sequence[VarId], solution: Mapping[VarId, bool]) -> None:
        """Refutadas por una incumbente de ``maxflip``: se envían ya, sin esperar al final de la llamada."""
        names = self.names
        assert names is not None
        with self.send_watch:
            refuted_names = names.names(refuted)
            self.guard.note_own_refuted(refuted_names)
            self.publisher.facts([], refuted_names, names.by_name(solution) if self.config.share_solutions else None)

    def on_probe_stalled(self, knowledge: BackboneKnowledge, chunk: Sequence[VarId]) -> bool:
        self.guard.take_decided()
        self.tracer.outcome(self._probe_id, Outcome.STALLED)
        return not self.hint.stop_requested

    def before_probe(self, knowledge: BackboneKnowledge, backend: SolverBackend) -> bool:
        if self.validation.base_invalid is not None:
            return False
        self._probes_since_sync += 1
        if self._probes_since_sync >= self.config.sync_every or self.hint.stop_requested:
            self._probes_since_sync = 0
            self._absorb(knowledge, backend)
        return not self.hint.stop_requested

    def on_probe_result(
        self,
        knowledge: BackboneKnowledge,
        chunk: Sequence[VarId],
        result: SolveResult,
        proven: Sequence[VarId],
        refuted: Sequence[VarId],
        solution: Mapping[VarId, bool],
    ) -> None:
        names = self.names
        assert names is not None
        share = self.config.share_solutions and result.is_optimal and bool(solution)
        self.tracer.outcome(self._probe_id, Outcome.OPTIMAL if result.is_optimal else Outcome.INFEASIBLE)
        with self.send_watch:
            self.publisher.facts(
                [(names.name(v), knowledge.values[v]) for v in proven],
                names.names(refuted),
                names.by_name(solution) if share else None,
            )

    def on_inconclusive(self, knowledge: BackboneKnowledge, chunk: Sequence[VarId], result: SolveResult) -> bool:
        decided = self.guard.take_decided()
        self.tracer.outcome(self._probe_id, Outcome.DECIDED if decided else Outcome.INCONCLUSIVE)
        # Sin límite por sondeo un resultado inconcluso es un fallo real (límite
        # global del solver o interrupción): se abandona como en el modo secuencial.
        # Salvo si lo interrumpimos nosotros porque otro trabajador ya lo decidió:
        # los hechos que lo deciden están en el búfer y _absorb los fusiona ahora.
        if self.config.probe_time_slice is None and not self.hint.stop_requested and not decided:
            return False
        assert self._backend is not None
        self._absorb(knowledge, self._backend)
        return not self.hint.stop_requested


class Worker(BaseWorker):
    """Trabajador genérico: un :class:`SolverBackend` + :class:`BackboneExtractor` + :class:`WorkerCoordinator`."""

    def __init__(
        self, comm: Communicator, config: ParaBackConfig, spec: WorkerSpec, backend_factory: Optional[BackendFactory] = None
    ) -> None:
        super().__init__(comm, config, spec)
        self.backend_factory: BackendFactory = backend_factory or default_backend_factory
        self.coordinator = WorkerCoordinator(comm, config, spec, self.tracer, self.hint)
        self.result: Optional[BackboneResult] = None
        #: Backend en uso mientras ``run`` está dentro del ``with`` (``None`` antes y después).
        self.backend: Optional[SolverBackend] = None
        self._backend_cls: Optional[Type[SolverBackend]] = None

    # ------------------------------------------------------------------ BaseWorker
    def capabilities(self) -> Mapping[str, bool]:
        self._backend_cls = self.backend_factory(self.spec)
        return self._backend_cls.capabilities()

    def interrupt(self) -> None:
        backend = self.backend
        if backend is not None:
            backend.interrupt()

    def on_update(self, update: protocol.Facts) -> None:
        backend = self.backend
        if backend is not None:
            self.coordinator.note_live_update(update, backend)

    def on_tick_extra(self) -> None:
        backend = self.backend
        if backend is not None and self.config.live_updates:
            self.coordinator.enforce_decided(backend)

    def execute(self) -> RunStatus:
        backend_cls = self._backend_cls
        assert backend_cls is not None
        with backend_cls(self.config.instance, self.spec.params) as backend:
            self.backend = backend
            wants_interrupt = self.config.interrupt_on_stop or self.config.share_base_optimum
            live = self.config.live_updates and (backend.supports_interrupt or backend.supports_live_fix)
            listening = (wants_interrupt and backend.supports_interrupt) or live
            if listening:
                self.coordinator.comm = self.start_listener(updates=live)
                self.coordinator.listener_traces = self.tracer.enabled
            try:
                return self._extract(backend)
            finally:
                # Dentro del ``with``: el hilo no debe llamar a interrupt() sobre un modelo ya liberado.
                self.backend = None
                if listening:
                    self.stop_listener()

    # ------------------------------------------------------------------ extracción
    @staticmethod
    def _adopt_base_hint(backend: SolverBackend, hint: Hint) -> Tuple[float, Optional[dict]]:
        """``(z*, x* por VarId de este backend)``: lo que el extractor verificará en vez de buscar."""
        optimum, by_name = hint
        return optimum, None if by_name is None else NameMap.of(backend).by_id(by_name)

    def _rotation(self) -> ModelRotation:
        """``rotation=auto``: rotar sólo con la instancia ya en memoria (no leerla sólo para eso)."""
        mode = self.spec.rotation
        if mode == "auto":
            return make_rotation("flippable", load=False)
        return make_rotation(mode, load=True)

    def _extract(self, backend: SolverBackend) -> RunStatus:
        order = self.spec.make_order()
        chunks = self.spec.make_chunks()
        rotation = self._rotation()
        # fixed=auto: literales fijados por el solver (si los informa) y propagación PB previa
        # (sólo si la instancia ya está en memoria).
        use_fixed = self.spec.fixed == "auto"
        names = NameMap.of(backend)
        # La base que ya se conoce: la x* del usuario, su z*, o el z*/x* que otro trabajador
        # relevó antes de empezar (interrupt() sin solve en curso no hace nada, así que hay
        # que mirarlo explícitamente en vez de esperar a que un solve inútil termine solo).
        choice = self.initial_base()
        optimum = None if choice is None else choice.optimum
        base_solution = None if choice is None or choice.values is None else names.by_id(choice.values)
        while True:
            extractor = BackboneExtractor(
                backend, optimum=optimum, order=order, chunks=chunks, coordinator=self.coordinator, base_solution=base_solution,
                rotation=rotation, use_fixed=use_fixed, forced_prepass=use_fixed,
            )
            result = extractor.run()
            if result.status is RunStatus.INTERRUPTED and extractor.knowledge is None:
                # ¿Fue un STOP real, o nos interrumpimos a nosotros mismos porque otro
                # trabajador ya resolvió la base? take_hint() prioriza STOP (devuelve None si
                # se pidió), así que reintentar aquí nunca ignora una orden de parada real.
                retry = self.take_hint()
                if retry is not None:
                    optimum = retry.optimum
                    base_solution = None if retry.values is None else names.by_id(retry.values)
                    continue
            break
        coordinator = self.coordinator
        if extractor.knowledge is None:
            # La base no se resolvió: se informa para que el orquestador lo registre.
            coordinator.publisher.base(BaseStatus(str(result.status)))
        stats = self.stats
        stats.solve_count = result.solve_count
        stats.total_solve_time = result.total_solve_time
        stats.probe_count = result.probe_count
        stats.backbone_found = len(result.backbone)
        stats.facts_sent = coordinator.facts_sent
        stats.facts_count = coordinator.facts_count
        stats.updates_received = coordinator.updates_received
        stats.send_time = coordinator.send_watch.total
        stats.absorb_time = coordinator.absorb_watch.total
        stats.live_fixes = coordinator.live_fixes
        stats.probes_decided = coordinator.probes_decided
        stats.invalid_solutions = coordinator.invalid_solutions
        stats.abandoned = sorted(backend.variable_name(v) for v in extractor.abandoned)
        stats.base_invalid = coordinator.base_invalid
        stats.refuted_by_rotation = result.refuted_by_rotation
        stats.fixed_by_solver = result.fixed_by_solver
        stats.forced_prepass = result.forced_prepass
        stats.refuted_by_incumbent = result.refuted_by_incumbent
        stats.maxflip_stalls = result.maxflip_stalls
        stats.native = dict(backend.native_stats)
        self.result = result
        return result.status


def make_worker(
    comm: Communicator, config: ParaBackConfig, spec: WorkerSpec, backend_factory: Optional[BackendFactory] = None
) -> BaseWorker:
    """El trabajador de ``spec``: :class:`Worker` o uno nativo de ``NATIVE_WORKERS`` (p. ej. Cadiback).

    ``backend_factory`` sólo se aplica a :class:`Worker`; un trabajador nativo ejecuta su propio programa.
    """
    target = NATIVE_WORKERS.get(spec.solver)
    worker: BaseWorker
    if target is None:
        worker = Worker(comm, config, spec, backend_factory)
    else:
        module_name, class_name = target.split(":")
        worker = getattr(importlib.import_module(module_name), class_name)(comm, config, spec)
        assert isinstance(worker, BaseWorker)
    worker.substitute_factory = backend_factory  # para su sustituto en el sitio, si lo necesita
    return worker


__all__ = [
    "BackendFactory",
    "ListeningCommunicator",
    "Worker",
    "WorkerCoordinator",
    "default_backend_factory",
    "make_worker",
]

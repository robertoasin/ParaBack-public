"""Base de los trabajadores que no sondean: derivan hechos de la base y de los hechos ajenos.

Un trabajador pasivo (:class:`PassiveWorker`) no tiene solver de sondeos. Espera la base
(``--base-solution``, ``--optimum`` o el ``BASE_HINT`` de otro trabajador), deriva lo que
pueda de ella, y después vive de los ``UPDATE``: cada lote de hechos ajenos le llega por el
hilo de escucha, se encola, y el hilo principal lo convierte en hechos nuevos que publica
como ``FACTS``. Termina con ``STOP`` (o cuando decide que ya no puede aportar nada más).

Lo que aporta cada subclase: :meth:`PassiveWorker.prepare` (lo que se calcula una vez,
antes de la base), :meth:`PassiveWorker.derive_from_base` y
:meth:`PassiveWorker.derive_from_facts`. Todas las publicaciones salen del hilo principal
(:meth:`publish`); el de escucha sólo encola.
"""
from __future__ import annotations

import abc
import logging
import queue
import threading
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from guroback.exact import ExactChecker
from guroback.extractor import InconsistentKnowledge, RunStatus
from guroback.opb import OPBInstance, ParseCancelled, parse_opb

from . import protocol
from .communicator import Communicator
from .config import ParaBackConfig, WorkerSpec
from .coordination import FactPublisher
from .protocol import BaseStatus
from .stats import Stopwatch
from .worker_base import BaseChoice, BaseWorker

log = logging.getLogger("paraback.passive")

#: Sin sondeos propios: nada que interrumpir salvo la espera; los hechos ajenos se absorben al llegar.
PASSIVE_CAPABILITIES = {"interrupt": True, "time_limit": False, "solution_hint": False, "live_fix": False}


class PassiveWorker(BaseWorker):
    """Trabajador sin sondeos: hechos derivados de la base y de los hechos ajenos."""

    def __init__(self, comm: Communicator, config: ParaBackConfig, spec: WorkerSpec) -> None:
        super().__init__(comm, config, spec)
        self.publisher = FactPublisher(comm, comm.rank, self.tracer)
        self.send_watch = Stopwatch()
        self._updates: "queue.Queue[protocol.Facts]" = queue.Queue()
        self._wake = threading.Event()
        self.instance: Optional[OPBInstance] = None
        self.checker: Optional[ExactChecker] = None
        #: z* (normalizado) y x* por nombre, cuando se conocen.
        self.optimum: Optional[float] = None
        self.x_star: Optional[Dict[str, bool]] = None
        #: Lo decidido (propio y ajeno), para no repetir y para el estado final.
        self.known_backbone: Dict[str, bool] = {}
        self.known_refuted: Set[str] = set()
        #: Con sólo ``--optimum`` (sin x*) este trabajador no manda BASE: hasta que llegue un
        #: UPDATE (prueba de que el orquestador ya tiene conocimiento) los hechos propios esperan.
        self._held: List[Tuple[List[Tuple[str, bool]], List[str], Optional[Dict[str, bool]]]] = []
        self._may_publish = False

    # ------------------------------------------------------------------ lo que aporta cada subclase
    @abc.abstractmethod
    def prepare(self) -> bool:
        """Cálculo previo a la base (``self.instance`` ya leída). ``False``: el trabajador se retira."""

    @abc.abstractmethod
    def derive_from_base(self) -> None:
        """Hechos que salen de la base (``self.optimum``/``self.x_star``); publica con :meth:`publish`."""

    @abc.abstractmethod
    def derive_from_facts(self, backbone: Sequence[Tuple[str, bool]], refuted: Sequence[str]) -> None:
        """Hechos que salen de un lote de hechos ajenos nuevos; publica con :meth:`publish`."""

    def idle(self) -> None:
        """Trabajo propio mientras no llegan hechos (por defecto nada)."""

    def cleanup(self) -> None:
        """Libera los recursos nativos en el hilo del trabajador, al terminar (por defecto nada)."""

    # ------------------------------------------------------------------ BaseWorker
    def capabilities(self) -> Mapping[str, bool]:
        return PASSIVE_CAPABILITIES

    def interrupt(self) -> None:
        self._wake.set()

    def on_update(self, update: protocol.Facts) -> None:
        self.stats.updates_received += 1
        self._updates.put(update)
        self._wake.set()

    def on_base_hint(self, optimum: float, values: Optional[Mapping[str, bool]] = None) -> None:
        super().on_base_hint(optimum, values)
        self._wake.set()

    @property
    def stop_requested(self) -> bool:
        return self.hint.stop_requested

    def execute(self) -> RunStatus:
        try:
            self.instance = parse_opb(self.config.instance, should_stop=lambda: self.hint.stop_requested)
        except ParseCancelled:
            self.send(protocol.base(self.rank, BaseStatus.INTERRUPTED, None, {}))
            return RunStatus.INTERRUPTED
        if self.config.exact_check:
            self.checker = ExactChecker(self.config.instance, self.instance)
        # Los UPDATE sólo se usan al llegar (on_update): no se guardan en el búfer del hilo.
        self.start_listener(base_hints=self.config.share_base_optimum, buffer_updates=False)
        try:
            return self._run_passive()
        finally:
            self.stop_listener()
            self.cleanup()
            self.stats.facts_sent = self.publisher.facts_sent
            self.stats.facts_count = self.publisher.facts_count
            self.stats.send_time = self.send_watch.total

    def _run_passive(self) -> RunStatus:
        if not self.prepare():
            log.warning("trabajador %d (%s) se retira: no puede aportar nada en esta instancia", self.rank, self.solver_name)
            self.send(protocol.base(self.rank, BaseStatus.INTERRUPTED, None, {}))
            return RunStatus.INTERRUPTED
        choice = self._wait_for_base()
        if choice is None:
            self.send(protocol.base(self.rank, BaseStatus.INTERRUPTED, None, {}))
            return RunStatus.INTERRUPTED
        self.optimum, self.x_star = choice.optimum, dict(choice.values) if choice.values else None
        instance = self.instance
        assert instance is not None
        if self.optimum is None and self.x_star is not None and instance.objective is not None:
            # --base-solution sin --optimum: la CLI ya validó que la solución es óptima; su coste es z*.
            bits = bytearray(bool(self.x_star.get(name, False)) for name in instance.names)
            self.optimum = float(instance.objective_value(bits))
        if self.x_star is not None and self.checker is not None:
            reason = self.checker.violation(self.x_star, self.optimum)
            if reason is not None:
                raise InconsistentKnowledge(f"la x* de la base ({choice.source}) no es exactamente válida: {reason}")
        self.hint.base_solved = True
        if self.x_star is not None:
            # La misma base que ya conoce el orquestador (si vino de un BASE_HINT): sólo deja constancia.
            self.send(protocol.base(self.rank, BaseStatus.FEASIBLE, self.optimum, self.x_star))
            self._may_publish = True
        self.derive_from_base()
        while not self.hint.stop_requested:
            self._absorb_updates()
            self._flush_held()
            if self.hint.stop_requested:
                break
            self.idle()
            self._wake.wait(self.config.poll_interval)
            self._wake.clear()
        self._absorb_updates()
        assert self.instance is not None
        decided = len(self.known_backbone) + len(self.known_refuted)
        return RunStatus.COMPLETE if decided >= self.instance.num_vars else RunStatus.INTERRUPTED

    def _wait_for_base(self) -> Optional[BaseChoice]:
        """Espera la base (archivo, opción o pista); ``None`` si llega STOP antes."""
        while not self.hint.stop_requested:
            choice = self.initial_base()
            if choice is not None:
                return choice
            self._wake.wait(self.config.poll_interval)
            self._wake.clear()
        return None

    def _absorb_updates(self) -> None:
        backbone: List[Tuple[str, bool]] = []
        refuted: List[str] = []
        while True:
            try:
                update = self._updates.get_nowait()
            except queue.Empty:
                break
            for name, value in update.backbone:
                if name not in self.known_backbone:
                    self.known_backbone[name] = value
                    backbone.append((name, value))
            for name in update.refuted:
                if name not in self.known_refuted:
                    self.known_refuted.add(name)
                    refuted.append(name)
        if backbone or refuted:
            self.stats.external_facts += len(backbone) + len(refuted)
            self.derive_from_facts(backbone, refuted)

    # ------------------------------------------------------------------ publicación
    def publish(
        self, backbone: Sequence[Tuple[str, bool]], refuted: Sequence[str], solution: Optional[Mapping[str, bool]] = None
    ) -> Tuple[int, int]:
        """Publica lo que aún no se sabía; devuelve cuántos backbones y refutadas nuevas había."""
        new_backbone = []
        for name, value in backbone:
            known = self.known_backbone.get(name)
            if known is None:
                if name in self.known_refuted:
                    raise InconsistentKnowledge(f"{self.solver_name}: {name} resulta backbone pero ya estaba refutada")
                self.known_backbone[name] = value
                new_backbone.append((name, value))
            elif known != value:
                raise InconsistentKnowledge(f"{self.solver_name}: {name} resulta backbone con valor {value} y ya lo era con {known}")
        new_refuted = []
        for name in refuted:
            if name in self.known_backbone:
                raise InconsistentKnowledge(f"{self.solver_name}: {name} resulta refutada pero ya era backbone")
            if name not in self.known_refuted:
                self.known_refuted.add(name)
                new_refuted.append(name)
        if new_backbone or new_refuted or solution is not None:
            if self._may_publish:
                with self.send_watch:
                    self.publisher.facts(new_backbone, new_refuted, solution)
            else:
                self._held.append((new_backbone, new_refuted, None if solution is None else dict(solution)))
        self.stats.backbone_found += len(new_backbone)
        return len(new_backbone), len(new_refuted)

    def _flush_held(self) -> None:
        if not self._may_publish and self.stats.updates_received:
            self._may_publish = True  # hay conocimiento global: ya se puede publicar
        if self._may_publish and self._held:
            held, self._held = self._held, []
            with self.send_watch:
                for backbone, refuted, solution in held:
                    self.publisher.facts(backbone, refuted, solution)

    def refute_by_solution(self, values: Mapping[str, bool], tag: str) -> int:
        """Una solución óptima (comprobada con enteros): refuta toda variable en que difiere de x*."""
        if self.checker is not None:
            reason = self.checker.violation(values, self.optimum)
            if reason is not None:
                self.stats.invalid_solutions += 1
                raise InconsistentKnowledge(f"{self.solver_name}: la solución derivada ({tag}) no es válida: {reason}")
        base = self.x_star or {}
        refuted = [name for name, value in values.items() if base.get(name) is not None and base[name] != value]
        _, new = self.publish([], refuted, values)
        self.stats.refuted_by_model += new
        self.stats.models += 1
        return new


__all__ = ["PASSIVE_CAPABILITIES", "PassiveWorker"]

"""Piezas de la coordinación de un trabajador, separadas para poder probarlas y ampliarlas.

:class:`paraback.worker.WorkerCoordinator` las compone; el trabajador Cadiback
(:mod:`paraback.cadiback_worker`) usa las que le sirven. Cada una tiene una sola
responsabilidad:

* :class:`NameMap`: traducción ``VarId`` ↔ nombre (en el cable sólo viajan nombres).
* :class:`BaseHintState`: el z* y x* de otro trabajador (``BASE_HINT``) frente a ``STOP``.
* :class:`LiveProbeGuard`: qué chunk se está sondeando, los hechos ajenos vistos y la
  interrupción de un sondeo que otro ya decidió (``live_updates``). Es la pieza que
  sabe *qué sondea cada trabajador*, la base de una futura coordinación de intenciones.
* :class:`ExactValidation`: la comprobación exacta de soluciones (``exact_check``).
* :class:`FactPublisher`: los mensajes ``BASE`` y ``FACTS`` y sus contadores.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from guroback.backend import SolverBackend, VarId
from guroback.exact import ExactChecker

from . import protocol
from .communicator import Communicator
from .config import ORCHESTRATOR_RANK, ParaBackConfig
from .protocol import BaseStatus
from .trace import Tracer, now

log = logging.getLogger("paraback.worker")


# ---------------------------------------------------------------------------- nombres
class NameMap:
    """Traducción entre los ``VarId`` de un backend y los nombres de variable del archivo."""

    def __init__(self, names: Mapping[VarId, str]) -> None:
        self._name_of: Dict[VarId, str] = dict(names)
        self._id_of: Dict[str, VarId] = {name: var for var, name in self._name_of.items()}

    @classmethod
    def of(cls, backend: SolverBackend, variables: Optional[Iterable[VarId]] = None) -> "NameMap":
        """Todas las variables binarias de ``backend`` (o las de ``variables``)."""
        vs = backend.binary_variables() if variables is None else variables
        return cls({v: backend.variable_name(v) for v in vs})

    def __contains__(self, name: object) -> bool:
        return name in self._id_of

    def __len__(self) -> int:
        return len(self._name_of)

    def name(self, var: VarId) -> str:
        return self._name_of[var]

    def names(self, variables: Iterable[VarId]) -> List[str]:
        return [self._name_of[v] for v in variables]

    def id_of(self, name: str) -> Optional[VarId]:
        return self._id_of.get(name)

    def ids(self) -> Mapping[str, VarId]:
        return self._id_of

    def by_name(self, values: Mapping[VarId, bool]) -> Dict[str, bool]:
        return {self._name_of[v]: x for v, x in values.items()}

    def by_id(self, values: Mapping[str, bool]) -> Dict[VarId, bool]:
        """Valores por ``VarId`` de los nombres que este backend conoce (los demás se ignoran)."""
        return {var: values[name] for name, var in self._id_of.items() if name in values}

    def pairs_by_id(self, pairs: Iterable[Tuple[str, bool]]) -> List[Tuple[VarId, bool]]:
        return [(self._id_of[n], v) for n, v in pairs if n in self._id_of]

    def known_ids(self, names: Iterable[str]) -> List[VarId]:
        return [self._id_of[n] for n in names if n in self._id_of]


# ---------------------------------------------------------------------------- z* ajeno
Hint = Tuple[float, Optional[Dict[str, bool]]]


class BaseHintState:
    """El z* (y x*) relevado por otro trabajador mientras éste no tiene base propia.

    ``STOP`` siempre gana: tras un ``STOP`` no se consume ningún hint (nunca se reintenta
    la base cuando hay que parar). Los métodos se llaman desde el hilo de escucha y desde
    el principal; cada uno lee o escribe un único atributo (atómico con el GIL), como antes.
    """

    def __init__(self) -> None:
        self.stop_requested = False
        #: ``True`` en cuanto la base propia (o la adoptada) está resuelta: un hint ya no aporta.
        self.base_solved = False
        #: ``(z*, x* por nombre | None)`` pendiente de consumir.
        self.pending: Optional[Hint] = None

    def note(self, optimum: float, values: Optional[Mapping[str, bool]] = None) -> bool:
        """Registra un z* ajeno; devuelve si conviene interrumpir el solve base en curso."""
        if self.base_solved or self.stop_requested:
            return False
        self.pending = (optimum, None if values is None else {str(n): bool(v) for n, v in values.items()})
        return True

    def take(self) -> Optional[Hint]:
        """Consume el hint pendiente, o ``None`` si no hay o si hay ``STOP`` pedido."""
        hint, self.pending = self.pending, None
        return None if self.stop_requested else hint

    def should_reinterrupt(self) -> bool:
        """Hay un hint sin consumir y la base sigue sin resolverse (un interrupt previo pudo perderse)."""
        return self.pending is not None and not self.base_solved and not self.stop_requested


# ---------------------------------------------------------------------------- sondeo en curso
class LiveProbeGuard:
    """El chunk que se sondea y los hechos ajenos que ya lo deciden (``docs/PLAN_PARABACK.md`` §7.4).

    El hilo de escucha llama a :meth:`note_update` y :meth:`enforce`; el principal a
    :meth:`start`, :meth:`end` y :meth:`take_decided`. ``interrupt()`` se pide con el
    cerrojo tomado: así nunca alcanza al sondeo siguiente.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._probing: Optional[FrozenSet[str]] = None
        #: El sondeo en curso sólo está decidido cuando lo está cada variable (``maxflip``).
        self._whole = False
        self._decided = False
        self._seen_backbone: Set[str] = set()
        self._seen_refuted: Set[str] = set()
        #: Nombre → ``VarId`` para las fijaciones vivas (vacío hasta tener base propia).
        self._ids: Mapping[str, VarId] = {}
        #: ``add_live_fixes`` activo en el backend.
        self.live_fix_enabled = False
        self.live_fixes = 0
        self.probes_decided = 0

    @property
    def probing(self) -> Optional[FrozenSet[str]]:
        """Nombres del chunk en sondeo, o ``None`` entre sondeos."""
        return self._probing

    def set_names(self, ids: Mapping[str, VarId]) -> None:
        with self._lock:
            self._ids = ids

    def start(self, names: Iterable[str], whole: bool = False) -> None:
        with self._lock:
            self._probing = frozenset(names)
            self._whole = whole
            self._decided = False

    def note_own_refuted(self, names: Iterable[str]) -> None:
        """Refutadas por el propio sondeo en curso (incumbentes de ``maxflip``): ya no hace falta que otro las decida."""
        with self._lock:
            self._seen_refuted.update(names)

    def end(self) -> bool:
        """El ``solve`` del sondeo devolvió; ``True`` si se interrumpió por estar decidido."""
        with self._lock:
            self._probing = None
            return self._decided

    def take_decided(self) -> bool:
        """``True`` (una vez) si el último sondeo se interrumpió por estar decidido."""
        with self._lock:
            decided, self._decided = self._decided, False
            return decided

    def note_update(self, update: protocol.Facts, backend: SolverBackend) -> None:
        """Hechos ajenos recién llegados: fijaciones vivas y, si deciden el chunk, interrupción."""
        with self._lock:
            self._seen_backbone.update(n for n, _ in update.backbone)
            self._seen_refuted.update(update.refuted)
            ids = self._ids
            fixes = [(ids[n], bool(v)) for n, v in update.backbone if n in ids]
            if fixes and self.live_fix_enabled:
                backend.add_live_fixes(fixes)
                self.live_fixes += len(fixes)
            self._enforce(backend)

    def enforce(self, backend: SolverBackend) -> None:
        """Tic del hilo de escucha: reinsiste en interrumpir un sondeo ya decidido."""
        with self._lock:
            self._enforce(backend)

    def _enforce(self, backend: SolverBackend) -> None:
        # Con el cerrojo tomado. interrupt() sin solve en curso no hace nada, así que se
        # repite en cada tic hasta end(): cubre los hechos que llegan entre before_probe
        # y el arranque del solve.
        probing = self._probing
        if probing is None or not backend.supports_interrupt:
            return
        if not self._decided:
            if self._whole:
                backbone, refuted = self._seen_backbone, self._seen_refuted
                if not all(n in backbone or n in refuted for n in probing):
                    return  # maxflip: quedan variables por decidir (se para en la primera)
            elif probing <= self._seen_backbone:
                pass  # todo el chunk es backbone: el sondeo sería infactible
            elif probing.isdisjoint(self._seen_refuted):
                return  # aún indeciso
            self._decided = True
            self.probes_decided += 1
        backend.interrupt()


# ---------------------------------------------------------------------------- comprobación exacta
class ExactValidation:
    """Comprobación con enteros de las soluciones de un solver en coma flotante (``exact_check``)."""

    def __init__(self) -> None:
        self.checker: Optional[ExactChecker] = None
        self.optimum: Optional[float] = None
        #: Soluciones de sondeo rechazadas por no ser exactamente factibles.
        self.invalid_solutions = 0
        #: Motivo si la solución base propia no es exactamente válida.
        self.base_invalid: Optional[str] = None

    def prepare(self, config: ParaBackConfig, backend: SolverBackend, rank: int) -> None:
        """Crea el comprobador si hace falta (no para backends exactos, ver ``exact_arithmetic``)."""
        # Un solver exacto (RoundingBack) no necesita la comprobación, y en instancias
        # enormes cuesta más que el propio sondeo (dbst_v50: 1 s por solución, 20 s de lectura).
        if not config.exact_check or backend.exact_arithmetic:
            return
        try:
            # La misma instancia que ya leyó el backend, si la leyó en Python (NapBack).
            self.checker = ExactChecker(config.instance, backend.instance())
        except Exception as exc:  # noqa: BLE001 - p. ej. sintaxis OPB no lineal
            log.warning("trabajador %d: sin comprobación exacta de soluciones (%s)", rank, exc)

    def check_base(self, values: Mapping[str, bool], optimum: Optional[float]) -> Optional[str]:
        """Motivo si la solución base no es exactamente válida (y lo recuerda en ``base_invalid``)."""
        self.optimum = optimum
        if self.checker is None:
            return None
        self.base_invalid = self.checker.violation(values, optimum)
        return self.base_invalid

    def accept(self, values: Mapping[str, bool], rank: int) -> bool:
        """¿Se puede refutar con esta solución de sondeo?"""
        if self.checker is None:
            return True
        reason = self.checker.violation(values, self.optimum)
        if reason is None:
            return True
        self.invalid_solutions += 1
        log.info("trabajador %d: solución de sondeo rechazada (%s)", rank, reason)
        return False


# ---------------------------------------------------------------------------- mensajes salientes
@dataclass
class FactPublisher:
    """Envía ``BASE`` y ``FACTS`` al orquestador y cuenta lo enviado."""

    comm: Communicator
    rank: int
    tracer: Tracer
    facts_sent: int = 0
    facts_count: int = 0

    def base(self, status: BaseStatus, optimum: Optional[float] = None, values: Optional[Mapping[str, bool]] = None) -> None:
        self.comm.send(ORCHESTRATOR_RANK, protocol.base(self.rank, status, optimum, values or {}))

    def intent(self, chunk: Sequence[str]) -> None:
        """Anuncia el chunk que se empieza a sondear (``announce_intents``)."""
        self.comm.send(ORCHESTRATOR_RANK, protocol.intent(self.rank, chunk, now() if self.tracer.enabled else None))

    def facts(
        self, backbone: Sequence[Tuple[str, bool]], refuted: Sequence[str], solution: Optional[Mapping[str, bool]] = None
    ) -> None:
        message = protocol.facts(
            self.rank, backbone=backbone, refuted=refuted, solution=solution, sent_at=now() if self.tracer.enabled else None
        )
        self.facts_sent += 1
        self.facts_count += len(backbone) + len(refuted)
        self.comm.send(ORCHESTRATOR_RANK, message)


__all__ = ["BaseHintState", "ExactValidation", "FactPublisher", "Hint", "LiveProbeGuard", "NameMap"]

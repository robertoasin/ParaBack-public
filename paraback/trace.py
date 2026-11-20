"""Traza de eventos de ParaBack para medir la compartición de hechos.

Con ``ParaBackConfig.trace_dir`` cada rango escribe ``rank<r>.jsonl`` en ese
directorio: un objeto JSON por línea con ``ev`` (tipo de evento), ``t``
(``time.time()``, reloj de pared común a los procesos de un mismo nodo) y los
campos del evento. Lo consume ``tools/sharing_report.py``, que calcula la
latencia de los hechos (de quien los demuestra a quien los recibe) y el tiempo
de sondeo gastado en chunks que otro trabajador ya había decidido.

Eventos (:class:`Event`; los campos los fijan los métodos de :class:`Tracer`):

* orquestador: ``facts`` (``src``, ``t_sent``, ``base``, ``backbone``, ``refuted``,
  ``dup_backbone``, ``dup_refuted``), ``update_sent`` (``dst``, ``n``), ``stop`` (``reason``).
* trabajador: ``probe`` (``id``, ``chunk``, ``s``, ``e``, ``decided``), ``outcome``
  (``id``, ``outcome``: ``optimal``, ``infeasible``, ``decided`` o ``inconclusive``),
  ``update_recv`` (``t_sent``, ``backbone``, ``refuted``).

Sin ``trace_dir`` un :class:`Tracer` no hace nada y no cuesta nada.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import IO, Any, Optional, Sequence

from guroback.extractor import StrEnum


class Event(StrEnum):
    """Tipos de evento de la traza (campo ``ev``)."""

    FACTS = "facts"
    UPDATE_SENT = "update_sent"
    UPDATE_RECV = "update_recv"
    PROBE = "probe"
    OUTCOME = "outcome"
    STOP = "stop"
    #: Orquestador, con ``announce_intents``: un trabajador anuncia el chunk que sondea.
    INTENT = "intent"


class Outcome(StrEnum):
    """Resultado de un sondeo (``outcome.outcome``)."""

    OPTIMAL = "optimal"
    INFEASIBLE = "infeasible"
    #: Interrumpido porque otro trabajador ya lo había decidido.
    DECIDED = "decided"
    #: Límite o interrupción sin decidir.
    INCONCLUSIVE = "inconclusive"
    #: Llamada ``maxflip`` cortada por estancamiento (sus incumbentes ya refutaron).
    STALLED = "stalled"


class Tracer:
    def __init__(self, directory: Optional[str], rank: int, append: bool = False) -> None:
        """``append``: sigue la traza del rango en vez de empezarla (el sustituto de un trabajador)."""
        self._out: Optional[IO[str]] = None
        self._lock = threading.Lock()
        if directory:
            os.makedirs(directory, exist_ok=True)
            self._out = open(os.path.join(directory, f"rank{rank}.jsonl"), "a" if append else "w", encoding="utf-8")

    @property
    def enabled(self) -> bool:
        return self._out is not None

    def emit(self, ev: str, **fields: Any) -> None:
        if self._out is None:
            return
        line = json.dumps({"ev": str(ev), "t": time.time(), **fields})
        with self._lock:
            if self._out is not None:
                self._out.write(line + "\n")

    # ------------------------------------------------------------------ eventos del orquestador
    def facts(
        self, src: int, t_sent: Optional[float], base: bool, backbone: Sequence[str], refuted: Sequence[str],
        dup_backbone: int = 0, dup_refuted: int = 0,
    ) -> None:
        """Hechos nuevos fusionados (``backbone``/``refuted``: nombres) y los ya conocidos que se repitieron."""
        self.emit(Event.FACTS, src=src, t_sent=t_sent, base=base, backbone=list(backbone), refuted=list(refuted),
                  dup_backbone=dup_backbone, dup_refuted=dup_refuted)

    def intent(self, src: int, chunk: Sequence[str], overlaps: int) -> None:
        """``src`` empieza a sondear ``chunk``, que se solapa con ``overlaps`` sondeos en curso de otros."""
        self.emit(Event.INTENT, src=src, chunk=list(chunk), overlaps=overlaps)

    def update_sent(self, dst: int, n: int) -> None:
        self.emit(Event.UPDATE_SENT, dst=dst, n=n)

    def stop(self, reason: str) -> None:
        self.emit(Event.STOP, reason=str(reason))

    # ------------------------------------------------------------------ eventos del trabajador
    def probe(self, probe_id: int, chunk: Sequence[str], start: float, end: float, decided: bool) -> None:
        self.emit(Event.PROBE, id=probe_id, chunk=list(chunk), s=start, e=end, decided=decided)

    def outcome(self, probe_id: int, outcome: Outcome) -> None:
        self.emit(Event.OUTCOME, id=probe_id, outcome=str(outcome))

    def update_recv(self, t_sent: Optional[float], backbone: Sequence[str], refuted: Sequence[str]) -> None:
        self.emit(Event.UPDATE_RECV, t_sent=t_sent, backbone=list(backbone), refuted=list(refuted))

    def close(self) -> None:
        with self._lock:
            if self._out is not None:
                self._out.close()
                self._out = None


def now() -> float:
    return time.time()


__all__ = ["Event", "Outcome", "Tracer", "now"]

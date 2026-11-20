"""Esquema de las estadísticas de ParaBack: de cada trabajador y de la ejecución (``--stats-json``).

Antes las estadísticas eran diccionarios con claves en cadenas repetidas en el
trabajador, el trabajador Cadiback, el orquestador, la CLI y tres herramientas, y los
dos trabajadores publicaban claves distintas con significados distintos
(``facts_sent`` contaba mensajes en uno y hechos en el otro). Ahora:

* :class:`WorkerStats` tiene **todas** las claves de los dos trabajadores con su valor
  por defecto; viaja en ``DONE`` como diccionario (``to_dict``/``from_dict``).
* :class:`WorkerRecord` es lo que el orquestador sabe de cada trabajador (su forma JSON
  es la de ``workers`` en ``--stats-json``).
* :class:`RunSummary` lee un ``--stats-json`` (también los anteriores a este esquema)
  para las herramientas: ``tools/paraback_report.py``, ``tools/sharing_report.py`` y
  ``tools/cluster/collect.sh``.

El JSON conserva la forma de siempre: los diccionarios de un trabajador siguen
admitiendo ``stats["clave"]`` y ``stats.get("clave")``.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import resource
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union


def max_rss_mb() -> float:
    """Pico de memoria residente de este proceso, en MB (``ru_maxrss``: KB en Linux, bytes en macOS)."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


class Stopwatch:
    """Segundos acumulados dentro de ``with watch:`` (el coste de la comunicación en las métricas).

    Cada cronómetro lo usa un solo hilo: el principal o el de escucha, nunca los dos.
    """

    def __init__(self) -> None:
        self.total = 0.0
        self._start = 0.0

    def __enter__(self) -> "Stopwatch":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.total += time.perf_counter() - self._start


class _MappingCompat:
    """``x["clave"]`` y ``x.get("clave", defecto)`` sobre los campos de una dataclass."""

    def __getitem__(self, key: str) -> Any:
        if key in self._field_names():
            return getattr(self, key)
        extra = getattr(self, "extra", {})
        if key in extra:
            return extra[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and (key in self._field_names() or key in getattr(self, "extra", {}))

    @classmethod
    def _field_names(cls) -> List[str]:
        return [f.name for f in dataclasses.fields(cls)]  # type: ignore[arg-type]


@dataclass
class WorkerStats(_MappingCompat):
    """Estadísticas de un trabajador al terminar (``DONE``)."""

    #: Especificación del trabajador (``WorkerSpec.describe()``).
    spec: str = ""
    # ---- llamadas al solver (trabajador genérico; Cadiback no las cuenta)
    solve_count: int = 0
    total_solve_time: float = 0.0
    probe_count: int = 0
    # ---- hechos
    #: Backbones que este trabajador demostró (incluidos los que otro ya conocía).
    backbone_found: int = 0
    #: Mensajes ``FACTS`` enviados.
    facts_sent: int = 0
    #: Hechos (backbones + refutadas) enviados en esos mensajes.
    facts_count: int = 0
    #: Mensajes ``UPDATE`` recibidos.
    updates_received: int = 0
    # ---- coste de la comunicación (s): lo que el trabajador hace en Python por los mensajes
    #: Hilo principal: traducir a nombres y enviar ``FACTS`` (con la solución, si se comparte).
    send_time: float = 0.0
    #: Hilo principal: fusionar los ``UPDATE`` en el conocimiento local antes de un sondeo.
    absorb_time: float = 0.0
    #: Hilo de escucha: leer cada ``UPDATE`` y usarlo en el sondeo en curso (fijaciones vivas,
    #: interrupción si ya está decidido; en Cadiback, traducirlo a su entrada).
    listener_time: float = 0.0
    # ---- hechos ajenos en el sondeo en curso
    live_fixes: int = 0
    probes_decided: int = 0
    # ---- comprobación exacta
    invalid_solutions: int = 0
    #: Variables que este extractor abandonó (su sonda individual dio una solución rechazada).
    abandoned: List[str] = field(default_factory=list)
    #: Motivo si la solución base propia no era exactamente válida (el trabajador se retiró).
    base_invalid: Optional[str] = None
    # ---- origen de la base
    base_from_hint: bool = False
    base_from_file: bool = False
    # ---- hechos sin sondeo (docs/PLAN_REFACTOR.md, fase 7)
    refuted_by_rotation: int = 0
    fixed_by_solver: int = 0
    forced_prepass: int = 0
    # ---- chunks=maxflip: refutadas por incumbentes en mitad de una llamada; llamadas cortadas por estancamiento
    refuted_by_incumbent: int = 0
    maxflip_stalls: int = 0
    # ---- trabajador Cadiback
    models: int = 0
    refuted_by_model: int = 0
    refuted_by_flip: int = 0
    #: Hechos ajenos entregados a Cadiback.
    external_facts: int = 0
    # ---- recursos
    #: Pico de memoria residente del proceso del trabajador (en local, el del proceso común).
    max_rss_mb: float = 0.0
    #: Contadores nativos del backend (``SolverBackend.native_stats``).
    native: Dict[str, float] = field(default_factory=dict)
    #: Claves desconocidas de un JSON (otra versión): se conservan, no se pierden.
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra")
        return {**extra, **data}

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "WorkerStats":
        data = dict(data or {})
        names = set(cls._field_names()) - {"extra"}
        known = {k: v for k, v in data.items() if k in names}
        extra = {k: v for k, v in data.items() if k not in names and k != "extra"}
        extra.update(data.get("extra") or {})
        # Esquema anterior de Cadiback: base_invalid=True en lugar del motivo.
        if known.get("base_invalid") is True:
            known["base_invalid"] = "solución base no válida en aritmética exacta"
        return cls(**known, extra=extra)


@dataclass
class WorkerRecord(_MappingCompat):
    """Lo que el orquestador sabe de cada trabajador (``workers`` de ``--stats-json``)."""

    rank: int
    spec: str = ""
    solver: str = ""
    capabilities: Dict[str, bool] = field(default_factory=dict)
    base_status: Optional[str] = None
    optimum: Optional[float] = None
    done_status: Optional[str] = None
    stats: WorkerStats = field(default_factory=WorkerStats)
    error: Optional[str] = None
    facts_received: int = 0
    backbone_contributed: int = 0
    refuted_contributed: int = 0
    #: ``"<especificación>: <motivo>"`` del trabajador al que sustituyó en el sitio (``None``: ninguno).
    replaced: Optional[str] = None

    @property
    def finished(self) -> bool:
        return self.done_status is not None or self.error is not None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stats"] = self.stats.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkerRecord":
        names = set(cls._field_names())
        known = {k: v for k, v in data.items() if k in names and k != "stats"}
        return cls(**known, stats=WorkerStats.from_dict(data.get("stats")))


@dataclass
class RunSummary(_MappingCompat):
    """Un ``--stats-json`` leído (``ParaBackResult.to_dict``)."""

    status: str = ""
    is_optimization: bool = False
    optimum: Optional[float] = None
    backbone: List[str] = field(default_factory=list)
    num_backbone: int = 0
    num_refuted: int = 0
    num_unknown: int = 0
    wall_time: float = 0.0
    #: Cuándo quedó completo el backbone, en el reloj de ``wall_time`` (``None`` si no lo quedó, y en
    #: los ``--stats-json`` anteriores al 2026-10-28). La diferencia es la espera a los trabajadores.
    complete_time: Optional[float] = None
    messages: int = 0
    solve_count: int = 0
    total_solve_time: float = 0.0
    error: Optional[str] = None
    abandoned: List[int] = field(default_factory=list)
    #: Trabajadores retirados (su solver murió o se quedó sin memoria); el motivo en ``workers[r].error``.
    retired: List[int] = field(default_factory=list)
    #: Pico de memoria del proceso del orquestador (MB).
    max_rss_mb: float = 0.0
    #: Con ``announce_intents``: sondeos anunciados y cuántos se solapaban con otro en curso.
    intents: int = 0
    intent_overlaps: int = 0
    #: ``UPDATE`` enviados por el orquestador y hechos que llevaban (``share_facts``, ``share_interval``).
    updates_sent: int = 0
    update_facts: int = 0
    #: Segundos que el orquestador pasó procesando mensajes y reenviando (no esperando): con
    #: ``wall_time``, si el orquestador es un cuello de botella.
    busy_time: float = 0.0
    #: Rango del trabajador cuya base fijó z* y x* (``--base-only``: quién resolvió la instancia).
    base_worker: Optional[int] = None
    workers: Dict[int, WorkerRecord] = field(default_factory=dict)
    #: Lo que la CLI añade (``config``) u otras claves desconocidas.
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunSummary":
        names = set(cls._field_names()) - {"workers", "extra"}
        known = {k: v for k, v in data.items() if k in names}
        extra = {k: v for k, v in data.items() if k not in names and k != "workers"}
        workers = {int(rank): WorkerRecord.from_dict({"rank": int(rank), **w}) for rank, w in dict(data.get("workers", {})).items()}
        return cls(**known, workers=workers, extra=extra)

    @classmethod
    def load(cls, path: Union[str, pathlib.Path]) -> "RunSummary":
        return cls.from_dict(json.loads(pathlib.Path(path).read_text(encoding="utf-8")))


__all__ = ["RunSummary", "Stopwatch", "WorkerRecord", "WorkerStats", "max_rss_mb"]

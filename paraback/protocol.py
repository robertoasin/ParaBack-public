"""Mensajes que intercambian el orquestador y los trabajadores de ParaBack.

El protocolo es independiente del transporte (:mod:`paraback.communicator`):
los mismos mensajes viajan por colas en memoria (referencia y tests) o por
MPI (Fase 2). Todos los payloads son estructuras simples (listas, dicts,
cadenas, números) para poder serializarse con ``pickle`` (``mpi4py`` en
minúsculas) o JSON sin código adicional.

Las variables se identifican en el cable por su **nombre** (``x12``), nunca
por ``VarId``: cada backend numera sus variables en el orden en que las lee y
ese orden no tiene por qué coincidir entre Gurobi, SCIP y RoundingSat.

Flujo de mensajes (``O`` = orquestador, ``W`` = trabajador)::

    W → O  HELLO     {"spec": str, "solver": str, "capabilities": {...}}
    W → O  BASE      {"status": str, "optimum": float|None, "values": {name: bool}}
    W → O  FACTS     {"backbone": [[name, bool]...], "refuted": [name...],
                      "solution": {name: bool} | None, "t"?: float}
    O → W  UPDATE    {"backbone": [[name, bool]...], "refuted": [name...],
                      "hint": {name: bool} | None, "t"?: float,
                      "probing_elsewhere"?: [name...]}          (sólo con announce_intents)
    O → W  BASE_HINT {"optimum": float, "values": {name: bool} | None}
    W → O  INTENT    {"chunk": [name...], "t"?: float}        (sólo con announce_intents)
    O → W  STOP      {"reason": str}
    W → O  DONE      {"status": str, "stats": {...}}
    W → O  ERROR     {"message": str, "fatal": bool}

``BASE_HINT`` es el z* **y la asignación óptima** x* del primer trabajador en
resolver su base, relevados al resto (``ParaBackConfig.share_base_optimum``)
para que interrumpan su propio solve base (si su backend lo soporta) y lo
sustituyan por una *verificación* de x* en z*: un solve con todas las
variables fijadas, lineal en la práctica, en lugar de buscar una solución de
coste z* desde cero, que sigue siendo NP-difícil (``paraback/worker.py``,
``Worker._extract``; ``BackboneExtractor(base_solution=...)``). Sin
``values`` el trabajador sólo puede reintentar con z* fijado. No debe confundirse con el
``hint`` de ``UPDATE``, que es una solución de sondeo para *warm start*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from guroback.extractor import StrEnum


class BaseStatus(StrEnum):
    """Estado de la base de un trabajador (``BASE.status``)."""

    #: Resolvió (o verificó) su base: trae z* y x*.
    FEASIBLE = "feasible"
    #: Demostró que la instancia es infactible.
    INFEASIBLE = "infeasible"
    #: Su base no es exactamente válida (comprobación exacta): se retira.
    INVALID = "invalid"
    #: No llegó a resolverla (límite, STOP).
    INTERRUPTED = "interrupted"


class StopReason(StrEnum):
    """Motivo de un ``STOP`` (``STOP.reason``)."""

    #: El conocimiento global está completo.
    COMPLETE = "complete"
    #: Se agotó ``time_limit``.
    TIME_LIMIT = "time_limit"
    #: Un trabajador falló o hubo una contradicción.
    ERROR = "error"
    #: Con ``base_only``: un trabajador demostró que la instancia es infactible.
    INFEASIBLE = "infeasible"
    #: El proceso del orquestador recibió SIGTERM o SIGINT: se para y se escribe lo que se sabe.
    SIGNAL = "signal"
    #: ``--base-only``: ya hay z* y x*; no se sondea nada.
    BASE = "base"
    #: Cierre MPI: todos terminaron y el orquestador libera a los trabajadores (``paraback/mpi.py``).
    SHUTDOWN = "shutdown"


class MessageKind(Enum):
    HELLO = "hello"
    BASE = "base"
    FACTS = "facts"
    UPDATE = "update"
    BASE_HINT = "base_hint"
    STOP = "stop"
    DONE = "done"
    ERROR = "error"
    #: El chunk que un trabajador empieza a sondear (``ParaBackConfig.announce_intents``).
    INTENT = "intent"


@dataclass(frozen=True)
class Message:
    kind: MessageKind
    sender: int
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> Tuple[str, int, Dict[str, Any]]:
        """Representación serializable (``pickle``/JSON)."""
        return self.kind.value, self.sender, dict(self.payload)

    @classmethod
    def from_wire(cls, data: Sequence[Any]) -> "Message":
        kind, sender, payload = data
        return cls(MessageKind(kind), int(sender), dict(payload))


# ---------------------------------------------------------------------------- constructores
def hello(sender: int, spec: str, solver: str, capabilities: Mapping[str, bool],
          replaces: Optional[Tuple[str, str]] = None) -> Message:
    """``replaces``: ``(especificación, motivo)`` del trabajador al que sustituye en el sitio (``paraback/worker_base.py``)."""
    payload: Dict[str, Any] = {"spec": spec, "solver": solver, "capabilities": dict(capabilities)}
    if replaces is not None:
        payload["replaces"] = {"spec": replaces[0], "reason": replaces[1]}
    return Message(MessageKind.HELLO, sender, payload)


def base(sender: int, status: str, optimum: Optional[float], values: Mapping[str, bool]) -> Message:
    return Message(MessageKind.BASE, sender, {"status": str(status), "optimum": optimum, "values": dict(values)})


def facts(
    sender: int,
    backbone: Sequence[Tuple[str, bool]] = (),
    refuted: Sequence[str] = (),
    solution: Optional[Mapping[str, bool]] = None,
    sent_at: Optional[float] = None,
) -> Message:
    payload: Dict[str, Any] = {
        "backbone": [[name, bool(value)] for name, value in backbone],
        "refuted": list(refuted),
        "solution": None if solution is None else dict(solution),
    }
    if sent_at is not None:
        payload["t"] = sent_at  # sólo con traza (paraback/trace.py)
    return Message(MessageKind.FACTS, sender, payload)


def update(
    sender: int,
    backbone: Sequence[Tuple[str, bool]] = (),
    refuted: Sequence[str] = (),
    hint: Optional[Mapping[str, bool]] = None,
    sent_at: Optional[float] = None,
    probing_elsewhere: Optional[Sequence[str]] = None,
) -> Message:
    payload: Dict[str, Any] = {
        "backbone": [[name, bool(value)] for name, value in backbone],
        "refuted": list(refuted),
        "hint": None if hint is None else dict(hint),
    }
    if sent_at is not None:
        payload["t"] = sent_at  # sólo con traza (paraback/trace.py)
    if probing_elsewhere is not None:
        payload["probing_elsewhere"] = list(probing_elsewhere)  # sólo con announce_intents
    return Message(MessageKind.UPDATE, sender, payload)


def intent(sender: int, chunk: Sequence[str], sent_at: Optional[float] = None) -> Message:
    """Lo que ``sender`` empieza a sondear (coordinación de intenciones, ``docs/PLAN_REFACTOR.md`` fase 6)."""
    payload: Dict[str, Any] = {"chunk": list(chunk)}
    if sent_at is not None:
        payload["t"] = sent_at
    return Message(MessageKind.INTENT, sender, payload)


def read_intent(message: Message) -> List[str]:
    return [str(n) for n in message.payload.get("chunk", [])]


def read_probing_elsewhere(message: Message) -> List[str]:
    """Variables que otros trabajadores están sondeando, si el ``UPDATE`` las trae."""
    return [str(n) for n in message.payload.get("probing_elsewhere", [])]


def base_hint(sender: int, optimum: float, values: Optional[Mapping[str, bool]] = None) -> Message:
    return Message(MessageKind.BASE_HINT, sender, {"optimum": optimum, "values": None if values is None else dict(values)})


def stop(sender: int, reason: str) -> Message:
    return Message(MessageKind.STOP, sender, {"reason": str(reason)})


def done(sender: int, status: str, stats: Mapping[str, Any]) -> Message:
    return Message(MessageKind.DONE, sender, {"status": str(status), "stats": dict(stats)})


def error(sender: int, message: str, fatal: bool = True) -> Message:
    """``fatal``: la ejecución entera falla (contradicción, traducción corrupta: una respuesta
    en la que no se puede confiar); si no, el trabajador sólo se retira (su solver murió o se
    quedó sin memoria: ``guroback.backend.SolverCrashed``) y los demás siguen."""
    return Message(MessageKind.ERROR, sender, {"message": message, "fatal": bool(fatal)})


def backbone_pairs(payload: Mapping[str, Any]) -> List[Tuple[str, bool]]:
    """Pares ``(nombre, valor)`` de un payload FACTS/UPDATE."""
    return [(str(name), bool(value)) for name, value in payload.get("backbone", [])]


# ---------------------------------------------------------------------------- lectores
# Un lector por mensaje, junto a su constructor: los módulos no leen ``payload["..."]``.
def _values(data: Any) -> Optional[Dict[str, bool]]:
    return None if data is None else {str(n): bool(v) for n, v in dict(data).items()}


@dataclass(frozen=True)
class Hello:
    spec: str
    solver: str
    capabilities: Dict[str, bool]
    #: Especificación y motivo del trabajador al que este sustituye en el sitio (``None``: ninguno).
    replaces_spec: Optional[str] = None
    replaces_reason: Optional[str] = None


@dataclass(frozen=True)
class Base:
    status: str
    optimum: Optional[float]
    values: Dict[str, bool]


@dataclass(frozen=True)
class Facts:
    """Contenido de ``FACTS`` (de un trabajador) o de ``UPDATE`` (del orquestador)."""

    backbone: List[Tuple[str, bool]]
    refuted: List[str]
    #: ``FACTS``: solución óptima del sondeo; ``UPDATE``: pista para ``add_solution_hint``.
    solution: Optional[Dict[str, bool]]
    #: Instante de envío (sólo con traza).
    sent_at: Optional[float]
    #: ``UPDATE`` con ``announce_intents``: lo que otros trabajadores están sondeando ahora.
    probing_elsewhere: Optional[List[str]] = None


@dataclass(frozen=True)
class BaseHint:
    optimum: float
    values: Optional[Dict[str, bool]]


@dataclass(frozen=True)
class Done:
    status: str
    stats: Dict[str, Any]


def read_hello(message: Message) -> Hello:
    p = message.payload
    replaces = p.get("replaces") or {}
    return Hello(
        str(p.get("spec", "")), str(p.get("solver", "")), {str(k): bool(v) for k, v in dict(p.get("capabilities", {})).items()},
        str(replaces["spec"]) if replaces else None, str(replaces.get("reason", "")) if replaces else None,
    )


def read_base(message: Message) -> Base:
    p = message.payload
    optimum = p.get("optimum")
    return Base(str(p.get("status")), None if optimum is None else float(optimum), _values(p.get("values")) or {})


def read_facts(message: Message) -> Facts:
    """``FACTS`` o ``UPDATE`` (la solución de uno es la pista del otro)."""
    p = message.payload
    solution = p.get("solution") if message.kind is MessageKind.FACTS else p.get("hint")
    sent_at = p.get("t")
    elsewhere = p.get("probing_elsewhere")
    return Facts(
        backbone_pairs(p),
        [str(n) for n in p.get("refuted", [])],
        _values(solution),
        None if sent_at is None else float(sent_at),
        None if elsewhere is None else [str(n) for n in elsewhere],
    )


def read_base_hint(message: Message) -> BaseHint:
    p = message.payload
    return BaseHint(float(p["optimum"]), _values(p.get("values")))


def read_stop(message: Message) -> str:
    """Motivo del ``STOP`` (un :class:`StopReason` o una cadena de otra versión)."""
    reason = str(message.payload.get("reason", ""))
    try:
        return StopReason(reason)
    except ValueError:
        return reason


def read_done(message: Message) -> Done:
    p = message.payload
    return Done(str(p.get("status")), dict(p.get("stats", {})))


def read_error(message: Message) -> str:
    return str(message.payload.get("message"))


def is_fatal_error(message: Message) -> bool:
    """``ERROR.fatal`` (por defecto fatal: un mensaje antiguo sin el campo falla la ejecución)."""
    return bool(message.payload.get("fatal", True))


__all__ = [
    "Base",
    "BaseHint",
    "BaseStatus",
    "Done",
    "Facts",
    "Hello",
    "Message",
    "MessageKind",
    "StopReason",
    "backbone_pairs",
    "base",
    "base_hint",
    "done",
    "error",
    "facts",
    "hello",
    "intent",
    "read_base",
    "read_base_hint",
    "read_done",
    "read_error",
    "is_fatal_error",
    "read_facts",
    "read_hello",
    "read_intent",
    "read_probing_elsewhere",
    "read_stop",
    "stop",
    "update",
]

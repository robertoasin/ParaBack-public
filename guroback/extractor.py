"""Algoritmo de extracción de backbones, independiente del solver.

Reproduce el método ``Guroback::solve`` de la implementación C++ original
hablando únicamente con :class:`guroback.backend.SolverBackend`:

1. Resolver la instancia base; si es de optimización, fijar el óptimo ``z*``
   como restricción y anular la función objetivo.
2. Mientras queden candidatos, tomar un *chunk* de tamaño ``k`` y sondear:
   * ``k == 1``: imponer ``x_i != v_i`` mediante cotas.
   * ``k > 1``: imponer ``sum_i flip_i >= 1`` con una restricción temporal.
3. Si el sondeo es infactible, todo el chunk es backbone: se fija de forma
   permanente y ``k`` se duplica. Si es óptimo, los candidatos cuyo valor cambió
   dejan de ser candidatos y ``k`` vuelve a 1.

Descomposición para el trabajo coordinado (ParaBack)
----------------------------------------------------

El bucle anterior se descompone en tres piezas intercambiables, todas con un
valor por defecto que reproduce **exactamente** el comportamiento secuencial
(mismo orden de sondeos, mismo número de llamadas al solver, mismo archivo):

* :class:`BackboneKnowledge`: el estado de cada variable (desconocida,
  backbone, no backbone), la solución base y ``z*``. Es la única información
  que un trabajador de ParaBack necesita compartir; cualquier solución óptima
  refuta candidatos (:meth:`BackboneKnowledge.absorb_solution`) y cualquier
  sondeo infactible demuestra backbones (:meth:`BackboneKnowledge.mark_backbone`).
* :class:`VariableOrder` y :class:`ChunkPolicy`: el orden en que se prueban
  los candidatos y el tamaño de los chunks. Son el "principio de búsqueda" con
  el que se diversifican los trabajadores del portfolio.
* :class:`Coordinator`: puntos de enganche invocados por el extractor antes y
  después de cada sondeo. El coordinador de ParaBack publica lo aprendido y
  absorbe lo que aprendieron los demás mutando el :class:`BackboneKnowledge`
  compartido; el extractor se encarga de trasladar esos hechos al backend
  (fijando permanentemente los backbones conocidos) y al archivo de salida.

Sondeo por máximo de cambios (``chunks=maxflip[:T]``)
----------------------------------------------------

Con :class:`MaxFlipChunks` cada llamada toma todos los candidatos U y maximiza cuántos
cambian de valor respecto de x* con el objetivo original fijado en ``z*``
(:meth:`ProbeScope.maximize_flips`). Cada incumbente es una solución óptima: tras la
comprobación exacta, los candidatos que cambia quedan refutados y se comparten en el
momento, sin esperar al final de la llamada. Si una llamada termina con óptimo 0,
ninguna solución óptima cambia ningún candidato de U: todos son backbone. Si termina
con óptimo k > 0, se repite con los candidatos que quedan. Con ``T`` (estancamiento),
una llamada que ya refutó algo se corta tras ``T`` segundos sin refutar nada nuevo:
demostrar que k es máximo no aporta nada, sólo la última llamada necesita la prueba.
"""
from __future__ import annotations

import abc
import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    AbstractSet,
    ClassVar,
    Dict,
    Generic,
    Hashable,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    TextIO,
    Tuple,
    TypeVar,
)

from .backend import IncumbentListener, SolveResult, SolverBackend, VarId, normalize_objective_value
from .opb import OPBInstance
from .rotation import ModelRotation, NoRotation

log = logging.getLogger("guroback")

#: Clave de :class:`BackboneKnowledge` (``VarId`` en un backend, nombre en ParaBack).
K = TypeVar("K", bound=Hashable)


# ---------------------------------------------------------------------------- conocimiento
class VarStatus(Enum):
    """Estado de una variable candidata durante la extracción."""

    UNKNOWN = "unknown"
    BACKBONE = "backbone"
    NOT_BACKBONE = "not_backbone"


class StrEnum(str, Enum):
    """Enum cuyos valores son cadenas y se comparan, formatean y serializan como ellas.

    ``RunStatus.COMPLETE == "complete"``, ``f"{RunStatus.COMPLETE}" == "complete"`` y
    ``json.dumps`` escribe ``"complete"``: el código y los archivos que usaban cadenas no
    cambian. (``enum.StrEnum`` existe desde Python 3.11.)
    """

    def __str__(self) -> str:
        return str(self.value)

    def __format__(self, spec: str) -> str:
        return format(str(self.value), spec)


class RunStatus(StrEnum):
    """Estado final de una extracción (secuencial o de ParaBack)."""

    #: Todas las variables clasificadas (el archivo termina en ``b 0``).
    COMPLETE = "complete"
    #: La instancia base no tiene solución.
    INFEASIBLE = "infeasible"
    #: Límite de tiempo, interrupción o parada pedida por el coordinador.
    INTERRUPTED = "interrupted"
    #: Sólo ParaBack: contradicción entre trabajadores o error de un trabajador.
    FAILED = "failed"


class InconsistentKnowledge(RuntimeError):
    """Dos fuentes afirman hechos incompatibles sobre una variable.

    Sólo puede ocurrir si dos trabajadores no resuelven el mismo problema (por
    ejemplo, óptimos distintos por tolerancias numéricas): es un error, nunca
    se resuelve en silencio.
    """


@dataclass
class BackboneKnowledge(Generic[K]):
    """Todo lo que se sabe (y se comparte) sobre el backbone de una instancia.

    ``values`` es la solución base: el valor candidato ``v_i`` de cada variable.
    Las variables se recorren siempre en el orden de ``values`` (orden del
    archivo), lo que hace determinista la salida.

    Es genérica en la clave ``K``: el extractor usa ``VarId`` del backend y el
    orquestador de ParaBack, nombres de variable (``BackboneKnowledge[str]``).
    """

    values: Dict[K, bool]
    optimum: Optional[float] = None
    status: Dict[K, VarStatus] = field(default_factory=dict)
    #: Variables backbone en orden de descubrimiento (local o importado).
    discovery: List[K] = field(default_factory=list)

    def __post_init__(self) -> None:
        for var in self.values:
            self.status.setdefault(var, VarStatus.UNKNOWN)

    # ------------------------------------------------------------------ consultas
    @property
    def variables(self) -> List[K]:
        return list(self.values)

    def unknown(self) -> List[K]:
        return [v for v, s in self.status.items() if s is VarStatus.UNKNOWN]

    def is_complete(self) -> bool:
        return all(s is not VarStatus.UNKNOWN for s in self.status.values())

    def backbone(self) -> List[Tuple[K, bool]]:
        """Pares ``(variable, valor)`` demostrados backbone, en orden de descubrimiento."""
        return [(v, self.values[v]) for v in self.discovery]

    def refuted(self) -> List[K]:
        return [v for v, s in self.status.items() if s is VarStatus.NOT_BACKBONE]

    def counts(self) -> Dict[str, int]:
        result = {s.value: 0 for s in VarStatus}
        for s in self.status.values():
            result[s.value] += 1
        return result

    # ------------------------------------------------------------------ hechos nuevos
    def mark_backbone(self, pairs: Iterable[Tuple[K, bool]]) -> List[K]:
        """Registra variables demostradas backbone; devuelve las que eran desconocidas."""
        newly: List[K] = []
        for var, value in pairs:
            if value != self.values[var]:
                raise InconsistentKnowledge(
                    f"la variable {var} se declara backbone con valor {value}, pero la solución base tiene {self.values[var]}"
                )
            current = self.status[var]
            if current is VarStatus.NOT_BACKBONE:
                raise InconsistentKnowledge(f"la variable {var} ya fue refutada y ahora se declara backbone")
            if current is VarStatus.UNKNOWN:
                self.status[var] = VarStatus.BACKBONE
                self.discovery.append(var)
                newly.append(var)
        return newly

    def mark_not_backbone(self, variables: Iterable[K]) -> List[K]:
        """Registra variables refutadas; devuelve las que eran desconocidas."""
        newly: List[K] = []
        for var in variables:
            current = self.status[var]
            if current is VarStatus.BACKBONE:
                raise InconsistentKnowledge(f"la variable {var} ya fue demostrada backbone y ahora se refuta")
            if current is VarStatus.UNKNOWN:
                self.status[var] = VarStatus.NOT_BACKBONE
                newly.append(var)
        return newly

    def absorb_solution(self, solution: Mapping[K, bool]) -> List[K]:
        """Refuta toda variable desconocida cuyo valor en ``solution`` difiere del base.

        ``solution`` debe ser una solución **óptima** (o factible, en problemas
        de decisión); puede ser parcial. Devuelve las variables recién refutadas.
        """
        differing = [v for v, x in solution.items() if self.status.get(v) is VarStatus.UNKNOWN and x != self.values[v]]
        return self.mark_not_backbone(differing)

    def merge(self, backbone: Iterable[Tuple[K, bool]], refuted: Iterable[K]) -> Tuple[List[K], List[K]]:
        """Incorpora hechos de otra fuente; devuelve ``(nuevos backbone, nuevos refutados)``."""
        return self.mark_backbone(backbone), self.mark_not_backbone(refuted)


# ---------------------------------------------------------------------------- estrategias
class VariableOrder(abc.ABC):
    """Orden inicial en que se sondean las variables candidatas."""

    name: ClassVar[str] = "abstract"

    def prepare(self, backend: SolverBackend) -> None:
        """Se invoca una vez, tras la solución base y justo antes de :meth:`order`.

        Permite a un orden pedir información al backend (p. ej. las puntuaciones de
        propagación de RB-WP). Por defecto no hace nada.
        """

    @abc.abstractmethod
    def order(self, variables: Sequence[VarId], knowledge: BackboneKnowledge) -> List[VarId]:
        """Permutación de ``variables``. Se calcula una vez, tras la solución base."""


class FileOrder(VariableOrder):
    """Orden de lectura del archivo (el de GuroBack original)."""

    name = "file"

    def order(self, variables: Sequence[VarId], knowledge: BackboneKnowledge) -> List[VarId]:
        return list(variables)


class ReverseOrder(VariableOrder):
    name = "reverse"

    def order(self, variables: Sequence[VarId], knowledge: BackboneKnowledge) -> List[VarId]:
        return list(reversed(variables))


class RandomOrder(VariableOrder):
    """Permutación aleatoria reproducible a partir de ``seed``."""

    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def order(self, variables: Sequence[VarId], knowledge: BackboneKnowledge) -> List[VarId]:
        ordered = list(variables)
        random.Random(self.seed).shuffle(ordered)
        return ordered


class StridedOrder(VariableOrder):
    """Empieza por las posiciones ``offset, offset+stride, ...`` y sigue con el resto.

    Con ``stride`` trabajadores y ``offset`` distinto en cada uno, los
    trabajadores atacan primero regiones disjuntas de la lista de candidatos.
    """

    name = "strided"

    def __init__(self, offset: int = 0, stride: int = 1) -> None:
        if stride < 1 or offset < 0:
            raise ValueError("stride >= 1 y offset >= 0")
        self.offset, self.stride = offset, stride

    def order(self, variables: Sequence[VarId], knowledge: BackboneKnowledge) -> List[VarId]:
        variables = list(variables)
        first = variables[self.offset % self.stride :: self.stride] if variables else []
        chosen = set(first)  # una vez: dentro de la comprensión era cuadrático (ped3: ~100 s)
        rest = [v for v in variables if v not in chosen]
        return first + rest


class ValueOrder(VariableOrder):
    """Primero las variables con valor ``value`` en la solución base, luego las demás."""

    name = "value"

    def __init__(self, value: bool = True) -> None:
        self.value = value

    def order(self, variables: Sequence[VarId], knowledge: BackboneKnowledge) -> List[VarId]:
        first = [v for v in variables if knowledge.values[v] == self.value]
        rest = [v for v in variables if knowledge.values[v] != self.value]
        return first + rest


class WeightedPropagationOrder(VariableOrder):
    """RB-WP (ICAART 2026, §3.1.1): por puntuación de actividad de propagación, de mayor a menor.

    Las variables que más a menudo forman parte de las razones de una propagación
    tienden a estar más restringidas (y a ser backbone). Las puntuaciones las da
    :meth:`SolverBackend.propagation_scores`; los empates conservan el orden del
    archivo. Si el backend no las ofrece, se usa el orden del archivo y se avisa.
    """

    name = "wp"

    def __init__(self) -> None:
        self.scores: Optional[Mapping[VarId, float]] = None

    def prepare(self, backend: SolverBackend) -> None:
        self.scores = backend.propagation_scores()
        if self.scores is None:
            log.warning("order=wp: el backend %s no ofrece puntuaciones de propagación; se usa el orden del archivo", backend.name)

    def order(self, variables: Sequence[VarId], knowledge: BackboneKnowledge) -> List[VarId]:
        scores = self.scores
        if not scores:
            return list(variables)
        position = {v: i for i, v in enumerate(variables)}
        return sorted(variables, key=lambda v: (-scores.get(v, 0.0), position[v]))


class ChunkPolicy(abc.ABC):
    """Tamaño del chunk de candidatos que se sondea de una vez."""

    name: ClassVar[str] = "abstract"

    @abc.abstractmethod
    def reset(self) -> None: ...

    @abc.abstractmethod
    def next_size(self, remaining: int) -> int:
        """Tamaño del próximo chunk dado el número de candidatos restantes (``>= 1``)."""

    @abc.abstractmethod
    def on_optimal(self, remaining: int) -> None:
        """El sondeo encontró una solución (al menos un candidato refutado)."""

    @abc.abstractmethod
    def on_infeasible(self, remaining: int) -> None:
        """El sondeo fue infactible (todo el chunk es backbone)."""

    def on_inconclusive(self, remaining: int) -> None:
        """El sondeo no concluyó (límite o interrupción). Por defecto no cambia nada."""

    def select(self, candidates: Sequence[VarId], size: int, busy: AbstractSet[VarId]) -> List[VarId]:
        """El chunk a sondear: entre 1 y ``size`` variables de ``candidates`` (en su orden).

        ``busy`` son candidatos que otro trabajador está sondeando ahora
        (:meth:`Coordinator.busy_elsewhere`); una política de coordinación de intenciones
        puede evitarlos. Por defecto se ignoran y se toman los ``size`` primeros, como siempre.
        """
        return list(candidates[:size])


class GeometricChunks(ChunkPolicy):
    """``k`` se multiplica por ``K`` tras un infactible y vuelve a 1 tras un óptimo (CadiBack, Algoritmo 1).

    ``K = ∞`` (``chunks=all``, el valor por defecto de CadiBack): tras un infactible el
    siguiente chunk son todos los candidatos restantes; con solución única bastan tres
    llamadas. CadiBack lo mide como su mejor configuración (``docs/PLAN_REFACTOR.md`` §1.8).
    """

    name = "geometric"

    def __init__(self, factor: float = 2.0) -> None:
        if not factor > 1:
            raise ValueError("el factor de crecimiento del chunk debe ser > 1 (inf: todos los restantes)")
        self.factor = factor
        self._size = 1

    def reset(self) -> None:
        self._size = 1

    def next_size(self, remaining: int) -> int:
        return max(1, min(self._size, remaining))

    def on_optimal(self, remaining: int) -> None:
        self._size = 1

    def on_infeasible(self, remaining: int) -> None:
        # Crece al menos en uno: con factores menores que 2, int(K·1) = 1 no crecería nunca.
        grown = remaining if self.factor == float("inf") else max(self._size + 1, math.ceil(self.factor * self._size))
        self._size = max(1, min(grown, remaining))


class DoublingChunks(GeometricChunks):
    """Política original de GuroBack y RoundingBack: ``k`` se duplica tras un infactible."""

    name = "doubling"

    def __init__(self) -> None:
        super().__init__(2)


class AllChunks(GeometricChunks):
    """``K = ∞``: tras un infactible, todos los candidatos restantes en un sondeo."""

    name = "all"

    def __init__(self) -> None:
        super().__init__(float("inf"))


class FixedChunks(ChunkPolicy):
    """Chunks de tamaño constante ``k`` (``k = 1``: una variable por sondeo)."""

    name = "fixed"

    def __init__(self, size: int = 1) -> None:
        if size < 1:
            raise ValueError("el tamaño del chunk debe ser >= 1")
        self._size = size

    def reset(self) -> None: ...

    def next_size(self, remaining: int) -> int:
        return max(1, min(self._size, remaining))

    def on_optimal(self, remaining: int) -> None: ...

    def on_infeasible(self, remaining: int) -> None: ...


class MaxFlipChunks(ChunkPolicy):
    """Todos los candidatos en cada llamada, maximizando cuántos cambian (``chunks=maxflip[:T]``).

    No es un tamaño de chunk sino otro tipo de sondeo (ver el docstring del módulo); el
    extractor lo reconoce y usa :meth:`BackboneExtractor._maxflip_loop`. ``stall`` = ``T``:
    segundos sin refutar nada nuevo tras los que se corta una llamada que ya refutó algo
    (``None``: cada llamada hasta demostrar su óptimo).
    """

    name = "maxflip"

    def __init__(self, stall: Optional[float] = None) -> None:
        if stall is not None and not stall >= 0:
            raise ValueError("el estancamiento de chunks=maxflip debe ser >= 0 segundos")
        self.stall = stall

    def reset(self) -> None: ...

    def next_size(self, remaining: int) -> int:
        return max(1, remaining)

    def on_optimal(self, remaining: int) -> None: ...

    def on_infeasible(self, remaining: int) -> None: ...


# ---------------------------------------------------------------------------- coordinación
class Coordinator(abc.ABC):
    """Puntos de enganche del extractor para el trabajo coordinado.

    Todos los métodos reciben el :class:`BackboneKnowledge` del extractor y
    pueden mutarlo (``merge``/``absorb_solution``) con hechos de otras fuentes;
    el extractor aplica esos hechos al backend antes del siguiente sondeo. La
    implementación por defecto (:class:`NullCoordinator`) no hace nada y
    reproduce la extracción secuencial.
    """

    def on_base_solution(self, knowledge: BackboneKnowledge, backend: SolverBackend) -> None:
        """La instancia base se resolvió: ``knowledge.values`` y ``knowledge.optimum`` están listos."""

    def before_probe(self, knowledge: BackboneKnowledge, backend: SolverBackend) -> bool:
        """Se va a elegir el próximo chunk. Devolver ``False`` detiene la extracción."""
        return True

    def busy_elsewhere(self, knowledge: BackboneKnowledge) -> AbstractSet[VarId]:
        """Variables que otros trabajadores están sondeando ahora (vacío sin coordinación de intenciones)."""
        return frozenset()

    def on_probe_start(self, chunk: Sequence[VarId], whole: bool = False) -> None:
        """Empieza el ``solve`` que sondea ``chunk`` (ya dentro del bloque temporal).

        Junto con :meth:`on_probe_end` delimita el intervalo en el que otro hilo
        puede incorporar hechos ajenos al sondeo en curso (fijaciones vivas) o
        abortarlo porque su resultado ya se conoce. ``whole`` (``chunks=maxflip``):
        el sondeo sólo está decidido cuando lo está cada variable del chunk, no en
        cuanto otro refuta una.
        """

    def on_probe_end(self, chunk: Sequence[VarId]) -> None:
        """El ``solve`` del sondeo de ``chunk`` devolvió (con cualquier estado)."""

    def on_probe_result(
        self,
        knowledge: BackboneKnowledge,
        chunk: Sequence[VarId],
        result: SolveResult,
        proven: Sequence[VarId],
        refuted: Sequence[VarId],
        solution: Mapping[VarId, bool],
    ) -> None:
        """Un sondeo terminó: ``proven`` son backbones nuevos, ``refuted`` candidatos descartados.

        ``solution`` es la solución óptima encontrada (vacía si fue infactible);
        vale la pena compartirla porque refuta candidatos en los demás trabajadores.
        """

    def base_accepted(self) -> bool:
        """``False`` si ``on_base_solution`` rechazó la base: el extractor no deriva hechos de ella."""
        return True

    def shared_instance(self) -> Optional[OPBInstance]:
        """La instancia ya leída en Python por quien coordina (p. ej. su comprobador exacto), o ``None``.

        La usan la rotación de modelos y la propagación previa para no leerla otra vez.
        """
        return None

    def on_derived(self, knowledge: BackboneKnowledge, backbone: Sequence[VarId], refuted: Sequence[VarId]) -> None:
        """Hechos obtenidos sin sondeo: rotación de la base, literales fijados en la raíz, propagación previa."""

    def validate_assignment(self, values: Mapping[VarId, bool]) -> bool:
        """Como :meth:`validate_solution`, para una asignación completa ya leída (incumbentes de ``maxflip``)."""
        return True

    def on_incumbent(self, knowledge: BackboneKnowledge, refuted: Sequence[VarId], solution: Mapping[VarId, bool]) -> None:
        """Una incumbente de ``maxflip`` refutó ``refuted`` en mitad de la llamada (``solution``: los candidatos)."""

    def on_probe_stalled(self, knowledge: BackboneKnowledge, chunk: Sequence[VarId]) -> bool:
        """Una llamada ``maxflip`` se cortó por estancamiento. ``True`` = seguir con la siguiente."""
        return True

    def validate_solution(self, backend: SolverBackend) -> bool:
        """Un sondeo devolvió una solución (aún en el bloque temporal). ``False`` = no fiarse de ella.

        Se invoca antes de refutar nada. Si devuelve ``False``, la solución no
        refuta ningún candidato: un chunk de varias variables se vuelve a
        sondear variable a variable, y una variable cuya sonda individual sigue
        dando una solución no válida se abandona (queda desconocida para este
        extractor). ParaBack la usa para descartar soluciones que, evaluadas con
        aritmética exacta, no son factibles (:mod:`guroback.exact`). Por
        defecto se acepta todo, como en la extracción secuencial.
        """
        return True

    def on_inconclusive(self, knowledge: BackboneKnowledge, chunk: Sequence[VarId], result: SolveResult) -> bool:
        """Un sondeo no concluyó (límite/interrupción). ``True`` = seguir; ``False`` = abandonar.

        Si se devuelve ``True`` el extractor aplaza el chunk al final del orden de
        candidatos y continúa con el siguiente (sondeos por rebanadas de tiempo).
        """
        return False

    def on_finish(self, knowledge: BackboneKnowledge, status: RunStatus) -> None:
        """La extracción terminó con ``status`` (``complete``, ``infeasible`` o ``interrupted``)."""


class NullCoordinator(Coordinator):
    """Sin coordinación: extracción secuencial clásica."""


# ---------------------------------------------------------------------------- resultado
@dataclass
class BackboneResult:
    """Resumen de una ejecución del extractor."""

    #: ``COMPLETE``, ``INFEASIBLE`` (instancia base sin solución) o ``INTERRUPTED``
    #: (límite de tiempo/nodos, interrupción o parada pedida por el coordinador).
    status: RunStatus
    is_optimization: bool
    optimum: Optional[float]
    #: Pares (nombre de variable, valor) demostrados backbone, en orden de descubrimiento.
    backbone: List[Tuple[str, bool]] = field(default_factory=list)
    solve_count: int = 0
    total_solve_time: float = 0.0
    #: Número de sondeos realizados por este extractor (sin contar la resolución base).
    probe_count: int = 0
    #: Variables refutadas (nombre), útil para la coordinación y los informes.
    refuted: List[str] = field(default_factory=list)
    #: Hechos obtenidos sin sondear (``docs/PLAN_REFACTOR.md`` fase 7): refutadas por rotación
    #: de modelos, backbones fijados por el solver en la raíz y por la propagación PB previa.
    refuted_by_rotation: int = 0
    fixed_by_solver: int = 0
    forced_prepass: int = 0
    #: ``chunks=maxflip``: refutadas por incumbentes en mitad de una llamada, y llamadas
    #: cortadas por estancamiento.
    refuted_by_incumbent: int = 0
    maxflip_stalls: int = 0

    @property
    def backbone_literals(self) -> List[str]:
        """Literales en el formato de salida (``"12"`` o ``"-12"``)."""
        return [_literal(name, value) for name, value in self.backbone]


def literal_label(name: str) -> str:
    """Etiqueta numérica de una variable ``x<k>`` (el C++ original recorta la ``x``)."""
    if len(name) > 1 and name[0] == "x" and name[1:].isdigit():
        return name[1:]
    return name


def _literal(name: str, value: bool) -> str:
    return ("" if value else "-") + literal_label(name)


def to_bool(value: float) -> bool:
    return value > 0.5


# ---------------------------------------------------------------------------- extractor
class BackboneExtractor:
    """Extractor de backbones sobre un :class:`SolverBackend` ya cargado.

    ``order``, ``chunks`` y ``coordinator`` son opcionales; sus valores por
    defecto reproducen el algoritmo secuencial original.

    ``base_solution`` (con ``optimum`` en problemas de optimización) sustituye la
    búsqueda de la solución base por su **verificación**: se fija ``z*`` y todas
    las variables dadas, y un único ``solve`` —pura propagación, lineal en la
    práctica— confirma que el backend acepta esa asignación con ese coste. Es
    lo que usa un trabajador de ParaBack con la solución óptima de otro
    (``BASE_HINT``). Si el backend la rechaza (tolerancias distintas, p. ej.)
    se lanza :class:`InconsistentKnowledge` en lugar de seguir con otra base.
    """

    def __init__(
        self,
        backend: SolverBackend,
        optimum: Optional[float] = None,
        *,
        order: Optional[VariableOrder] = None,
        chunks: Optional[ChunkPolicy] = None,
        coordinator: Optional[Coordinator] = None,
        base_solution: Optional[Mapping[VarId, bool]] = None,
        rotation: Optional[ModelRotation] = None,
        use_fixed: bool = False,
        forced_prepass: bool = False,
    ) -> None:
        if base_solution is not None and optimum is None and backend.has_objective():
            raise ValueError("base_solution exige optimum en problemas de optimización (hay que verificar el coste)")
        self._backend = backend
        self._given_optimum = optimum
        self._base_solution = base_solution
        self._order = order or FileOrder()
        self._chunks = chunks or DoublingChunks()
        self._coordinator = coordinator or NullCoordinator()
        self._rotation = rotation or NoRotation()
        self._use_fixed = use_fixed and backend.supports_fixed
        self._forced_prepass = forced_prepass
        if isinstance(self._chunks, MaxFlipChunks) and not backend.supports_max_flips:
            raise ValueError(f"chunks=maxflip exige un backend que maximice cambios (gurobi, roundingsat, bruteforce); {backend.name} no lo hace")
        self._refuted_by_rotation = 0
        self._fixed_by_solver = 0
        self._forced_count = 0
        self._refuted_by_incumbent = 0
        self._maxflip_stalls = 0
        #: Estado de la extracción; disponible desde que se resuelve la instancia base.
        self.knowledge: Optional[BackboneKnowledge] = None
        self._probe_count = 0
        self._fixed: Set[VarId] = set()
        self._written: Set[VarId] = set()
        #: Variables que se sondearán una a una (su chunk dio una solución rechazada).
        self._singletons: List[VarId] = []
        #: Variables abandonadas: su sonda individual dio una solución rechazada.
        self.abandoned: Set[VarId] = set()

    @property
    def backend(self) -> SolverBackend:
        return self._backend

    # ------------------------------------------------------------------ API pública
    def run(self, backbone_out: Optional[TextIO] = None) -> BackboneResult:
        """Ejecuta la extracción; escribe líneas ``b <lit>`` y ``b 0`` en ``backbone_out``."""
        backend = self._backend
        is_optimization = backend.has_objective()
        optimum = self._given_optimum

        log.info("Model is an %s problem", "optimization" if is_optimization else "feasibility")
        if optimum is not None:
            if is_optimization:
                log.info("Using optimum specified as parameter: %s", optimum)
                backend.fix_objective_value(optimum)
            else:
                log.info("Model is a feasibility problem, ignoring optimum parameter")
                optimum = None

        values: Optional[Dict[VarId, bool]] = None
        if self._base_solution is not None:
            log.info("Verifying the given base solution")
            result, values = self._verify_base_solution(self._base_solution)
            if not result.is_finished:
                log.info("\tInterrupted")
                return self._finish(RunStatus.INTERRUPTED, is_optimization, optimum)
            if not result.is_optimal:
                raise InconsistentKnowledge(
                    f"el backend {backend.name} rechaza la solución base dada"
                    + (f" con z* = {optimum}" if optimum is not None else "")
                    + f" ({result.status.value}); revise las tolerancias de factibilidad"
                )
        else:
            log.info("Solving the model")
            result = self._solve()
            if not result.is_finished:
                log.info("\tInterrupted")
                return self._finish(RunStatus.INTERRUPTED, is_optimization, optimum)
            if not result.is_optimal:
                log.info("Instance is infeasible or unbounded")
                return self._finish(RunStatus.INFEASIBLE, is_optimization, optimum)

        if is_optimization:
            if self._given_optimum is None:
                assert result.objective is not None, "un resultado OPTIMAL debe traer valor objetivo"
                # Se guarda ya normalizado: es el valor que los backends imponen como
                # ``objetivo == z*`` y así ``BackboneResult.optimum`` es idéntico entre
                # solvers aunque cada uno devuelva z* con su propio ruido flotante.
                optimum = normalize_objective_value(result.objective)
                log.info("Optimum is: %s", optimum)
                backend.fix_objective_value(optimum)
            else:
                log.info("Optimum used leads to feasible solution")
        else:
            log.info("Instance is feasible")

        log.info("Get solution values")
        variables = list(backend.binary_variables())
        if values is None:
            values = {v: to_bool(backend.solution_value(v)) for v in variables}
        knowledge = BackboneKnowledge(values=values, optimum=optimum)
        self.knowledge = knowledge

        backend.set_feasibility_focus()
        self._coordinator.on_base_solution(knowledge, backend)
        if self._coordinator.base_accepted():
            # Una base que el coordinador rechazó (p. ej. no exactamente válida) no deriva nada:
            # el trabajador se retira, no es una contradicción de la instancia.
            self._derive_from_base(knowledge, optimum)

        self._order.prepare(backend)
        ordered = self._order.order(variables, knowledge)
        self._chunks.reset()
        status = self._probe_loop(ordered, knowledge, backbone_out)
        self._sync(knowledge, backbone_out)
        if status is RunStatus.COMPLETE:
            log.info("Backbone extraction complete")
            if backbone_out is not None:
                backbone_out.write("b 0\n")
                backbone_out.flush()
        return self._finish(status, is_optimization, optimum)

    # ------------------------------------------------------------------ bucle de sondeos
    def _probe_loop(self, ordered: List[VarId], knowledge: BackboneKnowledge, backbone_out: Optional[TextIO]) -> RunStatus:
        if isinstance(self._chunks, MaxFlipChunks):
            status = self._maxflip_loop(ordered, knowledge, backbone_out, self._chunks.stall)
            if status is not None:
                return status
            # Sin progreso posible por incumbentes (rechazadas): sondeos clásicos para lo que queda.
            log.info("maxflip: no progress, falling back to doubling chunks")
            self._chunks = DoublingChunks()
        backend = self._backend
        coordinator = self._coordinator
        chunks = self._chunks
        # Sólo una política que redefine select() consulta lo que otros sondean (y se valida).
        custom_select = type(chunks).select is not ChunkPolicy.select
        while True:
            if not coordinator.before_probe(knowledge, backend):
                log.info("\tStopped by coordinator")
                return RunStatus.COMPLETE if knowledge.is_complete() else RunStatus.INTERRUPTED
            if self._use_fixed:
                self._absorb_fixed(knowledge)
            # Hechos nuevos (propios o ajenos) → backend y archivo, antes del próximo sondeo.
            self._sync(knowledge, backbone_out)
            candidates = [v for v in ordered if knowledge.status[v] is VarStatus.UNKNOWN and v not in self.abandoned]
            if not candidates:
                return RunStatus.COMPLETE if knowledge.is_complete() else RunStatus.INTERRUPTED

            self._singletons = [v for v in self._singletons if knowledge.status[v] is VarStatus.UNKNOWN]
            if self._singletons:
                chunk = [self._singletons.pop(0)]
            else:
                size = chunks.next_size(len(candidates))
                if custom_select:
                    chunk = chunks.select(candidates, size, coordinator.busy_elsewhere(knowledge))
                    if not 1 <= len(chunk) <= size or not set(chunk) <= set(candidates):
                        raise ValueError(f"{type(chunks).__name__}.select devolvió un chunk no válido: {chunk}")
                else:
                    chunk = candidates[:size]
            log.info("Candidates=%d chunk_size=%d", len(candidates), len(chunk))
            result, new_values, valid = self._probe(chunk, knowledge.values, candidates, coordinator)
            if not result.is_finished:
                log.info("\tInterrupted")
                if coordinator.on_inconclusive(knowledge, chunk, result):
                    remaining = len(knowledge.unknown())
                    if all(knowledge.status[v] is VarStatus.BACKBONE for v in chunk):
                        # Otro trabajador demostró todo el chunk mientras se sondeaba:
                        # es el resultado que habría dado este sondeo (infactible).
                        chunks.on_infeasible(remaining)
                    elif any(knowledge.status[v] is VarStatus.NOT_BACKBONE for v in chunk):
                        # Otro trabajador refutó una variable del chunk: el sondeo
                        # habría sido factible; se sigue sin aplazar el resto.
                        chunks.on_optimal(remaining)
                    else:
                        # Reintento coordinado: el chunk pasa al final del orden para que
                        # este trabajador avance con otros candidatos (y otro trabajador,
                        # con otra estrategia, pueda resolver el chunk difícil).
                        deferred = set(chunk)
                        ordered[:] = [v for v in ordered if v not in deferred] + list(chunk)
                        chunks.on_inconclusive(len(candidates))
                    continue
                # El coordinador pudo fusionar hechos ajenos que completan el backbone.
                return RunStatus.COMPLETE if knowledge.is_complete() else RunStatus.INTERRUPTED

            if result.is_optimal and not valid:
                if len(chunk) > 1:
                    log.info("\tOptimum rejected by coordinator: probing the chunk variable by variable")
                    self._singletons.extend(chunk)
                else:
                    log.info("\tOptimum rejected by coordinator: abandoning %s", backend.variable_name(chunk[0]))
                    self.abandoned.add(chunk[0])
                continue
            if result.is_optimal:
                log.info("\tOptimum")
                refuted = knowledge.absorb_solution(new_values)
                if self._rotation.active:
                    refuted += self._rotate(knowledge, new_values, candidates)
                    new_values = {c: new_values[c] for c in candidates}  # lo que se comparte, como sin rotación
                remaining = len(candidates) - len(refuted)
                chunks.on_optimal(remaining)
                coordinator.on_probe_result(knowledge, chunk, result, [], refuted, new_values)
            else:
                log.info("\tNOT Optimum")
                proven = knowledge.mark_backbone([(v, knowledge.values[v]) for v in chunk])
                remaining = len(candidates) - len(chunk)
                chunks.on_infeasible(remaining)
                coordinator.on_probe_result(knowledge, chunk, result, proven, [], {})

    def _maxflip_loop(
        self, ordered: List[VarId], knowledge: BackboneKnowledge, backbone_out: Optional[TextIO], stall: Optional[float]
    ) -> Optional[RunStatus]:
        """``chunks=maxflip``: llamadas que maximizan los cambios sobre los candidatos que quedan.

        Devuelve ``None`` si una llamada termina sin refutar nada pese a no demostrar
        nada (todas sus incumbentes rechazadas por la comprobación exacta, o un límite
        por sondeo sin progreso): repetirla daría lo mismo, y el llamador sigue con
        sondeos clásicos, que saben sondear una a una las variables dudosas.
        """
        backend = self._backend
        coordinator = self._coordinator
        while True:
            if not coordinator.before_probe(knowledge, backend):
                log.info("\tStopped by coordinator")
                return RunStatus.COMPLETE if knowledge.is_complete() else RunStatus.INTERRUPTED
            if self._use_fixed:
                self._absorb_fixed(knowledge)
            self._sync(knowledge, backbone_out)
            candidates = [v for v in ordered if knowledge.status[v] is VarStatus.UNKNOWN and v not in self.abandoned]
            if not candidates:
                return RunStatus.COMPLETE if knowledge.is_complete() else RunStatus.INTERRUPTED
            log.info("maxflip: candidates=%d", len(candidates))
            collector = _FlipCollector(self, knowledge, candidates, stall)
            result = self._maxflip_call(candidates, knowledge, collector)
            if result.is_optimal:
                assert result.objective is not None
                if result.objective < 0.5:
                    # Ninguna solución óptima cambia ningún candidato: todos son backbone.
                    log.info("\tOptimum 0: %d backbones", len(candidates))
                    pending = [(v, knowledge.values[v]) for v in candidates if knowledge.status[v] is VarStatus.UNKNOWN]
                    proven = knowledge.mark_backbone(pending)
                    coordinator.on_probe_result(knowledge, candidates, result, proven, [], {})
                    continue
                log.info("\tOptimum %d flips, %d refuted", round(result.objective), collector.refuted)
                coordinator.on_probe_result(knowledge, candidates, result, [], [], {})
                if collector.refuted == 0:
                    return None  # el óptimo cambia candidatos pero ninguna incumbente era válida
                continue
            if not result.is_finished and collector.stopped:
                log.info("\tStalled after %d refuted", collector.refuted)
                self._maxflip_stalls += 1
                if not coordinator.on_probe_stalled(knowledge, candidates):
                    return RunStatus.COMPLETE if knowledge.is_complete() else RunStatus.INTERRUPTED
                continue
            if result.is_finished:
                # Infactible: no puede pasar (x* es factible con 0 cambios) salvo que el solver
                # contradiga la base; se informa como cualquier sondeo infactible del chunk entero.
                raise InconsistentKnowledge(f"el backend {backend.name} declara infactible el sondeo maxflip, pero x* es factible")
            log.info("\tInterrupted")
            if not coordinator.on_inconclusive(knowledge, candidates, result):
                return RunStatus.COMPLETE if knowledge.is_complete() else RunStatus.INTERRUPTED
            if collector.refuted == 0 and all(knowledge.status[v] is VarStatus.UNKNOWN for v in candidates):
                return None  # límite por sondeo sin progreso: la misma llamada daría lo mismo

    def _maxflip_call(self, candidates: Sequence[VarId], knowledge: BackboneKnowledge, collector: "_FlipCollector") -> SolveResult:
        """Una llamada ``maxflip``: las incumbentes llegan a ``collector`` mientras dura el ``solve``."""
        backend = self._backend
        coordinator = self._coordinator
        values = knowledge.values
        self._probe_count += 1
        with backend.probe() as scope:
            scope.maximize_flips([(v, values[v]) for v in candidates])
            if backend.supports_solution_hint:
                backend.add_solution_hint(values)  # x*: factible, con 0 cambios
            backend.set_incumbent_listener(collector)
            coordinator.on_probe_start(candidates, whole=True)
            try:
                result = self._solve()
            finally:
                coordinator.on_probe_end(candidates)
                backend.set_incumbent_listener(None)
            if result.is_optimal and result.objective is not None and result.objective >= 0.5:
                # La solución final, por si el solver no la notificó como incumbente. Con óptimo 0
                # es x* (nada que refutar) y un servidor no tiene solución nueva que dar.
                collector.on_incumbent([backend.solution_value(v) for v in collector.variables])
        return result

    def _sync(self, knowledge: BackboneKnowledge, backbone_out: Optional[TextIO]) -> None:
        """Fija en el backend y escribe en la salida los backbones conocidos y aún no aplicados."""
        backend = self._backend
        for var in knowledge.discovery:
            if var in self._fixed:
                continue
            value = knowledge.values[var]
            backend.fix_variable(var, value)
            self._fixed.add(var)
            if backbone_out is not None and var not in self._written:
                backbone_out.write(f"b {_literal(backend.variable_name(var), value)}\n")
                backbone_out.flush()
                self._written.add(var)

    # ------------------------------------------------------------------ hechos sin sondeo
    def _derive_from_base(self, knowledge: BackboneKnowledge, optimum: Optional[float]) -> None:
        """Propagación PB previa y rotación del modelo base (``docs/PLAN_REFACTOR.md`` fase 7)."""
        backend = self._backend
        shared = self._coordinator.shared_instance()
        proven: List[VarId] = []
        if self._forced_prepass:
            instance = shared if shared is not None else backend.instance()
            if instance is not None:
                proven = self._prepass(knowledge, instance, optimum)
        refuted: List[VarId] = []
        self._rotation.prepare(backend, optimum, shared)
        if self._rotation.active:
            refuted = self._rotate(knowledge, knowledge.values, knowledge.unknown())
        if proven or refuted:
            log.info("Without probing: %d backbone (PB propagation), %d refuted (model rotation)", len(proven), len(refuted))
            self._coordinator.on_derived(knowledge, proven, refuted)

    def _prepass(self, knowledge: BackboneKnowledge, instance: OPBInstance, optimum: Optional[float]) -> List[VarId]:
        """Literales que la propagación unitaria PB exacta fuerza con ``objetivo = z*``: backbones."""
        from .napback_translation import PropagationConflict, forced_literals  # sólo con forced_prepass

        backend = self._backend
        by_name = {backend.variable_name(v): v for v in knowledge.variables}
        try:
            forced = forced_literals(instance, None if optimum is None else int(optimum))
        except PropagationConflict as exc:
            raise InconsistentKnowledge(f"la propagación PB demuestra infactibilidad con z* = {optimum}, pero hay solución base: {exc}") from None
        pairs = []
        for name, value in forced.items():
            var = by_name.get(name)
            if var is None:
                continue
            if knowledge.values[var] != value:
                raise InconsistentKnowledge(f"la propagación PB fuerza {name}={int(value)} pero la solución base tiene {int(not value)}")
            pairs.append((var, value))
        proven = knowledge.mark_backbone(pairs)
        self._forced_count += len(proven)
        return proven

    def _rotate(self, knowledge: BackboneKnowledge, model: Mapping[VarId, bool], candidates: Sequence[VarId]) -> List[VarId]:
        """Refuta por rotación los candidatos aún desconocidos que el modelo deja con su valor base."""
        values = knowledge.values
        same = [v for v in candidates if knowledge.status[v] is VarStatus.UNKNOWN and model.get(v) == values[v]]
        refuted = knowledge.mark_not_backbone(self._rotation.refute(model, same))
        self._refuted_by_rotation += len(refuted)
        return refuted

    def _absorb_fixed(self, knowledge: BackboneKnowledge) -> None:
        """Literales que el solver fijó en la raíz: backbones sin sondear (línea 6 del Algoritmo 1 de CadiBack)."""
        fixed = self._backend.fixed_literals()
        if not fixed:
            return
        pairs = []
        for var, value in fixed.items():
            status = knowledge.status.get(var)
            name = self._backend.variable_name(var)
            if knowledge.values[var] != value:
                raise InconsistentKnowledge(f"el solver fija {name}={int(value)} en la raíz pero la solución base tiene {int(not value)}")
            if status is VarStatus.NOT_BACKBONE:
                raise InconsistentKnowledge(f"el solver fija {name}={int(value)} en la raíz pero la variable ya estaba refutada")
            if status is VarStatus.UNKNOWN:
                pairs.append((var, value))
        proven = knowledge.mark_backbone(pairs)
        if proven:
            self._fixed_by_solver += len(proven)
            log.info("Fixed at the root by the solver: %d backbone", len(proven))
            self._coordinator.on_derived(knowledge, proven, [])

    # ------------------------------------------------------------------ internos
    def _verify_base_solution(self, solution: Mapping[VarId, bool]) -> Tuple[SolveResult, Optional[Dict[VarId, bool]]]:
        """Resuelve con todas las variables de ``solution`` fijadas (bloque temporal).

        Las variables que ``solution`` no menciona quedan libres y toman el valor
        que encuentre el solver.
        """
        backend = self._backend
        with backend.probe() as scope:
            for var, value in solution.items():
                scope.exclude_value(var, not value)
            result = self._solve()
            values = {v: to_bool(backend.solution_value(v)) for v in backend.binary_variables()} if result.is_optimal else None
        return result, values

    def _solve(self) -> SolveResult:
        result = self._backend.solve()
        log.info("RUNTIME: %s seconds", result.runtime)
        return result

    def _probe(
        self,
        chunk: Sequence[VarId],
        values: Mapping[VarId, bool],
        candidates: Sequence[VarId],
        coordinator: Coordinator,
    ) -> Tuple[SolveResult, Dict[VarId, bool], bool]:
        """Sondea ``chunk`` en un bloque temporal: (resultado, valores nuevos, solución aceptada)."""
        backend = self._backend
        new_values: Dict[VarId, bool] = {}
        valid = True
        self._probe_count += 1
        with backend.probe() as scope:
            if len(chunk) == 1:
                scope.exclude_value(chunk[0], values[chunk[0]])
            else:
                scope.require_any_flip([(v, values[v]) for v in chunk])
            coordinator.on_probe_start(chunk)
            try:
                result = self._solve()
            finally:
                coordinator.on_probe_end(chunk)
            if result.is_optimal:
                # Con rotación hace falta el modelo entero, no sólo los candidatos.
                read = backend.binary_variables() if self._rotation.active else candidates
                new_values = {c: to_bool(backend.solution_value(c)) for c in read}
                valid = coordinator.validate_solution(backend)
        return result, new_values, valid

    def _finish(self, status: RunStatus, is_optimization: bool, optimum: Optional[float]) -> BackboneResult:
        backend = self._backend
        knowledge = self.knowledge
        backbone = [] if knowledge is None else [(backend.variable_name(v), val) for v, val in knowledge.backbone()]
        refuted = [] if knowledge is None else [backend.variable_name(v) for v in knowledge.refuted()]
        if knowledge is not None:
            self._coordinator.on_finish(knowledge, status)
        return BackboneResult(
            status=status,
            is_optimization=is_optimization,
            optimum=optimum,
            backbone=backbone,
            solve_count=backend.solve_count,
            total_solve_time=backend.total_solve_time,
            probe_count=self._probe_count,
            refuted=refuted,
            refuted_by_rotation=self._refuted_by_rotation,
            fixed_by_solver=self._fixed_by_solver,
            forced_prepass=self._forced_count,
            refuted_by_incumbent=self._refuted_by_incumbent,
            maxflip_stalls=self._maxflip_stalls,
        )


class _FlipCollector(IncumbentListener):
    """Oyente de una llamada ``maxflip``: refuta y comparte lo que cambia cada incumbente."""

    def __init__(self, extractor: BackboneExtractor, knowledge: BackboneKnowledge, candidates: Sequence[VarId], stall: Optional[float]) -> None:
        self.extractor = extractor
        self.knowledge = knowledge
        self.candidates = list(candidates)
        self.variables = list(extractor.backend.binary_variables())
        self.stall = stall
        #: Refutadas en esta llamada (por incumbentes y por su rotación).
        self.refuted = 0
        #: Incumbentes que cambiaban candidatos pero no pasaron la comprobación exacta.
        self.rejected = 0
        #: ``should_stop`` pidió cortar la llamada (estancamiento).
        self.stopped = False
        self._last_new: Optional[float] = None

    def on_incumbent(self, values: Sequence[float]) -> None:
        knowledge = self.knowledge
        model = {v: to_bool(x) for v, x in zip(self.variables, values)}
        status, base = knowledge.status, knowledge.values
        if not any(status[v] is VarStatus.UNKNOWN and model[v] != base[v] for v in self.candidates):
            return  # nada nuevo (x*, o cambios ya refutados en esta llamada)
        extractor = self.extractor
        coordinator = extractor._coordinator
        if not coordinator.validate_assignment(model):
            self.rejected += 1
            return
        refuted = knowledge.absorb_solution({c: model[c] for c in self.candidates})
        extractor._refuted_by_incumbent += len(refuted)
        if extractor._rotation.active:
            refuted += extractor._rotate(knowledge, model, self.candidates)
        if refuted:
            self.refuted += len(refuted)
            self._last_new = time.monotonic()
            coordinator.on_incumbent(knowledge, refuted, {c: model[c] for c in self.candidates})

    def should_stop(self) -> bool:
        if self.stall is None or self._last_new is None:
            return False  # sin estancamiento, o la llamada aún no refutó nada: hay que demostrar
        if time.monotonic() - self._last_new >= self.stall:
            self.stopped = True
        return self.stopped


#: Registro de estrategias por nombre, para la CLI y las especificaciones de ParaBack.
VARIABLE_ORDERS: Dict[str, type] = {
    cls.name: cls for cls in (FileOrder, ReverseOrder, RandomOrder, StridedOrder, ValueOrder, WeightedPropagationOrder)
}
CHUNK_POLICIES: Dict[str, type] = {cls.name: cls for cls in (DoublingChunks, FixedChunks, GeometricChunks, AllChunks, MaxFlipChunks)}


__all__ = [
    "AllChunks",
    "BackboneExtractor",
    "BackboneKnowledge",
    "BackboneResult",
    "CHUNK_POLICIES",
    "ChunkPolicy",
    "Coordinator",
    "DoublingChunks",
    "FileOrder",
    "FixedChunks",
    "GeometricChunks",
    "InconsistentKnowledge",
    "MaxFlipChunks",
    "NullCoordinator",
    "RandomOrder",
    "ReverseOrder",
    "RunStatus",
    "StrEnum",
    "StridedOrder",
    "VARIABLE_ORDERS",
    "ValueOrder",
    "WeightedPropagationOrder",
    "VarStatus",
    "VariableOrder",
    "literal_label",
    "to_bool",
]

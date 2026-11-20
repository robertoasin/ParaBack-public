"""Rotación de modelos (*flippable*): refutar candidatos sin llamar al solver.

CadiBack (Biere, Froleyks, Wang, SAT 2023, Algoritmos 1 y 2) descarta, tras cada modelo,
los candidatos que se pueden **voltear**: si el modelo con sólo ``x_v`` cambiado sigue
siendo una solución (óptima), ``x_v`` no es backbone. En su evaluación evita el 30 % de las
llamadas al solver, y el 52 % con *really-flip*, que voltea de verdad y sigue con el modelo
nuevo. ``docs/PLAN_REFACTOR.md`` §1.8 y fase 7.

En PBO con ``objetivo = z*`` fijado, voltear ``x_v`` mantiene la optimalidad sólo si
``x_v`` no está en el objetivo (cambiaría el coste) y todas las restricciones en que
aparece siguen satisfechas. La comprobación es exacta (enteros, :mod:`guroback.exact`) y
sólo se hace sobre modelos **exactamente** válidos: un modelo de un solver en coma
flotante que viole algo por tolerancias no rota nada.

* :class:`NoRotation`: no rota (el extractor secuencial por defecto).
* :class:`FlippableRotation`: descarta los candidatos *flippable* del modelo.
* :class:`EagerFlipRotation`: además los voltea, y los siguientes se comprueban sobre el
  modelo volteado (*really-flip*).

Necesita la instancia en Python. ``prepare`` usa la que se le dé (la del comprobador
exacto del trabajador), la del backend (:meth:`SolverBackend.instance`) o, con
``load=True``, la lee; con ``load=False`` y sin instancia en memoria la rotación queda
inactiva (es lo que hace ``rotation=auto`` en ParaBack: no leer la instancia sólo para rotar).
"""
from __future__ import annotations

import abc
import logging
from typing import ClassVar, Dict, Iterable, List, Mapping, Optional, Sequence

from .backend import SolverBackend, VarId
from .exact import ExactChecker
from .opb import OPBInstance, holds, parse_opb

log = logging.getLogger("guroback")


class ModelRotation(abc.ABC):
    """Refuta candidatos a partir de un modelo óptimo, sin llamar al solver."""

    name: ClassVar[str] = "abstract"

    def prepare(self, backend: SolverBackend, optimum: Optional[float], instance: Optional[OPBInstance] = None) -> None:
        """Se invoca una vez, tras la solución base y con ``z*`` (``None`` sin objetivo)."""

    @property
    def active(self) -> bool:
        return False

    def refute(self, model: Mapping[VarId, bool], candidates: Iterable[VarId]) -> List[VarId]:
        """Candidatos (con el mismo valor en ``model`` que en la base) que no son backbone por rotación."""
        return []


class NoRotation(ModelRotation):
    name = "none"


class FlippableRotation(ModelRotation):
    """Descarta los candidatos que se pueden voltear en el modelo (``flippable`` de CadiBack)."""

    name = "flippable"
    #: Voltear de verdad cada candidato que se puede voltear (``really-flip``).
    eager: ClassVar[bool] = False
    #: Tras tantos modelos seguidos sin refutar nada, la rotación se hace sólo en uno de cada
    #: :attr:`THROTTLED_EVERY` (cuesta O(términos) en Python por modelo; el RoundingBack
    #: nativo también la frena cuando deja de rendir, ``Solver::cleanVarState``).
    BARREN_LIMIT: ClassVar[int] = 10
    THROTTLED_EVERY: ClassVar[int] = 16

    def __init__(self, load: bool = True) -> None:
        #: Leer la instancia si no está ya en memoria.
        self.load = load
        self._checker: Optional[ExactChecker] = None
        self._index_of: List[Optional[int]] = []
        self._optimum: Optional[float] = None
        self._objective_coef: Dict[int, int] = {}
        #: Candidatos descartados por rotación (estadística).
        self.refuted = 0
        #: Modelos que no rotaron porque no eran exactamente válidos.
        self.invalid_models = 0
        #: Modelos seguidos sin refutar nada, y modelos saltados por el freno.
        self._barren = 0
        self._seen = 0
        self.skipped_models = 0

    @property
    def active(self) -> bool:
        return self._checker is not None

    def prepare(self, backend: SolverBackend, optimum: Optional[float], instance: Optional[OPBInstance] = None) -> None:
        if instance is None:
            instance = backend.instance()
        if instance is None and self.load:
            instance = parse_opb(backend.instance_path)
        if instance is None:
            log.info("rotación de modelos inactiva: la instancia no está en memoria")
            return
        checker = ExactChecker(backend.instance_path, instance)
        index = checker._index
        self._index_of = [index.get(backend.variable_name(v)) for v in backend.binary_variables()]
        self._optimum = optimum
        if instance.objective is not None:
            for coef, i in instance.objective:
                self._objective_coef[i] = self._objective_coef.get(i, 0) + coef
        self._checker = checker

    def refute(self, model: Mapping[VarId, bool], candidates: Iterable[VarId]) -> List[VarId]:
        checker = self._checker
        if checker is None:
            return []
        self._seen += 1
        if self._barren >= self.BARREN_LIMIT and self._seen % self.THROTTLED_EVERY:
            self.skipped_models += 1
            return []
        inst = checker.instance
        x = bytearray(checker.num_vars)
        for var, value in model.items():
            i = self._index_of[var]
            if value and i is not None:
                x[i] = 1
        # Sólo un modelo exactamente válido (y de coste z*) justifica una refutación.
        if checker._objective_violation(x, self._optimum) is not None:
            self.invalid_models += 1
            return []
        lhs = checker._lhs(x)
        if checker._first_violation(x, lhs) is not None:
            self.invalid_models += 1
            return []
        refuted: List[VarId] = []
        for var in candidates:
            i = self._index_of[var]
            if i is None:
                continue  # la instancia no la conoce: no se arriesga nada
            if self._optimum is not None and self._objective_coef.get(i, 0):
                continue  # voltearla cambia el coste: no sigue siendo óptima
            delta = self._flip_deltas(inst, checker, i, x, lhs)  # O(apariciones de i)
            if delta is None:
                continue
            refuted.append(var)
            if self.eager:
                x[i] ^= 1
                for c, d in delta.items():
                    lhs[c] += d
        self.refuted += len(refuted)
        self._barren = 0 if refuted else self._barren + 1
        return refuted

    @staticmethod
    def _flip_deltas(inst: OPBInstance, checker: ExactChecker, i: int, x: bytearray, lhs: Sequence[int]) -> Optional[Dict[int, int]]:
        """Cambio del lado izquierdo de cada restricción de ``i`` al voltearla, o ``None`` si alguna se viola."""
        deltas = checker.flip_deltas(i, x)  # suma los coeficientes si aparece varias veces
        for c, d in deltas.items():
            if not holds(lhs[c] + d, inst.operator(c), inst.rhs(c)):
                return None
        return deltas


class EagerFlipRotation(FlippableRotation):
    """``really-flip``: voltea cada candidato que se puede voltear y sigue con el modelo nuevo."""

    name = "eager"
    eager = True


#: Rotaciones por nombre (``WorkerSpec.rotation``; ``auto`` lo resuelve ParaBack).
ROTATIONS = ("none", "flippable", "eager")


def make_rotation(name: str, load: bool = True) -> ModelRotation:
    """La rotación ``name``; ``load``: leer la instancia si no está en memoria."""
    if name == NoRotation.name:
        return NoRotation()
    if name == FlippableRotation.name:
        return FlippableRotation(load=load)
    if name == EagerFlipRotation.name:
        return EagerFlipRotation(load=load)
    raise ValueError(f"rotación desconocida {name!r}; disponibles: {', '.join(ROTATIONS)}")


__all__ = ["EagerFlipRotation", "FlippableRotation", "ModelRotation", "NoRotation", "ROTATIONS", "make_rotation"]

"""Comprobación exacta de soluciones 0/1 contra la instancia OPB.

Gurobi y SCIP trabajan en coma flotante con tolerancias: con coeficientes
grandes (``khb05250``: ``z*`` ≈ 1.1·10¹¹) pueden devolver como óptima una
asignación que, redondeada a 0/1 y evaluada con enteros, viola restricciones o
no alcanza ``objetivo == z*``. Una solución así refutaría candidatos que en
realidad son backbone. :class:`ExactChecker` evalúa la asignación completa
con aritmética entera (coste lineal en el tamaño de la instancia), para que
ParaBack sólo comparta refutaciones respaldadas por una solución exacta.

Sólo puede validar soluciones: una respuesta "infactible" (backbone) de un
solver en coma flotante no tiene certificado barato que comprobar.

El comprobador no copia la instancia: trabaja sobre los arrays planos de
:class:`~guroback.opb.OPBInstance`, que el trabajador comparte con su backend.
"""
from __future__ import annotations

from array import array
from collections import Counter
from itertools import chain, compress, repeat
from typing import Dict, Iterator, List, Mapping, Optional, Sequence

from .opb import OPERATORS, OPBInstance, holds, parse_opb


class ExactChecker:
    """Instancia OPB preparada para evaluar asignaciones por nombre de variable."""

    def __init__(self, instance_path: str, instance: Optional[OPBInstance] = None) -> None:
        """``instance``: la misma instancia ya leída con :func:`parse_opb` (evita leerla otra vez)."""
        if instance is None:
            instance = parse_opb(instance_path)
        self.instance = instance
        self._index: Dict[str, int] = {name: i for i, name in enumerate(instance.names)}
        #: Apariciones de cada variable en formato CSR (bajo demanda, sólo para volteos): la
        #: restricción y el coeficiente de cada término en que aparece.
        self._occ_start: Optional["array[int]"] = None
        self._occ: Optional["array[int]"] = None
        self._occ_coef: Optional[Sequence[int]] = None

    @property
    def num_vars(self) -> int:
        return len(self._index)

    @property
    def names(self) -> List[str]:
        return self.instance.names

    @property
    def has_objective(self) -> bool:
        return self.instance.objective is not None

    def _bits(self, values: Mapping[str, bool]) -> bytearray:
        x = bytearray(len(self._index))
        index = self._index
        for name, value in values.items():
            if value:
                i = index.get(name)
                if i is not None:
                    x[i] = 1
        return x

    def objective_value(self, values: Mapping[str, bool]) -> Optional[int]:
        """Coste exacto de ``values`` (las ausentes valen 0), o ``None`` sin función objetivo."""
        if self.instance.objective is None:
            return None
        return self.instance.objective_value(self._bits(values))

    def _iter_lhs(self, x: bytearray) -> Iterator[int]:
        """Lado izquierdo exacto de cada restricción bajo ``x``, en orden.

        ``compress`` + ``map`` recorren los términos en C sobre vistas de los arrays planos.
        """
        inst = self.instance
        coefs: Sequence[int] = memoryview(inst._coefs) if isinstance(inst._coefs, array) else inst._coefs
        indices = memoryview(inst._indices)
        start = inst._start
        get = x.__getitem__
        a = 0
        for c in range(len(inst._rhs)):
            b = start[c + 1]
            yield sum(compress(coefs[a:b], map(get, indices[a:b])))
            a = b

    def _lhs(self, x: bytearray) -> List[int]:
        return list(self._iter_lhs(x))

    def _first_violation(self, x: bytearray, lhs: Optional[List[int]] = None) -> Optional[str]:
        inst = self.instance
        ops, rhs_all = inst._ops, inst._rhs
        values = self._iter_lhs(x) if lhs is None else iter(lhs)
        for c, value in enumerate(values):
            op, rhs = OPERATORS[ops[c]], rhs_all[c]
            if not holds(value, op, rhs):
                terms = inst.terms_of(c)
                shown = " ".join(f"{coef:+d} x{i}" for coef, i in terms[:4]) + (" …" if len(terms) > 4 else "")
                return f"restricción violada: {shown} {op} {rhs} (lado izquierdo exacto {value})"
        return None

    def _objective_violation(self, x: bytearray, optimum: Optional[float]) -> Optional[str]:
        if optimum is None or self.instance.objective is None:
            return None
        cost = self.instance.objective_value(x)
        if cost != optimum:
            return f"objetivo exacto {cost} ≠ z* = {optimum:.0f} (diferencia {cost - optimum:+.0f})"
        return None

    def violation(self, values: Mapping[str, bool], optimum: Optional[float] = None) -> Optional[str]:
        """``None`` si ``values`` es exactamente factible (y de coste ``optimum``); si no, el motivo.

        Una variable del archivo ausente de ``values`` se toma como 0: si eso
        viola algo, la solución se rechaza (nunca se acepta una solución dudosa).
        """
        x = self._bits(values)
        return self._objective_violation(x, optimum) or self._first_violation(x)

    def model(self, values: Mapping[str, bool], optimum: Optional[float] = None) -> "ExactModel":
        """Evalúa ``values`` una vez para poder comprobar después volteos de una variable.

        Lo usa el trabajador Cadiback: una refutación por *flippable* tiene como
        testigo el último modelo con un único literal cambiado.
        """
        return ExactModel(self, values, optimum)

    def _build_occurrences(self) -> None:
        """CSR de apariciones por variable, con ordenación y ``map`` (en C) y no un bucle Python."""
        inst = self.instance
        n = len(self._index)
        indices = inst._indices
        start = inst._start
        # Restricción de cada término, en el orden de los arrays planos.
        constraint_of = array("i", chain.from_iterable(repeat(c, start[c + 1] - start[c]) for c in range(len(inst._rhs))))
        order = sorted(range(len(indices)), key=indices.__getitem__)  # estable: restricciones en orden
        counts = array("q", bytes(8 * (n + 1)))
        for j, k in Counter(indices).items():
            counts[j + 1] = k
        for j in range(n):
            counts[j + 1] += counts[j]
        coefs = inst._coefs
        self._occ = array("i", map(constraint_of.__getitem__, order))
        picked = map(coefs.__getitem__, order)
        self._occ_coef = array("q", picked) if isinstance(coefs, array) else list(picked)
        self._occ_start = counts

    def occurrences(self, i: int) -> "memoryview":
        """Restricciones en que aparece la variable de índice ``i``, una vez por término (CSR)."""
        if self._occ is None or self._occ_start is None:
            self._build_occurrences()
        assert self._occ is not None and self._occ_start is not None
        return memoryview(self._occ)[self._occ_start[i] : self._occ_start[i + 1]]

    def flip_deltas(self, i: int, x: bytearray) -> Dict[int, int]:
        """Cambio del lado izquierdo de cada restricción de ``i`` si se voltea en ``x`` (O(apariciones))."""
        occ = self.occurrences(i)
        assert self._occ_coef is not None and self._occ_start is not None
        a = self._occ_start[i]
        sign = -1 if x[i] else 1
        deltas: Dict[int, int] = {}
        for c, k in zip(occ, self._occ_coef[a : a + len(occ)]):
            deltas[c] = deltas.get(c, 0) + sign * k
        return deltas


class ExactModel:
    """Un modelo evaluado con enteros: su violación y la de cada volteo de una variable."""

    def __init__(self, checker: ExactChecker, values: Mapping[str, bool], optimum: Optional[float]) -> None:
        self._checker = checker
        self._optimum = optimum
        x = checker._bits(values)
        self._x = x
        # Una sola pasada por las restricciones: lados izquierdos y primera violación.
        self._lhs = checker._lhs(x)
        self.violation: Optional[str] = checker._objective_violation(x, optimum) or checker._first_violation(x, self._lhs)
        self._objective_coef: Dict[int, int] = {}
        objective = checker.instance.objective
        if objective is not None:
            for coef, i in objective:
                self._objective_coef[i] = self._objective_coef.get(i, 0) + coef

    def flip_violation(self, name: str) -> Optional[str]:
        """``None`` si el modelo con ``name`` volteado sigue siendo exactamente factible (y de coste z*)."""
        if self.violation is not None:
            return f"el modelo ya no era válido: {self.violation}"
        checker = self._checker
        i = checker._index.get(name)
        if i is None:
            return None  # la variable no aparece en la instancia: voltearla no cambia nada
        delta_sign = -1 if self._x[i] else 1
        if self._optimum is not None and self._objective_coef.get(i, 0):
            return f"voltear {name} cambia el objetivo en {delta_sign * self._objective_coef[i]:+d}"
        inst = checker.instance
        for c, delta in checker.flip_deltas(i, self._x).items():
            lhs = self._lhs[c] + delta
            op, rhs = inst.operator(c), inst.rhs(c)
            if not holds(lhs, op, rhs):
                return f"voltear {name} viola una restricción (lado izquierdo {lhs} {op} {rhs})"
        return None


__all__ = ["ExactChecker", "ExactModel"]

"""Cotas duales seguras de la relajación lineal, con aritmética racional.

Para ``min c·x`` con ``A x ≥ b`` (filas ``≥``), ``A x ≤ b`` (filas ``≤``), ``A x = b`` (filas
``=``) y cotas ``l ≤ x ≤ u``, **cualquier** vector dual ``y`` con el signo correcto (``y ≥ 0`` en
las filas ``≥``, ``y ≤ 0`` en las ``≤``, libre en las ``=``) da la cota lagrangiana

    c·x ≥ y·b + Σ_j min(r_j l_j, r_j u_j),   con  r = c − Aᵀy   (los costes reducidos),

válida para toda ``x`` factible, exacta o no la ``y``. Se toma la ``y`` del simplex en coma
flotante, se convierte a racional (es un número binario exacto) y se evalúa la cota con
:class:`fractions.Fraction` sobre los coeficientes enteros de la instancia: el resultado es
una cota inferior **demostrada** del coste de toda solución, sin tolerancias (la idea de
Neumaier y Shcherbina, 2004). Con el óptimo ``z*`` entero conocido:

* si ``r_j > 0`` y ``LB + r_j > z*``, ninguna solución óptima tiene ``x_j = 1``: ``x_j = 0`` es backbone;
* si ``r_j < 0`` y ``LB − r_j > z*``, ninguna tiene ``x_j = 0``: ``x_j = 1`` es backbone

(fijar ``x_j`` al valor contrario a su signo encarece la cota en ``|r_j|``). Es la fijación por
costes reducidos del branch-and-bound, aquí como certificado de backbone.

La misma cuenta sin objetivo certifica una **infactibilidad**: para toda ``x`` factible,
``0 = r·x + y·Ax ≥ y·b + Σ_j min(r_j l_j, r_j u_j)`` con ``r = −Aᵀy``, así que si el lado derecho
es positivo no hay ninguna (lema de Farkas). Un LP que el solver declara infactible sólo es un
hecho si su rayo de Farkas, pasado a racionales, lo demuestra (:func:`proves_infeasible_by_ray`).
"""
from __future__ import annotations

from fractions import Fraction
from typing import List, Optional, Sequence, Tuple

from guroback.opb import OPBInstance

#: Denominador máximo al convertir un dual en coma flotante a racional (acota el tamaño de los
#: números sin perder validez: cualquier ``y`` del signo correcto da una cota válida).
MAX_DENOMINATOR = 1 << 40


def rational_duals(instance: OPBInstance, duals: Sequence[float]) -> List[Fraction]:
    """Duales en racionales con el signo que exige cada fila (los de signo malo se anulan)."""
    out: List[Fraction] = []
    for c, value in enumerate(duals):
        op = instance.operator(c)
        y = Fraction(value).limit_denominator(MAX_DENOMINATOR) if value else Fraction(0)
        if op == ">=" and y < 0:
            y = Fraction(0)
        elif op == "<=" and y > 0:
            y = Fraction(0)
        out.append(y)
    return out


def safe_bound(
    instance: OPBInstance, y: Sequence[Fraction], lower: Sequence[int], upper: Sequence[int], minimize: bool = True,
    objective: bool = True,
) -> Tuple[Fraction, List[Fraction]]:
    """``(cota inferior demostrada, costes reducidos)`` del objetivo (negado si ``maximize``) con las cotas dadas.

    Con ``objective=False``, la del objetivo nulo: positiva, demuestra que no hay solución con esas cotas.
    """
    n = instance.num_vars
    reduced = [Fraction(0)] * n
    if objective and instance.objective is not None:
        sign = 1 if minimize else -1
        for coef, idx in instance.objective:
            reduced[idx] += sign * coef
    bound = Fraction(0)
    for c in range(len(instance.constraints)):
        yc = y[c]
        if not yc:
            continue
        bound += yc * instance.rhs(c)
        for coef, idx in instance.terms_of(c):
            reduced[idx] -= yc * coef
    for j in range(n):
        r = reduced[j]
        if r:
            bound += min(r * lower[j], r * upper[j])
    return bound, reduced


def reduced_cost_fixings(
    bound: Fraction, reduced: Sequence[Fraction], lower: Sequence[int], upper: Sequence[int], optimum: int
) -> List[Tuple[int, bool]]:
    """Variables libres que la cota fija en toda solución óptima: ``(índice, valor backbone)``."""
    out: List[Tuple[int, bool]] = []
    for j, r in enumerate(reduced):
        if lower[j] == upper[j] or not r:
            continue
        if r > 0 and bound + r > optimum:
            out.append((j, False))
        elif r < 0 and bound - r > optimum:
            out.append((j, True))
    return out


def proves_infeasible(bound: Fraction, optimum: int) -> bool:
    """La cota demuestra que con las cotas actuales no hay solución de coste ``z*``."""
    return bound > optimum


def proves_infeasible_by_ray(
    instance: OPBInstance, ray: Sequence[float], lower: Sequence[int], upper: Sequence[int]
) -> bool:
    """¿Demuestra el rayo de Farkas ``ray`` (en coma flotante, uno por fila) que no hay solución con estas cotas?

    Se prueban el rayo y su opuesto (la convención de signo es de cada solver): la cuenta es válida
    con cualquier ``y`` del signo correcto, así que probar las dos no puede dar una prueba falsa.
    """
    for sign in (1, -1):
        y = rational_duals(instance, [sign * value for value in ray])
        bound, _ = safe_bound(instance, y, lower, upper, objective=False)
        if bound > 0:
            return True
    return False


__all__ = [
    "MAX_DENOMINATOR", "proves_infeasible", "proves_infeasible_by_ray", "rational_duals", "reduced_cost_fixings", "safe_bound",
]

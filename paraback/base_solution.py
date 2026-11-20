"""Solución base dada por el usuario (``--base-solution``): x* óptima de una ejecución anterior.

Con ella ningún trabajador resuelve la instancia base: cada uno **verifica** x* en z*
(un solve con todas las variables fijadas, como con el ``BASE_HINT`` de otro
trabajador; ``docs/PLAN_PARABACK.md`` §7.2) y empieza a sondear. Es sólido porque el
backbone se define respecto de cualquier solución óptima, siempre que x* lo sea de verdad.

Formato: la salida de RoundingSat (y de los solvers de la PB Competition)::

    o 2
    s OPTIMUM FOUND
    v x1 -x2 -x3 ... (una o varias líneas "v")

Dos niveles de comprobación:

* :func:`read_base_solution` (lo que usa ParaBack): sólo el archivo, sin leer la instancia.
  Exige ``s OPTIMUM FOUND`` si hay ``o`` (``s SATISFIABLE`` sin optimalidad probada no
  sirve: el backbone saldría mal) y toma z* del último ``o``. La verificación de verdad la
  hace cada trabajador: fija z* y x* y resuelve (exacto en RoundingSat; en Gurobi/SCIP la
  solución pasa además por :class:`guroback.exact.ExactChecker`). Si x* no es factible o no
  cuesta z*, la ejecución falla (``InconsistentKnowledge``); una variable que falte queda
  libre y el solver la completa con coste z*, lo que sigue siendo una solución óptima.
  Leer la instancia aquí, antes de mpirun, costaba minutos y decenas de GB en las enormes.
* :func:`load_base_solution` / ``python -m paraback.base_solution FILE INSTANCIA``: además lee
  la instancia y comprueba con aritmética entera todas las variables, la factibilidad y que
  el coste coincida con ``o``. Para revisar soluciones antes de lanzar, no en cada ejecución.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from guroback.exact import ExactChecker
from guroback.pb_output import OPTIMUM_FOUND, SATISFIABLE, PBOutputError, parse_pb_output

from .config import ConfigError

_OPTIMAL = OPTIMUM_FOUND
_SATISFIABLE = SATISFIABLE


@dataclass(frozen=True)
class BaseSolution:
    path: str
    values: Dict[str, bool]
    #: z* exacto (``None`` si la instancia no tiene función objetivo).
    optimum: Optional[float]
    status: str


def read_solution_values(path: str) -> Dict[str, bool]:
    """Sólo la asignación (líneas ``v``), sin validar: lo que leen los trabajadores."""
    return _parse(path)[2]


def _parse(path: str) -> Tuple[Optional[str], Optional[int], Dict[str, bool]]:
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ConfigError(f"no se puede leer la solución base {path}: {exc}") from None
    with fh:
        try:
            parsed = parse_pb_output(fh)
        except PBOutputError as exc:
            raise ConfigError(f"{path}: {exc}") from None
    if parsed.repeated:
        raise ConfigError(f"{path}: literal repetido o vacío en las líneas 'v': {parsed.repeated[0]!r}")
    return parsed.status, parsed.objective, parsed.values


def _check_status(path: str, status: Optional[str], values: Dict[str, bool], has_objective: bool) -> None:
    """Hay asignación y su estado prueba optimalidad (``SATISFIABLE`` sólo vale sin objetivo)."""
    if not values:
        raise ConfigError(f"{path}: no hay solución (ninguna línea 'v')")
    accepted = (_OPTIMAL,) if has_objective else (_OPTIMAL, _SATISFIABLE)
    if status not in accepted:
        raise ConfigError(
            f"{path}: estado {status!r}; hace falta {' o '.join(repr(s) for s in accepted)} "
            "(sin optimalidad probada el backbone calculado desde esta solución sería incorrecto)"
        )


def read_base_solution(path: str) -> BaseSolution:
    """Comprobación ligera, sin la instancia: estado, z* (del ``o``) y asignación."""
    status, reported, values = _parse(path)
    _check_status(path, status, values, reported is not None)
    return BaseSolution(path=path, values=values, optimum=None if reported is None else float(reported), status=status or "")


def load_base_solution(path: str, instance: str, checker: Optional[ExactChecker] = None) -> BaseSolution:
    """Lee y valida ``path`` contra ``instance``; ``ConfigError`` con el motivo si no sirve."""
    status, reported, values = _parse(path)
    checker = checker or ExactChecker(instance)
    _check_status(path, status, values, checker.has_objective)
    names = set(checker.names)
    unknown = sorted(set(values) - names)
    if unknown:
        raise ConfigError(f"{path}: variables que no están en {instance}: {', '.join(unknown[:5])}{' …' if len(unknown) > 5 else ''} "
                          "(¿solución de otra instancia?)")
    missing = sorted(names - set(values))
    if missing:
        raise ConfigError(f"{path}: faltan {len(missing)} variables de la instancia (p. ej. {', '.join(missing[:5])})")
    cost = checker.objective_value(values)
    if cost is not None and reported is not None and cost != reported:
        raise ConfigError(
            f"{path}: el objetivo exacto de la solución es {cost} pero el archivo declara óptimo 'o {reported}': "
            "no es la solución cuya optimalidad se probó"
        )
    violation = checker.violation(values, None if cost is None else float(cost))
    if violation is not None:
        raise ConfigError(f"{path}: la solución no es factible para {instance}: {violation}")
    return BaseSolution(path=path, values=values, optimum=None if cost is None else float(cost), status=status or "")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m paraback.base_solution FILE INSTANCIA``: comprobación completa, sin lanzar nada."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("uso: python -m paraback.base_solution SOLUCION INSTANCIA", file=sys.stderr)
        return 2
    try:
        solution = load_base_solution(args[0], args[1])
    except ConfigError as exc:
        print(f"NO VÁLIDA: {exc}")
        return 1
    print(f"válida: {len(solution.values)} variables, z* = {solution.optimum}, estado {solution.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BaseSolution", "load_base_solution", "main", "read_base_solution", "read_solution_values"]

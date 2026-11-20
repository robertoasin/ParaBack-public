"""Comprobación de un backbone contra su definición, con un solver exacto (``--check``).

Lo que hace CadiBack con ``--check`` (Biere, Froleyks, Wang, SAT 2023, §5), sobre la
instancia PB con ``objetivo = z*``:

* cada literal ``l`` declarado backbone: ``¬l`` tiene que ser **infactible**;
* si el archivo está completo (``b 0``), cada variable no declarada tiene que admitir los
  **dos** valores en alguna solución óptima (si no, era backbone y falta).

Es una herramienta de tests y de disputas de paridad (CLAUDE.md §3), no de producción: un
``solve`` por backbone y, como mucho, dos por variable no backbone (los modelos que salen
de cada solve marcan de una vez todas las variables que ya se vieron con los dos valores).
El solver debe ser exacto (RoundingSat, SCIP exacto o fuerza bruta): en coma flotante un
"infactible" no se puede creer (NapBack se declara inexacto a propósito: su traducción no
se da por exacta).

    python -m guroback.check [--solver S] [--optimum Z] INSTANCIA BACKBONE
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from . import get_backend_class
from .backend import SolverBackend, SolveStatus, VarId, normalize_objective_value


@dataclass
class CheckReport:
    """Resultado de la comprobación."""

    #: Errores: backbones falsos o backbones que faltan (vacío si todo cuadra).
    errors: List[str] = field(default_factory=list)
    #: Comprobaciones que el solver no pudo decidir (límite).
    undecided: List[str] = field(default_factory=list)
    backbone_checked: int = 0
    non_backbone_checked: int = 0
    solves: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors and not self.undecided


def read_backbone_file(path: str) -> Tuple[Dict[str, bool], bool]:
    """``({nombre: valor}, completo)`` de un archivo ``b <lit>`` … ``b 0`` (GuroBack, ParaBack)."""
    backbone: Dict[str, bool] = {}
    complete = False
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) != 2 or parts[0] != "b":
                continue
            lit = parts[1]
            if lit == "0":
                complete = True
                continue
            negative = lit.startswith("-")
            label = lit.lstrip("-")
            backbone["x" + label if label.isdigit() else label] = not negative
    return backbone, complete


def _probe(backend: SolverBackend, var: VarId, value: bool) -> SolveStatus:
    """Resuelve con ``x_var = value``."""
    with backend.probe() as scope:
        scope.exclude_value(var, not value)
        return backend.solve().status


def check_backbone(
    backend: SolverBackend, backbone: Mapping[str, bool], complete: bool, optimum: Optional[float] = None
) -> CheckReport:
    """Comprueba ``backbone`` sobre ``backend`` (recién cargado; se resuelve aquí la base)."""
    report = CheckReport()
    if not backend.exact_arithmetic:
        raise ValueError(f"el comprobador necesita un solver exacto; {backend.name} trabaja en coma flotante")
    base = backend.solve()
    report.solves += 1
    if not base.is_optimal:
        if backbone or base.status is not SolveStatus.INFEASIBLE:
            report.errors.append(f"la instancia base no tiene solución óptima ({base.status.value})")
        return report
    if backend.has_objective():
        target = optimum if optimum is not None else base.objective
        assert target is not None
        backend.fix_objective_value(normalize_objective_value(target))
    by_name = {backend.variable_name(v): v for v in backend.binary_variables()}
    unknown_names = sorted(set(backbone) - set(by_name))
    if unknown_names:
        report.errors.append(f"variables que no están en la instancia: {', '.join(unknown_names[:5])}")
    for name, value in backbone.items():
        var = by_name.get(name)
        if var is None:
            continue
        status = _probe(backend, var, not value)
        report.solves += 1
        report.backbone_checked += 1
        if status is SolveStatus.OPTIMAL:
            report.errors.append(f"{name}={int(value)} no es backbone: hay una solución óptima con {name}={int(not value)}")
        elif status is not SolveStatus.INFEASIBLE:
            report.undecided.append(f"{name}: {status.value}")
    if not complete:
        return report
    # Cada variable no declarada tiene que verse con los dos valores en soluciones óptimas.
    seen: Dict[VarId, Set[bool]] = {v: set() for v in by_name.values()}

    def record() -> None:
        for v in backend.binary_variables():
            seen[v].add(backend.solution_value(v) > 0.5)

    for name, var in sorted(by_name.items(), key=lambda kv: kv[1]):
        if name in backbone:
            continue
        report.non_backbone_checked += 1
        for value in (False, True):
            if value in seen[var]:
                continue
            with backend.probe() as scope:
                scope.exclude_value(var, not value)
                result = backend.solve()
                report.solves += 1
                if result.is_optimal:
                    record()
            if result.status is SolveStatus.INFEASIBLE:
                report.errors.append(f"{name}={int(not value)} es backbone y no está declarado")
                break
            if not result.is_optimal:
                report.undecided.append(f"{name}={int(value)}: {result.status.value}")
    return report


def check_file(instance: str, backbone_path: str, solver: str = "roundingsat", optimum: Optional[float] = None,
               params: Optional[Mapping[str, str]] = None) -> CheckReport:
    backbone, complete = read_backbone_file(backbone_path)
    with get_backend_class(solver)(instance, params) as backend:
        return check_backbone(backend, backbone, complete, optimum)


def default_check_solver() -> str:
    """El primer solver exacto disponible: RoundingSat, SCIP exacto o fuerza bruta."""
    for name in ("roundingsat", "scip-exact"):
        try:
            if get_backend_class(name).is_available()[0]:
                return name
        except Exception:  # noqa: BLE001 - no disponible
            continue
    return "bruteforce"


def describe(report: CheckReport) -> str:
    verdict = "correcto" if report.ok else ("INCORRECTO" if report.errors else "sin decidir")
    lines = [f"backbone {verdict}: {report.backbone_checked} backbones y {report.non_backbone_checked} "
             f"no backbones comprobados en {report.solves} llamadas al solver"]
    lines += [f"  error: {e}" for e in report.errors[:20]]
    lines += [f"  sin decidir: {u}" for u in report.undecided[:20]]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m guroback.check", description=__doc__.splitlines()[0])
    p.add_argument("instance")
    p.add_argument("backbone")
    p.add_argument("--solver", default=None, help="solver exacto (por defecto: roundingsat, scip-exact o bruteforce)")
    p.add_argument("--optimum", type=float, default=None, help="z* (por defecto, el que encuentra el solver)")
    args = p.parse_args(argv)
    report = check_file(args.instance, args.backbone, args.solver or default_check_solver(), args.optimum)
    print(describe(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["CheckReport", "check_backbone", "check_file", "default_check_solver", "describe", "read_backbone_file"]

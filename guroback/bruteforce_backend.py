"""Backend de referencia por enumeración exhaustiva.

No es un solver de producción: sirve como **oráculo independiente** para
validar el extractor y la paridad entre backends reales sobre instancias
pequeñas (hasta ``MAX_FREE_VARS`` variables libres). Implementa el contrato
completo de :class:`guroback.backend.SolverBackend`, incluido el ciclo de vida
de sondeos, por lo que también ejercita la interfaz en los tests.
"""
from __future__ import annotations

import itertools
import time
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .backend import Assignment, BoundsProbeScope, ProbeScope, SolveResult, SolveStatus, SolverBackend, VarId, normalize_objective_value
from .opb import OPBInstance, parse_opb

MAX_FREE_VARS = 22


def enumerate_feasible(instance: OPBInstance) -> List[Tuple[int, ...]]:
    """Todas las asignaciones factibles de la instancia (sin cotas adicionales)."""
    if instance.num_vars > MAX_FREE_VARS:
        raise ValueError(f"demasiadas variables para fuerza bruta: {instance.num_vars}")
    feasible: List[Tuple[int, ...]] = []
    for point in itertools.product((0, 1), repeat=instance.num_vars):
        assignment = list(point)
        if all(c.is_satisfied(assignment) for c in instance.constraints):
            feasible.append(point)
    return feasible


def brute_force_backbone(instance: OPBInstance) -> Optional[Dict[VarId, bool]]:
    """Oráculo: backbone exacto por enumeración de todas las soluciones óptimas.

    Devuelve ``None`` si la instancia es infactible.
    """
    feasible = enumerate_feasible(instance)
    if not feasible:
        return None
    if instance.objective is not None:
        sign = 1 if instance.sense == "min" else -1
        best = min(sign * instance.objective_value(list(p)) for p in feasible)
        feasible = [p for p in feasible if sign * instance.objective_value(list(p)) == best]
    backbone: Dict[VarId, bool] = {}
    for idx in range(instance.num_vars):
        values = {p[idx] for p in feasible}
        if len(values) == 1:
            backbone[idx] = bool(values.pop())
    return backbone


class _BruteForceProbeScope(BoundsProbeScope[Tuple[Assignment, ...]]):
    def __init__(self, backend: "BruteForceBackend") -> None:
        super().__init__()
        self._backend = backend

    def _get_bounds(self, var: VarId) -> Tuple[float, float]:
        return self._backend._bounds[var]

    def _set_bounds(self, var: VarId, lb: float, ub: float) -> None:
        self._backend._bounds[var] = (int(lb), int(ub))

    def _add_any_flip(self, assignment: Sequence[Assignment]) -> Tuple[Assignment, ...]:
        cons = tuple(assignment)
        self._backend._flip_constraints.append(cons)
        return cons

    def _remove(self, constraint: Tuple[Assignment, ...]) -> None:
        self._backend._flip_constraints.remove(constraint)

    def _set_flip_objective(self, assignment: Sequence[Assignment]) -> None:
        self._backend._max_flips = tuple(assignment)

    def _restore_objective(self) -> None:
        self._backend._max_flips = None


class BruteForceBackend(SolverBackend):
    """Backend exacto por enumeración; sólo para instancias pequeñas."""

    name = "bruteforce"
    supports_max_flips = True
    #: Enumeración con enteros: sus soluciones son exactas (las subclases de test
    #: que simulan un solver inexacto lo ponen a ``False``).
    exact_arithmetic = True

    def __init__(self, instance_path: str, params: Optional[Mapping[str, str]] = None) -> None:
        super().__init__(instance_path, params)
        self._instance = parse_opb(instance_path)
        self._params: Dict[str, str] = dict(self.params)
        # Enumeración única del conjunto factible base; cada solve filtra esta lista.
        self._feasible = enumerate_feasible(self._instance)
        self._bounds: List[Tuple[int, int]] = [(0, 1)] * self._instance.num_vars
        self._flip_constraints: List[Tuple[Assignment, ...]] = []
        self._objective_fixed_to: Optional[int] = None
        self._objective_active = self._instance.objective is not None
        self._solution: Optional[Tuple[int, ...]] = None
        #: Asignación cuyos cambios de valor se maximizan (``maximize_flips``), o ``None``.
        self._max_flips: Optional[Tuple[Assignment, ...]] = None

    def _close(self) -> None:
        self._feasible = []

    # ------------------------------------------------------------------ introspección
    def instance(self) -> OPBInstance:
        return self._instance

    def binary_variables(self) -> Sequence[VarId]:
        return range(self._instance.num_vars)

    def variable_name(self, var: VarId) -> str:
        return self._instance.names[var]

    def has_objective(self) -> bool:
        return self._instance.objective is not None

    # ------------------------------------------------------------------ parámetros
    def set_parameter(self, name: str, value: str) -> None:
        self._params[name] = value

    def set_feasibility_focus(self) -> None:
        self._params["focus"] = "feasibility"

    # ------------------------------------------------------------------ estado base
    def fix_objective_value(self, value: float) -> None:
        # Como los demás backends: z* normalizado (el objetivo de una instancia OPB es entero).
        self._objective_fixed_to = int(normalize_objective_value(value))
        self._objective_active = False

    def fix_variable(self, var: VarId, value: bool) -> None:
        self._bounds[var] = (int(value), int(value))

    # ------------------------------------------------------------------ optimización
    def _admissible(self, point: Tuple[int, ...]) -> bool:
        for idx, (lb, ub) in enumerate(self._bounds):
            if not lb <= point[idx] <= ub:
                return False
        if self._objective_fixed_to is not None:
            if self._instance.objective_value(list(point)) != self._objective_fixed_to:
                return False
        for cons in self._flip_constraints:
            if not any(point[var] != int(value) for var, value in cons):
                return False
        return True

    def _solve(self) -> SolveResult:
        start = time.perf_counter()
        candidates = [p for p in self._feasible if self._admissible(p)]
        if not candidates:
            self._solution = None
            return SolveResult(SolveStatus.INFEASIBLE, None, time.perf_counter() - start, "infeasible")
        if self._max_flips is not None:
            return self._solve_max_flips(candidates, start)
        if self._objective_active:
            sign = 1 if self._instance.sense == "min" else -1
            candidates.sort(key=lambda p: (sign * self._instance.objective_value(list(p)), p))
            objective = float(self._instance.objective_value(list(candidates[0])))
        else:
            candidates.sort()
            objective = 0.0
        self._solution = candidates[0]
        return SolveResult(SolveStatus.OPTIMAL, objective, time.perf_counter() - start, "optimal")

    def _solve_max_flips(self, candidates: List[Tuple[int, ...]], start: float) -> SolveResult:
        """Como un branch-and-bound: incumbentes de más y más cambios, de una en una, al oyente."""
        assert self._max_flips is not None
        flips = self._max_flips

        def count(point: Tuple[int, ...]) -> int:
            return sum(point[var] != int(value) for var, value in flips)

        candidates.sort(key=lambda p: (count(p), p))
        listener = self._incumbent_listener
        best = -1
        for point in candidates:
            value = count(point)
            if value <= best:
                continue
            best = value
            if listener is not None:
                listener.on_incumbent([float(x) for x in point])
                if listener.should_stop():
                    self._solution = None
                    return SolveResult(SolveStatus.INTERRUPTED, None, time.perf_counter() - start, "interrupted")
        self._solution = candidates[-1]
        return SolveResult(SolveStatus.OPTIMAL, float(best), time.perf_counter() - start, "optimal")

    def solution_value(self, var: VarId) -> float:
        if self._solution is None:
            raise RuntimeError("no hay solución disponible")
        return float(self._solution[var])

    # ------------------------------------------------------------------ sondeos
    def _open_scope(self) -> ProbeScope:
        return _BruteForceProbeScope(self)

"""Backend de Gurobi para la extracción de backbones.

Es la traducción del ``Guroback`` original en C++ (``guroback.cpp``) al
contrato :class:`guroback.backend.SolverBackend`. Gurobi permite modificar
cotas y añadir/quitar restricciones sobre el modelo vivo y volver a llamar a
``optimize`` sin ninguna transición de etapa, por lo que los sondeos se
materializan directamente sobre el modelo base y se deshacen restaurando las
cotas guardadas y eliminando la restricción temporal.

Este backend **no** ofrece fijaciones vivas (``supports_live_fix``): la única
vía para imponer un backbone ajeno dentro de un ``optimize`` en curso son los
cortes de usuario (``cbCut``), que exigen ``PreCrush=1``, y ese parámetro
desactiva reducciones de presolve de las que dependen algunas instancias
(medido en ``fixnet4``: 5 sondeos en 35 s sin él, ninguno en 90 s con él).
Un trabajador Gurobi de ParaBack sigue aprovechando los hechos ajenos entre
sondeos y abandona los sondeos que otro ya decidió (``interrupt``).

``chunks=maxflip`` (``supports_max_flips``): el sondeo maximiza cuántas variables
cambian de valor respecto de x* con ``z*`` fijado, y un *callback* ``MIPSOL`` entrega
cada incumbente al extractor mientras Gurobi sigue buscando. ``MIPGapAbs = 0.99``
durante ese sondeo: el objetivo es entero, así que una incumbente es óptima en
cuanto la cota dual baja de ella + 1.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .backend import (
    BoundsProbeScope,
    Assignment,
    ProbeScope,
    SolveResult,
    SolveStatus,
    SolverBackend,
    VarId,
    normalize_objective_value,
)

# Parámetros aplicados antes de los del usuario (idénticos al ``main`` en C++).
DEFAULT_PARAMS: Mapping[str, str] = {"OutputFlag": "0", "MIPGap": "0.0"}

#: ``MIPGapAbs`` de los sondeos ``maximize_flips``: el objetivo cuenta variables (entero).
MAX_FLIPS_GAP_ABS = 0.99

#: Arrancar dos entornos de Gurobi a la vez desde dos hilos del mismo proceso (lanzador local:
#: un trabajador ``gurobi`` y otro ``lp``) corrompía la memoria de forma intermitente (3 de 8
#: ejecuciones; la validación de la licencia WLS no es reentrante). Toda creación y arranque
#: de un ``Env`` pasa por este cerrojo; bajo MPI cada trabajador es un proceso y no hace falta.
ENV_LOCK = threading.Lock()


def _param_number(value: str) -> Any:
    """Convierte la representación textual de un parámetro a ``int``, ``float`` o la deja como texto."""
    for caster in (int, float):
        try:
            return caster(value)
        except ValueError:
            continue
    return value


class _GurobiProbeScope(BoundsProbeScope[Any]):
    """Cotas del modelo vivo y restricciones ``Constr`` temporales."""

    def __init__(self, backend: "GurobiBackend") -> None:
        super().__init__()
        self._backend = backend

    def _get_bounds(self, var: VarId) -> Tuple[float, float]:
        gvar = self._backend._vars[var]
        return gvar.LB, gvar.UB

    def _set_bounds(self, var: VarId, lb: float, ub: float) -> None:
        gvar = self._backend._vars[var]
        gvar.UB = ub
        gvar.LB = lb

    def _add_any_flip(self, assignment: Sequence[Assignment]) -> Any:
        gp = self._backend._gp
        lhs = gp.LinExpr()
        for var, value in assignment:
            gvar = self._backend._vars[var]
            if value:
                lhs += 1.0 - gvar
            else:
                lhs += gvar
        return self._backend._model.addConstr(lhs >= 1.0)

    def _remove(self, constraint: Any) -> None:
        self._backend._model.remove(constraint)

    def _set_flip_objective(self, assignment: Sequence[Assignment]) -> None:
        backend = self._backend
        gp, model = backend._gp, backend._model
        self._saved_objective = (model.getObjective(), model.ModelSense, model.Params.MIPGapAbs)
        objective = gp.LinExpr()
        constant = 0.0
        for var, value in assignment:
            gvar = backend._vars[var]
            if value:
                constant += 1.0
                objective.addTerms(-1.0, gvar)
            else:
                objective.addTerms(1.0, gvar)
        objective.addConstant(constant)
        model.setObjective(objective, gp.GRB.MAXIMIZE)
        # Objetivo entero: basta demostrar que no hay solución con una variable más cambiada.
        model.Params.MIPGapAbs = MAX_FLIPS_GAP_ABS

    def _restore_objective(self) -> None:
        objective, sense, gap_abs = self._saved_objective
        model = self._backend._model
        model.setObjective(objective, sense)
        model.Params.MIPGapAbs = gap_abs

    def _after_restore(self) -> None:
        self._backend._model.update()


class GurobiBackend(SolverBackend):
    """Backend basado en ``gurobipy``."""

    name = "gurobi"
    supports_interrupt = True
    supports_time_limit = True
    supports_solution_hint = True
    supports_max_flips = True

    _STATUS_MAP: Dict[int, SolveStatus]

    @classmethod
    def is_available(cls) -> Tuple[bool, str]:
        try:
            import gurobipy as gp
        except ImportError as exc:
            return False, f"gurobipy no instalado: {exc}"
        try:
            # Arrancar un entorno vacío es lo que valida la licencia. Bajo el cerrojo: un entorno
            # arrancando mientras otro hilo arranca o libera el suyo corrompía la memoria.
            with ENV_LOCK:
                env = gp.Env(params={"OutputFlag": 0})
                env.dispose()
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de licencia o de librería
            return False, f"licencia de Gurobi no utilizable: {exc}"
        return True, ""

    def __init__(self, instance_path: str, params: Optional[Mapping[str, str]] = None) -> None:
        super().__init__(instance_path, params)
        import gurobipy as gp  # importación diferida: el paquete no exige gurobipy instalado

        self._gp = gp
        with ENV_LOCK:
            self._env = gp.Env(empty=True)
            try:
                for name, value in {**DEFAULT_PARAMS, **self.params}.items():
                    self._env.setParam(name, _param_number(value))
                self._env.start()
            except Exception:
                self._env.dispose()
                raise
        try:
            self._model = gp.read(instance_path, env=self._env)
        except Exception:
            self._env.dispose()
            raise

        GRB = gp.GRB
        self._STATUS_MAP = {
            GRB.OPTIMAL: SolveStatus.OPTIMAL,
            GRB.INFEASIBLE: SolveStatus.INFEASIBLE,
            GRB.UNBOUNDED: SolveStatus.UNBOUNDED,
            GRB.INF_OR_UNBD: SolveStatus.INFEASIBLE_OR_UNBOUNDED,
            GRB.INTERRUPTED: SolveStatus.INTERRUPTED,
        }
        # Algunas constantes (WORK_LIMIT, MEM_LIMIT) no existen en versiones antiguas de gurobipy.
        for limit_name in (
            "CUTOFF", "ITERATION_LIMIT", "NODE_LIMIT", "TIME_LIMIT",
            "SOLUTION_LIMIT", "USER_OBJ_LIMIT", "WORK_LIMIT", "MEM_LIMIT",
        ):
            code = getattr(GRB, limit_name, None)
            if code is not None:
                self._STATUS_MAP[code] = SolveStatus.LIMIT_REACHED

        all_vars = self._model.getVars()
        # Mapeo estable: el VarId es la posición en esta lista, fijada en la carga.
        self._vars = [v for v in all_vars if v.VType == GRB.BINARY]
        self._has_objective = any(v.Obj != 0.0 for v in all_vars)
        self._last_status: Optional[int] = None
        self._solution: Optional[List[float]] = None
        self._stats: Dict[str, float] = {"nodes": 0.0, "iterations": 0.0, "solving_time": 0.0}
        #: Excepción del oyente de incumbentes durante el último ``solve`` (se relanza al terminar).
        self._callback_error: Optional[BaseException] = None

    # ------------------------------------------------------------------ ciclo de vida
    def _close(self) -> None:
        self._solution = None
        with ENV_LOCK:  # liberar un entorno mientras otro hilo arranca el suyo corrompía la memoria
            self._model.dispose()
            self._env.dispose()

    # ------------------------------------------------------------------ introspección
    def binary_variables(self) -> Sequence[VarId]:
        return range(len(self._vars))

    def variable_name(self, var: VarId) -> str:
        return self._vars[var].VarName

    def has_objective(self) -> bool:
        return self._has_objective

    # ------------------------------------------------------------------ parámetros
    def set_parameter(self, name: str, value: str) -> None:
        self._model.setParam(name, _param_number(value))

    def set_feasibility_focus(self) -> None:
        self._model.Params.MIPFocus = 1

    # ------------------------------------------------------------------ estado base
    def fix_objective_value(self, value: float) -> None:
        gp = self._gp
        # Se redondea el óptimo entero para imponer el mismo problema de
        # factibilidad que SCIP (PLAN_SCIP.md §10).
        target = normalize_objective_value(value)
        # Repetida, getObjective() ya estaría vacío y la restricción sería "0 == z*" (infactible).
        if self._objective_already_fixed(target):
            return
        self._model.addConstr(self._model.getObjective() == target, name="fix_objective")
        self._model.setObjective(gp.LinExpr(), gp.GRB.MINIMIZE)
        self._model.update()
        self._fixed_objective_target = target

    def fix_variable(self, var: VarId, value: bool) -> None:
        gvar = self._vars[var]
        gvar.LB = float(value)
        gvar.UB = float(value)

    # ------------------------------------------------------------------ optimización
    def _solve(self) -> SolveResult:
        model = self._model
        if self._incumbent_listener is None:
            model.optimize()
        else:
            self._callback_error = None
            model.optimize(self._callback)
            if self._callback_error is not None:
                error, self._callback_error = self._callback_error, None
                raise error
        raw = model.Status
        self._last_status = raw
        status = self._STATUS_MAP.get(raw, SolveStatus.OTHER)
        if status is SolveStatus.OPTIMAL:
            # Los valores se copian ahora, igual que en el backend de SCIP:
            # Gurobi descarta los atributos de solución (`Var.X`) en cuanto se
            # modifica el modelo, y el extractor consulta `solution_value` justo
            # después de `fix_objective_value`, que añade una restricción y anula
            # la función objetivo.
            self._solution = [v.X for v in self._vars]
            objective: Optional[float] = model.ObjVal
        else:
            self._solution = None
            objective = None
        runtime = model.Runtime
        self._stats["nodes"] += model.NodeCount
        self._stats["iterations"] += model.IterCount
        self._stats["solving_time"] += runtime
        return SolveResult(status=status, objective=objective, runtime=runtime, raw_status=raw)

    def _callback(self, model: Any, where: int) -> None:
        """Incumbentes (``MIPSOL``) al oyente, y fin del ``solve`` si el oyente lo pide."""
        listener = self._incumbent_listener
        if listener is None or self._callback_error is not None:
            return
        try:
            if where == self._gp.GRB.Callback.MIPSOL:
                listener.on_incumbent(model.cbGetSolution(self._vars))
            if listener.should_stop():
                model.terminate()
        except BaseException as exc:  # noqa: BLE001 - gurobipy no propaga las excepciones de un callback
            self._callback_error = exc
            model.terminate()

    def solution_value(self, var: VarId) -> float:
        if self._solution is None:
            raise RuntimeError("no hay solución disponible: la última llamada a solve() no fue óptima")
        return self._solution[var]

    # ------------------------------------------------------------------ sondeos
    def _open_scope(self) -> ProbeScope:
        return _GurobiProbeScope(self)

    # ------------------------------------------------------------------ capacidades (ParaBack)
    def interrupt(self) -> None:
        """``Model.terminate()`` es seguro desde otro hilo; ``optimize`` devuelve ``INTERRUPTED``."""
        self._model.terminate()

    def set_time_limit(self, seconds: Optional[float]) -> None:
        self._model.Params.TimeLimit = self._gp.GRB.INFINITY if seconds is None else float(seconds)

    def add_solution_hint(self, values: Mapping[VarId, bool]) -> None:
        """Warm start parcial mediante el atributo ``Start`` de las variables."""
        for var, value in values.items():
            self._vars[var].Start = float(value)

    # ------------------------------------------------------------------ estadísticas
    @property
    def native_stats(self) -> Mapping[str, float]:
        """``nodes``, ``iterations`` y ``solving_time`` acumulados (atributos de Gurobi)."""
        return dict(self._stats)

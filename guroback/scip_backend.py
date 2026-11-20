"""Backend de SCIP para la extracción de backbones.

Implementa el contrato :class:`guroback.backend.SolverBackend` sobre PySCIPOpt
siguiendo el diseño de ``PLAN_SCIP.md``.

A diferencia de Gurobi, SCIP tiene una máquina de estados estricta: tras
``optimize()`` el modelo queda en la etapa ``SOLVED`` y no admite cambios de
cotas ni de restricciones. El backend resuelve esto con el ciclo
``PROBLEM ⇄ SOLVED``: antes de *cualquier* mutación se llama a
``freeTransform()``, que descarta sólo el problema **transformado** (presolve,
árbol de búsqueda, LP) y devuelve el control a la etapa ``PROBLEM``, donde las
variables y restricciones **originales** siguen siendo válidas. El modelo nunca
se vuelve a leer del archivo ni se reconstruye.

El modo de sondeo nativo de SCIP (``startProbing``) **no** se usa: sólo es
legal dentro de las etapas ``PRESOLVING``/``SOLVING``, es decir, desde un
plugin; invocarlo desde código de usuario aborta el proceso. Además sólo
propaga y resuelve el LP, por lo que no puede demostrar factibilidad entera.
Ver ``PLAN_SCIP.md`` §5.

Los backbones que llegan de otros trabajadores *durante* un ``optimize``
(:meth:`SCIPBackend.add_live_fixes`) no pueden esperar a ``freeTransform``: un
propagador (:class:`_LivePropagator`, sólo con ``enable_live_fixes``) los
aplica como cotas **globales** del problema transformado en el siguiente
ciclo de propagación de un nodo. Se descartan con el problema transformado al
terminar el ``solve``; el extractor los fija después en el problema original.
Las que ya estaban pendientes al empezar un ``solve`` se aplican antes como
cotas temporales del problema original: los propagadores no corren durante
el presolve, que resuelve por sí solo muchos sondeos.
"""
from __future__ import annotations

import logging
import lzma
import os
import shutil
import tempfile
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

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

log = logging.getLogger(__name__)

#: Parámetros aplicados antes de los del usuario. Equivalen al ``OutputFlag=0``
#: y ``MIPGap=0`` del backend de Gurobi; en SCIP ya son los valores por defecto,
#: se fijan de forma explícita por reproducibilidad.
DEFAULT_PARAMS: Mapping[str, str] = {"limits/gap": "0", "limits/absgap": "0"}

#: ``limits/absgap`` de los sondeos ``maximize_flips``: el objetivo cuenta variables (entero), así que
#: en cuanto la cota dual queda a menos de 1 de la incumbente, ésta es óptima (``gaplimit`` = óptimo).
MAX_FLIPS_GAP_ABS = 0.99

#: Alias de conveniencia ``nombre de Gurobi -> parámetros nativos de SCIP``.
#: Sólo se consultan para nombres sin ``/`` (los nativos de SCIP siempre lo
#: llevan), de modo que nunca ocultan un parámetro real del solver. Permiten
#: pasar la misma línea de comandos a ambos backends en la validación diferencial.
GUROBI_PARAM_ALIASES: Mapping[str, Tuple[str, ...]] = {
    "Threads": ("parallel/maxnthreads", "lp/threads"),
    "TimeLimit": ("limits/time",),
    "NodeLimit": ("limits/nodes",),
    "SolutionLimit": ("limits/solutions",),
    "MIPGap": ("limits/gap",),
    "MIPGapAbs": ("limits/absgap",),
    "FeasibilityTol": ("numerics/feastol",),
    # SCIP usa numerics/feastol también para la integralidad: IntFeasTol y
    # FeasibilityTol colapsan en el mismo parámetro (gana el último indicado).
    "IntFeasTol": ("numerics/feastol",),
    "OptimalityTol": ("numerics/dualfeastol",),
    "Seed": ("randomization/randomseedshift",),
}

#: Parámetros de Gurobi sin traducción numérica directa; se rechazan con un
#: mensaje que indica la alternativa en SCIP en lugar de fallar de forma opaca.
UNSUPPORTED_ALIASES: Mapping[str, str] = {
    "OutputFlag": "usa display/verblevel (0 = silencioso, 4 = por defecto)",
    "MIPFocus": "usa el énfasis de SCIP; el extractor ya aplica FEASIBILITY tras fijar el óptimo",
}

#: ``getStatus()`` de SCIP -> estado normalizado del contrato (PLAN_SCIP.md §8).
STATUS_MAP: Mapping[str, SolveStatus] = {
    "optimal": SolveStatus.OPTIMAL,
    "infeasible": SolveStatus.INFEASIBLE,
    "unbounded": SolveStatus.UNBOUNDED,
    "inforunbd": SolveStatus.INFEASIBLE_OR_UNBOUNDED,
    "timelimit": SolveStatus.LIMIT_REACHED,
    "nodelimit": SolveStatus.LIMIT_REACHED,
    "totalnodelimit": SolveStatus.LIMIT_REACHED,
    "stallnodelimit": SolveStatus.LIMIT_REACHED,
    "memlimit": SolveStatus.LIMIT_REACHED,
    "gaplimit": SolveStatus.LIMIT_REACHED,
    "primallimit": SolveStatus.LIMIT_REACHED,
    "duallimit": SolveStatus.LIMIT_REACHED,
    "sollimit": SolveStatus.LIMIT_REACHED,
    "bestsollimit": SolveStatus.LIMIT_REACHED,
    "restartlimit": SolveStatus.LIMIT_REACHED,
    "userinterrupt": SolveStatus.INTERRUPTED,
    "terminate": SolveStatus.INTERRUPTED,
    "unknown": SolveStatus.OTHER,
}

_BOOL_LITERALS = {"true": True, "false": False}


def _param_flag(value: str) -> Any:
    """Adapta el valor textual de un parámetro al tipo que espera PySCIPOpt.

    ``setParam`` convierte cadenas según el tipo del parámetro, salvo para los
    booleanos, que sólo aceptan ``"0"``/``"1"``; aquí se admite además
    ``true``/``false``.
    """
    if isinstance(value, str):
        literal = _BOOL_LITERALS.get(value.strip().lower())
        if literal is not None:
            return literal
    return value


def _resolve_param_names(name: str) -> Tuple[str, ...]:
    """Traduce un nombre de parámetro a uno o varios nombres nativos de SCIP."""
    if "/" in name:
        return (name,)
    alias = GUROBI_PARAM_ALIASES.get(name)
    if alias is not None:
        return alias
    hint = UNSUPPORTED_ALIASES.get(name)
    if hint is not None:
        raise ValueError(f"parámetro {name!r} sin equivalente directo en SCIP: {hint}")
    # Nombre desconocido: se pasa tal cual para que SCIP emita su propio error.
    return (name,)


class _SCIPProbeScope(BoundsProbeScope[Any]):
    """Modificaciones temporales materializadas en la etapa ``PROBLEM``.

    Las cotas originales se guardan la primera vez que se tocan y las
    restricciones temporales se conservan como objetos ``Constraint`` para
    poder borrarlas (:class:`~guroback.backend.BoundsProbeScope`).
    """

    def __init__(self, backend: "SCIPBackend") -> None:
        super().__init__()
        self._backend = backend

    def _before_change(self) -> None:
        self._backend._ensure_problem_stage()

    def _get_bounds(self, var: VarId) -> Tuple[float, float]:
        svar = self._backend._vars[var]
        return svar.getLbOriginal(), svar.getUbOriginal()

    def _set_bounds(self, var: VarId, lb: float, ub: float) -> None:
        self._backend._set_bounds(self._backend._vars[var], lb, ub)

    def _add_any_flip(self, assignment: Sequence[Assignment]) -> Any:
        return self._backend._add_any_flip(assignment)

    def _remove(self, constraint: Any) -> None:
        self._backend._model.delCons(constraint)

    def _set_flip_objective(self, assignment: Sequence[Assignment]) -> None:
        self._backend._set_max_flips(assignment)

    def _restore_objective(self) -> None:
        self._backend._clear_max_flips()


class _InterruptHandler:
    """Manejador de eventos del hilo que resuelve: señala la etapa SOLVING y aplica la interrupción.

    Sus eventos (nodo, LP, solución) sólo ocurren en la etapa SOLVING, con el LP y su
    interfaz ya creados: el primero de cada ``solve`` marca ``backend._lp_ready``, y sólo
    entonces :meth:`SCIPBackend.interrupt` llama a ``interruptLP`` desde otro hilo (ver
    allí la carrera). Además convierte la bandera de interrupción en
    ``interruptSolve()`` + ``interruptLP(True)`` desde el propio hilo que resuelve, que es
    el respaldo cuando la interrupción llegó antes de la etapa SOLVING o con un PySCIPOpt
    sin ``optimizeNogil()`` (``optimize()`` retiene el GIL y otro hilo sólo corre durante
    un manejador Python como este). Se construye de forma diferida porque hereda de
    ``pyscipopt.Eventhdlr``.
    """

    @staticmethod
    def build(backend: "SCIPBackend") -> Any:
        from pyscipopt import Eventhdlr, SCIP_EVENTTYPE

        events = SCIP_EVENTTYPE.NODEEVENT | SCIP_EVENTTYPE.LPEVENT | SCIP_EVENTTYPE.BESTSOLFOUND

        class Handler(Eventhdlr):
            def eventinit(self) -> None:
                self.model.catchEvent(events, self)

            def eventexit(self) -> None:
                self.model.dropEvent(events, self)

            def eventexec(self, event: Any) -> None:
                backend._lp_ready = True
                if backend._interrupt_flag.is_set():
                    self.model.interruptSolve()
                    if hasattr(self.model, "interruptLP"):
                        self.model.interruptLP(True)  # mismo hilo que resuelve: sin carrera
                    return
                # chunks=maxflip: cada incumbente va al oyente (como el callback MIPSOL de Gurobi) y
                # should_stop se consulta en cada evento; una excepción del oyente termina el solve.
                listener = backend._incumbent_listener
                if listener is None or backend._callback_error is not None:
                    return
                try:
                    # En modo exacto el almacén de soluciones exactas no emite BESTSOLFOUND: en cada
                    # evento se mira si la mejor solución cambió (su valor objetivo es entero).
                    if self.model.getNSols() > 0:
                        best = self.model.getBestSol()
                        value = self.model.getSolObjVal(best)
                        if value != backend._last_incumbent_value:
                            backend._last_incumbent_value = value
                            listener.on_incumbent([self.model.getSolVal(best, svar) for svar in backend._vars])
                    if listener.should_stop():
                        self.model.interruptSolve()
                except BaseException as exc:  # noqa: BLE001 - se relanza al terminar el solve
                    backend._callback_error = exc
                    self.model.interruptSolve()

        return Handler()


class _LivePropagator:
    """Propagador que traslada las fijaciones vivas al problema transformado.

    En cada llamada toma las fijaciones que aún no aplicó en este ``solve``
    (:meth:`SCIPBackend._take_live`) y ajusta la cota global de la variable
    transformada. Si la cota local del nodo ya contradice el backbone, el nodo
    se poda (``CUTOFF``): bajo ese backbone no contiene soluciones. Sólo se
    tocan variables activas o fijadas: una multiagregada por el presolve no
    admite cambios de cota (SCIP lo reporta como ``ERROR`` en ``tree.c`` aunque
    la excepción se capture), y una agregada o negada puede apuntar a una
    multiagregada sin que PySCIPOpt permita seguir la cadena. Esas se dejan para
    el ``fix_variable`` posterior (``live_skipped`` en ``native_stats``).
    """

    #: Estados de :meth:`Variable.getStatus` cuyas cotas globales se pueden ajustar sin error.
    TIGHTENABLE = frozenset({"LOOSE", "COLUMN", "FIXED"})

    @staticmethod
    def build(backend: "SCIPBackend") -> Any:
        from pyscipopt import Prop, SCIP_RESULT

        class LiveProp(Prop):
            def propexec(self, proptiming: Any) -> Dict[str, Any]:
                pending = backend._take_live()
                if not pending:
                    return {"result": SCIP_RESULT.DIDNOTRUN}
                model = self.model
                reduced = False
                for var, value in pending:
                    try:
                        tvar = model.getTransformedVar(backend._vars[var])
                        if tvar.getStatus() not in _LivePropagator.TIGHTENABLE:
                            backend._stats["live_skipped"] += 1
                            continue
                        if value:
                            if tvar.getUbLocal() < 0.5:
                                return {"result": SCIP_RESULT.CUTOFF}
                            infeasible, tightened = model.tightenVarLbGlobal(tvar, 1.0)
                        else:
                            if tvar.getLbLocal() > 0.5:
                                return {"result": SCIP_RESULT.CUTOFF}
                            infeasible, tightened = model.tightenVarUbGlobal(tvar, 0.0)
                    except Exception:  # noqa: BLE001 - una fijación viva es sólo una optimización
                        continue
                    if infeasible:
                        return {"result": SCIP_RESULT.CUTOFF}
                    if tightened:
                        reduced = True
                        backend._stats["live_bounds"] += 1
                return {"result": SCIP_RESULT.REDUCEDDOM if reduced else SCIP_RESULT.DIDNOTFIND}

        return LiveProp()


def _read_problem(model: Any, instance_path: str) -> None:
    """``readProblem`` que admite ``.xz`` (p. ej. ``normalized-PB06``), que SCIP no lee.

    La copia descomprimida sólo hace falta durante la lectura y se borra al
    terminar ésta.
    """
    if not instance_path.endswith(".xz"):
        model.readProblem(instance_path)
        return
    # SCIP elige el lector por la extensión que queda tras quitar ".xz".
    suffix = os.path.splitext(instance_path[: -len(".xz")])[1] or ".opb"
    fd, tmp_path = tempfile.mkstemp(prefix="guroback_scip_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as dst, lzma.open(instance_path, "rb") as src:
            shutil.copyfileobj(src, dst)
        model.readProblem(tmp_path)
    finally:
        os.unlink(tmp_path)


class SCIPBackend(SolverBackend):
    """Backend basado en ``pyscipopt``."""

    name = "scip"
    supports_interrupt = True
    supports_time_limit = True
    supports_solution_hint = True
    supports_live_fix = True
    #: ``chunks=maxflip``: objetivo temporal "mínimo de candidatas sin cambiar", ``limits/absgap``
    #: 0,99 (objetivo entero: ``gaplimit`` = óptimo) e incumbentes por el manejador de eventos.
    supports_max_flips = True

    @classmethod
    def is_available(cls) -> Tuple[bool, str]:
        try:
            from pyscipopt import Model
        except ImportError as exc:
            return False, f"pyscipopt no instalado: {exc}"
        try:
            Model().freeProb()
        except Exception as exc:  # noqa: BLE001 - fallo de la librería nativa de SCIP
            return False, f"SCIP no utilizable: {exc}"
        return True, ""

    def __init__(self, instance_path: str, params: Optional[Mapping[str, str]] = None) -> None:
        super().__init__(instance_path, params)
        # Importación diferida: el paquete no exige pyscipopt instalado.
        from pyscipopt import Model, SCIP_PARAMEMPHASIS, SCIP_STAGE, quicksum

        self._quicksum = quicksum
        self._stage_problem = SCIP_STAGE.PROBLEM
        self._emphasis_feasibility = SCIP_PARAMEMPHASIS.FEASIBILITY

        self._interrupt_flag = threading.Event()
        #: Protege ``_solving``: interrupt() sólo llama a SCIP mientras hay un solve en curso.
        self._solve_lock = threading.Lock()
        self._solving = False
        #: El solve en curso llegó a la etapa SOLVING (lo marca :class:`_InterruptHandler`):
        #: el LP y su interfaz existen y ``interruptLP`` desde otro hilo es seguro.
        self._lp_ready = False
        self._model = self._new_model(Model)
        try:
            self._model.hideOutput()
            _read_problem(self._model, instance_path)
            self._apply_params()
            self._model.includeEventhdlr(
                _InterruptHandler.build(self), "guroback_interrupt", "interrupción cooperativa para ParaBack"
            )

            all_vars = self._model.getVars()
            # Mapeo estable: el VarId es la posición en esta lista, fijada en la
            # carga y en la etapa PROBLEM. getVars() no se vuelve a llamar nunca,
            # porque tras optimize() puede devolver variables transformadas.
            self._vars = [v for v in all_vars if v.vtype() == "BINARY"]
            # Coeficientes objetivo capturados una sola vez: fix_objective_value
            # anula la función objetivo y después ya no se pueden recuperar.
            self._obj_terms: List[Tuple[Any, float]] = [(v, v.getObj()) for v in all_vars if v.getObj() != 0.0]
            self._obj_offset = self._model.getObjoffset()
        except Exception:
            self._model.freeProb()
            raise

        self._solution: Optional[List[float]] = None
        self._stats: Dict[str, float] = {
            "nodes": 0.0,
            "lps": 0.0,
            "iterations": 0.0,
            "presolving_time": 0.0,
            "solving_time": 0.0,
        }
        # Fijaciones vivas: las escribe el hilo de escucha y las consume el
        # propagador, que SCIP ejecuta en el hilo de optimize().
        self._live_enabled = False
        self._live_lock = threading.Lock()
        self._live: Dict[VarId, bool] = {}
        #: Fijaciones vivas ya aplicadas al problema transformado de este solve.
        self._live_applied: Set[VarId] = set()
        #: Sondeo ``maxflip`` en curso: la asignación cuyos cambios se maximizan.
        self._max_flips: Optional[List[Assignment]] = None
        self._saved_absgap: Optional[float] = None
        #: Excepción del oyente de incumbentes durante el último solve (se relanza al terminar).
        self._callback_error: Optional[BaseException] = None
        #: Valor objetivo de la última incumbente entregada al oyente en el solve en curso.
        self._last_incumbent_value: Optional[float] = None

    # ------------------------------------------------------------------ ganchos (SCIPExactBackend)
    def _new_model(self, model_cls: Any) -> Any:
        """El ``pyscipopt.Model`` vacío en el que se lee la instancia."""
        return model_cls()

    def _add_any_flip(self, assignment: Sequence[Assignment]) -> Any:
        """``Σ (x ≠ valor) ≥ 1``: al menos una variable de ``assignment`` cambia de valor."""
        lhs = self._quicksum((1.0 - self._vars[var]) if value else (1.0 * self._vars[var]) for var, value in assignment)
        return self._model.addCons(lhs >= 1.0, name="guroback_anyflip")

    def _apply_flip_objective(self, assignment: Sequence[Assignment]) -> None:
        """Objetivo "mínimo de candidatas sin cambiar": ``+x`` si vale 1, ``−x`` si vale 0 (la constante #ceros se añade fuera)."""
        self._model.setObjective(self._quicksum((1.0 if value else -1.0) * self._vars[var] for var, value in assignment), "minimize")

    def _clear_flip_objective(self, assignment: Sequence[Assignment]) -> None:
        self._model.setObjective(0, "minimize")

    def _set_max_flips(self, assignment: Sequence[Assignment]) -> None:
        if self._fixed_objective_target is None and self.has_objective():
            raise RuntimeError("maximize_flips exige el objetivo fijado en z* (fix_objective_value) antes")
        self._ensure_problem_stage()
        self._apply_flip_objective(assignment)
        self._max_flips = list(assignment)
        self._saved_absgap = float(self._model.getParam("limits/absgap"))
        self._model.setParam("limits/absgap", MAX_FLIPS_GAP_ABS)

    def _clear_max_flips(self) -> None:
        assignment, self._max_flips = self._max_flips, None
        if assignment is None:
            return
        self._ensure_problem_stage()
        self._clear_flip_objective(assignment)
        if self._saved_absgap is not None:
            self._model.setParam("limits/absgap", self._saved_absgap)
            self._saved_absgap = None

    def _flips_of(self, unchanged_objective: Any) -> Any:
        """Número de candidatas que cambian a partir del objetivo "sin cambiar" (sin su constante)."""
        assert self._max_flips is not None
        zeros = sum(1 for _, value in self._max_flips if not value)
        return len(self._max_flips) - (unchanged_objective + zeros)

    def _raw_objective(self) -> Any:
        """Valor objetivo de la mejor solución (``float``; el backend exacto devuelve el racional)."""
        return self._model.getObjVal()

    # ------------------------------------------------------------------ etapas
    def _ensure_problem_stage(self) -> None:
        """Devuelve el modelo a la etapa ``PROBLEM``, única en la que se puede mutar.

        ``freeTransform()`` descarta el problema transformado pero conserva el
        original (variables, restricciones, cotas), los parámetros y las
        soluciones en espacio original.
        """
        if self._model.getStage() != self._stage_problem:
            self._model.freeTransform()

    def _set_bounds(self, svar: Any, lb: float, ub: float) -> None:
        """Fija ``[lb, ub]`` evitando un estado intermedio con ``lb > ub``."""
        if lb <= svar.getUbOriginal():
            self._model.chgVarLb(svar, lb)
            self._model.chgVarUb(svar, ub)
        else:
            self._model.chgVarUb(svar, ub)
            self._model.chgVarLb(svar, lb)

    def _apply_params(self) -> None:
        """Aplica los valores por defecto del backend y luego los del usuario."""
        for name, value in DEFAULT_PARAMS.items():
            self.set_parameter(name, value)
        for name, value in self.params.items():
            self.set_parameter(name, value)

    # ------------------------------------------------------------------ ciclo de vida
    def _close(self) -> None:
        self._solution = None
        self._obj_terms = []
        self._vars = []
        self._model.freeProb()

    # ------------------------------------------------------------------ introspección
    def binary_variables(self) -> Sequence[VarId]:
        return range(len(self._vars))

    def variable_name(self, var: VarId) -> str:
        return self._vars[var].name

    def has_objective(self) -> bool:
        return bool(self._obj_terms)

    # ------------------------------------------------------------------ parámetros
    def set_parameter(self, name: str, value: str) -> None:
        self._ensure_problem_stage()
        coerced = _param_flag(value)
        for scip_name in _resolve_param_names(name):
            if scip_name == "numerics/feastol":
                self._keep_tolerances_consistent(float(coerced))
            self._model.setParam(scip_name, coerced)

    def _keep_tolerances_consistent(self, feastol: float) -> None:
        """SCIP 11 exige ``epsilon <= sumepsilon <= feastol`` al transformar (SCIP 10 no lo comprobaba).

        Apretar ``numerics/feastol`` por debajo de ``sumepsilon`` (1e-6 por defecto) haría
        fallar el ``optimize`` con "the value is invalid for the given parameter": se bajan
        ``sumepsilon`` y ``epsilon`` lo justo para mantener el orden.
        """
        sumepsilon = float(self._model.getParam("numerics/sumepsilon"))
        if sumepsilon > feastol:
            self._model.setParam("numerics/sumepsilon", feastol)
            log.info("numerics/sumepsilon %g -> %g para no superar numerics/feastol", sumepsilon, feastol)
        epsilon = float(self._model.getParam("numerics/epsilon"))
        if epsilon > min(sumepsilon, feastol):
            self._model.setParam("numerics/epsilon", min(sumepsilon, feastol))

    def set_feasibility_focus(self) -> None:
        self._ensure_problem_stage()
        self._model.setEmphasis(self._emphasis_feasibility, quiet=True)
        # El énfasis puede pisar parámetros indicados por el usuario; se reaplican.
        self._apply_params()

    # ------------------------------------------------------------------ estado base
    def fix_objective_value(self, value: float) -> None:
        target = normalize_objective_value(value)
        if self._objective_already_fixed(target):
            return
        self._ensure_problem_stage()
        if not self._obj_terms:
            if abs(target - self._obj_offset) > 1e-9:
                raise ValueError(
                    f"la función objetivo es constante ({self._obj_offset}); no puede valer {target}"
                )
            return
        lhs = self._quicksum(coef * svar for svar, coef in self._obj_terms)
        self._model.addCons(lhs == target - self._obj_offset, name="fix_objective")
        self._model.setObjective(0, "minimize")
        self._fixed_objective_target = target

    def fix_variable(self, var: VarId, value: bool) -> None:
        self._ensure_problem_stage()
        bound = float(value)
        self._set_bounds(self._vars[var], bound, bound)
        if self._live_enabled:
            with self._live_lock:
                self._live.pop(var, None)

    # ------------------------------------------------------------------ optimización
    def _solve(self) -> SolveResult:
        # La bandera se limpia al empezar el solve, no al llamar a SCIP: un interrupt() que
        # llegue mientras se aplican las fijaciones vivas pendientes no se pierde (el
        # manejador de eventos lo convierte en interruptSolve() en el primer evento).
        self._interrupt_flag.clear()
        if not self._live_enabled:
            return self._optimize()
        saved = self._apply_pending_live()
        if saved is None:
            # Una fijación viva contradice una cota del sondeo: bajo ese backbone
            # (válido en toda solución óptima) el sondeo es infactible.
            self._solution = None
            return SolveResult(status=SolveStatus.INFEASIBLE, objective=None, runtime=0.0, raw_status="live_fix_conflict")
        try:
            return self._optimize()
        finally:
            if saved:
                self._ensure_problem_stage()
                for var, (lb, ub) in saved.items():
                    self._set_bounds(self._vars[var], lb, ub)

    def _apply_pending_live(self) -> Optional[Dict[VarId, Tuple[float, float]]]:
        """Fija como cotas originales las fijaciones vivas pendientes; ``None`` si alguna es incompatible.

        Las marca además como aplicadas para el propagador, cuyo registro se
        reinicia aquí: ``freeTransform()`` descartó las cotas globales del
        ``solve`` anterior.
        """
        with self._live_lock:
            pending = list(self._live.items())
            self._live_applied = {v for v, _ in pending}
        saved: Dict[VarId, Tuple[float, float]] = {}
        if not pending:
            return saved
        self._ensure_problem_stage()
        for var, value in pending:
            svar = self._vars[var]
            lb, ub = svar.getLbOriginal(), svar.getUbOriginal()
            target = float(value)
            if target < lb or target > ub:
                for other, (old_lb, old_ub) in saved.items():
                    self._set_bounds(self._vars[other], old_lb, old_ub)
                return None
            if lb != target or ub != target:
                saved[var] = (lb, ub)
                self._set_bounds(svar, target, target)
        return saved

    def _optimize(self) -> SolveResult:
        model = self._model
        # Sin el GIL, otros hilos (interrupt(), la escucha de ParaBack) corren durante todo
        # el solve; los callbacks Python de PySCIPOpt lo recuperan ("with gil").
        solve = getattr(model, "optimizeNogil", model.optimize)
        with self._solve_lock:
            if self._interrupt_flag.is_set():
                # interrupt() llegó dentro de esta llamada a solve pero antes de SCIP (p. ej.
                # mientras se aplicaban las fijaciones vivas): SCIP podría terminar en el
                # presolve sin ningún evento en que atenderlo.
                self._solution = None
                return SolveResult(status=SolveStatus.INTERRUPTED, objective=None, raw_status="interrupted_before_solve")
            self._solving = True
            self._lp_ready = False
        self._callback_error = None
        self._last_incumbent_value = None
        try:
            solve()
        finally:
            with self._solve_lock:
                self._solving = False
                self._lp_ready = False
        if self._callback_error is not None:
            error, self._callback_error = self._callback_error, None
            raise error
        raw = model.getStatus()
        status = STATUS_MAP.get(raw, SolveStatus.OTHER)
        if raw == "gaplimit" and self._max_flips is not None:
            status = SolveStatus.OPTIMAL  # objetivo entero y absgap < 1: la incumbente es óptima
        if status is SolveStatus.OPTIMAL and model.getNSols() > 0:
            best = model.getBestSol()
            # Los valores se copian ahora: getBestSol() apunta al almacén de SCIP,
            # que no sobrevive a freeTransform(). getSolVal sobre una variable
            # original resuelve internamente las agregaciones del presolve.
            self._solution = [model.getSolVal(best, svar) for svar in self._vars]
            value = self._raw_objective()
            if self._max_flips is not None:
                value = self._flips_of(value)
            objective: Optional[float] = float(value)
        else:
            self._solution = None
            objective = None
        # Estadísticas nativas, sólo disponibles antes del próximo freeTransform().
        runtime = model.getSolvingTime()
        self._stats["nodes"] += model.getNNodes()
        self._stats["lps"] += model.getNLPs()
        # Iteraciones de símplex: el análogo de IterCount en Gurobi.
        self._stats["iterations"] += model.getNLPIterations()
        self._stats["presolving_time"] += model.getPresolvingTime()
        self._stats["solving_time"] += runtime
        return SolveResult(status=status, objective=objective, runtime=runtime, raw_status=raw)

    def solution_value(self, var: VarId) -> float:
        if self._solution is None:
            raise RuntimeError("no hay solución disponible: la última llamada a solve() no fue óptima")
        return self._solution[var]

    # ------------------------------------------------------------------ sondeos
    def _open_scope(self) -> ProbeScope:
        return _SCIPProbeScope(self)

    # ------------------------------------------------------------------ capacidades (ParaBack)
    def interrupt(self) -> None:
        """Pide a SCIP que pare el ``solve`` en curso; seguro desde otro hilo.

        Durante un ``solve`` sin GIL (``optimizeNogil``) y ya en la etapa SOLVING llama
        directamente a ``interruptLP(True)`` (PySCIPOpt vendorizado), que activa la
        bandera de parada de SCIP **y** detiene el LP en curso, también los LP exactos de
        SoPlex del modo exacto, que pueden durar minutos. Antes de esa etapa sólo
        ``interruptSolve()``, que activa la bandera y nada más: cada sondeo vuelve a
        transformar el problema y ``SCIPlpCreate`` publica el puntero del LP antes de
        crear su interfaz, así que un ``interruptLP`` desde otro hilo durante la
        transformación leía memoria sin inicializar y tumbaba el rango (fallos de
        segmentación de los trabajadores SCIP en el cluster, ronda R20). La etapa la
        señala el manejador de eventos (:class:`_InterruptHandler`, ``_lp_ready``), que
        además aplica la bandera en el hilo que resuelve. Fuera de una llamada a ``solve``
        la bandera se limpia al empezar la siguiente (``SCIPsolve`` también desactiva la
        interrupción de los LP); dentro de ella, aunque SCIP aún no haya empezado
        (fijaciones vivas pendientes), la bandera se conserva.
        """
        self._interrupt_flag.set()
        with self._solve_lock:
            if not self._solving:
                return
            model = self._model
            try:
                if self._lp_ready and hasattr(model, "interruptLP"):
                    model.interruptLP(True)
                else:
                    model.interruptSolve()
            except Exception:  # noqa: BLE001 - p. ej. SCIP ya salió de las etapas de resolución
                log.debug("no se pudo interrumpir SCIP; queda la bandera del manejador de eventos", exc_info=True)

    def enable_live_fixes(self) -> bool:
        if self._live_enabled:
            return True
        from pyscipopt import SCIP_PRESOLTIMING, SCIP_PROPTIMING

        self._ensure_problem_stage()  # includeProp sólo es legal en la etapa PROBLEM
        self._model.includeProp(
            _LivePropagator.build(self),
            "guroback_live",
            "backbones ajenos en el solve en curso (ParaBack)",
            presolpriority=0,
            presolmaxrounds=0,
            proptiming=SCIP_PROPTIMING.BEFORELP | SCIP_PROPTIMING.DURINGLPLOOP | SCIP_PROPTIMING.AFTERLPLOOP,
            presoltiming=SCIP_PRESOLTIMING.FAST,
            priority=1_000_000,
            freq=1,
            delay=False,
        )
        self._stats["live_bounds"] = 0.0
        self._stats["live_skipped"] = 0.0
        self._live_enabled = True
        return True

    def add_live_fixes(self, assignment: Sequence[Assignment]) -> None:
        if not self._live_enabled:
            return
        with self._live_lock:
            for var, value in assignment:
                self._live[var] = bool(value)

    def _take_live(self) -> List[Tuple[VarId, bool]]:
        """Fijaciones vivas aún no aplicadas en este ``solve`` (las marca como aplicadas)."""
        if not self._live or len(self._live) == len(self._live_applied):
            return []
        with self._live_lock:
            pending = [(v, x) for v, x in self._live.items() if v not in self._live_applied]
            self._live_applied.update(v for v, _ in pending)
        return pending

    def set_time_limit(self, seconds: Optional[float]) -> None:
        self._ensure_problem_stage()
        self._model.setParam("limits/time", 1e20 if seconds is None else float(seconds))

    def add_solution_hint(self, values: Mapping[VarId, bool]) -> None:
        """Solución parcial de arranque (``createPartialSol``); se ignora si SCIP no la admite."""
        self._ensure_problem_stage()
        model = self._model
        create_partial = getattr(model, "createPartialSol", None)
        if create_partial is None:  # pragma: no cover - versiones antiguas de PySCIPOpt
            return
        sol = create_partial()
        for var, value in values.items():
            model.setSolVal(sol, self._vars[var], float(value))
        model.addSol(sol, free=True)

    # ------------------------------------------------------------------ estadísticas
    @property
    def native_stats(self) -> Mapping[str, float]:
        """Igual que :attr:`scip_stats`; nombre común del contrato para el informe diferencial."""
        return self.scip_stats

    @property
    def scip_stats(self) -> Mapping[str, float]:
        """Contadores nativos acumulados sobre todas las llamadas a ``solve``.

        Claves: ``nodes``, ``lps``, ``iterations``, ``presolving_time`` y
        ``solving_time``. Se usan
        en el informe de la Fase 3 para comparar con ``NodeCount``/``IterCount``
        de Gurobi y para medir el peso del presolve repetido.
        """
        return dict(self._stats)


class SCIPExactBackend(SCIPBackend):
    """SCIP en modo exacto (aritmética racional): sus respuestas no dependen de tolerancias.

    Requiere el SCIP vendorizado compilado con ``EXACTSOLVE`` (``make scip-exact``). En modo
    exacto SCIP lee el OPB como restricciones ``exactlinear`` y sólo acepta restricciones,
    cotas y objetivos **exactos**; las variantes en coma flotante de PySCIPOpt se rechazan o,
    peor, se pierden en silencio. Por eso este backend usa siempre la API exacta
    (``PySCIPOpt/``, ``addConsExactLinear``, ``chgVar{Lb,Ub,Obj}Exact``, ``getPrimalboundExact``):

    * ``chgVarLb/Ub`` sólo cambia las cotas en coma flotante: tras ``freeTransform`` SCIP
      reconstruye el problema con las exactas y el cambio desaparece (una fijación o un
      sondeo se ignoran en silencio).
    * ``setObjective(0)`` no anula el objetivo exacto.
    * Los coeficientes del objetivo se leen exactos (``getObjExact``), no como ``double``. El
      lector OPB del SCIP vendorizado construye los racionales desde el texto (el original
      pasaba por ``strtod`` y redondeaba los enteros por encima de 2^53).

    ``exact_arithmetic = True``: ParaBack confía en sus soluciones y en sus respuestas
    "infactible" como en las de RoundingBack. El propagador de fijaciones vivas y las pistas
    de solución no son componentes aptos para el modo exacto: se desactivan (los hechos
    ajenos se aplican entre dos ``solve``).
    """

    name = "scip-exact"
    exact_arithmetic = True
    supports_solution_hint = False
    supports_live_fix = False

    @classmethod
    def is_available(cls) -> Tuple[bool, str]:
        ok, reason = SCIPBackend.is_available()
        if not ok:
            return ok, reason
        from pyscipopt import Model

        model = Model()
        model.hideOutput()
        try:
            model.freeProb()
            model.enableExactSolving(True)
            if not hasattr(model, "addConsExactLinear"):
                return False, "PySCIPOpt sin la API exacta de ParaBack (make scip-exact)"
        except Exception:  # noqa: BLE001 - SCIP compilado sin EXACTSOLVE
            return False, "SCIP compilado sin modo exacto (make scip-exact con PYTHON=.venv/bin/python)"
        finally:
            model.freeProb()
        return True, ""

    def __init__(self, instance_path: str, params: Optional[Mapping[str, str]] = None) -> None:
        super().__init__(instance_path, params)
        from fractions import Fraction

        self._fraction = Fraction
        # Coeficientes exactos del objetivo, capturados en la etapa PROBLEM (como _obj_terms).
        self._obj_exact: List[Tuple[Any, Any]] = [
            (v, Fraction(v.getObjExact())) for v, _ in self._obj_terms
        ]
        self._obj_offset_exact = Fraction(self._model.getOrigObjoffsetExact())
        #: z* exacto de la última resolución óptima (el ``double`` de SolveResult puede redondearlo).
        self._last_exact_objective: Optional[Any] = None

    def _new_model(self, model_cls: Any) -> Any:
        model = model_cls()
        model.hideOutput()
        model.freeProb()  # el modo exacto sólo se activa antes de crear el problema
        model.enableExactSolving(True)
        return model

    def _set_bounds(self, svar: Any, lb: float, ub: float) -> None:
        exact_lb, exact_ub = int(round(lb)), int(round(ub))
        if lb <= svar.getUbOriginal():
            self._model.chgVarLbExact(svar, exact_lb)
            self._model.chgVarUbExact(svar, exact_ub)
        else:
            self._model.chgVarUbExact(svar, exact_ub)
            self._model.chgVarLbExact(svar, exact_lb)

    def _add_any_flip(self, assignment: Sequence[Assignment]) -> Any:
        # Σ_{v=1} (1 − x) + Σ_{v=0} x ≥ 1   ⇔   Σ_{v=0} x − Σ_{v=1} x ≥ 1 − #{v=1}
        variables = [self._vars[var] for var, _ in assignment]
        coefs = [-1 if value else 1 for _, value in assignment]
        ones = sum(1 for _, value in assignment if value)
        return self._model.addConsExactLinear(variables, coefs, lhs=1 - ones, name="guroback_anyflip")

    def _apply_flip_objective(self, assignment: Sequence[Assignment]) -> None:
        for var, value in assignment:  # coeficientes exactos: setObjective en coma flotante no vale en modo exacto
            self._model.chgVarObjExact(self._vars[var], 1 if value else -1)

    def _clear_flip_objective(self, assignment: Sequence[Assignment]) -> None:
        for var, _ in assignment:
            self._model.chgVarObjExact(self._vars[var], 0)

    def _raw_objective(self) -> Any:
        exact = self._fraction(self._model.getPrimalboundExact())
        if self._max_flips is None:
            self._last_exact_objective = exact
        return exact

    def fix_objective_value(self, value: float) -> None:
        target = normalize_objective_value(value)
        if self._objective_already_fixed(target):
            return
        self._ensure_problem_stage()
        exact = self._last_exact_objective
        if exact is not None and float(exact) == target:
            z_star = exact  # el propio z* exacto: no se pierde nada al pasar por double
        else:
            if abs(target) >= 2 ** 53:
                log.warning("z* = %s supera 2^53: el valor recibido en coma flotante puede no ser exacto", target)
            z_star = self._fraction(int(target))
        if not self._obj_exact:
            if z_star != self._obj_offset_exact:
                raise ValueError(f"la función objetivo es constante ({self._obj_offset_exact}); no puede valer {z_star}")
            return
        rhs = z_star - self._obj_offset_exact
        self._model.addConsExactLinear(
            [v for v, _ in self._obj_exact], [c for _, c in self._obj_exact], lhs=rhs, rhs=rhs, name="fix_objective"
        )
        self._model.setObjective(0, "minimize")
        for v, _ in self._obj_exact:
            self._model.chgVarObjExact(v, 0)
        self._fixed_objective_target = target

    def enable_live_fixes(self) -> bool:
        return False  # el propagador Python no es un componente del modo exacto

    def add_solution_hint(self, values: Mapping[VarId, bool]) -> None:
        """Sin pistas: las soluciones parciales en coma flotante no son aptas para el modo exacto."""


__all__ = [
    "SCIPExactBackend",
    "DEFAULT_PARAMS",
    "GUROBI_PARAM_ALIASES",
    "STATUS_MAP",
    "SCIPBackend",
]

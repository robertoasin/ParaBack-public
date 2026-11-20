"""Trabajador LP: backbones demostrados por la relajación lineal con cotas duales seguras.

No sondea con un MIP. Resuelve la relajación lineal de la instancia (simplex dual, con las
variables fijadas a lo que ya se sabe) y de sus duales saca, con aritmética racional
(:mod:`paraback.lp_bounds`), una cota inferior demostrada y los costes reducidos: toda variable
cuyo coste reducido supera la holgura ``z* − cota`` es backbone. Itera (cada fijación sube la
cota) y absorbe los backbones ajenos como cotas nuevas. Con ``probe=1`` sondea además, por LP,
las candidatas que quedan: fija cada una al valor contrario a ``x*`` (o al de la relajación) y,
si la cota segura supera ``z*`` o el LP es infactible, es backbone. Un LP infactible sólo cuenta
si su rayo de Farkas lo demuestra en racionales: lo que dice el solver en coma flotante nunca es
un hecho por sí solo. Si la relajación devuelve una solución entera de coste ``z*``, es una
solución óptima: comprobada con enteros, refuta lo que cambia.

Motor del LP (``engine``): ``gurobi`` (por defecto) o ``soplex``, el LP de SCIP a través de
PySCIPOpt (la clase ``LP``). Los dos sólo dan pistas (duales, rayos, valores) y su precisión
cambia cuántos hechos aparecen, no si son ciertos; ``--float-max-bits`` pasa ``lp`` a ``soplex``
en las instancias de más bits, donde Gurobi no entra (``docs/PLAN_EVALUACION.md`` D10). El
``solve`` de SoPlex no suelta el GIL ni se puede interrumpir: cada llamada se limita a
:data:`SOPLEX_SOLVE_SECONDS` y se repite desde la base en la que se quedó, para que el hilo de
escucha atienda STOP entre medias.

Sólo prueba backbones (y refuta con soluciones enteras casuales): el complemento natural de
los solvers CDCL, que refutan deprisa y prueban despacio. Parámetros: ``probe`` (0/1, por
defecto 1), ``engine`` y, con Gurobi, ``Threads`` y cualquier parámetro suyo.
"""
from __future__ import annotations

import logging
import time
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from guroback.backend import normalize_objective_value
from guroback.extractor import InconsistentKnowledge
from guroback.opb import OPBInstance

from .communicator import Communicator
from .config import ConfigError, ParaBackConfig, WorkerSpec
from .lp_bounds import proves_infeasible, proves_infeasible_by_ray, rational_duals, reduced_cost_fixings, safe_bound
from .passive_worker import PassiveWorker

log = logging.getLogger("paraback.lp")

OWN_PARAMS = ("probe", "engine")
#: Motores de la relajación (``lp:engine=…``).
ENGINES = ("gurobi", "soplex")
#: Valor de la relajación a partir del cual se considera entera una componente.
INTEGRALITY_TOL = 1e-9
#: Tope de cada ``solve`` de SoPlex (s): no suelta el GIL, así que el hilo de escucha sólo corre entre llamadas.
SOPLEX_SOLVE_SECONDS = 2.0

# Resultado de un solve, igual para los dos motores.
OPTIMAL, INFEASIBLE, INTERRUPTED = "optimal", "infeasible", "interrupted"


def engine_of(spec: WorkerSpec) -> str:
    engine = str(spec.params.get("engine", "gurobi")).strip().lower()
    if engine not in ENGINES:
        raise ConfigError(f"lp: engine={engine!r} no válido; motores: {', '.join(ENGINES)}")
    return engine


def is_available(engine: str = "gurobi") -> Tuple[bool, str]:
    if engine == "soplex":
        try:
            from pyscipopt import LP  # noqa: F401
        except ImportError as exc:
            return False, f"PySCIPOpt no disponible: {exc}"
        return True, ""
    from guroback.gurobi_backend import GurobiBackend

    return GurobiBackend.is_available()


class _GurobiLP:
    """La relajación con Gurobi (gurobipy): simplex dual, ``terminate`` desde otro hilo."""

    def __init__(self, inst: OPBInstance, minimize: bool, params: Mapping[str, str]) -> None:
        import gurobipy as gp

        from guroback.gurobi_backend import ENV_LOCK

        self.gp = gp
        self.minimize = minimize
        self.optimizing = False
        self.env: Any = None
        self.model: Any = None
        with ENV_LOCK:  # dos entornos arrancando a la vez en dos hilos corrompen la memoria
            self.env = gp.Env(params={"OutputFlag": 0})
        objective = inst.objective
        assert objective is not None
        model = gp.Model("paraback-lp", env=self.env)
        self.model = model
        for key, value in params.items():
            if key not in OWN_PARAMS:
                model.setParam(key, _number(value))
        model.setParam("Method", 1)  # simplex dual: cada reoptimización parte de la base anterior
        model.setParam("InfUnbdInfo", 1)  # rayo de Farkas de los LP infactibles (se comprueba en racionales)
        x = model.addVars(inst.num_vars, lb=0.0, ub=1.0, vtype=gp.GRB.CONTINUOUS)
        model.setObjective(gp.LinExpr(list(objective.coefs), [x[i] for i in objective.indices]),
                           gp.GRB.MINIMIZE if minimize else gp.GRB.MAXIMIZE)
        sense = {">=": gp.GRB.GREATER_EQUAL, "<=": gp.GRB.LESS_EQUAL, "=": gp.GRB.EQUAL}
        for c in range(len(inst.constraints)):
            t = inst.terms_of(c)
            model.addLConstr(gp.LinExpr(list(t.coefs), [x[i] for i in t.indices]), sense[inst.operator(c)], inst.rhs(c))
        model.update()
        self.vars: Any = x

    def set_bounds(self, j: int, lower: int, upper: int) -> None:
        self.vars[j].LB, self.vars[j].UB = float(lower), float(upper)

    def solve(self, should_stop: Any) -> str:
        self.optimizing = True
        try:
            self.model.optimize()
        finally:
            self.optimizing = False
        status = self.model.Status
        if status == self.gp.GRB.OPTIMAL:
            return OPTIMAL
        if status in (self.gp.GRB.INFEASIBLE, self.gp.GRB.INF_OR_UNBD):
            return INFEASIBLE
        if status == self.gp.GRB.INTERRUPTED:
            return INTERRUPTED
        return f"estado {status} de Gurobi"

    def duals(self) -> List[float]:
        return list(self.model.getAttr("Pi", self.model.getConstrs()))

    def farkas(self) -> Optional[List[float]]:
        try:
            return list(self.model.getAttr("FarkasDual", self.model.getConstrs()))
        except self.gp.GurobiError:  # sin rayo (p. ej. INF_OR_UNBD resuelto en el presolve)
            return None

    def values(self) -> List[float]:
        return list(self.model.getAttr("X", list(self.vars.values())))

    def objective(self) -> float:
        """Valor del LP en el sentido de minimización (el de ``z*`` en el trabajador)."""
        return self.model.ObjVal if self.minimize else -self.model.ObjVal

    def interrupt(self) -> None:
        if self.model is not None and self.optimizing:
            try:
                self.model.terminate()
            except Exception:  # noqa: BLE001 - el modelo puede estar ya cerrado
                pass

    def free(self) -> None:
        # Se liberan en el hilo que los creó: dejarlos al recolector (otro hilo, o el cierre del
        # intérprete) corrompía la memoria en el lanzador local.
        from guroback.gurobi_backend import ENV_LOCK

        model, env, self.model, self.env, self.vars = self.model, self.env, None, None, None
        with ENV_LOCK:  # liberar un entorno mientras otro hilo arranca el suyo también corrompía
            for obj in (model, env):
                if obj is not None:
                    try:
                        obj.dispose()
                    except Exception:  # noqa: BLE001 - ya liberado
                        pass


class _SoplexLP:
    """La relajación con SoPlex (``pyscipopt.LP``): siempre ``min`` (el objetivo negado si es ``max``)."""

    def __init__(self, inst: OPBInstance, minimize: bool, params: Mapping[str, str]) -> None:
        from pyscipopt import LP
        from pyscipopt.scip import PY_SCIP_LPPARAM

        objective = inst.objective
        assert objective is not None
        sign = 1.0 if minimize else -1.0
        costs = [0.0] * inst.num_vars
        for coef, idx in objective:
            costs[idx] += sign * coef
        lp = LP("paraback-lp", "minimize")
        lp.setRealParam(PY_SCIP_LPPARAM.LPTILIM, SOPLEX_SOLVE_SECONDS)
        lp.addCols([[] for _ in range(inst.num_vars)], objs=costs, lbs=[0.0] * inst.num_vars, ubs=[1.0] * inst.num_vars)
        inf = lp.infinity()
        rows, lhss, rhss = [], [], []
        for c in range(len(inst.constraints)):
            t = inst.terms_of(c)
            rows.append([(i, float(a)) for a, i in zip(t.coefs, t.indices)])
            op, rhs = inst.operator(c), float(inst.rhs(c))
            lhss.append(rhs if op in (">=", "=") else -inf)
            rhss.append(rhs if op in ("<=", "=") else inf)
        if rows:
            lp.addRows(rows, lhss=lhss, rhss=rhss)
        self.lp: Any = lp
        self.ignored = sorted(k for k in params if k not in OWN_PARAMS)

    def set_bounds(self, j: int, lower: int, upper: int) -> None:
        self.lp.chgBound(j, float(lower), float(upper))

    def solve(self, should_stop: Any) -> str:
        while True:
            start = time.perf_counter()
            self.lp.solve()
            if self.lp.isOptimal():
                return OPTIMAL
            if self.lp.getDualRay() is not None:
                return INFEASIBLE
            if should_stop():
                return INTERRUPTED
            if time.perf_counter() - start < 0.9 * SOPLEX_SOLVE_SECONDS:
                # Ni óptimo, ni rayo, ni límite de tiempo: SoPlex no sabe (inestabilidad numérica).
                return "SoPlex sin óptimo ni rayo de Farkas"
            # Límite de tiempo de la llamada: se sigue desde la base en la que se quedó.

    def duals(self) -> List[float]:
        return list(self.lp.getDual())

    def farkas(self) -> Optional[List[float]]:
        ray = self.lp.getDualRay()
        return None if ray is None else list(ray)

    def values(self) -> List[float]:
        return list(self.lp.getPrimal())

    def objective(self) -> float:
        return float(self.lp.getObjVal())

    def interrupt(self) -> None:
        pass  # cada solve dura como mucho SOPLEX_SOLVE_SECONDS y el bucle mira STOP entre llamadas

    def free(self) -> None:
        self.lp = None


class LPWorker(PassiveWorker):
    """Backbones por costes reducidos y sondeos LP (``solver = lp``)."""

    def __init__(self, comm: Communicator, config: ParaBackConfig, spec: WorkerSpec) -> None:
        super().__init__(comm, config, spec)
        self.probe = str(spec.params.get("probe", "1")).strip().lower() in ("1", "true")
        self.engine_name = engine_of(spec)
        self._lp: Any = None
        self._lower: List[int] = []
        self._upper: List[int] = []
        self._minimize = True
        self._target = 0  # z* en el sentido de minimización
        self._candidates: List[int] = []  # índices sin decidir, por |coste reducido| descendente
        self._probed: set = set()
        #: Motivo por el que el trabajador dejó de aportar (LP infactible sin demostrar), o vacío.
        self._disabled = ""

    def capabilities(self) -> Dict[str, bool]:
        ok, reason = is_available(self.engine_name)
        if not ok:
            from .worker_base import WorkerUnavailable

            raise WorkerUnavailable(reason)
        return dict(super().capabilities())

    def interrupt(self) -> None:
        super().interrupt()
        lp = self._lp
        if lp is not None:
            lp.interrupt()

    def cleanup(self) -> None:
        lp, self._lp = self._lp, None
        if lp is not None:
            lp.free()

    # ------------------------------------------------------------------ PassiveWorker
    def prepare(self) -> bool:
        inst = self.instance
        assert inst is not None
        if inst.objective is None:
            log.warning("trabajador %d (lp): la instancia no tiene objetivo; los costes reducidos no dicen nada", self.rank)
            return False
        self._minimize = inst.sense != "max"
        start = time.perf_counter()
        try:
            if self.engine_name == "soplex":
                self._lp = _SoplexLP(inst, self._minimize, self.spec.params)
                if self._lp.ignored:
                    log.info("trabajador %d (lp, soplex): parámetros de Gurobi sin efecto: %s", self.rank, ", ".join(self._lp.ignored))
            else:
                self._lp = _GurobiLP(inst, self._minimize, self.spec.params)
        except MemoryError as exc:
            log.warning("trabajador %d (lp): sin memoria para la relajación (%s); se retira", self.rank, exc)
            self.cleanup()
            return False
        n = inst.num_vars
        self._lower, self._upper = [0] * n, [1] * n
        self.stats.native["build_time"] = time.perf_counter() - start
        for key in ("lp_solves", "rc_fixed", "probe_fixed", "probes", "farkas_proofs", "farkas_rejected"):
            self.stats.native[key] = 0.0
        return True

    def derive_from_base(self) -> None:
        if self._disabled:
            return
        if self.optimum is None:
            log.warning("trabajador %d (lp): sin z* no hay nada que fijar", self.rank)
            return
        z = int(normalize_objective_value(self.optimum))
        self._target = z if self._minimize else -z
        self._fix_loop()

    def derive_from_facts(self, backbone: Sequence[Tuple[str, bool]], refuted: Sequence[str]) -> None:
        if self._disabled or self.optimum is None or not backbone:
            return
        assert self.instance is not None
        index = {name: i for i, name in enumerate(self.instance.names)}  # pequeño frente al LP
        changed = False
        for name, value in backbone:
            j = index.get(name)
            if j is not None and self._lower[j] != self._upper[j]:
                self._set_bounds(j, value)
                changed = True
        if changed:
            self._fix_loop()

    def idle(self) -> None:
        """Sondeos LP de las candidatas que quedan, una por vuelta (entre medias se absorben hechos)."""
        if self._disabled or not self.probe or self.optimum is None or self._lp is None:
            return
        while self._candidates and not self.hint.stop_requested:
            j = self._candidates.pop()
            if self._lower[j] != self._upper[j] and j not in self._probed:
                self._probed.add(j)
                self._probe(j)
                return

    # ------------------------------------------------------------------ LP
    def _set_bounds(self, j: int, value: bool) -> None:
        self._lower[j] = self._upper[j] = int(value)
        self._lp.set_bounds(j, int(value), int(value))

    def _run_solve(self) -> str:
        status = self._lp.solve(lambda: self.hint.stop_requested)
        self.stats.native["lp_solves"] += 1
        return status

    def _solve(self) -> Optional[Tuple[Fraction, List[Fraction], List[float]]]:
        """Resuelve la relajación; ``(cota segura, costes reducidos, x)`` o ``None`` si se interrumpió."""
        assert self.instance is not None
        status = self._run_solve()
        if status == INTERRUPTED:
            return None
        if status == INFEASIBLE:
            # Con las cotas de los backbones (consecuencias de la instancia) la relajación no puede ser
            # infactible: demostrado, algún backbone es falso; sin demostrar, es la coma flotante del
            # solver y el trabajador deja de aportar (no hay contradicción que declarar).
            if self._farkas_proof():
                raise InconsistentKnowledge("lp: la relajación lineal es infactible con los backbones fijados (rayo de Farkas comprobado)")
            self._disabled = f"{self.engine_name} declara infactible la relajación con los backbones fijados y su rayo de Farkas no lo demuestra"
            log.warning("trabajador %d (lp) deja de aportar: %s", self.rank, self._disabled)
            return None
        if status != OPTIMAL:
            raise RuntimeError(f"lp: el LP terminó sin óptimo ({status})")
        y = rational_duals(self.instance, self._lp.duals())
        bound, reduced = safe_bound(self.instance, y, self._lower, self._upper, self._minimize)
        return bound, reduced, self._lp.values()

    def _fix_loop(self) -> None:
        """Fijación por costes reducidos hasta que la cota no fija nada más."""
        assert self.instance is not None
        names = self.instance.names
        rounds = 0
        while not self.hint.stop_requested:
            solved = self._solve()
            if solved is None:
                return
            bound, reduced, values = solved
            rounds += 1
            if proves_infeasible(bound, self._target):
                raise InconsistentKnowledge(f"lp: la cota segura {float(bound):.3f} supera z* = {self._target} con los backbones fijados")
            self._maybe_integral(values)
            fixings = reduced_cost_fixings(bound, reduced, self._lower, self._upper, self._target)
            for j, value in fixings:
                self._set_bounds(j, value)
            self.stats.native["lp_bound"] = float(bound)
            if fixings:
                new_b, _ = self.publish([(names[j], v) for j, v in fixings], [])
                self.stats.native["rc_fixed"] += new_b
                log.info("trabajador %d (lp): cota %.3f, %d backbones por costes reducidos (ronda %d)", self.rank, float(bound), len(fixings), rounds)
                continue
            # Nada más que fijar: las candidatas, por holgura ascendente (las más cerca de fijarse, al final: se sondean primero).
            free = [j for j in range(len(reduced)) if self._lower[j] != self._upper[j]]
            self._candidates = sorted(free, key=lambda j: abs(reduced[j]))
            return

    def _maybe_integral(self, values: Sequence[float]) -> None:
        """Una relajación entera de coste z* es una solución óptima: refuta lo que cambia (comprobada con enteros)."""
        if any(min(v, 1.0 - v) > INTEGRALITY_TOL for v in values):
            return
        assert self.instance is not None
        solution = {name: values[i] > 0.5 for i, name in enumerate(self.instance.names)}
        if self.checker is not None and self.checker.violation(solution, self.optimum) is not None:
            return  # redondeo que no es solución exacta: no es un hecho
        self.refute_by_solution(solution, "relajación entera")

    def _probe(self, j: int) -> None:
        """Sondeo LP de ``x_j``: fijada al valor contrario a x*, ¿la cota segura supera z* (o el LP es infactible)?"""
        assert self.instance is not None
        name = self.instance.names[j]
        base = self.x_star.get(name) if self.x_star else None
        if base is None:
            base = self._lp.values()[j] > 0.5
        self._lower[j] = self._upper[j] = int(not base)
        self._lp.set_bounds(j, int(not base), int(not base))
        self.stats.native["probes"] += 1
        try:
            status = self._run_solve()
            if status == INTERRUPTED:
                return
            proven = status == INFEASIBLE and self._farkas_proof()
            if not proven and status == OPTIMAL:
                # Filtro en coma flotante antes de la cuenta exacta (cara): sólo si podría probar.
                if self._lp.objective() > self._target + 0.5:
                    y = rational_duals(self.instance, self._lp.duals())
                    bound, _ = safe_bound(self.instance, y, self._lower, self._upper, self._minimize)
                    proven = proves_infeasible(bound, self._target)
                else:
                    self._maybe_integral(self._lp.values())
        finally:
            self._lower[j], self._upper[j] = 0, 1
            self._lp.set_bounds(j, 0, 1)
        if proven:
            self._set_bounds(j, bool(base))
            new_b, _ = self.publish([(name, bool(base))], [])
            self.stats.native["probe_fixed"] += new_b
            self._fix_loop()  # una fijación más sube la cota

    def _farkas_proof(self) -> bool:
        """El solver declara infactible el LP actual: ¿lo demuestra su rayo de Farkas, comprobado en racionales?"""
        assert self.instance is not None
        ray = self._lp.farkas()
        if ray is not None and proves_infeasible_by_ray(self.instance, ray, self._lower, self._upper):
            self.stats.native["farkas_proofs"] += 1
            return True
        self.stats.native["farkas_rejected"] += 1
        log.info("trabajador %d (lp): LP infactible según %s sin rayo de Farkas que lo demuestre; no es un hecho",
                 self.rank, self.engine_name)
        return False


def _number(value: Any) -> Any:
    for caster in (int, float):
        try:
            return caster(value)
        except (TypeError, ValueError):
            continue
    return value


__all__ = ["ENGINES", "LPWorker", "engine_of", "is_available"]

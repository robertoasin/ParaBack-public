"""Interfaz abstracta de solvers para la extracción de backbones.

Todo el análisis de backbones (``guroback.extractor``) habla únicamente con
esta interfaz. Ningún módulo de análisis debe importar ``gurobipy`` ni
``pyscipopt`` directamente.

Ciclo de vida de un backend
---------------------------

::

    LOADED ──fix_objective_value()──▶ BASE ──probe()──▶ PROBING ──(salir del with)──▶ BASE
      │                                 ▲                  │
      └── solve() ──────────────────────┘                  └── solve(), solution_value()
                                        │
                                        └── fix_variable() (permanente)

* **LOADED**: el modelo se leyó del archivo; las variables binarias tienen un
  identificador estable (``VarId``) que nunca cambia aunque el solver aplique
  presolve, agregue variables o transforme el problema.
* **BASE**: estado de referencia entre sondeos. Contiene las restricciones
  originales, la restricción que fija el valor óptimo (``fix_objective_value``)
  y las fijaciones permanentes de variables ya demostradas backbone
  (``fix_variable``).
* **PROBING**: dentro de ``with backend.probe() as scope:`` se aplican
  modificaciones *temporales* (cotas o una restricción "al menos un flip") que
  el backend deshace al salir del bloque, incluso si hay excepciones. El
  backend decide cómo materializarlas: Gurobi cambia cotas en el modelo vivo;
  SCIP debe volver a la etapa PROBLEM con ``freeTransform`` (ver
  ``PLAN_SCIP.md``).

Contrato de las modificaciones temporales
-----------------------------------------

* Las llamadas a ``solve`` y ``solution_value`` dentro de un sondeo se
  refieren al problema modificado.
* Al salir del bloque, el backend debe quedar exactamente en el estado BASE
  previo. Las fijaciones permanentes se aplican *fuera* del bloque.
* Los backends no deben reconstruir el modelo desde cero en cada sondeo.

Capacidades opcionales para el trabajo coordinado (ParaBack)
-----------------------------------------------------------

Un trabajador de ParaBack comparte lo que aprende con los demás *entre*
sondeos, y necesita además poder abandonar un sondeo que otro trabajador ya
resolvió. Para ello el contrato añade cuatro capacidades **opcionales**,
anunciadas con atributos de clase y con una implementación por defecto que
no hace nada, de modo que un backend mínimo sigue siendo válido:

* ``supports_interrupt`` / :meth:`SolverBackend.interrupt`: aborta la llamada
  a ``solve`` en curso desde otro hilo (o desde un manejador de señal). El
  ``solve`` interrumpido devuelve ``INTERRUPTED``; el estado BASE no cambia.
* ``supports_time_limit`` / :meth:`SolverBackend.set_time_limit`: límite de
  tiempo *por llamada* a ``solve``. Permite sondeos por rebanadas de tiempo
  cuando no hay interrupción asíncrona: un ``LIMIT_REACHED`` en un sondeo no
  es un error, el extractor puede volver a intentarlo tras sincronizarse.
* ``supports_solution_hint`` / :meth:`SolverBackend.add_solution_hint`:
  sugiere una solución (warm start) obtenida por otro trabajador.
* ``supports_live_fix`` / :meth:`SolverBackend.add_live_fixes`: incorpora
  backbones demostrados por otro trabajador **al solve en curso**, desde otro
  hilo y sin interrumpirlo, en el siguiente punto seguro del solver (nodo de
  branch-and-bound en SCIP, reinicio en RoundingBack; Gurobi no lo ofrece). Requiere
  :meth:`SolverBackend.enable_live_fixes` una vez, antes de los sondeos.

Una capacidad más, para el sondeo ``chunks=maxflip`` del extractor:

* ``supports_max_flips`` / :meth:`ProbeScope.maximize_flips` y
  :meth:`SolverBackend.set_incumbent_listener`: dentro de un sondeo, cambia el
  objetivo por "maximizar cuántas variables de una asignación cambian de valor"
  (con ``z*`` ya fijado, cada solución es óptima del problema original) y avisa
  de cada incumbente que el solver encuentra durante el ``solve``, no sólo de la
  final. El oyente puede además pedir que el ``solve`` termine (estancamiento).

Las fijaciones permanentes (:meth:`SolverBackend.fix_variable`) son el canal
por el que un trabajador incorpora los backbones demostrados por los demás:
fijar una variable backbone a su valor no altera el conjunto de soluciones
óptimas, así que reduce el espacio de búsqueda sin cambiar el resultado.
"""
from __future__ import annotations

import abc
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Generic, Iterator, List, Mapping, Optional, Sequence, Tuple, TypeVar

if TYPE_CHECKING:  # pragma: no cover
    from .opb import OPBInstance

log = logging.getLogger("guroback.backend")

#: Identificador estable de una variable binaria del problema original.
#: Es el índice (0..n-1) dentro de :meth:`SolverBackend.binary_variables`,
#: en el orden en que el solver leyó las variables del archivo.
VarId = int

#: Par (variable, valor) que describe una asignación parcial.
Assignment = Tuple[VarId, bool]


#: Tolerancia con la que ``normalize_objective_value`` considera entero un óptimo.
INTEGER_OBJECTIVE_TOLERANCE = 1e-6


def normalize_objective_value(value: float) -> float:
    """Redondea ``value`` si está a menos de ``INTEGER_OBJECTIVE_TOLERANCE`` de un entero.

    Las instancias PB06 tienen objetivo entero, pero cada solver devuelve ``z*``
    con su propio ruido de punto flotante. Redondearlo antes de imponer
    ``objetivo == z*`` garantiza que Gurobi y SCIP resuelvan exactamente el mismo
    problema de factibilidad (ver ``PLAN_SCIP.md`` §10). Los objetivos no enteros
    se dejan intactos.
    """
    rounded = round(value)
    if abs(value - rounded) < INTEGER_OBJECTIVE_TOLERANCE:
        return float(rounded)
    return value


class SolverCrashed(RuntimeError):
    """El proceso del solver murió por una señal o se quedó sin memoria.

    No es una respuesta errónea sino la ausencia de respuesta: el trabajador que la recibe
    se retira (``ERROR`` no fatal, ``paraback/worker_base.py``) y la ejecución sigue con los
    demás. Los errores de integridad (``InconsistentKnowledge``, una traducción corrupta)
    siguen fallando la ejecución entera.
    """


class Incompatible(SolverCrashed):
    """El solver no puede con esta instancia y lo descubre al prepararla (p. ej. Naps no la traduce a CNF).

    Que un solver admita una instancia no siempre se puede decidir antes de lanzar
    (``docs/PLAN_EVALUACION.md`` D11). Para ParaBack es una retirada, como :class:`SolverCrashed`,
    salvo que haya un sustituto (``--substitute``): entonces el mismo rango sigue con él
    (sustitución en el sitio, ``paraback/worker_base.py``).
    """


class SolveStatus(Enum):
    """Estado normalizado de una llamada a :meth:`SolverBackend.solve`."""

    OPTIMAL = "optimal"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    INFEASIBLE_OR_UNBOUNDED = "infeasible_or_unbounded"
    #: Límite de tiempo, nodos, memoria, trabajo, soluciones, etc.
    LIMIT_REACHED = "limit_reached"
    #: Interrupción externa (señal, callback, ``interruptSolve``).
    INTERRUPTED = "interrupted"
    #: Cualquier otro estado (problemas numéricos, subóptimo, ...).
    OTHER = "other"

    @property
    def is_finished(self) -> bool:
        """``True`` si el solver terminó con una respuesta concluyente.

        Reproduce la semántica del ``is_finished`` de la implementación C++
        original: sólo OPTIMAL, INFEASIBLE y UNBOUNDED son concluyentes.
        """
        return self in (SolveStatus.OPTIMAL, SolveStatus.INFEASIBLE, SolveStatus.UNBOUNDED)


@dataclass(frozen=True)
class SolveResult:
    """Resultado de una llamada a :meth:`SolverBackend.solve`."""

    status: SolveStatus
    #: Valor objetivo si ``status`` es OPTIMAL; ``None`` en otro caso.
    objective: Optional[float]
    #: Segundos consumidos por la llamada. ``0.0`` si el backend no la mide:
    #: :meth:`SolverBackend.solve` pone entonces la de reloj de pared que mide él.
    runtime: float = 0.0
    #: Código de estado nativo del solver, útil para diagnósticos.
    raw_status: Any = None

    @property
    def is_optimal(self) -> bool:
        return self.status is SolveStatus.OPTIMAL

    @property
    def is_finished(self) -> bool:
        return self.status.is_finished


class ProbeScope(abc.ABC):
    """Modificaciones temporales sobre el estado BASE de un backend.

    Se obtiene con ``with backend.probe() as scope:``. Todas las modificaciones
    se deshacen al salir del bloque.
    """

    @abc.abstractmethod
    def exclude_value(self, var: VarId, value: bool) -> None:
        """Impone temporalmente ``x_var != value``.

        Para variables binarias equivale a fijar ``x_var = 1 - value``. Los
        backends deben implementarlo mediante cotas, nunca reconstruyendo el
        modelo.
        """

    @abc.abstractmethod
    def require_any_flip(self, assignment: Sequence[Assignment]) -> None:
        """Impone temporalmente que al menos una variable difiera de ``assignment``.

        Es la restricción ``sum_i lit_i >= 1`` con ``lit_i = 1 - x_i`` si el
        valor asignado es 1 y ``lit_i = x_i`` si es 0. Se usa para sondear
        varios candidatos de una sola vez.
        """

    def maximize_flips(self, assignment: Sequence[Assignment]) -> None:
        """Cambia temporalmente el objetivo por ``max sum_i lit_i`` (los ``lit_i`` de :meth:`require_any_flip`).

        El objetivo es entero: el backend puede dar por óptima una incumbente en cuanto la
        cota dual baja de ``incumbente + 1``. Sólo con ``supports_max_flips``.
        """
        raise NotImplementedError(f"{type(self).__name__} no admite maximize_flips")

    @abc.abstractmethod
    def _restore(self) -> None:
        """Deshace todas las modificaciones (lo invoca :meth:`SolverBackend.probe` al salir)."""


class IncumbentListener(abc.ABC):
    """Recibe las soluciones que el solver encuentra durante un ``solve`` (:meth:`SolverBackend.set_incumbent_listener`).

    El backend lo llama desde el hilo que ejecuta ``solve``. Una excepción del oyente
    termina el ``solve`` y se propaga desde él.
    """

    @abc.abstractmethod
    def on_incumbent(self, values: Sequence[float]) -> None:
        """Nueva incumbente: ``values[i]`` es el valor de ``binary_variables()[i]``."""

    def should_stop(self) -> bool:
        """Se consulta a menudo durante el ``solve``; ``True`` lo termina con ``INTERRUPTED``."""
        return False


#: Restricción temporal tal como la representa cada solver (un ``Constr`` de Gurobi, …).
_Handle = TypeVar("_Handle")


class BoundsProbeScope(ProbeScope, Generic[_Handle]):
    """Sondeo materializado con cotas y restricciones temporales (Gurobi, SCIP, fuerza bruta).

    ``exclude_value`` guarda las cotas originales la primera vez que toca una variable y
    la fija al valor contrario; ``require_any_flip`` añade una restricción temporal. Al
    salir se restauran las cotas y se borran las restricciones. Cada solver aporta cómo
    leer y escribir cotas y cómo añadir y quitar la restricción.
    """

    def __init__(self) -> None:
        self._saved_bounds: Dict[VarId, Tuple[float, float]] = {}
        self._constraints: List[_Handle] = []
        self._flip_objective = False

    # ------------------------------------------------------------------ lo que aporta cada solver
    @abc.abstractmethod
    def _get_bounds(self, var: VarId) -> Tuple[float, float]: ...

    @abc.abstractmethod
    def _set_bounds(self, var: VarId, lb: float, ub: float) -> None: ...

    @abc.abstractmethod
    def _add_any_flip(self, assignment: Sequence[Assignment]) -> _Handle: ...

    @abc.abstractmethod
    def _remove(self, constraint: _Handle) -> None: ...

    def _set_flip_objective(self, assignment: Sequence[Assignment]) -> None:
        """Objetivo ``max sum lit_i`` (``maximize_flips``), guardando el que había."""
        raise NotImplementedError(f"{type(self).__name__} no admite maximize_flips")

    def _restore_objective(self) -> None:
        """Vuelve al objetivo guardado por :meth:`_set_flip_objective`."""
        raise NotImplementedError(f"{type(self).__name__} no admite maximize_flips")

    def _before_change(self) -> None:
        """Antes de modificar el modelo (SCIP: volver a la etapa ``PROBLEM``)."""

    def _after_restore(self) -> None:
        """Tras deshacer las modificaciones (Gurobi: ``update``)."""

    # ------------------------------------------------------------------ ProbeScope
    def exclude_value(self, var: VarId, value: bool) -> None:
        self._before_change()
        if var not in self._saved_bounds:
            self._saved_bounds[var] = self._get_bounds(var)
        flipped = 0.0 if value else 1.0
        self._set_bounds(var, flipped, flipped)

    def require_any_flip(self, assignment: Sequence[Assignment]) -> None:
        self._before_change()
        self._constraints.append(self._add_any_flip(assignment))

    def maximize_flips(self, assignment: Sequence[Assignment]) -> None:
        if self._flip_objective:
            raise RuntimeError("maximize_flips ya está activo en este sondeo")
        self._before_change()
        self._set_flip_objective(assignment)
        self._flip_objective = True

    def _restore(self) -> None:
        if self._saved_bounds or self._constraints or self._flip_objective:
            self._before_change()
            for var, (lb, ub) in self._saved_bounds.items():
                self._set_bounds(var, lb, ub)
            for constraint in self._constraints:
                self._remove(constraint)
            if self._flip_objective:
                self._restore_objective()
                self._flip_objective = False
            self._saved_bounds.clear()
            self._constraints.clear()
        self._after_restore()


class SolverBackend(abc.ABC):
    """Contrato que debe cumplir todo solver usado por el extractor de backbones.

    Convenciones para las implementaciones concretas:

    * El constructor recibe ``(instance_path, params)`` donde ``params`` es un
      mapa ``nombre -> valor`` (cadenas) con parámetros nativos del solver.
      El backend aplica sus valores por defecto (salida silenciosa, gap 0) y
      luego los del usuario. Debe llamar a ``super().__init__(instance_path, params)``
      y cargar el modelo del archivo una única vez.
    * ``binary_variables`` devuelve los ``VarId`` en un orden determinista y
      fijo durante toda la vida del objeto.
    * ``solve`` está implementado como método plantilla: mide tiempo y cuenta
      llamadas, delegando en :meth:`_solve`.
    * ``close`` libera todos los recursos nativos; el objeto es además un
      gestor de contexto.
    """

    #: Nombre con el que el backend se registra en la CLI (``--solver``).
    name: ClassVar[str] = "abstract"

    #: Capacidades opcionales (ver la sección "trabajo coordinado" del módulo).
    supports_interrupt: ClassVar[bool] = False
    supports_time_limit: ClassVar[bool] = False
    supports_solution_hint: ClassVar[bool] = False
    supports_live_fix: ClassVar[bool] = False
    #: :meth:`fixed_literals` informa de los literales que el solver fijó en la raíz.
    supports_fixed: ClassVar[bool] = False
    #: :meth:`ProbeScope.maximize_flips` y :meth:`set_incumbent_listener` (``chunks=maxflip``).
    supports_max_flips: ClassVar[bool] = False
    #: ``True`` si el solver trabaja con aritmética entera exacta: sus soluciones
    #: son exactamente factibles por construcción y no hace falta comprobarlas
    #: (:mod:`guroback.exact`). Gurobi y SCIP (coma flotante) lo dejan en ``False``.
    exact_arithmetic: ClassVar[bool] = False

    @classmethod
    def capabilities(cls) -> Mapping[str, bool]:
        """Resumen de las capacidades opcionales, útil para ``--list-solvers`` y ParaBack."""
        return {
            "interrupt": cls.supports_interrupt,
            "time_limit": cls.supports_time_limit,
            "solution_hint": cls.supports_solution_hint,
            "live_fix": cls.supports_live_fix,
            "fixed": cls.supports_fixed,
            "max_flips": cls.supports_max_flips,
        }

    @classmethod
    def is_available(cls) -> Tuple[bool, str]:
        """``(disponible, motivo)``: ¿puede este backend resolver en este entorno?

        Permite que la CLI (``--list-solvers``) y los tests diagnostiquen
        instalaciones parciales sin importar ``gurobipy`` ni ``pyscipopt``
        directamente. Las implementaciones que dependen de una librería nativa o
        de una licencia deben sobrescribirlo; por defecto se asume disponible.
        """
        return True, ""

    def __init__(self, instance_path: str, params: Optional[Mapping[str, str]] = None) -> None:
        self.instance_path = instance_path
        self.params: Mapping[str, str] = dict(params or {})
        self._solve_count = 0
        self._total_solve_time = 0.0
        self._closed = False
        #: Oyente de incumbentes del próximo ``solve`` (``supports_max_flips``).
        self._incumbent_listener: Optional[IncumbentListener] = None
        #: ``z*`` ya impuesto por :meth:`fix_objective_value` (normalizado), o ``None``.
        self._fixed_objective_target: Optional[float] = None

    # ------------------------------------------------------------------ ciclo de vida
    def __enter__(self) -> "SolverBackend":
        return self

    def __exit__(self, exc_type: Optional[type], exc: Optional[BaseException], tb: object) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - se registra; sólo se propaga si no había otro error
            if exc_type is None:
                raise
            # Un fallo al cerrar no sustituye al error que ya sale del bloque: una caída del solver
            # (SolverCrashed, retirada) acababa como un error fatal del cierre (h2-extraer, comppebphp_38).
            log.warning("fallo al cerrar %s tras %s", type(self).__name__, exc_type.__name__, exc_info=True)

    def close(self) -> None:
        """Libera recursos nativos. Idempotente."""
        if not self._closed:
            self._closed = True
            self._close()

    @abc.abstractmethod
    def _close(self) -> None:
        """Liberación de recursos específica del solver."""

    # ------------------------------------------------------------------ introspección
    @abc.abstractmethod
    def binary_variables(self) -> Sequence[VarId]:
        """Identificadores estables de todas las variables binarias del problema."""

    @abc.abstractmethod
    def variable_name(self, var: VarId) -> str:
        """Nombre de la variable en el archivo de instancia (p. ej. ``x12``)."""

    @abc.abstractmethod
    def has_objective(self) -> bool:
        """``True`` si alguna variable tiene coeficiente objetivo distinto de cero."""

    # ------------------------------------------------------------------ parámetros
    @abc.abstractmethod
    def set_parameter(self, name: str, value: str) -> None:
        """Fija un parámetro nativo del solver a partir de su representación textual.

        Debe lanzar ``ValueError`` (o la excepción nativa) si el parámetro no existe.
        """

    @abc.abstractmethod
    def set_feasibility_focus(self) -> None:
        """Configura el solver para priorizar encontrar soluciones factibles.

        Equivale a ``MIPFocus=1`` en Gurobi o al énfasis ``feasibility`` en SCIP.
        Se invoca una vez, tras conocer el óptimo, antes de la fase de sondeos.
        """

    # ------------------------------------------------------------------ estado base
    @abc.abstractmethod
    def fix_objective_value(self, value: float) -> None:
        """Convierte el problema en uno de factibilidad sobre el conjunto óptimo.

        Añade la restricción ``objetivo == value`` y sustituye la función
        objetivo por cero. Es una modificación permanente del estado BASE.

        Repetirla con el mismo ``z*`` no cambia nada: un trabajador de ParaBack cuya
        verificación de la base se interrumpe (llega el ``BASE_HINT`` de otro) la repite
        con un extractor nuevo sobre el mismo backend. Con otro valor es un error
        (:meth:`_objective_already_fixed`).
        """

    def _objective_already_fixed(self, target: float) -> bool:
        """``True`` si ``z* = target`` ya está impuesto (``fix_objective_value`` repetido no hace nada).

        Con otro valor lanza ``ValueError``: el estado BASE ya es el de otro óptimo. Cada
        backend que no sea idempotente por construcción lo consulta al principio y anota
        ``_fixed_objective_target`` cuando impone el valor.
        """
        current = self._fixed_objective_target
        if current is None:
            return False
        if current == target:
            return True
        raise ValueError(f"el backend {self.name} ya tiene z* = {current} fijado; no puede pasar a {target}")

    @abc.abstractmethod
    def fix_variable(self, var: VarId, value: bool) -> None:
        """Fija permanentemente ``x_var = value`` en el estado BASE.

        Se invoca cuando una variable se demuestra backbone; reduce el espacio
        de búsqueda de los sondeos siguientes.
        """

    # ------------------------------------------------------------------ optimización
    def solve(self) -> SolveResult:
        """Resuelve el problema en su estado actual (BASE o PROBING)."""
        start = time.perf_counter()
        result = self._solve()
        elapsed = time.perf_counter() - start
        self._solve_count += 1
        self._total_solve_time += elapsed
        self._account_solve(elapsed)
        return result if result.runtime else replace(result, runtime=elapsed)

    def _account_solve(self, elapsed: float) -> None:
        """Segundos de reloj de pared de la última llamada a ``solve`` (para ``native_stats``)."""

    @abc.abstractmethod
    def _solve(self) -> SolveResult:
        """Llamada nativa al solver y traducción de su estado a :class:`SolveResult`."""

    @abc.abstractmethod
    def solution_value(self, var: VarId) -> float:
        """Valor de ``var`` en la mejor solución de la última llamada a ``solve``.

        Sólo es válido si el último resultado fue OPTIMAL. Debe funcionar sobre
        la variable *original* aunque el solver la haya agregado o eliminado en
        presolve.
        """

    def set_incumbent_listener(self, listener: Optional[IncumbentListener]) -> None:
        """Oyente de las incumbentes de los próximos ``solve`` (``None``: ninguno). Sólo con ``supports_max_flips``."""
        if listener is not None and not self.supports_max_flips:
            raise NotImplementedError(f"el backend {self.name} no informa de incumbentes")
        self._incumbent_listener = listener

    # ------------------------------------------------------------------ sondeos
    @contextmanager
    def probe(self) -> Iterator[ProbeScope]:
        """Abre un bloque de modificaciones temporales (ver :class:`ProbeScope`).

        Las deshace al salir del bloque, también ante excepciones.
        """
        scope = self._open_scope()
        try:
            yield scope
        finally:
            scope._restore()

    @abc.abstractmethod
    def _open_scope(self) -> ProbeScope:
        """El :class:`ProbeScope` de un sondeo nuevo (lo cierra :meth:`probe`)."""

    # ------------------------------------------------------------------ capacidades opcionales
    def interrupt(self) -> None:
        """Pide abortar la llamada a :meth:`solve` en curso.

        Debe poder invocarse desde otro hilo mientras ``solve`` se ejecuta; el
        ``solve`` interrumpido devuelve ``SolveStatus.INTERRUPTED`` y el estado
        BASE no cambia. Si no hay ningún ``solve`` en curso no hace nada. Los
        backends que la soportan ponen ``supports_interrupt = True``; la
        implementación por defecto no hace nada.
        """

    def set_time_limit(self, seconds: Optional[float]) -> None:
        """Límite de tiempo (segundos de reloj o CPU según el solver) por llamada a ``solve``.

        ``None`` elimina el límite. Un ``solve`` que lo agota devuelve
        ``LIMIT_REACHED``. Los backends que lo soportan ponen
        ``supports_time_limit = True``; la implementación por defecto no hace nada.
        """

    def add_solution_hint(self, values: Mapping[VarId, bool]) -> None:
        """Sugiere una asignación (parcial) como punto de partida del próximo ``solve``.

        Es sólo una pista: no restringe el problema. Los backends que la
        soportan ponen ``supports_solution_hint = True``; la implementación por
        defecto la ignora.
        """

    def enable_live_fixes(self) -> bool:
        """Prepara el backend para :meth:`add_live_fixes`; devuelve si quedó activo.

        Se invoca una vez, en el hilo principal y fuera de un ``solve``, tras
        fijar ``z*`` (en adelante toda solución factible es óptima, así que un
        backbone es una desigualdad válida). Puede instalar callbacks o
        propagadores con coste propio, por eso no está activo por defecto. La
        implementación por defecto no hace nada y devuelve ``False``.
        """
        return False

    def add_live_fixes(self, assignment: Sequence[Assignment]) -> None:
        """Impone ``x_var = value`` en el ``solve`` en curso y en los siguientes.

        Cada par debe ser un backbone **demostrado** (vale en toda solución
        óptima): sólo entonces la fijación no cambia la respuesta de ningún
        sondeo. Es seguro llamarlo desde otro hilo mientras ``solve`` se
        ejecuta; el solver la aplica en su próximo punto seguro sin abortar la
        búsqueda. No toca el estado BASE: el extractor sigue fijando esos
        backbones con :meth:`fix_variable`, que además retira la fijación viva.
        Sin :meth:`enable_live_fixes` activo no hace nada.
        """

    def fixed_literals(self) -> Optional[Mapping[VarId, bool]]:
        """Literales que el solver fijó en la raíz desde la última llamada, o ``None`` si no lo sabe.

        Con ``objetivo = z*`` fijado, un literal fijado en la raíz es consecuencia de la
        instancia con su óptimo (y de los backbones ya fijados): es backbone sin sondear,
        como hace CadiBack en cada iteración (``docs/PLAN_REFACTOR.md`` §1.8). Sólo lo
        ofrecen los solvers exactos con servidor (RoundingSat, NapBack); un MIP en coma
        flotante fija variables en el presolve también por reducciones duales, que no son
        implicaciones.
        """
        return None

    def instance(self) -> Optional["OPBInstance"]:
        """La instancia ya leída en Python por este backend, o ``None`` si no la lee.

        Un trabajador de ParaBack la reutiliza para su :class:`~guroback.exact.ExactChecker`
        en lugar de leer el archivo otra vez: en instancias enormes cada lectura en Python
        cuesta varias veces el tamaño del texto (``docs/PLAN_REFACTOR.md`` §1.1). Los
        backends que leen la instancia con un lector nativo (Gurobi, SCIP, RoundingSat)
        devuelven ``None``; el comprobador la lee entonces una única vez.
        """
        return None

    def propagation_scores(self) -> Optional[Mapping[VarId, float]]:
        """Puntuación de actividad de propagación de cada variable (RB-WP), o ``None``.

        ICAART 2026, §3.1.1: cuando una variable forma parte de la razón de una
        propagación, suma ``coef(v, razón) / coef_máximo(razón)``. Se invoca una
        vez, tras la solución base y con ``z*`` fijado, desde el hilo principal;
        el backend puede resolver para medirla (con ``interrupt`` activo). La usa
        ``order=wp``; por defecto no hay puntuaciones.
        """
        return None

    # ------------------------------------------------------------------ estadísticas
    @property
    def solve_count(self) -> int:
        """Número de llamadas a :meth:`solve` realizadas."""
        return self._solve_count

    @property
    def total_solve_time(self) -> float:
        """Segundos acumulados dentro de :meth:`solve`."""
        return self._total_solve_time

    @property
    def native_stats(self) -> Mapping[str, float]:
        """Contadores nativos acumulados sobre todas las llamadas a ``solve``.

        Claves comunes a los backends que las publican: ``nodes`` (nodos de
        branch-and-bound) e ``iterations`` (iteraciones de símplex). Cada
        backend puede añadir las suyas. La validación diferencial de la Fase 3
        las usa para comparar el coste de reoptimización de Gurobi y SCIP; los
        backends que no lleven estadísticas devuelven un mapa vacío.
        """
        return {}


__all__ = [
    "Assignment",
    "BoundsProbeScope",
    "INTEGER_OBJECTIVE_TOLERANCE",
    "IncumbentListener",
    "normalize_objective_value",
    "ProbeScope",
    "SolveResult",
    "SolveStatus",
    "SolverBackend",
    "VarId",
]

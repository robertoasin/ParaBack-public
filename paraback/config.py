"""Parámetros de ejecución de ParaBack: especificación de trabajadores y del orquestador.

Un trabajador se describe con un :class:`WorkerSpec` (solver, orden de
variables, política de chunks y parámetros nativos). Su forma textual, usada
por la CLI de la Fase 2 (``--worker``), es::

    <solver>[:clave=valor[,clave=valor...]]

    gurobi
    scip:order=reverse
    roundingsat:order=random:7,chunks=fixed:4
    gurobi:order=strided:1/4,Threads=1,TimeLimit=60
    gurobi:chunks=maxflip:30

Las claves ``rotation`` (``auto``, ``none``, ``flippable``, ``eager``) y ``fixed``
(``auto``, ``none``) eligen los hechos que se obtienen sin sondear (``docs/PLAN_REFACTOR.md``
fase 7): rotación de modelos y literales fijados en la raíz por el solver o por la
propagación PB. ``auto`` rota y propaga sólo si la instancia ya está en memoria Python
(comprobador exacto, NapBack, fuerza bruta): no se lee sólo para eso.

Las claves ``order`` y ``chunks`` seleccionan estrategias registradas en
:data:`guroback.extractor.VARIABLE_ORDERS` y :data:`guroback.extractor.CHUNK_POLICIES`
(con argumentos tras ``:``); cualquier otra clave es un parámetro nativo del
solver y se pasa tal cual al backend. ``chunks=maxflip[:T]`` no es un tamaño de chunk
sino otro sondeo: maximizar cuántos candidatos cambian de valor, cortando la llamada
tras ``T`` s sin refutar nada nuevo (:class:`guroback.extractor.MaxFlipChunks`); sólo
con solvers que lo admiten (``supports_max_flips``: Gurobi, RoundingSat).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from guroback import BACKENDS, get_backend_class
from guroback.extractor import (
    CHUNK_POLICIES,
    AllChunks,
    GeometricChunks,
    VARIABLE_ORDERS,
    ChunkPolicy,
    DoublingChunks,
    FileOrder,
    FixedChunks,
    MaxFlipChunks,
    RandomOrder,
    ReverseOrder,
    StridedOrder,
    ValueOrder,
    VariableOrder,
    WeightedPropagationOrder,
)

#: Rango MPI reservado al orquestador; los trabajadores son 1..size-1.
ORCHESTRATOR_RANK = 0

#: Valores de ``WorkerSpec.rotation`` y ``WorkerSpec.fixed``.
ROTATION_MODES: Tuple[str, ...] = ("auto", "none", "flippable", "eager")
FIXED_MODES: Tuple[str, ...] = ("auto", "none")

#: Solvers en coma flotante: ``--float-max-bits`` los sustituye, y su infactibilidad no es una
#: prueba: ni para la ejecución ni la declara infactible si en el portfolio hay un solver exacto que
#: pueda demostrarla, y una solución comprobada con enteros la desmiente (``Orchestrator._on_base``).
FLOAT_SOLVERS: Tuple[str, ...] = ("gurobi", "scip", "lp")

#: Trabajadores pasivos (``paraback/passive_worker.py``): no sondean siguiendo un orden (su sustituto
#: no hereda ninguno) ni demuestran infactibilidad.
PASSIVE_WORKERS: Tuple[str, ...] = ("lp", "symmetry")

#: Solvers multihilo: ``--threads-per-worker`` les fija ``Threads`` si su especificación no lo hace.
THREADED_SOLVERS: Tuple[str, ...] = ("gurobi", "scip", "scip-exact", "lp")

#: Solvers con límite de memoria propio y cómo se expresa (``--memory-limit``, en MB): el solver
#: para con ``LIMIT_REACHED`` en lugar de que el sistema mate el proceso (y con él, bajo MPI,
#: el trabajo entero sin resultados). Gurobi: ``SoftMemLimit`` en GB; SCIP: ``limits/memory`` en MB.
MEMORY_LIMIT_PARAMS: Mapping[str, Tuple[str, float]] = {
    "gurobi": ("SoftMemLimit", 1.0 / 1024.0),
    "scip": ("limits/memory", 1.0),
    "scip-exact": ("limits/memory", 1.0),
    "lp": ("SoftMemLimit", 1.0 / 1024.0),
    # Servidores sin límite propio: ``RLIMIT_AS`` del subproceso (``guroback.line_server.preexec_with_limit``);
    # al agotarlo el servidor aborta y el trabajador se retira sin fallar la ejecución.
    "roundingsat": ("MemoryLimit", 1.0),
    "napback": ("MemoryLimit", 1.0),
    "cadiback": ("MemoryLimit", 1.0),
    # El detector de simetrías (bliss) corre en un subproceso con el mismo ``RLIMIT_AS``.
    "symmetry": ("MemoryLimit", 1.0),
}

#: Trabajadores que no son un ``SolverBackend`` + extractor de ParaBack, sino que ejecutan
#: el algoritmo de otro extractor y hablan el protocolo (``paraback.worker.make_worker``).
NATIVE_WORKERS: Mapping[str, str] = {
    "cadiback": "paraback.cadiback_worker:CadibackWorker",
    # Trabajadores pasivos (paraback/passive_worker.py): no sondean, derivan hechos.
    "symmetry": "paraback.symmetry_worker:SymmetryWorker",
    "lp": "paraback.lp_worker:LPWorker",
}
#: Órdenes que un trabajador nativo traduce a su propia opción (``cadiback --order=``).
NATIVE_ORDERS: Tuple[str, ...] = ("file", "reverse", "random", "strided")


class ConfigError(ValueError):
    """Configuración de ParaBack inválida."""


#: Errores de configuración que la CLI y el rango 0 de MPI informan con "Error: …" y código 1.
CONFIG_ERRORS = (ConfigError, ValueError, OSError)


def _split_strategy(text: str) -> Tuple[str, str]:
    name, _, args = text.partition(":")
    return name.strip(), args.strip()


def make_order(spec: str) -> VariableOrder:
    """Construye una :class:`VariableOrder` a partir de ``nombre[:argumentos]``."""
    name, args = _split_strategy(spec)
    if name not in VARIABLE_ORDERS:
        raise ConfigError(f"orden de variables desconocido {name!r}; disponibles: {', '.join(sorted(VARIABLE_ORDERS))}")
    if name == FileOrder.name:
        return FileOrder()
    if name == ReverseOrder.name:
        return ReverseOrder()
    if name == RandomOrder.name:
        return RandomOrder(int(args or 0))
    if name == StridedOrder.name:
        offset, _, stride = (args or "0/1").partition("/")
        return StridedOrder(int(offset or 0), int(stride or 1))
    if name == ValueOrder.name:
        return ValueOrder(args.lower() not in ("0", "false"))
    if name == WeightedPropagationOrder.name:
        return WeightedPropagationOrder()
    raise ConfigError(f"orden {name!r} registrado pero sin constructor")  # pragma: no cover


def make_chunks(spec: str) -> ChunkPolicy:
    """Construye una :class:`ChunkPolicy` a partir de ``nombre[:argumentos]``."""
    name, args = _split_strategy(spec)
    if name not in CHUNK_POLICIES:
        raise ConfigError(f"política de chunks desconocida {name!r}; disponibles: {', '.join(sorted(CHUNK_POLICIES))}")
    if name == DoublingChunks.name:
        return DoublingChunks()
    if name == FixedChunks.name:
        return FixedChunks(int(args or 1))
    if name == GeometricChunks.name:
        return GeometricChunks(float(args or 2))
    if name == AllChunks.name:
        return AllChunks()
    if name == MaxFlipChunks.name:
        try:
            stall = float(args) if args else None
        except ValueError:
            raise ConfigError(f"chunks={spec!r}: el estancamiento debe ser un número de segundos") from None
        try:
            return MaxFlipChunks(stall)
        except ValueError as exc:
            raise ConfigError(f"chunks={spec!r}: {exc}") from None
    raise ConfigError(f"política {name!r} registrada pero sin constructor")  # pragma: no cover


@dataclass(frozen=True)
class WorkerSpec:
    """Un trabajador del portfolio: qué solver ejecuta y con qué principio de búsqueda."""

    solver: str
    order: str = "file"
    chunks: str = "doubling"
    params: Mapping[str, str] = field(default_factory=dict)
    label: Optional[str] = None
    #: Rotación de modelos (``auto``: sólo si la instancia ya está en memoria).
    rotation: str = "auto"
    #: Literales fijados en la raíz: por el solver y por la propagación PB (``auto``) o ninguno.
    fixed: str = "auto"

    def __post_init__(self) -> None:
        if self.solver not in BACKENDS and self.solver not in NATIVE_WORKERS:
            known = sorted(set(BACKENDS) | set(NATIVE_WORKERS))
            raise ConfigError(f"solver desconocido {self.solver!r}; disponibles: {', '.join(known)}")
        make_order(self.order)
        make_chunks(self.chunks)
        if self.rotation not in ROTATION_MODES:
            raise ConfigError(f"rotation={self.rotation!r} no válido; valores: {', '.join(ROTATION_MODES)}")
        if self.fixed not in FIXED_MODES:
            raise ConfigError(f"fixed={self.fixed!r} no válido; valores: {', '.join(FIXED_MODES)}")
        if self.solver in NATIVE_WORKERS:
            if (self.chunks, self.rotation, self.fixed) != ("doubling", "auto", "auto"):
                raise ConfigError(f"{self.solver!r} usa su propio algoritmo: no admite chunks=, rotation= ni fixed=")
            # Cadiback sondea en el orden de ``--order`` (patches/cadiback/0002): sólo los órdenes
            # que no dependen de la solución base ni del solver (``value``, ``wp`` no). Los
            # trabajadores pasivos no sondean: ningún orden.
            allowed = NATIVE_ORDERS if self.solver == "cadiback" else ("file",)
            if _split_strategy(self.order)[0] not in allowed:
                raise ConfigError(f"{self.solver!r} sólo admite order= {', '.join(allowed)} (recibido {self.order!r})")
        if self.solver in BACKENDS and isinstance(make_chunks(self.chunks), MaxFlipChunks):
            try:
                supported = get_backend_class(self.solver).supports_max_flips
            except ImportError:
                supported = True  # sin el módulo del solver: el trabajador lo dirá al arrancar
            if not supported:
                raise ConfigError(f"chunks=maxflip: el solver {self.solver!r} no maximiza cambios (gurobi y roundingsat sí; bruteforce, en los tests)")
        object.__setattr__(self, "params", dict(self.params))

    @classmethod
    def parse(cls, text: str) -> "WorkerSpec":
        solver, _, rest = text.strip().partition(":")
        order, chunks, params = "file", "doubling", {}
        strategy: Dict[str, str] = {}
        if rest:
            for item in rest.split(","):
                key, sep, value = item.partition("=")
                if not sep:
                    raise ConfigError(f"esperaba clave=valor en {item!r} (trabajador {text!r})")
                key = key.strip()
                if key == "order":
                    order = value.strip()
                elif key == "chunks":
                    chunks = value.strip()
                elif key in ("rotation", "fixed"):
                    strategy[key] = value.strip()
                else:
                    params[key] = value.strip()
        return cls(solver=solver.strip(), order=order, chunks=chunks, params=params, **strategy)

    def make_order(self) -> VariableOrder:
        return make_order(self.order)

    def make_chunks(self) -> ChunkPolicy:
        return make_chunks(self.chunks)

    def describe(self) -> str:
        params = ",".join(f"{k}={v}" for k, v in self.params.items())
        text = f"{self.solver}:order={self.order},chunks={self.chunks}"
        # rotation y fixed sólo si no son los de por defecto: las descripciones de siempre no cambian.
        text += "".join(f",{k}={v}" for k, v in (("rotation", self.rotation), ("fixed", self.fixed)) if v != "auto")
        return text + ("," + params if params else "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "solver": self.solver, "order": self.order, "chunks": self.chunks, "params": dict(self.params),
            "label": self.label, "rotation": self.rotation, "fixed": self.fixed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkerSpec":
        return cls(
            solver=str(data["solver"]),
            order=str(data.get("order", "file")),
            chunks=str(data.get("chunks", "doubling")),
            params={str(k): str(v) for k, v in dict(data.get("params") or {}).items()},
            label=data.get("label"),
            rotation=str(data.get("rotation", "auto")),
            fixed=str(data.get("fixed", "auto")),
        )


@dataclass
class ParaBackConfig:
    """Parámetros del orquestador."""

    instance: str
    workers: List[WorkerSpec]
    #: Archivo de salida ``b <lit>`` / ``b 0`` (``None``: no escribir).
    backbone_out: Optional[str] = None
    #: Óptimo conocido (``GurobackOptimum``); se pasa a todos los trabajadores.
    optimum: Optional[float] = None
    #: Límite global de reloj en segundos (``None``: sin límite).
    time_limit: Optional[float] = None
    #: Límite por llamada a ``solve`` en los sondeos (sondeos por rebanadas). ``None``: sin límite.
    probe_time_slice: Optional[float] = None
    #: Sondeos entre dos sincronizaciones con el orquestador (1 = tras cada sondeo).
    sync_every: int = 1
    #: Relevar el z* y la asignación óptima x* del primer trabajador que resuelve
    #: su base a los que aún no resolvieron la suya, para que interrumpan su solve
    #: base (si su backend lo soporta) y se limiten a verificar x* en z* (un solve
    #: con todas las variables fijadas) en vez de buscar una solución propia
    #: (``paraback/worker.py``).
    share_base_optimum: bool = True
    #: Usar cada ``UPDATE`` en el sondeo en curso, sin esperar al siguiente: los
    #: backbones ajenos se imponen dentro del ``solve`` en marcha
    #: (``add_live_fixes``) y un sondeo cuyo chunk ya decidió otro trabajador se
    #: interrumpe (``paraback/worker.py``; ``docs/PLAN_PARABACK.md`` §7.4).
    live_updates: bool = True
    #: Comprobar con aritmética entera cada solución antes de refutar con ella
    #: (``guroback/exact.py``): Gurobi y SCIP aceptan, dentro de sus tolerancias,
    #: asignaciones 0/1 que no son factibles, y refutarían backbones reales.
    exact_check: bool = True
    #: Compartir las soluciones óptimas de los sondeos (refutan candidatos en los demás).
    share_solutions: bool = True
    #: Reenviar las soluciones a los trabajadores como pistas (``add_solution_hint``).
    share_hints: bool = False
    #: Reenviar a los trabajadores los hechos de los demás (``UPDATE``). ``False``: cada uno
    #: trabaja aislado y el orquestador sólo une lo que demuestran (para cuando el conjunto está
    #: completo y para la salida). No afecta a ``BASE_HINT`` (``share_base_optimum``).
    share_facts: bool = True
    #: Período (s) del reenvío: 0 = en cuanto llegan (cada ``FACTS`` con algo nuevo produce un
    #: ``UPDATE`` para cada uno de los demás); ``S > 0`` = como mucho un ``UPDATE`` por trabajador
    #: cada ``S`` s con todo lo acumulado. Los ``FACTS`` llegan al orquestador al momento en ambos
    #: casos: el conocimiento global, la parada al completarlo y la salida no esperan.
    share_interval: float = 0.0
    #: Detener a todos los trabajadores en cuanto el conocimiento global esté completo.
    stop_when_complete: bool = True
    #: Segundos de espera del orquestador entre comprobaciones de límite/estado.
    poll_interval: float = 0.05
    #: Hilo de escucha en cada trabajador: al llegar ``STOP`` interrumpe el sondeo en curso
    #: (``backend.interrupt()``) en lugar de esperar a que termine (``docs/PLAN_PARABACK.md`` §7.3).
    interrupt_on_stop: bool = True
    #: Nivel de log de ``paraback`` (el mismo valor por defecto que ``--log-level``).
    log_level: str = "WARNING"
    #: Directorio para la traza de eventos por rango (``paraback/trace.py``); ``None`` = sin traza.
    trace_dir: Optional[str] = None
    #: Solución óptima x* de una ejecución anterior (``--base-solution``, ya validada; ``optimum``
    #: es su z*): todos los trabajadores la verifican en vez de resolver la base.
    base_solution: Optional[str] = None
    #: ``--base-only``: parar en cuanto un trabajador resuelva la base (z* y x*), sin sondear. Sirve
    #: para calcular los óptimos de un conjunto de instancias con el portfolio ("gana el primero").
    base_only: bool = False
    #: ``--solution-out``: escribir x* (la base) en el formato de la competición PB (``s``, ``o``,
    #: ``v``), el mismo que lee ``--base-solution``.
    solution_out: Optional[str] = None
    #: Instante (``time.time()``) en que empezó la ejecución en el lanzador: ``time_limit`` se cuenta
    #: desde aquí, así incluye lo anterior a mpirun (lectura de la solución base, arranque).
    start_time: Optional[float] = None
    #: Segundos que el orquestador espera tras STOP a los trabajadores; los que siguen sin terminar
    #: se abandonan (se escriben los resultados y se aborta el trabajo MPI).
    stop_grace: float = 60.0
    #: Anunciar el chunk que cada trabajador empieza a sondear (``INTENT``) y reenviar a los
    #: demás lo que se está sondeando (``UPDATE.probing_elsewhere``). Sólo prepara la
    #: coordinación de intenciones: ninguna política lo usa todavía para elegir chunks
    #: (``ChunkPolicy.select``); el orquestador cuenta los solapamientos.
    announce_intents: bool = False
    #: Archivo de ``--stats-json`` (lo escribe el rango 0 al terminar).
    stats_json: Optional[str] = None
    #: ``--check``: solver exacto con el que comprobar el backbone escrito (``auto``: el primero disponible).
    check: Optional[str] = None
    #: Archivo donde el rango 0 deja el código de salida para el lanzador (``mpirun`` no lo propaga).
    status_file: Optional[str] = None
    #: ``--substitute``: el trabajador que ocupa el hueco de uno que no admite la instancia, antes de
    #: lanzar (enteros demasiado grandes, ``paraback/launch.py``) o al prepararla (sustitución en el
    #: sitio, ``paraback/worker_base.py``). Hereda el orden del hueco.
    substitute: Optional[WorkerSpec] = None

    def __post_init__(self) -> None:
        if not self.workers:
            raise ConfigError("ParaBack necesita al menos un trabajador")
        if self.sync_every < 1:
            raise ConfigError("sync_every debe ser >= 1")
        if self.time_limit is not None and self.time_limit <= 0:
            raise ConfigError("time_limit debe ser positivo")
        if self.probe_time_slice is not None and self.probe_time_slice <= 0:
            raise ConfigError("probe_time_slice debe ser positivo")
        if self.stop_grace <= 0:
            raise ConfigError("stop_grace debe ser positivo")
        if self.share_interval < 0:
            raise ConfigError("share_interval debe ser >= 0")

    @property
    def num_workers(self) -> int:
        return len(self.workers)

    @property
    def mpi_size(self) -> int:
        """Rangos necesarios: orquestador + trabajadores."""
        return 1 + self.num_workers

    def to_dict(self) -> Dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in ("workers", "substitute")}
        data["workers"] = [w.to_dict() for w in self.workers]
        data["substitute"] = None if self.substitute is None else self.substitute.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParaBackConfig":
        """Inverso de :meth:`to_dict` (la configuración viaja así a los rangos MPI)."""
        fields = dict(data)
        workers = [WorkerSpec.from_dict(w) for w in fields.pop("workers")]
        substitute = fields.pop("substitute", None)
        return cls(workers=workers, substitute=None if substitute is None else WorkerSpec.from_dict(substitute), **fields)


def native_worker_status(name: str) -> Tuple[bool, str]:
    """``(disponible, motivo)`` de un trabajador nativo (``NATIVE_WORKERS``): su módulo define ``is_available``.

    El módulo se importa aquí y no arriba porque importa a su vez esta configuración.
    """
    import importlib

    module = importlib.import_module(NATIVE_WORKERS[name].split(":")[0])
    status: Tuple[bool, str] = module.is_available()
    return status


def solver_status(name: str) -> Tuple[bool, str]:
    """``(disponible, motivo)`` de un backend registrado o de un trabajador nativo."""
    try:
        if name in NATIVE_WORKERS:
            return native_worker_status(name)
        return get_backend_class(name).is_available()
    except Exception as exc:  # noqa: BLE001 - un backend roto simplemente no está disponible
        return False, f"{type(exc).__name__}: {exc}"


def available_solvers(candidates: Sequence[str] = ("gurobi", "scip", "roundingsat")) -> List[str]:
    """Solvers reales utilizables en este entorno, en el orden indicado."""
    return [name for name in candidates if solver_status(name)[0]]


#: Principios de búsqueda con los que se diversifican los trabajadores: cada
#: worker ``i`` del portfolio usa ``_DIVERSIFICATION[i % len(_DIVERSIFICATION)]``,
#: distinto solver o no (CLAUDE.md: "cada cual siguiendo distintos principios
#: de búsqueda y orden de las variables a ser probadas").
_DIVERSIFICATION: Sequence[Tuple[str, str]] = (
    ("file", "doubling"),
    ("reverse", "doubling"),
    ("random:1", "doubling"),
    ("value:1", "fixed:1"),
    ("value:0", "doubling"),
    # K = ∞ (todos los restantes tras un infactible): la mejor configuración de CadiBack.
    ("random:2", "all"),
)


#: Variantes de RoundingBack (ICAART 2026, §3.1) que reciben, por turno, los trabajadores
#: ``roundingsat`` del portfolio: ``(etiqueta, orden, chunks, parámetros nativos)``.
#: Se cruzan heurísticas y órdenes porque ninguna variante gana en todas partes
#: (docs/PLAN_PARABACK.md §7.6): RB-PG es la más rápida en haplotipos nonunif, pero no avanza
#: nada en simp-unif-100_100.00, donde el backbone está al final del archivo y sólo
#: progresa RB-Base con ``reverse`` (4 303 variables en 60 s frente a 3-425 con los demás
#: órdenes); RB-WP es la mejor en 5xp1.b. Con k = 2 quedan RB-PG + RB-Base/reverse.
#: RB-Base-1 son sondeos de una variable (``fixed:1``) sin PG, para no repetir heurística; CadiBack
#: los mide como su peor opción, pero aportan diversidad. RB-All usa ``chunks=all``.
_ROUNDINGBACK_VARIANTS: Sequence[Tuple[str, str, str, Mapping[str, str]]] = (
    ("rb-pg", "file", "doubling", {"pguide": "1"}),
    ("rb-base-rev", "reverse", "doubling", {}),
    ("rb-wp", "wp", "doubling", {}),
    ("rb-base-1", "value:0", "fixed:1", {}),
    # Quinta variante: chunks de K = ∞ (CadiBack, docs/PLAN_REFACTOR.md §1.8); con k ≤ 4 no cambia nada.
    ("rb-all", "file", "all", {}),
)


def with_threads(spec: WorkerSpec, threads: Optional[int]) -> WorkerSpec:
    """``spec`` con ``Threads=threads`` si es un solver multihilo y no lo fija ya (``None``: sin cambios)."""
    if threads is None or spec.solver not in THREADED_SOLVERS or "Threads" in spec.params:
        return spec
    if threads < 1:
        raise ConfigError("Threads debe ser >= 1")
    return replace(spec, params={**spec.params, "Threads": str(threads)})


def with_memory_limit(spec: WorkerSpec, megabytes: Optional[float]) -> WorkerSpec:
    """``spec`` con el límite de memoria de su solver (:data:`MEMORY_LIMIT_PARAMS`) si no lo fija ya (``None``: sin cambios)."""
    if megabytes is None or spec.solver not in MEMORY_LIMIT_PARAMS:
        return spec
    if megabytes <= 0:
        raise ConfigError("el límite de memoria debe ser positivo (MB)")
    name, factor = MEMORY_LIMIT_PARAMS[spec.solver]
    if name in spec.params:
        return spec
    value = megabytes * factor
    text = f"{value:g}" if value != int(value) else str(int(value))
    return replace(spec, params={**spec.params, name: text})


def default_portfolio(solvers: Sequence[str], num_workers: Optional[int] = None, threads: Optional[int] = 1) -> List[WorkerSpec]:
    """Portfolio heterogéneo: recorre los solvers disponibles y diversifica el orden.

    Cada trabajador ``i`` recibe ``solvers[i % len(solvers)]`` y el principio de
    búsqueda ``_DIVERSIFICATION[i % len(_DIVERSIFICATION)]`` (los de RoundingBack,
    en cambio, las variantes de ``_ROUNDINGBACK_VARIANTS`` por turno), de modo que ya con
    un trabajador por solver (``num_workers == len(solvers)``) cada uno sondea
    las candidatas en un orden distinto; si ``num_workers`` excede
    ``len(_DIVERSIFICATION)`` los principios se repiten en ciclo. Los trabajadores
    nativos (``NATIVE_WORKERS``) siguen su propio algoritmo y reciben la
    especificación por defecto. ``threads`` es el ``Threads`` de los solvers multihilo
    (``None``: no se fija, lo decide quien use el portfolio, p. ej. ``--threads-per-worker``).
    """
    solvers = list(solvers)
    if not solvers:
        raise ConfigError("no hay ningún solver disponible para el portfolio")
    if num_workers is None:
        num_workers = len(solvers)
    specs: List[WorkerSpec] = []
    seen: Dict[str, int] = {}
    for i in range(num_workers):
        solver = solvers[i % len(solvers)]
        k = seen.get(solver, 0)
        seen[solver] = k + 1
        if solver == "roundingsat":
            # El k-ésimo RoundingBack recibe la variante k; al repetirse las variantes, con
            # el orden de otra entrada de _DIVERSIFICATION para que no haya dos iguales.
            label, order, chunks, native = _ROUNDINGBACK_VARIANTS[k % len(_ROUNDINGBACK_VARIANTS)]
            cycle = k // len(_ROUNDINGBACK_VARIANTS)
            if cycle and order != "wp":
                order = f"random:{cycle}"  # al repetirse, otra permutación de las candidatas
            label = label + (f"-{cycle}" if cycle else "")
            specs.append(WorkerSpec(solver=solver, order=order, chunks=chunks, params=dict(native), label=f"w{i + 1}-{label}"))
            continue
        if solver in NATIVE_WORKERS:
            # Su propio algoritmo (p. ej. Cadiback): no admite order= ni chunks=.
            specs.append(WorkerSpec(solver=solver, label=f"w{i + 1}-{solver}"))
            continue
        order, chunks = _DIVERSIFICATION[i % len(_DIVERSIFICATION)]
        spec = WorkerSpec(solver=solver, order=order, chunks=chunks, label=f"w{i + 1}-{solver}")
        specs.append(with_threads(spec, threads))
    return specs


__all__ = [
    "CONFIG_ERRORS",
    "ConfigError",
    "NATIVE_WORKERS",
    "ORCHESTRATOR_RANK",
    "MEMORY_LIMIT_PARAMS",
    "THREADED_SOLVERS",
    "ParaBackConfig",
    "WorkerSpec",
    "available_solvers",
    "default_portfolio",
    "make_chunks",
    "make_order",
    "native_worker_status",
    "solver_status",
    "with_memory_limit",
    "with_threads",
]

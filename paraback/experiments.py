"""Archivo de experimentos de ParaBack: portfolios con nombre en TOML.

Un archivo define varios experimentos (qué trabajadores, con qué opciones) para
compararlos sobre las mismas instancias. Lo usan

* la CLI: ``python -m paraback --config FILE --experiment NOMBRE inst.opb out``;
* ``tools/cluster/submit_experiments.py``: un array de Slurm por experimento.

Formato (``tools/cluster/experiments.toml`` es un ejemplo completo)::

    [defaults]                      # para todos los experimentos (cada uno puede redefinirlos)
    time_limit = 600                # --time-limit de ParaBack (s)
    repeats = 1                     # ejecuciones de cada instancia (los portfolios no son deterministas)
    options = ["--poll-interval", "0.05"]   # cualquier otra opción de la CLI

    [cluster]                       # sólo submit_experiments.py
    partition = "solver"
    account = "solver"              # --account de Slurm
    cores_per_node = 96
    mem_per_node = "384G"
    cores_per_worker = 4            # núcleos reservados (y fijados) por trabajador y por el orquestador
    mem_per_worker = "15G"          # memoria por trabajador; sin ella, la parte proporcional del nodo
    orchestrator_slot = true        # false: el orquestador comparte las CPUs de los trabajadores
    max_array_size = 0              # tareas por array (0 = un array por experimento)
    home_wait = 0                   # minutos que una tarea espera al home (micro-cortes del NFS); 0 = no espera
    gurobi_license = "wls"          # "wls": licencia compartida, gurobi_sessions limita; "node": una por
                                    # nodo; "server": servidor de tokens con tokens de sobra
    gurobi_sessions = 2             # sesiones WLS simultáneas entre TODOS los trabajos (sólo con "wls")

    [[experiment]]
    name = "p3"
    workers = ["gurobi:order=file", "scip:order=reverse", "roundingsat:order=file,pguide=1"]

    [[experiment]]
    name = "rb-strided-8"
    workers = [{ spec = "roundingsat:order=strided:{i}/8", count = 8 }]   # {i} = 0..count-1

    [[experiment]]
    name = "mix-48"
    workers = ["gurobi", { solvers = ["scip", "roundingsat"], count = 47 }]   # default_portfolio
    options = ["--no-live-updates"]

    [[experiment]]
    name = "seq-gurobi"
    workers = ["gurobi"]
    max_bits = 32                   # sólo instancias con enteros de hasta 32 bits (también min_bits)

Cada entrada de ``workers`` es una especificación ``--worker`` (``solver[:clave=valor,...]``),
``{ spec, count }`` (``count`` copias; ``{i}`` en ``spec`` se sustituye por 0..count-1) o
``{ solvers, count }`` (``count`` trabajadores diversificados con
:func:`paraback.config.default_portfolio`). ``options`` no puede elegir trabajadores
(``--worker``, ``--portfolio``, ``--solvers``) ni ``--time-limit``: eso es el propio experimento.

``min_bits`` y ``max_bits`` (sólo en un experimento) limitan sus instancias por el tamaño de su
mayor entero (``intsize=`` de la cabecera, :func:`guroback.opb.max_int_bits`):
``submit_experiments.py`` envía el experimento sólo con las que caen en el intervalo y anota las
demás en ``omitted.txt`` (``collect.sh`` las marca ``n/c``). Sirven para omitir los pares
(instancia, extractor) incompatibles de una ronda de referencias secuenciales
(``docs/PLAN_EVALUACION.md`` P5); la CLI no los aplica.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import ConfigError, WorkerSpec, default_portfolio
from .options import build_parser

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python < 3.11
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None  # type: ignore[assignment]

_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
#: Opciones de la CLI que fija el propio experimento (no pueden ir en ``options``).
_RESERVED_OPTIONS = ("--worker", "--portfolio", "--solvers", "--time-limit", "--config", "--experiment")
_DEFAULT_KEYS = {"time_limit", "options", "repeats"}
_EXPERIMENT_KEYS = {"name", "description", "workers", "cluster", "min_bits", "max_bits"} | _DEFAULT_KEYS
#: Claves de ``[cluster]`` (y de ``cluster`` dentro de un experimento) y sus valores por defecto.
CLUSTER_DEFAULTS: Mapping[str, Any] = {
    "partition": "",
    "account": "",  # --account de Slurm (en el cluster de pruebas, la partición solver lo exige)
    "cores_per_node": 96,
    "mem_per_node": "384G",
    # Recursos de cada tarea MPI (cada trabajador y el orquestador): Slurm reserva
    # cores_per_worker CPUs por tarea y run.sbatch fija cada proceso a las suyas.
    # Por defecto 4 núcleos y 15 GB: menos degrada a cada trabajador (caché, contención) y no
    # basta para muchas fórmulas. Un archivo que quiera menos debe decirlo explícitamente.
    "cores_per_worker": 4,
    "mem_per_worker": "15G",  # vacío: mem_per_cpu × cores_per_worker, o la parte proporcional del nodo
    "mem_per_cpu": "",  # alternativa a mem_per_worker (no ambas)
    "max_concurrent": 0,  # 0 = sin límite (salvo el de las sesiones de Gurobi)
    # Nodos que el experimento no usa nunca (--exclude de Slurm, p. ej. "mirador-node15"): en el cluster
    # de Chile la partición del proyecto tiene más prioridad que la de los concursos y uno queda para ellos.
    "exclude": "",
    "exclusive": False,
    "launcher": "mpirun",
    # El orquestador en una tarea MPI propia de cores_per_worker núcleos y mem_per_worker. Con
    # false, el trabajo sólo reserva los huecos de los trabajadores y el orquestador comparte sus
    # CPUs: está casi siempre ocioso (0,6 % del tiempo en ev3-mf), y en nodos pequeños (8 CPU en el
    # cluster de Chile) un hueco propio dejaría fuera a un trabajador. Entonces run.sbatch no fija
    # procesos a CPUs (mpirun --oversubscribe --bind-to none); sólo con launcher = "mpirun".
    "orchestrator_slot": True,
    # Tareas por array de Slurm (0 = sin partir). Slurm rechaza índices desde MaxArraySize (1001
    # por defecto): con más tareas, submit_experiments.py envía varios arrays consecutivos.
    "max_array_size": 0,
    # Licencia de Gurobi. "wls": una licencia WLS compartida por todos los trabajos, con
    # gurobi_sessions sesiones simultáneas (submit_experiments.py encadena y limita).
    # "node": una licencia de máquina por nodo en gurobi_license_dir/<nodo>/gurobi.lic
    # (tools/cluster/gurobi_node_license.sbatch), sin límite de sesiones; los experimentos con
    # Gurobi sólo van a los nodos que la tienen.
    # "server": un servidor de tokens (~/gurobi.lic con TOKENSERVER=) con tokens de sobra para
    # todos los nodos: ni encadenado ni límite de tareas (si se agotan, Gurobi falla al arrancar).
    "gurobi_license": "wls",
    "gurobi_sessions": 2,
    "gurobi_license_dir": "~/gurobi-licenses",
    "extra_minutes": 10,  # margen de --time de Slurm sobre time_limit
    # Minutos que una tarea espera al home si no responde (micro-cortes del NFS en el cluster de Chile,
    # 2026-11-14 y 2026-11-17: sin el home, Slurm no crea el log, la tarea falla en 0 s y el nodo se traga la
    # cola). Con > 0, el log va al disco del nodo y run.sbatch repite cada acceso al home hasta home_wait
    # minutos, se reencola si al empezar no vuelve y suma 2 × home_wait a --time. 0: como antes.
    "home_wait": 0,
    # Trazas de cada ejecución (--trace-dir: tools/sharing_report.py). Sólo sirven para medir la
    # compartición y en una ronda de miles de ejecuciones ocupan mucho en el home.
    "traces": True,
    "env": "~/paraback-env",
}


@dataclass(frozen=True)
class Experiment:
    name: str
    workers: Tuple[WorkerSpec, ...]
    time_limit: Optional[float] = None
    options: Tuple[str, ...] = ()
    description: str = ""
    cluster: Mapping[str, Any] = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()
    #: Ejecuciones de cada instancia: un portfolio no es determinista (carreras entre
    #: trabajadores) y sus tiempos se comparan con varias (CLAUDE.md §2).
    repeats: int = 1
    #: Sólo instancias cuyo mayor entero tiene entre ``min_bits`` y ``max_bits`` bits (``None``: sin cota).
    min_bits: Optional[int] = None
    max_bits: Optional[int] = None

    def admits_bits(self, bits: int) -> bool:
        """¿Entra en el experimento una instancia cuyo mayor entero tiene ``bits`` bits?"""
        return (self.min_bits is None or bits >= self.min_bits) and (self.max_bits is None or bits <= self.max_bits)

    @property
    def filters_bits(self) -> bool:
        return self.min_bits is not None or self.max_bits is not None

    @property
    def bits_label(self) -> str:
        """El intervalo de bits para los mensajes: ``≤ 32``, ``33–66``, ``≥ 67``; ``—`` sin filtro."""
        if self.min_bits is not None and self.max_bits is not None:
            return f"{self.min_bits}–{self.max_bits}"
        if self.max_bits is not None:
            return f"≤ {self.max_bits}"
        return "—" if self.min_bits is None else f"≥ {self.min_bits}"

    @property
    def num_workers(self) -> int:
        return len(self.workers)

    @property
    def gurobi_workers(self) -> int:
        # lp: la relajación con Gurobi, salvo con el motor SoPlex.
        return sum(1 for w in self.workers if w.solver == "gurobi" or (w.solver == "lp" and w.params.get("engine") != "soplex"))

    def worker_args(self) -> List[str]:
        out: List[str] = []
        for spec in self.workers:
            out += ["--worker", spec.describe()]
        return out

    def paraback_args(self) -> List[str]:
        """Argumentos de ``python -m paraback`` que reproducen el experimento (sin instancia ni salida)."""
        out = self.worker_args()
        if self.time_limit is not None:
            out += ["--time-limit", repr(float(self.time_limit))]
        return out + list(self.options)

    @property
    def license_limited(self) -> bool:
        """Usa Gurobi con la licencia WLS compartida: cuenta para ``gurobi_sessions``."""
        return self.gurobi_workers > 0 and self.cluster["gurobi_license"] == "wls"

    @property
    def num_tasks(self) -> int:
        """Tareas de Slurm: los trabajadores y, con ``orchestrator_slot``, el orquestador."""
        return self.num_workers + (1 if self.cluster["orchestrator_slot"] else 0)

    @property
    def cores_per_task(self) -> int:
        return int(self.cluster["cores_per_worker"])

    def mem_per_task_mb(self) -> int:
        """Memoria de cada tarea (MB): ``mem_per_worker``, ``mem_per_cpu × cores_per_worker`` o,
        sin ninguna, el 95 % de la parte del nodo que corresponde a sus núcleos."""
        if self.cluster.get("mem_per_worker"):
            return _megabytes(str(self.cluster["mem_per_worker"]))
        if self.cluster.get("mem_per_cpu"):
            return _megabytes(str(self.cluster["mem_per_cpu"])) * self.cores_per_task
        node = _megabytes(str(self.cluster["mem_per_node"]))
        return int(0.95 * node * self.cores_per_task / int(self.cluster["cores_per_node"]))

    def mem_per_cpu_mb(self) -> int:
        """``--mem-per-cpu`` de Slurm (MB): la memoria de la tarea repartida entre sus núcleos."""
        return -(-self.mem_per_task_mb() // self.cores_per_task)


def _megabytes(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?)B?\s*", text.upper())
    if not m:
        raise ConfigError(f"memoria no válida: {text!r} (p. ej. '384G', '3800M')")
    factor = {"K": 1 / 1024, "": 1, "M": 1, "G": 1024, "T": 1024 * 1024}[m.group(2)]
    return int(float(m.group(1)) * factor)


def _expand_workers(name: str, entries: Any) -> Tuple[List[WorkerSpec], List[str]]:
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"experimento {name!r}: 'workers' debe ser una lista no vacía")
    specs: List[WorkerSpec] = []
    warnings: List[str] = []
    for entry in entries:
        if isinstance(entry, str):
            specs.append(WorkerSpec.parse(entry))
            continue
        if not isinstance(entry, dict):
            raise ConfigError(f"experimento {name!r}: trabajador no válido {entry!r}")
        unknown = set(entry) - {"spec", "solvers", "count"}
        if unknown or ("spec" in entry) == ("solvers" in entry):
            raise ConfigError(f"experimento {name!r}: cada tabla de 'workers' lleva 'spec' o 'solvers' (y 'count'): {entry!r}")
        count = entry.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ConfigError(f"experimento {name!r}: 'count' debe ser un entero >= 1: {entry!r}")
        if "solvers" in entry:
            solvers = entry["solvers"]
            if not isinstance(solvers, list) or not all(isinstance(s, str) for s in solvers):
                raise ConfigError(f"experimento {name!r}: 'solvers' debe ser una lista de nombres: {entry!r}")
            # Sin Threads: lo fija --threads-per-worker al lanzar, como en el portfolio de la CLI.
            specs += default_portfolio(solvers, count, threads=None)
            continue
        template = str(entry["spec"])
        if count > 1 and "{i}" not in template:
            warnings.append(f"{count} copias idénticas de {template!r}: un solver determinista repite el mismo trabajo")
        specs += [WorkerSpec.parse(template.replace("{i}", str(i))) for i in range(count)]
    return specs, warnings


def _check_options(name: str, options: Any) -> Tuple[str, ...]:
    if not isinstance(options, list) or not all(isinstance(o, (str, int, float)) for o in options):
        raise ConfigError(f"experimento {name!r}: 'options' debe ser una lista de cadenas")
    tokens = tuple(str(o) for o in options)
    for token in tokens:
        if any(c.isspace() for c in token):
            raise ConfigError(f"experimento {name!r}: la opción {token!r} contiene espacios (un elemento por palabra)")
        if token.split("=", 1)[0] in _RESERVED_OPTIONS:
            raise ConfigError(f"experimento {name!r}: {token.split('=', 1)[0]} lo fija el experimento, no 'options'")
    parser = build_parser()
    parser.exit_on_error = False
    parser.allow_abbrev = False  # "--time" no debe colarse como --time-limit
    try:
        args, unknown = parser.parse_known_args([*tokens, "instancia", "salida"])
    except (Exception, SystemExit) as exc:  # argparse.ArgumentError, o error() en algunos casos
        raise ConfigError(f"experimento {name!r}: opciones no válidas {list(tokens)}: {exc}") from None
    if unknown:
        raise ConfigError(f"experimento {name!r}: opciones desconocidas {unknown}")
    if args.instance != "instancia" or args.backbone != "salida":
        raise ConfigError(f"experimento {name!r}: 'options' no puede llevar argumentos posicionales: {list(tokens)}")
    return tokens


def _cluster_settings(base: Mapping[str, Any], override: Any, where: str) -> Dict[str, Any]:
    if not isinstance(override, dict):
        raise ConfigError(f"{where}: 'cluster' debe ser una tabla")
    unknown = set(override) - set(CLUSTER_DEFAULTS)
    if unknown:
        raise ConfigError(f"{where}: claves de cluster desconocidas {sorted(unknown)} (válidas: {sorted(CLUSTER_DEFAULTS)})")
    merged = dict(base)
    if "mem_per_cpu" in override and "mem_per_worker" not in override:
        merged["mem_per_worker"] = ""  # mem_per_cpu explícita sustituye a la mem_per_worker heredada
    merged.update(override)
    return merged


def _check_cluster(exp: Experiment) -> None:
    cluster = exp.cluster
    cores = int(cluster["cores_per_node"])
    per_task = cluster["cores_per_worker"]
    if not isinstance(per_task, int) or isinstance(per_task, bool) or per_task < 1:
        raise ConfigError(f"experimento {exp.name!r}: cores_per_worker debe ser un entero >= 1")
    if cluster.get("mem_per_worker") and cluster.get("mem_per_cpu"):
        raise ConfigError(f"experimento {exp.name!r}: indique mem_per_worker o mem_per_cpu, no ambas")
    if not isinstance(cluster["orchestrator_slot"], bool):
        raise ConfigError(f"experimento {exp.name!r}: orchestrator_slot debe ser true o false")
    if not cluster["orchestrator_slot"] and cluster["launcher"] != "mpirun":
        raise ConfigError(f"experimento {exp.name!r}: orchestrator_slot = false sólo con launcher = \"mpirun\"")
    array = cluster["max_array_size"]
    if not isinstance(array, int) or isinstance(array, bool) or array < 0:
        raise ConfigError(f"experimento {exp.name!r}: max_array_size debe ser un entero >= 0 (0 = sin partir)")
    wait = cluster["home_wait"]
    if not isinstance(wait, int) or isinstance(wait, bool) or wait < 0:
        raise ConfigError(f"experimento {exp.name!r}: home_wait debe ser un entero >= 0 (minutos; 0 = sin esperar)")
    need = exp.num_tasks * per_task
    if need > cores:
        slot = cluster["orchestrator_slot"]
        raise ConfigError(
            f"experimento {exp.name!r}: {exp.num_workers} trabajadores{' + orquestador' if slot else ''} = "
            f"{exp.num_tasks} tareas × {per_task} núcleos = {need} no caben en un nodo de {cores} núcleos "
            f"(como mucho {cores // per_task - (1 if slot else 0)} trabajadores)"
        )
    node_mb = _megabytes(str(cluster["mem_per_node"]))
    need_mb = exp.num_tasks * exp.mem_per_task_mb()
    if need_mb > node_mb:
        raise ConfigError(
            f"experimento {exp.name!r}: {exp.num_tasks} tareas × {exp.mem_per_task_mb() / 1024:.1f} GB = "
            f"{need_mb / 1024:.0f} GB no caben en un nodo de {node_mb / 1024:.0f} GB"
        )
    if cluster["gurobi_license"] not in ("wls", "node", "server"):
        raise ConfigError(f"experimento {exp.name!r}: gurobi_license debe ser 'wls', 'node' o 'server'")
    sessions = int(cluster["gurobi_sessions"])
    if exp.license_limited and exp.gurobi_workers > sessions:
        raise ConfigError(
            f"experimento {exp.name!r}: {exp.gurobi_workers} trabajadores Gurobi y la licencia admite {sessions} sesiones simultáneas"
        )
    if not isinstance(cluster["exclude"], str):
        raise ConfigError(f"experimento {exp.name!r}: exclude debe ser una lista de nodos de Slurm (texto)")
    if not isinstance(cluster["traces"], bool):
        raise ConfigError(f"experimento {exp.name!r}: traces debe ser true o false")
    if cluster["launcher"] not in ("mpirun", "srun"):
        raise ConfigError(f"experimento {exp.name!r}: launcher debe ser 'mpirun' o 'srun'")


def parse_experiments(data: Mapping[str, Any], source: str = "<toml>") -> List[Experiment]:
    unknown = set(data) - {"defaults", "cluster", "experiment"}
    if unknown:
        raise ConfigError(f"{source}: secciones desconocidas {sorted(unknown)} (válidas: defaults, cluster, experiment)")
    defaults = data.get("defaults", {})
    if set(defaults) - _DEFAULT_KEYS:
        raise ConfigError(f"{source}: claves desconocidas en [defaults]: {sorted(set(defaults) - _DEFAULT_KEYS)}")
    cluster = _cluster_settings(CLUSTER_DEFAULTS, data.get("cluster", {}), f"{source} [cluster]")
    entries = data.get("experiment", [])
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{source}: no hay ningún [[experiment]]")
    experiments: List[Experiment] = []
    seen = set()
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise ConfigError(f"{source}: experimento sin 'name' válido (letras, dígitos, '_', '.', '-'): {name!r}")
        if name in seen:
            raise ConfigError(f"{source}: experimento repetido {name!r}")
        seen.add(name)
        unknown = set(entry) - _EXPERIMENT_KEYS
        if unknown:
            raise ConfigError(f"experimento {name!r}: claves desconocidas {sorted(unknown)} (válidas: {sorted(_EXPERIMENT_KEYS)})")
        workers, warnings = _expand_workers(name, entry.get("workers"))
        time_limit = entry.get("time_limit", defaults.get("time_limit"))
        if time_limit is not None and (not isinstance(time_limit, (int, float)) or isinstance(time_limit, bool) or time_limit <= 0):
            raise ConfigError(f"experimento {name!r}: 'time_limit' debe ser un número positivo")
        options = _check_options(name, entry.get("options", defaults.get("options", [])))
        repeats = entry.get("repeats", defaults.get("repeats", 1))
        if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
            raise ConfigError(f"experimento {name!r}: 'repeats' debe ser un entero >= 1")
        if repeats > 1 and len(workers) == 1:
            warnings.append(f"repeats = {repeats} con un único trabajador: un solver determinista repite el mismo resultado")
        bits = {}
        for key in ("min_bits", "max_bits"):
            value = entry.get(key)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
                raise ConfigError(f"experimento {name!r}: '{key}' debe ser un entero >= 1")
            bits[key] = value
        if bits["min_bits"] is not None and bits["max_bits"] is not None and bits["min_bits"] > bits["max_bits"]:
            raise ConfigError(f"experimento {name!r}: min_bits = {bits['min_bits']} > max_bits = {bits['max_bits']}")
        exp = Experiment(
            name=name,
            workers=tuple(workers),
            time_limit=None if time_limit is None else float(time_limit),
            options=options,
            description=str(entry.get("description", "")),
            cluster=_cluster_settings(cluster, entry.get("cluster", {}), f"experimento {name!r}"),
            warnings=tuple(warnings),
            repeats=repeats,
            min_bits=bits["min_bits"],
            max_bits=bits["max_bits"],
        )
        _check_cluster(exp)
        experiments.append(exp)
    return experiments


def load_experiments(path: str) -> List[Experiment]:
    if tomllib is None:  # pragma: no cover - Python < 3.11 sin tomli
        raise ConfigError("leer archivos de experimentos necesita Python >= 3.11 o el paquete 'tomli'")
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"no se puede leer {path}: {exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML no válido: {exc}") from None
    return parse_experiments(data, path)


def find_experiment(experiments: Sequence[Experiment], name: str) -> Experiment:
    for exp in experiments:
        if exp.name == name:
            return exp
    raise ConfigError(f"no hay ningún experimento {name!r} (hay: {', '.join(e.name for e in experiments)})")


__all__ = ["CLUSTER_DEFAULTS", "Experiment", "find_experiment", "load_experiments", "parse_experiments"]

"""Informe de una ronda de la evaluación del artículo (``docs/PLAN_EVALUACION.md`` §7).

Lee lo que deja ``tools/cluster/collect.sh`` en una ronda (``~/runs/evN`` o su tarball
descomprimido): ``compare.tsv`` y, por experimento, ``<exp>/args.txt`` y
``<exp>/<instancia>/trace/stats.json``. Con la tabla maestra de instancias
(``results/base25/instancias-pbo25.tsv``: familia y bits) escribe en Markdown:

* **Cobertura** de cada experimento: completas, interrumpidas, fallidas, n/c, PAR-2 (2 × límite a
  las no completadas, sobre sus instancias compatibles) y completas por tramo de bits. Es n/c lo que
  queda fuera de su cobertura (P4) y, sin sustituto, la ejecución en que todos los trabajadores se
  retiraron porque no admiten la instancia (P5: Naps no la traduce, ``Incompatible``).
* **VBS** de grupos de experimentos (``--vbs``; siempre el de todos menos ``--outside``), sobre
  todas las instancias: una n/c cuenta como no completada. Los experimentos de las rondas de
  ``--ref`` (p. ej. los secuenciales de ev1) sólo entran en los VBS y en los pares.
* **Únicas**: instancias que sólo completa un experimento, y el VBS sin él.
* **Pares** (``--pair A:B``) sobre las compatibles con los dos: media geométrica del cociente de
  tiempos (mínimo de 1 s), también por tramo de dificultad (las triviales pesan mucho en la media),
  victorias con umbral de empate (< 10 % o < 1 s) y qué completa sólo uno. A o B pueden ser un VBS
  de ``--vbs``.
* **Significación** de cada par: Wilcoxon de rangos con signo, bilateral, sobre el PAR-2 de las
  instancias compatibles con los dos sin las que no completa ninguno, con la correlación biserial de
  rangos como tamaño del efecto (positiva si el primero es más rápido); McNemar exacto sobre las que
  completa sólo uno. Los valores p se ajustan por Holm dentro de cada grupo (``--pair A:B@GRUPO``, p.
  ej. una pregunta de investigación; sin grupo, todos los pares juntos), con las dos pruebas de cada
  par en la misma familia.
* **Speedup y eficiencia frente al SBS** (``--sbs``): PAR-2 sobre todas las instancias (n/c como no
  completada), speedup = PAR-2(SBS) / PAR-2 y eficiencia = speedup por trabajador, en proporción a los
  del SBS; un VBS suma los de sus miembros. El orquestador no cuenta: no necesita su hueco de 4 núcleos y
  15 GB, y fuera de la evaluación iría junto a un trabajador con 1–2 GB más (2026-10-28). Como nadie completa
  algunas instancias, el PAR-2 tiene un suelo y el speedup un techo, que se dan; también el speedup
  sin ellas y sobre las que completa el SBS. El SBS puede ser un secuencial con el protocolo por bits
  (``--by-bits``: un experimento para ≤ 32 bits, otro para 33–66 y, si hay, otro para > 66), como lo
  ejecutan los portfolios.
* **Por familia**: completas de cada experimento y del VBS.
* **Paridad y hechos**: backbones completos distintos, backbones parciales (de ejecuciones sin
  terminar) que no están en el backbone completo de la instancia, y soluciones que la comprobación
  exacta rechazó (``invalid_solutions``).
* **Sustituciones en el sitio** (``replaced`` en ``--stats-json``): cuántas, por qué, cuánto tiempo
  perdió el hueco y cuántas de esas ejecuciones completaron.
* **Fallos y retiradas** por clase de error, **progreso** en las que nadie completa (fracción de
  candidatas decididas) y **coste** (horas·tarea de todas las ejecuciones, n/c incluidas,
  fracción que agota el límite, horas·nodo con 24 huecos por nodo y un hueco por trabajador más el
  del orquestador).

El tiempo de una ejecución completa es el **reloj del orquestador** (``wall_time`` de
``--stats-json``: desde que arranca hasta que todos los trabajadores han terminado), no el del
trabajo de Slurm (``wall`` de ``run.txt`` y de ``compare.tsv``), que suma el lanzador, el arranque de
MPI y el cierre de los procesos. En node906, las ejecuciones con Gurobi o SCIP pierden ahí 5–9 s
que en node907 no aparecen, y eso decidía las comparaciones de las instancias fáciles (ev1–ev3,
2026-10-27). ``--clock trabajo`` vuelve al del trabajo; una ejecución completa sin ``wall_time`` usa
el del trabajo y el informe lo cuenta. El coste sigue midiéndose con el del trabajo, que es lo que
ocupa el nodo. ``wall_time`` incluye la espera a los trabajadores que tras STOP no terminan (hasta
``stop_grace``, 60 s); desde el 2026-10-28 ``--stats-json`` guarda además ``complete_time``, el instante
en que el backbone quedó completo. ``--clock completo`` mide con él (y con ``wall_time`` donde no
está), y con el reloj del orquestador el informe cuenta cuántas completas esperaron 10 s o más.

Con ``--times`` escribe un TSV con el estado y el tiempo de cada experimento de la ronda por instancia
(el reloj de ``--clock``; vacío si no completó; n/c como en el informe) y ``complete_time`` donde está:
lo que leen las figuras del artículo (``tools/paper/figures.py``), sin los ``stats.json``.

Con ``--naps`` escribe además un TSV con las traducciones de Naps (``translation_time``,
``cnf_vars``, ``cnf_clauses``) de los experimentos con NapBack, para calibrar la regla de
compatibilidad de Naps (PLAN_EVALUACION D11).

Uso::

    python3 tools/ev_report.py RONDA [RONDA ...] [--ref RONDA ...] [--table results/base25/instancias-pbo25.tsv]
        [--outside exp,exp] [--vbs NOMBRE=exp,exp ...] [--pair A:B[@GRUPO] ...] [--by-bits NOMBRE=exp,exp[,exp] ...]
        [--sbs NOMBRE] [--clock orquestador|completo|trabajo] [-o informe.md] [--times tiempos.tsv] [--naps naps.tsv]
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import pathlib
import re
import statistics
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLE = ROOT / "results" / "base25" / "instancias-pbo25.tsv"
#: Límite de tiempo de la evaluación (P10) y huecos de un nodo (P2).
TIME_LIMIT = 3600.0
NODE_SLOTS = 24
#: Umbral de empate de las comparaciones por instancia (§7).
TIE_REL, TIE_ABS = 0.10, 1.0
INF = math.inf
#: Tramos de dificultad de los pares: el mayor de los dos tiempos (s).
SPANS = ((0.0, 10.0, "< 10 s"), (10.0, 100.0, "10–100 s"), (100.0, 1000.0, "100–1000 s"), (1000.0, INF, "≥ 1000 s"))
#: Errores de un trabajador que no admite la instancia (``guroback.backend.Incompatible`` y sus subclases).
INCOMPATIBLE = ("Incompatible:", "NapsCannotTranslate:")
#: Nivel de las pruebas de significación (tras Holm) y tamaño máximo para el Wilcoxon exacto (como SciPy).
ALPHA = 0.05
WILCOXON_EXACT_MAX = 50


def instance_name(file: str) -> str:
    """El nombre de ``run.sbatch`` y ``compare.tsv``: el archivo sin ``.xz``/``.gz``/``.opb``."""
    for suffix in (".xz", ".gz", ".opb"):
        if file.endswith(suffix):
            file = file[: -len(suffix)]
    return file


@dataclass
class Experiment:
    name: str
    round_dir: pathlib.Path
    status: Dict[str, str] = field(default_factory=dict)   # instancia → complete / interrupted / failed / died / n/c
    wall: Dict[str, float] = field(default_factory=dict)    # el del trabajo (``compare.tsv``): el coste
    #: Reloj del orquestador de las completas (``wall_time``); las que no lo tienen usan ``wall``.
    clock: Dict[str, float] = field(default_factory=dict)
    #: ``complete_time`` de las completas que lo tienen (``--stats-json`` desde el 2026-10-28).
    complete: Dict[str, float] = field(default_factory=dict)
    workers: Optional[int] = None                           # trabajadores (``--worker`` de args.txt)
    slots: Optional[int] = None                             # huecos, para el coste: los trabajadores y el orquestador
    #: Instancias en que no admitió la instancia ningún trabajador (n/c, aunque la ejecución falló).
    incompatible: Dict[str, str] = field(default_factory=dict)
    virtual: bool = False                                   # un VBS: sin stats.json
    _stats: Dict[str, Optional[Dict[str, Any]]] = field(default_factory=dict)

    def compatible(self, inst: str) -> bool:
        return self.status.get(inst, "n/c") != "n/c"

    def time(self, inst: str) -> float:
        """Tiempo hasta completar; ``INF`` si no completó o no es compatible."""
        if self.status.get(inst) != "complete":
            return INF
        return self.clock.get(inst, self.wall[inst])

    def stats(self, inst: str) -> Optional[Dict[str, Any]]:
        if self.virtual:
            return None
        if inst not in self._stats:
            self._stats[inst] = read_stats(self.round_dir / self.name / inst / "trace" / "stats.json")
        return self._stats[inst]


def read_stats(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        stats = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return stats if isinstance(stats, dict) else None


def read_round(round_dir: pathlib.Path, clock: str = "orquestador") -> Tuple[List[str], Dict[str, Experiment]]:
    """``clock``: ``orquestador`` (``wall_time``), ``completo`` (``complete_time``, o ``wall_time`` donde no
    está) o ``trabajo`` (el de ``compare.tsv``)."""
    with open(round_dir / "compare.tsv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    names = [c.split(":")[0] for c in (rows[0].keys() if rows else []) if c.endswith(":status")]
    exps = {n: Experiment(n, round_dir) for n in names}
    for e in exps.values():
        for r in rows:
            e.status[r["instance"]] = r[f"{e.name}:status"]
            try:
                e.wall[r["instance"]] = float(r[f"{e.name}:wall"])
            except ValueError:
                e.wall[r["instance"]] = INF
        args = round_dir / e.name / "args.txt"
        if args.exists():
            e.workers = args.read_text(encoding="utf-8").split().count("--worker")
            e.slots = e.workers + 1
        for inst, status in e.status.items():
            if status == "failed" and incompatible(e.stats(inst)):
                e.incompatible[inst] = error_class(e.stats(inst), status) or ""
                e.status[inst] = "n/c"
            elif status == "complete" and clock != "trabajo":
                # Sin pasar por la caché de ``stats``: las rondas de referencia no la necesitan y
                # todos los stats.json de una ronda ocupan cientos de MB.
                stats = read_stats(round_dir / e.name / inst / "trace" / "stats.json")
                wall_time = (stats or {}).get("wall_time")
                if isinstance(wall_time, (int, float)):
                    e.clock[inst] = float(wall_time)
                complete_time = (stats or {}).get("complete_time")
                if isinstance(complete_time, (int, float)):
                    e.complete[inst] = float(complete_time)
                    if clock == "completo":
                        e.clock[inst] = float(complete_time)
    return [r["instance"] for r in rows], exps


def incompatible(stats: Optional[Dict[str, Any]]) -> bool:
    """Todos los trabajadores se retiraron porque no admiten la instancia (sin sustituto: P5)."""
    workers = (stats or {}).get("workers") or {}
    return bool(workers) and all(str(w.get("error") or "").startswith(INCOMPATIBLE) for w in workers.values())


def vbs_experiment(name: str, members: Sequence[Experiment], instances: Sequence[str]) -> Experiment:
    """El VBS de ``members`` como un experimento más, para los pares: n/c si no es compatible ninguno."""
    e = Experiment(name, pathlib.Path(), virtual=True)
    for i in instances:
        e.wall[i] = vbs_time(members, i)
        e.status[i] = ("complete" if e.wall[i] < INF else
                       "interrupted" if any(m.compatible(i) for m in members) else "n/c")
    # Un VBS ejecuta a todos sus miembros: suma sus trabajadores.
    if all(m.workers is not None for m in members):
        e.workers = sum(m.workers or 0 for m in members)
    return e


def by_bits_experiment(name: str, members: Sequence[Optional[Experiment]], instances: Sequence[str],
                       bits: Dict[str, int]) -> Experiment:
    """Un secuencial con el protocolo por bits: en cada instancia, el miembro de su tramo (≤ 32, 33–66, > 66).

    Así ejecuta un portfolio el hueco de Gurobi (``--float-max-bits 32 --float-fallback scip-exact``): la
    referencia justa para él es GuroBack-mf con ScipBack-exacto-mf en 33–66 bits, no GuroBack-mf solo con
    sus n/c. Sin miembro para un tramo, sus instancias son n/c.
    """
    e = Experiment(name, pathlib.Path(), virtual=True)
    ranges = ["≤ 32", "33–66", "> 66"]
    for i in instances:
        index = ranges.index(bits_range(bits[i]))
        m = members[index] if index < len(members) else None
        if m is None or not m.compatible(i):
            e.status[i], e.wall[i] = "n/c", INF
        else:
            e.wall[i] = m.time(i)
            e.status[i] = "complete" if e.wall[i] < INF else m.status[i]
    present = [m for m in members if m is not None]
    if all(m.workers is not None for m in present):
        e.workers = max(m.workers or 0 for m in present)
    return e


def read_table(path: pathlib.Path) -> Dict[str, Dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        return {instance_name(r["file"]): r for r in csv.DictReader(f, delimiter="\t")}


def par2(times: Iterable[float]) -> float:
    """PAR-2 en horas: la suma de tiempos con 2 × límite a las no completadas."""
    return sum(t if t < INF else 2 * TIME_LIMIT for t in times) / 3600


def vbs_time(exps: Sequence[Experiment], inst: str) -> float:
    return min((e.time(inst) for e in exps), default=INF)


def geo_ratio(pairs: Sequence[Tuple[float, float]]) -> float:
    """Media geométrica de t(A)/t(B), con un mínimo de 1 s para no amplificar las triviales."""
    if not pairs:
        return math.nan
    return math.exp(statistics.fmean(math.log(max(a, 1.0) / max(b, 1.0)) for a, b in pairs))


def wilcoxon(diffs: Sequence[float], exact_max: int = WILCOXON_EXACT_MAX) -> Tuple[float, float, int]:
    """Wilcoxon de rangos con signo, bilateral, sobre diferencias pareadas: ``(p, r, n)``.

    Descarta las diferencias nulas (``zero_method="wilcox"`` de SciPy) y da rangos medios a los
    empates. Con n ≤ ``exact_max``, sin empates ni ceros, el valor p es exacto; si no, sale de la
    aproximación normal con corrección por empates y sin corrección de continuidad, como
    ``scipy.stats.wilcoxon``. ``r`` es la correlación biserial de rangos de pares,
    (R⁺ − R⁻) / (R⁺ + R⁻): positiva si dominan las diferencias positivas. ``n``: diferencias no nulas.
    """
    nonzero = [d for d in diffs if d != 0]
    n = len(nonzero)
    if n == 0:
        return math.nan, math.nan, 0
    order = sorted(range(n), key=lambda k: abs(nonzero[k]))
    ranks = [0.0] * n
    ties: List[int] = []
    k = 0
    while k < n:
        j = k
        while j + 1 < n and abs(nonzero[order[j + 1]]) == abs(nonzero[order[k]]):
            j += 1
        for m in range(k, j + 1):
            ranks[order[m]] = (k + j) / 2 + 1
        if j > k:
            ties.append(j - k + 1)
        k = j + 1
    total = n * (n + 1) / 2
    plus = sum(r for r, d in zip(ranks, nonzero) if d > 0)
    minus = total - plus
    if n <= exact_max and not ties and n == len(diffs):
        # Distribución exacta de R⁺: cuántos subconjuntos de {1, …, n} suman cada valor.
        counts = [1] + [0] * int(total)
        for v in range(1, n + 1):
            for s in range(int(total), v - 1, -1):
                counts[s] += counts[s - v]
        p = 2 * sum(counts[: int(min(plus, minus)) + 1]) / 2 ** n
    else:
        var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties) / 48
        p = math.erfc(abs(plus - total / 2) / math.sqrt(2 * var))
    return min(1.0, p), (plus - minus) / total, n


def mcnemar_exact(b: int, c: int) -> float:
    """McNemar exacto, bilateral: binomial(b + c, 1/2) sobre los pares discordantes."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def holm(ps: Sequence[float]) -> List[float]:
    """Valores p ajustados por Holm; los NaN (pruebas sin datos) se quedan igual y no cuentan."""
    idx = sorted((k for k, p in enumerate(ps) if not math.isnan(p)), key=lambda k: ps[k])
    out = list(ps)
    running = 0.0
    for rank, k in enumerate(idx):
        running = max(running, min(1.0, (len(idx) - rank) * ps[k]))
        out[k] = running
    return out


def num(x: float, digits: int = 1) -> str:
    """Número con coma decimal, como el resto de los informes en español."""
    return "—" if math.isnan(x) else f"{x:.{digits}f}".replace(".", ",")


def mark(p: float) -> str:
    """Valor p ajustado, en negrita si es significativo al nivel ``ALPHA``."""
    return f"**{pval(p)}**" if p < ALPHA else pval(p)


def pval(p: float) -> str:
    """Valor p: tres decimales, o notación científica por debajo de 0,001."""
    if math.isnan(p) or p >= 0.001:
        return num(p, 3)
    mantissa, exponent = f"{p:.1e}".split("e")
    return f"{mantissa.replace('.', ',')}e{int(exponent)}"


def tie(a: float, b: float) -> bool:
    return a < INF and b < INF and (abs(a - b) < TIE_ABS or abs(a - b) < TIE_REL * max(a, b))


def bits_range(bits: int) -> str:
    return "≤ 32" if bits <= 32 else "33–66" if bits <= 66 else "> 66"


def error_class(stats: Optional[Dict[str, Any]], status: str) -> Optional[str]:
    """Clase de un fallo: la primera línea del error sin el prefijo del trabajador ni detalles."""
    if status in ("complete", "interrupted", "n/c"):
        return None
    if status == "died" or stats is None:
        return "el trabajo murió sin resultado final"
    text = str(stats.get("error") or "").splitlines()[0] if stats.get("error") else ""
    if not text:
        return f"{status} sin mensaje"
    text = re.sub(r"^todos los trabajadores se retiraron: \d+: ", "", text)
    text = re.sub(r"trabajador \d+ \([^)]*\): ", "", text)
    text = re.sub(r"\s*\(.*$", "", text)       # detalles entre paréntesis (memoria, salida del solver)
    return text.strip().rstrip(":")[:100]


def table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def families(insts: Iterable[str], tab: Dict[str, Dict[str, str]]) -> str:
    c = collections.Counter(tab[i]["family"] for i in insts)
    return ", ".join(f"{f} {n}" if n > 1 else f for f, n in c.most_common()) or "—"


class Report:
    def __init__(self, instances: List[str], exps: Dict[str, Experiment], tab: Dict[str, Dict[str, str]],
                 outside: Sequence[str]):
        self.instances, self.exps, self.tab = instances, exps, tab
        self.inside = [e for n, e in exps.items() if n not in outside]

    def bits(self, inst: str) -> int:
        return int(self.tab[inst]["intsize"])

    # ------------------------------------------------------------------ secciones
    def coverage(self) -> str:
        ranges = ["≤ 32", "33–66", "> 66"]
        sizes = collections.Counter(bits_range(self.bits(i)) for i in self.instances)
        rows = []
        for e in self.exps.values():
            st = collections.Counter(e.status[i] for i in self.instances)
            compat = [i for i in self.instances if e.compatible(i)]
            by_range = collections.Counter(bits_range(self.bits(i)) for i in compat if e.time(i) < INF)
            rows.append([e.name, len(compat), st["complete"], st["interrupted"], st["failed"] + st["died"],
                         st["n/c"], num(par2(e.time(i) for i in compat))]
                        + [by_range[r] if any(bits_range(self.bits(i)) == r for i in compat) else "n/c" for r in ranges])
        header = ["experimento", "compatibles", "completas", "interrumpidas", "fallidas", "n/c", "PAR-2 (h)"]
        header += [f"{r} bits ({sizes[r]})" for r in ranges]
        return table(header, rows)

    def vbs(self, groups: Dict[str, List[Experiment]]) -> str:
        rows = []
        for name, members in groups.items():
            ts = [vbs_time(members, i) for i in self.instances]
            done = [t for t in ts if t < INF]
            rows.append([name, len(members), len(done), num(par2(ts)), num(sum(done), 0)])
        return table(["grupo", "experimentos", f"completas (de {len(self.instances)})", "PAR-2 (h)",
                      "suma de las completas (s)"], rows)

    def unique(self) -> str:
        rows = []
        for e in self.inside:
            others = [o for o in self.inside if o is not e]
            only = [i for i in self.instances if e.time(i) < INF and vbs_time(others, i) == INF]
            without = sum(vbs_time(others, i) < INF for i in self.instances)
            rows.append([e.name, len(only), without, families(only, self.tab)])
        return table(["experimento", "únicas", "VBS sin él", "familias de las únicas"], rows)

    def tests(self, a: Experiment, b: Experiment) -> Dict[str, Any]:
        """Wilcoxon sobre el PAR-2 (sin las que no completa ninguno) y McNemar exacto sobre las completas."""
        common = [i for i in self.instances if a.compatible(i) and b.compatible(i)]
        penalized = [(min(a.time(i), 2 * TIME_LIMIT), min(b.time(i), 2 * TIME_LIMIT))
                     for i in common if a.time(i) < INF or b.time(i) < INF]
        p_time, effect, n = wilcoxon([y - x for x, y in penalized])
        only_a = sum(a.time(i) < INF and b.time(i) == INF for i in common)
        only_b = sum(b.time(i) < INF and a.time(i) == INF for i in common)
        return {"n": n, "effect": effect, "p_time": p_time, "only_a": only_a, "only_b": only_b,
                "p_done": mcnemar_exact(only_a, only_b)}

    def significance(self, pairs: Sequence[Tuple[Experiment, Experiment, str]]) -> str:
        groups: Dict[str, List[Tuple[Experiment, Experiment, Dict[str, Any]]]] = collections.defaultdict(list)
        for a, b, group in pairs:
            groups[group].append((a, b, self.tests(a, b)))
        rows = []
        for group, members in groups.items():
            adjusted = holm([t[key] for _, _, t in members for key in ("p_time", "p_done")])
            for k, (a, b, t) in enumerate(members):
                p_time, p_done = adjusted[2 * k], adjusted[2 * k + 1]
                rows.append([group or "—", f"{a.name} frente a {b.name}", t["n"], num(t["effect"], 2),
                             pval(t["p_time"]), mark(p_time), f"{t['only_a']}/{t['only_b']}",
                             pval(t["p_done"]), mark(p_done)])
        return table(["grupo", "comparación", "n", "r", "p (Wilcoxon)", "Holm", "+/−", "p (McNemar)", "Holm"], rows)

    def pair(self, a: Experiment, b: Experiment) -> str:
        common = [i for i in self.instances if a.compatible(i) and b.compatible(i)]
        both = [(a.time(i), b.time(i)) for i in common if a.time(i) < INF and b.time(i) < INF]
        only_a = [i for i in common if a.time(i) < INF and b.time(i) == INF]
        only_b = [i for i in common if b.time(i) < INF and a.time(i) == INF]
        wins_a = wins_b = ties = 0
        for i in common:
            x, y = a.time(i), b.time(i)
            if x == INF and y == INF:
                continue
            if tie(x, y):
                ties += 1
            elif x < y:
                wins_a += 1
            else:
                wins_b += 1
        lines = [
            f"**{a.name} frente a {b.name}** ({len(common)} instancias compatibles con los dos): "
            f"completan los dos {len(both)}, sólo {a.name} {len(only_a)}, sólo {b.name} {len(only_b)}; "
            f"media geométrica t({a.name})/t({b.name}) = {num(geo_ratio(both), 2)} sobre las que completan los dos "
            f"(suma {num(sum(x for x, _ in both), 0)} s frente a {num(sum(y for _, y in both), 0)} s); "
            f"gana {a.name} {wins_a}, empate {ties}, gana {b.name} {wins_b}.",
            "* Media geométrica por dificultad (el mayor de los dos tiempos; instancias): "
            + "; ".join(f"{label} {num(geo_ratio(sel), 2)} ({len(sel)})" for lo, hi, label in SPANS
                        if (sel := [(x, y) for x, y in both if lo <= max(x, y) < hi])) + ".",
            f"* Sólo {a.name}: {families(only_a, self.tab)}.",
            f"* Sólo {b.name}: {families(only_b, self.tab)}.",
        ]
        t = self.tests(a, b)
        lines.append(f"* Wilcoxon sobre el PAR-2 de {t['n']} instancias (sin las que no completa ninguno): "
                     f"p = {pval(t['p_time'])}, r = {num(t['effect'], 2)} (positivo: {a.name} más rápido); "
                     f"McNemar exacto sobre {t['only_a']} frente a {t['only_b']}: p = {pval(t['p_done'])}. "
                     "Sin ajustar; con Holm, en «Significación».")
        return "\n".join(lines)

    def speedup(self, sbs: Experiment, rows: Sequence[Experiment], everyone: Sequence[Experiment]) -> str:
        insts = self.instances
        nobody = {i for i in insts if all(e.time(i) == INF for e in everyone)}
        possible = [i for i in insts if i not in nobody]
        done = [i for i in insts if sbs.time(i) < INF]

        def p2(e: Experiment, sub: Sequence[str]) -> float:
            return par2(e.time(i) for i in sub)

        def ratio(a: float, b: float) -> float:
            return a / b if b > 0 else math.nan

        def per(speedup: float, mine: Optional[int], ref: Optional[int]) -> str:
            return num(speedup * ref / mine, 2) if mine and ref else "?"

        ref = p2(sbs, insts)
        floor = 2 * TIME_LIMIT * len(nobody) / 3600
        lines = [
            f"Referencia (SBS): **{sbs.name}**, PAR-2 {num(ref)} h y {sum(sbs.time(i) < INF for i in insts)} completas. "
            f"PAR-2 sobre las {len(insts)} instancias, con las n/c como no completadas (como en el VBS); "
            "speedup = PAR-2(SBS) / PAR-2; eficiencia = speedup dividido por los trabajadores, en proporción a "
            "los del SBS (el orquestador no cuenta); un VBS suma los de sus miembros.",
            (f"Nadie completa {len(nobody)} instancias entre los experimentos del informe y sus referencias: el "
             f"PAR-2 no baja de {num(floor)} h y el speedup no puede pasar de {num(ratio(ref, floor), 2)}. "
             "«Sin imposibles»: el speedup sobre las demás." if nobody else
             "Alguno de los experimentos completa cada instancia: el PAR-2 no tiene suelo."),
            f"«En las del SBS»: el speedup sobre las {len(done)} que completa el SBS y, entre paréntesis, cuántas "
            "de ellas no completa el experimento.",
        ]
        table_rows = []
        for e in rows:
            sp = ratio(ref, p2(e, insts))
            lost = sum(e.time(i) == INF for i in done)
            table_rows.append([
                e.name, e.workers if e.workers is not None else "?", sum(e.time(i) < INF for i in insts),
                num(p2(e, insts)), num(sp, 2), per(sp, e.workers, sbs.workers),
                num(ratio(p2(sbs, possible), p2(e, possible)), 2) if nobody else "—",
                f"{num(ratio(p2(sbs, done), p2(e, done)), 2)} ({lost})" if done else "—",
            ])
        header = ["experimento", "trabajadores", "completas", "PAR-2 (h)", "speedup", "eficiencia",
                  "sin imposibles", "en las del SBS"]
        return "\n\n".join(lines) + "\n\n" + table(header, table_rows)

    def by_family(self) -> str:
        fams: Dict[str, List[str]] = collections.defaultdict(list)
        for i in self.instances:
            fams[self.tab[i]["family"]].append(i)
        rows = []
        for fam, insts in sorted(fams.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            rows.append([fam, len(insts)] + [sum(e.time(i) < INF for i in insts) for e in self.exps.values()]
                        + [sum(vbs_time(self.inside, i) < INF for i in insts)])
        return table(["familia", "n"] + list(self.exps) + ["VBS"], rows)

    def parity(self) -> str:
        differing, partial_false = [], []
        for inst in self.instances:
            complete: Dict[str, frozenset] = {}
            partial: Dict[str, frozenset] = {}
            for e in self.exps.values():
                s = e.stats(inst) if e.compatible(inst) else None
                if s is None or not isinstance(s.get("backbone"), list):
                    continue
                (complete if s.get("status") == "complete" else partial)[e.name] = frozenset(map(str, s["backbone"]))
            if len(set(complete.values())) > 1:
                differing.append(inst)
            if complete:
                full = next(iter(complete.values()))
                for name, bb in partial.items():
                    if not bb <= full:
                        partial_false.append((name, inst, sorted(bb - full)))
        lines = [f"* Instancias con backbones completos distintos: **{len(differing)}**"
                 + (f" ({', '.join(differing)})" if differing else "") + "."]
        lines.append(f"* Backbones parciales que no están en el backbone completo: **{len(partial_false)}**"
                     + ("" if partial_false else "."))
        for name, inst, lits in partial_false:
            lines.append(f"  * {name}, {inst} ({self.bits(inst)} bits): {' '.join(lits[:10])}"
                         + (" …" if len(lits) > 10 else ""))
        invalid = []
        for e in self.exps.values():
            per = {i: sum((w.get("stats") or {}).get("invalid_solutions") or 0 for w in s["workers"].values())
                   for i in self.instances if e.compatible(i) and (s := e.stats(i)) and s.get("workers")}
            per = {i: n for i, n in per.items() if n}
            if per:
                invalid.append(f"{e.name}: {sum(per.values())} en {len(per)} instancias")
        lines.append("* Soluciones rechazadas por la comprobación exacta: " + ("; ".join(invalid) if invalid else "ninguna") + ".")
        return "\n".join(lines)

    def substitutions(self) -> str:
        rows = []
        for e in self.exps.values():
            subs = []          # (instancia, motivo, segundos perdidos)
            for i in self.instances:
                for w in ((e.stats(i) or {}).get("workers") or {}).values():
                    if w.get("replaced"):
                        after = re.search(r"\(tras (\d+) s\)", w["replaced"])
                        reason = re.sub(r"^\S+: ", "", w["replaced"])          # sin la especificación del antecesor
                        reason = re.sub(r"\s*\(.*$", "", reason).rstrip(":")   # ni los detalles entre paréntesis
                        subs.append((i, reason, float(after.group(1)) if after else math.nan))
            if not subs:
                continue
            lost = [s for _, _, s in subs if not math.isnan(s)]
            reasons = collections.Counter(r for _, r, _ in subs)
            rows.append([e.name, len(subs), sum(e.time(i) < INF for i in {i for i, _, _ in subs}),
                         num(statistics.median(lost), 0) if lost else "—", num(max(lost), 0) if lost else "—",
                         "; ".join(f"{r} ({n})" for r, n in reasons.most_common())])
        return table(["experimento", "sustituciones", "completas", "perdido: mediana (s)", "máximo (s)", "motivo"],
                     rows) if rows else "Ninguna."

    def failures(self) -> str:
        rows = []
        for e in self.exps.values():
            c = collections.Counter(error_class(e.stats(i), e.status[i]) for i in self.instances)
            c.pop(None, None)
            c.update(f"{cls} (n/c)" for cls in e.incompatible.values())
            rows += [[e.name, n, cls] for cls, n in c.most_common()]
        return table(["experimento", "ejecuciones", "error"], rows) if rows else "Ninguno."

    def progress(self) -> str:
        unsolved = [i for i in self.instances if vbs_time(self.inside, i) == INF]
        rows = []
        for e in self.exps.values():
            fracs = []
            for i in unsolved:
                s = e.stats(i) if e.compatible(i) else None
                if s is None:
                    continue
                total = (s.get("num_backbone") or 0) + (s.get("num_refuted") or 0) + (s.get("num_unknown") or 0)
                if total:
                    fracs.append(((s.get("num_backbone") or 0) + (s.get("num_refuted") or 0)) / total)
            rows.append([e.name, len(fracs), num(statistics.fmean(fracs), 2) if fracs else "—",
                         num(statistics.median(fracs), 2) if fracs else "—"])
        return (f"{len(unsolved)} instancias sin completar por nadie ({families(unsolved, self.tab)}).\n\n"
                + table(["experimento", "con datos", "media decidida", "mediana"], rows))

    def cost(self) -> str:
        rows, total = [], 0.0
        for e in self.exps.values():
            ran = [i for i in self.instances if e.wall[i] < INF]       # también las n/c que se ejecutaron
            hours = sum(min(e.wall[i], 2 * TIME_LIMIT) for i in ran) / 3600
            timeouts = sum(e.status[i] == "interrupted" for i in ran)
            node = hours * e.slots / NODE_SLOTS if e.slots else math.nan
            total += 0 if math.isnan(node) else node
            rows.append([e.name, len(ran), num(hours, 0), f"{num(100 * timeouts / max(len(ran), 1), 0)} %",
                         e.slots or "?", num(node)])
        return table(["experimento", "tareas", "horas·tarea", "agotan el límite", "huecos", "horas·nodo"], rows) \
            + f"\n\nTotal: **{num(total, 0)} horas·nodo**."

    def naps_rows(self) -> List[List[Any]]:
        out = []
        for e in self.exps.values():
            for i in self.instances:
                s = e.stats(i) if e.compatible(i) else None
                workers = (s or {}).get("workers") or {}
                native = next(((w.get("stats") or {}).get("native") for w in workers.values()
                               if (w.get("stats") or {}).get("native", {}).get("translation_time") is not None), None)
                if native is None and not any(w.get("solver") in ("cadiback", "napback") for w in workers.values()):
                    continue
                row = self.tab[i]
                out.append([i, row["family"], row["intsize"], row["vars"], row["cons"], e.name, e.status[i],
                            f"{e.wall[i]:.1f}"]
                           + [f"{native[k]:.{d}f}" if native and k in native else ""
                              for k, d in (("translation_time", 3), ("cnf_vars", 0), ("cnf_clauses", 0))]
                           + [error_class(s, e.status[i]) or ""])
        return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("rounds", nargs="+", type=pathlib.Path, help="directorios de ronda (con compare.tsv)")
    p.add_argument("--ref", action="append", default=[], type=pathlib.Path, metavar="RONDA",
                   help="ronda de referencia: sus experimentos sólo entran en --vbs y --pair; repetible")
    p.add_argument("--table", type=pathlib.Path, default=TABLE, help="tabla maestra de instancias")
    p.add_argument("--outside", default="", help="experimentos fuera del VBS y de las únicas (separados por comas)")
    p.add_argument("--vbs", action="append", default=[], metavar="NOMBRE=exp,exp", help="un VBS más; repetible")
    p.add_argument("--pair", action="append", default=[], metavar="A:B[@GRUPO]",
                   help="comparación por instancia; GRUPO: familia del ajuste de Holm (p. ej. RQ2); repetible")
    p.add_argument("--by-bits", action="append", default=[], metavar="NOMBRE=exp,exp[,exp]",
                   help="un secuencial con el protocolo por bits: el experimento de ≤ 32 bits, el de 33–66 y, "
                        "opcional, el de > 66 (vacío: n/c); repetible")
    p.add_argument("--sbs", metavar="NOMBRE", help="referencia de la sección de speedup y eficiencia "
                                                   "(un experimento, un --by-bits o un --vbs)")
    p.add_argument("--clock", choices=("orquestador", "completo", "trabajo"), default="orquestador",
                   help="tiempo de las completas: wall_time de stats.json (por defecto), complete_time (sin la "
                        "espera a los trabajadores tras STOP) o el del trabajo (compare.tsv)")
    p.add_argument("-o", "--out", type=pathlib.Path, help="informe Markdown (por defecto, la salida estándar)")
    p.add_argument("--times", type=pathlib.Path, help="TSV con estado y tiempo por instancia de los experimentos "
                                                       "de la ronda (para tools/paper/figures.py)")
    p.add_argument("--naps", type=pathlib.Path, help="TSV con las traducciones de Naps (D11)")
    args = p.parse_args(argv)

    instances: List[str] = []
    exps: Dict[str, Experiment] = {}
    refs: Dict[str, Experiment] = {}
    for r in args.rounds + args.ref:
        insts, found = read_round(r, clock=args.clock)
        if instances and insts != instances:
            print(f"Error: {r} tiene otra lista de instancias", file=sys.stderr)
            return 1
        instances = insts
        for name in found:
            if name in exps or name in refs:
                print(f"Error: el experimento {name} está en dos rondas", file=sys.stderr)
                return 1
        (refs if r in args.ref else exps).update(found)
    tab = read_table(args.table)
    missing = [i for i in instances if i not in tab]
    if missing:
        print(f"Error: {len(missing)} instancias sin fila en {args.table}: {', '.join(missing[:5])}", file=sys.stderr)
        return 1
    outside = [n.strip() for n in args.outside.split(",") if n.strip()]
    user_vbs = {spec.partition("=")[0] for spec in args.vbs}
    groups = {"todos" + (f" (sin {', '.join(outside)})" if outside else ""): [n for n in exps if n not in outside]}
    pairs = []
    try:
        for n in outside:
            exps[n]
        for spec in args.vbs:
            name, _, members = spec.partition("=")
            groups[name] = [m.strip() for m in members.split(",") if m.strip()]
        known = {**refs, **exps}
        bits = {i: int(tab[i]["intsize"]) for i in instances}
        composites = []
        for spec in args.by_bits:
            name, _, members = spec.partition("=")
            names = [m.strip() for m in members.split(",")]
            if not 2 <= len(names) <= 3:
                print(f"Error: --by-bits {spec!r}: hacen falta 2 o 3 experimentos (≤ 32, 33–66 y > 66 bits)", file=sys.stderr)
                return 1
            known[name] = by_bits_experiment(name, [known[m] if m else None for m in names], instances, bits)
            composites.append(known[name])
        vbs = {name: [known[m] for m in members] for name, members in groups.items()}
        known.update({name: vbs_experiment(name, members, instances) for name, members in vbs.items()})
        for spec in args.pair:
            body, at, group = spec.rpartition("@")
            a, _, b = (body if at else spec).partition(":")
            pairs.append((known[a], known[b], group if at else ""))
        sbs = known[args.sbs] if args.sbs else None
    except KeyError as exc:
        print(f"Error: experimento desconocido {exc}", file=sys.stderr)
        return 1

    rep = Report(instances, exps, tab, outside)
    rounds = ", ".join(r.resolve().name for r in args.rounds)
    if refs:
        rounds += f" (referencia: {', '.join(r.resolve().name for r in args.ref)})"
    if args.clock in ("orquestador", "completo"):
        runs = [(e, i) for e in [*exps.values(), *refs.values()] for i in instances if e.status[i] == "complete"]
        without = sum(i not in e.clock for e, i in runs)
        clock = ("Tiempos de las completas: reloj del orquestador (`wall_time` de `--stats-json`), sin el lanzador, "
                 "el arranque de MPI ni el cierre de los procesos"
                 + (f"; {without} de {len(runs)} ejecuciones completas no lo tienen y usan el del trabajo." if without else "."))
        timed = [(e, i) for e, i in runs if i in e.complete]
        waits = [e.clock[i] - e.complete[i] for e, i in timed if args.clock == "orquestador" and i in e.clock]
        if args.clock == "completo":
            clock = (f"Tiempos de las completas: instante en que se completó el backbone (`complete_time` de "
                     f"`--stats-json`), sin la espera a los trabajadores tras STOP; {len(runs) - len(timed)} de "
                     f"{len(runs)} ejecuciones completas no lo tienen y usan `wall_time` o, sin él, el del trabajo.")
        elif timed:
            long_waits = [w for w in waits if w >= 10]
            clock += (f" {len(timed)} de {len(runs)} tienen además `complete_time`: {len(long_waits)} esperaron 10 s o "
                      f"más a algún trabajador después de completar el backbone"
                      + (f" (máximo {num(max(long_waits), 0)} s)." if long_waits else "."))
    else:
        clock = "Tiempos de las completas: los del trabajo de Slurm (`compare.tsv`)."
    parts = [
        f"# Informe de {rounds}\n\n{len(instances)} instancias, {len(exps)} experimentos, límite {TIME_LIMIT:.0f} s. "
        f"Generado por `tools/ev_report.py`. {clock}",
        "## Cobertura\n\nPAR-2 sobre las instancias compatibles con cada experimento; por tramo de bits, completas. "
        "n/c: fuera de su cobertura o, sin sustituto, ningún trabajador admitió la instancia.\n\n"
        + rep.coverage(),
        "## VBS\n\nSobre todas las instancias: una n/c cuenta como no completada.\n\n" + rep.vbs(vbs),
        "## Únicas\n\n" + rep.unique(),
    ]
    if pairs:
        parts.append("## Pares\n\nEmpate: menos del 10 % o de 1 s de diferencia; completar frente a no completar "
                     "es una victoria.\n\n" + "\n\n".join(rep.pair(a, b) for a, b, _ in pairs))
        parts.append(
            "## Significación\n\nTiempos: Wilcoxon de rangos con signo, bilateral, sobre el PAR-2 de las instancias "
            "compatibles con los dos, sin las que no completa ninguno (n: diferencias no nulas); r: correlación "
            "biserial de rangos, positiva si el primero es más rápido. Completas: McNemar exacto sobre las que "
            f"completa sólo uno (+/−). «Holm»: valores ajustados dentro de cada grupo, con las dos pruebas de cada "
            f"par en la misma familia; en negrita, los significativos al {ALPHA * 100:.0f} %.\n\n"
            + rep.significance(pairs))
    if sbs is not None:
        rows = [sbs] + [e for e in [*exps.values(), *composites, *(known[n] for n in groups if n in user_vbs)]
                        if e is not sbs]
        everyone = [e for e in [*exps.values(), *refs.values()] if not e.virtual]
        parts.append("## Speedup y eficiencia frente al SBS\n\n" + rep.speedup(sbs, rows, everyone))
    parts += [
        "## Por familia\n\n" + rep.by_family(),
        "## Paridad y hechos\n\n" + rep.parity(),
        "## Sustituciones en el sitio\n\nEl trabajador no admitió la instancia al prepararla y el mismo rango siguió "
        "con el sustituto; «perdido»: lo que tardó en descubrirlo.\n\n" + rep.substitutions(),
        "## Fallos y retiradas\n\nCon «(n/c)»: ningún trabajador admitió la instancia y no había sustituto (P5).\n\n"
        + rep.failures(),
        "## Progreso en las que nadie completa\n\nFracción de candidatas decididas (backbone o refutada) al terminar.\n\n"
        + rep.progress(),
        "## Coste\n\nCon el tiempo del trabajo de Slurm, que es lo que ocupa el nodo.\n\n" + rep.cost(),
    ]
    text = "\n\n".join(parts) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if args.times:
        with open(args.times, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(["instance"] + [f"{e}:{c}" for e in exps for c in ("status", "time", "complete_time")])
            for inst in instances:
                row = [inst]
                for e in exps.values():
                    t = e.time(inst)
                    row += [e.status[inst], f"{t:.2f}" if t < INF else "",
                            f"{e.complete[inst]:.2f}" if inst in e.complete else ""]
                w.writerow(row)
    if args.naps:
        with open(args.naps, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(["instance", "family", "intsize", "vars", "cons", "experiment", "status", "wall",
                        "translation_time", "cnf_vars", "cnf_clauses", "error"])
            w.writerows(rep.naps_rows())
    return 0


if __name__ == "__main__":
    sys.exit(main())

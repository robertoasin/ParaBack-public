"""Figuras del artículo (``paper/figures/``) a partir de ``results/ev*/times.tsv`` y de la tabla de instancias.

Las ``times.tsv`` las escribe ``tools/ev_report.py --times`` (reloj del orquestador; las órdenes están en
el README de cada ronda), así que las figuras se regeneran desde el repositorio, sin los ``stats.json``.
Con ``results/ev4/times.tsv`` presente entran también Full-11 y Full-23.

* ``fig-cactus.pdf``: instancias completadas frente al tiempo acumulado (las completas de cada configuración
  de la más rápida a la más lenta; el final de cada curva y sus no completadas dan el PAR-2), prolongadas
  con la misma línea, más fina, hasta el borde para comparar alturas.
* ``fig-families.pdf``: completadas por familia (ronda 1, por paradigma: el mejor extractor de cada uno
  por instancia), con el VBS de los nueve y ParaBack-Full; sólo las familias de al menos 5 instancias en
  que los paradigmas no completan lo mismo.
* ``fig-speedup.pdf``: speedup en PAR-2 frente al SBS (GuroBack-mf con ScipBack-exacto-mf en 33–66 bits,
  nadie por encima, D13), con el techo que ponen las instancias que no completa nadie.
* ``fig-scaling.pdf`` (con ev4): cociente de tiempo de Full-11 y Full-23 frente a ParaBack-Full, por tramo
  de dificultad (el mayor de los dos tiempos) y en total, como los pares de ``ev_report.py``.

Colores (paleta de referencia validada, como el graphical abstract): programación entera en azul, PB
nativo en naranja y SAT en aguamarina; los portfolios en negro y gris, con estilos de línea distintos;
las barras de configuraciones ejecutadas en azul y las de los VBS (virtuales) en gris.

Uso::

    .venv/bin/python tools/paper/figures.py            # escribe las figuras
    .venv/bin/python tools/paper/figures.py --check    # además, completas, PAR-2 y speedup de cada una

Requiere matplotlib (``tools/paper/requirements.txt``). Las fuentes son las del artículo, incrustadas en el PDF
(EJOR): Latin Modern (``\\usepackage{lmodern}``) de TeX Live (``kpsewhich``, o el directorio de ``FIGURES_FONTS``),
pasada a TrueType al vuelo porque matplotlib no incrusta bien una OpenType con contornos CFF. Como en el texto, las
herramientas van en versalitas (``\\tool`` de ``paper/macros.tex``: :func:`tool`) y las configuraciones en sans
serif (``\\cfg``: :func:`cfg`). Sin TeX Live, las Computer Modern de matplotlib (el mismo diseño), con un aviso, y
las herramientas en redonda.
"""
from __future__ import annotations

import argparse
import collections
import csv
import logging
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
RESULTS = ROOT / "results"
TABLE = RESULTS / "base25" / "instancias-pbo25.tsv"
OUT = ROOT / "paper" / "figures"
TIME_LIMIT = 3600.0
#: Ancho de la caja de texto del artículo (``\textwidth`` = 455,24 pt): las figuras se incluyen sin escalar.
TEXT_WIDTH = 455.24 / 72.27

# Paleta de referencia (skill dataviz, modo claro): huecos 1–3 de la categórica, tinta y grises.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SEQUENTIAL = ["#ffffff", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
#: Figura de familias: ninguna, hasta 1/3, hasta 2/3, más de 2/3 y todas. Cinco pasos de la rampa azul (blanco, 250,
#: 400, 550 y 700) separados al menos 16 puntos de L*, para que se distingan también impresos en gris; el primero
#: no nulo se separa del fondo más de 2:1. Los números van en blanco desde el paso 400 (APCA: Lc −69 frente a 40).
FAMILY_SHADES = ["#ffffff", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]


def family_shade(done: int, size: int) -> Tuple[str, str]:
    """(relleno, color del número) de una celda de la figura de familias."""
    if done == 0:
        level = 0
    elif done == size:
        level = 4
    elif 3 * done <= size:
        level = 1
    elif 3 * done <= 2 * size:
        level = 2
    else:
        level = 3
    return FAMILY_SHADES[level], INK if level < 2 else "white"

#: Nombre interno → (nombre en el artículo, trabajadores).
CONFIGS: Dict[str, Tuple[str, int]] = {
    "ev1-rb-pg": ("RB-PG", 1), "ev1-napback": ("NapBack", 1), "ev1-gurobi-mf": ("GuroBack-MF", 1),
    "ev2-rb3": ("RB$\\times$3", 3), "ev2-nap3": ("Nap$\\times$3", 3), "ev2-mip3": ("MIP$\\times$3", 3),
    "ev2-mix3": ("Mix-3", 3), "ev2-mix3-iso": ("Mix-3-Iso", 3),
    "ev3-g": ("Mix-3+G", 4), "ev3-mf": ("Mix-3+MF", 4), "ev3-mf-lp": ("Mix-3+MF+LP", 5),
    "ev3-mf-sym": ("Mix-3+MF+Sym", 5), "ev3-mf-all": ("ParaBack-Full", 6),
    "ev4-medio": ("Full-11", 11), "ev4-nodo": ("Full-23", 23),
}
#: Extractores de la ronda 1 por paradigma (los nueve; ``ev1-gurobi-x`` queda fuera, como en el VBS).
PARADIGMS = {
    "Integer programming": ["ev1-gurobi", "ev1-gurobi-mf", "ev1-scipx", "ev1-scipx-mf"],
    "Native PB": ["ev1-rb", "ev1-rb-pg", "ev1-rb-wp"],
    "SAT": ["ev1-napback", "ev1-napback-srv"],
}
NINE = [e for members in PARADIGMS.values() for e in members]
#: Nombres de familia en inglés para las figuras (la tabla maestra mezcla español e inglés).
FAMILY_NAMES = {
    "featureSubscription": "feature subscription", "síntesis lógica": "logic synthesis",
    "kidney exchange (KE)": "kidney exchange", "pebbling (PHP, VC)": "pebbling", "factor (size=9)": "factoring",
    "RCPSP (PSPLIB)": "RCPSP", "knapsack (knapPI)": "knapsack", "pebbling linealizado": "pebbling (linearized)",
    "pedigree (ped2, ped3)": "pedigree", "vertexcover grid": "vertex cover (grid)",
    "EmployeeScheduling": "employee scheduling", "domset hexgrid": "dominating set (hex grid)",
    "single-obj (LION 9)": "LION 9", "museo (BA, NG)": "BA, NG", "MIPLIB (heinz)": "MIPLIB (Heinz)",
    "VM consolidation (hw)": "VM consolidation", "decomp (matrix_5x3)": "matrix decomposition",
    "haplotype (simp)": "haplotype inference", "XCSP3 (COP)": "XCSP3", "primes (prime implicants)": "prime implicants",
    "aries (optical routing)": "optical routing", "QUBO/MINLPLib linealizado": "QUBO (linearized)",
    "maxcut": "max-cut", "minisat (Zahidi)": "minisat", "flexray": "FlexRay", "mps (MIPLIB/Netlib)": "MPS (Netlib)",
    "course-ass": "course assignment", "mult_diagcomm": "mult-diagcomm", "bnn": "BNN", "fir (MCM)": "FIR (MCM)", "tomografía discreta (lo)": "discrete tomography",
    "clique-coloring": "clique-coloring", "evencolouring": "even colouring", "testset (Poldner)": "testset (Poldner)",
}


def instance_name(file: str) -> str:
    for suffix in (".xz", ".gz", ".opb"):
        if file.endswith(suffix):
            file = file[: -len(suffix)]
    return file


@dataclass
class Data:
    instances: List[str]
    family: Dict[str, str]
    bits: Dict[str, int]
    #: experimento → instancia → tiempo de la completa (``None``: no completó o n/c).
    times: Dict[str, Dict[str, Optional[float]]]

    def vbs(self, members: Sequence[str]) -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {}
        for i in self.instances:
            done = [t for m in members if (t := self.times[m][i]) is not None]
            out[i] = min(done) if done else None
        return out

    def sbs(self) -> Dict[str, Optional[float]]:
        """GuroBack-mf hasta 32 bits, ScipBack-exacto-mf de 33 a 66, nadie por encima (D13)."""
        return {i: self.times["ev1-gurobi-mf"][i] if self.bits[i] <= 32 else
                self.times["ev1-scipx-mf"][i] if self.bits[i] <= 66 else None for i in self.instances}


def load() -> Data:
    with open(TABLE, encoding="utf-8") as f:
        table = {instance_name(r["file"]): r for r in csv.DictReader(f, delimiter="\t")}
    instances: List[str] = []
    times: Dict[str, Dict[str, Optional[float]]] = {}
    for rnd in ("ev1", "ev2", "ev3", "ev4"):
        path = RESULTS / rnd / "times.tsv"
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        names = [r["instance"] for r in rows]
        if instances and names != instances:
            sys.exit(f"{path}: otra lista de instancias")
        instances = names
        for col in rows[0]:
            if col.endswith(":time"):
                exp = col[: -len(":time")]
                times[exp] = {r["instance"]: float(r[col]) if r[col] else None for r in rows}
    return Data(instances, {i: table[i]["family"] for i in instances}, {i: int(table[i]["intsize"]) for i in instances}, times)


def par2(times: Dict[str, Optional[float]]) -> float:
    """PAR-2 en horas sobre todas las instancias (las no completadas y las n/c valen 2 × límite)."""
    return sum(t if t is not None else 2 * TIME_LIMIT for t in times.values()) / 3600


#: Las Latin Modern del artículo (``\\usepackage{lmodern}``) en uso: estilo → familia de matplotlib
#: (``rm`` redonda, ``sf`` sans serif, ``sc`` versalitas), o vacío si no están; lo fija :func:`style`.
LATIN_MODERN: Dict[str, str] = {}
#: Las Latin Modern de TeX Live que hacen falta (``kpsewhich``).
LATIN_MODERN_FILES = {"rm": "lmroman10-regular.otf", "sf": "lmsans10-regular.otf", "sc": "lmromancaps10-regular.otf"}
_FONT_DIR: Optional[tempfile.TemporaryDirectory] = None


def _otf_to_ttf(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Una OpenType con contornos CFF a TrueType (cúbicas a cuadráticas), como ``Snippets/otf2ttf.py`` de
    fontTools: matplotlib declara toda fuente incrustada con ``pdf.fonttype`` 42 como TrueType."""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont, newTable

    font = TTFont(str(src))
    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
        glyphs[name] = pen.glyph()
    font["loca"] = newTable("loca")
    font["glyf"] = glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = glyphs
    del font["CFF "]
    if "VORG" in font:
        del font["VORG"]
    glyf.compile(font)
    hmtx = font["hmtx"]
    for name, glyph in glyphs.items():
        if hasattr(glyph, "xMin"):
            hmtx[name] = (hmtx[name][0], glyph.xMin)
    font["maxp"] = maxp = newTable("maxp")
    maxp.tableVersion, maxp.maxZones = 0x00010000, 1
    for field in ("maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs",
                  "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
        setattr(maxp, field, 0)
    maxp.compile(font)
    post = font["post"]
    post.formatType, post.extraNames, post.mapping, post.glyphOrder = 2.0, [], {}, order
    font["head"].glyphDataFormat = 0
    font.sfntVersion = "\000\001\000\000"
    font.save(str(dst))


def latin_modern() -> Dict[str, str]:
    """Registra en matplotlib las Latin Modern de TeX Live (en TrueType) y devuelve su familia por estilo, o
    nada si falta alguna (``FIGURES_FONTS`` = directorio con los ``.otf``, si no se encuentran con
    ``kpsewhich``)."""
    global _FONT_DIR
    paths: Dict[str, Optional[str]] = {}
    for key, name in LATIN_MODERN_FILES.items():
        path = None
        if os.environ.get("FIGURES_FONTS"):
            path = str(pathlib.Path(os.environ["FIGURES_FONTS"]) / name)
        elif shutil.which("kpsewhich"):
            path = subprocess.run(["kpsewhich", name], capture_output=True, text=True).stdout.strip() or None
        paths[key] = path if path and pathlib.Path(path).is_file() else None
    missing = [LATIN_MODERN_FILES[k] for k, v in paths.items() if v is None]
    if missing:
        print(f"aviso: faltan {', '.join(missing)} (TeX Live o FIGURES_FONTS): las figuras van en Computer Modern "
              "y las herramientas, en redonda", file=sys.stderr)
        return {}
    from matplotlib import font_manager

    _FONT_DIR = tempfile.TemporaryDirectory(prefix="figures-lm-")
    families = {}
    for key, path in paths.items():
        assert path is not None
        ttf = pathlib.Path(_FONT_DIR.name) / (pathlib.Path(path).stem + ".ttf")
        _otf_to_ttf(pathlib.Path(path), ttf)
        font_manager.fontManager.addfont(str(ttf))
        families[key] = font_manager.FontProperties(fname=str(ttf)).get_name()
    return families


def cfg(name: str) -> str:
    """El nombre de una configuración en sans serif, como ``\\cfg`` del artículo (``\\mathsf``: Latin Modern Sans,
    o cmss10 sin ella). Guiones y "+" van fuera de la fórmula; las fórmulas del nombre (``$\\times$``), tal cual."""
    parts = re.split(r"(\$[^$]*\$|[-+])", name)
    return "".join(p if not p or p.startswith("$") or p in ("-", "+") else f"$\\mathsf{{{p}}}$" for p in parts)


def tool(name: str) -> str:
    """El nombre de una herramienta en versalitas, como ``\\tool`` del artículo (``\\mathcal`` apunta a la Latin
    Modern Roman Caps). El guion va fuera de la fórmula: dentro sería un signo menos."""
    if "sc" not in LATIN_MODERN:
        return name
    return "-".join(f"$\\mathcal{{{part}}}$" for part in name.split("-"))


def style() -> None:
    # fontTools avisa de las fechas de las TTF de Computer Modern al incrustarlas; no afecta al PDF.
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    plt.rcParams.update({
        "font.family": "serif", "font.serif": ["cmr10"], "mathtext.fontset": "cm", "axes.unicode_minus": False,
        "axes.formatter.use_mathtext": True, "font.size": 9, "axes.labelsize": 9, "legend.fontsize": 8,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "text.color": INK, "axes.labelcolor": INK,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.6, "xtick.color": INK2, "ytick.color": INK2,
        "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.minor.width": 0.4, "ytick.minor.width": 0.4,
        "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42, "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02, "legend.frameon": False,
    })
    LATIN_MODERN.clear()
    LATIN_MODERN.update(latin_modern())
    if LATIN_MODERN:
        # Texto y fórmulas en Latin Modern, como el artículo; las letras de las fórmulas, en cmmi10 (la cursiva
        # matemática de Latin Modern tiene el mismo diseño) y los símbolos que faltan (≥), de Computer Modern.
        plt.rcParams.update({
            "font.serif": [LATIN_MODERN["rm"]], "mathtext.fontset": "custom", "mathtext.rm": LATIN_MODERN["rm"],
            "mathtext.it": "cmmi10", "mathtext.bf": "cmb10", "mathtext.sf": LATIN_MODERN["sf"], "mathtext.tt": "cmtt10",
            "mathtext.cal": LATIN_MODERN["sc"], "mathtext.fallback": "cm",
        })


def save(fig: "plt.Figure", path: pathlib.Path) -> pathlib.Path:
    fig.savefig(path, metadata={"CreationDate": None, "Creator": "tools/paper/figures.py"})
    plt.close(fig)
    return path


# ---------------------------------------------------------------- cactus
def cactus(data: Data, out: pathlib.Path) -> pathlib.Path:
    """Completadas frente al tiempo acumulado: las completas de cada configuración, de la más rápida a la
    más lenta. El final de la parte continua da cuántas completa y su tiempo total; con 2 h por cada
    instancia no completada, su PAR-2 (en la leyenda). La misma línea, más fina, la prolonga hasta el borde."""
    series = [
        (tool("RB-PG"), data.times["ev1-rb-pg"], dict(color=ORANGE, lw=1.3)),
        (tool("NapBack"), data.times["ev1-napback"], dict(color=AQUA, lw=1.6)),
        (tool("GuroBack-MF") + " (SBS)", data.sbs(), dict(color=BLUE, lw=1.3)),
        ("VBS of the nine", data.vbs(NINE), dict(color=MUTED, lw=1.3, ls=(0, (1, 1.5)))),
        (cfg("Mix-3"), data.times["ev2-mix3"], dict(color=INK2, lw=1.2, ls=(0, (5, 2)))),
        (cfg("ParaBack-Full"), data.times["ev3-mf-all"], dict(color=INK, lw=1.5)),
    ]
    if "ev4-nodo" in data.times:
        series.append((cfg("Full-23"), data.times["ev4-nodo"], dict(color=INK, lw=2.4)))
    n = len(data.instances)
    curves = []
    for label, times, kw in series:
        done = sorted(t for t in times.values() if t is not None)
        xs, total = [0.0], 0.0
        for t in done:
            total += t / 3600
            xs.append(total)
        curves.append((label, kw, xs, list(range(len(done) + 1)), par2(times)))
    right = 2.5 * math.ceil(max(c[2][-1] for c in curves) / 2.5 + 0.2)
    fig, ax = plt.subplots(figsize=(TEXT_WIDTH, 0.5 * TEXT_WIDTH))
    for label, kw, xs, ys, hours in curves:
        ax.plot(xs, ys, label=f"{label}: {ys[-1]} in {xs[-1]:.1f} h, PAR-2 {hours:.1f} h", solid_capstyle="butt", **kw)
        ax.plot([xs[-1], right], [ys[-1], ys[-1]], **{**kw, "lw": 0.45 * kw["lw"]})
    ax.set_xlim(0, right)
    ax.set_ylim(50 * math.floor(min(c[3][-1] for c in curves) / 50), n + 4)
    ax.axhline(n, color=AXIS, lw=0.6, ls=(0, (2, 2)))
    ax.text(0.2, n - 2, f"{n} instances", va="top", fontsize=7.5, color=INK2)
    ax.set_xlabel("Cumulative time of the completed instances, fastest first (hours)")
    ax.set_ylabel("Instances completed")
    ax.grid(True, color=GRID, lw=0.5)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=2, handlelength=2.6, columnspacing=1.8)
    return save(fig, out / "fig-cactus.pdf")


# ---------------------------------------------------------------- familias
def families(data: Data, out: pathlib.Path, min_size: int = 5) -> pathlib.Path:
    full = data.times["ev3-mf-all"]
    headers = ["IP", "PB", "SAT", "VBS", cfg("Full")]
    best = {p: data.vbs(members) for p, members in PARADIGMS.items()}
    nine = data.vbs(NINE)
    by_family: Dict[str, List[str]] = collections.defaultdict(list)
    for i in data.instances:
        by_family[data.family[i]].append(i)
    rows = []
    for fam, insts in by_family.items():
        counts = [sum(best[p][i] is not None for i in insts) for p in PARADIGMS]
        if len(insts) < min_size or len(set(counts)) == 1:
            continue
        values = counts + [sum(nine[i] is not None for i in insts), sum(full[i] is not None for i in insts)]
        # Grupo: el paradigma que completa más (0–2), o empate entre los mejores (3).
        winner = max(range(3), key=lambda k: counts[k]) if counts.count(max(counts)) == 1 else 3
        margin = max(counts) - sorted(counts)[-2]
        rows.append((winner, -margin, -len(insts), FAMILY_NAMES.get(fam, fam), len(insts), values))
    rows.sort()
    half = (len(rows) + 1) // 2
    blocks = [rows[:half], rows[half:]]
    cell = 0.23
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, cell * half + 0.4), gridspec_kw={"wspace": 1.0})
    for ax, block in zip(axes, blocks):
        for y, (_, _, _, _, size, values) in enumerate(block):
            for x, v in enumerate(values):
                shade, ink = family_shade(v, size)
                ax.add_patch(plt.Rectangle((x, y), 1, 1, facecolor=shade, edgecolor="white", lw=1.0))
                ax.text(x + 0.5, y + 0.55, str(v), ha="center", va="center", fontsize=7, color=ink)
        ax.set_xlim(0, len(headers))
        ax.set_ylim(half, 0)
        ax.set_aspect("equal")
        ax.xaxis.tick_top()
        ax.set_xticks([x + 0.5 for x in range(len(headers))], headers, fontsize=7, rotation=90)
        ax.set_yticks([y + 0.5 for y in range(len(block))], [f"{r[3]} ({r[4]})" for r in block], fontsize=7.5)
        ax.tick_params(length=0, pad=2)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.plot([3, 3], [0, len(block)], color=INK, lw=0.9)          # paradigmas | VBS y ParaBack-Full
        for y in range(1, len(block)):                               # entre grupos de familias
            if block[y][0] != block[y - 1][0]:
                ax.plot([0, len(headers)], [y, y], color=INK, lw=0.9)
    return save(fig, out / "fig-families.pdf")


# ---------------------------------------------------------------- speedup
def speedups(data: Data) -> Tuple[float, List[Tuple[str, int, int, float, float, bool]]]:
    """(techo, filas): nombre, trabajadores, completas, PAR-2 (h), speedup, virtual."""
    sbs = par2(data.sbs())
    # Como ``ev_report.py``: las que no completa ningún experimento ejecutado (``ev1-gurobi-x`` incluido).
    nobody = [i for i in data.instances if all(t[i] is None for t in data.times.values())]
    ceiling = sbs / (2 * TIME_LIMIT * len(nobody) / 3600)
    rows = []
    for exp, (label, workers) in CONFIGS.items():
        if exp in data.times and not exp.startswith("ev1-"):
            t = data.times[exp]
            rows.append((label, workers, sum(v is not None for v in t.values()), par2(t), sbs / par2(t), False))
    three = data.vbs(["ev1-rb-pg", "ev1-napback"])
    sbs_t = data.sbs()
    three = {i: min((v for v in (three[i], sbs_t[i]) if v is not None), default=None) for i in data.instances}
    for label, workers, t in (("Virtual best of three", 3, three), ("Virtual best of the nine", 9, data.vbs(NINE))):
        rows.append((label, workers, sum(v is not None for v in t.values()), par2(t), sbs / par2(t), True))
    rows.sort(key=lambda r: r[4])
    return ceiling, rows


def speedup_bars(data: Data, out: pathlib.Path) -> pathlib.Path:
    ceiling, rows = speedups(data)
    fig, ax = plt.subplots(figsize=(TEXT_WIDTH, 0.205 * len(rows) + 0.55))
    for y, (label, workers, _, _, s, virtual) in enumerate(rows):
        ax.barh(y, s, height=0.62, color=MUTED if virtual else BLUE, edgecolor="none")
        ax.text(s + 0.03, y, f"{s:.2f}", va="center", fontsize=7.5, color=INK)
    ax.set_yticks(range(len(rows)), [f"{r[0] if r[5] else cfg(r[0])} ({r[1]})" for r in rows], fontsize=7.5)
    ax.tick_params(axis="y", length=0)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    top = math.ceil(ceiling * 2) / 2 + 0.25
    ax.set_xlim(0, top)
    ax.axvline(1, color=INK2, lw=0.7)
    ax.axvline(ceiling, color=INK2, lw=0.7, ls=(0, (3, 2)))
    ax.text(1, len(rows) - 0.35, " single best solver", fontsize=7, color=INK2, va="bottom")
    ax.text(ceiling, len(rows) - 0.35, f" ceiling {ceiling:.2f}", fontsize=7, color=INK2, va="bottom")
    ax.set_xlabel("Speedup in PAR-2 against the single best solver")
    ax.grid(True, axis="x", color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    return save(fig, out / "fig-speedup.pdf")


# ---------------------------------------------------------------- escalabilidad
#: Tramos de dificultad de ``ev_report.py`` (``SPANS``): el mayor de los dos tiempos.
SPANS = ((0.0, 10.0, "< 10 s"), (10.0, 100.0, "10–100 s"), (100.0, 1000.0, "100–1000 s"), (1000.0, math.inf, "≥ 1000 s"))
#: Las mismas etiquetas en mathtext, que valen con Latin Modern y con el respaldo cmr10 (sin la raya ni ≥, y con
#: otra letra en el "<" de OT1); el ≥ sale siempre de cmsy10, como en el artículo.
SPAN_LABELS = {"< 10 s": r"$<$10 s", "10–100 s": r"$\mathregular{10\endash 100}$ s",
               "100–1000 s": r"$\mathregular{100\endash 1\,000}$ s", "≥ 1000 s": r"$\geq$1$\,$000 s",
               "all": "all completed"}


def ratios(a: Dict[str, Optional[float]], b: Dict[str, Optional[float]]) -> List[Tuple[str, float, int]]:
    """Media geométrica de t(a)/t(b) en las que completan los dos, con un mínimo de 1 s (como
    ``ev_report.geo_ratio``): por tramo de dificultad y en total."""
    pairs = [(ta, tb) for i in a if (ta := a[i]) is not None and (tb := b[i]) is not None]
    out = []
    for lo, hi, label in SPANS + ((0.0, math.inf, "all"),):
        sel = [(x, y) for x, y in pairs if lo <= max(x, y) < hi]
        out.append((label, math.exp(sum(math.log(max(x, 1.0) / max(y, 1.0)) for x, y in sel) / len(sel)), len(sel)))
    return out


def scaling(data: Data, out: pathlib.Path) -> Optional[pathlib.Path]:
    """Cociente de tiempo de Full-11 y Full-23 frente a ParaBack-Full por tramo de dificultad (ronda 4)."""
    if "ev4-nodo" not in data.times:
        return None
    full = data.times["ev3-mf-all"]
    series = [(cfg("Full-11") + " (11 workers)", ratios(data.times["ev4-medio"], full), SEQUENTIAL[3]),
              (cfg("Full-23") + " (23 workers)", ratios(data.times["ev4-nodo"], full), SEQUENTIAL[6])]
    groups = [label for label, _, _ in series[0][1]]
    fig, ax = plt.subplots(figsize=(TEXT_WIDTH, 0.36 * TEXT_WIDTH))
    width = 0.36
    for k, (label, rows, color) in enumerate(series):
        for x, (_, r, _) in enumerate(rows):
            pos = x + (k - 0.5) * (width + 0.04)
            ax.bar(pos, r, width=width, color=color, edgecolor="none", label=label if x == 0 else None)
            ax.text(pos, r + 0.015, f"{r:.2f}", ha="center", va="bottom", fontsize=7.5, color=INK)
    ax.set_xticks(range(len(groups)),
                  [f"{SPAN_LABELS[g]}\n$n$ = {series[0][1][x][2]}, {series[1][1][x][2]}" for x, g in enumerate(groups)])
    ax.tick_params(axis="x", length=0)
    ax.set_xlim(-0.6, len(groups) - 0.4)
    ax.set_ylim(0, 1.12)
    ax.axhline(1, color=INK2, lw=0.7)
    ax.text(len(groups) - 0.42, 1.01, cfg("ParaBack-Full") + " (6 workers)", ha="right", va="bottom", fontsize=7, color=INK2)
    ax.set_ylabel("Time ratio against\n" + cfg("ParaBack-Full"))
    ax.grid(True, axis="y", color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=2, handlelength=1.4, columnspacing=2.0)
    return save(fig, out / "fig-scaling.pdf")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true", help="imprime completas, PAR-2 y speedup de cada configuración")
    p.add_argument("--out", type=pathlib.Path, default=OUT, help="directorio de las figuras (por defecto, paper/figures)")
    args = p.parse_args(argv)
    data = load()
    style()
    args.out.mkdir(parents=True, exist_ok=True)
    for path in (cactus(data, args.out), families(data, args.out), speedup_bars(data, args.out), scaling(data, args.out)):
        if path is not None:
            print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    if args.check:
        ceiling, rows = speedups(data)
        print(f"SBS: {sum(v is not None for v in data.sbs().values())} completas, PAR-2 {par2(data.sbs()):.1f} h; techo {ceiling:.2f}")
        for label, workers, done, hours, s, virtual in rows:
            print(f"  {label:28s} {workers:3d} trabajadores  {done:3d} completas  PAR-2 {hours:6.1f} h  speedup {s:.2f}"
                  + ("  (virtual)" if virtual else ""))
        if "ev4-nodo" in data.times:
            for exp in ("ev4-medio", "ev4-nodo"):
                print(f"  {CONFIGS[exp][0]}/ParaBack-Full: "
                      + "; ".join(f"{g} {r:.2f} ({n})" for g, r, n in ratios(data.times[exp], data.times["ev3-mf-all"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

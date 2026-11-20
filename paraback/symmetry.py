"""Simetrías de una instancia PB: automorfismos de su grafo y cierre de hechos bajo ellos.

Una **simetría** es una permutación de los literales (cerrada bajo negación) que lleva el
conjunto de restricciones a sí mismo y la función objetivo a sí misma. Lleva, por tanto,
soluciones a soluciones del mismo coste, así que:

* si ``l`` es backbone (verdadero en toda solución óptima), ``g(l)`` también lo es;
* si ``x_i`` no es backbone (alguna solución óptima la cambia), tampoco lo es ``x_j`` con
  ``g(x_i) = ±x_j``;
* si ``x*`` es óptima, ``g(x*)`` es otra solución óptima: toda variable en que difieren queda
  refutada.

Las simetrías se obtienen como automorfismos de un grafo coloreado (la construcción habitual
para fórmulas PB, la misma de BreakID o del detector de SCIP): un vértice por literal (los dos
de una variable, unidos por una arista), uno por restricción (color = operador y término
independiente, en forma canónica sobre literales con coeficientes positivos), uno por
término con coeficiente distinto de 1 (color = coeficiente; con coeficiente 1 el literal se
une directamente a la restricción) y uno para el objetivo. Todo automorfismo del grafo
induce una simetría de la instancia. Los automorfismos los calcula bliss (``python-igraph``).

**Cada generador se verifica con enteros** antes de usarse (:func:`verify_generator`): se
aplica a cada restricción y al objetivo y se comprueba que el multiconjunto de restricciones
canónicas no cambia. Un generador que no pase la verificación es un error de construcción
del grafo y se trata como conocimiento inconsistente, nunca como un hecho. Así los hechos
que publica el trabajador de simetrías no dependen de ninguna biblioteca más que de esta
comprobación.

**La detección va en un proceso hijo** (:class:`IsolatedDetection`, ``python -m
paraback.symmetry``) con el límite de memoria del hueco: bliss guarda cada generador como una
permutación de todos los vértices del grafo, y con grupos enormes no cabe (knapPI_14 de la PBO
2025: 10^18 589 automorfismos, 70 GB en el piloto de ev3). Así el límite no toca al rango MPI y
un STOP lo termina al momento (bliss no se puede interrumpir).

Literales: ``2*i`` es ``x_i`` verdadera y ``2*i + 1`` es ``x_i`` falsa.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import tempfile
import time
from array import array
from typing import IO, Counter as CounterT, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from guroback.line_server import preexec_with_limit
from guroback.opb import OPBInstance, parse_opb

#: Un generador: la imagen de cada literal (``array('i')`` de longitud ``2n``).
Generator = Sequence[int]

#: Forma canónica de una restricción sobre literales: ``(operador, rhs, ((coef, lit), …))``.
Canonical = Tuple[str, int, Tuple[Tuple[int, int], ...]]


def neg(lit: int) -> int:
    return lit ^ 1


def var(lit: int) -> int:
    return lit >> 1


# ---------------------------------------------------------------------------- forma canónica
def literal_terms(coefs: Sequence[int], indices: Sequence[int]) -> Tuple[List[Tuple[int, int]], int]:
    """``sum a_i x_i`` como ``sum |a_i| l_i - desplazamiento`` con ``l_i`` el literal adecuado.

    ``a x = -|a| x = |a| ¬x - |a|`` para ``a < 0``; los coeficientes nulos se ignoran.
    """
    terms: List[Tuple[int, int]] = []
    shift = 0
    for a, i in zip(coefs, indices):
        if a > 0:
            terms.append((a, 2 * i))
        elif a < 0:
            terms.append((-a, 2 * i + 1))
            shift += -a
    return terms, shift


def canonical(coefs: Sequence[int], indices: Sequence[int], op: str, rhs: int) -> Canonical:
    """Restricción en forma canónica: ``>=`` o ``=`` sobre literales con coeficientes positivos."""
    terms, shift = literal_terms(coefs, indices)
    rhs += shift
    if op == "<=":
        # sum |a| l <= rhs  <=>  sum |a| ¬l >= sum |a| - rhs
        total = sum(a for a, _ in terms)
        terms = [(a, neg(l)) for a, l in terms]
        rhs, op = total - rhs, ">="
    return op, rhs, tuple(sorted(terms))


def instance_forms(instance: OPBInstance) -> Tuple["CounterT[Canonical]", Optional[Tuple[int, Tuple[Tuple[int, int], ...]]]]:
    """Multiconjunto de restricciones canónicas y el objetivo como ``(desplazamiento, términos)``."""
    forms: "CounterT[Canonical]" = collections.Counter()
    for c in range(len(instance.constraints)):
        t = instance.terms_of(c)
        forms[canonical(t.coefs, t.indices, instance.operator(c), instance.rhs(c))] += 1
    objective = None
    if instance.objective is not None:
        terms, shift = literal_terms(instance.objective.coefs, instance.objective.indices)
        objective = (shift, tuple(sorted(terms)))
    return forms, objective


def apply(perm: Generator, form: Canonical) -> Canonical:
    op, rhs, terms = form
    return op, rhs, tuple(sorted((a, perm[l]) for a, l in terms))


def verify_generator(instance: OPBInstance, perm: Generator, forms: Optional["CounterT[Canonical]"] = None) -> Optional[str]:
    """``None`` si ``perm`` es una simetría de la instancia (con enteros); si no, por qué no."""
    n = instance.num_vars
    if len(perm) != 2 * n:
        return f"la permutación tiene {len(perm)} literales y la instancia {2 * n}"
    seen = bytearray(2 * n)
    for lit in range(2 * n):
        image = perm[lit]
        if not 0 <= image < 2 * n or seen[image]:
            return f"la imagen de {lit} no es una permutación"
        seen[image] = 1
        if perm[neg(lit)] != neg(image):
            return f"la imagen de ¬x{var(lit)} no es la negación de la imagen de x{var(lit)}"
    if forms is None:
        forms, _ = instance_forms(instance)
    mapped: "CounterT[Canonical]" = collections.Counter()
    for form, count in forms.items():
        mapped[apply(perm, form)] += count
    if mapped != forms:
        missing = next(iter((mapped - forms).elements()))
        return f"la imagen de una restricción no está en la instancia: {missing[0]} {missing[1]} {missing[2][:4]}…"
    if instance.objective is not None:
        terms, shift = literal_terms(instance.objective.coefs, instance.objective.indices)
        if tuple(sorted((a, perm[l]) for a, l in terms)) != tuple(sorted(terms)):
            return "no conserva la función objetivo"
    return None


# ---------------------------------------------------------------------------- grafo y generadores
def build_graph(instance: OPBInstance) -> Tuple[List[Tuple[int, int]], List[int]]:
    """Aristas y colores del grafo cuyos automorfismos son simetrías de la instancia.

    Vértices ``0..2n-1``: literales (color 0). Después, uno por restricción, uno por término con
    coeficiente ≠ 1 y uno por el objetivo (más sus términos), con colores por clase.
    """
    n = instance.num_vars
    colors = [0] * (2 * n)
    edges: List[Tuple[int, int]] = [(2 * i, 2 * i + 1) for i in range(n)]
    color_of: Dict[object, int] = {}

    def color(key: object) -> int:
        c = color_of.get(key)
        if c is None:
            c = color_of[key] = len(color_of) + 1
        return c

    def add_block(terms: Sequence[Tuple[int, int]], vertex: int) -> None:
        for a, lit in terms:
            if a == 1:
                edges.append((lit, vertex))
            else:
                t = len(colors)
                colors.append(color(("coef", a)))
                edges.append((lit, t))
                edges.append((t, vertex))

    for c in range(len(instance.constraints)):
        t = instance.terms_of(c)
        op, rhs, terms = canonical(t.coefs, t.indices, instance.operator(c), instance.rhs(c))
        v = len(colors)
        colors.append(color(("constraint", op, rhs)))
        add_block(terms, v)
    if instance.objective is not None:
        obj_terms, shift = literal_terms(instance.objective.coefs, instance.objective.indices)
        v = len(colors)
        colors.append(color(("objective", shift)))
        add_block(obj_terms, v)
    return edges, colors


def graph_size(instance: OPBInstance) -> Tuple[int, int]:
    """``(vértices, aristas)`` del grafo de :func:`build_graph`, sin construirlo."""
    n, m = instance.num_vars, len(instance.constraints)
    non_unit = sum(1 for c in range(m) for a in instance.terms_of(c).coefs if a != 1 and a != -1)
    terms = instance.num_terms
    vertices, edges = 2 * n + m + non_unit, n + terms + non_unit
    if instance.objective is not None:
        obj_non_unit = sum(1 for a in instance.objective.coefs if a != 1 and a != -1)
        vertices += 1 + obj_non_unit
        edges += len(instance.objective) + obj_non_unit
    return vertices, edges


class SymmetryError(RuntimeError):
    """El detector no se puede usar o un generador no pasa la verificación."""


def find_generators(instance: OPBInstance, *, verify: bool = True) -> Tuple[List[Generator], Dict[str, float]]:
    """Generadores (verificados) del grupo de simetrías de la instancia y métricas de la detección.

    Lanza :class:`SymmetryError` si falta ``python-igraph`` o si un generador no es una
    simetría exacta (lo que sería un error de construcción del grafo).
    """
    try:
        import igraph  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise SymmetryError(f"python-igraph no instalado: {exc}") from exc
    n = instance.num_vars
    start = time.perf_counter()
    edges, colors = build_graph(instance)
    graph = igraph.Graph(n=len(colors), edges=edges)
    built = time.perf_counter()
    raw = graph.automorphism_group(color=colors)
    detected = time.perf_counter()
    forms, _ = instance_forms(instance) if verify else (None, None)
    generators: List[Generator] = []
    for perm in raw:
        gen = array("i", perm[: 2 * n])
        if all(gen[l] == l for l in range(2 * n)):
            continue
        if verify:
            reason = verify_generator(instance, gen, forms)
            if reason is not None:
                raise SymmetryError(f"un automorfismo del grafo no es una simetría de la instancia: {reason}")
        generators.append(gen)
    metrics = {
        "graph_vertices": float(len(colors)),
        "graph_edges": float(len(edges)),
        "generators": float(len(generators)),
        "graph_time": built - start,
        "detection_time": detected - built,
        "verify_time": time.perf_counter() - detected,
    }
    return generators, metrics


# ---------------------------------------------------------------------------- detección en un proceso hijo
#: Códigos de salida del detector (:func:`main`): sin memoria y :class:`SymmetryError`.
DETECT_MEMORY_EXIT = 4
DETECT_SYMMETRY_EXIT = 3
#: El hijo sólo ejecuta bliss: OpenBLAS (que carga igraph) reservaría memoria para un hilo por
#: núcleo (96 en node906) y la contaría ``RLIMIT_AS``.
DETECT_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}


class DetectionFailed(RuntimeError):
    """El detector aislado terminó sin resultado y no fue por memoria ni por :class:`SymmetryError`."""


def detector_argv(path: str) -> List[str]:
    """La orden del detector aislado para la instancia ``path``."""
    return [sys.executable, "-m", "paraback.symmetry", path]


class IsolatedDetection:
    """:func:`find_generators` en un proceso hijo con ``RLIMIT_AS`` (``memory_limit_mb``; ``None``: sin límite).

    :meth:`run` bloquea hasta que el hijo termina y devuelve los generadores verificados y las
    métricas, o lanza :class:`MemoryError` (sin memoria o muerto por una señal),
    :class:`SymmetryError` (lo que lanzaría :func:`find_generators`) o :class:`DetectionFailed`
    (cualquier otro fallo del hijo, con el final de su salida de error). :meth:`kill` lo termina
    desde otro hilo.
    """

    def __init__(self, path: str, memory_limit_mb: Optional[float] = None) -> None:
        self.path = path
        self.memory_limit_mb = memory_limit_mb
        self.exit_code: Optional[int] = None
        self._proc: Optional["subprocess.Popen[bytes]"] = None
        self._killed = False

    def run(self) -> Tuple[List[Generator], Dict[str, float]]:
        with tempfile.TemporaryFile() as err:
            proc = self._proc = subprocess.Popen(detector_argv(self.path), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.PIPE, stderr=err, env={**os.environ, **DETECT_ENV},
                                                 preexec_fn=preexec_with_limit(self.memory_limit_mb))
            if self._killed:      # kill() llegó antes de que hubiera proceso
                proc.kill()
            assert proc.stdout is not None
            try:
                generators, metrics = _read_generators(proc.stdout)
                complete = True
            except (EOFError, ValueError):     # el hijo murió a medias (o no escribió nada)
                generators, metrics, complete = [], {}, False
            proc.stdout.close()
            code = self.exit_code = proc.wait()
            err.seek(0)
            tail = "\n".join(err.read().decode("utf-8", "replace").splitlines()[-5:])
        if code == 0 and complete:
            return generators, metrics
        if code == DETECT_SYMMETRY_EXIT:
            raise SymmetryError(tail)
        if code == DETECT_MEMORY_EXIT or code < 0:
            if self._killed:
                cause = "detenido"
            elif code == DETECT_MEMORY_EXIT:
                cause = f"sin memoria (MemoryLimit={self.memory_limit_mb} MB)"
            else:
                cause = f"muerto por la señal {-code}"
            raise MemoryError(f"detector de simetrías {cause}")
        raise DetectionFailed(f"el detector de simetrías terminó con código {code}: {tail}")

    def kill(self) -> None:
        self._killed = True
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()


def _write_generators(out: IO[bytes], generators: Sequence[Generator], literals: int, metrics: Dict[str, float]) -> None:
    """Cabecera JSON en una línea y, detrás, cada generador como ``literals`` enteros de 32 bits."""
    out.write(json.dumps({"generators": len(generators), "literals": literals, "metrics": metrics}).encode() + b"\n")
    for g in generators:
        out.write(array("i", g).tobytes())
    out.flush()


def _read_generators(stream: IO[bytes]) -> Tuple[List[Generator], Dict[str, float]]:
    header = json.loads(stream.readline())
    generators: List[Generator] = []
    for _ in range(header["generators"]):
        g = array("i")
        g.fromfile(stream, header["literals"])
        generators.append(g)
    return generators, header["metrics"]


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m paraback.symmetry INSTANCIA``: :func:`find_generators` con el resultado en binario por la salida estándar."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("uso: python -m paraback.symmetry INSTANCIA", file=sys.stderr)
        return 2
    try:
        instance = parse_opb(args[0])
        generators, metrics = find_generators(instance)
    except MemoryError as exc:
        print(f"sin memoria: {exc}", file=sys.stderr)
        return DETECT_MEMORY_EXIT
    except SymmetryError as exc:
        print(exc, file=sys.stderr)
        return DETECT_SYMMETRY_EXIT
    _write_generators(sys.stdout.buffer, generators, 2 * instance.num_vars, metrics)
    return 0


# ---------------------------------------------------------------------------- órbitas e imágenes
def literal_orbit(lit: int, generators: Sequence[Generator]) -> Set[int]:
    """Todos los literales a los que el grupo generado lleva ``lit`` (BFS sobre los generadores)."""
    seen = {lit}
    frontier = [lit]
    while frontier:
        nxt = []
        for l in frontier:
            for g in generators:
                m = g[l]
                if m not in seen:
                    seen.add(m)
                    nxt.append(m)
        frontier = nxt
    return seen


def variable_orbit(v: int, generators: Sequence[Generator]) -> Set[int]:
    """Variables a las que el grupo lleva ``x_v`` (con o sin negación)."""
    return {var(l) for l in literal_orbit(2 * v, generators)}


def image_of_assignment(values: Sequence[bool], g: Generator) -> List[bool]:
    """``g(x)``: la asignación en que ``g(l)`` es verdadero para cada literal ``l`` verdadero en ``x``."""
    out = [False] * len(values)
    for i, value in enumerate(values):
        m = g[2 * i if value else 2 * i + 1]
        out[var(m)] = (m & 1) == 0
    return out


def solution_images(values: Sequence[bool], generators: Sequence[Generator], limit: int) -> List[List[bool]]:
    """Hasta ``limit`` imágenes distintas de ``values`` bajo el grupo (BFS), sin ``values``."""
    key0 = bytes(values)
    seen = {key0}
    out: List[List[bool]] = []
    frontier = [list(values)]
    while frontier and len(out) < limit:
        nxt = []
        for x in frontier:
            for g in generators:
                y = image_of_assignment(x, g)
                key = bytes(y)
                if key not in seen:
                    seen.add(key)
                    out.append(y)
                    nxt.append(y)
                    if len(out) >= limit:
                        return out
        frontier = nxt
    return out


__all__ = [
    "Canonical",
    "DETECT_MEMORY_EXIT",
    "DETECT_SYMMETRY_EXIT",
    "DetectionFailed",
    "Generator",
    "IsolatedDetection",
    "SymmetryError",
    "build_graph",
    "canonical",
    "detector_argv",
    "find_generators",
    "graph_size",
    "image_of_assignment",
    "instance_forms",
    "literal_orbit",
    "neg",
    "solution_images",
    "var",
    "variable_orbit",
    "verify_generator",
]


if __name__ == "__main__":
    sys.exit(main())

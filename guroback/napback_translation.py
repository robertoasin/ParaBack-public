"""Traducción PB → CNF de NapBack (Naps) y sus comprobaciones de cordura.

NapBack calcula backbones sobre la CNF que Naps obtiene de la instancia PB. La
CNF no es equivalente a la PBO sino **equisatisfacible**: añade variables
auxiliares. Lo que la extracción necesita es que su proyección sobre las
variables PB tenga exactamente las mismas soluciones, y que la correspondencia
de ids (línea ``cv`` de Naps) sea biunívoca. Un fallo en cualquiera de las dos
cosas no produce ningún error: produce backbones erróneos con aspecto válido.
Por eso este módulo falla en voz alta (:class:`NapBackError`) ante cualquier
duda (``docs/PLAN_NAPBACK.md`` §4):

* :meth:`VarMap.from_cnf` — la línea ``cv`` (§4.1): una sola, tokens
  ``<sat>:x<k>``, biunívoca, variables SAT exactamente ``1..n_pb`` y cobertura
  exacta de las variables de la instancia; :meth:`VarMap.check_numbering` exige
  además la numeración por orden de aparición que usa Naps (derivada aparte).
* :func:`write_decision_instance` — la instancia de decisión ``obj = z*``
  (``naps -cnf`` no codifica el objetivo, §1.1 V4).
* :func:`translate` — invoca a Naps **siempre con ``-GM``**: el exportador por
  defecto (CaDiCaL) omite los literales fijados en el nivel 0 y la CNF deja
  de ser equisatisfacible (§1.1 V3). Nunca pasa ``-cSCIP``/``-SCIP`` (coma flotante).
* :func:`forced_literals` + :func:`check_nothing_lost` — comprobación
  obligatoria de §4.2, punto 5: todo literal que la propagación unitaria PB
  fuerza en la instancia original tiene que estar implicado por la CNF.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .backend import Incompatible, SolverCrashed
from .line_server import LineServerError, is_crash_exit, popen_die_with_parent
from .opb import OPBInstance, iter_lines, open_text

#: Variables de entorno con las rutas de los ejecutables; si no están se buscan
#: en ``<repo>/napback/build/…`` (``make napback``) y después en ``PATH``.
NAPS_BIN_ENV = "NAPS_BIN"
CADICAL_BIN_ENV = "CADICAL_BIN"

#: Versión de Naps con la que se hizo la Etapa 0 (``docs/PLAN_NAPBACK.md`` §1.1).
#: Otra versión puede cambiar el SAT por defecto o el formato de ``cv``.
NAPS_TESTED_VERSION = "1.4.1"

#: Exportadores admitidos. ``cadical`` sólo existe para los tests de regresión
#: de V3: pierde las unidades del nivel 0 y la comprobación 5 lo rechaza.
EXPORTERS: Mapping[str, Tuple[str, ...]] = {"glueminisat": ("-GM",), "cadical": ("-CD",)}

#: Opciones de Naps que no se aceptan: SCIP trabaja en coma flotante y los otros
#: cambian el exportador fijado por :data:`EXPORTERS`.
FORBIDDEN_NAPS_OPTIONS = ("-SCIP", "-cSCIP", "-iSCIP", "-cooperative-scip", "-GM", "-CD", "-KS", "-cadical", "-kissat", "-glueminisat")

_REPO = pathlib.Path(__file__).resolve().parent.parent
_CV_TOKEN = re.compile(r"^(\d+):(x(\d+))$")
_ABORTED_UNSAT = "Aborted to export to CNF due to unsatisfiability"
#: Naps sale con este código cuando atiende un SIGTERM o SIGINT (``Main.C``). NapBack nunca se los
#: manda (para interrumpirla usa SIGKILL), así que viene de la propia Naps: su límite de nodos
#: (``-max-nodes``) u otro límite interno. Es una falta de recursos, no una traducción corrupta.
NAPS_SELF_STOP_EXIT = 10
#: Límite de nodos de fórmula de Naps (``patches/naps/0002-fenv-node-limit.patch``): un nodo guarda
#: su índice en 28 bits. Cada nodo cuesta unos 12 bytes en un vector que crece ×1,5, más su celda
#: en la tabla de unicidad (24 bytes más la cabecera de ``malloc``) y su puntero en la tabla: unos
#: 80 bytes en el peor caso. Con un límite de memoria, los nodos pueden ocupar la mitad (el resto es
#: para la clausificación y la CNF): Naps se detiene limpiamente en vez de abortar sin memoria.
NAPS_MAX_NODES = 2**28
NAPS_NODE_BYTES = 80
NAPS_NODE_MEMORY_SHARE = 0.5
_FENV_PEAK = re.compile(r"FEnv nodes peak\s*:\s*(\d+)")


def naps_max_nodes(memory_limit_mb: Optional[float]) -> Optional[int]:
    """``-max-nodes`` de Naps para un límite de memoria en MB (``None``: el de Naps, 2^28)."""
    if not memory_limit_mb:
        return None
    return min(NAPS_MAX_NODES, int(memory_limit_mb * 2**20 * NAPS_NODE_MEMORY_SHARE / NAPS_NODE_BYTES))


class NapBackError(LineServerError):
    """La traducción, el mapa de variables o el proceso de NapBack no son de fiar."""


class NapBackCrashed(NapBackError, SolverCrashed):
    """Naps, el servidor de NapBack o CaDiCaL murieron (señal, memoria): el trabajador se retira (ver :class:`SolverCrashed`)."""


class NapsCannotTranslate(NapBackCrashed, Incompatible):
    """Naps no produce la CNF (sin memoria o en uno de sus límites): con ``--substitute``, el hueco sigue con el sustituto."""


class PropagationConflict(NapBackError):
    """La propagación unitaria PB ya demuestra que la instancia (con z*) no tiene solución.

    No es un fallo de la traducción: el backend lo convierte en ``INFEASIBLE``
    (p. ej. un z* por debajo del óptimo), igual que RoundingBack.
    """


# ---------------------------------------------------------------------- ejecutables
def _find(env: str, candidates: Iterable[pathlib.Path], names: Sequence[str]) -> Optional[str]:
    paths: List[str] = []
    if os.environ.get(env):
        paths.append(os.environ[env])
    paths.extend(str(c) for c in candidates)
    for name in names:
        which = shutil.which(name)
        if which:
            paths.append(which)
    for path in paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def find_naps() -> Optional[str]:
    """Ruta de ``naps_bignum_static`` (``make napback``), o ``None``."""
    build = _REPO / "napback" / "build" / "naps"
    return _find(NAPS_BIN_ENV, [build / f"naps_bignum_static-{NAPS_TESTED_VERSION}", build / "naps_bignum_static"], ["naps_bignum_static"])


def find_cadical() -> Optional[str]:
    """Ruta del ejecutable de CaDiCaL (comprobación 5 hasta que exista ``napback-server``)."""
    return _find(CADICAL_BIN_ENV, [_REPO / "napback" / "build" / "cadical" / "build" / "cadical"], ["cadical"])


def naps_version(binary: str) -> str:
    """Versión que anuncia Naps en su ayuda (``Naps 1.4.1:``)."""
    out = subprocess.run([binary, "-h"], capture_output=True, text=True, timeout=30)
    match = re.search(r"^Naps (\S+):", out.stdout + out.stderr, re.MULTILINE)
    return match.group(1) if match else "desconocida"


# ---------------------------------------------------------------------- mapa de variables
@dataclass(frozen=True)
class VarMap:
    """Correspondencia biunívoca ``x<k>`` ↔ variable SAT, verificada (§4.1)."""

    #: ``nombre PB -> variable SAT`` (siempre positiva).
    sat_of: Mapping[str, int]
    #: ``variable SAT -> nombre PB``.
    name_of: Mapping[int, str]
    #: Cabecera ``p cnf <num_sat_vars> <num_clauses>``.
    num_sat_vars: int
    num_clauses: int

    @property
    def num_pb_vars(self) -> int:
        return len(self.sat_of)

    def literal(self, name: str, value: bool) -> int:
        sat = self.sat_of[name]
        return sat if value else -sat

    @classmethod
    def from_lines(cls, header: Optional[str], cv_lines: Sequence[str], names: Iterable[str]) -> "VarMap":
        """Construye y verifica el mapa a partir de la cabecera y las líneas ``cv``.

        ``names`` son las variables de la instancia (las ``x<k>`` que aparecen en
        el archivo, como las numera ``scan_opb``): cada una debe estar mapeada y
        el mapa no puede contener otras.
        """
        if header is None:
            raise NapBackError("la CNF no tiene cabecera 'p cnf'")
        parts = header.split()
        if len(parts) != 4 or parts[:2] != ["p", "cnf"] or not parts[2].isdigit() or not parts[3].isdigit():
            raise NapBackError(f"cabecera DIMACS inválida: {header.strip()!r}")
        num_sat_vars, num_clauses = int(parts[2]), int(parts[3])
        if len(cv_lines) != 1:
            raise NapBackError(f"se esperaba exactamente una línea 'cv' y hay {len(cv_lines)}")
        tokens = cv_lines[0].split()
        if not tokens or tokens[0] != "cv":
            raise NapBackError(f"línea 'cv' inválida: {cv_lines[0][:80]!r}")
        sat_of: Dict[str, int] = {}
        name_of: Dict[int, str] = {}
        for token in tokens[1:]:
            match = _CV_TOKEN.match(token)
            if match is None:
                raise NapBackError(f"token de 'cv' inválido: {token!r} (se espera '<variable SAT>:x<k>')")
            sat, name = int(match.group(1)), match.group(2)
            if sat in name_of:
                raise NapBackError(f"la variable SAT {sat} aparece dos veces en 'cv' ({name_of[sat]} y {name})")
            if name in sat_of:
                raise NapBackError(f"{name} aparece dos veces en 'cv' (variables SAT {sat_of[name]} y {sat})")
            sat_of[name], name_of[sat] = sat, name
        n_pb = len(sat_of)
        if n_pb == 0:
            raise NapBackError("la línea 'cv' está vacía")
        # Naps numera las variables PB 1..n_pb por orden de aparición y las auxiliares después (§1.1 V2).
        if set(name_of) != set(range(1, n_pb + 1)):
            outside = sorted(v for v in name_of if not 1 <= v <= n_pb)
            raise NapBackError(f"las variables SAT mapeadas no son 1..{n_pb} (p. ej. {outside[:3]})")
        if n_pb > num_sat_vars:
            raise NapBackError(f"'cv' mapea {n_pb} variables pero la cabecera declara {num_sat_vars}")
        expected = set(names)
        missing, extra = expected - set(sat_of), set(sat_of) - expected
        if missing:
            raise NapBackError(f"{len(missing)} variables de la instancia no están en 'cv' (p. ej. {_first(missing)})")
        if extra:
            raise NapBackError(f"'cv' contiene {len(extra)} variables que no están en la instancia (p. ej. {_first(extra)})")
        return cls(sat_of=sat_of, name_of=name_of, num_sat_vars=num_sat_vars, num_clauses=num_clauses)

    @classmethod
    def from_cnf(cls, cnf_path: str, names: Iterable[str]) -> "VarMap":
        """Lee la cabecera y las líneas ``cv`` de la CNF de Naps y verifica el mapa."""
        header: Optional[str] = None
        cv_lines: List[str] = []
        # Lectura binaria línea a línea: la CNF puede ocupar gigabytes (khb05250: 2 GB).
        with open(cnf_path, "rb") as fh:
            for raw in iter_lines(fh):
                first = raw[:1]
                if first == b"p" and header is None:
                    header = raw.decode()
                elif first == b"c" and raw[:2] == b"cv" and raw[2:3] in (b" ", b"\n", b"\r", b""):
                    cv_lines.append(raw.decode())
        return cls.from_lines(header, cv_lines, names)


    def check_numbering(self, expected: Sequence[str]) -> None:
        """Exige que la variable SAT ``i`` sea la ``i``-ésima variable PB de ``expected`` (§4.1, punto 5).

        Naps numera las variables PB por orden de primera aparición en el archivo
        que lee (``PbSolver::exportVar``; comprobado en 40 instancias, §1.1 V2).
        :func:`first_appearance_order` deriva ese orden de forma independiente, así
        que cualquier permutación de ``cv`` se detecta aunque sea biunívoca y las
        variables permutadas tengan el mismo valor en x* (el caso que las
        comprobaciones semánticas no ven en instancias sin cláusulas, p. ej. OGR_5).
        """
        if len(expected) != self.num_pb_vars:
            raise NapBackError(f"el archivo nombra {len(expected)} variables y 'cv' mapea {self.num_pb_vars}")
        for sat, name in enumerate(expected, start=1):
            if self.name_of[sat] != name:
                raise NapBackError(
                    f"'cv' asigna la variable SAT {sat} a {self.name_of[sat]}, pero la {sat}-ésima variable del "
                    f"archivo es {name}: la numeración de Naps no es la esperada"
                )


def first_appearance_order(path: str) -> List[str]:
    """Nombres ``x<k>`` en el orden en que aparecen por primera vez (sin comentarios ``*``)."""
    token = re.compile(rb"x(\d+)")
    order: List[str] = []
    seen: Set[bytes] = set()
    with open(path, "rb") as fh:
        for line in iter_lines(fh):
            if line.lstrip().startswith(b"*"):
                continue
            for k in token.findall(line):
                if k not in seen:
                    seen.add(k)
                    order.append("x" + k.decode())
    return order


def _first(items: Set[str]) -> str:
    return sorted(items, key=lambda n: (len(n), n))[0]


def instance_names(indices: Iterable[int]) -> List[str]:
    """Nombres ``x<k>`` de los índices de ``scan_opb`` (los ``VarId`` de NapBack y RoundingBack)."""
    return [f"x{k}" for k in indices]


# ---------------------------------------------------------------------- instancia de decisión
def write_decision_instance(original: str, optimum: float, out_path: str) -> str:
    """Escribe ``restricciones originales + (objetivo = z*)`` sin línea ``min:``.

    ``naps -cnf`` no codifica el objetivo (§1.1 V4), así que la CNF de una
    instancia de optimización se obtiene de esta instancia de decisión. Los
    coeficientes se copian como texto (enteros arbitrarios); ``#constraint=`` de
    la cabecera se incrementa en uno si existe.
    """
    if optimum != int(optimum):
        raise NapBackError(f"NapBack requiere un óptimo entero; recibido {optimum}")
    target = int(optimum)
    found = False
    pending: List[str] = []  # sentencia min: repartida en varias líneas
    with open_text(original) as src, open(out_path, "w", encoding="utf-8") as out:
        for number, line in enumerate(iter_lines(src)):
            stripped = line.lstrip()
            if pending:
                pending.append(line)
                if ";" in line:
                    out.write(_objective_constraint("".join(pending), target))
                    pending = []
                continue
            if stripped.startswith("*"):
                if number == 0:  # cabecera "* #variable= N #constraint= M"
                    line = re.sub(r"#constraint= *(\d+)", lambda m: f"#constraint= {int(m.group(1)) + 1}", line, count=1)
                out.write(line)
            elif stripped.startswith("max:"):
                raise NapBackError("NapBack no admite objetivos 'max:'; normalice la instancia a 'min:'")
            elif stripped.startswith("min:"):
                if found:
                    raise NapBackError("la instancia tiene más de una línea 'min:'")
                found = True
                if ";" in line:
                    out.write(_objective_constraint(line, target))
                else:
                    pending.append(line)
            else:
                out.write(line)
    if pending:
        raise NapBackError("la sentencia 'min:' no termina en ';'")
    if not found:
        raise NapBackError("la instancia no tiene función objetivo: se traduce tal cual, sin instancia de decisión")
    return out_path


def _objective_constraint(statement: str, target: int) -> str:
    body = statement.split(";", 1)
    terms = body[0].strip()[len("min:"):].strip()
    if body[1].strip():
        raise NapBackError(f"texto inesperado tras el objetivo: {body[1].strip()[:40]!r}")
    if not terms:
        if target != 0:
            raise NapBackError(f"objetivo vacío con z* = {target}")
        return ""
    return f"{terms} = {target} ;\n"


# ---------------------------------------------------------------------- propagación PB
class TranslationCancelled(NapBackError):
    """``should_stop`` pidió abandonar la preparación (STOP de ParaBack): no es un fallo."""


def _check(should_stop: Optional[Callable[[], bool]], step: int) -> None:
    if should_stop is not None and step % 4096 == 0 and should_stop():
        raise TranslationCancelled("preparación abandonada")


def forced_literals(instance: OPBInstance, optimum: Optional[int] = None, should_stop: Optional[Callable[[], bool]] = None) -> Dict[str, bool]:
    """Literales que la propagación unitaria PB fuerza (con ``objetivo = z*`` si se da ``optimum``).

    Propagación por holgura hasta punto fijo, con enteros exactos. Es la parte
    barata de la equisatisfacibilidad que se puede comprobar sin un solver: la
    CNF tiene que implicar cada uno de estos literales (§4.2, punto 5). Lanza
    :class:`NapBackError` si la propagación ya demuestra que no hay solución.
    """
    rows: List[Tuple[List[Tuple[int, int, bool]], int]] = []
    statements = [(c.terms, c.op, c.rhs) for c in instance.constraints]
    if optimum is not None and instance.objective is not None:
        statements.append((instance.objective, "=", int(optimum)))
    for number, (terms, op, rhs) in enumerate(statements):
        _check(should_stop, number)
        for sign in {">=": (1,), "<=": (-1,), "=": (1, -1)}[op]:
            lits: List[Tuple[int, int, bool]] = []
            bound = sign * rhs
            for coef, index in terms:
                a = sign * coef
                if a >= 0:
                    lits.append((a, index, True))
                else:  # a·x = -|a| + |a|·¬x
                    lits.append((-a, index, False))
                    bound -= a
            rows.append((lits, bound))
    watch: Dict[int, List[int]] = {}
    for r, (lits, _) in enumerate(rows):
        for _, index, _ in lits:
            watch.setdefault(index, []).append(r)
    value: Dict[int, bool] = {}
    queue = list(range(len(rows)))
    queued = set(queue)
    steps = 0
    while queue:
        steps += 1
        _check(should_stop, steps)
        r = queue.pop()
        queued.discard(r)
        lits, bound = rows[r]
        slack = sum(a for a, index, positive in lits if value.get(index, positive) == positive) - bound
        if slack < 0:
            raise PropagationConflict("la propagación unitaria PB demuestra que la instancia (con z*) no tiene solución")
        for a, index, positive in lits:
            if a > slack and index not in value:
                value[index] = positive
                for other in watch[index]:
                    if other not in queued:
                        queued.add(other)
                        queue.append(other)
    return {instance.names[i]: v for i, v in value.items()}


def pb_clauses(
    instance: OPBInstance, limit: Optional[int] = None, should_stop: Optional[Callable[[], bool]] = None
) -> List[List[Tuple[str, bool]]]:
    """Restricciones PB que son cláusulas (basta un literal cierto), como listas ``(nombre, valor)``.

    ``Σ aᵢ·lᵢ ≥ b`` (normalizada a coeficientes positivos) es una cláusula si
    ``b ≥ 1`` y todo ``aᵢ ≥ b``. La CNF tiene que implicar cada una (§4.2,
    punto 5b): con un mapa ``cv`` que intercambie dos variables, alguna deja de
    estarlo aunque ambas tengan el mismo valor en x* y no estén forzadas, que es
    justo el caso que no detectan ni la verificación de x* ni la de literales
    forzados. Con ``limit`` se eligen primero las que cubren variables aún no
    cubiertas, para repartir la comprobación entre todas las variables.
    """
    clauses: List[List[Tuple[str, bool]]] = []
    seen: Set[Tuple[Tuple[str, bool], ...]] = set()
    for number, c in enumerate(instance.constraints):
        _check(should_stop, number)
        for sign in {">=": (1,), "<=": (-1,), "=": (1, -1)}[c.op]:
            lits: List[Tuple[int, str, bool]] = []
            bound = sign * c.rhs
            for coef, index in c.terms:
                a = sign * coef
                if a == 0:
                    continue
                if a > 0:
                    lits.append((a, instance.names[index], True))
                else:
                    lits.append((-a, instance.names[index], False))
                    bound -= a
            if bound >= 1 and lits and all(a >= bound for a, _, _ in lits):
                key = tuple(sorted((name, value) for _, name, value in lits))
                if key not in seen:
                    seen.add(key)
                    clauses.append(list(key))
    if limit is None or len(clauses) <= limit:
        return clauses
    chosen: List[List[Tuple[str, bool]]] = []
    covered: Set[str] = set()
    rest: List[List[Tuple[str, bool]]] = []
    for clause in clauses:
        if any(name not in covered for name, _ in clause):
            chosen.append(clause)
            covered.update(name for name, _ in clause)
        else:
            rest.append(clause)
    return (chosen + rest)[:limit]


# ---------------------------------------------------------------------- traducción
@dataclass
class Translation:
    """Resultado de :func:`translate`."""

    cnf_path: Optional[str]
    varmap: Optional[VarMap]
    #: ``True`` si Naps demostró UNSAT durante la traducción y no escribió CNF.
    trivially_unsat: bool
    seconds: float
    exporter: str
    #: Pico de nodos de fórmula que Naps informa (``FEnv nodes peak``), si lo informa.
    fenv_nodes: Optional[int] = None


def translate(
    instance: str,
    cnf_path: str,
    names: Iterable[str],
    *,
    naps: Optional[str] = None,
    exporter: str = "glueminisat",
    extra_options: Sequence[str] = (),
    timeout: Optional[float] = None,
    on_start: Optional[Callable[["subprocess.Popen[str]"], None]] = None,
    memory_limit_mb: Optional[float] = None,
    max_nodes: Optional[int] = None,
) -> Translation:
    """Traduce ``instance`` a CNF con Naps y verifica el mapa ``cv``.

    ``names`` son las variables que el mapa debe cubrir exactamente. Si Naps
    demuestra la insatisfacibilidad al traducir no escribe CNF: se devuelve
    ``trivially_unsat=True`` y es el llamador quien decide si eso es
    "infactible" (instancia de factibilidad) o un error (instancia de decisión
    con un z* que debería ser alcanzable). ``on_start`` recibe el proceso de
    Naps en cuanto arranca, para que el backend pueda matarlo desde otro hilo
    (``interrupt``). ``max_nodes`` fija el tope de nodos de Naps (``-max-nodes``); sin él, el que
    sale de ``memory_limit_mb`` (:func:`naps_max_nodes`).
    """
    if exporter not in EXPORTERS:
        raise ValueError(f"exportador desconocido {exporter!r}; use uno de {sorted(EXPORTERS)}")
    for option in extra_options:
        if option.split("=", 1)[0] in FORBIDDEN_NAPS_OPTIONS or "scip" in option.lower():
            raise NapBackError(f"opción de Naps no admitida en NapBack: {option!r}")
    binary = naps or find_naps()
    if binary is None:
        raise NapBackError(f"ejecutable de Naps no encontrado (compile con `make napback` o indique {NAPS_BIN_ENV})")
    if os.path.exists(cnf_path):
        os.remove(cnf_path)  # nunca leer una CNF de una ejecución anterior
    if max_nodes is None:
        max_nodes = naps_max_nodes(memory_limit_mb)
    argv = [binary, f"-cnf={cnf_path}", *EXPORTERS[exporter], *extra_options]
    argv += ([f"-max-nodes={max_nodes}"] if max_nodes is not None else []) + [instance]
    start = time.perf_counter()
    proc = popen_die_with_parent(argv, memory_limit_mb, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if on_start is not None:
        on_start(proc)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise NapBackError(f"Naps no terminó la traducción en {timeout} s") from None
    seconds = time.perf_counter() - start
    peak = _FENV_PEAK.search(output)
    fenv_nodes = int(peak.group(1)) if peak else None
    if _ABORTED_UNSAT in output:
        return Translation(None, None, True, seconds, exporter, fenv_nodes)
    if proc.returncode != 0 or not os.path.exists(cnf_path):
        tail = "\n".join(output.splitlines()[-8:])
        if proc.returncode == NAPS_SELF_STOP_EXIT:
            limit = "su límite de nodos" if "FEnv nodes overflow" in output else "un límite interno"
            raise NapsCannotTranslate(f"Naps se detuvo por {limit} sin escribir la CNF (código {proc.returncode}, "
                                      f"-max-nodes={max_nodes or NAPS_MAX_NODES}):\n{tail}")
        error = NapsCannotTranslate if is_crash_exit(proc.returncode, output) else NapBackError
        raise error(f"Naps terminó con código {proc.returncode} sin escribir la CNF:\n{tail}")
    varmap = VarMap.from_cnf(cnf_path, names)
    varmap.check_numbering(first_appearance_order(instance))
    return Translation(cnf_path, varmap, False, seconds, exporter, fenv_nodes)


# ---------------------------------------------------------------------- comprobación 5
def check_nothing_lost(
    cnf_path: str,
    varmap: VarMap,
    forced: Mapping[str, bool],
    *,
    cadical: Optional[str] = None,
    timeout: float = 300.0,
) -> None:
    """Comprueba que la CNF implica todos los literales ``forced`` (§4.2, punto 5).

    Resuelve ``CNF ∧ (∨ ¬f)``, que tiene que ser UNSAT. La CNF se pasa por la
    entrada estándar con la cláusula añadida al final (``--force`` admite la
    cabecera con una cláusula de menos), así que no se copia en disco. Si sale
    SAT, el modelo dice qué literales se perdieron; si no hay respuesta en
    ``timeout``, la CNF tampoco se da por buena. En la Etapa 2 esta misma
    consulta la hará ``napback-server`` con ``constrain``.
    """
    unmapped = [name for name in forced if name not in varmap.sat_of]
    if unmapped:
        raise NapBackError(f"{len(unmapped)} variables forzadas no están en 'cv' (p. ej. {_first(set(unmapped))})")
    lits = [varmap.literal(name, value) for name, value in forced.items()]
    if not lits:
        return
    binary = cadical or find_cadical()
    if binary is None:
        raise NapBackError(f"ejecutable de CaDiCaL no encontrado (compile con `make napback` o indique {CADICAL_BIN_ENV})")
    proc = popen_die_with_parent(
        [binary, "-q", "--force", "-t", str(max(1, int(timeout))), "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdin is not None
    try:
        with open(cnf_path, "r", encoding="ascii") as fh:
            shutil.copyfileobj(fh, proc.stdin, 1 << 20)
        proc.stdin.write(" ".join(str(-lit) for lit in lits) + " 0\n")
        proc.stdin.close()
        output = proc.stdout.read() if proc.stdout is not None else ""
    finally:
        proc.wait()
    status = next((line[2:].strip() for line in output.splitlines() if line.startswith("s ")), "")
    if status == "UNSATISFIABLE":
        return
    if status == "SATISFIABLE":
        model = {int(t) for line in output.splitlines() if line.startswith("v ") for t in line.split()[1:]}
        lost = sorted((name for name, value in forced.items() if -varmap.literal(name, value) in model), key=lambda n: int(n[1:]))
        raise NapBackError(
            f"la CNF no implica los {len(lits)} literales que la propagación PB fuerza: admite un modelo que "
            f"viola {len(lost)} (p. ej. {', '.join(f'{n}={int(forced[n])}' for n in lost[:5])}); "
            "no es equisatisfacible con la instancia"
        )
    raise NapBackError(f"no se pudo comprobar que la CNF implica los literales forzados (CaDiCaL: {status or output[-300:]!r})")


__all__ = [
    "CADICAL_BIN_ENV",
    "EXPORTERS",
    "NAPS_BIN_ENV",
    "NAPS_TESTED_VERSION",
    "NapBackError",
    "PropagationConflict",
    "Translation",
    "TranslationCancelled",
    "VarMap",
    "check_nothing_lost",
    "find_cadical",
    "find_naps",
    "first_appearance_order",
    "forced_literals",
    "instance_names",
    "naps_version",
    "pb_clauses",
    "translate",
    "write_decision_instance",
]

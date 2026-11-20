"""Lector del formato OPB lineal normalizado (competición PB06) con representación compacta.

Es el único lector de instancias en Python: lo usan el backend de referencia (fuerza
bruta), la comprobación exacta de soluciones (:mod:`guroback.exact`), las comprobaciones
de NapBack (:mod:`guroback.napback_translation`) y los tests. Los solvers reales leen
``.opb`` con sus propios lectores nativos. Admite ``.opb.gz`` y ``.opb.xz``.

Gramática soportada::

    * comentario
    min: +1 x1 +2 x2 ;
    +1 x1 +1 x2 >= 1 ;
    +1 x1 -1 x3 = 0 ;

Los términos no lineales (productos de literales) y los literales negados no están soportados.

Memoria
-------

En instancias enormes (pebbling: decenas de millones de términos) la lectura en Python
llegó a costar más que el propio solver. Por eso:

* :func:`parse_opb` lee por flujo, sentencia a sentencia, sin cargar el archivo entero
  en una cadena ni partirlo de una vez;
* todos los términos de todas las restricciones viven en tres arrays planos
  (coeficientes, índices de variable y desplazamiento de cada restricción): unos 12
  bytes por término en lugar de una tupla de tuplas de enteros Python (60–90 bytes);
* :attr:`OPBInstance.constraints` devuelve vistas (:class:`PBConstraint` con
  :class:`Terms` sobre ``memoryview``) que no copian nada.

Un trabajador de ParaBack lee la instancia **una sola vez** y la comparte entre su
backend y su :class:`~guroback.exact.ExactChecker` (:meth:`SolverBackend.instance`).

:func:`scan_opb` es la pasada ligera (sin construir la instancia) que usan RoundingSat y
NapBack para numerar sus variables; :func:`plain_copy` materializa una copia descomprimida
para los solvers nativos que no leen ``.gz``/``.xz``.
"""
from __future__ import annotations

import gzip
import lzma
import os
import re
import tempfile
from array import array
from dataclasses import dataclass, field
from typing import IO, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, TypeVar, Union, overload

Term = Tuple[int, int]  # (coeficiente, índice de variable)

_TOKEN = re.compile(r"[+-]?\d+|>=|<=|=|~?x\d+|min:|max:")
_VAR_TOKEN = re.compile(r"x(\d+)")
_INT64 = (-(2**63), 2**63 - 1)

#: Operadores de restricción, por código (``OPBInstance._ops``).
OPERATORS = (">=", "<=", "=")
_OP_CODE = {op: code for code, op in enumerate(OPERATORS)}

#: Coeficientes: ``array('q')`` mientras quepan en 64 bits; si no, lista de enteros Python.
Coefficients = Union["array[int]", List[int]]


class Terms(Sequence[Term]):
    """Secuencia de términos ``(coeficiente, índice)`` sobre dos vistas, sin copiar.

    Se compara igual que una tupla de pares, para que el código y los tests que construían
    ``tuple(terms)`` sigan funcionando.
    """

    __slots__ = ("coefs", "indices")

    def __init__(self, coefs: Sequence[int], indices: Sequence[int]) -> None:
        self.coefs = coefs
        self.indices = indices

    def __len__(self) -> int:
        return len(self.indices)

    def __iter__(self) -> Iterator[Term]:
        return zip(self.coefs, self.indices)

    @overload
    def __getitem__(self, i: int) -> Term: ...

    @overload
    def __getitem__(self, i: slice) -> List[Term]: ...

    def __getitem__(self, i: Union[int, slice]) -> Union[Term, List[Term]]:
        if isinstance(i, slice):
            return list(zip(self.coefs[i], self.indices[i]))
        return (self.coefs[i], self.indices[i])

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(a == tuple(b) for a, b in zip(self, other))

    def __hash__(self) -> int:
        return hash(tuple(self))

    def __repr__(self) -> str:
        return f"Terms({list(self)!r})"


@dataclass(frozen=True)
class PBConstraint:
    terms: Sequence[Term]
    op: str  # ">=", "<=" o "="
    rhs: int

    def lhs(self, assignment: Sequence[int]) -> int:
        return sum(coef for coef, idx in self.terms if assignment[idx])

    def is_satisfied(self, assignment: Sequence[int]) -> bool:
        return holds(self.lhs(assignment), self.op, self.rhs)


def holds(lhs: int, op: str, rhs: int) -> bool:
    """``lhs op rhs`` para los tres operadores de OPB."""
    if op == ">=":
        return lhs >= rhs
    if op == "<=":
        return lhs <= rhs
    return lhs == rhs


class _ConstraintView(Sequence[PBConstraint]):
    """``OPBInstance.constraints``: cada elemento es una vista de los arrays planos."""

    __slots__ = ("_instance",)

    def __init__(self, instance: "OPBInstance") -> None:
        self._instance = instance

    def __len__(self) -> int:
        return len(self._instance._rhs)

    @overload
    def __getitem__(self, c: int) -> PBConstraint: ...

    @overload
    def __getitem__(self, c: slice) -> List[PBConstraint]: ...

    def __getitem__(self, c: Union[int, slice]) -> Union[PBConstraint, List[PBConstraint]]:
        if isinstance(c, slice):
            return [self[k] for k in range(*c.indices(len(self)))]
        inst = self._instance
        if c < 0:
            c += len(self)
        if not 0 <= c < len(self):
            raise IndexError(c)
        return PBConstraint(inst.terms_of(c), OPERATORS[inst._ops[c]], inst._rhs[c])

    def __iter__(self) -> Iterator[PBConstraint]:
        inst = self._instance
        for c in range(len(inst._rhs)):
            yield PBConstraint(inst.terms_of(c), OPERATORS[inst._ops[c]], inst._rhs[c])


@dataclass
class OPBInstance:
    """Instancia OPB: nombres de variables, objetivo y restricciones en arrays planos."""

    names: List[str] = field(default_factory=list)
    objective: Optional[Terms] = None
    sense: str = "min"
    #: Términos de todas las restricciones, una tras otra.
    _coefs: Coefficients = field(default_factory=lambda: array("q"))
    _indices: "array[int]" = field(default_factory=lambda: array("i"))
    #: ``_start[c]:_start[c + 1]`` son los términos de la restricción ``c``.
    _start: "array[int]" = field(default_factory=lambda: array("q", [0]))
    _ops: bytearray = field(default_factory=bytearray)
    _rhs: List[int] = field(default_factory=list)

    @property
    def num_vars(self) -> int:
        return len(self.names)

    @property
    def num_terms(self) -> int:
        return len(self._indices)

    @property
    def constraints(self) -> Sequence[PBConstraint]:
        return _ConstraintView(self)

    def terms_of(self, c: int) -> Terms:
        """Términos de la restricción ``c`` (vista sin copia)."""
        a, b = self._start[c], self._start[c + 1]
        coefs = self._coefs
        view_coefs = memoryview(coefs)[a:b] if isinstance(coefs, array) else coefs[a:b]
        return Terms(view_coefs, memoryview(self._indices)[a:b])

    def operator(self, c: int) -> str:
        return OPERATORS[self._ops[c]]

    def rhs(self, c: int) -> int:
        return self._rhs[c]

    def objective_value(self, assignment: Sequence[int]) -> int:
        if self.objective is None:
            return 0
        return sum(coef for coef, idx in self.objective if assignment[idx])

    # ------------------------------------------------------------------ construcción
    def _add_constraint(self, coefs: List[int], indices: List[int], op: str, rhs: int) -> None:
        if isinstance(self._coefs, array) and any(not _INT64[0] <= k <= _INT64[1] for k in coefs):
            self._coefs = list(self._coefs)  # coeficientes de más de 64 bits: enteros Python
        self._coefs.extend(coefs)
        self._indices.extend(indices)
        self._start.append(len(self._indices))
        self._ops.append(_OP_CODE[op])
        self._rhs.append(rhs)


def open_text(path: str) -> IO[str]:
    """Abre un ``.opb``, ``.opb.gz`` u ``.opb.xz`` en modo texto."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    if path.endswith(".xz"):
        return lzma.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


#: Tamaño de los bloques de lectura de :func:`iter_lines`.
READ_BLOCK = 1 << 20

AnyStr_ = TypeVar("AnyStr_", str, bytes)


def iter_lines(fh: IO[AnyStr_], block: int = READ_BLOCK) -> Iterator[AnyStr_]:
    """Las líneas de ``fh`` (con su fin de línea), leídas en bloques de ``block``.

    Iterar un archivo línea a línea lee del disco en trozos de pocos KB y en cada uno suelta
    y recupera el GIL: tan seguido que otro hilo que lo espera (el de escucha de un
    trabajador de ParaBack, que tiene que poder interrumpir) no llega a obtenerlo y tarda
    segundos en atender un STOP. Con bloques grandes el hilo que lee cede el GIL con
    normalidad entre lectura y lectura.
    """
    empty = fh.read(0)  # "" o b"" según el modo del archivo
    newline = "\n" if isinstance(empty, str) else b"\n"
    pending: List[AnyStr_] = []  # trozos de una línea que aún no ha terminado
    while True:
        chunk = fh.read(block)
        if not chunk:
            break
        if newline not in chunk:  # type: ignore[operator]
            pending.append(chunk)  # una línea más larga que un bloque: se une una sola vez
            continue
        if pending:
            pending.append(chunk)
            chunk = empty.join(pending)  # type: ignore[assignment]
            pending = []
        lines = chunk.split(newline)  # type: ignore[arg-type]
        last = lines.pop()  # lo que sigue al último salto de línea: una línea sin terminar
        pending = [last] if last else []
        for line in lines:
            yield line + newline  # type: ignore[operator]
    tail = empty.join(pending)  # type: ignore[arg-type]
    if tail:
        yield tail


class ParseCancelled(Exception):
    """``should_stop`` pidió abandonar la lectura (p. ej. llegó STOP a un trabajador de ParaBack)."""


#: Cada cuántas sentencias se consulta ``should_stop``.
_CHECK_EVERY = 4096


def _statements(fh: IO[str]) -> Iterator[str]:
    """Sentencias terminadas en ``;`` (sin comentarios), leyendo línea a línea."""
    pending: List[str] = []
    for line in iter_lines(fh):
        if line.lstrip().startswith("*"):
            continue
        while ";" in line:
            head, _, line = line.partition(";")
            pending.append(head)
            yield "".join(pending)
            pending = []
        pending.append(line)
    tail = "".join(pending)
    if tail.strip():
        yield tail


def parse_opb(path: str, should_stop: Optional[Callable[[], bool]] = None) -> OPBInstance:
    """Lee un archivo OPB lineal y devuelve su representación compacta.

    Las variables se numeran en orden de primera aparición; si faltan índices ``x1..xmax``
    se añaden al final (``scan_opb``, en cambio, sólo cuenta las que aparecen). Con
    ``should_stop`` la lectura se puede abandonar (:class:`ParseCancelled`): en instancias
    grandes tarda segundos y es Python puro, sin otro punto de interrupción.
    """
    instance = OPBInstance()
    index: Dict[str, int] = {}

    def var_index(name: str) -> int:
        k = index.get(name)
        if k is None:
            k = index[name] = len(instance.names)
            instance.names.append(name)
        return k

    with open_text(path) as fh:
        for number, statement in enumerate(_statements(fh)):
            if should_stop is not None and number % _CHECK_EVERY == 0 and should_stop():
                raise ParseCancelled(path)
            tokens = _TOKEN.findall(statement)
            if not tokens:
                continue
            sense = None
            if tokens[0] in ("min:", "max:"):
                sense = tokens[0][:-1]
                tokens = tokens[1:]

            coefs: List[int] = []
            indices: List[int] = []
            op: Optional[str] = None
            rhs: Optional[int] = None
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                if tok in (">=", "<=", "="):
                    op = tok
                    rhs = int(tokens[i + 1])
                    i += 2
                    continue
                name = tokens[i + 1]
                if name.startswith("~"):
                    raise NotImplementedError("literales negados (~x) no soportados")
                if i + 2 < len(tokens) and tokens[i + 2].startswith("x"):
                    raise NotImplementedError("términos no lineales no soportados")
                coefs.append(int(tok))
                indices.append(var_index(name))
                i += 2

            if sense is not None:
                instance.sense = sense
                packed: Coefficients = array("q", coefs) if all(_INT64[0] <= k <= _INT64[1] for k in coefs) else coefs
                instance.objective = Terms(packed, array("i", indices))
            else:
                if op is None or rhs is None:
                    raise ValueError(f"restricción sin operador: {statement.strip()!r}")
                instance._add_constraint(coefs, indices, op, rhs)

    # Las variables se numeran x1..xn; garantizamos que todas existan aunque no aparezcan.
    numbered = sorted(int(n[1:]) for n in instance.names)
    if numbered and numbered[-1] > len(instance.names):
        for k in range(1, numbered[-1] + 1):
            var_index(f"x{k}")
    return instance


# ---------------------------------------------------------------------------- pasada ligera
#: Cabecera que RoundingSat exige en la primera línea.
HEADER_PREFIX = "* #variable= "


@dataclass(frozen=True)
class OPBScan:
    """Lo que se sabe de una instancia sin construirla."""

    has_objective: bool
    #: Índices ``k`` de las variables ``x<k>`` presentes, ordenados.
    indices: List[int]
    #: ``True`` si la primera línea ya es ``* #variable= N #constraint= M``.
    header_ok: bool
    #: Restricciones (sentencias ``;`` que no son el objetivo).
    num_constraints: int


def scan(path: str) -> OPBScan:
    """Una pasada por el texto: objetivo, variables presentes, cabecera y número de restricciones.

    Rechaza ``max:`` (RoundingSat y NapBack sólo entienden ``min:``).
    """
    has_objective = False
    indices: Set[int] = set()
    header_ok = False
    first = True
    statements = 0
    with open_text(path) as fh:
        for line in iter_lines(fh):
            if first:
                header_ok = line.startswith(HEADER_PREFIX)
                first = False
            stripped = line.lstrip()
            if not stripped or stripped.startswith("*"):
                continue
            if stripped.startswith("max:"):
                raise NotImplementedError("RoundingSat no admite objetivos 'max:'; normalice la instancia a 'min:'")
            if stripped.startswith("min:"):
                has_objective = True
            statements += stripped.count(";")
            indices.update(int(k) for k in _VAR_TOKEN.findall(stripped))
    return OPBScan(has_objective, sorted(indices), header_ok, statements - (1 if has_objective else 0))


_INTSIZE = re.compile(r"intsize=\s*(\d+)")


def max_int_bits(path: str) -> int:
    """Bits del mayor entero de la instancia: ``intsize=`` de la cabecera o, si no la hay, una pasada por el archivo."""
    biggest = 0
    with open_text(path) as fh:
        for number, line in enumerate(iter_lines(fh)):
            stripped = line.lstrip()
            if number == 0 and stripped.startswith("*"):
                found = _INTSIZE.search(stripped)
                if found:
                    return int(found.group(1))
            if not stripped or stripped.startswith("*"):
                continue
            for token in _TOKEN.findall(stripped):
                if token[0] in "+-0123456789":
                    value = abs(int(token))
                    if value > biggest:
                        biggest = value
    return biggest.bit_length()


def scan_opb(path: str) -> Tuple[bool, List[int], bool]:
    """``(tiene objetivo, índices k de las variables x<k> presentes, cabecera válida)`` (ver :func:`scan`)."""
    result = scan(path)
    return result.has_objective, result.indices, result.header_ok


def plain_copy(path: str, directory: Optional[str] = None, prefix: str = "guroback_", header: Optional[str] = None) -> str:
    """Copia temporal descomprimida de ``path`` (``.opb``/``.gz``/``.xz``); devuelve su ruta.

    Con ``header`` la primera línea es esa y se omite cualquier línea de cabecera
    ``* #variable=`` del original (RoundingSat exige la suya en la primera línea). Quien
    la pide la borra.
    """
    fd, tmp_path = tempfile.mkstemp(prefix=prefix, suffix=".opb", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out, open_text(path) as src:
            if header is not None:
                out.write(header.rstrip("\n") + "\n")
                for line in src:
                    if not line.startswith(HEADER_PREFIX):
                        out.write(line)
            else:
                for line in src:
                    out.write(line)
    except BaseException:
        os.unlink(tmp_path)
        raise
    return tmp_path


__all__ = [
    "HEADER_PREFIX",
    "OPBInstance",
    "OPBScan",
    "OPERATORS",
    "PBConstraint",
    "ParseCancelled",
    "Terms",
    "holds",
    "iter_lines",
    "open_text",
    "parse_opb",
    "plain_copy",
    "scan",
    "scan_opb",
]

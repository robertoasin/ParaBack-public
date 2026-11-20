"""Rasgos sintácticos de una instancia OPB para estimar el tamaño de su traducción a CNF.

Base de la regla de compatibilidad de Naps (``docs/PLAN_EVALUACION.md`` D11): Naps no traduce
algunas instancias (en la Ronda 1, 15 abortos por memoria con 13,5 GB y 3 límites internos) y en
otras la traducción se come el tiempo. La regla se decide antes de lanzar, sólo con la instancia
(P4), así que necesita una estimación barata del tamaño de la CNF. Cada restricción se normaliza a
``Σ cᵢ·lᵢ ≥ b`` con coeficientes positivos (una igualdad son dos desigualdades), se saturan los
coeficientes a ``b`` y se clasifica:

* **cláusula** (todo coeficiente saturado vale ``b``): cuesta 1;
* **cardinalidad** (coeficientes iguales, cota ``k = ⌈b/c⌉ > 1`` sobre ``n`` literales): cuesta
  ``n·min(k, n−k+1)``, del orden de una red de ordenación;
* **PB general**: cuesta ``n·bits(max cᵢ)``, del orden de un sumador;
* **trivial** (siempre cierta, ``b ≤ 0``): no cuesta.

El objetivo cuesta ``n·bits(max |cᵢ|)``: Naps recibe ``objetivo ≤ z*`` como una restricción más
(``docs/PLAN_NAPBACK.md`` §3.2) y suele ser la mayor. :attr:`OPBFeatures.size_estimate` suma todo.

La lectura es por flujo (:func:`guroback.opb.open_text`): no construye la instancia y sirve para
las enormes. Los literales negados y los términos no lineales no se admiten, como en el lector.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, fields
from typing import Dict, List, Optional, Tuple

from guroback.opb import _TOKEN, _statements, open_text


@dataclass
class OPBFeatures:
    vars: int = 0                  # variables distintas que aparecen
    constraints: int = 0           # restricciones del archivo (una igualdad cuenta una vez)
    equalities: int = 0
    terms: int = 0                 # términos de las restricciones (sin el objetivo)
    max_coef_bits: int = 0         # bits del mayor coeficiente o lado derecho (con el objetivo)
    clauses: int = 0               # tras normalizar: una igualdad aporta dos desigualdades
    clause_literals: int = 0
    cardinalities: int = 0
    cardinality_terms: int = 0
    cardinality_cost: int = 0
    general: int = 0
    general_terms: int = 0
    general_max_terms: int = 0
    general_max_bits: int = 0
    general_cost: int = 0
    trivial: int = 0
    objective_terms: int = 0
    objective_max_bits: int = 0
    objective_cost: int = 0
    parse_seconds: float = 0.0

    @property
    def size_estimate(self) -> int:
        """Tamaño estimado de la CNF de Naps (unidades: cláusulas de una restricción pequeña)."""
        return self.clauses + self.cardinality_cost + self.general_cost + self.objective_cost

    def as_row(self) -> Dict[str, object]:
        row: Dict[str, object] = asdict(self)
        row["parse_seconds"] = round(self.parse_seconds, 3)
        row["size_estimate"] = self.size_estimate
        return row


#: Columnas de :meth:`OPBFeatures.as_row`, en orden.
COLUMNS: List[str] = [f.name for f in fields(OPBFeatures)] + ["size_estimate"]


def _classify(features: OPBFeatures, coefs: List[int], rhs: int) -> None:
    """Añade a ``features`` la desigualdad ``Σ coefs·x ≥ rhs`` normalizada y clasificada."""
    rhs += sum(-c for c in coefs if c < 0)    # c·x con c < 0 es c + |c|·¬x
    mags = [abs(c) for c in coefs if c]
    if rhs <= 0:
        features.trivial += 1
        return
    sat = [min(c, rhs) for c in mags]
    n = len(sat)
    if all(c == rhs for c in sat):
        features.clauses += 1
        features.clause_literals += n
        return
    first = sat[0]
    if all(c == first for c in sat):
        k = -(-rhs // first)
        if k <= 1:
            features.clauses += 1
            features.clause_literals += n
            return
        features.cardinalities += 1
        features.cardinality_terms += n
        features.cardinality_cost += n * max(1, min(k, n - k + 1))
        return
    bits = max(sat).bit_length()
    features.general += 1
    features.general_terms += n
    features.general_max_terms = max(features.general_max_terms, n)
    features.general_max_bits = max(features.general_max_bits, bits)
    features.general_cost += n * bits


def _terms(tokens: List[str]) -> Tuple[List[int], List[str], str, int]:
    coefs: List[int] = []
    names: List[str] = []
    op, rhs = "", 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in (">=", "<=", "="):
            op, rhs = tok, int(tokens[i + 1])
            i += 2
            continue
        name = tokens[i + 1]
        if name.startswith("~"):
            raise NotImplementedError("literales negados (~x) no soportados")
        if i + 2 < len(tokens) and tokens[i + 2].startswith("x"):
            raise NotImplementedError("términos no lineales no soportados")
        coefs.append(int(tok))
        names.append(name)
        i += 2
    return coefs, names, op, rhs


def opb_features(path: str) -> OPBFeatures:
    """Los rasgos de la instancia ``path`` (``.opb``, ``.opb.gz`` u ``.opb.xz``)."""
    start = time.perf_counter()
    f = OPBFeatures()
    seen: set = set()
    with open_text(path) as fh:
        for statement in _statements(fh):
            tokens = _TOKEN.findall(statement)
            if not tokens:
                continue
            objective = tokens[0] in ("min:", "max:")
            coefs, names, op, rhs = _terms(tokens[1:] if objective else tokens)
            seen.update(names)
            top = max((abs(c) for c in coefs), default=0)
            f.max_coef_bits = max(f.max_coef_bits, top.bit_length(), abs(rhs).bit_length())
            if objective:
                f.objective_terms = len(coefs)
                f.objective_max_bits = top.bit_length()
                f.objective_cost = len(coefs) * top.bit_length()
                continue
            if not op:
                raise ValueError(f"restricción sin operador: {statement.strip()[:80]!r}")
            f.constraints += 1
            f.terms += len(coefs)
            if op in (">=", "="):
                _classify(f, coefs, rhs)
            if op in ("<=", "="):
                _classify(f, [-c for c in coefs], -rhs)
            if op == "=":
                f.equalities += 1
    f.vars = len(seen)
    f.parse_seconds = time.perf_counter() - start
    return f


def features_row(path: str) -> Tuple[str, Optional[Dict[str, object]], Optional[str]]:
    """``(ruta, fila, error)`` de una instancia, para un ``multiprocessing.Pool``.

    Vive aquí y no en ``tools/opb_features.py`` porque los hijos de un ``Pool`` tienen que poder
    importarla: con ``forkserver``, el método por defecto en Linux desde Python 3.14, un script
    cargado con otro nombre (como en los tests) no se puede importar y el ``Pool`` se queda
    esperando para siempre. Un error en una instancia no detiene a las demás.
    """
    try:
        return path, opb_features(path).as_row(), None
    except Exception as exc:  # noqa: BLE001 - se informa y se sigue con las demás
        return path, None, f"{type(exc).__name__}: {exc}"

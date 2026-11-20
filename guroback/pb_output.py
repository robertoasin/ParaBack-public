"""Lector de la salida de los solvers de la competición PB (``o``, ``s``, ``v``).

La escriben RoundingSat (``--print-sol=1``) y Naps como optimizadores, y es el formato
de las soluciones base del usuario (``--base-solution``)::

    o 2
    s OPTIMUM FOUND
    v x1 -x2 -x3 ...      (una o varias líneas "v")

Antes había tres lectores (RoundingSat, NapBack y ``paraback/base_solution.py``) con
diferencias: el de RoundingSat se quedaba sólo con la última línea ``v``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

#: Estados ``s`` de la competición.
OPTIMUM_FOUND = "OPTIMUM FOUND"
SATISFIABLE = "SATISFIABLE"
UNSATISFIABLE = "UNSATISFIABLE"
UNKNOWN = "UNKNOWN"


class PBOutputError(ValueError):
    """Una línea ``o`` que no es un entero."""


@dataclass
class PBOutput:
    #: Último ``s`` (``None`` si no hay ninguno).
    status: Optional[str] = None
    #: Último ``o`` (el mejor coste encontrado).
    objective: Optional[int] = None
    #: Asignación de todas las líneas ``v``, por nombre (``x12``).
    values: Dict[str, bool] = field(default_factory=dict)
    #: Literales vacíos o repetidos en las líneas ``v`` (una salida mal formada).
    repeated: List[str] = field(default_factory=list)
    #: Líneas ``Error: …`` (RoundingSat las escribe antes de abortar).
    errors: List[str] = field(default_factory=list)


def parse_pb_output(lines: Iterable[str]) -> PBOutput:
    out = PBOutput()
    for line in lines:
        if line.startswith("s "):
            out.status = line[2:].strip()
        elif line.startswith("o "):
            try:
                out.objective = int(line.split()[1])
            except (IndexError, ValueError):
                raise PBOutputError(f"línea 'o' no válida: {line.strip()!r}") from None
        elif line.startswith("v ") or line.strip() == "v":
            for token in line.split()[1:]:
                name = token.lstrip("-")
                if not name or name in out.values:
                    out.repeated.append(token)
                    continue
                out.values[name] = not token.startswith("-")
        elif line.startswith("Error:"):
            out.errors.append(line.strip())
    return out


__all__ = ["OPTIMUM_FOUND", "PBOutput", "PBOutputError", "SATISFIABLE", "UNKNOWN", "UNSATISFIABLE", "parse_pb_output"]

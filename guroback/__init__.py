"""Guroback: extractor de backbones para optimización pseudo-booleana / MILP.

El paquete separa el algoritmo (:mod:`guroback.extractor`) de los solvers
mediante la interfaz :class:`guroback.backend.SolverBackend`. Los backends se
registran en :data:`BACKENDS` y se cargan de forma diferida para no exigir
todas las dependencias nativas a la vez.
"""
from __future__ import annotations

import importlib
from typing import Dict, Type

from .backend import ProbeScope, SolveResult, SolveStatus, SolverBackend, VarId
from .extractor import (
    BackboneExtractor,
    BackboneKnowledge,
    BackboneResult,
    ChunkPolicy,
    Coordinator,
    RunStatus,
    VariableOrder,
    VarStatus,
)

__version__ = "1.0.0"

#: Nombre de backend -> ``"módulo:Clase"``, resuelto de forma diferida.
BACKENDS: Dict[str, str] = {
    "gurobi": "guroback.gurobi_backend:GurobiBackend",
    "scip": "guroback.scip_backend:SCIPBackend",
    "scip-exact": "guroback.scip_backend:SCIPExactBackend",
    "roundingsat": "guroback.roundingsat_backend:RoundingSatBackend",
    "napback": "guroback.napback_backend:NapBackBackend",
    "bruteforce": "guroback.bruteforce_backend:BruteForceBackend",
}


def get_backend_class(name: str) -> Type[SolverBackend]:
    """Resuelve un nombre de backend a su clase, importando el módulo bajo demanda."""
    try:
        target = BACKENDS[name]
    except KeyError:
        raise ValueError(f"backend desconocido {name!r}; disponibles: {', '.join(sorted(BACKENDS))}") from None
    module_name, class_name = target.split(":")
    return getattr(importlib.import_module(module_name), class_name)


__all__ = [
    "BACKENDS",
    "BackboneExtractor",
    "BackboneKnowledge",
    "BackboneResult",
    "ChunkPolicy",
    "Coordinator",
    "RunStatus",
    "ProbeScope",
    "SolveResult",
    "SolveStatus",
    "SolverBackend",
    "VarId",
    "VarStatus",
    "VariableOrder",
    "get_backend_class",
]

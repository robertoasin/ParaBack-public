"""Línea de comandos compatible con el ejecutable C++ original.

::

    python -m guroback [--solver NAME] [Param=Value ...] <instanceFile> <backboneFile>
    python -m guroback --list-solvers

``--solver`` elige el backend registrado en :data:`guroback.BACKENDS`
(``gurobi``, ``scip``, ``roundingsat`` o ``bruteforce``); por defecto ``gurobi``. Los argumentos
``Param=Value`` se pasan al solver sin modificar, salvo
``GurobackOptimum=<valor>``, que fija el óptimo conocido de la instancia.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Dict, List, Optional, Sequence, TextIO

from . import BACKENDS, get_backend_class
from .extractor import BackboneExtractor
from .line_server import exit_on_sigterm

USAGE = (
    "Usage: guroback [--solver NAME] [Param=Value ...] <instanceFile> <backboneFile>\n"
    "       guroback --list-solvers"
)
OPTIMUM_PARAM = "GurobackOptimum"


class CLIError(Exception):
    pass


def parse_args(argv: Sequence[str]) -> Dict[str, object]:
    args: List[str] = list(argv)
    solver = "gurobi"
    i = 0
    while i < len(args):
        if args[i] == "--solver":
            if i + 1 >= len(args):
                raise CLIError("--solver requires a value")
            solver = args[i + 1]
            del args[i : i + 2]
        elif args[i].startswith("--solver="):
            solver = args[i].split("=", 1)[1]
            del args[i]
        else:
            i += 1
    if solver not in BACKENDS:
        raise CLIError(f"Unknown solver {solver!r}. Available: {', '.join(sorted(BACKENDS))}")
    if len(args) < 2:
        raise CLIError(USAGE)

    params: Dict[str, str] = {}
    optimum: Optional[float] = None
    for arg in args[:-2]:
        if "=" not in arg:
            raise CLIError(f"Invalid argument format: {arg}. Expected format: ParamName=Value.")
        name, value = arg.split("=", 1)
        if name == OPTIMUM_PARAM:
            optimum = float(value)
        else:
            params[name] = value
    return {"solver": solver, "params": params, "optimum": optimum, "instance": args[-2], "backbone": args[-1]}


def list_solvers(out: Optional[TextIO] = None) -> int:
    """Informa de qué backends registrados son utilizables en este entorno.

    Sirve para diagnosticar instalaciones parciales (``pip install -e ".[scip]"``
    sin Gurobi, o una licencia de Gurobi no válida) antes de lanzar una ejecución.
    """
    stream = sys.stdout if out is None else out
    for name in sorted(BACKENDS):
        try:
            backend_cls = get_backend_class(name)
            available, reason = backend_cls.is_available()
            caps = ",".join(k for k, v in backend_cls.capabilities().items() if v) or "-"
        except Exception as exc:  # noqa: BLE001 - el motivo exacto es la información útil
            available, reason, caps = False, f"{type(exc).__name__}: {exc}", "-"
        status = "disponible" if available else "NO DISPONIBLE  " + reason
        print(f"{name:12} {status}  [capacidades: {caps}]", file=stream)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--list-solvers" in args:
        return list_solvers()
    # --check: al terminar, comprobar el archivo contra la definición con un solver exacto.
    check = "--check" in args
    args = [a for a in args if a != "--check"]
    try:
        opts = parse_args(args)
    except CLIError as exc:
        print(exc, file=sys.stderr)
        return 1

    exit_on_sigterm()  # que timeout/kill cierren el backend y borren sus temporales
    logger = logging.getLogger("guroback")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        code = _run(opts)
    finally:
        logger.removeHandler(handler)
    if code == 0 and check:
        from .check import check_file, default_check_solver, describe

        report = check_file(str(opts["instance"]), str(opts["backbone"]), default_check_solver())
        print(describe(report))
        code = 0 if report.ok else 1
    return code


def _run(opts: Dict[str, object]) -> int:
    backend_cls = get_backend_class(str(opts["solver"]))
    try:
        start = time.perf_counter()
        with open(str(opts["backbone"]), "w", encoding="utf-8") as backbone_out:
            params = opts["params"]
            optimum = opts["optimum"]
            assert isinstance(params, dict) and (optimum is None or isinstance(optimum, float))
            with backend_cls(str(opts["instance"]), params) as backend:
                extractor = BackboneExtractor(backend, optimum=optimum)
                extractor.run(backbone_out)
        elapsed_us = int((time.perf_counter() - start) * 1_000_000)
        print(f"TOTAL RUNTIME: {elapsed_us} microseconds")
    except Exception as exc:  # noqa: BLE001 - reportar cualquier error del solver con código 1
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0

"""Resolución de la configuración, logging y señales: lo común a los modos local y MPI.

Antes la CLI, ``paraback/mpi.py`` y ``paraback/local.py`` configuraban cada uno el logging
a su manera (con niveles por defecto distintos) y sólo el modo MPI convertía ``SIGTERM``
en un cierre ordenado. Un solo camino:

* :func:`resolve_config`: argumentos → :class:`ParaBackConfig` (trabajadores o portfolio,
  hilos, solución base, ``start_time``, archivos de salida). Se hace una vez, en el
  lanzador; el rango 0 de MPI recibe el resultado ya resuelto (JSON) y lo difunde.
* :func:`configure_logging`: un manejador para ``paraback`` con prefijo ``[rango r]`` si
  hay rango; ``guroback`` siempre a ``WARNING``.
* :func:`install_signal_handlers`: ``SIGTERM`` → ``SystemExit`` (los ``with`` cierran los
  backends y borran sus temporales), en local y en cada rango MPI.
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
import time
from typing import List, Optional, Sequence

from guroback import BACKENDS, get_backend_class
from guroback.opb import max_int_bits
from guroback.check import default_check_solver
from guroback.signals import exit_on_sigterm

from .base_solution import read_base_solution
from .config import (available_solvers, ConfigError, default_portfolio, FLOAT_SOLVERS, ParaBackConfig, PASSIVE_WORKERS,
                     with_memory_limit, with_threads, WorkerSpec)

#: Atributo con el que se reconoce el manejador que instala :func:`configure_logging`.
_HANDLER_MARK = "_paraback_handler"

log = logging.getLogger("paraback.launch")

#: Bits del mayor entero que admite cada sustituto exacto (``--float-fallback``); los que no
#: figuran no tienen tope. SCIP exacto trata todo racional desde 1e20 (``SCIP_DEFAULT_INFINITY``,
#: ``scip/src/scip/rational.cpp``) como infinito y su lector OPB rechaza objetivos y lados así
#: (``SCIP: read error!``, base25: 9 instancias de 73 a 721 bits); 2^66 < 1e20 < 2^67.
FALLBACK_MAX_BITS = {"scip-exact": 66}
#: Tope de ``lp`` con el motor SoPlex: el mismo tramo que SCIP exacto (por encima, ``--substitute``).
SOPLEX_MAX_BITS = 66


def substitute_for(template: WorkerSpec, replaced: WorkerSpec, position: int) -> WorkerSpec:
    """El trabajador que ocupa el hueco de ``replaced`` (posición ``position``): ``template`` con el orden del hueco.

    Si el sustituido sondeaba siguiendo un orden, lo hereda (cada hueco ataca su región,
    ``docs/PLAN_EVALUACION.md`` P7); si no (``maxflip``, ``lp``, ``symmetry``), o si ``template``
    no admite ese orden, ``random:<posición>``, distinto del de cualquier otro hueco.
    """
    ordered = replaced.solver not in PASSIVE_WORKERS and not replaced.chunks.startswith("maxflip")
    if ordered:
        try:
            return dataclasses.replace(template, order=replaced.order)
        except ConfigError:
            pass
    return dataclasses.replace(template, order=f"random:{position}")


def adapt_float_solvers(specs: Sequence[WorkerSpec], instance: str, max_bits: Optional[int], fallback: str,
                        substitute: Optional[WorkerSpec] = None) -> List[WorkerSpec]:
    """Con enteros de más de ``max_bits`` bits en la instancia, cada solver en coma flotante pasa a ``fallback``.

    Gurobi y SCIP aceptan soluciones falsas y óptimos erróneos con coeficientes grandes
    (``REPORTE_SCIP.md``; khb05250 en R20): a partir de ``max_bits`` se usa un solver exacto. Se
    conservan el orden y los chunks (salvo ``maxflip``, que exige ``supports_max_flips``) y se
    descartan los parámetros, propios del solver sustituido. ``lp`` no se sustituye: sus hechos ya
    se demuestran en racionales y sólo cambia de motor, a SoPlex (``lp:engine=soplex``).

    Protocolo por tamaño de entero (CLAUDE.md "Política de solvers en coma flotante"): hasta
    ``max_bits``, los solvers en coma flotante; hasta :data:`FALLBACK_MAX_BITS` del sustituto (y
    :data:`SOPLEX_MAX_BITS` para ``lp``), el sustituto; por encima, el hueco lo ocupa
    ``substitute`` (:func:`substitute_for`) o, sin él, queda vacío con un aviso y sólo siguen los
    solvers sin tope (RoundingSat, NapBack, Cadiback). Si no queda ninguno, :class:`ConfigError`.
    """
    if max_bits is None or not any(s.solver in FLOAT_SOLVERS for s in specs):
        return list(specs)
    bits = max_int_bits(instance)
    if bits <= max_bits:
        return list(specs)
    if fallback not in BACKENDS:
        raise ConfigError(f"--float-fallback {fallback!r} no es un backend registrado")
    fallback_max = FALLBACK_MAX_BITS.get(fallback)
    try:
        supports_maxflip = bool(get_backend_class(fallback).supports_max_flips)
    except Exception:  # noqa: BLE001 - sin el módulo del solver: lo dirá al arrancar
        supports_maxflip = False
    out = []
    for position, spec in enumerate(specs):
        if spec.solver not in FLOAT_SOLVERS:
            out.append(spec)
            continue
        limit = SOPLEX_MAX_BITS if spec.solver == "lp" else fallback_max
        if limit is not None and bits > limit:
            if substitute is None:
                log.warning("la instancia tiene enteros de %d bits (> %d): %s queda fuera", bits, limit, spec.describe())
                continue
            replacement = substitute_for(substitute, spec, position)
            log.warning("la instancia tiene enteros de %d bits (> %d): %s queda fuera; ocupa su hueco %s",
                        bits, limit, spec.describe(), replacement.describe())
            out.append(replacement)
        elif spec.solver == "lp":
            log.warning("la instancia tiene enteros de %d bits (> %d): %s pasa al motor soplex", bits, max_bits, spec.describe())
            out.append(dataclasses.replace(spec, params={**{k: v for k, v in spec.params.items() if k == "probe"}, "engine": "soplex"}))
        else:
            chunks = spec.chunks if supports_maxflip or not spec.chunks.startswith("maxflip") else "doubling"
            log.warning("la instancia tiene enteros de %d bits (> %d): %s pasa a %s", bits, max_bits, spec.describe(), fallback)
            out.append(WorkerSpec(fallback, order=spec.order, chunks=chunks, rotation=spec.rotation, fixed=spec.fixed))
    if not out:
        raise ConfigError(f"ningún trabajador admite enteros de {bits} bits (> {fallback_max} de {fallback}): "
                          "añada roundingsat o napback, o --substitute")
    return out


def resolve_workers(args: argparse.Namespace) -> List[WorkerSpec]:
    """``--worker`` (repetible) o el portfolio por defecto; ``--threads-per-worker`` y ``--memory-limit`` en ambos casos."""
    if args.threads_per_worker < 1:
        raise ConfigError("--threads-per-worker debe ser >= 1")
    memory_limit = getattr(args, "memory_limit", None)
    if memory_limit is not None and memory_limit <= 0:
        raise ConfigError("--memory-limit debe ser positivo (MB)")
    if args.worker:
        specs = [WorkerSpec.parse(text) for text in args.worker]
    else:
        solvers = [s.strip() for s in args.solvers.split(",") if s.strip()] if args.solvers else available_solvers()
        if args.portfolio == "auto":
            num = None
        else:
            try:
                num = int(args.portfolio)
            except ValueError:
                raise ConfigError(f"--portfolio espera 'auto' o un entero, no {args.portfolio!r}") from None
            if num < 1:
                raise ConfigError("--portfolio debe ser >= 1")
        specs = default_portfolio(solvers, num, threads=None)
    substitute = parse_substitute(args)
    if getattr(args, "float_max_bits", None) is not None and getattr(args, "instance", None):
        specs = adapt_float_solvers(specs, args.instance, args.float_max_bits, getattr(args, "float_fallback", "scip-exact"), substitute)
    return [with_memory_limit(with_threads(s, args.threads_per_worker), memory_limit) for s in specs]


def parse_substitute(args: argparse.Namespace) -> Optional[WorkerSpec]:
    """``--substitute`` tal cual (sin hilos ni límite de memoria), o ``None``."""
    text = getattr(args, "substitute", None)
    if not text:
        return None
    substitute = WorkerSpec.parse(text)
    if substitute.solver in FLOAT_SOLVERS:
        raise ConfigError(f"--substitute {text!r}: el sustituto no puede ser un solver en coma flotante")
    return substitute


def resolve_substitute(args: argparse.Namespace) -> Optional[WorkerSpec]:
    """El sustituto en el sitio (``config.substitute``), con los hilos y el límite de memoria de los demás."""
    substitute = parse_substitute(args)
    if substitute is None:
        return None
    return with_memory_limit(with_threads(substitute, args.threads_per_worker), getattr(args, "memory_limit", None))


def check_solver(name: str) -> str:
    """El solver de ``--check``, validado antes de extraer nada (``auto``: el primero exacto disponible)."""
    if name == "auto":
        return default_check_solver()
    try:
        backend_cls = get_backend_class(name)
    except Exception as exc:  # noqa: BLE001 - nombre desconocido o módulo que no se importa
        raise ConfigError(f"--check-solver {name!r}: {exc}") from None
    if not backend_cls.exact_arithmetic:
        raise ConfigError(f"--check-solver {name!r} trabaja en coma flotante: --check necesita un solver exacto")
    ok, reason = backend_cls.is_available()
    if not ok:
        raise ConfigError(f"--check-solver {name!r} no está disponible: {reason}")
    return name


def resolve_config(args: argparse.Namespace, start_time: Optional[float] = None) -> ParaBackConfig:
    """La configuración completa a partir de los argumentos (``build_parser``)."""
    if not args.instance or not args.backbone:
        raise ConfigError("faltan argumentos: <instancia> <backbone.out>")
    if not os.path.exists(args.instance):
        raise ConfigError(f"la instancia no existe: {args.instance}")
    start = time.time() if start_time is None else start_time
    optimum, base_solution = args.optimum, None
    if args.base_solution:
        # Sin leer la instancia (en las enormes eran minutos y decenas de GB antes de mpirun):
        # cada trabajador verifica x* en z* con su solver, y los de coma flotante además con
        # aritmética exacta; una solución que no sirve hace fallar la ejecución.
        solution = read_base_solution(args.base_solution)
        if optimum is not None and solution.optimum is not None and optimum != solution.optimum:
            raise ConfigError(f"--optimum {optimum:g} no coincide con el coste exacto de --base-solution ({solution.optimum:g})")
        optimum, base_solution = solution.optimum, os.path.abspath(args.base_solution)

    def absolute(path: Optional[str]) -> Optional[str]:
        return None if path is None else os.path.abspath(path)

    return ParaBackConfig(
        instance=os.path.abspath(args.instance),
        workers=resolve_workers(args),
        backbone_out=os.path.abspath(args.backbone),
        optimum=optimum,
        base_solution=base_solution,
        start_time=start,
        stop_grace=args.stop_grace,
        time_limit=args.time_limit,
        probe_time_slice=args.probe_slice,
        sync_every=args.sync_every,
        share_solutions=not args.no_share_solutions,
        share_facts=not args.no_share_facts,
        share_interval=args.share_interval,
        share_base_optimum=not args.no_share_base_optimum,
        live_updates=not args.no_live_updates,
        exact_check=not args.no_exact_check,
        share_hints=args.share_hints,
        announce_intents=args.announce_intents,
        poll_interval=args.poll_interval,
        trace_dir=absolute(args.trace_dir),
        interrupt_on_stop=not args.no_interrupt,
        log_level=args.log_level.upper(),
        stats_json=absolute(args.stats_json),
        check=check_solver(args.check_solver) if args.check else None,
        base_only=bool(getattr(args, "base_only", False)),
        solution_out=absolute(getattr(args, "solution_out", None)),
        substitute=resolve_substitute(args),
    )


def configure_logging(level: str, rank: Optional[int] = None) -> None:
    """Un manejador en ``stderr`` para ``paraback`` (se sustituye si ya había uno de aquí)."""
    logger = logging.getLogger("paraback")
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    prefix = "" if rank is None else f"[rango {rank}] "
    handler.setFormatter(logging.Formatter(f"{prefix}%(levelname)s %(name)s: %(message)s"))
    setattr(handler, _HANDLER_MARK, True)
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False  # sin duplicados si alguien configuró el logger raíz
    logging.getLogger("guroback").setLevel(logging.WARNING)


def install_signal_handlers() -> None:
    """``SIGTERM`` cierra los backends y sus temporales (sólo en el hilo principal)."""
    exit_on_sigterm()


__all__ = ["check_solver", "configure_logging", "install_signal_handlers", "resolve_config", "resolve_workers"]

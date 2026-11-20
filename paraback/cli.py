"""Línea de comandos de ParaBack (``docs/PLAN_PARABACK.md`` §9.3).

::

    python -m paraback [--worker SPEC ...] [--portfolio auto|N] [--solvers LISTA]
                       [--time-limit S] [--probe-slice S] [--sync-every N]
                       [--no-share-solutions] [--no-share-base-optimum] [--no-live-updates] [--no-exact-check]
                       [--share-hints] [--no-interrupt]
                       [--optimum Z] [--threads-per-worker T] [--stats-json FILE]
                       [--launcher auto|mpi|local] [--mpirun CMD] [--mpirun-arg ARG ...]
                       [--log-level L] <instancia.opb> <backbone.out>
    python -m paraback --list-solvers
    python -m paraback --config EXPERIMENTOS.toml [--experiment NOMBRE [OPCIONES] <instancia> <backbone.out>]

Con ``--config`` y ``--experiment`` los trabajadores, ``--time-limit`` y las opciones salen
del archivo de experimentos (:mod:`paraback.experiments`); las opciones de la línea de
comandos se aplican después y mandan. ``--config`` solo lista los experimentos del archivo.

Sin ``--worker`` se usa ``default_portfolio`` sobre los solvers disponibles
(o los de ``--solvers``); ``--portfolio N`` fija el número de trabajadores.

Lanzamiento:

* ``--launcher mpi`` (o ``auto`` si hay ``mpirun`` y ``mpi4py``): el proceso
  resuelve la configuración (:func:`paraback.launch.resolve_config`) una sola vez, la
  guarda en un JSON temporal y ejecuta ``mpirun -n 1+k python -m paraback --_in-mpi
  --_config FILE``; el rango 0 la lee y la difunde. Resolverla aquí deja fuera del
  trabajo MPI la comprobación de licencias y fija ``start_time`` en el lanzador, que
  es desde donde cuenta ``--time-limit``. El código de salida vuelve en un archivo
  (``ParaBackConfig.status_file``): ``mpirun`` no lo propaga de forma fiable.
* ``--launcher local``: referencia con hilos en un solo proceso (sin MPI).
* Dentro de ``mpirun`` (variables ``OMPI_COMM_WORLD_SIZE``/``PMI_SIZE``/``PMIX_RANK``/
  ``MPI_LOCALNRANKS`` o ``--_in-mpi``) cada rango ejecuta :func:`paraback.mpi.main_mpi`; así también
  puede lanzarse a mano: ``mpirun -n 4 python -m paraback --worker ... inst out``.

Código de salida: 0 si el resultado es ``complete`` o ``infeasible``, 2 si
``interrupted`` (límite de tiempo) y 1 si ``failed`` o error.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import replace
from typing import List, Optional, Sequence, TextIO

from guroback.check import check_file, default_check_solver, describe
from guroback.cli import list_solvers as list_backends
from guroback.extractor import RunStatus

from .config import CONFIG_ERRORS, NATIVE_WORKERS, ConfigError, ParaBackConfig, solver_status
from .experiments import find_experiment, load_experiments
from .launch import configure_logging, install_signal_handlers, resolve_config, resolve_workers
from .options import build_parser
from .orchestrator import ParaBackResult

EXIT_OK, EXIT_FAILED, EXIT_INTERRUPTED = 0, 1, 2
_EXIT_BY_STATUS = {"complete": EXIT_OK, "infeasible": EXIT_OK, "interrupted": EXIT_INTERRUPTED, "failed": EXIT_FAILED}

#: Variables de entorno con las que los lanzadores MPI habituales marcan a sus procesos.
_MPI_ENV = ("OMPI_COMM_WORLD_SIZE", "PMI_SIZE", "PMIX_RANK", "MPI_LOCALNRANKS")


# ---------------------------------------------------------------------------- configuración
def build_config(args: argparse.Namespace) -> ParaBackConfig:
    """La configuración: la ya resuelta por el lanzador (``--_config``) o la de los argumentos."""
    if args.config_file:
        with open(args.config_file, encoding="utf-8") as fh:
            return ParaBackConfig.from_dict(json.load(fh))
    return resolve_config(args)


# ---------------------------------------------------------------------------- resultado
def report(result: ParaBackResult, config: ParaBackConfig, out: Optional[TextIO] = None) -> int:
    """Escribe backbone y métricas, imprime el resumen y devuelve el código de salida."""
    stream = sys.stdout if out is None else out
    if config.backbone_out:
        with open(config.backbone_out, "w", encoding="utf-8") as fh:
            result.write(fh)
    if config.solution_out:
        with open(config.solution_out, "w", encoding="utf-8") as fh:
            result.write_solution(fh)
    if config.stats_json:
        data = result.to_dict()
        data["config"] = config.to_dict()
        with open(config.stats_json, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
    print(
        f"paraback: status={result.status} backbone={len(result.backbone)} refuted={len(result.refuted)} "
        f"unknown={len(result.unknown)} optimum={result.optimum} workers={len(result.workers)} "
        f"solves={result.solve_count} messages={result.messages}",
        file=stream,
    )
    for rank, w in sorted(result.workers.items()):
        print(
            f"  w{rank} {w.spec or '?'}: {w.error or w.done_status} solves={w.stats.solve_count} "
            f"backbone+={w.backbone_contributed} refuted+={w.refuted_contributed}",
            file=stream,
        )
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)
    print(f"TOTAL RUNTIME: {int(result.wall_time * 1_000_000)} microseconds", file=stream)
    code = _EXIT_BY_STATUS.get(str(result.status), EXIT_FAILED)
    if config.check and config.backbone_out and code == EXIT_OK and result.status is not RunStatus.INFEASIBLE:
        solver = default_check_solver() if config.check == "auto" else config.check
        check = check_file(config.instance, config.backbone_out, solver, result.optimum)
        print(describe(check), file=stream)
        if not check.ok:
            code = EXIT_FAILED
    stream.flush()
    return code


# ---------------------------------------------------------------------------- lanzadores
def in_mpi_environment() -> bool:
    return any(name in os.environ for name in _MPI_ENV)


def mpi_launcher_available(mpirun: str) -> bool:
    from .mpi_communicator import mpi_available  # importa mpi4py sólo si se pregunta por MPI

    return shutil.which(mpirun) is not None and mpi_available()


def _mpirun_command(args: argparse.Namespace, config_file: str, nprocs: int) -> List[str]:
    cmd = [args.mpirun, "-n", str(nprocs)]
    extra = list(args.mpirun_arg)
    if not extra:
        try:
            version = subprocess.run([args.mpirun, "--version"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            version = ""
        if "Open MPI" in version:
            # Un rango por trabajador aunque haya menos núcleos que 1+k (el orquestador está ocioso).
            extra = ["--oversubscribe"]
    cmd += extra
    cmd += [sys.executable, "-m", "paraback", "--_in-mpi", "--_config", config_file]
    return cmd


def run_with_mpirun(args: argparse.Namespace, config: ParaBackConfig) -> int:
    with tempfile.TemporaryDirectory(prefix="paraback_") as tmp:
        config_file = os.path.join(tmp, "config.json")
        status_file = os.path.join(tmp, "status")
        with open(config_file, "w", encoding="utf-8") as fh:
            json.dump(replace(config, status_file=status_file).to_dict(), fh)
        cmd = _mpirun_command(args, config_file, config.mpi_size)
        logging.getLogger("paraback.cli").info("lanzando: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd)
        # mpirun reenvía SIGINT/SIGTERM a los rangos; sin reenviarlas, matar al
        # lanzador dejaría el trabajo MPI huérfano.
        forwarded = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        for sig in forwarded:
            signal.signal(sig, lambda signum, _frame: proc.send_signal(signum))
        try:
            proc.wait()
        finally:
            for sig, handler in forwarded.items():
                signal.signal(sig, handler)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        if os.path.exists(status_file):
            with open(status_file, encoding="utf-8") as fh:
                return int(fh.read().strip() or EXIT_FAILED)
        print(f"Error: mpirun terminó con código {proc.returncode} sin resultado del orquestador", file=sys.stderr)
        return EXIT_FAILED


def run_in_mpi(args: argparse.Namespace) -> int:
    from .mpi import main_mpi  # importa mpi4py

    install_signal_handlers()  # mpirun reenvía SIGTERM: que cada rango cierre su backend y sus temporales

    def on_result(result: ParaBackResult, config: ParaBackConfig) -> int:
        code = report(result, config)
        if config.status_file:
            with open(config.status_file, "w", encoding="utf-8") as fh:
                fh.write(str(code))
            return EXIT_OK  # el lanzador propaga el código; así mpirun no aborta el trabajo
        return code

    return main_mpi(lambda: build_config(args), on_result)


def run_local_mode(config: ParaBackConfig) -> int:
    from .local import run_local

    install_signal_handlers()
    return report(run_local(config), config)


def expand_experiment(argv: Sequence[str]) -> List[str]:
    """``--config F --experiment N ...`` → argumentos del experimento seguidos de los demás.

    Los de la línea de comandos van detrás, así que sus opciones escalares mandan; elegir
    trabajadores a la vez que un experimento (``--worker``, ``--portfolio``, ``--solvers``) es un error.
    """
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--config")
    pre.add_argument("--experiment")
    known, rest = pre.parse_known_args(list(argv))
    if known.experiment is None:
        return list(argv)
    if known.config is None:
        raise ConfigError("--experiment necesita --config")
    clash = [t for t in rest if t.split("=", 1)[0] in ("--worker", "--portfolio", "--solvers")]
    if clash:
        raise ConfigError(f"--experiment ya fija los trabajadores; sobra {clash[0]}")
    exp = find_experiment(load_experiments(known.config), known.experiment)
    for warning in exp.warnings:
        print(f"Aviso ({exp.name}): {warning}", file=sys.stderr)
    return exp.paraback_args() + rest


def list_experiments(path: str) -> int:
    try:
        experiments = load_experiments(path)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(f"{'experimento':<24} {'trab.':>5} {'gurobi':>6} {'rep.':>4} {'núcleos':>7} {'memoria':>8} {'límite':>8} {'bits':>6}  descripción")
    for exp in experiments:
        limit = "—" if exp.time_limit is None else f"{exp.time_limit:g}s"
        cores, mem = exp.num_tasks * exp.cores_per_task, exp.num_tasks * exp.mem_per_task_mb() / 1024
        print(f"{exp.name:<24} {exp.num_workers:>5} {exp.gurobi_workers:>6} {exp.repeats:>4} {cores:>7} {mem:>7.0f}G {limit:>8} "
              f"{exp.bits_label:>6}  {exp.description}")
        for warning in exp.warnings:
            print(f"{'':<24} aviso: {warning}")
    return EXIT_OK


def list_solvers() -> int:
    """Backends registrados y trabajadores nativos (``cadiback``) operativos en este entorno."""
    list_backends()
    for name in sorted(NATIVE_WORKERS):
        available, reason = solver_status(name)
        status = "disponible" if available else "NO DISPONIBLE  " + reason
        print(f"{name:12} {status}  [trabajador nativo]")
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        raw = expand_experiment(raw)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    args = build_parser().parse_args(raw)
    if args.config and args.experiment is None and not args.in_mpi:
        return list_experiments(args.config)
    if args.list_solvers:
        return list_solvers()
    if args.in_mpi or in_mpi_environment():
        return run_in_mpi(args)

    configure_logging(args.log_level)
    try:
        config = build_config(args)
    except CONFIG_ERRORS as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    launcher = args.launcher
    if launcher == "auto":
        launcher = "mpi" if mpi_launcher_available(args.mpirun) else "local"
    if launcher == "mpi":
        if not mpi_launcher_available(args.mpirun):
            print(f"Error: se pidió --launcher mpi pero no hay {args.mpirun!r} o mpi4py", file=sys.stderr)
            return EXIT_FAILED
        return run_with_mpirun(args, config)
    return run_local_mode(config)


__all__ = [
    "CONFIG_ERRORS",
    "EXIT_FAILED",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "build_config",
    "build_parser",
    "main",
    "report",
    "resolve_workers",
]

"""ParaBack sobre MPI: orquestador del rango 0 y programa de cada rango.

``mpirun -n 1+k python -m paraback --_in-mpi ...`` (lo construye el lanzador de
:mod:`paraback.cli`) ejecuta :func:`main_mpi` en todos los rangos:

* el rango 0 obtiene la configuración (la que resolvió el lanzador, o la de sus
  argumentos si se lanzó a mano con ``mpirun``) y la difunde (``bcast``), de modo
  que la resolución de ``--portfolio auto`` (que comprueba licencias) ocurre una
  sola vez; después ejecuta :class:`MPIOrchestrator`;
* el rango ``r >= 1`` ejecuta :class:`paraback.worker.Worker` con
  ``config.workers[r - 1]``.

Cierre ordenado (sin handles ni ``isend`` huérfanos, ``docs/PLAN_PARABACK.md`` §13):
cuando todos los trabajadores han enviado ``DONE``/``ERROR``, el orquestador
envía a cada uno un ``STOP`` con motivo :data:`SHUTDOWN`. Cada trabajador, tras
terminar su extracción, recibe y descarta mensajes hasta ver ese ``STOP``; como
MPI entrega en orden entre cada par de rangos, en ese momento ya ha recibido
todo lo que el orquestador le envió y todos los ``isend`` de ambos lados pueden
completarse (``Waitall``) antes del ``Barrier`` final. Las esperas y el abandono
están en :mod:`paraback.shutdown`.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Callable, Optional, Tuple, Union

from . import protocol
from .config import CONFIG_ERRORS, ORCHESTRATOR_RANK, ParaBackConfig
from .launch import configure_logging
from .mpi_communicator import MPICommunicator
from .orchestrator import Orchestrator, ParaBackResult
from .shutdown import SHUTDOWN, drain_until_shutdown, finish_orchestrator, worker_shutdown_wait
from .worker import make_worker

log = logging.getLogger("paraback.mpi")

#: Lo que el rango 0 difunde: la configuración o el mensaje de error que la impide.
_Broadcast = Union[Tuple[str, Any], None]


class MPIOrchestrator(Orchestrator):
    """Orquestador cuyos trabajadores ya lanzó ``mpirun`` en los rangos ``1..k``."""

    def __init__(self, config: ParaBackConfig, comm: MPICommunicator) -> None:
        super().__init__(config, comm)
        self.mpi_comm = comm

    def launch_workers(self) -> None:
        # Los procesos existen desde que arrancó mpirun; nada que lanzar.
        pass

    def wait_workers(self) -> None:
        # El bucle de eventos ya esperó todos los DONE/ERROR (o terminó por una
        # excepción del propio orquestador): se libera a los trabajadores.
        for rank in self.workers:
            self.mpi_comm.send(rank, protocol.stop(self.mpi_comm.rank, SHUTDOWN))


def main_mpi(
    build_config: Callable[[], ParaBackConfig],
    on_result: Callable[[ParaBackResult, ParaBackConfig], int],
    mpi_comm: Optional[Any] = None,
) -> int:
    """Programa de un rango MPI.

    ``build_config`` sólo se invoca en el rango 0; ``on_result`` (escribir el
    backbone, las métricas y decidir el código de salida) también. Devuelve el
    código de salida de este rango: los trabajadores devuelven 0 salvo que no
    hayan podido cerrar ordenadamente.
    """
    comm = MPICommunicator(mpi_comm)
    rank, size = comm.rank, comm.size

    # 1. Configuración: la resuelve el rango 0 y la difunde (o difunde el error).
    payload: _Broadcast = None
    if rank == ORCHESTRATOR_RANK:
        try:
            payload = ("ok", build_config().to_dict())
        except CONFIG_ERRORS as exc:
            payload = ("error", str(exc))
    payload = comm.mpi_comm.bcast(payload, root=ORCHESTRATOR_RANK)
    assert payload is not None
    if payload[0] != "ok":
        if rank == ORCHESTRATOR_RANK:
            print(f"Error: {payload[1]}", file=sys.stderr)
        return 1
    config = ParaBackConfig.from_dict(payload[1])
    if size != config.mpi_size:
        if rank == ORCHESTRATOR_RANK:
            print(
                f"Error: la configuración tiene {config.num_workers} trabajadores y necesita "
                f"{config.mpi_size} rangos MPI (mpirun -n {config.mpi_size}), pero hay {size}",
                file=sys.stderr,
            )
        return 1
    configure_logging(config.log_level, rank)

    # 2. Orquestador o trabajador.
    code = 0
    if rank == ORCHESTRATOR_RANK:
        try:
            result = MPIOrchestrator(config, comm).run()
            code = finish_orchestrator(result, config, on_result, comm.close, comm.mpi_comm.Abort)
        except BaseException:
            # Sin orquestador los trabajadores esperarían indefinidamente: se aborta el trabajo
            # MPI. También ante SystemExit (SIGTERM de mpirun): MPI_Abort termina a todos ya.
            log.exception("el orquestador falló; abortando todos los rangos")
            comm.mpi_comm.Abort(1)
            raise
    else:
        worker = make_worker(comm, config, config.workers[rank - 1])
        worker.run()
        wait = worker_shutdown_wait(config)
        if not drain_until_shutdown(worker.channel, wait):
            log.error("trabajador %d: el orquestador no envió el cierre en %.0f s", rank, wait or 0.0)
            comm.mpi_comm.Abort(1)
        comm.close()
    comm.barrier()
    return code


__all__ = ["MPIOrchestrator", "SHUTDOWN", "drain_until_shutdown", "main_mpi"]

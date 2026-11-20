"""Implementación de referencia de ParaBack sin MPI: trabajadores en hilos.

Sirve para validar el protocolo, la fusión de conocimiento y la coordinación de
los backends refactorizados dentro de un solo proceso (tests de la Fase 1) y
como respaldo cuando no hay MPI. El paralelismo real lo aporta la
implementación MPI de la Fase 2, que reutiliza :class:`Orchestrator` y
:class:`Worker` sin cambios y sólo sustituye el transporte y el lanzamiento.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional, TextIO

from .communicator import LocalBus
from .config import ORCHESTRATOR_RANK, ParaBackConfig
from .orchestrator import Orchestrator, ParaBackResult
from .worker import BackendFactory, make_worker

log = logging.getLogger("paraback.local")


class LocalOrchestrator(Orchestrator):
    """Orquestador cuyo transporte es un :class:`LocalBus` y cuyos trabajadores son hilos."""

    def __init__(
        self, config: ParaBackConfig, bus: Optional[LocalBus] = None, backend_factory: Optional[BackendFactory] = None
    ) -> None:
        self.bus = bus or LocalBus(config.mpi_size)
        super().__init__(config, self.bus.endpoint(ORCHESTRATOR_RANK))
        self.backend_factory = backend_factory
        self._threads: List[threading.Thread] = []

    def launch_workers(self) -> None:
        for index, spec in enumerate(self.config.workers):
            rank = index + 1
            worker = make_worker(self.bus.endpoint(rank), self.config, spec, self.backend_factory)
            thread = threading.Thread(target=worker.run, name=f"paraback-worker-{rank}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def wait_workers(self) -> None:
        # Los hilos abandonados (no terminaron tras STOP) no se pueden matar: son daemon y se
        # dejan atrás; esperar por ellos colgaría la ejecución que el orquestador ya cerró.
        for rank, thread in enumerate(self._threads, start=1):
            if rank not in self.abandoned:
                thread.join()


def run_local(
    config: ParaBackConfig, backbone_out: Optional[TextIO] = None, backend_factory: Optional[BackendFactory] = None
) -> ParaBackResult:
    """Ejecuta ParaBack en un proceso (hilos) y escribe el backbone si se pide.

    ``backend_factory`` sustituye la búsqueda de la clase de backend en el registro (tests).
    """
    logging.getLogger("paraback").setLevel(config.log_level)
    result = LocalOrchestrator(config, backend_factory=backend_factory).run()
    if backbone_out is not None:
        result.write(backbone_out)
    return result


__all__ = ["LocalOrchestrator", "run_local"]

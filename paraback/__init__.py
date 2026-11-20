"""ParaBack: extractor de backbones paralelo (portfolio) sobre MPI.

Componentes:

* :mod:`paraback.config`: :class:`WorkerSpec` y :class:`ParaBackConfig`
  (parámetros de ejecución del orquestador y de cada trabajador).
* :mod:`paraback.protocol`: mensajes ``HELLO/BASE/FACTS/UPDATE/STOP/DONE/ERROR``.
* :mod:`paraback.communicator`: interfaz :class:`Communicator` y la
  implementación en memoria :class:`LocalBus`.
* :mod:`paraback.orchestrator`: :class:`Orchestrator` (abstracto) con el
  conocimiento global, la fusión de hechos y el ciclo de vida.
* :mod:`paraback.worker`: :class:`Worker` y :class:`WorkerCoordinator`, que
  conectan el extractor de ``guroback`` con el protocolo.
* :mod:`paraback.local`: referencia con hilos (:func:`run_local`).
* :mod:`paraback.mpi_communicator`: :class:`MPICommunicator` (``mpi4py``).
* :mod:`paraback.mpi`: :class:`MPIOrchestrator` y :func:`main_mpi` (programa de cada rango).
* :mod:`paraback.cli`: ``python -m paraback`` / ``paraback``; lanza ``mpirun -n 1+k``
  o, con ``--launcher local``, la referencia con hilos.

Los módulos MPI no se importan aquí para que ``import paraback`` no exija
``mpi4py``. Ver ``docs/PLAN_PARABACK.md``.
"""
from __future__ import annotations

from .communicator import Communicator, LocalBus, LocalCommunicator
from .config import ORCHESTRATOR_RANK, ConfigError, ParaBackConfig, WorkerSpec, available_solvers, default_portfolio
from .local import LocalOrchestrator, run_local
from .orchestrator import GlobalKnowledge, Orchestrator, OrchestratorState, ParaBackResult, WorkerRecord
from .protocol import Message, MessageKind
from .worker import Worker, WorkerCoordinator

__version__ = "0.2.0"

__all__ = [
    "Communicator",
    "ConfigError",
    "GlobalKnowledge",
    "LocalBus",
    "LocalCommunicator",
    "LocalOrchestrator",
    "Message",
    "MessageKind",
    "ORCHESTRATOR_RANK",
    "Orchestrator",
    "OrchestratorState",
    "ParaBackConfig",
    "ParaBackResult",
    "Worker",
    "WorkerCoordinator",
    "WorkerRecord",
    "WorkerSpec",
    "available_solvers",
    "default_portfolio",
    "run_local",
]

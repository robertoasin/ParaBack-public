"""Transporte de mensajes entre el orquestador y los trabajadores.

:class:`Communicator` es la interfaz que usan :mod:`paraback.orchestrator` y
:mod:`paraback.worker`. Tiene dos implementaciones previstas:

* :class:`LocalCommunicator` (este módulo): colas en memoria dentro de un
  proceso; los trabajadores corren en hilos. Es la implementación de
  referencia con la que se validan el protocolo y la coordinación sin MPI.
* ``MPICommunicator`` (Fase 2, ``paraback/mpi_communicator.py``): ``mpi4py``
  con ``comm.isend``/``comm.iprobe`` sobre ``MPI.COMM_WORLD``; el rango 0 es
  el orquestador (ver ``docs/PLAN_PARABACK.md`` §5).

La semántica que deben respetar ambas: entrega fiable y en orden entre cada
par de rangos, ``poll`` no bloqueante y ``wait`` con tiempo de espera.
"""
from __future__ import annotations

import abc
import queue
import threading
from typing import Dict, List, Optional

from .protocol import Message


class Communicator(abc.ABC):
    """Canal de mensajes punto a punto con identidad (rango) y tamaño del grupo."""

    @property
    @abc.abstractmethod
    def rank(self) -> int: ...

    @property
    @abc.abstractmethod
    def size(self) -> int: ...

    @abc.abstractmethod
    def send(self, dest: int, message: Message) -> None:
        """Envío no bloqueante (o con búfer) a ``dest``."""

    @abc.abstractmethod
    def poll(self) -> List[Message]:
        """Todos los mensajes pendientes, sin bloquear (lista vacía si no hay)."""

    @abc.abstractmethod
    def wait(self, timeout: Optional[float] = None) -> Optional[Message]:
        """Espera un mensaje hasta ``timeout`` segundos; ``None`` si expira."""

    def broadcast(self, message: Message, exclude: int = -1) -> None:
        """Envía ``message`` a todos los demás rangos (salvo ``exclude``)."""
        for dest in range(self.size):
            if dest != self.rank and dest != exclude:
                self.send(dest, message)

    @abc.abstractmethod
    def close(self) -> None: ...


class LocalBus:
    """Grupo de ``size`` :class:`LocalCommunicator` conectados por colas en memoria."""

    def __init__(self, size: int) -> None:
        if size < 1:
            raise ValueError("size >= 1")
        self._size = size
        self._queues: Dict[int, "queue.Queue[Message]"] = {rank: queue.Queue() for rank in range(size)}
        self._lock = threading.Lock()
        self.sent: int = 0

    @property
    def size(self) -> int:
        return self._size

    def endpoint(self, rank: int) -> "LocalCommunicator":
        return LocalCommunicator(self, rank)

    def _deliver(self, dest: int, message: Message) -> None:
        if dest not in self._queues:
            raise ValueError(f"rango destino inválido: {dest}")
        with self._lock:
            self.sent += 1
        self._queues[dest].put(message)

    def _queue(self, rank: int) -> "queue.Queue[Message]":
        return self._queues[rank]


class LocalCommunicator(Communicator):
    def __init__(self, bus: LocalBus, rank: int) -> None:
        self._bus = bus
        self._rank = rank
        self._closed = False

    @property
    def rank(self) -> int:
        return self._rank

    @property
    def size(self) -> int:
        return self._bus.size

    def send(self, dest: int, message: Message) -> None:
        self._bus._deliver(dest, message)

    def poll(self) -> List[Message]:
        pending: List[Message] = []
        q = self._bus._queue(self._rank)
        while True:
            try:
                pending.append(q.get_nowait())
            except queue.Empty:
                return pending

    def wait(self, timeout: Optional[float] = None) -> Optional[Message]:
        try:
            return self._bus._queue(self._rank).get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True


__all__ = ["Communicator", "LocalBus", "LocalCommunicator"]

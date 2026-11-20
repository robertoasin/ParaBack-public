"""Transporte MPI de ParaBack (``mpi4py`` sobre ``MPI.COMM_WORLD``).

Implementa :class:`paraback.communicator.Communicator` con envíos no
bloqueantes (``isend``) y recepción por sondeo emparejado (``improbe`` +
``Message.recv``), que es seguro con ``MPI_THREAD_MULTIPLE`` aunque el hilo de
escucha del trabajador (:class:`paraback.listener.ListeningCommunicator`) reciba
mientras el hilo principal envía. Los mensajes de :mod:`paraback.protocol`
viajan con ``pickle`` (métodos en minúsculas de ``mpi4py``); MPI garantiza
entrega fiable y en orden entre cada par de rangos, que es la semántica que
exige el orquestador.

``mpi4py`` se importa de forma diferida: importar :mod:`paraback` no exige MPI.
"""
from __future__ import annotations

import threading
import time
from typing import Any, List, Optional

from .communicator import Communicator
from .protocol import Message

#: Espera máxima entre dos ``improbe`` en :meth:`MPICommunicator.wait` (segundos).
_MAX_BACKOFF = 0.01


class MPICommunicator(Communicator):
    """Canal punto a punto sobre un comunicador MPI (por defecto ``COMM_WORLD``)."""

    TAG = 7

    def __init__(self, comm: Any = None) -> None:
        from mpi4py import MPI

        self._MPI = MPI
        self._comm = MPI.COMM_WORLD if comm is None else comm
        self._rank = self._comm.Get_rank()
        self._size = self._comm.Get_size()
        self._pending: List[Any] = []  # requests de isend aún no completadas
        self._lock = threading.Lock()
        self._closed = False
        self.sent = 0

    @property
    def mpi_comm(self) -> Any:
        return self._comm

    @property
    def rank(self) -> int:
        return self._rank

    @property
    def size(self) -> int:
        return self._size

    def send(self, dest: int, message: Message) -> None:
        if not 0 <= dest < self._size or dest == self._rank:
            raise ValueError(f"rango destino inválido: {dest}")
        request = self._comm.isend(message.to_wire(), dest=dest, tag=self.TAG)
        with self._lock:
            self.sent += 1
            self._pending.append(request)
            # Liberar las que ya se completaron para no acumular requests.
            self._pending = [r for r in self._pending if not r.Test()]

    def _receive_one(self) -> Optional[Message]:
        probed = self._comm.improbe(source=self._MPI.ANY_SOURCE, tag=self.TAG)
        if probed is None:
            return None
        return Message.from_wire(probed.recv())

    def poll(self) -> List[Message]:
        out: List[Message] = []
        while True:
            message = self._receive_one()
            if message is None:
                return out
            out.append(message)

    def wait(self, timeout: Optional[float] = None) -> Optional[Message]:
        deadline = None if timeout is None else time.monotonic() + timeout
        delay = 0.0005
        while True:
            message = self._receive_one()
            if message is not None:
                return message
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                delay = min(delay, remaining)
            time.sleep(delay)
            delay = min(delay * 2, _MAX_BACKOFF)

    def barrier(self) -> None:
        self._comm.Barrier()

    def close(self) -> None:
        """Completa los envíos pendientes (el receptor debe estar drenando sus mensajes)."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            pending, self._pending = self._pending, []
        if pending:
            self._MPI.Request.Waitall(pending)


def mpi_available() -> bool:
    try:
        import mpi4py  # noqa: F401  (sólo comprueba que se puede importar)
    except ImportError:
        return False
    return True


__all__ = ["MPICommunicator", "mpi_available"]

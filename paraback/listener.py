"""Hilo de escucha de un trabajador: recibe mensajes mientras el hilo principal resuelve.

:class:`ListeningCommunicator` envuelve el canal de un trabajador con un hilo que mueve
los mensajes entrantes a un búfer y avisa de los que no pueden esperar al siguiente
sondeo: ``STOP`` (interrumpir), ``BASE_HINT`` (dejar de buscar la base) y ``UPDATE``
(hechos ajenos para el sondeo en curso). Lo usan los dos trabajadores
(:mod:`paraback.worker_base`), que implementan :class:`ListenerCallbacks`.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, List, Mapping, Optional, Protocol

from . import protocol
from .communicator import Communicator
from .stats import Stopwatch
from .trace import Tracer

log = logging.getLogger("paraback.worker")


class ListenerCallbacks(Protocol):
    """Lo que un trabajador hace con los mensajes urgentes (desde el hilo de escucha)."""

    def on_stop_signal(self) -> None: ...

    def on_base_hint(self, optimum: float, values: Optional[Mapping[str, bool]]) -> None: ...

    def on_update(self, update: protocol.Facts) -> None: ...

    def on_tick(self) -> None: ...


class ListeningCommunicator(Communicator):
    """Envuelve un :class:`Communicator` con un hilo que recibe en segundo plano.

    El hilo mueve los mensajes entrantes a un búfer propio; :meth:`poll` y
    :meth:`wait` leen de ese búfer. Cuando recibe ``STOP`` invoca
    ``on_stop`` y lo repite cada ``interval`` segundos hasta que se detiene
    la escucha, para cubrir la carrera en la que ``STOP`` llega justo antes de
    que empiece un ``solve`` (``interrupt()`` sin ``solve`` en curso no hace nada).
    Un ``BASE_HINT`` invoca ``on_base_hint`` (una sola vez por mensaje, no se
    repite como ``STOP``): es el z* (y la asignación óptima) de otro trabajador,
    útil sólo mientras este trabajador no haya resuelto su propia base
    (:meth:`BaseWorker.on_base_hint <paraback.worker_base.BaseWorker.on_base_hint>`).
    Un ``UPDATE`` invoca ``on_update`` ya leído (:class:`paraback.protocol.Facts`),
    además de quedar en el búfer para ``before_probe``; ``on_tick`` se invoca en
    cada vuelta del bucle (como mucho cada ``interval`` segundos).
    Los envíos van directos al canal interno (MPI con ``THREAD_MULTIPLE`` o
    colas en memoria: ambos admiten un emisor y un receptor concurrentes).
    """

    def __init__(
        self,
        inner: Communicator,
        on_stop: Callable[[], None],
        interval: float = 0.05,
        on_base_hint: Optional[Callable[[float, Optional[Mapping[str, bool]]], None]] = None,
        on_update: Optional[Callable[[protocol.Facts], None]] = None,
        on_tick: Optional[Callable[[], None]] = None,
        tracer: Optional[Tracer] = None,
        buffer_updates: bool = True,
    ) -> None:
        self.inner = inner
        #: ``False``: los ``UPDATE`` sólo van a ``on_update`` (quien no los vuelve a leer del
        #: búfer, como Cadiback, no los acumula durante toda la ejecución).
        self._buffer_updates = buffer_updates
        self._tracer = tracer
        self._on_stop = on_stop
        self._on_base_hint = on_base_hint
        self._on_update = on_update
        self._on_tick = on_tick
        self._interval = interval
        self._buffer: List[protocol.Message] = []
        #: Tiempo de este hilo con los ``UPDATE`` (``WorkerStats.listener_time``).
        self.update_watch = Stopwatch()
        self._cond = threading.Condition()
        self._running = False
        self._stopped_seen = False
        self._thread: Optional[threading.Thread] = None

    @property
    def rank(self) -> int:
        return self.inner.rank

    @property
    def size(self) -> int:
        return self.inner.size

    def send(self, dest: int, message: protocol.Message) -> None:
        self.inner.send(dest, message)

    @classmethod
    def for_worker(
        cls,
        inner: Communicator,
        callbacks: ListenerCallbacks,
        interval: float,
        *,
        base_hints: bool = True,
        updates: bool = True,
        buffer_updates: bool = True,
        tracer: Optional[Tracer] = None,
    ) -> "ListeningCommunicator":
        """Hilo de escucha de un trabajador; ``base_hints``/``updates`` eligen qué se le entrega."""
        return cls(
            inner,
            callbacks.on_stop_signal,
            interval,
            on_base_hint=callbacks.on_base_hint if base_hints else None,
            on_update=callbacks.on_update if updates else None,
            on_tick=callbacks.on_tick,
            tracer=tracer,
            buffer_updates=buffer_updates,
        )

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._listen, name=f"paraback-listener-{self.rank}", daemon=True)
        self._thread.start()

    def stop_listening(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _listen(self) -> None:
        while self._running:
            message = self.inner.wait(timeout=self._interval)
            if message is not None:
                if message.kind is protocol.MessageKind.UPDATE:
                    with self.update_watch:
                        self._handle_update(message)
                else:
                    with self._cond:
                        self._buffer.append(message)
                        self._cond.notify_all()
                if message.kind is protocol.MessageKind.STOP:
                    self._stopped_seen = True
                elif message.kind is protocol.MessageKind.BASE_HINT and self._on_base_hint is not None:
                    hint = protocol.read_base_hint(message)
                    self._on_base_hint(hint.optimum, hint.values)
            if self._stopped_seen:
                try:
                    self._on_stop()
                except Exception as exc:  # noqa: BLE001 - un fallo al interrumpir no debe matar el hilo
                    log.warning("trabajador %d: interrupt() falló: %s", self.rank, exc)
            elif self._on_tick is not None:
                try:
                    self._on_tick()
                except Exception as exc:  # noqa: BLE001 - un fallo en el tic no debe matar el hilo
                    log.warning("trabajador %d: el tic del hilo de escucha falló: %s", self.rank, exc)

    def _handle_update(self, message: protocol.Message) -> None:
        update = protocol.read_facts(message) if self._tracer is not None or self._on_update is not None else None
        if self._tracer is not None and update is not None:
            trace_update(self._tracer, update)
        if self._buffer_updates:
            with self._cond:
                self._buffer.append(message)
                self._cond.notify_all()
        if self._on_update is not None and update is not None:
            try:
                self._on_update(update)
            except Exception as exc:  # noqa: BLE001 - es una optimización: el UPDATE sigue en búfer
                log.warning("trabajador %d: no se pudo usar el UPDATE en el sondeo en curso: %s", self.rank, exc)

    def poll(self) -> List[protocol.Message]:
        with self._cond:
            out, self._buffer = self._buffer, []
        if not self._running:
            out.extend(self.inner.poll())
        return out

    def wait(self, timeout: Optional[float] = None) -> Optional[protocol.Message]:
        if not self._running:
            with self._cond:
                if self._buffer:
                    return self._buffer.pop(0)
            return self.inner.wait(timeout)
        with self._cond:
            if not self._buffer:
                self._cond.wait(timeout)
            return self._buffer.pop(0) if self._buffer else None

    def close(self) -> None:
        self.stop_listening()
        self.inner.close()


def trace_update(tracer: Tracer, update: protocol.Facts) -> None:
    """Llegada de un ``UPDATE`` a este rango (primera vez que lo ve; ``tools/sharing_report.py``)."""
    tracer.update_recv(update.sent_at, [n for n, _ in update.backbone], update.refuted)


__all__ = ["ListenerCallbacks", "ListeningCommunicator", "trace_update"]

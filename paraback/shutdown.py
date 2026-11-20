"""Cierre de una ejecución de ParaBack: motivos de STOP, esperas y abandono.

Antes estaba repartido entre el orquestador, ``paraback/mpi.py`` (``SHUTDOWN``, una espera
fija de 600 s, ``MPI_Abort``) y la CLI. Las piezas:

* El orquestador manda ``STOP`` (:class:`~paraback.protocol.StopReason`: ``complete``,
  ``time_limit``, ``error``) y espera ``stop_grace`` segundos; los trabajadores que no han
  terminado se abandonan (``Orchestrator._abandon_unresponsive``).
* Con MPI, cuando todos han terminado el rango 0 manda ``STOP(shutdown)`` a cada uno;
  un trabajador que ya terminó descarta mensajes hasta verlo (:func:`drain_until_shutdown`),
  así todos los ``isend`` se completan antes del ``Barrier`` final.
* Cuánto espera un trabajador ese ``STOP(shutdown)`` (:func:`worker_shutdown_wait`): lo
  que le queda al límite global más ``stop_grace`` y un margen. **Antes eran 600 s fijos**:
  un trabajador que terminaba pronto (base no válida, NapBack que no sirve para la
  instancia, un error) abortaba todo el trabajo MPI a los 600 s aunque los demás siguieran
  dentro de su límite. Sin límite global espera indefinidamente: el orquestador siempre
  manda el cierre o aborta el trabajo.
* Si hay abandonados, el rango 0 escribe los resultados y aborta el trabajo MPI
  (:func:`finish_orchestrator`): el cierre ordenado los esperaría para siempre.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Callable, Optional

from . import protocol
from .communicator import Communicator
from .config import ParaBackConfig
from .orchestrator import ParaBackResult
from .protocol import StopReason

log = logging.getLogger("paraback.shutdown")

#: Motivo del ``STOP`` final con el que el orquestador libera a los trabajadores.
SHUTDOWN = StopReason.SHUTDOWN

#: Margen sobre ``límite restante + stop_grace`` que un trabajador espera al cierre (s).
SHUTDOWN_MARGIN = 120.0


def worker_shutdown_wait(config: ParaBackConfig, now: Optional[float] = None) -> Optional[float]:
    """Segundos que un trabajador que ya terminó espera ``STOP(shutdown)``; ``None`` = sin límite.

    El orquestador termina, como mucho, al agotar ``time_limit`` (contado desde
    ``start_time``) más ``stop_grace``; después manda el cierre o aborta el trabajo.
    """
    if config.time_limit is None:
        return None
    current = time.time() if now is None else now
    elapsed = 0.0 if config.start_time is None else max(0.0, current - config.start_time)
    return max(0.0, config.time_limit - elapsed) + config.stop_grace + SHUTDOWN_MARGIN


def drain_until_shutdown(channel: Communicator, timeout: Optional[float]) -> bool:
    """Descarta mensajes hasta recibir ``STOP(shutdown)``; ``False`` si expira ``timeout`` (``None``: nunca)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        wait = 1.0
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            wait = min(remaining, wait)
        message = channel.wait(timeout=wait)
        if message is not None and message.kind is protocol.MessageKind.STOP and protocol.read_stop(message) == SHUTDOWN:
            return True


def finish_orchestrator(
    result: ParaBackResult,
    config: ParaBackConfig,
    on_result: Callable[[ParaBackResult, ParaBackConfig], int],
    close: Callable[[], None],
    abort: Callable[[int], None],
) -> int:
    """Cierre del rango 0: resultados y cierre ordenado, o resultados y ``abort`` si hay abandonados.

    ``on_result`` escribe el backbone y las métricas y devuelve el código de salida; se
    llama exactamente una vez.
    """
    if result.abandoned:
        # Hay rangos que no atienden a STOP: el cierre ordenado (Waitall, Barrier) los
        # esperaría para siempre. Se escriben los resultados y se mata el trabajo MPI;
        # el lanzador lee el código del archivo de estado que deja on_result.
        code = on_result(result, config)
        log.warning("trabajadores %s abandonados tras STOP: abortando el trabajo MPI", result.abandoned)
        sys.stdout.flush()
        sys.stderr.flush()
        abort(code)
        return code
    close()
    return on_result(result, config)


__all__ = ["SHUTDOWN", "SHUTDOWN_MARGIN", "drain_until_shutdown", "finish_orchestrator", "worker_shutdown_wait"]

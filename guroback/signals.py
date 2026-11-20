"""Señales del proceso: que ``SIGTERM`` cierre los backends en lugar de matar el proceso en seco."""
from __future__ import annotations

import signal
import threading
from typing import Any


def exit_on_sigterm() -> None:
    """Convierte ``SIGTERM`` en ``SystemExit`` para que los ``with`` cierren los backends.

    Sin esto, ``timeout``, ``kill`` o ``mpirun`` terminan el proceso Python sin
    ejecutar ``close()``: los subprocesos mueren igualmente (``PREEXEC`` de
    :mod:`guroback.line_server`), pero las copias temporales de RoundingBack y los
    directorios de trabajo de NapBack (con CNFs de hasta gigabytes) quedarían en
    ``/tmp``. Sólo tiene efecto en el hilo principal (``signal.signal`` lo exige) y si
    nadie instaló otro manejador. Lo usan las dos CLI, también en modo local.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    if signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None):
        return

    def _raise(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _raise)


__all__ = ["exit_on_sigterm"]

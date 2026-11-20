"""Cliente común de los servidores de backbones con protocolo de líneas.

RoundingBack (``roundingsat --backbone-server=1``) y NapBack (``napback-server``,
sobre CaDiCaL) hablan el mismo protocolo (``docs/PLAN_PARABACK.md`` §3.3): una orden
por línea en stdin, respuestas ``r …`` en stdout, la solución en una línea
``v ±l …`` tras ``r sat``, contadores en ``s clave valor …`` y unidades vivas por
un *pipe* heredado. Este módulo reúne lo que no depende del solver:

* :func:`popen_die_with_parent`: los subprocesos reciben ``SIGKILL`` si muere el
  proceso Python que los lanzó (``PR_SET_PDEATHSIG``, Linux), para no dejar
  huérfanos cuando ``mpirun`` aborta un rango.
* :class:`LineServer`: arranque (espera ``r ready [n]``), órdenes, lectura de la
  solución y de las estadísticas, ``SIGINT`` y cierre ordenado. Es seguro usarlo
  desde varios hilos: las órdenes se serializan con :attr:`LineServer.io_lock` (una
  secuencia como ``solve`` + ``read_solution`` se hace con él tomado) y el *pipe* de
  unidades vivas tiene su propio cerrojo frente a ``close``.
* :func:`find_executable`: la búsqueda de un ejecutable (variable de entorno,
  ruta del repositorio, ``PATH``) que repetía cada backend.

Los errores se lanzan con la clase que indique cada backend (``RoundingSatError``,
``NapBackError``), así que su contrato hacia fuera no cambia.
"""
from __future__ import annotations

import collections
import fcntl
import os
import resource
import select
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, TextIO, Tuple, Type

from .backend import SolverCrashed
from .signals import exit_on_sigterm

#: Una escritura de hasta PIPE_BUF bytes en una tubería es atómica (POSIX): entera, o
#: EAGAIN si no cabe. Más larga, en una tubería no bloqueante el sistema escribe sólo lo
#: que cabe y no da error, y el resto de la línea se perdía: el servidor pegaba el último
#: literal cortado al primero de la siguiente línea ("369" + "-189" → la unidad falsa 369;
#: ronda R60, ped3). Las unidades vivas se escriben, pues, en líneas de como mucho PIPE_BUF.
LIVE_LINE_BYTES = getattr(select, "PIPE_BUF", 4096)
#: Capacidad pedida para la tubería de unidades vivas (Linux, F_SETPIPE_SZ; sin permiso se
#: queda la de 64 KiB): un lote de backbones de 40 000 literales ocupa ~240 KB.
LIVE_PIPE_BYTES = 1 << 20


def live_lines(literals: Sequence[int], limit: int = LIVE_LINE_BYTES) -> List[bytes]:
    """Los literales en líneas completas de como mucho ``limit`` bytes (con el salto de línea)."""
    lines: List[bytes] = []
    current: List[str] = []
    size = 0
    for lit in literals:
        token = str(lit)
        extra = len(token) + (1 if current else 0)
        if current and size + extra + 1 > limit:
            lines.append((" ".join(current) + "\n").encode())
            current, size, extra = [], 0, len(token)
        current.append(token)
        size += extra
    if current:
        lines.append((" ".join(current) + "\n").encode())
    return lines


class LineServerError(RuntimeError):
    """El servidor falló o respondió algo inesperado."""


class ServerCrashed(LineServerError, SolverCrashed):
    """El servidor murió por una señal o por falta de memoria (ver :class:`SolverCrashed`)."""


#: Segundos que se espera el código de salida de un servidor al que no se pudo escribir (``LineServer._write_failure``).
WRITE_FAILURE_WAIT = 5.0

#: Códigos de salida que delatan una muerte, no una respuesta: señal (negativos) o ``abort``
#: (134, ``std::bad_alloc`` o ``OutOfMemoryException`` sin capturar) o ``SIGKILL`` del sistema (137).
CRASH_EXIT_CODES = (134, 137)


def is_crash_exit(code: Optional[int], output: str = "") -> bool:
    """``True`` si ``code`` (y la salida) indican que el proceso murió en vez de responder."""
    if code is not None and (code < 0 or code in CRASH_EXIT_CODES):
        return True
    return "OutOfMemoryException" in output or "bad_alloc" in output or "failed to map segment" in output


def _resolve_prctl() -> Any:
    if os.name != "posix" or os.uname().sysname != "Linux":
        return None
    try:
        import ctypes

        return ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None


#: ``prctl(2)`` resuelto en el padre: el ``preexec_fn`` sólo hace una llamada C, sin
#: importar ni reservar memoria (``preexec_fn`` corre entre ``fork`` y ``exec`` y
#: ParaBack tiene hilos vivos).
_PRCTL = _resolve_prctl()
_PR_SET_PDEATHSIG = 1


def _die_with_parent() -> None:  # pragma: no cover - se ejecuta en el hijo, antes de exec
    """El hijo recibe ``SIGKILL`` si muere el proceso que lo lanzó.

    El ``close()``/``__del__`` del backend no se ejecuta si el proceso Python
    muere por una señal (p. ej. ``mpirun`` abortando un rango de ParaBack):
    sin esto el subproceso quedaría huérfano.
    """
    _PRCTL(_PR_SET_PDEATHSIG, int(signal.SIGKILL))


#: ``preexec_fn`` de los subprocesos de los solvers (``None`` fuera de Linux).
PREEXEC = _die_with_parent if _PRCTL is not None else None


def address_space_limit(memory_limit_mb: Optional[float]) -> Optional[int]:
    """``RLIMIT_AS`` en bytes para ``memory_limit_mb`` (``None`` = sin límite), nunca por encima del límite duro."""
    if memory_limit_mb is None:
        return None
    limit = int(memory_limit_mb * 1024 * 1024)
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    return limit


def preexec_with_limit(memory_limit_mb: Optional[float]) -> Any:
    """:data:`PREEXEC` más ``RLIMIT_AS`` del hijo si hay ``memory_limit_mb`` (CLAUDE.md "Estado", punto 5).

    Los servidores (RoundingSat, NapBack, Cadiback, Naps) no tienen límite de memoria propio:
    sin esto crecen hasta que el sistema mata al trabajo entero (base25: 7 muertes a 54–63 GB).
    Con el límite, ``malloc`` falla y el servidor aborta (``bad_alloc``/``OutOfMemoryException``),
    lo que el backend convierte en :class:`ServerCrashed` y el trabajador en una retirada.
    """
    limit = address_space_limit(memory_limit_mb)
    if limit is None:
        return PREEXEC
    rlimit = (limit, limit)
    setrlimit = resource.setrlimit
    rlimit_as = resource.RLIMIT_AS
    prctl = _PRCTL
    pdeathsig = int(signal.SIGKILL)

    def _preexec() -> None:  # pragma: no cover - se ejecuta en el hijo, antes de exec
        if prctl is not None:
            prctl(_PR_SET_PDEATHSIG, pdeathsig)
        setrlimit(rlimit_as, rlimit)

    return _preexec


def popen_die_with_parent(argv: Sequence[str], memory_limit_mb: Optional[float] = None, **kwargs: Any) -> "subprocess.Popen[str]":
    """``subprocess.Popen`` cuyo hijo muere con este proceso (ver :data:`PREEXEC`) y, con ``memory_limit_mb``, con ``RLIMIT_AS``."""
    return subprocess.Popen(list(argv), preexec_fn=preexec_with_limit(memory_limit_mb), text=True, **kwargs)


def find_executable(env_var: str, candidates: Iterable[str], path_names: Iterable[str] = ()) -> Optional[str]:
    """Primer ejecutable entre ``$env_var``, ``candidates`` y ``path_names`` (en ``PATH``), o ``None``."""
    paths = [os.environ.get(env_var, ""), *map(str, candidates)]
    paths += [found for name in path_names if (found := shutil.which(name))]
    for path in paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


class LineServer:
    """Proceso servidor con el protocolo de líneas de ``BackboneServer``."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        label: str,
        error: Type[Exception] = LineServerError,
        crashed: Type[Exception] = ServerCrashed,
        memory_limit_mb: Optional[float] = None,
    ) -> None:
        self.argv = list(argv)
        self.label = label
        self._error = error
        #: Excepción cuando el servidor muere (señal, memoria): subclase de ``error`` y de :class:`SolverCrashed`.
        self._crashed = crashed
        #: ``PARABACK_SERVER_LOG=<dir>``: registro de cada orden, respuesta y unidad viva con su instante
        #: (``<dir>/<label>-<pid>.log``), para reproducir una sesión del servidor que dio un hecho falso.
        self._log: Optional[TextIO] = None
        #: Serializa las órdenes y sus respuestas: tómelo para una secuencia (``solve`` + ``read_solution``).
        self.io_lock = threading.RLock()
        self._live_lock = threading.Lock()
        # Canal de unidades vivas: el servidor hereda el extremo de lectura; el de
        # escritura es no bloqueante (una unidad que no cabe se descarta: es opcional).
        live_read, self.live_write = os.pipe()
        os.set_blocking(self.live_write, False)
        try:
            fcntl.fcntl(self.live_write, getattr(fcntl, "F_SETPIPE_SZ", 1031), LIVE_PIPE_BYTES)
        except OSError:
            pass  # sin permiso (pipe-max-size) o fuera de Linux: la capacidad por defecto
        self.live_read = live_read
        try:
            self._proc = popen_die_with_parent(
                self.argv,
                memory_limit_mb,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                pass_fds=(live_read,),
            )
        except BaseException:
            os.close(self.live_write)
            raise
        finally:
            os.close(live_read)
        self._diagnostics: Deque[str] = collections.deque(maxlen=50)
        self.num_vars = 0
        log_dir = os.environ.get("PARABACK_SERVER_LOG")
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
                self._log = open(os.path.join(log_dir, f"{label}-{self._proc.pid}.log"), "a", encoding="utf-8")
                self._log.write(f"# {time.time():.3f} argv {' '.join(self.argv)}\n")
            except OSError:
                self._log = None
        ready = self._read_reply()
        if not ready.startswith("ready"):
            raise self._error(f"el servidor no arrancó: {ready}")
        parts = ready.split()
        self.num_vars = int(parts[1]) if len(parts) > 1 else 0

    @property
    def pid(self) -> int:
        return self._proc.pid

    def alive(self) -> bool:
        return self._proc.poll() is None

    def _read_line(self, prefix: str) -> str:
        return self.read_tagged((prefix,))[1]

    def read_tagged(self, prefixes: Sequence[str]) -> Tuple[str, str]:
        """La siguiente línea que empieza por alguno de ``prefixes``: ``(prefijo, resto)``.

        Las demás se guardan como diagnóstico. Sirve para leer las líneas ``v`` que un solve
        ``maxflip`` emite antes de su respuesta ``r`` (:meth:`send` + ``read_tagged(("v", "r "))``).
        """
        assert self._proc.stdout is not None
        with self.io_lock:
            while True:
                line = self._proc.stdout.readline()
                if line == "":
                    code = self._proc.poll()
                    tail = "\n".join(self._diagnostics)
                    error = self._crashed if is_crash_exit(code, tail) else self._error
                    raise error(f"{self.label} terminó inesperadamente (código {code}); última salida:\n{tail}")
                line = line.rstrip("\n")
                self._trace("<", line)
                for prefix in prefixes:
                    if line.startswith(prefix):
                        return prefix, line[len(prefix):]
                self._diagnostics.append(line)

    def _read_reply(self) -> str:
        return self._read_line("r ")

    def _trace(self, direction: str, line: str) -> None:
        if self._log is not None:
            try:
                self._log.write(f"{time.time():.3f} {direction} {line}\n")
                self._log.flush()
            except OSError:
                self._log = None

    def _write(self, line: str) -> None:
        assert self._proc.stdin is not None
        self._trace(">", line)
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._write_failure(exc) from exc

    def _write_failure(self, exc: OSError) -> Exception:
        """Error de una orden que no se pudo escribir: el servidor cerró su entrada, casi siempre porque murió.

        Como al leer (:meth:`read_tagged`), una muerte por señal o memoria es :attr:`_crashed`, una
        retirada, y no un error del protocolo, que tumba la ejecución (h2-extraer, 2026-11-12:
        comppebphp_opt_pyrheightequalsnholes_38, el servidor de RoundingSat murió y escribirle falló).
        La tubería se rompe al cerrarse los descriptores del proceso, justo antes de que el sistema
        lo dé por terminado: se espera un momento a su código de salida.
        """
        try:
            code: Optional[int] = self._proc.wait(timeout=WRITE_FAILURE_WAIT)
        except subprocess.TimeoutExpired:
            code = None
        tail = "\n".join(self._diagnostics)
        error = self._crashed if is_crash_exit(code, tail) else self._error
        return error(f"no se pudo escribir en {self.label} (código {code}): {exc}; última salida:\n{tail}")

    def send(self, line: str) -> None:
        """Envía una orden sin esperar su respuesta (el llamador la lee con :meth:`read_tagged`)."""
        with self.io_lock:
            self._write(line)

    def command(self, line: str) -> str:
        """Envía una orden y devuelve la respuesta (sin el prefijo ``r``)."""
        with self.io_lock:
            self._write(line)
            reply = self._read_reply()
        if reply.startswith("error"):
            raise self._error(f"{self.label} rechazó {line!r}: {reply}")
        return reply

    def read_solution(self) -> Dict[int, bool]:
        """Lee la línea ``v`` que sigue a una respuesta ``sat``."""
        with self.io_lock:
            line = self._read_line("v")
        return self.parse_model(line)

    @staticmethod
    def parse_model(line: str) -> Dict[int, bool]:
        """Los literales de una línea ``v`` (sin el prefijo) como ``{variable: valor}``."""
        values: Dict[int, bool] = {}
        for token in line.split():
            lit = int(token)
            values[abs(lit)] = lit > 0
        return values

    def query(self, line: str, prefix: str) -> str:
        """Envía una orden que responde con una línea de prefijo ``prefix`` (no ``r``) y la devuelve."""
        with self.io_lock:
            self._write(line)
            return self._read_line(prefix)

    def stats(self) -> Dict[str, float]:
        with self.io_lock:
            self._write("stats")
            line = self._read_line("s ")
        tokens = line.split()
        return {tokens[i]: float(tokens[i + 1]) for i in range(0, len(tokens) - 1, 2)}

    def interrupt(self) -> None:
        if self.alive():
            self._proc.send_signal(signal.SIGINT)

    def write_live(self, literals: Sequence[int]) -> bool:
        """Escribe unidades vivas en líneas completas; ``False`` si no cupieron todas o el canal está cerrado.

        Las unidades son opcionales: las que no caben se descartan, pero nunca se escribe
        media línea (ver ``LIVE_LINE_BYTES``). Con el cerrojo de las unidades vivas: ``close``
        no puede cerrar el descriptor (y el sistema reutilizar su número para otro archivo)
        mientras se escribe en él.
        """
        if not literals:
            return False
        with self._live_lock:
            if self.live_write < 0:
                return False
            for line in live_lines(literals):
                try:
                    written = os.write(self.live_write, line)
                except (BlockingIOError, BrokenPipeError, OSError):
                    return False  # tubería llena: el resto se descarta, sin cortar ninguna línea
                if written != len(line):
                    # No debería ocurrir (escritura atómica); si ocurre, el servidor tiene media
                    # línea: se cierra el canal para que nunca la complete con otra.
                    os.close(self.live_write)
                    self.live_write = -1
                    raise self._error(f"{self.label}: escritura parcial de unidades vivas ({written} de {len(line)} bytes)")
                self._trace("live", line.decode().rstrip("\n"))
        return True

    def close(self) -> None:
        with self._live_lock:
            fd, self.live_write = self.live_write, -1
        if fd >= 0:
            os.close(fd)
        if self._proc.poll() is None:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write("quit\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        for stream in (self._proc.stdin, self._proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    # BrokenPipeError: el servidor murió con una orden aún en el búfer de su entrada, que
                    # close() intenta entregar. El descriptor queda cerrado igualmente; antes la excepción
                    # tapaba la caída del servidor y tumbaba la ejecución (h2-extraer, comppebphp_38).
                    pass


__all__: List[str] = ["LineServer", "LineServerError", "PREEXEC", "exit_on_sigterm", "find_executable", "popen_die_with_parent"]

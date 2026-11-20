"""Trabajador base de ParaBack: el protocolo común al trabajador genérico y a los nativos.

Antes :class:`paraback.worker.Worker` y :class:`paraback.cadiback_worker.CadibackWorker`
repetían el envío de ``HELLO``/``ERROR``/``DONE``, la captura de errores, el hilo de
escucha, el estado del z* relevado (``BASE_HINT``) frente a ``STOP`` y la elección de la
base. :class:`BaseWorker` los reúne; cada trabajador sólo aporta cómo se abre su solver,
cómo se interrumpe y cómo extrae.

Ciclo de :meth:`BaseWorker.run`::

    capacidades ──falla──▶ HELLO {} + ERROR
        │
        HELLO ──▶ execute() ──▶ DONE (estado + WorkerStats)
                      │
                      ├── InconsistentKnowledge ──▶ ERROR "conocimiento inconsistente: …"
                      └── otra excepción ─────────▶ ERROR "Tipo: mensaje"

Sustitución en el sitio: si :meth:`execute` descubre que su solver no admite la instancia
(:class:`~guroback.backend.Incompatible`, p. ej. Naps no la traduce) y hay un sustituto
(``config.substitute``, ``--substitute``), el rango no se retira: el mismo proceso arranca el
sustituto con el orden del hueco y su ``HELLO`` dice a quién reemplaza, para que el orquestador
le reenvíe todo lo que ya se sabe. Sin sustituto, o si el sustituto tampoco puede, es una
retirada más (``docs/PLAN_EVALUACION.md`` P5–P6).

Elección de la base (:meth:`BaseWorker.initial_base`), en este orden: la x* del usuario
(``--base-solution``), el z* del usuario (``--optimum``), el z* y x* relevados por otro
trabajador (``BASE_HINT``) y, si no hay nada de eso, la búsqueda propia (con reintento si
llega un ``BASE_HINT`` mientras se busca; eso lo hace cada trabajador a su manera).
"""
from __future__ import annotations

import abc
import contextvars
import logging
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from guroback.backend import Incompatible, SolverCrashed
from guroback.extractor import InconsistentKnowledge, RunStatus

from . import protocol
from .base_solution import read_solution_values
from .communicator import Communicator
from .config import ORCHESTRATOR_RANK, ParaBackConfig, WorkerSpec
from .coordination import BaseHintState
from .listener import ListeningCommunicator
from .stats import WorkerStats, max_rss_mb
from .trace import Tracer

log = logging.getLogger("paraback.worker")


#: ``(especificación, motivo)`` del trabajador al que reemplaza el que se está construyendo
#: (:meth:`BaseWorker._hand_over`); ``None`` para un trabajador normal.
_REPLACING: "contextvars.ContextVar[Optional[Tuple[str, str]]]" = contextvars.ContextVar("paraback_replacing", default=None)


class WorkerUnavailable(RuntimeError):
    """El solver del trabajador no se puede usar en este entorno (el mensaje va tal cual en ERROR)."""


@dataclass(frozen=True)
class BaseChoice:
    """De dónde sale la base de un trabajador antes de buscarla."""

    #: z* (``None`` en problemas de decisión o si sólo se sabe que hay que buscarlo).
    optimum: Optional[float]
    #: x* por nombre, si se conoce (se verifica en vez de buscarla).
    values: Optional[Dict[str, bool]]
    #: ``"file"`` (``--base-solution``), ``"option"`` (``--optimum``) o ``"hint"`` (``BASE_HINT``).
    source: str


class BaseWorker(abc.ABC):
    """Un rango trabajador: habla el protocolo y delega la extracción en :meth:`execute`."""

    def __init__(self, comm: Communicator, config: ParaBackConfig, spec: WorkerSpec) -> None:
        if comm.rank == ORCHESTRATOR_RANK:
            raise ValueError("el rango del orquestador no puede ser trabajador")
        self.comm = comm
        self.config = config
        self.spec = spec
        self.rank = comm.rank
        #: ``(especificación, motivo)`` del trabajador al que sustituye en el sitio, o ``None``.
        self.replaces = _REPLACING.get()
        self.tracer = Tracer(config.trace_dir, comm.rank, append=self.replaces is not None)
        #: Fábrica de backends para el sustituto (la de :func:`paraback.worker.make_worker`).
        self.substitute_factory: Optional[Callable[..., Any]] = None
        #: Canal efectivo: ``comm`` o un :class:`ListeningCommunicator` sobre él durante ``run``.
        #: Tras ``run`` conserva los mensajes que el hilo de escucha dejó en búfer (``paraback/mpi.py``).
        self.channel: Communicator = comm
        self.hint = BaseHintState()
        self.stats = WorkerStats(spec=spec.describe(), base_from_file=bool(config.base_solution))
        self.listener: Optional[ListeningCommunicator] = None

    # ------------------------------------------------------------------ lo que aporta cada trabajador
    @property
    def solver_name(self) -> str:
        return self.spec.solver

    @abc.abstractmethod
    def capabilities(self) -> Mapping[str, bool]:
        """Capacidades que anuncia ``HELLO``; :class:`WorkerUnavailable` (u otra excepción) si no hay solver."""

    @abc.abstractmethod
    def execute(self) -> RunStatus:
        """Abre el solver, extrae y devuelve el estado de ``DONE`` (rellena ``self.stats``)."""

    @abc.abstractmethod
    def interrupt(self) -> None:
        """Interrumpe lo que esté resolviendo ahora (seguro desde el hilo de escucha)."""

    def interrupt_base(self) -> None:
        """Interrumpe la búsqueda de la base propia porque llegó un ``BASE_HINT``."""
        self.interrupt()

    def on_tick_extra(self) -> None:
        """Trabajo propio de cada vuelta del hilo de escucha (por defecto nada)."""

    # ------------------------------------------------------------------ protocolo
    def run(self) -> None:
        try:
            handover = self._run()
        finally:
            self.tracer.close()
        if handover is not None:
            self._hand_over(handover)

    def _run(self) -> Optional[str]:
        """El protocolo de un trabajador; devuelve el motivo si hay que ceder el rango a un sustituto."""
        started = time.perf_counter()
        try:
            capabilities = dict(self.capabilities())
        except Exception as exc:  # noqa: BLE001 - p. ej. el módulo del solver no se puede importar
            message = str(exc) if isinstance(exc, WorkerUnavailable) else f"{type(exc).__name__}: {exc}"
            self.send(protocol.hello(self.rank, self.spec.describe(), self.solver_name, {}, self.replaces))
            self.send(protocol.error(self.rank, message))
            return None
        self.send(protocol.hello(self.rank, self.spec.describe(), self.solver_name, capabilities, self.replaces))
        try:
            status = self.execute()
        except (KeyboardInterrupt, SystemExit) as exc:
            # SIGTERM en el proceso del trabajador (bajo MPI, mpirun la reenvía a todos los rangos):
            # el ``with`` del backend ya lo cerró; se informa como interrumpido para que el
            # orquestador, que también la recibe, escriba lo que se sabe en vez de abortar.
            log.warning("trabajador %d (%s) interrumpido por una señal (%s)", self.rank, self.solver_name, type(exc).__name__)
            status = RunStatus.INTERRUPTED
        except InconsistentKnowledge as exc:
            self.send(protocol.error(self.rank, f"conocimiento inconsistente: {exc}"))
            return None
        except Incompatible as exc:
            if self.config.substitute is not None and self.replaces is None and not self.hint.stop_requested:
                first = str(exc).splitlines()[0] if str(exc) else ""
                return f"{type(exc).__name__}: {first} (tras {time.perf_counter() - started:.0f} s)"
            self._retire(exc)
            return None
        except (SolverCrashed, MemoryError) as exc:
            self._retire(exc)
            return None
        except Exception as exc:  # noqa: BLE001 - el orquestador debe enterarse de cualquier fallo
            log.error("trabajador %d (%s) falló: %s\n%s", self.rank, self.solver_name, exc, traceback.format_exc())
            self.send(protocol.error(self.rank, f"{type(exc).__name__}: {exc}"))
            return None
        self.stats.max_rss_mb = max_rss_mb()
        self.send(protocol.done(self.rank, status, self.stats.to_dict()))
        return None

    def _retire(self, exc: BaseException) -> None:
        """El solver murió o no hay memoria: no hay respuesta en la que desconfiar, sólo falta de
        ella. Retirada (``ERROR`` no fatal): el orquestador sigue con los demás."""
        message = f"{type(exc).__name__}: {exc}" if str(exc) else f"{type(exc).__name__}: memoria agotada"
        log.warning("trabajador %d (%s) se retira: %s", self.rank, self.solver_name, message)
        self.send(protocol.error(self.rank, message, fatal=False))

    def _hand_over(self, reason: str) -> None:
        """Sustitución en el sitio: el sustituto (``config.substitute``, con el orden del hueco) sigue en este rango.

        Usa el mismo canal, así que recibe lo que el hilo de escucha dejó en búfer (``STOP``,
        ``BASE_HINT``); lo demás se lo reenvía el orquestador al ver su ``HELLO``.
        """
        from .launch import substitute_for  # importan este módulo
        from .worker import make_worker

        assert self.config.substitute is not None
        spec = substitute_for(self.config.substitute, self.spec, self.rank - 1)
        log.warning("trabajador %d: %s no admite la instancia (%s); el hueco sigue con %s",
                    self.rank, self.spec.describe(), reason, spec.describe())
        token = _REPLACING.set((self.spec.describe(), reason))
        try:
            replacement = make_worker(self.channel, self.config, spec, self.substitute_factory)
        finally:
            _REPLACING.reset(token)
        replacement.run()
        self.channel = replacement.channel

    def send(self, message: protocol.Message) -> None:
        self.comm.send(ORCHESTRATOR_RANK, message)

    # ------------------------------------------------------------------ hilo de escucha
    def start_listener(self, *, base_hints: bool = True, updates: bool = True, buffer_updates: bool = True) -> ListeningCommunicator:
        """Arranca el hilo de escucha sobre ``comm``; :meth:`stop_listener` lo detiene."""
        listener = ListeningCommunicator.for_worker(
            self.comm, self, self.config.poll_interval, base_hints=base_hints, updates=updates,
            buffer_updates=buffer_updates, tracer=self.tracer if self.tracer.enabled else None,
        )
        self.listener = listener
        self.channel = listener
        listener.start()
        return listener

    def stop_listener(self) -> None:
        if self.listener is not None:
            self.listener.stop_listening()
            self.stats.listener_time = self.listener.update_watch.total

    def on_stop_signal(self) -> None:
        # Visible de inmediato (no sólo cuando se drena el búfer, que nunca ocurre durante el
        # solve base): un STOP en ese momento debe ganarle a cualquier BASE_HINT pendiente.
        self.hint.stop_requested = True
        if self.config.interrupt_on_stop:
            self.interrupt()

    def on_base_hint(self, optimum: float, values: Optional[Mapping[str, bool]] = None) -> None:
        if self.hint.note(optimum, values):
            self.interrupt_base()

    def on_update(self, update: protocol.Facts) -> None:
        """Hechos ajenos llegados durante la extracción (sólo si ``start_listener(updates=True)``)."""

    def on_tick(self) -> None:
        # Un interrupt() que llega antes de que arranque el solve base se pierde: mientras
        # haya un BASE_HINT sin consumir y la base siga sin resolver, se repite.
        if self.hint.should_reinterrupt():
            self.interrupt_base()
        self.on_tick_extra()

    # ------------------------------------------------------------------ base
    def initial_base(self) -> Optional[BaseChoice]:
        """La base que ya se conoce sin buscarla, o ``None`` si hay que buscarla."""
        if self.config.base_solution:
            # x* del usuario (--base-solution, ya validada en la CLI): se verifica, no se busca.
            return BaseChoice(self.config.optimum, read_solution_values(self.config.base_solution), "file")
        if self.config.optimum is not None:
            return BaseChoice(self.config.optimum, None, "option")
        return self.take_hint()

    def take_hint(self) -> Optional[BaseChoice]:
        """El z*/x* relevado pendiente (``None`` si no hay o si llegó ``STOP``)."""
        hint = self.hint.take()
        if hint is None:
            return None
        self.stats.base_from_hint = True
        return BaseChoice(hint[0], hint[1], "hint")


__all__ = ["BaseChoice", "BaseWorker", "WorkerUnavailable"]

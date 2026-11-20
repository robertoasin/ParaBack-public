"""Trabajador de simetrías: cierra los hechos bajo el grupo de simetrías de la instancia.

No sondea nada. Al arrancar calcula los generadores del grupo de simetrías
(:mod:`paraback.symmetry`: automorfismos del grafo de la instancia, cada uno verificado con
enteros) y después:

* con la base ``x*``: publica las imágenes ``g(x*)`` (otras soluciones óptimas, comprobadas
  con enteros antes de publicarse) y lo que refutan, hasta ``images=<n>`` soluciones;
* con cada hecho ajeno nuevo: publica su órbita (un backbone ``l`` → todos los ``g(l)``; una
  refutada ``x_i`` → todas las variables a las que el grupo lleva ``x_i``).

Sin simetrías (ningún generador no trivial) se retira; también si el grafo supera
``max_vertices`` (bliss necesita más de 12 GB con los 785 000 vértices de ped3) o si el
detector se queda sin memoria o muere: un pasivo que no cabe se retira, nunca tumba la ejecución. El
detector corre en un proceso hijo (:class:`~paraback.symmetry.IsolatedDetection`) con
``RLIMIT_AS`` = ``MemoryLimit`` (lo pone ``--memory-limit``); con muchos generadores bliss no
cabe aunque el grafo sea pequeño (knapPI_14: 80 000 vértices, 70 GB en el piloto de ev3).
Parámetros: ``images`` (por defecto 200: cuántas imágenes de ``x*`` se exploran por anchura),
``max_vertices`` (por defecto 200 000) y ``MemoryLimit`` (MB).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from guroback.extractor import InconsistentKnowledge

from .communicator import Communicator
from .config import ParaBackConfig, WorkerSpec
from .passive_worker import PassiveWorker
from .symmetry import (
    DetectionFailed,
    Generator,
    IsolatedDetection,
    SymmetryError,
    graph_size,
    literal_orbit,
    solution_images,
    var,
    variable_orbit,
)

log = logging.getLogger("paraback.symmetry")

DEFAULT_IMAGES = 200
#: Vértices del grafo a partir de los cuales el trabajador se retira sin intentarlo.
DEFAULT_MAX_VERTICES = 200_000


def is_available() -> Tuple[bool, str]:
    try:
        import igraph  # type: ignore[import-untyped]  # noqa: F401 - sólo se comprueba que está
    except ImportError as exc:
        return False, f"python-igraph no instalado (pip install python-igraph): {exc}"
    return True, ""


class SymmetryWorker(PassiveWorker):
    """Cierre de los hechos bajo las simetrías de la instancia (``solver = symmetry``)."""

    def __init__(self, comm: Communicator, config: ParaBackConfig, spec: WorkerSpec) -> None:
        super().__init__(comm, config, spec)
        self.generators: List[Generator] = []
        self._index: Dict[str, int] = {}
        for key in spec.params:
            if key not in ("images", "max_vertices", "MemoryLimit"):
                raise ValueError(f"symmetry: parámetro desconocido {key!r} (sólo images=<n>, max_vertices=<n> y MemoryLimit=<MB>)")
        self.images = int(spec.params.get("images", DEFAULT_IMAGES))
        self.max_vertices = int(spec.params.get("max_vertices", DEFAULT_MAX_VERTICES))
        limit = spec.params.get("MemoryLimit")
        self.memory_limit_mb: Optional[float] = float(limit) if limit is not None else None

    def capabilities(self) -> Dict[str, bool]:
        ok, reason = is_available()
        if not ok:
            from .worker_base import WorkerUnavailable

            raise WorkerUnavailable(reason)
        return dict(super().capabilities())

    # ------------------------------------------------------------------ PassiveWorker
    def prepare(self) -> bool:
        assert self.instance is not None
        self._index = {name: i for i, name in enumerate(self.instance.names)}
        vertices, edges = graph_size(self.instance)
        self.stats.native["graph_vertices"] = float(vertices)
        if vertices > self.max_vertices:
            log.warning("trabajador %d (symmetry): grafo de %d vértices > max_vertices=%d; se retira", self.rank, vertices, self.max_vertices)
            return False
        start = time.perf_counter()
        # bliss no se puede interrumpir: la detección va en un proceso hijo, que un hilo espera, y
        # si llega STOP antes de que acabe se mata y el trabajador se retira sin esperarla (en R90
        # el orquestador esperaba los 60 s de stop_grace a este trabajador: injcomp pasaba de 10 s a 70).
        result: List[Tuple[List[Generator], Dict[str, float]]] = []
        failure: List[BaseException] = []
        detection = IsolatedDetection(self.config.instance, self.memory_limit_mb)

        def detect() -> None:
            try:
                result.append(detection.run())
            except BaseException as exc:  # noqa: BLE001 - se relanza en el hilo principal
                failure.append(exc)

        thread = threading.Thread(target=detect, name=f"paraback-symmetry-{self.rank}", daemon=True)
        thread.start()
        while thread.is_alive():
            if self.hint.stop_requested:
                detection.kill()
                log.info("trabajador %d (symmetry): STOP durante la detección de simetrías; se retira", self.rank)
                return False
            thread.join(self.config.poll_interval)
        error: Optional[BaseException] = failure[0] if failure else None
        if isinstance(error, (MemoryError, DetectionFailed)):
            # Como un solver que muere: el trabajador se retira y la ejecución sigue sin él.
            log.warning("trabajador %d (symmetry): %s con el grafo de %d vértices; se retira", self.rank, error, vertices)
            self.stats.native["detection_exit"] = float(detection.exit_code if detection.exit_code is not None else 0)
            self.generators = []
            return False
        if isinstance(error, SymmetryError):
            if "no instalado" in str(error):
                raise error
            # Un automorfismo que no es simetría exacta: error de construcción, nunca un hecho.
            raise InconsistentKnowledge(f"simetrías: {error}") from error
        if error is not None:
            raise error
        self.generators, metrics = result[0]
        self.stats.native.update(metrics)
        log.info(
            "trabajador %d (symmetry): %d generadores en %.2f s (grafo de %d vértices)",
            self.rank, len(self.generators), time.perf_counter() - start, int(metrics["graph_vertices"]),
        )
        return bool(self.generators)

    def derive_from_base(self) -> None:
        if self.x_star is None or self.images <= 0:
            return
        assert self.instance is not None
        names = self.instance.names
        values = [bool(self.x_star.get(name, False)) for name in names]
        refuted_total = 0
        for image in solution_images(values, self.generators, self.images):
            solution = {name: image[i] for i, name in enumerate(names)}
            refuted_total += self.refute_by_solution(solution, "imagen de x* por una simetría")
            if self.hint.stop_requested:
                break
        self.stats.native["image_refuted"] = float(refuted_total)
        log.info("trabajador %d (symmetry): las imágenes de x* refutan %d variables", self.rank, refuted_total)

    def derive_from_facts(self, backbone: Sequence[Tuple[str, bool]], refuted: Sequence[str]) -> None:
        assert self.instance is not None
        names = self.instance.names
        out_backbone: List[Tuple[str, bool]] = []
        for name, value in backbone:
            if self.hint.stop_requested:
                return
            i = self._index.get(name)
            if i is None:
                continue
            for lit in literal_orbit(2 * i if value else 2 * i + 1, self.generators):
                other = names[var(lit)]
                if other != name:
                    out_backbone.append((other, (lit & 1) == 0))
        out_refuted: List[str] = []
        for name in refuted:
            if self.hint.stop_requested:
                return
            i = self._index.get(name)
            if i is None:
                continue
            out_refuted.extend(names[v] for v in variable_orbit(i, self.generators) if v != i)
        if out_backbone or out_refuted:
            new_b, new_r = self.publish(out_backbone, out_refuted)
            self.stats.native["orbit_backbone"] = self.stats.native.get("orbit_backbone", 0.0) + new_b
            self.stats.native["orbit_refuted"] = self.stats.native.get("orbit_refuted", 0.0) + new_r


__all__ = ["SymmetryWorker", "is_available"]

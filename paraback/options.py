"""Opciones de la línea de comandos de ParaBack (las usan ``paraback/cli.py`` y ``paraback/experiments.py``).

El parser vive aparte para que el archivo de experimentos pueda validar sus ``options``
sin importar la CLI (antes cada uno importaba al otro dentro de una función).
"""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paraback", description="Extractor de backbones paralelo (portfolio) sobre MPI.")
    p.add_argument("instance", nargs="?", help="instancia .opb (admite .gz/.xz según el solver)")
    p.add_argument("backbone", nargs="?", help="archivo de salida 'b <lit>' ... 'b 0'")
    p.add_argument("--list-solvers", action="store_true", help="muestra los backends operativos y termina")
    g = p.add_argument_group("portfolio")
    g.add_argument("--config", default=None, metavar="FILE",
                   help="archivo TOML de experimentos (paraback/experiments.py); solo, lista los experimentos")
    g.add_argument("--experiment", default=None, metavar="NOMBRE",
                   help="experimento de --config: sus trabajadores, --time-limit y opciones")
    g.add_argument("--worker", action="append", default=[], metavar="SPEC",
                   help="trabajador 'solver[:order=...,chunks=...,Param=Valor]' (repetible)")
    g.add_argument("--portfolio", default="auto", metavar="auto|N",
                   help="sin --worker: portfolio por defecto con N trabajadores (auto = uno por solver disponible)")
    g.add_argument("--solvers", default=None, metavar="LISTA",
                   help="solvers del portfolio por defecto, separados por comas (por defecto: gurobi,scip,roundingsat disponibles)")
    g.add_argument("--threads-per-worker", type=int, default=1, metavar="T",
                   help="parámetro Threads de Gurobi/SCIP si el trabajador no lo fija (por defecto 1)")
    g.add_argument("--memory-limit", type=float, default=None, metavar="MB",
                   help="límite de memoria por trabajador si el trabajador no lo fija: Gurobi SoftMemLimit y SCIP "
                        "limits/memory (paran con LIMIT_REACHED en vez de que el sistema los mate); RoundingSat, "
                        "NapBack y Cadiback reciben RLIMIT_AS en su subproceso (al agotarlo abortan y el trabajador "
                        "se retira sin fallar la ejecución)")
    e = p.add_argument_group("ejecución")
    e.add_argument("--time-limit", type=float, default=None, metavar="S", help="límite global de reloj (s)")
    e.add_argument("--probe-slice", type=float, default=None, metavar="S", help="límite por sondeo (s); los inconclusos se aplazan")
    e.add_argument("--sync-every", type=int, default=1, metavar="N", help="sondeos entre sincronizaciones")
    e.add_argument("--no-share-solutions", action="store_true", help="no compartir las soluciones de los sondeos")
    e.add_argument("--share-interval", type=float, default=0.0, metavar="S",
                   help="reenviar los hechos ajenos a cada trabajador como mucho cada S s, acumulados "
                        "(por defecto 0: en cuanto llegan)")
    e.add_argument("--no-share-facts", action="store_true",
                   help="no reenviar hechos entre trabajadores: cada uno trabaja aislado y el orquestador "
                        "sólo une lo que demuestran")
    e.add_argument("--no-share-base-optimum", action="store_true",
                   help="no relevar z* al primer trabajador que lo encuentre (cada uno resuelve su base por su cuenta)")
    e.add_argument("--no-live-updates", action="store_true",
                   help="usar los backbones ajenos sólo entre sondeos (no dentro del solve en curso)")
    e.add_argument("--no-exact-check", action="store_true",
                   help="no comprobar con aritmética exacta las soluciones antes de refutar con ellas")
    e.add_argument("--share-hints", action="store_true", help="reenviar soluciones como pistas (warm start)")
    e.add_argument("--announce-intents", action="store_true",
                   help="anunciar el chunk que se sondea y contar los solapamientos (prepara la coordinación de intenciones)")
    e.add_argument("--no-interrupt", action="store_true", help="no interrumpir el sondeo en curso al recibir STOP")
    e.add_argument("--poll-interval", type=float, default=0.05, metavar="S", help="intervalo de sondeo de mensajes (s)")
    e.add_argument("--optimum", type=float, default=None, metavar="Z", help="óptimo conocido (GurobackOptimum)")
    e.add_argument("--stop-grace", type=float, default=60.0, metavar="S",
                   help="tras STOP, segundos de espera a los trabajadores; los que no terminan se abandonan "
                        "y se escriben los resultados conocidos (por defecto 60)")
    e.add_argument("--base-solution", default=None, metavar="FILE",
                   help="solución óptima previa (salida de RoundingSat: 's OPTIMUM FOUND' y líneas 'v'): "
                        "se valida y todos los trabajadores parten de ella sin resolver la base")
    e.add_argument("--base-only", action="store_true",
                   help="parar en cuanto un trabajador resuelva la base (z* y x*), sin sondear: el portfolio como "
                        "buscador de óptimos ('gana el primero')")
    e.add_argument("--solution-out", default=None, metavar="FILE",
                   help="escribe la base x* en el formato de la competición PB (lo que lee --base-solution)")
    e.add_argument("--float-max-bits", type=int, default=None, metavar="N",
                   help="si la instancia tiene enteros de más de N bits, los trabajadores en coma flotante (gurobi, scip, lp) "
                        "se sustituyen por --float-fallback (cabecera 'intsize=' o una pasada por el archivo)")
    e.add_argument("--float-fallback", default="scip-exact", metavar="SOLVER",
                   help="solver exacto que sustituye a los de coma flotante con --float-max-bits (por defecto scip-exact; "
                        "sólo hasta 66 bits: por encima el hueco queda vacío y siguen RoundingSat, NapBack y Cadiback)")
    e.add_argument("--substitute", default=None, metavar="SPEC",
                   help="trabajador que ocupa el hueco de uno que la instancia no admite: antes de lanzar (con "
                        "--float-max-bits, los de coma flotante por encima de 66 bits) o al prepararla (sustitución en el "
                        "sitio: NapBack o Cadiback cuando Naps no la traduce); hereda el orden del hueco o toma "
                        "random:<posición>. Sin esta opción el hueco queda vacío o el trabajador se retira. "
                        "Ejemplo: roundingsat:pguide=1")
    e.add_argument("--trace-dir", default=None, metavar="DIR",
                   help="escribe una traza de eventos por rango en DIR (tools/sharing_report.py)")
    e.add_argument("--stats-json", default=None, metavar="FILE", help="escribe las métricas de la ejecución en JSON")
    e.add_argument("--check", action="store_true",
                   help="al terminar, comprueba el backbone escrito contra la definición con un solver exacto; "
                        "código 1 si no cuadra")
    e.add_argument("--check-solver", default="auto", metavar="SOLVER",
                   help="solver exacto de --check (por defecto el primero de roundingsat, scip-exact o bruteforce)")
    e.add_argument("--log-level", default="WARNING", help="nivel de log de paraback (DEBUG, INFO, WARNING...)")
    m = p.add_argument_group("lanzamiento")
    m.add_argument("--launcher", choices=("auto", "mpi", "local"), default="auto",
                   help="mpi: mpirun con 1+k rangos; local: hilos en un proceso; auto: mpi si está disponible")
    m.add_argument("--mpirun", default="mpirun", metavar="CMD", help="ejecutable del lanzador MPI")
    m.add_argument("--mpirun-arg", action="append", default=[], metavar="ARG", help="argumento extra para mpirun (repetible)")
    # Internos: los usa el lanzador al invocarse a sí mismo bajo mpirun.
    p.add_argument("--_in-mpi", dest="in_mpi", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--_config", dest="config_file", default=None, help=argparse.SUPPRESS)
    return p


__all__ = ["build_parser"]

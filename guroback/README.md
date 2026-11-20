# GuroBack

GuroBack es un extractor de backbones para optimización pseudo-booleana / MILP.
Dada una instancia factible con valor óptimo `z*`, el backbone es el conjunto de
variables que toman el mismo valor en **todas** las soluciones óptimas.

El proyecto está organizado en tres capas desacopladas:

| Capa | Módulo | Responsabilidad |
|---|---|---|
| Interfaz | `guroback/backend.py` | Contrato abstracto `SolverBackend` (ciclo de vida, cotas, sondeos temporales, estados normalizados) y capacidades opcionales para el trabajo coordinado (`interrupt`, `set_time_limit`, `add_solution_hint`). |
| Algoritmo | `guroback/extractor.py` | `BackboneExtractor`: el método de reoptimización sucesiva, independiente del solver, descompuesto en `BackboneKnowledge` (estado compartible), `VariableOrder`/`ChunkPolicy` (principio de búsqueda) y `Coordinator` (puntos de enganche). |
| Backends | `guroback/gurobi_backend.py`, `guroback/scip_backend.py`, `guroback/roundingsat_backend.py`, `guroback/bruteforce_backend.py` | Implementaciones concretas, registradas en `guroback.BACKENDS`. |

Ningún módulo de análisis llama a `gurobipy`, a `pyscipopt` ni al ejecutable de
RoundingSat directamente. Sobre estas capas se construye **ParaBack**
(`paraback/`, en la raíz del repositorio): el extractor paralelo tipo portfolio
que ejecuta varios backends a la vez compartiendo lo que aprenden
(`docs/PLAN_PARABACK.md`).

## Instalación

Requiere Python ≥ 3.9. Desde la raíz del repositorio:

```sh
pip install -e ".[test]"        # sólo el backend bruteforce (sin solvers nativos)
pip install -e ".[scip,test]"   # backend SCIP
pip install -e ".[gurobi,test]" # backend Gurobi (requiere licencia válida)
pip install -e ".[all]"         # todos los solvers Python + mpi4py + pytest
make roundingsat                # compila RoundingBack (backend roundingsat), requiere cmake y Boost
```

Los mismos objetivos están en el `Makefile` de la raíz: `make install-scip`,
`make install-gurobi`, `make install-all`, `make roundingsat`, `make test`, `make cpp`.

`gurobipy` incluye las librerías de Gurobi; sólo hace falta una licencia
(`GRB_LICENSE_FILE`). Las licencias de nodo (`HOSTID`) no funcionan dentro de
contenedores ni en nodos de cluster; ahí se necesita una licencia WLS.

`pyscipopt` necesita la librería nativa de SCIP. La vía más simple es
conda-forge (`mamba install -c conda-forge scip pyscipopt`); también sirve
`pip install pyscipopt` si SCIP ya está instalado en el sistema. El modo exacto
(`scip-exact`) usa el SCIP vendorizado: `make scip-exact`.

Para comprobar qué backends están operativos en el entorno actual:

```sh
python -m guroback --list-solvers   # o: make check-solvers
```

## Uso

La línea de comandos conserva la convención del ejecutable C++ original:

```sh
python -m guroback [--solver gurobi|scip|roundingsat|bruteforce] [Param=Value ...] <instanceFile> <backboneFile>
```

`--solver` (por defecto `gurobi`) elige el backend; también se admite la forma
`--solver=scip`. El algoritmo, la salida y los mensajes de log son idénticos con
cualquiera de ellos.

* Los `Param=Value` se pasan al solver sin modificar, por ejemplo
  `Threads=4 TimeLimit=3600 FeasibilityTol=1e-9 OptimalityTol=1e-9 IntFeasTol=1e-9`.
  El límite de tiempo aplica a cada llamada al solver, no al total.
* `GurobackOptimum=<valor>` **no** se pasa al solver: fija el óptimo conocido de la
  instancia y el backbone se calcula sobre las soluciones con ese valor.
* Con `--solver scip` los `Param=Value` usan nombres nativos de SCIP
  (`limits/time=3600`, `numerics/feastol=1e-9`, `parallel/maxnthreads=4`). Para
  poder lanzar la misma línea de comandos contra ambos solvers, el backend
  traduce además los nombres equivalentes de Gurobi: `Threads`, `TimeLimit`,
  `NodeLimit`, `SolutionLimit`, `MIPGap`, `MIPGapAbs`, `FeasibilityTol`,
  `IntFeasTol`, `OptimalityTol` y `Seed`. `OutputFlag` y `MIPFocus` se rechazan
  con un mensaje que indica la alternativa en SCIP.
* Con `--solver roundingsat` los `Param=Value` son opciones nativas de RoundingSat
  (`opt-mode=linear`, `luby-mult=50`); se traducen `TimeLimit` (límite de CPU por
  llamada), `OutputFlag` y `Threads=1`, y se rechazan con explicación los que no
  tienen equivalente (`MIPGap`, `Seed`, tolerancias: RoundingSat es exacto).
* `--solver bruteforce` enumera todas las asignaciones y sólo sirve para instancias
  pequeñas (≤ 22 variables). Es el oráculo independiente usado en los tests.

El archivo de salida contiene una línea `b <lit>` por literal del backbone
(`b 12` para `x12 = 1`, `b -12` para `x12 = 0`) y termina con `b 0`. Si la
extracción se interrumpe por un límite del solver, la línea `b 0` no se escribe.

Uso como librería:

```python
from guroback import BackboneExtractor, get_backend_class
from guroback.extractor import RandomOrder, FixedChunks

with get_backend_class("gurobi")("instancia.opb", {"Threads": "4"}) as backend:
    result = BackboneExtractor(backend).run()
print(result.backbone, result.solve_count, result.total_solve_time)

# El orden de sondeo y el tamaño de chunk son intercambiables; el backbone no cambia.
with get_backend_class("scip")("instancia.opb") as backend:
    result = BackboneExtractor(backend, order=RandomOrder(seed=7), chunks=FixedChunks(4)).run()
```

`BackboneExtractor` acepta además un `Coordinator` con puntos de enganche
(`before_probe`, `on_probe_result`, …) que ParaBack usa para publicar y absorber
hechos entre sondeos; con los valores por defecto el comportamiento es idéntico
al del extractor secuencial original.

## Tests

```sh
python -m pytest
```

Los tests de cada solver se omiten automáticamente (indicando el motivo) si su
librería, su licencia o su ejecutable no están disponibles; `make test-scip`,
`make test-gurobi`, `make test-roundingsat` y `make test-paraback` ejecutan sólo
un grupo. El resto de la suite valida el algoritmo y el
contrato de la interfaz con el backend `bruteforce`, comparando contra un oráculo
que enumera todas las soluciones óptimas.

Para añadir un backend nuevo basta registrarlo en `guroback.BACKENDS` y añadir su
nombre al fixture `backend_name` de `tests/conftest.py`: la suite
`tests/test_backend_contract.py` debe pasar sin cambios.

## Backend SCIP

`guroback/scip_backend.py` implementa el mismo contrato sobre PySCIPOpt. La
diferencia esencial con Gurobi es la máquina de estados de SCIP: tras
`optimize()` el modelo queda en la etapa `SOLVED` y no admite cambios. El
backend mantiene el ciclo **`PROBLEM ⇄ SOLVED`**: antes de cualquier mutación
llama a `freeTransform()`, que descarta sólo el problema *transformado*
(presolve, árbol, LP) conservando el original, los parámetros y el mapeo de
variables. El modelo nunca se vuelve a leer del archivo.

El modo de sondeo nativo (`startProbing`) **no** se usa: fuera de las etapas
`PRESOLVING`/`SOLVING` aborta el proceso, y aunque se invocase desde un plugin
sólo propaga y resuelve el LP, por lo que no puede demostrar factibilidad
entera. El diseño completo, con el mapeo método a método, la traducción de
estados y las tolerancias, estaba en `PLAN_SCIP.md` del repositorio de origen (ver `CLAUDE.md`).

`SCIPBackend.scip_stats` acumula `nodes`, `lps`, `iterations`,
`presolving_time` y `solving_time` de todas las llamadas al solver, para las
métricas del informe de validación diferencial. Se publica también como
`native_stats`, el nombre común del contrato: el backend de Gurobi expone por ahí
sus `nodes`, `iterations` y `solving_time`, y los backends sin estadísticas
devuelven un mapa vacío.

## Backend RoundingBack (`roundingsat`)

`guroback/roundingsat_backend.py` envuelve el ejecutable de RoundingBack
(`roundingsat/`, RoundingSat modificado) en el mismo contrato. El ejecutable
incorpora un **modo servidor** (`--backbone-server=1`,
`roundingsat/src/BackboneServer.hpp`) que lee órdenes por la entrada estándar:
`assume`/`flip` materializan los sondeos con *assumptions* (sin reiniciar la
búsqueda ni contaminar la base de restricciones aprendidas), `fix` añade
unidades permanentes, `limit` acota el tiempo por llamada y `solve` responde
`sat` + solución, `unsat` o `unknown`. En problemas de optimización el óptimo
se calcula con una ejecución normal previa y el servidor arranca con
`--already-solved=1 --optimum-value=z*`, como hace `extract_backbone.sh`.

```sh
make roundingsat                                   # compila roundingsat/build/roundingsat
python -m guroback --solver roundingsat benchmarks/toy/normalized-C17.opb c17.bb
ROUNDINGSAT_BIN=/otra/ruta/roundingsat python -m guroback --solver roundingsat ...
```

## Validación diferencial (Gurobi vs SCIP)

`tests/test_differential.py` comprueba que ambos backends producen exactamente el
mismo backbone sobre `benchmarks/toy`, y `tools/differential_report.py` genera las
métricas de paridad, tiempos y llamadas al solver:

```sh
make test-differential   # suite de paridad (se salta si falta un solver)
make report              # tabla de métricas
```

Gurobi toma la licencia de `GRB_LICENSE_FILE`; dentro de un contenedor hace falta
una licencia WLS (las de nodo se rechazan). Si no se dispone de una, añadir
`GUROBI_LIMITED=1` a ambos objetivos para que `gurobipy` use su licencia limitada
integrada (suficiente para `benchmarks/toy`, no para la colección PB06). Para
validar sobre instancias arbitrarias, incluidas las `.opb.xz` de
`normalized-PB06`:

```sh
python3 tools/differential_report.py --param TimeLimit=60 --param Threads=1 ruta/a/instancia.opb.xz ...
```

Los resultados estaban en `REPORTE_SCIP.md` del repositorio de origen (ver `CLAUDE.md`).

## Implementación C++ original (legado)

`guroback.cpp` y su `Makefile` son la implementación original basada en la API
C++ de Gurobi 10/11. Se conservan como referencia del comportamiento; el
algoritmo en Python los reproduce paso a paso (misma fase base, mismos sondeos por
cotas y por restricción "al menos un flip", mismo formato de salida).

Para compilarlo, ajustar `GUROBI_INSTALL_DIR` en el `Makefile` (debe contener
`include/` y `lib/` con `libgurobiXXX.so` y `libgurobi_c++.a`) y ejecutar:

```sh
make guroback
./guroback [parameters] <instanceFile> <backboneFile>
```

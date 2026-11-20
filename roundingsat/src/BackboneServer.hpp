/***********************************************************************
Modo servidor de RoundingBack para el extractor de backbones ParaBack.

Con ``--backbone-server=1`` el proceso no ejecuta ``calculateBackbones``:
tras leer la instancia (y, en problemas de optimización, tras fijar el
óptimo con ``--already-solved=1 --optimum-value=V``) entra en un bucle que
lee órdenes de la entrada estándar y responde por la salida estándar. Así
el backend Python ``guroback.roundingsat_backend`` puede materializar el
contrato ``SolverBackend`` (sondeos temporales con assumptions, fijaciones
permanentes, límites de tiempo, interrupción) sin recrear el solver ni la
base de restricciones en cada sondeo.

Protocolo (una orden por línea; respuestas con prefijo ``r``):

    assume l1 l2 ...   literales que se asumen en el PRÓXIMO solve (temporal)
    flip l1 l2 ...     "al menos uno de estos literales es falso" en el
                       PRÓXIMO solve (temporal; ver activateBackboneConstraint)
    maxflip l1 l2 ...  el PRÓXIMO solve maximiza cuántos de estos literales cambian de
                       valor (LSU: una solución, cota "al menos k+1 cambios", otra solución,
                       ... hasta "unsat"); emite "v ±1 ±2 ..." por cada solución encontrada,
                       al momento, y responde "r optimum k" (k = máximo de cambios; 0 = ninguno
                       puede cambiar) o "r unknown" (límite/interrupción). Las assumptions y
                       los grupos "flip" pendientes se aplican también. Es el sondeo
                       chunks=maxflip del extractor (sólo Gurobi hasta ahora).
    fix l              unidad permanente (la variable queda fijada)
    fixed              "r fixed l1 l2 ...": literales originales fijados en el nivel 0
                       que no se habían informado antes (unidades aprendidas o fijadas)
    live fd            descriptor heredado (pipe) por el que el cliente escribe,
                       en cualquier momento, líneas "l1 l2 ...\n" de unidades
                       permanentes; se leen también DURANTE un solve, en cada
                       reinicio de Luby (y en cada INPROCESSED), sin abortarlo
    limit s            presupuesto de tiempo de CPU por solve (-1 = sin límite)
    solve              resuelve; responde "r sat" + "v ±1 ±2 ..." (variables
                       originales), "r unsat" o "r unknown" (límite/interrupción)
    phase l1 l2 ...    solución (parcial) que cuenta en los potenciales de --pguide (RB-PG);
                       sin --pguide no hace nada
    wp start|stop|get  RB-WP: empieza (y pone a cero) / termina de puntuar la actividad de
                       propagación; "get" responde "w s1 s2 ... sn" (variables originales)
    stats              "s conflicts N decisions N propagations N solvetime T solves N live_units N
                       live_nonroot N maxflip_solutions N pguide_solutions N"
    quit               termina el proceso

Un literal es un entero con signo: ``+k``/``k`` es ``x_k = 1`` y ``-k`` es
``x_k = 0``. Las órdenes ``assume`` y ``flip`` se consumen en el siguiente
``solve``; las restricciones ``flip`` se neutralizan después fijando su
variable auxiliar a verdadero, de modo que toda restricción aprendida sigue
siendo consecuencia de la fórmula original (TASK.md: "limpieza de la base de
datos de restricciones").

Las unidades de ``live`` son backbones que otro trabajador de ParaBack ya
demostró: igual que ``fix``, son consecuencia de la fórmula con el óptimo
fijado y pueden añadirse en mitad de la búsqueda. Solver::solve devuelve
RESTARTED en un reinicio sólo si el descriptor tiene datos (sin datos, el
coste es un ``poll`` por reinicio); el servidor las añade en el nivel 0 y
vuelve a llamar a solve con las mismas assumptions, conservando todo lo
aprendido y la actividad de las variables.
***********************************************************************/

#pragma once

#include "Solver.hpp"

namespace rs {
namespace server {

/// Bucle de órdenes. ``rootInfeasible`` indica que la lectura de la
/// instancia (o la fijación del óptimo) ya demostró la infactibilidad.
void serve(Solver& solver, bool rootInfeasible);

}  // namespace server
}  // namespace rs

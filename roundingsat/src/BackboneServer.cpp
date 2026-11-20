/***********************************************************************
Implementación del modo servidor (ver BackboneServer.hpp).
***********************************************************************/

#include "BackboneServer.hpp"

#include <fcntl.h>
#include <poll.h>
#include <unistd.h>

#include <cerrno>
#include <iostream>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "ConstrExp.hpp"
#include "IntSet.hpp"
#include "globals.hpp"

namespace rs {

bool live_units_pending() {
  pollfd pfd{live_units_fd, POLLIN, 0};
  return poll(&pfd, 1, 0) > 0 && (pfd.revents & (POLLIN | POLLHUP)) != 0;
}

namespace server {

namespace {

struct ServerState {
  Solver& solver;
  bool rootInfeasible;
  double limit = -1;                          // presupuesto de CPU por solve; -1 = ilimitado
  std::vector<Lit> assumptions;               // pendientes para el próximo solve
  std::vector<std::vector<Lit>> flipGroups;   // pendientes para el próximo solve
  std::vector<Lit> maxflip;                   // orden "maxflip": literales base cuyo cambio se maximiza en el próximo solve
  long long maxflipSolutions = 0;             // soluciones (líneas "v") emitidas por los solves "maxflip"
  long long nbSolves = 0;
  long long liveUnits = 0;                    // unidades vivas añadidas (orden "live")
  long long liveNonRoot = 0;                  // unidades vivas que llegaron con el solver fuera del nivel 0
  std::string liveBuffer;                     // línea viva a medio leer
  double solveTime = 0;
  IntSet noAssumptions;                       // conjunto vacío reutilizable
  std::vector<bool> fixedReported;            // variables originales ya informadas por "fixed"

  explicit ServerState(Solver& s, bool infeasible) : solver(s), rootInfeasible(infeasible) {}
};

void reply(const std::string& msg) {
  std::cout << "r " << msg << std::endl;  // endl: vaciar el búfer, el cliente espera la línea
}

void printSolution(const Solver& solver) {
  std::cout << "v";
  const std::vector<Lit>& sol = solver.lastSol;
  for (Var v = 1; v <= solver.getNbOrigVars() && v < (Var)sol.size(); ++v) {
    std::cout << (sol[v] > 0 ? " " : " -") << v;
  }
  std::cout << std::endl;
}

/// Convierte un token en literal de una variable ORIGINAL; 0 si no es válido.
Lit parseLit(const Solver& solver, const std::string& token) {
  long long value = 0;
  size_t used = 0;
  try {
    value = std::stoll(token, &used);
  } catch (const std::exception&) {
    return 0;
  }
  // Todo el token debe ser el número: "369-189" (dos literales pegados) no es 369.
  if (used != token.size()) return 0;
  long long var = value < 0 ? -value : value;
  if (var < 1 || var > solver.getNbOrigVars()) return 0;
  return static_cast<Lit>(value);
}

bool parseLits(const Solver& solver, std::istringstream& is, std::vector<Lit>& out) {
  std::string token;
  while (is >> token) {
    Lit l = parseLit(solver, token);
    if (l == 0) return false;
    out.push_back(l);
  }
  return true;
}

/// Añade en el nivel 0 las unidades vivas disponibles en live_units_fd (líneas de
/// literales). Son backbones ya demostrados por otro trabajador: permanentes, igual
/// que "fix". Devuelve false si la fórmula queda infactible en la raíz.
bool drainLiveUnits(ServerState& st) {
  if (live_units_fd < 0) return true;
  // Toda adición de restricciones exige nivel de decisión 0 (Solver::addInputConstraint): en
  // otro nivel, una unidad verdadera pero falsa en la rama que se explora se toma por
  // "inconsistente" y el sondeo se responde "unsat" (backbones falsos; ronda R20, ped3).
  // Quien llama debe haber vuelto al nivel 0; si no, las unidades esperan al siguiente drenaje.
  if (st.solver.decisionLevel() != 0) return true;
  char chunk[4096];
  while (true) {
    ssize_t n = read(live_units_fd, chunk, sizeof(chunk));
    if (n > 0) {
      st.liveBuffer.append(chunk, static_cast<size_t>(n));
      continue;
    }
    if (n == 0) {  // el cliente cerró su extremo: no habrá más unidades
      close(live_units_fd);
      live_units_fd = -1;
    } else if (errno == EINTR) {
      continue;
    }
    break;  // EAGAIN: no hay más datos por ahora
  }
  size_t end = st.liveBuffer.rfind('\n');
  if (end == std::string::npos) return true;
  std::istringstream is(st.liveBuffer.substr(0, end));
  st.liveBuffer.erase(0, end + 1);
  std::vector<Lit> lits;
  std::string token;
  while (is >> token) {
    Lit l = parseLit(st.solver, token);
    if (l != 0) lits.push_back(l);  // un literal inválido se ignora: la unidad es opcional
  }
  if (lits.empty() || st.rootInfeasible) return !st.rootInfeasible;
  st.solver.setAssumptions(st.noAssumptions);  // toda adición exige nivel de decisión 0
  for (Lit l : lits) {
    std::pair<ID, ID> unit = st.solver.addUnitConstraint(l, Origin::BACKBONECALCULATION);
    st.liveUnits++;
    if (unit.second == ID_Unsat) {
      st.rootInfeasible = true;
      return false;
    }
  }
  return true;
}

/// Restricción temporal reificada ``sum_i ¬l_i + degree·aux >= degree`` con la assumption
/// ``¬aux`` (``degree = 1``: "flip", exactamente igual que Solver::activateBackboneConstraint;
/// ``degree = k+1``: la cota de "maxflip", al menos k+1 cambios).
struct Reified {
  ID id;
  Var aux;
};

Reified addReified(Solver& solver, const std::vector<Lit>& lits, long long degree, IntSet& assumps) {
  CeArb input = solver.cePools.takeArb();
  input->reset();
  for (Lit l : lits) input->addLhs(1, -l);
  input->addRhs(degree);
  Var aux = solver.getNbVars() + 1;
  solver.setNbVars(aux);
  input->addLhs(degree, aux);
  assumps.add(-aux);
  std::pair<ID, ID> cons = solver.addConstraint(input, Origin::BACKBONECALCULATION);
  return {cons.second, aux};
}

/// Neutraliza una restricción reificada: ``aux := 1`` (toda restricción aprendida con ella
/// queda implicada por la fórmula) y se borra para no acumular basura. Exige nivel 0.
void neutralize(ServerState& st, const Reified& r) {
  if (!st.rootInfeasible) {
    std::pair<ID, ID> unit = st.solver.addUnitConstraint(r.aux, Origin::BACKBONECALCULATION);
    if (unit.second == ID_Unsat) st.rootInfeasible = true;  // no debería ocurrir
  }
  if (r.id != ID_Undef && r.id != ID_Unsat) st.solver.dropExternal(r.id, true, true);
}

/// Un solve con las assumptions dadas que incorpora las unidades vivas en cada reinicio:
/// "sat", "unsat" (inconsistente con las assumptions, o infactible en la raíz: entonces
/// st.rootInfeasible queda a true) o "unknown" (SIGINT o límite de tiempo).
std::string solveOnce(ServerState& st, const IntSet& assumps) {
  Solver& solver = st.solver;
  try {
    while (true) {
      solver.setAssumptions(assumps);
      SolveAnswer out = solver.solve();
      if (out.state == SolveState::SAT) return "sat";
      if (out.state == SolveState::INCONSISTENT) return "unsat";
      if (out.state == SolveState::UNSAT) {
        // Inconsistencia en la raíz: la fórmula (con las unidades fijadas) es infactible.
        st.rootInfeasible = true;
        return "unsat";
      }
      // INPROCESSED / RESTARTED: incorporar las unidades vivas y seguir buscando. Tras
      // INPROCESSED el solver sigue en el nivel de decisión en que estaba (RESTARTED y
      // SAT ya vuelven al 0): se vuelve al 0 antes de añadir nada (ver drainLiveUnits).
      // live_nonroot cuenta las veces que había unidades pendientes en esa situación: con la
      // versión anterior, cada una podía convertir un sondeo satisfacible en "unsat".
      if (solver.decisionLevel() != 0 && live_units_pending()) st.liveNonRoot++;
      solver.setAssumptions(assumps);
      if (!drainLiveUnits(st)) return "unsat";
    }
  } catch (const AsynchronousInterrupt&) {
    asynch_interrupt = false;
    return "unknown";
  }
}

/// Cuántos de los literales base ``lits`` cambian de valor en la última solución.
long long countFlips(const Solver& solver, const std::vector<Lit>& lits) {
  const std::vector<Lit>& sol = solver.lastSol;
  long long flips = 0;
  for (Lit l : lits) {
    Var v = toVar(l);
    if (v < (Var)sol.size() && sol[v] == -l) ++flips;
  }
  return flips;
}

/// Ejecuta un solve con las modificaciones temporales pendientes y las deshace.
std::string runSolve(ServerState& st) {
  Solver& solver = st.solver;
  st.nbSolves++;
  if (st.rootInfeasible) {
    st.assumptions.clear();
    st.flipGroups.clear();
    st.maxflip.clear();
    return "unsat";
  }

  // Toda adición de restricciones exige nivel de decisión 0.
  solver.setAssumptions(st.noAssumptions);
  if (!drainLiveUnits(st)) {
    st.assumptions.clear();
    st.flipGroups.clear();
    st.maxflip.clear();
    return "unsat";
  }

  IntSet assumps;
  for (Lit l : st.assumptions) assumps.add(l);

  // Cada grupo "flip" se codifica como sum_i (¬l_i) + aux >= 1 asumiendo ¬aux,
  // exactamente igual que Solver::activateBackboneConstraint.
  std::vector<Reified> activated;
  bool inconsistentInput = false;
  for (const std::vector<Lit>& group : st.flipGroups) {
    activated.push_back(addReified(solver, group, 1, assumps));
    if (activated.back().id == ID_Unsat) inconsistentInput = true;
  }
  // "maxflip": la primera cota pide al menos un cambio (la solución base, con 0 cambios, ya se conoce).
  const std::vector<Lit> maxflip = st.maxflip;
  if (!maxflip.empty()) {
    activated.push_back(addReified(solver, maxflip, 1, assumps));
    if (activated.back().id == ID_Unsat) inconsistentInput = true;
  }

  std::string answer;
  if (inconsistentInput) {
    // Con aux libre la restricción siempre es satisfacible, así que la
    // inconsistencia sólo puede venir de la fórmula ya fijada.
    st.rootInfeasible = true;
    answer = "unsat";
  } else {
    double savedTimeout = options.timeout.get();
    if (st.limit >= 0) options.timeout.get() = stats.getTime() + st.limit;
    asynch_interrupt = false;
    double start = aux::cpuTime();
    if (maxflip.empty()) {
      answer = solveOnce(st, assumps);
    } else {
      // LSU (búsqueda lineal SAT-UNSAT) sobre "maximizar cuántos literales base cambian":
      // cada solución se emite al momento como línea "v" (es óptima del problema original,
      // con z* fijado) y la cota sube a "al menos k+1 cambios"; la cota anterior se
      // neutraliza ya (aux := 1). "unsat" con cota k+1 demuestra que k es el máximo.
      long long best = 0;
      while (true) {
        std::string r = solveOnce(st, assumps);
        if (r != "sat") {
          answer = r == "unsat" && !st.rootInfeasible ? "optimum " + std::to_string(best) : r;
          break;
        }
        best = countFlips(solver, maxflip);
        printSolution(solver);
        ++st.maxflipSolutions;
        if (best >= (long long)maxflip.size()) {  // todos cambian: no hay cota mejor
          answer = "optimum " + std::to_string(best);
          break;
        }
        Reified previous = activated.back();
        activated.pop_back();
        assumps.remove(-previous.aux);
        solver.setAssumptions(st.noAssumptions);  // nivel 0 para neutralizar y añadir
        neutralize(st, previous);
        if (st.rootInfeasible) {
          answer = "unsat";
          break;
        }
        activated.push_back(addReified(solver, maxflip, best + 1, assumps));
        if (activated.back().id == ID_Unsat) {  // no debería ocurrir (aux libre)
          st.rootInfeasible = true;
          answer = "unsat";
          break;
        }
      }
    }
    st.solveTime += aux::cpuTime() - start;
    options.timeout.get() = savedTimeout;
  }

  // Deshacer: volver al nivel 0, neutralizar cada grupo fijando aux = 1
  // (toda restricción aprendida con aux queda implicada por la fórmula) y
  // borrar la restricción auxiliar para no acumular basura.
  solver.setAssumptions(st.noAssumptions);
  for (const Reified& act : activated) neutralize(st, act);
  st.assumptions.clear();
  st.flipGroups.clear();
  st.maxflip.clear();
  return answer;
}

void printStats(const ServerState& st) {
  std::cout << "s conflicts " << stats.NCONFL << " decisions " << stats.NDECIDE << " propagations " << stats.NPROP
            << " solvetime " << st.solveTime << " solves " << st.nbSolves << " live_units " << st.liveUnits
            << " live_nonroot " << st.liveNonRoot << " maxflip_solutions " << st.maxflipSolutions
            << " pguide_solutions " << st.solver.getPguideSolutions() << std::endl;
}

/// RB-WP: "w s1 s2 ... sn", the score of each original variable (1..n).
void printWpScores(const Solver& solver) {
  const std::vector<double>& scores = solver.getWpScores();
  std::ostringstream os;
  os.precision(17);
  os << "w";
  for (Var v = 1; v <= solver.getNbOrigVars(); ++v) os << " " << (v < (Var)scores.size() ? scores[v] : 0.0);
  std::cout << os.str() << std::endl;
}

}  // namespace

void serve(Solver& solver, bool rootInfeasible) {
  ServerState st(solver, rootInfeasible);
  std::cout << "r ready " << solver.getNbOrigVars() << std::endl;

  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream is(line);
    std::string command;
    if (!(is >> command)) continue;

    if (command == "quit") {
      reply("bye");
      return;
    } else if (command == "solve") {
      std::string answer = runSolve(st);
      reply(answer);
      if (answer == "sat") printSolution(solver);
    } else if (command == "assume") {
      std::vector<Lit> lits;
      if (!parseLits(solver, is, lits)) {
        reply("error invalid literal in: " + line);
        continue;
      }
      st.assumptions.insert(st.assumptions.end(), lits.begin(), lits.end());
      reply("ok");
    } else if (command == "flip") {
      std::vector<Lit> lits;
      if (!parseLits(solver, is, lits) || lits.empty()) {
        reply("error invalid literal in: " + line);
        continue;
      }
      st.flipGroups.push_back(lits);
      reply("ok");
    } else if (command == "maxflip") {
      // maxflip l1 l2 ...: el próximo solve maximiza cuántos de estos literales base cambian
      // (LSU); responde "r optimum k" tras emitir una línea "v" por cada solución.
      std::vector<Lit> lits;
      if (!parseLits(solver, is, lits) || lits.empty()) {
        reply("error invalid literal in: " + line);
        continue;
      }
      st.maxflip = lits;
      reply("ok");
    } else if (command == "fix") {
      std::vector<Lit> lits;
      if (!parseLits(solver, is, lits) || lits.empty()) {
        reply("error invalid literal in: " + line);
        continue;
      }
      if (!st.rootInfeasible) {
        solver.setAssumptions(st.noAssumptions);
        for (Lit l : lits) {
          std::pair<ID, ID> unit = solver.addUnitConstraint(l, Origin::BACKBONECALCULATION);
          if (unit.second == ID_Unsat) {
            st.rootInfeasible = true;
            break;
          }
        }
      }
      reply(st.rootInfeasible ? "ok infeasible" : "ok");
    } else if (command == "live") {
      // live FD: descriptor heredado por el que llegarán unidades vivas durante los solves.
      int fd = -1;
      if (!(is >> fd) || fd < 0 || fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK) == -1) {
        reply("error invalid live descriptor in: " + line);
        continue;
      }
      live_units_fd = fd;
      reply("ok");
    } else if (command == "limit") {
      double value = -1;
      if (!(is >> value)) {
        reply("error invalid limit in: " + line);
        continue;
      }
      st.limit = value;
      reply("ok");
    } else if (command == "phase") {
      // phase l1 l2 ...: una solución (parcial) encontrada fuera de este solve (la base x*, la de
      // otro trabajador) que cuenta en los potenciales de --pguide; sin --pguide no hace nada.
      std::vector<Lit> lits;
      if (!parseLits(solver, is, lits)) {
        reply("error invalid literal in: " + line);
        continue;
      }
      if (options.pguide && !lits.empty()) solver.pguideRecord(lits);
      reply("ok");
    } else if (command == "wp") {
      // wp start | wp stop | wp get: puntuaciones de RB-WP (ver Solver::wpAccumulate).
      std::string sub;
      is >> sub;
      if (sub == "start" || sub == "stop") {
        solver.setWpScoring(sub == "start");
        reply("ok");
      } else if (sub == "get") {
        printWpScores(solver);
      } else {
        reply("error unknown wp subcommand: " + sub);
      }
    } else if (command == "fixed") {
      // fixed: literales de variables originales fijados en el nivel 0 que no se habían
      // informado aún. Son consecuencia de la fórmula con el óptimo, las unidades "fix" y
      // las vivas: las restricciones "flip" llevan una auxiliar nueva que después vale 1,
      // así que nada de lo que permiten aprender restringe las variables originales.
      std::ostringstream os;
      os << "fixed";
      Var n = solver.getNbOrigVars();
      if ((Var)st.fixedReported.size() <= n) st.fixedReported.resize(n + 1, false);
      if (!st.rootInfeasible) {
        for (Var v = 1; v <= n; ++v) {
          if (st.fixedReported[v]) continue;
          if (isUnit(solver.getLevel(), v)) {
            os << " " << v;
            st.fixedReported[v] = true;
          } else if (isUnit(solver.getLevel(), -v)) {
            os << " " << -v;
            st.fixedReported[v] = true;
          }
        }
      }
      reply(os.str());
    } else if (command == "stats") {
      printStats(st);
    } else {
      reply("error unknown command: " + command);
    }
  }
}

}  // namespace server
}  // namespace rs

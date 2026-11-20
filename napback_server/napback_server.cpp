/***********************************************************************
napback-server: servidor de sondeos de backbones sobre CaDiCaL para NapBack.

Es la mitad nativa de guroback.napback_backend (docs/PLAN_NAPBACK.md §2). Lee la
CNF que Naps obtuvo de la instancia PB (con -GM: ver §1.1, V3) y responde
por la entrada/salida estándar con el MISMO protocolo de líneas que el modo
servidor de RoundingBack (roundingsat/src/BackboneServer.hpp), de modo que
el cliente Python (guroback.line_server) es común:

    napback-server <cnf> <n_pb> [--<opción de CaDiCaL>=<valor> ...]

<n_pb> es el número de variables PB: Naps las numera 1..n_pb y las
auxiliares de la codificación van después (§1.1, V2; lo verifica VarMap en
Python antes de arrancar el servidor). Todos los literales del protocolo son
de esas variables; las auxiliares nunca salen ni entran.

    assume l1 l2 ...   assumptions del PRÓXIMO solve (temporal)
    flip l1 ... ln     "al menos uno de estos literales es falso" en el PRÓXIMO
                       solve (temporal). El primer grupo usa constrain() de
                       CaDiCaL (cláusula válida sólo en ese solve; es la
                       técnica por defecto de Cadiback). Los demás grupos, si
                       los hay, usan una variable de activación a: cláusula
                       (¬l1 ∨ … ∨ ¬ln ∨ a) con la assumption ¬a y, después,
                       la unidad a, que la convierte en tautología; así todo
                       lo aprendido sigue siendo consecuencia de la CNF.
    fix l ...          unidades permanentes (backbones demostrados)
    fixed              "r fixed l1 l2 ...": literales PB fijados en el nivel 0 que no se
                       habían informado antes
    live fd            descriptor heredado (pipe, no bloqueante) por el que el
                       cliente escribe líneas "l1 l2 ...\n" de unidades en
                       cualquier momento; se leen al empezar cada solve y
                       DURANTE el solve (el Terminator lo detecta, el solve se
                       corta, se añaden en el nivel 0 y se reanuda con las
                       mismas assumptions y el mismo flip, conservando todo lo
                       aprendido)
    limit s            presupuesto de CPU por solve en segundos (-1: sin límite)
    solve              "r sat" + "v ±1 ±2 … ±n_pb", "r unsat" o "r unknown"
                       (límite o SIGINT)
    stats              "s conflicts N decisions N propagations N solvetime T
                       solves N live_units N live_resumes N" (live_resumes: solves
                       cortados y reanudados para incorporar unidades vivas)
    quit               "r bye" y termina

SIGINT durante un solve lo aborta ("r unknown") sin perder el estado; la
bandera se limpia al empezar cada solve (como en RoundingBack). El manejador
usa SA_RESTART para que un SIGINT fuera de un solve no rompa la lectura de
órdenes.
***********************************************************************/

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "cadical.hpp"

namespace {

volatile sig_atomic_t g_interrupted = 0;

void on_sigint(int) { g_interrupted = 1; }

double cpu_time() {
  timespec ts{};
  clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
  return static_cast<double>(ts.tv_sec) + 1e-9 * static_cast<double>(ts.tv_nsec);
}

bool fd_readable(int fd) {
  if (fd < 0) return false;
  pollfd pfd{fd, POLLIN, 0};
  return poll(&pfd, 1, 0) > 0 && (pfd.revents & (POLLIN | POLLHUP)) != 0;
}

enum class Stop { NONE, INTERRUPT, LIMIT, LIVE };

/// Decide cuándo cortar un solve. SIGINT se mira en cada llamada (una lectura
/// de memoria); el reloj y el pipe de unidades vivas, cada 256 llamadas.
class Watchdog : public CaDiCaL::Terminator {
 public:
  double deadline = -1;  // tiempo de CPU absoluto; < 0: sin límite
  int live_fd = -1;
  Stop why = Stop::NONE;

  void reset(double limit) {
    why = Stop::NONE;
    calls_ = 0;
    deadline = limit >= 0 ? cpu_time() + limit : -1;
  }

  bool terminate() override {
    if (g_interrupted) {
      why = Stop::INTERRUPT;
      return true;
    }
    if ((++calls_ & 255u) != 0) return false;
    if (deadline >= 0 && cpu_time() > deadline) {
      why = Stop::LIMIT;
      return true;
    }
    if (fd_readable(live_fd)) {
      why = Stop::LIVE;
      return true;
    }
    return false;
  }

 private:
  uint32_t calls_ = 0;
};

struct Server {
  CaDiCaL::Solver solver;
  Watchdog watchdog;
  int num_pb = 0;
  int max_var = 0;          // variables en uso (CNF + activaciones)
  bool root_unsat = false;  // la CNF con las unidades fijadas no tiene solución
  double limit = -1;
  std::vector<int> assumptions;
  std::vector<std::vector<int>> flip_groups;
  std::string live_buffer;
  long long solves = 0;
  long long live_units = 0;
  long long live_resumes = 0;  // solves cortados por unidades vivas y reanudados
  double solve_time = 0;
  std::vector<bool> fixed_reported;  // variables PB ya informadas por "fixed"
};

void reply(const std::string& msg) { std::cout << "r " << msg << std::endl; }

/// Literal de una variable PB (1..n_pb); 0 si el token no es válido.
int parse_lit(const Server& s, const std::string& token) {
  char* end = nullptr;
  errno = 0;
  long value = std::strtol(token.c_str(), &end, 10);
  if (errno != 0 || end == token.c_str() || *end != '\0') return 0;
  long var = value < 0 ? -value : value;
  if (var < 1 || var > s.num_pb) return 0;
  return static_cast<int>(value);
}

bool parse_lits(const Server& s, std::istringstream& is, std::vector<int>& out) {
  std::string token;
  while (is >> token) {
    int lit = parse_lit(s, token);
    if (lit == 0) return false;
    out.push_back(lit);
  }
  return true;
}

void add_unit(Server& s, int lit) {
  s.solver.add(lit);
  s.solver.add(0);
}

/// Añade las unidades vivas disponibles (backbones de otros trabajadores).
/// Sólo entre solves: CaDiCaL no admite cláusulas nuevas durante la búsqueda.
void drain_live_units(Server& s) {
  int& fd = s.watchdog.live_fd;
  if (fd < 0) return;
  char chunk[4096];
  while (true) {
    ssize_t n = read(fd, chunk, sizeof(chunk));
    if (n > 0) {
      s.live_buffer.append(chunk, static_cast<size_t>(n));
      continue;
    }
    if (n == 0) {  // el cliente cerró su extremo: no habrá más unidades
      close(fd);
      fd = -1;
    } else if (errno == EINTR) {
      continue;
    }
    break;  // EAGAIN
  }
  size_t end = s.live_buffer.rfind('\n');
  if (end == std::string::npos) return;
  std::istringstream is(s.live_buffer.substr(0, end));
  s.live_buffer.erase(0, end + 1);
  std::string token;
  while (is >> token) {
    int lit = parse_lit(s, token);
    if (lit == 0) continue;  // una unidad viva es opcional: se ignora si no es válida
    add_unit(s, lit);
    s.live_units++;
  }
}

std::string run_solve(Server& s) {
  s.solves++;
  std::vector<int> activations;
  std::string answer;
  if (s.root_unsat) {
    answer = "unsat";
  } else {
    // Grupos flip a partir del segundo: variable de activación (ver cabecera).
    for (size_t g = 1; g < s.flip_groups.size(); ++g) {
      int a = ++s.max_var;
      for (int lit : s.flip_groups[g]) s.solver.add(-lit);
      s.solver.add(a);
      s.solver.add(0);
      activations.push_back(a);
    }
    g_interrupted = 0;
    s.watchdog.reset(s.limit);
    double start = cpu_time();
    while (true) {
      drain_live_units(s);
      // assume/constrain valen para un solo solve: se reponen en cada reanudación.
      for (int lit : s.assumptions) s.solver.assume(lit);
      for (int a : activations) s.solver.assume(-a);
      if (!s.flip_groups.empty()) {
        for (int lit : s.flip_groups[0]) s.solver.constrain(-lit);
        s.solver.constrain(0);
      }
      s.watchdog.why = Stop::NONE;
      int res = s.solver.solve();
      if (res == 10) {
        answer = "sat";
        break;
      }
      if (res == 20) {
        // Sin nada temporal, la insatisfacibilidad es de la CNF (con las unidades fijadas).
        if (s.assumptions.empty() && s.flip_groups.empty()) s.root_unsat = true;
        answer = "unsat";
        break;
      }
      if (s.watchdog.why == Stop::LIVE) {  // incorporar unidades y reanudar
        s.live_resumes++;
        continue;
      }
      answer = "unknown";
      break;
    }
    s.solve_time += cpu_time() - start;
  }
  if (answer == "sat") {
    std::string line = "v";
    line.reserve(static_cast<size_t>(s.num_pb) * 7);
    for (int v = 1; v <= s.num_pb; ++v) {
      line += s.solver.val(v) > 0 ? " " : " -";
      line += std::to_string(v);
    }
    reply(answer);
    std::cout << line << std::endl;
  } else {
    reply(answer);
  }
  // Neutralizar las activaciones: con la unidad a, (… ∨ a) es una tautología.
  for (int a : activations) add_unit(s, a);
  s.assumptions.clear();
  s.flip_groups.clear();
  return answer;
}

void print_stats(const Server& s) {
  std::cout << "s conflicts " << s.solver.get_statistic_value("conflicts") << " decisions "
            << s.solver.get_statistic_value("decisions") << " propagations "
            << s.solver.get_statistic_value("propagations") << " solvetime " << s.solve_time << " solves "
            << s.solves << " live_units " << s.live_units << " live_resumes " << s.live_resumes << std::endl;
}

void serve(Server& s) {
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream is(line);
    std::string command;
    if (!(is >> command)) continue;
    if (command == "quit") {
      reply("bye");
      return;
    } else if (command == "solve") {
      run_solve(s);
    } else if (command == "assume" || command == "flip" || command == "fix") {
      std::vector<int> lits;
      if (!parse_lits(s, is, lits) || (command != "assume" && lits.empty())) {
        reply("error invalid literal in: " + line);
        continue;
      }
      if (command == "assume") {
        s.assumptions.insert(s.assumptions.end(), lits.begin(), lits.end());
      } else if (command == "flip") {
        s.flip_groups.push_back(lits);
      } else {
        for (int lit : lits) add_unit(s, lit);
      }
      reply("ok");
    } else if (command == "live") {
      int fd = -1;
      if (!(is >> fd) || fd < 0 || fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK) == -1) {
        reply("error invalid live descriptor in: " + line);
        continue;
      }
      s.watchdog.live_fd = fd;
      reply("ok");
    } else if (command == "limit") {
      double value = -1;
      if (!(is >> value)) {
        reply("error invalid limit in: " + line);
        continue;
      }
      s.limit = value;
      reply("ok");
    } else if (command == "fixed") {
      // Literales PB fijados en el nivel 0 aún no informados: implicados por la CNF (constrain
      // vale sólo en su solve y las activaciones son variables nuevas que después valen 1).
      std::ostringstream os;
      os << "fixed";
      if (s.fixed_reported.size() <= static_cast<size_t>(s.num_pb)) s.fixed_reported.resize(s.num_pb + 1, false);
      if (!s.root_unsat) {
        for (int v = 1; v <= s.num_pb; ++v) {
          if (s.fixed_reported[v]) continue;
          int f = s.solver.fixed(v);
          if (f != 0) {
            os << " " << (f > 0 ? v : -v);
            s.fixed_reported[v] = true;
          }
        }
      }
      reply(os.str());
    } else if (command == "stats") {
      print_stats(s);
    } else {
      reply("error unknown command: " + command);
    }
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 3) {
    std::cerr << "uso: napback-server <cnf> <n_pb> [--<opción>=<valor> ...]" << std::endl;
    reply("error usage");
    return 1;
  }
  struct sigaction sa {};
  sa.sa_handler = on_sigint;
  sigemptyset(&sa.sa_mask);
  sa.sa_flags = SA_RESTART;
  sigaction(SIGINT, &sa, nullptr);

  Server s;
  s.solver.set("quiet", 1);
  for (int i = 3; i < argc; ++i) {
    if (!s.solver.set_long_option(argv[i])) {
      reply(std::string("error invalid CaDiCaL option: ") + argv[i]);
      return 1;
    }
  }
  char* end = nullptr;
  long num_pb = std::strtol(argv[2], &end, 10);
  if (end == argv[2] || *end != '\0' || num_pb < 1) {
    reply(std::string("error invalid n_pb: ") + argv[2]);
    return 1;
  }
  int vars = 0;
  const char* err = s.solver.read_dimacs(argv[1], vars, 1);
  if (err) {
    reply(std::string("error reading CNF: ") + err);
    return 1;
  }
  if (num_pb > vars) {
    reply("error n_pb " + std::to_string(num_pb) + " exceeds CNF variables " + std::to_string(vars));
    return 1;
  }
  s.num_pb = static_cast<int>(num_pb);
  s.max_var = vars;
  // Las variables PB aparecen en assumptions, flips y unidades futuras: congelarlas
  // impide que el inprocesado de CaDiCaL las elimine.
  for (int v = 1; v <= s.num_pb; ++v) s.solver.freeze(v);
  s.solver.connect_terminator(&s.watchdog);
  std::cout << "r ready " << s.num_pb << std::endl;
  serve(s);
  s.solver.disconnect_terminator();
  return 0;
}

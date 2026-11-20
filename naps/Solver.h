/****************************************************************************************[Solver.h]
  Copyright (c) 2005-2010, Niklas Een, Niklas Sorensson

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
  associated documentation files (the "Software"), to deal in the Software without restriction,
  including without limitation the rights to use, copy, modify, merge, publish, distribute,
  sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all copies or
  substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
  NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
  NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
  DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
  OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 **************************************************************************************************/

#ifndef Solver_h
#define Solver_h

#include <signal.h>
#include <unordered_set>

#include "Config.h"
#include "Report.h"

//#include "MiniSat.h"
#include "SolverTypes.h"
//#include "SatELite.h"
#include "glueminisat/core/Solver.h"
#include "glueminisat/simp/SimpSolver.h"
//#include "es1sat/core/Solver.h"
//#include "es1sat/simp/SimpSolver.h"
//#include "ccminisat/core/Solver.h"
//#include "ccminisat/simp/SimpSolver.h"
#ifdef USE_CADICAL2
#include "cadical2/src/cadical.hpp"
#else
#include "cadical3/src/cadical.hpp"
#endif
extern "C" {
#include "kissat/src/kissat.h"
}
//=================================================================================================

//class Solver_MiniSat;

class Solver {

  public:
    Report* report;
    Config*             config;
    bool                own_config;

  protected:
    BasicSolverStats     basicStats;
    bool                 dummyOK;

  public:

    double              solving_time, starting_time;
    bool                solving;
    int                 goal_lit_flips;

    Solver(Config* _config, bool _own_config)
      : report(&_config->report)
        , config(_config)
        , own_config(_own_config)
        , solving_time(0.0)
        , solving(false)
  {}

    Solver() : Solver(new Config(), true) {}
    //Solver(SolverT stype) ;

    virtual ~Solver() {
      if (own_config) delete config;
    }

    virtual bool& ok_ref() ;
    virtual BasicSolverStats& stats_ref() ;
    virtual lbool assigns(Var v) const ;
    virtual int trail_size() ;
    virtual Lit trail(int index) ;
    virtual void setVerbosity(int level) ;
    Var newVar() {return newVar(true);} ;
    virtual Var newVar(bool dvar) ;
    virtual bool addClause(const vec<Lit>& ps) ;
    virtual bool add_ESClause(const vec<Lit>& ps) ;
    virtual bool add_CCClause(const vec<Lit>& ps, int k) ;
    virtual bool addUnit(Lit p) ;
    virtual void freeze(Var x) ;
    virtual void suggestPolarity(Var x, lbool value) ;
    virtual void setGoal(const vec<Lit>& ps, const vec<Int>& Cs) { assert(false); }

    virtual bool solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps);

    bool solve(const vec<Lit>& assumps, vec<Lit>& failed_assumps) {
      starting_time = cpuTime(); solving = true;
      bool result = solve_(assumps, failed_assumps);
      double time = cpuTime() - starting_time;
      solving_time += time, solving = false;
      if (result) basicStats.sat_solving_time += time;
      else if (timed_out) basicStats.timeout_solving_time += time;
      else basicStats.unsat_solving_time += time;
      return result;
    }
    bool solve(const vec<Lit>& assumps) {
      vec<Lit> tmp; return solve(assumps, tmp);
    }
    bool solve() {
      vec<Lit> tmp; return solve(tmp);
    }

    virtual lbool model(Var v) ;
    virtual bool varElimed(Var x) ;
    virtual int ucore_vars(int idx) ;
    virtual int ucore_varsSize() ;
    virtual int ucore_clauses(int idx) ;
    virtual int ucore_clausesSize() ;
    virtual bool okay() ;
    virtual int nVars() const ;

    virtual void exportCnf(cchar* filename, Var out_max) ;

    // Async-signal-safe interrupt: causes solve_() to return false early.
    virtual void interrupt() {}

    // Time-budget based termination: set before solve_(), clear after.
    bool timed_out = false;
    virtual void setTimeout(double seconds) {}
    virtual void clearTimeout() { timed_out = false; }
};
//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

// Helper function to invoke a proper sat solver
//Solver* Solver_invoke(SolverT stype) ;
Solver* Solver_invoke(SolverT stype, Config* config = NULL) ;


//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
// class Solver_MiniSat : public Solver {
//     MiniSat::Solver*    minisat;

// public:

//     Solver_MiniSat() ;

//     bool& ok_ref() ;
//     BasicSolverStats& stats_ref  () ;
//     lbool assigns(Var v) const ;
//     int trail_size() ;
//     Lit trail(int index) ;
//     void setVerbosity(int level) ;
//     // Var newVar() ;
//     Var newVar(bool dvar) ;
//     bool addClause(const vec<Lit>& ps) ;
//     bool addUnit(Lit p) ;
//     void suggestPolarity(Var x, lbool value) ;

//     bool solve_(const vec<Lit>& assumps) ;

//     lbool model(Var v) ;
//     // bool varElimed(Var x) ;
//     bool okay() ;
//     int nVars() const ;
//     void exportCnf(cchar* filename, Var out_max) ;
// };

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
class Solver_GlueMiniSat : public Solver {
  protected:

    GlueMiniSat::Lit lit2lit(const Lit lit) {
#ifdef DEBUG
      try {
#endif
        return GlueMiniSat::mkLit(var(lit), sign(lit));
#ifdef DEBUG
      } catch (GlueMiniSat::OutOfMemoryException&) {
        report->reportf("Out of Memory in executing GlueMiniSat::mkLit\n");
        raise(SIGTERM);
        __builtin_unreachable();
      }
#endif
    }
    void vec2vec(const vec<Lit>& in, GlueMiniSat::vec<GlueMiniSat::Lit>& out) {
      for (int i=0; i < in.size(); i++)
        out.push(lit2lit(in[i]));
    }
    lbool lbool2lbool(GlueMiniSat::lbool v) const {
      // case that lbool is the same structure of MiniSat2
      return toLbool(GlueMiniSat::toInt(v));
      // case that lbool is the same structure of MiniSat
      // switch (GlueMiniSat::toInt(v)) {
      //     case 0: return toLbool(+1);
      //     case 1: return toLbool(-1);
      //     case 2: return toLbool( 0);
      // }
      // assert(false);
      // return(ll_Error);
    }

  public:

    Solver_GlueMiniSat() : Solver() {}
    Solver_GlueMiniSat(Config* _config, bool _own_config)
      : Solver(_config, _own_config)
    {}

    BasicSolverStats& stats_ref  () {
      return Solver::basicStats;
    }
};

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
class Solver_CoreGlueMiniSat : public Solver_GlueMiniSat {

  GlueMiniSat::Solver*    core_glueminisat;

  public:

  Solver_CoreGlueMiniSat() : Solver_CoreGlueMiniSat(new Config, true) {}
  Solver_CoreGlueMiniSat(Config* _config, bool _own_config)
      : Solver_GlueMiniSat(_config, _own_config)
      , core_glueminisat( (GlueMiniSat::SimpSolver*) new GlueMiniSat::Solver )
  {}
  ~Solver_CoreGlueMiniSat() { delete core_glueminisat; }

  bool& ok_ref() ;
  lbool assigns(Var v) const ;
  int trail_size() ;
  Lit trail(int index) ;
  void setVerbosity(int level) ;
  // Var newVar() ;
  Var newVar(bool dvar) ;
  bool addClause(const vec<Lit>& ps) ;
  bool addUnit(Lit p) ;
  bool solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) ;
  lbool model(Var v) ;
  bool okay() ;
  int nVars() const ;
  void exportCnf(cchar* filename, Var out_max) ;

  int ucore_vars(int idx) ;
  int ucore_varsSize() ;
  int ucore_clauses(int idx) ;
  int ucore_clausesSize() ;

  void interrupt() ;
};

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
class Solver_SimpGlueMiniSat : public Solver_GlueMiniSat {

  GlueMiniSat::SimpSolver*    simp_glueminisat;

  public:

  Solver_SimpGlueMiniSat() : Solver_SimpGlueMiniSat(new Config, true) {}
  Solver_SimpGlueMiniSat(Config* _config, bool _own_config)
      : Solver_GlueMiniSat(_config, _own_config)
      , simp_glueminisat( new GlueMiniSat::SimpSolver )
  {}
  ~Solver_SimpGlueMiniSat() { delete simp_glueminisat; }

  bool& ok_ref() ;
  lbool assigns(Var v) const ;
  int trail_size() ;
  Lit trail(int index) ;
  void setVerbosity(int level) ;
  // Var newVar() ;
  Var newVar(bool dvar) ;
  bool addClause(const vec<Lit>& ps) ;
  bool addUnit(Lit p) ;
  bool solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) ;
  lbool model(Var v) ;
  bool okay() ;
  int nVars() const ;
  void exportCnf(cchar* filename, Var out_max) ;

  void freeze(Var x) ;
  bool varElimed(Var x) ;

  void interrupt() ;
};

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
class Solver_CaDiCaL : public Solver, public CaDiCaL::FixedListener2 {
  CaDiCaL::Solver*    cadical;
  bool                ok;
  int                 num_vars;
  std::unordered_set<int>      used_var;
  std::vector<int>    tmp_lits;
  std::vector<int>    fixed_lits;
  std::vector<Int>    goal_cs;
  std::vector<Lit>    goal_ps;
  std::vector<lbool>  model_vals;
  int                 verbose;
  int                 num_report;

  struct CaDiCaLTimeoutTerminator : public CaDiCaL::Terminator {
    double deadline;
    bool& timed_out_ref;
    CaDiCaLTimeoutTerminator(double dl, bool& ref) : deadline(dl), timed_out_ref(ref) {}
    bool terminate() override {
      if (cpuTime() >= deadline) { timed_out_ref = true; return true; }
      return false;
    }
  };
  CaDiCaLTimeoutTerminator* timeout_term = nullptr;

  int lit2dimacs(const Lit lit) const {
    return sign(lit) ? -(var(lit) + 1) : var(lit) + 1;
    // int v = var(lit);
    // return sign(lit) ? -(var(lit)) : var(lit);
  }
  int var2dimacs(const Var var) const {
    return var+1;
  }

  public:

  Solver_CaDiCaL() : Solver_CaDiCaL(new Config(), true) {}
  Solver_CaDiCaL(Config* _config, bool _own_config)
      : Solver(_config, _own_config)
      , cadical(new CaDiCaL::Solver)
      , ok(true)
      , num_vars(0)
      , verbose(0)
      , num_report(0)
  {
    if (!config->simp_solver)
      cadical->set_long_option("--plain");
    cadical->prefix((report->reportPrefix + "[CD] ").c_str());
    cadical->connect_fixed_listener(this);
    if (config->polarity_def == 1)
      cadical->set_long_option("--phase=true");
    else if (config->polarity_def == -1)
      cadical->set_long_option("--phase=false");
    std::string sat_ratio = "--stabilizeratio=" + std::to_string(config->sat_ratio);
    cadical->set_long_option(sat_ratio.c_str());
    cadical->set("terminateint", 1);
  }

  ~Solver_CaDiCaL() {
    // Detach ourselves before going away: the constructor connected 'this' as a fixed listener,
    // and 'setTimeout()' may have connected 'timeout_term' as a terminator.
    cadical->disconnect_fixed_listener2();
    if (timeout_term) cadical->disconnect_terminator();
    delete cadical;
    delete timeout_term;
  }

  bool& ok_ref() ;
  BasicSolverStats& stats_ref  () ;
  lbool assigns(Var v) const ;
  lbool assigns_(Var v) const ;
  int trail_size() ;
  Lit trail(int index) ;
  void setVerbosity(int level) ;
  // Var newVar() ;
  Var newVar(bool dvar) ;
  bool addClause(const vec<Lit>& ps) ;
  bool addUnit(Lit p) ;
  void suggestPolarity(Var x, lbool value) ;
  void setGoal(const vec<Lit>& ps, const vec<Int>& Cs) ;
  Int evalGoal() ;

  bool solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) ;

  lbool model(Var v) ;
  // bool varElimed(Var x) ;
  bool okay() ;
  int nVars() const ;
  void exportCnf(cchar* filename, Var out_max) ;

  void freeze(Var x) ;
  bool varElimed(Var x) ;

  void interrupt() ;
  void setTimeout(double seconds) override;
  void clearTimeout() override;

  // Interface of FixedListener
  void notify_fixed (int lit);
};

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
class Solver_Kissat : public Solver {
  kissat*             kissat_solver;
  bool                ok;
  int                 num_vars;
  std::vector<int>    tmp_lits;
  std::vector<lbool>  model_vals;
  int                 verbose;
  int                 num_report;

  struct KissatTermState { double deadline; bool* timed_out; };
  KissatTermState kissat_term_state;

  int lit2dimacs(const Lit lit) const {
    return sign(lit) ? -(var(lit) + 1) : var(lit) + 1;
  }
  int var2dimacs(const Var var) const {
    return var + 1;
  }

  public:

  Solver_Kissat() : Solver(), kissat_solver(nullptr), ok(true), num_vars(0) {}
  Solver_Kissat(Config* _config, bool _own_config)
      : Solver(_config, _own_config)
      , kissat_solver(kissat_init())
      , ok(true)
      , num_vars(0)
  {
    if (!config->simp_solver)
      kissat_set_configuration(kissat_solver, "plain");
    if (config->polarity_def == 1)
      kissat_set_option(kissat_solver, "phase", 1);
    else if (config->polarity_def == -1)
      kissat_set_option(kissat_solver, "phase", 0);
  }

  ~Solver_Kissat() { if (kissat_solver) kissat_release(kissat_solver); }

  bool& ok_ref() ;
  BasicSolverStats& stats_ref() ;
  lbool assigns(Var v) const ;
  int trail_size() ;
  Lit trail(int index) ;
  void setVerbosity(int level) ;
  Var newVar(bool dvar) ;
  bool addClause(const vec<Lit>& ps) ;
  bool addUnit(Lit p) ;

  bool solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) ;

  lbool model(Var v) ;
  bool okay() ;
  int nVars() const ;
  void exportCnf(cchar* filename, Var out_max) ;

  void interrupt() ;
  void setTimeout(double seconds) override;
  void clearTimeout() override;
};

//=================================================================================================

#endif

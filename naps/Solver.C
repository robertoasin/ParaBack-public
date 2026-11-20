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

#include "Solver.h"
#include "glueminisat/utils/Stat.h"
#include <stdexcept>
//#include "Main.h"

//=================================================================================================

// Virtual class for SAT solvers
bool& Solver::ok_ref() {static bool x; assert(false); return x;} ;
lbool Solver::assigns(Var v) const {assert(false); return ll_Undef;} ;
BasicSolverStats& Solver::stats_ref() {assert(false); return basicStats;} ;
int Solver::trail_size() {assert(false); return 0;} ;
Lit Solver::trail(int index) {assert(false); return lit_Error;} ;
void Solver::setVerbosity(int level) {assert(false); } ;
// Var Solver_MiniSat::newVar()   {
//     return newVar(true);
// }
Var Solver::newVar(bool dvar) {assert(false); return 0;} ;
bool Solver::addClause(const vec<Lit>& ps) {assert(false); return false;} ;

bool Solver::add_ESClause(const vec<Lit>& ps) {assert(false); return false;} ;
bool Solver::add_CCClause(const vec<Lit>& ps, int k) {assert(false); return false;} ;
bool Solver::addUnit(Lit p) {assert(false); return false;};

void Solver::freeze(Var x) {} ;
void Solver::suggestPolarity(Var x, lbool value) {} ;
bool Solver::solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) {assert(false); return false;} ;

lbool Solver::model(Var v) {assert(false); return ll_Error;} ;
bool Solver::varElimed(Var x) {return false;} ;

int Solver::ucore_vars(int idx) {assert(false); return 0;};
int Solver::ucore_varsSize() {assert(false); return 0;};
int Solver::ucore_clauses(int idx) {assert(false); return 0;};
int Solver::ucore_clausesSize() {assert(false); return 0;};
bool Solver::okay() {assert(false); return false;} ;
int Solver::nVars() const {assert(false); return 0;} ;


void Solver::exportCnf(cchar* filename, Var out_max) {
  report->reportf("-cnf: unsupported option.\n");
  throw std::runtime_error("-cnf: unsupported option.");
}

//- - - - - - - - - -
Solver* Solver_invoke(SolverT stype, Config* config) {
  bool createconfig = config==NULL;
  int verbosity = 0;
  bool simp_solver = true;
  if ( createconfig )
    config = new Config;
  else {
    verbosity = config->sat_verbosity;
    simp_solver = config->simp_solver;
  }

  Solver* solver = NULL;
  switch (stype) {
    case st_GlueMiniSat:
    case st_MiniSat22:
    case st_Glucose10:
      if (verbosity >= 2) {
        config->report.reportf("SatSolver: GlueMiniSat Mode: ");
        if(stype == st_GlueMiniSat) config->report.reportf("original\n");
        if(stype == st_MiniSat22) config->report.reportf("MiniSat22\n");
        if(stype == st_Glucose10) config->report.reportf("Glucose10\n");
      }
      if(simp_solver)
        solver = new Solver_SimpGlueMiniSat(config, createconfig);
      else
        solver = new Solver_CoreGlueMiniSat(config, createconfig);
      break;
    case st_Default:
      stype = st_CaDiCaL;
    case st_CaDiCaL:
      if (verbosity >= 2) config->report.reportf("SatSolver: CaDiCaL\n");
      solver = new Solver_CaDiCaL(config, createconfig);
      break;
    case st_Kissat:
      if (verbosity >= 2) config->report.reportf("SatSolver: Kissat\n");
      solver = new Solver_Kissat(config, createconfig);
      break;
    default:
      assert(false);
      break;
  }
  return solver;
} ;
//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

// // methods for MiniSAT
// //
// Solver_MiniSat::Solver_MiniSat()  {
//     minisat = new MiniSat::Solver;
// } ;
// bool& Solver_MiniSat::ok_ref() {
//     return minisat->ok;
// }
// BasicSolverStats& Solver_MiniSat::stats_ref  () {
//     return (BasicSolverStats&)minisat->stats;
// }
// lbool Solver_MiniSat::assigns(Var v) const {
//     return toLbool(minisat->assigns[v]);
// }
// int Solver_MiniSat::trail_size() {
//     return minisat->trail.size();
// }
// Lit Solver_MiniSat::trail(int index) {
//     return minisat->trail[index];
// }
// void Solver_MiniSat::setVerbosity(int level) {
//     minisat->verbosity = level;
// }
// Var Solver_MiniSat::newVar(bool dvar)   {
//     return minisat    ->newVar(dvar);
// }
// bool Solver_MiniSat::addClause(const vec<Lit>& ps) {
//     return minisat    ->addClause(ps);
// }
// bool Solver_MiniSat::addUnit(Lit p) {
//     return minisat    ->addUnit(p);
// }
// void Solver_MiniSat::suggestPolarity(Var x, lbool value) {
//     minisat->polarity_sug[x] = toInt(value);
// }
// bool Solver_MiniSat::solve_(const vec<Lit>& assumps) {
//     return minisat->solve(assumps);
// }
// lbool Solver_MiniSat::model(Var v) {
//     return minisat ->model[v];
// }
// bool Solver_MiniSat::okay() {
//     return minisat->okay();
// }
// int Solver_MiniSat::nVars() const {
//     return minisat->nVars() ;
// }
// void Solver_MiniSat::exportCnf(cchar* filename, Var out_max) {
//     minisat->exportClauses(filename);
// }
//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

// methods for CoreGlueMiniSat class
//
// Solver_CoreGlueMiniSat::Solver_CoreGlueMiniSat()  {
//     core_glueminisat = (GlueMiniSat::SimpSolver*) new GlueMiniSat::Solver;
// } ;

bool& Solver_CoreGlueMiniSat::ok_ref() {
  return core_glueminisat->getOK();
}
// BasicSolverStats& Solver_CoreGlueMiniSat::stats_ref  () {
//     return Solver::basicStats;
// }
lbool Solver_CoreGlueMiniSat::assigns(Var v) const {
  return lbool2lbool(core_glueminisat->getAssign(v));
}
int Solver_CoreGlueMiniSat::trail_size() {
  return core_glueminisat->getTrailSize();
}
Lit Solver_CoreGlueMiniSat::trail(int index) {
  return Lit(GlueMiniSat::var(core_glueminisat->getTrail(index)), GlueMiniSat::sign(core_glueminisat->getTrail(index)));
}
void Solver_CoreGlueMiniSat::setVerbosity(int level) {
  core_glueminisat->verbosity = level;
}
// newVar()
Var Solver_CoreGlueMiniSat::newVar(bool dvar)   {
  return core_glueminisat->newVar(true, dvar);
}
bool Solver_CoreGlueMiniSat::addClause(const vec<Lit>& ps) {
  GlueMiniSat::vec<GlueMiniSat::Lit> ps_;
  vec2vec(ps, ps_);
#ifdef DEBUG
  try {
#endif
    return core_glueminisat->addClause_(ps_);
#ifdef DEBUG
  } catch (GlueMiniSat::OutOfMemoryException&) {
    report->reportf("Out of Memory in adding a clause to core_glueminisat\n");
    raise(SIGTERM);
  }
#endif
  return core_glueminisat->addClause_(ps_);
}
bool Solver_CoreGlueMiniSat::addUnit(Lit p) {
#ifdef DEBUG
  try {
#endif
    return core_glueminisat->addClause(lit2lit(p));
#ifdef DEBUG
  } catch (GlueMiniSat::OutOfMemoryException&) {
    report->reportf("Out of Memory in adding unit to core_glueminisat\n");
    raise(SIGTERM);
  }
  return false;
#endif
}
bool Solver_CoreGlueMiniSat::solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) {
  bool ret = false;
  failed_assumps.clear();
  GlueMiniSat::vec<GlueMiniSat::Lit> assumps_;
  vec2vec(assumps, assumps_);
#ifdef DEBUG
  try {
#endif
    ret = core_glueminisat->solve(assumps_);
    for (int i=0; i < core_glueminisat->conflict.size(); i++) {
      GlueMiniSat::Lit p = core_glueminisat->conflict[i];
      Lit q = Lit(GlueMiniSat::var(p), GlueMiniSat::sign(p));
      failed_assumps.push(~q);
    }
#ifdef DEBUG
  } catch (GlueMiniSat::OutOfMemoryException&) {
    report->reportf("Out of Memory in solving by core_glueminisat\n");
    raise(SIGTERM);
  }
#endif
  if (config->sat_verbosity >= 2)
    GlueMiniSat::printStats(*core_glueminisat);
  basicStats.conflicts    += core_glueminisat->conflicts;
  basicStats.decisions    += core_glueminisat->decisions;
  basicStats.propagations += core_glueminisat->propagations;
  basicStats.starts       += core_glueminisat->starts;
  return ret;
}
lbool Solver_CoreGlueMiniSat::model(Var v) {
  return lbool2lbool(core_glueminisat->model[v]);
}

int Solver_CoreGlueMiniSat::ucore_vars(int idx) {
  return ((GlueMiniSat::Solver*)core_glueminisat)->ucore_vars[idx];
  // return core_glueminisat->getUcore_vars(idx);
}
int Solver_CoreGlueMiniSat::ucore_varsSize() {
  return ((GlueMiniSat::Solver*)core_glueminisat)->ucore_vars.size();
  //return core_glueminisat->getUcore_varsSize();
}
int Solver_CoreGlueMiniSat::ucore_clauses(int idx) {
  return ((GlueMiniSat::Solver*)core_glueminisat)->ucore_clauses[idx];
  // return core_glueminisat->getUcore_clauses(idx);
}
int Solver_CoreGlueMiniSat::ucore_clausesSize() {
  return ((GlueMiniSat::Solver*)core_glueminisat)->ucore_clauses.size();
  //return core_glueminisat->getUcore_clausesSize();
}

bool Solver_CoreGlueMiniSat::okay() {
  return core_glueminisat->okay();
}
int Solver_CoreGlueMiniSat::nVars() const {
  return core_glueminisat->nVars() ;
}

void Solver_CoreGlueMiniSat::exportCnf(cchar* filename, Var out_max) {
  core_glueminisat->toDimacs(filename, out_max);
}
void Solver_CoreGlueMiniSat::interrupt() {
  core_glueminisat->interrupt();
}
//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

// methods for SimpGlueMiniSat general class
//
// Solver_SimpGlueMiniSat::Solver_SimpGlueMiniSat()  {
//     simp_glueminisat = new GlueMiniSat::SimpSolver;
// } ;

bool& Solver_SimpGlueMiniSat::ok_ref() {
  return simp_glueminisat->getOK();
}
lbool Solver_SimpGlueMiniSat::assigns(Var v) const {
  return lbool2lbool(simp_glueminisat->getAssign(v));
}
int Solver_SimpGlueMiniSat::trail_size() {
  return simp_glueminisat->getTrailSize();
}
Lit Solver_SimpGlueMiniSat::trail(int index) {
  return Lit(GlueMiniSat::var(simp_glueminisat->getTrail(index)), GlueMiniSat::sign(simp_glueminisat->getTrail(index)));
}
void Solver_SimpGlueMiniSat::setVerbosity(int level) {
  simp_glueminisat->verbosity = level;
}
// newVar()
Var Solver_SimpGlueMiniSat::newVar(bool dvar)   {
  return simp_glueminisat->newVar(true, dvar);
}
bool Solver_SimpGlueMiniSat::addClause(const vec<Lit>& ps) {
  GlueMiniSat::vec<GlueMiniSat::Lit> ps_;
  vec2vec(ps, ps_);
#ifdef DEBUG
  try {
#endif
    return simp_glueminisat->addClause_(ps_);
#ifdef DEBUG
  } catch (GlueMiniSat::OutOfMemoryException&) {
    report->reportf("Out of Memory in adding a clause to simp_glueminisat\n");
    raise(SIGTERM);
  }
#endif
  return simp_glueminisat->addClause_(ps_);
}
bool Solver_SimpGlueMiniSat::addUnit(Lit p) {
#ifdef DEBUG
  try {
#endif
    return simp_glueminisat->addClause(lit2lit(p));
#ifdef DEBUG
  } catch (GlueMiniSat::OutOfMemoryException&) {
    report->reportf("Out of Memory in adding unit to simp_glueminisat\n");
    raise(SIGTERM);
  }
#endif
  return false;
}

void Solver_SimpGlueMiniSat::freeze(Var x) {
  simp_glueminisat->setFrozen(x, true); //            printf("variable %d is frozen\n", x);
}

bool Solver_SimpGlueMiniSat::solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) {
  bool ret = false;
  failed_assumps.clear();
  GlueMiniSat::vec<GlueMiniSat::Lit> assumps_;
  vec2vec(assumps, assumps_);
#ifdef DEBUG
  try {
#endif
    ret = simp_glueminisat->solve(assumps_);
    for (int i=0; i < simp_glueminisat->conflict.size(); i++) {
      GlueMiniSat::Lit p = simp_glueminisat->conflict[i];
      Lit q = Lit(GlueMiniSat::var(p), GlueMiniSat::sign(p));
      failed_assumps.push(~q);
    }
#ifdef DEBUG
  } catch (GlueMiniSat::OutOfMemoryException&) {
    report->reportf("Out of Memory in solving by simp_glueminisat\n");
    raise(SIGTERM);
  }
#endif
  if (config->sat_verbosity >= 2)
    GlueMiniSat::printStats(*simp_glueminisat);
  basicStats.conflicts    += simp_glueminisat->conflicts;
  basicStats.decisions    += simp_glueminisat->decisions;
  basicStats.propagations += simp_glueminisat->propagations;
  basicStats.starts       += simp_glueminisat->starts;
  return ret;
}
lbool Solver_SimpGlueMiniSat::model(Var v) {
  return lbool2lbool(simp_glueminisat->model[v]);
}

bool Solver_SimpGlueMiniSat::varElimed(Var x) {
  return simp_glueminisat->isEliminated(x);
}

bool Solver_SimpGlueMiniSat::okay() {
  return simp_glueminisat->okay();
}
int Solver_SimpGlueMiniSat::nVars() const {
  return simp_glueminisat->nVars() ;
}

void Solver_SimpGlueMiniSat::exportCnf(cchar* filename, Var out_max) {
  simp_glueminisat->toDimacs(filename, out_max);
}
void Solver_SimpGlueMiniSat::interrupt() {
  simp_glueminisat->interrupt();
}
//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

// methods for CaDiCaL class
//
// Solver_CaDiCaL::Solver_CaDiCaL()  {
//     cadical = new CaDiCaL::Solver;
//     ok = true;
//     num_vars = 0;
//     verbose = 0;
//     num_report = 0;
//     if (!config->simp_solver)
//         cadical->set_long_option("--plain");
//     cadical->prefix("c [CD] ");
//     cadical->connect_fixed_listener(this);
//     if (config->polarity_def == 1)
//         cadical->set_long_option("--phase=true");
//     else if (config->polarity_def == -1)
//         cadical->set_long_option("--phase=false");

//     std::string sat_ratio = "--stabilizeratio=" + std::to_string(config->sat_ratio);
//     cadical->set_long_option(sat_ratio.c_str());
// }
bool& Solver_CaDiCaL::ok_ref() {
  return ok;
}
BasicSolverStats& Solver_CaDiCaL::stats_ref() {
  return basicStats;
}
lbool Solver_CaDiCaL::assigns(Var v) const {
  lbool ret = assigns_(v);
  // printf("[CD] state = %d, assigns(%d) = %c\n", cadical->state(), v, "TFU"[toInt(ret)]);
  return ret;
}
lbool Solver_CaDiCaL::assigns_(Var v) const {
  if (cadical->state() & CaDiCaL::State::VALID) {
    int f = cadical->fixed(var2dimacs(v));
    if (f == 0) return ll_Undef;
    return f == 1 ? ll_True : ll_False;
  }
  return ll_Undef;
}
int Solver_CaDiCaL::trail_size() {
  return fixed_lits.size();
}
Lit Solver_CaDiCaL::trail(int index) {
  int p = fixed_lits[index];  // p is a dimacs variable
  int v = abs(p) - 1;         // v is a naps variable
  Lit l = toLit(v + v + ((p > 0) ? 0 : 1));
  return l;
}
void Solver_CaDiCaL::setVerbosity(int level) {
  if (level > 1) {
    cadical->set("verbose", level-1);
    verbose = level-1;
  }
  if (level) {
    cadical->set("report", 1);
    num_report = 1;
  }
}
Var Solver_CaDiCaL::newVar(bool dvar)   {
  //     return num_vars++;
  Var var = cadical->declare_one_more_variable () -1;
#ifdef DEBUG
  if(num_vars == var)
    report->reportf(4,"[CD] newVar: %d\n", var+1);
  else
    report->reportf(4,"[CD] newVar: %d, var+1: %d\n", num_vars+1, var+1);
#endif
  num_vars = max(num_vars,var+1);
  used_var.insert(var+1);
#ifdef DEBUG
  assert(num_vars == var+1);
#endif
  return var;
}
bool Solver_CaDiCaL::addClause(const vec<Lit>& ps) {
  // printf("[CD] addClause: ");
  tmp_lits.clear();
  for (int i=0; i < ps.size(); i++) {
    tmp_lits.push_back(lit2dimacs(ps[i]));
    // printf("%d ", lit2dimacs(ps[i]));
  }
  // printf("\n");
  cadical->clause(tmp_lits);
  return true;
}
bool Solver_CaDiCaL::addUnit(Lit p) {
  // printf("[CD] addUnit: %d\n", lit2dimacs(p));
  cadical->clause(lit2dimacs(p));
  // printf("[CD] addUnit: %d enterring simplify\n", lit2dimacs(p));
  // int res = cadical->simplify(0);
  // printf("[CD] addUnit: %d finished\n", lit2dimacs(p));
  if (cadical->inconsistent()) ok = false;
  return true;
}
void Solver_CaDiCaL::suggestPolarity(Var x, lbool value) {
  assert(value != ll_Undef);
  int lit = value == ll_True ? +var2dimacs(x) : -var2dimacs(x);
  cadical->phase(lit);
}
void Solver_CaDiCaL::setGoal(const vec<Lit>& ps, const vec<Int>& Cs) {
  assert(ps.size() == Cs.size());
  goal_ps.resize(ps.size());
  goal_cs.resize(Cs.size());
  for (int i=0; i < ps.size(); i++) {
    goal_ps[i] = ps[i];
    goal_cs[i] = Cs[i];
  }
}
Int Solver_CaDiCaL::evalGoal() {
  Int sum = 0;
  // report->reportf(3, "[CD] evalGoal: ");
  for (int i=0; i < (int)goal_ps.size(); i++) {
    // cadical->val(lit) returns the value of variable in lit, NOT the value of lit.
    int val_lit = sign(goal_ps[i]) ? -(cadical->val(lit2dimacs(goal_ps[i]))) : cadical->val(lit2dimacs(goal_ps[i]));
    assert(val_lit != 0);
    // report->reportf(3, "dimacs_lit=%d, val=%d ", lit2dimacs(goal_ps[i]), val_lit);
    if (val_lit > 0)
      sum += goal_cs[i];
  }
  // report->reportf(3, "\n");
  return sum;
}
bool Solver_CaDiCaL::solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) {
  if (!ok) return false;

  failed_assumps.clear();

  cadical->set("quiet", config->sat_verbosity < 2);
  cadical->set("verbose", verbose);
  cadical->set("report", num_report);

  int res = 20;
  // IMPORTANT: run preprocessing before setting assumptions.
  // CaDiCaL's simplify() may reset internal assumptions state.
  // Following lines are commented out to avoid heavy preprocessing.
  // res = cadical->simplify(0);
  // if (cadical->inconsistent()) ok = false;

  //printf("[CD] assump: ");
  for (int i=0; i < assumps.size(); i++) {
    cadical->assume(lit2dimacs(assumps[i]));
    //printf("%d ", lit2dimacs(assumps[i]));
  }
  //printf("\n");

  if (ok) res = cadical->solve();
  assert(res == 10 || res == 20 || res == 0);  // 0 = UNKNOWN (interrupted)

  // report->reportf("[CD] current goal_ps and goal_cs: ");
  // for (int i=0; i < (int)goal_ps.size(); i++) {
  //   char *cs = toString(goal_cs[i]);
  //   report->reportf("%s*~%d ", cs, goal_ps[i]);
  // }
  // report->reportf("\n");
  if (res == 10 && config->flip_min && goal_ps.size() > 0) {
    Int old_goal = evalGoal();
    int flip_count = 0;
    for (int i=0; i < (int)goal_ps.size(); i++) {
      bool is_assump = false;
      for (int j=0; j < assumps.size(); j++) {
        if (goal_ps[i] == assumps[j]) {
          is_assump = true;
          break;
        }
      }
      if (is_assump)
        continue;
      assert(goal_cs[i] > 0);
      int val_lit = sign(goal_ps[i]) ? -(cadical->val(lit2dimacs(goal_ps[i]))) : cadical->val(lit2dimacs(goal_ps[i]));
      if (val_lit < 0) {          // for maximization
        bool succ = cadical->flip(lit2dimacs(goal_ps[i]));
        if (succ) {
          Solver::goal_lit_flips++;
          flip_count++;
        }
      }
    }
    if (config->sat_verbosity >= 2) {
      Int new_goal = evalGoal();
      assert(new_goal >= old_goal);
      if (new_goal > old_goal) {
        assert(flip_count > 0);
        char *old_goal_str = toString(old_goal);
        char *new_goal_str = toString(new_goal);
        report->reportf("[CD]\n");
        report->reportf("[CD] increased goal from %s to %s by %d flipped literals\n", old_goal_str, new_goal_str, flip_count);
        xfree(old_goal_str);
        xfree(new_goal_str);
      }
    }
  }

  // copy the assignment if SAT
  model_vals.clear();
  if (res == 10) {
    model_vals.resize(num_vars + 1, ll_Undef);
    //printf("[CD] Found model: ");
    for (int v=1; v <= num_vars; v++) {
      //printf("%d ", cadical->val(v));
      if (used_var.find(v) == used_var.end())
        continue;
      if (cadical->val(v) > 0)
        model_vals[v] = ll_True;
      else
        model_vals[v] = ll_False;
    }
    //printf("\n");
  }
  else if (res == 20) {
    // collect failed assumptions
    for (int i=0; i < assumps.size(); i++) {
      int lit = lit2dimacs(assumps[i]);
      if (cadical->failed(lit)) {
        failed_assumps.push(assumps[i]);
      }
    }
  }

  if (cadical->inconsistent())
    ok = false;

  if (config->sat_verbosity >= 3) {
    cadical->statistics();
    cadical->resources();
    report->reportf("[CD]\n");
    report->reportf("[CD] --- [ result ] --------------------------------------------------------\n");
    report->reportf("[CD]\n");
    if (res == 10)
      report->reportf("[CD] SATISFIABLE\n");
    else if (res == 20)
      report->reportf("[CD] UNSATISFIABLE\n");
    else
      report->reportf("[CD] UNKNOWN\n");
    //report->reportf("[CD]\n");
    report->reportf("c\n");
  }
  // update statistics
  const CaDiCaL::Stats& stats = cadical->get_stats();
  basicStats.conflicts    = stats.conflicts;
  basicStats.decisions    = stats.decisions;
  basicStats.starts       = stats.restarts;
  basicStats.propagations = stats.propagations.cover;
  basicStats.propagations += stats.propagations.probe;
  basicStats.propagations += stats.propagations.search;
  basicStats.propagations += stats.propagations.transred;
  basicStats.propagations += stats.propagations.vivify;
  // stats.propagations.walk disappeard: uncommented by sakai
  // basicStats.propagations += stats.propagations.walk;

  // suppress log messages during simplify(0)
  cadical->set("quiet", true);

  return res == 10;
}
lbool Solver_CaDiCaL::model(Var v) {
  int dv = var2dimacs(v);
  //report->reportf("[CD] model(%d) = %c\n", dv, "TFU"[toInt(model_vals[dv])]);
  if (dv < (int)model_vals.size())
    return model_vals[dv];
  return ll_Undef;
}
bool Solver_CaDiCaL::okay() {
  return cadical->inconsistent();
}
int Solver_CaDiCaL::nVars() const {
  return num_vars;
}
void Solver_CaDiCaL::exportCnf(cchar* filename, Var out_max) {
  cadical->write_dimacs(filename);
}
void Solver_CaDiCaL::freeze(Var x) {
  cadical->freeze(var2dimacs(x));
}
bool Solver_CaDiCaL::varElimed(Var x) {
  return cadical->eliminated(var2dimacs(x));
}
void Solver_CaDiCaL::notify_fixed (int lit) {
  //printf("[CD] notify_fixed(%d)\n", lit);
  fixed_lits.push_back(lit);
}
void Solver_CaDiCaL::interrupt() {
  cadical->terminate();
}
void Solver_CaDiCaL::setTimeout(double seconds) {
  clearTimeout();   // drops a terminator left over from a previous call; also resets 'timed_out'
  timeout_term = new CaDiCaLTimeoutTerminator(cpuTime() + seconds, timed_out);
  cadical->connect_terminator(timeout_term);
}
void Solver_CaDiCaL::clearTimeout() {
  if (timeout_term) {
    cadical->disconnect_terminator();
    delete timeout_term;
    timeout_term = nullptr;
  }
  Solver::clearTimeout();
}

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
// methods for Kissat class
//
// Solver_Kissat::Solver_Kissat()  {
//     kissat_solver = kissat_init();
//     ok = true;
//     num_vars = 0;
//     if (!config->simp_solver)
//         kissat_set_configuration(kissat_solver, "plain");

//     if (config->polarity_def == 1)
//         kissat_set_option(kissat_solver, "phase", 1);
//     else if (config->polarity_def == -1)
//         kissat_set_option(kissat_solver, "phase", 0);
// }
bool& Solver_Kissat::ok_ref() {
  return ok;
}
BasicSolverStats& Solver_Kissat::stats_ref() {
  return basicStats;
}
lbool Solver_Kissat::assigns(Var v) const {
  int val = kissat_fixed_value(kissat_solver, var2dimacs(v));
  if (val == 0) return ll_Undef;
  return val > 0 ? ll_True : ll_False;
}
int Solver_Kissat::trail_size() {
  return 0;
}
Lit Solver_Kissat::trail(int index) {
  assert(false);
  return lit_Undef;
}
void Solver_Kissat::setVerbosity(int level) {
  if (level == 0)
    kissat_set_option(kissat_solver, "quiet", true);
  else
    kissat_set_option(kissat_solver, "verbose", level-1);
}
Var Solver_Kissat::newVar(bool dvar)   {
  return num_vars++;
}
bool Solver_Kissat::addClause(const vec<Lit>& ps) {
  for (int i=0; i < ps.size(); i++)
    kissat_add(kissat_solver, lit2dimacs(ps[i]));
  kissat_add(kissat_solver, 0);
  return true;
}
bool Solver_Kissat::addUnit(Lit p) {
  kissat_add(kissat_solver, lit2dimacs(p));
  kissat_add(kissat_solver, 0);
  return true;
}
bool Solver_Kissat::solve_(const vec<Lit>& assumps, vec<Lit>& failed_assumps) {
  // kissat doesn't support incremental SAT solving
  assert(assumps.size() == 0);
  if (!ok) return false;

  int res = kissat_solve(kissat_solver);
  assert(res == 10 || res == 20);

  // copy the assignment if SAT
  model_vals.clear();
  if (res == 10) {
    model_vals.resize(num_vars + 1, ll_Undef);
    for (int v=1; v <= num_vars; v++) {
      if (kissat_value(kissat_solver, v) > 0)
        model_vals[v] = ll_True;
      else
        model_vals[v] = ll_False;
    }
  }

  if (res == 20)
    ok = false;

  if (config->sat_verbosity >= 3) {
    kissat_print_statistics(kissat_solver);
    report->reportf(3, "\n");
    report->reportf(3, "--- [ kissat result ] ------------------------------------------------------\n");
    report->reportf(3, "\n");
    if (res == 10)
      report->reportf("SATISFIABLE\n");
    else if (res == 20)
      report->reportf("UNSATISFIABLE\n");
    else
      report->reportf("UNKNOWN\n");
  }
  // update statistics
  basicStats.conflicts    = kissat_get_num_conflicts(kissat_solver);
  basicStats.decisions    = kissat_get_num_decisions(kissat_solver);
  basicStats.starts       = kissat_get_num_restarts(kissat_solver);
  basicStats.propagations = kissat_get_num_propagations(kissat_solver);

  return res == 10;
}
lbool Solver_Kissat::model(Var v) {
  int dv = var2dimacs(v);
  if (dv < (int)model_vals.size())
    return model_vals[dv];
  return ll_Undef;
}
bool Solver_Kissat::okay() {
  return ok;
}
int Solver_Kissat::nVars() const {
  return num_vars;
}
void Solver_Kissat::exportCnf(cchar* filename, Var out_max) {
  assert(false);
}
void Solver_Kissat::interrupt() {
  kissat_terminate(kissat_solver);
}
void Solver_Kissat::setTimeout(double seconds) {
  timed_out = false;
  kissat_term_state = {cpuTime() + seconds, &timed_out};
  kissat_set_terminate(kissat_solver, &kissat_term_state,
    [](void* s) -> int {
      auto* ts = static_cast<KissatTermState*>(s);
      if (cpuTime() >= ts->deadline) { *ts->timed_out = true; return 1; }
      return 0;
    });
}
void Solver_Kissat::clearTimeout() {
  kissat_set_terminate(kissat_solver, nullptr, nullptr);
  Solver::clearTimeout();
}
//=================================================================================================

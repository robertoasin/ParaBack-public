#include <gtest/gtest.h>

//#include "Config.h"
//#include "SolverTypes.h"
#include "Solver.h"
// #include "Report.h"

Report test_report;

static bool contains_lit(const vec<Lit>& lits, Lit target) {
  for (int i = 0; i < lits.size(); i++) {
    if (lits[i] == target) return true;
  }
  return false;
}

static void expect_failed_is_subset_of_assumptions(const vec<Lit>& assumptions,
    const vec<Lit>& failed) {
  for (int i = 0; i < failed.size(); i++) {
    EXPECT_TRUE(contains_lit(assumptions, failed[i]))
      << "failed_assumps must contain original assumption literals";
  }
}

// When SAT, model(var(l)) must agree with the polarity of each assumption l.
static void expect_model_consistent_with_assumptions(Solver* solver,
    const vec<Lit>& assumps) {
  for (int i = 0; i < assumps.size(); i++) {
    Lit l = assumps[i];
    lbool expected = sign(l) ? ll_False : ll_True;
    EXPECT_EQ(solver->model(var(l)), expected)
      << "model must satisfy assumption literal at index " << i;
  }
}

static void add_clause1(Solver& solver, Lit a) {
  vec<Lit> c;
  c.push(a);
  ASSERT_TRUE(solver.addClause(c));
}

static void add_clause2(Solver& solver, Lit a, Lit b) {
  vec<Lit> c;
  c.push(a);
  c.push(b);
  ASSERT_TRUE(solver.addClause(c));
}

// Mirrors CaDiCaL's cadical.hpp [Example] but through the wrapper `Solver` API:
//   (-TIE v SHIRT) ^ (TIE v SHIRT) ^ (-TIE v -SHIRT)
// This has a unique model: TIE=false, SHIRT=true.
// Under assumption TIE it becomes UNSAT, and under assumption -SHIRT it becomes UNSAT.
static void run_cadical_example_like_incremental(Solver* solver) {
  ASSERT_NE(solver, nullptr);

  Var tie_var = solver->newVar(true);
  Var shirt_var = solver->newVar(true);
  Lit TIE = Lit(tie_var);
  Lit SHIRT = Lit(shirt_var);

  solver->freeze(tie_var);
  solver->freeze(shirt_var);

  add_clause2(*solver, ~TIE, SHIRT);
  add_clause2(*solver, TIE, SHIRT);
  add_clause2(*solver, ~TIE, ~SHIRT);

  // Solve without assumptions (SAT, unique model).
  {
    vec<Lit> none;
    vec<Lit> failed;
    const bool sat = solver->solve(none, failed);
    EXPECT_TRUE(sat);
    EXPECT_EQ(failed.size(), 0);
    EXPECT_EQ(solver->model(tie_var), ll_False);
    EXPECT_EQ(solver->model(shirt_var), ll_True);
    expect_model_consistent_with_assumptions(solver, none);
  }

  // Solve under one assumption: TIE.
  {
    vec<Lit> assumps;
    assumps.push(TIE);
    vec<Lit> failed;
    const bool sat = solver->solve(assumps, failed);
    EXPECT_FALSE(sat);
    expect_failed_is_subset_of_assumptions(assumps, failed);
    EXPECT_TRUE(contains_lit(failed, TIE));
  }

  // Solve under another assumption: -SHIRT.
  {
    vec<Lit> assumps;
    assumps.push(~SHIRT);
    vec<Lit> failed;
    const bool sat = solver->solve(assumps, failed);
    EXPECT_FALSE(sat);
    expect_failed_is_subset_of_assumptions(assumps, failed);
    EXPECT_TRUE(contains_lit(failed, ~SHIRT));
  }
}

// Slightly richer case: each assumption alone is satisfiable, but together UNSAT.
// a -> x, b -> y, and not(x and y).  With assumptions {a,b} it is UNSAT and both are necessary.
static void run_two_assumptions_both_necessary(Solver* solver) {
  ASSERT_NE(solver, nullptr);
  Var a_var = solver->newVar(true);
  Var b_var = solver->newVar(true);
  Var x_var = solver->newVar(true);
  Var y_var = solver->newVar(true);

  solver->freeze(a_var);
  solver->freeze(b_var);
  solver->freeze(x_var);
  solver->freeze(y_var);

  Lit a = Lit(a_var);
  Lit b = Lit(b_var);
  Lit x = Lit(x_var);
  Lit y = Lit(y_var);

  add_clause2(*solver, ~a, x);     // a -> x
  add_clause2(*solver, ~b, y);     // b -> y
  add_clause2(*solver, ~x, ~y);    // not (x and y)

  // Each alone SAT.
  {
    vec<Lit> assumps;
    assumps.push(a);
    vec<Lit> failed;
    const bool sat = solver->solve(assumps, failed);
    EXPECT_TRUE(sat);
    EXPECT_EQ(failed.size(), 0);
    expect_model_consistent_with_assumptions(solver, assumps);
  }
  {
    vec<Lit> assumps;
    assumps.push(b);
    vec<Lit> failed;
    const bool sat = solver->solve(assumps, failed);
    EXPECT_TRUE(sat);
    EXPECT_EQ(failed.size(), 0);
    expect_model_consistent_with_assumptions(solver, assumps);
  }

  // Together UNSAT; both necessary -> both should be reported.
  {
    vec<Lit> assumps;
    assumps.push(a);
    assumps.push(b);
    vec<Lit> failed;
    const bool sat = solver->solve(assumps, failed);
    EXPECT_FALSE(sat);
    expect_failed_is_subset_of_assumptions(assumps, failed);
    EXPECT_TRUE(contains_lit(failed, a));
    EXPECT_TRUE(contains_lit(failed, b));
  }
}

// Chain implication: a -> b -> c -> d.
// Positive assumption {a} forces b,c,d=true.
// Negative assumption {~d} blocks the chain.
// {~a, ~d}: both satisfied freely.
// {a, ~d}: UNSAT.
static void run_sat_chain_implication(Solver* solver) {
  ASSERT_NE(solver, nullptr);
  Var av = solver->newVar(true);
  Var bv = solver->newVar(true);
  Var cv = solver->newVar(true);
  Var dv = solver->newVar(true);
  solver->freeze(av); solver->freeze(bv);
  solver->freeze(cv); solver->freeze(dv);
  Lit a = Lit(av), b = Lit(bv), c = Lit(cv), d = Lit(dv);

  add_clause2(*solver, ~a, b);   // a -> b
  add_clause2(*solver, ~b, c);   // b -> c
  add_clause2(*solver, ~c, d);   // c -> d

  // {a}: SAT, a=true and b,c,d must also be true.
  {
    vec<Lit> assumps; assumps.push(a);
    vec<Lit> failed;
    EXPECT_TRUE(solver->solve(assumps, failed));
    EXPECT_EQ(failed.size(), 0);
    expect_model_consistent_with_assumptions(solver, assumps);
    EXPECT_EQ(solver->model(bv), ll_True);
    EXPECT_EQ(solver->model(cv), ll_True);
    EXPECT_EQ(solver->model(dv), ll_True);
  }

  // {~d}: SAT, d=false; a can be false (no forced chain).
  {
    vec<Lit> assumps; assumps.push(~d);
    vec<Lit> failed;
    EXPECT_TRUE(solver->solve(assumps, failed));
    EXPECT_EQ(failed.size(), 0);
    expect_model_consistent_with_assumptions(solver, assumps);
  }

  // {~a, ~d}: SAT, both assumptions simultaneously satisfiable.
  {
    vec<Lit> assumps; assumps.push(~a); assumps.push(~d);
    vec<Lit> failed;
    EXPECT_TRUE(solver->solve(assumps, failed));
    EXPECT_EQ(failed.size(), 0);
    expect_model_consistent_with_assumptions(solver, assumps);
  }

  // {a, ~d}: UNSAT (a forces d=true, contradicts ~d).
  {
    vec<Lit> assumps; assumps.push(a); assumps.push(~d);
    vec<Lit> failed;
    EXPECT_FALSE(solver->solve(assumps, failed));
    expect_failed_is_subset_of_assumptions(assumps, failed);
  }
}

// At-least-one constraint: (x v y v z).
// Assuming two negatives forces the third to be true.
// Assuming one positive is trivially SAT.
static void run_sat_at_least_one(Solver* solver) {
  ASSERT_NE(solver, nullptr);
  Var xv = solver->newVar(true);
  Var yv = solver->newVar(true);
  Var zv = solver->newVar(true);
  solver->freeze(xv); solver->freeze(yv); solver->freeze(zv);
  Lit x = Lit(xv), y = Lit(yv), z = Lit(zv);

  // x v y v z
  { vec<Lit> c; c.push(x); c.push(y); c.push(z); ASSERT_TRUE(solver->addClause(c)); }

  // {~x, ~y}: SAT; model must have x=false, y=false, z=true.
  {
    vec<Lit> assumps; assumps.push(~x); assumps.push(~y);
    vec<Lit> failed;
    EXPECT_TRUE(solver->solve(assumps, failed));
    EXPECT_EQ(failed.size(), 0);
    expect_model_consistent_with_assumptions(solver, assumps);
    EXPECT_EQ(solver->model(zv), ll_True);
  }

  // {x, ~z}: SAT; model must have x=true, z=false.
  {
    vec<Lit> assumps; assumps.push(x); assumps.push(~z);
    vec<Lit> failed;
    EXPECT_TRUE(solver->solve(assumps, failed));
    EXPECT_EQ(failed.size(), 0);
    expect_model_consistent_with_assumptions(solver, assumps);
  }

  // {~x, ~y, ~z}: UNSAT.
  {
    vec<Lit> assumps; assumps.push(~x); assumps.push(~y); assumps.push(~z);
    vec<Lit> failed;
    EXPECT_FALSE(solver->solve(assumps, failed));
    expect_failed_is_subset_of_assumptions(assumps, failed);
  }
}

// Multiple assumptions, all satisfiable simultaneously.
// x and y are free. Under assumptions {x, ~y}, the formula is SAT,
// and the model must agree with both literals.
static void run_sat_model_consistent_with_multiple_assumptions(Solver* solver) {
  ASSERT_NE(solver, nullptr);
  Var x_var = solver->newVar(true);
  Var y_var = solver->newVar(true);
  Lit x = Lit(x_var);
  Lit y = Lit(y_var);

  solver->freeze(x_var);
  solver->freeze(y_var);

  // No clauses: any assignment satisfies the formula.
  {
    vec<Lit> assumps;
    assumps.push(x);
    assumps.push(~y);
    vec<Lit> failed;
    const bool sat = solver->solve(assumps, failed);
    EXPECT_TRUE(sat);
    EXPECT_EQ(failed.size(), 0);
    expect_model_consistent_with_assumptions(solver, assumps);
  }
}

TEST(FailedAssumptions, CoreGlueMiniSat) {
  {
    Solver* solver = new Solver_CoreGlueMiniSat();
    run_cadical_example_like_incremental(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_CoreGlueMiniSat();
    run_two_assumptions_both_necessary(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_CoreGlueMiniSat();
    run_sat_model_consistent_with_multiple_assumptions(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_CoreGlueMiniSat();
    run_sat_chain_implication(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_CoreGlueMiniSat();
    run_sat_at_least_one(solver);
    delete solver;
  }
}

TEST(FailedAssumptions, SimpGlueMiniSat) {
  {
    Solver* solver = new Solver_SimpGlueMiniSat();
    run_cadical_example_like_incremental(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_SimpGlueMiniSat();
    run_two_assumptions_both_necessary(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_SimpGlueMiniSat();
    run_sat_model_consistent_with_multiple_assumptions(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_SimpGlueMiniSat();
    run_sat_chain_implication(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_SimpGlueMiniSat();
    run_sat_at_least_one(solver);
    delete solver;
  }
}

TEST(FailedAssumptions, CaDiCaL) {
  {
    Solver* solver = new Solver_CaDiCaL();
    run_cadical_example_like_incremental(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_CaDiCaL();
    run_two_assumptions_both_necessary(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_CaDiCaL();
    run_sat_model_consistent_with_multiple_assumptions(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_CaDiCaL();
    run_sat_chain_implication(solver);
    delete solver;
  }
  {
    Solver* solver = new Solver_CaDiCaL();
    run_sat_at_least_one(solver);
    delete solver;
  }
}

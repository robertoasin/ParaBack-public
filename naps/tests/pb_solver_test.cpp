#include <gtest/gtest.h>
// #include <typeinfo>

// necessary for PbSolver class
#include "Config.h"
#include "PbSolver.h"
#include "PbParser.h"
#include "Debug.h"
#include "SolverTypes.h"

void output(PbSolver* S) {
  PbSolver_Minimize* minSolver = dynamic_cast<PbSolver_Minimize*>(S);
  if (minSolver != nullptr && minSolver->get_pb_solver_status()==sst_best) {
    char* bv = toString(minSolver->best_goalvalue);
    minSolver->report->reportf("OPT: %s, ", bv);
    xfree(bv);
  }
  if( S->get_pb_solver_status()==sst_sat)
    S->report->reportf("SAT: ");
  if( S->get_pb_solver_status()==sst_sat || S->get_pb_solver_status()==sst_best) {
    // 'best_model' also covers unnamed (clausification/solver-internal) vars: print named only.
    for (int i = 0; i < S->named_vars.size(); i++) {
      Var x = S->named_vars[i];
      if (x >= S->best_model.size()) break;
      if(*(S->index2name[x]) != '@' )
        S->report->reportf(" %s%s", S->best_model[x]?"":"-", S->index2name[x]);
    }
    S->report->reportf("\n");
  } else if( S->get_pb_solver_status()==sst_all ) {
    S->report->reportf("ALL MODELS: %d\n", S->get_number_models());
  } else if( S->get_pb_solver_status()==sst_unsat ) {
    S->report->reportf("UNSAT\n");
  }
}

void sat_case(PbSolver* solver) {
  vec<Lit> ps; vec<Int> Cs; Int rhs;
  int ineq;  // -2 for <, -1 for <=, 0 for ==, 1 for >=, 2 for >.
  int add;
  Var var;

  Cs.push(Int(2));
  var = solver->getVar("x1");
  ps.push(Lit(var));
  Cs.push(Int(3));
  var = solver->getVar("x2");
  ps.push(Lit(var));
  Cs.push(Int(6));
  var = solver->getVar("x3");
  ps.push(Lit(var));
  // ps.push(Lit(solver->getVar("x3")));
  ineq = -1;
  rhs = Int(7);
  // 2 * x1 + 3 * x2 + 6 * x3 <= 7
  solver->report->reportf("2 x1 3 x2 6 x3 <= 7;\n");
  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  Cs.clear(); ps.clear();
  solver->report->reportf("addConstr: %d\n", add);

  Cs.push(Int(2));
  ps.push(Lit(solver->getVar("x1")));
  Cs.push(Int(3));
  ps.push(Lit(solver->getVar("x2")));
  Cs.push(Int(6));
  ps.push(Lit(solver->getVar("x3")));
  ineq = 1;
  rhs = Int(4);
  // 2 * x1 + 3 * x2 + 6 * x3 >= 4
  solver->report->reportf("2 x1 3 x2 6 x3 >= 4;\n");
  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  Cs.clear(); ps.clear();
  solver->report->reportf("addConstr: %d\n", add);
}

void set_goal_case(PbSolver* solver) {
  vec<Lit> ps; vec<Int> Cs; Int rhs;

  Cs.push(Int(1));
  ps.push(Lit(solver->getVar("x1")));
  Cs.push(Int(-1));
  ps.push(Lit(solver->getVar("x2")));
  Cs.push(Int(-1));
  ps.push(Lit(solver->getVar("x3")));
  solver->report->reportf("goal: 1 x1 -1 x2 -1 x3\n");
  solver->addGoal(ps, Cs);
  Cs.clear(); ps.clear();
  solver->report->reportf("addGoal: \n");
}

void min_multistage_case(PbSolver* solver) {
  vec<Lit> ps; vec<Int> Cs; Int rhs;
  int ineq;  // -2 for <, -1 for <=, 0 for ==, 1 for >=, 2 for >.
  int add;

  Cs.push(Int(1));
  ps.push(Lit(solver->getVar("x1")));
  Cs.push(Int(1));
  ps.push(Lit(solver->getVar("x2")));
  Cs.push(Int(4));
  ps.push(Lit(solver->getVar("x3")));
  Cs.push(Int(4));
  ps.push(Lit(solver->getVar("x4")));
  Cs.push(Int(4));
  ps.push(Lit(solver->getVar("x5")));
  ineq = 1;
  rhs = Int(5);
  // 1 * x1 + 1 * x2 + 4 * x3 + 4 * x4+ 4 * x5 >= 5
  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  Cs.clear(); ps.clear();
  solver->report->reportf("addConstr: %d\n", add);

  Cs.push(Int(1));
  ps.push(Lit(solver->getVar("x1")));
  Cs.push(Int(1));
  ps.push(Lit(solver->getVar("x2")));
  Cs.push(Int(10004));
  ps.push(Lit(solver->getVar("x3")));
  Cs.push(Int(10004));
  ps.push(Lit(solver->getVar("x4")));
  Cs.push(Int(20002));
  ps.push(Lit(solver->getVar("x5")));
  // 1 * x1 + 1 * x2 + 10004 * x3 + 10004 * x4 + 20002 * x5
  solver->addGoal(ps, Cs);
  Cs.clear(); ps.clear();
  solver->report->reportf("addGoal: \n");
}

void unsat_case(PbSolver* solver) {

  vec<Lit> ps; vec<Int> Cs; Int rhs;
  int ineq;  // -2 for <, -1 for <=, 0 for ==, 1 for >=, 2 for >.
  int add;

  Cs.push(Int(1));
  ps.push(Lit(solver->getVar("x1")));
  Cs.push(Int(2));
  ps.push(Lit(solver->getVar("x2")));
  ineq = -1;
  rhs = Int(1);
  // 1 * x1 + 2 * x2 <= 1
  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  Cs.clear(); ps.clear();
  solver->report->reportf("added: 1 * x1 + 2 * x2 <= 1\nresult: %d\n", add);

  Cs.push(Int(1));
  ps.push(Lit(solver->getVar("x1")));
  Cs.push(Int(2));
  ps.push(Lit(solver->getVar("x2")));
  ineq = 1;
  rhs = Int(2);
  // 1 * x1 + 2 * x2 >= 2
  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  Cs.clear(); ps.clear();
  solver->report->reportf("added: 1 * x1 + 2 * x2 >= 2\nresult: %d\n", add);
}

void set_extraGoal_for_garden9x9 (PbSolver* solver) {
  vec<Lit> ps; vec<Int> Cs; Int rhs;

  Cs.push(Int(1));
  ps.push(Lit(solver->getVar("x1")));
  Cs.push(Int(-1));
  ps.push(Lit(solver->getVar("x2")));
  Cs.push(Int(-1));
  ps.push(Lit(solver->getVar("x3")));
  // 1 * x1 - 1 * x2 - 1 * x3
  solver->addGoal(ps, Cs);
  Cs.clear(); ps.clear();
  solver->report->reportf("addGoal: \n");
}

TEST(PB_solver_test, sat)
{

  // seting solver1
  PbSolver* solver1 = new PbSolver() ;
  solver1->report->setReportFlag(0, true);

  sat_case(solver1);

  solver1->coding();
  solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", solver1->get_pb_solver_status());
  EXPECT_TRUE(solver1->get_pb_solver_status()==sst_sat);
  output(solver1);
  solver1->report->reportf("\n");

  delete solver1;
}

TEST(PB_solver_test, unsat)
{

  // seting solver1
  PbSolver* solver1 = new PbSolver() ;
  solver1->report->setReportFlag(0, true);

  unsat_case(solver1);

  solver1->coding();
  solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", solver1->get_pb_solver_status());
  EXPECT_TRUE(solver1->get_pb_solver_status()==sst_unsat);
  output(solver1);
  solver1->report->reportf("\n");

  delete solver1;
}

TEST(PB_solver_test, scip_sat)
{
  Config* config = new Config();
  config->verbosity = 3;
  config->use_scip = true;

  PbSolver* solver1 = new PbSolver(config);
  solver1->report->setReportFlag(0, true);

  sat_case(solver1);

  solver1->coding();
  solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", solver1->get_pb_solver_status());
  EXPECT_TRUE(solver1->get_pb_solver_status()==sst_sat);
  output(solver1);
  solver1->report->reportf("\n");

  delete solver1;
  delete config;
}

TEST(PB_solver_test, scip_unsat)
{
  Config* config = new Config();
  config->verbosity = 1;
  config->use_scip = true;

  PbSolver* solver1 = new PbSolver(config);
  solver1->report->setReportFlag(0, true);

  unsat_case(solver1);

  solver1->coding();
  solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", solver1->get_pb_solver_status());
  EXPECT_TRUE(solver1->get_pb_solver_status()==sst_unsat);

  delete solver1;
  delete config;
}

TEST(PB_solver_test, empty_constraint) {
  Config* config = new Config();
  //config->verbosity = 4;

  PbSolver* solver = new PbSolver_Minimize(config);
  set_goal_case(solver);

  solver->coding();
  solver->solve();

  output(solver);
  EXPECT_EQ(solver->get_best_goalvalue(), -2);

  delete solver;
  delete config;
}

TEST(PB_solver_test, incremental) {
  Config* config = new Config();
  //config->verbosity = 1;
  //config->single_stage_mode = true;
  config->repeated_optimizations = true;
  config->verbosity = 1;
  Report* test_report = new Report();
  test_report->setReportFlag(config->verbosity, true);
  config->report = *test_report;

  PbSolver* solver = new PbSolver_Minimize(config);

  vec<Lit> ps; vec<Int> Cs, Cg; Int rhs;
  int ineq;  // -2 for <, -1 for <=, 0 for ==, 1 for >=, 2 for >.
  Var x1 = solver->getVar("x1");
  Var x2 = solver->getVar("x2");
  Var x3 = solver->getVar("x3");

  Cg.push(Int(-2));   Cg.push(Int(-1));   Cg.push(Int(-2));
  Cs.push(Int(1));  ps.push(Lit(x1,false));
  Cs.push(Int(1));  ps.push(Lit(x2,false));
  Cs.push(Int(2));  ps.push(Lit(x3,false));
  dump(solver->config, 3, &solver->index2name, ps, Cs);

  // goal: -2 * x1 - 1 * x2 - 2 * x3
  solver->addGoal(ps, Cg);

  ineq = -1;  // <=
  rhs = Int(3);
  dump(solver->config, 3, &solver->index2name, ps, Cs);
  // 1 * x1 + 1 * x2 + 2 * x3 <= 3
  int add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  EXPECT_EQ(add, 1);
  solver->coding();
  solver->solve();
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_best);
  EXPECT_EQ(solver->best_model[0], 1);
  EXPECT_EQ(solver->best_model[1], 0);
  EXPECT_EQ(solver->best_model[2], 1);
  EXPECT_EQ(solver->get_best_goalvalue(), -4);
  char* gv = toString(solver->get_best_goalvalue());
  printf("best_goalvalue(): %s\n", gv);
  xfree(gv);

  ineq = -1;
  rhs = Int(2);  // <=
  // 1 * x1 + 1 * x2 + 2 * x3 <= 2
  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  solver->convertPbs(0);
  solver->solve();
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_best);
  EXPECT_EQ(solver->best_model[0], 1);
  EXPECT_EQ(solver->best_model[1], 1);
  EXPECT_EQ(solver->best_model[2], 0);
  EXPECT_EQ(solver->get_best_goalvalue(), -3);
  gv = toString(solver->get_best_goalvalue());
  printf("best_goalvalue(): %s\n", gv);
  xfree(gv);

  delete solver;
  delete config;
  delete test_report;
}

// Regression test for report/20260715_naps_vars_after_coding_bugreport.md:
// a var getVar()'ed after the first coding() must be readable from 'best_model'
// and its hard constraint must be reflected in 'best_goalvalue' deterministically
// (the old code read 'best_model' out of bounds for such vars).
TEST(PB_solver_test, incremental_var_after_coding) {
  Config* config = new Config();
  config->repeated_optimizations = true;
  config->verbosity = 1;
  Report* test_report = new Report();
  test_report->setReportFlag(config->verbosity, true);
  config->report = *test_report;

  PbSolver* solver = new PbSolver_Minimize(config);

  vec<Lit> ps; vec<Int> Cs;
  Var a = solver->getVar("a");
  Var b = solver->getVar("b");

  // a + b >= 1,  goal: a + b
  Cs.push(Int(1));  ps.push(Lit(a));
  Cs.push(Int(1));  ps.push(Lit(b));
  EXPECT_EQ(solver->addConstr(false, ps, Cs, Int(1), 1, Int_MAX, -2, lit_Undef, lit_Undef), 1);
  solver->addGoal(ps, Cs);
  ps.clear(); Cs.clear();

  solver->coding();
  solver->solve();
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_best);
  EXPECT_EQ(solver->get_best_goalvalue(), 1);

  // Register 'c' AFTER the first coding() and force it: c >= 1
  Var c = solver->getVar("c");
  Cs.push(Int(1));  ps.push(Lit(c));
  EXPECT_EQ(solver->addConstr(false, ps, Cs, Int(1), 1, Int_MAX, -2, lit_Undef, lit_Undef), 1);
  ps.clear(); Cs.clear();

  // goal: a + b + 50 c  (addGoal replaces the previous goal)
  Cs.push(Int(1));   ps.push(Lit(a));
  Cs.push(Int(1));   ps.push(Lit(b));
  Cs.push(Int(50));  ps.push(Lit(c));
  solver->addGoal(ps, Cs);
  ps.clear(); Cs.clear();

  solver->convertPbs(0);
  solver->solve();
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_best);
  EXPECT_EQ(solver->get_best_goalvalue(), 51);
  ASSERT_GE(solver->best_model.size(), c+1);
  EXPECT_TRUE(solver->best_model[c]);
  // 'c' must be visible through the named-vars view as well
  // (named_vars may end with solver-internal '@goal_constraint*' vars created during solve)
  bool c_named = false;
  for (int i = 0; i < solver->named_vars.size(); i++)
    if (solver->named_vars[i] == c) { c_named = true; break; }
  EXPECT_TRUE(c_named);

  delete solver;
  delete config;
  delete test_report;
}

TEST(PB_solver_test, scip_incremental) {
  Config* config = new Config();
  config->repeated_optimizations = true;
  config->use_scip = true;
  // Create a SAT solver externally
  Report* test_report = new Report();
  config->report = *test_report;


  PbSolver* solver = new PbSolver_Minimize(config);

  vec<Lit> ps; vec<Int> Cs, Cg; Int rhs;
  int ineq;
  Var x1 = solver->getVar("x1");
  Var x2 = solver->getVar("x2");
  Var x3 = solver->getVar("x3");

  Cg.push(Int(2));   Cg.push(Int(1));   Cg.push(Int(2));

  Cs.push(Int(1));  ps.push(Lit(x1,false));
  Cs.push(Int(1));  ps.push(Lit(x2,false));
  Cs.push(Int(2));  ps.push(Lit(x3,false));

  solver->addGoal(ps, Cg);

  ineq = -1;  // <=
  rhs = Int(3);
  int add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  EXPECT_EQ(add, 1);
  solver->coding();
  solver->solve();
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_best);

  for (int i = 0; i < solver->best_model.size(); i++)
    EXPECT_EQ(solver->best_model[i], 0);

  ineq = 1;
  rhs = Int(2);  // >=

  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  solver->solve();
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_best);
  EXPECT_EQ(solver->best_model[0], 0);
  EXPECT_EQ(solver->best_model[1], 0);
  EXPECT_EQ(solver->best_model[2], 1);
  EXPECT_EQ(solver->get_best_goalvalue(), 2);

  delete solver;
  delete config;
  delete test_report;
}

TEST(PB_solver_test, external_solver_injection) {
  Config* config = new Config();
  config->solver = st_CaDiCaL;

  // Create a SAT solver externally
  Report* test_report = new Report();
  config->report = *test_report;
  Solver* sat_solver = Solver_invoke(config->solver, config);

  // Create PbSolver with injected SAT solver
  PbSolver* pb_solver = new PbSolver(config, sat_solver);

  // Simple check to ensure it works
  vec<Lit> ps; vec<Int> Cs; Int rhs;
  Config* c = pb_solver->config; // accessing config to ensure it's linked
  EXPECT_EQ(c, config);

  // Add a simple constraint: 1*x1 >= 1
  Cs.push(Int(1));
  ps.push(Lit(pb_solver->getVar("x1")));
  int ineq = 1; // >=
  rhs = Int(1);
  pb_solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);

  pb_solver->coding();
  pb_solver->solve();
  EXPECT_EQ(pb_solver->get_pb_solver_status(), sst_sat);

  // Clean up
  delete pb_solver; // specific sat_solver should NOT be deleted by PbSolver
  delete sat_solver; // We own it, so we delete it
  delete config; // We own it
  delete test_report; // We own it
}

TEST(PB_solver_test, assumption)
{
  Config*   config  = new Config() ;
  config->verbosity = 2;
  PbSolver* solver = new PbSolver(config) ;

  Var y1 = solver->getVar("y1");
  Var y2 = solver->getVar("y2");
  Var y3 = solver->getVar("y3");
  Var y4 = solver->getVar("y4");

  vec<Lit> ps; vec<Int> Cs; Int rhs;
  int ineq;  // -2 for <, -1 for <=, 0 for ==, 1 for >=, 2 for >.
  int add;

  Cs.push(Int(1));  ps.push(Lit(y1));
  Cs.push(Int(1));  ps.push(Lit(y2));
  Cs.push(Int(2));  ps.push(Lit(y3));
  Cs.push(Int(2));  ps.push(Lit(y4));
  ineq = -1;
  rhs = Int(4);
  // 1 * y1 + 1 * y2 + 2 * y3 + 2 * y4 <= 4
  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  solver->report->reportf("added: 1 * y1 + 1 * y2 + 2 * y3 + 2 * y4 <= 4\nresult: %d\n", add);

  ineq = 1;
  rhs = Int(3);
  // 1 * y1 + 1 * y2 + 2 * y3 + 2 * y4 >= 3
  add = solver->addConstr(false, ps, Cs, rhs, ineq, Int_MAX, -2, lit_Undef, lit_Undef);
  solver->report->reportf("added: 1 * y1 + 1 * y2 + 2 * y3 + 2 * y4 >= 3\nresult: %d\n", add);
  ps.clear();

  solver->coding();

  Lit ly1 = Lit(y1,true), ly2 = Lit(y2,true), ly3 = Lit(y3,true), ly4 = Lit(y4,true);
  vec<Lit> assump, failed_assumps;

  assump.push(ly1); assump.push(ly2); assump.push(ly3); assump.push(ly4);
  solver->solve(assump, failed_assumps);
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_unsat);
  solver->report->reportf("failed_assumps: ");
  for (int i = 0; i < failed_assumps.size(); i++)
    solver->report->reportf("%s%s, ", sign(failed_assumps[i])?"~":"", solver->index2name[var(failed_assumps[i])]);
  solver->report->reportf("\n");
  assump.clear(); failed_assumps.clear();

  assump.push(ly1); assump.push(ly3); assump.push(ly4);
  solver->solve(assump, failed_assumps);
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_unsat);
  solver->report->reportf("failed_assumps: ");
  for (int i = 0; i < failed_assumps.size(); i++)
    solver->report->reportf("%s%s, ", sign(failed_assumps[i])?"~":"", solver->index2name[var(failed_assumps[i])]);
  solver->report->reportf("\n");
  assump.clear(); failed_assumps.clear();

  assump.push(ly1); assump.push(ly3);
  solver->solve(assump, failed_assumps);
  EXPECT_TRUE(solver->get_pb_solver_status()==sst_sat);
  EXPECT_TRUE(failed_assumps.size() == 0);
  for (int i = 0; i < solver->named_vars.size(); i++) {
    Var x = solver->named_vars[i];
    if (x >= solver->best_model.size()) break;
    solver->report->reportf("%s=%d, ", solver->index2name[x], solver->best_model[x]);
  }
  solver->report->reportf("\n");
  assump.clear(); failed_assumps.clear();

  delete solver;
  delete config;
}

TEST(PB_solver_test, two_solvers)
{

  // seting solver2
  PbSolver* solver2 = new PbSolver() ;

  sat_case(solver2);

  // seting solver1
  PbSolver* solver1 = new PbSolver() ;
  solver1->report->setReportFlag(0, true);

  unsat_case(solver1);

  // executions
  solver1->coding();
  solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", solver1->get_pb_solver_status());
  EXPECT_TRUE(solver1->get_pb_solver_status() == sst_unsat);
  output(solver1);

  solver2->coding();
  solver2->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", solver1->get_pb_solver_status());
  EXPECT_TRUE(solver2->get_pb_solver_status() == sst_sat);
  output(solver2);
  solver2->report->reportf("\n");

  delete solver1;
  delete solver2;
}

TEST(PB_solver_test, minimize)
{
  // solver1->report->setReportFlag(1, true);

  // seting solver1
  PbSolver* solver1 = new PbSolver_Minimize() ;
  // solver1->config->verbosity = 4;

  sat_case(solver1);
  set_goal_case(solver1);

  solver1->coding();
  int status = solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", status);

  output(solver1);
  solver1->report->reportf("\n");
  EXPECT_TRUE(solver1->get_pb_solver_status() == sst_best);
  EXPECT_EQ(solver1->get_best_goalvalue(), -1);

  delete solver1;
}

TEST(PB_solver_test, mapfile) {

  Config* config = new Config();
  config->verbosity = 1;

  PbSolver* solver1 = new PbSolver(config);
  solver1->allocateVars( 100 );
  parse_Map_file(config, "tests/sugar.map", *solver1);

  Var var;
  var = solver1->getVar("BOOL40");
  EXPECT_EQ(var, 95-1);
  var = solver1->getVar("x2");
  EXPECT_GE(var, 101);
  var = solver1->getVar("x3");
  EXPECT_GE(var, 102);

  delete config;
  delete solver1;
}

TEST(PB_solver_test, hitting)
{
  Config* config = new Config();
  config->verbosity = 1;
  config->minimization_strategy = mt_Hitting;
  // seting solver1
  PbSolver* solver1 = new PbSolver_Minimize(config) ;

  sat_case(solver1);
  set_goal_case(solver1);

  solver1->coding();
  int status = solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", status);

  output(solver1);
  solver1->report->reportf("\n");
  EXPECT_TRUE(solver1->get_pb_solver_status() == sst_best);
  EXPECT_EQ(solver1->get_best_goalvalue(), -1);

  delete solver1;
  delete config;
}

TEST(PB_solver_test, hitting_with_scip_inner)
{
  Config* config = new Config();
  config->verbosity = 1;
  config->minimization_strategy = mt_Hitting;
  config->inner_with_scip = true;
  // seting solver1
  PbSolver* solver1 = new PbSolver_Minimize(config) ;

  sat_case(solver1);
  set_goal_case(solver1);

  solver1->coding();
  int status = solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", status);

  output(solver1);
  solver1->report->reportf("\n");
  EXPECT_TRUE(solver1->get_pb_solver_status() == sst_best);
  EXPECT_EQ(solver1->get_best_goalvalue(), -1);

  delete solver1;
  delete config;
}

TEST(PB_solver_test, min_multistage)
{
  // solver1->report->setReportFlag(1, true);

  // seting solver1
  PbSolver* solver1 = new PbSolver_Minimize() ;

  min_multistage_case(solver1);

  solver1->coding();
  int status = solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", status);

  output(solver1);
  solver1->report->reportf("\n");
  EXPECT_TRUE(solver1->get_pb_solver_status() == sst_best);
  EXPECT_EQ(solver1->get_best_goalvalue(), 10005);

  delete solver1;
}

TEST(PB_solver_test, garden) {
  Config* config = new Config();
  config->input = "Examples/garden9x9.opb";
  //config->verbosity = 2;
  config->solver = st_GlueMiniSat;
  config->dis_conflict();
  // pb_solver->report->setReportFlag(1, true);

  PbSolver* pb_solver = new PbSolver_Minimize(config);

  parse_PB_file(config, config->input, *pb_solver, config->old_format);

  pb_solver->coding();
  pb_solver->solve();
  pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, true);
  EXPECT_EQ(pb_solver->get_best_goalvalue(), 20);

  delete pb_solver;
  delete config;
}

TEST(PB_solver_test, scip_garden) {
  Config* config = new Config();
  config->input = "Examples/garden9x9.opb";
  //config->verbosity = 2;
  config->use_scip = true;
  config->dis_conflict();
  // pb_solver->report->setReportFlag(1, true);

  PbSolver* pb_solver = new PbSolver_Minimize(config);

  parse_PB_file(config, config->input, *pb_solver, config->old_format);

  pb_solver->coding();
  pb_solver->solve();
  pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, true);
  EXPECT_EQ(pb_solver->get_best_goalvalue(), 20);

  delete pb_solver;
  delete config;
}

TEST(PB_solver_test, garden_with_extra_goal) {
  Config* config = new Config();
  config->input = "Examples/garden9x9.opb";
  //config->verbosity = 2;
  config->solver = st_GlueMiniSat;
  config->dis_conflict();
  config->repeated_optimizations = true;
  // ReportFlag(1, true);

  PbSolver* pb_solver = new PbSolver_Minimize(config);

  parse_PB_file(config, config->input, *pb_solver, config->old_format);

  vec<Lit> assump_ps;
  pb_solver->coding(assump_ps);   // code only once
  pb_solver->solve(assump_ps);
  pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, true);
  EXPECT_EQ(pb_solver->get_best_goalvalue(), 20);

  // add extra goal
  set_extraGoal_for_garden9x9(pb_solver);

  // Necessary to clear assumptions or to provde a fresh variable
  assump_ps.clear();

  pb_solver->solve(assump_ps);
  pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, true);
  EXPECT_EQ(pb_solver->get_best_goalvalue(), -2);

  delete pb_solver;
  delete config;
}

TEST(PB_solver_test, scip_garden_with_extra_goal) {
  Config* config = new Config();
  config->input = "Examples/garden9x9.opb";
  //config->verbosity = 2;
  config->use_scip = true;
  config->dis_conflict();
  config->repeated_optimizations = true;
  // ReportFlag(1, true);

  PbSolver* pb_solver = new PbSolver_Minimize(config);

  parse_PB_file(config, config->input, *pb_solver, config->old_format);

  vec<Lit> assump_ps;
  pb_solver->coding(assump_ps);   // code only once
  pb_solver->solve(assump_ps);
  pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, true);
  EXPECT_EQ(pb_solver->get_best_goalvalue(), 20);

  // add extra goal
  set_extraGoal_for_garden9x9(pb_solver);

  // Necessary to clear assumptions or to provde a fresh variable
  assump_ps.clear();

  pb_solver->solve(assump_ps);
  pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, true);
  EXPECT_EQ(pb_solver->get_best_goalvalue(), -2);

  delete pb_solver;
  delete config;
}

TEST(PB_solver_test, counting)
{
  // eportFlag(1, true);

  Config* config = new Config();
  config->command = cmd_AllSolutions;

  //config->verbosity = 2;
  // solver1->report->setReportFlag(1, true);

  // seting solver1
  PbSolver* solver1 = new PbSolver_Counting(config) ;
  // solver1->config->verbosity = 4;

  sat_case(solver1);

  solver1->coding();
  int status = solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", status);

  output(solver1);
  solver1->report->reportf("\n");
  EXPECT_TRUE(solver1->get_pb_solver_status() == sst_all);
  EXPECT_EQ(solver1->get_number_models(), 2);

  delete solver1;
  delete config;
}

TEST(PB_solver_test, project_counting)
{
  // solver1->report->setReportFlag(1, true);

  Config* config = new Config();
  config->command = cmd_InterestSolutions;

  //config->verbosity = 2;
  // solver1->report->setReportFlag(1, true);

  // seting solver1
  PbSolver* solver1 = new PbSolver_Counting(config) ;
  // solver1->config->verbosity = 4;

  sat_case(solver1);

  solver1->coding();
  solver1->addInterestVars(1);
  int status = solver1->solve();
  solver1->report->reportf("status: %d, (0: unknown, 1: sat, 2: unsat, 3:best, 4:all, 5:cnf)\n", status);

  output(solver1);
  solver1->report->reportf("\n");
  EXPECT_TRUE(solver1->get_pb_solver_status() == sst_all);
  EXPECT_EQ(solver1->get_number_models(), 2);

  delete solver1;
  delete config;
}

// Regression for report/20260714_naps_assumption_bug.md (bug A / bug B).
// Scenario:  a => x0 + x1 + x2 >= 2 (activation literal llt = a),  minimize 1*x0 + 2*x1 + 2*x2,
// solve([a]).  With repeated_optimizations on and a non-uniform goal, maximizeGoal used to drop
// the user assumption 'a' from its scratch stack, so 'a' went false, the guarded constraint
// became vacuous, and the optimum wrongly relaxed to 0.  With 'a' honoured the optimum is 3
// (the two cheapest of the three variables) and model[a] is true.
static void run_assumption_kept(MinT strat) {
  Config* config = new Config();
  config->verbosity = 0;
  config->repeated_optimizations = true;
  config->minimization_strategy = strat;
  Report* test_report = new Report();
  test_report->setReportFlag(config->verbosity, true);
  config->report = *test_report;

  PbSolver_Minimize* solver = new PbSolver_Minimize(config);
  Var a  = solver->getVar("a");
  Var x0 = solver->getVar("x0");
  Var x1 = solver->getVar("x1");
  Var x2 = solver->getVar("x2");

  vec<Lit> ps; vec<Int> Cs;
  ps.push(Lit(x0)); ps.push(Lit(x1)); ps.push(Lit(x2));
  Cs.push(Int(1));  Cs.push(Int(1));  Cs.push(Int(1));
  // a => x0 + x1 + x2 >= 2   (llt = a)
  int add = solver->addConstr(false, ps, Cs, Int(2), 1, Int_MAX, -1, Lit(a), lit_Undef);
  EXPECT_EQ(add, 1) << "strategy=" << (int)strat;

  // goal: minimize 1*x0 + 2*x1 + 2*x2
  vec<Int> Cg; Cg.push(Int(1)); Cg.push(Int(2)); Cg.push(Int(2));
  solver->addGoal(ps, Cg);
  ps.clear();

  solver->coding();
  vec<Lit> assump; assump.push(Lit(a));
  solver->solve(assump);

  EXPECT_TRUE(solver->get_pb_solver_status() == sst_best) << "strategy=" << (int)strat;
  EXPECT_EQ(solver->get_best_goalvalue(), 3) << "strategy=" << (int)strat;
  EXPECT_TRUE(solver->best_model[a]) << "assumption 'a' was dropped, strategy=" << (int)strat;

  delete solver;
  delete config;
  delete test_report;
}

TEST(PB_solver_test, assumption_kept_repeated) {
  // Hitting / Sequential_Hitting are scheduled for deletion and intentionally excluded.
  run_assumption_kept(mt_Sequential_Binary);
  run_assumption_kept(mt_Binary);
  run_assumption_kept(mt_Sequential);
  run_assumption_kept(mt_Sequential_Sc2);
}

TEST(PB_solver_test, sc2_assumption_no_segfault) {
  // Bug B: mt_Sc2 + repeated_optimizations + solve(assumps) used to SIGSEGV because the UNSAT
  // core still contained the user assumption, which was then indexed into the sc2-goal maps.
  // It must now run cleanly and return the correct optimum.
  run_assumption_kept(mt_Sc2);
}

int main(int argc, char **argv)
{
  // Initialize Google Test
  ::testing::InitGoogleTest(&argc, argv);

  // solver1_test();
  return RUN_ALL_TESTS();
}

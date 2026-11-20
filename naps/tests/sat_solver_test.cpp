#include <gtest/gtest.h>

// necessary for Solver class
//#include "Config.h"
//#include "SolverTypes.h"
#include "Solver.h"
#include "Debug.h"

Report test_report;

void printClause(vec<Lit>& clause) {
  for(int i = 0; i < clause.size(); i++)
    test_report.reportf(" %s%d", (sign(clause[i]) ? "-" : ""), var(clause[i]));
}

TEST(Sat_solver_test, TwoSolvers)
{
  // seting solver1
  // Solver* solver1 = Solver_invoke( st_CaDiCaL );
  Solver* solver1 = new Solver_CaDiCaL() ;
  // Solver* solver1 = new Solver_CoreGlueMiniSat() ;
  Var var11 = solver1->newVar(true);

  Lit lit11 = Lit(var11);
  Lit lit12 = ~lit11;

  vec<Lit> clause11, clause12;
  clause11.push(lit11);
  printf("clause11: "); printClause(clause11); printf("\n");
  solver1->addClause(clause11);

  solver1->assigns(var11);

  clause12.push(lit12);
  printf("clause12: "); printClause(clause12); printf("\n");
  solver1->addClause(clause12);

  // seting solver2
  Solver* solver2 = new Solver_CoreGlueMiniSat() ;
  Var var21 = solver2->newVar(true);

  Lit lit21 = Lit(var21);

  vec<Lit> clause21, clause22;
  clause21.push(lit21);
  printf("clause21: "); printClause(clause21); printf("\n");
  solver2->addClause(clause21);

  // solving on solver2
  int result2 = solver2->solve();
  if(result2) printf("solver2: SAT\n"); else printf("solver2: UNSAT\n");
  EXPECT_TRUE(result2);
  delete solver2;

  // solving on solver1
  int result1 = solver1->solve();
  if(result1) printf("solver1: SAT\n"); else printf("solver1: UNSAT\n");
  EXPECT_FALSE(result1);
  delete solver1;
}

int main(int argc, char **argv)
{
  // Initialize Google Test
  ::testing::InitGoogleTest(&argc, argv);

  // solver1_test();
  return RUN_ALL_TESTS();
}


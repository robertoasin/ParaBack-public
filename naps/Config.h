#ifndef Config_h
#define Config_h

#include "Report.h"
#include "Int.h"

enum SolverT  { st_Default, st_MiniSat, st_SatELite, st_GlueMiniSat, st_MiniSat22, st_Glucose10, st_ES1Sat, st_CCMiniSat, st_CaDiCaL, st_Kissat };
static const char* SolverT_name[] = { "Default", "MiniSat", "SatELite", "GlueMiniSat", "MiniSat22", "Glucose10", "ES1Sat", "CCMiniSat", "CaDiCaL", "Kissat" };
enum ConvertT { ct_Sorters, ct_Adders, ct_BDDs, ct_Mixed, ct_Mixed2, ct_Mixed3, ct_Mixed4, ct_Mixed5, ct_Mixed6, ct_Undef };
enum Command  { cmd_Minimize, cmd_FirstSolution, cmd_AllSolutions, cmd_InterestSolutions, cmd_Soft };
enum GpwT     { gt_none, gt_positive, gt_negative, gt_low, gt_high, gt_both };
enum ResultT  { rt_sat, rt_unsat, rt_optimized, rt_unknown, rt_all };
enum MinT     { mt_Sequential, mt_Binary, mt_Hitting, mt_Sequential_Binary, mt_Sequential_Hitting, mt_Sc2, mt_Sequential_Sc2};
enum BddOrderT { bo_decreasing, bo_increasing, bo_spread_factor };
struct Config {
  public:
    bool     satlive   = true;
    bool     ansi      = false;
    char*    cnf_export= NULL;      // export as cnf
    int      reserve_var = -1;      // Not to use sat-variables <= this value
    char*    map_file  = NULL;      // analize map-file generated from sugar
    int      verbosity = 0;
    int      inner_verbosity = 0;
    int      sat_verbosity = 0;
    int      report_trunc_len = 20;
    bool     try_parse = false;     // (hidden option -- if set, then "try" to parse, but don't output "s UNSUPPORTED" if you fail, instead exit with error code 5)
    bool     model_out = true;
    //bool     dimacs    = false;     // read Dimacs format
    bool     dimacs    = false;     // read Dimacs format
    bool     eager_cl  = true;     // sending SAT solver eagerly
    bool     maxsat    = false;     // MaxSat mode

    SolverT  solver        = st_Default;
    SolverT  inner_solver  = st_Default;
    bool     inner_with_scip = false;
    bool     is_inner_solver  = false;   // true when this config is for inner solver
    bool     simp_solver   = true;
    ConvertT convert       = ct_Mixed;
    ConvertT convert_goal  = ct_Mixed; // ct_Sorters;  //ct_Undef;
    bool     convert_goal_reusing = true;
    bool     convert_weak  = true;
    //bool     avoid_band_constraint = false;
    int      optbase_construction_alg = 2;    // 0: original  1:  BB,   2: hashBB
    int      optbase_measure = 2;             // 0:obt_digits, 1:obt_comb, 2:obt_kind, 3:obt_new
    int      optbase_primes = 29;
    int      optbase_thres = 100; // 500;
    int      optbase_reduced_primes = 1; //3;
    int      avoid_band_constraint = 0;
    // 0: band form, 1: band form if not-eq constraint, 2: only standard form
    bool     split         = false;
    bool     no_prop       = false;
    GpwT     convert_gpw   = gt_positive;
    GpwT     convert_gpw_goal = gt_positive;
    bool     convert_bdd_monotonic = true;
    int      convert_bdd_decomposition = 5;  // 0: non-decomposition, 1:binary base, 2:multi base
                                             //    2: always, n: strategy n-2  (3<=n<=5)
    BddOrderT convert_bdd_order = bo_decreasing;
    bool     single_stage_mode = false;
    bool     sat_solver_after_stage = true;  // to be eleminated
    bool     repeated_optimizations = true;;
    bool     or_detection  = true;
    bool     es1_detection = false;
    bool     cc_detection = false;
    bool     cc_sort   = true;
    bool     cc_sort_goal   = true;
    int      cc_thres      = 30; // use cc if k is less than <thres> %
    double   bdd_thres     = 180;
    double   sort_thres    = 90;
    // double   goal_bias     = 3;
    int      bdd_max_constraints = 2500000;
    int      bdd_max_adder_cost  = 3400;
    int      sort_max_adder_cost  = 750000;

    double   goal_bdd_thres     = 180;
    double   goal_sort_thres    = 270; // 3*90
    int      goal_bdd_max_constraints = 2500000;
    int      goal_bdd_max_adder_cost  = 2500;
    int      goal_sort_max_adder_cost  = 750000;

    Int      goal_ub  = Int_MAX;
    Int      goal_lb       = Int_MIN;
    Command  command       = cmd_FirstSolution;
    //bool     binary_minimization = true;
    bool     positive_mode = false;
    MinT      minimization_strategy = mt_Sequential_Binary;   // 0: sequential, 1: binary, 2: alternative
    double   bin_coeff    = 3;
    int      seq_thres    = 3;
    int      consec_unsat = 1;
    bool     band_for_goal = false;
    bool     goal_gcd = true;
    bool     branch_pbvars = false;
    bool     branch_goal_vars = false;  // This implies opt_branch_pbvars.
    int      polarity_sug  = 1;
    int      polarity_def  = 1;
    bool     flip_min      = true;
    int      sat_ratio     = 50;
    bool     old_format    = false;
    bool     extract_ucore = false;
    int      ucore_mode    = 3;
    bool     sc2_split     = true;
    int      sc2_split_max = 10;
    bool     sat_before_sc2 = true;

    bool     use_scip      = false;
    bool     use_cooperative_scip = false;   // interleave SCIP with NaPS optimization loop
    // Use SCIP-style normalization (normalizePb_SCIP, no unit propagation at parse time)
    // so all constraints are preserved in constrs and available to the SCIP instance.
    // Set true whenever SCIP is used (primary or cooperative).
    bool     use_scip_constraints = false;
    double   scip_time_budget = 5.0;        // time limit per cooperative SCIP call (seconds)
    double   scip_time_budget_factor = 1.4;  // multiplier applied to scip_time_budget after each cooperative SCIP call
    bool     scip_use_ub      = false;       // pass UB_goalvalue to cooperative SCIP calls
    double   sat_time_budget = 5.0;         // SAT time limit per call in coop SCIP mode (-1: no limit)
    double   sat_time_budget_factor = 1.4;   // multiplier applied to sat_time_budget after each cooperative SAT call
    double   double_unknown_budget_factor = 2.0;  // extra multiplier applied to both budgets when both SAT and SCIP time out in the same iteration
    double   scip_promising_gap_threshold  = 30.0;  // presolver gap < this (%) triggers early budget boost (negative = disabled)
    double   scip_promising_boost_budget   = 1000.0; // initial SCIP budget when presolver gap is below threshold
    bool     scip_promising_single_stage   = false;   // switch to single-stage mode when presolver gap is below threshold
    double   scip_as_presolve    = 10.0;          // SCIP presolver time limit before CNF coding (0 = disabled)
    double   scip_as_presolve_for_dec = 500.0;    // SCIP presolver for decision problems (only when -cSCIP is enabled)
    bool     scip_no_presolve    = false;          // disable SCIP's internal presolver (presolving/maxrounds=0)
    bool     scip_stage_prop = false;         // at stage boundaries, run SCIPpresolve with equality constraint to propagate genuinely-fixed variables to SAT
    int      scip_stage_prop_rounds = 3;      // max presolving rounds for stage-boundary propagation (-1: unlimited)
    bool     scip_solve_prop = false;              // before each cooperative SCIPsolve, run SCIPpresolve with dual reductions disabled to propagate variables fixed by the combination of stage equality + ub_hint constraints
    int      scip_solve_prop_rounds = 3;           // max presolving rounds for solve-time propagation (-1: unlimited)
    bool     scip_eager_free_transform = false;    // call free transform more conservative

    bool     model_check   = false;

    double   given_time    = -1.0;   // total wall-clock time limit in seconds (-1 = no limit)

    Report   report;                   // value member; always valid

    const char*    input  = NULL;
    char*    result = NULL;

    // statistics
    int      stats_bdd_cost = 0;
    int      stats_sort_cost = 0;
    int      stats_adder_cost = 0;
    int      stats_bdd_raw_constraints = 0;
    int      stats_bdd_bin_constraints = 0;
    int      stats_bdd_mul_constraints = 0;
    int      stats_sort_constraints = 0;
    int      stats_cc_sort = 0;
    int      stats_adder_constraints = 0;
    int      stats_monotonic_coding = 0, stats_non_monotonic_coding = 0;
    int      stats_std_form = 0, stats_band_form = 0;
    int      stats_sat_calls = 0, stats_unsat_calls = 0;
    int      stats_bdd_nodes = 0;
    int      stats_es1_detection = 0;
    int      stats_cc_detection = 0;
    int      stats_regen_variables = 0;
    int      stats_elim_fixed_lits = 0;
    //int      stats_goal_lit_flips = 0;
    int      stats_goal_stages = 0;
    int      stats_goal_shrink = 0;
    int      stats_coeff_separations = 0;
    vec<Int> stats_goal_gcd_seq;
    double   stats_cnf_coding_time;
    double   stats_opt_base_calc_time = 0.0;
    int64    stats_opt_base_calc_steps = 0;
    double   stats_BDD_coding_time = 0.0;
    double   stats_srt_coding_time = 0.0;
    double   stats_coding_time = 0.0;
    int      stats_inner_solver_invocation = 0;
    int      stats_inner_solving_count = 0;
    double   stats_basic_solver_time= 0.0;
    double   stats_minimizing_solver_time= 0.0;
    double   stats_counting_solver_time= 0.0;
    double   stats_scip_time = 0.0;
    int      stats_coop_scip_lb_improvements = 0;  // SCIP solution improved LB
    int      stats_coop_scip_ub_improvements = 0;  // SCIP dual bound tightened UB
    int      stats_scip_fixed_vars = 0;            // SCIP global fixed variables
    int      stats_sat_fixed_vars  = 0;            // SAT trail fixed variables (newly learned)
    int      stats_scip_free_transform_count = 0;  // number of SCIPfreeTransform calls

    void     dis_conflict()
    {
      if(command != cmd_FirstSolution && solver == st_Kissat)
        solver = st_CaDiCaL;
      flip_min = false;
      if(solver == st_Default)
        solver = command == cmd_FirstSolution ? st_Kissat : st_CaDiCaL;

      if(inner_solver == st_Kissat)
        inner_solver = st_CaDiCaL;
      if(inner_solver == st_Default)
        inner_solver = st_CaDiCaL;
    }
};

#endif

/******************************************************************************************[Main.h]
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

#ifndef Main_h
#define Main_h

#include "Int.h"
#include "Version.h"
#include "FEnv.h"
#include "Report.h"
#include "Solver.h"

//=================================================================================================

// /**** Moved to Config.h
// enum SolverT  { st_Default, st_MiniSat, st_SatELite, st_GlueMiniSat, st_MiniSat22, st_Glucose10, st_ES1Sat, st_CCMiniSat, st_CaDiCaL, st_Kissat };
// enum ConvertT { ct_Sorters, ct_Adders, ct_BDDs, ct_Mixed, ct_Mixed2, ct_Mixed3, ct_Mixed4, ct_Mixed5, ct_Mixed6, ct_Undef };
// enum Command  { cmd_Minimize, cmd_FirstSolution, cmd_AllSolutions, cmd_InterestSolutions, cmd_Soft };
// enum GpwT     { gt_none, gt_positive, gt_negative, gt_low, gt_high, gt_both };
// enum ResultT  { rt_sat, rt_unsat, rt_optimized, rt_unknown, rt_all };
// ****/

// // -- output options:
// extern bool     opt_satlive;
// extern bool     opt_ansi;
// extern char*    opt_cnf;
// extern int      opt_verbosity;
// extern bool     opt_try;
// extern bool     opt_model_out;
// extern bool     opt_dimacs;
// extern bool     opt_eager_cl;

// // -- solver options:
// extern SolverT  opt_solver;
// extern bool	opt_simp_solver;
// extern ConvertT opt_convert;
// extern ConvertT opt_convert_goal;
// extern bool     opt_convert_goal_reusing;
// extern GpwT     opt_convert_gpw;
// extern bool     opt_convert_bdd_monotonic;
// //extern bool     opt_convert_bdd_binary_decomposition;
// extern int      opt_convert_bdd_decomposition;
// extern BddOrderT opt_convert_bdd_order;
// extern bool     opt_single_stage_mode;
// extern bool     opt_sat_solver_after_stage;
// extern bool     opt_convert_weak;
// extern int      opt_optbase_construction_alg;
// extern int      opt_optbase_measure;
// extern int      opt_optbase_primes;
// extern int      opt_optbase_thres;
// extern int      opt_optbase_reduced_primes;
// extern bool     opt_or_detection;
// extern bool     opt_es1_detection;
// extern bool     opt_cc_detection;
// extern bool     opt_cc_sort;
// extern bool     opt_cc_sort_goal;
// extern int      opt_avoid_band_constraint;
// extern bool     opt_split;
// extern bool     opt_no_prop;
// extern int      opt_cc_thres;
// extern double   opt_bdd_thres;
// extern double   opt_sort_thres;
// extern int      opt_consec_unsat;
// // extern double   opt_goal_bias;
// extern int      opt_bdd_max_constraints;
// extern int      opt_bdd_max_adder_cost;
// extern int      opt_sort_max_adder_cost;
// extern double   opt_goal_bdd_thres;
// extern double   opt_goal_sort_thres;
// extern int      opt_goal_bdd_max_constraints;
// extern int      opt_goal_bdd_max_adder_cost;
// extern int      opt_goal_sort_max_adder_cost;

// extern Int      opt_goal_ub;
// extern Int      opt_goal_lb;
// extern Command  opt_command;
// //extern bool     opt_binary_minimization;
// extern bool      opt_positive_mode;
// extern int      opt_minimization;
// extern double   opt_bin_coeff;
// extern int      opt_seq_thres;
// extern bool     opt_band_for_goal;
// extern bool     opt_goal_gcd;
// extern bool     opt_branch_pbvars;
// extern bool     opt_branch_goal_vars;
// extern int      opt_polarity_sug;
// extern int      opt_polarity_def;
// extern bool     opt_flip_min;
// extern int      opt_sat_ratio;
// extern bool     opt_extract_ucore;
// extern int      opt_ucore_mode;

// // -- extra stats
// extern int      stats_bdd_cost;
// extern int      stats_sort_cost;
// extern int      stats_adder_cost;
// extern int      stats_bdd_raw_constraints;
// extern int      stats_bdd_bin_constraints;
// extern int      stats_bdd_mul_constraints;
// extern int      stats_sort_constraints;
// extern int      stats_cc_sort;
// extern int      stats_adder_constraints;
// extern int      stats_monotonic_coding,stats_non_monotonic_coding;
// extern int      stats_std_form,stats_band_form;
// extern int      stats_sat_calls,stats_unsat_calls;
// extern int      stats_bdd_nodes;
// extern int      stats_es1_detection;
// extern int      stats_cc_detection;
// extern int	    stats_regen_variables;
// extern int	    stats_elim_fixed_lits;
// //extern int      stats_goal_lit_flips;
// extern int      stats_goal_stages;
// extern int      stats_goal_shrink;
// extern int      stats_coeff_separations;
// extern vec<Int> stats_goal_gcd_seq;
// extern double   stats_cnf_coding_time;
// extern double   stats_opt_base_calc_time;
// extern int64    stats_opt_base_calc_steps;
// extern double   stats_BDD_coding_time;
// extern double   stats_srt_coding_time;

// // -- debug:
// extern bool     opt_model_check;
// // -- files:
// extern char*    opt_input;
// extern char*    opt_result;

//=================================================================================================

#endif

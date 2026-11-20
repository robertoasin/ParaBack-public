/**************************************************************************************[PbSolver.h]
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

#ifndef PbSolver_h
#define PbSolver_h

#include "Config.h"
#include "Global.h"
#include "Solver.h"
#include "Map.h"
#include "StackAlloc.h"
#include "FEnv.h"
#include "Hardware.h"



//=================================================================================================
// PbSolver -- Pseudo-boolean solver (linear boolean constraints):

enum solverStatus  { sst_unknown, sst_sat, sst_unsat, sst_best, sst_all, sst_cnf };
static const char* solverStatus_strs[] = { "sst_unknown", "sst_sat", "sst_unsat", "sst_best", "sst_all", "sst_cnf" };
// enum solve_Command { sc_Minimize, sc_FirstSolution, sc_AllModels, sc_InterestModels, sc_cnf };
// static const char* solve_Command_strs[] = { "sc_Minimize", "sc_FirstSolution", "sc_AllModels", "sc_InterestModels", "sc_cnf" };

enum Minimization_Mode {cmm_First_solve, cmm_Sequential, cmm_Binary, cmm_Hitting, cmm_sc2};
static const char* Minimization_Mode_strs[] = { "cmm_First_solve", "cmm_Sequential", "cmm_Binary", "cmm_Hitting", "cmm_Sc2" };

class PbSolver {
  public:
    Config*             config;
    Config*             config_inner;
  protected:
    bool                own_config;     // Release config when destruting this class if it is true.
    bool                own_sat_solver; // Release sat_solver when destructing this class if it is true.

  public:
    Report*             report;         // convenience pointer → always &config->report
    Config*             sat_config;     // Config copy for SAT solver with deepened report prefix; always owned.
    Solver*             sat_solver;     // Underlying SAT solver.
    PbSolver*           pb_solver_inner; // Inner solver for minimization mode.
    Clausifier*         clausifier;     // Converter of Formula to clauses
  protected:
    bool&               ok;             // True means unsatisfiability has not been detected.

    StackAlloc<char*>   mem;            // Used to allocate the 'Linear' constraints stored in 'constrs' (other 'Linear's, such as the goal function, are allocated with 'xmalloc()')

  public:
    vec<Linear*>        constrs;        // Vector with all constraints.
    vec<Linear*>        constrs_bk;     // Vector with all constraints for model check.
    vec<Linear*>        constrs_deleted;// Constraints dropped from 'constrs' (propagated away, merged, ...).
                                        // They live on 'mem', so they are kept here until their
                                        // coefficients can be destructed -- see 'freeConstrs()'.

    // Remove constraint 'i' from 'constrs' while keeping its 'Linear' reachable for destruction.
    void dropConstr(int i) {
      if (constrs[i] != NULL) { constrs_deleted.push(constrs[i]); constrs[i] = NULL; } }

  protected:
    vec<int>            n_occurs;       // Lit -> int: Number of occurrences.
    vec<vec<int> >      occur;          // Lit -> vec<int>: Occur lists. Left empty until 'setupOccurs()' is called.

    int                 propQ_head;     // Head of propagation queue (index into 'trail').

  public:
    static Config* makeSatConfig(Config* c) {
        Config* nc = new Config();
        nc->report        = c->report;   // copy Report (deepened below)
        nc->report.deepenPrefix();
        nc->simp_solver   = c->simp_solver;
        nc->polarity_def  = c->polarity_def;
        nc->sat_ratio     = c->sat_ratio;
        nc->sat_verbosity = c->sat_verbosity;
        nc->verbosity     = c->verbosity;
        nc->flip_min      = c->flip_min;
        nc->ansi          = c->ansi;
        return nc;
    }

    PbSolver(Config* _config, bool _own_config, Solver* _sat_solver, bool _own_sat_solver)
      : config(_config)
        , own_config(_own_config)
        , own_sat_solver(_own_sat_solver)
        , report(&_config->report)
        , sat_config(makeSatConfig(_config))
        , sat_solver(_own_sat_solver ? Solver_invoke(config->solver, sat_config) : _sat_solver )
        , pb_solver_inner(nullptr)
        , clausifier(nullptr)
        , ok(sat_solver->ok_ref())
        , propQ_head(0)
        , stats(sat_solver->stats_ref())
        , declared_n_vars(-1)
        , declared_n_constrs(-1)
        , pb_i_vars(-1)
        , pb_solver_status(sst_unknown)
        , var_dec_mode(true)
        , minimization_mode(false)
        , solve_reentry(false)
        , scip_env(nullptr)
        , scip_vars(nullptr)
        , scip_constrs_added(0)
        , scip_trail_synced(0)
        , scip_last_goal(nullptr)
        , scip_coop_offset(0.0)
        , scip_event_fired(false)
        // , scip_coop_stagecount(0)
        , scip_in_transformed_state(false)
        , scip_ub_hint_cons(nullptr)
        , scip_last_lb_hint_scip(0.0)
        , scip_last_was_timelimit(false)
        , scip_last_nnodes(1)  // for safe since 1 and timeout force freeing transform
        , interrupted(false)
        , convert_previousStage(0)
        {
            config_inner = new Config();
            config_inner->verbosity = config->inner_verbosity;
            config_inner->sat_verbosity = config->sat_verbosity;
            config_inner->repeated_optimizations = true;
            config_inner->use_scip = config->inner_with_scip;
            config_inner->solver = config->inner_solver;
            if(config->inner_solver == st_GlueMiniSat) {
              config_inner->flip_min = false;
            }
            config_inner->report = sat_config->report;
            config_inner->is_inner_solver = true;
            config_inner->report.setReportFlag(config->inner_verbosity, config->ansi);
        }
    PbSolver(Config* _config, Solver* _sat_solver)
      : PbSolver(_config, false, _sat_solver, false)
    {}
    PbSolver()                 : PbSolver(new Config(), true,  NULL, true) {}
    PbSolver(Config* _config)  : PbSolver(_config,      false, NULL, true) {}
    // NOTE: the command line tool never gets here -- 'Main.C' leaves via 'exit()' on purpose.
    // This matters for the Python bindings, which construct and destroy solvers repeatedly.
    virtual ~PbSolver() {
      freeScip();
      delete pb_solver_inner;   // holds 'config_inner' with 'own_config' false, so free it first
      delete clausifier;
      freeConstrs();
      freeVarNames();
      delete config_inner;
      if (own_config) delete config;
      if (own_sat_solver) delete sat_solver;   // 'ok' and 'stats' refer into it -- keep last
      delete sat_config;
    }

    void freeConstrs();   // Destructs the 'Linear's in 'constrs'/'constrs_bk' and releases 'mem'.
    void freeVarNames();  // Frees the strings 'getVar()' duplicated into 'index2name'.

    // copy gurad
    PbSolver(const PbSolver&) = delete;
    PbSolver& operator=(const PbSolver&) = delete;


    // Main internal methods:
    //
    bool    propagate(Linear& c);
    void    propagate();
    bool    addUnit  (Lit p) { return sat_solver->addUnit(p); }
    bool    normalizePb(bool goalMode, vec<Lit>& ps, vec<Int>& Cs, Int& C, Lit llt=lit_Undef);
    bool    normalizePb_SCIP(bool normalized, vec<Lit>& ps, vec<Int>& Cs, Int& C, Lit llt=lit_Undef);
    void    storePb    (const vec<Lit>& ps, const vec<Int>& Cs, Int lo, Int hi, Lit llp=lit_Undef);
    void    setupOccurs();   // Called on demand from 'propagate()'.
    bool    findIntervals();
    bool    rewriteES1(Linear& c);
    bool    rewriteCC(Linear& c);
    Formula rewriteCCbySorter(Linear& c, int stage);
    bool    rewriteAlmostClauses();
    bool    rewritePureClause(const vec<Lit>& ps, const vec<Int>& Cs, Int lo, Int hi, Lit llt);
    bool    convertPbs(int stage);   // Called from 'solve()' to convert PB constraints to clauses.
                                     // first_call -> stage: true -> 0, false -> 1, 2, ...
    bool    model_check();
    bool    goalValueCheck(Linear& goalExp, Int goal_val);
    bool    checkAssumptions();  // true if constrs has at least one constraint with llt != lit_Undef

    // Helpers (semi-internal):
    //
    lbool   value(Var x) const { return sat_solver->assigns(x); }
    lbool   value(Lit p) const { return sign(p) ? ~value(var(p)) : value(var(p)); }
    int     nVars()      const { return sat_solver->nVars(); }
    int     nConstrs()   const { return constrs.size(); }
    virtual Int evalGoal(const Linear& goal) { return Int_MIN;};


    // Helpers
    virtual Int get_best_goalvalue() { return Int_MIN;};
    virtual int get_number_models() { return -1;};
    virtual Linear* get_original_goal() { return NULL;};
    virtual void addGoal          (const vec<Lit>& ps, const vec<Int>& Cs) {};

    virtual void addInterestVars(int num);

    // Public variables:
    BasicSolverStats& stats;

    int     declared_n_vars;            // Number of variables declared in file header (-1 = not specified).
    int     declared_n_constrs;         // Number of constraints declared in file header (-1 = not specified).
    int     pb_n_vars;                  // Actual number of variables (before clausification).
    int     pb_n_constrs;               // Actual number of constraints (before clausification).
    int     pb_i_vars;                  // Number of interest variables.

    Map<cchar*, int>    name2index;
    vec<cchar*>         index2name;
    vec<Var>            named_vars;     // SAT vars created by 'getVar()', in creation order (values are
                                        // strictly increasing, but not contiguous: clausification and the
                                        // SAT solver itself may create unnamed vars in between).
    vec<bool>           best_model;     // Best model found (size is 'nVars()' at the last commit).

    // Guarded 'best_model' access: vars created after the last commit read as false.
    bool    bestModelVal (Var v) const { return v < best_model.size() && best_model[v]; }
    bool    bestModelTrue(Lit p) const { return sign(p) ? !bestModelVal(var(p)) : bestModelVal(var(p)); }
private:
    solverStatus        pb_solver_status;  // sst_unknown, sst_sat, sst_unsat, sst_best, sst_all.
public:
    virtual solverStatus        get_pb_solver_status() const { return pb_solver_status; }
    virtual void                set_pb_solver_status(solverStatus status) { pb_solver_status = status; }
    vec<int>            ucore_var_id;

    // Controlling declaration of decision variables
    bool    var_dec_mode;
    // Flag to indicate minimization mode, and not in coding mode
    bool    minimization_mode;
    // Memo for BDD structure to be reused in minimization mode
    std::map<Pair< int, Interval<Int> >, Pair< Interval<Int>, Formula> > memo_bdd_conv_int_in_min_mode;

protected:
    bool    solve_reentry;

public:
    // SCIP state fields
    void*   scip_env;
    void*   scip_vars;
    int     scip_constrs_added;
    int     scip_trail_synced;   // number of SAT trail entries synced to SCIP variable bounds
    void*   scip_last_goal;
    double  scip_coop_offset;     // sum of coef for negated literals in coop_goal; bridges SCIP obj ↔ NaPS internal units
    bool    scip_event_fired;     // set in eventExecBestSol; reset before each SCIPsolve(); used to detect presolver path
    // int     scip_coop_stagecount; // 0 = non-coop SCIP; else = stageCount of current coop call (set around solveScip)
    bool    scip_in_transformed_state; // true when SCIPfreeTransform was skipped; SCIP holds its B&B state
    void*   scip_ub_hint_cons;         // persistent coop UB-hint constraint (managed across solveScip calls)
    double  scip_last_lb_hint_scip;    // last value passed to SCIPsetObjlimit; NaN = not set
    bool    scip_last_was_timelimit;   // true when previous solveScip ended with SCIP_STATUS_TIMELIMIT
    int     scip_last_nnodes;          // number of nodes processed in previous solveScip call
    bool    interrupted;          // set by interruptSolve(); used to prefer sst_unknown over sst_unsat on interrupt
    double  scip_current_time_budget; // tracks current per-call budget; initialized from scip_time_budget, multiplied by scip_time_budget_factor each coop call
    double  sat_current_time_budget;  // tracks current per-call budget; initialized from sat_time_budget, multiplied by sat_time_budget_factor each coop call
    double  solve_start_time;         // cpuTime() at the start of solve(); used to compute remaining time within given_time
    vec<lbool> globally_fixed_var;    // per-variable confirmed-fixed value from either SAT or SCIP (ll_Undef = not fixed)
    void    freeScip();
    void    initScipEnv();
    void    initScipForCoop();
    void    syncSatFixedVars();   // sync SAT trail to globally_fixed_var and SCIP bounds
    void    addScipLinearConstraint(const vec<Lit>& ps, const vec<Int>& Cs, Int lb, Int ub = Int_MAX);
    double  getScipFeastol() const;
    void    checkScipPrecisionSafety();
    solverStatus solveScip(vec<Lit>& assump_ps,
                           double time_limit  = -1.0,
                           const Linear* coop_goal = nullptr,
                           Int    lb_hint     = Int_MIN,
                           Int*   out_lb      = nullptr,
                           Int*   out_dual_ub = nullptr,
                           Int    ub_hint     = Int_MAX,
                           double* out_scip_gap = nullptr,
                           int*   out_nnodes  = nullptr);
    virtual void interruptSolve();

    // Problem specification:
    //
    // Note: id = k-1 between sat-solver variable id in NaPS and k in sat-solvers.
    void    allocateVars(int satVar);    // get new sat-solver variables until satVar
    void    alignVar    (cchar* name, int satVar);
                                         // assign var-name with satVar
    int     getVar      (cchar* name);   // Fetch sat-solver variable, or register if not found
    void    allocConstrs(int n_vars, int n_constrs);

    /////////////////////
    // body ineq rhs, body ineq2 rhs2
    //   e.g.  -1: exp <= rhs and 1: exp >= rhs2
    // -2: <,  -1: <=,  0: ==,  1: >=,  2: >
    ///// addConstr( ) ////////////////
    // normalized: the constraint is in normalized form
    // ps:         list of literals
    // Cs:         list of coefficients
    // rhs, ineq, rhs2, ineq2:
    //             exp [ineq] rhs, and exp [ineq2] rhs2
    // llt:        llt implies the constraint if llt is a defined literal
    // rlt:        the constraint implies rlt if rlt is a defined literal
    bool    addConstr   (bool normalized, const vec<Lit>& ps, const vec<Int>& Cs, Int rhs, int ineq, Int rhs2=Int_MAX, int ineq2= -1, Lit llt=lit_Undef, Lit rlt=lit_Undef);
    // addConstr_( ): helper for addConstr( )
    bool    addConstr_  (bool normalized, const vec<Lit>& ps, const vec<Int>& Cs, Int rhs, int ineq, Int rhs2=Int_MAX, int ineq2= -1, Lit llt=lit_Undef);


    // Solve:
    //
    bool    okay(void) { return ok; }

    virtual solverStatus  coding(vec<Lit>& assump_ps);  // code constrs and send to sat_solver
    virtual solverStatus  coding() { vec<Lit> empty_assump_ps; return coding(empty_assump_ps); }

    // Returns best/first solution found or Int_MAX if UNSAT.
    // failed_assumps is filled with the failed assumptions if UNSAT.

    virtual solverStatus  solve(vec<Lit>& assump_ps, vec<Lit>& failed_assumps);
    virtual solverStatus  solve(vec<Lit>& assump_ps) {
       vec<Lit> failed_assumps;
       return solve(assump_ps, failed_assumps);
    }
    virtual solverStatus  solve() {
       vec<Lit> assump_ps; vec<Lit> failed_assumps;
       return solve(assump_ps, failed_assumps);
    }

    solverStatus sat_solver_invoke(vec<Lit>& assump_ps, vec<Lit>& failed_assumps, double time_limit = -1.0);
    solverStatus sat_solver_invoke(vec<Lit>& assump_ps, double time_limit = -1.0) {
       vec<Lit> failed_assumps;
       return sat_solver_invoke(assump_ps, failed_assumps, time_limit);
    }
    void commitBestModel();  // Copies the current model from sat_solver to best_model, and runs model_check.

    virtual void outputResult(solverStatus status, Config* config, bool normalExit = true);
    void         printStats(BasicSolverStats& stats, double cpu_time, Solver* solver, PbSolver* inner_pb_solver = nullptr);


    // Exporting info into CNF
    void exportVar(cchar* filename);
    void exportInterVar (cchar* filename);
    void exportToCnf(cchar* filename);

    //
    // conversions
  protected:
    int                 convert_previousStage;  // Previous stage value for convertPbs().
    vec<int>            convert_base_sort;      // Multi-base for sorting network (persistent across stages).
    vec<int>            convert_base_bdd;       // Multi-base for BDD encoding (persistent across stages).
    Formula tryConvertBdd(Linear& c, int max_cost, int stage);
    Formula tryConvertSort(Linear& c, int max_cost, int stage);
    Formula tryConvertAdder(Linear& c);
    Formula convertOneConstraint(Linear& c, ConvertT convert,
        int& bdd_max_adder_cost, int& sort_max_adder_cost, int stage);

    Pair<Interval<Int>, Formula>
      buildBDD_i(const vec<Lit>& ps, const vec<Int>& Cs,
          Int lo, int size, Int sum, Int material_left,
          std::map<Pair< int, Interval<Int> >,
          Pair< Interval<Int> , Formula> > &memo,
          int max_cost);
    Pair<Pair<Interval<Int>,Interval<Int> >, Formula>
      buildBDD_i_b(const vec<Lit>& ps, const vec<Int>& Cs,
          Int lo, Int hi, int size, Int sum, Int material_left,
          std::map<Pair< int, Pair< Interval<Int> , Interval<Int> > >,
          Pair<Pair< Interval<Int> , Interval<Int> >, Formula> > &memo_b,
          int max_cost);

  public:
    Formula linearAddition(const Linear& l);
    Formula buildConstraint(const Linear& c, vec<int>& base, int max_cost, int stage);
  protected:
    bool buildConstraintp(vec<Formula>& ps, vec<Int>& Cs, vec<Formula>& carry, vec<int>& base, int digit_no, vec<vec<Formula> >& out_digits, int max_cost);
    Formula buildConstraintp(vec<Formula>& ps, vec<Int>& Cs, vec<int>& base, Int lo, Int hi, int max_cost);
    Formula buildConstraintGpw(vec<Formula>& ps, vec<Int>& Cs, vec<int>& base, Int lo, int max_cost);
  public:
    Formula convertToBdd_one(vec<Lit>& ls, vec<Int>& Cs, Pair<Interval<Int>,Interval<Int> >& intervals, vec<int>& base, int decomp_mode, bool reuse_memo, int max_cost, int stage);
    Formula convertToBdd(const Linear& c, Pair< Interval<Int>, Interval<Int> >& intervals, vec<int>& base, int decomp_mode, bool reuse_memo, int max_cost, int stage);

};

class PbSolver_Minimize : public PbSolver {
  public:
    Linear*             original_goal;  // Linear expression for goal function specified in the input for optimization.  NOTE! We are always minimizing.
    Linear*             goal;           // The normalized goal function.  NULL means no goal function specified.     Int                 goal_offset;    // Real goal is goal_coeff*goal
                                        //    Linear*             goal1;          // The bigger part of goal function.  NULL means no goal function specified.     Int                 goal_offset;    // Real goal is goal_coeff*goal
                                        //    Linear*             goal2;          // The smaller part of goal function.  NULL means no goal function specified.     Int                 goal_offset;    // Real goal is goal_coeff*goal
    Int                 goal_offset;    // The constant part of normalized goal function.
    Int                 goal_coeff;     // Real goal is goal_coeff*goal+goal_offset
    Interval<Int>       goal_known_interval; // Range of known goal values
    vec<int>            goal_multi_base_sort;  // Multi-base for sorting network
    vec<int>            goal_multi_base_bdd;   // Multi-base for bdd expansion
private:
    solverStatus        pb_solver_status;  // sst_unknown, sst_sat, sst_unsat, sst_best, sst_all.
public:
    virtual solverStatus        get_pb_solver_status() const override { return pb_solver_status; }
    virtual void                set_pb_solver_status(solverStatus status) override { pb_solver_status = status; }


  public:
    PbSolver_Minimize(Config* _config, bool _own_config, Solver* _sat_solver, bool _own_sat_solver)
      : PbSolver(_config, _own_config, _sat_solver, _own_sat_solver)
        , original_goal(NULL)
        , goal(NULL)
        , pb_solver_status(sst_unknown)
        , best_goalvalue(Int_MIN)
        , last_printed_goalvalue(Int_MAX)
        , has_model_for_bestvalue(false)
      , presolve_lb(Int_MIN)
      , presolve_ub(Int_MAX)
      , presolve_scip_gap(-1.0)
      , presolve_nnodes(0)
      , num_improved_by_scip(0)
      , num_improved_by_sat(0)
  {}

    PbSolver_Minimize()                : PbSolver_Minimize(new Config(), true,  NULL, true) {}
    PbSolver_Minimize(Config* _config) : PbSolver_Minimize(_config,      false, NULL, true) {}
    PbSolver_Minimize(Config* _config, Solver* _sat_solver)
      : PbSolver_Minimize(_config, false, _sat_solver, false)
    {}

    ~PbSolver_Minimize() override { freeGoals(); }

    // Destructs 'goal', 'original_goal' and 'goals'.  They come from 'xmalloc()' and are not
    // owned by 'mem'.  Also called from 'addGoal()', which replaces any goal set earlier.
    void freeGoals();

    // Main internal methods:
    //
    Int     normalizeGoal(vec<Lit>& ps, vec<Int>& Cs);
    void    simplifyGoal(vec<Lit>& ps, vec<Int>& Cs);
    bool    convertPbGoal();
    // Called from 'solve()' to convert a PB constraint for goal to clauses.
    void    solverSetting(Linear* goalExp);
    bool    divideGoal(Linear* goalExp, vec<Linear*>& goals);
    vec<Linear*> goals;
    vec<Int>     goals_best_values;
    bool    printBestGoalValue();
    bool    addGoalConstraint(const vec<Lit>& goal_ps, const vec<Int>& goal_Cs, Int try_moreThanOrEq, vec<Lit>& assump_ps, int stageCount, Int rhs2 = Int_MAX);
    // One iteration of the hitting-set minimization strategy, called from 'maximizeGoal'.
    // Returns true iff the caller should 'continue' its enclosing while-loop immediately.
    bool    processHittingSetIteration(Linear* goalExp, const Int& LB_goalvalue, Int& UB_goalvalue,
                                        ResultT& current_result, bool& exec_loop,
                                        const vec<Lit>& goal_ps, const vec<Lit>& failed_assumptions,
                                        vec<Lit>& hitting_assumptions,
                                        const vec<Var>& goalInd2outerVar, const vec<Var>& goalInd2innerVar,
                                        const vec<Var>& outerVar2goalInd, const vec<bool>& goalInd2sign);
    // If the stratification threshold in sc2 mode is met, add (*sc2_goal)[i] to hitting_assumptions.
    void    pushSc2HittingAssumptionIfAboveThreshold(Linear* sc2_goal, const vec<Int>& strat_weights,
                                                      vec<Lit>& hitting_assumptions);
    // Computes stratification weights strat_weights from the range of coefficients of sc2_goal (reducing them to config->sc2_split_max or fewer).
    void    computeSc2StratWeights(Linear* sc2_goal, vec<Int>& strat_weights);
    bool    findModelForBestValueBySAT(Linear* goalExp, Int& lb, vec<Lit>& assump_ps, int stageCount, double budget);
    bool    findModelForBestValueBySCIP(Linear* goalExp, Int& lb, double budget);
    void    findModelForBestValue(Linear* goalExp, Int& lb, vec<Lit>& assump_ps, int stageCount);
    // void    get_hitting_sets(vec<Lit>& hitting_assumptions, const vec<vec<Lit>>& hitting_sets, const vec<Lit>& goal_ps_orig, const vec<Int>& goal_Cs_orig, int verbosity);
    ResultT maximizeGoal(Linear* goalExp, Int& lb, Int& ub, vec<Lit>& assump_ps, int stageCount = 1, bool finalStep = false);
    // Wrapper for default arguments
    ResultT maximizeGoal(Linear* goalExp, Int& lb, Int& ub) {
      vec<Lit> empty_ps; return maximizeGoal(goalExp, lb, ub, empty_ps); }
    Int     evalGoalUB(const Linear& goal);
    Int     evalGoalLB(const Linear& goal);
    Int     evalGoalUB(const Linear& goal, const vec<Lit>& hitting_assumptions, const vec<Var>& outerVar2goalInd);


    // Helpers (semi-internal):
    //
    Int     calcGoalValue(Int C) const { return -(goal_coeff * C) + goal_offset; }      // convert value in maximize into original
    Int     divFloor(Int a, Int b) const { return ( a<0 ? ((a+1)/b)-1 : a/b ); }     // assume b is positive
    Int     divCeil(Int a, Int b)  const { return ( a<=0 ? a/b : ((a-1)/b)+1 ); }     // assume b is positive
    Int     InvGoalLB(Int C) const { return divCeil(goal_offset - C, goal_coeff); }  // convert value in original ub into LB
    Int     InvGoalUB(Int C) const { return divFloor(goal_offset - C, goal_coeff); } // convert value in original lb into UB
    Int     evalGoal(const Linear& goal) override;
    Int     best_goalvalue;         // Value of goal function for best model found so far.
    Int     last_printed_goalvalue; // Last value printed via "o"; prevents duplicate output when SCIP and SAT find the same value.
    Int     get_best_goalvalue() override {return best_goalvalue; }
    Linear* get_original_goal() override {return original_goal; }
    bool    has_model_for_bestvalue;
    Int     presolve_lb;        // primal bound from SCIP presolver (Int_MIN = not available)
    Int     presolve_ub;        // dual bound from SCIP presolver (Int_MAX = not available)
    double  presolve_scip_gap;  // SCIP gap % from presolver: |primal-dual|/|dual|×100 (-1 = not available)
    int     presolve_nnodes;    // number of nodes explored in SCIP presolver (0 = not run)
    int     num_improved_by_scip;  // number of times LB/UB was found by SCIP
    int     num_improved_by_sat;   // number of times LB/UB was found by SAT

    void    addGoal     (const vec<Lit>& ps, const vec<Int>& Cs) override;

    solverStatus  coding(vec<Lit>& assump_ps) override; // code constrs and send to sat_solver
    solverStatus  coding() override { vec<Lit> empty_assump_ps; return coding(empty_assump_ps); }
    solverStatus  solve(vec<Lit>& assump_ps) override;   // Returns best/first solution found or Int_MAX if UNSAT.
    solverStatus  solve() override { vec<Lit> empty_assump_ps; return solve(empty_assump_ps); }
};

class PbSolver_Counting : public PbSolver {
public:

  PbSolver_Counting(Config* _config, bool _own_config, Solver* _sat_solver, bool _own_sat_solver)
    : PbSolver(_config, _own_config, _sat_solver, _own_sat_solver)
    , number_models(0)
    , pb_solver_status(sst_unknown)
    {}
  PbSolver_Counting()                : PbSolver_Counting(new Config(), true,  NULL, true) {}
  PbSolver_Counting(Config* _config) : PbSolver_Counting(_config,      false, NULL, true) {}
  PbSolver_Counting(Config* _config, Solver* _sat_solver)
    : PbSolver_Counting(_config, false, _sat_solver, false)
    {}

  // Main internal methods:
  ResultT enumerate(bool interestMode, vec<Lit>& assump_ps);
  ResultT enumerateScip(bool interestMode, vec<Lit>& assump_ps);

  // Public variable
  int number_models; // Number of models found
  int get_number_models() override { return number_models; }
private:
  solverStatus        pb_solver_status;  // sst_unknown, sst_sat, sst_unsat, sst_best, sst_all.
public:
  virtual solverStatus        get_pb_solver_status() const override { return pb_solver_status; }
  virtual void                set_pb_solver_status(solverStatus status) override { pb_solver_status = status; }

  solverStatus solve(vec<Lit>& assump_ps) override;
  solverStatus solve() override { vec<Lit> empty_assump_ps; return solve(empty_assump_ps); }

};

//=================================================================================================

#endif

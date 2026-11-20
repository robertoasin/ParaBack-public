#include "Global.h"
#include "Int.h"
#include "PbSolver.h"
#include <stdexcept>

#ifdef USE_SCIP

#include <scip/scip.h>
#include <scip/scipdefplugins.h>
#include <cmath>

static const char* scipStatusStr(SCIP_STATUS s) {
    switch (s) {
        case SCIP_STATUS_UNKNOWN:        return "UNKNOWN";
        case SCIP_STATUS_USERINTERRUPT:  return "USERINTERRUPT";
        case SCIP_STATUS_NODELIMIT:      return "NODELIMIT";
        case SCIP_STATUS_TOTALNODELIMIT: return "TOTALNODELIMIT";
        case SCIP_STATUS_STALLNODELIMIT: return "STALLNODELIMIT";
        case SCIP_STATUS_TIMELIMIT:      return "TIMELIMIT";
        case SCIP_STATUS_MEMLIMIT:       return "MEMLIMIT";
        case SCIP_STATUS_GAPLIMIT:       return "GAPLIMIT";
        case SCIP_STATUS_SOLLIMIT:       return "SOLLIMIT";
        case SCIP_STATUS_BESTSOLLIMIT:   return "BESTSOLLIMIT";
        case SCIP_STATUS_RESTARTLIMIT:   return "RESTARTLIMIT";
        case SCIP_STATUS_OPTIMAL:        return "OPTIMAL";
        case SCIP_STATUS_INFEASIBLE:     return "INFEASIBLE";
        case SCIP_STATUS_UNBOUNDED:      return "UNBOUNDED";
        case SCIP_STATUS_INFORUNBD:      return "INFORUNBD";
        case SCIP_STATUS_TERMINATE:      return "TERMINATE";
        case SCIP_STATUS_PRIMALLIMIT:    return "PRIMALLIMIT";
        case SCIP_STATUS_DUALLIMIT:      return "DUALLIMIT";
        default:                         return "UNKNOWN_STATUS";
    }
}

static SCIP_DECL_MESSAGEINFO(scipMessageInfo) {
    (void)file;
    Report* rep = reinterpret_cast<Report*>(SCIPmessagehdlrGetData(messagehdlr));
    rep->reportf(2, "%s", msg);
}

static SCIP_DECL_MESSAGEWARNING(scipMessageWarning) {
    (void)file;
    Report* rep = reinterpret_cast<Report*>(SCIPmessagehdlrGetData(messagehdlr));
    rep->reportf(2, "%s", msg);
}

static SCIP_DECL_MESSAGEDIALOG(scipMessageDialog) {
    (void)file;
    Report* rep = reinterpret_cast<Report*>(SCIPmessagehdlrGetData(messagehdlr));
    rep->reportf(2, "%s", msg);
}

static SCIP_DECL_EVENTINITSOL(eventInitSolBestSol) {
    SCIPcatchEvent(scip, SCIP_EVENTTYPE_BESTSOLFOUND, eventhdlr, NULL, NULL);
    return SCIP_OKAY;
}

static SCIP_DECL_EVENTEXITSOL(eventExitSolBestSol) {
    SCIPdropEvent(scip, SCIP_EVENTTYPE_BESTSOLFOUND, eventhdlr, NULL, -1);
    return SCIP_OKAY;
}

static SCIP_DECL_EVENTEXEC(eventExecBestSol) {
    PbSolver* pb = (PbSolver*)SCIPeventhdlrGetData(eventhdlr);
    SCIP_SOL* sol = SCIPgetBestSol(scip);
    if (!sol) return SCIP_OKAY;

    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)pb->scip_vars;

    pb->scip_event_fired = true;
    pb->set_pb_solver_status(sst_sat);

    PbSolver_Minimize* min_solver = dynamic_cast<PbSolver_Minimize*>(pb);

    if (!min_solver) {
        // Non-minimize case: always record model
        pb->best_model.growTo(pb->pb_n_vars, false);
        for (int i = 0; i < pb->pb_n_vars; i++) {
            double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
            pb->best_model[i] = (val > 0.5);
        }
    } else if (min_solver->original_goal) {
        // Compute new_goalvalue directly from SCIP solution without touching best_model.
        // The old code unconditionally wrote best_model at lines 71-74 before checking
        // whether the solution actually improves best_goalvalue. When it did not improve,
        // best_model ended up pointing to a worse solution while best_goalvalue kept the
        // better value, creating an inconsistency detected by the final model check.
        Int new_goalvalue = 0;
        const Linear& og = *(min_solver->original_goal);
        for (int i = 0; i < og.size; i++) {
            Lit l = og[i];
            double val = SCIPgetSolVal(scip, sol, (*vars)[var(l)]);
            bool v_true = (val > 0.5);
            if (sign(l) ? !v_true : v_true)
                new_goalvalue += og(i);
        }
        if (min_solver->best_goalvalue == Int_MIN || new_goalvalue < min_solver->best_goalvalue) {
            // Record model only when genuinely improved
            min_solver->best_model.growTo(min_solver->pb_n_vars, false);
            for (int i = 0; i < min_solver->pb_n_vars; i++) {
                double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
                min_solver->best_model[i] = (val > 0.5);
            }

            min_solver->best_goalvalue = new_goalvalue;
            min_solver->last_printed_goalvalue = new_goalvalue;
            min_solver->num_improved_by_scip++;
            char* tmp = toString(new_goalvalue);
            min_solver->report->reportf(2, "in SCIP_DECL_EVENTEXEC ");
            min_solver->report->reportf(1, "found by SCIP\n");
            if (min_solver->config->is_inner_solver) {
                min_solver->pb_solver_inner->report->reportf(2, "o %s\n", tmp);
            } else {
                printf("o %s\n", tmp);
                fflush(stdout);
            }
            xfree(tmp);

            if(min_solver->config->model_check)
              if(!min_solver->model_check()) {
                min_solver->report->reportf(1, "Wrong model\n");
                throw std::runtime_error("Wrong model\n");
              } else min_solver->report->reportf("Model check passed\n");
            if(!min_solver->config->use_cooperative_scip)
              min_solver->set_pb_solver_status(sst_sat);
        }
    } else {
        // min_solver present but no original_goal: record model unconditionally
        pb->best_model.growTo(pb->pb_n_vars, false);
        for (int i = 0; i < pb->pb_n_vars; i++) {
            double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
            pb->best_model[i] = (val > 0.5);
        }
    }
    return SCIP_OKAY;
}

void PbSolver::interruptSolve() {
    // Flag method is abandoned. The signal handler now uses alarm(10) and exits directly.
}

void PbSolver::freeScip() {
    report->reportf(2, "freeScip\n");
    if (scip_env) {
        SCIP* scip = (SCIP*)scip_env;
        if (scip_in_transformed_state) {
            SCIPfreeTransform(scip);
            scip_in_transformed_state = false;
        }
        if (scip_ub_hint_cons) {
            SCIP_CONS* c = (SCIP_CONS*)scip_ub_hint_cons;
            SCIPdelCons(scip, c);
            SCIPreleaseCons(scip, &c);
            scip_ub_hint_cons = nullptr;
        }
        std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;
        if (vars) {
            for (int i = 0; i < vars->size(); i++) {
                SCIPreleaseVar(scip, &(*vars)[i]);
            }
            delete vars;
            scip_vars = nullptr;
        }
        SCIPfree(&scip);
        scip_env = nullptr;
    }
}

void PbSolver::initScipEnv() {
    if (!ok || scip_env) return;

    SCIP* scip = nullptr;
    SCIPcreate(&scip);
    SCIPincludeDefaultPlugins(scip);

    // Register custom message handler to prefix SCIP output with "c "
    SCIP_MESSAGEHDLR* msghdlr = nullptr;
    SCIPmessagehdlrCreate(&msghdlr,
        FALSE,              // bufferedoutput
        nullptr,            // filename (use stdout)
        FALSE,              // quiet
        scipMessageWarning,
        scipMessageDialog,
        scipMessageInfo,
        nullptr,            // messagehdlrfree
        (SCIP_MESSAGEHDLRDATA*)report
    );
    SCIPsetMessagehdlr(scip, msghdlr);
    SCIPmessagehdlrRelease(&msghdlr);

    SCIPcreateProbBasic(scip, "NapsProb");

    // Let NaPS handle SIGINT/SIGTERM, not SCIP
    SCIPsetBoolParam(scip, "misc/catchctrlc", FALSE);

    // Hide SCIP output if verbosity is low
    if (config->verbosity < 2) {
        SCIPsetIntParam(scip, "display/verblevel", 0);
    }

    if (config->scip_no_presolve) {
        SCIPsetIntParam(scip, "presolving/maxrounds", 0);
    }

    scip_env = (void*)scip;

    std::vector<SCIP_VAR*>* vars = new std::vector<SCIP_VAR*>();
    scip_vars = (void*)vars;

    pb_n_vars = nVars();
    for (int i = 0; i < pb_n_vars; i++) {
        lbool asgn = value(Var(i));
        double lb = (asgn == ll_True)  ? 1.0 : 0.0;
        double ub = (asgn == ll_False) ? 0.0 : 1.0;
        SCIP_VAR* v = nullptr;
        char name[256];
        snprintf(name, sizeof(name), "x%d", i);
        SCIPcreateVarBasic(scip, &v, name, lb, ub, 0.0, SCIP_VARTYPE_BINARY);
        SCIPaddVar(scip, v);
        vars->push_back(v);
    }

    SCIPincludeEventhdlr(scip, "naps_bestsol", "notify NaPS of new best solution",
        NULL,                   // SCIP_DECL_EVENTCOPY
        NULL,                   // SCIP_DECL_EVENTFREE
        NULL,                   // SCIP_DECL_EVENTINIT
        NULL,                   // SCIP_DECL_EVENTEXIT
        eventInitSolBestSol,    // SCIP_DECL_EVENTINITSOL
        eventExitSolBestSol,    // SCIP_DECL_EVENTEXITSOL
        NULL,                   // SCIP_DECL_EVENTDELETE
        eventExecBestSol,
        (SCIP_EVENTHDLRDATA*)this);
}

void PbSolver::initScipForCoop() {
    report->reportf(2, "initScipForCoop\n");
    if (!ok) return;
    initScipEnv();
    if (!scip_env) return;

    SCIP* scip = (SCIP*)scip_env;
    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;

    globally_fixed_var.growTo(pb_n_vars, ll_Undef);

    // Sync all current constraints to SCIP.
    // For cooperative mode this is called before convertPbs() clears constrs,
    // so all original problem constraints are captured here.
    int sync_limit = (int)constrs.size();
    // Free transform only when new PB constraints arrive: SCIPaddCons requires PROBLEM stage.
    // Trail-only increases are synced via SCIPchgVarLbGlobal/UbGlobal which are valid in
    // SOLVING stage, so we can skip SCIPfreeTransform and preserve the B&B state.
    bool needs_free_transform = scip_in_transformed_state &&
        (scip_constrs_added < sync_limit);
    if (needs_free_transform) {
        report->reportf(2, "initScipForCoop: freeing transform (new constraints)\n");
        if (SCIPfreeTransform(scip) != SCIP_OKAY)
            throw std::runtime_error("SCIPfreeTransform failed in initScipForCoop");
        config->stats_scip_free_transform_count++;
        scip_in_transformed_state = false;
        scip_last_lb_hint_scip = 0.0;
        scip_last_was_timelimit = false;
    } else if (scip_in_transformed_state && sat_solver->trail_size() > scip_trail_synced) {
        report->reportf(2, "initScipForCoop: syncing trail in SOLVING stage (no free)\n");
    }
    // Reusable buffers to avoid repeated allocation across the constraint loop.
    std::vector<SCIP_VAR*> c_vars;
    std::vector<double>    c_vals;

    for (; scip_constrs_added < sync_limit; scip_constrs_added++) {
        Linear* c = constrs[scip_constrs_added];
        if (c == nullptr) continue;  // deleted by propagate()

        char cname[256];
        snprintf(cname, sizeof(cname), "c%d", scip_constrs_added);

        double lhs = c->lo == Int_MIN ? -SCIPinfinity(scip) : todouble(c->lo);
        double rhs = c->hi == Int_MAX ?  SCIPinfinity(scip) : todouble(c->hi);

        // Build coefficient arrays and adjust lhs/rhs for negated literals
        // before calling SCIPcreateConsBasicLinear, so SCIP 10.x never sees
        // an intermediate state where lhs > rhs.
        c_vars.clear();
        c_vals.clear();
        for (int j = 0; j < c->size; j++) {
            Lit l = (*c)[j];
            Int coef = (*c)(j);
            bool is_neg = sign(l);
            int v = var(l);
            double d_coef = todouble(coef);
            if (is_neg) {
                c_vars.push_back((*vars)[v]);
                c_vals.push_back(-d_coef);
                if (lhs > -SCIPinfinity(scip)) lhs -= d_coef;
                if (rhs <  SCIPinfinity(scip)) rhs -= d_coef;
            } else {
                c_vars.push_back((*vars)[v]);
                c_vals.push_back(d_coef);
            }
        }

        SCIP_CONS* cons = nullptr;
        SCIPcreateConsBasicLinear(scip, &cons, cname,
                                  (int)c_vars.size(), c_vars.data(), c_vals.data(),
                                  lhs, rhs);

        if (c->llt != lit_Undef) {
            printf("s UNSUPPORTED\n");
            report->reportf("WARNING: SCIP linkage cannot currently handle DEFINITION description (lit != lit_Undef).\n");
            throw std::runtime_error("SCIP linkage cannot currently handle DEFINITION description (lit != lit_Undef).\n");
        }

        SCIPaddCons(scip, cons);
        SCIPreleaseCons(scip, &cons);
    }

    // Sync fixed variables from the SAT solver trail to SCIP variable bounds.
    // propagate() simplifies constraints in-place by removing fixed literals and
    // adjusting bounds, and deletes constraints that become trivially satisfied.
    // SCIP must know those variable assignments; otherwise it sees an
    // under-constrained problem and may produce solutions that violate the
    // original constraints.
    int trail_end = sat_solver->trail_size();
    for (int i = scip_trail_synced; i < trail_end; i++) {
        Lit l = sat_solver->trail(i);
        int v = var(l);
        if (v >= pb_n_vars) continue;   // auxiliary variable — no SCIP counterpart
        globally_fixed_var[v] = sign(l) ? ll_False : ll_True;
        SCIP_VAR* sv = (*vars)[v];
        if (sign(l)) {
            SCIPchgVarUbGlobal(scip, sv, 0.0);   // variable fixed to 0
        } else {
            SCIPchgVarLbGlobal(scip, sv, 1.0);   // variable fixed to 1
        }
    }
    scip_trail_synced = trail_end;
}

void PbSolver::syncSatFixedVars() {
    if (!scip_env) return;
    globally_fixed_var.growTo(pb_n_vars, ll_Undef);
    SCIP* scip = (SCIP*)scip_env;
    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;

    int newly_fixed = 0;
    int trail_end = sat_solver->trail_size();
    for (int i = scip_trail_synced; i < trail_end; i++) {
        Lit l = sat_solver->trail(i);
        int v = var(l);
        if (v >= pb_n_vars) continue;   // auxiliary variable — no SCIP counterpart
        if (globally_fixed_var[v] == ll_Undef) {
            globally_fixed_var[v] = sign(l) ? ll_False : ll_True;
            SCIP_VAR* sv = (*vars)[v];
            if (sign(l)) {
                SCIPchgVarUbGlobal(scip, sv, 0.0);
            } else {
                SCIPchgVarLbGlobal(scip, sv, 1.0);
            }
            newly_fixed++;
        }
    }
    scip_trail_synced = trail_end;

    config->stats_sat_fixed_vars += newly_fixed;
    if (newly_fixed > 0 && config->verbosity >= 1)
        report->reportf(1, "c SAT fixed %d new variables\n", newly_fixed);
}

void PbSolver::addScipLinearConstraint(const vec<Lit>& ps, const vec<Int>& Cs, Int lb, Int ub) {
    report->reportf(2, "addScipLinearConstraint\n");
    if (!scip_env) return;
    SCIP* scip = (SCIP*)scip_env;
    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;

    // Constraint addition requires PROBLEM stage. If SCIP is holding B&B state
    // (SOLVED/TRANSFORMED), free it now. At this call site the previous stage's
    // B&B state is no longer needed (the next solveScip will use a different goal).
    if (scip_in_transformed_state) {
        if (SCIPfreeTransform(scip) != SCIP_OKAY)
            throw std::runtime_error("SCIPfreeTransform failed in addScipLinearConstraint");
        config->stats_scip_free_transform_count++;
        scip_in_transformed_state = false;
        scip_last_lb_hint_scip = 0.0;
    }

    static int stage_constr_count = 0;
    char cname[64];
    snprintf(cname, sizeof(cname), "stage_c%d", stage_constr_count++);

    double d_lhs = (lb == Int_MIN) ? -SCIPinfinity(scip) : todouble(lb);
    double d_rhs = (ub == Int_MAX) ?  SCIPinfinity(scip) : todouble(ub);

    std::vector<SCIP_VAR*> c_vars;
    std::vector<double>    c_vals;
    c_vars.reserve(ps.size());
    c_vals.reserve(ps.size());

    for (int j = 0; j < ps.size(); j++) {
        Lit l = ps[j];
        Int coef = Cs[j];
        bool is_neg = sign(l);
        int v = var(l);
        double d_coef = todouble(coef);
        if (is_neg) {
            c_vars.push_back((*vars)[v]);
            c_vals.push_back(-d_coef);
            if (d_lhs > -SCIPinfinity(scip)) d_lhs -= d_coef;
            if (d_rhs <  SCIPinfinity(scip)) d_rhs -= d_coef;
        } else {
            c_vars.push_back((*vars)[v]);
            c_vals.push_back(d_coef);
        }
    }

    SCIP_CONS* cons = nullptr;
    SCIPcreateConsBasicLinear(scip, &cons, cname,
                              (int)c_vars.size(), c_vars.data(), c_vals.data(),
                              d_lhs, d_rhs);
    SCIPaddCons(scip, cons);
    SCIPreleaseCons(scip, &cons);

    // Stage-boundary propagation (-scip-stage-prop option).
    // Run SCIPpresolve with the just-added equality constraint (lb=ub=V_N) to find
    // variables that are logically fixed by primal constraint propagation.
    // Dual reductions are disabled (misc/allowdualreds=FALSE) because they exploit
    // the current objective function (maximize goal_N): variables absent from goal_N
    // would be incorrectly fixed to their lower bounds even though those fixings are
    // not valid for all feasible solutions of goal_N=V_N.
    // The equality constraint remains in SCIP after SCIPfreeTransform because it was
    // added in PROBLEM stage; this is correct for all later stages (see CLAUDE.md).
    if (config->use_cooperative_scip && config->scip_stage_prop) {
        report->reportf(2, "Running SCIP presolve for stage-boundary propagation (rounds=%d)\n", config->scip_stage_prop_rounds);
        int saved_rounds = -1;
        SCIPgetIntParam(scip, "presolving/maxrounds", &saved_rounds);
        SCIPsetIntParam(scip, "presolving/maxrounds", config->scip_stage_prop_rounds);
        double saved_time_limit = -1;
        SCIPgetRealParam(scip, "limits/time", &saved_time_limit);
        SCIPsetRealParam(scip, "limits/time", 1e20);

        // Disable dual reductions so only primal constraint propagation fixes
        // variables.  Parameter names differ across SCIP versions:
        //   SCIP <=7: misc/allowdualreds  (covers all dual reductions)
        //   SCIP  8+: misc/allowstrongdualreds + misc/allowweakdualreds
        // Use SCIPgetBoolParam return value to skip unknown parameters silently.
        const char* dual_param_names[] = {
            "misc/allowdualreds",
            "misc/allowstrongdualreds",
            "misc/allowweakdualreds",
        };
        const int n_dual_params = 3;
        SCIP_Bool dual_saved[3] = {TRUE, TRUE, TRUE};
        bool dual_exists[3] = {false, false, false};
        for (int i = 0; i < n_dual_params; i++) {
            // SCIPgetParam returns NULL silently; SCIPgetBoolParam prints an error
            // for unknown parameters even when we check its return code.
            if (SCIPgetParam(scip, dual_param_names[i]) == nullptr) continue;
            dual_exists[i] = true;
            SCIPgetBoolParam(scip, dual_param_names[i], &dual_saved[i]);
            SCIPsetBoolParam(scip, dual_param_names[i], FALSE);
        }

        SCIP_RETCODE presolve_rc = SCIPpresolve(scip);

        int newly_fixed = 0;
        if (presolve_rc == SCIP_OKAY && SCIPgetStage(scip) == SCIP_STAGE_PRESOLVED) {
            for (int i = 0; i < pb_n_vars; i++) {
                if (globally_fixed_var[i] == ll_Undef) {
                    double glb = SCIPvarGetLbGlobal((*vars)[i]);
                    double gub = SCIPvarGetUbGlobal((*vars)[i]);
                    bool is_fixed = false, fixed_val = false;
                    if      (glb >= 0.5) { is_fixed = true; fixed_val = true; }
                    else if (gub <= 0.5) { is_fixed = true; fixed_val = false; }
                    if (is_fixed) {
                        if (!sat_solver->addUnit(Lit(i, fixed_val ? false : true))) break;
                        globally_fixed_var[i] = fixed_val ? ll_True : ll_False;
                        newly_fixed++;
                    }
                }
            }
        } else if (presolve_rc == SCIP_OKAY &&
                   SCIPgetStatus(scip) == SCIP_STATUS_INFEASIBLE) {
            assert(false);
            report->reportf(1, "Unexpectedly SCIP stage-boundary infeasible\n");
            exit(1);
            sat_solver->addUnit(Lit(0)); sat_solver->addUnit(~Lit(0));
        }

        config->stats_scip_fixed_vars += newly_fixed;
        if (newly_fixed > 0 && config->verbosity >= 1)
            report->reportf(1, "c SCIP stage-boundary propagated %d fixed variables\n", newly_fixed);

        if (SCIPfreeTransform(scip) != SCIP_OKAY)
            throw std::runtime_error("SCIPfreeTransform failed after stage-boundary presolve");
        config->stats_scip_free_transform_count++;
        scip_in_transformed_state = false;
        scip_last_lb_hint_scip = 0.0;

        for (int i = 0; i < n_dual_params; i++) {
            if (dual_exists[i]) SCIPsetBoolParam(scip, dual_param_names[i], dual_saved[i]);
        }
        SCIPsetIntParam(scip, "presolving/maxrounds", saved_rounds);
        SCIPsetRealParam(scip, "limits/time", saved_time_limit);
    }
}

double PbSolver::getScipFeastol() const {
    if (!scip_env) return 0.0;
    return SCIPfeastol((SCIP*)scip_env);
}

void PbSolver::checkScipPrecisionSafety() {
    if (!config->use_cooperative_scip && config->scip_as_presolve <= 0.0) return;
    const double feastol    = getScipFeastol();
    if (feastol <= 0.0) return;
    const double rhs_limit  = 1.0 / feastol;
    const Int coef_limit((double)(1LL << 53));
    const Int zero = 0;

    for (int i = 0; i < constrs.size(); i++) {
        if (!constrs[i]) continue;
        const Linear& c = *constrs[i];
        if (c.lo == Int_MIN && c.hi == Int_MAX) continue;

        // Check A: RHS feasibility tolerance
        auto over_rhs = [&](Int b) {
            double ab = todouble(b); if (ab < 0.0) ab = -ab;
            return ab >= rhs_limit;
        };
        if ((c.lo != Int_MIN && over_rhs(c.lo)) ||
            (c.hi != Int_MAX && over_rhs(c.hi))) {
            report->reportf(1,
                "SCIP precision unsafe (constr %d rhs): disabling presolver and cooperative SCIP\n", i);
            config->scip_as_presolve   = 0.0;
            config->use_cooperative_scip = false;
            return;
        }
        // Check B: coefficient sum (LP activity precision)
        Int coef_sum = 0;
        for (int j = 0; j < c.size; j++) {
            Int ci = c(j);
            coef_sum += ci < zero ? -ci : ci;
            if (coef_sum >= coef_limit) {
                report->reportf(1,
                    "SCIP precision unsafe (constr %d coef sum): disabling presolver and cooperative SCIP\n", i);
                config->scip_as_presolve   = 0.0;
                config->use_cooperative_scip = false;
                return;
            }
        }
    }
}

solverStatus PbSolver::solveScip(vec<Lit>& assump_ps,
                                 double time_limit,
                                 const Linear* coop_goal,
                                 Int lb_hint,
                                 Int* out_lb,
                                 Int* out_dual_ub,
                                 Int ub_hint,
                                 double* out_scip_gap,
                                 int* out_nnodes) {
    report->reportf(2, "solveScip\n");
    if (!ok) {
        set_pb_solver_status(sst_unsat);
        return get_pb_solver_status();
    }

    // Initialize SCIP environment, variables, and sync any new constraints.
    // For cooperative mode, original constraints were already synced via initScipForCoop()
    // called from coding() before convertPbs() cleared constrs, so this is a no-op there.
    initScipForCoop();

    SCIP* scip = (SCIP*)scip_env;
    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;

    // Handle goal if this is a minimizing solver
    PbSolver_Minimize* min_solver = dynamic_cast<PbSolver_Minimize*>(this);

    // Determine whether SCIPfreeTransform can be skipped at the end of this solve.
    // Based on previous call's scip_last_goal: if coop_goal pointer is unchanged,
    // the objective is already set and SCIP's B&B state (tree, cuts, LP) can be continued.
    bool can_skip_free_transform =
        (coop_goal != nullptr) &&
        (scip_last_goal == (void*)coop_goal) &&  // goal set in a previous call
        (assump_ps.size() == 0);                  // no temporary assump constraints

    // If SCIP holds its B&B state but we cannot continue (goal changed or assump non-empty),
    // free the transform now so SCIPchgVarObj/SCIPsetObjsense can run in PROBLEM stage.
    if (scip_in_transformed_state && !can_skip_free_transform) {
        report->reportf(2, "solveScip: freeing transform (scip_in_transformed_state && !can_skip_free_transform)\n");
        if (SCIPfreeTransform(scip) != SCIP_OKAY)
            throw std::runtime_error("SCIPfreeTransform failed before goal/assump change");
        config->stats_scip_free_transform_count++;
        scip_in_transformed_state = false;
        scip_last_lb_hint_scip = 0.0;
    }

    if (coop_goal == nullptr && min_solver && min_solver->original_goal && scip_last_goal != (void*)min_solver->original_goal) {
        SCIPsetObjsense(scip, SCIP_OBJSENSE_MINIMIZE);

        // Reset all obj coefficients to 0 first in case of overwriting
        for (size_t i = 0; i < vars->size(); i++) {
            SCIPchgVarObj(scip, (*vars)[i], 0.0);
        }

        Linear* goal = min_solver->original_goal;
        for (int j = 0; j < goal->size; j++) {
            Lit l = (*goal)[j];
            Int coef = (*goal)(j);
            bool is_neg = sign(l);
            int v = var(l);

            double d_coef = todouble(coef);
            double obj_coef = is_neg ? -d_coef : d_coef;

            double curr_obj = SCIPvarGetObj((*vars)[v]);
            SCIPchgVarObj(scip, (*vars)[v], curr_obj + obj_coef);
        }
        scip_last_goal = (void*)min_solver->original_goal;
    } else {
      // Cooperative mode: maximize the internal NaPS goal 'goalEXP' (same units as LB/UB_goalvalue).
      // Here each stage in NaPS maximization has a goalEXP to maximize.
      // In NaPS, maximizing each stage's goalEXP step by step induces minimum original goal value.

      // Negated literal ~xi with coef c contributes c*(1-xi) = c - c*xi to the goalEXP,
      // so the SCIP_obj = NaPS_obj - scip_coop_offset, where
      // scip_coop_offset = sum of coef for all negated literals.
      if (coop_goal != nullptr && scip_last_goal != (void*)coop_goal) {
        SCIPsetObjsense(scip, SCIP_OBJSENSE_MAXIMIZE);
        for (size_t i = 0; i < vars->size(); i++)
          SCIPchgVarObj(scip, (*vars)[i], 0.0);
        scip_coop_offset = 0.0;
        for (int j = 0; j < coop_goal->size; j++) {
            Lit l = (*coop_goal)[j];
            Int coef = (*coop_goal)(j);
            bool is_neg = sign(l);
            int v = var(l);
            double d_coef = todouble(coef);
            double obj_coef = is_neg ? -d_coef : d_coef;
            if (is_neg) scip_coop_offset += d_coef;
            double curr_obj = SCIPvarGetObj((*vars)[v]);
            SCIPchgVarObj(scip, (*vars)[v], curr_obj + obj_coef);
        }
        scip_last_goal = (void*)coop_goal;
        report->reportf(2, "Cooperative mode: scip_coop_offset=%f\n", scip_coop_offset);
      }
    }

    // Apply assumptions as temporary constraints
    std::vector<SCIP_CONS*> assump_constrs;
    for (int i = 0; i < assump_ps.size(); i++) {
        Lit l = assump_ps[i];
        bool is_neg = sign(l);
        int v = var(l);

        char cname[256];
        snprintf(cname, sizeof(cname), "assump%d", i);

        double bound = is_neg ? 0.0 : 1.0;
        SCIP_CONS* cons = nullptr;
        SCIPcreateConsBasicLinear(scip, &cons, cname, 0, nullptr, nullptr, bound, bound);
        SCIPaddCoefLinear(scip, cons, (*vars)[v], 1.0);
        SCIPaddCons(scip, cons);
        assump_constrs.push_back(cons);
    }

    // Time limit and cooperative lower-bound cutoff
    if (time_limit > 0.0)
        SCIPsetRealParam(scip, "limits/time", time_limit);
    else
        SCIPsetRealParam(scip, "limits/time", 1e20);  // effectively no limit
    if(config->verbosity >= 2){
        char* lb_hint_str = toString(lb_hint); char* ub_hint_str = toString(ub_hint);
        report->reportf(2, "Coop Mode: Using SCIP obj units for LB/UB: %s, %s\n", lb_hint_str, ub_hint_str);
        xfree(lb_hint_str); xfree(ub_hint_str);
    }
    // SCIPsetObjlimit cannot relax the cutoff when lb decreased,
    // and SCIP 10.0.2 dropped SOLVED from its allowed-stage list (was OK in 9.2.2).
    // must_free conditions that are independent of lb_hint (SOLVED stage, timelimit) must be
    // checked even when lb_hint == Int_MIN; only the LB-relaxation check requires lb_hint != Int_MIN.
    if (coop_goal != nullptr && can_skip_free_transform && scip_in_transformed_state) {
        bool must_free = SCIPgetStage(scip) == SCIP_STAGE_SOLVED ||
                         (scip_last_was_timelimit && (config->scip_eager_free_transform || scip_last_nnodes > 0));
        if (!must_free && lb_hint != Int_MIN) {
            double new_lb_scip_chk = todouble(lb_hint) - scip_coop_offset;
            must_free = new_lb_scip_chk < scip_last_lb_hint_scip;
        }
        if (must_free) {
            if(scip_last_nnodes > 0){
              report->reportf(2, "SCIPfreeTransform due to timeout and having shadow nodes\n");
            }
            report->reportf(2, "forcing SCIPfreeTransform before solve\n");
            if (SCIPfreeTransform(scip) != SCIP_OKAY)
                throw std::runtime_error("SCIPfreeTransform failed before SCIPsetObjlimit");
            config->stats_scip_free_transform_count++;
            scip_in_transformed_state = false;
            can_skip_free_transform = false;
            scip_last_lb_hint_scip = 0.0;
            scip_last_was_timelimit = false;
            scip_last_nnodes = 1;
        }
    }
    if (coop_goal != nullptr && lb_hint != Int_MIN) {
        // Convert lb_hint from NaPS internal to SCIP obj units: SCIP_obj = NaPS_internal - scip_coop_offset
        double new_lb_scip = todouble(lb_hint) - scip_coop_offset;
        SCIPsetObjlimit(scip, new_lb_scip);
        scip_last_lb_hint_scip = new_lb_scip;
    }

    // Cooperative UB hint: goalEXP <= ub_hint  =>  SCIP_obj <= ub_hint - scip_coop_offset
    // Managed as a persistent constraint (scip_ub_hint_cons) so that when SCIP retains
    // its B&B state across calls, only the RHS is updated via SCIPchgRhsLinear.
    if (coop_goal != nullptr && ub_hint != Int_MAX) {
        double scip_ub = todouble(ub_hint) - scip_coop_offset;
        if (!can_skip_free_transform) {
            // Non-skip path: recreate the persistent UB hint constraint from scratch.
            if (scip_ub_hint_cons != nullptr) {
                SCIP_CONS* c = (SCIP_CONS*)scip_ub_hint_cons;
                SCIPdelCons(scip, c);
                SCIPreleaseCons(scip, &c);
                scip_ub_hint_cons = nullptr;
            }
            SCIP_CONS* cons = nullptr;
            SCIPcreateConsBasicLinear(scip, &cons, "coop_ub_hint",
                                      0, nullptr, nullptr, -SCIPinfinity(scip), scip_ub);
            for (int j = 0; j < coop_goal->size; j++) {
                Lit l = (*coop_goal)[j];
                bool is_neg = sign(l);
                int v = var(l);
                double d_coef = todouble((*coop_goal)(j));
                SCIPaddCoefLinear(scip, cons, (*vars)[v], is_neg ? -d_coef : d_coef);
            }
            SCIPaddCons(scip, cons);
            scip_ub_hint_cons = (void*)cons;  // held without SCIPreleaseCons (persistent)
        } else {
            // Skip path: only update the RHS; coefficients unchanged (same coop_goal pointer).
            SCIP_CONS* c = (SCIP_CONS*)scip_ub_hint_cons;
            if (c != nullptr)
                SCIPchgRhsLinear(scip, c, scip_ub);
        }
    } else if (scip_ub_hint_cons != nullptr) {
        // ub_hint no longer needed; drop the persistent constraint.
        SCIP_CONS* c = (SCIP_CONS*)scip_ub_hint_cons;
        SCIPdelCons(scip, c);
        SCIPreleaseCons(scip, &c);
        scip_ub_hint_cons = nullptr;
    }

    // Solve-time propagation (-scip-solve-prop option).
    // Run SCIPpresolve with dual reductions disabled after ub_hint constraint is in place,
    // so propagation can exploit both the stage equality constraint (goal = V_N) and the
    // ub_hint constraint (coop_goal <= ub_hint). Only valid when can_skip_free_transform=false
    // (SCIP is in PROBLEM stage; B&B state is not preserved in this path anyway).
    if (config->use_cooperative_scip && config->scip_solve_prop &&
        !can_skip_free_transform && coop_goal != nullptr) {
        report->reportf(2, "Running SCIP presolve for solve-time propagation (rounds=%d)\n", config->scip_solve_prop_rounds);
        int saved_rounds = -1;
        SCIPgetIntParam(scip, "presolving/maxrounds", &saved_rounds);
        SCIPsetIntParam(scip, "presolving/maxrounds", config->scip_solve_prop_rounds);
        double saved_time_limit = -1;
        SCIPgetRealParam(scip, "limits/time", &saved_time_limit);
        SCIPsetRealParam(scip, "limits/time", 1e20);

        const char* sp_dual_param_names[] = {
            "misc/allowdualreds",
            "misc/allowstrongdualreds",
            "misc/allowweakdualreds",
        };
        const int sp_n_dual_params = 3;
        SCIP_Bool sp_dual_saved[3] = {TRUE, TRUE, TRUE};
        bool sp_dual_exists[3] = {false, false, false};
        for (int i = 0; i < sp_n_dual_params; i++) {
            if (SCIPgetParam(scip, sp_dual_param_names[i]) == nullptr) continue;
            sp_dual_exists[i] = true;
            SCIPgetBoolParam(scip, sp_dual_param_names[i], &sp_dual_saved[i]);
            SCIPsetBoolParam(scip, sp_dual_param_names[i], FALSE);
        }

        SCIP_RETCODE sp_presolve_rc = SCIPpresolve(scip);

        int sp_newly_fixed = 0;
        if (sp_presolve_rc == SCIP_OKAY && SCIPgetStage(scip) == SCIP_STAGE_PRESOLVED) {
            for (int i = 0; i < pb_n_vars; i++) {
                if (globally_fixed_var[i] == ll_Undef) {
                    double glb = SCIPvarGetLbGlobal((*vars)[i]);
                    double gub = SCIPvarGetUbGlobal((*vars)[i]);
                    bool is_fixed = false, fixed_val = false;
                    if      (glb >= 0.5) { is_fixed = true; fixed_val = true; }
                    else if (gub <= 0.5) { is_fixed = true; fixed_val = false; }
                    if (is_fixed) {
                        if (!sat_solver->addUnit(Lit(i, fixed_val ? false : true))) break;
                        globally_fixed_var[i] = fixed_val ? ll_True : ll_False;
                        sp_newly_fixed++;
                    }
                }
            }
        } else if (sp_presolve_rc == SCIP_OKAY &&
                   SCIPgetStatus(scip) == SCIP_STATUS_INFEASIBLE) {
            report->reportf(1, "SCIP solve-time presolve found infeasibility\n");
            sat_solver->addUnit(Lit(0)); sat_solver->addUnit(~Lit(0));
        }

        config->stats_scip_fixed_vars += sp_newly_fixed;
        if (sp_newly_fixed > 0 && config->verbosity >= 1)
            report->reportf(1, "c SCIP solve-time propagated %d fixed variables\n", sp_newly_fixed);

        // Return to PROBLEM stage for SCIPsolve below.
        // ub_hint and equality constraints survive SCIPfreeTransform (PROBLEM stage constraints).
        if (SCIPfreeTransform(scip) != SCIP_OKAY)
            throw std::runtime_error("SCIPfreeTransform failed after solve-time presolve");
        config->stats_scip_free_transform_count++;
        scip_in_transformed_state = false;
        scip_last_lb_hint_scip = 0.0;

        for (int i = 0; i < sp_n_dual_params; i++) {
            if (sp_dual_exists[i]) SCIPsetBoolParam(scip, sp_dual_param_names[i], sp_dual_saved[i]);
        }
        SCIPsetIntParam(scip, "presolving/maxrounds", saved_rounds);
        SCIPsetRealParam(scip, "limits/time", saved_time_limit);
    }

    // Solve
    double scip_start_time = cpuTime();
    scip_event_fired = false;
    SCIPsolve(scip);
    config->stats_scip_time += cpuTime() - scip_start_time;

    SCIP_STATUS status = SCIPgetStatus(scip);
    report->reportf(2, "SCIP status: %d (%s)\n", (int)status, scipStatusStr(status));
    solverStatus res = sst_unknown;

    SCIP_SOL* final_sol = SCIPgetBestSol(scip);
    if (status == SCIP_STATUS_OPTIMAL) {
        assert(final_sol);
        if(!final_sol) {
            report->reportf(1, "Unexpectedly SCIPgetStatus==SCIP_STATUS_OPTIMAL && SCIPgetBestSol==false\n");
            exit(1);
        }
        if (!scip_event_fired) { // Presolver path: eventExecBestSol was never called, so populate best_model and print "o" here.
            best_model.growTo(pb_n_vars, false);
            for (int i = 0; i < pb_n_vars; i++) {
                double val = SCIPgetSolVal(scip, final_sol, (*vars)[i]);
                best_model[i] = (val > 0.5);
            }
            if (min_solver && min_solver->original_goal) {
                Int new_gv = min_solver->evalGoal(*(min_solver->original_goal));
                min_solver->best_goalvalue = new_gv;
                if (new_gv < min_solver->last_printed_goalvalue) {
                    min_solver->last_printed_goalvalue = new_gv;
                    char* tmp = toString(new_gv);
                    // min_solver->found_by_scip++;
                    min_solver->report->reportf(1, "found by presolver\n");
                    if (config->is_inner_solver) {
                        report->reportf(2, "o %s\n", tmp);
                    } else {
                        printf("o %s\n", tmp);
                        fflush(stdout);
                    }
                    xfree(tmp);
                }
            }
        }
        res = min_solver ? sst_best : sst_sat;
    } else if ((status == SCIP_STATUS_INFEASIBLE || status == SCIP_STATUS_INFORUNBD) && !interrupted) {
        // SCIP proved no solution strictly better than lb_hint exists → lb_hint is optimal.
        // In cooperative mode this is always an optimality proof (lb_hint != Int_MIN is guaranteed).
        // Check this before final_sol: a solution found before the proof does not change the conclusion.
        res = sst_unsat;
    } else if (final_sol != nullptr && best_model.size() > 0 && !(interrupted && config->is_inner_solver)) {
        // Solution exists and event handler already populated best_model.
        // If best_model is empty the solution was found during SCIP preprocessing
        // (before SCIP_EVENTTYPE_BESTSOLFOUND was registered) and "o" was never printed,
        // so we treat it as unknown rather than claiming a solution we never reported.
        res = sst_sat;
    } else if (status == SCIP_STATUS_USERINTERRUPT || status == SCIP_STATUS_TERMINATE || interrupted) {
        res = sst_unknown;
    } else {
        res = sst_unknown;
    }

    // Extract SCIP dual bound as cooperative UB.
    // SCIP_dual is an upper bound on SCIP_obj = NaPS_internal - scip_coop_offset,
    // so NaPS_UB = SCIP_dual + scip_coop_offset.
    if (out_lb != nullptr && coop_goal != nullptr && (status == SCIP_STATUS_OPTIMAL || status == SCIP_STATUS_TIMELIMIT)) {
        double primal = SCIPgetPrimalbound(scip);
        if (!SCIPisInfinity(scip, primal) && !SCIPisInfinity(scip, -primal))
            *out_lb = Int(primal + scip_coop_offset -0.3);
        else if (lb_hint != Int_MIN)
            *out_lb = lb_hint;
    }

    if (out_dual_ub != nullptr && coop_goal != nullptr) {
        if ((status == SCIP_STATUS_INFEASIBLE || status == SCIP_STATUS_INFORUNBD) && lb_hint != Int_MIN) {
            // SCIP proved no solution achieves NaPS_internal > lb_hint: dual bound = lb_hint
            *out_dual_ub = ub_hint;
        } else {
            double dual = SCIPgetDualbound(scip);
            if (!SCIPisInfinity(scip, dual) && !SCIPisInfinity(scip, -dual)) {
              // if(config->verbosity >= 2){
              //   report->reportf(2, "Dual bound in double: %f\n", dual + scip_coop_offset);
              //   char* tmp = toString(Int(dual + scip_coop_offset));  // Int(double) converts nearest integer.
              //   report->reportf(2, "Dual bound in Int: %s\n", tmp);
              //   xfree(tmp);
              // }
              *out_dual_ub = Int(dual + scip_coop_offset + 0.3);
              // if(min_solver && *out_dual_ub < ub_hint) {
              //   min_solver->found_by_scip++;
              // }
            }
        }
    }

    if (out_scip_gap != nullptr && coop_goal != nullptr) {
        double primal = SCIPgetPrimalbound(scip);
        double dual   = SCIPgetDualbound(scip);
        if (!SCIPisInfinity(scip, primal) && !SCIPisInfinity(scip, -primal) &&
            !SCIPisInfinity(scip, dual)   && !SCIPisInfinity(scip, -dual)   &&
            dual != 0.0)
            *out_scip_gap = fabs(primal - dual) / fabs(dual) * 100.0;
    }

    // Conditionally free the transformed problem space.
    // When coop_goal is unchanged and no constraints were added, skip SCIPfreeTransform so
    // SCIP can continue its B&B exploration (tree, cuts, LP) from the current state next call.
    if (can_skip_free_transform) {
        scip_in_transformed_state = true;
        scip_last_was_timelimit = (status == SCIP_STATUS_TIMELIMIT);
        scip_last_nnodes = SCIPgetNNodes(scip);
        if (out_nnodes) *out_nnodes = (int)scip_last_nnodes;
        // report->reportf(2, "Skipping SCIPfreeTransform: SCIP retains B&B state for next call\n");
    } else {
        if (out_nnodes) *out_nnodes = (int)SCIPgetNNodes(scip);
        report->reportf(2, "solveScip: freeing transform (!can_skip_free_transform)\n");
        if (SCIPfreeTransform(scip) != SCIP_OKAY)
            throw std::runtime_error("Failed to free SCIP transformed problem space");
        config->stats_scip_free_transform_count++;
        scip_in_transformed_state = false;
        // After SCIPfreeTransform, SCIP has no active objective cutoff.
        // Use -infinity sentinel so any subsequent lb_hint won't falsely trigger
        // the "LB relaxed" must_free check (new_lb_scip < scip_last_lb_hint_scip).
        scip_last_lb_hint_scip = -1e300;
        scip_last_was_timelimit = false;
        scip_last_nnodes = 1;
    }

    // Remove assumption constraints so they are not carried over
    for (int i = 0; i < assump_constrs.size(); i++) {
        SCIPdelCons(scip, assump_constrs[i]);
        SCIPreleaseCons(scip, &assump_constrs[i]);
    }
    // scip_ub_hint_cons is persistent across calls; released only in freeScip().

    return res;
}

ResultT PbSolver_Counting::enumerateScip(bool interestMode, vec<Lit>& assump_ps) {
    initScipForCoop();
    if (!scip_env) return rt_unknown;

    SCIP* scip = (SCIP*)scip_env;
    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;

    // No optimization objective — solve as feasibility problem
    SCIPsetObjsense(scip, SCIP_OBJSENSE_MINIMIZE);
    for (size_t i = 0; i < vars->size(); i++)
        SCIPchgVarObj(scip, (*vars)[i], 0.0);

    int enum_vars = (interestMode && pb_i_vars >= 0) ? pb_i_vars : pb_n_vars;
    ResultT result = rt_unknown;

    auto interrupted_result = [&]() { return number_models > 0 ? rt_sat : rt_unknown; };

    while (true) {
        if (interrupted) { result = interrupted_result(); break; }

        scip_event_fired = false;
        SCIP_RETCODE rc = SCIPsolve(scip);
        if (rc != SCIP_OKAY) { result = interrupted_result(); break; }

        SCIP_STATUS status = SCIPgetStatus(scip);

        if (status == SCIP_STATUS_INFEASIBLE || status == SCIP_STATUS_INFORUNBD) {
            result = rt_all;
            break;
        } else if (status == SCIP_STATUS_USERINTERRUPT ||
                   status == SCIP_STATUS_TERMINATE || interrupted) {
            result = interrupted_result();
            break;
        }

        SCIP_SOL* sol = SCIPgetBestSol(scip);
        if (!sol) { result = interrupted_result(); break; }

        // Record model
        best_model.growTo(pb_n_vars, false);
        for (int i = 0; i < pb_n_vars; i++) {
            double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
            best_model[i] = (val > 0.5);
        }
        set_pb_solver_status(sst_sat);
        number_models++;

        // Output model (same format as enumerate())
        report->reportf("MODEL# %d:", number_models);
        int col = 0;
        for (int x = 0; x < enum_vars; x++) {
            if (config->model_out && (!config->positive_mode || best_model[x])) {
                if (col % 2000 >= 1999) printf("\nv");
                col++;
                report->reportf(" %s%s", best_model[x] ? "" : "-", index2name[x]);
            }
        }
        report->reportf("\n");

        if (interrupted) { result = interrupted_result(); break; }

        // Add ban constraint: Σ (¬model_literal_i) ≥ 1
        vec<Lit> ban_ps;
        vec<Int> ban_Cs;
        for (int i = 0; i < enum_vars; i++) {
            ban_ps.push(Lit(i, best_model[i]));  // true→¬x_i, false→x_i
            ban_Cs.push(Int(1));
        }
        SCIPfreeTransform(scip);  // maybe omitted
        config->stats_scip_free_transform_count++;
        addScipLinearConstraint(ban_ps, ban_Cs, Int(1), Int_MAX);
    }

    return result;
}

#else // USE_SCIP

void PbSolver::interruptSolve() {
    interrupted = true;
    if (sat_solver) sat_solver->interrupt();
    if (pb_solver_inner) pb_solver_inner->interruptSolve();
}

void PbSolver::freeScip() {
}

void PbSolver::addScipLinearConstraint(const vec<Lit>&, const vec<Int>&, Int, Int) {
}

void PbSolver::checkScipPrecisionSafety() {
}

solverStatus PbSolver::solveScip(vec<Lit>& assump_ps, double, const Linear*, Int, Int*, Int*, Int, double*, int*) {
    report->reportf(0,"ERROR: SCIP solver was disabled at compile time.\n");
    throw std::runtime_error("SCIP solver was disabled at compile time.");
    return sst_unknown; // Unreachable
}

ResultT PbSolver_Counting::enumerateScip(bool, vec<Lit>&) {
    report->reportf(0,"ERROR: SCIP solver was disabled at compile time.\n");
    throw std::runtime_error("SCIP solver was disabled at compile time.");
}

#endif // USE_SCIP

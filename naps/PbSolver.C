/**************************************************************************************
  Copyright (c) 2005-2010, Niklas Een, Niklas Sorensson

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
  associated documentation files (the "Software"), to deal in the Software without restriction,
  including without limitaion the rights to use, copy, modify, merge, publish, distribute,
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

//#include "Config.h"
//#include "Global.h"
//#include "MiniSat.h"
#include "Config.h"
#include <stdexcept>
#include <signal.h>
#include "Debug.h"
#include "Int.h"
#include "PbSolver.h"
#include "Solver.h"
#include "Sort.h"
//#include "Main.h"

//extern int verbosity;
int n_defequivs=0;
int n_goalasps=0;
int n_goal_constraints=0;
int n_relaxing_constraints=0;

//=================================================================================================
// Interface required by parser:

static Int gcd(Int small, Int big) {
    while (small != 0) {
        Int t = big % small;
        big = small;
        small = t;
    }
    return big;
}

static Int gcdg(Int x, Int y) {
  return (x <= y ? gcd(x,y) : gcd(y,x) ); }

void PbSolver::allocateVars(int satVar)
{
    char        buf[30];
    static int n_reservVar = 0;

    int x = index2name.size();
    assert(x == sat_solver->nVars());
    for(int i = x-1; i < satVar; i++) {
        sprintf(buf, "@reserv%d", n_reservVar++);
        getVar(buf);
    }
    assert(index2name.size() == sat_solver->nVars());
}

void PbSolver::alignVar(cchar* name, int satVar)
{
    allocateVars(satVar);
    index2name[satVar] = xstrdup(name);
    name2index.set(index2name[satVar], satVar);
    if (config->verbosity >= 2)
        report->reportf("variable: %s is assigned to sat-variable: %d\n", name, satVar+1);
}

int PbSolver::getVar(cchar* name)
{
  int ret;
  if (!name2index.peek(name, ret)){
    // Create new variable:
    // Var x = index2name.size();
    Var x = sat_solver->newVar(var_dec_mode);        // (reserve one SAT variable for each PB variable)
    index2name.growTo(x+1, NULL);
    index2name[x] = xstrdup(name);
    named_vars.push(x);
    n_occurs.growTo((x+1)*2, 0);
    // assert(index2name.size()*2 == n_occurs.size());
    //assigns   .push(toInt(ll_Undef));
    //int n =
                                                     // assert(x == sat_solver->nVars()-1);
    // assert(x == index2name.size()-1);
    ret = name2index.set(index2name[x], x);
#ifdef DEBUG
    //	report->reportf(2, "1: NewVar: %d(%d)%c\n", n, ret, var_dec_mode?'d':'-');
#endif
  }
  return ret;
}


void PbSolver::freeConstrs()
{
  for (int i = 0; i < constrs.size(); i++)
    if (constrs[i] != NULL) constrs[i]->~Linear();
  constrs.clear(true);

  // Constraints dropped by propagation/merging since the last 'convertPbs()'.
  for (int i = 0; i < constrs_deleted.size(); i++)
    constrs_deleted[i]->~Linear();
  constrs_deleted.clear(true);

  // With 'model_check' on, 'convertPbs()' hands the 'Linear's over to 'constrs_bk' and keeps
  // 'mem' alive for them rather than destructing them, so dispose of them here.  None of the
  // three vectors refer to the same 'Linear': 'convertPbs()' only copies the non-NULL entries
  // into 'constrs_bk' and clears 'constrs' right afterwards.
  for (int i = 0; i < constrs_bk.size(); i++)
    if (constrs_bk[i] != NULL) constrs_bk[i]->~Linear();
  constrs_bk.clear(true);

  mem.freeAll();   // 'StackAlloc' has no destructor of its own
}


void PbSolver::freeVarNames()
{
  for (int i = 0; i < index2name.size(); i++)
    xfree((char*)index2name[i]);   // 'xstrdup()'ed in 'getVar()'
  index2name.clear(true);
  named_vars.clear(true);
}


void PbSolver::allocConstrs(int n_vars, int n_constrs)
{
  declared_n_vars    = n_vars;
  declared_n_constrs = n_constrs;
}

void PbSolver::addInterestVars(int num)
{
  if(pb_i_vars == -1)
    pb_i_vars = num; //report->reportf("pb_i_vars=%d\n",pb_i_vars);
}

// Given a minimization goal a0*L0 + ... + an*Ln ( <= C ) with a goal value C.
// We will transform this to an equivalent maximization goal with:
//   -a0*(not L0) + -a1*(not L1) + ... + -an*(not Ln) ( >= -C ).
// After that we transform the co-efficients to be positive.
// E.g. 2x - 5y <= 3
//     -2x + 5y >= -3
//     2~x + 5y >= -3 + 2

// -2 for <, -1 for <=, 0 for ==, 1 for >=, 2 for >.
int revertInequality(int in)
{
  switch (in) {
    case -2: return  1;
    case -1: return  2;
    case  1: return -2;
    case  2: return -1;
  }
  return in;
}

// Normalized constraints are in forms of
//   a0*L0 + a1*L1 + ... + an*Ln >= k
// where a0, a1, ... , an are positive integers in increasing order.
bool PbSolver::addConstr(bool normalized, const vec<Lit>& ps, const vec<Int>& Cs, Int rhs, int ineq, Int rhs2, int ineq2, Lit llt, Lit rlt)
{

#ifdef DEBUG
  if(normalized) {
    for(int i=0; i<Cs.size(); i++) assert(Cs[i] > 0);         // check sign
    for(int i=1; i<Cs.size(); i++) assert(Cs[i] >= Cs[i-1]);  // check increasing order
    assert(ineq == 1);
  }
#endif

  if(ineq == 0) {
    assert(rhs2==Int_MAX);
    ineq = -1; ineq2 = 1;
    rhs2 = rhs;
  }

  if(rlt != lit_Undef) {
    assert( !normalized );
    if(rhs2 == Int_MAX){
      report->reportf(3, "standard form\n");
      addConstr_(false, ps, Cs, rhs, revertInequality(ineq), Int_MAX, 0, ~rlt);
    } else {   // band form
      assert( ineq < 0 && ineq2 > 0 );  // < or <= && >= or >

      // need to introduce ~rlt => d1 \vee d2, ie. {rlt,d1,d2}, and
      // put d1 => ~(ax ineq rhs) and d2 => ~(ax ineq2 rhs2)
      char        buf[30];
      sprintf(buf, "@defequiv%d", n_defequivs++);
      Lit d1 = Lit(getVar(buf));
      sprintf(buf, "@defequiv%d", n_defequivs++);
      Lit d2 = Lit(getVar(buf));
      addConstr_(false, ps, Cs, rhs, revertInequality(ineq), Int_MAX, 0, d1);
      addConstr_(false, ps, Cs, rhs2, revertInequality(ineq2), Int_MAX, 0, d2);
      vec<Lit> ban;
      ban.push(rlt); ban.push(d1); ban.push(d2);
      sat_solver->addClause(ban);
    }
  }

  if(rlt == lit_Undef || llt != lit_Undef)
    addConstr_(normalized, ps, Cs, rhs, ineq, rhs2, ineq2, llt);
  return ok;
}

bool PbSolver::addConstr_(bool normalized, const vec<Lit>& ps, const vec<Int>& Cs, Int rhs, int ineq, Int rhs2, int ineq2, Lit llt)
{


#ifdef DEBUG
  { static cchar* ineq_name[5] = { "<", "<=" ,"==", ">=", ">" };
    report->reportf(3, "addConstr_: ");
    if(llt!=lit_Undef) {dump(config, 3, &index2name, llt); report->reportf(3, " =>, "); }
    //      if(rlt!=lit_Undef) {dump(config, 3, rlt); report->reportf(3, " <=, "); }
    dump(config, 3, &index2name, ps, Cs);
    report->reportf(3, " %s ", ineq_name[ineq+2]);
    dump(config, 3, rhs);
    report->reportf(3, ", %s ", ineq_name[ineq2+2]);
    dump(config, 3, rhs2);
    report->reportf(3, "\n"); }
#endif

  vec<Lit>    norm_ps;
  vec<Int>    norm_Cs;
  Int         norm_rhs;
  Int         norm_rhs2;
  Lit         norm_llt;
  //  Lit         norm_rlt;
  //  char        buf[20];

#define Copy    do{ norm_ps.clear(); norm_Cs.clear(); for (int i = 0; i < ps.size(); i++) norm_ps.push(ps[i]), norm_Cs.push( Cs[i]); norm_rhs =  rhs; norm_rhs2 = rhs2; norm_llt = llt; }while(0)
#define CopyInv do{ norm_ps.clear(); norm_Cs.clear(); for (int i = 0; i < ps.size(); i++) norm_ps.push(ps[i]), norm_Cs.push(-Cs[i]); norm_rhs = -rhs; norm_rhs2 = -rhs2; norm_llt = llt; }while(0)
  // non-normalize ORIGINAL
  if (ineq == 0){  // ==
    assert(rhs2==Int_MAX);
    assert( !normalized );

    //    if(rlt==lit_Undef) {
    Copy;
    if (normalizePb(false, norm_ps, norm_Cs, norm_rhs, norm_llt))
      //	report->reportf("storePB11:"),
      storePb(norm_ps, norm_Cs, norm_rhs, Int_MAX, norm_llt);

    CopyInv;
    if (normalizePb(false, norm_ps, norm_Cs, norm_rhs, norm_llt))
      //	report->reportf("storePB12:"),
      storePb(norm_ps, norm_Cs, norm_rhs, Int_MAX, norm_llt);

  }else{ // case that ineq is not =
    if (rhs2 == Int_MAX) {
      if (ineq > 0) { // >= or >
        Copy;
      }else{          // <= or <
        assert(!normalized);
        CopyInv;    // multiplies -1 to the in-equation.
        ineq = -ineq;
      }
      assert(ineq == 1 || ineq == 2 );
      if (ineq == 2) { // >
        assert(!normalized);
        ++norm_rhs;
      }
      if (normalizePb(normalized, norm_ps, norm_Cs, norm_rhs, llt)) {    // normalized is only considered in this case
                                                                         //report->reportf("storePB16: "), dump(config, norm_ps, norm_Cs);
        storePb(norm_ps, norm_Cs, norm_rhs, Int_MAX, llt);
      }
    } else {
      if( ineq > 0 && ineq2 < 0 ) {
        int tmp_ineq = ineq;
        ineq = ineq2; ineq2 = tmp_ineq;
        Int tmp_rhs = norm_rhs;
        norm_rhs = norm_rhs2; norm_rhs2 = tmp_rhs;
      }
      assert( ineq < 0 && ineq2 > 0 );  // < or <= && >= or >
      assert( !normalized );
      if (ineq == -2)  // <
        --rhs;
      if (ineq2 == 2)  // >
        ++rhs2;

      //      if(rlt==lit_Undef) {
      CopyInv;
      if (normalizePb(false, norm_ps, norm_Cs, norm_rhs, norm_llt))
        //	  report->reportf("storePB17:"),
        storePb(norm_ps, norm_Cs, norm_rhs, Int_MAX, norm_llt);

      Copy;
      if (normalizePb(false, norm_ps, norm_Cs, norm_rhs2, norm_llt))
        //	  report->reportf("storePB18:"),
        storePb(norm_ps, norm_Cs, norm_rhs2, Int_MAX, norm_llt);

    }
  }
  return ok;
}



// Normalize a PB constraint to only positive constants. Depends on:
//
//   bool    ok            -- Will be set to FALSE if constraint is unsatisfiable.
//   lbool   value(Lit)    -- Returns the value of a literal (however, it is sound to always return 'll_Undef', but produces less efficient results)
//   bool    addUnit(Lit)  -- Enqueue unit fact for propagation (returns FALSE if conflict detected).
//
// The two vectors 'ps' and 'Cs' (which are modififed by this method) should be interpreted as:
//
//   'p[0]*C[0] + p[1]*C[1] + ... + p[N-1]*C[N-1] >= C[N]'
//
// The method returns TRUE if constraint was normalized, FALSE if the constraint was already
// satisfied or determined contradictory. The vectors 'ps' and 'Cs' should ONLY be used if
// TRUE is returned.
//
// Pure mathematical normalization without any SAT-solver interaction.
// Used in SCIP mode. Performs: sign normalization, x/~x grouping, sorting,
// trivially true/false detection, one-pass GCD simplification, and coefficient trimming.
// Returns true if the constraint should be stored, false if it can be discarded.
bool PbSolver::normalizePb_SCIP(bool normalized, vec<Lit>& ps, vec<Int>& Cs, Int& C, Lit llt)
{
  assert(ps.size() == Cs.size());
  // assert(llt == lit_Undef);
  if (!ok) return false;

  Int sum;
  if (!normalized) {
    // Group all x/~x pairs and normalize to positive coefficients:
    Map<Var, Pair<Int,Int> > var2consts(Pair_new(0,0));
    vec<Var> used_vars;
    for (int i = 0; i < ps.size(); i++){
      Var x = var(ps[i]);
      Pair<Int,Int>& consts = var2consts.ref(x);
      if (consts.fst == 0 && consts.snd == 0)
        used_vars.push(x);
      if (sign(ps[i]))
        consts.fst += Cs[i];
      else
        consts.snd += Cs[i];
    }
    vec<Pair<Int,Lit> > Csps(used_vars.size());
    for (int i = 0; i < used_vars.size(); i++){
      Var x = used_vars[i];
      const Pair<Int,Int>& consts = var2consts.ref(x);
      if (consts.fst < consts.snd){
        C -= consts.fst;
        Csps[i] = Pair_new(consts.snd - consts.fst, Lit(x));
      } else {
        C -= consts.snd;
        Csps[i] = Pair_new(consts.fst - consts.snd, ~Lit(x));
      }
    }
    sort(Csps);
    sum = 0;
    for (int i = 0; i < Csps.size(); i++){
      Cs[i] = Csps[i].fst; ps[i] = Csps[i].snd; sum += Cs[i];
      if (sum < 0) {
        fprintf(stderr, "ERROR! Too large constants encountered in constraint.\n");
        throw std::runtime_error("ERROR! Too large constants encountered in constraint.\n");
      }
    }
    ps.shrink(ps.size() - Csps.size());
    Cs.shrink(Cs.size() - Csps.size());
  } else {
    vec<Pair<Int,Lit> > Csps(ps.size());
    for (int i = 0; i < ps.size(); i++) Csps[i] = Pair_new(Cs[i], ps[i]);
    sort(Csps);
    for (int i = 0; i < Csps.size(); i++) Cs[i] = Csps[i].fst, ps[i] = Csps[i].snd;
    return true;  // normalized=true: sorting only, skip further processing
  }

  // Trivially true or false?
  if (C <= 0) return false;
  if (sum < C) { ok = false; return false; }

  // GCD simplification (one pass):
  if (Cs.size() > 0) {
    Int div = Cs[0];
    for (int i = 1; i < Cs.size(); i++) div = gcd(div, Cs[i]);
    if (div > 1) {
      for (int i = 0; i < Cs.size(); i++) Cs[i] /= div;
      C = (C + div - 1) / div;
    }
    // Coefficient trimming (one pass):
    for (int i = 0; i < Cs.size(); i++)
      if (Cs[i] > C) Cs[i] = C;
  }

  return true;
}

bool PbSolver::normalizePb(bool normalized, vec<Lit>& ps, vec<Int>& Cs, Int& C, Lit llt)
{
  //**/report->reportf("\nnormalizePb: "); for (int i = 0; i < ps.size(); i++) report->reportf(" "),dump(config, Cs[i]),report->reportf("*"),dump(config, ps[i]); report->reportf(" >= "),dump(config, C),report->reportf("\n");

  assert(ps.size() == Cs.size());
  if (!ok) return false;

  if ( ! config->no_prop ) {
    // Remove assigned literals and literals with zero coefficients:
    int new_sz = 0;
    bool eliminated = false;
    for (int i = 0; i < ps.size(); i++){
      if (value(ps[i]) != ll_Undef){
        report->reportSf(2, 'V');
        config->stats_elim_fixed_lits++;
        eliminated = true;
        if (value(ps[i]) == ll_True)
          C -= Cs[i];
      }else if (Cs[i] != 0){
        ps[new_sz] = ps[i];
        Cs[new_sz] = Cs[i];
        new_sz++;
      }
    }
    if(eliminated) {
      ps.shrink(ps.size() - new_sz);
      Cs.shrink(Cs.size() - new_sz);
    }
    //**/report->reportf("No zero, no assigned :");  for (int i = 0; i < ps.size(); i++) {report->reportf(" "); dump(config, Cs[i]); report->reportf("*"); dump(config, ps[i]);} report->reportf(" >= %d\n", toint(C));
  }

  Int sum;
  if ( ! normalized) {
    // Group all x/~x pairs
    //
    Map<Var, Pair<Int,Int> >    var2consts(Pair_new(0,0));     // Variable -> negative/positive polarity constant
    vec<Var> used_vars;
    for (int i = 0; i < ps.size(); i++){
      Var             x      = var(ps[i]);
      Pair<Int,Int>&  consts = var2consts.ref(x);
      if (consts.fst == 0 && consts.snd == 0)
        used_vars.push(x);                                    // Track first occurrence
      if (sign(ps[i]))
        consts.fst += Cs[i];
      else
        consts.snd += Cs[i];
    }

    // Normalize constants to positive values only:
    //
    vec<Pair<Int,Lit> > Csps(used_vars.size());
    for (int i = 0; i < used_vars.size(); i++){
      Var x = used_vars[i];
      const Pair<Int,Int>& consts = var2consts.ref(x);
      if (consts.fst < consts.snd){
        // Negative polarity will vanish
        C -= consts.fst;
        Csps[i] = Pair_new(consts.snd - consts.fst, Lit(x));
      }else{
        // Positive polarity will vanish
        C -= consts.snd;
        Csps[i] = Pair_new(consts.fst - consts.snd, ~Lit(x));
      }
    }
    // Sort literals on growing constant values:
    //
    sort(Csps);     // (use lexicographical order of 'Pair's here)
    sum = 0;
    for (int i = 0; i < Csps.size(); i++){
      Cs[i] = Csps[i].fst, ps[i] = Csps[i].snd, sum += Cs[i];
      if (sum < 0) {
        fprintf(stderr, "ERROR! Too large constants encountered in constraint.\n");
        throw std::runtime_error("ERROR! Too large constants encountered in constraint.\n");
      }
    }
    ps.shrink(ps.size() - Csps.size());
    Cs.shrink(Cs.size() - Csps.size());

  } else {  // case: normalized  perhaps unnecessary because already sorted in this case.
    if (ps.size() == 0) {
      // All literals were eliminated by the literal-elimination step above.
      if (C <= 0) return false;  // trivially true
      if (llt != lit_Undef) { if (!addUnit(~llt)) ok = false; } else ok = false;
      return false;  // UNSAT
    }
    vec<Pair<Int,Lit> > Csps(ps.size());
    for (int i = 0; i < ps.size(); i++)   Csps[i] = Pair_new(Cs[i], ps[i]);
    sort(Csps);
    for (int i = 0; i < Csps.size(); i++) Cs[i] = Csps[i].fst, ps[i] = Csps[i].snd;
  }


  if(normalized) return true;

  // for quickhack
  //return true;

  //**/report->reportf("Before propagateion:"); if(llt!=lit_Undef) {dump(config, llt); report->reportf(" => ");} dump(config, ps,Cs); report->reportf(" >= "); dump(config, C); report->reportf("\n");
  // Propagate already present consequences:
  //
  bool changed;
  do{
    changed = false;
    //    if(llt==lit_Undef && rlt==lit_Undef)
    while (ps.size() > 0 && sum-Cs.last() < C){
      changed = true;
      //**/ report->reportf("addUnit("); dump(config, ps.last()); report->reportf(")\n");
      if (llt != lit_Undef) {
        vec<Lit> ban;
        ban.push( ~llt );
        ban.push(ps.last());
        sat_solver->addClause(ban);
      } else 	if (!addUnit(ps.last())){
        ok = false;
        return false;
      }
      sum -= Cs.last();
      C   -= Cs.last();
      ps.pop(); Cs.pop();
    }

    //**/report->reportf("After detection :"); if(llt!=lit_Undef) {dump(config, llt); report->reportf(" => ");} dump(config, ps,Cs); report->reportf(" >= "); dump(config, C); report->reportf("\n");
    // Trivially true or false?
    if (C <= 0) {  // ture constraint
      return false;
    }
    if (sum < C){  // false constraint
      if(llt!=lit_Undef) {
        //**/ report->reportf("addUnit("); dump(config, ~dlt); report->reportf(")\n");
        if (!addUnit(~llt))
          ok = false;
      } else ok = false;
      return false;
    }

    //**/report->reportf("Nontrivial\n");

    assert(sum - Cs[ps.size()-1] >= C);

    // GCD:
    assert(Cs.size() > 0);
    Int     div = Cs[0];
    for (int i = 1; i < Cs.size(); i++)
      div = gcd(div, Cs[i]);
    for (int i = 0; i < Cs.size(); i++)
      Cs[i] /= div;
    C = (C + div-1) / div;
    if (div != 1)
      changed = true;

    // Trim constants:
    for (int i = 0; i < Cs.size(); i++)
      if (Cs[i] > C)
        changed = true,
                Cs[i] = C;
  } while (changed);

  //**/report->reportf("NormalizedPb:"); for (int i = 0; i < ps.size(); i++) report->reportf(" "),dump(config, Cs[i]),report->reportf("*"),dump(config, ps[i]); report->reportf(" >= "),dump(config, C),report->reportf("\n");

  return true;
}

void PbSolver::storePb(const vec<Lit>& ps, const vec<Int>& Cs, Int lo, Int hi, Lit llt)
{
#ifdef DEBUG
  if(config->verbosity >= 4) { report->reportf("storePb: ");     if(llt!=lit_Undef) {dump(config, &index2name, llt); report->reportf(" =>, "); }    dump(config, &index2name, ps, Cs);      report->reportf(" lo=");    dump(config, lo);     report->reportf(", hi=");     dump(config, hi);     report->reportf("\n"); }
#endif
  assert(ps.size() == Cs.size());
  for (int i = 0; i < ps.size(); i++) n_occurs[index(ps[i])]++;

  if(llt!=lit_Undef && n_occurs.size() > index(llt))
    n_occurs[index(llt)]++;

  if(!config->eager_cl || !rewritePureClause(ps, Cs, lo, hi, llt)) {
    constrs.push(new (mem.alloc(sizeof(Linear) + ps.size()*(sizeof(Lit) + sizeof(Int)))) Linear(ps, Cs, lo, hi, llt));
    //        constrs.push(new (xmalloc<Linear>(sizeof(Linear) + ps.size()*(sizeof(Lit) + sizeof(Int)))) Linear(ps, Cs, lo, hi, dlt));
#ifdef DEBUG
    //report->reportf(4, "STORED: "), dump(config, 4, constrs.last()), report->reportf(4, "\n");
#endif
  }
}

//=================================================================================================

// Returns TRUE if the constraint should be deleted. May set the 'ok' flag to false
bool PbSolver::propagate(Linear& c)
{
  //**/report->reportf("propagate c: "); dump(config, &index2name, c); report->reportf("\n");
  //**/dump(config, c, sat_solver->assigns_ref()); report->reportf("\n");

  // Remove assigned literals:
  Int     sum = 0, true_sum = 0;
  int     j = 0;
  for (int i = 0; i < c.size; i++){
    assert(c(i) > 0);
    if (value(c[i]) == ll_Undef){
      sum += c(i);
      c(j) = c(i);
      c[j] = c[i];
      j++;
    }else if (value(c[i]) == ll_True)
      true_sum += c(i);
  }
  c.size = j;
  if (c.lo != Int_MIN) c.lo -= true_sum;
  if (c.hi != Int_MAX) c.hi -= true_sum;

  if(c.llt!=lit_Undef && value(c.llt) == ll_True) {
    c.llt=lit_Undef;
    //      c.rlt=lit_Undef;
  }

  /*
        if(c.rlt!=lit_Undef && value(c.rlt) == ll_False) {
        assert(c.hi == Int_MAX);
        Int csum=0;
        for(int i=0; i<c.size; i++) {  // ~(c <= a x b y ) to -c+1+a+b <= a ~x b ~y
        sum += c(i); c[i] = ~c[i];
        }
        c.lo = Int(1) - c.lo + sum;
        c.llt = lit_Undef;
        c.rlt = lit_Undef;

        vec<Lit>    norm_ps; vec<Int>    norm_Cs;

        for (int i = 0; i < c.size; i++) {
        norm_ps.push(c[i]); norm_Cs.push(c(i));
        }
        if (normalizePb(norm_ps, norm_Cs, c.lo, lit_Undef)) {
        for (int i=0; i < norm_ps.size(); i++) {
        c[i] = norm_ps[i]; c(i) = norm_Cs[i]; c.size = norm_ps.size();
        }
        } else return true;
        }
   */
  // Propagate:
  while (c.size > 0){
    if (c(c.size-1) > c.hi){
      /**/ report->reportf(3, "addUnit("); dump(config, 3, &index2name, ~c[c.size-1]); report->reportf(3, ")\n");
      addUnit(~c[c.size-1]);
      sum -= c(c.size-1);
      c.size--;
    } else if (c.llt==lit_Undef && sum - c(c.size-1) < c.lo){
      /**/ report->reportf(3, "addUnit("); dump(config, 3, &index2name, c[c.size-1]); report->reportf(3, ")\n");
      addUnit(c[c.size-1]);
      sum -= c(c.size-1);
      if (c.lo != Int_MIN) c.lo -= c(c.size-1);
      if (c.hi != Int_MAX) c.hi -= c(c.size-1);
      c.size--;
    } else
      break;
  }

  if (c.lo <= 0)  c.lo = Int_MIN;
  if (c.hi > sum) c.hi = Int_MAX;

  //**/report->reportf("AFTER propagate(c)\n");
  //**/dump(config, c, sat_solver->assigns_ref()); report->reportf("\n\n");
  if (c.size == 0){
    if (c.lo > 0 || c.hi < 0) {
      if(c.llt==lit_Undef || !addUnit(~c.llt)) {
        ok = false;
      }
    }
    //**/report->reportf("The constraint c shrinked into length 0 with ok=%s\n", ok?"true":"false");
    return true;  // trivially satisfied or UNSAT — delete constraint
  } else if(c.lo == Int_MIN && c.hi == Int_MAX) {
    //**/ if(c.llt!=lit_Undef) {report->reportf("addUnit("); dump(config, &index2name, ~c.llt); report->reportf(")\n");}
    /*
       if(c.rlt!=lit_Undef && !addUnit(c.rlt))
       ok = false;
     */
    //**/report->reportf("The constraint c is trivial.\n");
    return true;
  }

  return false;
}

void PbSolver::propagate()
{
  if (nVars() == 0) return;
  if (occur.size() == 0) setupOccurs();

  report->reportf(3, "  -- Unit propagations: ", constrs.size());
  bool found = false;

  while (propQ_head < sat_solver->trail_size()){
    //**/report->reportf("propagate("); dump(config, trail[propQ_head]); report->reportf(")\n");
    Var     x = var(sat_solver->trail(propQ_head++));
    for (int pol = 0; pol < 2; pol++){
      vec<int>&   cs = occur[index(Lit(x,pol))];
      for (int i = 0; i < cs.size(); ){
        if (constrs[cs[i]] == NULL) {
          cs[i] = cs.last(); cs.pop(); continue;   // Remove stale entry
        }
        int trail_sz = sat_solver->trail_size();
#ifdef DEBUG
        if(config->verbosity >= 4) { report->reportf("propagate: constr[%d] by var ", cs[i]); dump(config, &index2name, Lit(x, false)); report->reportf(": "); dump(config, &index2name, *constrs[cs[i]]); }
#endif
        if (propagate(*constrs[cs[i]])) {
#ifdef DEBUG
          if(config->verbosity >= 4) report->reportf(" -> deleting\n");
#endif
          dropConstr(cs[i]);
          cs[i] = cs.last(); cs.pop();              // Remove newly deleted entry
        } else {
#ifdef DEBUG
          if(config->verbosity >= 4) report->reportf("\n");
#endif
          i++;
        }
        if (sat_solver->trail_size() > trail_sz) found = true, report->reportf(3, "p");
        if (!ok) return;
      }
    }
  }
  //    while (propQ_head < trail.size()){
  //        //**/report->reportf("propagate("); dump(config, trail[propQ_head]); report->reportf(")\n");
  //        Var     x = var(trail[propQ_head++]);
  //        for (int pol = 0; pol < 2; pol++){
  //            vec<int>&   cs = occur[index(Lit(x,pol))];
  //            for (int i = 0; i < cs.size(); i++){
  //                if (constrs[cs[i]] == NULL) continue;
  //                int trail_sz = trail.size();
  //                if (propagate(*constrs[cs[i]]))
  //                    constrs[cs[i]] = NULL;
  //                if (trail.size() > trail_sz) found = true, report->reportf("p");
  //                if (!ok) return;
  //            }
  //        }
  //    }

  if (!found) report->reportf(3, "(none)\n");
  else        report->reportf(3, "\n");

  occur.clear(true);
}


void PbSolver::setupOccurs()
{
  // Allocate vectors of right capacities:
  occur.growTo(nVars()*2);
  // On a repeated 'coding()', 'nVars()' includes clausification/solver-internal vars that
  // 'getVar()' never saw, so 'n_occurs' must be extended before the loop below reads it.
  n_occurs.growTo(nVars()*2, 0);
  for (int i = 0; i < nVars()*2; i++){
    vec<int> tmp(xmalloc<int>(n_occurs[i]), n_occurs[i]); tmp.clear();
    tmp.moveTo(occur[i]); }
  //    for (int i = 0; i < pb_n_vars; i++){
  //      report->reportf("occur[%d]=%d, ", i, occur[i]);

  // Fill vectors:
  for (int i = 0; i < constrs.size(); i++){
    if (constrs[i] == NULL) continue;
    for (int j = 0; j < constrs[i]->size; j++)
      assert(occur[index((*constrs[i])[j])].size() < n_occurs[index((*constrs[i])[j])]),
        occur[index((*constrs[i])[j])].push(i);
    if (constrs[i]->llt!=lit_Undef) {
      Lit lt = constrs[i]->llt;
      assert(occur[index(lt)].size() < n_occurs[index(lt)]);
      occur[index(lt)].push(i);
    }
    /*
       if (constrs[i]->rlt!=lit_Undef) {
       Lit rt = constrs[i]->rlt;
       assert(occur[index(rt)].size() < n_occurs[index(rt)]);
       occur[index(rt)].push(i);
       }
     */
  }
}


// Left-hand side equal
static bool lhsEq(const Linear& c, const Linear& d) {
  if (c.size == d.size){
    for (int i = 0; i < c.size; i++) if (c[i] != d[i] || c(i) != d(i)) return false;
    return true;
  }else return false; }
// Left-hand side equal complementary (all literals negated)
static bool lhsEqc(const Linear& c, const Linear& d) {
  if (c.size == d.size){
    for (int i = 0; i < c.size; i++) if (c[i] != ~d[i] || c(i) != d(i)) return false;
    return true;
  }else return false; }

// Convert to k1 <= linearExpression <= k2 from adjacent PB-constraints if possible
bool PbSolver::findIntervals()
{
  report->reportf(3, "  -- Detecting band-form from adjacent constraints: ");

  bool found = false;
  bool result = true;
  int i = 0;
  Linear* prev;
  for (; i < constrs.size() && constrs[i] == NULL; i++);
  if (i < constrs.size()){
    prev = constrs[i++];
    for (; i < constrs.size(); i++){
      if (constrs[i] == NULL) continue;
      Linear& c = *prev;
      Linear& d = *constrs[i];

      //        if(c.dlt==d.dlt || (c.dlt!=lit_Undef && d.dlt!=lit_Undef && c.dlt==d.dlt)) {
      if(c.llt==d.llt) {
        if (lhsEq(c, d)){
          if (d.lo < c.lo) d.lo = c.lo;
          if (d.hi > c.hi) d.hi = c.hi;
          dropConstr(i-1);
          if (config->verbosity >= 2) report->reportf("=");
          found = true;
          if (d.lo > d.hi && c.llt == lit_Undef) {
            result = false;
            break;
          }
        }
        if (lhsEqc(c, d)){
          Int sum = 0;
          for (int j = 0; j < c.size; j++)
            sum += c(j);
          Int lo = (c.hi == Int_MAX) ? Int_MIN : sum - c.hi;
          Int hi = (c.lo == Int_MIN) ? Int_MAX : sum - c.lo;
          if (d.lo < lo) d.lo = lo;
          if (d.hi > hi) d.hi = hi;
          dropConstr(i-1);
          if (config->verbosity >= 2) report->reportf("#");
          found = true;
          if (d.lo > d.hi && c.llt == lit_Undef) {
            result = false;
            break;
          }
        }
      }
      prev = &d;
    }
  }
  if (config->verbosity >= 3) {
    if (!found) report->reportf("(none)\n");
    else        report->reportf("\n");
  }
  return result;
}

bool PbSolver::rewriteES1(Linear& c)
{
  assert(c.lo != Int_MIN);
  //**/report->reportf(" -- detection of ES1-clause: "); dump(config, c); report->reportf("\n");
  assert(c.size >= 2);

  // Assuming sorted coefficients in ascending order

  if(c(c.size-1) == 1 && (c.lo == c.size-1 || c.lo == 1) && c.lo == c.hi && c.llt == lit_Undef) {  // ES1 detected
    if (config->verbosity >= 2) report->reportf("E"); //**/dump(config, &index2name, c); report->reportf("\n");
    vec<Lit> ps;
    for (int j = 0; j < c.size; j++)
      if(c.lo != 1)
        ps.push(~c[j]);
      else ps.push(c[j]);

    sat_solver->add_ESClause(ps);
    config->stats_es1_detection++;
    return true;
  }

  int bder=0;
  Int sum =0;
  if( c(c.size-1) != 1 || c.lo != 1) {
    int n = c.size;
    for (; n > 0 && c(n-1) == c.lo; n--);
    bder = n;
    for (; n > 0; n--) sum += c(n-1);
  }  // x + 2y + 2z + 2w >= 2;   sum = 1 and bder = 1 (y).

  if(config->or_detection && sum < c.lo) {
    //if (config->verbosity >= 2) report->reportf("."); //**/dump(config, &index2name, c); report->reportf("\n");
    vec<Lit> ps;
    assert(c(c.size-1) == c.lo);
    //if (config->verbosity >= 2) report->reportf(".\n");
    if (c.llt != lit_Undef) {
      Lit dl = c.llt;
      ps.push(~dl);      // dl => x0 v x1 ...
    }
    for (int j = bder; j < c.size; j++)
      ps.push(c[j]);
    sat_solver->addClause(ps);
    if(c.hi == Int_MAX)
      return true;
    else {
      sum = 0;
      for(int j = 0; j < c.size; j++) {
        c[j] = ~(c[j]);
        sum += c(j);
      }
      c.lo = sum; c.lo -= c.hi;
      c.hi = Int_MAX;
      return false;
    }
  }

  return false;
}

bool PbSolver::rewriteCC(Linear& c)
{

  assert(c.lo != Int_MIN);
  if(c.llt != lit_Undef)
    return false;

  //**/report->reportf(" -- detection of CC-clause: "); dump(config, c); report->reportf("\n");
  assert(c.size >= 2);

  if(c(c.size-1) == 1) {
    assert(c.lo <= Int(c.size));
    assert(c.hi == Int_MAX || c.hi <= Int(c.size));
    if(c.hi != Int_MAX && toint(c.hi) >= (c.size*(100-config->cc_thres)/100)) {
      vec<Lit> ps;
      ps.clear();
      for (int j = 0; j < c.size; j++)
        ps.push(~(c[j]));
      sat_solver->add_CCClause(ps, c.size - toint(c.hi));
      config->stats_cc_detection++;
      //**/if(c.size - toint(c.hi)==1)report->reportf("k=%d\n",c.size - toint(c.hi));
      c.hi = Int_MAX;
    }
    if(toint(c.lo) <= c.size*(config->cc_thres)/100) {
      vec<Lit> ps;
      for (int j = 0; j < c.size; j++)
        ps.push(c[j]);
      if (config->verbosity >= 2) {
        report->reportf("C"); //**/dump(config, &index2name, c); report->reportf("\n");
      }
      sat_solver->add_CCClause(ps,toint(c.lo));
      config->stats_cc_detection++;
      //**/if(c.lo==1)report->reportf("k=%d\n",c.lo);
      c.lo = Int_MIN;
    }
    if(c.lo == Int_MIN && c.hi != Int_MAX) {
      for (int j = 0; j < c.size; j++)
        c[j] = ~c[j];
      //      c.lo = Int(c.size - toint(c.hi)); c.hi = Int_MAX;
      c.lo = Int(c.size) - c.hi; c.hi = Int_MAX;
      if (config->verbosity >= 2) {
        report->reportf("c.lo=%d, c.size=%d", toint(c.lo), c.size);
        report->reportf("\nrewriteCC4"); /**/dump(config, &index2name, c); report->reportf("\n");
      }
    }
  }
  return (c.lo == Int_MIN && c.hi == Int_MAX);
}

bool PbSolver::rewriteAlmostClauses()  // called from convertPbs when stage=0 (first_call)
{
  vec<Lit>    ps;
  vec<Int>    Cs;
  bool        found = false;
  int         n_splits = 0;
  char        buf[30];

  if (config->verbosity >= 2)
    report->reportf("  -- Clauses(.)/Splits(s): ");
  for (int i = 0; i < constrs.size(); i++){
    if (constrs[i] == NULL) continue;
    Linear& c   = *constrs[i];
    //**/report->reportf("rewriteAlmostClauses: "),dump(config, c),report->reportf("\n");
    if(c.lo == Int_MIN && c.hi == Int_MAX) {  // true constraint
      /*
         if(c.rlt != lit_Undef)
         sat_solver->addUnit(c.rlt);
       */
      dropConstr(i);
      continue;      // Remove this clause
    }
    assert(c.lo != Int_MIN && c.hi == Int_MAX && c.lo <= c.hi);

    // assume sorted coefficient in ascending order
    int bder=0;
    Int sum =0;
    if( c(c.size-1) != 1 || c.lo != 1) {
      int n = c.size;
      for (; n > 0 && c(n-1) == c.lo; n--);
      bder = n;
      for (; n > 0; n--) sum += c(n-1);
    }
    //**/report->reportf("bder=%d, sum=%d, lo=%d\n", bder, toint(sum), toint(c.lo));
    if (sum < c.lo){
      // assert(c(c.size-1) == c.lo);
      if (c(c.size-1) != c.lo) {
        //**/report->reportf("rewriteAlmostClauses: c(c.size-1) != c.lo: "),dump(config, c),report->reportf("\n");
        //**/report->reportf("c.size=%d, bder=%d, sum=%d, lo=%d, llt=", c.size, bder, toint(sum), toint(c.lo));
        //**/if(c.llt != lit_Undef) dump(config, c.llt); else report->reportf("lit_Undef");
        //**/report->reportf("\n");
      }
      // Pure clause:
      //if (config->verbosity >= 2) report->reportf(".");
      found = true;

      ps.clear();
      if (c.llt != lit_Undef) {
        Lit dl = c.llt;
        ps.push(~dl);      // dl => x0 v x1 ...
      }
      for (int j = bder; j < c.size; j++)
        ps.push(c[j]);
      sat_solver->addClause(ps);
      dropConstr(i);          // Remove this clause

    } else if (config->split && c.llt==lit_Undef && c.size-bder >= 3){
      // Split clause part:
      report->reportf(2, "s");
      found = true;
      sprintf(buf, "@split%d", n_splits);
      n_splits++;
      Var x = getVar(buf);// assert(x == sat_solver->nVars()-1);
      ps.clear();
      ps.push(Lit(x));
      for (int j = bder; j < c.size; j++)
        ps.push(c[j]);
      sat_solver->addClause(ps);
      if (!sat_solver->okay()){
        report->reportf(2, "\n");
        return false; }

      ps.clear();
      Cs.clear();
      ps.push(~Lit(x));
      Cs.push(c.lo);
      for (int j = 0; j < bder; j++)
        ps.push(c[j]), Cs.push(c(j));
      if (!addConstr(false, ps, Cs, c.lo, 1, Int_MAX, -1))
        return false;  // unsat

      dropConstr(i);          // Remove this clause
    }
  }

  if (config->verbosity >= 3) {
    if (!found) report->reportf("(none)\n");
    else        report->reportf("\n");
  }
  return true;
}

bool PbSolver::rewritePureClause(const vec<Lit>& ps, const vec<Int>& Cs, Int lo, Int hi, Lit llt)
{
  if (config->use_scip || config->use_scip_constraints || config->use_cooperative_scip) return false;
  if(lo == Int_MIN && hi == Int_MAX) {  // true constraint
    return true;
  }
  assert(lo != Int_MIN && hi == Int_MAX && lo <= hi);
  assert(ps.size()==Cs.size());

  if(Cs.size() == 0) {
    if(lo > 0) {
      if (llt == lit_Undef)
        ok = false;
      else {
        vec<Lit> pss;
        Lit dl = llt;
        pss.push(~dl);
        sat_solver->addClause(pss);
      }
    }
    return true;
  }

#ifdef DEBUG
  Int cs = Cs[0];
  for (int i = 1; i < Cs.size(); i++)
    assert(cs <= Cs[i]),cs = Cs[i];
#endif
  // assume sorted coefficient in ascending order
  int bder=0;
  Int sum =0;
  if( Cs[ps.size()-1] != 1 || lo != 1) {
    int n = ps.size();
    for (; n > 0 && Cs[n-1] == lo; n--);
    bder = n;
    for (; n > 0; n--) sum += Cs[n-1];
  }
  //**/report->reportf("bder=%d, sum=%s, lo=%s\n", bder, toString(sum), toString(c.lo));
  if (sum < lo){
    assert(bder == ps.size() || Cs[ps.size()-1] == lo);
    //   if (Cs[ps.size()-1] != lo) {
    //     /**/report->reportf("rewriteAlmostClauses: Cs[size-1] != lo: "),dump(config, ps, Cs, false),report->reportf("\n");
    //     /**/report->reportf("Cs.size=%d, bder=%d, sum=%s, lo=%s, llt=", Cs.size(), bder, toString(sum), toString(lo));
    //     /**/if(llt != lit_Undef) dump(config, llt); else report->reportf("lit_Undef");
    //     /**/report->reportf("\n");
    //   }
    // Pure clause:
    report->reportf(3, ".");

    vec<Lit> pss;
    if (llt != lit_Undef) {
      Lit dl = llt;
      pss.push(~dl);      // dl => x0 v x1 ...
    }
    for (int j = bder; j < ps.size(); j++) {
      pss.push(ps[j]);
    }
#ifdef DEBUG
    report->reportf(3, "Clause sending directly to SAT solver: ");
    dump(config, 3, &index2name, pss);
    report->reportf(3, "\n");
#endif
    sat_solver->addClause(pss);
    return true;
  } else
    return false;
}

bool PbSolver::model_check(){
  // Use constrs_bk if set; otherwise (e.g., sc2) fall back to constrs, but
  // limit to pb_n_constrs to exclude solver-internal constraints added during solving
  // (@goal_constraint, @relaxing_constraint, etc.).
  vec<Linear*>& constrs_to_check = (constrs_bk.size() > 0) ? constrs_bk : constrs;
  int check_limit = (constrs_bk.size() > 0) ? constrs_to_check.size() : pb_n_constrs;
  if (config->verbosity >= 4) {
    report->reportf("model_check: ");
    for (int i = 0; i < named_vars.size(); i++) {
      Var x = named_vars[i];
      if (x >= best_model.size()) break;   // named_vars is increasing; rest are post-commit vars
      if(*(index2name[x]) != '@')
        report->reportf(" %s%s", best_model[x]?"":"-", index2name[x]);
    }
    report->reportf("\n");
  }
  report->reportf(4, "model_check: check_limit=%d pb_n_constrs=%d constrs.size=%d pb_n_vars=%d best_model.size=%d\n",
    check_limit, pb_n_constrs, constrs.size(), pb_n_vars, best_model.size());
  report->reportf(4, "constrs_to_check[i]: ");
  for (int i = 0; i < check_limit; i++){
    if (constrs_to_check[i] == NULL) continue;  // skip constraints removed by propagation
    if (config->verbosity >= 4)
      dump(config, &index2name, constrs_to_check[i], 10), report->reportf(", ");
    Int lhs = 0;
    for (int j = 0; j < constrs_to_check[i]->size; j++){
      Lit x = (*constrs_to_check[i])[j];
#ifdef DEBUG
      if (config->verbosity >= 4)
        report->reportf("1: %s(%d), sign=%d, ", index2name[var(x)], var(x), (int)sign(x) );
#endif
      if (bestModelTrue(x))
        lhs += (*constrs_to_check[i])(j);
    }
    if (config->verbosity >= 4)
      report->reportf("Value("), dump(config, lhs), report->reportf("), ");

    Lit x = constrs_to_check[i]->llt;
    if (lhs < constrs_to_check[i]->lo || lhs > constrs_to_check[i]->hi) {
      if(x == lit_Undef) {
        return false;
      } else {
        if (bestModelTrue(x)) {
          return false;
        }
      }
    }
  }
  report->reportf(4, "\n");
  return true;
}

bool PbSolver::checkAssumptions() {
    for (int i = 0; i < constrs.size(); i++)
        if (constrs[i] != NULL && constrs[i]->llt != lit_Undef)
            return true;
    return false;
}

bool PbSolver::goalValueCheck(Linear& goalExp, Int goal_val) {
  assert(best_model.size() > 0);
  Int lhs = 0;
  for (int j = 0; j < goalExp.size; j++){
    Lit x = goalExp[j];
    if (bestModelTrue(x))
      lhs += goalExp(j);
  }
  if (config->verbosity >= 3)
    report->reportf("Value("), dump(config, lhs), report->reportf(")\n");
  return lhs == goal_val;
}

// Correspondence between pb-variable and sat-variable
void PbSolver::exportVar(cchar* filename)
{
  FILE* out = fopen(filename, "a"); assert(out != NULL);
  fprintf(out, "cv");
  for (int i = 0; i < named_vars.size(); i++)
    fprintf(out, " %d:%s", named_vars[i]+1, index2name[named_vars[i]]);
  fprintf(out, "\n");
  fclose(out);
}

void PbSolver::exportInterVar(cchar* filename)
{
  if (pb_i_vars == -1) return;
  FILE* out = fopen(filename, "a"); assert(out != NULL);
  fprintf(out, "c p show");
  for (int i = 0; i < pb_i_vars; i++)
    fprintf(out, " %d", i+1);
  fprintf(out, " 0\n");
  fclose(out);
}

void PbSolver::exportToCnf(cchar* filename) {
  sat_solver->exportCnf(filename, pb_n_vars - 1);
  exportVar(filename);
  exportInterVar(filename);
}

solverStatus PbSolver::coding(vec<Lit>& assump_ps)
{
  if (config->use_scip) return sst_unknown;
  if (!ok) {set_pb_solver_status(sst_unsat); return get_pb_solver_status();} // unsat

  double start_time = cpuTime();
  // Convert constraints:
  pb_n_vars = nVars();
  pb_n_constrs = constrs.size();
  if (config->verbosity >= 2) report->reportf("Converting %d PB-constraints to clauses...\n", constrs.size());
  if(pb_n_constrs>0) propagate();

  // For cooperative SCIP: create SCIP env first so checkScipPrecisionSafety()
  // can read the actual feastol via SCIPfeastol() instead of falling back to 0.
  if (config->use_cooperative_scip) {
    config->scip_as_presolve = config->scip_as_presolve_for_dec;
    initScipEnv();
  }
  checkScipPrecisionSafety();

  // Sync constraints (only if still enabled after the precision check).
  if (config->use_cooperative_scip) {
    initScipForCoop();
  }

  // SCIP presolver: run before CNF coding to obtain bounds or prove optimality/infeasibility.
  if (config->use_cooperative_scip && config->scip_as_presolve > 0.0) {
    vec<Lit> empty_assump;
    report->reportf(1, "Running SCIP presolver (time limit: %g sec)...\n", config->scip_as_presolve);
    solverStatus ps_result = solveScip(empty_assump, config->scip_as_presolve);
    report->reportf(1, "SCIP presolver: %s\n", solverStatus_strs[ps_result]);

    if (ps_result == sst_unsat) {
      ok = false;
      set_pb_solver_status(sst_unsat);
      return sst_unsat;
    }
    if (ps_result == sst_sat) {
      set_pb_solver_status(sst_sat);
      return sst_sat;
    }
  }

  if (clausifier == NULL)   // keep the existing one (and its vmap) on a repeated 'coding()'
    clausifier = new Clausifier(sat_solver, config, report);
  if (!convertPbs(0)){
    if (interrupted) { set_pb_solver_status(sst_unknown); return get_pb_solver_status(); }
    assert(!ok); set_pb_solver_status(sst_unsat); return get_pb_solver_status();
  } // unsat

  /*   // to be separated into PbSolver_Export::export() function
       if (config->cnf != NULL) {
       if (strlen(config->cnf) == 0) exit(0);
       if (config->verbosity >= 2) {
       report->reportf("Exporting CNF to: \b%s\b\n", config->cnf);
       }
       sat_solver->exportCnf(config->cnf, (Var)(pb_n_vars-1));
       exportVar(config->cnf);
       exportInterVar(pb_i_vars, config->cnf);
       solver_status = sst_cnf;
       raise(SIGUSR1);
       }
   */

  sat_solver->setVerbosity(config->sat_verbosity);

  if(!ok) set_pb_solver_status(sst_unsat);
  config->stats_coding_time += (cpuTime() - start_time);
  return get_pb_solver_status();
}

void PbSolver::commitBestModel() {
  // Cover every SAT var existing at commit time so that vars 'getVar()'ed after a
  // previous 'coding()' (and Tseitin/solver-internal vars) are readable too.
  // 'model(x)' may be ll_Undef for solver-internal vars: treat as false.
  best_model.growTo(nVars(), false);
  for (Var x = 0; x < best_model.size(); x++) {
    best_model[x] = (sat_solver->model(x) == ll_True);
  }
  if (config->model_check) {
    if (!model_check()) {
      report->reportf("Wrong model\n");
      throw std::runtime_error("Wrong model\n");
    } else {
      report->reportf("Model check passed\n");
    }
  }
}

solverStatus PbSolver::sat_solver_invoke(vec<Lit>& assump_ps, vec<Lit>& failed_assumps, double time_limit)
{
  if (time_limit > 0) sat_solver->setTimeout(time_limit);
  solverStatus result = sst_unknown;
  if (sat_solver->solve(assump_ps, failed_assumps)) { // sat returned
    config->stats_sat_calls++;
    result = sst_sat;
  } else {
    if (sat_solver->timed_out || interrupted) {
      // timed_out: time budget exceeded; interrupted: async signal (SIGINT etc.)
      result = sst_unknown;
    } else {
      config->stats_unsat_calls++;
      result = sst_unsat;
    }
  }
  report->reportf(2, "sat_solver_result: %s\n", solverStatus_strs[result]);
  syncSatFixedVars();
  if (time_limit > 0) sat_solver->clearTimeout();
  return result;
}

solverStatus PbSolver::solve(vec<Lit>& assump_ps, vec<Lit>& failed_assumps)
{
  if (config->use_cooperative_scip && get_pb_solver_status() == sst_sat) {
    return sst_sat;
  }
  if (config->use_scip) {
    solverStatus s = solveScip(assump_ps);
    set_pb_solver_status(s);
    return s;
  }
  // PbSolver::code() must be called to coding an initial constraints
  double start_time = cpuTime();

  config->stats_cnf_coding_time += cpuTime();

  // solve once
  solverStatus result = sat_solver_invoke(assump_ps, failed_assumps);
  if (result == sst_sat) commitBestModel();
  set_pb_solver_status(result);

  if(config->extract_ucore && result == sst_unsat) {
    int id, ind_size = index2name.size();
    for(int i = 0; i < sat_solver->ucore_varsSize(); i++)
      if ((id = sat_solver->ucore_vars(i)) < ind_size)
        ucore_var_id.push(id);
  }
  config->stats_basic_solver_time += (cpuTime() - start_time);
  return result;
}

#include <unistd.h>

static void safe_write(const char* str) {
    if (str) {
        int len = 0;
        while (str[len]) len++;
        if (len > 0) {
            int ret = write(1, str, len);
            (void)ret; // Ignore return value in signal handler
        }
    }
}

static void safe_write_int(int val) {
    char buf[32];
    int i = sizeof(buf) - 1;
    buf[i] = '\0';
    if (val == 0) {
        buf[--i] = '0';
    } else {
        bool neg = val < 0;
        if (neg) val = -val;
        while (val > 0) {
            buf[--i] = '0' + (val % 10);
            val /= 10;
        }
        if (neg) buf[--i] = '-';
    }
    int len = sizeof(buf) - 1 - i;
    if (len > 0) {
        int ret = write(1, &buf[i], len);
        (void)ret; // Ignore return value in signal handler
    }
}

void PbSolver::outputResult(solverStatus status, Config* config, bool normalExit)
{
  if (!config->satlive || config->verbosity <= 0) return;

  // On timeout/signal: if pb_solver_status was set to sst_unsat by an intermediate
  // SAT call inside the optimization loop (meaning "no better solution above this bound"),
  // but we actually found a solution, correct the status to sst_sat.
  if (!normalExit && status == sst_unsat && best_model.size() > 0) {
    safe_write("c timeout or signal occurred, but we found a solution\n");
    status = sst_sat;
  }

  bool correct_model = true;
  bool error_coccurs = false;
  Int GB = INT_MAX, EG = INT_MAX;
  if((status == sst_sat || status == sst_best) && config->model_check) {   // && normalExit) {
    // Model check shouldn't be executed in signal handler when berbosity >= 4 (not async-signal-safe)
    correct_model = model_check();

    if (get_original_goal() != NULL) {
      GB = get_best_goalvalue();
      EG = evalGoal(*(get_original_goal()));
    }
  }

  if (!normalExit) safe_write("c\n"); // Avoid mixing with existing output

  switch (status) {
    case sst_unsat:
      safe_write("s UNSATISFIABLE\n"); break;
    case sst_sat:
      if (get_number_models() != -1) {
        safe_write("s FOUND MODELS: "); safe_write_int(get_number_models()); safe_write("\n");
      }
      else if (get_best_goalvalue() == Int_MIN && best_model.size() > 0)
        safe_write("s SATISFIABLE\n");
      else {
        // S.best_goalvalue = S.best_goalvalue * S.goal_coeff + S.goal_offset;
        // char* tmp = toString(get_best_goalvalue());
        if (!config->maxsat && GB == EG)
             safe_write("s SATISFIABLE\n");
        else {
          error_coccurs = true;
        }
        // printf("s SATISFIABLE\n");
        // xfree(tmp);
      }
      break;
    case sst_best:
      {
        if(GB == EG) {
          safe_write("s OPTIMUM FOUND\n");
        } else {
          error_coccurs = true;
        }
      }
      break;
    case sst_all:
      safe_write("s ALL MODELS: "); safe_write_int(get_number_models()); safe_write("\n");
      break;
    case sst_unknown:
      safe_write("s UNKNOWN\n");
      break;
    case sst_cnf:
      safe_write("s Exported CNF to: "); safe_write(config->cnf_export); safe_write("\n");
      break;
  }

  if (config->model_out && config->verbosity >= 1 && !error_coccurs) {
    int i=0;
    if( status == sst_sat ||
        status == sst_best ) {
      safe_write("v");
      // Iterate named vars only: 'best_model' also covers unnamed (clausification/solver
      // internal) vars, which must never appear on the "v" line (competition convention:
      // only variables read from the input file, i.e. named ones without the '@' prefix).
      for (int k = 0; k < named_vars.size(); k++) {
        Var x = named_vars[k];
        if (x >= best_model.size()) break;   // named_vars is increasing; rest are post-commit vars
        if(!config->positive_mode || best_model[x]) {
          if(i % 2000 >= 1999) safe_write("\nv");
          i++;
          if(*(index2name[x]) != '@' || config->verbosity >= 4) {
            safe_write(best_model[x] ? " " : " -");
            safe_write(index2name[x]);
          }
        }
      }
      safe_write("\n");
    }

    if (config->extract_ucore && normalExit && status == sst_unsat){
      safe_write("v");
      for (int x = 0; x < ucore_var_id.size(); x++){
        if(i % 2000 >= 1999) safe_write("\nv");
        i++;
        safe_write(" ");
        safe_write(index2name[ucore_var_id[x]]);
      }
      safe_write("\n");
    }
  }

  if((status == sst_sat || status == sst_best) && config->model_check) {   // && normalExit) {
    // Model check shouldn't be executed in signal handler when berbosity >= 4 (not async-signal-safe)
    if(!correct_model) safe_write("c Wrong model\n");
    else safe_write("c Model check passed\n");

    if (get_original_goal() != NULL) {
      if(config->verbosity >= 4) {
        report->reportf(1, "Original goal: ");
        dump(config, 1, &index2name, get_original_goal());
        report->reportf(1, "\n");
      }
      char* GB_str = toString(GB), *EG_str = toString(EG);
      if(GB == EG) {
        report->reportf("Model check has passed with goal value: %s\n", GB_str);
      } else {
        report->reportf("Goal value is wrong wrt best assignment: (best_goalvalue: %s, evalGoal_value: %s\n", GB_str, EG_str );
        throw std::runtime_error("Model is differ from the best goalvalue\n");
      }
      xfree(GB_str), xfree(EG_str);
    }
  }

  if (sat_solver->solving) sat_solver->solving_time += (cpuTime() - sat_solver->starting_time);
  if (config->verbosity >= 1) {
    printStats(stats, cpuTime(), sat_solver, pb_solver_inner);
  }

  if (normalExit) fflush(stdout);
}

void PbSolver::printStats(BasicSolverStats& stats, double cpu_time, Solver* solver, PbSolver* pb_solver_inner)
{
  report->reportf("_____________________________________________________________________________\n");
  report->reportf("std/band forms        : %d,%d\n", config->stats_std_form, config->stats_band_form);
  report->reportf("2cl/3cl ITE-codings   : %d,%d\n", config->stats_monotonic_coding, config->stats_non_monotonic_coding);
  report->reportf("BDD/srt/adr Max costs : %d,%d,%d\n", config->stats_bdd_cost,config->stats_sort_cost,config->stats_adder_cost);
  report->reportf("BDDraw/bin/mul/srt/adr: %d,%d,%d,%d,%d\n", config->stats_bdd_raw_constraints, config->stats_bdd_bin_constraints, config->stats_bdd_mul_constraints, config->stats_sort_constraints, config->stats_adder_constraints);
  report->reportf("BDD nodes             : %d\n", config->stats_bdd_nodes);
  report->reportf("Es1/CC detections     : %d,%d\n", config->stats_es1_detection,config->stats_cc_detection);
  report->reportf("CC sort               : %d\n", config->stats_cc_sort);
  report->reportf("SAT/UNSAT calls       : %d,%d\n", config->stats_sat_calls,config->stats_unsat_calls);
  report->reportf("regenrated variables  : %d\n", config->stats_regen_variables);
  report->reportf("eliminated fixed lits : %d\n", config->stats_elim_fixed_lits);
  report->reportf("goal lit flips        : %d\n", solver->goal_lit_flips);
  report->reportf("goal stages           : %d\n", config->stats_goal_stages);
  report->reportf("goal shrink           : %d\n", config->stats_goal_shrink);
  report->reportf("goal coeff separations: %d\n", config->stats_coeff_separations);
  report->reportf("restarts              : %" I64_fmt "\n", stats.starts);
  report->reportf("conflicts             : %-12" I64_fmt "   (%.0f /sec)\n", stats.conflicts   , stats.conflicts   /cpu_time);
  report->reportf("decisions             : %-12" I64_fmt "   (%.0f /sec)\n", stats.decisions   , stats.decisions   /cpu_time);
  report->reportf("propagations          : %-12" I64_fmt "   (%.0f /sec)\n", stats.propagations, stats.propagations/cpu_time);
  report->reportf("inspects              : %-12" I64_fmt "   (%.0f /sec)\n", stats.inspects    , stats.inspects    /cpu_time);
  report->reportf("CPU time (solving tm) : %.4g s        (%.4g s, SAT %.4g s, UNSAT %.4g s)\n", cpu_time, solver->solving_time, stats.sat_solving_time, stats.unsat_solving_time);
  report->reportf("SAT solver time       : %.4g s\n", solver->solving_time);
  report->reportf("SAT solver SAT time   : %.4g s\n", stats.sat_solving_time);
  report->reportf("SAT solver UNSAT time : %.4g s\n", stats.unsat_solving_time);
  report->reportf("SAT solver TO time    : %.4g s\n", stats.timeout_solving_time);
  // if (inner_pb_solver != nullptr) {
  //   report->reportf("CPU time (inner)      : %.4g s\n", inner_pb_solver->config->cpu_time);
  // }
  report->reportf("Time for SCIP         : %.4g s\n", config->stats_scip_time);
  if (config->use_cooperative_scip) {
    report->reportf("Coop SCIP LB improvements           : %d\n", config->stats_coop_scip_lb_improvements);
    report->reportf("Coop SCIP UB improvements           : %d\n", config->stats_coop_scip_ub_improvements);
    report->reportf("SCIP globally fixed variables       : %d\n", config->stats_scip_fixed_vars);
    report->reportf("SAT  globally fixed variables       : %d\n", config->stats_sat_fixed_vars);
    report->reportf("SCIP freeTransform calls            : %d\n", config->stats_scip_free_transform_count);
  }
  report->reportf("Time for Opt-base     : %.4g s\n", config->stats_opt_base_calc_time);
  report->reportf("Steps for Opt-base    : %" I64_fmt "\n", config->stats_opt_base_calc_steps);
  report->reportf("Time for BDD coding   : %.4g s\n", config->stats_BDD_coding_time);
  report->reportf("Time for srt coding   : %.4g s\n", config->stats_srt_coding_time);
  report->reportf("Inner solver invocations            : %d\n", config->stats_inner_solver_invocation);
  report->reportf("Inner solving count                 : %d\n", config->stats_inner_solving_count);
  if (pb_solver_inner != nullptr) {
    report->reportf("Time for inner coding               : %.4g s\n", pb_solver_inner->config->stats_coding_time);
    report->reportf("Time for inner basic pb solver      : %.4g s\n", pb_solver_inner->config->stats_basic_solver_time);
    report->reportf("Time for inner minimizing pb solver : %.4g s\n", pb_solver_inner->config->stats_minimizing_solver_time + pb_solver_inner->config->stats_scip_time);
    report->reportf("Time for inner sat solver           : %.4g s\n", pb_solver_inner->sat_solver->solving_time);
  }
  report->reportf("_____________________________________________________________________________\n");
  fflush(stdout);
}

//=================================================================================================

void PbSolver_Minimize::freeGoals()
{
  // 'goals' entries may alias 'goal' (see 'divideGoal()'), so compare before freeing.
  for (int i = 0; i < goals.size(); i++)
    if (goals[i] != goal && goals[i] != original_goal) freeLinear(goals[i]);
  goals.clear(true);

  if (goal != original_goal) freeLinear(goal);
  freeLinear(original_goal);
  goal = original_goal = NULL;
}


void PbSolver_Minimize::addGoal(const vec<Lit>& ps_orig, const vec<Int>& Cs_orig)
{
  freeGoals();   // a goal set earlier is replaced, not accumulated

  vec<Lit> ps;;vec<Int> Cs;
  for(int i = 0; i < ps_orig.size(); i++)
    ps.push(ps_orig[i]), Cs.push(Cs_orig[i]);

  original_goal = new (xmalloc<char>(sizeof(Linear) + ps.size()*(sizeof(Lit) + sizeof(Int)))) Linear(ps, Cs, Int_MIN, Int_MAX, lit_Undef);

  goal_offset = PbSolver_Minimize::normalizeGoal(ps, Cs);  // convert it into MAXIMIZING problem


  //**/report->reportf("MIN: "); dump(config, ps, Cs); report->reportf("\n");
  assert(ps.size() == Cs.size());
  assert(ps.size() >= 0);

  // sort in ascending order
  vec<Pair<Int,Lit> > Csps(ps.size());
  for (int i = 0; i < ps.size(); i++)
    Csps[i] = Pair<Int,Lit>(Cs[i], ps[i]);
  sort(Csps);
  for (int i = 0; i < ps.size(); i++)
    Cs[i] = Csps[i].fst, ps[i] = Csps[i].snd;

#ifdef DEBUG
  for(int i = 0; i < ps.size(); i++){
    assert(Cs[i] > 0);
  }
  for (int i = 1; i < ps.size(); i++) {
    assert( Cs[i] >= Cs[i-1] );
  }
#endif

  // GCD;
  vec<Int> norm_Cs;
  if(config->goal_gcd) {
    if(ps.size() != 0) {
      goal_coeff = Cs[0];
      for (int i = 1; i < Cs.size(); i++)
        goal_coeff = gcdg(goal_coeff, Cs[i]);
    } else goal_coeff = 1;
  } else goal_coeff = 1;

  report->reportf(2, "goal was devided by "),dump(config, 2, goal_coeff),report->reportf(2, " with offset "),dump(config, 2, goal_offset),report->reportf(2, ".\n");

  if(goal_coeff != 1) {
    for (int i = 0; i < Cs.size(); i++)
      norm_Cs.push( Cs[i] / goal_coeff );
    goal = new (xmalloc<char>(sizeof(Linear) + ps.size()*(sizeof(Lit) + sizeof(Int)))) Linear(ps, norm_Cs, Int_MIN, Int_MAX, lit_Undef);
  } else
    goal = new (xmalloc<char>(sizeof(Linear) + ps.size()*(sizeof(Lit) + sizeof(Int)))) Linear(ps, Cs, Int_MIN, Int_MAX, lit_Undef);
}



// Delete terms with zero coefficient

void PbSolver_Minimize::simplifyGoal(vec<Lit>& ps, vec<Int>& Cs)
{
  /*
     bool hasZero = false;
     for (int i = 0; i <ps.size(); i++){
     if (Cs[i] == 0){
     hasZero = true;
     break;
     }
     }
   */
  //  if(hasZero){
  int shrink = 0;
  for (int i = 0; i <ps.size(); i++){
    if (Cs[i] == 0) shrink++;
    else if (shrink > 0) {
      Cs[i-shrink] = Cs[i]; ps[i-shrink] = ps[i];
    }
  }
  assert(ps.size() >= shrink);
  //    if(ps.size() == shrink) shrink--;  // avoid null goal, leaving at least one 0*x.
  if(shrink > 0){
    ps.shrink(shrink); Cs.shrink(shrink);
  }
  //  }
}

Int PbSolver_Minimize::normalizeGoal(vec<Lit>&ps, vec<Int>& Cs)
{
  Int offset = 0;

  PbSolver_Minimize::simplifyGoal(ps, Cs); // Remove zero valued co-efficients

  // Normalize constants to positive values only:
  for(int i = 0; i < ps.size(); i++){
    if (Cs[i] > 0){
      offset += Cs[i];
      ps[i] = ~ps[i];
    } else {
      Cs[i] = -Cs[i];
    }
  }
  //   report->reportf("CS= ");
  //   for(int i = 0; i < ps.size(); i++) dump(config, Cs[i]),report->reportf(" ");
  //   report->reportf("\n");

  return offset;
}


// static
// Int evalGoal(Linear& goal, Solver* sat_solver)
Int PbSolver_Minimize::evalGoal(const Linear& goal)
{
  Int sum = 0;
  for (int i = 0; i < goal.size; i++){
    // assert(sat_solver->model(var(goal[i])) != ll_Undef);
    // if (( sign(goal[i]) && sat_solver->model(var(goal[i])) == ll_False)
    // ||  (!sign(goal[i]) && sat_solver->model(var(goal[i])) == ll_True )
    if (bestModelTrue(goal[i]))
      sum += goal(i);
  }
  return sum;
}

Int PbSolver_Minimize::evalGoalUB(const Linear& goal, const vec<Lit>& hitting_assumptions, const vec<Var>& outerVar2goalInd) {
  Int sum = 0;
  // report->reportf(1, "evalGoalUB: goal: ");
  // dump(config, 1, &index2name, goal, 0);
  // report->reportf(1, "\nevalGoalUB: hitting_assumptions: ");
  for (int i = 0; i < hitting_assumptions.size(); i++) {
    // dump(config, 1, goal(outerVar2goalInd[var(hitting_assumptions[i])]));
    // report->reportf(1, " * ");
    // dump(config, 1, &index2name, hitting_assumptions[i]);
    // report->reportf(1, " ");
    sum += goal(outerVar2goalInd[var(hitting_assumptions[i])]);
  }
  // report->reportf(1, "\n");
  return sum;
}

// static
Int PbSolver_Minimize::evalGoalUB(const Linear& goal)
  // Int evalGoalUB(Linear& goal, Solver* sat_solver)
{
  Int sum = 0;
  for (int i = 0; i < goal.size; i++){
    //printf("assigns(%s%d) = %c\n", sign(goal[i])?"-":"", var(goal[i])+1, "TFU"[toInt(sat_solver->assigns(var(goal[i])))]);
    if (value(goal[i]) == ll_Undef) {
      if (goal(i) > 0)
        sum += goal(i);
    }
    else {
      if( value(goal[i]) == ll_True )
        //   if (( sign(goal[i]) && sat_solver->assigns(var(goal[i])) == ll_False) ||
        //       (!sign(goal[i]) && sat_solver->assigns(var(goal[i])) == ll_True ))
        //report->reportf("UB added ps: "),dump(config, goal[i]),report->reportf(" Cs: "),dump(config, goal(i)),report->reportf("\n"),
        sum += goal(i);

    }
  }
  return sum;
}

//static
Int PbSolver_Minimize::evalGoalLB(const Linear& goal)
{
  Int sum = 0;
  for (int i = 0; i < goal.size; i++){
    //printf("assigns(%s%d) = %c\n", sign(goal[i])?"-":"", var(goal[i])+1, "TFU"[toInt(sat_solver->assigns(var(goal[i])))]);
    if ( value(goal[i]) != ll_Undef) {
      if( value(goal[i]) == ll_True)
        //   if (( sign(goal[i]) && sat_solver->assigns(var(goal[i])) == ll_False) ||
        //       (!sign(goal[i]) && sat_solver->assigns(var(goal[i])) == ll_True ))
        sum += goal(i);
    }
  }
  return sum;
}
//static
//Int evalGoal(Linear& goal, vec<lbool>& model)
//{
//    Int sum = 0;
//    for (int i = 0; i < goal.size; i++){
//        assert(model[var(goal[i])] != ll_Undef);
//        if (( sign(goal[i]) && model[var(goal[i])] == ll_False)
//        ||  (!sign(goal[i]) && model[var(goal[i])] == ll_True )
//        )
//            sum += goal(i);
//    }
//    return sum;
//}

void PbSolver_Minimize::solverSetting(Linear* goalExp) {
  vec<Lit> goal_ps;
  vec<Int> goal_Cs;

  assert (goalExp != NULL);
  // Sort the constraints in descending order of the coefficients
  vec<Pair<Int,int> > Csps(goalExp->size);
  for (int i = 0; i < goalExp->size; i++) {
    Int c = (*goalExp)(i);
    Csps[i] = Pair_new(c > 0 ? -c : c, i);  // to sort in descending order
  }
  if (config->flip_min)
    sort(Csps);
  for (int i = 0; i < goalExp->size; i++) {
    int idx = Csps[i].snd;
    goal_ps.push((*goalExp)[idx]);
    goal_Cs.push((*goalExp)(idx));
  }
  // DEBUG
  // for (int i = 0; i < goalExp->size; i++) {dump(config, goal_Cs[i]); report->reportf(" "); dump(config, goal_ps[i]); report->reportf(" "); }  report->reportf("\n");
  if (config->flip_min)
    sat_solver->setGoal(goal_ps, goal_Cs);

  if (config->polarity_sug != 0) {
    for (int i = 0; i < goal_Cs.size(); i++)
      //    sat_solver->suggestPolarity(var(goal_ps[i]), ((goal_Cs[i]*config->polarity_sug > 0 && !sign(goal_ps[i])) || (goal_Cs[i]*config->polarity_sug < 0 && sign(goal_ps[i]))) ? ll_False : ll_True);
      sat_solver->suggestPolarity(var(goal_ps[i]), ((goal_Cs[i]*config->polarity_sug > 0 && !sign(goal_ps[i])) || (goal_Cs[i]*config->polarity_sug < 0 && sign(goal_ps[i]))) ? ll_True : ll_False);
    // TODO: check polarity strategy!!
  }

  // Freeze goal function variables (for SatELite and Simplification solvers):
  if (config->simp_solver){
    for (int i = 0; i < goal->size; i++)
      //report->reportf("FreeZing %d\n",var((*goal)[i])),
      sat_solver->freeze(var((*goal)[i]));
  }
}

bool PbSolver_Minimize::printBestGoalValue() {
      bool improve = false;
      char* real_gv = toString(best_goalvalue);
      if (best_goalvalue < last_printed_goalvalue && best_goalvalue <= config->goal_ub) {
        last_printed_goalvalue = best_goalvalue;
        if (config->is_inner_solver) {
            report->reportf(2, "o %s\n", real_gv);
        } else {
            printf("o %s\n", real_gv);
        }
        improve = true;
      } else {
        if (best_goalvalue > config->goal_ub) {
          report->reportf("Found a solution but worse than the given initial goal: %s\n", real_gv);
        }
        if(best_goalvalue > last_printed_goalvalue) {
          char* last_gv = toString(last_printed_goalvalue);
          report->reportf("Something is wrong! last_printed_goalvalue: %s < best_goalvalue: %s\n", last_gv, real_gv);
          xfree(last_gv);
          // if(best_goalvalue > last_printed_goalvalue) {
          //   throw std::runtime_error("Internal Error\n");
          // }
        }
      }
      xfree(real_gv);
      return improve;
}

static bool updateLBGoalvalue(Report* report, Int goal_lb_cand, Int& LB_goalvalue, int verbosity) {
  bool updated = false;
  if (goal_lb_cand > LB_goalvalue) {
    if (verbosity >= 3) {
      char* cand_str = toString(goal_lb_cand);
      if (LB_goalvalue != Int_MIN ) {
        char* curr_str = toString(LB_goalvalue);
        report->reportf("goal lb updated from %s to %s by fixed literals\n", curr_str, cand_str);
        xfree(curr_str);
      } else{
        report->reportf("goal lb updated from -oo to %s by fixed literals\n", cand_str);
      }
      xfree(cand_str);
    }
    LB_goalvalue = goal_lb_cand;
    updated = true ;
    // if (config->add_assumption_literal_after_fixed) {
    //   if(!addConstr(true, goal_ps, goal_Cs, LB_goalvalue, 1))
    //     assert(false);
    //   else convertPbs(stageCount);
    // } else {
    //   Lit assump_lit = Lit(sat_solver->newVar(true));
    //   if(!addConstr(true, goal_ps, goal_Cs, LB_goalvalue, 1, Int_MAX, 0, assump_lit))
    //     assert(false);
    //   else {
    //     convertPbs(stageCount);
    //     assump_ps.push(assump_lit);
    //   }
    // }
  }
  return updated;
}

static bool updateUBGoalvalue(Report* report, Int goal_ub_cand, Int& UB_goalvalue, int verbosity) {
  bool updated = false;
  if (UB_goalvalue > goal_ub_cand) {
    if (verbosity >= 3) {
      char* cand_str = toString(goal_ub_cand);
      if (UB_goalvalue != Int_MAX ) {
        char* curr_str = toString(UB_goalvalue);
        report->reportf("goal ub updated from %s to %s by fixed literals\n", curr_str, cand_str);
        xfree(curr_str);
      } else {
        report->reportf("goal ub updated from oo to %s by fixed literals\n", cand_str);
      }
      xfree(cand_str);
    }
    UB_goalvalue = goal_ub_cand;
    updated = true;
  }
  return updated;
}

bool PbSolver_Minimize::addGoalConstraint(const vec<Lit>& goal_ps, const vec<Int>& goal_Cs, Int try_moreThanOrEq, vec<Lit>& assump_ps, int stageCount, Int rhs2) {
  bool unsat = false;
  // Lit assump_lit = Lit(sat_solver->newVar(true));
  char        buf[30];
  sprintf(buf, "@goal_constraint%d", n_goal_constraints++);
  Lit assump_lit = Lit(getVar(buf));
  if (addConstr(false, goal_ps, goal_Cs, try_moreThanOrEq, 1, rhs2, -1, assump_lit)) {
    convertPbs(stageCount);
    assump_ps.push(assump_lit);
    report->reportf(3, "addGoalConstraint: assump_lit: "),dump(config, 3, &index2name,assump_lit),report->reportf(3, "\n");
  } else {
    unsat = true;
  }
  return unsat;
}

static Minimization_Mode set_minimizationMode(Report* report, Config* config, solverStatus sat_solver_result=sst_sat, int consec_UNSAT=0, Int diff_goalvalue=Int_MAX) {
  Minimization_Mode minimizationMode = cmm_Sequential;
  switch(config->minimization_strategy) {
    case mt_Sequential:
      minimizationMode = cmm_Sequential;
      break;
    case mt_Binary:
      if(diff_goalvalue < config->seq_thres)
           minimizationMode = cmm_Sequential;
      else minimizationMode = cmm_Binary;
      break;
    case mt_Hitting:
      if(diff_goalvalue < config->seq_thres)
           minimizationMode = cmm_Sequential;
      else minimizationMode = cmm_Hitting;
      break;
    case mt_Sequential_Binary:
      if((sat_solver_result == sst_unsat && consec_UNSAT >= config->consec_unsat) || diff_goalvalue < config->seq_thres)
           minimizationMode = cmm_Sequential;
      else minimizationMode = cmm_Binary;
      break;
    case mt_Sequential_Hitting:
      if(sat_solver_result != sst_sat || diff_goalvalue < config->seq_thres)
           minimizationMode = cmm_Sequential;
      else minimizationMode = cmm_Hitting;
      break;
    case mt_Sc2:
      minimizationMode = cmm_sc2;
      break;
    case mt_Sequential_Sc2:
      if(sat_solver_result != sst_sat || diff_goalvalue < config->seq_thres)
           minimizationMode = cmm_Sequential;
      else minimizationMode = cmm_sc2;
      break;
    default:
      report->reportf("Not implemented");
      assert(false);
  }
  return minimizationMode;
}

// Adds (*sc2_goal)[i] to hitting_assumptions if it meets the stratification threshold in sc2 mode.
void PbSolver_Minimize::pushSc2HittingAssumptionIfAboveThreshold(Linear* sc2_goal, const vec<Int>& strat_weights,
                                                                  vec<Lit>& hitting_assumptions) {
  for(int i=0; i < sc2_goal->size; i++) {
    dump(config, 3, &index2name, (*sc2_goal)[i]); report->reportf(3, "*");
    dump(config, 3, (*sc2_goal)(i)); report->reportf(3, ": ");
    if(value((*sc2_goal)[i]) != ll_Undef) {
      report->reportf(3, "sc2 goal literal: already assigned\n");
      continue;
    }
    Int thres = 1;
    if (thres < strat_weights[strat_weights.size()-1]) {
      thres = strat_weights[strat_weights.size()-1];
    }
    if((*sc2_goal)(i) >= thres) {
      report->reportf(3, "sc2 goal literal: meets threshold: "); dump(config, 3, thres); report->reportf(3, "\n");
      hitting_assumptions.push((*sc2_goal)[i]);
    } else {
      report->reportf(3, "\n");
      // report->reportf(3, "sc2 goal literal: does not meet threshold %d\n", thres);
    }
  }
}

// Computes stratification weights strat_weights from the range of coefficients of sc2_goal (Cs_kinds).
// If the number of distinct values is config->sc2_split_max or fewer, all distinct values are used directly.
// If it exceeds the limit, the boundaries with larger differences between adjacent values are prioritized to reduce the number of weights to config->sc2_split_max.
void PbSolver_Minimize::computeSc2StratWeights(Linear* sc2_goal, vec<Int>& strat_weights) {
  vec<Int> Cs_kinds; vec<int> Cs_numbers;
  Cs_kinds.push((*sc2_goal)(0)); Cs_numbers.push(1);
  for (int i=1; i<sc2_goal->size; i++) {
    if((*sc2_goal)(i) == Cs_kinds.last()) Cs_numbers.last()++;
    else { Cs_kinds.push((*sc2_goal)(i)); Cs_numbers.push(1); }
  }
  if(config->verbosity >=2) {
    report->reportf(2, "sc2_goal kinds and numbers: ");
    for (int i=0; i < Cs_kinds.size(); i++) {
      dump(config, 2, Cs_kinds[i]);
      report->reportf(2, ",%d; ", Cs_numbers[i]);
    }
    report->reportf(2, "\n");
  }
  if(Cs_kinds.size() <= config->sc2_split_max) {
    if(Cs_kinds[0] != 1) {
      strat_weights.push(1);
    }
    for(int i=0; i < Cs_kinds.size(); i++) {
      strat_weights.push(Cs_kinds[i]);
    }
  } else {
    // Choose strat_weights from Cs_kinds so that the number of strat_weights is at most config->sc2_split_max
    // by cutting from larger diffs.
    assert(Cs_kinds.size() > config->sc2_split_max);
    vec<Pair<Int, int>> weight_diffs;
    for (int i=1; i < Cs_kinds.size(); i++) {
      weight_diffs.push(Pair<Int, int> (Cs_kinds[i] - Cs_kinds[i-1], i));
    }
    sort(weight_diffs, LessThan_reverse<Pair<Int, int>>());
    if(config->verbosity >=2 ) {
      report->reportf(2, "sc2 weight diffs: ");
      for (int i=0; i < weight_diffs.size(); i++) {
        dump(config, 2, weight_diffs[i].fst);
        report->reportf(2, ",%d; ", weight_diffs[i].snd);
      }
      report->reportf(2, "\n");
    }
    assert(config->sc2_split_max <= weight_diffs.size());
    strat_weights.growTo(config->sc2_split_max);
    for(int i=config->sc2_split_max-1; i >= 0; i--) {
      strat_weights[config->sc2_split_max-1-i] = Cs_kinds[weight_diffs[i].snd];
    }

    // Include the smallest weight and 1 in strat_weights if they are not included.
    if(strat_weights[0] != Cs_kinds[0]) {
      strat_weights.push();
      for(int i=strat_weights.size()-1; i>0; i--) {
        strat_weights[i] = strat_weights[i-1];
      }
      strat_weights[0] = Cs_kinds[0];
    }
    if(strat_weights[0] != 1) {
      strat_weights.push();
      for(int i=strat_weights.size()-1; i>0; i--) {
        strat_weights[i] = strat_weights[i-1];
      }
      strat_weights[0] = 1;
    }
  }
  if(config->verbosity >=2) {
    report->reportf(2, "sc2 strat weights: ");
    for (int i=0; i < strat_weights.size(); i++) {
      dump(config, 2, strat_weights[i]);
      report->reportf(2, ", ");
    }
    report->reportf(2, "\n");
  }
}

// One iteration of the hitting-set minimization strategy, called from 'maximizeGoal'.
// Returns true iff the caller should 'continue' its enclosing while-loop immediately.
bool PbSolver_Minimize::processHittingSetIteration(Linear* goalExp, const Int& LB_goalvalue, Int& UB_goalvalue,
                                                    ResultT& current_result, bool& exec_loop,
                                                    const vec<Lit>& goal_ps, const vec<Lit>& failed_assumptions,
                                                    vec<Lit>& hitting_assumptions,
                                                    const vec<Var>& goalInd2outerVar, const vec<Var>& goalInd2innerVar,
                                                    const vec<Var>& outerVar2goalInd, const vec<bool>& goalInd2sign) {
  hitting_assumptions.clear();
  vec<Lit> ps; vec<Int> Cs;
  for(int j = 0; j < failed_assumptions.size(); j++) {
    int goalInd_x = outerVar2goalInd[var(failed_assumptions[j])];
    // report->reportf("goalInd_x: %d\n", goalInd_x);
    Var x = goalInd2innerVar[goalInd_x];
    bool sign_x = goalInd2sign[goalInd_x];
    ps.push(~Lit(x, sign_x));
    Cs.push(Int(1));
  }
  if(failed_assumptions.size() > 0) {
    //if (verbosity >= 3)
    report->reportf(2, "adding hitting set constraint: "), dump(config, 2, &(pb_solver_inner->index2name), ps, Cs), report->reportf(2, " >= 1\n");
    if( ! pb_solver_inner->addConstr(false, ps, Cs, Int(1), 1) ) {  // expr >= 1
      report->reportf("failed to solve hitting set in inner-solver.\n");
      assert(false);
    }
    if (!pb_solver_inner->convertPbs(0)){ assert(!ok); set_pb_solver_status(sst_unsat); assert(false); }
  }
  report->reportf(2, "----------------- invoking inner solver\n");
  config->stats_inner_solving_count++;
  solverStatus sat_solver_result = pb_solver_inner->solve();
  if(config->verbosity >= 2) {
    report->reportf("================= inner solver finished with ");
    switch (sat_solver_result) {
      case sst_sat: report->reportf("sat"); break;
      case sst_unsat: report->reportf("unsat"); break;
      case sst_best: report->reportf("best"); break;
      case sst_all: report->reportf("all"); break;
      case sst_cnf: report->reportf("cnf"); break;
      case sst_unknown: report->reportf("unknown"); break;
    }
    report->reportf("\n");
  }
  if( sat_solver_result != sst_best) {
    if (sat_solver_result == sst_unknown) {
      // Inner solver was interrupted (e.g., ^C); re-raise the signal so that
      // NaPS's signal handler can output results cleanly and exit.
      raise(SIGTERM);
      // Fallback if handler returns (should not normally happen)
      exec_loop = false;
      return true;
    }
    report->reportf("inner solver finished without sst_best.\n");
    assert(false);
  }
  report->reportf(2, "assignment of inner solver: ");
  for(int j=0; j< min(20,goal_ps.size()); j++) {
    Var inner_x = goalInd2innerVar[j];
    dump(config, 2, &(pb_solver_inner->index2name), Lit(inner_x));
    report->reportf(2, "=%d, ", pb_solver_inner->best_model[inner_x]);
  }
  report->reportf(2, "\n");

  for(int i=0; i < goal_ps.size(); i++) {
    Var inner_x = goalInd2innerVar[i];
    Var outer_x = goalInd2outerVar[i];
    bool outer_sign = goalInd2sign[i];

    bool vvalue = pb_solver_inner->best_model[inner_x];
    bool lvalue = outer_sign?!vvalue:vvalue;
    if( lvalue ) {
      hitting_assumptions.push( Lit(outer_x, outer_sign) );
      // report->reportf("%s%s, ", outer_sign?"~":"", index2name[outer_x]);
      // dump(config, Lit(outer_x, outer_sign)), report->reportf(", ");
    }
  }
  report->reportf(3, "hitting set: ");
  dump(config, 3, &(index2name), hitting_assumptions);
  report->reportf(3, "\n");

  Int ub_cand = evalGoalUB(*goalExp, hitting_assumptions, outerVar2goalInd);
  if(updateUBGoalvalue(report, ub_cand, UB_goalvalue, config->verbosity)) {
    if (config->verbosity >= 2){
      char* hi = toString(UB_goalvalue);
      char* lo = toString(LB_goalvalue);
      report->reportf("Optimum bounded: [%s,%s].\n", lo, lo, hi);
      xfree(hi), xfree(lo);
    }
    if(LB_goalvalue == UB_goalvalue) {
      assert(get_pb_solver_status() != sst_unknown);
      if (config->verbosity >= 2) report->reportf("best found due to opt=lb\n");
      current_result = rt_optimized;
      exec_loop = false;
      return true;
    }
  }
  return false;
}

// Assemble the assumption set handed to the SAT solver: the caller's assumptions (user, treated
// as read-only) followed by this stage's internal literals.  hitting is populated in hitting/sc2
// modes, goalc (goal_assumptions) in Sequential/Binary; both are empty otherwise.
static void buildSatAssumps(vec<Lit>& out, const vec<Lit>& user, const vec<Lit>& hitting, const vec<Lit>& goalc) {
  out.clear();
  for (int i = 0; i < user.size(); i++)    out.push(user[i]);
  for (int i = 0; i < hitting.size(); i++) out.push(hitting[i]);
  for (int i = 0; i < goalc.size(); i++)   out.push(goalc[i]);
}

// Remove the caller's assumptions ('user') from a SAT core, so that core-guided reasoning (sc2
// relaxation, hitting-set extraction) only sees this stage's internal soft literals.  The user
// literals are permanently enforced, so dropping them is sound; an empty result means the
// instance is UNSAT under the given assumptions.
static void dropUserAssumpsFromCore(vec<Lit>& core, const vec<Lit>& user) {
  int keep = 0;
  for (int i = 0; i < core.size(); i++) {
    bool is_user = false;
    for (int j = 0; j < user.size(); j++)
      if (core[i] == user[j]) { is_user = true; break; }
    if (!is_user) core[keep++] = core[i];
  }
  core.shrink(core.size() - keep);
}

ResultT PbSolver_Minimize::maximizeGoal(Linear* goalExp, Int& LB_goalvalue, Int& UB_goalvalue, vec<Lit>& assump_ps, int stageCount, bool finalStep) {
  ResultT current_result = get_pb_solver_status() == sst_sat ? rt_sat : rt_unknown;
  bool    exec_loop = true;
  int     consec_UNSAT = 0;  // config->consec_unsat is not implemented
  Int try_morethan = Int_MIN;

  if(updateLBGoalvalue(report, evalGoalLB(*goalExp), LB_goalvalue, config->verbosity)) {
    report->reportf(2, "LB updated by evalGoalLB to "), dump(config, 2, LB_goalvalue), report->reportf(2, "\n");
    has_model_for_bestvalue = false;
  }
  if(updateUBGoalvalue(report, evalGoalUB(*goalExp), UB_goalvalue, config->verbosity)){
    report->reportf(2, "UB updated by evalGoalUB to "), dump(config, 2, UB_goalvalue), report->reportf(2, "\n");
  }

  // If a valid model exists (from presolver or previous stage), initialize LB from it.
  // evalGoal(*goalExp) evaluates goalExp against best_model — correct for any stage's goalExp,
  // but only when every goal var is covered by the committed model: 'normalizeGoal' flips
  // literals, so reading a post-commit var as false could overestimate the (maximized) LB.
  bool goal_covered_by_model = true;
  for (int i = 0; i < goalExp->size; i++)
    if (var((*goalExp)[i]) >= best_model.size()) { goal_covered_by_model = false; break; }
  if (get_pb_solver_status() == sst_sat && best_model.size() > 0 && goal_covered_by_model) {
    if(updateLBGoalvalue(report, evalGoal(*goalExp), LB_goalvalue, config->verbosity)) {
      report->reportf(2, "LB updated by evalGoal to "), dump(config, 2, LB_goalvalue), report->reportf(2, "\n");
      report->reportf(1, "o "), dump(config, 1, best_goalvalue), report->reportf(1, " omitted printing the best_goalvalue\n");
      // printBestGoalValue();
      has_model_for_bestvalue = true;
    }
  }

  if(LB_goalvalue == UB_goalvalue && get_pb_solver_status() == sst_sat) {
    current_result = rt_optimized;
    exec_loop = false;
  }

  Minimization_Mode minimizationMode
    = get_pb_solver_status() == sst_sat ? set_minimizationMode(report, config) : cmm_First_solve;
  if(minimizationMode == cmm_sc2 && config->sat_before_sc2) {
    minimizationMode = cmm_First_solve;
  }

  if(config->verbosity >= 2) {
    report->reportf(2, "minimizationMode: %s\n", Minimization_Mode_strs[minimizationMode]);
  }

  assert(goalExp != NULL);
  vec<Lit>  goal_ps; vec<Int>  goal_Cs;
  for(int i = 0; i < goalExp->size; i++) goal_ps.push((*goalExp)[i]), goal_Cs.push((*goalExp)(i));
  report->reportf(2, "goal coeffs: "),dump(config, 2, goal_Cs), report->reportf(2, "\n");

  // vec<bool> in_goal_vars;
  // in_goal_vars.growTo(pb_n_vars, false);
  // for(int i = 0; i < goal_ps.size(); i++)  in_goal_vars[var(goal_ps[i])] = true;

  // Preparation for hitting sets strategy
  vec<Lit> failed_assumptions;
  vec<Lit> hitting_assumptions;  // used also in sc2
  // Internal goal-bound activation literals (Sequential/Binary/First_solve).  These are this
  // stage's own scratch literals and are kept OUT of the caller's assump_ps so that the caller's
  // assumptions survive the whole optimisation loop (see maximizeGoal's assumption contract).
  vec<Lit> goal_assumptions;
  vec<Var>  goalInd2outerVar, outerVar2goalInd;
  vec<Var>  goalInd2innerVar, innerVar2goalInd;
  vec<bool> goalInd2sign;

  if(config->minimization_strategy == mt_Hitting || config->minimization_strategy == mt_Sequential_Hitting) {
    if(pb_solver_inner != nullptr) {
      delete pb_solver_inner;
    }
    // // Moved to Constructor of PbSolver.h#96
    // Config* config_inner = new Config();
    // config_inner->verbosity = 3;
    // config_inner->repeated_optimizations = true;
    // config_inner->single_stage_mode = true;
    pb_solver_inner = new PbSolver_Minimize(config_inner);
    config->stats_inner_solver_invocation++;
    for (int i=0; i<goal_ps.size(); i++) {
      Var outer_x = var(goal_ps[i]);
      Var inner_x = pb_solver_inner->getVar(index2name[outer_x]);
      bool sign_x = sign(goal_ps[i]);

      goalInd2outerVar.push(outer_x);
      goalInd2innerVar.push(inner_x);
      goalInd2sign.push(sign_x);

      outerVar2goalInd.growTo(outer_x + 1, i);  // where i is padding (the value has no meaning)
      innerVar2goalInd.growTo(inner_x + 1, i);
      outerVar2goalInd[outer_x] = i;
      innerVar2goalInd[inner_x] = i;

      // report->reportf("goalInd: %d, outer: %d, inner: %d\n", i, outer_x, inner_x);
    }
    // add internal goal
    vec<Lit> tmp_ps; vec<Int> tmp_Cs;
    for (int i=0; i<goal_ps.size(); i++) {
      // Var outer_x = goalInd2outerVar[i];
      bool sign_x = goalInd2sign[i];
      Int coeff_x = goal_Cs[i];
      Var inner_x = goalInd2innerVar[i];

      tmp_ps.push(Lit(inner_x, sign_x));
      tmp_Cs.push(-(coeff_x));
      // report->reportf("outer: %d, inner: %d, coeff: ", outer_x, inner_x), dump(config, coeff_x), report->reportf(", sign: %d\n", sign_x);
    }
    pb_solver_inner->addGoal(tmp_ps, tmp_Cs);
    pb_solver_inner->coding();
  }

  // Preparation for Sc2
  Linear* sc2_goal = nullptr;
  vec<int> Lit2sc2goalInd;
  vec<Int> strat_weights;
  Int UB_sc2_goal = Int_MAX;
  if(config->minimization_strategy == mt_Sc2 || config->minimization_strategy == mt_Sequential_Sc2) {
    UB_sc2_goal = 0;
    sc2_goal = new (xmalloc<char>(sizeof(Linear) + goal_ps.size()*(sizeof(Lit) + sizeof(Int))))
      Linear(goal_ps, goal_Cs, goalExp->lo, goalExp->hi, goalExp->llt);
    for (int i = 0; i < sc2_goal->size; i++) {
      Lit p = (*sc2_goal)[i];
      Lit2sc2goalInd.growTo(index(p) +1, i);
      Lit2sc2goalInd[index(p)] = i;
      UB_sc2_goal += (*sc2_goal)(i);
    }

    if (config->sc2_split) {
      computeSc2StratWeights(sc2_goal, strat_weights);
    } else {
      strat_weights.push(1);
    }
  }


  // if (config->verbosity >= 3)
    report->reportf(3, "Maximize: "), dump(config, 3, &(index2name), goalExp), report->reportf(3, "\n");

  // assume positive coefficients in asscending order in goalExp
#ifdef DEBUG
  {
    Int cs = goal_Cs[0];
    for (int i = 1; i < goal_Cs.size(); i++) assert(cs <= goal_Cs[i]),cs = goal_Cs[i];
  }
#endif

  solverSetting(goalExp);

  // if(pb_solver_status == sst_unknown) {
  //   minimizationMode = cmm_Sequential;
  // }

  Int cached_goal_bound = Int_MIN;  // last bound passed to addGoalConstraint; Int_MIN = no cache
  Lit cached_goal_lit;              // activation literal (valid iff cached_goal_bound != Int_MIN)
  double sat_remaining_time_budget = sat_current_time_budget;  // accumulated SAT time remaining until next SCIP call

  while (exec_loop) {
    if (interrupted) { exec_loop = false; break; }
    solverStatus sat_solver_result = sst_unknown;

    if(minimizationMode == cmm_Hitting) {
      if (processHittingSetIteration(goalExp, LB_goalvalue, UB_goalvalue, current_result, exec_loop,
                                      goal_ps, failed_assumptions, hitting_assumptions,
                                      goalInd2outerVar, goalInd2innerVar, outerVar2goalInd, goalInd2sign)) {
        continue;
      }
    } else if (minimizationMode == cmm_sc2) {
      hitting_assumptions.clear();  // to be removed
      pushSc2HittingAssumptionIfAboveThreshold(sc2_goal, strat_weights, hitting_assumptions);
      while(hitting_assumptions.size() == 0) {
        strat_weights.pop();
        if(strat_weights.size() == 0) {
          current_result = rt_optimized;
          exec_loop = false;
          break;
        }
        pushSc2HittingAssumptionIfAboveThreshold(sc2_goal, strat_weights, hitting_assumptions);
      }
      if(!exec_loop) break;
    } else if (minimizationMode != cmm_First_solve) {
      if(minimizationMode == cmm_Binary && UB_goalvalue - LB_goalvalue < config->seq_thres) {
        if (config->verbosity >= 2) {
          report->reportf("Sequential mode due to the small gap between UB and LB.");
        }
        minimizationMode = cmm_Sequential;
      }
      switch (minimizationMode) {
        case cmm_Sequential:
          try_morethan = LB_goalvalue ;
          break;
        case cmm_Binary:
          {
            assert(LB_goalvalue != Int_MIN);
            assert(UB_goalvalue != Int_MAX);
            Int bin_coeff = Int((int)(config->bin_coeff * 1000));
            try_morethan = (UB_goalvalue*1000 + LB_goalvalue*(bin_coeff-1000))/bin_coeff ;
          }
          break;
        default:
          assert(false);
      }
      if (config->verbosity >= 2) {
        char* hi = toString(try_morethan);
        report->reportf("Try to find a solution more than %s\n", hi);
        xfree(hi);
      }

      if(try_morethan != Int_MIN) {
        bool goal_constraint_unsat;
        if (cached_goal_bound == try_morethan + 1) {
          goal_assumptions.push(cached_goal_lit);
          goal_constraint_unsat = false;
          report->reportf(3, "Reusing goal constraint for same bound (prev timeout)\n");
        } else {
          goal_constraint_unsat = addGoalConstraint(goal_ps, goal_Cs, try_morethan+1, goal_assumptions, stageCount);
          if (!goal_constraint_unsat) {
            cached_goal_bound = try_morethan + 1;
            cached_goal_lit = goal_assumptions.last();
          }
        }
        if (goal_constraint_unsat) {
          if (config->verbosity >= 3) report->reportf("unsat returned without sat-solver\n");
          sat_solver_result = sst_unsat;
        } else {
#ifdef DEBUG
          if (config->verbosity >= 4)
            for (int i=0; i < goal_assumptions.size(); i++) {
              report->reportf("2 goal_assumptions[%d] = %d\n", i, index(goal_assumptions[i]));
            }
#endif
        }
      }
    }

    failed_assumptions.clear();
    // Invoke sat_solver
    if (sat_solver_result != sst_unsat) {
      if(minimizationMode == cmm_Hitting || minimizationMode == cmm_sc2) {
        vec<Lit> unified_assump_ps;
        buildSatAssumps(unified_assump_ps, assump_ps, hitting_assumptions, goal_assumptions);
        report->reportf(2, "invoking solver with assumptions: ");
        dump(config, 2, &(index2name), unified_assump_ps);
        report->reportf(2, "\n");
        failed_assumptions.clear();
        if(config->use_cooperative_scip) {
          if (sat_remaining_time_budget > 0.0) {
            double sat_t0 = cpuTime();
            sat_solver_result = sat_solver_invoke(unified_assump_ps, failed_assumptions, sat_remaining_time_budget);
            sat_remaining_time_budget -= cpuTime() - sat_t0;
          } else {
            sat_solver_result = sst_unknown;
          }
        } else {
          sat_solver_result = sat_solver_invoke(unified_assump_ps, failed_assumptions);
        }
        // Strip the caller's assumptions from the core so core-guided relaxation / hitting-set
        // extraction never index their variables (which are outside the goal-literal maps).
        dropUserAssumpsFromCore(failed_assumptions, assump_ps);
        if(sat_solver_result != sst_unknown) {
          num_improved_by_sat++;
        }
      } else {
        vec<Lit> merged_assump_ps;
        buildSatAssumps(merged_assump_ps, assump_ps, hitting_assumptions, goal_assumptions);  // hitting empty here
        report->reportf(2, "assumptions: ");
#ifdef DEBUG
        for(int i = 0; i < merged_assump_ps.size(); i++) {
          Lit l = merged_assump_ps[i];
          Var v = var(l);
          report->reportf(2, "%s%s, ", sign(l) ? "~" : "", index2name[v]);
        }
#endif
        report->reportf(2, "\n");
        if(!config->use_cooperative_scip) {
          report->reportf(2, "invoking solver\n");
          sat_solver_result = sat_solver_invoke(merged_assump_ps);
        } else {
          if(sat_remaining_time_budget > 0.0) {
            report->reportf(2, "invoking solver with time budget: %f\n", sat_remaining_time_budget);
            double sat_t0 = cpuTime();
            sat_solver_result = sat_solver_invoke(merged_assump_ps, sat_remaining_time_budget);
            sat_remaining_time_budget -= cpuTime() - sat_t0;
          } else {
            report->reportf(2, "no time budget left for sat solver\n");
            sat_solver_result = sst_unknown;
          }
        }
        if(sat_solver_result != sst_unknown) {
          num_improved_by_sat++;
        }
      }
    }

    if (interrupted) { exec_loop = false; break; }

    // processes for sat result
    if (sat_solver_result == sst_sat) {
      if (config->verbosity >= 3) report->reportf("process after sat\n");
      consec_UNSAT = 0;

      set_pb_solver_status(sst_sat);
      report->reportf(2, "pb_solver_status: %s\n", solverStatus_strs[get_pb_solver_status()]);

      // Evaluate *goalExp directly against the sat_solver's model before committing it as
      // the new best model: under cmm_sc2 the sat result only satisfies the current
      // stratified subset of the goal, so it is not guaranteed to improve on LB_goalvalue.
      Int candidate_goalvalue = 0;
      for (int i = 0; i < goalExp->size; i++) {
        Lit p = (*goalExp)[i];
        assert(sat_solver->model(var(p)) != ll_Undef);
        bool val = (sat_solver->model(var(p)) == ll_True);
        if (sign(p) ? !val : val) candidate_goalvalue += (*goalExp)(i);
      }

      if (updateLBGoalvalue(report, candidate_goalvalue, LB_goalvalue, config->verbosity) || !has_model_for_bestvalue) {
        // best model update
        commitBestModel();
        has_model_for_bestvalue = true;
        report->reportf(3, "known_best_model: ");
        dump(config, 3, &index2name, best_model);
        report->reportf(3, "\n");

        // update model
#if DEBUG
        if (config->verbosity >= 4)
          report->reportf("original_goal: "),dump(config, original_goal);
#endif
        //print "o best_goalvalue"
        best_goalvalue = evalGoal(*original_goal);
        if(config->verbosity >= 1) {
          report->reportf(2, "in maximizeGoal ");
          report->reportf(1, "found by SAT\n");
          printBestGoalValue();
        }
      } else if (config->verbosity >= 2) {
        char* cand_str = toString(candidate_goalvalue);
        char* lb_str = toString(LB_goalvalue);
        report->reportf(2, "SAT model found but did not improve goal value (%s <= %s); discarding model.\n", cand_str, lb_str);
        xfree(cand_str); xfree(lb_str);
      }

      if( !(config->repeated_optimizations) && (minimizationMode != cmm_First_solve) && goal_assumptions.size() > 0 ) {
        Lit p = goal_assumptions.last(); goal_assumptions.pop();
        sat_solver->addUnit(p);
        report->reportf(2, "addUnit: "), dump(config, 2, &index2name, p), report->reportf(2, "\n");
      }
      if( config->repeated_optimizations && (minimizationMode != cmm_First_solve) && goal_assumptions.size() >= 2 ) {
        Lit p = goal_assumptions.last(); goal_assumptions.pop(); goal_assumptions.pop(); goal_assumptions.push(p);
      }

      has_model_for_bestvalue = true ;
      consec_UNSAT = 0;

      if (minimizationMode == cmm_Hitting || (minimizationMode == cmm_sc2 && strat_weights.size() <= 1)) {
        current_result = rt_optimized;
        exec_loop = false;
      } else {
        current_result = rt_sat;

        if(minimizationMode == cmm_sc2) {
          strat_weights.pop();
        }

        if(updateLBGoalvalue(report, evalGoalLB(*goalExp), LB_goalvalue, config->verbosity)) {
          has_model_for_bestvalue = false;
        }

        // updates UB based on the fixed literals
        updateUBGoalvalue(report, evalGoalUB(*goalExp), UB_goalvalue, config->verbosity);

        if (minimizationMode != cmm_First_solve && minimizationMode != cmm_sc2) {
#ifdef DEBUG
          if (config->verbosity >= 3){
            char* lo = toString(LB_goalvalue);
            if(try_morethan == Int_MIN)
              report->reportf("DEBUG-found: LB_goalvalue: %s for try_morethan: oo\n", lo);
            else {
              char *hi = toString(try_morethan);
              report->reportf("DEBUG-found: LB_goalvalue: %s for try_morethan: %s\n", lo, hi);
              xfree(hi);
            }
            xfree(lo);
          }
#endif
          assert(LB_goalvalue >= try_morethan);
        }

        if (config->verbosity >= 2){
          char* hi = toString(UB_goalvalue);
          char* lo = toString(LB_goalvalue);
          report->reportf("Found a solution: %s.  Optimum bounded: [%s,%s].\n", lo, lo, hi);
          xfree(hi), xfree(lo);
        }

        if(LB_goalvalue == UB_goalvalue) {
          assert(get_pb_solver_status() != sst_unknown);
          if (config->verbosity >= 2) report->reportf("best found due to opt=lb\n");
          current_result = rt_optimized;
          exec_loop = false;
        }
      }
    } else if (sat_solver_result == sst_unknown) {
      // SAT solver timed out (time budget exceeded); proceed to cooperative SCIP call
      report->reportf(2, "SAT solver timed out; proceeding to cooperative SCIP call\n");
      // consec_UNSAT++;  // signal to minimizationMode selection that no progress was made
      // pop the threshold assumption in Sequential/Binary mode (not Hitting/First_solve)
      if ((minimizationMode == cmm_Sequential || minimizationMode == cmm_Binary) && goal_assumptions.size() > 0)
        goal_assumptions.pop();
      // LB/UB unchanged; exec_loop stays true so SCIP cooperative call proceeds
    } else {  // unsat returned
      if (minimizationMode == cmm_Hitting || minimizationMode == cmm_sc2) {
        hitting_assumptions.clear();
        // The core was already stripped of the caller's assumptions (dropUserAssumpsFromCore).
        // An empty core here means the instance is UNSAT under the given assumptions, so report
        // UNSAT rather than relaxing soft goal literals (which would index out of bounds).
        if (failed_assumptions.size() == 0) {
          report->reportf(2, "UNSAT under the given assumptions (core has no soft literal)\n");
          current_result = rt_unsat;
          set_pb_solver_status(sst_unsat);
          exec_loop = false;
          break;
        }
        if (config->verbosity >= 2) {
          // Only for debugging that sorts failed assumptions by index
          sort(failed_assumptions);
          report->reportf(2, "failed assumptions: ");
	        for(int i = 0; i < failed_assumptions.size(); i++) {
            Var x = var(failed_assumptions[i]);
            int lsign = sign(failed_assumptions[i]);
            report->reportf(2, "%s%s, ", lsign ? "~" : "", index2name[x]);
	        }
          report->reportf(2, "\n");
        }

        // Finding minimum coefficient to calculate LB
        // Preparation for a constrint on relaxation variable
        if (minimizationMode == cmm_sc2) {
          vec<Lit> ps; vec<Int> Cs;
          // Rewrite code for finding min value and corresponding literal
          Int min_penalty = Int_MAX;
          for(int i = 0; i < failed_assumptions.size(); i++) {
            // failed_assumptions has been stripped of the caller's assumptions, so every entry
            // is an sc2 goal literal with a valid Lit2sc2goalInd mapping.
            assert(index(failed_assumptions[i]) < Lit2sc2goalInd.size());
            int ind = Lit2sc2goalInd[index(failed_assumptions[i])];
            assert(ind < sc2_goal->size && (*sc2_goal)[ind] == failed_assumptions[i]);
            Lit p = (*sc2_goal)[ind];
            Int C = (*sc2_goal)(ind);

            ps.push(~p); Cs.push(Int(1));
            if(C < min_penalty) {
              min_penalty = C;
            }
          }
          UB_sc2_goal -= min_penalty;
          report->reportf(2, "Min penalty: "); dump(config, 2, min_penalty); report->reportf(2, "\n");
          updateUBGoalvalue(report, UB_sc2_goal, UB_goalvalue, config->verbosity);

          if(LB_goalvalue == UB_goalvalue) {
            if (config->verbosity >= 2) report->reportf("best found due to opt==lb\n");
            current_result = rt_optimized;
            exec_loop = false;
          }
          if (config->verbosity >= 2){
            char* lo = toString(LB_goalvalue);
            char* hi = toString(UB_goalvalue);
            report->reportf("Better solution not found.  Optimum bounded [%s,%s]\n", lo, hi);
            xfree(lo),xfree(hi);
          }

          // Modifying sc2_goals (i.e. soft literals)
          for(int i = 0; i < failed_assumptions.size(); i++) {
            int ind = Lit2sc2goalInd[index(failed_assumptions[i])];
            (*sc2_goal)(ind) -= min_penalty;
          }
          // Augmenting each relaxation variable and its constraint
          vec<Lit> new_rel_ps; vec<Int> new_rel_Cs;
          // this->addConstr(false, ps, Cs, Int(1), 1, Int_MAX, -2, lit_Undef, lit_Undef);
          for(int j = 2; j <= failed_assumptions.size(); j++) {
            char        buf[30];
            sprintf(buf, "@relaxing_constraint%d", n_relaxing_constraints++);
            Lit rel = Lit(getVar(buf));
            if(config->verbosity >= 3) {
              report->reportf(3, "Relaxation constraint: ");
              for(int i = 0; i < ps.size(); i++) {

              }
            }
            if( ! this->addConstr(false, ps, Cs, Int(j), 1, Int_MAX, -2, lit_Undef, rel)) {
              report->reportf("UNSAT unexpectedly.\n");
              assert(false);
              throw std::runtime_error("Error in SC2 optimization\n");
            }

            // For extending *sc2_goal by (rel,min_penalty)
            new_rel_ps.push(~rel); new_rel_Cs.push(min_penalty);
          }

          // Rebuilding sc2_goal with the newly added relaxation variables
          {
            vec<Lit> sc2_ps; vec<Int> sc2_Cs;
            for (int i = 0; i < sc2_goal->size; i++) {
              sc2_ps.push((*sc2_goal)[i]); sc2_Cs.push((*sc2_goal)(i));
            }
            for (int i = 0; i < new_rel_ps.size(); i++) {
              sc2_ps.push(new_rel_ps[i]); sc2_Cs.push(new_rel_Cs[i]);
            }

            Linear* new_sc2_goal = new (xmalloc<char>(sizeof(Linear) + sc2_ps.size()*(sizeof(Lit) + sizeof(Int))))
              Linear(sc2_ps, sc2_Cs, sc2_goal->lo, sc2_goal->hi, sc2_goal->llt);
            freeLinear(sc2_goal);
            sc2_goal = new_sc2_goal;

            Lit2sc2goalInd.clear();
            for (int i = 0; i < sc2_goal->size; i++) {
              Lit p = (*sc2_goal)[i];
              Lit2sc2goalInd.growTo(index(p) +1, i);
              Lit2sc2goalInd[index(p)] = i;
            }
          }

          if( ! this->convertPbs(0) ) {
            report->reportf("UNSAT unexpectedly.\n");
            assert(false);
            throw std::runtime_error("Error in SC2 optimization\n");
          }
        }
      } else {
        if (config->verbosity >= 3) report->reportf("process after unsat\n");
        consec_UNSAT++;
        if(minimizationMode != cmm_First_solve && goal_assumptions.size() > 0) goal_assumptions.pop();

        // First solve
        if (minimizationMode == cmm_First_solve) {
          exec_loop = false;
          current_result = rt_unsat;
        // Sequential mode
        } else if(minimizationMode == cmm_Sequential) {
          exec_loop = false;
          if(current_result == rt_sat) {
            current_result = rt_optimized;
          // } else if(current_result == rt_unknown) {
          //   // has_model_for_bestvalue means best_model is a valid feasible solution at LB.
          //   // This can happen in mt_Sequential_Binary when Binary UNSAT switches to Sequential:
          //   // the LB was set from a known model (e.g., a previous stage), so "no solution > LB"
          //   // means the optimal IS LB, not that the problem is unsatisfiable.
          //   if(has_model_for_bestvalue)
          //     current_result = rt_optimized;
          //   else
          //     current_result = rt_unsat;
          } else {
            report->reportf("current_result: %s\n", solverStatus_strs[current_result]);
            assert(false);
          }
        // Binary mode
        } else if (minimizationMode == cmm_Binary) {
          // updates LB based on the fixed literals
          if(updateLBGoalvalue(report, evalGoalLB(*goalExp), LB_goalvalue, config->verbosity)) {
            has_model_for_bestvalue = false;
          }

          // Updates UB to the current threshold because of UNSAT
          UB_goalvalue = try_morethan;
          // updates UB based on the fixed literals
          updateUBGoalvalue(report, evalGoalUB(*goalExp), UB_goalvalue, config->verbosity);

          if(LB_goalvalue == UB_goalvalue) {
            if (config->verbosity >= 2) report->reportf("best found due to opt==lb\n");
            current_result = rt_optimized;
            exec_loop = false;
          }
          if (config->verbosity >= 2){
            char* lo = toString(LB_goalvalue);
            char* hi = toString(UB_goalvalue);
            report->reportf("Better solution not found.  Optimum bounded [%s,%s]\n", lo, hi);
            xfree(lo),xfree(hi);
          }
        } else {
          assert(false);
        }
      }
    }

    // Cooperative SCIP call for maximizing goalExp
    bool trigger_scip = (sat_remaining_time_budget <= 0.0);
    if (exec_loop && config->use_cooperative_scip && trigger_scip) {
      Int scip_lb = Int_MIN;
      Int scip_dual_ub = Int_MAX;
      vec<Lit> empty_assump;
      num_improved_by_scip = 0;
      // scip_coop_stagecount = stageCount;

      if(config->verbosity >= 2) {
        char* lb_s = toString(LB_goalvalue);
        char* ub_s = toString(UB_goalvalue);
        report->reportf(2, "Calling solveScip for cooperative mode with LB_goalvalue:       %s, UB_goalvalue: %s\n", lb_s, ub_s);
        xfree(lb_s);
        xfree(ub_s);
      }
      // For the case pb_solver_status == sst_unknown and LB_goalvalue != Int_MIN.
      // Decrease LB_goalvalue by 1 because SCIP returns sst_unsat even if having a solution with value LB_goalvalue.
      Int lb_hint = (pb_solver_status == sst_unknown) ? Int_MIN : LB_goalvalue;
      solverStatus scip_result;
      report->reportf(2, "scip solving with time budget: %f\n", scip_current_time_budget);
      if (config->scip_use_ub)
        scip_result = solveScip(empty_assump, scip_current_time_budget, goalExp, lb_hint, &scip_lb, &scip_dual_ub, UB_goalvalue);
      else
        scip_result = solveScip(empty_assump, scip_current_time_budget, goalExp, lb_hint, &scip_lb, &scip_dual_ub);

      if (scip_lb != Int_MIN) {
        Int original_LB_goalvalue = LB_goalvalue;
        if (updateLBGoalvalue(report, scip_lb, LB_goalvalue, config->verbosity)) {
          if (scip_result == sst_sat || scip_result == sst_best)
               has_model_for_bestvalue = true;
          else has_model_for_bestvalue = false;
          config->stats_coop_scip_lb_improvements++;
          if (config->verbosity >= 2) {
            char* from_val = toString(original_LB_goalvalue); char* to_val = toString(scip_lb);
            report->reportf("SCIP primal bound improved LB: %s -> %s\n", from_val, to_val);
            xfree(from_val), xfree(to_val);
          }
        }
      }

      // UB update from SCIP's LP dual bound (maximize mode: dual >= optimal)
      bool scip_ub_improved = false;
      Int original_UB_goalvalue = UB_goalvalue;
      if (updateUBGoalvalue(report, scip_dual_ub, UB_goalvalue, config->verbosity)) {
        config->stats_coop_scip_ub_improvements++;
        scip_ub_improved = true;
        if (config->verbosity >= 2) {
          char* from_val = toString(original_UB_goalvalue); char* to_val = toString(scip_dual_ub);
          report->reportf("SCIP LP bound tightened UB: %s -> %s\n", from_val, to_val);
          xfree(from_val), xfree(to_val);
        }
      }

      report->reportf(3, "pb_solver_status: %s\n", solverStatus_strs[get_pb_solver_status()]);
      report->reportf(3, "LB_goalvalue: %s\n", toString(LB_goalvalue));
      report->reportf(3, "UB_goalvalue: %s\n", toString(UB_goalvalue));
      if(get_pb_solver_status() == sst_sat && LB_goalvalue >= UB_goalvalue) {
        current_result = rt_optimized;
        exec_loop = false;
      } else if(scip_result == sst_unsat) {
        if(get_pb_solver_status() == sst_sat) {
          current_result = rt_optimized;
          exec_loop = false;
        } else if(get_pb_solver_status() == sst_unknown) {
          // report->reportf("SCIP found unsat where pb_solver_status == sst_unknown\n");
          // report->reportf("We don't trust SCIP result in this case, and switch off cooperative mode.\n");
          // config->use_cooperative_scip = false;
          current_result = rt_unsat;
          exec_loop = false;
        }
      }

      if(exec_loop) {
        if (num_improved_by_sat>0) {
          sat_current_time_budget = min(sat_current_time_budget * config->sat_time_budget_factor, config->given_time/2.0);
        }
        if (scip_result != sst_unknown || scip_ub_improved || num_improved_by_scip>0) {
          scip_current_time_budget = min(scip_current_time_budget * config->scip_time_budget_factor, config->given_time/2.0);
        }
        if (num_improved_by_sat==0 && !scip_ub_improved && num_improved_by_scip==0 &&
            config->double_unknown_budget_factor > 1.0) {
          sat_current_time_budget  = min(sat_current_time_budget * config->double_unknown_budget_factor, config->given_time/2.0);
          scip_current_time_budget = min(scip_current_time_budget * config->double_unknown_budget_factor, config->given_time/2.0);
          report->reportf(2, "Both SAT and SCIP timed out; boosting budgets to sat=%f scip=%f\n",
                          sat_current_time_budget, scip_current_time_budget);
        }
        sat_remaining_time_budget = sat_current_time_budget;
        report->reportf(1, "# improved by sat (lb/ub): %d, # improved by scip (lb): %d\n", num_improved_by_sat, num_improved_by_scip);
        num_improved_by_sat = 0;
        report->reportf(3, "SCIP triggered: next sat_remaining_time_budget=%f sat_budget=%f\n",
                        sat_remaining_time_budget, sat_current_time_budget);
        report->reportf(2, "SCIP returned: %s\n", solverStatus_strs[scip_result]);
        if(config->verbosity >= 2) {
          char* scip_lb_s = toString(scip_lb);
          char* dual_ub_s = toString(scip_dual_ub);
          // char* lb_s = toString(LB_goalvalue);
          // char* ub_s = toString(UB_goalvalue);
          // report->reportf(2, "After calling solveScip for cooperative mode with LB_goalvalue: %s, UB_goalvalue: %s\n", lb_s, ub_s);
          // xfree(lb_s);
          // xfree(ub_s);
          report->reportf(2, "SCIP primal LB: %s, dual UB: %s\n", scip_lb_s, dual_ub_s);
          xfree(scip_lb_s);
          xfree(dual_ub_s);
        }
        // scip_coop_stagecount = 0;
      }
    }

    // Set next minimizationMode
    if (exec_loop) {
      minimizationMode = set_minimizationMode(report, config, sat_solver_result, consec_UNSAT, UB_goalvalue - LB_goalvalue);
      minimizationMode = get_pb_solver_status() == sst_unknown ? cmm_First_solve : minimizationMode;
    }
  }

  freeLinear(sc2_goal);   // only allocated in sc2 mode; NULL otherwise
  return current_result;
}


// return true if goal can be splitted into two stage maximization goal1 and goal2
bool PbSolver_Minimize::divideGoal(Linear* goalExp, vec<Linear*>& goals) {

  report->reportf(4, "Entering divideGoal\n");
  // Slice goal co-efficients
  int gsize = (*goalExp).size;

  // vec<Int> goal_Cs; vec<Lit> goal_ps;
  // for (int i = 0; i < gsize; i++)
  //   goal_Cs.push((*goalExp)(i)), goal_ps.push((*goalExp)[i]);

  vec<Int> goal_Cs(gsize); vec<Lit> goal_ps(gsize);

  for (int i = 0; i < goal_Cs.size(); i++)
    goal_Cs[i] = (*goalExp)(i), goal_ps[i] = (*goalExp)[i];

  for (int i = 0; i < goal_Cs.size(); i++) {
    //**/ report->reportf("CS[%d]: ",i),dump(config, goal_Cs[i]),report->reportf(", ");
    int digits = (int)log10(todouble(goal_Cs[i])) +1 ;
    int cut_line = max(0, digits > 7 ? (digits > 11 ? digits/2 -2 : digits/2 -1) : digits/2 ) ;    // half of digits of Cs[i]
                                                                                                   //**/ report->reportf("cut_line: %d ", cut_line);
    if(cut_line > 1) {
      const int dunit = 10;
      Int q = goal_Cs[i];
      Int r = 0;
      Int r_ref = 1;
      for ( int j = 0; j < cut_line; j++ ) {
        r = r + (q % dunit) * r_ref;
        r_ref *= dunit;
        q = q / dunit;
      }
      //**/ report->reportf("q,r,r_ref: "),dump(config, q),report->reportf(", "),dump(config, r),report->reportf(", "),dump(config, r_ref),report->reportf("\n");
      if(r > 0 && log10(todouble(r)) < cut_line-1) {
        assert(goal_Cs[i] - r > 0);
        assert(r > 0);
        Int newCs = goal_Cs[i] - r, additionalCs = r;
        //**/ if (config->verbosity >= 3)
        //**/   report->reportf("Goal[%d]: ", i), dump(config, goal_Cs[i]),report->reportf("*"),dump(config, goal_ps[i]),
        //**/   report->reportf(" into "),dump(config, newCs),report->reportf("*"),dump(config, goal_ps[i]),
        //**/   report->reportf(" + "),dump(config, additionalCs),report->reportf("*"),dump(config, goal_ps[i]),report->reportf("\n");
        goal_Cs[i] = newCs;
        goal_Cs.push(additionalCs);
        Lit p = goal_ps[i];
        goal_ps.push(p);
      } else if (log10(todouble(r_ref - r )) < cut_line-1) {
        Int newCs = goal_Cs[i] + r_ref - r, additionalCs = r - r_ref;
        //**/ if (config->verbosity >= 3)
        //**/   report->reportf("Goal[%d]: ", i), dump(config, goal_Cs[i]),report->reportf("*"),dump(config, goal_ps[i]),
        //**/   report->reportf(" into "),dump(config, newCs),report->reportf("*"),dump(config, goal_ps[i]),
        //**/   report->reportf(" + "),dump(config, additionalCs),report->reportf("*"),dump(config, goal_ps[i]),report->reportf("\n");
        goal_Cs[i] = newCs;
        goal_Cs.push( Int(0)-additionalCs );
        Lit p = goal_ps[i];
        goal_ps.push(~p);
      }
      //**/else {
      //**/ if (config->verbosity >= 2)
      //**/   report->reportf("Preserved Goal[%d]: ", i), dump(config, goal_Cs[i]),report->reportf("*"),dump(config, goal_ps[i]),report->reportf("\n");
      //**/}
    }
  }
  int shrink = goal_Cs.size() - gsize;
  if(shrink > 0) {
    config->stats_coeff_separations += shrink;
    if (config->verbosity >= 3)
      report->reportf("Num of divided co-efficients: %d\n", shrink);
  }

  //**/ for (int i = 0; i < goal_ps.size(); i++)
  //**/   report->reportf("Goal2[%d]: ", i), dump(config, goal_Cs[i]),report->reportf("*"),dump(config, goal_ps[i]),report->reportf("\n");

  // make increasing order and divide coefficients
  vec<Pair<Int,Lit> > Csps(goal_Cs.size());
  for (int i = 0; i < goal_Cs.size(); i++ ) Csps[i] = Pair_new(goal_Cs[i], goal_ps[i]);
  assert(goal_ps.size() == goal_Cs.size());

  sort(Csps);
  if (config->verbosity >= 3) {
    report->reportf(2, "Goal3: ");
    dump(config, 2, &index2name, Csps);
      // for (int i = 0; i < Csps.size(); i++)
      //   report->reportf("%d: ", i),dump(config, Csps[i].fst),report->reportf("*"),dump(config, &index2name, Csps[i].snd),report->reportf(", ");
    report->reportf(2, "\n");
  }

  // devide small and big coefficients, if possible, and take GCD for latter
  Int sum = 0;
  vec<Pair<int, Int> > cuttingCandPoint;
  for (int i = 0; i < Csps.size(); i++) {
    if (i != 0 && sum < Csps[i].fst) cuttingCandPoint.push(Pair<int, Int>(i,sum));
    sum += Csps[i].fst;
  }

  //**/ report->reportf("cuttingCandPoints: ");
  //**/ for (int i = 0; i < cuttingCandPoint.size(); i++)
  //**/   report->reportf(" %d %s, ",cuttingCandPoint[i].fst, toString(cuttingCandPoint[i].snd));

  if (cuttingCandPoint.size() <= 0) {
    goals.push(goalExp);
    report->reportf(4,"Leaving divideGoal 1\n");
    return false;
  }

  int idx = cuttingCandPoint.size() - 1;
  int cp = cuttingCandPoint[idx].fst;
  Int sumSmalls = cuttingCandPoint[idx].snd;
  Int smallestDifference = Int_MAX;
  bool possible = false;

  if (config->verbosity >= 3)
    report->reportf("candidate of cutting points: %d\n", cp);
  for (int i = Csps.size() - 1; i >= 1; i--) {
    if(i == cp) {
      //**/ report->reportf(" sumSmalls, smallestDifference:0%s %s\n", toString(sumSmalls), toString(smallestDifference));
      if (sumSmalls < smallestDifference && sumSmalls < Csps[cp].fst) {
        possible = true;
        break;
      } else if (idx <= 0) {
        break;
      } else {
        idx--;
        cp = cuttingCandPoint[idx].fst;
        sumSmalls = cuttingCandPoint[idx].snd;
      }
    }
    Int diff = Csps[i].fst - Csps[i-1].fst;
    if (diff != 0 && smallestDifference > diff )
      smallestDifference = diff ;
  }

  if (possible) {
    vec<Int> small_Cs, big_Cs;
    vec<Lit> small_ps, big_ps;
    for (int i = 0; i < cp; i++)
      small_Cs.push(Csps[i].fst), small_ps.push(Csps[i].snd);
    for (int i = cp; i < Csps.size(); i++)
      big_Cs.push(Csps[i].fst), big_ps.push(Csps[i].snd);

    //GCD for big
    Int gcd_val = big_Cs[0];
    for (int i = 1; i < big_Cs.size(); i++)
      gcd_val = gcdg(gcd_val, big_Cs[i]);
    config->stats_goal_gcd_seq.push(gcd_val);

    if (gcd_val != 1) {
      config->stats_goal_shrink++;
      if (config->verbosity >= 1)
        report->reportf("goal1 is divided by "),dump(config, gcd_val),report->reportf("\n");
      for (int i = 0; i < big_Cs.size(); i++)
        big_Cs[i] /= gcd_val;
    }

    if (config->verbosity >= 2) {
      char *smallest = toString(big_Cs[0]),
           *biggest = toString(big_Cs[big_Cs.size()-1]),
           *with = toString(gcd_val);
      report->reportf("Next goal: size: %d smallest: %s biggest: %s multiplied with %s\n", big_Cs.size(), smallest, biggest, with );
      xfree(smallest), xfree(biggest), xfree(with);

      smallest = toString(small_Cs[0]), biggest = toString(small_Cs[small_Cs.size()-1]);
      report->reportf("Rest goal: size: %d smallest: %s biggest: %s\n", small_Cs.size(), smallest, biggest );
      xfree(smallest), xfree(biggest);
    }
    //     xfree(goal1), xfree(goal2);
    Linear* goal_big = new (xmalloc<char>(sizeof(Linear) + big_Cs.size()*(sizeof(Lit) + sizeof(Int))))
      Linear(big_ps, big_Cs, Int_MIN, Int_MAX, lit_Undef);
    report->reportf(3,"Added big goal\n");
    goals.push(goal_big);
    Linear* goal_small = new (xmalloc<char>(sizeof(Linear) + small_Cs.size()*(sizeof(Lit) + sizeof(Int))))
      Linear(small_ps, small_Cs, Int_MIN, Int_MAX, lit_Undef);
    //**/ report->reportf("divided(big): "),dump(config, big_ps,big_Cs,false),report->reportf("\n"),report->reportf("divided(small): "),dump(config, small_ps,small_Cs,false),report->reportf("\n");

    divideGoal(goal_small, goals);
  } else {
    goals.push(goalExp);
  }

  report->reportf(3,"Leaving divideGoal 2\n");
  return possible;
}

solverStatus PbSolver_Minimize::coding(vec<Lit>& assump_ps) {
  if (config->use_scip) return sst_unknown;

  report->reportf(2, "assumptions before coding: ");
  for(int i = 0; i < assump_ps.size(); i++) {
    Lit l = assump_ps[i];
    report->reportf(2, "%s%s, ", sign(l) ? "~" : "", index2name[var(l)]);
  }
  report->reportf(2, "\n");

  if (!ok) {set_pb_solver_status(sst_unsat);
    report->reportf(2, "pb_solver_status: %s\n", solverStatus_strs[get_pb_solver_status()]);
    return get_pb_solver_status();} // unsat

  minimization_mode = false;
  pb_n_vars = nVars();
  pb_n_constrs = constrs.size();
  if(pb_n_constrs>0) propagate();

  // Current SCIP will fail if there are assumptions, so skip if there are assumptions
  if(checkAssumptions()) {
    config->use_cooperative_scip = false;
    config->use_scip = false;
  }

  if (config->use_cooperative_scip) initScipEnv();
  checkScipPrecisionSafety();

  //  add counstraints for goal when config->goal_ub or config->gaol_lb are supplied
  if(config->goal_ub != Int_MAX || config->goal_lb != Int_MIN) {
    vec<Lit> goal_ps; vec<Int> goal_Cs;
    for(int i = 0; i < goal->size; i++) goal_ps.push((*goal)[i]), goal_Cs.push((*goal)(i));
    if ( config->goal_ub != Int_MAX ) {
      Int LB = InvGoalLB(config->goal_ub);
      if (!config->repeated_optimizations) {
        if (!addConstr(false, goal_ps, goal_Cs, LB, 1 )) {   // 1: goal >=  InvGoalLB(ub)
          set_pb_solver_status(sst_unsat);
          report->reportf(2, "pb_solver_status: %s\n", solverStatus_strs[get_pb_solver_status()]);
          return get_pb_solver_status();
        }
      } else {
        char        buf[30];
        sprintf(buf, "@goal_constraint%d", n_goal_constraints++);
        Lit assump_lit = Lit(getVar(buf));
        if (addConstr(false, goal_ps, goal_Cs, LB, 1, Int_MAX, 0, assump_lit)) {   // 1: goal >=  InvGoalLB(ub)
          assump_ps.push(assump_lit);
        } else assert(false);
      }
    }
    if ( config->goal_lb != Int_MIN ) {
      Int UB = InvGoalUB(config->goal_lb);
      if (!config->repeated_optimizations) {
        if (!addConstr(false, goal_ps, goal_Cs, UB, -1 )) {   // -1: goal <= InvGoalUB(lb)
          set_pb_solver_status(sst_unsat);
          report->reportf(2, "pb_solver_status: %s\n", solverStatus_strs[get_pb_solver_status()]);
          return get_pb_solver_status();
        }
      } else {
        char        buf[30];
        sprintf(buf, "@goal_constraint%d", n_goal_constraints++);
        Lit assump_lit = Lit(getVar(buf));
        if (addConstr(false, goal_ps, goal_Cs, UB, -1, Int_MAX, 0, assump_lit)) {   // -1: goal <= InvGoalUB(lb)
          assump_ps.push(assump_lit);
        } else assert(false);
      }
    }
  }

  // For cooperative SCIP: sync all constraints before convertPbs() clears constrs
  if (config->use_cooperative_scip) initScipForCoop();

  // SCIP presolver: run before CNF coding to obtain bounds or prove optimality/infeasibility.
  if (config->use_cooperative_scip && config->scip_as_presolve > 0.0 && goal != NULL) {
    vec<Lit> empty_assump;
    Int ps_lb = Int_MIN, ps_ub = Int_MAX;
    double ps_scip_gap = -1.0;
    report->reportf(1, "Running SCIP presolver (time limit: %g sec)...\n", config->scip_as_presolve);
    solverStatus ps_result = solveScip(empty_assump, config->scip_as_presolve,
                                       goal, Int_MIN, &ps_lb, &ps_ub, Int_MAX, &ps_scip_gap,
                                       &presolve_nnodes);
    report->reportf(1, "SCIP presolver: %s\n", solverStatus_strs[ps_result]);

    if (ps_result == sst_unsat) {
      set_pb_solver_status(sst_unsat);
      return get_pb_solver_status();
    }
    if (ps_result == sst_best) {
      // Optimal found: best_model/best_goalvalue set by solveScip()
      // sst_best is valid because presolver ran with full 'goal'
      set_pb_solver_status(sst_best);
      return get_pb_solver_status();
    }
    if (ps_lb != Int_MIN) {
      // Feasible solution found (timeout with solution):  sst_sat set by solveScip()
      set_pb_solver_status(sst_sat);
      has_model_for_bestvalue = true;
    }
    presolve_lb       = ps_lb;
    presolve_ub       = ps_ub;
    presolve_scip_gap = ps_scip_gap;
  }

  // Convert constraints:
  if (config->verbosity >= 2) report->reportf("Converting %d PB-constraints to clauses...\n", constrs.size());

  if (clausifier == NULL)   // keep the existing one (and its vmap) on a repeated 'coding()'
    clausifier = new Clausifier(sat_solver, config, report);
  if (!convertPbs(0)){
    if (interrupted) { set_pb_solver_status(sst_unknown); return get_pb_solver_status(); }
    assert(!ok); set_pb_solver_status(sst_unsat); return get_pb_solver_status();
  } // unsat

  sat_solver->setVerbosity(config->sat_verbosity);

  if(!ok) set_pb_solver_status(sst_unsat);
  report->reportf(2, "pb_solver_status: %s\n", solverStatus_strs[get_pb_solver_status()]);
  return get_pb_solver_status();
}

bool PbSolver_Minimize::findModelForBestValueBySCIP(Linear* goalExp, Int& LB_goalvalue, double budget) {
    vec<Lit> empty_assump;
    Int scip_lb = Int_MIN, scip_ub = Int_MAX;
    report->reportf(2, "findModelForBestValue: SCIP time limit: %f sec\n", budget);
    solverStatus res = solveScip(empty_assump, budget, goalExp, LB_goalvalue - 1, &scip_lb, &scip_ub);
    if (res == sst_best) {
      LB_goalvalue = evalGoal(*goalExp);
      best_goalvalue = evalGoal(*original_goal);
      has_model_for_bestvalue = true;
      report->reportf(2, "in findModelForBestValue (SCIP) ");
      printBestGoalValue();
      report->reportf(1, "found by SCIP\n");
      return true;
    }
    report->reportf(2, "findModelForBestValue: SCIP failed\n");
    return false;
}

bool PbSolver_Minimize::findModelForBestValueBySAT(Linear* goalExp, Int& LB_goalvalue, vec<Lit>& assump_ps, int stageCount, double budget) {
    vec<Lit> goal_ps;
    vec<Int> goal_Cs;
    for(int i = 0; i < goalExp->size; i++) goal_ps.push((*goalExp)[i]), goal_Cs.push((*goalExp)(i));
    assert(LB_goalvalue != Int_MIN);

    // TODO: if the last minimizationMode is cmm_Sc2, we don't need to add the goal constraint
    if(addGoalConstraint(goal_ps, goal_Cs, LB_goalvalue, assump_ps, stageCount)) {
        assert(false);
    }
    report->reportf(2, "findModelForBestValue: SAT time limit: %f sec\n", budget);
    solverStatus sat_res = sat_solver_invoke(assump_ps, budget);
    if (sat_res == sst_sat) {
      commitBestModel();
      LB_goalvalue = evalGoal(*goalExp);
      best_goalvalue = evalGoal(*original_goal);
      report->reportf(2, "in findModelForBestValue\n");
      printBestGoalValue();
      report->reportf(1, "found by SAT\n");
      has_model_for_bestvalue = true;
      return true;
    }
    // SAT timed out
    report->reportf(2, "findModelForBestValue: SAT timed out\n");
    return false;
}

// need taking car the case pb_solver_status == sst_unsat or sst_sat.
void PbSolver_Minimize::findModelForBestValue(Linear* goalExp, Int& LB_goalvalue, vec<Lit>& assump_ps, int stageCount) {
  report->reportf(2, "findModelForBestValue: pb_solver_status=%s\n", solverStatus_strs[get_pb_solver_status()]);
  if(!has_model_for_bestvalue) {
    double elapsed = cpuTime() - solve_start_time;
    double remaining = config->given_time <= 0 ? 1e20 : std::max(100.0, config->given_time - elapsed);
    double total_budget = scip_current_time_budget + sat_current_time_budget;
    double scip_time_limit = remaining * scip_current_time_budget / total_budget;
    double sat_time_limit  = remaining * sat_current_time_budget  / total_budget;

    if (config->use_cooperative_scip && scip_current_time_budget >= sat_current_time_budget) {
      // SCIP-first: SCIP has larger budget and has contributed → try SCIP, fall back to SAT.
      report->reportf(2, "findModelForBestValue: trying SCIP first (scip_budget=%f >= sat_budget=%f)\n",
                        scip_current_time_budget, sat_current_time_budget);
      if (!findModelForBestValueBySCIP(goalExp, LB_goalvalue, scip_time_limit)) {
        report->reportf(2, "findModelForBestValue: SCIP timed out, falling back to SAT (no limit)\n");
        if (!findModelForBestValueBySAT(goalExp, LB_goalvalue, assump_ps, stageCount, 1e20)) {
          report->reportf(1, "Internal Error\n");
          throw std::runtime_error("findModelForBestValue: Internal Error\n");
        }
      }
    } else if (config->use_cooperative_scip) {
      // SAT-first: SAT has larger budget → try SAT with time limit, fall back to SCIP.
      report->reportf(2, "findModelForBestValue: trying SAT first (sat_budget=%f > scip_budget=%f)\n",
                        sat_current_time_budget, scip_current_time_budget);
      if (!findModelForBestValueBySAT(goalExp, LB_goalvalue, assump_ps, stageCount, sat_time_limit)) {
        report->reportf(2, "findModelForBestValue: SAT timed out, falling back to SCIP (no limit)\n");
        if (!findModelForBestValueBySCIP(goalExp, LB_goalvalue, 1e20)) {
          report->reportf(1, "Internal Error\n");
          throw std::runtime_error("findModelForBestValue: Internal Error\n");
        }
      }
    } else {
      report->reportf(2, "findModelForBestValue: trying SAT\n");
      if (!findModelForBestValueBySAT(goalExp, LB_goalvalue, assump_ps, stageCount, 1e20)) {
        report->reportf(1, "Internal Error\n");
        throw std::runtime_error("findModelForBestValue: Internal Error\n");
      }
    }

  }
}

//=================================================================================================
// Main solver/optimizer:
solverStatus PbSolver_Minimize::solve(vec<Lit>& assump_ps)
{
  if (config->use_cooperative_scip && config->scip_as_presolve > 0.0 &&
      get_pb_solver_status() == sst_best) return sst_best;
  if (config->use_scip) {
    solverStatus st = solveScip(assump_ps);
    set_pb_solver_status(st);
    return st;
  }
  assert(goal != NULL);
  if (solve_reentry) {
    if (!config->repeated_optimizations ) {
      report->reportf(1, "Error: solve() cannot be called more than once when config->repeated_optimizations is true. (use -aslaf option)\n");
      assert(false);
    }
  }

  double start_time = cpuTime();
  solve_start_time = start_time;
  scip_current_time_budget = min(config->scip_time_budget, config->given_time/2.0);
  sat_current_time_budget  = min(config->sat_time_budget, config->given_time/2.0);
  if (config->use_cooperative_scip && config->scip_as_presolve > 0.0 &&
      ((presolve_lb != Int_MIN || presolve_ub != Int_MAX) || presolve_nnodes > 1)) {
    bool gap_promising = config->scip_promising_gap_threshold >= 0.0 &&
                         presolve_scip_gap >= 0.0 && presolve_scip_gap < config->scip_promising_gap_threshold;
    bool node_promising = presolve_nnodes > 1;
    if (gap_promising || node_promising) {
      scip_current_time_budget = config->scip_promising_boost_budget;
      if (config->scip_promising_single_stage) {
        config->single_stage_mode = true;
        if (gap_promising && node_promising)
          report->reportf(1, "SCIP presolver gap=%.1f%% < threshold=%.1f%%, nodes=%d > 1; boosting initial SCIP budget to %.1f sec; switching to single-stage mode\n",
                          presolve_scip_gap, config->scip_promising_gap_threshold, presolve_nnodes, scip_current_time_budget);
        else if (gap_promising)
          report->reportf(1, "SCIP presolver gap=%.1f%% < threshold=%.1f%%; boosting initial SCIP budget to %.1f sec; switching to single-stage mode\n",
                          presolve_scip_gap, config->scip_promising_gap_threshold, scip_current_time_budget);
        else
          report->reportf(1, "SCIP presolver nodes=%d > 1; boosting initial SCIP budget to %.1f sec; switching to single-stage mode\n",
                          presolve_nnodes, scip_current_time_budget);
      } else {
        if (gap_promising && node_promising)
          report->reportf(1, "SCIP presolver gap=%.1f%% < threshold=%.1f%%, nodes=%d > 1; boosting initial SCIP budget to %.1f sec\n",
                          presolve_scip_gap, config->scip_promising_gap_threshold, presolve_nnodes, scip_current_time_budget);
        else if (gap_promising)
          report->reportf(1, "SCIP presolver gap=%.1f%% < threshold=%.1f%%; boosting initial SCIP budget to %.1f sec\n",
                          presolve_scip_gap, config->scip_promising_gap_threshold, scip_current_time_budget);
        else
          report->reportf(1, "SCIP presolver nodes=%d > 1; boosting initial SCIP budget to %.1f sec\n",
                          presolve_nnodes, scip_current_time_budget);
      }
    } else {
      scip_current_time_budget = config->scip_time_budget * config->scip_time_budget_factor;
    }
    if (config->given_time > 0)
      scip_current_time_budget = min(scip_current_time_budget, config->given_time / 2.0);
  }
  solve_reentry = true;
  minimization_mode = true;

  if (config->convert_goal != ct_Undef)
    config->convert = config->convert_goal;
  config->cc_sort = config->cc_sort_goal;
  // config->sort_thres *= config->goal_bias;
  config->bdd_thres = config->goal_bdd_thres;
  config->sort_thres = config->goal_sort_thres;
  config->bdd_max_constraints = config->goal_bdd_max_constraints;
  config->bdd_max_adder_cost = config->goal_bdd_max_adder_cost;
  config->sort_max_adder_cost = config->goal_sort_max_adder_cost;
  config->convert_gpw = config->convert_gpw_goal;

  bool divide = false;
  if(!config->single_stage_mode) {
    divide = divideGoal(goal, goals);
    if (config->verbosity >= 1) report->reportf("total stages of optimization mode: %d\n", goals.size());
  }

  ResultT result = rt_unknown;
  has_model_for_bestvalue = false;
  if(divide) {
    Int LB_goalvalue, UB_goalvalue;
    for(int stageCount=1; stageCount<=goals.size(); stageCount++) {
      config->stats_goal_stages++;
      if (config->verbosity >= 1) report->reportf("multi stage optimization mode: %d-th step\n", stageCount);
      if (config->verbosity >= 2)  // temporally 2 => 0
        report->reportf(2, "Maximizing current goal: "),dump(config, 2, &index2name, goals[stageCount-1]),report->reportf(2, "\n");

      LB_goalvalue = Int_MIN, UB_goalvalue = Int_MAX;
      result = maximizeGoal(goals[stageCount-1], LB_goalvalue, UB_goalvalue, assump_ps, stageCount, stageCount==goals.size());

      if(config->verbosity >=1 && result == rt_optimized) {
        char* best_goalvalue_str = toString(get_best_goalvalue());
        report->reportf("Optimized the %d-th goal (%d) with best_original_goalvalue: %s\n", stageCount, result, best_goalvalue_str);
        xfree(best_goalvalue_str);
      }
      if (result == rt_unsat) {
        set_pb_solver_status(sst_unsat);
        report->reportf(2, "pb_solver_status: %s\n", solverStatus_strs[get_pb_solver_status()]);
        config->stats_minimizing_solver_time += (cpuTime() - start_time);
        return get_pb_solver_status();
      }
      goals_best_values.push(LB_goalvalue);
      // check model is feasible for each completed stages
      if(config->model_check) {
        for(int i=0; i<=stageCount-1; i++) {
          report->reportf(2, "check whether model is feasible for %d-th stage:", i+1);
          report->reportf(3,"goal %d: ", i+1), dump(config, 3, &index2name, goals[i]), report->reportf(3,"in [%s, %s]\n",
                           toString(goals_best_values[i]), toString(goals_best_values[i]));
          if(has_model_for_bestvalue) {
            if(!goalValueCheck(*goals[i], goals_best_values[i])) {
              report->reportf("Error: model is not feasible for %d-th stage\n", i+1);
              throw std::runtime_error("Error: model is not feasible\n");
            }
          }
        }
      }
      // After a non-final stage, add a permanent constraint fixing the current stage's optimal value
      if (result == rt_optimized && stageCount < goals.size()) {
        Linear* goalExp = goals[stageCount-1];
        vec<Lit> goal_ps; vec<Int> goal_Cs;
        for(int i = 0; i < goalExp->size; i++) goal_ps.push((*goalExp)[i]), goal_Cs.push((*goalExp)(i));
        if(addGoalConstraint(goal_ps, goal_Cs, LB_goalvalue, assump_ps, stageCount)) {
          assert(false); // should not be UNSAT at the optimal value
        }
        if (!config->repeated_optimizations) {
          Lit p = assump_ps.last(); assump_ps.pop();
          sat_solver->addUnit(p);
        }
        // Sync the stage-completion constraint to cooperative SCIP as equality (lb=ub=V).
        // The equality goals[N]=V_N is valid for all subsequent stages because V_N is the
        // proven maximum and subsequent stages require goals[N]>=V_N, making it an equality.
        if (config->use_cooperative_scip) {
          // SCIP uses double precision: two independent precision checks guard against
          // wrong models being accepted from the solution candidate storage.
          // Check A: feasibility tolerance (feastol * |rhs|) must be < 1 so that
          //   SCIP cannot accept solutions violating the integer equality by >= 1 unit.
          // Check B: sum(|c_i|) must be < 2^53 so that the constraint activity sum
          //   is exactly representable in double.
          bool stage_unsafe = false;
          double feastol = getScipFeastol();
          if (feastol > 0.0) {
            double abs_lb = todouble(LB_goalvalue);
            if (abs_lb < 0.0) abs_lb = -abs_lb;
            if (feastol * abs_lb >= 1.0) stage_unsafe = true;
          }
          if (!stage_unsafe) {
            const Int coef_limit((double)(1LL << 53));
            Int coef_sum = 0;
            const Int zero = 0;
            for (int j = 0; j < goal_Cs.size() && !stage_unsafe; j++) {
              Int ci = goal_Cs[j];
              coef_sum += ci < zero ? -ci : ci;
              if (coef_sum >= coef_limit) stage_unsafe = true;
            }
          }
          if (stage_unsafe) {
            config->use_cooperative_scip = false;
            report->reportf(1, "Disabling cooperative SCIP for subsequent stages: "
                               "stage constraint precision unsafe\n");
          } else {
            addScipLinearConstraint(goal_ps, goal_Cs, LB_goalvalue, LB_goalvalue);
          }
        }
        // The stage's optimal was found. If a model was obtained (best_model is populated),
        // reset pb_solver_status to sst_sat so the next stage's maximizeGoal correctly
        // initialises its lower bound from the known model and selects the right
        // minimisation mode.  Without this, a UNSAT return from the SAT solver during
        // the current stage's search would leave pb_solver_status == sst_unsat, causing
        // the next stage to treat the problem as if no solution exists yet.
        // Note: if the stage was trivially optimized (LB==UB from literal bounds) without
        // any SAT calls, best_model may be empty -- in that case we leave pb_solver_status
        // unchanged so that evalGoal() is not called on an empty model at the next stage.
        if (best_model.size() > 0) set_pb_solver_status(sst_sat);
        // report->reportf("reset_printed_goalvalue "), dump(config, last_printed_goalvalue), report->reportf(" -> Int_MAX\n");
        // last_printed_goalvalue = Int_MAX;

        // reset time budgets, check efficiency
        // scip_current_time_budget = config->scip_time_budget;
        // sat_current_time_budget  = config->sat_time_budget;
      }
    }
    findModelForBestValue(goals[goals.size()-1], LB_goalvalue, assump_ps, goals.size());
  } else {
    Int LB_goalvalue = Int_MIN, UB_goalvalue = Int_MAX;
    // above code possiblly improved as follows:
    // Int LB_goalvalue = evalGoalLB(goal), UB_goalvalue = evalGoalUB(goal);
    if(config->goal_ub < UB_goalvalue) { // report->reportf("config->goal_ub"),dump(config, config->goal_ub),report->reportf("\n");
      LB_goalvalue = InvGoalLB(config->goal_ub);
    }
    if(config->goal_lb > Int_MIN) { // report->reportf("config->goal_lb"),dump(config, config->goal_lb),report->reportf("\n");
      UB_goalvalue = InvGoalUB(config->goal_lb);
    }
    // SCIP in presolve minimize goal, it is impossible to use presolve_ub as UB_goalvalue.
    // // Apply presolver dual bound as initial UB (single-stage only: presolve used full 'goal')
    if (config->use_cooperative_scip && config->scip_as_presolve > 0.0 &&
        presolve_ub != Int_MAX && presolve_ub < UB_goalvalue) {
      report->reportf(2, "Use UB found in presolve\n");
      UB_goalvalue = presolve_ub;
    }
    if (config->use_cooperative_scip && config->scip_as_presolve > 0.0 &&
        presolve_lb != Int_MIN && presolve_lb > LB_goalvalue) {
      report->reportf(2, "Use LB found in presolve\n");
      LB_goalvalue = presolve_lb;
    }

    result = maximizeGoal(goal, LB_goalvalue, UB_goalvalue, assump_ps, 1, true);
    if(result == rt_optimized) {
      findModelForBestValue(goal, LB_goalvalue, assump_ps, 1);
    }
  }



  if (result == rt_optimized)   set_pb_solver_status(sst_best);
  else if (result == rt_unsat)  set_pb_solver_status(sst_unsat);
  else if (result == rt_sat )   set_pb_solver_status(sst_sat);

  report->reportf(2, "pb_solver_status: %s\n", solverStatus_strs[get_pb_solver_status()]);
  config->stats_minimizing_solver_time += (cpuTime() - start_time);
  return get_pb_solver_status();
}

solverStatus PbSolver_Counting::solve(vec<Lit>& assump_ps)
{
  double start_time = cpuTime();

  bool interestMode = (config->command == cmd_InterestSolutions);

  if (config->use_scip) {
    ResultT result = enumerateScip(interestMode, assump_ps);
    if      (result == rt_unknown) set_pb_solver_status(sst_unknown);
    else if (result == rt_unsat)   set_pb_solver_status(sst_unsat);
    else if (result == rt_sat)     set_pb_solver_status(sst_sat);
    else if (result == rt_all)     set_pb_solver_status(sst_all);
    else assert(false);
    config->stats_counting_solver_time += (cpuTime() - start_time);
    return get_pb_solver_status();
  }

    // Freeze all input variables  (for SatELite and Simplification solvers):
    for (int i = 0; i < named_vars.size(); i++)
      //report->reportf("FreeZing %d\n",named_vars[i]),
      sat_solver->freeze(named_vars[i]);

    sat_solver->setVerbosity(config->sat_verbosity);

    // AllModels mode or InterestModels mode
    ResultT result = enumerate( interestMode, assump_ps );
    if (result == rt_unknown)    set_pb_solver_status(sst_unknown);
    else if (result == rt_unsat) set_pb_solver_status(sst_unsat);
    else if (result == rt_sat) set_pb_solver_status(sst_sat);
    else if (result == rt_all) set_pb_solver_status(sst_all);
    else assert(false);

    config->stats_counting_solver_time += (cpuTime() - start_time);
    return get_pb_solver_status();
}

//=================================================================================================

ResultT PbSolver_Counting::enumerate(bool interestMode, vec<Lit>& assump_ps)
{
  ResultT result = rt_unknown;

  if (interestMode) {
    assert(pb_i_vars != -1);
  }
  bool     exec_loop = true;  // bool sat = false;
                              //int      assump_litn;
  // Enumerate over named (PB) vars only; interest vars are the first 'pb_i_vars' named ones.
  int vars = named_vars.size();
  if(interestMode) vars = pb_i_vars;

  while (exec_loop) {
    if (sat_solver->solve(assump_ps)) {   // Initially asump_ps is empty
                                          // sat returned
                                          //       if (config->verbosity >= 3) report->reportf("process after sat\n");

      result = rt_sat;
      config->stats_sat_calls++;
      set_pb_solver_status(sst_sat);

      commitBestModel();

      vec<Lit>    ban;
      number_models++;

      report->reportf("MODEL# %d:", number_models);
      int i = 0;
      for (int k = 0; k < vars; k++) {
        Var x = named_vars[k];
        ban.push(Lit(x, bestModelVal(x)));
        if (config->model_out && (!config->positive_mode || bestModelVal(x))) {
          if (i % 2000 >= 1999) printf("\nv");
          i++;
          report->reportf(" %s%s", bestModelVal(x)?"":"-", index2name[x]);
        }
      }
      report->reportf("\n");
      sat_solver->addClause(ban);
      if (interrupted) { exec_loop = false; break; }

    } else { // unsat returnedassump_ps.clear();

      //       if (config->verbosity >= 3) report->reportf("process after unsat\n");
      config->stats_unsat_calls++;

      if(result == rt_sat)          result = rt_all;
      else if(result == rt_unknown) result = rt_unsat;
      else assert(false);
      break;
      // exec_loop = false;
    }
  }
  return result;
}


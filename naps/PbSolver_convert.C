/******************************************************************************[PbSolver_convert.C]
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

#include "PbSolver.h"
#include "Hardware.h"
#include "Debug.h"

Formula PbSolver::rewriteCCbySorter(Linear& c, int stage)
{
  assert(c.lo != Int_MIN);
  if(c(c.size-1) == 1) {
    assert(c.lo <= Int(c.size));
    assert(c.hi == Int_MAX || c.hi <= Int(c.size));

    vec<int> dummy;
    return buildConstraint(c, dummy, INT_MAX, stage);

  } else return _undef_ ;
}

Formula PbSolver::tryConvertBdd(Linear& c, int max_cost, int stage)
{
  Pair<Interval<Int>, Interval<Int>> intervals =
    Pair<Interval<Int>, Interval<Int>>(Interval_new(Int_MIN, Int_MIN), Interval_new(Int_MAX, Int_MAX));
  Formula result = convertToBdd(c, intervals, convert_base_bdd,
    config->convert_bdd_decomposition,
    (stage != 0) && config->convert_goal_reusing,
    max_cost, stage);
  if (stage != 0) report->reportf(3, "Bdd: base: "), dump(config, 3, convert_base_bdd), report->reportf(3, "\n");
  return result;
}

Formula PbSolver::tryConvertSort(Linear& c, int max_cost, int stage)
{
  Formula result = buildConstraint(c, convert_base_sort, max_cost, stage);
  if (stage != 0) report->reportf(2, "Sorter: base: "), dump(config, 2, convert_base_sort), report->reportf(2, "\n");
  return result;
}

Formula PbSolver::tryConvertAdder(Linear& c)
{
  return linearAddition(c);
}

Formula PbSolver::convertOneConstraint(Linear& c, ConvertT convert,
  int& bdd_max_adder_cost, int& sort_max_adder_cost, int stage)
{
  int adder_cost = estimatedAdderCost(c);
  report->reportf(2, "estimatedAdderCost: %d\n", adder_cost);

  switch (convert) {
  case ct_Sorters:
    report->reportf(2, "Converting by sorters\n");
    return tryConvertSort(c, INT_MAX, stage);
  case ct_Adders:
    report->reportf(2, "Converting by adders\n");
    return tryConvertAdder(c);
  case ct_BDDs:
    report->reportf(2, "Converting by bdds\n");
    return tryConvertBdd(c, INT_MAX, stage);

  case ct_Mixed:
  case ct_Mixed2:
  case ct_Mixed3: {
    Formula result = _undef_;
    // Step 1: Try BDD (ct_Mixed and ct_Mixed2 only)
    if (convert == ct_Mixed || convert == ct_Mixed2) {
      if (bdd_max_adder_cost == 0) {
        report->reportf(2, "Try to convert by bdds\n");
        result = tryConvertBdd(c, (int)(adder_cost * config->bdd_thres), stage);
      } else if (adder_cost <= bdd_max_adder_cost) {
        report->reportf(2, "Try to convert by bdds\n");
        result = tryConvertBdd(c, (int)(bdd_max_adder_cost * config->bdd_thres), stage);
      }
      if (result == _undef_) {
        if (stage == 0 && bdd_max_adder_cost == 0 && adder_cost > 0)
          bdd_max_adder_cost = adder_cost - 1;
        // report->reportf(2, "Switching to sorting network\n");
      }
    }
    // Step 2: Try Sort
    if (result == _undef_) {
      if (convert == ct_Mixed2) {
        report->reportf(2, "Converting by sorters\n");
        result = tryConvertSort(c, INT_MAX, stage);
      } else if (sort_max_adder_cost == 0) {
        report->reportf(2, "Try to convert by sorters\n");
        result = tryConvertSort(c, (int)(adder_cost * config->sort_thres), stage);
      } else if (adder_cost <= sort_max_adder_cost) {
        report->reportf(2, "Try to convert by sorters\n");
        result = tryConvertSort(c, (int)(sort_max_adder_cost * config->sort_thres), stage);
      }
      if (result == _undef_) {
        if (stage == 0 && sort_max_adder_cost == 0 && adder_cost > 0)
          sort_max_adder_cost = adder_cost - 1;
        // report->reportf(2, "Switching to adder coding\n");
      }
    }
    // Step 3: Adder fallback
    if (result == _undef_) {
      report->reportf(2, "Converting by adders\n");
      result = tryConvertAdder(c);
    }
    return result;
  }

  case ct_Mixed4:
  case ct_Mixed5:
  case ct_Mixed6: {
    Formula result = _undef_;
    // Step 1: Try Sort (ct_Mixed4 and ct_Mixed5 only)
    if (convert == ct_Mixed4 || convert == ct_Mixed5) {
      if (sort_max_adder_cost == 0) {
        report->reportf(2, "Try to convert by sorters\n");
        result = tryConvertSort(c, (int)(adder_cost * config->sort_thres), stage);
      } else if (adder_cost <= sort_max_adder_cost) {
        report->reportf(2, "Try to convert by sorters\n");
        result = tryConvertSort(c, (int)(sort_max_adder_cost * config->sort_thres), stage);
      }
      if (result == _undef_) {
        if (stage == 0 && sort_max_adder_cost == 0 && adder_cost > 0)
          sort_max_adder_cost = adder_cost - 1;
        report->reportf(2, "Switching to BDD network\n");
      }
    }
    // Step 2: Try BDD
    if (result == _undef_) {
      if (convert == ct_Mixed5) {
        report->reportf(2, "Converting by bdds\n");
        result = tryConvertBdd(c, INT_MAX, stage);
      } else if (bdd_max_adder_cost != 0 && adder_cost <= bdd_max_adder_cost) {
        report->reportf(2, "Try to convert by bdds\n");
        result = tryConvertBdd(c, (int)(bdd_max_adder_cost * config->bdd_thres), stage);
      } else {
        report->reportf(2, "Try to convert by bdds\n");
        result = tryConvertBdd(c, (int)(adder_cost * config->bdd_thres), stage);
      }
      if (result == _undef_) {
        if (stage == 0 && bdd_max_adder_cost != 0 && adder_cost > 0)
          bdd_max_adder_cost = adder_cost - 1;
        // report->reportf(2, "Switching to adder coding\n");
      }
    }
    // Step 3: Adder fallback
    if (result == _undef_) {
      report->reportf(2, "Converting by adders\n");
      result = tryConvertAdder(c);
    }
    return result;
  }

  default:
    assert(false);
    return _undef_;
  }
}

bool PbSolver::convertPbs(int stage)
{
  if (config->use_scip) return true;

  vec<Formula> converted_constrs;

  if (stage != convert_previousStage) {
    convert_base_sort.clear();
    convert_base_bdd.clear();
    memo_bdd_conv_int_in_min_mode.clear();
    convert_previousStage = stage;
  }

  if (stage == 0 && config->or_detection && !config->es1_detection) {
    if (!rewriteAlmostClauses()) {
      ok = false;
      return false;
    }
  }

  if (!findIntervals()) {
    ok = false;
    return false;
  }

  int bdd_max_adder_cost  = config->bdd_max_adder_cost;
  int sort_max_adder_cost = config->sort_max_adder_cost;

  for (int i = 0; i < constrs.size(); i++) {
    if (interrupted) return false;

    if (stage == 0) {
      convert_base_sort.clear();
      convert_base_bdd.clear();
    }

    if (constrs[i] == NULL) continue;
    Linear& c = *constrs[i];
    if (c.lo == Int_MIN && c.hi == Int_MAX) continue;

    report->reportf(3, "---[%4d]---> \n", constrs.size() - 1 - i);

    bool converted = false;

    // if (config->es1_detection && config->solver == st_ES1Sat) {
    //   if (rewriteES1(c)) converted = true;
    // } else if (config->cc_detection && config->solver == st_CCMiniSat) {
    //   if (rewriteCC(c)) converted = true;
    // } else
    if (config->cc_sort) {
      Formula result;
      if ((result = rewriteCCbySorter(c, stage)) != _undef_) {
        if (result == _0_) { ok = false; return false; }
        converted_constrs.push(result); converted = true; config->stats_cc_sort++;
      }
    }

    if (!converted) {
      Formula result = convertOneConstraint(c, config->convert,
        bdd_max_adder_cost, sort_max_adder_cost, stage);
      if (result == _0_) { ok = false; return false; }
      converted_constrs.push(result);
    }
    if (!ok) return false;
  }

  report->reportf(2, "\n");
  // Update constrs_bk only when minimization_mode is false (coding phase).
  // This prevents solver-internal constraints (e.g. @relaxing_constraint*)
  // from leaking into constrs_bk when convertPbs(0) is called during the solving phase (e.g. sc2 loop).
  if ((stage == 0) && config->model_check && !minimization_mode) {
    for (int i = 0; i < constrs.size(); i++)
      if (constrs[i] != NULL) constrs_bk.push(constrs[i]);
  }
  if (!config->model_check) {
    // 'mem.clear()' below only releases raw bytes, so destruct the coefficients first
    // (each 'Int' owns an 'mpz_t' in the GMP build).  Skipped when 'model_check' is on:
    // 'constrs_bk'/'constrs_deleted' keep these 'Linear's alive on 'mem', and '~PbSolver()'
    // disposes of them.
    for (int i = 0; i < constrs.size(); i++)
      if (constrs[i] != NULL) constrs[i]->~Linear();
    for (int i = 0; i < constrs_deleted.size(); i++)
      constrs_deleted[i]->~Linear();
    constrs_deleted.clear();
  }
  constrs.clear();
  if (!config->model_check) mem.clear();

  clausifier->clausify(converted_constrs);

  return ok;
}

/***************************************************************************[PbSolver_convertBdd.C]
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
#include "FEnv.h"
#include "Sort.h"
#include "Debug.h"
#include "OptimalBase.h"
#include <sys/time.h>   // introduced by Nagatsuka

//=================================================================================================

#define lit2fml(p) id(var(var(p)),sign(p))


//static
  Pair<Interval<Int>, Formula>
PbSolver::buildBDD_i(const vec<Lit>& ps, const vec<Int>& Cs,
    Int lo, int size, Int sum, Int material_left,
    std::map<Pair< int, Interval<Int> >,
    Pair< Interval<Int> , Formula> > &memo,
    int max_cost)
{
  Int lower_limit = (lo == Int_MIN) ? Int_MIN : lo - sum;
  //  Int upper_limit = (hi == Int_MAX) ? Int_MAX : hi - sum;
  //  lo - sum <= (Cs[0]*ps[0] + ... + Cs[size]*ps[size]) <= hi - sum

  //  assert(config->avoid_band_constraint!=2 || upper_limit == Int_MAX);
  //  if (config->avoid_band_constraint==2)
  //      assert(upper_limit == Int_MAX);

#ifdef DEBUG
  /**/report->reportf(4, "# buildBDD2\n"),
    report->reportf(4, " size=%d, sum=",size),
    dump(config, 4, sum),
    report->reportf(4, ", left="),
    dump(config, 4, material_left),
    report->reportf(4, ",  lo_limit="), dump(config, 4, lower_limit),report->reportf(4, "\n");
  /**/report->reportf(4, "max_cost=%d, topSize()=%d\n", max_cost, FEnv::topSize());
#endif

  if (FEnv::topSize() > max_cost)
    return Pair_new( Interval_new(lower_limit,lower_limit),_undef_);     // (mycket elegant!)

  if ((lower_limit <= 0 ) || lower_limit > material_left) {
    Interval<Int> lower_interval;
    //      Interval<Int> upper_interval;
    Formula fm;  Int zero = 0;  // Int minus_one = -1;
    if (lower_limit <= 0) lower_interval = Interval_new(Int_MIN,zero);
    else lower_interval = Interval_new(material_left + 1, Int_MAX);
    //      if (upper_limit >= 0) upper_interval = Interval_new(material_left, Int_MAX);
    //      else upper_interval = Interval_new(Int_MIN, minus_one);

    //      if (lower_limit <= 0 && upper_limit >= material_left)
    if (lower_limit <= 0)
      fm = _1_ ;
    else
      fm = _0_ ;
    return Pair_new(lower_interval, fm);
  }

  Pair<Interval<Int>, Formula> result;
  Interval<Int> intervals = Interval_new(lower_limit,lower_limit);

  Pair<int, Interval<Int> >
    key = Pair_new( size, intervals);
  Formula fm;

  if (!memo.count(key)){
#ifdef DEBUG
    /**/report->reportf(4, "<%d,",size),report->reportf(4, "low="),dump(config, 4, lower_limit),report->reportf(4, "> not found\n");
#endif
    assert(size > 0);
    size--;
    material_left -= Cs[size];

    Pair< Interval<Int>, Formula>
      tt = buildBDD_i(ps, Cs, lo, size, sum + Cs[size], material_left, memo, max_cost);
    if (tt.snd == _undef_) // return _undef_;
      return tt;

    Pair< Interval<Int>, Formula>
      ff = buildBDD_i(ps, Cs, lo, size, sum, material_left, memo, max_cost);
    if (ff.snd == _undef_) //return _undef_;
      return ff;

    Int tt_lo_beta = tt.fst.fst, tt_lo_gamma = tt.fst.snd;
    Int ff_lo_beta = ff.fst.fst, ff_lo_gamma = ff.fst.snd;
    //        Int tt_hi_beta = tt.fst.snd.fst, tt_hi_gamma = tt.fst.snd.snd;
    //        Int ff_hi_beta = ff.fst.snd.fst, ff_hi_gamma = ff.fst.snd.snd;

    intervals = Interval_new(max(tt_lo_beta.add(Cs[size]),ff_lo_beta), min(tt_lo_gamma.add(Cs[size]),ff_lo_gamma));
    key = Pair_new( size+1, intervals );

#ifdef DEBUG
    /**/report->reportf(4, "tt=["),dump(config, 4, tt_lo_beta),report->reportf(4, ","),dump(config, 4, tt_lo_gamma),report->reportf(4, "]"),
      report->reportf(4, " and ff=["),dump(config, 4, ff_lo_beta),report->reportf(4, ","),dump(config, 4, ff_lo_gamma),report->reportf(4, "]"),
      report->reportf(4, " and <%d,[",size+1),dump(config, 4, intervals.fst),report->reportf(4, ","),dump(config, 4, intervals.snd),report->reportf(4, "]> added\n");
    //**/if( tt.snd == ff.snd) report->reportf(2, "*");
#endif
    //      assert(K == newK);  K = newK;
    if(tt.snd != ff.snd) {
      config->stats_bdd_nodes++;

      //      report->reportf("Lo.beta,Hi.gamma="); dump(config, intervals.fst.fst);
      //      report->reportf(","); dump(config, intervals.snd.snd); report->reportf("\n");
      //   if(!config->convert_bdd_monotonic)
      //     fm = ITE(lit2fml(ps[size]), tt.snd, ff.snd);
      //   else if(intervals.snd.snd == Int_MAX)
      //     fm = ITEn(lit2fml(ps[size]), tt.snd, ff.snd);
      //   else if(intervals.fst.fst == Int_MIN)
      //     fm = ITEp(lit2fml(ps[size]), tt.snd, ff.snd);
      //   else          // only negative side NOW ##
      //     fm = ITE(lit2fml(ps[size]), tt.snd, ff.snd);
      // } else fm = tt.snd;

      // tentatively forcing monotonic conversion
      //  if(!config->convert_bdd_monotonic)
      //    fm = ITE(lit2fml(ps[size]), tt.snd, ff.snd);
      //  else
      fm = ITEn(lit2fml(ps[size]), tt.snd, ff.snd);
    } else fm = tt.snd;
    result = Pair_new(intervals, fm);
    memo[key]= result;
  } else {
    result = memo[key];

#ifdef DEBUG
    /**/ report->reportf(4, "<%d,",size),dump(config, 4, lower_limit),report->reportf(4, "> found\n");
#endif

  }

  return result;
}

//static
  Pair<Pair<Interval<Int>,Interval<Int> >, Formula>
PbSolver::buildBDD_i_b(const vec<Lit>& ps, const vec<Int>& Cs,
    Int lo, Int hi, int size, Int sum, Int material_left,
    std::map<Pair< int, Pair< Interval<Int> , Interval<Int> > >,
    Pair<Pair< Interval<Int> , Interval<Int> >, Formula> > &memo_b,
    int max_cost)
{
  Int lower_limit = (lo == Int_MIN) ? Int_MIN : lo - sum;
  Int upper_limit = (hi == Int_MAX) ? Int_MAX : hi - sum;
  // lo - sum <= (Cs[0]*ps[0] + ... + Cs[size]*ps[size]) <= hi - sum

  // commented out due to config handling
  // assert(config->avoid_band_constraint!=2 || upper_limit == Int_MAX);

#ifdef DEBUG
  /**/report->reportf(4, "# buildBDD2\n"),
    report->reportf(4, " size=%d, sum=",size),
    dump(config, 4, sum),
    report->reportf(4, ", left="),

    dump(config, 4, material_left),
    report->reportf(4, ",  lo_limit="), dump(config, 4, lower_limit),report->reportf(4, "  upper_limit="), dump(config, 4, upper_limit),report->reportf(4, "\n");
  /**/report->reportf(4, "max_cost=%d, topSize()=%d\n", max_cost, FEnv::topSize());
#endif

  if (FEnv::topSize() > max_cost)
    return Pair_new(Pair_new(Interval_new(lower_limit,lower_limit),
          Interval_new(upper_limit,upper_limit) ),
        _undef_);     // (mycket elegant!)

  if ((lower_limit <= 0 && upper_limit >= material_left)
      || lower_limit > material_left || upper_limit < 0) {
    Interval<Int> lower_interval;
    Interval<Int> upper_interval;
    Formula fm;  Int zero = 0;  Int minus_one = -1;
    if (lower_limit <= 0) lower_interval = Interval_new(Int_MIN,zero);
    else lower_interval = Interval_new(material_left + 1, Int_MAX);
    if (upper_limit >= 0) upper_interval = Interval_new(material_left, Int_MAX);
    else upper_interval = Interval_new(Int_MIN, minus_one);

    if (lower_limit <= 0 && upper_limit >= material_left)
      fm = _1_ ;
    else
      fm = _0_ ;
    return Pair_new( Pair_new(lower_interval,upper_interval), fm);
  }

  Pair<Pair<Interval<Int>,Interval<Int> >, Formula> result;
  Pair<Interval<Int>,Interval<Int> > intervals
    = Pair_new(Interval_new(lower_limit,lower_limit),
        Interval_new(upper_limit,upper_limit));

  Pair<int, Pair< Interval<Int>,Interval<Int> > >
    key = Pair_new( size, intervals);
  Formula fm;

  if (!memo_b.count(key)){
#ifdef DEBUG
    /**/report->reportf(4, "<%d,",size),report->reportf(4, "["),dump(config, 4, lower_limit),report->reportf(4, ","),dump(config, 4, upper_limit),report->reportf(4, "]> not found\n");
#endif
    assert(size > 0);
    size--;
    material_left -= Cs[size];

    Pair< Pair< Interval<Int>, Interval<Int> >, Formula>
      tt = buildBDD_i_b(ps, Cs, lo, hi, size, sum + Cs[size], material_left, memo_b, max_cost);
    if (tt.snd == _undef_) // return _undef_
      return tt;

    Pair< Pair< Interval<Int>, Interval<Int> >, Formula>
      ff = buildBDD_i_b(ps, Cs, lo, hi, size, sum, material_left, memo_b, max_cost);
    if (ff.snd == _undef_) //return _undef_
      return ff;

    Int tt_lo_beta = tt.fst.fst.fst, tt_lo_gamma = tt.fst.fst.snd;
    Int ff_lo_beta = ff.fst.fst.fst, ff_lo_gamma = ff.fst.fst.snd;
    Int tt_hi_beta = tt.fst.snd.fst, tt_hi_gamma = tt.fst.snd.snd;
    Int ff_hi_beta = ff.fst.snd.fst, ff_hi_gamma = ff.fst.snd.snd;

    intervals = Pair_new(Interval_new(max(tt_lo_beta.add(Cs[size]),ff_lo_beta), min(tt_lo_gamma.add(Cs[size]),ff_lo_gamma)),
        Interval_new(max(tt_hi_beta.add(Cs[size]),ff_hi_beta), min(tt_hi_gamma.add(Cs[size]),ff_hi_gamma)));
    //      key = Pair_new( size, intervals );
    key = Pair_new( size+1, intervals );

#ifdef DEBUG
    /**/report->reportf(4, "tt=["),dump(config, 4, tt_lo_beta),report->reportf(4, ","),dump(config, 4, tt_lo_gamma),report->reportf(4, "]["),dump(config, 4, tt_hi_beta),report->reportf(4, ","),dump(config, 4, tt_hi_gamma),report->reportf(4, "] and ff=["),dump(config, 4, ff_lo_beta),report->reportf(4, ","),dump(config, 4, ff_lo_gamma),report->reportf(4, "]["),dump(config, 4, ff_hi_beta),report->reportf(4, ","),dump(config, 4, ff_hi_gamma),report->reportf(4, "] and <%d,[",size+1),dump(config, 4, intervals.fst.fst),report->reportf(4, ","),dump(config, 4, intervals.fst.snd),report->reportf(4, "]["),dump(config, 4, intervals.snd.fst),report->reportf(4, ","),dump(config, 4, intervals.snd.snd),report->reportf(4, "]> added\n");
    //**/if( tt.snd == ff.snd) report->reportf(2, "*");
#endif
    //      assert(K == newK);  K = newK;
    if(tt.snd != ff.snd) {
      config->stats_bdd_nodes++;

      //      report->reportf("Lo.beta,Hi.gamma="); dump(config, intervals.fst.fst);
      //      report->reportf(","); dump(config, intervals.snd.snd); report->reportf("\n");

      //
      // tentatively forcing to monotonic conversion
      // if(!config->convert_bdd_monotonic)
      //   fm = ITE(lit2fml(ps[size]), tt.snd, ff.snd);
      // else if(intervals.snd.snd == Int_MAX)
      if(intervals.snd.snd == Int_MAX)
        fm = ITEn(lit2fml(ps[size]), tt.snd, ff.snd);
      else if(intervals.fst.fst == Int_MIN)
        fm = ITEp(lit2fml(ps[size]), tt.snd, ff.snd);
      else          // only negative side NOW ##
        fm = ITE(lit2fml(ps[size]), tt.snd, ff.snd);
    } else fm = tt.snd;
    result = Pair_new(intervals, fm);
    memo_b[key]= result;
  } else {
    result = memo_b[key];

#ifdef DEBUG
    /**/ report->reportf(4, "<%d,[",size),dump(config, 4, lower_limit),report->reportf(4, ","),dump(config, 4, upper_limit),report->reportf(4, "]> found\n");
#endif

  }

  return result;
}

// Formula
//   buildBDD(const vec<Lit>& ps, const vec<Int>& Cs,
//            Int lo, Int hi, int size, Int sum, Int material_left,
//            Map<Pair< int, Int>, Formula> &memo,
//            int max_cost)
// {
//     Int lower_limit = (lo == Int_MIN) ? Int_MIN : lo - sum;
//     Int upper_limit = (hi == Int_MAX) ? Int_MAX : hi - sum;
//     // lo - sum <= (Cs[0]*ps[0] + ... + Cs[size]*ps[size]) <= hi - sum

//     // commented out due to config handling
//     // assert(config->avoid_band_constraint!=2 || upper_limit == Int_MAX);

// #ifdef DEBUG
//     /**/report->reportf(3, "# buildBDD1\n"),report->reportf(3, " size=%d, sum=%d, left=%d, ",size,toint(sum),toint(material_left)),report->reportf(3, "  lo_limit="), dump(config, 3, lower_limit),report->reportf(3, "  upper_limit="), dump(config, 3, upper_limit),report->reportf(3, "\n");
//     /**/report->reportf(4, "max_cost=%d, topSize()=%d\n", max_cost, FEnv::topSize());
// #endif

//     if (FEnv::topSize() > max_cost)
//       return _undef_;     // (mycket elegant!)

//     if (lower_limit <= 0 && upper_limit >= material_left)
//       return _1_;
//     else if (lower_limit > material_left || upper_limit < 0)
//       return _0_;

//     Pair<Int, Formula> result;

//     Pair<int, Int >
//       key = Pair_new( size, lower_limit );
//     Formula fm;

//     if (!memo.peek(key, fm)){
// #ifdef DEBUG
//       /**/report->reportf(4, "<%d,",size),report->reportf(4, "<"),dump(config, 4, lower_limit),report->reportf(4, ","),dump(config, 4, upper_limit),report->reportf(4, ">> not found\n");
// #endif
//         assert(size > 0);
//         size--;
//         material_left -= Cs[size];

//         Formula
//           tt = buildBDD(ps, Cs, lo, hi, size, sum + Cs[size], material_left, memo, max_cost);
//         if (tt == _undef_) // return _undef_;
//           return tt;

//         Formula
//           ff = buildBDD(ps, Cs, lo, hi, size, sum, material_left, memo, max_cost);
//         if (ff == _undef_) //return _undef_;
//           return ff;

// #ifdef DEBUG
//         /**/report->reportf(4, "<%d,[",size),dump(config, 4, key.snd),report->reportf(4, "]> added\n");
//         //**/if(tt == ff) report->reportf(2, "*");
// #endif
//         if(tt != ff) {
//           config->stats_bdd_nodes++;
//           if(config->convert_bdd_monotonic &&
//              (config->avoid_band_constraint==2 ||
//               (config->avoid_band_constraint==1 && hi==Int_MAX))
//               )
//             fm = ITEn(lit2fml(ps[size]), tt, ff);
//           else fm = ITE(lit2fml(ps[size]), tt, ff);
//         } else fm = tt;
//         memo.set(key, fm);
//     }
// #ifdef DEBUG
//     /**/ else report->reportf(4, "<%d,[",size),dump(config, 4, key.snd),report->reportf(4, "]> found\n");
// #endif

//     return fm;
// }

// Sort Csls by spread factor: groups with larger spread go to leaves (front of
// array), groups with smaller spread go to root (end of array).
// Spread factor of a group with coefficient c and k items: f = min(k+1, sum/c+1)
// Tiebreak: smaller coefficient to the leaf side.
static void sortCslsBySpreadFactor(vec<Pair<Int,Lit> >& Csls, Int sum)
{
  sort(Csls); // ascending by coeff to form contiguous groups

  struct GroupInfo {
    Int spread;
    Int coeff;
    int start;
  };
  vec<Pair<Pair<Int,Int>,int> > groups; // ((spread, coeff), start_idx)
  int n = Csls.size();
  for (int i = 0; i < n; ) {
    Int c = Csls[i].fst;
    int j = i + 1;
    while (j < n && Csls[j].fst == c) j++;
    int k = j - i;
    Int f_k = Int(k + 1);
    Int f_c = sum / c + Int(1);
    Int f   = (f_k < f_c) ? f_k : f_c;
    groups.push(Pair_new(Pair_new(f, c), i));
    i = j;
  }

  // Primary: descending spread (large spread → leaf/front)
  // Secondary: ascending coeff (small coeff → leaf/front)
  struct SpreadGroupLess {
    bool operator()(const Pair<Pair<Int,Int>,int>& a,
                    const Pair<Pair<Int,Int>,int>& b) const {
      if (a.fst.fst != b.fst.fst) return a.fst.fst > b.fst.fst;
      return a.fst.snd < b.fst.snd;
    }
  };
  sort(groups, SpreadGroupLess());

  vec<Pair<Int,Lit> > new_Csls;
  for (int gi = 0; gi < groups.size(); gi++) {
    int start = groups[gi].snd;
    Int c = Csls[start].fst;
    for (int i = start; i < n && Csls[i].fst == c; i++)
      new_Csls.push(Csls[i]);
  }
  new_Csls.moveTo(Csls);
}

// New school: Use the new 'ITE' construction of the formula environment 'FEnv'.
//
//Formula convertToBdd_one(const Linear& c, int max_cost, int decomp_mode)
Formula PbSolver::convertToBdd_one(vec<Lit>& ls, vec<Int>& Cs, Pair<Interval<Int>,Interval<Int> >& intervals, vec<int>& base, int decomp_mode, bool reuse_memo, int max_cost, int stage)
{
  Int     sum = 0;
  Formula ret;
  Int lo = intervals.fst.fst;
  Int hi = intervals.snd.snd;

  if( lo > hi ) {
    report->reportf(2, "false detected: lo = %s > %s = hi\n", toString(lo), toString(hi));
    return ( _0_ );
  }


  if (hi == Int_MAX) config->stats_std_form++;
  else config->stats_band_form++;

  if (config->avoid_band_constraint==2)
    assert(hi == Int_MAX);

  for (int j = 0; j < ls.size(); j++)
    sum += Cs[j];
  //        sum += c(j);

  vec<Pair<Int,Lit> > Csls;
  vec<Lit> norm_ls;
  vec<Int> norm_Cs;
  //    vec<int> base;
  bool perform_decomposition = false;
  static int lastPsCs_size = 0;

  if (decomp_mode == 1) { // binary-base decomposition
                          // decompose terms; e.g. 11x into x + 2x + 8x
                          // report->reportf("B");
    for (int i = 0; i < ls.size(); i++) {
      Int div, rem, co = 1;
      //      div = c(i);
      div = Cs[i];
      while( div > 0 ) {
        // report->reportf("div=%d, rem=%d, co=%d\n",toint(div),toint(rem),toint(co));
        rem = div % 2;  div = div / 2;
        if(rem != 0) {
          //      Csls.push(Pair_new(co, c[i]));
          Csls.push(Pair_new(co, ls[i]));
          // report->reportf("A: ps[%d]=", i),dump(config, ls[i]),report->reportf(", Cs[%d]=", i),dump(config, co),report->reportf("\n");
        }
        co *= 2;
      }
    }
    // report->reportf("\nBin:Be ls,Cs="),dump(config, Csls); report->reportf(", lo="),dump(config, lo),report->reportf(", hi="),dump(config, hi),report->reportf("\n");
    if(config->convert_bdd_order == bo_increasing) sortr(Csls);
    else if(config->convert_bdd_order == bo_spread_factor) sortCslsBySpreadFactor(Csls, sum);
    else sort(Csls);
    //report->reportf("Bin:Af ls,Cs="),dump(config, Csls); report->reportf(", lo="),dump(config, lo),report->reportf(", hi="),dump(config, hi),report->reportf("\n");

  } else if (decomp_mode >= 2) { // multi-base decomposition
                                 //==================
                                 //      vec<Int> Cs2;
                                 //      for(int k = 0; k < c.size; k++)
                                 //        Cs.push(c(k));
    vec<Int> dummy;
    int cost;
    //      vec<int> base;

    // get measure for multibased decomposition
    int kind_weight,count_weight;  // introduced by Nagatsuka
    Int biggest;

    count_weight=Cs.size();
    kind_weight=CountKind_Sort(Cs);
    biggest=Cs[Cs.size()-1];
    int64 const10_10 = 10000000000LL;
    Int thresh = const10_10;

    // Make a dicision for the strategy of the decompositions
    switch (decomp_mode) {
      case 2: perform_decomposition = true;
              break;
      case 3: // strategy 1
              perform_decomposition = 20 < kind_weight;
              break;
      case 4: // strategy 2
              perform_decomposition = ((count_weight / 5) < kind_weight)
                && (count_weight > 10) ;
              break;
      case 5: // strategy 3 (default)
              perform_decomposition = ((count_weight / 5) < kind_weight)
                && (count_weight > 10)
                && (biggest < thresh);
              break;
      default: assert(false);
    }
#ifdef DEBUG
    report->reportf(3, "convertToBdd_one1: count=%d kind=%d max_coef=",count_weight,kind_weight), dump(config, 3, Cs[Cs.size()-1]),report->reportf(3, "\n");
#endif

    if(perform_decomposition) {
      report->reportSf(2, 'D');

      if(base.size() == 0) {
#ifdef DEBUG
        struct timeval s, t;    // for time evaluation
        gettimeofday(&s, NULL);  // for time evaluation
#endif
        OptimalBase ob(config, obt_kind, report);
        ob.optimizeBase(Cs, dummy, cost, base);  // the last argument declares non-carryimportant
                                                 //        optimizeBase(Cs, dummy, cost, base, config->optbase_measure);  // the last argument declares non-carryimportant
#ifdef DEBUG
        gettimeofday(&t, NULL);  // for time evaluation
        report->reportf(3, "convertToBdd_one2: OptimzingBaseTime : %lf\n",(t.tv_sec - s.tv_sec) + (t.tv_usec - s.tv_usec)*1.0E-6);
        // report time
#endif
      }

      // if base is [], set it to [1]
      if(base.size()==0) base.push(1);

#ifdef DEBUG
      /**/if (config->verbosity >= 3) {report->reportf(3, "\nconvertToBdd_one3: Base:"); for (int i = 0; i < base.size(); i++) report->reportf(3, " %d", base[i]); report->reportf(3, "\n");}
      //==================
#endif
      assert(base.size() > 0);
      if(base[0] == 1) {
        for (int i = 0; i < ls.size(); i++) {
          Csls.push(Pair_new(Cs[i], ls[i]));
        }
      } else {
        int i;
        for ( i = 0; i < ls.size(); i++){
          Int div, rem, co;
          div = Cs[i];
          int k = 0;
          co = 1;
          while(div > 0) {
            rem = div % base[k]; div = div / base[k];
            //report->reportf("base=%d, div=%d, rem=%d, co=%d\n",base[k],toint(div),toint(rem),toint(co));
            if(rem != 0) {
              Csls.push(Pair_new(co*rem, ls[i]));
              //report->reportf("BDD-cost:%5d\n", FEnv::topSize());
              //report->reportf("ls[%d]=", i),dump(config, c[i]),report->reportf(", Cs[%d]=", i),dump(config, co*rem),report->reportf("\n");
            }
            co *= base[k];
            k++;
            //report->reportf("k=%d\n",k);
            if(k>base.size()-1){
              if(div != 0) {
                Csls.push(Pair_new(co*div, ls[i]));
                //report->reportf("ls[%d]=", i),dump(config, c[i]),report->reportf(", Cs[%d]=", i),dump(config, co*div),report->reportf("\n");
              }
              break;
            }
          }
        }
        //report->reportf("i=%d finished\n",i);
      }

      /*
         for (int i = 0; i < Csls.size(); i++)
         norm_ls.push(Csls[i].snd), norm_Cs.push(Csls[i].fst);
         report->reportf("ls,Cs="),dump(config, norm_ls,norm_Cs);
       */

      // report->reportf("\nDec:Be ls,Cs="),dump(config, Csls); report->reportf(", lo="),dump(config, lo),report->reportf(", hi="),dump(config, hi),report->reportf("\n");
      if(config->convert_bdd_order == bo_increasing) sortr(Csls);
      else if(config->convert_bdd_order == bo_spread_factor) sortCslsBySpreadFactor(Csls, sum);
      else sort(Csls);
      // report->reportf("Dec:Af ls,Cs="),dump(config, Csls); report->reportf(", lo="),dump(config, lo),report->reportf(", hi="),dump(config, hi),report->reportf("\n");
    } else {
      //report->reportf("count=%d kind=%d max_coef=",count_weight,kind_weight); dump(config, Cs[Cs.size()-1]); report->reportf(" non-decomposition \n\n");
      report->reportSf(2, 'd');
      for (int i = 0; i < ls.size(); i++) {
        Csls.push(Pair_new(Cs[i], ls[i]));
      }
      // report->reportf("\nNon:Be ls,Cs="),dump(config, Csls); report->reportf(", lo="),dump(config, lo),report->reportf(", hi="),dump(config, hi),report->reportf("\n");
      if(config->convert_bdd_order == bo_increasing) sortr(Csls);
      else if(config->convert_bdd_order == bo_spread_factor) sortCslsBySpreadFactor(Csls, sum);
      // report->reportf("Non:Af ls,Cs="),dump(config, Csls); report->reportf(", lo="),dump(config, lo),report->reportf(", hi="),dump(config, hi),report->reportf("\n");
    }

  } else {  // non-decomposition // decomp_mode: 0 /////////////////
            //int kind_weight=Cs.size(), count_weight=CountKind_Sort(Cs);
            //report->reportf("count=%d kind=%d max_coef=",count_weight,kind_weight); dump(config, Cs[Cs.size()-1]); report->reportf("\n\n");

    for (int i = 0; i < ls.size(); i++) {
      Csls.push(Pair_new(Cs[i], ls[i]));
    }
    // report->reportf("\nNon:Be ls,Cs="),dump(config, Csls); report->reportf(", lo="),dump(config, lo),report->reportf(", hi="),dump(config, hi),report->reportf("\n");
    if(config->convert_bdd_order == bo_increasing) sortr(Csls);
    else if(config->convert_bdd_order == bo_spread_factor) sortCslsBySpreadFactor(Csls, sum);
    // report->reportf("Non:Af ls,Cs="),dump(config, Csls); report->reportf(", lo="),dump(config, lo),report->reportf(", hi="),dump(config, hi),report->reportf("\n");
  }

  for (int i = 0; i < Csls.size(); i++)
    norm_ls.push(Csls[i].snd), norm_Cs.push(Csls[i].fst);

  /**/ report->reportf(3, "convertToBdd_one4:"),report->reportf(3, "ls,Cs="),dump(config, 3, &index2name, norm_ls,norm_Cs),
    report->reportf(3, ", lo="),dump(config, 3, lo),report->reportf(3, ", hi="),dump(config, 3, hi),report->reportf(3, "\n");

  if(!reuse_memo)
    FEnv::push();

  if(minimization_mode) {
    assert( hi == Int_MAX );
    if(config->convert_goal_reusing && Csls.size() != lastPsCs_size) {
      lastPsCs_size = Csls.size();
      memo_bdd_conv_int_in_min_mode.clear();
      report->reportf(2, "mem for reusing cleared.\n");
    }
    Pair< Interval<Int>, Formula>
      rootInfo = buildBDD_i(norm_ls, norm_Cs, lo, Csls.size(), 0, sum, memo_bdd_conv_int_in_min_mode, max_cost);
    ret = rootInfo.snd;
    intervals.fst.fst = rootInfo.fst.fst;
    intervals.fst.snd = rootInfo.fst.snd;
  } else {
    if(hi == Int_MAX) {
      std::map<Pair< int, Interval<Int> >,
        Pair< Interval<Int>, Formula> > memo;
      Pair< Interval<Int>, Formula>
        rootInfo = buildBDD_i(norm_ls, norm_Cs, lo, Csls.size(), 0, sum, memo, max_cost);
      ret = rootInfo.snd;
      intervals.fst.fst = rootInfo.fst.fst;
      intervals.fst.snd = rootInfo.fst.snd;
    } else {
      std::map<Pair< int, Pair< Interval<Int> , Interval<Int> > >,
        Pair<Pair< Interval<Int> , Interval<Int> >, Formula> > memo;
      Pair< Pair<Interval<Int>,Interval<Int> >, Formula>
        rootInfo = buildBDD_i_b(norm_ls, norm_Cs, lo, hi, Csls.size(), 0, sum, memo, max_cost);
      ret = rootInfo.snd;
      intervals.fst.fst = rootInfo.fst.fst.fst;
      intervals.fst.snd = rootInfo.fst.fst.snd;
      intervals.snd.fst = rootInfo.fst.snd.fst;
      intervals.snd.snd = rootInfo.fst.snd.snd;
    }
  }

  if (decomp_mode == 1) config->stats_bdd_bin_constraints++;
  else if (decomp_mode >= 2)
    if (perform_decomposition && base.size()>0 && base[0]>1) config->stats_bdd_mul_constraints++;
    else config->stats_bdd_raw_constraints++;
  else config->stats_bdd_raw_constraints++;

  config->stats_bdd_cost = max(config->stats_bdd_cost, FEnv::topSize());
  if (config->verbosity >= 3) {
    if (stage == 0)
      report->reportf("BDD-cost: %5d      MaxCost: %5d   ", FEnv::topSize(), max_cost);
    else
      report->reportf("Goal-BDD-cost: %5d      MaxCost: %5d   ", FEnv::topSize(), max_cost);
    if (decomp_mode >= 3) { // multi-base
      report->reportf("Base:");
      for (int i = 0; i < base.size(); i++)
        report->reportf(" %d", base[i]);
    }
    report->reportf(3, "\n");
  }
  //if (ret == _undef_ )
  if (ret == _undef_ && !reuse_memo) {
    FEnv::pop();
  } else {
    if(!reuse_memo)
      FEnv::keep();
  }

  return ret;
}

// decomp_mode = 0: non-decomposition
//             = 1: binary-base decomposition,
//             >=2: multi-base decomposition,
//                 2: always decompose
//                 3: by strategy 1
//                 4: by strategy 2
//                 5: by strategy 3 (default)
Formula PbSolver::convertToBdd(const Linear& c, Pair< Interval<Int>, Interval<Int> >& intervals, vec<int>& base, int decomp_mode, bool reuse_memo, int max_cost, int stage)
{
  vec<Lit>    ls;
  vec<Int>        Cs;
  Int csum = 0;
  Formula ret;

#ifdef DEBUG
  report->reportf(3, "convBDD: "), dump(config, 3, &index2name, c), report->reportf(3, "\n");
#endif

  double start_time = cpuTime();

  if(max_cost != INT_MAX && c.size > config->bdd_max_constraints) return _undef_ ;  // negligible time consumed

  for (int j = 0; j < c.size; j++)
    ls.push(c[j]),
      Cs.push(c(j)),
      csum += c(j);

  if(config->avoid_band_constraint==0 || (config->avoid_band_constraint==1 && c.lo!=c.hi)) {
    intervals.fst.fst = c.lo;
    intervals.fst.snd = c.lo;
    intervals.snd.fst = c.hi;
    intervals.snd.snd = c.hi;
    ret = convertToBdd_one(ls, Cs, intervals, base, decomp_mode, reuse_memo, max_cost, stage);
  }else{
    Pair< Interval<Int>, Interval<Int> > interval2 = Pair< Interval<Int>, Interval<Int> >(Interval_new(c.lo,c.lo),Interval_new(Int_MAX,Int_MAX));
    //      ret = convertToBdd_one(ls, Cs, c.lo, Int_MAX, base, decomp_mode, reuse_memo, max_cost, first_call);
    ret = convertToBdd_one(ls, Cs, interval2, base, decomp_mode, reuse_memo, max_cost, stage);
    if(ret == _undef_ ) {
      config->stats_BDD_coding_time += (cpuTime() - start_time);
      return ret;
    }
    intervals.fst.fst = interval2.fst.fst;
    intervals.fst.snd = interval2.fst.snd;
    intervals.snd.fst = Int_MAX;
    intervals.snd.snd = Int_MAX;

    if(c.hi != Int_MAX) {
      ls.clear(); Cs.clear();
      for(int i=0; i<c.size; i++)
        ls.push(~c[i]), Cs.push(c(i));
      Pair< Interval<Int>, Interval<Int> > interval3 = Pair< Interval<Int>, Interval<Int> >(Interval_new(csum - c.hi,csum - c.hi),Interval_new(Int_MAX,Int_MAX));
      ret &= convertToBdd_one(ls, Cs, interval3, base, decomp_mode, reuse_memo, max_cost, stage);
      intervals.snd.fst = csum - interval3.fst.snd ;
      intervals.snd.snd = csum - interval3.fst.fst ;
    }
  }
  if (config->verbosity >= 3)
    report->reportf(3, "convertToBdd resulted intervals: ["),dump(config, 3, intervals.fst.fst),
      report->reportf(3, ","),dump(config, 3, intervals.fst.snd),report->reportf(3, "] and ["),
      dump(config, 3, intervals.snd.fst),report->reportf(3, ","),dump(config, 3, intervals.snd.snd),report->reportf(3, "]\n");

  if(ret != _undef_ && (c.llt != lit_Undef)) {
    Formula f = _1_ ;
    if(c.llt!=lit_Undef)
      f &= ~lit2fml(c.llt) | ret ;   // llt => ret
    /*
       if(c.rlt!=lit_Undef)
       f &= lit2fml(c.rlt) | ~ret ;   // rlt <= ret
     */
    ret = f;
  }
  config->stats_BDD_coding_time += (cpuTime() - start_time);
  return ret;

}

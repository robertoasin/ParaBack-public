/**************************************************************************[OptimalBase.C]
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

#include "Map.h"
#include "PbSolver.h"
#include "OptimalBase.h"
#include "Primes.h"
//#include "Main.h"
#include "Sort.h"
#include "Debug.h"
#include <queue>
#include <utility>

//=================================================================================================

int num_primes(const Config* config) {
  return std::min(config->optbase_primes, (int)elemsof(primes));
}

#define ExpensiveBigConstants

void OptimalBase::optimizeBase(const vec<Int>& seq, int carry_ins, const vec<Int>& rhs, int dcost, vec<int>& base, int& cost_bestfound, vec<int>& base_bestfound)
{
  if (dcost >= cost_bestfound)
    return;
#ifdef DEBUG
  /**/static int depth = 0;
#endif

  // "Base case" -- don't split further, build sorting network for current sequence:
  int final_cost = 0;
  for (int i = 0; i < seq.size(); i++){
    if (seq[i] > INT_MAX)
      goto TooBig;
#ifdef ExpensiveBigConstants
    final_cost += toint(seq[i]);
#else
    int c; for (c = 1; c*c < seq[i]; c++);
    final_cost += c;
#endif
    if (final_cost < 0)
      goto TooBig;
  }
#ifdef DEBUG
  for (int n = depth; n != 0; n--) report->reportf(3, "  ");
  report->reportf(3, "dcost=%d,final_cost(msd)=%d,h=%d cost_bestfound=%d  Best_base:", dcost, final_cost, seq.size(),cost_bestfound);
#endif
  if (dcost + seq.size() > cost_bestfound){   //cost_alpha = dcost + seq.size(), final_cost = dcost + SUM of seqs
#ifdef DEBUG
    report->reportf(3, "Pruned.\n");
#endif
    return;
  }
  if (dcost + final_cost < cost_bestfound){
    base.copyTo(base_bestfound);
    cost_bestfound = dcost + final_cost;
#ifdef DEBUG
    for (int j = 0; j < base_bestfound.size(); j++)
      report->reportf(3, " %d", base_bestfound[j]);
#endif
  }
#ifdef DEBUG
  report->reportf(3, "\n");
#endif

TooBig:;

       // <<== could count 1:s here for efficiency

       vec<Int> new_seq;
       vec<Int> new_rhs;
#ifdef DEBUG
       report->reportf(3, "\nbase: ");
       dump(config, 3, base);

#endif
#ifdef PickSmallest
       int p = -1;
       for (int i = 0; i < seq.size(); i++)
         if (seq[i] > 1){ p = seq[i]; break; }
       if (p != -1){
#else
         int num_p = num_primes(config);
         for (int i = 0; i < num_p; i++){
           int p    = primes[i];
#endif
           int rest = carry_ins;   // Sum of all the remainders.
           Int div;

#ifdef DEBUG
           if (config->verbosity >= 4) {
             for (int n = depth; n != 0; n--) report->reportf("  ");
             report->reportf("prime=%d   carry_ins=%d  New seq:", p, rest);
           }
#endif
           for (int j = 0; j < seq.size(); j++){
             rest += toint(seq[j] % Int(p));
             div = seq[j] / Int(p);
             if (div > 0){
#ifdef DEBUG
               if (config->verbosity >= 3) report->reportf(" %d", toint(div));
#endif
               new_seq.push(div);
             }
           }
#ifdef DEBUG
           if (config->verbosity >= 4) {
             /**/report->reportf("  ");
             /**/for (int n = depth; n != 0; n--) report->reportf("  "); report->reportf("rest(current carry*p)=%d\n", rest);
             //**/for (int n = depth; n != 0; n--) report->reportf("  "); report->reportf("New rhs:");
           }
#endif

           base.push(p);
#ifdef DEBUG
           /**/depth++;
#endif
           optimizeBase(new_seq, rest/p, new_rhs, dcost+(obt == obt_comb ? rest : 0), base, cost_bestfound, base_bestfound);
#ifdef DEBUG
           /**/depth--;
#endif
           base.pop();

           new_seq.clear();
           new_rhs.clear();
         }
       }

       class CompareDist {
         public:
           template <class T>
             bool operator()(Pair<int,T > x, Pair<int, T > y) {
               return x.fst > y.fst;
             }
       };

       // Pair<int, contener > means that <costA, <<base, B>, carry_ins> >
       typedef Pair<Pair<std::vector<int>, Int >, int> container;
       inline int              getCostA(Pair<int,container> d) {return d.fst;}
       inline std::vector<int> getBase (Pair<int,container> d) {return d.snd.fst.fst;}
       inline Int              getB    (Pair<int,container> d) {return d.snd.fst.snd;}
       inline int              getCarry(Pair<int,container> d) {return d.snd.snd;}
       // combination of vec with priority_queue does not work.

       // B = b1 * ... * bn for base=<b1,...,bn>
       // Case obt_digits:
       //   final_cost =  sum of all digits
       //   dcost = final_cost - sum of most significant digits
       //   costA = dcost + num of msds ,

       // Case obt_comb:  used in Sorting networks
       //   final_cost =  sum of all digits and carries
       //   dcost = final_cost - sum of msignificant digits
       //   costA = dcost + num of msds ,

       // Case obt_kind:  used in BDD conversion
       //   final_cost =  sum of cost_weighted_sort(digits) for each significance
       //   dcost = final_cost - sum of cost_weighted_sort(most significant digits)
       //   costA = dcost

       void OptimalBase::optimizeBaseBB(const vec<Int>& seq, std::vector<int>& base_bestfound)
       {

         std::priority_queue<Pair<int, container>, std::vector<Pair<int, container> >, CompareDist > Q;
         int cost_bestfound = INT_MAX;
         std::vector<int> initBase;

         Int sum_msb = 0, div, B;

         for (int i = 0; i < seq.size(); i++)
           sum_msb += seq[i];

         if(obt == obt_kind)
           cost_bestfound = seq.size() + CountKind_Sort(seq);
         else
           if (sum_msb < INT_MAX) cost_bestfound = toint(sum_msb);

         Q.push( Pair_new((int)seq.size()+(obt==obt_kind ? 1 : 0), Pair_new(Pair_new(initBase,1), 0)) );

#ifdef DEBUG
         if (config->verbosity >= 3){
           report->reportf("-1: Q.size()=%d, getCostA(Q.top())=%d, cost_bestfound=%d\n",Q.size(), getCostA(Q.top()), cost_bestfound);
         }
#endif

         int num_p = num_primes(config);
         while( Q.size() != 0 && getCostA(Q.top()) < cost_bestfound ) {
           Pair<int, container> cBase = Q.top();  Q.pop();
           int seq_len = 0, new_seq_len = 0;

           B = getB(cBase);

           for (int j = 0; j < seq.size(); j++){
             div = seq[j] / B ;
             if (div > 0) seq_len++;
           }

#ifdef DEBUG
           if (config->verbosity >= 4){
             report->reportf("O: costA=%d, carry_ins=%d, cost_bestfound=%d\n   base:", getCostA(cBase), getCarry(cBase), cost_bestfound);
             for (int i = 0; i < (int)getBase(cBase).size(); i++)
               report->reportf(" %d", getBase(cBase)[i]);
             report->reportf("\n");
           }
           //    if (config->verbosity >= 3){
           //      report->reportf("   Seq:");
           //      for (int i = 0; i < (int)getSeq(cBase).size(); i++)
           //	report->reportf(" "),dump(config, getSeq(cBase)[i]);
           //      report->reportf("\n");
           //}
#endif

           for (int i = 0; i < num_p; i++){
             int p = primes[i];

             if (seq[seq.size()-1] / B < (Int)p) break;

             int rest = (obt == obt_comb ? getCarry(cBase) : 0);  //, seq_shrink = 0;
             vec<int> rest_seq;
             // int rest = 0, seq_shrink = 0;
             int digit;
             vec<Int> new_seq;
             std::vector<int> new_base = getBase(cBase);
             sum_msb = 0; new_seq_len = 0;
             for (int j = 0; j < seq.size(); j++){
               digit = toint(seq[j]/B % Int(p));
               if (digit > 0){
                 rest += digit;
                 rest_seq.push(digit);
               }
               div = seq[j] / B / Int(p);
               if (div > 0){
                 sum_msb += div;
                 new_seq.push(div);
                 new_seq_len++;
               }
             }

             int new_costA;
             if(obt == obt_kind){
               sort(rest_seq);
               new_costA = getCostA(cBase) + CountKind_Sort(rest_seq) + rest_seq.size() - seq_len + new_seq_len;
               //report->reportf("old_constA=%d, kind-rest=%d, size-rest=%d, seq_len=%d, new_seq_len=%d\n", getCostA(cBase), CountKind_Sort(rest_seq), rest_seq.size(), seq_len, new_seq_len);
             } else {
               new_costA = getCostA(cBase) + rest - seq_len + new_seq_len;
               //report->reportf("old_constA=%d, rest=%d, seq_len=%d, new_seq_len=%d\n", getCostA(cBase), rest, seq_len, new_seq_len);
             }

#ifdef DEBUG
             if (config->verbosity >= 4){
               report->reportf("prime=%d, rest=%d, New seq:", p, rest);
               for (int j = 0; j < new_seq.size(); j++)
                 report->reportf(" %d", toint(new_seq[j]));
               report->reportf("\n");
               report->reportf("1: new_costA=%d\n", new_costA);
             }
#endif



             if (new_costA <= cost_bestfound) {
               new_base.push_back(p);

#ifdef DEBUG
               if (config->verbosity >= 4){
                 report->reportf("2: new_base:");
                 for(int j = 0; j < (int)new_base.size(); j++)
                   report->reportf(" %d", new_base[j]);
                 report->reportf("  new_costA=%d, new_carry=%d, cand_cost_bestfound=%d\n", new_costA, rest/p, new_costA - new_seq.size() + toint(sum_msb));
               }
#endif

               Q.push(Pair_new(new_costA, Pair_new(Pair_new(new_base, B*p), rest/p )));

               int new_cost;
               if(obt == obt_kind){
                 sortUnique(new_seq);
                 new_cost = new_costA - 1 + new_seq.size();
               } else
                 new_cost = new_costA - new_seq.size() + toint(sum_msb);
               if (sum_msb < INT_MAX && new_cost < cost_bestfound) {
                 base_bestfound = new_base, cost_bestfound = new_cost;
#ifdef DEBUG
                 if (config->verbosity >= 3){
                   report->reportf("  cost_bestfound=%d\n", cost_bestfound);
                 }
#endif
               }
             }
           }
         }
       }


       // Using Pair<int, Int> for <costA, B>
       // Using Map<Int, Pair<int, Pair<vec<int>, int> > >
       //      for <B,        costA,    base,     carry_ins>
       typedef Pair<int, Pair<std::vector<int>, int> > container2;
       inline int      getCostA(container2 d) {return d.fst;}
       inline std::vector<int>
         getBase (Pair<int, Pair<std::vector<int >, int> > d) {return d.snd.fst;}
       inline int      getCarry(container2 d) {return d.snd.snd;}


       void OptimalBase::optimizeBaseHashBB(const vec<Int>& seq, std::vector<int>& base_bestfound)
       {

         std::priority_queue<Pair<int, Int>, std::vector<Pair<int, Int> >, CompareDist > Q;
         Map<Int, container2> memo;

         int cost_bestfound = INT_MAX;
         std::vector<int> initBase;

         Int sum_msb = 0, div, B;
         uint64 calc_steps = 0;

         for (int i = 0; i < seq.size(); i++)
           sum_msb += seq[i];

         if(obt == obt_kind)
           //    cost_bestfound = seq.size() + CountKind_Sort(seq);
           cost_bestfound = count_weighted_sort(seq);
         else
           if (sum_msb < INT_MAX) cost_bestfound = toint(sum_msb);

         //  Q.push( Pair_new((int)seq.size()+(obt==obt_kind ? 1 : 0), 1) );
         Q.push( Pair_new( (obt==obt_kind ? 0 : (int)seq.size()), 1) );
         //  memo.set( 1, Pair_new((int)seq.size()+(obt==obt_kind ? 1 : 0), Pair_new(initBase, 0)) );
         memo.set( 1, Pair_new( (obt==obt_kind ? 0 : (int)seq.size()), Pair_new(initBase, 0)) );

#ifdef DEBUG
         if (config->verbosity >= 4){
           report->reportf("-1: Q.size()=%d, getCostA(Q.top())=%d, cost_bestfound=%d\n",Q.size(), Q.top().fst, cost_bestfound);
         }
#endif

         vec<Int> seq_div_B(seq.size());
         vec<int> rest_seq;
         vec<Int> new_seq;
         int num_p = num_primes(config);
         while( Q.size() != 0 && Q.top().fst < cost_bestfound ) {
           int costA = Q.top().fst;
           B = Q.top().snd;  Q.pop();
           container2 cBase;
           if (!memo.peek(B, cBase)) assert(false);

           if (getCostA(cBase) != costA) continue;

           int seq_len = 0, new_seq_len = 0;

           for (int j = 0; j < seq.size(); j++){
             div = seq[j] / B ;
             seq_div_B[j] = div;
             if (div > 0) seq_len++;
           }

#ifdef DEBUG
           if (config->verbosity >= 4){
             report->reportf("O: costA=%d, carry_ins=%d, cost_bestfound=%d\n   base:", costA, getCarry(cBase), cost_bestfound);
             for (int i = 0; i < (int)getBase(cBase).size(); i++)
               report->reportf(" %d", getBase(cBase)[i]);
             report->reportf("\n");
           }
           //    if (config->verbosity >= 3){
           //      report->reportf("   Seq:");
           //      for (int i = 0; i < (int)getSeq(cBase).size(); i++)
           //	report->reportf(" "),dump(config, getSeq(cBase)[i]);
           //      report->reportf("\n");
           //}
#endif

           //**/report->reportf("calc_steps %" PRIu64 " > %d * 1000000\n", calc_steps, config->optbase_thres);
           //**/report->reportf("optbase uses %d primes\n", num_p);
           if (config->optbase_thres != 0 && calc_steps > (uint64)config->optbase_thres * 1000000) {
             num_p = config->optbase_reduced_primes;
             calc_steps = 0;
             if (config->verbosity >= 2) report->reportf("optbase uses %d primes\n", num_p);
           }

           for (int i = 0; i < num_p; i++){
             int p = primes[i];

             //if (seq[seq.size()-1] / B < (Int)p) break;
             if (seq_div_B[seq_div_B.size()-1] < (Int)p) break;

             int rest = (obt == obt_comb ? getCarry(cBase) : 0);  //, seq_shrink = 0;
                                                                  //vec<int> rest_seq;
             rest_seq.clear();
             // int rest = 0, seq_shrink = 0;
             int digit;
             //vec<Int> new_seq;
             new_seq.clear();
             std::vector<int> new_base = getBase(cBase);
             sum_msb = 0; new_seq_len = 0;
             for (int j = 0; j < seq.size(); j++){
               digit = toint(seq_div_B[j] % Int(p));
               if (digit > 0){
                 rest += digit;
                 if (obt == obt_kind)
                   rest_seq.push(digit);
               }
               div = seq_div_B[j] / Int(p);
               if (div > 0){
                 sum_msb += div;
                 if (obt == obt_kind || config->verbosity >= 3)
                   new_seq.push(div);
                 new_seq_len++;
               }
               config->stats_opt_base_calc_steps++;
               calc_steps++;
             }

             int new_costA;
             if(obt == obt_kind){
               sort(rest_seq);
               //	new_costA = costA + CountKind_Sort(rest_seq) + rest_seq.size() - seq_len + new_seq_len;
               new_costA = costA + count_weighted_sort(rest_seq);
               //report->reportf("old_constA=%d, kind-rest=%d, size-rest=%d, seq_len=%d, new_seq_len=%d\n", getCostA(cBase), CountKind_Sort(rest_seq), rest_seq.size(), seq_len, new_seq_len);
             } else {
               new_costA = costA + rest - seq_len + new_seq_len;
               //report->reportf("old_constA=%d, rest=%d, seq_len=%d, new_seq_len=%d\n", getCostA(cBase), rest, seq_len, new_seq_len);
             }

#ifdef DEBUG
             if (config->verbosity >= 4){
               report->reportf("prime=%d, rest=%d, New seq:", p, rest);
               for (int j = 0; j < new_seq.size(); j++)
                 report->reportf(" %d", toint(new_seq[j]));
               report->reportf("\n");
               report->reportf("1: new_costA=%d\n", new_costA);
             }
#endif


             if (new_costA <= cost_bestfound) {
               new_base.push_back(p);

#ifdef DEBUG
               if (config->verbosity >= 4){
                 report->reportf("2: new_base:");
                 for(int j = 0; j < (int)new_base.size(); j++)
                   report->reportf(" %d", new_base[j]);
                 report->reportf("  new_costA=%d, new_carry=%d, cand_cost_bestfound=%d\n", new_costA, rest/p, new_costA - new_seq_len + toint(sum_msb));
               }
#endif
               container2 ncbase;
               if (!memo.peek(B*p, ncbase)) {
                 Q.push(Pair_new(new_costA, B*p));
                 memo.set(B*p, Pair_new(new_costA, Pair_new(new_base, rest/p )));
               } else if (new_costA < getCostA(ncbase)) {
                 Q.push(Pair_new(new_costA, B*p));
                 memo.remove(B*p);
                 memo.set(B*p, Pair_new(new_costA, Pair_new(new_base, rest/p )));
               } // else nothing to do.

               int new_cost = INT_MAX;
               if(obt == obt_kind){
                 //	  sortUnique(new_seq);
                 //	  new_cost = new_costA - 1 + new_seq.size();
                 new_cost = new_costA + count_weighted_sort(new_seq);
               } else {
                 Int nc = (Int)new_costA - (Int)new_seq_len + sum_msb;
                 if (nc < INT_MAX) new_cost = toint(nc);
               }
               if (new_cost < cost_bestfound) {
                 base_bestfound = new_base, cost_bestfound = new_cost;
#ifdef DEBUG
                 if (config->verbosity >= 4){
                   report->reportf("  cost_bestfound=%d\n", cost_bestfound);
                 }
#endif
               }
             }
           }
         }
       }

       void OptimalBase::optimizeBase(const vec<Int>& seq, const vec<Int>& rhs, int& cost_bestfound, vec<int>& base_bestfound)
       {
         vec<int>    base;
         double start_time = cpuTime();

         cost_bestfound = INT_MAX;
         base_bestfound.clear();

         if (config->optbase_construction_alg == 0) {
#ifdef DEBUG
           if (config->verbosity >= 3) report->reportf("Optimized base by heuristic\n");
#endif
           optimizeBase(seq, 0, rhs, 0, base, cost_bestfound, base_bestfound);
         } else {
           //**/ seq.clear(); seq.push(5); seq.push(6); seq.push(7);

#ifdef DEBUG
           if (config->verbosity >= 3)  report->reportf("optimizeBase(Seq:"), dump(config, 3, seq);
#endif
               std::vector<Int> vseq; std::vector<int> vbase;
               int i;
               for (i = 0; i < seq.size(); i++) {
// #ifdef DEBUG
//                if (config->verbosity >= 3) report->reportf(" "),dump(config, seq[i]);
// #endif
               vseq.push_back(seq[i]);
               }
#ifdef DEBUG
               if (config->verbosity >= 3) report->reportf(" (%d)\n", i);
#endif

               if(config->optbase_construction_alg == 1) {
#ifdef DEBUG
               if (config->verbosity >= 3) report->reportf("Optimized base by branch and bound\n");
#endif
               optimizeBaseBB(seq, vbase);
               } else {
#ifdef DEBUG
                 if (config->verbosity >= 3) report->reportf("Optimized base by modulo equivalence\n");
#endif
                 optimizeBaseHashBB(seq, vbase);
               }

#ifdef DEBUG
               if (config->verbosity >= 2) report->reportf("Base:");
#endif
               base_bestfound.clear();
               for (int i = 0; i < (int)vbase.size(); i++) {
#ifdef DEBUG
                 if (config->verbosity >= 2) report->reportf(" %d", vbase[i]);
#endif
                 base_bestfound.push(vbase[i]);
               }
#ifdef DEBUG
               if (config->verbosity >= 2) report->reportf("\n");
#endif

         }

         config->stats_opt_base_calc_time += (cpuTime() - start_time);
       }


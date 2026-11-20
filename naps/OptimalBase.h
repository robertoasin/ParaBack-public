/**************************************************************************[OptimalBase.h]
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

#ifndef OptimalBase_h
#define OptimalBase_h

#include "PbSolver.h"
#include "Global.h"
#include "Config.h"

//=================================================================================================

enum OptBaseT  { obt_digits, obt_comb, obt_kind, opt_new };

// returns the cardinarity of the given sorted sequence as a set.
  template <class T>
int CountKind_Sort(const vec<T>& seq)
{
  assert(seq.size()>0);
  int i,c=1;
  for(i=0;i<(seq.size()-1);i++){
    if(seq[i]!=seq[i+1])
      c++;
  }
  return(c);
}

  template <class T>
int count_weighted_sort(const vec<T>& seq)  // assuming seq is sorted
{ 
  //assert(seq.size() > 0);
  if(seq.size() == 0) return 0;
  double sum = 0;
  int num_succ_dig = 1;
  for(int i = 1; i < seq.size(); i++) {
    if(seq[i-1] == seq[i]) 
      num_succ_dig++;
    else {
      sum += 1.0 / (double)num_succ_dig;
      num_succ_dig = 1;
    }  
  }
  sum += 1.0 / (double)num_succ_dig;
  return (int)(sum*10);
}

struct OptimalBase {
  Report* report;
  Config* config;
  const OptBaseT obt;

  OptimalBase(Config* _config, OptBaseT _obt, Report* _report) : report(_report), config(_config), obt(_obt) {}
  //
  void optimizeBase(const vec<Int>& seq, int carry_ins, const vec<Int>& rhs, int dcost, vec<int>& base, int& cost_bestfound, vec<int>& base_bestfound);
  void optimizeBaseBB(const vec<Int>& seq, std::vector<int>& base_bestfound);
  void optimizeBaseHashBB(const vec<Int>& seq, std::vector<int>& base_bestfound);
  void optimizeBase(const vec<Int>& seq, const vec<Int>& rhs, int& cost_bestfound, vec<int>& base_bestfound);
};


#endif

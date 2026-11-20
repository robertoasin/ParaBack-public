/*****************************************************************************************[Debug.h]
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

#ifndef Debug_h
#define Debug_h

#include "SolverTypes.h"
#include "Linear.h"
#include "Config.h"
#include "FEnv.h"

//=================================================================================================


void dump(Config* config, int level, Int num);
void dump(Config* config, int level, const vec<cchar*>* index2name, Lit p);
void dump(Config* config, int level, Formula f);
void dump(Config* config, int level, const vec<int>& base, int trunc_len = -1);
void dump(Config* config, int level, const vec<Int>& IntSeq, int trunc_len = -1);
void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<bool>& bs, int trunc_len = -1);
void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<Lit>& ps, int trunc_len = -1);
void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<Lit>& ps, const vec<Int>& Cs, int trunc_len = -1);
void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<Pair<Int,Lit> >& Csls, int trunc_len = -1);
void dump(Config* config, int level, const vec<Formula>& fs, int trunc_len = -1);
void dump(Config* config, int level, const vec<Formula>& fs, const vec<Int>& Cs, int trunc_len = -1);
void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<Lit>& ps, const vec<Int>& Cs, const vec<int>& assigns, int trunc_len = -1);
void dump(Config* config, int level, const vec<cchar*>* index2name, const Linear& pb, int trunc_len = -1);
void dump(Config* config, int level, const vec<cchar*>* index2name, const Linear& pb, const vec<int>& assigns, int trunc_len = -1);
macro void dump(Config* config, int level, const vec<cchar*>* index2name, Linear* pb, int trunc_len = -1) { dump(config, level, index2name, *pb, trunc_len); }

macro void dump(Config* config, Int num) { dump(config, 1, num); }
macro void dump(Config* config, const vec<cchar*>* index2name, Lit p) { dump(config, 1, index2name, p); }
macro void dump(Config* config, Formula f) { dump(config, 1, f); };
macro void dump(Config* config, const vec<int>& base, int trunc_len = -1) { dump(config, 1, base, trunc_len); }
macro void dump(Config* config, const vec<Int>& IntSeq, int trunc_len = -1) { dump(config, 1, IntSeq, trunc_len); }
macro void dump(Config* config, const vec<cchar*>* index2name, const vec<bool>& bs, int trunc_len = -1) { dump(config, 1, index2name, bs, trunc_len); }

macro void dump(Config* config, const vec<cchar*>* index2name, const vec<Lit>& ps, int trunc_len = -1) { dump(config, 1, index2name, ps, trunc_len); }
macro void dump(Config* config, const vec<cchar*>* index2name, const vec<Lit>& ps, const vec<Int>& Cs, int trunc_len = -1) { dump(config, 1, index2name, ps, Cs, trunc_len); };
macro void dump(Config* config, const vec<cchar*>* index2name, const vec<Pair<Int,Lit> >& Csls, int trunc_len = -1) { dump(config, 1, index2name, Csls, trunc_len); }
macro void dump(Config* config, const vec<cchar*>* index2name, const vec<Lit>& ps, const vec<Int>& Cs, const vec<int>& assigns, int trunc_len = -1) { dump(config, 1, index2name, ps, Cs, assigns, trunc_len); }
macro void dump(Config* config, const vec<Formula>& fs, int trunc_len = -1) { dump(config, 1, fs, trunc_len); }
macro void dump(Config* config, const vec<Formula>& fs, const vec<Int>& Cs, int trunc_len = -1) { dump(config, 1, fs, Cs, trunc_len); }
macro void dump(Config* config, const vec<cchar*>* index2name, const Linear& pb, const vec<int>& assigns, int trunc_len = -1) { dump(config, 1, index2name, pb, assigns, trunc_len); }
macro void dump(Config* config, const vec<cchar*>* index2name, const Linear& pb, int trunc_len = -1) { dump(config, 1, index2name, pb, trunc_len); }
macro void dump(Config* config, const vec<cchar*>* index2name, Linear* pb, int trunc_len = -1) { dump(config, 1, index2name, *pb, trunc_len); }

//=================================================================================================

#endif

/*****************************************************************************************[Debug.C]
  Copyright (c) 2005-201level,  Niklas Een, Niklas Sorensson

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

#include "Global.h"
#include "Debug.h"
#include "Report.h"

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
int truncSize(int n, int tsize) {
  if (tsize == 0) return n;
  return min(n, tsize);
}

inline int effectiveTruncLen(const Config* config, int trunc_len) {
  return (trunc_len >= 0) ? trunc_len : config->report_trunc_len;
}

void dump(Config* config, int level, Int num) {
  if      (num == Int_MIN) config->report.reportf(level,  "-oo");
  else if (num == Int_MAX) config->report.reportf(level,  "+oo");
  else                     { char* tmp = toString(num); config->report.reportf(level,  "%s", tmp); xfree(tmp); } }


  void dump(Config* config, int level, const vec<cchar*>* index2name, Lit p) {
    if (index2name == NULL)
      config->report.reportf(level,  "%s%d", sign(p)?"~":"", var(p));
    else{
      if (var(p) < index2name->size())
        config->report.reportf(level,  "%s%s", sign(p)?"~":"", (*index2name)[var(p)]);
      else
        config->report.reportf(level,  "%s@%d", sign(p)?"~":"", var(p));
    }
  }


void dump(Config* config, int level, Formula f) {
  if (f == _0_)      config->report.reportf(level,  "_0_");
  else if (f == _1_) config->report.reportf(level,  "_1_");
  else if (Atom_p(f))
    config->report.reportf(level,  "%sv%d", sign(f)?"~":"", index(f));
  else
    config->report.reportf(level,  "%s@%d", sign(f)?"~":"", index(f));
}

void dump(Config* config, int level, const vec<int>& base, int trunc_len)
{
  int sz = truncSize(base.size(), effectiveTruncLen(config, trunc_len));
  for (int i = 0; i < sz; i++)
    config->report.reportf(level,  "%d ", base[i]);
}

void dump(Config* config, int level, const vec<Int>& IntSeq, int trunc_len)
{
  int sz = truncSize(IntSeq.size(), effectiveTruncLen(config, trunc_len));
  for (int i = 0; i < sz; i++)
    dump(config, level, IntSeq[i]), config->report.reportf(level, " ");
}
void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<bool>& bs, int trunc_len)
{ int i;
  int sz = truncSize(bs.size(), effectiveTruncLen(config, trunc_len));
  for (i = 0; i < sz; i++){
    // 'bs' (e.g. 'best_model') may cover unnamed clausification/solver-internal vars.
    cchar* name = (i < index2name->size() && (*index2name)[i] != NULL) ? (*index2name)[i] : "@unnamed";
    config->report.reportf(level,  "%s%s, ", bs[i]?"":"-", name);
  }
  if (i+1 < bs.size()) config->report.reportf(level,  "...");
}

void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<Lit>& ps, int trunc_len)
{ int i;
  int sz = truncSize(ps.size(), effectiveTruncLen(config, trunc_len));
  config->report.reportf(level,  "(%d)",ps.size());
  for (i = 0; i < sz; i++){
    dump(config, level, index2name, ps[i]);
    config->report.reportf(level,  " ");
  }
  if (i+1 < ps.size()) config->report.reportf(level,  "...");
}

void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<Lit>& ps, const vec<Int>& Cs, int trunc_len)
{   int i;
  int sz = truncSize(ps.size(), effectiveTruncLen(config, trunc_len));
  assert(ps.size() == Cs.size());
  config->report.reportf(level,  "(%d)",ps.size());
  for (i = 0; i < sz; i++){
    dump(config, level, Cs[i]);
    config->report.reportf(level,  "*");
    dump(config, level, index2name, ps[i]);
    config->report.reportf(level,  " ");
  }
  if (i+1 < ps.size()) config->report.reportf(level,  "...");
}

void dump(Config* config, int level, const vec<cchar*>* index2name,const vec<Pair<Int,Lit> >& Csls, int trunc_len)
{   int i;
  config->report.reportf(level,  "(%d)",Csls.size());
  for (i = 0; i < truncSize(Csls.size(), effectiveTruncLen(config, trunc_len)); i++){
    dump(config, level, Csls[i].fst);
    config->report.reportf(level,  "*");
    dump(config, level, index2name, Csls[i].snd);
    config->report.reportf(level,  " ");
  }
  if (i+1 < Csls.size()) config->report.reportf(level,  "...");
}

void dump(Config* config, int level, const vec<Formula>& fml, int trunc_len)
{   int i;
  int sz = truncSize(fml.size(), effectiveTruncLen(config, trunc_len));
  config->report.reportf(level,  "(%d)",fml.size());
  for (i = 0; i < sz; i++){
    dump(config, level, fml[i]);
    config->report.reportf(level,  " ");
  }
  if (i+1 < fml.size()) config->report.reportf(level,  "...");
}

void dump(Config* config, int level, const vec<Formula>& fs, const vec<Int>& Cs, int trunc_len)
{   int i;
  int sz = truncSize(fs.size(), effectiveTruncLen(config, trunc_len));
  assert(fs.size() == Cs.size());
  config->report.reportf(level,  "(%d)",fs.size());
  for (i = 0; i < sz; i++){
    dump(config, level, Cs[i]);
    config->report.reportf(level,  "*");
    dump(config, level, fs[i]);
    config->report.reportf(level,  " ");
  }
  if (i+1 < fs.size()) config->report.reportf(level,  "...");
}


void dump(Config* config, int level, const vec<cchar*>* index2name, const vec<Lit>& ps, const vec<Int>& Cs, const vec<int>& assigns, int trunc_len) {   int i;
  assert(ps.size() == Cs.size());
  config->report.reportf(level,  "(%d)",ps.size());
  for (i = 0; i < truncSize(ps.size(), effectiveTruncLen(config, trunc_len)); i++){
    dump(config, level, Cs[i]);
    config->report.reportf(level,  "*");
    dump(config, level, index2name, ps[i]);
    if (assigns[var(ps[i])] == toInt(ll_Undef))
      config->report.reportf(level,  ":?");
    else if ((assigns[var(ps[i])] == toInt(ll_True) && !sign(ps[i])) || (assigns[var(ps[i])] == toInt(ll_False) && sign(ps[i])))
      config->report.reportf(level,  ":1");
    else
      config->report.reportf(level,  ":0");
    config->report.reportf(level,  " ");
  }
  if (i+1 < ps.size()) config->report.reportf(level,  "...");
}

void dump(Config* config, int level, const vec<cchar*>* index2name, const Linear& pb, int trunc_len) {
  if(pb.llt != lit_Undef) {
    dump(config, level, index2name, pb.llt); config->report.reportf(level,  " => ");
  }
  config->report.reportf(level,  "(%d)",pb.size);
  for (int i = 0; i < truncSize(pb.size, effectiveTruncLen(config, trunc_len)); i++){
    dump(config, level, pb(i));
    config->report.reportf(level,  "*");
    dump(config, level, index2name, pb[i]);
    config->report.reportf(level,  "  ");
  }
  config->report.reportf(level,  "in ["); dump(config, level, pb.lo); config->report.reportf(level,  ","); dump(config, level, pb.hi); config->report.reportf(level,  "]");
}


void dump(Config* config, int level, const vec<cchar*>* index2name, const Linear& pb, const vec<int>& assigns, int trunc_len) {
  if(pb.llt != lit_Undef) {
    dump(config, level, index2name, pb.llt);
    if (assigns[var(pb.llt)] == toInt(ll_Undef))
      config->report.reportf(level,  ":?");
    else if ((assigns[var(pb.llt)] == toInt(ll_True) && !sign(pb.llt)) || (assigns[var(pb.llt)] == toInt(ll_False) && sign(pb.llt)))
      config->report.reportf(level,  ":1");
    else
      config->report.reportf(level,  ":0");
    config->report.reportf(level,  " => ");  }
  /*
     if(pb.rlt != lit_Undef) {
     dump(config, level, pb.rlt);
     if (assigns[var(pb.rlt)] == toInt(ll_Undef))
     config->report.reportf(level,  ":?");
     else if ((assigns[var(pb.rlt)] == toInt(ll_True) && !sign(pb.rlt)) || (assigns[var(pb.rlt)] == toInt(ll_False) && sign(pb.rlt)))
     config->report.reportf(level,  ":1");
     else
     config->report.reportf(level,  ":0");
     config->report.reportf(level,  " <= ");
     }
   */
  for (int i = 0; i < truncSize(pb.size, effectiveTruncLen(config, trunc_len)); i++){
    dump(config, level, pb(i));
    config->report.reportf(level,  "*");
    dump(config, level, index2name, pb[i]);
    if (assigns[var(pb[i])] == toInt(ll_Undef))
      config->report.reportf(level,  ":?");
    else if ((assigns[var(pb[i])] == toInt(ll_True) && !sign(pb[i])) || (assigns[var(pb[i])] == toInt(ll_False) && sign(pb[i])))
      config->report.reportf(level,  ":1");
    else
      config->report.reportf(level,  ":0");
    if (i+1 < pb.size) config->report.reportf(level,  "...");
  }
  config->report.reportf(level,  "in ["); dump(config, level, pb.lo); config->report.reportf(level,  ","); dump(config, level, pb.hi); config->report.reportf(level,  "]");
}

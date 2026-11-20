/**************************************************************************************[Hardware.h]
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

#ifndef Hardware_h
#define Hardware_h

#include "FEnv.h"
#include "Config.h"
#include "Linear.h"
#include "Solver.h"
//#include "PbSolver.h"

//=================================================================================================


int  estimatedAdderCost(const Linear& c);
void oddEvenSort(vec<Formula>& fs);
void rippleAdder(const vec<Formula>& xs, const vec<Formula>& ys, vec<Formula>& out);
void addPb(const vec<Formula>& ps, const vec<Int>& Cs_, vec<Formula>& out, int bits);

struct Clausifier
{
  Solver*      sat_solver;
  Report* report;
  Config*      config;
  vec<Lit>     tmp_clause;
  vec<Formula> tmp_marked;

  /*WARNING*/ FEnv::CompMap<int>      occ;
  /*WARNING*/ FEnv::CompMap<Var>      vmap;
  /*WARNING*/ FEnv::CompMap<Lit,true> vmapp;
  FMap<bool>   seen;

  Clausifier(Solver* _s, Config* config, Report* _report) : 
    sat_solver(_s)
    , report(_report)
    , config(config)
    , occ(0)
    , vmap (var_Undef)
    , vmapp(lit_Undef) 
  {}

  inline void clause(Lit a, Lit b) {
    tmp_clause.clear(); tmp_clause.push(a); tmp_clause.push(b); sat_solver->addClause(tmp_clause); }
  inline void clause(Lit a, Lit b, Lit c) {
    tmp_clause.clear(); tmp_clause.push(a); tmp_clause.push(b); tmp_clause.push(c); sat_solver->addClause(tmp_clause); }
  inline void clause(Lit a, Lit b, Lit c, Lit d) {
    tmp_clause.clear(); tmp_clause.push(a); tmp_clause.push(b); tmp_clause.push(c); tmp_clause.push(d); sat_solver->addClause(tmp_clause); }


  void  usage  (Formula f);
  void _collect(Formula f, vec<Formula>& out);
  void  collect(Formula f, vec<Formula>& out);

  Lit   basicClausify   (Formula f);
  Lit   polarityClausify(Formula f);

  void clausify(const vec<Formula>& fs, vec<Lit>& out);
  void clausify(const vec<Formula>& fs);
};

// FEnv::CompMap<int>      Clausifier::occ  (0);
// FEnv::CompMap<Var>      Clausifier::vmap (var_Undef);
// FEnv::CompMap<Lit,true> Clausifier::vmapp(lit_Undef);


//=================================================================================================

#endif

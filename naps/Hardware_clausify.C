/*****************************************************************************[Hardware_clausify.C]
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

#include "Hardware.h"
#include "Debug.h"

void Clausifier::usage(Formula f)
{
  if (Atom_p(f))
    return;

  occ.set(f,occ.at(f)+1);

  if (occ.at(f) == 1){
    if (Bin_p(f)){
      usage(left(f)); usage(right(f));
    }else if (ITE_p(f) || ITEp_p(f) || ITEn_p(f)){
      usage(cond(f)); usage(tt(f)); usage(ff(f));
    }else{
      assert(FA_p(f));
      usage(FA_x(f)); usage(FA_y(f)); usage(FA_c(f));
    }
  }
}

void Clausifier::collect(Formula f, vec<Formula>& out)
{
  tmp_marked.clear();
  _collect(left(f), out);
  _collect(right(f),out);
  for (int i = 0; i < tmp_marked.size(); i++)
    seen.set(tmp_marked[i],false);
}

void Clausifier::_collect(Formula f, vec<Formula>& out)
{
  if (!seen.at(f)){
    seen.set(f,true);
    tmp_marked.push(f);
    if (Bin_p(f) && op(f) == op_And && !sign(f) && occ.at(f) == 1){
      _collect(left(f) ,out);
      _collect(right(f),out);
    }
    else
      out.push(f);
  }
}

Lit Clausifier::polarityClausify(Formula f)
{
  Lit result = lit_Undef;

  if (Atom_p(f)){
#if 1
    assert(!Const_p(f));
#else
    if (Const_p(f)){
      Var x = sat_solver->newVar(); //report->reportf("pol 1: newVar = %d\n", x);
      sat_solver->addUnit(Lit(x, (f == _0_)));
      result = Lit(x);
    }else
#endif
      result = Lit(index(f),sign(f));
  }else if (vmapp.at(f) != lit_Undef &&
      ( !config->simp_solver || !sat_solver->varElimed(var(vmapp.at(f))) ) ){
    result = vmapp.at(f);
  }else{
    result = (vmapp.at(~f) != lit_Undef &&
        (!config->simp_solver || !sat_solver->varElimed(var(vmapp.at(~f)))) ) ?
      Lit(var(vmapp.at(~f))) : Lit(sat_solver->newVar(!config->branch_pbvars));
    if(vmapp.at(f) != lit_Undef) config->stats_regen_variables++;

    //        if(vmapp.at(~f) == lit_Undef)
    //          result = Lit(sat_solver->newVar(!config->branch_pbvars));
    //        else if(config->simp_solver && sat_solver->varElimed(var(vmapp.at(~f)))) {
    //          config->stats_regen_variables++;
    //          result = Lit(sat_solver->newVar(!config->branch_pbvars));
    //        } else result = Lit(var(vmapp.at(~f)));

    if (Bin_p(f)){
      if (op(f) == op_And){
        vec<Formula> conj;
        collect(f, conj);
        assert(conj.size() > 1);
        if (!sign(f)){
          for (int i = 0; i < conj.size(); i++)
            clause(~result,polarityClausify(conj[i]));
        }else{
          vec<Lit> ls;
          ls.push(result);
          for (int i = 0; i < conj.size(); i++)
            ls.push(polarityClausify(~conj[i]));
          sat_solver->addClause(ls);
        }
        //printf("and: %d = ", var(result));
        //for (int i = 0; i < conj.size(); i++)
        //    printf("%c%d ", sign(polarityClausify(conj[i])) ? '-' : ' ',
        //           var(polarityClausify(conj[i])));
        //printf("\n");
      }else{
        Lit l  = polarityClausify( left (f));
        Lit r  = polarityClausify( right(f));
        Lit nl = polarityClausify(~left (f));
        Lit nr = polarityClausify(~right(f));

        //printf("equiv:\n");
        assert(op(f) == op_Equiv);
        if (!sign(f)){
          clause(~result, nl,  r);
          clause(~result,  l, nr);
        }else{
          clause( result, nl, nr);
          clause( result,  l,  r);
        }
      }
    }else if (ITE_p(f)){
      config->stats_non_monotonic_coding++;
      Lit  c = polarityClausify( cond(f));
      Lit nc = polarityClausify(~cond(f));
      if (!sign(f)){
        Lit  a = polarityClausify( tt  (f));
        Lit  b = polarityClausify( ff  (f));
        clause(~result, nc,  a);
        clause(~result,  c,  b);
        clause( a,  b, ~result);
      }else{
        Lit na = polarityClausify(~tt  (f));
        Lit nb = polarityClausify(~ff  (f));
        clause( result, nc, na);
        clause( result,  c, nb);
        clause(na, nb,  result);
      }
    }else if (ITEn_p(f)){
      config->stats_monotonic_coding++;
      if (!sign(f)){
        Lit  c = polarityClausify( cond(f));
        Lit  a = polarityClausify( tt  (f));
        Lit  b = polarityClausify( ff  (f));
        clause(~result,  a);
        clause(~result,  c,  b);
      }else{
        Lit nc = polarityClausify(~cond(f));
        Lit na = polarityClausify(~tt  (f));
        Lit nb = polarityClausify(~ff  (f));
        clause( result, nb);
        clause( result, nc, na);
      }
    }else if (ITEp_p(f)){
      config->stats_monotonic_coding++;
      if (!sign(f)){
        Lit nc = polarityClausify(~cond(f));
        Lit  a = polarityClausify( tt  (f));
        Lit  b = polarityClausify( ff  (f));
        clause(~result,  b);
        clause(~result,  nc,  a);
      }else{
        Lit  c = polarityClausify( cond(f));
        Lit na = polarityClausify(~tt  (f));
        Lit nb = polarityClausify(~ff  (f));
        clause( result, na);
        clause( result, c, nb);
      }
    }else{
      assert(FA_p(f));

      if (isCarry(f)){
        //printf("carry:\n");
        if (!sign(f)){
          Lit  a = polarityClausify( FA_x(f));
          Lit  b = polarityClausify( FA_y(f));
          Lit  c = polarityClausify( FA_c(f));
          clause(~result,  a,  b);
          clause(~result,  c,  a);
          clause(~result,  c,  b);
        }else{
          Lit na = polarityClausify(~FA_x(f));
          Lit nb = polarityClausify(~FA_y(f));
          Lit nc = polarityClausify(~FA_c(f));
          clause( result, na, nb);
          clause( result, nc, na);
          clause( result, nc, nb);
        }
      }else{
        Lit  a = polarityClausify( FA_x(f));
        Lit  b = polarityClausify( FA_y(f));
        Lit  c = polarityClausify( FA_c(f));
        Lit na = polarityClausify(~FA_x(f));
        Lit nb = polarityClausify(~FA_y(f));
        Lit nc = polarityClausify(~FA_c(f));

        //printf("sum:\n");
        if (!sign(f)){
          clause(~result, nc,  na,  b);
          clause(~result, nc,   a, nb);
          clause(~result,  c,  na, nb);
          clause(~result,  c,   a,  b);
        }else{
          clause( result, nc,  na, nb);
          clause( result, nc,   a,  b);
          clause( result,  c,  na,  b);
          clause( result,  c,   a, nb);
        }
      }
    }
    result = Lit(var(result),sign(f));
    vmapp.set(f,result);
  }

  assert(result != lit_Undef);

  return result;
}

Lit Clausifier::basicClausify(Formula f)
{
  Var result = var_Undef;

  //**/ report->reportf("basicClausify("); dump(report, f); report->reportf(")\n");
  if (Atom_p(f)){
    assert(!Const_p(f));
    result = index(f);
  }else if (vmap.at(f) != var_Undef && !sat_solver->varElimed(vmap.at(f))){
    result = vmap.at(f);
  }else{
    result = sat_solver->newVar(!config->branch_pbvars); report->reportf("bas 1: newVar = %d\n", result);
    Lit p  = Lit(result);
    if (Bin_p(f)){

      if (op(f) == op_And){
        vec<Formula> conj;
        collect(f, conj);
        assert(conj.size() > 1);
        for (int i = 0; i < conj.size(); i++)
          clause(~p,basicClausify(conj[i]));

        tmp_clause.clear();
        tmp_clause.push(p);
        for (int i = 0; i < conj.size(); i++)
          tmp_clause.push(~basicClausify(conj[i]));
        sat_solver->addClause(tmp_clause);
      }else{

        Lit l = basicClausify(left (f));
        Lit r = basicClausify(right(f));

        //printf("equiv:\n");
        assert(op(f) == op_Equiv);
        clause(~p, ~l,  r);
        clause(~p,  l, ~r);
        clause( p, ~l, ~r);
        clause( p,  l,  r);
      }
    }else if (ITE_p(f)){
      config->stats_non_monotonic_coding++;
      Lit c = basicClausify(cond(f));
      Lit a = basicClausify(tt  (f));
      Lit b = basicClausify(ff  (f));
      /*
         if(config->convert_bdd_monotonic) {
         clause(~p,  a);
         clause(~p,  c,  b);
         clause( p, ~b);
         clause( p, ~c, ~a);
         } else {
       */
      clause(~p, ~c,  a);
      clause(~p,  c,  b);
      clause( p, ~c, ~a);
      clause( p,  c, ~b);

      // not neccessary !!
      clause(~a, ~b,  p);
      clause( a,  b, ~p);
    }else if (ITEn_p(f)){
      config->stats_monotonic_coding++;
      Lit c = basicClausify(cond(f));
      Lit a = basicClausify(tt  (f));
      Lit b = basicClausify(ff  (f));
      clause(~p,  a);
      clause(~p,  c,  b);
      clause( p, ~b);
      clause( p, ~c, ~a);
    }else if (ITEp_p(f)){
      config->stats_monotonic_coding++;
      Lit c = basicClausify(cond(f));
      Lit a = basicClausify(tt  (f));
      Lit b = basicClausify(ff  (f));
      clause(~p,  b);
      clause(~p, ~c,  a);
      clause( p, ~a);
      clause( p,  c, ~b);
    }else{
      assert(FA_p(f));

      Lit a = basicClausify(FA_x(f));
      Lit b = basicClausify(FA_y(f));
      Lit c = basicClausify(FA_c(f));

      if (isCarry(f)){
        //printf("carry:\n");
        clause(~p,  a,  b);
        clause(~p,  c,  a);
        clause(~p,  c,  b);
        clause( p, ~c, ~a);
        clause( p, ~c, ~b);
        clause( p, ~a, ~b);
      }else{
        //printf("sum:\n");
        clause(~p, ~c,  ~a,  b);
        clause(~p, ~c,   a, ~b);
        clause(~p,  c,  ~a, ~b);
        clause(~p,  c,   a,  b);
        clause( p, ~c,  ~a, ~b);
        clause( p, ~c,   a,  b);
        clause( p,  c,  ~a,  b);
        clause( p,  c,   a, ~b);
      }
    }
    vmap.set(f,result);
    }

    assert(result != var_Undef);

    return Lit(result,sign(f));
  }


  void Clausifier::clausify(const vec<Formula>& fs, vec<Lit>& out)
  {

    for (int i = 0; i < fs.size(); i++)
      usage(fs[i]);

    if (config->convert_weak) {
      for (int i = 0; i < fs.size(); i++)
        if(fs[i] != _1_ )
          out.push(polarityClausify(fs[i]));
    } else
      for (int i = 0; i < fs.size(); i++)
        if(fs[i] != _1_ )
          out.push(basicClausify(fs[i]));

    if( ! FEnv::check_nodes_size() ){
      report->reportf("FEnv nodes overflow\n");
      raise(SIGTERM);
    }
  }


  void Clausifier::clausify(const vec<Formula>& fs)
  {
    vec<Lit>  out;
    clausify(fs, out);
#ifdef DEBUG
    for(int i=0; i<fs.size(); i++)
      assert(fs[i] != _0_ );
#endif
    //     report->reportf("clausify: out literals:");
    //     for(int i=0; i<out.size(); i++)
    //       report->reportf(" %d: ",i),dump(report, out[i]);
    //     report->reportf("\n");

    for (int i = 0; i < out.size(); i++)
      sat_solver->addUnit(out[i]);
  }

#ifndef Linear_h
#define Linear_h

#include "SolverTypes.h"

//=================================================================================================
// Linear -- a class for storing pseudo-boolean constraints:

class Linear {
  int     orig_size;  // Allocated terms in constraint.
  public:
  int     size;       // Terms in constraint.
  Int     lo, hi;     // Sum should be in interval [lo,hi] (inclusive).
  Lit     llt;     // Literal implies constraint (lit_Undef for normal case).
                   //    Lit     rlt;     // Constraint implies literal (lit_Undef for normal case)
  private:
  char    data[0];    // (must be last element of the struct)
  public:
  // NOTE: Cannot be used by normal 'new' operator!
  Linear(const vec<Lit>& ps, const vec<Int>& Cs, Int low, Int high, Lit ll) {
    orig_size = size = ps.size(), lo = low, hi = high; llt = ll; 
    char* p = data;
    for (int i = 0; i < ps.size(); i++) *(Lit*)p = ps[i], p += sizeof(Lit);
    for (int i = 0; i < Cs.size(); i++) new ((Int*)p) Int(Cs[i]), p += sizeof(Int); }

  // NOTE: Cannot be used by normal 'delete' operator either!  'Linear' lives on raw memory
  // (from 'xmalloc()' or a 'StackAlloc'), so this destructor must be invoked explicitly
  // before that memory is released.  The coefficients were placement-new'd above and own
  // heap data in the GMP build (each 'Int' holds an 'mpz_t'), so skipping this leaks them.
  // 'lo' and 'hi' are ordinary members and are destructed automatically.
  ~Linear() {
    Int* Cs = (Int*)(data + sizeof(Lit)*orig_size);
    for (int i = 0; i < orig_size; i++) Cs[i].~Int(); }

  Lit operator [] (int i) const { return *(Lit*)(data + sizeof(Lit)*i); }
  Int operator () (int i) const { return *(Int*)(data + sizeof(Lit)*orig_size + sizeof(Int)*i); }
  Lit& operator [] (int i) { return *(Lit*)(data + sizeof(Lit)*i); }
  Int& operator () (int i) { return *(Int*)(data + sizeof(Lit)*orig_size + sizeof(Int)*i); }

};


// Destruct and release a 'Linear' allocated with 'xmalloc()'.  For 'Linear's living on a
// 'StackAlloc', call '~Linear()' directly and let the allocator release the raw memory.
macro void freeLinear(Linear* c) {
  if (c != NULL) { c->~Linear(); xfree((char*)c); } }

#endif

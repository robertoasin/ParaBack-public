# Modifications for ParaBack

This directory is SCIP (https://github.com/scipopt/scip) at commit
`03f5846fe7ca4ebf8734d9abc03b9832f52fa4c7`, distributed under the Apache License 2.0
(`LICENSE`), with changes made by the ParaBack authors in 2026. `paraback.patch` contains every
change with respect to that commit, and each modified file carries a notice in its first line.

* Exact LP solves can be interrupted from another thread: `SCIPlpiExactInterrupt()` in the exact
  LP interfaces (`src/lpiexact/*`) and the interruption of the exact and project-and-shift LPs
  (`src/scip/lpexact.*`, `src/scip/scip_lp.c`); an interruption requested
  while the problem is being transformed or presolved is no longer cleared (`src/scip/scip_solve.c`).
* Thread safety: the LP interface structure is published only once it is complete
  (`src/lpi/lpi_spx.cpp`, `src/lpiexact/lpiexact_spx.cpp`), and the global rationals of RECTLU
  are per thread (`src/rectlu/rectlu_factor.c`).
* The OPB reader keeps coefficients exact in exact solving mode (`src/scip/reader_opb.c`).
* The last exact lower bound is reset between solves of the same problem (`src/scip/stat.c`).

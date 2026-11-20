# Modifications for ParaBack

This directory is PySCIPOpt (https://github.com/scipopt/PySCIPOpt) at commit
`c1fb4309af73ba4da8ff0ac085817cb769a6dd0d`, distributed under the MIT License (`LICENSE`), with
changes made by the ParaBack authors in 2026: bindings for SCIP's exact solving mode (exact linear
constraints, bounds, objective and solution values) and for interrupting the LP
(`src/pyscipopt/scip.pxd`, `src/pyscipopt/scip.pxi`).
`paraback.patch` contains every change with respect to that commit.

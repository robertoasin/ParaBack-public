# Modifications for ParaBack

This directory is SoPlex (https://github.com/scipopt/soplex) at commit
`996f0329ea7b7b8850520cf76a3a9f24767f134c`, distributed under the Apache License 2.0
(`LICENSE`), with changes made by the ParaBack authors in 2026. `paraback.patch` contains every
change with respect to that commit, and each modified file carries a notice in its first line.

* Rational solves can be interrupted through a flag set from another thread
  (`src/soplex.h`, `src/soplex.hpp`, `src/soplex/solverational.hpp`).

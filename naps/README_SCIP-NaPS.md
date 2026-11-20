# SCIP-NaPS

## Overall:

*SCIP-NaPS* is a pseudo-Boolean solver that invokes SCIP first and then NaPS.
The solver is for decision problems and Optimization problems.

---

## Installation

The software uses Python interface for the SCIP Optimization Suite
  ```pip PySCIPOpt```

See `INSTALL.md` for its installation details.

## Excecution example

```
./scip-naps.py --pbsolver=./naps-bignum_static PROBLEM.opb
```

## Usage

For showing its usage, try 
  ```scip-naps.py --help```

Be sure to specify NaPS by `--pbsolver`, otherwise it will not be invoked.

---

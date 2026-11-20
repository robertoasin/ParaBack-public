# pynaps

[日本語](README.ja.md)

Python interface for the pseudo-Boolean constraint optimization solver
[NaPS](https://www.trs.css.i.nagoya-u.ac.jp/projects/NaPS/). This is a thin
binding that mirrors the C++ API (`PbSolver` / `PbSolver_Minimize` /
`PbSolver_Counting`) with identically named methods.


## Installation

```
pip install pynaps
```

Using a [venv virtual environment](https://docs.python.org/3/library/venv.html)
is recommended to avoid conflicts with system packages.

> Currently distributed for Linux, but can also be built on `MacOS` and
> `FreeBSD`. For instructions on building from source and generating wheels,
> see
> [PYTHON_DEVELOPMENT.md](https://gitlab.com/naps2040334/naps/PYTHON_DEVELOPMENT.md)
> at the repository root.

## Usage

```python
# Only needed when running from a source checkout (not required after
# `pip install pynaps`):
# import sys; sys.path.insert(0, "build_python")
import naps

cfg = naps.Config()
cfg.verbosity = 1
s = naps.PbSolverMinimize(cfg)

x1, x2, x3 = s.getVar("x1"), s.getVar("x2"), s.getVar("x3")
L = naps.Lit
# 2 x1 + 3 x2 + 6 x3 <= 7   (ineq: -2 '<', -1 '<=', 0 '==', 1 '>=', 2 '>')
s.addConstr([L(x1), L(x2), L(x3)], [2, 3, 6], rhs=7, ineq=-1)
# goal: 1 x1 - 1 x2 - 1 x3  (minimize)
s.addGoal([L(x1), L(x2), L(x3)], [1, -1, -1])

s.coding()
status = s.solve()                     # -> naps.SolverStatus
assert status == naps.SolverStatus.best
print(s.get_best_goalvalue())          # Python int (arbitrary precision)
print(s.best_model)                    # list[bool] (indexed by the variable numbers returned by getVar)
```

Main API:

| Python | Corresponding C++ | Notes |
|---|---|---|
| `Config()` | `Config` | Exposes `verbosity`, `use_scip`, `minimization_strategy`, etc. as properties |
| `Lit(var, sign=False)` | `Lit` | `~lit` negates; `.var` / `.sign` accessors |
| `PbSolver(cfg)` / `PbSolverMinimize(cfg)` / `PbSolverCounting(cfg)` | respective classes | Three constructor forms: no args, `(cfg)`, or `(cfg, sat_solver)` |
| `s.addConstr(ps, Cs, rhs, ineq, ...)` | `addConstr` | Coefficients and rhs are Python `int` (arbitrary precision via GMP) |
| `s.coding()` / `s.coding(assumps)` | `coding` | The latter returns `(status, updated assumps)` |
| `s.solve()` / `s.solve(assumps)` | `solve` | The GIL is released while running |
| `s.failed_assumptions` | the `failed` output of `solve(assumps, failed)` | Failed assumptions from the most recent `solve` call (see Notes below) |
| `s.get_best_goalvalue()` | same name | Returns `None` when undefined (`Int_MIN`/`Int_MAX`) |
| `naps.parse_PB_file(cfg, path, s)` | `parse_PB_file` | Reads OPB/DIMACS input |
| `naps.Solver_invoke(SolverT.CaDiCaL, cfg)` | `Solver_invoke` | For injecting an external SAT solver |
| `naps.HAS_SCIP` | build-time `USE_SCIP` | Whether SCIP integration is available |

Enums: `SolverStatus` (`sst_*`), `SolverT` (`st_*`), `Command` (`cmd_*`),
`MinT` (`mt_*`).

## Notes

- `failed_assumptions` is only populated by the base `PbSolver`'s
  `solve(assumps)`. `PbSolverMinimize` / `PbSolverCounting`'s
  `solve(assumps)` calls the C++ `solve(vec<Lit>&)` (optimization/counting),
  so `failed` is always an empty list there too (the C++ side does not
  override `solve(vec&, vec&)` for these classes either).
- `PbSolver(cfg)` does not own the `Config` (same as in C++). The binding
  keeps the `Config` alive via `keep_alive` for the solver's lifetime, so
  this normally doesn't need to be a concern.
- There is a code path where the C++ side calls `exit()` on a parse error
  etc. (when `abort_on_error=True`), which terminates the whole Python
  interpreter. Converting this to an exception is future work.
- Assumes a GMP build (the default). Combining with `USE_64BIT_INT=ON` is
  not supported.

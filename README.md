# ParaBack

ParaBack extracts the **backbone** of pseudo-Boolean optimization problems: the literals that take
the same value in every optimal solution. It runs a portfolio of extractors based on different
solvers (integer programming with Gurobi, exact SCIP, RoundingSat, and SAT encodings with NaPS and
CaDiCaL) in parallel over MPI. The extractors share every fact they prove (backbone literals,
refuted literals and optimal solutions), so no extractor repeats work that another one has done.
Every solution of a floating-point solver is checked with exact integer arithmetic before it is
used.

This repository accompanies the article *Parallel Backbone Extraction for Pseudo-Boolean
Optimization* by Roberto Asín-Achá, Albert Oliveras and Enric Rodríguez-Carbonell (submitted).
It contains the code, three small example instances, the configurations and results of the
experiments of the article, and the backbones that ParaBack computed for the instances of all the
Pseudo-Boolean Competitions from 2005 to 2026 (`data/`).

## Installation

On Linux, with [conda](https://conda-forge.org) (or micromamba):

```bash
conda env create -f environment.yml
conda activate paraback
export CMAKE_PREFIX_PATH=$CONDA_PREFIX
pip install --no-deps -e .
make roundingsat napback scip-exact PYTHON=$(which python) NAPS_STATIC=OFF
make check-solvers PYTHON=$(which python)
```

`make` builds RoundingSat with its probing server, NaPS, CaDiCaL and CadiBack, and SoPlex, exact
SCIP and PySCIPOpt, all from the sources in this repository. The integer-programming workers need
`gurobipy` with a [Gurobi](https://www.gurobi.com) license;
the other workers do not. `make check-solvers` lists the workers that are available.

## Usage

```bash
python -m paraback --worker roundingsat:pguide=1 --worker cadiback examples/normalized-C17.opb C17.bb
```

The input is an instance in the format of the Pseudo-Boolean Competition (`.opb`, optionally
compressed with xz or gzip). The output lists one backbone literal per line (`b 12`, `b -5`) and
ends with `b 0` when the backbone is complete. `--launcher local` runs the workers as threads in a
single process, without MPI. `python -m paraback --help` lists all the options (in Spanish, like the
comments in the code).

The configurations of the article, with a time limit of one hour:

```bash
# Mix-3+MF: RoundingBack with phase guidance, NapBack and GuroBack in disjoint orders, and
# GuroBack with the maximum-flip probe
python -m paraback \
  --worker roundingsat:pguide=1,order=strided:0/3 --worker cadiback:order=strided:1/3 \
  --worker gurobi:order=strided:2/3 --worker gurobi:chunks=maxflip:5 \
  --threads-per-worker 4 --float-max-bits 32 --float-fallback scip-exact \
  --substitute roundingsat:pguide=1 --time-limit 3600 instance.opb instance.bb

# ParaBack-Full: Mix-3+MF plus the linear-relaxation and symmetry workers
python -m paraback \
  --worker roundingsat:pguide=1,order=strided:0/3 --worker cadiback:order=strided:1/3 \
  --worker gurobi:order=strided:2/3 --worker gurobi:chunks=maxflip:5 --worker lp --worker symmetry \
  --threads-per-worker 4 --float-max-bits 32 --float-fallback scip-exact \
  --substitute roundingsat:pguide=1 --time-limit 3600 instance.opb instance.bb
```

With `--float-max-bits 32`, on instances with integers above 32 bits the Gurobi workers are
replaced by exact SCIP and the linear-relaxation worker uses SoPlex; above 66 bits, both slots go
to the `--substitute` worker.

## Experiments of the article

* `experiments/` has the configuration of every round: the search for the optimal values of the
  2025 competition (`experiments-base.toml`), the five rounds of the evaluation
  (`experiments-ev1.toml` to `experiments-ev5.toml`) and the three rounds on the instances of all
  the competitions (`experiments-hist1.toml` to `experiments-hist3.toml`). Any experiment runs as
  `python -m paraback --config experiments/experiments-ev3.toml --experiment ev3-mf-all instance.opb instance.bb`;
  the `[cluster]` sections describe how we ran them with Slurm.
* `results/evN/times.tsv` has the status and time of every run of the evaluation, and
  `results/evN/compare.tsv` the size of every backbone, which agree across all the runs that
  complete an instance. `results/base25/` lists the instances of the 2025 competition with their
  optimal values and the solver that found them.
* `tools/paper/figures.py` draws the figures of the article from these files
  (`python tools/paper/figures.py --check --out figures`, with the packages of
  `tools/paper/requirements.txt`). `tools/ev_report.py` computes the tables from the log of every
  run, which is not included because of its size (about 440 MB compressed).

## Backbones of the Pseudo-Boolean Competitions

`data/` contains the result of running ParaBack on the 38 490 distinct instances of the
Pseudo-Boolean Competitions from 2005 to 2026. Two files are taken as the same instance when they
differ only in their comments, and copies with renamed variables take the result of their
representative.

* `data/instances.csv` has one row per instance: its name (`instance`), its type (`OPT-LIN`,
  `DEC-LIN`, or `OPT-NLC` and `DEC-NLC` for the non-linear ones), its numbers of `variables` and
  `constraints` and the bits of its integers (`int_bits`), the competitions where it appears
  (`first_competition`, `competitions`), its directory and file in the archives of the
  competitions (`source`, `file`), the SHA-256 of its contents without comments (`sha256`), the
  `representative` of a copy with renamed variables, its `status`, the `optimum` and the solver
  that found it (`optimum_solver`: `gurobi`, `roundingsat`, `naps` or `scip-exact`), the number of
  backbone literals (`backbone_size`, proven ones in partial backbones) and the answer recorded in
  the competitions (`competition_answer`: `sat`, `unsat`, `unsat-disputed` when the organizers
  flagged some answer as wrong, or empty when no solver answered).
* `data/backbones/*.tsv.xz` contain the backbones, one instance per line: the name, the status and
  a string with one character per variable, where the character at position *i* refers to the
  variable `x`*i*: `1` or `0` for a backbone literal with that value, `-` for a variable outside
  the backbone and, in partial backbones, `?` for a variable not proven to be in the backbone.

| status | instances | meaning |
|---|---:|---|
| `complete` | 14 260 | complete backbone |
| `partial` | 1 043 | optimal solution found (any solution, without objective) and part of the backbone proven |
| `infeasible` | 12 602 | proved infeasible by an exact solver in our runs |
| `infeasible-known` | 2 410 | infeasible according to the answers of the competitions; not run |
| `infeasible-gurobi` | 865 | declared infeasible only by Gurobi, without confirmation by an exact solver |
| `no-optimum` | 6 507 | no optimal solution found within the time limits |
| `failed` | 29 | every run ran out of memory |
| `nonlinear` | 774 | non-linear constraints; out of the scope of ParaBack |

The optimality of the optimal values found by Gurobi is not certified (column `optimum_solver`).
In Python, the backbone literals of every instance are

```python
import glob, lzma
for path in sorted(glob.glob("data/backbones/*.tsv.xz")):
    with lzma.open(path, "rt") as f:
        next(f)  # header
        for line in f:
            name, status, backbone = line.rstrip("\n").split("\t")
            literals = [i if c == "1" else -i for i, c in enumerate(backbone, start=1) if c in "01"]
```

## Third-party code

| directory | software | license | changes |
|---|---|---|---|
| `roundingsat/` | [RoundingBack](https://github.com/matiasfrancia/roundingback), based on [RoundingSat](https://gitlab.com/MIAOresearch/software/roundingsat) | MIT | probing server (`src/BackboneServer.*`) |
| `scip/`, `soplex/` | [SCIP](https://github.com/scipopt/scip), [SoPlex](https://github.com/scipopt/soplex) | Apache 2.0 | interruptible exact solves; see `MODIFICATIONS.md` |
| `PySCIPOpt/` | [PySCIPOpt](https://github.com/scipopt/PySCIPOpt) | MIT | bindings for exact solving; see `MODIFICATIONS.md` |
| `naps/` | [NaPS](https://gitlab.com/naps2040334/naps) | MIT (MiniSat+) | patches in `patches/naps/`, applied at build time |
| `cadical/`, `cadiback/` | [CaDiCaL](https://github.com/arminbiere/cadical), [CadiBack](https://github.com/arminbiere/cadiback) | MIT | patches in `patches/cadiback/`, applied at build time |
| `napback/` | [NapBack](https://github.com/bryan-alvarado-ulloa/napback) | MIT | none |
| `guroback/guroback.cpp` | [GuroBack](https://github.com/bryan-alvarado-ulloa/guroback) | MIT | none (original C++ implementation, `make cpp`) |

Gurobi is not included. The symmetry worker uses [python-igraph](https://python.igraph.org)
(GPL 2.0 or later), an optional dependency installed with the environment.

## License

The code of ParaBack is distributed under the MIT License (`LICENSE`). The third-party code keeps
its own license, in its directory.

## Citation

```bibtex
@unpublished{asinacha2026paraback,
  author = {Asín-Achá, Roberto and Oliveras, Albert and Rodríguez-Carbonell, Enric},
  title  = {Parallel Backbone Extraction for Pseudo-Boolean Optimization},
  note   = {Submitted},
  year   = {2026}
}
```

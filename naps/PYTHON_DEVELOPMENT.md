# naps Python Bindings — Development Guide

[日本語](PYTHON_DEVELOPMENT.ja.md)

This document covers building the Python bindings from source, building
wheels, and the PyPI release process. For the end-user API (usage examples,
API reference, notes on behavior), see
[python/README.md](python/README.md).

## Build

```sh
pip install pybind11            # once (pytest is also recommended: pip install pytest)
make py                         # generates build_python/naps.cpython-*.so
```

`make py` configures a separate build tree (`build_python/`) with
`BUILD_PYTHON=ON`. Since the Python extension requires `-fPIC`, the external
libraries (CaDiCaL / Kissat) are built separately from the normal build into
`cadical3/build_pic` and `kissat/build_pic`.

## Building wheels

You can build a `pip install`-able wheel (`pyproject.toml` / `setup.py`
invoke the existing CMake build — the `naps_python` target with
`BUILD_PYTHON=ON`; no changes to `CMakeLists.txt` are needed).

```sh
# Build one wheel for the currently active Python (pyenv)
python3 -m pip wheel . -w dist --no-deps

# Build for all pyenv-managed versions at once
# (default: 3.9.20 / 3.10.15 / 3.11.10 / 3.12.6). Missing versions are
# installed automatically via `pyenv install`.
make wheels
# or specify target versions explicitly
python/build_wheels.sh 3.9.20 3.12.6
```

Output goes to `dist/*.whl`. The PIC builds of cadical3/kissat
(`cadical3/build_pic`, `kissat/build_pic`) are shared across versions, so
building the second and later versions is fast.

To statically link SCIP into the wheel for a self-contained build, pass the
prefix of a PIC-built static SCIP via an environment variable (see
`INSTALL.md` for how to build that prefix;
`-DCMAKE_POSITION_INDEPENDENT_CODE=ON` is required):

```sh
SCIP_STATIC_PREFIX=/path/to/scip_static_install python3 -m pip wheel . -w dist --no-deps
```

To install and smoke-test:

```sh
python3 -m venv /tmp/naps-venv
/tmp/naps-venv/bin/pip install dist/pynaps-*.whl
/tmp/naps-venv/bin/python -c "import naps; print(naps.__version__)"
```

`tests/python/conftest.py` inserts `build_python/` at the front of
`sys.path`, so if you want to run the existing pytest suite against the
wheel build, temporarily move `build_python/` out of the way first.

Notes:

- Without `SCIP_STATIC_PREFIX`, the resulting wheel only works on **Linux
  machines that have SCIP (scipoptsuite) installed system-wide** (it
  dynamically links `libscip.so`). Embedding a static SCIP via
  `SCIP_STATIC_PREFIX` removes this constraint. The plan is to distribute on
  PyPI under the name **pynaps** (the name `naps` on PyPI is already taken
  by a different project; the import name remains `naps`). manylinux wheels
  embed a static SCIP inside the CI manylinux container and bundle the
  remaining dependencies (libgmp etc.) via `auditwheel repair` (see
  `plans/20260716_pypi_deployment.md`).
- `Version.h`'s `naps_version` is the single source of truth for the
  version, used for the wheel, PyPI, and `naps.__version__`. The new naming
  scheme (`docs/versioning.md`) is already in normalized PEP 440 form, so
  all of these stay consistent (the old naming scheme, e.g. `1.04a9`, had a
  mismatch because leading zeros were dropped, becoming `1.4a9`; the new
  scheme resolves this). `.devN` and other pre-release versions are not
  picked up by a plain `pip install` — `--pre` is required.
- In an offline environment where pip's build isolation (which
  auto-fetches pybind11) is unavailable, first run
  `pip install pybind11 setuptools wheel` against the target interpreter,
  then use `pip wheel . -w dist --no-deps --no-build-isolation`.

## Publishing to PyPI (GitLab CI)

`.gitlab-ci.yml` combined with cibuildwheel (configured in
`pyproject.toml`'s `[tool.cibuildwheel]`) builds manylinux_2_28 wheels
(CPython 3.9–3.13, x86_64) and publishes them to PyPI via Trusted
Publishing. The static SCIP is built inside the CI container by
`ci/build_static_scip.sh`.

See `docs/versioning.md` for the version naming scheme (PEP 440-compliant:
`.devN` for development versions, no qualifier for stable releases,
`.postN` for post-release fixes). Release steps (see
`report/20260717_pypi_deployment_phase2.md` for one-time setup such as
registering the Trusted Publisher):

1. Update `naps_version` in `Version.h` and commit
2. Push a tag matching the version (e.g. `git tag v1.4.1 && git push origin v1.4.1`)
   — CI detects and fails on a tag/`Version.h` mismatch
3. After the pipeline's `build_wheels` completes, manually trigger
   `publish_testpypi` (rehearsal) or `publish_pypi`

To try just the wheel build without pushing a tag, use GitLab's Run
pipeline (Web UI). Passing `CIBW_BUILD=cp312-*` etc. as a variable narrows
the build to a single target Python version.

## Testing

There is a pytest port that corresponds 1:1 with `tests/pb_solver_test.cpp`
(run from the repository root):

  * Add `-s` to also show `print` output.

  ```sh
  python3 -m pytest tests/python/test_pb_solver.py -v
  ```

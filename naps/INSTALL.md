# Installation of NaPS and SCIP-NaPS

*NOTE!*  You need GNU-make and GCC to compile this software.

## INSTALLATION of NaPS

We recommend to BigNum vesion, which is compiled by

```
make r
```

### Enabling SCIP in NaPS

`USE_SCIP` defaults to `ON`, so `scipoptsuite` must be available for the build
to succeed. If SCIP is not needed, pass `-D USE_SCIP=OFF` to cmake (e.g.
uncomment the `USE_SCIP=OFF` line in `Makefile`).

#### Dynamic build (`make r`, `make d`, ...)

* Install `scipoptsuite` before compiling NaPS.
  - Easy way: Download precompiled packages from [SCIP website](https://www.scipopt.org/index.php#download)
    (or `apt install scipoptsuite` on Debian/Ubuntu). This provides `libscip.so`,
    which is enough for dynamic-linked builds.
* If `libscip.so` cannot be found under `/usr/lib`, `/lib`, `/usr/local/lib`,
  the build fails with a clear error. Pass `-D USE_SCIP=OFF` if you want to
  build without SCIP instead.

#### Static build (`make rs`, `make rx`)

Precompiled `scipoptsuite` packages only provide `libscip.so`, not the static
`libscip.a` needed for `--static` linking. To build a static NaPS binary with
SCIP support, compile SCIP from source as a static (PIC) library and install
it under `scip_static/` at the repository root:

```sh
# Get the scipoptsuite source (SCIP + SoPlex) matching your target version,
# e.g. from https://github.com/scipopt/scip and https://github.com/scipopt/soplex
# or the full SCIP Optimization Suite bundle.

cmake -S <scipoptsuite-src> -B <scipoptsuite-src>/build_static \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=<path-to-naps>/scip_static \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DNO_EXTERNAL_CODE=ON -DSHARED=OFF -DLPS=spx -DSYM=snauty -DTPI=none \
  -DAMPL=OFF -DGCG=OFF -DUG=OFF -DBUILD_TESTING=OFF
cmake --build <scipoptsuite-src>/build_static --parallel
cmake --install <scipoptsuite-src>/build_static
```

(`BUILD_TESTING=OFF` avoids a known issue where SCIP's `examples/` subdirectory
picks up a system-installed `scip-config.cmake` instead of the one being built.
`CMAKE_POSITION_INDEPENDENT_CODE=ON` also allows the same static SCIP to be
linked into the Python extension module, see `PYTHON_DEVELOPMENT.md`.)

Once `scip_static/lib/cmake/scip/scip-config.cmake` exists, `make rs` /
`make rx` will find it automatically. To use a different location, pass
`SCIP_STATIC_PREFIX=<path>` to `make`, e.g.:

```sh
make rs SCIP_STATIC_PREFIX=/path/to/scip_static_install
```

If no static SCIP is found, the build fails at cmake-configure time with a
message pointing back to this section. Pass `-D USE_SCIP=OFF` if you want a
static build without SCIP support.

### TIPS:

* For compile error related to gmp.h, install libgmp3-dev gmp-devel library.

* For compile error related to zlib.h, install zlib-devel library.

* To compile binaries for test, install googletest library.

  ### TIPS on FreeBSD:

  * Use `gmake` for `make`.

## INSTALLATION of SCIP-NaPS

* pythin library

  - PySCIPOpt

    ```
    pip3 install pyscipopt
    ```

### TIPS on FreeBSD:

- `pip install pyscipopt` do not work on FreeBSD.  Ask an administrator to install a package `py311-PySCIPOpt`, e.g.,

  ```
  pkg install py311-PySCIPOpt-5.4.1_1
  ```

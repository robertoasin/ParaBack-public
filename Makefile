# Build y ejecución de Guroback / ParaBack con cualquiera de sus backends.
#
# Los paquetes Python (`guroback/`, `paraback/`) se instalan con pip a partir de
# `pyproject.toml`; los solvers son extras opcionales, de modo que se puede
# trabajar sólo con Gurobi, sólo con SCIP o con ambos. RoundingBack es C++
# (`make roundingsat`). ParaBack usa MPI (extra `mpi` + un `mpirun`) o, sin MPI,
# la referencia con hilos (`LAUNCHER=local`). La implementación C++ original
# (legado, sólo Gurobi) se compila con `make cpp`.

PYTHON  ?= python3
PIP     ?= $(PYTHON) -m pip
PYTEST  ?= $(PYTHON) -m pytest
INSTANCE ?= examples/normalized-C17.opb
BACKBONE ?= backbone.out
REPEATS ?= 5
# ParaBack: WORKERS="gurobi scip:order=reverse roundingsat:order=random:1" (vacío: portfolio
# por defecto con los solvers disponibles), LAUNCHER=auto|mpi|local, PARABACK_ARGS extra.
WORKERS ?=
LAUNCHER ?= auto
PARABACK_ARGS ?=

# Por defecto Gurobi usa la licencia de GRB_LICENSE_FILE (una licencia WLS
# funciona en contenedores y nodos de cluster; una de nodo, no). Como alternativa de
# emergencia, GUROBI_LIMITED=1 oculta el archivo de licencia (variable de
# entorno y $HOME) para que gurobipy recurra a su licencia limitada integrada,
# suficiente para examples/ (máx. 2000 variables y 2000 restricciones).
# Sin esta variable no se toca el entorno del usuario.
GUROBI_LIMITED ?= 0
LIMITED_HOME := $(CURDIR)/.gurobi-limited-home
ifeq ($(GUROBI_LIMITED),1)
SOLVER_ENV := env -u GRB_LICENSE_FILE HOME=$(LIMITED_HOME)
PREP_ENV   := mkdir -p $(LIMITED_HOME)
else
SOLVER_ENV :=
PREP_ENV   := :
endif

.PHONY: help install install-gurobi install-scip install-all check-solvers \
        run-scip run-gurobi run-roundingsat run-paraback roundingsat napback scip-exact cpp clean

help:
	@echo "Objetivos disponibles:"
	@echo "  install          Instala guroback sin solvers (backend bruteforce)"
	@echo "  install-scip     Instala guroback con pyscipopt"
	@echo "  install-gurobi   Instala guroback con gurobipy (requiere licencia)"
	@echo "  install-all      Instala guroback + paraback con todos los solvers Python y mpi4py"
	@echo "  roundingsat      Compila RoundingBack (roundingsat/build/roundingsat) con el modo servidor"
	@echo "  check-solvers    Muestra qué backends están operativos en este entorno"
	@echo "  napback          Compila Naps (sin SCIP), CaDiCaL, Cadiback y napback-server en napback/build/"
	@echo "  scip-exact       Compila SoPlex, SCIP (modo exacto) y PySCIPOpt vendorizados (PYTHON=.venv/bin/python)"
	@echo "  (añade GUROBI_LIMITED=1 sólo si tu licencia de Gurobi no vale en este entorno)"
	@echo "  run-scip         Extrae el backbone de INSTANCE con SCIP"
	@echo "  run-gurobi       Extrae el backbone de INSTANCE con Gurobi"
	@echo "  run-roundingsat  Extrae el backbone de INSTANCE con RoundingBack (modo servidor)"
	@echo "  run-paraback     Extrae el backbone de INSTANCE con ParaBack (WORKERS, LAUNCHER, PARABACK_ARGS)"
	@echo "  cpp              Compila la implementación C++ original (legado)"

# ------------------------------------------------------------------ instalación
install:
	$(PIP) install -e .

install-scip:
	$(PIP) install -e ".[scip]"

install-gurobi:
	$(PIP) install -e ".[gurobi]"

install-all:
	$(PIP) install -e ".[all]"

# ------------------------------------------------------------------ verificación
check-solvers:
	$(PYTHON) -m guroback --list-solvers
	@command -v mpirun >/dev/null && $(PYTHON) -c "import mpi4py" 2>/dev/null \
	  && echo "mpi          disponible  [$$(mpirun --version | head -1)]" \
	  || echo "mpi          NO DISPONIBLE  (ParaBack usará --launcher local)"

# ------------------------------------------------------------------ ejecución
run-scip:
	$(PYTHON) -m guroback --solver scip $(INSTANCE) $(BACKBONE)

run-gurobi:
	@$(PREP_ENV)
	$(SOLVER_ENV) $(PYTHON) -m guroback --solver gurobi $(INSTANCE) $(BACKBONE)

run-roundingsat: roundingsat
	$(PYTHON) -m guroback --solver roundingsat $(INSTANCE) $(BACKBONE)

run-paraback: roundingsat
	@$(PREP_ENV)
	$(SOLVER_ENV) $(PYTHON) -m paraback --launcher $(LAUNCHER) $(foreach w,$(WORKERS),--worker $(w)) \
	  $(PARABACK_ARGS) $(INSTANCE) $(BACKBONE)

# ------------------------------------------------------------------ RoundingBack (C++)
# Requiere cmake, un compilador C++17 y las cabeceras de Boost (conda: libboost-devel;
# apt: libboost-dev). El binario incorpora el modo servidor que usa guroback.roundingsat_backend.
RS_BUILD := roundingsat/build
RS_BIN   := $(RS_BUILD)/roundingsat

# Además de recompilar si cambian las fuentes, se reconstruye desde cero si el
# binario existente no arranca (p. ej. enlazado contra otra versión de Boost en
# una imagen anterior) o no incluye el modo servidor.
roundingsat: $(RS_BIN)
	@$(RS_BIN) --help 2>/dev/null | grep -q backbone-server || { \
	  echo "$(RS_BIN) no arranca o no tiene --backbone-server: reconfigurando desde cero"; \
	  rm -rf $(RS_BUILD)/CMakeCache.txt $(RS_BUILD)/CMakeFiles $(RS_BIN); \
	  $(MAKE) --no-print-directory $(RS_BIN); }

$(RS_BIN): roundingsat/CMakeLists.txt $(wildcard roundingsat/src/*.cpp roundingsat/src/*.hpp)
	mkdir -p $(RS_BUILD)
	cd $(RS_BUILD) && cmake -DCMAKE_BUILD_TYPE=Release .. > cmake.log
	$(MAKE) -C $(RS_BUILD) roundingsat

# ------------------------------------------------------------------ NapBack (Naps + CaDiCaL)
# Las fuentes están vendorizadas en naps/, cadical/ y cadiback/ (tal cual en los
# commits de abajo, docs/PLAN_NAPBACK.md §1.1; sus historiales, en /.upstream/). No se
# compila dentro de ellas: Naps construye CaDiCaL 3 y Kissat en su propio árbol y
# Cadiback espera ../cadical, así que cada una se copia a napback/build/ y se
# compila allí, sin red. Naps se compila SIN SCIP (-D USE_SCIP=OFF): NapBack nunca
# lo usa (coma flotante) y su `make rs` lo descargaría de internet; para que enlace
# sin SCIP se aplica patches/naps/0001-no-scip-stubs.patch. Naps se invoca
# siempre con -GM: el exportador CaDiCaL de esta versión pierde las unidades del
# nivel 0 (V3).
# Sin sus tests de GoogleTest (CMAKE_DISABLE_FIND_PACKAGE_GTest): no los usamos, y si el
# entorno trae GTest (el conda del cluster, 2026-10-23) Naps los enlaza con su g++ del
# sistema contra una libgtest de otro compilador y la compilación falla.
NB_BUILD        := napback/build
NAPS_COMMIT     := e655ffc2abc74ed9bb63869af071d55bcfe614a8
CADICAL_COMMIT  := c60730422e758ef1cebe7aeddf2dda31c996bf04
CADIBACK_COMMIT := 789329d8fcda851085ed72f1b07d8c3f46243b8a
NAPBACK_COMMIT  := 739abb1d76c62ab54620b1090290ce7614f30130
NAPS_BIN        := $(NB_BUILD)/naps/naps_bignum_static-1.4.1
# Naps enlazado estático (libgmp.a, libz.a). OFF donde no hay libgmp.a, p. ej. el entorno
# conda de tools/cluster/setup.sh: enlaza libgmp.so por RPATH (el nombre del binario no cambia).
NAPS_STATIC     ?= ON
CADICAL_BIN     := $(NB_BUILD)/cadical/build/cadical
CADIBACK_BIN    := $(NB_BUILD)/cadiback/cadiback
NB_SERVER       := $(NB_BUILD)/napback-server

napback: $(NAPS_BIN) $(CADICAL_BIN) $(CADIBACK_BIN) $(NB_SERVER)
	@$(NAPS_BIN) -h 2>&1 | grep -q '^Naps 1.4.1:' || { echo "$(NAPS_BIN) no es Naps 1.4.1"; exit 1; }
	@echo "NapBack: $(NAPS_BIN), $(CADICAL_BIN), $(CADIBACK_BIN), $(NB_SERVER)"

# copy-src SRC DEST: copia limpia de las fuentes vendorizadas al directorio de compilación.
define copy-src
	rm -rf $(2) && mkdir -p $(dir $(2)) && cp -a $(1) $(2)
endef

# patches/naps/*.patch se aplican a la copia de compilación, nunca al código vendorizado
# (vendor-check sigue exigiendo que naps/ sea idéntico al commit fijado).
$(NAPS_BIN): $(shell find naps -type f 2>/dev/null) $(wildcard patches/naps/*.patch)
	$(call copy-src,naps,$(NB_BUILD)/naps)
	for p in $(sort $(wildcard patches/naps/*.patch)); do patch -p1 --forward --quiet -d $(NB_BUILD)/naps < $$p || exit 1; done
	cmake -S $(NB_BUILD)/naps -B $(NB_BUILD)/naps/build_nb -D CMAKE_BUILD_TYPE=Release -D STATIC_BUILD=$(NAPS_STATIC) -D USE_SCIP=OFF \
	  -D CMAKE_DISABLE_FIND_PACKAGE_GTest=ON > $(NB_BUILD)/naps-cmake.log
	cmake --build $(NB_BUILD)/naps/build_nb
	cp $(NB_BUILD)/naps/build_nb/naps $@

$(CADICAL_BIN): $(shell find cadical -type f 2>/dev/null)
	$(call copy-src,cadical,$(NB_BUILD)/cadical)
	cd $(NB_BUILD)/cadical && ./configure > configure.log && $(MAKE)

# patches/cadiback/*.patch (modo --paraback del trabajador nativo) se aplican a la copia.
$(CADIBACK_BIN): $(CADICAL_BIN) $(shell find cadiback -type f 2>/dev/null) $(wildcard patches/cadiback/*.patch)
	$(call copy-src,cadiback,$(NB_BUILD)/cadiback)
	for p in $(sort $(wildcard patches/cadiback/*.patch)); do patch -p1 --forward --quiet -d $(NB_BUILD)/cadiback < $$p || exit 1; done
	# config.hpp: lo mismo que cadiback/generate, que exige un .git; el commit es el fijado.
	cd $(NB_BUILD)/cadiback && ./configure > configure.log \
	  && printf '#define VERSION "%s"\n#define GITID "%s"\n#define BUILD "%s"\n' \
	       "$$(cat VERSION)" "$(CADIBACK_COMMIT)" "$$(sed '/^COMPILE/!d;s,^COMPILE=,,;s, -I.*,,' makefile)" > config.hpp \
	  && $(MAKE)

# Servidor de sondeos (mismo protocolo que RoundingBack) enlazado con la libcadical fijada.
$(NB_SERVER): napback_server/napback_server.cpp $(CADICAL_BIN)
	$(CXX) -O3 -DNDEBUG -std=c++17 -Wall -Wextra -Werror -I$(NB_BUILD)/cadical/src $< $(NB_BUILD)/cadical/build/libcadical.a -o $@

# ------------------------------------------------------------------ SCIP exacto (scip-exact)
# soplex/, scip/ y PySCIPOpt/ están vendorizados (último commit; historiales en /.upstream/).
# Se compilan fuera del árbol en scip_exact/ (ignorado): SoPlex con GMP y MPFR, SCIP con
# EXACTSOLVE=on (falla si falta GMP, MPFR, Boost o el LP exacto) y PySCIPOpt contra esa
# instalación, en el intérprete $(PYTHON) (p. ej. PYTHON=.venv/bin/python). PySCIPOpt se
# copia a scip_exact/build/ para que el pip no deje artefactos en el árbol vendorizado.
# Requiere libgmp-dev, libmpfr-dev y libboost-dev.
SCIP_COMMIT     := 03f5846fe7ca4ebf8734d9abc03b9832f52fa4c7
SOPLEX_COMMIT   := 996f0329ea7b7b8850520cf76a3a9f24767f134c
PYSCIPOPT_COMMIT := c1fb4309af73ba4da8ff0ac085817cb769a6dd0d
SX_BUILD        := scip_exact/build
SX_PREFIX       := $(CURDIR)/scip_exact/install
SX_SOPLEX_LIB   := $(SX_PREFIX)/lib/libsoplexshared.so
SX_SCIP_LIB     := $(SX_PREFIX)/lib/libscip.so

scip-exact: $(SX_SCIP_LIB)
	rm -rf $(SX_BUILD)/PySCIPOpt && mkdir -p $(SX_BUILD) && cp -a PySCIPOpt $(SX_BUILD)/PySCIPOpt
	SCIPOPTDIR=$(SX_PREFIX) $(PIP) install --no-deps --force-reinstall $(SX_BUILD)/PySCIPOpt > $(SX_BUILD)/pyscipopt-install.log
	@$(PYTHON) -c "from pyscipopt import Model; m = Model(); m.freeProb(); m.enableExactSolving(True); print('SCIP', m.version(), 'con modo exacto')"

$(SX_SOPLEX_LIB): $(shell find soplex -type f 2>/dev/null)
	mkdir -p $(SX_BUILD)
	cmake -S soplex -B $(SX_BUILD)/soplex -DCMAKE_BUILD_TYPE=Release -DGMP=on -DMPFR=on -DBOOST=on -DPAPILO=off \
	  -DCMAKE_INSTALL_PREFIX=$(SX_PREFIX) > $(SX_BUILD)/../soplex-cmake.log
	cmake --build $(SX_BUILD)/soplex -j$$(nproc) > $(SX_BUILD)/../soplex-build.log
	cmake --install $(SX_BUILD)/soplex > /dev/null

$(SX_SCIP_LIB): $(SX_SOPLEX_LIB) $(shell find scip/src -type f 2>/dev/null) scip/CMakeLists.txt
	mkdir -p $(SX_BUILD)
	cmake -S scip -B $(SX_BUILD)/scip -DCMAKE_BUILD_TYPE=Release -DEXACTSOLVE=on -DLPS=spx -DLPSEXACT=spx \
	  -DSOPLEX_DIR=$(SX_PREFIX) -DGMP=on -DMPFR=on -DPAPILO=off -DZIMPL=off -DIPOPT=off -DAMPL=off -DREADLINE=off \
	  -DCMAKE_INSTALL_PREFIX=$(SX_PREFIX) > $(SX_BUILD)/../scip-cmake.log
	cmake --build $(SX_BUILD)/scip -j$$(nproc) > $(SX_BUILD)/../scip-build.log
	cmake --install $(SX_BUILD)/scip > /dev/null

# ------------------------------------------------------------------ legado C++
cpp:
	$(MAKE) -C guroback guroback

clean:
	$(MAKE) -C guroback clean 2>/dev/null || true
	rm -rf build dist *.egg-info .pytest_cache $(LIMITED_HOME)
	rm -rf $(RS_BUILD)/CMakeFiles $(RS_BUILD)/CMakeCache.txt $(RS_BUILD)/Makefile $(RS_BUILD)/cmake_install.cmake $(RS_BUILD)/cmake.log $(RS_BIN)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

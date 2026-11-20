# フロントエンド用 Makefile
BUILD_BASE=build

#CXX=g++
#
## activate if FreeBSD
#OS := $(shell uname)
#ifeq ($(OS), FreeBSD)
#  CXX=g++12
#else ifeq ($(OS), Darwin)
#  CXX=/opt/homebrew/bin/g++-15
#endif

.PHONY: d d2 r r2 rs rx py wheels wheels-static scip-static clean clean-scip

# CMAKE_OPTS = -DCMAKE_MAKE_PROGRAM=${MAKE}
# CMAKE_OPTS = -DCMAKE_MAKE_PROGRAM=${MAKE} -D CMAKE_VERBOSE_MAKEFILE=ON -D USE_SCIP=OFF
CMAKE_OPTS = -DCMAKE_MAKE_PROGRAM=${MAKE} -D CMAKE_VERBOSE_MAKEFILE=ON
CMAKE_BUILD_OPTS = --parallel $(shell echo $(MAKEFLAGS) | sed -n 's/.*-j\([0-9]\+\).*/\1/p')

CMAKE_OPTS += -DCMAKE_VERBOSE_MAKEFILE=ON

VERSION := $(shell sed -n 's/.*naps_version "\(.*\)".*/\1/p' Version.h)

ifdef USE_SYSTEM_COMPILER
  CMAKE_OPTS += -D USE_SYSTEM_COMPILER=ON
endif

ifdef SCIP_STATIC_PREFIX
  CMAKE_OPTS += -D SCIP_STATIC_PREFIX=$(SCIP_STATIC_PREFIX)
endif

# 静的リンク版 (rs / rx) が要求する静的 SCIP を自動ビルドする。
# SCIP_STATIC_PREFIX が明示指定された場合は利用者任せとし、自動ビルドしない。
ifndef SCIP_STATIC_PREFIX
SCIP_STATIC_CONFIG = $(CURDIR)/scip_static/lib/cmake/scip/scip-config.cmake
rs rx: | $(SCIP_STATIC_CONFIG)
$(SCIP_STATIC_CONFIG):
	$(MAKE) scip-static
endif

# CaDiCaL バージョン選択: make ... CADICAL2=1 で 2.x (cadical2/) を使用
# デフォルトは 3.x (cadical3/)
# Debug
d:
	cmake $(CMAKE_OPTS) -B $(BUILD_BASE)_debug -D CMAKE_BUILD_TYPE=Debug -D USE_64BIT_INT=ON
	cmake --build $(BUILD_BASE)_debug $(CMAKE_BUILD_OPTS)
	cp $(BUILD_BASE)_debug/naps naps_debug-$(VERSION)

d2:
	cmake $(CMAKE_OPTS) -B $(BUILD_BASE)_debug2 -D CMAKE_BUILD_TYPE=Debug -D USE_64BIT_INT=ON -DUSE_CADICAL2=ON
	cmake --build $(BUILD_BASE)_debug2 $(CMAKE_BUILD_OPTS)
	cp $(BUILD_BASE)_debug2/naps naps_debug2-$(VERSION)

# Release
r:
	cmake $(CMAKE_OPTS) -B $(BUILD_BASE)_release -D CMAKE_BUILD_TYPE=Release
	cmake --build $(BUILD_BASE)_release $(CMAKE_BUILD_OPTS)
	cp $(BUILD_BASE)_release/naps naps_release-$(VERSION)

r2:
	cmake $(CMAKE_OPTS) -B $(BUILD_BASE)_release2 -D CMAKE_BUILD_TYPE=Release -DUSE_CADICAL2=ON
	cmake --build $(BUILD_BASE)_release2 $(CMAKE_BUILD_OPTS)
	cp $(BUILD_BASE)_release2/naps naps_release2-$(VERSION)

# Python バインディング (pybind11)。事前に pip install pybind11 が必要。
py:
	cmake $(CMAKE_OPTS) -B $(BUILD_BASE)_python -D CMAKE_BUILD_TYPE=Release -D BUILD_PYTHON=ON \
	  -D pybind11_DIR=$(shell python3 -m pybind11 --cmakedir) \
	  -D PYTHON_EXECUTABLE=$(shell python3 -c 'import sys; print(sys.executable)') \
	  -D Python_EXECUTABLE=$(shell python3 -c 'import sys; print(sys.executable)')
	cmake --build $(BUILD_BASE)_python $(CMAKE_BUILD_OPTS) --target naps_python

# Python バインディングの wheel をまとめてビルド (pyenv の複数バージョン向け)。
# 対象バージョンの指定は python/build_wheels.sh を参照。dist/ に生成される。
wheels:
	python/build_wheels.sh

wheels-static:
	SCIP_STATIC_PREFIX="$(CURDIR)/scip_static" python/build_wheels.sh

# Release Static / Bignum
rs:
	cmake $(CMAKE_OPTS) -B $(BUILD_BASE)_rs -D CMAKE_BUILD_TYPE=Release -D STATIC_BUILD=ON
	cmake --build $(BUILD_BASE)_rs $(CMAKE_BUILD_OPTS)
	cp $(BUILD_BASE)_rs/naps naps_bignum_static-$(VERSION)

# Release Static / 64bit integer
rx:
	cmake $(CMAKE_OPTS) -B $(BUILD_BASE)_rx -D CMAKE_BUILD_TYPE=Release -D STATIC_BUILD=ON -D USE_64BIT_INT=ON
	cmake --build $(BUILD_BASE)_rx $(CMAKE_BUILD_OPTS)
	cp $(BUILD_BASE)_rx/naps naps_64-bit_static-$(VERSION)

# 静的 SCIP を scip/ にダウンロード・ビルドし scip_static/ にインストールする。
# USE_SYSTEM_COMPILER を明示的に伝播し、naps 本体（CMakeLists.txt）と
# 同じコンパイラ選択方針で SCIP/SoPlex をビルドする（ABI 不一致防止）。
scip-static:
	SCIP_SRC_DIR="$(CURDIR)/scip" USE_SYSTEM_COMPILER="$(USE_SYSTEM_COMPILER)" bash install_scip_static.sh "$(CURDIR)/scip_static"

cleanPB:
	rm -rf build_*

clean:
	$(MAKE) cleanPB
	rm -rf glueminisat/build_* cadical*/cmake_stamps cadical*/build* kissat/cmake_stamps kissat/build*
	rm -f kissat/src/makefile

# SCIP の展開ソース(scip/scipoptsuite-*/)と静的インストール(scip_static/)を削除する。
# ダウンロード済み tarball (scip/*.tgz) は再ダウンロード(約19MB)を避けるため残す。
# tarball も含めて完全に消す場合は `rm -rf scip` を手動実行する。
# 再ビルド(数分〜十数分)が必要になるため clean には含めない。
clean-scip:
	rm -rf scip_static
	if [ -d scip ]; then find scip -mindepth 1 -maxdepth 1 ! -name '*.tgz' -exec rm -rf {} +; fi
#	-$(MAKE) -C $(CURDIR)/cadical clean
#	-$(MAKE) -C $(CURDIR)/kissat clean


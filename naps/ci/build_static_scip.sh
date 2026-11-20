#!/usr/bin/env bash
# cibuildwheel の before-all として manylinux_2_28 コンテナ内で実行され、
# PIC 付き静的 SCIP をビルドして $PREFIX (既定: /project/scip_static) へ
# インストールする。レシピは report/20260716_pypi_deployment_phase1.md で
# 検証済みのもの（NO_EXTERNAL_CODE=ON により SCIP/SoPlex は Boost・GMP・
# zlib・readline のいずれにも依存しない）。
#
# ローカルの scip_static/ を作り直す場合にも使える:
#   bash ci/build_static_scip.sh "$(pwd)/scip_static"
set -euo pipefail

SCIP_VERSION=10.0.2
SCIP_SHA256=eecc29f31e8c8a3089c95ef99dd310d05e1546ba40f4ff36551d75a5f5c47073
PREFIX="${1:-/project/scip_static}"

if [ -f "$PREFIX/lib/cmake/scip/scip-config.cmake" ]; then
    echo "== static SCIP already installed at $PREFIX — skipping build =="
    exit 0
fi

# naps 本体のビルドに必要な開発パッケージ（SCIP 自体はどれにも依存しない）。
# manylinux_2_28 = AlmaLinux 8 ベース。コンテナ外で実行された場合は skip。
if command -v dnf >/dev/null 2>&1; then
    dnf -y install cmake gmp-devel zlib-devel
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

curl -fsSL -o scipoptsuite.tgz \
    "https://scipopt.org/download/release/scipoptsuite-${SCIP_VERSION}.tgz"
echo "${SCIP_SHA256}  scipoptsuite.tgz" | sha256sum -c -
tar xzf scipoptsuite.tgz

cmake -S "scipoptsuite-${SCIP_VERSION}" -B build_static \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DNO_EXTERNAL_CODE=ON -DSHARED=OFF -DLPS=spx -DSYM=snauty -DTPI=none \
    -DAMPL=OFF -DGCG=OFF -DUG=OFF -DBUILD_TESTING=OFF
cmake --build build_static --parallel "$(nproc)"
cmake --install build_static

echo "== static SCIP ${SCIP_VERSION} installed to $PREFIX =="

#!/usr/bin/env bash
# ローカルの静的 SCIP を PREFIX (既定: ./scip_static) にビルド・インストールする。
# `make scip-static` から呼ばれ、`make rs` / `make rx`（静的リンク naps）が要求する
# scip_static/lib/cmake/scip/scip-config.cmake を生成する。
#
# レシピは ci/build_static_scip.sh（cibuildwheel 用・検証済み）を踏襲する
# （NO_EXTERNAL_CODE=ON により SCIP/SoPlex は Boost・GMP・zlib・readline のいずれにも
#  依存しない。CMAKE_INSTALL_LIBDIR=lib は manylinux 等の lib64 配置を回避するため必須）。
#
# ci 版との違い: ソースを一時ディレクトリではなく SCIP_SRC_DIR (既定: ./scip) に
# ダウンロード・展開して保持する。これは Linux 以外（FreeBSD 等）で SCIP に
# ローカルパッチを当ててから再ビルドできるようにするため。tgz と展開ソースは消さず、
# 展開済みなら再展開しない（手動パッチを保護する）。
#
# ファイル名について: リポジトリ直下の `rm -rf build_*`（cleanPB ターゲット）に
# 巻き込まれて誤削除されないよう、`build_` プレフィックスを避けている。
#
# 使い方:
#   bash install_scip_static.sh [PREFIX]
#   SCIP_SRC_DIR=<dir> bash install_scip_static.sh [PREFIX]
set -euo pipefail

SCIP_VERSION=10.0.2
SCIP_SHA256=eecc29f31e8c8a3089c95ef99dd310d05e1546ba40f4ff36551d75a5f5c47073

PREFIX="${1:-$(pwd)/scip_static}"
SCIP_SRC_DIR="${SCIP_SRC_DIR:-$(pwd)/scip}"
SRC="${SCIP_SRC_DIR}/scipoptsuite-${SCIP_VERSION}"
TGZ="${SCIP_SRC_DIR}/scipoptsuite-${SCIP_VERSION}.tgz"

# 冪等: 既にインストール済みならスキップ。
if [ -f "$PREFIX/lib/cmake/scip/scip-config.cmake" ]; then
    echo "== static SCIP already installed at $PREFIX — skipping build =="
    exit 0
fi

mkdir -p "$SCIP_SRC_DIR"

# ダウンロード（tgz が無ければ取得）。取得後は sha256sum で SHA256 を検証する
# （FreeBSD にも /sbin/sha256sum があり -c 形式に対応する）。万一 sha256sum が
# 無い環境では検証行が失敗するので、その場合は手動でソースを配置してから再実行する。
# なお make clean-scip は tgz を残すので、再展開・再ビルドは再ダウンロード不要。
if [ ! -f "$TGZ" ]; then
    curl -fsSL -o "$TGZ" \
        "https://scipopt.org/download/release/scipoptsuite-${SCIP_VERSION}.tgz"
fi
echo "${SCIP_SHA256}  ${TGZ}" | sha256sum -c -

# 展開（未展開のときだけ = 手動パッチを保護）。
if [ ! -d "$SRC" ]; then
    tar xzf "$TGZ" -C "$SCIP_SRC_DIR"
fi

# OS 別パッチの適用フック（当面は自動適用しない）。
#   FreeBSD 等で SCIP がそのままビルドできない場合は、この位置で
#   scip/scipoptsuite-<ver>/ に対してローカルパッチを当ててから cmake を実行する。

JOBS="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)"

# naps 本体（CMakeLists.txt 冒頭のコンパイラ選択ロジック）と同じ基準で
# コンパイラを選ぶ。ここがずれると、naps 本体（GCC/libstdc++）と
# SCIP/SoPlex（システムデフォルト、FreeBSD なら clang/libc++）とで
# 標準ライブラリの ABI が食い違い、静的リンク時に std::__1::... 系の
# 未定義参照が大量発生する。USE_SYSTEM_COMPILER が設定されていれば
# naps 本体と同様に上書きせず cmake のデフォルトに任せる。
SCIP_COMPILER_OPTS=()
if [ -z "${USE_SYSTEM_COMPILER:-}" ]; then
    case "$(uname -s)" in
        FreeBSD)
            LATEST_GXX="$(ls -1 /usr/local/bin/g++[0-9]* 2>/dev/null | sort -V | tail -n1 || true)"
            if [ -n "$LATEST_GXX" ]; then
                LATEST_GCC="${LATEST_GXX/g++/gcc}"
                SCIP_COMPILER_OPTS=(-DCMAKE_CXX_COMPILER="$LATEST_GXX" -DCMAKE_C_COMPILER="$LATEST_GCC")
            fi
            ;;
        Darwin)
            LATEST_GXX="$(ls -1 /opt/homebrew/bin/g++-[0-9]* /usr/local/bin/g++-[0-9]* 2>/dev/null | sort -V | tail -n1 || true)"
            if [ -n "$LATEST_GXX" ]; then
                SCIP_COMPILER_OPTS=(-DCMAKE_CXX_COMPILER="$LATEST_GXX")
            fi
            ;;
        *)
            if command -v g++ >/dev/null 2>&1; then
                SCIP_COMPILER_OPTS=(-DCMAKE_CXX_COMPILER="$(command -v g++)")
            fi
            ;;
    esac
fi

cmake -S "$SRC" -B "$SRC/build_static" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DNO_EXTERNAL_CODE=ON -DSHARED=OFF -DLPS=spx -DSYM=snauty -DTPI=none \
    -DAMPL=OFF -DGCG=OFF -DUG=OFF -DBUILD_TESTING=OFF \
    "${SCIP_COMPILER_OPTS[@]}"
cmake --build "$SRC/build_static" --parallel "$JOBS"
cmake --install "$SRC/build_static"

echo "== static SCIP ${SCIP_VERSION} installed to $PREFIX =="

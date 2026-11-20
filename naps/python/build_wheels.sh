#!/usr/bin/env bash
# 複数の Python バージョン (pyenv 管理) 向けに pynaps の wheel をまとめてビルドする。
# (配布名は pynaps、import 名は naps)
#
# 使い方:
#   python/build_wheels.sh                      # デフォルトのバージョン一覧でビルド
#   python/build_wheels.sh 3.9.20 3.12.6         # 指定したバージョンのみビルド
#
# 各バージョンが pyenv に未導入の場合は `pyenv install -s` で導入してからビルドする。
# 生成された wheel はリポジトリ直下の dist/ に集約される。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEFAULT_VERSIONS=(3.9.20 3.10.15 3.11.10 3.12.6)
VERSIONS=("$@")
if [ "${#VERSIONS[@]}" -eq 0 ]; then
    VERSIONS=("${DEFAULT_VERSIONS[@]}")
fi

if ! command -v pyenv >/dev/null 2>&1; then
    echo "pyenv が見つかりません。https://github.com/pyenv/pyenv を参照してください。" >&2
    exit 1
fi

PYENV_ROOT="$(pyenv root)"
mkdir -p dist

# 今回ビルドした wheel だけを最後に表示する。dist/ には過去バージョンや
# 改名前 (naps-*) の wheel が残っていることがあり、全件を列挙すると
# どれが今回の成果物か分からなくなるため。
BUILT=()

for ver in "${VERSIONS[@]}"; do
    if ! pyenv versions --bare | grep -qx "$ver"; then
        echo "== pyenv install $ver =="
        pyenv install -s "$ver"
    fi

    PYBIN="$PYENV_ROOT/versions/$ver/bin/python3"
    echo "== building wheel for Python $ver =="
    "$PYBIN" -m pip install --quiet --upgrade pip

    # 一旦作業ディレクトリに出力し、生成された wheel を記録しつつ dist/ へ移す。
    tmpdir="$(mktemp -d)"
    "$PYBIN" -m pip wheel . -w "$tmpdir" --no-deps
    for whl in "$tmpdir"/*.whl; do
        mv -f "$whl" dist/
        BUILT+=("dist/$(basename "$whl")")
    done
    rm -rf "$tmpdir"
done

echo "== done =="
printf '%s\n' ${BUILT[@]+"${BUILT[@]}"}

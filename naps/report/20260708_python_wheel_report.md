# 作業レポート: naps Python バインディングの wheel 化（複数 Python バージョン対応）

日付: 2026-07-08 / ブランチ: pybind
プラン: plans/wheel-python-moonlit-bachman.md（naps Python バインディングの wheel 化）

## 目的と結果

pybind11 バインディング（python/naps_bindings.cpp）から `pip install` 可能な wheel を
生成できるようにし、pyenv 管理下の複数 Python バージョン向けに手間なく一括生成できる
ようにした。既存の `CMakeLists.txt`（`BUILD_PYTHON` オプション・`naps_python`
ターゲット）は**無改造**のまま再利用している。

**結果: Python 3.9.20 / 3.10.15 / 3.11.10 / 3.12.6 の4バージョンすべてで wheel を
生成し、各wheelを使い捨て venv にインストールして `import naps` と
`tests/python/test_pb_solver.py`（20件）の pass を確認した。**

```
$ make wheels
== building wheel for Python 3.9.20 ==
...
== pyenv install 3.10.15 == / == pyenv install 3.11.10 ==
...
== done ==
dist/naps-1.4-cp310-cp310-linux_x86_64.whl
dist/naps-1.4-cp311-cp311-linux_x86_64.whl
dist/naps-1.4-cp312-cp312-linux_x86_64.whl
dist/naps-1.4-cp39-cp39-linux_x86_64.whl
```

各 wheel に含まれる `.so` はビルド対象バージョンと一致（`naps.cpython-39-*.so`,
`-310-`, `-311-`, `-312-`）。4バージョン一括ビルド（うち2バージョンは pyenv でのソース
ビルドを含む）で所要時間 6分14秒。cadical3/kissat の外部ソルバビルドはバージョン間で
キャッシュ共有されるため、2バージョン目以降は数秒〜十数秒で完了する。

## 成果物

| ファイル | 内容 |
|---|---|
| pyproject.toml | `[build-system]` のみ（setuptools + pybind11 を isolated build env に自動導入）（新規） |
| setup.py | `CMakeExtension`/`CMakeBuild(build_ext)`。既存 CMake ビルドを呼び出し `.so` を wheel に直接配置（新規） |
| python/build_wheels.sh | pyenv 管理下の複数バージョンを一括ビルドするスクリプト（新規） |
| Makefile | `wheels` ターゲット追加。既存 `py` ターゲットに legacy `PYTHON_EXECUTABLE` 指定を追加（後述のバグ修正） |
| .gitignore | `dist/`, `*.egg-info/`, `build_python*` を追記 |
| python/README.md | 「wheel を作る」節を追加（手順・配布時の注意・オフライン代替手順） |

## 実装のポイント

1. **CMakeLists.txt は無改造**: `pybind11_add_module()` は `LIBRARY_OUTPUT_DIRECTORY`
   を自前で上書きしないため、`cmake -DCMAKE_LIBRARY_OUTPUT_DIRECTORY=<extdir>` を
   コマンドラインから渡すだけで `.so` を wheel の配置先に直接生成させられる。
   setup.py の `CMakeBuild` はこの1点だけを利用し、既存 `Makefile` の `py:` ターゲット
   と同等の cmake 呼び出しを行う。
2. **wheel のパッケージ構成**: `naps_bindings.cpp` は `PYBIND11_MODULE(naps, m)` で
   トップレベルモジュール `naps` を直接定義しているため、`packages=[]` +
   `ext_modules=[CMakeExtension("naps")]` のみで、リポジトリ直下の雑多なファイルを
   巻き込まずに `naps*.so` と dist-info だけの wheel が生成される（`unzip -l` で確認済み）。
3. **バージョン**: `Version.h` の `naps_version "1.04"` を正規表現で読み取り wheel の
   version に使用（単一の情報源を維持）。PEP 440 正規化により wheel ファイル名上は
   `1.4` になる（実害なし、README に注記）。

## 発見したバグと修正（重要）

検証中、**3.12.6 向けにビルドしたはずの wheel に Python 3.9 用の `.so` が混入する**
不具合を発見した。原因は pybind11 3.0.4 の CMake モジュール（pybind11Common.cmake）
にあり、本プロジェクトの `cmake_minimum_required(VERSION 3.19)` が CMake 3.27 未満
（ポリシー CMP0148 が NEW にならない）かつ `PYBIND11_FINDPYTHON` 未設定の場合、
新方式の `Python_EXECUTABLE` ではなく **legacy な `PYTHON_EXECUTABLE`
（PATH 検索、pyenv shim 経由）** を使う "classic mode" にフォールバックする。
これにより、`-D Python_EXECUTABLE=<対象バージョン>` を渡していても無視され、
`.python-version` が指すバージョン（3.9.20）に常にリンクされてしまっていた
（単一バージョン運用では `.python-version` と実行中バージョンが一致するため
表面化しなかった潜在バグ）。

**修正**: `setup.py` の cmake 呼び出しと `Makefile` の `py:` ターゲットの両方で、
`-D PYTHON_EXECUTABLE=<対象python>`（legacy）と `-D Python_EXECUTABLE=<対象python>`
（新方式）を両方明示するようにした。修正後、4バージョンすべてで対象バージョンに
一致した `.so`（`cpython-39`/`-310`/`-311`/`-312`）が生成されることを確認済み。

## 検証結果

- `python3 -m pip wheel . -w dist --no-deps`（3.9.20）: 成功、`unzip -l` で内容確認
  （`naps.cpython-39-*.so` + dist-info のみ）
- 使い捨て venv へのインストール + `import naps` + `tests/python/test_pb_solver.py`
  （`build_python/` 退避の上）: 20/20 pass
- `make wheels`（デフォルト4バージョン、うち3.10.15/3.11.10は新規 `pyenv install`）:
  全バージョンで wheel 生成成功、各バージョンで `import naps` と pytest 20/20 pass
  を確認（3.9.20 / 3.10.15 / 3.11.10 / 3.12.6）
- 回帰確認: `make r`（通常リリースビルド）、`make py`（既存の in-place バインディング
  ビルド）とも Makefile 修正後も従来どおり成功。C++ 側 `pb_solver_test` も
  リポジトリルートから実行して 20/20 pass（garden 系テストは相対パス
  `Examples/garden9x9.opb` に依存するため実行時カレントディレクトリに注意、
  既存仕様どおり）

## 既知の制約（README に記載済み）

- 生成される wheel は SCIP（scipoptsuite）がシステムにインストールされた Linux
  マシンでのみ動作する。manylinux 互換ではないため PyPI 配布は不可。同じ OS
  ディストリビューション・アーキテクチャで SCIP 導入済みの他マシンへの配布は可能。
- オフライン環境では pip のビルド分離（pybind11 自動取得）が使えないため、
  事前手動 `pip install pybind11 setuptools wheel` + `--no-build-isolation` の
  代替手順が必要（README に記載）。

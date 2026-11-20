# naps Python バインディング — 開発ガイド

[English](PYTHON_DEVELOPMENT.md)

ソースからのビルド、wheel の生成、PyPI へのリリース手順をまとめています。
利用者向けの API（使い方・API 一覧・挙動に関する注意事項）は
[python/README.ja.md](python/README.ja.md) を参照してください。

## ビルド

```sh
pip install pybind11            # 初回のみ（pytest も推奨: pip install pytest）
make py                         # build_python/naps.cpython-*.so を生成
```

`make py` は `BUILD_PYTHON=ON` で別ビルドツリー（`build_python/`）を構成します。
Python 拡張には -fPIC が必要なため、外部ライブラリ（CaDiCaL / Kissat）は
`cadical3/build_pic`・`kissat/build_pic` に通常ビルドと分離して生成されます。

## wheel を作る

`pip install` できる wheel を生成できます（`pyproject.toml` / `setup.py` が既存の
CMake ビルド（`BUILD_PYTHON=ON` の `naps_python` ターゲット）を呼び出します。
CMakeLists.txt の変更は不要です）。

```sh
# 現在アクティブな Python (pyenv) 向けに1つ生成
python3 -m pip wheel . -w dist --no-deps

# pyenv 管理下の複数バージョン (デフォルト: 3.9.20 / 3.10.15 / 3.11.10 / 3.12.6)
# に対してまとめて生成。未導入バージョンは pyenv install で自動導入される
make wheels
# あるいは対象バージョンを指定
python/build_wheels.sh 3.9.20 3.12.6
```

生成物は `dist/*.whl`。cadical3・kissat の PIC ビルド（`cadical3/build_pic`,
`kissat/build_pic`）はバージョン間で共有されるため、2 つ目以降のバージョンの
ビルドは速い。

SCIP を wheel に静的リンクして自己完結させる場合は、PIC 付きでビルドした
静的 SCIP の prefix を環境変数で渡す（prefix の作り方は INSTALL.md 参照。
`-DCMAKE_POSITION_INDEPENDENT_CODE=ON` が必須）:

```sh
SCIP_STATIC_PREFIX=/path/to/scip_static_install python3 -m pip wheel . -w dist --no-deps
```

インストールして動作確認する場合:

```sh
python3 -m venv /tmp/naps-venv
/tmp/naps-venv/bin/pip install dist/pynaps-*.whl
/tmp/naps-venv/bin/python -c "import naps; print(naps.__version__)"
```

`tests/python/conftest.py` は `build_python/` を `sys.path` の先頭に挿入するため、
wheel 版に対して既存の pytest を実行したい場合は `build_python/` を一時的に
リネームするなどして退避してから実行すること。

注意点:

- `SCIP_STATIC_PREFIX` を指定しない場合、生成される wheel は **SCIP（scipoptsuite）
  がシステムにインストールされた Linux マシン**でのみ動作する（`libscip.so` を
  動的リンクしているため）。`SCIP_STATIC_PREFIX` で静的 SCIP を組み込めばこの
  制約はなくなる。PyPI へは **pynaps** という名前で配布する計画
  （PyPI の `naps` は別プロジェクトに使用済み。import 名は `naps` のまま）。
  manylinux wheel は CI 上の manylinux コンテナで静的 SCIP を組み込み、
  `auditwheel repair` で残りの依存（libgmp 等）を同梱して生成する
  （`plans/20260716_pypi_deployment.md` 参照）。
- `Version.h` の `naps_version` が wheel・PyPI・`naps.__version__` の版の出どころ。
  新命名（`docs/versioning.md`）は PEP 440 正規形なので、これらの表記はすべて一致する
  （旧命名 `1.04a9` はゼロ埋めが落ちて `1.4a9` になる非互換があったが、新命名で解消）。
  `.devN` 等の pre-release 版は `pip install` 既定では入らず `--pre` が必要。
- オフライン環境など pip のビルド分離（pybind11 の自動取得）が使えない場合は
  `pip install pybind11 setuptools wheel` を対象インタプリタへ事前に実行した上で
  `pip wheel . -w dist --no-deps --no-build-isolation` を使う。

## PyPI への公開（GitLab CI）

`.gitlab-ci.yml` + cibuildwheel（設定は `pyproject.toml` の `[tool.cibuildwheel]`）で
manylinux_2_28 の wheel（CPython 3.9–3.13, x86_64）を生成し、Trusted Publishing で
PyPI へ公開する。静的 SCIP は CI コンテナ内で `ci/build_static_scip.sh` により
ビルドされる。

バージョン命名規則は `docs/versioning.md` を参照（PEP 440 準拠。開発版は `.devN`、
安定版は修飾子なし、リリース後の修正は `.postN`）。リリース手順（Trusted Publisher の
登録などの初回セットアップは `report/20260717_pypi_deployment_phase2.md` を参照）:

1. `Version.h` の `naps_version` を更新してコミット
2. バージョンと一致するタグを push（例: `git tag v1.4.1 && git push origin v1.4.1`）
   — タグと `Version.h` の不一致は CI が検出して失敗する
3. パイプラインの `build_wheels` 完了後、`publish_testpypi`（リハーサル）または
   `publish_pypi` を手動トリガ

タグを打たずに wheel ビルドだけ試すには GitLab の Run pipeline（Web UI）を使う。
変数 `CIBW_BUILD` に `cp312-*` などを与えると対象 Python を 1 つに絞れる。

## テスト

tests/pb_solver_test.cpp と 1:1 対応の pytest 移植があります（リポジトリルートで実行）:

  * `-s` を追加することで、printメッセージも表示される

  ```sh
  python3 -m pytest tests/python/test_pb_solver.py -v
  ```

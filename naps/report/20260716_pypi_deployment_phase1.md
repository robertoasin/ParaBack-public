# PyPI 公開体制の構築 Phase 1 完了レポート（pynaps）

日付: 2026-07-16
計画書: [plans/20260716_pypi_deployment.md](../plans/20260716_pypi_deployment.md)
ブランチ: pybind

## 結論

PyPI 公開（パッケージ名 **pynaps**、import 名 `naps`）に向けたローカルで完了できる整備
（Phase 1）をすべて完了した。**静的 SCIP 10.0.2 を組み込んだ wheel の生成・インストール・
求解動作までローカルで実証済み**。残るは gitlab.com 移設後の CI 整備（Phase 2）のみ。

## 方針決定（相談の結果）

- **別リポジトリ（scipoptsuite-deploy 方式）は作らない**。scipoptsuite-deploy は複数上流
  リポジトリのバイナリ成果物作成用で PyPI とは無関係。PySCIPOpt 自身はパッケージングを
  自リポジトリ内に置いており、naps も同じ標準形とする。
- ホスティングは gitlab.com へ移設予定（大学 GitLab 廃止のため）。PyPI Trusted Publishing
  は gitlab.com 対応・cibuildwheel は GitLab CI 対応のため、GitHub ミラーは必須でない。
- wheel 対象は Linux x86_64 のみ（FreeBSD は wheel タグ非対応、macOS は当面対象外）。
- PyPI の `naps` は既存パッケージ（FPGA ツール）に使用済みのため **pynaps** を採用
  （`pynaps`・`naps-solver` とも空きであることを 2026-07-16 に確認）。

## 実施内容

### 1. 静的 SCIP 10.0.2（PIC 付き）のビルド

- ソース: `/home/sakai/proj/naps/scipoptsuite-10.0.2`（ユーザー用意）
- prefix: `/home/sakai/proj/naps/scip_static_install`（9.2.2 の旧成果物は削除して再作成）
- レシピ（9.2.2 での検証結果を継承、PIC を追加）:

```bash
cmake -S scipoptsuite-10.0.2 -B scipoptsuite-10.0.2/build_static \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/home/sakai/proj/naps/scip_static_install \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DNO_EXTERNAL_CODE=ON -DSHARED=OFF -DLPS=spx -DSYM=snauty -DTPI=none \
  -DAMPL=OFF -DGCG=OFF -DUG=OFF -DBUILD_TESTING=OFF
```

- 成果物: `libscip.a`（19.5MB, PIC）、`libsoplex.a`、`lib/cmake/scip/scip-config.cmake` 一式
- **PIC が必須の理由**: Python 拡張モジュール(.so)に静的ライブラリをリンクするには全
  オブジェクトが PIC でコンパイルされている必要がある。9.2.2 検証時の非 PIC ビルドは
  実行ファイル（make rs）には使えたが Python 拡張には使えなかった。PIC 版 1 本で両用途に対応。
- 注意: `-DBUILD_TESTING=OFF` が必要（デフォルト ON だと examples が configure され、
  システムの apt 版 scip-config.cmake を誤って拾い MPFR エラーになる）。

### 2. make rs 再検証（10.0.2/PIC）

- `make rs SCIP_STATIC_PREFIX=/home/sakai/proj/naps/scip_static_install` 成功
- `naps_bignum_static-1.04a7`: statically linked（ldd で動的依存なし）
- apt 版（同じ 10.0.2）との結果比較: `-SCIP` 5 インスタンス + `-cSCIP` 1 インスタンスで
  すべて一致（s/v/o 行の diff で確認）。同一バージョン同士の比較となり信頼性が向上。

### 3. パッケージングファイルの整備

- **pyproject.toml**: `[project]` にメタデータ集約（name=pynaps, readme, license,
  requires-python>=3.9, classifiers, dynamic version）
- **setup.py**: 静的メタデータを pyproject.toml へ移し、環境変数 `SCIP_STATIC_PREFIX` を
  cmake 引数 `-DSCIP_STATIC_PREFIX=` へ橋渡しする処理を追加
- **python/README.md**: 「PyPI 配布想定外」の記載を新方針（pynaps / 静的 SCIP +
  auditwheel / manylinux CI）に更新。wheel 例のファイル名も pynaps- に修正

### 4. wheel のローカル検証

```
dist/pynaps-1.4a7-cp312-cp312-linux_x86_64.whl (5.9MB)
```

- クリーン venv に pip install → `import naps` 成功、`naps.HAS_SCIP == True`
- 拡張モジュールの動的依存から **libscip.so が消滅**（静的組み込み成功）。
  残る依存は libgmp / libstdc++ / libm / libgcc_s / libc のみ
- 機能テスト: 小さい PB 最小化問題（2x1+3x2+6x3<=7, min x1-x2-x3）を SAT 経路と
  SCIP 経路（`cfg.use_scip=True`）の両方で求解し、最適値 -1 で一致
- `auditwheel show`: 現状タグは linux_x86_64、このマシン（glibc 2.39）でのビルドでは
  manylinux_2_39 相当に制約される → **広い互換性には manylinux_2_28 コンテナ内での
  ビルドが必要**（Phase 2 の CI 設計どおり）

## 変更ファイル（pybind ブランチ、未コミット）

| ファイル | 変更 |
|---|---|
| `CMakeLists.txt` | SCIP_STATIC_PREFIX 分岐（find_package(SCIP CONFIG) + libscip ターゲット）※前タスクから継続 |
| `Makefile` | SCIP_STATIC_PREFIX 変数の CMAKE_OPTS 橋渡し ※前タスクから継続 |
| `pyproject.toml` | メタデータ拡充（pynaps） |
| `setup.py` | メタデータ移設・SCIP_STATIC_PREFIX 対応 |
| `python/README.md` | 配布方針の更新 |
| `plans/20260716_pypi_deployment.md` | 新規（計画書） |

## Phase 2 への引き継ぎ事項

1. gitlab.com への移設完了後、`.gitlab-ci.yml` + cibuildwheel（manylinux_2_28_x86_64,
   cp39–cp313）を整備。`CIBW_BEFORE_ALL` で本レポート記載のレシピどおり静的 SCIP を
   コンテナ内ビルド（SCIP バージョンをキーにキャッシュ）
2. PyPI に `pynaps` を Trusted Publisher（gitlab.com）として登録、TestPyPI でリハーサル
3. wheel ジョブはタグ push 時のみ実行（gitlab.com 無料 CI 分数対策）
4. 保留事項: バージョン表記（`1.04a7` → PyPI 上 `1.4a7`）の整理、pybind ブランチの
   master への取り込み方針、公開時の作者メールアドレス記載の有無

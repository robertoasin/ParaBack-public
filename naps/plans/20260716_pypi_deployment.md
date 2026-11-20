# NaPS の PyPI 公開体制の構築（パッケージ名: pynaps）

## Context

NaPS の公開方針の相談を受けた。C++ バイナリ利用者にはリポジトリからのセルフコンパイルを期待し（Ubuntu/FreeBSD/macOS でビルド実績）、Python 利用者には PyPI で配布したい。「PyPI デプロイ手続きを scipoptsuite-deploy のような別リポジトリにすべきか」が論点だったが、調査の結果と質疑により以下が確定した:

- **別リポジトリは作らない**。scipoptsuite-deploy は複数上流リポジトリのバイナリ成果物（Docker・libscip zip）を作る役割で PyPI とは無関係。PyPI パッケージである PySCIPOpt 自身はパッケージングを自リポジトリ内（cibuildwheel + CI）に置いており、これが業界標準。naps も同じ形にする。
- ホスティングは **gitlab.com へ移設予定**（大学 GitLab は廃止予定）。PyPI の Trusted Publishing は gitlab.com（SaaS）に対応しており、cibuildwheel も GitLab CI を公式サポートするため、**GitHub ミラーは必須ではない**。GitHub の利点（無料 macOS/ARM runner・可視性）は、対象を Linux x86_64 のみとした今回のスコープでは決定打にならない。後から追加可能。
- ユーザー決定事項: パッケージ名 **pynaps**（import 名は `naps` のまま。PyPI で空き確認済み）、wheel 対象は **Linux x86_64 のみ**（FreeBSD は wheel タグ非対応でソースビルド、macOS は当面対象外）。
- SCIP は **10.0.2 ソース**（ユーザーが `/home/sakai/proj/naps/scipoptsuite-10.0.2/` に用意済み）を使う。apt 版と同一バージョンになるため、動作比較が同一バージョン同士で行える。10.0.2 でも `NO_EXTERNAL_CODE`（CMakeLists.txt:23）・`SHARED`（scip/CMakeLists.txt:69）・`SYM=snauty`（デフォルト、nauty ソース内蔵）が確認済みで、9.2.2 と同じレシピが使える。

前段の検証（同セッション）で、SCIP 9.2.2 の静的ビルド（`libscip.a`）と naps `make rs` への静的リンクは成功済み。`SCIP_STATIC_PREFIX` オプションが CMakeLists.txt / Makefile に追加済み（pybind ブランチ上、未コミット）。

### 技術的裏取り済みの事実

- `python/README.md:94-97` の「libscip.so 動的リンクのため manylinux 非互換、PyPI 配布想定外」は解消可能: 静的 SCIP リンク + `auditwheel repair`（残る動的依存 libgmp 等を wheel に同梱する標準ツール）で manylinux wheel を作れる。PySCIPOpt が同方式で配布中。
- **現状の `libscip.a` は -fPIC なしでビルドされており、Python 拡張 (.so) にはリンク不可**（実行ファイルには可）。`-DCMAKE_POSITION_INDEPENDENT_CODE=ON` を付けて再ビルドが必要。PIC 版 1 本で `make rs` と wheel の両方に使える。
- `naps_python` ターゲットは `${LINK_LIBS}` をリンクする（CMakeLists.txt:309）ため、`SCIP_STATIC_PREFIX` 指定時に追加変更なしで静的 SCIP を拾う。
- PyPI の `naps` は既存パッケージ（FPGA ツール、活発）に取られている。`naps-solver`・`pynaps` は空き（2026-07-16 確認）。
- setup.py は `Version.h` からバージョン抽出（`1.04a7` → PEP 440 正規化で `1.4a7` になる既知の挙動あり）。
- 現在 CI 設定は皆無（.github も .gitlab-ci.yml もなし）。ブランチは pybind（bindings と本日の変更が同居）。

## Phase 1: ローカルで完了できるパッケージング整備（gitlab.com 移設前に着手可能）

1. **計画書の保存**: 本プランを `plans/20260716_pypi_deployment.md` に保存（プロジェクト運用ルール）。
2. **静的 SCIP 10.0.2 を PIC 付きでビルド**:
   ソースは `/home/sakai/proj/naps/scipoptsuite-10.0.2`。検証済みレシピ（`NO_EXTERNAL_CODE=ON, SHARED=OFF, LPS=spx, SYM=snauty, TPI=none, AMPL=OFF, GCG=OFF, UG=OFF, BUILD_TESTING=OFF`）に `-DCMAKE_POSITION_INDEPENDENT_CODE=ON` を追加して configure・ビルド・install。
   prefix は `/home/sakai/proj/naps/scip_static_install` を継続使用するが、**9.2.2 の残骸と混ざらないよう先に中身を削除**してからインストールする。
   再検証: `make rs SCIP_STATIC_PREFIX=...` が 10.0.2/PIC でも成功し、`-SCIP`/`-cSCIP` の結果が apt 版（同じ 10.0.2）と一致すること。
3. **pyproject.toml のメタデータ拡充**: `name = "pynaps"`、dynamic version（Version.h 由来）、readme、license、`requires-python = ">=3.9"`、classifiers。build-system は現行の setuptools + pybind11 を維持（scikit-build への移行はしない — 変更最小化）。
4. **setup.py の対応**: パッケージ名変更の整合、環境変数 `SCIP_STATIC_PREFIX` があれば cmake 引数 `-DSCIP_STATIC_PREFIX=...` に橋渡しする数行を追加。
5. **ローカル wheel ビルド検証**:
   - `SCIP_STATIC_PREFIX=... pip wheel . -w dist --no-deps`
   - `python -c "import naps"` と小さな PB 問題の求解（SCIP 経由の API があれば SCIP パスも）
   - `auditwheel show dist/*.whl` で不足 .so（libgmp 等）の一覧を確認（完全な manylinux 化は CI のコンテナ内で行うため、ここでは情報収集まで）
6. **ドキュメント更新**: `python/README.md` の「PyPI 配布想定外」記載を新方針（pynaps / 静的 SCIP + auditwheel）に書き換え。

## Phase 2: CI と公開（gitlab.com 移設後 — 移設完了が前提）

1. `.gitlab-ci.yml` を作成: cibuildwheel（`manylinux_2_28_x86_64`、cp39–cp313）。`CIBW_BEFORE_ALL` で静的 SCIP 10.0.2(PIC) をコンテナ内ビルド（scipoptsuite ソース tarball を取得、キャッシュキーは SCIP バージョン）。wheel ジョブはタグ push 時のみ実行（無料 CI 分数の節約）。
2. PyPI 側設定: `pynaps` プロジェクトを Trusted Publisher（gitlab.com のネームスペース/プロジェクト/ジョブ指定）として事前登録。まず TestPyPI でリハーサル。
3. タグ push → wheel ビルド → PyPI 公開のフローを確立。

## 検証方法

- Phase 1: ローカル wheel の import・求解テスト（`timeout` 付き）、`auditwheel show` の出力確認、`make rs` の再検証（PIC 化後）。
- Phase 2: TestPyPI から `pip install --index-url https://test.pypi.org/simple/ pynaps` → クリーンな venv で import・求解確認。

## 留意点・保留事項

- **ブランチ集約**: bindings と SCIP_STATIC_PREFIX 変更は pybind ブランチ。公開までに master へどう取り込むかは別途決定（本プランのスコープ外）。
- **バージョン表記**: `1.04a7` は PyPI 上 `1.4a7` になる。公開を機に PEP 440 と整合する命名（例: `1.4.0a7`）への移行を検討する余地（ユーザー判断待ち、本プランでは変更しない）。
- **ライセンス**: SCIP 9.2.2 は Apache 2.0 → wheel にライセンス表記を同梱。GMP(LGPL) は静的リンクせず共有ライブラリのまま auditwheel に同梱させる（差し替え可能性が保たれ LGPL 的に安全側）。naps 自体の LICENSE は MiniSat+ 系 MIT スタイル。
- **import 名衝突**: 既存 PyPI `naps`（FPGA）も `import naps` を提供するため両方インストールすると衝突するが、利用者層が重ならずまれ。ドキュメントに注記のみ。
- **SCIP バージョン**: 10.0.2 ソース（`../scipoptsuite-10.0.2`）を使用。apt 版と同一バージョンのため動作比較が正確になり、`PbSolver_scip.C` の SCIP 10.x 向け調整コードとも整合する。9.2.2 での検証結果（ビルドレシピ・リンク成立）は 10.0.2 に引き継ぐ前提だが、10.0.2 で初回ビルドが通ることは Phase 1 で確認する。
- gitlab.com 無料枠の CI 分数（月 400 compute minutes、公開プロジェクトは軽減係数あり）に対し、SCIP ビルド込みの wheel ジョブは重い → タグ時のみ実行 + キャッシュで対処。

## 変更対象ファイル

- `pyproject.toml`（メタデータ拡充）
- `setup.py`（name 変更、SCIP_STATIC_PREFIX 橋渡し）
- `python/README.md`（配布方針の記載更新）
- `plans/20260716_pypi_deployment.md`（新規、本プランの写し）
- `.gitlab-ci.yml`（新規 — Phase 2、移設後）
- （変更不要の確認のみ: CMakeLists.txt の naps_python は LINK_LIBS 経由で静的 SCIP を拾う）

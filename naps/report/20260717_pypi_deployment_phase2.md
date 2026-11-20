# PyPI 公開体制の構築 Phase 2 完了レポート（pynaps — GitLab CI 整備）

日付: 2026-07-17 〜 2026-07-21
計画書: [plans/20260716_pypi_deployment.md](../plans/20260716_pypi_deployment.md)（Phase 2 節）
ブランチ: beta（origin = gitlab.com、コミット済み・push 済み）
前提: gitlab.com への移設完了（origin = `git@gitlab.com:naps2040334/naps.git`）

## いま何が終わっていて、次に何をすればいいか（本公開まで間が空くとき用の要約）

- **TestPyPI への publish まで成功済み**（2026-07-21）。CI 一連（manylinux wheel ビルド →
  静的 SCIP 組み込み → auditwheel → pytest → Trusted Publishing で TestPyPI 公開）が
  gitlab.com 上で端から端まで通った。TestPyPI 上に `pynaps 1.4.1.dev1` が存在する。
- **技術的に未確認な点はもう無い**。残るは「本番 PyPI への公開」という運用操作だけ。
- **本番 PyPI 公開の手順は下記「本公開チェックリスト」に集約**した。しばらく後に
  再開するときはそこだけ見ればよい。
- 未着手のユーザー作業は **本番 PyPI 側の pending publisher 登録**（TestPyPI とは別）と、
  **公開したい版を決めて Version.h を `1.4.1` にしてタグを打つ**こと。

## 結論

gitlab.com 移設の完了を受けて、Phase 2（CI 整備 + 公開フロー）を完了し、**TestPyPI への
publish まで実証した**。タグ push → manylinux wheel ビルド（cp39–cp313, 静的 SCIP 組み込み）
→ 手動トリガで PyPI / TestPyPI へ Trusted Publishing 公開、というフローが `.gitlab-ci.yml`
として稼働している。

途中の失敗と修正（すべて解決済み）:

1. 初回リハーサルで `build_wheels` が失敗（**lib64 問題**）→ `-DCMAKE_INSTALL_LIBDIR=lib`
   で修正（commit 58e45c5）。
2. `publish_testpypi` が OIDC トークン取得に失敗（**id_token 名の誤り**）→
   `PYPI_ID_TOKEN` を `TESTPYPI_ID_TOKEN` に修正（commit 96bad37）。
3. バージョン表記の PEP 440 非互換（`1.04`→`1.4` 問題）→ 命名を PEP 440 準拠に移行
   （commit fc168bb、`docs/versioning.md`）。Version.h は `1.4.1.dev1`。

セッション途中でユーザーが docker を導入したため、CI と同一の manylinux_2_28 コンテナで
**全 5 wheel（cp39–cp313）の生成・各 pytest 24 passed・別 distro での動作までローカル実証済み**
（後述「ローカル docker での実走検証」）。その後 gitlab.com 上でも TestPyPI publish が成功。

## 作成・変更ファイル（beta ブランチ、push 済み: HEAD=96bad37）

| ファイル | 変更 | コミット |
|---|---|---|
| `.gitlab-ci.yml` | 新規。build_wheels（cibuildwheel 4.1.0 + docker:dind）、publish_testpypi / publish_pypi（Trusted Publishing、手動トリガ）。publish_testpypi の id_token は `TESTPYPI_ID_TOKEN` | 5ea4bad, 96bad37 |
| `ci/build_static_scip.sh` | 新規。manylinux コンテナ内で静的 SCIP 10.0.2 (PIC) をビルド（CIBW の before-all）。`-DCMAKE_INSTALL_LIBDIR=lib` で lib64 問題を回避 | 5ea4bad, 58e45c5 |
| `ci/check_version_tag.py` | 新規。タグと Version.h の PEP 440 正規化比較（不一致なら公開前に CI を落とす） | 5ea4bad, fc168bb |
| `pyproject.toml` | `[project.urls]`（gitlab.com）と `[tool.cibuildwheel]`（ビルド対象・manylinux_2_28・before-all・環境変数・pytest）を追加 | 5ea4bad |
| `Version.h` | `1.04a9` → `1.4.1.dev1`（PEP 440 準拠へ） | fc168bb |
| `docs/versioning.md` | 新規。バージョン命名規則（PEP 440 準拠、dev/post の使い分け） | fc168bb |
| `python/README.md` | 「PyPI への公開（GitLab CI）」節を追加（リリース手順）、命名規則参照 | 5ea4bad, fc168bb |
| `report/20260717_pypi_deployment_phase2.md` | 本レポート | 各コミット |

## CI の設計

### パイプラインの起動条件（無料 CI 分数対策）

`workflow.rules` により**タグ push 時と Web UI の Run pipeline のみ**。ブランチへの
push ではパイプラインは一切走らない。

### build_wheels ジョブ

- cibuildwheel の GitLab CI 公式構成（python:3.12 + docker:dind サービス）。
- ビルド対象は `pyproject.toml` の `[tool.cibuildwheel]` で
  **cp39〜cp313 × manylinux_2_28_x86_64 の 5 wheel** に固定
  （`cibuildwheel --print-build-identifiers` でこの 5 つだけが選ばれることを検証済み）。
  musllinux・i686・PyPy は対象外。
- `before-all` = `ci/build_static_scip.sh`: コンテナ内で scipopt.org から
  scipoptsuite-10.0.2.tgz を取得（**SHA256 ピン留め**:
  `eecc29f3...c47073`、2026-07-17 に実ダウンロードで採取）し、Phase 1 検証済みレシピ
  （`-DCMAKE_POSITION_INDEPENDENT_CODE=ON -DNO_EXTERNAL_CODE=ON -DSHARED=OFF -DLPS=spx
  -DSYM=snauty -DTPI=none ...`）で `/project/scip_static` へインストール。
  あわせて naps 本体用の `cmake gmp-devel zlib-devel` を dnf で導入。
  **`-DCMAKE_INSTALL_LIBDIR=lib` が必須**（後述の初回パイプライン失敗を参照）。
- `environment = { SCIP_STATIC_PREFIX = "/project/scip_static" }` により setup.py →
  CMake の静的 SCIP 経路（`-DSCIP_STATIC_PREFIX`）が有効化される。
- wheel ごとのテスト: 既存の pytest スイート `tests/python/`（24 テスト、
  `Examples/garden9x9.opb` 使用、メモリリーク回帰含む）をコンテナ内で実行。
  conftest.py が挿入する `build_python/` はコンテナに存在しないため、
  インストール済み wheel の naps が正しくテストされる。
- タグパイプラインでは cibuildwheel 実行前に `ci/check_version_tag.py` が
  タグと `Version.h` の一致を検査（PyPI はバージョン番号を再利用できないため、
  誤バージョン公開を公開前に阻止する）。

### publish ジョブ（Trusted Publishing / OIDC — API トークン保存不要）

- `id_tokens` + `twine upload`（twine 6.1+ が OIDC トークンを自動交換する）。
  **id_tokens の名前は twine が探す環境変数名と一致させる必要がある**:
  twine は `--repository <name>` のとき `<NAME>_ID_TOKEN` を探すため、
  publish_pypi は `PYPI_ID_TOKEN`（aud: pypi、repository 指定なし=pypi）、
  publish_testpypi は **`TESTPYPI_ID_TOKEN`**（aud: testpypi）にする。
  ※初回リハーサルで publish_testpypi を `PYPI_ID_TOKEN` にしていて
  `TrustedPublishingFailure: ... TESTPYPI_ID_TOKEN not found` で失敗し修正した
  （PyPI 公式ドキュメントの GitLab 例が testpypi でも `PYPI_ID_TOKEN` を使う
  書き方で、実際の twine の挙動と食い違っていた）。
- **どちらも手動トリガ**（`when: manual`）: タグを push しただけでは公開されず、
  wheel とジョブログを確認してからパイプライン画面のボタンで公開する。
  publish_pypi はタグパイプラインのみに出現。

### 計画からの変更点（要注意）

1. **SCIP ビルドの GitLab キャッシュは実装しない**。計画では「SCIP バージョンを
   キーにキャッシュ」としていたが、cibuildwheel はプロジェクトをコンテナへ
   **tar コピー**する方式（`oci_container.py` の `copy_into`。dind でも動くのは
   このため）で、コンテナ内 `/project` への書き込みはホストのジョブ作業領域に
   残らず、GitLab キャッシュに載せられない。wheel ジョブはタグ時のみで頻度が
   低いため、毎回コンテナ内ビルド（1 パイプラインにつき SCIP ビルドは 1 回）とした。
2. **SoPlex の Boost / gmp-devel 依存リスクは不存在と確認**（計画時の未確定事項）。
   ローカルの `scipoptsuite-10.0.2/build_static/CMakeCache.txt` を確認したところ
   `NO_EXTERNAL_CODE=ON` により **BOOST / GMP / ZLIB / READLINE / PAPILO すべて OFF**
   でビルドされていた。つまり静的 SCIP は外部ライブラリ依存ゼロで、コンテナに
   必要なのは naps 本体用の gmp-devel / zlib-devel だけ。
3. SCIP ソースの取得方法は「scipopt.org から直接ダウンロード + SHA256 検証」に確定
   （HTTP 200・直リンク可・19MB・展開ディレクトリ名 `scipoptsuite-10.0.2/` を確認済み）。

## 初回パイプラインの失敗と修正（lib64 問題）

最初のリハーサル（beta @ 5ea4bad, Web UI の Run pipeline）で `build_wheels` が失敗した。
SCIP のビルド自体は成功していたが、naps 側の CMake が静的 SCIP を見つけられなかった:

```
CMake Error at CMakeLists.txt:232 (message):
  静的 SCIP が見つかりません: /project/scip_static
```

**原因**: CMake の `GNUInstallDirs` は RHEL 系（manylinux_2_28 = AlmaLinux 8）で
ライブラリを **`lib64`** に置くため、`scip-config.cmake` が
`/project/scip_static/lib64/cmake/scip/` にインストールされ、
`CMakeLists.txt` と `ci/build_static_scip.sh` の skip 判定が期待する
`lib/cmake/scip/scip-config.cmake` と食い違った。

**なぜ Phase 1 で気づけなかったか**: ローカル検証機は Ubuntu（Debian 系）で、
Debian のマルチアーキ規約では常に `lib`（正確には `lib/x86_64-linux-gnu`）が使われる。
この差は RHEL 系コンテナでしか再現しない。

**修正**: `ci/build_static_scip.sh` の cmake configure に **`-DCMAKE_INSTALL_LIBDIR=lib`**
を追加し、distro の規約によらずインストール先を `lib` に固定した。
修正後、ローカル docker での実走で `lib64` の出現がゼロになり `lib/cmake/scip/scip-config.cmake`
が正しく生成されることを確認済み（次節）。

## ローカル docker での実走検証

初回パイプライン失敗を受けてユーザーが docker を導入したため、**CI と同一の
manylinux_2_28 コンテナでの実走検証がローカルで可能になった**。
`pip install cibuildwheel==4.1.0` 後、リポジトリルートで:

```sh
CIBW_BUILD='cp312-*' cibuildwheel --platform linux --output-dir wheelhouse   # 1本だけ（約6分）
cibuildwheel --platform linux --output-dir wheelhouse                        # 全5本
```

`pyproject.toml` の `[tool.cibuildwheel]` を読むため、**GitLab CI と全く同じ
イメージ・`before-all`・pytest が走る**。今回の lib64 問題もこれで再現・修正確認できた。
push → CI 待ち → ログ確認のサイクルより大幅に速いので、以後 CI 設定を触る際は
まずローカルで回すこと。

### cp312 単体の検証結果（2026-07-17、修正後）

| 工程 | 結果 |
|---|---|
| manylinux_2_28 イメージ pull | ✅ 46秒（以後ローカルキャッシュが効く。CI は毎回 pull） |
| 静的 SCIP のビルド（before_all） | ✅ 238秒 |
| SCIP のインストール先 | ✅ `lib/cmake/scip/scip-config.cmake`、`lib/libscip.a`（**lib64 の出現ゼロ**） |
| naps 拡張のビルド | ✅ 75秒（CI で失敗していた地点を通過） |
| auditwheel repair | ✅ manylinux_2_27 + 2_28 の両タグ（要求した 2_28 より緩い判定） |
| pytest（tests/python/） | ✅ **24 passed in 2.13s** |
| 生成物 | ✅ `pynaps-1.4a8-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` (6.0MB)、全体 6 分 |

### wheel の内容検証

拡張モジュールの動的依存から **`libscip.so` が消滅**（静的リンク成功）。
`libgmp` は auditwheel が `pynaps.libs/libgmp-d944b113.so.10.3.2` として同梱
（計画どおり。静的リンクを避け差し替え可能な形にしており LGPL 的に安全側）。
残る依存は libz / libstdc++ / libm / libgcc_s / libc のみで、いずれも manylinux の
許容リストに含まれる。

**別 distro での実地確認**: AlmaLinux 8（glibc 2.28 系）でビルドした wheel を
このマシン（Ubuntu, glibc 2.39）のクリーン venv に `pip install` し、
import と求解に成功。manylinux wheel の目的（ビルド環境と異なる distro で動く）を実証:

```
version: 1.04a8
HAS_SCIP: True
status: SolverStatus.best / optimum: -1    ← Phase 1 の検証値と一致
```

### 落とし穴（ローカル実行時）

- cibuildwheel は **package_dir が作業ディレクトリ配下にある必要がある**
  （`package_dir must be inside the working directory`）。リポジトリルートで実行すること。
- cibuildwheel はプロジェクトを **tar でコンテナへコピー**するため、`.gitignore` 対象の
  ファイルも含まれる。ローカルに `scip_static/` があると `build_static_scip.sh` の
  skip 判定に引っかかり、**ホスト(Ubuntu)でビルドした SCIP がコンテナ内で使われてしまう**
  （今回は `scip_static/` が無かったため CI と同条件で再現できた）。
  CI 再現目的で回すときは `scip_static/` を退避すること。

## 検証済み事項（ローカル）

### 静的検証（docker 導入前に実施）

| 検証 | 結果 |
|---|---|
| `.gitlab-ci.yml` の YAML 構文（PyYAML でパース、ジョブ構成確認） | OK |
| `pyproject.toml` の TOML 構文（tomllib）と cibuildwheel 設定値 | OK |
| `cibuildwheel==4.1.0 --print-build-identifiers` | 意図どおり cp39–cp313 manylinux_x86_64 の 5 件のみ |
| `ci/build_static_scip.sh` の bash 構文・チェックサム検証ロジック | OK |
| `ci/check_version_tag.py` 6 ケース（一致 v1.04a8 / 1.4a8、不一致 v1.04a7 / 1.4a7 / v1.5、不正 vfoo） | 一致のみ exit 0 |
| scipoptsuite-10.0.2.tgz の直リンク取得・SHA256 採取・展開名 | OK |

### 実走検証（docker 導入後、lib64 修正込み）

| 検証 | 結果 |
|---|---|
| cp312 単体のフル実走（SCIP → ビルド → auditwheel → pytest） | ✅ 6 分で完走、24 passed |
| SCIP のインストール先が `lib`（lib64 問題の修正確認） | ✅ lib64 の出現ゼロ |
| wheel から `libscip.so` 依存が消えていること | ✅ 静的リンク成功、libgmp は同梱 |
| 別 distro（Ubuntu/glibc 2.39）での import・求解 | ✅ HAS_SCIP=True、最適値 -1 が Phase 1 と一致 |
| 全 5 wheel（cp39–cp313）のフル実走 | ✅ **12 分で 5 wheels produced、各 24 passed、エラーゼロ** |

全 5 wheel（各 6.0MB、manylinux_2_27 + 2_28 タグ）:
`pynaps-1.4a8-cp{39,310,311,312,313}-...-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl`。
コンテナは 5 バージョンで共有されるため静的 SCIP のビルドは 1 回のみ。

**検証完了（2026-07-21、gitlab.com 上）**: パイプライン完走と TestPyPI への Trusted
Publishing 疎通（`pynaps 1.4.1.dev1` の publish 成功）。**残るは本番 PyPI の Publisher
登録と公開操作のみ**（技術的な未確認点は無い）。

## 完了済みの作業（2026-07-17 〜 21）

- ✅ コード・CI 一式のコミットと gitlab.com への push（origin/beta, HEAD=`96bad37`）。
  内訳: CI 一式（5ea4bad）/ lib64 修正（58e45c5）/ 命名 PEP 440 移行（fc168bb）/
  id_token 修正（96bad37）。既定 push 先も `origin` に設定済み（旧 `nagoya` から変更）。
- ✅ **TestPyPI の pending publisher 登録**（下表の値で登録済み）。
- ✅ **TestPyPI リハーサル publish 成功**（`pynaps 1.4.1.dev1`）。OIDC 疎通・
  build_wheels・auditwheel・pytest・手動トリガまで gitlab.com 上で確認。

パイプラインの回し方（メモ）: `.gitlab-ci.yml` は現状 beta ブランチにのみ存在し、
GitLab のデフォルトブランチ（main）には無い。そのため Pipelines 画面に Run pipeline
ボタンが出ないので、`https://gitlab.com/naps2040334/naps/-/pipelines/new` に直接アクセスし
ref=`beta` を指定して回す（将来 main へマージすれば解消）。

## 本公開チェックリスト（PyPI）★これが唯一の残作業★

**しばらく後に再開するときはここだけ見ればよい。** 命名規則は `docs/versioning.md`。

1. **本番 PyPI 側の pending publisher 登録**（TestPyPI とは別アカウント・別登録）。
   https://pypi.org/manage/account/publishing/ の「GitLab」タブで下表を入力:

   | 項目 | 値 |
   |---|---|
   | PyPI Project Name | `pynaps` |
   | Namespace | `naps2040334` |
   | Project name | `naps` |
   | Top-level pipeline file path | `.gitlab-ci.yml` |
   | Environment name | （空欄のまま） |

2. **公開する版を決め、`Version.h` を正式版 `1.4.1` に更新**（`.dev` を外す）してコミット・push。
3. **タグを打って push**: `git tag v1.4.1 && git push origin v1.4.1`。
   （タグと Version.h の不一致は CI の `ci/check_version_tag.py` が検出して build を止める。）
4. **タグパイプラインの `build_wheels`（5 wheel + pytest）成功を確認**。
5. **`publish_pypi` を手動トリガ**（タグパイプラインには `publish_testpypi` のボタンも
   出るので、本番は必ず `publish_pypi` の方を押す）。
6. **`pip install pynaps` で確認**（正式版なので `--pre` 不要）。

注意:
- **PyPI は一度使った版番号を再利用できない**。本番公開前に間違いがないか確認する。
- 本番前にもう一度 TestPyPI で試したいときは、`Version.h` を `1.4.1.dev2` 等に上げて
  リハーサルを繰り返せる（`.gitlab-ci.yml` の変更は不要）。TestPyPI も同一版の再upは不可。
- CI 分数を節約したいリハーサルでは、Settings → CI/CD → Variables に `CIBW_BUILD` =
  `cp312-*` を一時登録すると 1 wheel のみになる（Run pipeline 画面に Variables 欄が
  出ないため）。使い終わったら削除する。

## 留意点・保留事項

- **CI 時間の見積り**: gitlab.com 無料 SaaS runner（small, 2 vCPU）で SCIP ビルド +
  5 wheel + テストのフルパイプラインは長め（おおよそ 1 時間前後を想定、実測は初回で）。
  無料枠は月 400 compute minutes（公開プロジェクトは軽減係数あり）。タグ時のみの
  実行なので通常運用では問題にならない見込み。恒常的に不足するなら medium runner
  ではなく対象 Python の削減やリリース頻度で調整する。
- **cp314**: cibuildwheel 4.1 は CPython 3.14 に対応済み。`pyproject.toml` の `build` に
  `"cp314-*"` と classifiers を足すだけで追加できる（今回は計画どおり cp39–cp313）。
- **バージョン表記（2026-07-21 決着）**: Phase 1 からの保留（`1.04` が PyPI で `1.4` に
  正規化される件）を、命名を PEP 440 準拠に移行して解決した。**過去のコミット・タグは
  変更せず、今後の版から新命名**（`メジャー.マイナー.ステージ`、ゼロ埋め廃止、開発版
  `.devN`・安定版 修飾子なし・リリース後修正 `.postN`）。ステージは旧 `a/b/c` を数値化
  （a=0, b=1, c=2）。**Version.h を `1.04a9` → `1.4.1.dev1` に変更**、本番 PyPI 初投入は
  正式版 `1.4.1` から。詳細は新設した `docs/versioning.md`。この新命名では Version.h・
  CLI・`naps.__version__`・wheel・PyPI がすべて一致（正規化で変化しないため）。
- **sdist は公開しない**（wheel のみ）。ソース利用者はリポジトリからのビルドを想定。
- CLAUDE.md の「SCIP 連携は libscip が見つかった場合のみ有効」「Python バインディングは
  未配線」の記載は現状（beta ブランチ）と食い違ったまま（前タスクからの持ち越し、別途更新推奨）。

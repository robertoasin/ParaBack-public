# 開発環境整備: .claude/CLAUDE.md の再構成と .claude/agents/ の新設

## Context

naps（Pseudo-Boolean ソルバ、C++）の開発環境整備として、Claude Code 向け設定を整える。

現状の問題:
- 現行 [.claude/CLAUDE.md](.claude/CLAUDE.md) はアーキテクチャ解説のみで、ビルド・テスト・実行方法や
  ディレクトリ注意事項（旧 [.claude/CLAUDE.md.bk](.claude/CLAUDE.md.bk) にあった内容）が欠落している。
- サブエージェント定義（`.claude/agents/`）が存在しない。既存のワークフロー文書は
  [.agent/workflows/build.md](.agent/workflows/build.md) と [.agent/workflows/run.md](.agent/workflows/run.md) にあり、要移設。
- プラン置き場のパスが不一致（実体は `./plans/` に39ファイル。旧 .clinerules は `./.claude/plans/` を
  指定していたが、ユーザーが symlink を削除済み）。

ユーザー決定事項:
1. エージェントは **build-tester / regression-runner / code-explorer / wrong-model-debugger** の4つ
2. CLAUDE.md は**分割構成**（簡潔な入口 + アーキテクチャ詳細は `docs/architecture.md` に分離）
3. プラン置き場は **./plans/ に統一**
4. `.agent/workflows/` の build.md・run.md は適切な場所へ移動（→ `.claude/commands/` に移設し `/build` `/run` コマンド化）
5. `err-insts*/` にも長時間インスタンスがある → 注意事項とエージェント指示に反映

## 作成・変更ファイル

### 1. `docs/architecture.md`（新規）

現行 `.claude/CLAUDE.md` のアーキテクチャ解説をそのまま移設:
- 処理フロー図（Main.C → PbSolver → PbParser → convert系 → Hardware_clausify → Solver）
- クラス階層（PbSolver / PbSolver_Minimize / PbSolver_Counting）
- `divideGoal` によるマルチステージ辞書式最適化の詳説（SCIP 連携の注意点含む）
- エンコーディング戦略表（`-ct`）・最小化戦略（`-minimization`）

加えて、ソースファイルマップを追記（探索結果より）:
PbSolver.C(3464行・中核), PbSolver_scip.C(1061行・SCIP連携), Solver.C(816行・SAT抽象レイヤ),
Main.C(753行・CLI), PbSolver_convertBdd.C(736行), PbParser.C(629行), OptimalBase.C(555行) など
各ファイル1行説明の表。

### 2. `.claude/CLAUDE.md`（全面書き換え）

簡潔な入口として以下の節で構成:

- **プロジェクト概要**（1〜2行）+ `docs/architecture.md` への参照
- **ビルド**（Makefile は CMake のフロントエンド。詳細は `/build` コマンド＝ .claude/commands/build.md）:
  - `make r`: リリース（GMP BigNum、推奨）→ `naps_release-<VER>`
  - `make d`: デバッグ + 64bit int → `naps_debug-<VER>`
  - `make rs` / `rx`: 静的リンク版、`make clean` / `make cleanPB`
  - `USE_SYSTEM_COMPILER=1` でシステム標準コンパイラ使用
- **テスト**: googletest 必須。`make d` 後に `ctest --test-dir build_debug` または
  `./build_debug/pb_solver_test --gtest_filter=...`（リポジトリルートから実行）
- **実行例**: `./naps_debug Examples/garden9x9.opb`、`timeout 10 ./naps_debug -hitting -iSCIP Examples/stein27.opb`
  など settings.json の許可済みパターンに沿った例（詳細は `/run` コマンド）
- **ディレクトリ注意事項**（旧 CLAUDE.md.bk・.agent/workflows/build.md から継承・拡充）:
  - `cadical2/` `cadical3/` `kissat/` `glueminisat/`: 外部ツール、原則変更禁止
  - `Examples_debug/` および `err-insts*/`: 実行時間が非常に長いインスタンスあり（1時間超も）、
    **必ず timeout を付けて実行**
  - `benchmark` / `instances`: NFS への symlink（環境によって解決不可）
- **運用ルール**:
  - プランは `./plans/YYYYMMDD_タスク名.md`（グローバル CLAUDE.md と一致させる）
  - レポートは `./report/`
- **既知の注意**: 現ブランチ（stratification）では Python バインディングは未配線
  （setup.py はあるが CMakeLists.txt に `naps_python` ターゲットが無い。pybind ブランチ側）

### 2.5 `.claude/commands/build.md`・`.claude/commands/run.md`（`.agent/workflows/` から移設）

既存の [.agent/workflows/build.md](.agent/workflows/build.md)・[run.md](.agent/workflows/run.md) は
frontmatter（`description:`）付きで Claude Code のスラッシュコマンド形式と一致するため、
`.claude/commands/` へ移動して `/build`・`/run` として使えるようにする。移設時に内容を修正:
- build.md: 「`make r` はリリース静的ビルド」→ 正しくは動的リンク（静的は `make rs`）に訂正。
  外部プロジェクト（変更禁止）一覧はそのまま維持
- run.md: `Examples/garden9x9.opb.opb` の typo を `Examples/garden9x9.opb` に修正。
  `-dm`, `-1`, `-A`, `-v2` などの使用例は維持し、timeout 付き実行の推奨を追記
- 移設後、空になった `.agent/` ディレクトリは削除

### 3. `.claude/agents/build-tester.md`（新規）

- frontmatter: `name: build-tester`, `description`（コード変更後のビルド・テスト実行と失敗解析に使用）,
  `tools: Bash, Read, Grep, Glob`
- 本文: `make d` / `make r` の使い分け、エラー抽出（`grep -E "error:|Built target naps$"`）、
  ctest・gtest バイナリ（pb_solver_test / failed_assumptions_test / sat_solver_test）と
  `--gtest_filter` の使い方、失敗時はエラー全文と該当ソース箇所（file:line）を報告する指示

### 4. `.claude/agents/regression-runner.md`（新規）

- frontmatter: `tools: Bash, Read, Grep, Glob`
- 本文: `Examples/*.opb` と `err-insts*/` を timeout 付きで実行し、
  `o` 行（最適値）・`s` 行（SAT/UNSAT/OPTIMUM）をルート直下の過去バイナリ
  （`naps_release` / `naps-1.03a49` 等）の出力と比較する手順。
  **Examples_debug/ と err-insts*/ の双方に長時間インスタンスがあるため timeout 必須**を明記、
  NFS symlink 不達時のスキップ指示

### 5. `.claude/agents/code-explorer.md`（新規）

- frontmatter: `tools: Read, Grep, Glob`（読み取り専用）
- 本文: PbSolver.C（3464行）等の大規模ソースの調査専用。file:line 付きの要約を返し、
  コード全文は貼らない指示。docs/architecture.md を先に読む指示

### 6. `.claude/agents/wrong-model-debugger.md`（新規）

- frontmatter: `tools: Bash, Read, Grep, Glob`
- 本文: 誤モデル・誤最適値の調査手順:
  1. `naps_debug` で再現（timeout 付き）
  2. 出力モデルを OPB 制約に対して検証
  3. 参照解（過去バイナリ / `scip-normalized-*.txt` の SCIP 出力）と比較
  4. オプション二分探索（`-ct`, `-minimization`, `-sc2-split` 等）で原因の切り分け
  5. err-insts3/ の既存調査資産（ggrep.sh, *.grepped）の参照

### 7. `.clinerules`（新規・通常ファイル）

ユーザーが旧 symlink を削除済みのため、統一後のルールで通常ファイルとして再作成:
- プラン: `./plans/YYYYMMDD_タスク名.md` に保存、新機能実装前にユーザー承認を得る
- レポート: `./report/` に保存

### 8. 後始末

- `.claude/CLAUDE.md.bk` を削除（内容は新 CLAUDE.md に吸収済み。行動原則はグローバル
  ~/.claude/CLAUDE.md と重複するため再掲しない）
- `.agent/` ディレクトリを削除（build.md・run.md は .claude/commands/ へ移設済みのため）
- `report/` ディレクトリを作成（グローバル CLAUDE.md の運用ルールに対応、.gitkeep 配置）

## 検証方法

1. 新 CLAUDE.md と docs/architecture.md の記載コマンドを実際に確認:
   - `make d` の成功（または既存 build_debug があれば `ctest --test-dir build_debug` のみ）
   - `timeout 10 ./naps_debug* Examples/garden9x9.opb` が動くこと
2. 各エージェントを小タスクで起動して動作確認:
   - code-explorer: 「PbSolver_Minimize::solve の概要を要約」
   - build-tester: 「pb_solver_test を1フィルタで実行して結果報告」
   - regression-runner / wrong-model-debugger: Examples/equal5.opb 等の小インスタンスで試験
3. `/build`・`/run` コマンドが認識されること（.claude/commands/ 移設後にコマンド一覧で確認）
4. 記載内容とファイル実体の整合（プランパス ./plans/、テストバイナリ名など）を最終確認

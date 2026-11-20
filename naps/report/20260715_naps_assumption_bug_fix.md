# 作業レポート — naps: `repeated_optimizations` の assumption 消失 / `Sc2` segfault 修正

作成日: 2026-07-15
対象: naps（ブランチ `pybind`）1.4a6
起点レポート: [report/20260714_naps_assumption_bug.md](20260714_naps_assumption_bug.md)
計画書: `~/.claude/plans/bug-report-20260714-naps-assumption-bug-jazzy-walrus.md`

## 結論

バグ A（assumption 消失）とバグ B（`Sc2` segfault）を両方修正した。再現例題
`docs/repro/naps_assumption_ignored.py` が **exit 0（OK）** になり、gtest 回帰テストを追加、
既存テスト（ctest 全 3 + gtest 22）と CLI 最適化経路の非回帰を確認済み。

## 根本原因

`PbSolver_Minimize::maximizeGoal`（PbSolver.C）が、**呼び出し側から渡された `assump_ps`
（ユーザー assumption）を、内部 goal-bound 活性化リテラルの作業スタックとして流用していた**こと。

- **バグ A**: SAT 成功後の `pop(); pop(); push(p);`（上から 2 番目を捨てる操作）が、ガードを
  `assump_ps.size() >= 2` という総数だけで判定していたため、`[a, g1]`（a=ユーザー assumption、
  g1=goal リテラル）の状態で発火して **`a` を捨てていた**。以降 `a` なしで SAT が呼ばれ、
  `llt=a` の制約が空虚に真 → 最適値が緩む・model[a]=False。
- **バグ B**: `Sc2` は `assump_ps` を混ぜて SAT に渡すため UNSAT core（`failed_assumptions`）に
  `a` が混入し、`Lit2sc2goalInd[index(a)]` が範囲外読み出し → segfault。
  同型の out-of-bounds が Hitting 経路（`processHittingSetIteration` の
  `outerVar2goalInd[var(...)]`）にも潜在し、バグ A 修正で顕在化する関係だった。

## 修正内容（すべて PbSolver.C 内）

1. **`goal_assumptions` 専用 vec を導入**（`hitting_assumptions` と同流儀）。探索ループ内の
   `assump_ps` への push/pop（cached 再利用、addGoalConstraint、SAT 成功後の pop-pop-push、
   タイムアウト時 pop、UNSAT 時 pop）を**すべて `goal_assumptions` に移譲**。
   `maximizeGoal` は引数 `assump_ps` を**読み取り専用**にし、return 時も変更しない。
2. **SAT 起動時にマージ**: `buildSatAssumps()` ラムダで
   `merged = assump_ps ++ hitting_assumptions ++ goal_assumptions` を構築し、
   Sequential/Binary・Hitting/sc2 の両経路を統一。
3. **UNSAT core フィルタ**: `dropUserAssumpsFromCore()` ラムダで、hitting/sc2 の SAT 起動直後に
   `failed_assumptions` から `assump_ps` に属するリテラルを除去（恒久強制されるため除去は健全）。
   これで sc2 緩和ループと Hitting の core 添字参照の out-of-bounds を 1 箇所で封じる。
4. **空 core の graceful 処理**: フィルタ後 core が空（= assumption 下で実行不能）なら、
   緩和せず `sst_unsat` で正しく復帰（従来の `assert` を置換）。
5. sc2 緩和ループに防御的 assert（フィルタ後リテラルが sc2 goal リテラルであることの同一性検査）。

**呼び出し側（solve の多段ステージ確定、findModelForBestValueBySAT）は変更なし**。ステージ確定
リテラルの永続はこれら呼び出し側が担う設計（計画で確認済み）。

## 除外事項

- **Hitting / Sequential_Hitting は削除予定のため個別修正・テスト対象外**（ユーザー指示）。
  共有 core フィルタで巻き添え保護されるに留める（正しさは保証しない）。

## 追加テスト（tests/pb_solver_test.cpp）

- `TEST(PB_solver_test, assumption_kept_repeated)`: `repeated_optimizations=true`、`llt=a`、
  不揃い係数 `[1,2,2]` で `solve([a])` → 最適値 3・model[a]=true。
  戦略 Sequential_Binary / Binary / Sequential / Sequential_Sc2 を検証（Hitting 系は除外）。
- `TEST(PB_solver_test, sc2_assumption_no_segfault)`: `mt_Sc2 + repeated + solve([a])` が
  クラッシュせず最適値 3。

## 検証エビデンス

| 項目 | 結果 |
|-----|------|
| `docs/repro/naps_assumption_ignored.py`（build_python） | **exit 0（OK）**。バグ A 全戦略で最適値 3・model[a]=True、バグ B 落ちず最適値 3 |
| 追加 gtest 2 件 | PASS |
| `pb_solver_test` 全体 | 22 tests PASSED |
| `failed_assumptions_test` / `sat_solver_test` | PASS |
| `ctest --test-dir build_debug` | 100%（3/3）|
| CLI 非回帰（新 build_debug/naps vs 旧 naps_debug-1.04a6） | garden9x9=20, stein27=18, equal6=2, minus-goal=-61 等、中間 `o` 値含め完全一致 |

## 残タスク（naps 外・別件）

- avs 側の最終確認: Phase 3 プロトタイプで `ballade1-106-108.mid` step0=242895 / フル=267563 と一致（avs リポジトリ）。
- 対象外の既知事項: `coding()` の goal_ub/lb 経路の同種アンチパターン、assumption/llt 変数の
  freeze 漏れ、`o <最適値>` の stdout 直書き（いずれも別件）。

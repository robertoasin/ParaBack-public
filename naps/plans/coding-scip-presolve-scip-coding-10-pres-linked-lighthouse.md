# Plan: SCIP cooperative モードにおける coding 前 presolve 実行

## Context

`PbSolver_Minimize::coding()` 内の `convertPbs()` は大規模問題で時間がかかる。SCIP 協調モード（`-cSCIP`）において coding 前に SCIP を presolver（デフォルト 10 秒）として起動し、以下の効果を得る：

- **最適解（sst_best）**: `solve()` 先頭でスキップ → 最適化ループ全体を省略
- **解あり（sst_sat）**: `best_model` を記録し `maximizeGoal` 各ステージの初期 LB を効率化
- **UNSAT**: coding より前に判明すれば早期リターン

**重要設計原則**:
- presolver は `goal`（full normalized goal）で動かすため `set_pb_solver_status(sst_sat/sst_best)` は正当
- 協調 SCIP が `goalExp`（stage-specific goal）で動く `maximizeGoal` 内では `pb_solver_status` を書き換えてはならない（既存コードはすでに守っている）
- multi-stage の場合、`presolve_ub` は `goal` に対するものであり各 stage の `goalExp` には適用不可。ただし `best_model` があれば `evalGoal(*goalExp)` で各 stage の primal LB は計算可能

## 変更ファイル

- [Config.h](Config.h)
- [Main.C](Main.C)
- [PbSolver.h](PbSolver.h)
- [PbSolver.C](PbSolver.C)

---

## 変更詳細

### 1. `Config.h`（行 109 直後）

`double_unknown_budget_factor`（行 109）の直後、`bool model_check` の前に追加：

```cpp
double   scip_presolve_time  = 10.0;   // SCIP presolver time limit before CNF coding (0 = disabled)
```

### 2. `Main.C`

#### docstring（行 102 直後）

`-scip-use-ub` 行の後に追加：

```
"  -scip-presolve-time=<sec>  Run SCIP as presolver before CNF coding; 0 disables (default: 10.0).\n"
```

`%g` プレースホルダは使わず固定値で記述（`fprintf` 引数リスト変更が不要）。

#### オプション解析（行 242 直後）

`-scip-use-ub` 解析行の後に追加：

```cpp
else if (strncmp(arg, "-scip-presolve-time=", 20) == 0) config->scip_presolve_time = atof(arg+20);
```

### 3. `PbSolver.h` — `PbSolver_Minimize` クラス

#### コンストラクタ初期化子リスト（行 357 直後）

`has_model_for_bestvalue(false)` の後に追加：

```cpp
      , presolve_lb(Int_MIN)
      , presolve_ub(Int_MAX)
```

#### メンバ宣言（行 397 付近、`has_model_for_bestvalue` の後）

```cpp
    Int     presolve_lb;   // primal bound from SCIP presolver (Int_MIN = not available)
    Int     presolve_ub;   // dual bound from SCIP presolver (Int_MAX = not available)
```

---

### 4. `PbSolver.C` — `PbSolver_Minimize::coding()` （行 2539 直後）

`initScipForCoop()` 呼び出し（行 2539）の直後、`convertPbs(0)` の前に挿入：

```cpp
    // SCIP presolver: run before CNF coding to obtain bounds or prove optimality/infeasibility.
    if (config->scip_presolve_time > 0.0 && goal != NULL) {
      vec<Lit> empty_assump;
      Int ps_lb = Int_MIN, ps_ub = Int_MAX;
      report->reportf(1, "c Running SCIP presolver (time limit: %g sec)...\n", config->scip_presolve_time);
      solverStatus ps_result = solveScip(empty_assump, config->scip_presolve_time,
                                         goal, Int_MIN, &ps_lb, &ps_ub);
      report->reportf(1, "c SCIP presolver: %s\n", solverStatus_strs[ps_result]);

      if (ps_result == sst_unsat) {
        // Problem infeasible before coding
        set_pb_solver_status(sst_unsat);
        return get_pb_solver_status();
      }
      if (ps_result == sst_best) {
        // Optimal found: best_model/best_goalvalue set by solveScip()
        // Setting sst_best is valid because presolver ran with full 'goal'
        set_pb_solver_status(sst_best);
        return get_pb_solver_status();
      }
      if (ps_lb != Int_MIN) {
        // Feasible solution found (timeout with solution): best_model set by solveScip()
        set_pb_solver_status(sst_sat);
        has_model_for_bestvalue = true;
      }
      presolve_lb = ps_lb;
      presolve_ub = ps_ub;
    }
```

`convertPbs()` は常に実行する（multi-stage では SAT エンコーディングが必要なため）。

**ポイント**:
- `sst_best` → `set_pb_solver_status(sst_best)` して return。solve() 先頭でスキップされる（変更 5 参照）
- `sst_unknown` + `ps_lb != Int_MIN` → 途中解あり → `sst_sat` を設定し `best_model` 活用
- `sst_unknown` + `ps_lb == Int_MIN` → 解なし → status 変更なし、通常通り最適化

---

### 5. `PbSolver.C` — `PbSolver_Minimize::solve()` （行 2601 の前）

`solve()` 関数定義（行 2599）の先頭、`if (config->use_scip)` チェック（行 2601）の前に追加：

```cpp
  if (get_pb_solver_status() == sst_best) return sst_best;
```

presolver が最適解を見つけて `sst_best` を設定した場合、最適化ループ全体をスキップして即座に返す。

**ポイント**:
- Main.C は `sst_best != sst_unsat` なので solve() を呼び出すが、この1行で即リターン
- `best_model` と `best_goalvalue` は `solveScip()` によって設定済み
- multi-stage / single-stage に関わらず正しく動作（`divide` 判定が不要）

---

### 6. `PbSolver.C` — `PbSolver_Minimize::maximizeGoal()` （行 1718 直後）

`evalGoalLB` / `evalGoalUB` による初期化（行 1715-1718）の直後に追加：

```cpp
  // If a valid model exists (from presolver or previous stage), initialize LB from it.
  // evalGoal(*goalExp) evaluates goalExp against best_model — correct for any stage's goalExp.
  if (get_pb_solver_status() == sst_sat && best_model.size() > 0) {
    updateLBGoalvalue(report, evalGoal(*goalExp), LB_goalvalue, config->verbosity);
    has_model_for_bestvalue = true;
  }
```

**ポイント**:
- `evalGoal(*goalExp)` は `best_model` に対して `goalExp` を評価する → stage-specific goal でも正しく LB を計算
- multi-stage で stage i の `goalExp = goals[i-1]` に対しても `best_model`（presolve の解）から LB を計算可能
- `pb_solver_status == sst_sat` は presolve 以外でも成立するが（stage 間の carry-over）、常に valid な LB を与えるため安全

---

### 7. `PbSolver.C` — `PbSolver_Minimize::solve()` single-stage UB 初期化（行 2702 と 2703 の間）

`config->goal_lb` 適用後（行 2701）、`maximizeGoal` 呼び出し（行 2703）の前に追加：

```cpp
    // Apply presolver dual bound as initial UB (single-stage only: presolve used full 'goal')
    if (config->use_cooperative_scip && config->scip_presolve_time > 0.0 &&
        presolve_ub != Int_MAX && presolve_ub < UB_goalvalue)
      UB_goalvalue = presolve_ub;
```

**ポイント**:
- `presolve_ub`（SCIP dual bound）は `goal` 全体に対するものなので single-stage にのみ適用
- multi-stage には適用しない（各 stage の goalExp と `goal` が異なるため）

---

## 動作確認（Phase 1: presolve）

```bash
# ビルド
make d

# presolve あり（デフォルト 10 秒）
./naps_debug Examples/garden9x9.opb.opb -cSCIP

# presolve を無効化（既存動作と同一結果を確認）
./naps_debug Examples/garden9x9.opb.opb -cSCIP -scip-presolve-time=0

# 小問題で presolve が OPTIMAL を返すケースをテスト
./naps_debug Examples/empty.opb -cSCIP

# テスト実行
cd build_debug && ctest -V
```

確認ポイント：
1. `sst_unsat` ケース: coding 前に非充足と判明して正しく終了
2. `sst_best` ケース: `solve()` 先頭で即リターン、正しい最適値を出力
3. 解あり + timeout ケース: `sst_sat` が設定され `maximizeGoal` 内で初期 LB が高速化される
4. multi-stage: `presolve_ub` 未適用、`evalGoal` で各 stage の LB を初期化
5. `scip-presolve-time=0` で無効化: 既存動作と同一結果

> **ステータス**: Phase 1 は実装済み・全テスト通過

---

---

# Phase 2: `findModelForBestValue` でのソルバ選択

## Context

`findModelForBestValue`（PbSolver.C:2593）は `has_model_for_bestvalue == false` かつ `LB == UB` のときに最良値を実現するモデルを探す。現状は常に SAT solver を呼び出しているが、SAT が苦手で SCIP が得意な問題（例: `normalized-ns894788.opb`）ではここで進行が止まる。

**根本原因**: `has_model_for_bestvalue = false` は `evalGoalLB`（固定リテラルから LB を計算）が LB を改善したときに設定される。直前の「本物の primal LB 改善」が SCIP によるものか SAT によるものかを追跡していないため、不適切なソルバを呼んでしまう。

**解決方針**: 最後に primal LB を改善したソルバを `lb_last_improved_by_scip` フラグで記録し、`findModelForBestValue` 内でそれに応じてソルバを選択する。

## 変更ファイル

- [PbSolver.h](PbSolver.h) — フラグ宣言・初期化子
- [PbSolver.C](PbSolver.C) — フラグ更新箇所 3 箇所・`findModelForBestValue` 修正

---

## 変更詳細

### 8. `PbSolver.h` — `PbSolver_Minimize` クラス

#### コンストラクタ初期化子リスト（`presolve_ub(Int_MAX)` の後）

```cpp
      , lb_last_improved_by_scip(false)
```

#### メンバ宣言（`presolve_ub` の後）

```cpp
    bool    lb_last_improved_by_scip;  // true when last primal LB update came from cooperative SCIP (not SAT)
```

---

### 9. `PbSolver.C` — フラグを `true` に設定する箇所

#### 9a. `maximizeGoal()` — 協調 SCIP が primal LB を改善した直後（行 2234 付近）

`has_model_for_bestvalue = true;`（行 2235）の直後に追加：

```cpp
          lb_last_improved_by_scip = true;
```

差分イメージ（行 2232–2242）：
```cpp
      if (scip_lb != Int_MIN) {
        Int original_LB_goalvalue = LB_goalvalue;
        if (updateLBGoalvalue(report, scip_lb, LB_goalvalue, config->verbosity)) {
          has_model_for_bestvalue = true;
          lb_last_improved_by_scip = true;  // ← 追加
          config->stats_coop_scip_lb_improvements++;
```

#### 9b. `coding()` — presolver が primal LB を返した直後（`has_model_for_bestvalue = true;` の後）

```cpp
      if (ps_lb != Int_MIN) {
        set_pb_solver_status(sst_sat);
        has_model_for_bestvalue = true;
        lb_last_improved_by_scip = true;  // ← 追加
      }
```

---

### 10. `PbSolver.C` — フラグを `false` に設定する箇所

#### `maximizeGoal()` — SAT solver が解を見つけた直後（行 2083 付近）

`has_model_for_bestvalue = true;`（行 2083）の直後に追加：

```cpp
      has_model_for_bestvalue = true;
      lb_last_improved_by_scip = false;  // ← 追加: SAT が LB を primal 改善
```

**ポイント**: `evalGoalLB`（固定リテラル LB）が `has_model_for_bestvalue = false` にする箇所（行 1717, 2093, 2182）ではフラグを変更しない。`evalGoalLB` はソルバ実行の後処理であり、どのソルバが LB を「実質的に」改善したかは変わらない。

---

### 11. `PbSolver.C` — `findModelForBestValue()` の修正（行 2593–2631）

`if (!has_model_for_bestvalue) {` の直後に SCIP パスを追加する。

**前提条件**: `findModelForBestValue` が呼ばれる時点では `LB_goalvalue` は `goalExp` の最大値（最適値）。

```cpp
void PbSolver_Minimize::findModelForBestValue(Linear* goalExp, Int& LB_goalvalue, vec<Lit>& assump_ps, int stageCount) {

  if (!has_model_for_bestvalue) {
    // If last primal LB improvement came from cooperative SCIP, prefer SCIP for model retrieval.
    // This avoids stalling in SAT on SCIP-friendly problems.
    if (config->use_cooperative_scip && lb_last_improved_by_scip) {
      vec<Lit> empty_assump;
      Int scip_lb = Int_MIN, scip_ub = Int_MAX;
      report->reportf(2, "findModelForBestValue: trying SCIP (last LB from SCIP)\n");
      // lb_hint = LB_goalvalue - 1: SCIP finds solutions with objective > lb_hint,
      // i.e. objective >= LB_goalvalue (the known optimal value).
      // Passing LB_goalvalue itself would cause SCIP to search for a strictly better
      // solution and return sst_unsat.
      solveScip(empty_assump, config->scip_time_budget, goalExp, LB_goalvalue - 1, &scip_lb, &scip_ub);
      if (scip_lb != Int_MIN) {
        // SCIP found a feasible solution; best_model is updated via eventExecBestSol.
        LB_goalvalue = evalGoal(*goalExp);
        best_goalvalue = evalGoal(*original_goal);
        has_model_for_bestvalue = true;
        report->reportf(2, "in findModelForBestValue (SCIP):\n");
        printBestGoalValue();
        return;
      }
      report->reportf(2, "findModelForBestValue: SCIP failed, falling back to SAT\n");
    }

    // SAT path (original, used when lb_last_improved_by_scip == false or SCIP fallback)
    vec<Lit> goal_ps;
    vec<Int> goal_Cs;
    for (int i = 0; i < goalExp->size; i++) goal_ps.push((*goalExp)[i]), goal_Cs.push((*goalExp)(i));

    assert(LB_goalvalue != Int_MIN);
    if (addGoalConstraint(goal_ps, goal_Cs, LB_goalvalue, assump_ps, stageCount)) {
      if (config->verbosity >= 3) report->reportf("unsat returned without sat-solver unexpectedly\n");
      assert(false);
    }

    if (sat_solver_invoke(assump_ps) != sst_sat) {
      if (config->verbosity >= 3) report->reportf("unsat returned without sat-solver unexpectedly\n");
      assert(false);
    } else {
      best_model.clear();
      for (Var x = 0; x < pb_n_vars; x++) assert(sat_solver->model(x) != ll_Undef), best_model.push(sat_solver->model(x) == ll_True);
      LB_goalvalue = evalGoal(*goalExp);
      best_goalvalue = evalGoal(*original_goal);
      report->reportf(2, "in findModelForBestValue: \n");
      printBestGoalValue();
      has_model_for_bestvalue = true;
    }
  }
}
```

**ポイント**:
- `lb_hint = LB_goalvalue - 1`: SCIP は「lb_hint より優れた解」を探す。`LB_goalvalue` を渡すと最適値より優れた解を要求してしまい sst_unsat になる。`LB_goalvalue - 1` を渡すことで `objective >= LB_goalvalue` の解（＝最適値の解）を探す
- `solveScip` は `has_model_for_bestvalue` を設定しない（設定はコード上の呼び出し元の責任）。`scip_lb != Int_MIN` で SCIP が実行可能解を見つけたかを判定し、`has_model_for_bestvalue = true` を明示的に設定する
- `lb_last_improved_by_scip == false` のとき（SAT が LB を改善）: SCIP ブロックをスキップし、直接 SAT パスへ → SAT 有利問題では既存動作と同一
- `lb_last_improved_by_scip == true` で SCIP が失敗した場合（`scip_lb == Int_MIN`）: SAT フォールバックを実行（堅牢性確保）
- `config->use_cooperative_scip == true` ならば SCIP は初期化済みのため `scip_env` の NULL チェックは不要

---

## 動作確認（Phase 2）

```bash
make d

# SCIP が得意・SAT が苦手な問題でモデル探索が SCIP で行われること確認
./naps_debug -mc -nm -v2 -cSCIP err-insts/normalized-ns894788.opb

# SAT が得意な問題で SCIP パスにフォールバックせず SAT が動くこと確認
./naps_debug -cSCIP Examples/garden9x9.opb.opb

# 全テスト
cd build_debug && ctest -V
```

確認ポイント：
1. `normalized-ns894788.opb`: ログに `findModelForBestValue: trying SCIP` が出現し、停止しない
2. `lb_last_improved_by_scip == false` の問題（SAT 有利）: SAT パスを使用（既存動作と同一）
3. SCIP がモデルを見つけられない場合: SAT フォールバックが動作し、assert(false) に到達しない
4. `-mc` オプション付き: モデルチェックが正常通過

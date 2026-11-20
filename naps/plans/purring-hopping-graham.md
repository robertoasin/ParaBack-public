# 計画: Counting モード（-A/-AI）での SCIP 対応

## 背景・問題

`-AI -SCIP` を組み合わせた場合、`PbSolver_Counting::solve()` は `use_scip` フラグを無視して
SAT ベースの `enumerate()` を呼ぶ。しかし `coding()` は SCIP モードで即時リターンするため:
- `pb_n_vars` が未初期化のまま
- PB 制約が SAT ソルバに登録されていない
→ 禁止節が機能せず、同じモデルを無限に出力する

## 目標

Counting モードでも SCIP を使ってモデル列挙する。
- 基本制約は他モードと同様に SCIP に登録（`initScipForCoop()` 経由）
- 禁止節は PB 制約 `Σ (¬model_literal_i) ≥ 1` として SCIP に追加
- UNSAT になるまでループ（= 全モデル列挙完了）

## 禁止節の定式化

モデル `m[0..n-1]` に対する禁止節:
```
Σ_{i: m[i]=false} x_i + Σ_{i: m[i]=true} (1 - x_i) ≥ 1
```

`addScipLinearConstraint(ps, Cs, lb=1, ub=Int_MAX)` に渡す:
- `ps[i] = Lit(i, m[i])` — m[i]=true なら負リテラル ¬x_i
- `Cs[i] = 1`
- 内部で負リテラルは係数 −1・lhs 調整に変換 → 正しく `≥ 1` 制約になる

Interest モード（-AI）では `pb_i_vars` 個の変数のみ使用。

## 変更ファイル

### 1. Main.C (リバート)

前セッションで追加した counting モード用の `use_scip = false` を除去する。

```cpp
// この5行を削除:
// Counting mode (-A/-AI) uses SAT-based enumeration and does not support SCIP.
if ((config->command == cmd_AllSolutions || ...)
    && config->use_scip) {
  config->use_scip = false;
}
```

### 2. PbSolver.h

`PbSolver_Counting` クラスに `enumerateScip` を追加:

```cpp
// enumerate() の宣言の近く
ResultT enumerateScip(bool interestMode, vec<Lit>& assump_ps);
```

### 3. PbSolver_scip.C (`#ifdef USE_SCIP` ブロック内)

`enumerateScip` の本実装を追加:

```cpp
ResultT PbSolver_Counting::enumerateScip(bool interestMode, vec<Lit>& assump_ps) {
    // 1. SCIP 初期化（制約登録・変数生成）→ pb_n_vars も設定される
    initScipForCoop();
    if (!scip_env) return rt_unknown;

    SCIP* scip = (SCIP*)scip_env;
    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;

    // 目的関数なし（充足可能性問題として解く）
    SCIPsetObjsense(scip, SCIP_OBJSENSE_MINIMIZE);
    for (size_t i = 0; i < vars->size(); i++)
        SCIPchgVarObj(scip, (*vars)[i], 0.0);

    int enum_vars = (interestMode && pb_i_vars >= 0) ? pb_i_vars : pb_n_vars;
    ResultT result = rt_unknown;

    while (true) {
        if (interrupted) { result = rt_unknown; break; }

        scip_event_fired = false;
        SCIP_RETCODE rc = SCIPsolve(scip);
        if (rc != SCIP_OKAY) { result = rt_unknown; break; }

        SCIP_STATUS status = SCIPgetStatus(scip);

        if (status == SCIP_STATUS_INFEASIBLE || status == SCIP_STATUS_INFORUNBD) {
            result = rt_all;
            break;
        } else if (status == SCIP_STATUS_USERINTERRUPT ||
                   status == SCIP_STATUS_TERMINATE || interrupted) {
            result = rt_unknown;
            break;
        }

        SCIP_SOL* sol = SCIPgetBestSol(scip);
        if (!sol) { result = rt_unknown; break; }

        // モデル記録
        best_model.clear();
        for (int i = 0; i < pb_n_vars; i++) {
            double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
            best_model.push(val > 0.5);
        }
        set_pb_solver_status(sst_sat);
        number_models++;

        // モデル出力（既存 enumerate() と同じフォーマット）
        report->reportf("MODEL# %d:", number_models);
        int col = 0;
        for (int x = 0; x < enum_vars; x++) {
            if (config->model_out && (!config->positive_mode || best_model[x])) {
                if (col % 2000 >= 1999) printf("\nv");
                col++;
                report->reportf(" %s%s", best_model[x] ? "" : "-", index2name[x]);
            }
        }
        report->reportf("\n");

        if (interrupted) { result = rt_unknown; break; }

        // 禁止節を PB 制約として SCIP に追加
        vec<Lit> ban_ps;
        vec<Int> ban_Cs;
        for (int i = 0; i < enum_vars; i++) {
            ban_ps.push(Lit(i, best_model[i]));  // ¬model_literal
            ban_Cs.push(Int(1));
        }

        SCIPfreeTransform(scip);
        addScipLinearConstraint(ban_ps, ban_Cs, Int(1), Int_MAX);
    }

    return result;
}
```

### 4. PbSolver_scip.C (`#else` ブロック内)

非 SCIP コンパイル時のスタブを追加（エラーを出して停止）:

```cpp
ResultT PbSolver_Counting::enumerateScip(bool interestMode, vec<Lit>& assump_ps) {
    report->reportf(0, "ERROR: SCIP solver was disabled at compile time.\n");
    throw std::runtime_error("SCIP solver was disabled at compile time.");
}
```

### 5. PbSolver.C — `PbSolver_Counting::solve()`

SCIP パスを追加:

```cpp
solverStatus PbSolver_Counting::solve(vec<Lit>& assump_ps)
{
  double start_time = cpuTime();

  if (config->use_scip) {
    bool interestMode = (config->command == cmd_InterestSolutions);
    ResultT r = enumerateScip(interestMode, assump_ps);
    if      (r == rt_unknown) set_pb_solver_status(sst_unknown);
    else if (r == rt_unsat)   set_pb_solver_status(sst_unsat);
    else if (r == rt_sat)     set_pb_solver_status(sst_sat);
    else if (r == rt_all)     set_pb_solver_status(sst_all);
    config->stats_counting_solver_time += (cpuTime() - start_time);
    return get_pb_solver_status();
  }

  // 既存 SAT ベース処理 (変更なし)
  for (int i = 0; i < pb_n_vars; i++)
    sat_solver->freeze(i);
  ...
```

## 検証

```bash
make r

# SCIP なしで 880 モデルが出るか確認（基準）
./naps_release -AI Examples/magic4-ordered.opb | grep "ALL MODELS"
# → s ALL MODELS: 880

# SCIP ありで同じ結果になるか
./naps_release -AI -SCIP Examples/magic4-ordered.opb | grep "ALL MODELS"
# → s ALL MODELS: 880

# timeout で停止するか確認
timeout 0.1 ./naps_release -AI -SCIP Examples/magic4-ordered.opb
# → TERMINATED by signal 15 と出て停止すること
```

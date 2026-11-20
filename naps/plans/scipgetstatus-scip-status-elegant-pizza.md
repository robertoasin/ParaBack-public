# Context

`solveScip` は `out_dual_ub`（双対上界）を出力パラメータで返すが、
SCIP が OPTIMAL を返したとき primal (LB) を返す対称的なパラメータがない。
呼び出し側は `best_goalvalue` を直接参照して LB 更新を行っているが、
`best_goalvalue` は `original_goal` の値であり `goalExp` の最適値ではない。
SCIP 側の primal bound (`SCIPgetPrimalbound`) を使うことで正確な goalEXP の最適値が得られる。

# 変更内容

## 1. `PbSolver.h` — パラメータ順序と追加

`out_lb` を `lb_hint` の直後・`out_dual_ub` の前に挿入する：

```cpp
solverStatus solveScip(vec<Lit>& assump_ps,
                       double time_limit  = -1.0,
                       const Linear* coop_goal = nullptr,
                       Int    lb_hint     = Int_MIN,
                       Int*   out_lb      = nullptr,   // ← 追加
                       Int*   out_dual_ub = nullptr,
                       Int    ub_hint     = Int_MAX);
```

## 2. `PbSolver_scip.C` — 実装側

シグネチャを同様に更新し、`out_dual_ub` ブロックの OPTIMAL 分岐で `out_lb` を設定：

```cpp
if (status == SCIP_STATUS_OPTIMAL) {
    if (out_lb != nullptr) {
        double primal = SCIPgetPrimalbound(scip);
        if (!SCIPisInfinity(scip, primal) && !SCIPisInfinity(scip, -primal))
            *out_lb = Int(primal + scip_coop_offset);
        else if (lb_hint != Int_MIN)
            *out_lb = lb_hint;  // fallback
    }
    // out_dual_ub は既存の SCIPgetDualbound 分岐で設定（OPTIMAL では primal == dual）
    ...
}
```

## 3. `PbSolver_scip.C` — スタブ（line 624）

引数順を合わせる：

```cpp
solverStatus PbSolver::solveScip(vec<Lit>&, double, const Linear*, Int, Int*, Int*, Int) {
```

## 4. `PbSolver.C` — 呼び出し側

```cpp
Int scip_lb = Int_MIN;
// ub_hint あり:
scip_result = solveScip(empty_assump, budget, goalExp, LB_goalvalue, &scip_lb, &scip_dual_ub, UB_goalvalue);
// ub_hint なし:
scip_result = solveScip(empty_assump, budget, goalExp, LB_goalvalue, &scip_lb, &scip_dual_ub);
```

LB 更新ブロックを `scip_lb` 使用に変更（stageCount 制限なし）：

```cpp
if (scip_lb != Int_MIN) {
    if (updateLBGoalvalue(report, scip_lb, LB_goalvalue, config->verbosity)) {
        has_model_for_bestvalue = true;
        config->stats_coop_scip_lb_improvements++;
        ...
    }
}
```

# Verification

`make d` でビルドし、cooperative SCIP で OPTIMAL が返るケースを実行して
`out_lb` に goalEXP の最適値が正しく入り、LB が更新されることをログで確認する。

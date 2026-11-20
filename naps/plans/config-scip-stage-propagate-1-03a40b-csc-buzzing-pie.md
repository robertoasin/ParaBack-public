# Plan: scip_stage_propagate のデュアル削減バグ修正

## Context

`-scip-stage-propagate` オプション有効時、ステージ境界で変数を固定（fix）する処理が誤解答を生じさせている。正常ログと異常ログの比較で最終最適値に差異（2650173698357 vs 2650173698363）が発生しており、ステージ2→3境界で3945個の変数が誤って固定されているのが原因。

## 根本原因の分析

### SCIPの目的関数の状態（addScipLinearConstraint 呼び出し時点）

ステージN の `maximizeGoal()` 完了後、`addScipLinearConstraint(goal_N, V_N, V_N)` が呼ばれる時点で：

- SCIPの目的関数 = **ステージN の `coop_goal`（maximize goal_N）**
- 追加された等式制約 = `goal_N = V_N`
- この2つが共存した状態で `SCIPpresolve()` が実行される

等式制約により `goal_N = V_N` は定数化されるため、`goal_N` に現れる変数については目的関数ベースのデュアル削減は原則として効果がない。

### デュアル削減が誤固定を引き起こすパターン

**`goal_N` に現れない変数へのデュアル削減**が問題の核心：

SCIP の `dualfix` プリソルバーは次の推論を行う：

> 「変数 `z` は目的関数 `goal_N` に現れない（目的係数 = 0）→ `z` を下限値 0 に固定しても目的値は変わらない → 固定してよい」

しかしこれは **`goal_N = V_N` を満たすすべての割当で `z = 0`** であることを意味しない。ステージN+1 では `z = 1` が必要な実行可能解が存在しうる。

### 実証的な証拠

- ステージ1→2で51変数固定 → `goal_2` の lb が 1162→1163 に上昇
- `goal_1` に現れない変数が `goal_2` に正の係数で現れており、0 に固定されることで一部の `goal_2` 達成解が排除された
- ステージ2→3で3945変数固定 → 最終最適値が 6 だけ大きくなる誤解答

## 修正方針

`SCIPpresolve()` 呼び出しの前に `misc/allowdualreds` と `misc/allowstrongdualreds` を `FALSE` に設定し、デュアル削減を無効化する。`SCIPfreeTransform()` 後にパラメータを復元する。

## 変更ファイル

**[PbSolver_scip.C](PbSolver_scip.C) 行 382-421**

### 修正内容（差分）

```diff
     if (config->use_cooperative_scip && config->scip_stage_propagate) {
         int saved_rounds = -1;
         SCIPgetIntParam(scip, "presolving/maxrounds", &saved_rounds);
         SCIPsetIntParam(scip, "presolving/maxrounds", config->scip_stage_propagate_rounds);
 
+        // Dual reductions exploit the current objective function and can incorrectly
+        // fix variables not present in goal_N (objective coefficient = 0) to their
+        // lower bounds. These fixings are unsound for all feasible solutions of the
+        // equality constraint and would corrupt subsequent stages.
+        SCIP_Bool saved_dualreds = TRUE, saved_strongdualreds = TRUE;
+        SCIPgetBoolParam(scip, "misc/allowdualreds", &saved_dualreds);
+        SCIPgetBoolParam(scip, "misc/allowstrongdualreds", &saved_strongdualreds);
+        SCIPsetBoolParam(scip, "misc/allowdualreds", FALSE);
+        SCIPsetBoolParam(scip, "misc/allowstrongdualreds", FALSE);
+
         SCIP_RETCODE presolve_rc = SCIPpresolve(scip);
```

`SCIPfreeTransform()` の後にパラメータを復元：

```diff
         if (SCIPfreeTransform(scip) != SCIP_OKAY)
             throw std::runtime_error("SCIPfreeTransform failed after stage-boundary presolve");
         config->stats_scip_free_transform_count++;
         scip_in_transformed_state = false;
         scip_last_lb_hint_scip = 0.0;
 
+        SCIPsetBoolParam(scip, "misc/allowdualreds", saved_dualreds);
+        SCIPsetBoolParam(scip, "misc/allowstrongdualreds", saved_strongdualreds);
+
         SCIPsetIntParam(scip, "presolving/maxrounds", saved_rounds);
     }
```

### コメント更新

[PbSolver_scip.C:376-379](PbSolver_scip.C) のコメントを実態に合わせて修正:

```diff
-    // Run SCIPpresolve with the just-added equality constraint (lb=ub=V_N) to find
-    // variables that are logically fixed by constraint propagation alone — unlike the
-    // B&B-based global bounds in solveScip (disabled via false&&), these fixings are
-    // sound regardless of the objective function used in subsequent stages.
+    // Run SCIPpresolve with the just-added equality constraint (lb=ub=V_N) to find
+    // variables that are logically fixed by primal constraint propagation.
+    // Dual reductions are disabled (misc/allowdualreds=FALSE) because they exploit
+    // the current objective function (maximize goal_N): variables absent from goal_N
+    // would be incorrectly fixed to their lower bounds even though those fixings are
+    // not valid for all feasible solutions of goal_N=V_N.
```

## パラメータ復元の正確性

`allowdualreds` / `allowstrongdualreds` の保存・復元は既存の `presolving/maxrounds` と同一パターン。ブロック内に早期 return なし、`SCIPfreeTransform()` の直後に無条件で復元されるため、ステージ境界伝播の外側での SCIP 動作は変化しない。

観測された差異の正確な説明（「旧ログ」= `-scip-stage-prop` なしの実行との比較）:
- **freeTransform 2→4回**: ステージ境界ごとに `SCIPpresolve()` + `SCIPfreeTransform()` が追加されるため（2ステージ境界 × 1回 = +2回）。これは `-scip-stage-prop` 機能そのものの動作であり、`allowdualreds` 制御の副作用ではない
- **presolvingメッセージ増加**: 誤固定がなくなり後続ステージの問題規模が正しく（大きく）なったため、SCIP の実際の解法でより多くのプリソルビングが走る期待通りの挙動

## 検証方法

1. `make d` でデバッグビルド
2. 同じインスタンスで `-scip-stage-prop` 付きと無しで実行：
   ```
   ./naps_debug -nm -mc -cSCIP -v3 -scip-stage-prop <instance>
   ```
3. 最終最適値が正常ログと一致することを確認（期待値: `2650173698357`）
4. 「SCIP stage-boundary propagated N fixed variables」の N が大幅に減少することを確認（デュアル削減が原因だった変数が固定されなくなる）
5. `cd build_debug && ctest` で回帰テストを実行

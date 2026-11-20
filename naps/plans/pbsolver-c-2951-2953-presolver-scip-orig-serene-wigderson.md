# 分析: presolve_ub の再利用可否と SCIP Presolver 結果の活用

## 結論（先に）

**ユーザーの推測は誤り。** SCIP presolver は internal goal を**最大化**しており、`presolve_ub` は `UB_goalvalue` として直接利用可能。コメントアウトを元に戻すのが正しい。

---

## Context

[PbSolver.C:2951-2953](../../../proj/naps/naps/PbSolver.C) でコメントアウトされたコードは、SCIP presolver の dual bound を `UB_goalvalue` として利用しようとするものだった。

ユーザーの推測：「SCIP presolver は original goal の最小化問題を解いているので、最大化すべき goal と異なり `presolve_ub` の再利用は不可能」

---

## 誤りの根拠：SCIP の目的関数方向

`solveScip()` の目的関数設定ロジック（[PbSolver_scip.C:515-563](../../../proj/naps/naps/PbSolver_scip.C)）:

```cpp
if (coop_goal == nullptr && min_solver && ...) {
    SCIPsetObjsense(scip, SCIP_OBJSENSE_MINIMIZE);  // original_goal の最小化
    ...
} else {
    if (coop_goal != nullptr && ...) {
        SCIPsetObjsense(scip, SCIP_OBJSENSE_MAXIMIZE);  // 内部 goal の最大化
```

Presolver 呼び出し（[PbSolver.C:2674-2675](../../../proj/naps/naps/PbSolver.C)）:

```cpp
solverStatus ps_result = solveScip(empty_assump, config->scip_as_presolve,
                                   goal, Int_MIN, &ps_lb, &ps_ub);
```

- 第3引数 `coop_goal = goal = this->goal`（内部正規化最大化形式）= **nullptr ではない**
- よって SCIP は `SCIP_OBJSENSE_MAXIMIZE` で動作
- 最大化対象は `this->goal`（`maximizeGoal()` で使う goal と同一オブジェクト）

---

## `presolve_lb` / `presolve_ub` のセマンティクス

`out_lb`（= `presolve_lb`）の設定（[PbSolver_scip.C:723-728](../../../proj/naps/naps/PbSolver_scip.C)）:
```cpp
*out_lb = Int(primal + scip_coop_offset - 0.3);
// primal = SCIPgetPrimalbound()（最大化の最良実行可能値 = 内部 goal の下界）
```

`out_dual_ub`（= `presolve_ub`）の設定（[PbSolver_scip.C:736-744](../../../proj/naps/naps/PbSolver_scip.C)）:
```cpp
*out_dual_ub = Int(dual + scip_coop_offset + 0.3);
// dual = SCIPgetDualbound()（最大化問題の dual bound = 内部 goal の上界）
```

SCIP の最大化問題における dual bound = 最適値の**上界**。

よって:
- `presolve_ub` = 内部 goal 最適値の上界 → `UB_goalvalue` に直接使用可能（✓）
- `presolve_lb` = 内部 goal の下界（最良実行可能値）→ `LB_goalvalue` に直接使用可能（✓）

---

## 提案する変更

### 変更ファイル
- [PbSolver.C:2949-2953](../../../proj/naps/naps/PbSolver.C)

### 変更内容

**コメントアウトを解除し、コメントを修正する（単一ステージの `else` ブランチ内）:**

```cpp
// Apply presolver dual bound as initial UB (single-stage only: presolve used full 'goal')
if (config->use_cooperative_scip && config->scip_as_presolve > 0.0 &&
    presolve_ub != Int_MAX && presolve_ub < UB_goalvalue)
  UB_goalvalue = presolve_ub;
```

**さらに `presolve_lb` を `LB_goalvalue` に活用することも検討可能:**

```cpp
if (config->use_cooperative_scip && config->scip_as_presolve > 0.0 &&
    presolve_lb != Int_MIN && presolve_lb > LB_goalvalue)
  LB_goalvalue = presolve_lb;
```

これにより、presolver がタイムアウトで実行可能解を見つけた場合の下界も活用できる。

### 変更が適用される条件
- 単一ステージモードのみ（`else` ブランチ、[PbSolver.C:2939](../../../proj/naps/naps/PbSolver.C)）
- 多段階最適化では presolver は full `goal` を解いており、各ステージの goal とは異なるため適用不可

---

## 検証方法

```bash
# デバッグビルド
make d

# 小規模インスタンスで presolver の bound が正しく反映されるか確認
./naps_debug -cSCIP -scip-as-presolve 1.0 -v2 Examples/equal5.opb 2>&1 | grep -E "presolver|UB|LB|goal"

# テスト
cd build_debug && ctest -V
```

verbosity 2 で `UB_goalvalue` / `LB_goalvalue` の初期値が presolver の bound を反映しているかログで確認する。

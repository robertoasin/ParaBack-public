# バグ修正計画：best_model が意図せず変化する問題

## Context

`-cSCIP -mc` 使用時、`best_goalvalue=-167` が確定した後に  
`evalGoal(*original_goal)` が `-141` を返し例外が発生する。

ログの流れ：
```
o -167                          ← SCIP event handler が発火、best_model=-167解に更新
c findModelForBestValue: pb_solver_status=sst_sat
s OPTIMUM FOUND
c Model check passed            ← 制約充足チェックは通過（-141解は feasible）
c Goal value is wrong wrt best assignment: (best_goalvalue: -167, evalGoal_value: -141
c ERROR: Exception caught: Model is differ from the best goalvalue
```

## 根本原因

**ファイル**: [PbSolver_scip.C](PbSolver_scip.C)、`eventExecBestSol` 関数（約64〜122行目）

### バグのメカニズム

```c++
// 71-74行: best_model を SCIP 解で【無条件に】上書き
pb->best_model.growTo(pb->pb_n_vars, false);
for (int i = 0; i < pb->pb_n_vars; i++) {
    double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
    pb->best_model[i] = (val > 0.5);
}
// ...
// 89行: この best_model を使って new_goalvalue を計算
Int new_goalvalue = min_solver->evalGoal(*(min_solver->original_goal));
if (min_solver->best_goalvalue == Int_MIN || new_goalvalue < min_solver->best_goalvalue) {
    // 92-96行: 改善時だけ best_model を再更新・best_goalvalue も更新
    min_solver->best_goalvalue = new_goalvalue;
    ...
}
// ← 改善でない場合: best_model=-141解 のまま、best_goalvalue=-167 → 不整合！
```

### 発火シナリオ

1. 協調SCIPソルブで解 `-167` を発見 → `best_model=-167解`, `best_goalvalue=-167`
2. `has_model_for_bestvalue=false`（event handler は設定しない）のため `findModelForBestValue` が再実行
3. `findModelForBestValueBySCIP(LB_goalvalue-1=-168)` が呼ばれる
4. SCIP が再探索し、まず中間解 `-141` を発見 → event handler が発火
5. **71-74行**: `best_model` が `-141` 解で無条件上書きされる
6. **89行**: `new_goalvalue=-141`、**90行**: `-141 < -167` → 偽 → 92-96行スキップ
7. 結果: `best_model=-141解`, `best_goalvalue=-167` → **不整合確定**
8. SCIP が UNSAT（-168以下の解なし）を証明 → `findModelForBestValueBySCIP` が false を返す
9. `model_check()` は `-141` 解が制約を満たすため通過
10. `evalGoal(*original_goal)=-141 ≠ best_goalvalue=-167` → **例外**

## 修正方針

`evalGoal` は `best_model` を参照するため、事前に `best_model` を上書きしてから評価する現在の設計が問題。  
**SCIP解の目的値を `best_model` を変更せずに直接計算**することで、改善判定後にのみ `best_model` を更新する。

## 修正内容

**対象ファイル**: [PbSolver_scip.C](PbSolver_scip.C)

### 変更箇所

`eventExecBestSol` 関数の以下の部分を修正：

#### Before（現在のコード、71-96行付近）:

```c++
// 71-74: 無条件に best_model を更新
pb->best_model.growTo(pb->pb_n_vars, false);
for (int i = 0; i < pb->pb_n_vars; i++) {
    double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
    pb->best_model[i] = (val > 0.5);
}
pb->scip_event_fired = true;
pb->set_pb_solver_status(sst_sat);

PbSolver_Minimize* min_solver = dynamic_cast<PbSolver_Minimize*>(pb);

if (!min_solver) {
    pb->best_model.growTo(pb->pb_n_vars, false);
    for (int i = 0; i < pb->pb_n_vars; i++) {
        double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
        pb->best_model[i] = (val > 0.5);
    }
} else if (min_solver->original_goal) {
    Int new_goalvalue = min_solver->evalGoal(*(min_solver->original_goal));
    if (min_solver->best_goalvalue == Int_MIN || new_goalvalue < min_solver->best_goalvalue) {
        min_solver->best_model.growTo(min_solver->pb_n_vars, false);
        for (int i = 0; i < min_solver->pb_n_vars; i++) {
            double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
            min_solver->best_model[i] = (val > 0.5);
        }
        min_solver->best_goalvalue = new_goalvalue;
        ...
    }
}
```

#### After（修正後）:

```c++
pb->scip_event_fired = true;
pb->set_pb_solver_status(sst_sat);

PbSolver_Minimize* min_solver = dynamic_cast<PbSolver_Minimize*>(pb);

if (!min_solver) {
    // 非最小化ケース: best_model を更新
    pb->best_model.growTo(pb->pb_n_vars, false);
    for (int i = 0; i < pb->pb_n_vars; i++) {
        double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
        pb->best_model[i] = (val > 0.5);
    }
} else if (min_solver->original_goal) {
    // best_model を変更せずに SCIP 解から直接目的値を計算
    Int new_goalvalue = 0;
    const Linear& og = *(min_solver->original_goal);
    for (int i = 0; i < og.size; i++) {
        Lit l = og[i];
        double val = SCIPgetSolVal(scip, sol, (*vars)[var(l)]);
        bool v_true = (val > 0.5);
        if (sign(l) ? !v_true : v_true)
            new_goalvalue += og(i);
    }
    if (min_solver->best_goalvalue == Int_MIN || new_goalvalue < min_solver->best_goalvalue) {
        // 改善時のみ best_model を更新
        min_solver->best_model.growTo(min_solver->pb_n_vars, false);
        for (int i = 0; i < min_solver->pb_n_vars; i++) {
            double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
            min_solver->best_model[i] = (val > 0.5);
        }
        min_solver->best_goalvalue = new_goalvalue;
        ...  // 以下は現状のまま（printBestGoalValue, model_check 等）
    }
} else {
    // min_solver だが original_goal がない場合: best_model を更新
    pb->best_model.growTo(pb->pb_n_vars, false);
    for (int i = 0; i < pb->pb_n_vars; i++) {
        double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
        pb->best_model[i] = (val > 0.5);
    }
}
```

### 変更の要点
- **削除**: 無条件の `best_model` 更新（旧71-74行）と `!min_solver` ブランチの冗長な更新（旧83-87行）
- **追加**: `original_goal` の係数・リテラルを使い SCIP 解から直接 `new_goalvalue` を計算するループ
- **既存ロジック**: 改善時の `best_model`/`best_goalvalue` 更新・print・model_check は変更なし

## 検証方法

```bash
# 1. デバッグビルド
make d

# 2. 問題のあった instance を再現
./naps_debug -v3 -vs2 -cSCIP -given-time=2000 -scip-solve-prop -scip-stage-prop \
  -scip-promising-budget=2000 -scip-budget=10 -mc \
  err-insts3/wrong-model/normalized-50-250-false-45-90-4-2000opt.opb 2>&1 | \
  grep -e "^o " -e "^s " -e "Model" -e "wrong wrt" -e "Exception"

# 期待される正常出力:
# o -167
# s OPTIMUM FOUND
# c Model check passed
# c Model check has passed with goal value: -167

# 3. err-insts3/wrong-model/ 配下の全 instance で確認
for f in err-insts3/wrong-model/*.opb; do
  echo -n "$f: "
  ./naps_debug -v0 -cSCIP -given-time=300 -scip-solve-prop -scip-stage-prop \
    -scip-promising-budget=2000 -scip-budget=10 -mc "$f" 2>&1 | \
    grep -e "^s " -e "wrong wrt" -e "Exception"
done
```

## 影響範囲

- `PbSolver_scip.C` の `eventExecBestSol` 関数のみ変更
- `-cSCIP` を使わない通常モードへの影響なし
- 非最小化・original_goal なしの各ケースは既存ロジックを維持

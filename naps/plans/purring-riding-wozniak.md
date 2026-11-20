# プラン: SCIP プレソルバー解決時の最適値 `o` 未出力バグ修正

## Context

`-SCIP` オプション使用時、SCIP のプレソルバーが問題を解いた場合（`SCIPsolve()` の通常解析フェーズではなくプリプロセッシングで解けた場合）、OPB 形式の最適値行 `o <value>` が出力されないバグ。

## 現状の動作

### `best_model` の設定タイミング

| 経路 | `best_model` 設定箇所 |
|------|-----------------------|
| 通常パス | `eventExecBestSol`（行 44-48）: イベント発火のたびに `SCIPgetSolVal` で更新 |
| プレソルバー経路 | `solveScip()` 行 262-266: `SCIPgetSolVal` で設定（イベントは呼ばれない） |

どちらの経路でも `v` 行（解の変数値）は正しく出力されている。

### `o` 行が出力されない理由

- 通常パス: `eventExecBestSol`（行 54-60）が `o <value>` を出力 → **正常**
- プレソルバー経路: `eventExecBestSol` は呼ばれず、`solveScip()` 行 267-269 は `best_goalvalue` を設定するだけで `o` を出力しない → **バグ**

## 修正方針

`eventExecBestSol` が「今回の `SCIPsolve()` 呼び出し中に発火したか」を追跡する `scip_event_fired` フラグを使う。

- `scip_event_fired == false`（プレソルバー経路）：`best_model` の設定と `o` 出力を行う
- `scip_event_fired == true`（通常パス）：`eventExecBestSol` が既に両方実施済みなので不要

これにより `best_model.clear()` + SCIPgetSolVal ループも通常パスでは不要になる（ユーザー提案の最適化）。

### 変更ファイル

1. **`PbSolver.h`**（SCIP state fields 付近、行 210-215）
   - `bool scip_event_fired;` フィールドを追加
   - コンストラクタ初期化リストに `, scip_event_fired(false)` を追加

2. **`PbSolver_scip.C`**（`eventExecBestSol`、行 37-64）
   - `best_model` 設定後に `pb->scip_event_fired = true;` を追加

3. **`PbSolver_scip.C`**（`solveScip()`、行 250-271）
   - `SCIPsolve()` の直前に `scip_event_fired = false;` でリセット
   - `if (status == SCIP_STATUS_OPTIMAL)` ブロック内を `!scip_event_fired` で条件分岐
     - `false`（プレソルバー）の場合: `best_model.clear()` + SCIPgetSolVal ループ + `o` 出力
     - `true`（通常）の場合: `eventExecBestSol` が既に処理済みなので何もしない

## 修正後のコード（差分）

### PbSolver.h — フィールド追加

```cpp
// SCIP state fields
void*   scip_env;
void*   scip_vars;
int     scip_constrs_added;
void*   scip_last_goal;
bool    scip_event_fired;   // ← 追加: 今回の SCIPsolve() 中にイベントが発火したか
bool    interrupted;
```

コンストラクタ初期化リスト（行 105-109 付近）：
```cpp
, scip_last_goal(nullptr)
, scip_event_fired(false)  // ← 追加
, interrupted(false)
```

### PbSolver_scip.C — eventExecBestSol（行 44-48 付近）

```cpp
pb->best_model.clear();
for (int i = 0; i < pb->pb_n_vars; i++) {
    double val = SCIPgetSolVal(scip, sol, (*vars)[i]);
    pb->best_model.push(val > 0.5);
}
pb->scip_event_fired = true;  // ← 追加
pb->set_pb_solver_status(sst_sat);
```

### PbSolver_scip.C — solveScip()（行 250-271）

```cpp
// Solve
double scip_start_time = cpuTime();
scip_event_fired = false;   // ← 追加: 今回の solve 前にリセット
SCIPsolve(scip);
config->stats_scip_time += cpuTime() - scip_start_time;

...

if (status == SCIP_STATUS_OPTIMAL) {
    if (final_sol && !scip_event_fired) {  // ← 条件を追加: presolver 経路のみ
        best_model.clear();
        for (int i = 0; i < pb_n_vars; i++) {
            double val = SCIPgetSolVal(scip, final_sol, (*vars)[i]);
            best_model.push(val > 0.5);
        }
        if (min_solver && min_solver->original_goal) {
            min_solver->best_goalvalue = min_solver->evalGoal(*(min_solver->original_goal));
            // ← 追加: presolver 経路では o を出力（通常パスは eventExecBestSol が出力済み）
            char* tmp = toString(min_solver->best_goalvalue);
            if (config->is_inner_solver) {
                report->reportf(2, "o %s\n", tmp);
            } else {
                printf("o %s\n", tmp);
                fflush(stdout);
            }
            xfree(tmp);
        }
    }
    res = min_solver ? sst_best : sst_sat;
}
```

## 検証方法

1. ビルド: `make d`
2. バグ再現インスタンスで実行:
   ```
   ./naps_debug -SCIP -v2 err-insts2/normalized-lo_6x6_005.opb.metafix.opb
   ```
3. 出力に `o <数値>` 行が含まれることを確認
4. 通常の SCIP 経路（プレソルバーを通らない問題）で `o` が**重複しない**ことを確認
5. `cd build_debug && ctest` でリグレッションがないことを確認

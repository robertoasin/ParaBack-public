# 実装計画：scip_last_was_timelimit による誤答修正

## Context

`-cSCIP` モードで SCIP が `SCIP_STATUS_TIMELIMIT` で終了した後、次の `solveScip` 呼び出し時に `SCIPfreeTransform` をスキップすると、SCIP 内部の対称性シャドウツリーが陳腐化して誤った枝刈りが起きる。その結果、真の最適値より悪い値で `OPTIMUM FOUND` が出る。

修正方法：前回の `solveScip` が TIMELIMIT で終わった場合は必ず `SCIPfreeTransform` を呼ぶ。

## 変更ファイル

- `PbSolver.h`：メンバ変数追加
- `PbSolver_scip.C`：変数セット・リセット、`must_free` 条件追加

---

## 変更詳細

### 1. `PbSolver.h`

**初期化リスト（~行116）** に追加：
```cpp
, scip_last_was_timelimit(false)
```

**メンバ変数宣言（~行228）** に追加：
```cpp
bool    scip_last_was_timelimit;   // true when previous solveScip ended with SCIP_STATUS_TIMELIMIT
```

### 2. `PbSolver_scip.C`

**`must_free` 条件（行580–581）** を修正：
```cpp
// 現在
bool must_free = new_lb_scip < scip_last_lb_hint_scip ||
                 SCIPgetStage(scip) == SCIP_STAGE_SOLVED;

// 修正後
bool must_free = new_lb_scip < scip_last_lb_hint_scip ||
                 SCIPgetStage(scip) == SCIP_STAGE_SOLVED ||
                 scip_last_was_timelimit;
```

**`SCIPfreeTransform` 呼び出し後のリセット（行586–588）** に追加：
```cpp
scip_last_was_timelimit = false;
```

**`solveScip` 末尾の `can_skip_free_transform` 分岐（行728–736）** で状態を記録：
- `can_skip_free_transform == true` のとき（SCIP 状態保持）：
  ```cpp
  scip_last_was_timelimit = (status == SCIP_STATUS_TIMELIMIT);
  ```
- `can_skip_free_transform == false` のとき（`SCIPfreeTransform` 呼び出し後）：
  ```cpp
  scip_last_was_timelimit = false;
  ```

---

## 検証

```bash
make d
# 誤答が出ていたインスタンスで確認（ログファイルあり）
# 期待: goal value = 48（goalEXP = 16）で OPTIMUM FOUND
```

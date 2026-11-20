# Context

SCIP を 9.2.2 → 10.0.2 に切り替えた後、cooperative モードで `solveScip()` を繰り返し呼び出すと以下のエラーが発生する。

```
[scip_prob.c:1469] ERROR: method is not callable in SCIP stage <10>
```

Stage 10 = `SCIP_STAGE_SOLVED`（SCIPsolve() 完了後の状態）。

---

## 根本原因

### API breaking change（SCIP 9.2.2 → 10.0.2）

`SCIPsetObjlimit()` の呼び出し可能ステージが変更された：

| バージョン | SCIP_STAGE_SOLVED (10) から呼べるか |
|---|---|
| SCIP 9.2.2 (`/home/sakai/proj/naps/scipoptsuite-9.2.2/scip/src/scip/scip_prob.h`) | **可能**（リストに含まれる） |
| SCIP 10.0.2 (`/usr/include/scip/scip_prob.h`) | **不可**（SOLVING(9) が上限） |

### エラーが発生するコードパス

[PbSolver_scip.C:466-571](PbSolver_scip.C) の B&B 状態保持ロジック：

1. `can_skip_free_transform = true`（同じゴール・仮定制約なし）のとき、`SCIPsolve()` 後に `SCIPfreeTransform()` をスキップ
2. → SCIP は `SCIP_STAGE_SOLVED` (10) に留まる（`scip_in_transformed_state = true`）
3. 次回 `solveScip()` 呼び出し → `initScipForCoop()` で新制約・新 trail がなければ `SCIPfreeTransform()` を再度スキップ
4. → **SCIP が SOLVED (10) のまま `SCIPsetObjlimit()` が呼ばれてエラー**

既存コード（563-570行）は `lb が減少した` 場合のみ `SCIPfreeTransform()` を強制するが、`lb が増加した` 通常ケースは対処されていない。SCIP 9.2.2 では問題なかったが、10.0.2 で割れた。

---

## 修正方針

**ファイル**: [PbSolver_scip.C](PbSolver_scip.C)（1ファイルのみ変更）

**変更箇所**: lines 563–570 の条件式を拡張する

### 現在のコード（563-572行）

```cpp
if (can_skip_free_transform && scip_in_transformed_state && new_lb_scip < scip_last_lb_hint_scip) {
    // SCIPsetObjlimit cannot relax the cutoff in SOLVING stage; force a restart.
    report->reportf(2, "lb_hint decreased in SCIP obj units; forcing SCIPfreeTransform\n");
    if (SCIPfreeTransform(scip) != SCIP_OKAY)
        throw std::runtime_error("SCIPfreeTransform failed on lb relaxation");
    scip_in_transformed_state = false;
    can_skip_free_transform = false;
}
SCIPsetObjlimit(scip, new_lb_scip);
```

### 修正後

```cpp
if (can_skip_free_transform && scip_in_transformed_state) {
    // SCIPsetObjlimit cannot relax the cutoff when lb decreased,
    // and SCIP 10.0.2 dropped SOLVED from its allowed-stage list (was OK in 9.2.2).
    bool must_free = new_lb_scip < scip_last_lb_hint_scip ||
                     SCIPgetStage(scip) == SCIP_STAGE_SOLVED;
    if (must_free) {
        report->reportf(2, "forcing SCIPfreeTransform before SCIPsetObjlimit\n");
        if (SCIPfreeTransform(scip) != SCIP_OKAY)
            throw std::runtime_error("SCIPfreeTransform failed before SCIPsetObjlimit");
        scip_in_transformed_state = false;
        can_skip_free_transform = false;
        scip_last_lb_hint_scip = 0.0;
    }
}
SCIPsetObjlimit(scip, new_lb_scip);
```

---

## 影響範囲

- `scip_in_transformed_state = true` のとき（B&B 保持パス）、SCIP 10.x では `SCIPsolve()` の後は常に SOLVED (10) になるため、実質的に毎回 `SCIPfreeTransform()` してから `SCIPsetObjlimit()` を呼ぶことになる
- B&B 継続最適化は失われるが、SCIP 10.x では `SCIPfreeTransform()` なしに `SCIPsetObjlimit()` を呼ぶ手段がなく、これが唯一正しい修正
- SCIP 9.2.2 との互換性は保たれる（SOLVED から SCIPfreeTransform → PROBLEM は常に有効）

---

## 検証方法

```bash
# ビルド
make d

# 同じコマンドで再現確認（エラーが出なくなることを確認）
./naps_release -nm -v3 -mc -cSCIP \
  benchmark/PB16/normalized-PB16/OPT-BIGINT-LIN/lion9-single-obj/normalized-single-obj-f58-fob_fxl_loss.seq-B-3-1-irabcde.opb \
  2>&1 | grep -E "scip_prob|ERROR|SCIP status"
```

エラーメッセージ `[scip_prob.c:1469] ERROR: method is not callable in SCIP stage <10>` が消えることを確認する。

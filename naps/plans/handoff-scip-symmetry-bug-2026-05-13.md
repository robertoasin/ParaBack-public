# 引き継ぎ：-cSCIP 誤答バグの調査結果（2026-05-13）

## 状況

`naps-1.03a37.sh -v3 -cSCIP -given-time=2000 -mc` で誤った `OPTIMUM FOUND` が出るバグを調査中。
再現が難しいが、`can_skip_free_transform` 導入後に発生するようになった。

**ログファイル（再現例）：**
`nabeshima/logs/wrong-opt-logs/normalized-hw64-vm75p-opt.opb.negationfix.opb.log.v`
- 問題の真の最適値：**48**（goalEXP内部値では **16**）
- ソルバーの誤答：goal value = **54**（goalEXP = **10**）で OPTIMUM FOUND

**確認済み事実：**
- 毎回 `SCIPfreeTransform` を強制するバージョンでは誤答なし
- `can_skip_free_transform` による B&B 状態保持が原因

---

## 内部単位の整理

NaPS は最小化問題を goalEXP の最大化に変換して解く。

```
scip_coop_offset ≈ 64     (goalExp 内の否定リテラル係数和)
SCIP_obj = goalEXP - scip_coop_offset   (SCIPが最大化する値)
goalEXP  = SCIP_obj + scip_coop_offset

真の最適: original_goal=47 → goalEXP=17 → SCIP_obj=-47
誤答:     original_goal=54 → goalEXP=10 → SCIP_obj=-54
```

---

## 根本原因：SCIP 対称性シャドウツリーの陳腐化

### フロー

```
3回目 solveScip（56s, TIMELIMIT）
  → initScipForCoop: freeing transform（trail 増加を検出）
  → SCIP が対称性計算：537 generators、シャドウツリー構築
  → dual=-47, primal=-54、TIMELIMIT で中断
  → can_skip_free_transform=true, scip_in_transformed_state=true

4回目 solveScip（156.8s）
  → trail 増加なし → must_free の条件を確認：
      new_lb_scip(-54) < scip_last_lb_hint_scip(-54) → false
      SCIPgetStage == SCIP_STAGE_SOLVED              → false（TIMELIMITはSOLVINGステージ）
  → SCIPfreeTransform を呼ばずに B&B を継続
  → 新規 B&B ノードがシャドウツリーに存在しない
  → 警告："Attempting orbital reduction on nodes not existing in the symmetry shadowtree"
  → シャドウツリー外ノードへの誤った対称性リダクション → 真の最適枝を誤剪定
  → SCIP が dual=primal=-54（gap=0%）で OPTIMAL 宣言（本来は-47のはず）
  → scip_dual_ub = Int(-54 + 64 + 0.3) = 10 → UB を 17→10 に更新
  → LB=UB=10 → OPTIMUM FOUND（誤）
```

### "Both SAT and SCIP timed out" メッセージについて

SCIP はタイムアウトしていないが、LB が変化しなかった（`scip_lb=10`=既存 LB）ため
`num_improved_by_scip=0` となり誤解を招くメッセージが出る。
→ このメッセージ自体はバグではなく誤解を招く実装。

### SAT trail 同期は正しい（レベル 0 のみ）

`Solver_CaDiCaL::trail_size()` は `fixed_lits.size()` を返す。
`fixed_lits` は CaDiCaL の `notify_fixed` コールバック（レベル 0 永続固定のみ）で追加される。
→ `SCIPchgVarLbGlobal`/`SCIPchgVarUbGlobal` への同期はレベル 0 のみで正しい。

---

## 修正方針（実装済み）

`PbSolver_scip.C:580` の `must_free` 条件を以下のように修正済み：

```c
bool must_free = new_lb_scip < scip_last_lb_hint_scip ||
                 SCIPgetStage(scip) == SCIP_STAGE_SOLVED ||
                 (scip_last_was_timelimit && (config->scip_eager_free_transform || scip_last_nnodes > 0));
```

**各フラグの意味：**

- `scip_last_was_timelimit`：前回の solveScip が `SCIP_STATUS_TIMELIMIT` で返ったことを記録するメンバ変数
- `scip_last_nnodes`：TIMELIMIT 時の `SCIPgetNNodes(scip)` を保存したメンバ変数
- `config->scip_eager_free_transform`：`-scip-eager-free-transform` オプションで常に free する保守的モード

**条件の意味：**

| TIMELIMIT 時の状況 | `scip_last_nnodes` | 動作 |
|---|---|---|
| プリソルブ中断（対称性計算含む） | 0 | free しない（プリソルブ状態を再利用） |
| B&B 開始後に中断（シャドウツリー使用済み） | ≥ 1 | **free する**（正確性確保）|

シャドウツリーは B&B 開始後に初めて使われるため、`scip_last_nnodes > 0` が  
陳腐化の正確な判定条件となる。プリソルブだけで中断した場合は  
再利用することで高コストなプリソルブ（対称性計算等 30s 超）を節約できる。

---

## SCIPfreeTransform が必要な条件まとめ

| 条件 | 現在の検出 |
|------|-----------|
| ゴール関数（coop_goal）が変わった | ✅ scip_last_goal ポインタ比較 |
| PB制約が追加された | ✅ scip_constr_synced 比較 |
| 確定変数が追加された（trail増加） | ✅ trail_size 比較 |
| lb_hint が前回より下がった（下界緩和） | ✅ new_lb_scip < scip_last_lb_hint_scip |
| SCIP stage が SOLVED | ✅ SCIPgetStage 比較 |
| **TIMELIMIT後のB&B継続（対称性陳腐化）** | ✅ `scip_last_was_timelimit && scip_last_nnodes > 0` |

---

## 関連コード箇所

| ファイル | 行 | 内容 |
|---------|-----|------|
| `PbSolver_scip.C` | ~477 | `can_skip_free_transform` 判定開始 |
| `PbSolver_scip.C` | ~577–592 | `must_free` 条件（修正対象） |
| `PbSolver_scip.C` | ~640–675 | SCIP status → `sst_best` 変換 |
| `PbSolver_scip.C` | ~696–717 | primal/dual → scip_lb/scip_dual_ub 変換 |
| `PbSolver_scip.C` | ~284–302 | trail sync（レベル 0 のみ、正しい） |
| `PbSolver.C` | ~2291–2360 | SCIP 呼び出し後の LB/UB 更新・終了判定 |
| `Solver.C` | ~430–438 | `Solver_CaDiCaL::trail_size()`（fixed_lits のみ） |

---

## 残タスク

- [ ] `normalized-hw64-vm75p-opt.opb.negationfix.opb` で再実行して goal value = 47 を確認
- [ ] 他の誤答インスタンス（`err-insts/`, `err-insts2/`）でも同様に修正が効くか確認
- [ ] `-scip-eager-free-transform` オプションの有無で性能差を計測

# sat_current_time_budget / scip_current_time_budget 更新フローの妥当性検討

## 対象コード（現在の状態）

- [PbSolver.C:2615-2616](PbSolver.C#L2615) — solve() での初期化
- [PbSolver.C:1812](PbSolver.C#L1812) — scip_trigger_budget ローカル変数の初期化
- [PbSolver.C:1984-2011](PbSolver.C#L1984) — SAT ソルバー呼び出し・タイマー計測
- [PbSolver.C:2200](PbSolver.C#L2200) — SCIP トリガー判定（変更済み）
- [PbSolver.C:2249-2262](PbSolver.C#L2249) — 予算更新ロジック（変更済み）

---

## 変数の役割

| 変数 | 種別 | 役割 |
|------|------|------|
| `sat_current_time_budget` | メンバ変数 | SAT に与える 1 エポック分の時間上限（累積伸長） |
| `scip_current_time_budget` | メンバ変数 | SCIP 1 回の呼び出しに与える時間上限（累積伸長） |
| `scip_trigger_budget` | ローカル変数 | SAT 時間のカウントダウン。0 以下になったら SCIP を起動 |
| `sat_solver_succeeded` | ローカル変数 | "前回 SCIP 以降に SAT が 1 度でも成功したか" のフラグ |

---

## 更新フローの概要（現在）

```
solve() 開始:
  sat_current_time_budget  = config->sat_time_budget   (初期値)
  scip_current_time_budget = config->scip_time_budget  (初期値)

maximizeGoal() ループ先頭:
  scip_trigger_budget = sat_current_time_budget        (エポック開始時にリセット)

ループ本体:
  SAT 呼び出し (scip_trigger_budget を時間制限として渡す)
  scip_trigger_budget -= 実際の SAT 実行時間
  if sat_result != sst_unknown → sat_solver_succeeded = true

  trigger_scip = (scip_trigger_budget <= 0.0)          ← 変更後：予算枯渇のみ

  if trigger_scip:
    SCIP 呼び出し (scip_current_time_budget を時間制限として渡す)

    [予算更新]
    if sat_solver_succeeded:
      sat_current_time_budget *= sat_time_budget_factor
    if scip_result != sst_unknown || scip_ub_improved:
      scip_current_time_budget *= scip_time_budget_factor
    if !sat_solver_succeeded && scip_result==sst_unknown && !scip_ub_improved:  ← 変更後：AND条件
      sat_current_time_budget  *= double_unknown_budget_factor
      scip_current_time_budget *= double_unknown_budget_factor

    scip_trigger_budget = sat_current_time_budget      (次エポックのリセット)
    sat_solver_succeeded = false
```

---

## 行 2200 の変更について

**変更前:** `trigger_scip = (scip_trigger_budget <= 0.0) || (sat_solver_result == sst_unknown)`  
**変更後:** `trigger_scip = (scip_trigger_budget <= 0.0)`

SAT ソルバーは `scip_trigger_budget` を時間制限として渡されるため、タイムアウトした場合は実際の経過時間がほぼ `scip_trigger_budget` と等しく、減算後に `scip_trigger_budget ≈ 0`（または負）になる。つまり旧来の `|| sst_unknown` 条件は実質的に冗長だった。

**変更の効果:** SCIP トリガー判定が純粋に「累積 SAT 時間の予算消費」として一元化された。SAT タイムアウトが予算内に収まる稀なケース（割り込み等）でも SCIP を即起動しなくなるが、次のループで `scip_trigger_budget <= 0` が成立するため挙動は実質同等。

---

## 行 2255 の変更について

**変更前:** `!sat_solver_succeeded && (scip_result == sst_unknown || !scip_ub_improved)`  
**変更後:** `!sat_solver_succeeded && (scip_result == sst_unknown && !scip_ub_improved)`

旧条件では「SAT 失敗 AND (SCIP タイムアウト OR SCIP の UB 改善なし)」であり、ケース D・F で SCIP に二重乗算が発生していた:

| ケース | sat_solver_succeeded | scip_result | scip_ub_improved | 変更前 scip 乗算 | 変更後 scip 乗算 |
|--------|----------------------|-------------|------------------|-----------------|-----------------|
| D | false | ≠ unknown | false | ×factor ×double ⚠️ | ×factor のみ ✓ |
| E | false | unknown   | false | ×double             | ×double ✓ |
| F | false | unknown   | true  | ×factor ×double ⚠️ | ×factor のみ ✓ |

**変更の効果:** `&&` により double boost が「SAT も SCIP も完全に失敗（タイムアウト＋UB 改善なし）」の場合にのみ適用されるようになり、コメント "Both SAT and SCIP timed out" と整合する。ケース D（SCIP が正常終了したが UB 改善なし）での過剰な SCIP 予算膨張が解消。

---

## 残存する設計上の考慮点（バグではなく仕様として確認すべき点）

### sat_solver_succeeded の意味論

`sat_solver_succeeded` はループ内で一度でも SAT が成功すれば `true` になり、SCIP トリガー時にリセットされる。これは「エポック全体での SAT 生産性」を示す指標で、直前の SAT 呼び出しの成否ではない。

例：SAT が iteration 1 で成功、iteration 2 でタイムアウトして SCIP 起動 → `sat_solver_succeeded = true` → `sat_current_time_budget` が増加。

意図通りであればコメントで明示すべき。直前の呼び出し成否のみ見たい場合は変数を SCIP 直前のループ中のみリセットする別設計が必要。

### scip_trigger_budget の残量破棄

SCIP 呼び出し後は常に `scip_trigger_budget = sat_current_time_budget` でリセットされる（行 2262）。SAT がタイムアウトして残量があった場合でもその残量は捨てられ、次エポックは満額からスタートする。「SCIP 後は SAT に常に満額を与える」という一貫した設計であり、問題はない。

---

## 検証方法

1. `make d` でデバッグビルド
2. verbosity=2 (-v2) で cooperative SCIP モードを有効にした問題を実行
3. ログの `sat_budget=` / `scip solving with time budget=` の推移を観察
4. ケース D（SAT タイムアウト、SCIP 正常終了、UB 改善なし）で二重乗算が起きていないことを確認

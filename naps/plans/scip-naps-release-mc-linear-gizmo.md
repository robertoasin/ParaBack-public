# Plan: SCIP double-precision safety checks

## Context

SCIP は double 精度（IEEE 754 倍精度）で動作するため、整数係数の PB 制約を扱うときに精度の問題が生じ得る。

**Feasibility tolerance 問題**: SCIP の feasibility tolerance は `feastol × max(1, |rhs|)`（デフォルト feastol = 1e-6）。`|rhs| ≥ 1/feastol = 1e6` になると tolerance ≥ 1 unit となり、整数制約を 1 以上違反する解を許容する。

**Activity 計算精度問題**: `activity = sum(c_i * x_i)` (binary x_i) の浮動小数点和で `sum(|c_i|) ≥ 2^53 ≈ 9e15` になると整数値の activity が正確に表現できない。

### Bug 3 の根本原因（`normalized-single-obj-f58-fob_fxl_loss`）

`checkScipPrecisionSafety` が問題制約 (`constrs[]`) に対して **Check A（RHS）しかチェックしていない**ことが原因。

BIGINT-LIN カテゴリのインスタンスでは、問題制約の係数和 `Σ|c_i|` が `2^53 ≈ 9e15` を超えることがある。この場合 SCIP の LP relaxation での activity 計算に丸め誤差 ≥ 1 unit が生じ、LP の dual bound が誤って締まり過ぎる → SCIP が「goals[0] ≤ 582」を証明（実際は 587 が最適）→ stage 1 が誤った最適値で確定 → 以降のステージで "Something is wrong!" が連発 → 最終的に誤答 9,349,970,566（正解: 7,765,267,206）。

**修正**: `checkScipPrecisionSafety` に Check B（係数和チェック）を追加する。

安全チェックは **2 か所**：

| 場所 | 対象 | チェック |
|---|---|---|
| `coding()` の `propagate()` 直後 | 問題制約 (`constrs[]`) | RHS + 係数和（Check A + B） |
| stage 境界の `addScipLinearConstraint` 直後 | ゴール関数係数 (`goal_Cs`) + ゴール最適値 | RHS + 係数和（Check A + B）← 実装済み |

---

## Part 1: 初期制約チェック（`checkScipPrecisionSafety`）の修正

### Call Location

`PbSolver_Minimize::coding()` ([PbSolver.C:2531](PbSolver.C#L2531) 付近):

```
propagate();                     ← ここで問題制約が簡略化される
checkScipPrecisionSafety();      ← ここに挿入（既存）
initScipForCoop();               // 2574
solveScip(presolver);            // 2577 (scip_presolve_time)
convertPbs();
```

### Check Logic（Check A: RHS + Check B: 係数和）

各 `constrs[i]`（null / 両端 sentinel はスキップ）に対して:
```
Check A: c.lo ≠ Int_MIN かつ |c.lo| ≥ 1/feastol  → unsafe
         c.hi ≠ Int_MAX かつ |c.hi| ≥ 1/feastol  → unsafe
Check B: Σ|c(j)| for j in [0, c.size)  ≥ 2^53    → unsafe
```
1件でも unsafe: `config->scip_presolve_time = 0.0` & `config->use_cooperative_scip = false`

`Linear` の係数アクセス: `c(j)` (operator()), サイズ: `c.size`

### 実装 — [PbSolver.h](PbSolver.h) への宣言追加（既存）

`getScipFeastol()` の近くに追加済み:
```cpp
void    checkScipPrecisionSafety();
```

### 実装 — [PbSolver_scip.C](PbSolver_scip.C) 修正（`checkScipPrecisionSafety` に Check B を追加）

現在の実装（lines 394-418）を以下に置き換える:
```cpp
void PbSolver::checkScipPrecisionSafety() {
    if (!config->use_cooperative_scip && config->scip_presolve_time <= 0.0) return;
    const double feastol    = getScipFeastol();
    if (feastol <= 0.0) return;
    const double rhs_limit  = 1.0 / feastol;
    const Int coef_limit((double)(1LL << 53));

    for (int i = 0; i < constrs.size(); i++) {
        if (!constrs[i]) continue;
        const Linear& c = *constrs[i];
        if (c.lo == Int_MIN && c.hi == Int_MAX) continue;

        // Check A: RHS feasibility tolerance
        auto over_rhs = [&](Int b) {
            double ab = todouble(b); if (ab < 0.0) ab = -ab;
            return ab >= rhs_limit;
        };
        if ((c.lo != Int_MIN && over_rhs(c.lo)) ||
            (c.hi != Int_MAX && over_rhs(c.hi))) {
            report->reportf(1,
                "SCIP precision unsafe (constr %d rhs): disabling presolver and cooperative SCIP\n", i);
            config->scip_presolve_time   = 0.0;
            config->use_cooperative_scip = false;
            return;
        }
        // Check B: coefficient sum (LP activity precision)
        Int coef_sum = 0;
        for (int j = 0; j < c.size; j++) {
            Int ci = c(j);
            coef_sum += ci < 0 ? -ci : ci;
            if (coef_sum >= coef_limit) {
                report->reportf(1,
                    "SCIP precision unsafe (constr %d coef sum): disabling presolver and cooperative SCIP\n", i);
                config->scip_presolve_time   = 0.0;
                config->use_cooperative_scip = false;
                return;
            }
        }
    }
}
```

`#else` スタブ（変更なし）:
```cpp
void PbSolver::checkScipPrecisionSafety() {}
```

---

## Part 2: Stage 境界チェック（既存コードへの係数和チェック追加）

### 既存コード（[PbSolver.C:2771-2784](PbSolver.C#L2771)）の現状

```cpp
if (config->use_cooperative_scip) {
  addScipLinearConstraint(goal_ps, goal_Cs, LB_goalvalue, LB_goalvalue);
  double feastol = getScipFeastol();
  double abs_lb = todouble(LB_goalvalue);
  if (abs_lb < 0.0) abs_lb = -abs_lb;
  if (feastol > 0.0 && feastol * abs_lb >= 1.0) {          // Check A: RHS
    config->use_cooperative_scip = false;
    report->reportf(2, "Disabling cooperative SCIP ...\n");
  }
}
```

### 修正後（Check B: ゴール係数和を追加）

```cpp
if (config->use_cooperative_scip) {
  addScipLinearConstraint(goal_ps, goal_Cs, LB_goalvalue, LB_goalvalue);
  bool stage_unsafe = false;
  // Check A: RHS feasibility tolerance
  double feastol = getScipFeastol();
  if (feastol > 0.0) {
    double abs_lb = todouble(LB_goalvalue);
    if (abs_lb < 0.0) abs_lb = -abs_lb;
    if (feastol * abs_lb >= 1.0) stage_unsafe = true;
  }
  // Check B: goal coefficient sum (activity precision)
  if (!stage_unsafe) {
    const Int coef_limit((double)(1LL << 53));
    Int coef_sum = 0;
    for (int j = 0; j < goal_Cs.size() && !stage_unsafe; j++) {
      Int ci = goal_Cs[j];
      coef_sum += ci < 0 ? -ci : ci;
      if (coef_sum >= coef_limit) stage_unsafe = true;
    }
  }
  if (stage_unsafe) {
    config->use_cooperative_scip = false;
    report->reportf(2, "Disabling cooperative SCIP for subsequent stages: "
                       "stage constraint precision unsafe\n");
  }
}
```

---

## Files to Modify

| ファイル | 変更内容 |
|---|---|
| [PbSolver_scip.C](PbSolver_scip.C) | `checkScipPrecisionSafety` に Check B（係数和）を追加（lines 394-418） |
| ~~[PbSolver.h](PbSolver.h)~~ | 宣言追加済み（変更不要） |
| ~~[PbSolver.C](PbSolver.C)~~ | 呼び出し追加済み・Part 2 実装済み（変更不要） |

## Verification

```bash
make d

# 安全な問題 → SCIP 有効のまま（precision メッセージなし）
./naps_debug -v3 -mc -cSCIP Examples/garden9x9.opb 2>&1 | grep -E "(precision|disabl)"
# → 何も出ないはず

# Bug 3 再現インスタンス（BIGINT-LIN, 問題制約に大きい係数和）
# → precision unsafe メッセージが出て SCIP が無効化され、正答 7765267206 になるはず
./naps_debug -v3 -nm -mc -cSCIP -double-unknown-budget-factor=2 \
  benchmark/PB16/normalized-PB16/OPT-BIGINT-LIN/fob_fxl-single-obj/normalized-single-obj-f58-fob_fxl_loss.seq-B-3-1-irabcde.opb \
  2>&1 | grep -E "(precision|disabl|OPTIMUM|Something)"
# → precision unsafe (constr X coef sum): ... が出て SCIP 無効化
# → "s OPTIMUM FOUND" の後に 7765267206

# lion9 インスタンス（係数和チェック先行実装の参照用）
./naps_debug -v3 -nm -mc -cSCIP -double-unknown-budget-factor=2 -sat-budget=6 \
  benchmark/PB16/normalized-PB16/OPT-BIGINT-LIN/lion9-single-obj/normalized-single-obj-f56-FCS_FC1_order3_PB.seq-B-2-1-EDCBAir.opb \
  2>&1 | grep -E "(precision|disabl|Optimized)"
```

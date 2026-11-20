# Plan: findModelForBestValue ログ誤りと remaining 計算バグの修正

## Context

ユーザーが観測したログ:
```
c addGoalConstraint: assump_lit: @goal_constraint0
c findModelForBestValue: trying SAT first (sat_budget=10.000000 > scip_budget=14.000000)
c findModelForBestValue: SAT time limit: 100000000000000000000.000000 sec
```

2つのバグが存在する:

1. **ログメッセージが誤り**: `findModelForBestValueBySAT` は呼ばれた経緯に関わらず常に
   `"trying SAT first (sat_budget > scip_budget)"` を出力する。上のログでは `num_improved_by_scip == 0`
   のため `else` ブランチ（行2784）から `1e20` を渡して呼ばれているが、メッセージには
   「SAT first」「sat_budget > scip_budget」と書かれており、実際の値（10 > 14）と矛盾する。

2. **`remaining` 計算が誤り**: `given_time <= 0`（無制限）の場合 `remaining = elapsed`
   になる。SAT-first ブランチで `sat_time_limit = elapsed * ratio` が極小になるバグがある。

## 修正方針

### 修正1: ログメッセージを呼び出し側へ移動

**対象ファイル**: `PbSolver.C`

- `findModelForBestValueBySAT`（行2741-2742）から "trying SAT first..." ログを **削除**
- `findModelForBestValueBySCIP`（行2716-2717）から "trying SCIP first..." ログを **削除**
- `findModelForBestValue` の各分岐前に適切なログを追加:
  - SCIP-first ブランチ（行2776前）: `"trying SCIP first (scip_budget=%f >= sat_budget=%f)"`
  - SAT-first ブランチ（行2781前）: `"trying SAT first (sat_budget=%f > scip_budget=%f)"`
  - else ブランチ（行2785前）: `"trying SAT (no cooperative SCIP, budget=unlimited)"`

### 修正2: `remaining` の無制限時の計算を修正

**対象**: `PbSolver.C` 行2768

変更前:
```cpp
double remaining = config->given_time <= 0 ? elapsed : std::max(100.0, config->given_time - elapsed);
```

変更後:
```cpp
double remaining = config->given_time <= 0 ? 1e20 : std::max(100.0, config->given_time - elapsed);
```

`given_time <= 0` のとき残り時間を実質無制限（1e20）にする。

## 修正対象ファイル

- `PbSolver.C`: 行2716-2717, 行2741-2742, 行2768, 行2775-2785

## 検証

```bash
make d
# ログに以下が出ることを確認:
# - else ブランチ時: "trying SAT (no cooperative SCIP, budget=unlimited)" と表示
# - SAT-first ブランチ時: "trying SAT first (sat_budget=X > scip_budget=Y)" は Y < X のときのみ表示
# - "1e20" は表示されない（else ブランチのログが変わるため）
```

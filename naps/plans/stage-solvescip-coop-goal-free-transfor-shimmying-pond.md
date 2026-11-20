# Plan: ステージ境界での真の確定変数をSCIP→SAT伝播

## 背景・問題

マルチステージ最適化において、stage1 の `solveScip` 内（旧 L621-655）で SCIP の
グローバル境界を SAT の永続ユニットとして伝播していた。これには B&B ベースの
目的関数依存の境界が含まれ、stage2 の `evalGoalUB` が過剰に絞り込まれる誤動作が
発生した（現在 `false &&` で無効化済み）。

**真の目的**: stage1 の最適値 V1 が確定した時点で、
「`goals[0] = V1` という等式制約から制約伝播によって論理的に固定される変数のみ」
を SAT ソルバーへ送付する。

**等式制約 `lb = ub = V1` について**:
後続 stage の実行可能解はすべて `goals[0] = V1` を満たすため（V1 が真の最大値であり、
stage 2 以降の制約 `goals[0] >= V1` と合わせて等式が成立）、等式制約を SCIP に永続的に
残しても LP 緩和に矛盾は生じない。むしろ正確に探索空間を絞り込むため有益である。
したがって **ub 制約を presolve 後に削除する必要はない**（実装がシンプルになる）。

---

## 変更ファイルと内容

### 1. [Config.h](Config.h) — オプション追加

SCIP 関連フィールド群（L110付近）に追加：

```cpp
bool     scip_stage_propagate = false;      // ステージ境界でSCIPpresolveを用いた確定変数伝播を有効化
int      scip_stage_propagate_rounds = 3;   // presolve の最大ラウンド数（-1: 無制限）
```

### 2. [Main.C](Main.C) — CLIオプション追加

`-cooperative-scip` 関連オプション群（L241付近）に追加：

```cpp
else if (oneof(arg, "scip-stage-prop")) config->scip_stage_propagate = true;
else if (strncmp(arg, "-scip-stage-prop-rounds=", 24) == 0)
    config->scip_stage_propagate_rounds = atoi(arg+24);
```

CLIフラグ: `-scip-stage-prop`（有効化）、`-scip-stage-prop-rounds=N`（ラウンド数指定）

### 3. [PbSolver.C](PbSolver.C:2769) — 等式制約として渡す

ステージ境界の `addScipLinearConstraint` 呼び出し（L2769）を等式（lb=ub=V1）に変更：

```cpp
// 変更前
addScipLinearConstraint(goal_ps, goal_Cs, LB_goalvalue);
// 変更後（等式 goals[0]=V1 は後続 stage でも常に有効な制約であり永続させる）
addScipLinearConstraint(goal_ps, goal_Cs, LB_goalvalue, LB_goalvalue);
```

### 4. [PbSolver_scip.C](PbSolver_scip.C) — `addScipLinearConstraint` に伝播ロジック追加

`SCIPaddCons` / `SCIPreleaseCons`（現 L337-338）の直後に以下を挿入：

```cpp
    SCIPaddCons(scip, cons);
    SCIPreleaseCons(scip, &cons);

    // ステージ境界での確定変数伝播（-scip-stage-prop オプション）
    // goals[N]=V_N の等式制約を SCIPpresolve で伝播させ、
    // 制約伝播により論理的に固定される変数のみを SAT へ送付する。
    // solveScip L621 の B&B ベース伝播（false && で無効化済み）とは異なり健全。
    // 等式制約は後続 stage の LP 緩和においても常に有効（CLAUDE.md 参照）。
    if (config->use_cooperative_scip && config->scip_stage_propagate) {
        // presolve ラウンド数を制限して高速化
        int saved_rounds;
        SCIPgetIntParam(scip, "presolving/maxrounds", &saved_rounds);
        SCIPsetIntParam(scip, "presolving/maxrounds", config->scip_stage_propagate_rounds);

        SCIP_RETCODE presolve_rc = SCIPpresolve(scip);

        int newly_fixed = 0;
        if (presolve_rc == SCIP_OKAY && SCIPgetStage(scip) == SCIP_STAGE_PRESOLVED) {
            for (int i = 0; i < pb_n_vars; i++) {
                if (value(Var(i)) == ll_Undef) {
                    double glb = SCIPvarGetLbGlobal((*vars)[i]);
                    double gub = SCIPvarGetUbGlobal((*vars)[i]);
                    bool is_fixed = false, fixed_val = false;
                    if      (glb >= 0.5) { is_fixed = true; fixed_val = true; }
                    else if (gub <= 0.5) { is_fixed = true; fixed_val = false; }
                    if (is_fixed) {
                        if (!sat_solver->addUnit(Lit(i, fixed_val ? false : true))) break;
                        newly_fixed++;
                    }
                }
            }
        } else if (presolve_rc == SCIP_OKAY &&
                   SCIPgetStatus(scip) == SCIP_STATUS_INFEASIBLE) {
            // 制約から矛盾 → 問題は UNSAT（起こりえないはずだが安全のため処理）
            sat_solver->addUnit(Lit(0)); sat_solver->addUnit(~Lit(0));
        }

        if (newly_fixed > 0 && config->verbosity >= 1)
            report->reportf(1, "c SCIP stage-boundary propagated %d fixed variables\n",
                            newly_fixed);

        // PRESOLVED → PROBLEM へ戻す（次の solveScip の initScipForCoop が再初期化する）
        // 等式制約（lb=ub=V_N）は PROBLEM 段階に追加済みなので SCIPfreeTransform 後も保持される。
        if (SCIPfreeTransform(scip) != SCIP_OKAY)
            throw std::runtime_error("SCIPfreeTransform failed after stage-boundary presolve");
        scip_in_transformed_state = false;
        scip_last_lb_hint_scip = 0.0;

        SCIPsetIntParam(scip, "presolving/maxrounds", saved_rounds);
    }
```

---

## 変更点のまとめ

| ファイル | 変更内容 |
|---|---|
| Config.h | `scip_stage_propagate`（bool, デフォルト false）と `scip_stage_propagate_rounds`（int, デフォルト 3）を追加 |
| Main.C | `-scip-stage-prop` と `-scip-stage-prop-rounds=N` CLIオプションを追加 |
| PbSolver.C:2769 | `addScipLinearConstraint(goal_ps, goal_Cs, LB_goalvalue, LB_goalvalue)` で等式化（ub 削除不要） |
| PbSolver_scip.C | `addScipLinearConstraint` の末尾に `SCIPpresolve` + 変数抽出 + SAT伝播ロジックを追加 |
| .claude/CLAUDE.md | マルチステージ最適化の等式制約の永続性について記述追加 |

---

## デフォルト動作と `false &&` との関係

| 設定 | 動作 |
|---|---|
| `false &&`（L621）のまま、`-scip-stage-prop` なし | B&B ベース伝播なし、新伝播なし（現状） |
| `false &&` のまま、`-scip-stage-prop` あり | **新機能**: ステージ境界で制約伝播ベースの正確な固定変数のみを SAT へ送付 |

`solveScip` L621 の `false &&` は引き続き維持する。

---

## 検証方法

1. `make d` でデバッグビルド
2. `-cooperative-scip` のみ（`-scip-stage-prop` なし）: 現状維持、stage2 が正しく探索することを確認
3. `-cooperative-scip -scip-stage-prop` あり:
   - "SCIP stage-boundary propagated N fixed variables" が出力されること
   - stage2 の UB が適切な値（Int_MAX より絞られているが正確）であること
   - 最終結果が正解であること
4. 既存テスト: `cd build_debug && ctest -V`

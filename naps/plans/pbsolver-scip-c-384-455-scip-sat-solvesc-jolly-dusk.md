# 拡張プラン: syncSatFixedVars — SAT ソルブ直後の globally_fixed_var 更新と SCIP 即時通知

## Context（前プランとの関係）

前プランで実装した `globally_fixed_var` は、現在 `initScipForCoop`（`solveScip` の先頭で呼ばれる）内でのみ更新される。

```
sat_solver_invoke  →  SAT 結果処理  →  solveScip → initScipForCoop（ここで更新）
                       ^^^^^^^^^^^^^^^^
                       この区間、globally_fixed_var が SAT の最新 trail を反映していない
```

この遅延は現状では問題にならないが、以下の改善を行う：

1. **SCIP への即時通知**: SAT ソルブ直後に `SCIPchgVarLbGlobal/UbGlobal` を呼ぶことで、次の `SCIPsolve` が最新の変数固定を活用できる（SOLVING ステージでも有効、PbSolver_scip.C:238 のコメントより確認済み）
2. **globally_fixed_var の権威性向上**: 配列が常に「現時点で既知の全確定変数」を正確に反映する状態になる
3. **SAT 確定変数のカウント統計**: SAT ソルブで新たに確定した変数数を statistics に表示する

---

## 設計

### 新メソッド `PbSolver::syncSatFixedVars()`

`initScipForCoop` の trail sync ループを独立メソッドとして抽出し、新規確定変数のカウントを追加する。

**処理内容**:
- `scip_env == nullptr` なら即リターン（SCIP 未初期化時のガード）
- `sat_solver->trail_size()` から `scip_trail_synced` 以降の新エントリを走査
- 各エントリについて、**`globally_fixed_var[v] == ll_Undef` の場合のみ**代入・カウント（SCIP 側で既に確定済みの変数は上書きしない）
- SCIP が初期化済みなら `SCIPchgVarLbGlobal` / `SCIPchgVarUbGlobal` を呼ぶ
- 新規確定変数数を `config->stats_sat_fixed_vars` に加算
- `scip_trail_synced` を更新

### 新統計カウンタ `stats_sat_fixed_vars`

`stats_scip_fixed_vars` と対称的に SAT 側の確定変数数を追跡する。

---

## 変更ファイルと箇所

### 1. [Config.h:163付近](Config.h#L163) — 統計フィールド追加

`stats_scip_fixed_vars` の直後に追加:

```cpp
int      stats_scip_fixed_vars = 0;            // SCIP global fixed variables
int      stats_sat_fixed_vars  = 0;            // SAT trail fixed variables (newly learned)
int      stats_scip_free_transform_count = 0;  // number of SCIPfreeTransform calls
```

### 2. [PbSolver.C](PbSolver.C) — 統計出力追加

`stats_scip_fixed_vars` を表示している箇所（cooperative SCIP 統計ブロック）の直後に追加:

```cpp
report->reportf("SCIP globally fixed variables       : %d\n", config->stats_scip_fixed_vars);
report->reportf("SAT  globally fixed variables       : %d\n", config->stats_sat_fixed_vars);  // ← 追加
```

### 3. [PbSolver.h](PbSolver.h) — メソッド宣言追加

既存の SCIP 関連メソッド宣言（`freeScip`, `initScipEnv`, `initScipForCoop` 等）付近に追加:

```cpp
void    syncSatFixedVars();   // sync SAT trail to globally_fixed_var and SCIP bounds
```

### 4. [PbSolver_scip.C:~321](PbSolver_scip.C#L321) — syncSatFixedVars 実装

`initScipForCoop` の直前に新関数として追加:

```cpp
void PbSolver::syncSatFixedVars() {
    if (!scip_env) return;
    globally_fixed_var.growTo(pb_n_vars, ll_Undef);
    SCIP* scip = (SCIP*)scip_env;
    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;

    int newly_fixed = 0;
    int trail_end = sat_solver->trail_size();
    for (int i = scip_trail_synced; i < trail_end; i++) {
        Lit l = sat_solver->trail(i);
        int v = var(l);
        if (v >= pb_n_vars) continue;   // auxiliary variable — no SCIP counterpart
        if (globally_fixed_var[v] == ll_Undef) {  // SCIPから既に確定済みの変数は上書きしない
            globally_fixed_var[v] = sign(l) ? ll_False : ll_True;
            SCIP_VAR* sv = (*vars)[v];
            if (sign(l)) {
                SCIPchgVarUbGlobal(scip, sv, 0.0);
            } else {
                SCIPchgVarLbGlobal(scip, sv, 1.0);
            }
            newly_fixed++;
        }
    }
    scip_trail_synced = trail_end;

    config->stats_sat_fixed_vars += newly_fixed;
    if (newly_fixed > 0 && config->verbosity >= 1)
        report->reportf(1, "c SAT fixed %d new variables\n", newly_fixed);
}
```

**注意**: `SCIPchgVarLbGlobal/UbGlobal` は `globally_fixed_var` の更新有無に関わらず呼ぶ  
（SCIP がその変数の bound をまだ受け取っていない可能性があるため）。

### 5. [PbSolver.C:1232](PbSolver.C#L1232) — sat_solver_invoke からの呼び出し

`report->reportf(2, "sat_solver_result: ...")` の後、`clearTimeout` の前に追加:

```cpp
report->reportf(2, "sat_solver_result: %s\n", solverStatus_strs[result]);
syncSatFixedVars();   // ← 追加: update globally_fixed_var and notify SCIP
if (time_limit > 0) sat_solver->clearTimeout();
return result;
```

---

## 設計上の注意点

**globally_fixed_var の ll_Undef チェック**  
SCIP 側で既に確定済み（`globally_fixed_var[v] != ll_Undef`）の変数は、SAT trail 上でも同じ値で確定されるはず。カウントの重複を避けるため、`ll_Undef` の場合のみ更新・カウントする。  
一方、`SCIPchgVarLbGlobal` は条件なしで呼ぶ（SCIP 内部で冪等に処理される）。

**Kissat は trail() 未対応**  
`Solver_Kissat::trail_size()` は 0 を返すため、trail sync は機能しない（現状と同じ）。  
最適化の cooperative SCIP では CaDiCaL が使われるため実用上問題なし。

**SCIP 未初期化時の安全性**  
`scip_env == nullptr` のガードにより、SCIP を使わないモードでは完全に no-op。

**initScipForCoop の trail ループは安全網として残す**  
`scip_trail_synced` が既に最新なら for ループは 0 回実行される。削除はしない。

---

## 変更ファイルまとめ

| ファイル | 変更箇所 | 内容 |
|---|---|---|
| [Config.h:163付近](Config.h#L163) | 統計フィールド | `stats_sat_fixed_vars` 追加 |
| [PbSolver.C](PbSolver.C) | 統計出力ブロック | `stats_sat_fixed_vars` 表示追加 |
| [PbSolver.h](PbSolver.h) | SCIP メソッド宣言付近 | `void syncSatFixedVars()` 宣言追加 |
| [PbSolver_scip.C:~321](PbSolver_scip.C#L321) | initScipForCoop の直前 | `syncSatFixedVars()` 実装追加 |
| [PbSolver.C:1232](PbSolver.C#L1232) | sat_solver_invoke 末尾 | `syncSatFixedVars()` 呼び出し追加 |

計 5 箇所、新規ファイルなし。

---

## 検証方法

```bash
make d

# 統計に "SAT globally fixed variables" が表示されることを確認
./naps_debug "benchmark/PB10/.../normalized-monodevelop-nunit.cudf.trendy.opb" \
  -cSCIP -scip-stage-prop -scip-solve-prop -v1 2>&1 \
  | grep -E "fixed variables|SAT fixed"

# initScipForCoop の trail ループが no-op になることを確認（-v2 で trail メッセージなし）
./naps_debug "..." -cSCIP -v2 2>&1 | grep "syncing trail"

# テストスイート
cd build_debug && ctest -V
```

# Plan: sat_solver_invoke にタイムアウトを追加し sst_unknown 処理を整備

## 学習節の継続性について

タイムアウト後に再び `sat_solver_invoke` を呼び出しても **学習節は破棄されない**。  
CaDiCaL は `External::solve()` 終了時に `reset_limits()`（時間・コンフリクト制限のリセット）のみを呼ぶため、redundant clause データベースは次の `solve()` 呼び出しでも引き継がれる。Kissat の `kissat_set_terminate` も同様の動作をする。  
つまり SAT が短い予算で打ち切られても、SCIP 協調呼び出しを挟んで再開するたびに蓄積された学習節が活きる。

---

## Context

SCIP 協調モード（`use_cooperative_scip`）では `maximizeGoal()` ループ内で SAT ソルバと SCIP を交互に呼び出す。  
しかし現在 `sat_solver_invoke` はタイムアウト引数を持たず、SAT ソルバが長時間解けない問題に対して無制限に走り続けるため、SCIP への制御移行が遅れる。  
`solveScip` と同様に時間予算制限を SAT 側にも持たせ、タイムアウト時に `sst_unknown` を返してから SCIP 協調呼び出しへ進む設計にする。  
ユーザー回答: SAT 予算は SCIP と同様に動的スケーリング（factor 乗算）、タイムアウト時は `consec_UNSAT++` して minimizationMode 選択に影響させる。

---

## 変更ファイル一覧

| ファイル | 変更種別 |
|---|---|
| `Solver.h` | タイムアウト IF 追加（`timed_out` フラグ・仮想メソッド） |
| `Solver.C` | CaDiCaL・Kissat に Terminator/callback ベースの実装 |
| `PbSolver.h` | `sat_solver_invoke` シグネチャ拡張、`sat_current_time_budget` フィールド |
| `Config.h` | `sat_time_budget`・`sat_time_budget_factor` 設定追加 |
| `Main.C` | CLI オプション `-sat-budget`・`-sat-budget-factor` 追加 |
| `PbSolver.C` | `sat_solver_invoke` 実装・呼び出し箇所・ループ内処理変更 |

---

## Step 1: Solver にタイムアウトインターフェースを追加

### Solver.h の変更

`Solver` ベースクラス（行 ~124 付近）に追加:

```cpp
bool timed_out = false;  // set true when solve_ is stopped by timeout
virtual void setTimeout(double seconds) {}
virtual void clearTimeout() { timed_out = false; }
```

`Solver_CaDiCaL` クラス定義内に以下を追加:
- `struct CaDiCaLTimeoutTerminator : public CaDiCaL::Terminator` をネスト定義
  - フィールド: `double deadline;` + `bool& timed_out_ref;`
  - `bool terminate() override { if (cpuTime() >= deadline) { timed_out_ref = true; return true; } return false; }`
- `CaDiCaLTimeoutTerminator* timeout_term = nullptr;` フィールド
- `void setTimeout(double seconds) override;`
- `void clearTimeout() override;`

`Solver_Kissat` クラス定義内に以下を追加:
- `struct KissatTermState { double deadline; bool* timed_out; };`
- `KissatTermState kissat_term_state;`
- `void setTimeout(double seconds) override;`
- `void clearTimeout() override;`

### Solver.C の変更

**Solver_CaDiCaL::setTimeout / clearTimeout**（行 656 付近の `interrupt()` の直後に追加）:

```cpp
void Solver_CaDiCaL::setTimeout(double seconds) {
    timed_out = false;
    timeout_term = new CaDiCaLTimeoutTerminator(cpuTime() + seconds, timed_out);
    cadical->connect_terminator(timeout_term);
}
void Solver_CaDiCaL::clearTimeout() {
    if (timeout_term) {
        cadical->disconnect_terminator();
        delete timeout_term;
        timeout_term = nullptr;
    }
    Solver::clearTimeout();
}
```

**Solver_Kissat::setTimeout / clearTimeout**（行 772 付近の `interrupt()` の直後に追加）:

```cpp
void Solver_Kissat::setTimeout(double seconds) {
    timed_out = false;
    kissat_term_state = {cpuTime() + seconds, &timed_out};
    kissat_set_terminate(kissat_solver, &kissat_term_state,
        [](void* s) -> int {
            auto* ts = static_cast<KissatTermState*>(s);
            if (cpuTime() >= ts->deadline) { *ts->timed_out = true; return 1; }
            return 0;
        });
}
void Solver_Kissat::clearTimeout() {
    kissat_set_terminate(kissat_solver, nullptr, nullptr);
    Solver::clearTimeout();
}
```

---

## Step 2: Config.h に設定項目追加

`Config.h`（`scip_time_budget` / `scip_time_budget_factor` の近く、行 104-105）に追加:

```cpp
double sat_time_budget = -1.0;        // SAT time limit per call in coop SCIP mode (-1: no limit)
double sat_time_budget_factor = 1.4;  // multiplier applied to sat_time_budget after each call
```

---

## Step 3: Main.C に CLI オプション追加

`-scip-budget` / `-scip-budget-factor` の近くに追加:

```
-sat-budget <secs>          SAT time budget per call in cooperative SCIP mode (default: -1 = no limit)
-sat-budget-factor <f>      Factor to scale SAT time budget after each call (default: 1.4)
```

---

## Step 4: PbSolver.h の変更

`PbSolver_Minimize` クラス内の `scip_current_time_budget` 宣言（行 222）の直下に追加:

```cpp
double sat_current_time_budget;  // tracks per-call budget; initialized from sat_time_budget, scaled by sat_time_budget_factor
```

`sat_solver_invoke` のシグネチャ変更（行 277-281）:

```cpp
// 変更前
solverStatus sat_solver_invoke(vec<Lit>& assump_ps, vec<Lit>& failed_assumps);
solverStatus sat_solver_invoke(vec<Lit>& assump_ps) {
    vec<Lit> failed_assumps;
    return sat_solver_invoke(assump_ps, failed_assumps);
}

// 変更後
solverStatus sat_solver_invoke(vec<Lit>& assump_ps, vec<Lit>& failed_assumps, double time_limit = -1.0);
solverStatus sat_solver_invoke(vec<Lit>& assump_ps, double time_limit = -1.0) {
    vec<Lit> failed_assumps;
    return sat_solver_invoke(assump_ps, failed_assumps, time_limit);
}
```

---

## Step 5: PbSolver.C の変更

### 5-1. sat_solver_invoke 実装（行 1145-1173）

`time_limit` パラメータを追加し、タイムアウト設定/解除を行う:

```cpp
solverStatus PbSolver::sat_solver_invoke(vec<Lit>& assump_ps, vec<Lit>& failed_assumps, double time_limit)
{
    if (time_limit > 0) sat_solver->setTimeout(time_limit);
    solverStatus result = sst_unknown;
    if (sat_solver->solve(assump_ps, failed_assumps)) {
        // ... 既存の SAT 処理（変更なし）
    } else {
        if (sat_solver->timed_out || interrupted) {
            // timed_out: 時間予算超過、interrupted: SIGINT 等
            result = sst_unknown;
        } else {
            config->stats_unsat_calls++;
            result = sst_unsat;
        }
    }
    if (time_limit > 0) sat_solver->clearTimeout();
    return result;
}
```

### 5-2. sat_current_time_budget の初期化（行 2534 付近）

`scip_current_time_budget = config->scip_time_budget;` の直後に追加:

```cpp
sat_current_time_budget = config->sat_time_budget;
```

### 5-3. maximizeGoal() の呼び出し箇所（行 1955・1966）

```cpp
// 行 1955
sat_solver_result = sat_solver_invoke(unified_assump_ps, failed_assumptions, sat_current_time_budget);

// 行 1966
sat_solver_result = sat_solver_invoke(assump_ps, sat_current_time_budget);
```

呼び出し後（行 1967〜1970 付近）に予算更新を追加:

```cpp
sat_current_time_budget *= config->sat_time_budget_factor;
```

### 5-4. sst_unknown 処理の追加（行 2073 付近）

現在の `else { // unsat returned` を以下に変更:

```cpp
} else if (sat_solver_result == sst_unknown) {
    // SAT ソルバがタイムアウト
    report->reportf(2, "SAT solver timed out (budget=%.1fs); proceeding to cooperative SCIP call\n",
                    sat_current_time_budget / config->sat_time_budget_factor);
    consec_UNSAT++;  // minimizationMode 選択に影響させる
    // Sequential/Binary モードでは仮定リテラルを pop する
    // （Hitting・First_solve は assump_ps を単一の threshold 仮定としては使わない）
    // exec_loop は true のまま → SCIP 協調呼び出しへ進む
    // 境界値 (LB/UB) は更新しない（UNSAT が証明されていないため）
    if ((minimizationMode != cmm_Hitting) && (minimizationMode != cmm_First_solve) && assump_ps.size() > 0)
        assump_ps.pop();
} else {  // sst_unsat
    // ... 既存の UNSAT 処理（変更なし）
```

---

## 検証

### ユニットテスト
```bash
make d && cd build_debug && ctest -V
```

### 手動検証（協調 SCIP モード）
```bash
# sat-budget を短く設定してタイムアウトを意図的に発生させる
./naps_debug problem.opb --cooperative-scip --sat-budget 0.1 --sat-budget-factor 1.4 -v2
```

期待ログ:
- `"SAT solver timed out"` メッセージが出力される
- その後 `"Calling solveScip for cooperative mode"` が実行される
- 解が見つかった場合は `pb_solver_status: sst_sat` と表示される

### 非協調モードへの非影響確認
```bash
# -sat-budget なし（デフォルト -1.0）で挙動が変わらないことを確認
./naps_debug problem.opb -v2
```

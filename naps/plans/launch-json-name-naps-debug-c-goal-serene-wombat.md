# プラン: naps_debug タイムアウト & ^C 応答性修正

## Context

`-cSCIP` モードで `naps_debug` を実行すると、2つの問題が起きる：
1. **タイムアウト超過** — `invoking solver with time budget: 5.000000` の後、10秒以上 CaDiCaL から戻らない
2. **^C が効かない** — プロセスが簡単には終了しない

## 根本原因

### 問題1: terminateint=10 がデバッグビルドで粗すぎる

CaDiCaL のタイムアウト機構は **polling** ベース。

```
cadical3/src/internal.hpp: terminated_asynchronously()
  → lim.terminate.check-- が 0 になった時だけ terminator->terminate() を呼ぶ
  → デフォルト: lim.terminate.check = factor * terminateint = 1 * 10 = 10
```

デバッグビルドは最適化なし（-O0）のため、1 decision あたりの処理が非常に重い。
`terminated_asynchronously` が decision ループで1回呼ばれると仮定すると:
- 1 decision ≈ 1秒（デバッグビルドの重い問題）
- terminateint=10 → 10 decisions 後にチェック → **約10秒後に初めてチェック**
- budget=5秒でも最初のチェックが10秒後になり、タイムアウトが超過する

`Solver.C:674-686` の `setTimeout`:
```cpp
void Solver_CaDiCaL::setTimeout(double seconds) {
  timed_out = false;
  timeout_term = new CaDiCaLTimeoutTerminator(cpuTime() + seconds, timed_out);
  cadical->connect_terminator(timeout_term);  // terminateint は変更しない ← 問題
}
```

### 問題2: SCIP版 interruptSolve が CaDiCaL を interrupt しない

`PbSolver_scip.C:116-122` (USE_SCIP版):
```cpp
void PbSolver::interruptSolve() {
    interrupted = true;
    if (scip_env) {
        SCIPinterruptSolve((SCIP*)scip_env);
        // sat_solver->interrupt() が呼ばれない ← バグ
    }
}
```

^C → `interruptSolve()` → SCIP には伝わるが **CaDiCaL には伝わらない**。
CaDiCaL が実行中の場合、タイムアウトが切れるまで戻らない。

## 修正内容

### Fix 1: `Solver.C` — setTimeout 時に terminateint を 1 に設定

**ファイル**: [Solver.C](Solver.C) (行 674-686)

```cpp
// 変更前
void Solver_CaDiCaL::setTimeout(double seconds) {
  timed_out = false;
  timeout_term = new CaDiCaLTimeoutTerminator(cpuTime() + seconds, timed_out);
  cadical->connect_terminator(timeout_term);
}

// 変更後
void Solver_CaDiCaL::setTimeout(double seconds) {
  timed_out = false;
  cadical->set("terminateint", 1);  // 毎 terminated_asynchronously 呼び出しでチェック
  timeout_term = new CaDiCaLTimeoutTerminator(cpuTime() + seconds, timed_out);
  cadical->connect_terminator(timeout_term);
}
```

`clearTimeout` 後は terminator が disconnect されるので、`terminateint=1` のままでも
terminator の `terminate()` は呼ばれない（terminator が null）。パフォーマンスへの影響は最小。

### Fix 2: `PbSolver_scip.C` — interruptSolve で sat_solver も interrupt する

**ファイル**: [PbSolver_scip.C](PbSolver_scip.C) (行 116-122)

```cpp
// 変更前
void PbSolver::interruptSolve() {
    report->reportf(2, "interruptSolve\n");
    interrupted = true;
    if (scip_env) {
        SCIPinterruptSolve((SCIP*)scip_env);
    }
}

// 変更後
void PbSolver::interruptSolve() {
    report->reportf(2, "interruptSolve\n");
    interrupted = true;
    if (sat_solver) sat_solver->interrupt();   // CaDiCaL にも即座に伝える
    if (scip_env) {
        SCIPinterruptSolve((SCIP*)scip_env);
    }
}
```

## 変更ファイル

| ファイル | 行 | 内容 |
|---|---|---|
| [Solver.C](Solver.C) | 674-686 | `cadical->set("terminateint", 1)` を setTimeout 内に追加 |
| [PbSolver_scip.C](PbSolver_scip.C) | 116-122 | `sat_solver->interrupt()` を interruptSolve に追加 |

## 検証

1. `make d` でデバッグビルド
2. VSCode の `naps_debug` デバッグ設定を実行
3. `c invoking solver with time budget: 5.000000` の後、おおよそ 5-6 秒で次の出力が来ることを確認
4. 実行中に Ctrl+C を押し、すみやかにプロセスが終了することを確認

# 改善計画: SIGTERM 中断時の double free 修正

## 根本原因

`SIGTERM_handler` 内で `free(dummy_heap)` および `outputResult()` (内部で `toString()` → `malloc`/`free` を呼ぶ) が実行される。  
これらは `SCIPsolve()` が `malloc`/`free` の途中でシグナルに割り込まれた場合に glibc のヒープ破壊を引き起こす（async-signal-unsafe）。

## 解決方針

SCIP の `SCIPinterruptSolve()` を使って SCIP を安全に中断させ、`malloc`/`free` を含む処理を**シグナルハンドラ外**（通常のコードパス）で行う。

- `SCIP_STATUS_USERINTERRUPT (10)` / `SCIP_STATUS_TERMINATE (11)` が返ったとき、最終解が `best_model` に格納済みであれば `sst_sat` で返す（既存の `else if (final_sol != nullptr)` 分岐で処理可能）
- 非 SCIP ソルバーの場合は既存の動作を変えない

## 変更ファイルと変更内容

### [MODIFY] `PbSolver.h`

`PbSolver` クラスに virtual メソッドを追加:
```cpp
virtual void interruptSolve() {}
```

### [MODIFY] `PbSolver_scip.C`

`PbSolver::interruptSolve` をオーバーライドして追加:
```cpp
void PbSolver::interruptSolve() {
    if (scip_env) {
        SCIPinterruptSolve((SCIP*)scip_env);
    }
}
```

また `SCIPsolve` 後のステータス判定に `SCIP_STATUS_USERINTERRUPT` / `SCIP_STATUS_TERMINATE` の明示的なケースを追加して DEBUG 出力を抑制:
```cpp
} else if (status == SCIP_STATUS_USERINTERRUPT || status == SCIP_STATUS_TERMINATE) {
    // シグナルによる中断 (final_sol があれば sst_sat を既存分岐が処理)
    res = sst_unknown;
}
```
※ `final_sol != nullptr` の分岐が先に来るので、解があれば `sst_sat`、なければ `sst_unknown` に自然に処理される。既存コードを少し整理するだけ。

### [MODIFY] `Main.C`

1. グローバルフラグ追加（`dummy_heap` の直下）:
```cpp
static volatile sig_atomic_t g_interrupted_signum = 0;
```

2. `SIGTERM_handler` を修正:
```cpp
static void SIGTERM_handler(int signum) {
    if (config->use_scip) {
        // SCIPsolve 中断: malloc/free を含む処理はシグナルハンドラ外で行う
        g_interrupted_signum = signum;
        if (pb_solver) pb_solver->interruptSolve();
        return;
    }
    // 非 SCIP: 既存の動作を維持
    free(dummy_heap);
    main_report.reportf("\n");
    if(signum == SIGUSR1) {
        pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, true);
    } else {
        main_report.reportf("*** TERMINATED *** by signal %d\n", signum);
        pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, false);
    }
    _exit(pb_solver->get_best_goalvalue() == Int_MAX ? 0 : 10);
}
```

3. `main()` 内、`pb_solver->solve(assump_ps)` の直後に追加:
```cpp
if (g_interrupted_signum) {
    main_report.reportf("\n");
    main_report.reportf("*** TERMINATED *** by signal %d\n", (int)g_interrupted_signum);
    pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, false);
    _exit(pb_solver->get_best_goalvalue() == Int_MAX ? 0 : 10);
}
```

## 検証計画

```bash
# ビルド
make d

# タイムアウト中断で double free が出ないことを確認
timeout 0.1s ./naps_debug -SCIP Examples/garden9x9.opb
# 期待: o <値> + s SATISFIABLE が表示されコアダンプなし

# 正常完了（変化なし）
timeout 1s ./naps_debug -SCIP Examples/garden9x9.opb
# 期待: o ... + s OPTIMUM FOUND

# テスト全通
cd build_debug && ctest -V
```

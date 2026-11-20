# プラン: -SCIPモード ^C 高速終了

## Context

大きな問題を `-SCIP` で解く際、^C を押しても1分以上停止しない問題がある。
原因：ファイルパース中（`scip_env` が null の段階）に ^C を受けると、
`SIGTERM_handler` が `interruptSolve()` を呼んで return するだけで、
SCIPsolve に割り込む手段がなく、パース完了まで延々と処理が続く。

**壊してはいけない既存動作：**
SCIPsolve が実行中（`scip_env` 非 null）に ^C を受けた場合は
`SCIPinterruptSolve()` → main() での `outputResult()` → `_exit()` の流れを維持する。

## 根本原因

```
SIGTERM_handler:
  if (config->use_scip) {      // ← パース中でも true
    g_interrupted_signum = ...
    pb_solver->interruptSolve() // scip_env==null なので何もしない
    return;                    // ← パース処理が延々と続く
  }
```

## 修正箇所

**ファイル: `Main.C`**

`SIGTERM_handler` の `use_scip` ブランチを次のように変更する：

### Before
```cpp
static void SIGTERM_handler(int signum) {
  if (config->use_scip) {
    g_interrupted_signum = signum;
    if (pb_solver) pb_solver->interruptSolve();
    return;
  }
  // ... inner_with_scip branch ...
  // ... immediate exit (Branch C) ...
  free(dummy_heap);
  ...
  _exit(...);
}
```

### After
```cpp
static void SIGTERM_handler(int signum) {
  if (config->use_scip) {
    if (pb_solver && pb_solver->scip_env) {
      // SCIPsolve 実行中 → graceful interrupt（既存動作を維持）
      g_interrupted_signum = signum;
      pb_solver->interruptSolve();
      return;
    }
    // scip_env == null: パース中 or coding 中 → 即時終了へ fall-through
  }
  // ... inner_with_scip branch（変更なし）...
  // Branch C: 即時終了（use_scip かつ未開始の場合もここに到達）
  free(dummy_heap);
  main_report.reportf("\n");
  if (signum == SIGUSR1) {
    pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, true);
  } else {
    main_report.reportf("*** TERMINATED *** by signal %d\n", signum);
    pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, false);
  }
  _exit(pb_solver->get_best_goalvalue() == Int_MAX ? 0 : 10);
}
```

## 各シナリオの動作確認

| タイミング | `scip_env` | 修正後の動作 | 既存動作との差異 |
|---|---|---|---|
| パース中に ^C | null | Branch C で即時終了 | **改善**（即時）|
| coding() 中に ^C | null | Branch C で即時終了 | **改善**（即時）|
| solveScip 初期化中（制約追加前後）に ^C | 非 null | SCIPinterruptSolve → main() で outputResult | 変更なし |
| SCIPsolve 実行中・解発見後に ^C | 非 null | SCIPinterruptSolve → main() で outputResult | **変更なし（要件）** |

## 安全性

- `scip_env` の読み取りはシグナルハンドラ内で行うが、`void*` の代入は x86_64 でアトミック。
  最悪ケース（代入と同時）でも stale な null を読む → 即時終了（安全側に倒れる）。
- `scip_env` は public フィールド（`PbSolver.h:212`）なのでハンドラから直接アクセス可能。
- `dummy_heap` の free は非 SCIP パスでも行われているパターンと同じ。

## 変更規模

- **変更箇所**: `Main.C` の `SIGTERM_handler` のみ（5行程度の変更）
- `PbSolver_scip.C` / `PbSolver.h` への変更は不要

## 検証方法

1. 大きな OPB ファイルでパース中（数秒以内）に ^C → 即時終了することを確認
2. `-SCIP` で解を1つ以上発見（`o` 行出力）した後に ^C → `outputResult` が呼ばれ `s` 行が出力されることを確認
3. 通常終了（最適解発見）は影響なし → 既存テスト (`ctest`) が通ることを確認

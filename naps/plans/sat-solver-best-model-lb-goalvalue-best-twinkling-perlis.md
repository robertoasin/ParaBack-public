# sat_solver_invoke から best_model コピーを外に出す（下準備リファクタ）

## Context

最終的なゴールは、`PbSolver_Minimize::maximizeGoal`（PbSolver.C:1992）で SAT が返ったときに、
無条件で `best_model`/`LB_goalvalue`/`best_goalvalue` を上書きするのではなく、
`*goalExp` の値が直前の `LB_goalvalue` より改善している場合のみ採用するように変更すること
（`cmm_sc2` ではSAT呼び出し前に課される制約が層別化された部分制約のみであり、
`goalExp > 直前のLB_goalvalue` が保証されないため）。

その前段階として、まず `sat_solver_invoke`（PbSolver.C:1221）が **無条件に** `best_model` へ
モデルをコピーしている処理を、呼び出し元側に移す。これにより、後続のステップで
`maximizeGoal` の呼び出し元だけを「改善時のみコピー」に変更できるようになる
（`sat_solver_invoke` 自体や他の呼び出し元の挙動は変えない）。

このプランは **その下準備の2ステップのみ**を対象とし、動作を変えない純粋なリファクタリングとする。
改善判定ロジックの追加は次のステップで別途行う。

## 現状の呼び出し関係

`sat_solver_invoke` の呼び出しは実質3箇所に収束する:

1. `PbSolver::solve`（PbSolver.C:1272）— 1回呼んで即座に結果を使う。
2. `PbSolver_Minimize::maximizeGoal`（PbSolver.C:2278/2284/2301/2306）— ループ内の4分岐
   （cmm_Hitting/cmm_sc2用、通常用×cooperative-scipあり/なし）それぞれから呼ばれるが、
   いずれも直後に共通の「process after sat」ブロック（`if (sat_solver_result == sst_sat) {`,
   PbSolver.C:2322付近）に合流する。
3. `PbSolver_Minimize::findModelForBestValueBySAT`（PbSolver.C:3030)
   — ここは**既に**呼び出し直後に `best_model` への再コピーを重複して行っている
   （PbSolver.C:3032-3036、`sat_solver_invoke` 内のコピーと全く同じ処理を二重に行っている）。

`sat_solver_invoke` 内のコピー処理の直後には `config->model_check` によるモデル検証
（`model_check()`）も付随しており、これは現在の `best_model` を検証するため、
コピーと**同時に**移動する必要がある（コピーだけ外に出すと `model_check()` が古いモデルを
検証してしまい壊れる）。

## Step 1: コピー(+model_check)ルーチンを sat_solver_invoke から括り出す

### 1-1. 新しいヘルパー関数を追加

`PbSolver.h` に宣言を追加（`sat_solver_invoke` の宣言の近く、PbSolver.h:299-303付近）:

```cpp
void commitBestModel();  // sat_solver の現在のモデルを best_model へコピーし、model_check を実行する
```

`PbSolver.C` に定義を追加（`sat_solver_invoke` の直前または直後）:

```cpp
void PbSolver::commitBestModel() {
  best_model.growTo(pb_n_vars, false);
  for (Var x = 0; x < pb_n_vars; x++) {
    assert(sat_solver->model(x) != ll_Undef);
    best_model[x] = (sat_solver->model(x) == ll_True);
  }
  if (config->model_check) {
    if (!model_check()) {
      report->reportf("Wrong model\n");
      throw std::runtime_error("Wrong model\n");
    } else {
      report->reportf("Model check passed\n");
    }
  }
}
```

### 1-2. sat_solver_invoke からコピー処理を削除

`PbSolver::sat_solver_invoke`（PbSolver.C:1221-1254）から以下を削除する:

```cpp
    best_model.growTo(pb_n_vars, false);
    for (Var x = 0; x < pb_n_vars; x++) {
      assert(sat_solver->model(x) != ll_Undef);
      best_model[x] = (sat_solver->model(x) == ll_True);
    }
    if (config->model_check) {
      if(!model_check()) {
        report->reportf("Wrong model\n");
        throw std::runtime_error("Wrong model\n");
      }
      else report->reportf("Model check passed\n");
    }
```

削除後は `result = sst_sat;` の直後、`} else {`（unsat処理）の前が空になる
（`config->stats_sat_calls++;` はそのまま残す）。

## Step 2: 各呼び出し元で SAT 時に commitBestModel() を呼ぶ

### 2-1. `PbSolver::solve`（PbSolver.C:1256-1283）

```cpp
  solverStatus result = sat_solver_invoke(assump_ps, failed_assumps);
  if (result == sst_sat) commitBestModel();
  set_pb_solver_status(result);
```

### 2-2. `PbSolver_Minimize::maximizeGoal`（PbSolver.C:2322付近）

4つの呼び出し箇所（2278/2284/2301/2306）はすべて直後にこのブロックへ合流するため、
呼び出しを分散させず、合流後のブロック先頭1箇所にまとめる:

```cpp
    if (sat_solver_result == sst_sat) {
      if (config->verbosity >= 3) report->reportf("process after sat\n");
      consec_UNSAT = 0;

      commitBestModel();
      report->reportf(3, "known_best_model: ");
      dump(config, 3, &index2name, best_model);
      report->reportf(3, "\n");
      set_pb_solver_status(sst_sat);
      ...
```

（`best_model.growTo(...)` 〜 `best_model[x] = ...` のループを `commitBestModel();` の
1行に置き換えるだけで、それ以降の `dump`/`set_pb_solver_status`/`LB_goalvalue = evalGoal(*goalExp)`/
`best_goalvalue = evalGoal(*original_goal)` 等は今回は一切変更しない。)

### 2-3. `PbSolver_Minimize::findModelForBestValueBySAT`（PbSolver.C:3021-3048）

既存の重複コピーループ（3032-3036）を `commitBestModel();` に置き換える:

```cpp
    solverStatus sat_res = sat_solver_invoke(assump_ps, budget);
    if (sat_res == sst_sat) {
      commitBestModel();
      LB_goalvalue = evalGoal(*goalExp);
      best_goalvalue = evalGoal(*original_goal);
      ...
```

## 変更しない箇所

- `maximizeGoal` の "process after sat" ブロックのうち、`commitBestModel()` 呼び出しと
  `dump` 以降（`LB_goalvalue = evalGoal(*goalExp);` 等）は今回は変更しない
  （改善判定の導入は次のステップで行う）。
- `PbSolver_Counting::enumerate`（PbSolver.C:3371付近）は `sat_solver->solve()` を直接呼んでおり
  `sat_solver_invoke` 経由ではないため対象外。

## 検証方法

1. `make r` でリリースビルドする（`make rs` ではなく `make r`）。
2. 既存の手元のテスト入力（`equal5.opb` 等）を、`model_check` を有効にするオプション付きで
   sc2以外の通常モード（デフォルト/seq/bin）・sc2モードの両方で実行し、
   リファクタ前と結果（最適値・SAT/UNSAT判定・実行時間の傾向）が一致することを確認する。
   特に `model_check` が「移動後も確実に実行されている」ことを、
   verbosity を上げて `"Model check passed"` ログが従来通り出ることで確認する。
3. リファクタ自体は動作を変えないはずなので、最適値・モデルの内容がリファクタ前後で
   完全に一致することを確認する（差分が出た場合は呼び出し位置の見落としを疑う）。

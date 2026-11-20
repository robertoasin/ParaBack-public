# SCIP シグナル処理と NaPS 側の対処

## 1. SCIP のシグナルに関する振る舞い

### 1.1 デフォルトの SCIP シグナルハンドリング

SCIP はデフォルトで `misc/catchctrlc = TRUE` が設定されており、SIGINT を自前でキャッチして `SCIPinterruptSolve()` を呼ぶ組み込みハンドラを持つ。

### 1.2 NaPS での無効化

NaPS は SCIP 環境初期化時に以下を実行して SCIP 独自のシグナルハンドラを**無効化**している。

```cpp
// PbSolver_scip.C  solveScip() 初期化ブロック内
SCIPsetBoolParam(scip, "misc/catchctrlc", FALSE);
```

これにより、SIGINT は NaPS の `SIGTERM_handler` のみが処理する。

### 1.3 SCIPinterruptSolve() の意味

```c
void SCIPinterruptSolve(SCIP* scip);
```

- `SCIPsolve()` の**呼び出し元スレッド**から呼んでよい（async-signal-safe ではないが、信号ハンドラから直接呼ぶのは危険なため NaPS では間接的に呼ぶ設計にしている）
- SCIP の内部フラグを立てるだけで、処理は次のチェックポイントで停止する（即時ではない）
- 停止後の `SCIPgetStatus()` は通常 `SCIP_STATUS_USERINTERRUPT (= 10)` を返す

### 1.4 SCIP_STATUS 値（使用するもの）

| 値  | 定数名                      | 意味                                              |
|----:|-----------------------------|---------------------------------------------------|
|   1 | `SCIP_STATUS_OPTIMAL`       | 最適解が証明された                                |
|   2 | `SCIP_STATUS_INFEASIBLE`    | 充足不能が証明された                              |
|   4 | `SCIP_STATUS_INFORUNBD`     | 充足不能 or 非有界                                |
|  10 | `SCIP_STATUS_USERINTERRUPT` | `SCIPinterruptSolve()` により中断                 |
|  11 | `SCIP_STATUS_TERMINATE`     | SIGTERM を受信して停止（SCIP 自身がキャッチした場合）|

### 1.5 SCIP_EVENTTYPE_BESTSOLFOUND の登録タイミングと注意点

```cpp
static SCIP_DECL_EVENTINITSOL(eventInitSolBestSol) {
    SCIPcatchEvent(scip, SCIP_EVENTTYPE_BESTSOLFOUND, eventhdlr, NULL, NULL);
    return SCIP_OKAY;
}
```

- このコールバックは `SCIPsolve()` の**分枝限定開始直前**（Presolve 完了後）に呼ばれる
- **Presolve 段階**で見つかった解に対してはイベントが発火しない
- 結果として、`SCIPgetBestSol()` が非 null を返しても、NaPS の `eventExecBestSol` が呼ばれていない（`best_model` が空、`best_goalvalue` が未設定）場合が存在する

---

## 2. NaPS が管理するシグナル関連の状態

### 2.1 グローバル変数

```cpp
// Main.C
static volatile sig_atomic_t g_interrupted_signum = 0;  // 受信したシグナル番号（0=未受信）
```

### 2.2 PbSolver インスタンス変数

```cpp
// PbSolver.h
bool interrupted;  // interruptSolve() 呼び出し済みかどうか
```

- コンストラクタで `false` に初期化
- `interruptSolve()` 内で `true` に設定
- **一度 `true` になったらリセットしない**（同一インスタンスの再呼び出しを防ぐための番人として機能）

---

## 3. SIGTERM_handler の処理フロー

```
シグナル受信
    │
    ├─[config->use_scip == true]──────────────────────────────────────────┐
    │  メイン SCIP 実行中                                                   │
    │  g_interrupted_signum = signum                                        │
    │  pb_solver->interruptSolve()  ←  interrupted=true, SCIPinterruptSolve │
    │  return  ← ハンドラを即座に抜ける（ヒープ操作はしない）              │
    │                                                                        │
    ├─[config->inner_with_scip && pb_solver_inner && !interrupted]────────┤
    │  inner SCIP 実行中（未中断）                                          │
    │  g_interrupted_signum = signum                                        │
    │  pb_solver->pb_solver_inner->interruptSolve()                         │
    │  return  ← maximizeGoal() が SCIP 終了後に raise(SIGTERM) する        │
    │                                                                        │
    └─[それ以外]──────────────────────────────────────────────────────────┘
       非 SCIP 経路（旧来の動作）
       free(dummy_heap)
       outputResult()
       _exit()
```

### 重要な設計判断

**なぜハンドラ内で `outputResult()` / `free()` / `_exit()` を呼ばないのか（SCIP 経路）**

SCIP の `SCIPsolve()` 内部ではヒープの確保・解放が行われている。シグナルハンドラから `free()` を呼ぶと**ヒープ破壊（double free / corruption）**が起きる。
これを避けるため、SCIP 経路では「シグナル番号を記録して戻る」だけにし、`SCIPsolve()` が正常に返った後にヒープ安全な状態で出力・終了する。

---

## 4. Main.C での中断後処理

```cpp
pb_solver->solve(assump_ps);           // ここで SCIPsolve() が実行される

if (g_interrupted_signum) {            // SCIP 経路で中断された場合
    main_report.reportf("\n");
    main_report.reportf("*** TERMINATED *** by signal %d\n", (int)g_interrupted_signum);
    pb_solver->outputResult(pb_solver->get_pb_solver_status(), config, false);
    _exit(pb_solver->get_best_goalvalue() == Int_MAX ? 0 : 10);
}
```

`solve()` が戻ってきた時点でヒープは安全なため、通常の出力・終了処理を行う。

---

## 5. solveScip() での終了ステータス判定

```cpp
SCIP_STATUS status = SCIPgetStatus(scip);
SCIP_SOL*   final_sol = SCIPgetBestSol(scip);

if (status == SCIP_STATUS_OPTIMAL) {
    // best_model を final_sol から直接設定（イベント経由と重複するが確実）
    best_model.clear();
    for (int i = 0; i < pb_n_vars; i++) { ... }
    if (min_solver && min_solver->original_goal)
        min_solver->best_goalvalue = min_solver->evalGoal(...);
    res = min_solver ? sst_best : sst_sat;

} else if (final_sol != nullptr && best_model.size() > 0
           && !(interrupted && config->is_inner_solver)) {
    // 中断されたが、イベントハンドラが発火済み（best_model 有効）
    // かつメイン SCIP または inner SCIP の未中断呼び出し
    res = sst_sat;

} else if ((status == SCIP_STATUS_INFEASIBLE
            || status == SCIP_STATUS_INFORUNBD) && !interrupted) {
    // 中断なしに充足不能が証明された
    res = sst_unsat;

} else if (status == SCIP_STATUS_USERINTERRUPT
           || status == SCIP_STATUS_TERMINATE || interrupted) {
    // 中断された（解なし、または inner SCIP として扱う）
    res = sst_unknown;

} else {
    res = sst_unknown;  // 予期しないステータス
}
```

### 各条件の判断根拠

| 条件 | 判断 | 理由 |
|------|------|------|
| `SCIP_STATUS_OPTIMAL` | `sst_best` / `sst_sat` | 最適性証明済み。min_solver があれば最適値確定 |
| `final_sol != nullptr && best_model.size() > 0 && !(interrupted && is_inner_solver)` | `sst_sat` | イベント経由で解確認済み。inner かつ中断の場合は除外 |
| `final_sol != nullptr && best_model.size() == 0` | → 下位条件へ | Presolve 段階で見つかった解（イベント未発火）。"o" 未出力のため `sst_unknown` として扱う |
| `INFEASIBLE / INFORUNBD && !interrupted` | `sst_unsat` | 中断なしに充足不能が確定した場合のみ |
| `USERINTERRUPT / TERMINATE / interrupted` | `sst_unknown` | 中断されたため結論不確定 |

### `best_model.size() > 0` 条件の必要性（Issue 3 調査で判明）

Presolve フェーズ（`SCIPsolve()` 開始直後、`SCIP_EVENTTYPE_BESTSOLFOUND` 登録前）に SCIP が解を見つけることがある。この場合：

- `SCIPgetBestSol()` は非 null を返す
- `eventExecBestSol` は呼ばれない → `best_model` は空
- この状態で `sst_sat` を返すと "s SATISFIABLE" + 空モデルという矛盾した出力になる

`best_model.size() > 0` を加えることで「NaPS が自ら確認・出力した解のみを有効とする」という一貫したポリシーを実現する。

---

## 6. inner SCIP 中断時の追加フロー（-hitting -iSCIP）

```
SIGTERM 受信
  → SIGTERM_handler:  !pb_solver_inner->interrupted
  → pb_solver_inner->interruptSolve()  [interrupted=true, SCIPinterruptSolve()]
  → return

SCIPsolve() 終了
  → solveScip(): interrupted=true, is_inner_solver=true
  → sst_unknown を返す

maximizeGoal() (PbSolver.C):
  sat_solver_result == sst_unknown
  → raise(SIGTERM)          ← シグナルを再発行

SIGTERM 受信（2回目）
  → SIGTERM_handler:  pb_solver_inner->interrupted == true
  → !interrupted が false → inner SCIP 経路は通らない
  → 非 SCIP 経路: free(dummy_heap), outputResult(), _exit()
```

### `raise(SIGTERM)` を使う理由

- `maximizeGoal()` からは `_exit()` を直接呼べない（SCIP が終了した後でもヒープが inner SCIP 関連の統計アクセスに使われる可能性がある）
- シグナルハンドラに制御を渡すことで、2回目のハンドラ呼び出し時には `inner SCIP 未中断` 条件が偽になり、非 SCIP 経路（`outputResult()` + `_exit()`）が実行される

### inner SCIP での `sst_sat` 排除理由

```cpp
!(interrupted && config->is_inner_solver)
```

- `is_inner_solver = true` かつ `interrupted = true` の場合、`final_sol` が存在しても `sst_sat` にしない
- inner SCIP が中断された際に `sst_sat` を返すと、`maximizeGoal()` の `sat_solver_result != sst_best` チェックでアサートが発動する

---

## 7. `interrupted` フラグの役割一覧

| 用途 | 詳細 |
|------|------|
| `INFEASIBLE` 誤判定防止 | `!interrupted` で guard することで、中断後の `INFEASIBLE` ステータスを `sst_unsat` と誤解釈しない |
| 2回目のハンドラ呼び出し制御 | `!pb_solver_inner->interrupted` で `maximizeGoal()` の `raise(SIGTERM)` 後の再ルーティングを可能にする |
| `sst_sat` 判定の除外 | inner SCIP の中断時に `sst_sat` を返さないようにする |

---

## 8. 既知の残留リスク・注意事項

### 8.1 `interruptSolve()` と `scip_env` の競合

`interruptSolve()` は以下のように `scip_env` を確認してから `SCIPinterruptSolve()` を呼ぶ：

```cpp
void PbSolver::interruptSolve() {
    interrupted = true;
    if (scip_env) {
        SCIPinterruptSolve((SCIP*)scip_env);
    }
}
```

シグナルが `solveScip()` の初期化ブロック内（`scip_env = (void*)scip;` の直後、`SCIPsolve()` 呼び出し前）で届いた場合、`SCIPinterruptSolve()` は正しく呼ばれるが、SCIP はその後すぐに `SCIPsolve()` に入るため、実際には中断されない可能性がある。ただしこの場合は `interrupted = true` であるため、`SCIPsolve()` が正常完了しても `sst_unknown` または `sst_best` として正しく扱われる。

### 8.2 Presolve 解の扱い

Presolve で見つかった解（`best_model.size() == 0` かつ `final_sol != nullptr`）は現状 `sst_unknown` として捨てている。この解は正当な feasible solution だが、"o" 出力をせずに `s SATISFIABLE` を出すという矛盾を避けるため意図的に無視している。将来的には Presolve 解も拾って "o" を出力することが可能だが、未実装。

### 8.3 SCIP のマルチスレッド環境

SCIP を並列モードで動作させた場合、`eventExecBestSol` が別スレッドから呼ばれる可能性がある。`best_model` への書き込みはスレッドセーフでないため、SCIP の並列化機能を有効にする際は追加の排他制御が必要になる。現在は SCIP のデフォルト設定（シングルスレッド）で動作していることが前提。

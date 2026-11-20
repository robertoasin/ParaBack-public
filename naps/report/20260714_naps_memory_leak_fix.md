# 作業レポート — naps `PbSolver` のメモリリーク修正

実施日: 2026-07-14
計画書: [`plans/20260714_naps_memory_leak.md`](../plans/20260714_naps_memory_leak.md)
発端: avs からの引き継ぎ資料 [`report/20260714_naps_memory_leak.md`](20260714_naps_memory_leak.md)

## 結果

**リークは解消した。** avs の再現スクリプトは `FAIL`（1.2 MB/回の増加）から `OK`（0.0 MB/回、RSS 頭打ち）になった。
**競技モード（CLI）への性能影響はゼロ**であることを実測で確認済み。

---

## 1. 調査で判明した前提（引き継ぎ資料の想定を修正するもの）

### (a) CLI はデストラクタを一切通らない → 競技モードへの影響はゼロ

`Main.C` は `pb_solver` を `new` した後、必ず `exit()` / `_exit()` で終了し `delete` を呼ばない
（Main.C:649-662, 701-711。701 行のコメント「*faster than "return", which will invoke the destructor*」が意図を明記）。

gdb で `PbSolver::~PbSolver` と `PbSolver_Minimize::~PbSolver_Minimize` にブレークポイントを張り、
競技インスタンス（変数 168,151 / 制約 340,015）で実行した結果、**両者とも一度もヒットせずに終了**した。

したがって当初案の「`config->repeated_optimizations == true` のときだけ解放する」というガードは**不要**と判断し、
**無条件に解放**する実装にした。同フラグは「solve() を複数回呼べるモード」という別の意味で
PbSolver.C の 6 箇所で既に使われており、流用すると意味が二重になる。

### (b) デストラクタだけでは直らなかった

`coding()` は内部で `mem.clear()` を呼ぶ（PbSolver_convert.C:249）が、これは `StackAlloc` の生バイトを
`xfree` するだけで、その上に placement new された `Linear` の `Int` 係数のデストラクタを呼ばない。
GMP ビルドでは `Int` は値ごとに `mpz_t` をヒープに確保する（ADTs/Int.h:146-174）ため、係数の数だけ `mpz` が漏れる。

引き継ぎ資料 §3 の「coding() を足しても 1 回あたりの増分が変わらない」という測定結果はこれで説明がつく。
**発生源（`mem.clear()` の直前）で `~Linear()` を呼ぶ必要があった。**

### (c) リーク量として支配的だったのは SAT ソルバ本体

`Solver` の派生クラス（`Solver_CaDiCaL` / `Solver_Kissat` / `Solver_CoreGlueMiniSat` / `Solver_SimpGlueMiniSat`）に
**デストラクタが1つも無く**、`delete sat_solver` しても `new CaDiCaL::Solver` / `kissat_init()` の中身が丸ごと漏れていた。

### (d) `clausifier` は未初期化ポインタだった

コンストラクタの初期化リストに含まれておらず、`delete clausifier` を足す前に `nullptr` 初期化が必須だった。
（`coding()` を呼ばずに `convertPbs()` を先に呼ぶと野良ポインタを参照する潜在バグでもあった）

---

## 2. 変更内容

| ファイル | 変更 |
|---|---|
| `Linear.h` | `~Linear()` を追加（placement new された係数 `Int` を破棄）。`xmalloc` 由来の `Linear` 用に `freeLinear()` ヘルパを追加 |
| `PbSolver_convert.C` | `mem.clear()` の直前に `constrs` / `constrs_deleted` の `~Linear()` を呼ぶ（**本丸**） |
| `PbSolver.h` | `clausifier(nullptr)` 初期化を追加（**必須**）。`~PbSolver()` を拡張。`~PbSolver_Minimize()` を新設。`constrs_deleted` と `dropConstr()` を追加 |
| `PbSolver.C` | `freeConstrs()` / `freeVarNames()` / `freeGoals()` を実装。`constrs[i] = NULL` の 6 箇所を `dropConstr(i)` に置換。`addGoal()` の先頭で `freeGoals()`。`sc2_goal` を `freeLinear()` で解放 |
| `Solver.h` | `Solver_CaDiCaL` / `Solver_Kissat` / `Solver_CoreGlueMiniSat` / `Solver_SimpGlueMiniSat` にデストラクタを追加。`Solver_Kissat()` の `kissat_solver` 未初期化を修正 |
| `Solver.C` | `setTimeout()` が `timeout_term` を上書きしてリークしていたのを修正 |
| `tests/pb_solver_test.cpp` | 抜けていた `delete` を補完（下記） |
| `tests/python/test_memory_leak.py` | リーク回帰テストを新設 |

外部ソルバ（`cadical2/` `kissat/` `glueminisat/`）のソースには一切触っていない。

### テストコードの `delete` 補完

valgrind の出力をクリーンに保ち、将来のリーク回帰を検出できるようにするため、
`tests/pb_solver_test.cpp` の抜けを埋めた:

- **solver 自体が `delete` されていなかった 4 テスト**: `empty_constraint` / `incremental` /
  `scip_incremental` / `assumption`
- **`config` が `delete` されていなかった 10 テスト**: `scip_sat` / `scip_unsat` / `hitting` /
  `hitting_with_scip_inner` / `garden` / `scip_garden` / `garden_with_extra_goal` /
  `scip_garden_with_extra_goal` / `counting` / `project_counting`
- **`Report` が `delete` されていなかった 2 テスト**: `incremental` / `scip_incremental`
- **`toString()` の戻り値が `xfree` されていなかった 2 箇所**: `incremental`

解放順は `solver` → `config` → `report`（`own_config=false` のため solver が先）。
`sat_solver_test` と `failed_assumptions_test` は元から対応が取れていた。

### 調査中に追加で見つけて直したリーク

計画時には把握していなかったが、valgrind で検出して修正したもの:

1. **伝播・マージで削除された制約**（`constrs[i] = NULL` の 6 箇所）の `Linear` が破棄されずに消えていた。
   ポインタを失うと破棄できないため、`constrs_deleted` に退避してから `mem` 解放前にまとめて破棄する形にした。
2. **`addGoal()` が `goal` / `original_goal` を無条件に上書き**していた。goal を 2 回設定すると前回分が丸ごと漏れる
   （`garden_with_extra_goal` テストで再現）。`addGoal()` の先頭で `freeGoals()` を呼ぶようにした。

---

## 3. 検証結果（エビデンス）

### (1) リーク解消 — avs の再現スクリプト

```
$ PYTHONPATH=build_python python3 docs/repro/naps_pbsolver_memory_leak.py
```

| | 修正前 | 修正後 |
|---|---|---|
| 12 回目破棄後の RSS | 31 MB（単調増加） | **17 MB（頭打ち）** |
| 後半 5 回の 1 回あたり増分 | 1.2 MB | **0.0 MB** |
| 判定 | `FAIL` (exit 1) | **`OK` (exit 0)** |

### (2) valgrind — リークは完全に 0

GMP リリースビルドの全テストバイナリに `valgrind --leak-check=full` をかけた:

| テストバイナリ | definitely lost | indirectly lost |
|---|---|---|
| `pb_solver_test`（全 20 テスト） | **0 bytes** | **0 bytes** |
| `sat_solver_test` | **0 bytes** | **0 bytes** |
| `failed_assumptions_test` | **0 bytes** | **0 bytes** |

修正前は `pb_solver_test` 全体で definitely lost 12,952 bytes / indirectly lost 1,872,920 bytes だった。

途中経過として、ライブラリ側を直した時点で残っていた 12,952 bytes は
**テストコードが `new Config()` / `new Report()` / solver を `delete` していない**分だったため、
これも併せて修正した（下記 §2 の「テストコードの `delete` 補完」）。
最後に残った 6 bytes はライブラリ側の `printBestGoalValue()` の `toString()` 未解放（PbSolver.C:1800）で、これも塞いだ。

### (3) 競技モードの性能 — 影響なし

`benchmark/PB12/normalized-PB12/OPT-SMALLINT-LIN/graca/normalized-ped2.B.recomb1-0.01-1.opb`
（変数 168,151 / 制約 340,015 / 総項数 約 558,041）:

| バイナリ | wall | 最大 RSS | 結果 |
|---|---|---|---|
| 旧 `naps_release-1.04a5` | 4.18 s | 131,512 KB | `o 7` `s OPTIMUM FOUND` |
| 新 `build_release/naps` | **4.16 s** | **131,116 KB** | `o 7` `s OPTIMUM FOUND` |

デストラクタを通らないため、想定どおり差は誤差範囲。

### (4) 回帰 — 結果は全モードで一致

| モード | 旧 | 新 |
|---|---|---|
| `garden9x9.opb` | `o 20` | `o 20` |
| `stein27.opb` | `o 18` | `o 18` |
| `-hitting stein27.opb` | `o 18` | `o 18` |
| `-sc2 stein27.opb` | `o 18` | `o 18` |
| `-mc garden9x9.opb`（model_check） | `o 20` | `o 20` |
| `equal5.opb` | `s SATISFIABLE` | `s SATISFIABLE` |
| `-1 garden9x9.opb` | `s SATISFIABLE` | `s SATISFIABLE` |

### (5) テストスイート

- `ctest --test-dir build_debug`（64bit int）: **3/3 passed**
- `ctest --test-dir build_release`（GMP）: **3/3 passed**
- `pytest tests/python/`: **21 passed**（既存 20 + 新規のリーク回帰テスト 1）

デストラクタを追加したことで、これまで走らなかった破棄処理が全 gtest で実行される。
クラッシュしないことが二重解放が無いことのエビデンスになる。

---

## 4. 未対応（今回スコープ外）

引き継ぎ資料および調査で見つかったが、今回は手を付けていないもの:

- **`MinT.Sc2` / `MinT.Sequential_Sc2` が Python から選べない**（引き継ぎ資料 §6、`python/naps_bindings.cpp:151-156`）。
  ユーザー判断により別タスク。2 行の追加で露出できる。
- **`mem` が `StackAlloc<char*>` にバイト数を渡しており 8 倍の過剰確保になっている**（`PbSolver.C:517`）。
  CLI のメモリ使用量にも効く大きな改善だが、`Int` の 8 バイトアラインメントを壊すリスクがあるため別タスク。
- `divideGoal` の中間 `goal_small` が `goals` に入らず迷子になる経路（`PbSolver.C:2922-2926`）。
  今回のテストでは踏まれず valgrind に出なかったが、多段分割が深くなる問題では漏れうる。
- `toString()` の戻り値が `reportf()` の引数で free されない残りの箇所
  （PbSolver.C:2689-2690, 3248-3249, PbSolver_convertBdd.C:406）。
  いずれも verbosity 依存の出力経路で、今回のテストでは踏まれなかった。
- `FEnv::nodes` グローバルの単調増加（プロセス終了時には解放されるので valgrind 上は
  "still reachable" 337 KB。ただし PbSolver インスタンス間で共有される設計上の地雷）。
- `naps_bindings.cpp:198` の `Config::input` setter が毎回 `xstrdup` してリーク。

## 5. avs 側での最終確認（依頼）

```sh
vsep example/ballade1/ballade1.mid --debug 1    # 254 ステップ。RSS が一定に収まること
```

従来 23 GB まで膨らんでいたフル曲が、Python API 経路で解けるようになるはず。

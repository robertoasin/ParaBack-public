# naps メモリリーク修正

## Context

avs（voice separation）の Phase 3 性能調査で、naps の Python API 経路に重大なメモリリークが報告された
（`report/20260714_naps_memory_leak.md`）。`PbSolver_Minimize` を生成して破棄しても確保したメモリが
解放されず、avs のフル曲（254 ステップ、1 ステップごとに solver を作り捨てる）で RSS が 23 GB まで
膨らみ、**Python API 経路ではフル曲が解けない**。従来の subprocess 経路（プロセスが毎回終了して OS が
回収する）では顕在化しなかった問題。

### 調査で判明した前提（レポートの内容を修正・補強するもの）

- **CLI（Main.C）はデストラクタを一切通らない。** `pb_solver` を `new` した後、必ず `exit()` / `_exit()`
  で終了し `delete pb_solver` を呼ばない（[Main.C:649-662](Main.C#L649-L662), [Main.C:701-711](Main.C#L701-L711)。
  701 行のコメント「*faster than "return", which will invoke the destructor*」が意図を明記）。
  → **デストラクタに何を書いても競技モードの終了速度には影響しない。`config->repeated_optimizations`
  によるガードは不要**（同フラグは「solve() を複数回呼べるモード」という別の意味で
  [PbSolver.C:2405,2410,2969,2986,3154,3266](PbSolver.C#L2405) に既に使われており、流用は意味を汚す）。

  **実測エビデンス**（2026-07-14、`build_release/naps` = 1.04a5、GMP リリースビルド）:
  ```
  gdb -batch -ex "break PbSolver::~PbSolver" \
             -ex "break PbSolver_Minimize::~PbSolver_Minimize" -ex run -ex "info breakpoints" \
    --args ./build_release/naps -nm \
      benchmark/PB12/normalized-PB12/OPT-SMALLINT-LIN/graca/normalized-ped2.B.recomb1-0.01-1.opb
  ```
  | 項目 | 値 |
  |---|---|
  | 問題規模 | 変数 168,151 / 制約 340,015 / 総項数 約 558,041 |
  | 実行時間 | 3.33 秒（wall）、最適値 7、`s OPTIMUM FOUND`、exit 30 |
  | 最大 RSS | 129 MB |
  | **デストラクタ呼び出し** | **0 回**（両ブレークポイントとも未ヒットのまま `exited with code 036`） |

  仮にデストラクタが走ったとすると、mpz が約 124 万個（係数 558,041 + lo/hi 340,015×2）あり
  `mpz_clear` + `free` で 0.1 秒前後、加えて CaDiCaL の解放で合計 0.1〜0.3 秒（実行時間の 3〜10%）に
  なるはずだが、**実行されないのでこのコストは発生しない**。タイムアウト／シグナル経由
  （[Main.C:561,563](Main.C#L561)）も `_exit()` なので同様。
- **リークはデストラクタだけでは直らない。** `coding()` が内部で `mem.clear()` を呼ぶ
  （[PbSolver_convert.C:249](PbSolver_convert.C#L249)）が、これは StackAlloc の生バイトを `xfree` するだけで、
  その上に placement new された `Linear` の `Int` 係数（GMP ビルドでは全て `mpz_t` をヒープに持つ）の
  デストラクタを呼ばない。レポート §3 の「coding() を足しても増分が変わらない」という測定結果はこれで説明がつく。
- **リーク量として支配的なのは SAT ソルバ本体。** `Solver` の派生クラス（`Solver_CaDiCaL` / `Solver_Kissat` /
  `Solver_CoreGlueMiniSat` / `Solver_SimpGlueMiniSat`）に**デストラクタが1つも無く**、`delete sat_solver`
  しても `new CaDiCaL::Solver` / `kissat_init()` の中身が丸ごと漏れる（[Solver.h:78-80, 219-410](Solver.h#L78-L80)）。
- **`clausifier` は未初期化ポインタ。** コンストラクタの初期化リスト（[PbSolver.h:88-134](PbSolver.h#L88-L134)）に
  含まれていない。`delete clausifier` を足す前に `nullptr` 初期化が必須（さもなくば野良ポインタを delete する）。

## 方針

デストラクタで**無条件に**解放する（ガードなし）。加えて、デストラクタでは救えない
`coding()` 中の `Int` リークを発生源で塞ぐ。

---

## 変更点

### 1. `Linear.h` — デストラクタと解放ヘルパを追加

`Linear` は `xmalloc` / `StackAlloc` の生メモリ上に placement new され（[Linear.h:20-24](Linear.h#L20-L24)）、
係数 `Int` も placement new される。デストラクタが無いため GMP の `mpz` が必ず漏れる。

```cpp
  // NOTE: allocated with placement new on raw memory (xmalloc / StackAlloc).
  // The destructor must be invoked explicitly before the raw memory is released.
  ~Linear() {
    Int* cs = (Int*)(data + sizeof(Lit)*orig_size);
    for (int i = 0; i < orig_size; i++) cs[i].~Int();
  }   // 'lo' / 'hi' are members and are destructed automatically
```
- `size` ではなく `orig_size` を使う（placement new されたのは構築時の項数）。
- メンバ `Int lo, hi`（[Linear.h:13](Linear.h#L13)）は通常のメンバなので自動で破棄される。
- `NO_GMP`（64bit int）ビルドでは `~Int()` は trivial なので実質 no-op。

`xmalloc` 由来の `Linear`（goal 系）用に、同ファイルへ inline ヘルパも置く:

```cpp
inline void freeLinear(Linear* c) { if (c != NULL) { c->~Linear(); xfree((char*)c); } }
```

### 2. `PbSolver_convert.C:241-251` — `mem.clear()` の前に `~Linear()` を呼ぶ

**ここが本丸。** デストラクタでは救えない、`coding()` のたびに発生するリーク。

```cpp
  if ((stage == 0) && config->model_check && !minimization_mode) {
      for (...) if (constrs[i] != NULL) constrs_bk.push(constrs[i]);   // 既存
  }
  if (!config->model_check) {
      // 'mem' を解放する前に Int(GMP) のデストラクタを明示的に呼ぶ
      for (int i = 0; i < constrs.size(); i++)
          if (constrs[i] != NULL) constrs[i]->~Linear();
  }
  constrs.clear();
  if (!config->model_check) mem.clear();   // 既存
```
`model_check` が ON のときは `constrs_bk` が同じ `Linear` を指し続けるため破棄しない（既存の設計どおり）。
その分はデストラクタ側（下記 `freeConstrs()`）で回収する。

### 3. `PbSolver.h` — コンストラクタ初期化リストと `~PbSolver()`

初期化リスト（[PbSolver.h:88-134](PbSolver.h#L88-L134)）に `clausifier(nullptr)` を追加（**必須**）。

```cpp
virtual ~PbSolver() {
  freeScip();               // scip_vars / SCIP env（既存）
  delete pb_solver_inner;   // config_inner を参照するので config_inner より先
  delete clausifier;
  freeConstrs();            // constrs / constrs_bk の Linear → mem.freeAll()
  freeVarNames();           // index2name の xstrdup 文字列
  delete config_inner;
  if (own_config) delete config;
  if (own_sat_solver) delete sat_solver;   // ok / stats がこれを参照するので最後
  delete sat_config;
}
```

### 4. `PbSolver.C` — 2つのヘルパを実装

```cpp
void PbSolver::freeConstrs() {
    for (int i = 0; i < constrs.size(); i++)
        if (constrs[i] != NULL) constrs[i]->~Linear();
    constrs.clear(true);
    constrs_bk.clear(true);   // constrs と同じ Linear を指すので ~Linear() は呼ばない
    mem.freeAll();            // StackAlloc には dtor が無い（ADTs/StackAlloc.h:78 のコメント参照）
}

void PbSolver::freeVarNames() {
    for (int i = 0; i < index2name.size(); i++) xfree((char*)index2name[i]);  // PbSolver.C:64 の xstrdup
    index2name.clear(true);
}
```
- `constrs_bk` は `constrs` と**同じ `Linear` を指す**（[PbSolver_convert.C:246](PbSolver_convert.C#L246) で
  ポインタを複製しているだけ）。両方に `~Linear()` を呼ぶと二重解放になる。
- `mem.freeAll()` は必須。`clear()` は `freeAll()` 後に `init()` で新しいチャンクを確保し直すので、
  破棄時は `freeAll()` を使う。

`xfree(sc2_goal)`（[PbSolver.C:2561](PbSolver.C#L2561)）を `freeLinear(sc2_goal)` に置換（solve 中のリーク）。

### 5. `PbSolver.h` — `~PbSolver_Minimize()` を新設

現状デストラクタが存在しない（[PbSolver.h:353-458](PbSolver.h#L353-L458)）。

```cpp
~PbSolver_Minimize() override {
    // goals[0] は 'goal' と同一ポインタになり得る（PbSolver.C:2928, 3215）ので二重解放を避ける
    for (int i = 0; i < goals.size(); i++)
        if (goals[i] != NULL && goals[i] != goal && goals[i] != original_goal)
            freeLinear(goals[i]);
    if (goal != original_goal) freeLinear(goal);
    freeLinear(original_goal);
}
```
`PbSolver_Counting` は追加のヒープメンバを持たないのでデストラクタ不要。

### 6. `Solver.h` — 派生クラスにデストラクタを追加（**リーク量として最大**）

外部ソルバ（`cadical2/` `kissat/` `glueminisat/`）のソースには一切触らない。naps 側の `Solver.h` のみ。

| クラス | 確保 | 追加するデストラクタ |
|---|---|---|
| `Solver_CoreGlueMiniSat` ([Solver.h:228](Solver.h#L228)) | `(SimpSolver*) new GlueMiniSat::Solver` | ⚠️ 下記注意 |
| `Solver_SimpGlueMiniSat` ([Solver.h:264](Solver.h#L264)) | `new GlueMiniSat::SimpSolver` | `delete simp_glueminisat;` |
| `Solver_CaDiCaL` ([Solver.h:327](Solver.h#L327)) | `new CaDiCaL::Solver`, `timeout_term` | `delete cadical; delete timeout_term;` |
| `Solver_Kissat` ([Solver.h:405](Solver.h#L405)) | `kissat_init()` | `if (kissat_solver) kissat_release(kissat_solver);` |

⚠️ **`Solver_CoreGlueMiniSat` は要注意**: `core_glueminisat` は `SimpSolver*` 型なのに実体は
`GlueMiniSat::Solver`（[Solver.h:228](Solver.h#L228) の C 形式キャスト）。`delete` する前に
GlueMiniSat 側の継承関係とデストラクタが virtual かを確認し、必要なら
`delete (GlueMiniSat::Solver*)core_glueminisat;` とキャストし直す。実装時に
[Solver.h:219-260](Solver.h#L219-L260) と glueminisat のヘッダを精読して判断する。

`timeout_term` については `clearTimeout()`（[Solver.C:682](Solver.C#L682)）が既に delete していないか確認し、
二重解放を避ける。

---

## 二重解放の落とし穴（実装時に必ず確認）

1. `constrs_bk` ⊂ `constrs`（同じ `Linear` を指す）→ `~Linear()` は片方だけ
2. `goals[0]` == `goal` になり得る → 除外チェック
3. `goal` と `original_goal` が同一になり得るか → 除外チェック
4. `pb_solver_inner` は `config_inner` を `own_config=false` で持つ → inner を先に delete
5. `sat_config` は `sat_solver` に `own_config=false` で渡されている → 二重解放にならない（既存で確認済み）
6. `scip_last_goal` は `original_goal` を指す識別タグ（[PbSolver_scip.C:225](PbSolver_scip.C#L225)）。
   `~PbSolver_Minimize()` が goal を free した後に `~PbSolver()` の `freeScip()` が走るが、
   `freeScip()` はこのタグを参照しないので安全。実装時に再確認する。

## スコープ外（今回は直さない。レポートに残す）

- `MinT.Sc2` / `MinT.Sequential_Sc2` の Python 公開（レポート §6）— ユーザー判断により別タスク
- `mem` が `StackAlloc<char*>` にバイト数を渡しており **8 倍の過剰確保**になっている
  （[PbSolver.C:517](PbSolver.C#L517)）。CLI のメモリ使用量にも効く大きな改善だが、
  アラインメント（`Int` は 8 バイト境界が必要）を壊すリスクがあるため別タスク
- `divideGoal` の中間 `goal_small` が `goals` に入らず迷子になる経路（[PbSolver.C:2922-2926](PbSolver.C#L2922)）
- `toString()` の戻り値が `reportf()` 引数で free されない 4 箇所
  （PbSolver.C:1755, 2689-2690, 3248-3249, PbSolver_convertBdd.C:406）
- `FEnv::nodes` グローバルの単調増加（プロセス終了時には解放されるが、インスタンス間で共有）
- `naps_bindings.cpp:198` の `Config::input` setter が毎回 `xstrdup` してリーク

---

## 検証

**重要: `make d` は 64bit int（`NO_GMP`）ビルドなので `Int` が POD になり、mpz リークが再現しない。**
GMP ビルド（`make r` 相当）での確認が必須。

1. **既存テストの回帰**（＝二重解放が無いことの確認）
   ```bash
   make d && ctest --test-dir build_debug
   ```
   デストラクタを追加すると、これまで走らなかった破棄処理が 20 個の gtest すべてで走る。
   クラッシュしないことが二重解放が無いことのエビデンスになる。

2. **CLI の回帰**（結果が変わらないこと）
   ```bash
   make r
   timeout 60 ./naps_release-<VER> Examples/garden9x9.opb
   timeout 60 ./naps_release-<VER> Examples/stein27.opb -hitting -iSCIP   # pb_solver_inner 経路
   ```
   最適値が従来バイナリ（`naps_release-1.04a5`）と一致すること。

3. **リーク解消の直接確認**（本丸）
   ```bash
   pip install -e .   # または build_python をビルド
   python /home/sakai/proj/transcription/avs/docs/repro/naps_pbsolver_memory_leak.py
   ```
   現状は「後半 5 回の 1 回あたり増分 > 1 MB」で `FAIL` (exit 1)。
   修正後は RSS が数回で頭打ちになり `OK` (exit 0) となること。

4. **回帰テストの追加**
   上記 repro を `tests/python/test_memory_leak.py` に移植し、`ROUNDS` を小さめ（6 程度）にして
   常設する。RSS は `/proc/self/status` の `VmRSS` から取得（Linux 前提）。

5. **（可能なら）ASAN / valgrind**
   ```bash
   cmake -B build_asan -DCMAKE_BUILD_TYPE=Debug -DUSE_GMP=ON -DCMAKE_CXX_FLAGS="-fsanitize=address"
   ```
   leak sanitizer で残存リークを洗う。

6. **avs 側の最終確認**（ユーザー側で実施）
   ```bash
   vsep example/ballade1/ballade1.mid --debug 1   # 254 ステップ。RSS が一定に収まること
   ```

## 成果物

- 計画書を `./plans/20260714_naps_memory_leak.md` にコピー（CLAUDE.md の運用ルール）
- 作業レポートを `./report/20260714_naps_memory_leak_fix.md` に作成

# naps Python インターフェース（pybind11）の設計・実装と pb_solver_test.cpp の Python 移植

## Context（背景と目的）

`pybind` ブランチ（db1b0b9 "1.04"、作業ツリーはクリーン）上で、PB ソルバ naps を Python から
利用できるようにする。第一目標は **tests/pb_solver_test.cpp（746行・20テストケース）と同等の
Python テストが動作すること**。

ユーザー確認済みの方針:
- 実現技術: **pybind11**（pip 導入、ヘッダーオンリー、CMake 統合）
- API スタイル: **C++ ミラー**（getVar / addConstr / coding / solve など同名の薄いバインディング）

環境調査結果:
- Python 3.9.20（pyenv、.python-version 済み）、cmake 3.28。pybind11 / pytest は未導入（pip で導入）
- Python 拡張(.so)には全オブジェクトの -fPIC が必要:
  - cadical3/kissat の configure は `-fPIC` を直接受理（cadical3/configure:182, kissat/configure:271）
  - SCIP は共有ライブラリ（/usr/lib/libscip.so）なので問題なし
  - glueminisat・naps 本体は CMake の `POSITION_INDEPENDENT_CODE` で対応
- Int は GMP ベースで `Int(const char*)` コンストラクタと `toString(Int)` があり（ADTs/Int.h:166,
  Global.h の toString）、Python 任意精度 int と文字列経由で相互変換できる

## 成果物（新規・変更ファイル）

1. **新規 `python/naps_bindings.cpp`** — pybind11 モジュール `naps` 本体
2. **変更 `CMakeLists.txt`** — `BUILD_PYTHON` オプションとモジュールターゲット追加
3. **変更 `Makefile`** — `make py` ターゲット追加
4. **新規 `tests/python/test_pb_solver.py`** — pb_solver_test.cpp の pytest 移植
5. **新規 `python/README.md`** — ビルド・実行手順（簡潔に）

## API 設計（モジュール `naps`）

### enum（C++ の enum を py::enum_ で公開）
- `SolverStatus`: unknown/sat/unsat/best/all/cnf（solverStatus, PbSolver.h:36）
- `SolverT`: Default/…/GlueMiniSat/…/CaDiCaL/Kissat（Config.h:7）
- `Command`: Minimize/FirstSolution/AllSolutions/InterestSolutions/Soft（Config.h:10）
- `MinT`: Sequential/Binary/Hitting/Sequential_Binary/Sequential_Hitting（Config.h:13）

### クラス
- **`Config`**: デフォルトコンストラクタ + テストで使うフィールドを property 公開
  （verbosity, use_scip, inner_with_scip, repeated_optimizations, minimization_strategy,
  command, input, solver, old_format）+ `dis_conflict()` メソッド
- **`Lit`**: `Lit(var: int, sign: bool = False)`、`~lit`（`__invert__`）で否定、
  `.var` / `.sign` / `__eq__` / `__repr__`。内部で MiniSat の Lit と相互変換
- **`Report`**: `setReportFlag(level, flag)` のみ公開（テストの出力制御用）
- **`Solver`**: opaque クラス（外部注入用）。モジュール関数 `Solver_invoke(SolverT, Config)` で生成
- **`PbSolver`** — コンストラクタ 3 種（PbSolver.h:135-139 に対応）:
  `PbSolver()` / `PbSolver(config)` / `PbSolver(config, sat_solver)`
  - `getVar(name: str) -> int`
  - `addConstr(ps: list[Lit], Cs: list[int], rhs: int, ineq: int, rhs2: int = None,
    ineq2: int = -1, llt: Lit = None, rlt: Lit = None, normalized: bool = False) -> bool`
    （ineq: -2 '<', -1 '<=', 0 '==', 1 '>=', 2 '>'。rhs2=None → Int_MAX）
  - `coding() -> SolverStatus` / `coding(assumps: list[Lit]) -> (SolverStatus, list[Lit])`
    （C++ 側が assump_ps を変更しうるため、リスト指定時は更新後のリストも返す。
    garden_with_extra_goal テストの `coding(assump_ps); solve(assump_ps)` パターンに対応）
  - `solve(assumps: list[Lit] = None) -> SolverStatus`、solve 後に
    `failed_assumptions -> list[Lit]` プロパティで失敗 assumption を取得
  - `convertPbs(stage: int) -> bool`、`addInterestVars(num: int)`、`okay() -> bool`
  - `get_pb_solver_status() -> SolverStatus`
  - `best_model -> list[bool]`（コピーを返す）、`index2name(i) -> str`、`nVars() -> int`
  - `outputResult(status, config, normalExit=True)`
  - `report -> Report`（参照、keep_alive）
- **`PbSolverMinimize(PbSolver)`**: 同じコンストラクタ 3 種 +
  `get_best_goalvalue() -> int | None`（Int_MIN のとき None）
- **`PbSolverCounting(PbSolver)`**: 同上 + `get_number_models() -> int`

### モジュール関数・定数
- `parse_PB_file(config, filename, solver, old_format=False, abort_on_error=True)`（PbParser.h:40）
- `Solver_invoke(SolverT, Config) -> Solver`（Solver.h:140）
- `HAS_SCIP: bool`（コンパイル時 USE_SCIP マクロ）— SCIP 系テストの skipif に使用

### 実装上の要点
- **Int 変換**: Python int → `py::str` → `Int(const char*)`（GMP 文字列コンストラクタ）、
  Int → `toString()` → `py::int_(py::str(s))` + `xfree(s)`。GMP ビルド（既定）のみ対象
- **所有権**: `PbSolver(config)` / `PbSolver(config, solver)` は own しない実装なので
  `py::keep_alive<1, 2>`（および `<1, 3>`）で Config / Solver を solver 生存中保持。
  コピー禁止クラスなので `std::unique_ptr` ホルダー（pybind11 既定）でそのまま扱える
- **GIL**: `solve` / `coding` / `convertPbs` / `parse_PB_file` は
  `py::call_guard<py::gil_scoped_release>` で解放
- **注意**: パースエラー等で C++ 側が exit() する経路がある（abort_on_error=True）。
  今回はミラー方針のためそのまま。例外化は将来課題として README に記載

## CMakeLists.txt の変更

1. `option(BUILD_PYTHON "Build Python bindings" OFF)` を追加
2. **EXT_CONFIG_OPT / EXT_BUILD_SUBDIR 決定箇所（95-101行）に BUILD_PYTHON 分岐を追加**:
   - `set(CMAKE_POSITION_INDEPENDENT_CODE ON)`（naps ソース群と add_subdirectory(glueminisat) に継承）
   - `EXT_BUILD_SUBDIR` を `build_pic`（Debug は `build_debug_pic`）にし、
     `EXT_CONFIG_OPT` に `-fPIC` を追加 → 非 PIC の通常ビルド（cadical3/build 等）と成果物を分離
3. テストセクション（231-271行）の後に pb_solver_test（247-250行）と同型のターゲットを追加:
   ```cmake
   if(BUILD_PYTHON)
       find_package(pybind11 CONFIG REQUIRED)
       pybind11_add_module(naps_python python/naps_bindings.cpp ${PB_SRCS} ${ADTS_SRCS})
       set_target_properties(naps_python PROPERTIES OUTPUT_NAME naps)
       target_link_directories(naps_python PRIVATE ${CADICAL_LIB_DIR} ${KISSAT_LIB_DIR}
                               /usr/local/lib /opt/homebrew/lib)
       target_link_options(naps_python PRIVATE -Wl,--allow-multiple-definition)
       target_link_libraries(naps_python PRIVATE ${LINK_LIBS})
       add_dependencies(naps_python ext_cadical ext_kissat fix_kitten_symbols libglueminisat)
   endif()
   ```
   （ターゲット名は実行ファイル `naps` と衝突しないよう `naps_python`、OUTPUT_NAME で `naps.cpython-39-*.so` を生成）

## Makefile の変更

```make
# Python bindings
py:
	cmake $(CMAKE_OPTS) -B $(BUILD_BASE)_python -D CMAKE_BUILD_TYPE=Release -D BUILD_PYTHON=ON \
	      -D pybind11_DIR=$(shell python3 -m pybind11 --cmakedir)
	cmake --build $(BUILD_BASE)_python $(CMAKE_BUILD_OPTS)
```

## テスト移植（tests/python/test_pb_solver.py）

ヘルパー関数（output / sat_case / unsat_case / set_goal_case / min_multistage_case /
set_extraGoal_for_garden9x9）を同名の Python 関数として移植し、各 TEST を 1:1 で
pytest 関数に変換（EXPECT_TRUE/EQ → assert）。report->reportf は print に置換。

| gtest ケース | 検証内容（C++ と同一） |
|---|---|
| sat / unsat / two_solvers | status == sat / unsat、2 solver の独立動作 |
| scip_sat / scip_unsat / scip_incremental / scip_garden* | `@pytest.mark.skipif(not naps.HAS_SCIP)` を付与 |
| empty_constraint | get_best_goalvalue() == -2 |
| incremental | convertPbs(0) 後の再 solve、best_model == [0,0,1]、goalvalue == 2 |
| external_solver_injection | Solver_invoke + PbSolver(config, solver)、status == sat |
| assumption | solve(assumps) 3 回、failed_assumptions の非空/空を検証 |
| minimize / hitting / hitting_with_scip_inner | get_best_goalvalue() == -1 |
| min_multistage | get_best_goalvalue() == 10005 |
| garden / garden_with_extra_goal | parse_PB_file("Examples/garden9x9.opb")、goalvalue 20 → 追加ゴール後 -2 |
| counting / project_counting | get_number_models() == 2、addInterestVars(1) |

実行はリポジトリルートで（garden テストが Examples/ 相対パスを使うため）:
```
PYTHONPATH=build_python python3 -m pytest tests/python/test_pb_solver.py -v
```

## 実装手順

1. `pip install pybind11 pytest`（pyenv 3.9.20 環境へ）
2. CMakeLists.txt / Makefile 変更 → `make py` でビルド確立（まず空に近いモジュールでリンク確認）
3. `python/naps_bindings.cpp` を実装（enum → Config/Lit → PbSolver 系 → module 関数の順）
4. `tests/python/test_pb_solver.py` を移植し、順次 green 化
5. プランを `plans/20260708_python_interface.md` へ、完了レポートを `report/` へ格納

## 検証

1. `make py` が成功し `build_python/naps.cpython-39-x86_64-linux-gnu.so` が生成される
2. `PYTHONPATH=build_python python3 -m pytest tests/python/test_pb_solver.py -v` が全件 pass
   （SCIP 系は HAS_SCIP に応じ実行/skip）
3. 対照確認: 同一ビルドツリーの C++ 版 `./build_python/pb_solver_test` も green で、
   Python 版と判定値（goalvalue, モデル数, status）が一致する
4. 既存ビルドへの非影響確認: `make r` が従来どおり成功する（BUILD_PYTHON=OFF 既定、
   PIC 成果物は cadical3/build_pic 等に分離されるため既存 build_release に影響しない）

# 作業レポート: naps Python インターフェース（pybind11）の実装

日付: 2026-07-08 / ブランチ: pybind（ベース: db1b0b9 "1.04"）
プラン: plans/20260708_python_interface.md

## 目的と結果

tests/pb_solver_test.cpp と同等の Python プログラムが動作することを目標に、
pybind11 による C++ ミラー API の Python バインディングを実装した。

**結果: Python 移植テスト 20/20 pass（SCIP 系 7 件を含む）。
同一ビルドツリーの C++ 版 pb_solver_test も 20/20 pass で判定値が一致。**

```
$ python3 -m pytest tests/python/test_pb_solver.py -v
============================== 20 passed in 1.63s ==============================
$ ./build_python/pb_solver_test
[  PASSED  ] 20 tests.
```

## 成果物

| ファイル | 内容 |
|---|---|
| python/naps_bindings.cpp | pybind11 モジュール `naps`（新規、約330行） |
| python/README.md | ビルド・使用方法・注意事項（新規） |
| tests/python/test_pb_solver.py | pb_solver_test.cpp の 1:1 移植（新規、20テスト） |
| tests/python/conftest.py | リポジトリルートへの chdir と sys.path 設定（新規） |
| CMakeLists.txt | BUILD_PYTHON オプション、PIC 化、naps_python ターゲット追加 |
| Makefile | `make py` ターゲット追加 |

## 実装のポイント

1. **ビルド**: `make py` → `build_python/naps.cpython-39-x86_64-linux-gnu.so`。
   Python 拡張に必要な -fPIC のため、BUILD_PYTHON=ON 時は
   `CMAKE_POSITION_INDEPENDENT_CODE ON` を設定し、CaDiCaL / Kissat は
   `-fPIC` 付きで `cadical3/build_pic` / `kissat/build_pic` に分離ビルド
   （通常ビルドの成果物と混ざらない）。SCIP は共有ライブラリなのでそのまま。
2. **Int（GMP 任意精度）⇔ Python int**: 10 進文字列経由で相互変換。
   Int_MIN / Int_MAX（未定義センチネル）は None にマップ。
3. **所有権**: PbSolver は Config / 注入 Solver を所有しないため
   `py::keep_alive` で寿命を束ねた。pybind11 のインスタンス台帳により
   `pb_solver.config is config`（外部注入テストのポインタ同一性）も成立。
4. **solve の呼び分け**: PbSolver_Minimize / PbSolver_Counting は
   `solve(vec<Lit>&)` をオーバーライドするが `solve(vec<Lit>&, vec<Lit>&)` は
   オーバーライドしない。そこで基底クラスの `solve(assumps)` は 2 引数版
   （failed_assumptions が得られる）、派生クラスは 1 引数版（最適化/数え上げ）を
   呼ぶよう、Python クラスごとに solve を定義した。
5. **GIL**: solve / coding / convertPbs / parse_PB_file の実行中は解放。
6. **coding(assumps)**: C++ 側が assump_ps を変更しうるため
   `(status, 更新後リスト)` のタプルを返す（garden_with_extra_goal の
   `coding(assump_ps); solve(assump_ps)` パターンに対応）。

## 検証記録

- `make py` 成功（pybind11 3.x, Python 3.9.20/pyenv, cmake 3.28, GCC 13）
- `python3 -m pytest tests/python/test_pb_solver.py -v` → 20 passed
- `cmake --build build_python --target pb_solver_test` → C++ gtest 20 passed（対照確認）
- `make r` 回帰確認 → 成功。`./naps_release Examples/garden9x9.opb` → OPTIMUM FOUND / o 20

## 既知の制限（python/README.md にも記載）

- failed_assumptions は基底 PbSolver の solve(assumps) のみ有効（C++ と同じ意味論）
- パースエラー等の exit() 経路はインタプリタごと終了する（例外化は将来課題）
- GMP ビルド前提（USE_64BIT_INT との組み合わせは未対応）

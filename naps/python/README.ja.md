# pynaps

[English](README.md)


擬ブール制約最適化問題解決ソルバ [NaPS](https://www.trs.css.i.nagoya-u.ac.jp/projects/NaPS/) の Python インターフェース。
C++ API（`PbSolver` / `PbSolver_Minimize` / `PbSolver_Counting`）を同名メソッドでミラーする薄いバインディングです。


## インストール

```
pip install pynaps
```

システムパッケージとの干渉を防ぐために、[venv仮想環境](https://docs.python.org/3/library/venv.html)の利用をおすすめします。

> 現在Linuxを対象として配布していますが、`MacOS`および`FreeBSD`にてビルド可能です。
> ソースからのビルド・wheel 生成手順はリポジトリルートの
> [PYTHON_DEVELOPMENT.ja.md](https://gitlab.com/naps2040334/naps/PYTHON_DEVELOPMENT.ja.md) を参照してください。

## 使い方

```python
# ソースツリーから直接実行する場合のみ必要（`pip install pynaps` 後は不要）:
# import sys; sys.path.insert(0, "build_python")
import naps

cfg = naps.Config()
cfg.verbosity = 1
s = naps.PbSolverMinimize(cfg)

x1, x2, x3 = s.getVar("x1"), s.getVar("x2"), s.getVar("x3")
L = naps.Lit
# 2 x1 + 3 x2 + 6 x3 <= 7   (ineq: -2 '<', -1 '<=', 0 '==', 1 '>=', 2 '>')
s.addConstr([L(x1), L(x2), L(x3)], [2, 3, 6], rhs=7, ineq=-1)
# goal: 1 x1 - 1 x2 - 1 x3  （最小化）
s.addGoal([L(x1), L(x2), L(x3)], [1, -1, -1])

s.coding()
status = s.solve()                     # -> naps.SolverStatus
assert status == naps.SolverStatus.best
print(s.get_best_goalvalue())          # Python int（任意精度対応）
print(s.best_model)                    # list[bool]（index は getVar が返した変数番号）
```

主な API:

| Python | 対応する C++ | 備考 |
|---|---|---|
| `Config()` | `Config` | verbosity, use_scip, minimization_strategy などを property 公開 |
| `Lit(var, sign=False)` | `Lit` | `~lit` で否定、`.var` / `.sign` |
| `PbSolver(cfg)` / `PbSolverMinimize(cfg)` / `PbSolverCounting(cfg)` | 各クラス | 引数なし・`(cfg)`・`(cfg, sat_solver)` の 3 形 |
| `s.addConstr(ps, Cs, rhs, ineq, ...)` | `addConstr` | 係数・右辺は Python int（GMP 経由で任意精度） |
| `s.coding()` / `s.coding(assumps)` | `coding` | 後者は `(status, 更新後 assumps)` を返す |
| `s.solve()` / `s.solve(assumps)` | `solve` | 実行中は GIL 解放 |
| `s.failed_assumptions` | `solve(assumps, failed)` の failed | 直近 solve の失敗 assumption（下記注意） |
| `s.get_best_goalvalue()` | 同名 | 未定義（Int_MIN/Int_MAX）は `None` |
| `naps.parse_PB_file(cfg, path, s)` | `parse_PB_file` | OPB/DIMACS 読み込み |
| `naps.Solver_invoke(SolverT.CaDiCaL, cfg)` | `Solver_invoke` | 外部 SAT ソルバ注入用 |
| `naps.HAS_SCIP` | ビルド時 `USE_SCIP` | SCIP 連携の有無 |

enum: `SolverStatus`（sst_\*）, `SolverT`（st_\*）, `Command`（cmd_\*）, `MinT`（mt_\*）。

## 注意事項

- `failed_assumptions` が得られるのは基底 `PbSolver` の `solve(assumps)` のみ。
  `PbSolverMinimize` / `PbSolverCounting` の `solve(assumps)` は C++ の
  `solve(vec<Lit>&)`（最適化・数え上げ）を呼ぶため、failed は常に空リストになる
  （C++ でも `solve(vec&, vec&)` はこれらのクラスでオーバーライドされていない）。
- `PbSolver(cfg)` は Config を所有しない（C++ と同じ）。バインディングは keep_alive で
  solver 生存中 Config を保持するので通常は意識不要。
- パースエラー等で C++ 側が `exit()` する経路がある（`abort_on_error=True` 時）。
  この場合 Python インタプリタごと終了する。例外への変換は将来課題。
- GMP ビルド（既定）を前提とする。`USE_64BIT_INT=ON` との組み合わせは未対応。

# 計画: SATソルバのバージョン表示（CaDiCaL）

## Context

Main.C の594行目で SAT ソルバ名を `reportf` で出力しているが、バージョン番号が含まれていない。
CaDiCaL には `CaDiCaL::Solver::version()` という static メソッドがあり、バージョン文字列を返す。
これを利用して CaDiCaL 選択時にバージョンも併記する。

## 変更対象

**ファイル**: [Main.C](Main.C) の 593〜595 行

```cpp
// 変更前
} else {
    main_report.reportf(1, "Using SAT solver: %s\n", SolverT_name[config->solver]);
}
```

```cpp
// 変更後
} else {
    if (config->solver == st_CaDiCaL) {
        main_report.reportf(1, "Using SAT solver: %s %s\n",
                            SolverT_name[config->solver],
                            CaDiCaL::Solver::version());
    } else {
        main_report.reportf(1, "Using SAT solver: %s\n", SolverT_name[config->solver]);
    }
}
```

## 補足

- `CaDiCaL::Solver::version()` は static メソッドなのでインスタンス不要
- ヘッダの追加は不要（Main.C → PbSolver.h → Solver.h → cadical3/src/cadical.hpp と
  推移的にインクルード済み）
- `st_CaDiCaL` は [Config.h:7](Config.h#L7) の enum `SolverT` で定義済み
- 出力例: `c Using SAT solver: CaDiCaL 3.0.0`

## 検証

```bash
make d
./naps_debug Examples/garden9x9.opb.opb 2>&1 | grep "Using SAT"
# → "c Using SAT solver: CaDiCaL 3.0.0" のような出力を確認
```

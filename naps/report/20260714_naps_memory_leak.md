# 引き継ぎ資料 — naps `PbSolver` のメモリリーク

作成日: 2026-07-14
対象: naps（`~/proj/naps`、`git.trs.css.i.nagoya-u.ac.jp/naps/naps`）1.04a5 / wheel `naps-1.4a5`
報告元: avs（voice separation）Phase 3 の性能調査中に発覚
再現例題: [`docs/repro/naps_pbsolver_memory_leak.py`](../docs/repro/naps_pbsolver_memory_leak.py)（naps 単体で完結、avs 非依存）

## 1. 症状

**`PbSolver`（`PbSolverMinimize`）を生成して破棄しても、確保したメモリが解放されない。**
Python 側で `del solver` + `gc.collect()` をしても RSS が単調に増え続ける。
1 個あたりのリーク量は問題サイズに比例する。

```
naps 1.04a5  (vars=2000, constrs=4000, rounds=12)
  起動時         RSS =       15 MB
   1 回目 破棄後 RSS =       18 MB
   ...
  12 回目 破棄後 RSS =       31 MB
後半 5 回の 1 回あたり増分: 1.2 MB   ← 頭打ちにならない
```

## 2. avs への影響（実用上の障害）

avs はスライディングウィンドウの **1 ステップごとに `PbSolverMinimize` を作り捨てる。**

- 例題 `example/ballade1/ballade1-106-108.mid`（138 イベント、3 ステップ）— 問題なし。
- **フル曲 `example/ballade1/ballade1.mid`（10160 イベント、254 ステップ）— RSS が 23 GB まで膨らむ。**
  実測: 1 ステップ（窓 400 相当）あたり約 22 MB のリーク。41 分走らせた時点で RSS 24.8 GB。
  32 GB のマシンでメモリを食い尽くすため、**フル曲は現状 Python API 経路では解けない。**

従来は PB ファイルを書き出して外部バイナリ `naps` を `subprocess` で起動していたため、
ソルバのプロセスが毎ステップ終了して OS がメモリを回収しており、この問題は顕在化しなかった。
avs の Phase 1（Python API への移行）で表面化した。

**当面の回避策: `vsep --solver naps-bin`**（従来の subprocess 経路。プロセスが毎回終わるので安全）。

## 3. 切り分け — どの段階のメモリが漏れているか

`PbSolverMinimize` を 10 回作り捨てて、段階ごとの増分を測った（vars=2000, constrs=4000）。

| 段階 | 1 回あたりの増分 |
|---|---|
| **構築のみ（`addConstr` / `addGoal` まで）** | **1.30 MB** |
| + `coding()` | 1.13 MB |
| + `coding()` + `solve()` | 1.19 MB |

**構築だけで既に漏れており、`coding()` / `solve()` は追加のリークをほぼ生まない。**
したがって、`solve()` 時に確保されるもの（inner solver など）は原因ではない。
**`PbSolver` のコンストラクタと `addConstr` が確保するメンバが解放されていない。**

## 4. 原因の所在

`naps/PbSolver.h` のデストラクタ（141 行あたり）:

```cpp
virtual ~PbSolver() {
  freeScip();
  if (own_config) delete config;
  if (own_sat_solver) delete sat_solver;
  delete sat_config;
}
```

解放しているのは `config` / `sat_solver` / `sat_config` の 3 つだけ。
一方 `PbSolver` は以下を自分で確保している（`naps/PbSolver.h` のメンバ宣言、44 行〜）:

| メンバ | 確保箇所 | 備考 |
|---|---|---|
| `StackAlloc<char*> mem` | `addConstr` → `storePb()` | **`constrs` に積む `Linear` の実体。§3 のとおり構築だけで漏れるので本命。** |
| `vec<Linear*> constrs` | 同上 | |
| `vec<Linear*> constrs_bk` | model check 用 | |
| `Config* config_inner` | `PbSolver.h:122` の `new Config()` | |
| `Clausifier* clausifier` | `PbSolver.C:1195` と `PbSolver.C:3040` の 2 箇所で `new` | **2 回目の `new` で 1 回目が漏れる可能性がある**（上書き前に `delete` しているか要確認） |

※ `pb_solver_inner`（`PbSolver.C:2136`）は今回の調査対象外（§3 のとおり `solve()` は
追加リークを生んでいないため、優先度は低い）。

## 5. 直ったことの確認方法

```sh
python docs/repro/naps_pbsolver_memory_leak.py
```

- 現状: 後半 5 回の 1 回あたり増分が 1 MB を超え、`FAIL` で終了（exit 1）。
- 修正後: RSS が数回で頭打ちになり、`OK` で終了（exit 0）。

avs 側の最終確認は、フル曲を Python API 経路で流して RSS が一定に収まること:

```sh
vsep example/ballade1/ballade1.mid --debug 1    # 254 ステップ。RSS を watch する
```

## 6. ついでに直せるもの — `mt_Sc2` が Python から使えない

`naps/Config.h:13` には

```cpp
enum MinT { mt_Sequential, mt_Binary, mt_Hitting, mt_Sequential_Binary,
            mt_Sequential_Hitting, mt_Sc2, mt_Sequential_Sc2 };
```

とあるが、`naps/python/naps_bindings.cpp:151-156` の enum 定義に `mt_Sc2` と
`mt_Sequential_Sc2` が入っていないため、**Python からは選べない**。

```cpp
    py::enum_<MinT>(m, "MinT")
        ...
        .value("Sequential_Hitting", mt_Sequential_Hitting);   // ← Sc2 系が無い
```

avs の性能調査では `minimization_strategy` が効くことが分かっており
（step0 の求解: 既定 `Sequential_Binary` 30.4s に対し `Binary` は **15.9s**）、
`Sc2` も試したい。2 行の追加で露出できる。

## 7. 参考: avs 側で測った性能データ

`example/ballade1/ballade1.mid`（10160 イベント、254 ステップ）を Python API 経路で解いたときの
1 ステップの内訳。**求解が支配的で、制約の積み込みと符号化は無視できる。**

| 窓 | 制約数 | 確定変数 | `addConstr` | `coding()` | `solve()` | 最適値 |
|---|---|---|---|---|---|---|
| 62 | 284 | 0 | 0.01s | 0.01s | **19.2s** | 219980 |
| 144 | 1039 | 389 | 0.04s | 0.02s | **40.9s** | 330117 |
| 262 | 2205 | 1043 | 0.07s | 0.03s | **21.6s** | 509687 |

254 ステップの合計は約 1 時間（外挿）。

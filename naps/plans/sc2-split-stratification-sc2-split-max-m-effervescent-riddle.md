# `fprintf(stderr, doc, ...)` 引数欠落バグの修正

## Context

前回のタスク（`-sc2-split` / `-sc2-split-max` オプション追加）の実装完了報告時に、
[Main.C](Main.C) 内で `doc` ヘルプ文字列を使う `fprintf` 呼び出しが2箇所存在し、
そのうち引数なし実行時（usage 表示用、[Main.C:460](Main.C#L460)）の呼び出しに
`config->scip_as_presolve` と `config->scip_as_presolve_for_dec` の2引数が
既存のバグとして欠落していることが判明した（`-h/-help` 用の [Main.C:240](Main.C#L240) には
正しく含まれている）。

`doc` 文字列は単一の書式文字列であり、両方の `fprintf` 呼び出しが同じプレースホルダ順序
（`%s/%g/%d` の出現順）に依存しているため、引数が2つ欠けている `Main.C:460` 側は
`-scip-as-presolve=` 以降のヘルプ本文に表示される全ての `%g`/`%d` の値が2つ分ずれて出力される
（例えば `-scip-as-presolve=` の default 値の位置に本来 `-scip-promising-gap=` の値が
表示される、等）。これはユーザーが引数なしで `naps` を実行した際の usage 表示が壊れて見える
不具合であり、修正する。

## 対象ファイル

- [Main.C](Main.C) のみ変更

## 実装内容

[Main.C:460](Main.C#L460) の `fprintf(stderr, doc, naps_version, config->scip_time_budget,
config->scip_time_budget_factor, config->scip_promising_gap_threshold, ...)` について、
`config->scip_time_budget_factor` の直後・`config->scip_promising_gap_threshold` の直前に、
[Main.C:240](Main.C#L240) と同じ位置にある `config->scip_as_presolve, config->scip_as_presolve_for_dec`
を挿入し、2箇所の引数リストを完全に一致させる。

修正後、両 `fprintf` 呼び出しの引数リストは（`sc2_split_max` を挿入済みの現状を含めて）
完全に同一になる：

```
naps_version, scip_time_budget, scip_time_budget_factor, scip_as_presolve, scip_as_presolve_for_dec,
scip_promising_gap_threshold, scip_promising_boost_budget, sat_time_budget, sat_time_budget_factor,
double_unknown_budget_factor, optbase_primes, optbase_thres, optbase_reduced_primes, cc_thres,
bdd_thres, sort_thres, bdd_max_constraints, bdd_max_adder_cost, sort_max_adder_cost,
goal_bdd_thres, goal_sort_thres, goal_bdd_max_constraints, goal_bdd_max_adder_cost,
goal_sort_max_adder_cost, sc2_split_max, bin_coeff, seq_thres, consec_unsat
```

`doc` 文字列自体（ヘルプ本文）は変更しない。

## 検証方法

1. `make r` でリリースビルドする（メモリ [[ビルドコマンド]] の通り `make rs` ではなく `make r`）。
2. `./naps` を引数なしで実行し、`-h`/`-help` の出力と usage 出力の両方で
   `-scip-as-presolve=`、`-scip-as-presolve-dec=`、`-scip-promising-gap=` 等の `default:` 値が
   一致することを diff で確認する（`./naps -h` と `./naps`（引数なし）の出力を比較し、
   両者の `[def: ...]` / `(default: ...)` 部分が同一になっていることを確認）。

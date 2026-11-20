# プラン: Inner SCIP の "o " 出力を report 経由に変更

## Context

`./naps_debug -hitting -iSCIP Examples/stein27.opb` を実行すると、inner solver として
使われる SCIP が best solution を発見するたびに `printf("o %s\n", tmp)` で直接 stdout
に出力する。この出力は verbosity 設定を無視するため、-vi2 等の指定なしでも
"o -XX" などが混入してしまう。

期待動作：
- デフォルト：inner SCIP の "o " 出力を抑止
- `-vi2` 指定時："cc o 数値" 形式で report 経由で出力

---

## 根本原因

`PbSolver_scip.C:55` の `printf("o %s\n", tmp)` が report システムを経由せず、
verbosity チェックなしに stdout に直接書き込む。

main solver が SCIP の場合（`-SCIP`）は "o value" の直接出力が正当だが、  
inner solver が SCIP の場合（`-iSCIP`）は report 経由での "cc o value" が正しい。

---

## 問題箇所の全体像

| ファイル | 行 | 状況 | 問題 |
|---|---|---|---|
| `PbSolver_scip.C:55` | `printf("o %s\n", tmp)` | `-iSCIP` 使用時 | 常に直接出力 |
| `PbSolver.C:1553` | `printf("o %s\n", real_gv)` | `-vi2` 等で inner_verbosity≥1 | `printBestGoalValue()` が `printf` |

`PbSolver.C:1963` の呼び出し箇所: `if(config->verbosity >= 1) { printBestGoalValue(); }`  
→ `-vi2` で `inner_verbosity=2` が設定されると inner solver の verbosity が 2 になり、  
`printBestGoalValue()` が呼ばれて `printf("o ...")` が直接出力される。

---

## 変更ファイルと内容

### 1. `Config.h`（1行追加）

`is_inner_solver` フラグを追加。inner 用 config を識別するために使う。

```cpp
// 既存行の近く (inner_with_scip の近辺)
bool     is_inner_solver  = false;   // true when this config is for inner solver
```

### 2. `PbSolver.h`（コンストラクタ内、2行追加）

`config_inner` 生成ブロック（行 112〜121）の末尾に追加：

```cpp
config_inner->is_inner_solver = true;
config_inner->report.setReportFlag(config->inner_verbosity, config->ansi);
```

理由：
- `is_inner_solver = true` で PbSolver_scip.C 側がフラグで判断できる
- `setReportFlag(inner_verbosity, ...)` により、現在 `sat_config` からコピーされる
  main verbosity ではなく inner_verbosity でレポートレベルが設定される
  （-vi2 で inner_verbosity=2 になると reportf(2,...) が通る）

### 3. `PbSolver.C`（`printBestGoalValue` 関数、行 1553 の置換）

```cpp
// 変更前
printf("o %s\n", real_gv);

// 変更後
if (config->is_inner_solver) {
    report->reportf(2, "o %s\n", real_gv);
} else {
    printf("o %s\n", real_gv);
}
```

- inner solver の場合：`report->reportf(2, ...)` → `-vi2` なし（reportLevel=0）なら抑止、
  `-vi2` あり（reportLevel=2）なら "cc o 数値" 出力
- main solver の場合：従来通り `printf("o %s\n", ...)` で直接出力

### 4. `PbSolver_scip.C`（行 55-56 の置換）

```cpp
// 変更前
printf("o %s\n", tmp);
fflush(stdout);

// 変更後
if (pb->config->is_inner_solver) {
    pb->report->reportf(2, "o %s\n", tmp);
} else {
    printf("o %s\n", tmp);
    fflush(stdout);
}
```

- inner solver の場合：`pb->report` は prefix "cc " を持ち、reportLevel=inner_verbosity
  - `-vi2` なし（inner_verbosity=0）→ `reportf(2, ...)` は抑止される
  - `-vi2` あり（inner_verbosity=2）→ "cc o 数値" として出力
- main solver の場合：従来通り `printf("o %s\n", ...)` でそのまま出力

---

## "cc " prefix の仕組み

- `makeSatConfig()` で `sat_config->report = c->report` + `deepenPrefix()` → "cc "
- `config_inner->report = sat_config->report` → inner の report は既に "cc " prefix
- よって `pb->report->reportf(2, "o %s\n", ...)` → "cc o 数値" になる

---

## 検証

```bash
make d  # デバッグビルド

# (A) -iSCIP のみ: "o " 出力が消えることを確認
./naps_debug -hitting -iSCIP Examples/stein27.opb 2>&1 | grep "^o "
# → 何も出ないこと（inner SCIP の "o " が抑止される）

# (B) -vi2 のみ（非 SCIP inner）: "o " 出力が消えることを確認
./naps_debug -hitting -vi2 Examples/stein27.opb 2>&1 | grep "^o "
# → 何も出ないこと（inner solver の printBestGoalValue が抑止される）

# (C) -iSCIP -vi2: "cc o 数値" が出ることを確認
./naps_debug -hitting -iSCIP -vi2 Examples/stein27.opb 2>&1 | grep "cc o"
# → "cc o <値>" が複数行出ること

# (D) -hitting -vi2 （非 SCIP inner）: "cc o 数値" が出ることを確認
./naps_debug -hitting -vi2 Examples/stein27.opb 2>&1 | grep "cc o"
# → "cc o <値>" が複数行出ること

# (E) main SCIP: "o " 出力が変わらないことを確認
./naps_debug -SCIP Examples/stein27.opb 2>&1 | grep "^o "
# → 改善した目的関数値が "o 数値" 形式で出ること（is_inner_solver=false なので変化なし）
```

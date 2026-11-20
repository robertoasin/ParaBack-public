# 計画: Config::report 値メンバー化 + Report* 引数の完全削除

## Context

`Config::report` を値メンバー化し、加えて PbSolver・Solver 系クラスのコンストラクタから
`Report*` 引数を完全に取り除く。SAT ソルバー向けの deepened prefix は、従来の
`sat_report`（Report* ポインタ）ではなく `sat_config`（Config のコピー、report を深化済み）
経由に切り替える。

## 変更後の設計

```
Config::report（値メンバー）
  └─ PbSolver.report  → &config->report （convenience pointer、所有しない）
  └─ sat_config->report → config->report のコピー、deepenPrefix() 適用済み
       └─ Solver.report → &sat_config->report （Solver は常に config->report を使う）
       └─ config_inner->report → sat_config->report のコピー
```

`sat_report`（Report*）は廃止し `sat_config`（Config*、常に PbSolver が所有）に置き換える。

## 変更ファイル一覧

| ファイル | 主な変更 |
|---|---|
| [Config.h](Config.h) | `Report* report` → `Report report`、forward decl 削除、`#include "Report.h"` 追加 |
| [Debug.C](Debug.C) | `config->report->` → `config->report.`（全 61 箇所、replace_all） |
| [PbSolver.h](PbSolver.h) | `sat_report*` → `sat_config*`、own_report 廃止、`Report*` 引数削除 |
| [Solver.h](Solver.h) | own_report 廃止、`Report*` 引数削除、全サブクラスのコンストラクタ簡略化 |
| [Solver.C](Solver.C) | `Solver_invoke` から `Report*` 引数削除 |
| [Main.C](Main.C) | `config->report = &main_report` → `config->report = main_report` |
| [tests/pb_solver_test.cpp](tests/pb_solver_test.cpp) | `config->report = test_report` → `config->report = *test_report`、Solver_invoke の呼び出し更新 |

## 詳細な変更手順

### 1. Config.h

```cpp
// Before:
class Report;        // 前方宣言
#include "Int.h"
...
Report*  report = nullptr;

// After:
#include "Report.h"  // 完全定義
#include "Int.h"
...
Report   report;     // 値メンバー（常に有効）
```

### 2. Debug.C

```cpp
// replace_all: config->report->  →  config->report.
```

### 3. PbSolver.h

#### 3-a. メンバー宣言変更

```cpp
// 削除:
bool     own_report;
Report*  sat_report;   // ← sat_config に置き換え

// 追加:
Config*  sat_config;   // SAT ソルバー用 config（deepened report を持つ、常に所有）

// 変更なし:
Report*  report;       // convenience pointer → 常に &config->report
```

#### 3-b. static ヘルパーを置き換え

```cpp
// 削除: makeSatReport(Report*)

// 追加:
static Config* makeSatConfig(Config* c) {
    Config* nc = new Config(*c);
    nc->report.deepenPrefix();
    return nc;
}
```

#### 3-c. コンストラクタシグネチャ（`Report*` 引数を全て削除）

```cpp
// Before:
PbSolver(Config* _config, bool _own_config, Solver* _sat_solver, bool _own_sat_solver, Report* _report = nullptr)
PbSolver(Config* _config, Solver* _sat_solver, Report* _report = nullptr)
PbSolver(Report* _report = nullptr)
PbSolver(Config* _config, Report* _report = nullptr)

// After:
PbSolver(Config* _config, bool _own_config, Solver* _sat_solver, bool _own_sat_solver)
PbSolver(Config* _config, Solver* _sat_solver) : PbSolver(_config, false, _sat_solver, false) {}
PbSolver()                                     : PbSolver(new Config(), true, NULL, true) {}
PbSolver(Config* _config)                      : PbSolver(_config, false, NULL, true) {}
```

#### 3-d. 初期化リスト変更

```cpp
// Before:
, own_report(...)
, report(_report ? ... : ...)
, sat_report(makeSatReport(report))
, sat_solver(_own_sat_solver ? Solver_invoke(config->solver, config, sat_report) : _sat_solver)

// After:
, report(&_config->report)
, sat_config(makeSatConfig(_config))
, sat_solver(_own_sat_solver ? Solver_invoke(config->solver, sat_config) : _sat_solver)
```

#### 3-e. コンストラクタ本体

```cpp
// 削除:
if (own_report && config != nullptr) { ... }
if (config != nullptr && config->report == nullptr) { ... }

// config_inner への report 伝達（値メンバーのコピー代入）:
// Before: config_inner->report = sat_report;
// After:  config_inner->report = sat_config->report;
```

#### 3-f. デストラクタ

```cpp
// Before:
if (own_report) delete report;
delete sat_report;

// After:
delete sat_config;   // 常に所有
// report は config->report のアドレスのため delete 不要
```

### 4. Solver.h

#### 4-a. メンバー宣言

```cpp
// 削除:
bool  own_report;
```

#### 4-b. Solver 基底クラスコンストラクタ（`Report*` 引数削除）

```cpp
// Before:
Solver(Config* _config, bool _own_config, Report* _report = nullptr)
  : report(_report ? _report : ...)
  , own_report(...)

// After:
Solver(Config* _config, bool _own_config)
  : report(&_config->report)
  ...
// コンストラクタ本体の setReportFlag ブロックも削除

Solver() : Solver(new Config(), true) {}
```

#### 4-c. Solver サブクラスのコンストラクタ（全て `Report*` 引数削除）

対象: `Solver_GlueMiniSat`, `Solver_CoreGlueMiniSat`, `Solver_SimpGlueMiniSat`,
     `Solver_CaDiCaL`, `Solver_Kissat`

```cpp
// Before（代表例）:
Solver_CaDiCaL(Report* _report = nullptr) : Solver_CaDiCaL(new Config(), true, _report) {}
Solver_CaDiCaL(Config* _config, bool _own_config, Report* _report = nullptr)
    : Solver(_config, _own_config, _report) ...

// After:
Solver_CaDiCaL() : Solver_CaDiCaL(new Config(), true) {}
Solver_CaDiCaL(Config* _config, bool _own_config)
    : Solver(_config, _own_config) ...
```

#### 4-d. デストラクタ

```cpp
// 削除:
if (own_report) delete report;
```

### 5. Solver.C — `Solver_invoke` の `Report*` 引数削除

```cpp
// Before:
Solver* Solver_invoke(SolverT stype, Config* config = NULL, Report* report = nullptr)
// 内部: new Solver_CaDiCaL(config, createconfig, report)

// After:
Solver* Solver_invoke(SolverT stype, Config* config = NULL)
// 内部: new Solver_CaDiCaL(config, createconfig)
```

Solver.h の宣言も合わせて更新。

### 6. Main.C

```cpp
// Before（行 514）:
config->report = &main_report;

// After:
config->report = main_report;
```

### 7. tests/pb_solver_test.cpp

```cpp
// Before（行 361）:
Report* test_report = new Report();
config->report = test_report;
Solver* sat_solver = Solver_invoke(config->solver, config, test_report);

// After:
Report* test_report = new Report();
config->report = *test_report;
Solver* sat_solver = Solver_invoke(config->solver, config);
// delete test_report は残す（所有権はテスト側のまま）
```

## 検証手順

```bash
make d
./build_debug/pb_solver_test           # 20/20 通過確認
./build_debug/sat_solver_test          # 通過確認
./naps_debug Examples/garden9x9.opb -hitting -vi2 -vs2 2>&1 | head -20
# "c ", "cc ", "ccc " の prefix が正しく出ることを確認
```

# プラン：SCIPタイムアウト時間の定数倍スケーリング

## Context

協調SCIPモード（`-cSCIP`）では、各SCIP呼び出しに固定の `scip_time_budget`（デフォルト10.0秒）が使われる。
問題が複雑なほど後の反復で時間が必要になるため、呼び出しごとにタイムバジェットを定数倍（デフォルト1.2）していくことで、後半の反復で十分な探索時間を確保する。

## 変更対象ファイル

| ファイル | 変更内容 |
|---|---|
| [Config.h](Config.h) | `scip_time_budget_factor` フィールド追加 |
| [Main.C](Main.C) | `-scip-budget-factor=` オプション解析追加 |
| [PbSolver.h](PbSolver.h) | `scip_current_time_budget` メンバ変数追加 |
| [PbSolver.C](PbSolver.C) | 初期化・使用・乗算の3箇所を変更 |

---

## 変更詳細

### 1. Config.h（行104付近）

`scip_time_budget` の直後に追加：

```cpp
double   scip_time_budget = 10.0;        // time limit per cooperative SCIP call (seconds)
double   scip_time_budget_factor = 1.2;  // multiplier applied to scip_time_budget after each cooperative SCIP call
```

### 2. Main.C（行100・235付近）

**ドキュメント（行100の直後）:**
```cpp
"  -scip-budget-factor=<f>   Multiply SCIP time budget by this factor after each call (default: 1.2).\n"
```

**オプション解析（行235の直後）:**
```cpp
else if (strncmp(arg, "-scip-budget-factor=", 20) == 0) config->scip_time_budget_factor = atof(arg+20);
```

### 3. PbSolver.h（行221付近）

SCIP関連メンバ変数の末尾に追加：

```cpp
double  scip_current_time_budget; // current SCIP time budget; starts at scip_time_budget, multiplied by scip_time_budget_factor each coop call
```

### 4. PbSolver.C

**初期化：`PbSolver_Minimize::solve()` 内（行2524付近、`start_time`の設定近辺）に追加：**

```cpp
scip_current_time_budget = config->scip_time_budget;
```

**呼び出し箇所の変更（行2151）：**

```cpp
// before:
solveScip(empty_assump, config->scip_time_budget, goalExp, LB_goalvalue, &scip_dual_ub);
// after:
solveScip(empty_assump, scip_current_time_budget, goalExp, LB_goalvalue, &scip_dual_ub);
```

**乗算（行2151の呼び出し直後）：**

```cpp
scip_current_time_budget *= config->scip_time_budget_factor;
```

---

## 設計上のポイント

- `config->scip_time_budget` は変更しない（元の設定値を保持）
- `scip_current_time_budget` はインスタンスの状態として管理し、`solve()` 開始時にリセット
- `use_scip` のみのモード（行2512の `solveScip(assump_ps)`）には影響しない
- `scip_time_budget_factor = 1.0` で従来と同じ挙動

---

## 検証方法

```bash
# デバッグビルド
make d

# デフォルト動作（factor=1.2）の確認
./naps_debug Examples/garden9x9.opb.opb -cSCIP -v2 2>&1 | grep -E "Calling solveScip|budget"

# factor=1.0 で旧来と同等
./naps_debug Examples/garden9x9.opb.opb -cSCIP -scip-budget-factor=1.0 -v2

# factor=2.0 で急激に増加
./naps_debug Examples/garden9x9.opb.opb -cSCIP -scip-budget-factor=2.0 -v2

# ビルドが通ることの確認
cd build_debug && ctest
```

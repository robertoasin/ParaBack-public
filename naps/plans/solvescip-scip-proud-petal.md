# プラン: SCIPプレソルバ無効化オプションの追加

## Context

`solveScip()` を呼ぶ際に、SCIP フレームワーク内部のプレソルバ（presolving）を無効化したい。
SCIP のプレソルバはデフォルトで有効だが、問題によっては不要または有害な場合があり、
オプションで切り替えられるようにすることでデバッグや性能評価が容易になる。

現在の状況：
- SCIP 環境は `initScipForCoop()` で初期化される（`PbSolver_scip.C:138-248`）
- SCIP パラメータ設定は `SCIPsetIntParam()` / `SCIPsetBoolParam()` で行う（同138-248）
- プレソルバ関連パラメータは現在一切設定されていない（デフォルト有効）

---

## 変更ファイル

| ファイル | 役割 |
|---------|------|
| `Config.h` | 新オプション `scip_no_presolve` のフィールド追加 |
| `Main.C` | CLI フラグ `-scip-no-presolve` のパース追加 |
| `PbSolver_scip.C` | `initScipForCoop()` でプレソルバ無効化パラメータ設定 |

---

## 実装手順

### Step 1: `Config.h` にフラグ追加

SCIP 関連ブール変数が集まっている箇所（`use_scip_constraints` 等の近く、約98-106行）に追加:

```cpp
bool     scip_no_presolve     = false;   // SCIPの内部プレソルバを無効化
```

### Step 2: `Main.C` にCLIオプション追加

既存の `-scip-use-ub` 等（約243行付近）の直後に追加:

```c
else if (strcmp(arg, "-scip-no-presolve") == 0) config->scip_no_presolve = true;
```

ヘルプ文字列（`-scip-budget=` のドキュメントがある付近）にも追加:

```c
"  -scip-no-presolve          Disable SCIP's internal presolver (presolving/maxrounds=0).\n"
```

### Step 3: `PbSolver_scip.C` の `initScipForCoop()` にパラメータ設定

`display/verblevel` 設定（約168行）の直後に追加:

```cpp
if (config->scip_no_presolve) {
    SCIPsetIntParam((SCIP*)scip_env, "presolving/maxrounds", 0);
}
```

`presolving/maxrounds=0` は SCIP でプレソルバを完全無効化する標準パラメータ。

---

## 設計上の判断

- **設定場所は `initScipForCoop()`**: SCIP 環境は一度初期化され再利用されるため、
  `solveScip()` 内ではなく初期化時に設定するのが適切。
- **既存パターンに従う**: `strcmp` / `oneof` によるフラグ解析、`config->` へのフィールド追加、
  `SCIPsetIntParam()` によるパラメータ設定、すべて既存コードのパターンと一致。
- **オプション名**: 既存の `-scip-*` プレフィックスに統一。

---

## 検証方法

```bash
# ビルド
make d   # naps_debug

# プレソルバ有効（デフォルト）で実行
./naps_debug Examples/garden9x9.opb.opb -SCIP -v2

# プレソルバ無効で実行（ログでプレソルバステップがスキップされることを確認）
./naps_debug Examples/garden9x9.opb.opb -SCIP -scip-no-presolve -v2

# 結果が同一であることを確認（最適値が変わらない）
```

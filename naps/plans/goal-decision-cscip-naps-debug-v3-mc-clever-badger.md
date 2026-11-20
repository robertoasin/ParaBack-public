# Plan: decision問題 + -cSCIP の Wrong model バグ修正

## Context

`-cSCIP` でgoalのないdecision問題を解く際、`model_check` が "Wrong model" を出力する。

**根本原因:** `checkScipPrecisionSafety()` が SCIP 初期化前に呼ばれるため `getScipFeastol()` が 0.0 を返し、チェックが早期リターンで無効化されている。

---

## 調査結果

### 誤った解の検証

SCIP presolver が返した解の合計値: **106,925,263** (等式制約 106,925,262 より +1)。
SCIP の内部プリソルバが制約を浮動小数点変換した際に精度が落ち、違反を見逃した。

### checkScipPrecisionSafety が機能しない理由 (両方の coding() で共通)

```cpp
// PbSolver::coding()、PbSolver_Minimize::coding() ともに同じ順序
propagate();
checkScipPrecisionSafety();   // ← scip_env = NULL
  └─ getScipFeastol()
       └─ if (!scip_env) return 0.0;  → feastol=0.0 → 即return → チェック空振り
initScipForCoop();            // ← SCIP はここで初めて作成される
```

### なぜ Check A が有効か

SCIP デフォルト feastol = 1e-6 → `rhs_limit = 1/1e-6 = 1,000,000`  
constraint 2 (正規化後): `lo = 106,925,262 > 1,000,000` → **Check A で unsafe 検出 → SCIP無効化**

---

## 修正方針

`initScipForCoop()` の env 作成部分を `initScipEnv()` として独立させ、
「env作成 → checkScipPrecisionSafety → 制約同期」の順に並べ替える。

---

## 修正内容

### 変更ファイル: [PbSolver_scip.C](PbSolver_scip.C), [PbSolver.h](PbSolver.h), [PbSolver.C](PbSolver.C)

---

### 1. `PbSolver.h`: `initScipEnv()` を宣言追加

[PbSolver.h](PbSolver.h) の `initScipForCoop()` 宣言の近くに追加:
```cpp
void initScipEnv();          // Create SCIP env/vars/handlers (no constraint sync)
```

---

### 2. `PbSolver_scip.C`: `initScipForCoop()` を分割

[PbSolver_scip.C:155](PbSolver_scip.C#L155) の `initScipForCoop()` について:

- `if (!scip_env)` ブロック全体 (env作成・変数追加・ハンドラ登録) を **`initScipEnv()`** として切り出す
- `initScipForCoop()` は冒頭で `initScipEnv()` を呼ぶ形にリファクタ

```cpp
void PbSolver::initScipEnv() {
    if (!ok || scip_env) return;
    // ... (現在の if (!scip_env) { ... } ブロックの中身をそのまま移動) ...
}

void PbSolver::initScipForCoop() {
    report->reportf(2, "initScipForCoop\n");
    if (!ok) return;
    initScipEnv();    // ← 旧 if (!scip_env) { } ブロックをここに置き換え
    if (!scip_env) return;

    SCIP* scip = (SCIP*)scip_env;
    std::vector<SCIP_VAR*>* vars = (std::vector<SCIP_VAR*>*)scip_vars;
    // ... 以降は現行コードのまま (制約同期・trail同期) ...
}
```

---

### 3. `PbSolver.C (PbSolver::coding)`: 呼び出し順を修正

**変更前:**
```cpp
checkScipPrecisionSafety();

if (config->use_cooperative_scip) {
    config->scip_as_presolve = config->scip_as_presolve_for_dec;
    initScipForCoop();
}
```

**変更後:**
```cpp
if (config->use_cooperative_scip) {
    config->scip_as_presolve = config->scip_as_presolve_for_dec;
    initScipEnv();               // env を先に作成して feastol を取得可能にする
}
checkScipPrecisionSafety();      // 実際の feastol で精度チェック

if (config->use_cooperative_scip) {
    initScipForCoop();           // 制約同期 (initScipEnv は idempotent なので無害)
}
```

---

### 4. `PbSolver.C (PbSolver_Minimize::coding)`: 同様に1行追加

[PbSolver.C:2615-2616](PbSolver.C#L2615) 付近:

**変更前:**
```cpp
if(pb_n_constrs>0) propagate();
checkScipPrecisionSafety();
```

**変更後:**
```cpp
if(pb_n_constrs>0) propagate();
if (config->use_cooperative_scip) initScipEnv();  // feastol 取得のために先に env 作成
checkScipPrecisionSafety();
```

---

## 動作フロー (修正後)

1. `initScipEnv()` で SCIP env・変数・ハンドラを作成
2. `checkScipPrecisionSafety()` が `SCIPfeastol()` で実際の feastol を取得 (e.g. 1e-6)
3. constraint 2 の lo=106,925,262 > 1,000,000 → unsafe 検出 → SCIP 無効化
4. presolver ブロックがスキップされ、CNF コーディング → SAT ソルバが正確に解く

---

## 検証方法

```bash
make d
./naps_debug -v3 -mc benchmark/PB06/final/normalized-PB06/SATUNSAT-BIGINT/submitted-PB06/manquiho/Aardal_1/normalized-prob10.opb -cSCIP
```

期待される出力:
- `"SCIP precision unsafe (constr N rhs): disabling presolver and cooperative SCIP"` が出る
- SCIP presolver は起動しない
- SAT ソルバが正しい解またはUNSATを返す
- `"Wrong model"` が出ない

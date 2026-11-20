# 改良方針B：rewritePureClause ガード + 確定変数の SCIP 同期

## 問題の構造

### 1. rewritePureClause による節の漏れ（方針B 本来の対象）

`storePb()` → `rewritePureClause()` が `true` を返すと制約は `sat_solver->addClause()` に
直接送られ `constrs` に入らない。`initScipForCoop()` は `constrs` だけを見るため SCIP に届かない。

### 2. propagate() による確定変数と制約消去（改良点）

`propagate()` は `addUnit()` で変数を確定させながら制約を in-place で簡略化する。

```
元の制約:  x + y + z >= 2    (constrs[i])
x=1 確定 → propagate() が x を除去し lo を調整:  y + z >= 1   (in-place 書き換え)
          → 自明になった場合:  constrs[i] = NULL  (削除)
```

`initScipForCoop()` が呼ばれる時点では:
- **簡略化済み制約**が `constrs` に残っている（固定リテラルは除去済み、bound は調整済み）
- **削除済み制約**は NULL で SCIP には届かない
- **SCIP は確定変数 x=1 を知らない** → lb(x)=0, ub(x)=1 のまま

SCIP が x=0 を選んでも SCIP 制約上はペナルティなし → 元の `x + y + z >= 2` が violated。

**確定変数の SCIP 変数への反映が必須。**

---

## 改良方針B の修正内容

### ファイル 1：`PbSolver.C` — `rewritePureClause()` にガード追加

[PbSolver.C:982](../PbSolver.C#L982) の1行を変更:

```cpp
// 変更前
if (config->use_scip || config->use_scip_constraints) return false;

// 変更後
if (config->use_scip || config->use_scip_constraints || config->use_cooperative_scip) return false;
```

**効果**: 協調モードでは `storePb()` 時の早期節送信をスキップ。
制約は全て `constrs` に入り、`initScipForCoop()` を経由してSCIPに届く。
節としての SAT 送信は `convertPbs()` 内の `rewriteAlmostClauses()` が代わりに行う。

---

### ファイル 2：`PbSolver.h` — scip_trail_synced フィールド追加

SCIP ステートフィールド群（line 216〜）に追加:

```cpp
int     scip_trail_synced;   // SATソルバー trail のうちSCIPに同期済みの件数
```

コンストラクタ初期化リスト（line 108 あたり）に追加:

```cpp
, scip_trail_synced(0)
```

---

### ファイル 3：`PbSolver_scip.C` — `initScipForCoop()` に trail 同期を追加

#### 3a. SCIPfreeTransform 判定を拡張

既存コード (lines 222–230) を以下に置き換える:

```cpp
// New NaPS constraints or newly fixed variables while SCIP held B&B state:
// free the transform to allow global bound changes and new constraints.
bool needs_free_transform = scip_in_transformed_state &&
    (scip_constrs_added < sync_limit ||
     sat_solver->trail_size() > scip_trail_synced);
if (needs_free_transform) {
    report->reportf(2, "initScipForCoop: freeing transform (new constrs or trail)\n");
    if (SCIPfreeTransform(scip) != SCIP_OKAY)
        throw std::runtime_error("SCIPfreeTransform failed in initScipForCoop");
    scip_in_transformed_state = false;
    scip_last_lb_hint_scip = 0.0;
}
```

#### 3b. trail 同期ループ（constrs 同期ループの直後、line 271 の後）

```cpp
// Sync fixed variables from SAT solver trail to SCIP variable bounds.
// Required for correctness: propagate() simplifies and deletes constraints
// from constrs using fixed variable values.  SCIP must know those assignments
// or it will see an under-constrained problem and may return invalid solutions.
int trail_end = sat_solver->trail_size();
for (int i = scip_trail_synced; i < trail_end; i++) {
    Lit l = sat_solver->trail(i);
    int v = var(l);
    if (v >= pb_n_vars) continue;   // auxiliary variable, no SCIP counterpart
    SCIP_VAR* sv = (*vars)[v];
    if (sign(l)) {
        SCIPchgVarUbGlobal(scip, sv, 0.0);   // variable fixed to 0
    } else {
        SCIPchgVarLbGlobal(scip, sv, 1.0);   // variable fixed to 1
    }
}
scip_trail_synced = trail_end;
```

---

## 変更サマリー

| ファイル | 変更箇所 | 変更量 |
|----------|---------|--------|
| `PbSolver.C:982` | `rewritePureClause()` ガード1行追加 | +1行 |
| `PbSolver.h:219` | `scip_trail_synced` フィールド宣言 | +1行 |
| `PbSolver.h:108` | コンストラクタ初期化 | +1行 |
| `PbSolver_scip.C:222–231` | transform 判定条件拡張（既存9行を13行に） | +4行 |
| `PbSolver_scip.C:272` | trail 同期ループ追加 | +13行 |

**合計**: 既存コードの最小変更。`Solver.h` / `Solver.C` への変更なし。

---

## 正確性の根拠

| ケース | 修正後の動作 |
|--------|------------|
| `rewritePureClause` が節を早期送信していたケース | ガード追加により `constrs` に入る → SCIP に届く |
| `propagate()` が制約を簡略化（リテラル除去・bound 調整） | trail 同期により SCIP 変数の lb/ub が確定値に固定 → SCIP も同じ前提で解く |
| `propagate()` が制約を削除（自明満足） | trail 同期により SCIP が削除理由（確定変数値）を知る → 矛盾した解を生成しない |
| 確定変数が 0 の場合（`x = 0`） | `SCIPchgVarUbGlobal(sv, 0.0)` で ub=0 に固定 |
| 確定変数が 1 の場合（`x = 1`） | `SCIPchgVarLbGlobal(sv, 1.0)` で lb=1 に固定 |

---

## 速度への影響

- **SATへの節の早期供給**: `rewritePureClause` ガードにより `storePb()` 時の早期送信はなくなる。代わりに `rewriteAlmostClauses()` が `convertPbs()` 時に節を送る。**実質的な節の集合は同じ**。
- **協調モードでの影響**: SCIPが主役でありSATは補助。パース時vs.convertPbs時の数百ms差は全体の実行時間に対して無視できる。
- **trail 同期のコスト**: `O(trail_size())` のループで各変数に `SCIPchgVarLbGlobal/UbGlobal` を呼ぶ。1回の変数更新は O(1)。通常 trail は小さいため無視できる。
- **SCIP の探索効率**: 確定変数の lb/ub が絞られることで SCIP の LP 緩和が強化される可能性がある（正の副作用）。

---

## 検証手順

```bash
make d   # デバッグビルド

# 1. propagate が発火する問題（純粋節あり）で協調モード実行
./naps_debug Examples/equal5.opb -coop-scip -v3 2>&1 | grep -E "addUnit|propagate|initScip|trail"

# 2. SCIP が出した解を model_check が検証することを確認（-model-check オプション）
./naps_debug Examples/equal5.opb -coop-scip -model-check -v2

# 3. 全テスト
cd build_debug && ctest -V
```

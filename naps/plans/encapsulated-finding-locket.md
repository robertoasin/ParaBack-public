# Plan: normalizePb を分割して SCIP 利用時の誤答を修正（Step 1）

## Context

`-SCIP` オプション使用時に誤った最適値が返される（例: 正解 25 に対し 22）。

**根本原因**: `normalizePb()` はPB制約の数学的正規化に加え、SATソルバ固有の処理（単位伝播・割り当て済みリテラル除去）を行う。SCIP モードではこれらの処理が：
1. 強制リテラル（unit assignment）を SAT ソルバにのみ記録し `constrs` に入れない
2. 後続の制約正規化で SAT ソルバの割り当てを参照して変数を消去し、簡約された制約を `constrs` に格納する
3. SCIP は `constrs` のみを受け取るため、unit 制約と、それに依存して簡約された制約が欠落する

**方針**: `normalizePb` を以下の2つに分割し、SCIP 利用時には純粋な正規化のみ呼ぶ。

## 現在の normalizePb の処理区分

```
normalizePb()
├─ [A] 割り当て済みリテラル・ゼロ係数の除去   ← SAT ソルバ依存 (value())
├─ [B] 符号正規化・x/~x グループ化・ソート   ← 純粋な数学的正規化
│       ※ normalized=true の場合ここで return true
└─ do { changed } while ループ:
    ├─ [C] ユニット伝播（addUnit / addClause） ← SAT ソルバ依存
    ├─ [D] 自明充足・矛盾チェック
    ├─ [E] GCD 簡約                            ← 純粋な数学的簡約
    └─ [F] 係数トリミング                      ← 純粋な数学的簡約
```

## 変更対象ファイル

- [PbSolver.h](../proj/naps/naps/PbSolver.h) — `normalizePb_core` の宣言を追加
- [PbSolver.C](../proj/naps/naps/PbSolver.C) — 実装の追加と `addConstr_` の変更

## 実装内容

### 1. 新関数 `normalizePb_core` の追加（PbSolver.C）

**シグネチャ**（PbSolver.h にも追加）:
```cpp
bool normalizePb_core(bool normalized, vec<Lit>& ps, vec<Int>& Cs, Int& C, Lit llt = lit_Undef);
```

**処理内容**（B + D + E + F のみ、A・C を含まない）:
```
1. [B] normalized=false の場合: x/~x グループ化・符号正規化・係数ソート・sum 計算
        normalized=true  の場合: ソートのみ → return true
2. [D] C <= 0: return false（自明充足、格納不要）
        sum < C: ok = false, return false（矛盾）
3. [E] GCD 簡約（1パス）
4. [F] 係数トリミング（1パス）
5. return true
```

※ ユニット伝播ループは含まない（1パスの GCD/Trim のみ）。非 SCIP では SCIP の代わりに SAT ソルバが推論するため正確性は維持される。

### 2. addConstr_ の変更（PbSolver.C:191, 196, 214, 234, 239）

5箇所の `normalizePb(...)` 呼び出しを条件分岐に変更:

```cpp
// 変更前（例）:
if (normalizePb(false, norm_ps, norm_Cs, norm_rhs, norm_llt))
    storePb(...);

// 変更後:
if (config->use_scip
        ? normalizePb_core(false, norm_ps, norm_Cs, norm_rhs, norm_llt)
        : normalizePb(false, norm_ps, norm_Cs, norm_rhs, norm_llt))
    storePb(...);
```

`normalizePb` 本体は変更しない。

### 3. PbSolver.h への宣言追加

```cpp
// 既存:
bool normalizePb(bool goalMode, vec<Lit>& ps, vec<Int>& Cs, Int& C, Lit llt = lit_Undef);
// 追加:
bool normalizePb_core(bool normalized, vec<Lit>& ps, vec<Int>& Cs, Int& C, Lit llt = lit_Undef);
```

## 変更しないもの

- `normalizePb` 本体（非 SCIP パスは現状のまま）
- `solveScip()` / `PbSolver_scip.C`
- `storePb`, `addConstr_` の構造（呼び出し箇所のみ変更）

## 検証方法

1. ビルド: `make d`
2. 誤答問題で正しい最適値が返ることを確認:
   ```
   ./naps_debug -nm -mc -SCIP err-insts/normalized-f20c10b_016_area_delay.opb
   # → o 25, s OPTIMUM FOUND を確認（従来は o 22）
   ```
3. 非 SCIP の動作が変わらないことを確認:
   ```
   ./naps_debug Examples/garden9x9.opb -mc
   # → o 20, Model check passed を確認
   ```
4. SCIP でも model check が通ることを確認:
   ```
   ./naps_debug Examples/garden9x9.opb -SCIP -mc
   # → o 20, Model check passed を確認
   ```
5. テスト: `cd build_debug && ctest -V`

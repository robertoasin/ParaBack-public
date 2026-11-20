# 計画比較: addConstr_ の SCIP 対応リファクタリング

## 背景・現状

`addConstr_` は `config->use_scip_constraints` フラグで処理を分岐している (5 か所):
- `false` → `normalizePb` (SAT ソルバー連携: リテラル除去・ユニット伝播・反復 GCD)
- `true` → `normalizePb_SCIP` (純数学的正規化のみ。SAT 連携なし)

`use_scip_constraints` は `use_scip` または `use_cooperative_scip` が有効な場合に自動設定される。  
理由: `normalizePb` が制約を削除したり係数を変えると SCIP に渡す制約が変わるためサプレスしていた。

本ソフトウェアは **高速求解を第一目的** とするため、以下の 2 案を比較する。

---

## プラン A: normalizePb_SCIP + extractClause 分離 (フェーズ分割)

### 概要

`normalizePb` の作業を "数学的正規化" と "SAT 連携" の 2 関数に分割し、段階的に移動させる。

```
Phase 1: addConstr_  →  normalizePb_SCIP(数学)  +  extractClause(SAT連携)  → storePb
Phase 2: addConstr_  →  normalizePb_SCIP(数学)  → storePb
         coding      →  extractClause(SAT連携) を各 Linear 制約に適用
```

### extractClause の内容 (normalizePb が行い normalizePb_SCIP が行わない処理)

| 処理 | normalizePb の対応行 |
|------|----------------------|
| 割り当て済みリテラルの除去 | 353–375 (`!config->no_prop` 時) |
| ユニット伝播 + addUnit / addClause | 440–459 |
| UNSAT/tautology 再チェック | 462–473 |
| 反復 GCD + 係数トリム (do-while) | 479–495 |

### SCIP への影響

- `constrs` には `normalizePb_SCIP` で数学的に正規化された PB 制約が格納される
- `initScipForCoop()` はこれをそのまま SCIP へ送る (`SCIPcreateConsBasicLinear`)
- Phase 2 以降、`extractClause` で求まったユニット伝播の結果は SAT ソルバーの割り当てに反映され、`initScipForCoop()` が変数の bounds に取り込む (line 135-136)
- ただし `extractClause` は `coding` まで実行されないため、**SCIP に送る前に SAT 伝播が反映されない** (bounds が緩い)

### 性能への影響

**利点:**
- コードが整理され保守しやすい
- SCIP は完全な PB 制約構造を受け取る → LP 緩和が有効に機能しやすい

**欠点 (高速求解視点):**
1. **制約間カスケード遅延**: `normalizePb` では制約 A の伝播が制約 B の簡単化に即座に波及していたが、Phase 2 後は `coding` まで遅延
2. **SCIP が簡単化前の制約を受け取る**: より大きな係数・より多くの変数 → SCIP の LP 緩和の解像度が粗い
3. **ユニット伝播による変数固定が遅れる**: SCIP の変数 bounds が `coding` まで確定しない

---

## プラン B: normalizePb を SCIP モードでも実行 + SAT 節を SCIP へ転送

### 概要

`use_scip_constraints` フラグを廃止し、常に `normalizePb` を実行する。  
`normalizePb` がユニット伝播で加えた節 (ユニット節 + 二項節) を SCIP に転送することで SCIP も簡単化された問題を解く。

```
addConstr_  →  normalizePb(SAT連携あり)  → storePb (簡単化済み制約)
initScipForCoop()  →  簡単化済み PB 制約 + SAT 割り当て (bounds) を SCIP へ送る
                   →  llt!=lit_Undef 由来の二項節も SCIP へ転送 (追加実装)
```

### normalizePb が SCIP に与える情報

| 情報源 | SCIP への伝達方法 | 現状 vs プランB |
|--------|-----------------|----------------|
| 簡単化済み PB 制約 (`constrs`) | `SCIPcreateConsBasicLinear` | 現状: normalizePb_SCIP のみ適用 → プランB: 反復 GCD + 係数トリムも適用済み |
| ユニット節 (`addUnit` → SAT trail) | `initScipForCoop()` の変数 bounds (lb=ub=0/1) | 現状: ユニット伝播なし → プランB: SCIP は固定変数として受け取れる |
| 二項節 (`llt!=lit_Undef` 時の `addClause`) | **現状: 伝達なし** → プランB: SCIP に SCIPcreateConsBasicLinear で追加 | 追加実装が必要 |
| トリビアル充足で消えた制約 | SCIP には送られない (正しい) | 変わらず正しい |

### 実装上の変更点

1. **[Main.C]** `use_scip_constraints` の設定を削除 (フラグ廃止)
2. **[addConstr_]** ternary (`use_scip_constraints ?`) を `normalizePb` 固定に変更 (5 か所)
3. **[PbSolver_scip.C] `initScipForCoop()`** に拡張:
   - 既存: `constrs` の PB 制約 + SAT 割り当て → SCIP 変数 bounds
   - 追加: `normalizePb` が加えた二項節を SCIP に送る
     - SAT ソルバーの `clause_db` または `addClause` 時に節を別途キャプチャするリスト (例: `vec<vec<Lit>> scip_clauses`) を持つ
     - `initScipForCoop()` でそれを `SCIPcreateConsBasicLinear` (0/1 変数での線形制約) に変換

### 二項節の SCIP 変換

`normalizePb` が `llt != lit_Undef` の場合に追加する節:  
`~llt ∨ ps[i]` ← 「`llt` が真 → `ps[i]` が真」

SCIP の linear 制約に変換:
- 変数が正リテラル `x`: `x[llt] - 1 + x[ps[i]] >= 0` → `x[ps[i]] >= 1 - x[llt]`
- これは `SCIPcreateConsBasicLinear` で表現可能

### 性能への影響

**利点 (高速求解視点):**
1. **制約間カスケード即時**: `addConstr_` 時点で伝播が連鎖 → SCIP 送付前に最大限の簡単化
2. **SCIP が最簡化済み制約を受け取る**: 係数が小さく変数数が少ない → LP 緩和の解像度向上
3. **SCIP の変数 bounds が確定している**: ユニット伝播で固定された変数は SCIP で自動除去
4. **SCIP の探索空間が縮小**: より速い求解が期待できる

**欠点:**
1. **実装複雑度**: 二項節のキャプチャ機構と SCIP への転送が新規実装
2. **制約が消える**: `normalizePb` がトリビアル充足と判断して削除した制約は SCIP に渡らない (ただし SCIP が知る必要のない情報なので正しい)
3. **SCIP の LP 緩和**: 元の PB 制約構造から変換されるため、節として表現されることで LP 強度が下がる可能性 (ただし簡単化済み PB は残っているので大きな問題にはならない)

---

## プラン B 実装詳細 (二項節なし・即時実施)

### 実現可能性の確認

`initScipForCoop()` ([PbSolver_scip.C:134-136](PbSolver_scip.C#L134-L136)):
```cpp
lbool asgn = value(Var(i));
double lb = (asgn == ll_True)  ? 1.0 : 0.0;
double ub = (asgn == ll_False) ? 0.0 : 1.0;
```
→ SAT ソルバーの割り当て (`value()`) を SCIP 変数の bounds に反映済み。`normalizePb` が `addUnit(x)` を呼べば、`initScipForCoop()` 呼び出し時点で `x` の bounds が固定される。**追加実装不要。**

`initScipForCoop()` の呼び出しタイミング: `coding()` 内、`convertPbs(0)` の前。すべての `addConstr_` 完了後なので SAT trail が完成している。

`llt != lit_Undef` 制約は既に `initScipForCoop()` が throw ([line 194-198](PbSolver_scip.C#L194-L198)) → 既存制約であり変更不要。

### 変更箇所 (最小変更)

**[PbSolver.C] `addConstr_` の 5 か所のみ変更:**

| 行 | 変更前 | 変更後 |
|----|--------|--------|
| 192-193 | `config->use_scip_constraints ? normalizePb_SCIP(false,...) : normalizePb(false,...)` | `normalizePb(false,...)` |
| 198-199 | 同上 | `normalizePb(false,...)` |
| 217-218 | `config->use_scip_constraints ? normalizePb_SCIP(normalized,...) : normalizePb(normalized,...)` | `normalizePb(normalized,...)` |
| 238-239 | `config->use_scip_constraints ? normalizePb_SCIP(false,...) : normalizePb(false,...)` | `normalizePb(false,...)` |
| 244-245 | 同上 | `normalizePb(false,...)` |

**`use_scip_constraints` フラグの扱い:**
- `Config.h` のフラグは残す (互換性のため)
- `Main.C` の設定ロジックも残す (将来の `normalizePb_SCIP` 利用時のため)
- ただし `addConstr_` での分岐を撤廃するため事実上無効化

### normalizePb が SAT ソルバーに送る節と SCIP への転送箇所

`normalizePb` が SAT ソルバーに送る節は 2 種類:

| 種類 | コード | 発生条件 | SCIP への転送 |
|------|--------|---------|--------------|
| **ユニット節** (1 リテラル) | `addUnit(ps.last())` | `llt == lit_Undef` かつ `sum - Cs.last() < C` | **既に対応済み**: `initScipForCoop()` が SAT trail の変数割り当てを bounds に変換 |
| **二項節** (2 リテラル) | `sat_solver->addClause([~llt, ps.last()])` | `llt != lit_Undef` かつ `sum - Cs.last() < C` | **未対応**: SCIP に渡っていない。また `initScipForCoop()` は `llt != lit_Undef` 制約に対して throw するため現状 SCIP 非対応 |
| **ユニット節** (UNSAT 時) | `addUnit(~llt)` | `llt != lit_Undef` かつ `sum < C` | bounds 経由で反映 |

**二項節の SCIP 転送が必要な場合の実装場所:**

```
addConstr_()  →  normalizePb()  →  addClause([~llt, ps[i]]) to SAT
                                 ↓ キャプチャ (追加実装)
                                 scip_pending_clauses に記録
                                 
initScipForCoop()  →  PB 制約を SCIP に送付
                   →  scip_pending_clauses の各節を SCIPcreateConsLinear に変換して追加送付
```

実装の具体的な手順:
1. `PbSolver` に `vec<vec<Lit>> scip_pending_clauses` フィールド追加
2. `normalizePb` 内の `sat_solver->addClause(ban)` の後 (または代替として)、`config->use_scip || config->use_cooperative_scip` の場合に `scip_pending_clauses.push(ban)` を追加
3. `initScipForCoop()` の末尾に `scip_pending_clauses` を SCIP 線形制約に変換するループを追加:
   - 節 `[~llt, ps[i]]` → SCIP 制約: `x[ps[i]] + (1 - x[llt]) ≥ 1` → `x[ps[i]] - x[llt] ≥ 0`

**ただし今回の実装スコープ (二項節なし) では:**
- ユニット節 → `initScipForCoop()` の bounds で既に対応
- 二項節 → 今後の議論後に判断 (`llt != lit_Undef` が PB 制約である場合の扱いを含む)

### 変更による動作の変化

| 場面 | 変更前 (use_scip_constraints=true) | 変更後 |
|------|-----------------------------------|--------|
| 制約が trivially true | `normalizePb_SCIP` は `false` を返し `constrs` に入らない場合もあり | `normalizePb` が同様に `false` を返す。動作同等 |
| ユニット伝播 | 発生しない | `normalizePb` が `addUnit` → SAT assigns → SCIP bounds に反映 |
| 反復 GCD/トリム | 1 パスのみ | 収束まで繰り返す → 係数がより小さくなる |
| 制約間カスケード | なし | `normalizePb` が即時カスケード |
| `constrs` の制約サイズ | 正規化前 (大きめ) | 最簡化済み (小さめ) |

---

## 比較まとめ

| 観点 | プランA | プランB |
|------|---------|---------|
| 簡単化のタイミング | coding まで遅延 | addConstr_ 時 (即時) |
| SCIP の制約品質 | 正規化前の PB | 最簡化済み PB + 二項節 |
| SCIP の変数 bounds | coding 前は緩い | addConstr_ 後から固定 |
| SAT 求解前の簡単化 | 遅延あり | 最大化 |
| 実装難度 | 低〜中 | 中〜高 (二項節キャプチャ) |
| コードの明快さ | 高 (役割分担が明確) | 中 (SAT/SCIP 連携が複雑) |
| **高速求解への貢献** | 小〜中 | 大 |

**推奨:** 高速求解を第一目的とするならプランBが有利。ただし、二項節キャプチャの実装コストと、SCIP が PB 構造を失うリスクを考慮し、**フェーズを分けて実施する**:
1. まずプランBの「normalizePb 統一 + SAT 割り当て → SCIP bounds 反映」部分を実装 (二項節なし)
2. 性能測定後に二項節の SCIP 転送を追加判断

---

## 簡単化遅延の性能影響チェック項目

Phase 1/2 実装後に以下を計測・比較:

| 確認項目 | 方法 | 問題になるしきい目 |
|---------|------|-----------------|
| 固定リテラル除去数 | `config->stats_elim_fixed_lits` | プランB で増加すれば有効な証拠 |
| coding 時間 | `config->stats_coding_time` | 大幅増加なら extractClause のコスト |
| SAT 解き時間 | ベンチマーク比較 | 増加すれば CNF が大きい |
| SCIP 求解時間 | ログから SCIP time | プランB で減少すれば有効 |
| `constrs` サイズ | `-v2` の "Converting N PB-constraints" | 削減されれば簡単化効果あり |

```bash
# 比較実行例 (-v2 で統計出力)
./naps_debug problem.opb -v2 2>&1 | grep -E "stats|coding|propagat|Converting"
./naps_debug problem.opb -cSCIP -v2 2>&1 | grep -E "stats|coding|propagat|SCIP"
```

---

## 変更ファイル一覧 (プランA/B 共通)

| ファイル | 変更内容 |
|---------|---------|
| [PbSolver.h](PbSolver.h) | (A) `extractClause` 宣言追加 / (B) `use_scip_constraints` 依存削除 |
| [PbSolver.C](PbSolver.C) | (A) `extractClause` 実装・`addConstr_` 5 か所書き換え・`coding` 追加 / (B) `normalizePb` 固定に変更 |
| [PbSolver_scip.C](PbSolver_scip.C) | (B のみ) `initScipForCoop` に二項節転送を追加 |
| [Main.C](Main.C) | (B のみ) `use_scip_constraints` 設定ロジック削除 |

---

## 検証方法 (共通)

1. **ビルド:** `make d` でデバッグビルドが通ること
2. **テスト:** `cd build_debug && ctest -V` が全通過
3. **回帰テスト:**
   - `./naps_debug Examples/garden9x9.opb.opb` が同じ最適値を出力
   - `-cSCIP` モードで変更前後の結果が一致 (`./naps_debug problem.opb -cSCIP`)
4. **性能ベンチマーク:** 上記性能チェック項目を実施

# SCIP協調モード：節・確定変数の同期漏れ 修正方針比較

## 問題

`rewritePureClause()` は `storePb()` から呼ばれ（パース時）、純粋節と判定した制約を
`sat_solver->addClause()` に直接送り `constrs` には push しない。
`initScipForCoop()` は `constrs` しか参照しないため、これらの節がSCIPに届かない。

---

## 方針A：initScipForCoop でSATソルバーから取得して登録

### 概要

`initScipForCoop()` の中で、CaDiCaL の `traverse_clauses()` と `trail()` API を使い、
SATソルバーが保持する irredundant 節・確定変数をSCIPに同期する。

### 変更ファイル

| ファイル | 変更内容 |
|----------|---------|
| `Solver.h` | `Solver` 基底クラスに `traverse_clauses(callback)` 仮想メソッド追加 |
| `Solver.C` | `Solver_CaDiCaL::traverse_clauses()` を CaDiCaL::ClauseIterator で実装 |
| `PbSolver.h` | `scip_trail_synced`（int）、`scip_sat_clauses_synced`（bool）追加・初期化 |
| `PbSolver_scip.C` | `initScipForCoop()` 内: transform判定拡張、trail同期ループ、節同期ブロック追加 |

変更ファイル数：4、追加行数：約50行

### 速度への影響

- `rewritePureClause()` による**パース時の早期節送信は維持**される。SATソルバーへの情報提供が早い。
- 初回 `initScipForCoop()` 時に CaDiCaL の全 irredundant 節を走査するコストが加わる（通常は節数が少ない段階なので小さい）。
- 節が「SATに直接」と「SCIPに線形制約」の両方に登録される（二重処理だが元々SCIP側が欠落していた分の補完）。
- **確定変数（trail）の同期**も実現でき、SCIPの変数境界が早期に絞られる → SCIPの探索効率が上がる可能性がある。

### 課題

- `Solver_Kissat::traverse_clauses()` は非対応（協調モードは CaDiCaL 使用なので実用上は無問題）。
- `rewriteAlmostClauses()` が `convertPbs()` 内で節を SAT に送るタイミング（`initScipForCoop()` 後）は依然カバーできない。ただしこの段階の節は `constrs` を介して SCIP に渡っているため問題なし。

---

## 方針B：協調モード時に eager_cl を無効化（または rewritePureClause にガード追加）

### 概要

`rewritePureClause()` の冒頭（`PbSolver.C:982`）に `use_cooperative_scip` のガードを追加する。
あるいは `coding()` の先頭で協調モード時に `config->eager_cl = false` とセットする。
これにより `rewritePureClause()` がスキップされ、全制約が `constrs` に入る。
`initScipForCoop()`（`constrs` を走査）は自然にSCIPへ同期し、
`rewriteAlmostClauses()` が `convertPbs()` 内で節をSATに送るため最終的な節の数は変わらない。

### 変更ファイル

| ファイル | 変更内容 |
|----------|---------|
| `PbSolver.C:982` | `\|\| config->use_cooperative_scip` を1行追加 |

変更ファイル数：1、追加行数：1

```cpp
// 変更前
if (config->use_scip || config->use_scip_constraints) return false;

// 変更後
if (config->use_scip || config->use_scip_constraints || config->use_cooperative_scip) return false;
```

### 速度への影響

- `rewritePureClause()` によるパース時の早期節送信がなくなる。SATソルバーへの節の供給が `convertPbs()`（`coding()` 内）まで遅れる。
- ただし同じ節は `rewriteAlmostClauses()` が `convertPbs()` の stage=0 で処理してSATに送るため、**最終的に送られる節の集合は同じ**。
- 協調モードでは SCIP が主役でありSATは補助。SATへの早期節供給の効果は純粋モードより限定的であり、遅延の影響は小さいと考えられる。
- 確定変数（trail）の SCIP 同期は**この方針ではカバーしない**（`propagate()` で確定した変数がSCIPに届かない問題は残る）。ただし trail 同期の欠如は別問題であり、もし重要なら後で独立して対処できる。

---

## 比較サマリー

| 観点 | 方針A（SATから取得） | 方針B（ガード追加） |
|------|---------------------|---------------------|
| 変更量 | 大（4ファイル、約50行） | 最小（1ファイル、1行） |
| 正確性 | ○ 節 + 確定変数を両方カバー | △ 節はカバー、確定変数は別途 |
| SATへの早期節供給 | 維持（eager_cl 有効のまま） | 遅延（convertPbs まで送られない） |
| 協調モードでの速度影響 | ほぼ中立（初回走査コスト小） | SATへの節遅延は小さい（SCIPが主役） |
| SCIPの探索効率 | 確定変数境界絞りで改善の可能性 | 変化なし |
| 実装リスク | 中（Solver抽象に新API追加） | 低（1行のガード） |
| 保守性 | やや複雑 | シンプル・明快 |

## 推奨

**まず方針Bで正確性問題を修正し、確定変数の同期は必要に応じて方針Aの一部として別途追加する**。

- 節の同期漏れは方針Bで1行で解決できる
- 確定変数のSCIP同期（方針Aの4b）は独立した改善として後から追加しやすい
- 方針Aの全実装は、ベンチマークで有意な効果が確認されてから検討する

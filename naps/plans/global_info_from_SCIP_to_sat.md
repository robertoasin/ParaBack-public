# SCIP で確定した変数を SAT ソルバへ還元する改良計画

NaPS の SAT ソルバ（CaDiCaL 等）で確定した変数を SCIP へ初期制約として渡している現在の連携に加え、**SCIP の探索やプレソルブによってグローバルに値が確定した変数を SAT ソルバへ還元する**ための実装計画です。

## 目的
SCIP は強力なプレソルブや LP 緩和により、SAT ソルバ単独では見つけにくい変数の確定（固定）を行うことができます。この確定情報を SAT ソルバ側に「単位節（Unit Clause）」として還元することで、SAT ソルバ側の探索空間を削減し、全体の最適化速度と効率を向上させます。

## 実装ステップ

### 1. SCIP 実行後の確定変数の走査
`PbSolver::solveScip()` 内で `SCIPsolve(scip)` を実行した後、SCIP がグローバルに境界を更新した変数を調べます。

- NaPS と SCIP の変数は `scip_vars`（`std::vector<SCIP_VAR*>`）によってインデックス `i` で対応づけられています。
- 各変数 `v = (*vars)[i]` について、SCIP の API を用いてグローバル境界を取得します。
  - `double glb = SCIPvarGetLbGlobal(v);`
  - `double gub = SCIPvarGetUbGlobal(v);`
- 対象はバイナリ変数であるため、以下のように確定を判定します：
  - `glb >= 0.5` の場合：変数 `i` は **True (1)** に確定
  - `gub <= 0.5` の場合：変数 `i` は **False (0)** に確定

### 2. NaPS 側（SAT ソルバ）への単位節追加
SCIP によって確定された変数のうち、NaPS 側でまだ未確定のものだけを SAT ソルバに通知します。

- NaPS 側での現在の割り当ては `value(Var(i))` で取得できます。
- `value(Var(i)) == ll_Undef` （未確定）であった場合のみ通知を行います。
- SAT ソルバへの通知は単位節の追加として行います：
  - True に確定した場合：`sat_solver->addUnit(Lit(i, false))`
  - False に確定した場合：`sat_solver->addUnit(Lit(i, true))`
- もし `addUnit` が `false` を返した場合、直ちに問題が **UNSAT** であることが判明したことになります。この場合は `sst_unsat` を設定して即座に関数を抜けるようにエラーハンドリングを行います。

### 3. ログと統計情報の追加
連携の効果を測定するため、SCIP によって新たに確定された変数の数をカウントし、ログ出力します。
- `PbSolver_scip.C` の `solveScip()` 終了付近で `c SCIP fixed N variables globally` のように出力。
- （オプション）必要であれば `config` クラスに統計用の変数を追加し、最終的な統計表示 (`printStats`) に含めます。

## 修正対象ファイルと変更の概要

### `PbSolver_scip.C`
#### `[MODIFY] PbSolver_scip.C`
`PbSolver::solveScip()` の内部、`SCIPsolve(scip)` 実行後の結果処理部分にロジックを追加します。
`SCIPfreeTransform(scip)` が呼ばれた後（あるいは前）に元の変数配列をループし、上記 1, 2, 3 の処理を行います。

## User Review Required
> [!IMPORTANT]
> - この変数の還元処理を実装してよろしいでしょうか？
> - SCIP が判定した UNSAT の扱いについて：SAT ソルバに単位節を追加していく過程で UNSAT になった場合、全体を `sst_unsat` とみなして終了させますが、この挙動で問題ありませんか？
> - ログ出力として、標準出力（verbosity に応じて）に `c SCIP fixed N variables globally` のようなメッセージを出力するだけで十分でしょうか？統計情報（`printStats`）への項目追加も行いますか？

# improved-plan-b.md の妥当性検証結果

`plans/improved-plan-b.md` の改善計画を検証しました。結論から申し上げますと、この方針は**NaPSの内部アーキテクチャおよびSCIPの仕様に完全に合致しており、極めて妥当かつ安全なアプローチ**です。

計画されている「SATソルバーによる制約の簡単化（in-place更新）の恩恵を受けつつ、SCIPに正確に同期する」という目的に対して、不足や矛盾はありません。

以下に、ソースコードの挙動に基づいた具体的な妥当性の検証結果をまとめます。

## 1. `propagate()` による簡単化と SCIP の不整合解消の正確性
現在の `PbSolver::propagate()` の実装（`PbSolver.C:527〜`）を確認したところ、確かに確定した変数（`value(c[i]) != ll_Undef`）を取り除き、制約の定数項（`c.lo`, `c.hi`）を in-place で書き換えています。
`initScipForCoop()` はこの書き換えられた後の `constrs` を読んで SCIP 制約を作成するため、SCIP は「変数が除去され、右辺値が小さくなった制約」を受け取ります。提案されている **「SATソルバーの trail（確定変数）を走査し、`SCIPchgVarLbGlobal` / `SCIPchgVarUbGlobal` によって SCIP 側でも該当変数を 0 または 1 に固定する」** という対応は、この不整合を完全に解消する唯一かつ正しいアプローチです。

## 2. `sat_solver->trail()` の参照レベルの安全性
SCIP の Global Bound を固定してしまって問題ないか（探索途中の深いレベルの一時的な仮定ではないか）という点ですが、これも安全です。
- NaPS が `solveScip()` を呼び出すのは初期の `coding()` フェーズ時、あるいは SAT ソルバーの `solve()` ループがタイムアウト等で戻ってきた後です。
- SAT ソルバーは `solve()` 終了時点で探索木をトップレベル（Decision Level 0）まで巻き戻しています。特に標準の CaDiCaL ラッパー（`Solver.C` の `Solver_CaDiCaL::trail_size()`）では、リスナー経由で取得した**グローバルに確定した変数のみ**をリスト (`fixed_lits`) に保持する設計になっています。
- したがって、ここで取得できる trail はすべて「いかなる状況でも覆らない確定変数」であり、SCIP 側に Global な固定値として渡すのは完全に適切です。

## 3. `rewritePureClause` のガード条件の追加
`PbSolver.C:982` に `config->use_cooperative_scip` を追加することで、純粋な節（pure clause）が早期送信されて `constrs` から消滅するのを防げます。
これにより、SCIP が問題の全体像（純粋な節を含むすべての制約）を把握できるようになります。SAT 側への節の送信は、計画にある通り `convertPbs()` 内の `rewriteAlmostClauses()` で確実に行われるため、SAT 側のパフォーマンスや推論能力が損なわれることはありません。これは既存の `-SCIP` モード（`config->use_scip`）で既に実績のある安全な制御であり、協調モードに拡張するのは理にかなっています。

## 4. SCIP の Stage (状態) 遷移の考慮
提案された `PbSolver_scip.C` のコードでは、`needs_free_transform` という条件を用いて「新しい制約が追加される場合」や「新しい trail が同期される場合」に `SCIPfreeTransform(scip)` を呼んで SCIP を Problem Stage（問題構築フェーズ）に戻しています。
SCIP の API 仕様上、Problem Stage であれば `SCIPchgVarLbGlobal` や `SCIPchgVarUbGlobal` による変数の境界変更は安全に実行でき、次回の `SCIPsolve()` 時のプレソルブや LP 緩和に正しく反映されます。

---

## 結論
この計画は、コードの変更量が最小限（合計＋20行程度）に抑えられつつも、協調モードにおける根深い非同期バグ（「SAT側で確定した値と異なる値をSCIPが選んでしまうことで、事実上の違反モデルを生成してしまう」という問題）を根本的に解決する非常に優れた設計です。

追加の懸念点も見当たりませんので、**この方針 B の計画のまま実装（コード変更およびビルド・テスト）に進んでいただいて全く問題ありません。**

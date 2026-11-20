# 計画: coding() 後に getVar した変数が壊れるバグの修正（読み出し層の限定リファクタ）


## Context

`report/20260715_naps_vars_after_coding_bugreport.md`: repeated_optimizations=True の
incremental フロー（Python API）で、初回 `coding()` 後に `getVar` した変数 c について
(1) best_model / index2name 読み出しに現れない【100%】、
(2) c へのハード制約の効果が `get_best_goalvalue()` で非決定的に落ちる（51⇔1）、
(3) `coding()` 再呼び出しで "no such variable index" 例外。

### 確定した根本原因（調査済み）

- `pb_n_vars` は初回 coding() で `pb_n_vars = nVars()`（SAT 総変数数のスナップショット、
  PbSolver.C:1190 / Minimize 版 3035）に固定され、以後更新されない。
- `getVar`（PbSolver.C:56-77）は sat_solver->newVar + index2name/n_occurs 拡張を正しく行う。
  **SAT レベルの制約は正しく積まれており、壊れているのは読み出し層のみ。**
- 症状(1): `commitBestModel`（PbSolver.C:1253-1267）が `best_model.growTo(pb_n_vars)` +
  `[0,pb_n_vars)` コピーのため、後付け変数が best_model に載らない。
- 症状(2): `PbSolver_Minimize::evalGoal`（PbSolver.C:1663-1676）が `best_model[var(goal[i])]`
  を境界チェックなしで読む。`best_goalvalue = evalGoal(*original_goal)`（PbSolver.C:2463）で
  vec<bool> の**範囲外読み（未初期化ヒープ）** → cold-start でぶれる実測と整合。
  model_check（1114,1125）、goalValueCheck（1147）も同型。
- 症状(3): 2回目 coding() で pb_n_vars = nVars() が Tseitin 補助変数
  （Hardware_clausify.C:74,86,241、index2name 未登録）込みで再計算され
  pb_n_vars > index2name.size()。Python の index2name(i)（naps_bindings.cpp:279-285）が例外。
  さらに C++ 側にも 2 つの UB: setupOccurs（PbSolver.C:714-744）の n_occurs 範囲外読み、
  clausifier 無条件 new（1226, 3125）のリーク。

### 方針（ユーザー確定 2026-07-15）

- **読み出し層の限定リファクタ**（PB/SAT 変数空間の完全分離はしない）。
- Version.h 更新・wheel 再ビルドはスコープ外（コード修正+テストまで）。
- Python テストに加えて **gtest（tests/pb_solver_test.cpp）にもテストケースを追加**。
- mt_Hitting / Sequential_Hitting 系は削除予定のため修正・テスト対象外。

## 変更内容

### Step 1: C++ コア（PbSolver.h / PbSolver.C / Debug.C）

1. **named 変数台帳**: PbSolver.h:218 付近に `vec<Var> named_vars;`（getVar が作った SAT var の
   生成順リスト。var 値は単調増加）。getVar（PbSolver.C:56-77）で push、
   freeVarNames（PbSolver.C:103-108）で clear。
   **注意（ユーザー指摘）**: nVars() は getVar / clausify 以外に **CaDiCaL が内部で SAT 変数を
   追加して突然増えることがある**。named_vars の単調増加は getVar 経由のみなので保たれるが、
   「nVars() == named + Tseitin の合計」という対応は仮定しないこと。
   commitBestModel のフルサイズコピーで CaDiCaL 内部変数の model(x) が ll_Undef を
   返し得るため、ll_Undef→false 緩和（下記 3.）はこの点でも必須。
2. **防御的アクセスヘルパ**（PbSolver.h、inline）:
   - `bool bestModelVal(Var v) const { return v < best_model.size() && best_model[v]; }`
   - `bool bestModelTrue(Lit p) const`（sign 考慮）
3. **commitBestModel フルサイズ化**（PbSolver.C:1253-1267）:
   `best_model.growTo(nVars(), false)`、ループ `x < nVars()`、
   `assert(model(x) != ll_Undef)` を削除し ll_Undef→false 扱い
   （CaDiCaL/Kissat は範囲外 var に ll_Undef を返す: Solver.C:645-651, 784-789）。
4. **best_model 直接添字の置換**: evalGoal（1670-1671）、model_check（1114,1125）、
   goalValueCheck（1147）を bestModelTrue に。goalValueCheck の
   `assert(best_model.size() >= pb_n_vars)`（1143）は `assert(best_model.size() > 0)` に。
   Main.C:490-514 の model_check(PbSolver&) は**呼び出し箇所なしの dead code** →
   最小変更方針により削除はせず bestModelVal 化のガードのみ。
5. **index2name[x] を [0,pb_n_vars) で引く箇所の named_vars 化**（NULL deref 排除）:
   - model_check verbose ループ（1093-1097）
   - **outputResult の v 行（PbSolver.C:1429-1438）** — 現行実装は best_model.size() で
     ループし index2name[x] を直接 deref するため、フルサイズ化後は Tseitin 変数で
     NULL deref → named_vars ベース化が必須。
     **競技モードの規約（ユーザー指摘）**: v 行に出せるのは **OPB ファイルから読み込んだ
     変数のみ**。内部生成の named 変数は全て `@` プレフィックス
     （@defequiv / @split / @goal_constraint / @relaxing_constraint / @defsofts、
     grep で全数確認済み）であり、非 @ の named 変数 = OPB 由来変数なので、
     named_vars ループ + **既存の `*(index2name[x]) != '@'` スキップを維持**すれば
     規約は保存される（CLI では出力完全一致が期待値）。verbosity>=4 の @ 込み出力は
     デバッグ用途のため従来どおり。
   - exportVar（1160-1161）: `named_vars[i]+1 : 名前`（CNF 変数番号 = SAT var+1 を維持）
   - Debug.C:74-81 の dump(vec<bool>): NULL 名ガード
   - exportToCnf の pb_n_vars-1（1178）は CLI 専用 identity 経路のため現状維持
6. **2回目 coding() のガード**（チェックリスト3対応）:
   - setupOccurs 先頭に `n_occurs.growTo(nVars()*2, 0);` を追加、
     `assert(nVars() == pb_n_vars)`（718）を削除
   - `if (clausifier == nullptr)` ガード（1226, 3125、リーク防止 + vmap 再利用）
7. **LB シードの範囲ガード**（PbSolver.C:2157-2166）: goalExp の全 var が
   best_model.size() 未満のときのみ evalGoal で LB シード。
   ※ false 扱いでは不健全: normalizeGoal（1638-1658）がリテラル反転するため
   範囲外→false が LB を過大評価し得る（誤った最適値確定リスク）。
8. **enumerate（PbSolver_Counting、PbSolver.C:3449-3552）**:
   手書きモデルコピー（3508-3519）→ `commitBestModel()` 呼び出しに置換。
   freeze（3467-3469）・出力/ban ループ（3496, 3526-3534）を named_vars ベースに
   （interestMode は `vars = interestMode ? pb_i_vars : named_vars.size()`）。
9. **SCIP 経路（PbSolver_scip.C）は変更しない**: 内部整合しており範囲外読みなし。
   「SCIP/coop 経路は coding 後の追加変数を SCIP に伝搬しない」を既知の制限として
   レポートに明記。

### Step 2: Python バインディング dense 化（python/naps_bindings.cpp）

- `index2name(i)`（279-285）: i を **named インデックス**（getVar 登録順）に変更。
  `i >= named_vars.size()` で例外、`index2name[named_vars[i]]` を返す。
- `best_model` プロパティ（286-291）: `named_vars` 順の dense リスト
  （`v >= best_model.size()` で break）。best_model[i] と index2name(i) が常に同一空間。
- docstring に「i は登録順。getVar 戻り値（SAT 変数番号）とは coding 後登録の変数で
  一致しない」ことを明記。
- 全変数を coding 前に登録する既存クライアントでは完全 identity（挙動不変）。
- 注記: solve 中の内部変数（@goal_constraint 等）が best_model 末尾に現れるようになる
  （@ プレフィックスでフィルタ可能、既存クライアントへの実害なし）。

### Step 3: テスト

1. **Python**（tests/python/test_pb_solver.py、test_incremental:159-194 の直後）:
   - `test_incremental_var_after_coding`: repro A ケース相当を 10 回ループ
     （旧コードは非決定 OOB のため反復必須）。goalvalue==51、'c' in model かつ True。
   - `test_incremental_var_before_coding`: B ケース（回避策の継続動作）。
   - `test_recoding_after_new_var`: 2 回目に coding() を呼ぶ変種。例外なしで 51。
2. **gtest**（tests/pb_solver_test.cpp）:
   - `TEST(PB_solver_test, incremental_var_after_coding)`: coding→solve 後に
     getVar("c") + addConstr(c>=1) + addGoal(a,b,c;1,1,50) + convertPbs(0) + solve()。
     `get_best_goalvalue()==51`、`best_model[c]==true`、`best_model.size() >= c+1`。
   - output() ヘルパ（10-30）と 475-476 のループを NULL 名 skip / named_vars ベースに修正。

## 検証

1. `make d` + `ctest --test-dir build_debug`（既存 + 新規 gtest green）
2. `make r` → CLI 回帰: 既存 naps_release-1.04a7 と新バイナリで Examples/*.opb を
   `timeout 10` 付き実行し s/o/v 行を比較（identity 修正なので完全一致が期待値。
   `-A`（enumerate 経路）も 1 件以上）。regression-runner エージェント利用可。
3. `make py` → `PYTHONPATH=build_python python3 docs/repro/naps_vars_after_coding_dropped.py`
   が **exit 0**（チェックリスト1）。pytest（tests/python/）green。
4. 2回目 coding() が例外を出さないこと（チェックリスト3、test_recoding_after_new_var）。
5. 推奨（任意）: ASan 付きデバッグビルドで repro を実行し、修正前 heap-buffer-overflow
   検出 / 修正後クリーンを確認（根本原因の実証）。
6. チェックリスト2（avs 側 bench/incremental_probe.py）は外部プロジェクト側の検証項目
   として完了レポートに記載。

## 完了時

- `./report/` に作業レポート（修正内容・検証結果・既知の制限:
  SCIP 経路の後付け変数非対応、model_check の追加制約検査対象外
  （PbSolver_convert.C:244-247 の constrs_bk 退避条件）、2回目 coding() のフル意味論は
  best-effort）を保存。
- Version.h 更新・wheel 再ビルドは行わない（ユーザー指示）。

## リスク・未確定点

- Python index2name の引数空間変更: getVar 戻り値を直接 index2name() に渡すクライアントは
  coding 後登録変数で挙動が変わる（観測済みの使用パターンは全て best_model index ループ
  なので実害なし見込み。docstring で明示）。
- 2回目 coding() のフル意味論（propagate 再実行の副作用等）は「エラーなし + repro が
  正しく動く」までの best-effort 保証。

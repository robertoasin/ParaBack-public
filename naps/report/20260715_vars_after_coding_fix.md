# 作業レポート: coding() 後に getVar した変数が壊れるバグの修正

- **日付**: 2026-07-15
- **対象バグ**: `report/20260715_naps_vars_after_coding_bugreport.md`
- **計画書**: `plans/20260715_vars_after_coding_fix.md`
- **ブランチ**: beta（コミット b9779ee = 1.04a7 からの修正）
- **方針**: 読み出し層の限定リファクタ（PB/SAT 変数空間の完全分離は不要と判明）

## 根本原因（調査で確定）

SAT レベルでは coding() 後に getVar した変数の制約も正しく CNF 化されており、
壊れていたのは**読み出し層のみ**だった。

1. **症状(1)** c が best_model / index2name に載らない【100%】:
   `commitBestModel()` が初回 `coding()` でスナップショットされた `pb_n_vars`
   （= その時点の `nVars()`）サイズでしか best_model を作らないため。
2. **症状(2)** goalvalue が 51⇔1 で非決定的にぶれる:
   `PbSolver_Minimize::evalGoal()` が `best_model[var(goal[i])]` を境界チェックなしで
   読んでおり、best_model.size() 以上の変数では **vec\<bool\> の範囲外読み
   （未初期化ヒープ参照）** になっていた。`best_goalvalue = evalGoal(*original_goal)`
   がこのゴミを拾い、cold-start 時のヒープ内容次第で 50c の寄与が消えていた。
   model_check / goalValueCheck / Main.C の model_check も同型の範囲外読みを持っていた。
3. **症状(3)** 2 回目 coding() で "no such variable index":
   再 coding 時に `pb_n_vars = nVars()` が Tseitin 補助変数（index2name 未登録）込みで
   再計算され `pb_n_vars > index2name.size()` になるため。さらに C++ 側にも
   `setupOccurs()` の n_occurs 範囲外読みと clausifier の無条件 new（リーク）があった。

## 修正内容

### C++ コア

| ファイル | 変更 |
|---|---|
| `PbSolver.h` | `vec<Var> named_vars`（getVar 生成順の SAT var 台帳）を追加。防御的アクセス `bestModelVal(Var)` / `bestModelTrue(Lit)`（範囲外は false）を追加。 |
| `PbSolver.C` getVar/freeVarNames | named_vars の push / clear。 |
| `PbSolver.C` commitBestModel | `best_model.growTo(nVars())` にフルサイズ化。`model()==ll_Undef` は false 扱い（CaDiCaL が内部で変数を追加するため assert は不当）。 |
| `PbSolver.C` evalGoal / model_check / goalValueCheck | best_model 直接添字を bestModelTrue に置換（範囲外読み排除）。 |
| `PbSolver.C` model_check verbose / outputResult v 行 / exportVar | `[0,pb_n_vars)` ループを named_vars ベースに置換（Tseitin 変数の NULL 名参照排除）。**v 行は既存の `@` スキップを維持**し「OPB 由来変数のみ出力」の競技規約を保存（内部変数は全て `@` プレフィックスであることを確認済み）。 |
| `PbSolver.C` setupOccurs | `n_occurs.growTo(nVars()*2)` を追加し `assert(nVars()==pb_n_vars)` を削除（再 coding 時の範囲外読み対策）。 |
| `PbSolver.C` coding ×2 箇所 | `if (clausifier == NULL)` ガード（再 coding のリーク防止・vmap 再利用）。 |
| `PbSolver.C` maximizeGoal の LB シード | goalExp の全変数が best_model 範囲内のときのみ evalGoal で LB 更新（normalizeGoal がリテラル反転するため、範囲外→false 扱いは maximize LB を過大評価し得る）。 |
| `PbSolver.C` PbSolver_Counting::solve / enumerate | freeze・ban・モデル出力を named_vars ベースに。手書きモデルコピーを `commitBestModel()` に統一。 |
| `Debug.C` dump(vec\<bool\>) | 無名変数の NULL 名ガード（`@unnamed` 表示）。 |
| `Main.C` model_check | bestModelVal 化（dead code だが範囲外読みのガードのみ実施、削除はせず）。 |

### Python バインディング（python/naps_bindings.cpp）

- `best_model` プロパティ: **named_vars 順（getVar 登録順）の dense リスト**に変更。
  Tseitin 等の無名変数は Python に露出しない。
- `index2name(i)`: i を **登録順インデックス**に変更（`best_model[i]` と常に同一空間）。
- 全変数を coding 前に登録する既存クライアントでは完全 identity（挙動不変）。
- docstring に「i は登録順で、getVar 戻り値（SAT 変数番号）とは coding 後登録の変数で
  一致しない」ことを明記。

### テスト追加

- gtest `tests/pb_solver_test.cpp`: `incremental_var_after_coding`
  （coding→solve 後に getVar("c") + c>=1 + goal 差し替え + convertPbs(0) + solve →
  goalvalue==51、best_model[c]==true）。output ヘルパ等の NULL 名参照も修正。
- Python `tests/python/test_pb_solver.py`:
  - `test_incremental_var_after_coding`（repro A ケースを 10 回反復、非決定性検出）
  - `test_incremental_var_before_coding`（回避策 B の継続動作）
  - `test_recoding_after_new_var`（**coding() 再呼び出し**が例外なしで 51 になる）

## 検証結果

| 項目 | 結果 |
|---|---|
| `make d` + `ctest --test-dir build_debug` | **3/3 スイート passed**（pb_solver_test 23 ケース、新規含む） |
| CLI 回帰（Examples/*.opb 47 件 + `-1`/`-A`×3/`-mc`×2、新旧デバッグバイナリの s/o/v 行比較） | **全 53 ケース完全一致**（magic4-ordered は実行 20 秒超のため個別に完全実行で一致確認） |
| `docs/repro/naps_vars_after_coding_dropped.py` | **exit 0**。goalvalue 分布 {51: 20}（20/20）、c が best_model に 20/20 回搭載 |
| pytest `tests/python/`（pyenv 3.9.20 + build_python） | **24 passed**（新規 3 テスト含む） |

### バグレポートのチェックリスト対応

- [x] `docs/repro/naps_vars_after_coding_dropped.py` が exit 0（c が毎回モデルに載り、goalvalue=51 安定）
- [ ] `bench/incremental_probe.py 3 example/ballade1/ballade1-106-108.mid` — **avs 側での検証項目**
      （naps リポジトリからは実行不可。修正版モジュール/wheel での再測定を依頼）
- [x] `coding()` の再呼び出しが `no such variable index` を出さない（`test_recoding_after_new_var`）

## 既知の制限（今回のスコープ外、変更なし）

1. **SCIP / coop-SCIP 経路**: coding() 後に追加した変数は SCIP へ伝搬されない
   （`PbSolver_scip.C` の `v >= pb_n_vars` ガードが後付け named 変数を補助変数扱いする）。
   SCIP 経路内部は `pb_n_vars` で整合しており範囲外読みはない。
2. **model_check の検査対象**: Python から `convertPbs(0)` で追加した制約は
   `minimization_mode==true` のため `constrs_bk` に退避されず、model_check の
   検査対象にならない（`PbSolver_convert.C` の退避条件）。
3. **2 回目 coding() のフル意味論**: 「エラーを出さない + repro が正しく動く」までを保証
   （propagate 再実行の副作用等の網羅検証はしていない）。
4. Python の `best_model` に、solve 中に内部生成される `@goal_constraint*` 等の
   named 変数が（commit 後）末尾に現れるようになった。従来どおり `@` プレフィックスで
   フィルタ可能。
5. incremental で goal を差し替えて再 solve すると
   `c Something is wrong! last_printed_goalvalue: ...` という診断が verbosity 1 で出る
   （o 行の単調性チェックによる既存の表示。今回の修正とは無関係の cosmetic な既知事項）。

## 配布

ユーザー指示により Version.h 更新（1.04a8）と wheel 再ビルドは未実施。
avs 側での検証時は `make py`（pybind11 のある Python を明示）または
`python/build_wheels.sh` で修正版を生成のこと。

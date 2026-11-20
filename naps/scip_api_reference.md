# SCIP API リファレンス (PbSolver_scip.C)

`PbSolver_scip.C` で利用されている SCIP API の一覧をカテゴリごとにまとめました。引数の役割（とくにコード内でどのように設定されているか）についても記載しています。

## 1. 環境と初期化・メモリ管理

| API名 | 概要 | 引数の役割 |
| --- | --- | --- |
| `SCIPcreate` | SCIPインスタンスの生成 | `scip`: 生成されたSCIPインスタンスへのポインタを受け取るためのポインタ（`SCIP**`）。 |
| `SCIPincludeDefaultPlugins` | 標準プラグインの読み込み | `scip`: プラグインを読み込む対象のSCIPインスタンス（`SCIP*`）。 |
| `SCIPfree` | SCIPインスタンスの解放とクリーンアップ | `scip`: 解放するSCIPインスタンスのポインタのアドレス（`SCIP**`）。 |
| `SCIPfreeTransform` | 変換後空間の解放（元の問題空間に戻す） | `scip`: 対象のSCIPインスタンス（`SCIP*`）。目的関数や制約の大幅な変更前に呼び出される。 |

## 2. 変数の操作

| API名 | 概要 | 引数の役割 |
| --- | --- | --- |
| `SCIPcreateVarBasic` | 変数の作成 | `scip`: インスタンス。<br>`var`: 作成された変数のポインタの格納先。<br>`name`: 変数の名前。<br>`lb`, `ub`: 変数の下限と上限（NaPSでは通常0.0と1.0）。<br>`obj`: 目的関数の係数。<br>`vartype`: 変数の型（NaPSでは `SCIP_VARTYPE_BINARY`）。 |
| `SCIPaddVar` | 変数を問題に追加 | `scip`: インスタンス。<br>`var`: 追加する変数（`SCIP_VAR*`）。 |
| `SCIPreleaseVar` | 変数の参照カウンタを減らす | `scip`: インスタンス。<br>`var`: 解放する変数のポインタのアドレス。メモリリークを防ぐために呼ばれる。 |
| `SCIPchgVarObj` | 変数の目的関数係数を変更 | `scip`: インスタンス。<br>`var`: 対象の変数。<br>`newobj`: 新たに設定する目的関数の係数値。 |
| `SCIPvarGetObj` | 変数の目的関数係数を取得 | `var`: 対象の変数。現在の係数値を返す。 |
| `SCIPchgVarLbGlobal` | 変数のグローバルな下限を変更 | `scip`: インスタンス。<br>`var`: 対象の変数。<br>`newbound`: 新しい下限値。SAT側で1に固定された場合などに1.0に設定される。 |
| `SCIPchgVarUbGlobal` | 変数のグローバルな上限を変更 | `scip`: インスタンス。<br>`var`: 対象の変数。<br>`newbound`: 新しい上限値。SAT側で0に固定された場合などに0.0に設定される。 |
| `SCIPvarGetLbGlobal` | 変数のグローバルな下限を取得 | `var`: 対象の変数。現在のグローバルな下限値を返す。 |
| `SCIPvarGetUbGlobal` | 変数のグローバルな上限を取得 | `var`: 対象の変数。現在のグローバルな上限値を返す。 |

## 3. 制約の操作

| API名 | 概要 | 引数の役割 |
| --- | --- | --- |
| `SCIPcreateConsBasicLinear`| 空の線形制約を生成 | `scip`: インスタンス。<br>`cons`: 生成された制約のポインタの格納先。<br>`name`: 制約名。<br>`nvars`: 初期変数数（NaPSでは0から開始）。<br>`vars`, `vals`: 初期変数と係数の配列（NaPSではnullptr）。<br>`lhs`, `rhs`: 制約の左辺（下限）と右辺（上限）。 |
| `SCIPaddCoefLinear` | 線形制約に変数の項（係数）を追加 | `scip`: インスタンス。<br>`cons`: 対象の線形制約。<br>`var`: 追加する変数。<br>`val`: 変数の係数。 |
| `SCIPchgLhsLinear` | 既存の線形制約の左辺（下限）を変更 | `scip`: インスタンス。<br>`cons`: 対象の線形制約。<br>`lhs`: 新しい下限値。 |
| `SCIPchgRhsLinear` | 既存の線形制約の右辺（上限）を変更 | `scip`: インスタンス。<br>`cons`: 対象の線形制約。<br>`rhs`: 新しい上限値。 |
| `SCIPaddCons` | 制約を問題に追加 | `scip`: インスタンス。<br>`cons`: 追加する制約。 |
| `SCIPdelCons` | 制約を問題から削除 | `scip`: インスタンス。<br>`cons`: 削除する制約。仮定制約や過去のヒント制約の破棄などに使われる。 |
| `SCIPreleaseCons` | 制約の参照カウンタを減らす | `scip`: インスタンス。<br>`cons`: 解放する制約のポインタのアドレス。 |

## 4. 問題の設定・パラメータ

| API名 | 概要 | 引数の役割 |
| --- | --- | --- |
| `SCIPcreateProbBasic` | 空の最適化問題を作成 | `scip`: インスタンス。<br>`name`: 問題の名前（NaPSでは "NapsProb" など）。 |
| `SCIPsetObjsense` | 目的関数の方向を設定 | `scip`: インスタンス。<br>`objsense`: `SCIP_OBJSENSE_MINIMIZE` (最小化) または `SCIP_OBJSENSE_MAXIMIZE` (最大化)。 |
| `SCIPsetObjlimit` | 目的関数値のカットオフ境界を設定 | `scip`: インスタンス。<br>`objlimit`: カットオフ境界となる値。探索の枝刈りに使われる。 |
| `SCIPinfinity` | SCIP上の「無限大」の値を返す | `scip`: インスタンス。戻り値として無限大の `double` 値を得る。 |
| `SCIPisInfinity` | 値が無限大として扱われるか判定 | `scip`: インスタンス。<br>`val`: 判定したい値。 |
| `SCIPsetBoolParam` | bool型のパラメータを設定 | `scip`: インスタンス。<br>`name`: パラメータ名（例: "misc/catchctrlc"）。<br>`value`: 設定値（TRUE / FALSE）。 |
| `SCIPsetIntParam` | int型のパラメータを設定 | `scip`: インスタンス。<br>`name`: パラメータ名（例: "display/verblevel"）。<br>`value`: 設定値。 |
| `SCIPsetRealParam` | double型のパラメータを設定 | `scip`: インスタンス。<br>`name`: パラメータ名（例: "limits/time"）。<br>`value`: 設定値。 |

## 5. 探索の実行と中断

| API名 | 概要 | 引数の役割 |
| --- | --- | --- |
| `SCIPsolve` | 最適化（探索やプレソルビング）を実行 | `scip`: 探索を実行するSCIPインスタンス。 |
| `SCIPinterruptSolve` | 実行中の最適化プロセスを中断 | `scip`: 中断させるSCIPインスタンス。NaPSからの割り込み時に呼ばれる。 |

## 6. 解と境界の取得

| API名 | 概要 | 引数の役割 |
| --- | --- | --- |
| `SCIPgetStatus` | SCIPの現在のステータスを取得 | `scip`: インスタンス。戻り値で `SCIP_STATUS_OPTIMAL` や `SCIP_STATUS_INFEASIBLE` などを返す。 |
| `SCIPgetBestSol` | これまでに見つかった最良の解を取得 | `scip`: インスタンス。解のポインタ（`SCIP_SOL*`）を返す。 |
| `SCIPgetSolVal` | 解における指定した変数の値を取得 | `scip`: インスタンス。<br>`sol`: 取得対象の解。<br>`var`: 値を知りたい変数。 |
| `SCIPgetPrimalbound` | 現在の主境界（Primal Bound）を取得 | `scip`: インスタンス。見つかっている最良解の目的関数値を返す。 |
| `SCIPgetDualbound` | 現在の双対境界（Dual Bound）を取得 | `scip`: インスタンス。理論上の最適解の限界値（下界または上界）を返す。 |

## 7. イベントハンドラ

| API名 | 概要 | 引数の役割 |
| --- | --- | --- |
| `SCIPincludeEventhdlr` | カスタムイベントハンドラを追加 | `scip`: インスタンス。<br>`name`: ハンドラ名。<br>`desc`: 説明文。<br>他引数: コールバック関数群や登録するユーザーデータ（NaPSでは `PbSolver*` のインスタンス自身）。 |
| `SCIPcatchEvent` | 特定のイベントの捕捉を開始 | `scip`: インスタンス。<br>`eventtype`: 捕捉するイベント（例: `SCIP_EVENTTYPE_BESTSOLFOUND`）。<br>`eventhdlr`: ハンドラの実体。 |
| `SCIPdropEvent` | イベントの捕捉を終了 | 引数は `SCIPcatchEvent` とほぼ同様。不要になったイベントフックを解除する。 |
| `SCIPeventhdlrGetData` | ハンドラに登録したユーザーデータを取得 | `eventhdlr`: イベントハンドラ。初期化時に登録した `PbSolver` インスタンスを取得するために利用。 |

## 8. メッセージハンドラ

| API名 | 概要 | 引数の役割 |
| --- | --- | --- |
| `SCIPmessagehdlrCreate` | カスタムメッセージハンドラを生成 | `messagehdlr`: 生成されるハンドラのポインタの格納先。<br>他引数: ログのレベル別コールバック関数（info, warning, dialog 等）や、登録するユーザーデータ（NaPSでは `Report*` インスタンス）。 |
| `SCIPsetMessagehdlr` | 生成したハンドラをSCIPに適用 | `scip`: インスタンス。<br>`messagehdlr`: 適用するハンドラ。 |
| `SCIPmessagehdlrRelease` | メッセージハンドラを解放 | `messagehdlr`: 解放するハンドラのポインタのアドレス。 |
| `SCIPmessagehdlrGetData` | ハンドラに登録したユーザーデータを取得 | `messagehdlr`: ハンドラ。ログ出力処理内で、登録された `Report` インスタンスを取得して NaPS フォーマットで出力するために利用。 |

# SCIP協調モード: coop_goal 不変時の探索継続

## Context

SCIP協調モードにおいて、`solveScip()` はタイムアウト後に再呼び出しされるたびに
L502 で `SCIPfreeTransform()` を呼んでいる。これによりSCIPの探索状態（B&Bツリー、
学習カット、LP緩和情報）が全て破棄され、次回呼び出し時に前処理から再開される。

しかし SCIP は、問題が変化していなければ `SCIPsolve()` を再度呼ぶことでタイムアウト前の
状態から**継続**して探索する設計になっている。`coop_goal` ポインタが変わっていない状態
（＝最小化の同じ段階）では、SCIPの探索状態を保持したまま次の呼び出しで継続できる。

## 現状分析

`solveScip()` 終盤（L501-554）の現在のフロー:
```
SCIPsolve(scip)                          // L414
→ 結果抽出                               // L417-499
→ SCIPfreeTransform(scip)               // L502-504 ← 状態を毎回破棄
→ 固定変数抽出 (SCIPvarGetLbGlobal)      // L506-541
→ assump_constrs 削除                    // L543-547
→ ub_hint_cons 削除                      // L548-551
```

スキップ可能な条件（以下が全て真のとき）:
1. `coop_goal != nullptr` かつ `scip_last_goal == (void*)coop_goal`（目的が変わっていない）
2. `assump_ps.size() == 0`（協調モードでは常に空）
3. `initScipForCoop()` が新制約を追加しなかった（`scip_constrs_added` 変化なし）

## 実装方針

### 新メンバー変数（PbSolver.h L219 の後）

```cpp
bool    scip_in_transformed_state;  // SCIPfreeTransformをスキップした状態
void*   scip_ub_hint_cons;          // 協調モード用の永続UBヒント制約
double  scip_last_lb_hint_scip;     // 前回SCIPsetObjlimitに渡した値(NaN=未設定)
```

コンストラクタ初期化リストにも追加（`scip_event_fired` の近く）。

### freeScip()（L121-136）

`scip_ub_hint_cons` を保持する場合があるので、解放処理を追加:
```cpp
if (scip_ub_hint_cons) {
    SCIP_CONS* c = (SCIP_CONS*)scip_ub_hint_cons;
    SCIPdelCons(scip, c);
    SCIPreleaseCons(scip, &c);
    scip_ub_hint_cons = nullptr;
}
scip_in_transformed_state = false;
```

### initScipForCoop()（L211-251 の制約同期ループ直前）

transformed 状態で新制約が発生した場合は先に `SCIPfreeTransform()`:
```cpp
int sync_limit = (int)constrs.size();
if (scip_in_transformed_state && scip_constrs_added < sync_limit) {
    SCIP* scip = (SCIP*)scip_env;
    SCIPfreeTransform(scip);
    scip_in_solved_state = false;
    scip_last_lb_hint_scip = std::numeric_limits<double>::quiet_NaN();
}
for (; scip_constrs_added < sync_limit; scip_constrs_added++) { ... }
```

### solveScip() の変更（L380 付近からL554）

#### スキップ条件判定（L380 直前に追加）

```cpp
bool can_skip_free_transform =
    scip_in_transformed_state &&
    (coop_goal != nullptr) &&
    (scip_last_goal == (void*)coop_goal) &&
    (assump_ps.size() == 0);
// ↑ initScipForCoop() 内で新制約があれば既に scip_in_transformed_state = false に戻っている
```

#### 目的係数設定（L312-360）

既存の `scip_last_goal` キャッシュで `can_skip_free_transform == true` のときは
すでにスキップされる。変更不要。

#### lb_hint の設定（L390-392 付近）

`SCIPsetObjlimit` は SOLVING ステージでも呼べるが値を「緩める」方向は禁止:
```cpp
if (coop_goal != nullptr && lb_hint != Int_MIN) {
    double new_lb_scip = todouble(lb_hint) - scip_coop_offset;
    if (can_skip_free_transform &&
        !std::isnan(scip_last_lb_hint_scip) &&
        new_lb_scip < scip_last_lb_hint_scip) {
        // lb が下がった（緩和方向）→ スキップ不可にして SCIPfreeTransform を強制
        can_skip_free_transform = false;
    }
    SCIPsetObjlimit(scip, new_lb_scip);
    scip_last_lb_hint_scip = new_lb_scip;
}
```

#### ub_hint 制約の管理（L396-409 を置き換え）

ub_hint を永続制約（`scip_ub_hint_cons`）として管理する:

```cpp
if (coop_goal != nullptr && ub_hint != Int_MAX) {
    double scip_ub = todouble(ub_hint) - scip_coop_offset;
    if (!can_skip_free_transform) {
        // 非スキップパス: 古い永続制約を削除し新規作成
        if (scip_ub_hint_cons != nullptr) {
            SCIP_CONS* c = (SCIP_CONS*)scip_ub_hint_cons;
            SCIPdelCons(scip, c);
            SCIPreleaseCons(scip, &c);
            scip_ub_hint_cons = nullptr;
        }
        SCIP_CONS* cons = nullptr;
        SCIPcreateConsBasicLinear(scip, &cons, "coop_ub_hint",
                                  0, nullptr, nullptr, -SCIPinfinity(scip), scip_ub);
        for (int j = 0; j < coop_goal->size; j++) { /* 係数追加 (既存コードと同じ) */ }
        SCIPaddCons(scip, cons);
        scip_ub_hint_cons = (void*)cons;
        // SCIPreleaseCons は ここでは呼ばない (永続管理)
    } else {
        // スキップパス: RHSのみ更新（係数は coop_goal が同じなので変わらない）
        SCIP_CONS* c = (SCIP_CONS*)scip_ub_hint_cons;
        if (c != nullptr) {
            SCIPchgRhsLinear(scip, c, scip_ub);
        }
        // c == nullptr は scip_in_transformed_state が false のときしか発生しない
        // → can_skip_free_transform == true ならこの分岐に来ない
    }
} else if (scip_ub_hint_cons != nullptr) {
    // ub_hint が不要になった → 永続制約を削除
    SCIP_CONS* c = (SCIP_CONS*)scip_ub_hint_cons;
    SCIPdelCons(scip, c);
    SCIPreleaseCons(scip, &c);
    scip_ub_hint_cons = nullptr;
}
```

#### SCIPsolve 後の処理（L502-551 を置き換え）

```cpp
// L502-504 の SCIPfreeTransform をこれで置き換え:
if (can_skip_free_transform) {
    scip_in_transformed_state = true;
    report->reportf(2, "Skipping SCIPfreeTransform: SCIP search continues from previous state\n");
} else {
    if (SCIPfreeTransform(scip) != SCIP_OKAY)
        throw std::runtime_error("Failed to free SCIP transformed problem space");
    scip_in_transformed_state = false;
    scip_last_lb_hint_scip = std::numeric_limits<double>::quiet_NaN();
}

// 固定変数抽出 (L506-541): 変更なし（SCIPvarGetLbGlobal はステージ非依存）

// assump_constrs 削除 (L543-547): 変更なし（協調モードでは空）

// ub_hint_cons 削除 (L548-551): 永続管理するので削除しない
// → L548-551 を削除
```

## 重要なリスクと注意点

1. **`SCIPchgRhsLinear` in SOLVING stage**: SCIP内部の `chgRhs` が `consdata->row != NULL`
   のとき `SCIPchgRowRhs` も呼び LP行を更新する。SOLVING 段階でも動作するが、LP を
   再解するコストが発生する可能性がある。性能面では許容範囲。

2. **`SCIPsetObjlimit` の緩和禁止**: SOLVING ステージでは objlimit を「緩める」
   （値を小さくする MAX 問題での下界を下げる）と `SCIP_INVALIDDATA` になる。
   `lb_hint` が下がるケースを検出して強制 `SCIPfreeTransform` するガードが必要。

3. **`scip_ub_hint_cons` のリリース管理**: 現在は `SCIPreleaseCons` を
   `SCIPdelCons` 直後に呼んでいたが、永続管理では削除のタイミングが変わる。
   `freeScip()` での必須リリースを忘れないこと。

4. **`enumerateScip()`（L556-629）への影響なし**: こちらは `coop_goal == nullptr`
   で呼ばれるため、スキップ条件を満たさず既存の動作を維持する。

## 実装中に発見・対処したバグ

### バグ1: `can_skip_free_transform` の計算タイミング誤り

**問題:** 当初の設計では `can_skip_free_transform` を goal 設定ブロックの**後**に計算していた。
しかし、goal 設定ブロック内で `scip_last_goal = (void*)coop_goal` が更新されるため、
設定後に `scip_last_goal == (void*)coop_goal` を比較すると**常に true** になってしまい、
「目的が前回と同じ」という判定ができなくなる。

**根本原因:** `scip_last_goal` は「前回のゴールをキャッシュ」する変数だが、
goal 設定ブロックが走った後は現在の `coop_goal` と同じ値になるため、
設定後の比較では初回呼び出しと2回目以降を区別できない。

**修正:** `can_skip_free_transform` を goal 設定ブロックの**前**（`scip_last_goal` が
前回の値を保持している状態）で計算するよう移動。これにより：
- 初回呼び出し: `scip_last_goal == nullptr` ≠ `coop_goal` → `can_skip = false` → `SCIPfreeTransform()` 実行
- 2回目以降（同一ゴール）: `scip_last_goal == (void*)coop_goal` → `can_skip = true` → スキップ

また、当初 `can_skip_free_transform` の条件に `scip_in_transformed_state` を含めていたが、
これも不要かつ有害だった（`scip_in_transformed_state` は `can_skip` が true のときのみ
true になる循環依存があり、条件を外さない限り永遠に true にならない）。

### バグ2: `lb_hint` 緩和チェックの誤発火

**問題:** lb_hint（SCIP obj units 換算値）が前回より小さいかどうかを比較して
`SCIPfreeTransform()` を強制するガード処理を実装したが、このチェックが誤って
毎回発火していた。

**根本原因:** 初回呼び出し（`can_skip = false`）の後、`SCIPfreeTransform()` が実行され
`scip_last_lb_hint_scip = 0.0` にリセットされる。2回目の呼び出しでは
`can_skip = true` になるが、`scip_coop_offset` が大きい（例: 100）場合、
`new_lb_scip = lb_hint - offset = 56 - 100 = -44` となり、
リセット値の `0.0` より小さいため「緩和」と誤判定される。

**修正:** チェック条件に `scip_in_transformed_state` を追加：
```cpp
if (can_skip_free_transform && scip_in_transformed_state && new_lb_scip < scip_last_lb_hint_scip)
```
SCIPfreeTransform を直前に呼んだ場合（`scip_in_transformed_state = false`）は
SCIP が PROBLEM ステージにあり `SCIPsetObjlimit` の制限がないため、チェック不要。
SCIPfreeTransform をスキップした場合（`scip_in_transformed_state = true`）のみ
チェックすることで誤発火を防ぐ。

## 変更対象ファイル

- [PbSolver.h](PbSolver.h) L219付近（新メンバー変数）、コンストラクタ初期化
- [PbSolver_scip.C](PbSolver_scip.C)
  - L121-136: `freeScip()` へのリリース処理追加
  - L211-252: `initScipForCoop()` へのガード追加
  - L380-554: `solveScip()` 後半の条件分岐化

## 検証方法

1. **スキップ確認**: `-v2` で `Skipping SCIPfreeTransform` ログが出ること
2. **B&Bノード蓄積確認**: SCIP の表示で B&B ノード数が呼び出しをまたいで増え続けること
3. **最適解の正当性**: `make d` でビルドし `ctest -V` で既存テスト全通過
4. **ベンチマーク比較**: 協調モード（`-cSCIP` または `-scip-ub`）を有効にして
   典型問題の LB/UB 改善速度が向上していること
5. **ASAN チェック**: `make d` ビルド（ASAN 付き）で `scip_ub_hint_cons` の
   二重解放・リークがないこと

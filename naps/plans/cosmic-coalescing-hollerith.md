# 計画：SCIPメッセージ行頭への "c " プレフィックス付与

## Context

`-SCIP -v2` などで実行すると、SCIPが生成するメッセージが stdout に直接出力される。
naps の他の出力（Report クラス経由）はすべて行頭に "c " を付けるが、SCIP メッセージだけは素の stdout 出力になっており、出力形式が統一されていない。

## 現状の理解

- `Report.C:10,49` — Report クラスは `reportPrefix = "c "` を使い、すべての行頭に "c " を付ける
- `Report.C:65-73` — `reportf(int level, ...)` は `level > reportLevel` なら何もしない
- `PbSolver.h:54` — `PbSolver` は `Report* report` を持つ
- `PbSolver_scip.C:78-80` — `verbosity < 2` のとき `display/verblevel=0` でSCIP出力を抑制
- `PbSolver_scip.C:208` — `SCIPsolve()` を呼ぶとSCIPが直接 stdout へメッセージを出力
- `PbSolver_scip.C:37-38` — イベントハンドラ内の `printf("o %s\n", ...)` は既に正しい形式（変更不要）

## 方針

SCIPの `SCIPmessagehdlrCreate` + `SCIPsetMessagehdlr` API でカスタムメッセージハンドラを登録する。
ハンドラのコールバックでは **`report->reportf(2, "%s", msg)`** を呼ぶことで：
- `Report` クラスが "c " プレフィックスを各行頭に付ける（既存機能の再利用）
- `reportf` のレベルフィルタ（level 2）が verbosity < 2 のとき二重に抑制

`messagehdlrdata` に `Report*` を渡してコールバック内から参照する。

## 変更ファイル

`PbSolver_scip.C` のみ（`#ifdef USE_SCIP` ブロック内）

## 実装詳細

### 追加する静的コールバック関数（ファイル先頭付近、`#ifdef USE_SCIP` 直後）

```cpp
static SCIP_DECL_MESSAGEINFO(scipMessageInfo) {
    (void)file;
    Report* rep = (Report*)SCIPmessagehdlrGetData(messagehdlr);
    rep->reportf(2, "%s", msg);
}

static SCIP_DECL_MESSAGEWARNING(scipMessageWarning) {
    (void)file;
    Report* rep = (Report*)SCIPmessagehdlrGetData(messagehdlr);
    rep->reportf(2, "%s", msg);
}

static SCIP_DECL_MESSAGEDIALOG(scipMessageDialog) {
    (void)file;
    Report* rep = (Report*)SCIPmessagehdlrGetData(messagehdlr);
    rep->reportf(2, "%s", msg);
}
```

### `solveScip()` 内の変更

`SCIPincludeDefaultPlugins(scip);` と `SCIPcreateProbBasic(...)` の間に以下を追加：

```cpp
// Register custom message handler to prefix SCIP output with "c "
SCIP_MESSAGEHDLR* msghdlr = nullptr;
SCIPmessagehdlrCreate(&msghdlr,
    FALSE,              // bufferedoutput
    nullptr,            // filename (use stdout)
    FALSE,              // quiet
    scipMessageWarning,
    scipMessageDialog,
    scipMessageInfo,
    nullptr,            // messagehdlrfree
    (SCIP_MESSAGEHDLRDATA*)report
);
SCIPsetMessagehdlr(scip, msghdlr);
SCIPmessagehdlrRelease(&msghdlr);
```

## 検証方法

```bash
make d
./naps_debug -v2 -SCIP Examples/test.opb 2>&1 | grep -v "^c " | grep -v "^s " | grep -v "^o " | head
# → 何も出ない（SCIPメッセージがすべて "c " 始まりになっている）

./naps_debug -v1 -SCIP Examples/test.opb
# → SCIPメッセージが引き続き抑制されること確認
```

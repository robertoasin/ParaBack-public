# SCIP_STATUS 表示テーブルの SCIP バージョン非依存化

## Context

SCIP 10.0.0 で `SCIP_STATUS` enum の整数値が全面的に変更された（公式 CHANGELOG 明記）。

| 定数 | 9.2.2 | 10.0.2 |
|------|-------|--------|
| SCIP_STATUS_OPTIMAL | 11 | 1 |
| SCIP_STATUS_INFEASIBLE | 12 | 2 |
| SCIP_STATUS_INFORUNBD | 14 | 4 |
| SCIP_STATUS_TIMELIMIT | 5 | 23 |
| SCIP_STATUS_NODELIMIT | 2 | 20 |

現状の `SCIP_STATUS_strs[]` 配列（[PbSolver_scip.C:12-31](PbSolver_scip.C#L12-L31)）は
整数インデックスで文字列を引くため、SCIP 10.0.2 でビルドすると
例えば OPTIMAL（値=1）に対して `SCIP_STATUS_strs[1]` = "USERINTERRUPT" が表示される。

**機能的影響はない**（`status == SCIP_STATUS_OPTIMAL` 等の比較はコンパイル時に
ヘッダから enum 定数値を解決するため、どちらのバージョンでも正しく動作する）。
ただし verbosity=2 の `SCIP status: N (...)` ログが誤った名前を表示する。

## 修正方針

`SCIP_STATUS_strs[]` と添字アクセスを削除し、インライン `switch` に置き換える。
enum 定数ベースなのでどの SCIP バージョンでも正しい文字列が返る。

## 変更ファイル

[PbSolver_scip.C](PbSolver_scip.C)

### Step 1: SCIP_STATUS_strs 配列を削除（行 12-31）

```c
// 削除対象
const char* SCIP_STATUS_strs[] = {
    "UNKNOWN",          //  0
    ...
    "DUALLIMIT",        // 17
};
```

### Step 2: 配列参照箇所（行 618-621）を switch に置き換え

```c
// 変更前
int status_i = (int)status;
const char* status_str = (status_i >= 0 && status_i < (int)(sizeof(SCIP_STATUS_strs)/sizeof(SCIP_STATUS_strs[0])))
    ? SCIP_STATUS_strs[status_i] : "UNKNOWN_STATUS";
report->reportf(2, "SCIP status: %d (%s)\n", status_i, status_str);

// 変更後
auto scipStatusStr = [](SCIP_STATUS s) -> const char* {
    switch (s) {
        case SCIP_STATUS_UNKNOWN:        return "UNKNOWN";
        case SCIP_STATUS_USERINTERRUPT:  return "USERINTERRUPT";
        case SCIP_STATUS_NODELIMIT:      return "NODELIMIT";
        case SCIP_STATUS_TOTALNODELIMIT: return "TOTALNODELIMIT";
        case SCIP_STATUS_STALLNODELIMIT: return "STALLNODELIMIT";
        case SCIP_STATUS_TIMELIMIT:      return "TIMELIMIT";
        case SCIP_STATUS_MEMLIMIT:       return "MEMLIMIT";
        case SCIP_STATUS_GAPLIMIT:       return "GAPLIMIT";
        case SCIP_STATUS_SOLLIMIT:       return "SOLLIMIT";
        case SCIP_STATUS_BESTSOLLIMIT:   return "BESTSOLLIMIT";
        case SCIP_STATUS_RESTARTLIMIT:   return "RESTARTLIMIT";
        case SCIP_STATUS_OPTIMAL:        return "OPTIMAL";
        case SCIP_STATUS_INFEASIBLE:     return "INFEASIBLE";
        case SCIP_STATUS_UNBOUNDED:      return "UNBOUNDED";
        case SCIP_STATUS_INFORUNBD:      return "INFORUNBD";
        case SCIP_STATUS_TERMINATE:      return "TERMINATE";
        case SCIP_STATUS_PRIMALLIMIT:    return "PRIMALLIMIT";
        case SCIP_STATUS_DUALLIMIT:      return "DUALLIMIT";
        default:                         return "UNKNOWN_STATUS";
    }
};
report->reportf(2, "SCIP status: %d (%s)\n", (int)status, scipStatusStr(status));
```

## 確認方法

- `make d` でビルドが通ること
- `-v2` オプション付きで実行し `SCIP status: N (OPTIMAL)` 等が正しく表示されること
- SCIP 10.0.2 の別マシンで同様に確認（ビルド後、ログの status 文字列が正しいこと）

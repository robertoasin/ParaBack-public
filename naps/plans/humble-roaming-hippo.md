# 計画: dump 関数に trunc_len オーバーライド引数を追加

## Context

`dump` 関数群は現在 `config->report_trunc_len` を固定で参照して出力を打ち切る。
呼び出し側が一時的に別の打ち切り長を指定したい場面（詳細ダンプ・テスト等）に対応するため、
最終引数 `int trunc_len = -1` を追加し、`-1` のときは従来通り `config->report_trunc_len` を、
0 以上が指定されたときはその値を優先して使うように変更する。

---

## 対象関数（11 個）

`report_trunc_len` を参照している dump 関数（Debug.C / Debug.h）：

| 関数シグネチャ（省略形）                                               |
|---------------------------------------------------------------------|
| `dump(Config*, int, const vec<int>&)`                               |
| `dump(Config*, int, const vec<Int>&)`                               |
| `dump(Config*, int, index2name, const vec<bool>&)`                  |
| `dump(Config*, int, index2name, const vec<Lit>&)`                   |
| `dump(Config*, int, index2name, const vec<Lit>&, const vec<Int>&)`  |
| `dump(Config*, int, index2name, const vec<Pair<Int,Lit>>&)`         |
| `dump(Config*, int, const vec<Formula>&)`                           |
| `dump(Config*, int, const vec<Formula>&, const vec<Int>&)`          |
| `dump(Config*, int, index2name, const vec<Lit>&, const vec<Int>&, const vec<int>& assigns)` |
| `dump(Config*, int, index2name, const Linear&)`                     |
| `dump(Config*, int, index2name, const Linear&, const vec<int>& assigns)` |

---

## 変更方針

### センチネル値

`-1` を「config 値を使用」の意味とする。
- `report_trunc_len` は非負整数（0 = 全件表示）なので `-1` は曖昧さなし
- 呼び出し側で `0` を渡せば「全件強制表示」も可能（`truncSize` の既存ロジックと整合）

### 各 dump 関数での処理

```cpp
// 末尾引数の有効値を決定するヘルパー（Debug.C 内ローカル）
inline int effectiveTruncLen(const Config* config, int trunc_len) {
    return (trunc_len >= 0) ? trunc_len : config->report_trunc_len;
}
```

各 dump 関数で `config->report_trunc_len` を渡していた箇所を次のように変更：

```cpp
// Before
int sz = truncSize(base.size(), config->report_trunc_len);
// After
int sz = truncSize(base.size(), effectiveTruncLen(config, trunc_len));
```

`truncSize` 自体は変更不要（現状のまま）。

---

## ファイル別変更内容

### 1. `Debug.C`

1. `effectiveTruncLen` ヘルパー関数を `truncSize` の直下に追加（ファイル内ローカル）
2. 11 個の dump 関数に `int trunc_len = -1` を最終引数として追加
3. 各関数内の `config->report_trunc_len` を `effectiveTruncLen(config, trunc_len)` に置換

### 2. `Debug.h`

1. 11 個の関数宣言に `int trunc_len = -1` を追加
2. 対応するマクロ（2引数短縮版）にも同様に `int trunc_len = -1` を追加し、内部呼び出しへ転送

   例：
   ```cpp
   // Before
   macro void dump(Config* config, const vec<int>& base) { dump(config, 2, base); }
   // After
   macro void dump(Config* config, const vec<int>& base, int trunc_len = -1) { dump(config, 2, base, trunc_len); }
   ```

---

## 変更しないもの

- `truncSize` 関数（変更不要）
- `report_trunc_len` を使わない dump 関数（`Int num`, `Lit p`, `Formula f`）
- 呼び出し側の既存コード（デフォルト引数により全件互換）
- 外部ライブラリ（`cadical3/`, `kissat/`, `glueminisat/`）

---

## 検証方法

1. `make d` でビルドエラーなし
2. `cd build_debug && ctest -V` で全テスト通過
3. 手動確認: `dump(config, level, vec, 5)` のように引数を渡した場合に 5 件で打ち切られることをデバッグ出力で確認

# Plan: Int(double x) コンストラクタの追加

## Context
SCIP との連携で `double` 型の目的関数値（例: 123.002, 122.955）を `Int` 型に変換する必要がある。
整数値に近い `double` を最近傍整数に丸めたいため、`round()` を使うコンストラクタを追加する。

## 変更箇所

ファイル: [ADTs/Int.h](ADTs/Int.h)

GMP版クラス（`#else` ブロック内、159行目付近）の既存コンストラクタ群の後に追加：

```cpp
  Int(double x) {
    data = xmalloc<mpz_t>(1); assert(((intp)data & 1) == 0);
    mpz_set_d(*data, round(x));
    // mpz_init は不要 — mpz_set_d の前に mpz_init_set_d を使う
  }
```

※ `mpz_init` + `mpz_set_d` のパターンに合わせて正しくは：

```cpp
  Int(double x) {
    data = xmalloc<mpz_t>(1); assert(((intp)data & 1) == 0);
    mpz_init(*data);
    mpz_set_d(*data, round(x));
  }
```

`round()` は `<cmath>` 由来（ゼロから遠い方向への四捨五入）。`<cmath>` は既に他のヘッダ経由でインクルード済みか確認済み。

## 配置位置

[ADTs/Int.h:159-163](ADTs/Int.h#L159) の `Int(const char* str)` コンストラクタの直後に挿入する。

## 検証

```cpp
Int a(123.002);  // → 123
Int b(122.955);  // → 123
Int c(-1.5);     // → -2 (round はゼロから遠い方向)
```

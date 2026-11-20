# FreeBSD 静的ビルドでの ABI 不一致（libstdc++ / libc++）解消

## Context（背景・目的）

FreeBSD 上で `gmake rs` を実行すると、`std::__1::cerr` などの libc++
シンボルが大量に未定義参照となりリンクに失敗する
（[report/20260721_make_scip_static.md](../report/20260721_make_scip_static.md) の
続き。今回はビルド自体ではなく、既に導入済みの `scip-static` 自動ビルド経路で
発生した ABI 不一致を修正する）。

### 原因

- naps 本体は [CMakeLists.txt:9-19](../CMakeLists.txt#L9-L19) により、
  FreeBSD では `/usr/local/bin/g++[0-9]*` の最新版（例: `g++15`）を自動選択する。
  GCC は標準ライブラリとして **libstdc++**（シンボル名前空間 `std::`）を使う。
- 一方 [build_scip_static.sh](../build_scip_static.sh) はコンパイラを一切指定せず
  素の `cmake` を呼んでいるため、`scip_static/lib/libscip.a` /
  `libsoplex.a` は FreeBSD のシステムデフォルトコンパイラ（clang）でビルドされる。
  clang の標準ライブラリは **libc++**（シンボル名前空間 `std::__1::`）。
- `make rs` は `--static` で完全静的リンクするため、libc++ の実体はリンクグラフに
  含まれず、libscip.a / libsoplex.a が要求する `std::__1::...` シンボルが
  未定義参照になる。

## 確定した方針

`build_scip_static.sh` に、naps 本体（CMakeLists.txt）と同じコンパイラ選択ロジック
（FreeBSD: `/usr/local/bin/g++[0-9]*` の最新版、macOS: `/opt/homebrew/bin/g++-[0-9]*`
または `/usr/local/bin/g++-[0-9]*` の最新版、それ以外: `g++`）を bash で踏襲し、
SCIP 自身の cmake 呼び出しに `-DCMAKE_CXX_COMPILER` / `-DCMAKE_C_COMPILER` として
明示的に渡す。これにより naps 本体と SCIP/SoPlex が常に同じ標準ライブラリ実装で
ビルドされることを保証する。

`USE_SYSTEM_COMPILER`（既存オプション）が設定されている場合は、naps 本体と同様に
コンパイラ上書きをせず cmake のデフォルト（システムのデフォルトコンパイラ）に任せる。
現状 `make scip-static` 呼び出し時にこの環境変数が伝播していないため、
Makefile 側も合わせて修正する。

## 変更内容

### 1. [build_scip_static.sh](../build_scip_static.sh)

- 冒頭（ダウンロード・展開の前後、cmake 呼び出しの前）に、CMakeLists.txt の
  8〜33行目のロジックと同等の bash 版コンパイラ選択処理を追加。
  `USE_SYSTEM_COMPILER` が空でなければスキップ。
- 選択結果を `SCIP_CMAKE_COMPILER_OPTS` のような変数に積み、
  既存の `cmake -S ... -B ...` 呼び出しの引数に追加する。

### 2. [Makefile](../Makefile)

- `scip-static:` ターゲットの呼び出し行に `USE_SYSTEM_COMPILER="$(USE_SYSTEM_COMPILER)"`
  を追加し、`make rs USE_SYSTEM_COMPILER=1` 等でも一貫した経路になるようにする。

## スコープ外

- 既存の `ci/build_static_scip.sh`（cibuildwheel 用、Linux manylinux 限定）は
  変更しない。manylinux コンテナ内は単一のシステムコンパイラのみのため、
  この ABI 不一致は発生しない。
- 既に `scip_static/` が存在する環境（例えば別コンパイラで一度ビルド済み）を
  自動検知して再ビルドを促す仕組みは実装しない（`clean-scip` で手動対応）。

## 検証

このセッションは Linux 環境のため、FreeBSD 実機でのフルビルド確認はできない。
以下で代替する。

1. `bash -n build_scip_static.sh` — 構文チェック。
2. 追加したコンパイラ選択ロジックを、`uname -s` の分岐を手動でなぞって確認
   （FreeBSD / Darwin / Linux 各ケースで CMakeLists.txt と同じ変数が選ばれるか）。
3. 可能であれば Linux 上で `make scip-static` を実行し、既存の動作
   （Linux では `g++` を明示指定しても元々の挙動と変わらないはず）が壊れていない
   ことを確認する。
4. 実機検証はユーザー側の FreeBSD 環境で `make clean-scip && make rs` を
   実行してもらう必要がある旨を report に明記する。

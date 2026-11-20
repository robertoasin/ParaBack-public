# make による静的 SCIP のダウンロード・ビルド・インストール自動化

## Context（背景・目的）

`make rs` / `make rx`（静的リンク naps）は、事前に用意された `scip_static/`
（`scip_static/lib/cmake/scip/scip-config.cmake` を含む静的 PIC 版 SCIP）を前提に
[CMakeLists.txt:226-244](../CMakeLists.txt#L226-L244) が検出・リンクする。
しかし `scip_static/` を作る手段は従来 make に配線されておらず、唯一の自動化スクリプト
[ci/build_static_scip.sh](../ci/build_static_scip.sh) は cibuildwheel の before-all
からのみ呼ばれていた。結果、`make rs` を使うには利用者が手動で SCIP を静的ビルドする
必要があった（[INSTALL.md:29-66](../INSTALL.md#L29-L66)）。

本タスクの目的は、**scipoptsuite ソースを `scip/` にダウンロード・展開し、静的 PIC 版 SCIP を
`scip_static/` にビルド・インストールするルーチンを make ターゲットとして追加**し、
`make rs` / `make rx` から利用できるようにすること。

## 確定した方針

1. **ソースは `scip/` に保持**（一時ディレクトリではない）。理由: Linux 以外（FreeBSD 等）では
   ローカルパッチを当てないと SCIP がビルドできない可能性があり、ソースを手元に残して
   パッチ適用・再ビルドを可能にしたいため。→ tgz と展開ソースを消さず、**展開済みなら再展開しない**
   （手動パッチを保護）。
2. **独立ターゲット + rs からの自動起動**: `make scip-static` を明示実行可能にし、
   さらに `make rs` / `rx` が `scip_static/` 不在を検知したら自動で `scip-static` を実行する。
3. **既存 `ci/build_static_scip.sh` は当面触らず、make 用に別ルートを新設**。
   検証済みレシピ（SHA256 検証・`-DCMAKE_INSTALL_LIBDIR=lib` の lib64 修正・PIC・
   `NO_EXTERNAL_CODE=ON` 等）は踏襲する。後日、必要に応じて両者を一元化する。

## 変更内容

### 1. 新スクリプト [build_scip_static.sh](../build_scip_static.sh)（リポジトリルート直下・新規）

`ci/build_static_scip.sh` のレシピを踏襲しつつ、ソース保持版に改変。

- 変数: `SCIP_VERSION=10.0.2`、`SCIP_SHA256=eecc29f...`（既存値を流用）。
- インストール先: `PREFIX="${1:-$(pwd)/scip_static}"`。
- ソース展開先: `SCIP_SRC_DIR="${SCIP_SRC_DIR:-$(pwd)/scip}"`、`SRC="$SCIP_SRC_DIR/scipoptsuite-$SCIP_VERSION"`。
- 冪等ガード: `$PREFIX/lib/cmake/scip/scip-config.cmake` があればスキップ。
- ダウンロード: `$SCIP_SRC_DIR/scipoptsuite-<ver>.tgz` が無ければ `curl` で取得 → 常に `sha256sum -c` で検証。
- 展開: `$SRC` が無いときだけ `tar xzf`（展開済みなら触れず、手動パッチ保護）。
- パッチ適用フック（コメントのみ）: 展開後・cmake 前に OS 別パッチを当てる余地を残す。
- ビルド・インストール（cmake オプションは検証済みレシピを踏襲）:
  `-DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DNO_EXTERNAL_CODE=ON`
  `-DSHARED=OFF -DLPS=spx -DSYM=snauty -DTPI=none -DAMPL=OFF -DGCG=OFF -DUG=OFF -DBUILD_TESTING=OFF`
- 移植性: 並列数は `$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)`。

### 2. [Makefile](../Makefile)

- `.PHONY` に `scip-static clean-scip` を追加。
- 独立ターゲット `scip-static`（`SCIP_SRC_DIR="$(CURDIR)/scip" bash build_scip_static.sh "$(CURDIR)/scip_static"`）。
- rs / rx からの自動起動（`SCIP_STATIC_PREFIX` 未指定時のみ、order-only 依存）:
  ```make
  ifndef SCIP_STATIC_PREFIX
  SCIP_STATIC_CONFIG = $(CURDIR)/scip_static/lib/cmake/scip/scip-config.cmake
  rs rx: | $(SCIP_STATIC_CONFIG)
  $(SCIP_STATIC_CONFIG):
  	$(MAKE) scip-static
  endif
  ```
- `clean-scip`（`rm -rf scip scip_static`。`clean` には含めない）。

### 3. [.gitignore](../.gitignore)

`/scip/` を無視対象に追加（`scip_static/` は既存の `*_static` でカバー済み）。

## スコープ外

- `ci/build_static_scip.sh` および CI（cibuildwheel / pyproject.toml）は変更しない。
- OS 別パッチの自動適用は実装しない（フックのコメントのみ）。
- `USE_SCIP=OFF` での静的ビルド経路は既存挙動のまま（自動依存は付かない）。

## 検証（timeout 必須。SCIP ビルドは SoPlex 込みで数分〜十数分）

1. `make scip-static` → `scip/scipoptsuite-10.0.2/` 展開、
   `scip_static/lib/cmake/scip/scip-config.cmake` と `scip_static/lib/libscip.a` 生成。
2. `make rs` → `naps_bignum_static-<VER>` 生成、`file`/`ldd` で静的リンク確認、
   `timeout 10 ./naps_bignum_static-<VER> Examples/garden9x9.opb` で動作確認。
3. 自動起動: `scip_static/` を退避してから `make rs` → 途中で `scip-static` が自動実行。
4. 冪等性: 続けて `make scip-static` → skipping でスキップ。
5. パッチ保護: `scip/scipoptsuite-10.0.2/` を改変 → `scip_static/` を消して `make scip-static`
   → 再展開されず改変が残る。

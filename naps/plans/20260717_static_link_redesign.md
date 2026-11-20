# make r / make rs の動的・静的リンク役割分担の再設計

## Context

前段のセッションで SCIP を静的リンクする `SCIP_STATIC_PREFIX` オプションを CMakeLists.txt / Makefile に追加したが、これを**指定せずに** `make rs` を実行すると、既存の `find_library(SCIP_LIBRARY NAMES scip PATHS /usr/lib ...)` ロジックがそのまま使われ、apt 版の動的ライブラリ `/usr/lib/libscip.so` を見つけてしまう。`STATIC_BUILD=ON`（`--static` フルスタティックリンク）とこの動的ライブラリの組み合わせは本質的に矛盾し、`ld: attempted static link of dynamic object` という分かりにくいエラーで失敗する。

目的は、**`make r` は常に動的リンクの release バイナリ、`make rs`（`rx` 含む）は常に静的リンクの release バイナリを作る**、という役割分担が「知らないと踏み抜く地雷」なしに常に成立するよう Makefile / CMakeLists.txt を再設計すること。

ユーザーの決定事項:
- 静的ビルド済み SCIP（`libscip.a` 一式）の配置場所は **リポジトリ直下の新規ディレクトリ `scip_static/`** に規約化する。`cadical3/`・`kissat/` と同様の「リポジトリ内に外部ライブラリを置く」パターンに倣う。`SCIP_STATIC_PREFIX` 未指定時はここを自動的に見る。
- `scip_static/` が存在しない（あるいは静的 SCIP が見つからない）状態で `make rs` を実行した場合は、**CMake configure 時点で分かりやすい `FATAL_ERROR` を出して止める**（`.so` へのフォールバックは絶対にしない）。
- **動的ビルド（`make r`/`make d` 等、`STATIC_BUILD=OFF`）で `libscip.so` が見つからない場合も、同様に `FATAL_ERROR` で止める**よう変更する（現行は `message(WARNING ...)` で SCIP 機能を黙って無効化し続行していたが、この「サイレントな機能低下」を廃止する）。SCIP なしでビルドしたい場合は明示的に `-D USE_SCIP=OFF` を指定する必要がある（[Makefile:17](Makefile#L17) に既にコメントアウトされた切り替え例がある）。

### 前段までに確定している事実

- `STATIC_BUILD` オプションは [CMakeLists.txt:68](CMakeLists.txt#L68) で定義され、SCIP 検出ロジック（[CMakeLists.txt:212-234](CMakeLists.txt#L212-L234)）より前に評価済みなので `if(STATIC_BUILD)` で分岐可能。
- `SCIP_STATIC_PREFIX` 経由の `find_package(SCIP CONFIG REQUIRED PATHS ... NO_DEFAULT_PATH)` は動作実績あり（`libscip`ターゲット名、`scip-config.cmake` が SoPlex への依存も連鎖解決する）。
- 静的 SCIP ビルドの検証済みレシピ（SCIP 10.0.2、`/home/sakai/proj/naps/scipoptsuite-10.0.2` ソース使用）:
  ```
  cmake -S <scipoptsuite-src> -B <build-dir> \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=<prefix> \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DNO_EXTERNAL_CODE=ON -DSHARED=OFF -DLPS=spx -DSYM=snauty -DTPI=none \
    -DAMPL=OFF -DGCG=OFF -DUG=OFF -DBUILD_TESTING=OFF
  ```
  （`BUILD_TESTING=OFF` は `examples/` サブディレクトリが apt 版の `scip-config.cmake` を誤って拾う問題を避けるために必須。`CMAKE_POSITION_INDEPENDENT_CODE=ON` は Python wheel 用の PIC 化にも使うため付けておく。）
- 現在 `/home/sakai/proj/naps/scip_static_install` に上記レシピでビルド済みの SCIP 10.0.2 静的一式が存在する（検証用）。
- `.gitignore` には `build_python*` のみがあり、`scip_static/` 用のエントリは無い。

## 設計

### 1. CMakeLists.txt: `STATIC_BUILD` で SCIP 検出ロジックを完全に分岐

現行の [CMakeLists.txt:212-234](CMakeLists.txt#L212-L234) を、`STATIC_BUILD` の値で最初に分岐する形に再構成する:

```cmake
# 4. SCIPの設定
if(USE_SCIP)
    if(STATIC_BUILD)
        # 静的リンクでは .so への意図しないフォールバックを避けるため、
        # 静的 libscip.a を専用 prefix からのみ探す。
        if(NOT SCIP_STATIC_PREFIX)
            set(SCIP_STATIC_PREFIX "${CMAKE_SOURCE_DIR}/scip_static")
        endif()
        if(NOT EXISTS "${SCIP_STATIC_PREFIX}/lib/cmake/scip/scip-config.cmake")
            message(FATAL_ERROR
                "静的 SCIP が見つかりません: ${SCIP_STATIC_PREFIX}\n"
                "INSTALL.md の手順で SCIP を静的ビルドし ${CMAKE_SOURCE_DIR}/scip_static "
                "にインストールするか、-D SCIP_STATIC_PREFIX=<path> で場所を指定してください。\n"
                "SCIP なしで静的ビルドしたい場合は -D USE_SCIP=OFF を指定してください。")
        endif()
        find_package(SCIP CONFIG REQUIRED
            PATHS ${SCIP_STATIC_PREFIX}
            NO_DEFAULT_PATH)
        list(APPEND LINK_LIBS libscip)
        add_compile_definitions(USE_SCIP)
    else()
        # 動的リンクではシステムの libscip.so を探す（従来通り）。
        find_library(SCIP_LIBRARY NAMES scip
            PATHS /usr/lib /lib /usr/local/lib
            NO_DEFAULT_PATH)
        if(SCIP_LIBRARY)
            list(APPEND LINK_LIBS ${SCIP_LIBRARY})
            add_compile_definitions(USE_SCIP)
        else()
            message(FATAL_ERROR
                "SCIP library (libscip.so) not found in /usr/lib, /lib, /usr/local/lib.\n"
                "Install scipoptsuite, or specify -D USE_SCIP=OFF to build without SCIP.")
        endif()
    endif()
endif()
```

ポイント:
- `STATIC_BUILD=ON`（`make rs`/`make rx`）は `.so` 探索コードパスに絶対に到達しない。`SCIP_STATIC_PREFIX` 明示指定 → 未指定なら `scip_static/` 既定 → どちらにも無ければ `FATAL_ERROR` で人間可読なメッセージを出して止める。
- `STATIC_BUILD=OFF`（`make r`/`make d` 等）も `libscip.so` が見つからなければ `FATAL_ERROR` で止める。従来の「警告だけ出して SCIP 機能を黙って無効化」というサイレントな挙動は廃止する。
- どちらの経路でも `USE_SCIP=OFF` を明示すれば `if(USE_SCIP)` 自体を通らないので、SCIP 抜きのビルドは意図的に選べば引き続き可能。

#### 追記（2026-07-17）: Python ビルド対応のため分岐条件を拡張

上記コード例は本計画の実装時点（60d6415）のもので、**現行の実装とは分岐条件が異なる**。本計画は
`make r` / `make rs` の役割分担のみを対象としており、`BUILD_PYTHON`（Python 拡張のビルド）を
想定していなかった。その結果、`if(STATIC_BUILD)` を外側の分岐にしたことで、`STATIC_BUILD=OFF`
である Python 拡張のビルドでは `SCIP_STATIC_PREFIX` が**黙って無視され** `libscip.so` に動的
リンクされる問題が生じた（wheel への静的 SCIP 埋め込みが前提の manylinux 化・PyPI 公開方針と
矛盾する。[plans/20260716_pypi_deployment.md](20260716_pypi_deployment.md) 参照）。

このため現行の [CMakeLists.txt:216-](../CMakeLists.txt#L216) では、既定値の補完と静的 SCIP の
採用判定を分離している:

```cmake
# 1. STATIC_BUILD なら prefix 未指定時に scip_static/ を既定採用
if(STATIC_BUILD AND NOT SCIP_STATIC_PREFIX)
    set(SCIP_STATIC_PREFIX "${CMAKE_SOURCE_DIR}/scip_static")
endif()

# 2. prefix が決まっていれば構成を問わず静的 SCIP を使う
if(SCIP_STATIC_PREFIX)
    ...  # FATAL_ERROR ガード + find_package(SCIP CONFIG REQUIRED)
else()
    ...  # システムの libscip.so
endif()
```

本計画の目的（`make r` は常に動的・`make rs` は常に静的・`.so` へのサイレントフォールバック
廃止）は、この形でもすべて維持される:

- `STATIC_BUILD=ON` は必ず prefix が入るため `.so` 経路に到達しない（従来どおり）。
- `SCIP_STATIC_PREFIX` を明示した場合のみ、`STATIC_BUILD=OFF` でも静的 SCIP を使う
  （wheel ビルド用。`setup.py` が環境変数から橋渡しする）。
- Python ビルドに既定 prefix は**補わない**。`scip_static/` を持たない利用者の
  `pip install .` を従来どおりシステムの `libscip.so` で通すため。

### 2. Makefile

変更不要（`SCIP_STATIC_PREFIX` を明示上書きする経路は [Makefile:29-31](Makefile#L29-L31) に既にあり、CMakeLists.txt 側でデフォルト値を補完する設計にするため）。

### 3. `.gitignore`

`scip_static/` を追加する（ビルド成果物のため）。

### 4. INSTALL.md

`### Enabling SCIP in NaPS` セクションに、静的ビルド手順を追記する:
- ソース入手（`scipopt/scip` の release tarball、または scipoptsuite フルセット）
- 上記レシピでの configure/build/install（install prefix = `<repo>/scip_static`）
- `make rs` はこれを自動検出すること、見つからなければ `FATAL_ERROR` で誘導されること

## 検証方法

1. `scip_static/` が存在しない状態で `rm -rf build_rs && make rs` → 分かりやすい `FATAL_ERROR` メッセージで configure が止まることを確認。
2. `/home/sakai/proj/naps/scip_static_install` の中身を `scip_static/` にコピーし、`make rs` → 成功し `naps_bignum_static-<VER>` が生成されることを確認。`file`/`ldd` で静的性を再確認。
3. `-SCIP`/`-cSCIP` で `Examples/` の代表インスタンスを解き、既存 `make r`（動的、apt 版 SCIP 10.0.2）と結果が一致することを確認（前段セッションと同じ比較手順）。
4. `rm -rf build_release && make r` → apt 版 `libscip.so` が入った状態のまま引き続き動的リンクで成功すること（回帰確認）。`ldd naps_release-<VER>` で `libscip.so` への動的依存が残っていることを確認。
5. `make rx`（64bit integer 静的）も同様に成功することを確認。
6. （任意）`apt remove` 相当を行わずに確認する代替として、`USE_SCIP=OFF` を明示指定した `cmake` 呼び出しで動的ビルドが SCIP 抜きで成功することを確認し、`FATAL_ERROR` 化が意図しないビルド不能を招かないことを確かめる。

## 変更対象ファイル

- `CMakeLists.txt`（SCIP 検出ロジックの再構成、212-234行目付近）
- `.gitignore`（`scip_static/` 追加）
- `INSTALL.md`（静的 SCIP ビルド手順の追記）
- Makefile は変更なし（確認のみ）

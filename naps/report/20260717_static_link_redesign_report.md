# make r / make rs 動的・静的リンク役割分担 再設計 完了レポート

日付: 2026-07-17
計画書: [plans/20260717_static_link_redesign.md](../plans/20260717_static_link_redesign.md)
ブランチ: pybind

## 結論

`make r`（動的リンク）と `make rs`/`make rx`（静的リンク）の役割分担が、SCIP の有無に関わらず
「知らないと踏み抜く地雷」なしに常に成立するよう CMakeLists.txt を再設計した。
全パターン（静的・動的・64bit整数静的）でビルド成功、`-SCIP`/`-cSCIP` の求解結果一致を確認済み。

## 発端

前段のセッションで SCIP 静的リンク用に `SCIP_STATIC_PREFIX` オプションを追加したが、これを
**指定せずに** `make rs` を実行すると、既存の `find_library` ロジックが apt 版の動的ライブラリ
`/usr/lib/libscip.so` を見つけてしまい、`STATIC_BUILD=ON`（`--static` フルスタティックリンク）
との組み合わせで

```
/usr/bin/ld: attempted static link of dynamic object `/usr/lib/libscip.so'
```

という分かりにくいエラーで失敗することが判明した。

## 方針決定（相談の結果）

- 静的ビルド済み SCIP（`libscip.a` 一式）の配置場所は **リポジトリ直下の新規ディレクトリ
  `scip_static/`** に規約化。`cadical3/`・`kissat/` と同様の「リポジトリ内に外部ライブラリを
  置く」パターンに倣う。`SCIP_STATIC_PREFIX` 未指定時はここを自動的に見る。
- `scip_static/` が無い状態で `make rs` を実行した場合は、CMake configure 時点で分かりやすい
  `FATAL_ERROR` を出して止める（`.so` へのフォールバックはしない）。
- 動的ビルド（`make r`/`make d` 等）で `libscip.so` が見つからない場合も、従来の
  `message(WARNING ...)` によるサイレントな機能無効化をやめ、同様に `FATAL_ERROR` で止める
  よう変更。SCIP なしでビルドしたい場合は `-D USE_SCIP=OFF` を明示する運用に統一。

## 実施内容

### 1. CMakeLists.txt: SCIP 検出ロジックを `STATIC_BUILD` で完全分岐

[CMakeLists.txt:212-244](../CMakeLists.txt#L212-L244) を再構成。

```cmake
if(USE_SCIP)
    if(STATIC_BUILD)
        if(NOT SCIP_STATIC_PREFIX)
            set(SCIP_STATIC_PREFIX "${CMAKE_SOURCE_DIR}/scip_static")
        endif()
        if(NOT EXISTS "${SCIP_STATIC_PREFIX}/lib/cmake/scip/scip-config.cmake")
            message(FATAL_ERROR "静的 SCIP が見つかりません: ...")
        endif()
        find_package(SCIP CONFIG REQUIRED PATHS ${SCIP_STATIC_PREFIX} NO_DEFAULT_PATH)
        list(APPEND LINK_LIBS libscip)
        add_compile_definitions(USE_SCIP)
    else()
        find_library(SCIP_LIBRARY NAMES scip PATHS /usr/lib /lib /usr/local/lib NO_DEFAULT_PATH)
        if(SCIP_LIBRARY)
            list(APPEND LINK_LIBS ${SCIP_LIBRARY})
            add_compile_definitions(USE_SCIP)
        else()
            message(FATAL_ERROR "SCIP library (libscip.so) not found ...")
        endif()
    endif()
endif()
```

`STATIC_BUILD=ON` の経路は `.so` 探索コードパスに一切到達しない構造にし、動的ライブラリを
誤って静的リンクしようとする組み合わせ自体を発生させないようにした。

> **追記（2026-07-17）**: 上記は本タスク完了時点のコードで、**現行の実装とは分岐条件が異なる**。
> 本タスクは `BUILD_PYTHON`（Python 拡張）を想定しておらず、`if(STATIC_BUILD)` を外側の分岐に
> したため、`STATIC_BUILD=OFF` である Python 拡張のビルドで `SCIP_STATIC_PREFIX` が黙って無視され
> `libscip.so` に動的リンクされる問題が生じた。現行は既定値の補完
> （`if(STATIC_BUILD AND NOT SCIP_STATIC_PREFIX)`）と静的 SCIP の採用判定（`if(SCIP_STATIC_PREFIX)`）
> を分離している。本タスクの目的（`make r` は常に動的・`make rs` は常に静的・`.so` への
> サイレントフォールバック廃止）はいずれも維持。詳細は
> [plans/20260717_static_link_redesign.md](../plans/20260717_static_link_redesign.md) の
> 「追記（2026-07-17）」節を参照。

### 2. Makefile

変更不要と判断。`SCIP_STATIC_PREFIX` の明示上書き経路（[Makefile:29-31](../Makefile#L29-L31)）は
既存のまま活かし、既定値の補完は CMakeLists.txt 側に持たせた。

### 3. `.gitignore`

`scip_static/` 追加が必要か確認したところ、既存の `*_static` パターン（[.gitignore:18](../.gitignore#L18)）
が `scip_static` にもマッチすることを `git check-ignore -v` で確認。**変更不要**。

### 4. INSTALL.md

`### Enabling SCIP in NaPS` を「Dynamic build」「Static build」の2節に分割し、静的ビルド用に
検証済みの SCIP 10.0.2 静的ビルドレシピ（`NO_EXTERNAL_CODE=ON -DSHARED=OFF -DLPS=spx
-DSYM=snauty -DTPI=none -DAMPL=OFF -DGCG=OFF -DUG=OFF -DBUILD_TESTING=OFF`）と
`scip_static/` への配置手順、`SCIP_STATIC_PREFIX` での上書き方法を追記。

## 検証結果（すべて timeout 付きで実行）

| # | 検証内容 | 結果 |
|---|---|---|
| 1 | `scip_static/` 未配置で `make rs` | `FATAL_ERROR` で configure が明確に停止することを確認 |
| 2 | `scip_static/` に静的 SCIP（10.0.2, PIC）を配置し `SCIP_STATIC_PREFIX` 指定なしで `make rs` | 自動検出され成功。`naps_bignum_static-1.04a7` は `file`/`ldd` で完全な静的バイナリと確認 |
| 3 | 静的版と `make r`（apt 動的、同じ SCIP 10.0.2）で `-SCIP` 実行し結果比較 | `example.opb` で `s`/`v`/`o` 行が完全一致 |
| 4 | `make r` の回帰確認 | `libscip.so.10.0` への動的リンクを維持したまま成功。`-SCIP` 正常動作 |
| 5 | `make rx`（64bit integer 静的） | 成功。`file` で statically linked を確認 |

## 変更ファイル（pybind ブランチ、未コミット）

| ファイル | 変更 |
|---|---|
| `CMakeLists.txt` | SCIP 検出ロジックを `STATIC_BUILD` で完全分岐、両経路とも未検出時は `FATAL_ERROR` |
| `INSTALL.md` | 静的/動的ビルドの SCIP 要件を節分割し、静的ビルド手順（`scip_static/` 配置）を追記 |
| `plans/20260717_static_link_redesign.md` | 新規（計画書） |
| `.gitignore` | 変更なし（既存パターンでカバー済みと確認） |
| `Makefile` | 変更なし（既存の `SCIP_STATIC_PREFIX` 橋渡しをそのまま活用） |

## 保留・引き継ぎ事項

- `scip_static/` はローカル検証用に `/home/sakai/proj/naps/scip_static_install` の内容を
  コピーして作成済み（`.gitignore` の `*_static` パターンで追跡対象外）。CI 環境（Phase 2,
  [plans/20260716_pypi_deployment.md](../plans/20260716_pypi_deployment.md) 参照）では
  同じレシピをコンテナ内で実行する想定。
- `USE_SCIP=OFF` を明示しない限り常に SCIP が必須になった仕様変更は、`.claude/CLAUDE.md`
  の「SCIP連携は `libscip` が見つかった場合のみ有効」という記載と食い違うため、
  CLAUDE.md 側の更新が必要（本タスクのスコープ外、別途対応を推奨）。

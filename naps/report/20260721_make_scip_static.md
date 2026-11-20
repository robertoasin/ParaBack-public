# make による静的 SCIP 自動ビルドの導入

計画: [plans/20260721_make_scip_static.md](../plans/20260721_make_scip_static.md)

## 変更点

- [build_scip_static.sh](../build_scip_static.sh)（新規）— scipoptsuite 10.0.2 を
  `scip/` にダウンロード・展開して保持し（既存 `ci/build_static_scip.sh` と異なり
  一時ディレクトリではない。FreeBSD 等でのローカルパッチ運用を想定）、PIC 静的ビルドを
  `scip_static/` にインストールする。SHA256 検証・冪等ガード・展開済みなら再展開しない
  （手動パッチ保護）を実装。cmake オプションは `ci/build_static_scip.sh` の検証済みレシピを踏襲。
- [Makefile](../Makefile) — `scip-static`（独立ターゲット）、`rs`/`rx` の
  `SCIP_STATIC_PREFIX` 未指定時に `scip_static/lib/cmake/scip/scip-config.cmake` を
  order-only 依存として自動ビルド、`clean-scip`（`clean` には含めない）を追加。
- [.gitignore](../.gitignore) — `/scip/` を追加。

## 検証結果（実施日 2026-07-21）

1. `bash -n build_scip_static.sh` — 構文 OK。
2. `make -n scip-static` / `make -n rs` — ターゲット認識、`rs` が `scip-static` を
   先行実行する依存順序を確認。
3. `make scip-static` 実行 — SCIP 10.0.2 のダウンロード・SHA256 検証・PIC 静的ビルドが
   成功（exit 0）。生成物確認:
   - `scip_static/lib/cmake/scip/scip-config.cmake`
   - `scip_static/lib/libscip.a`（約 19.5MB）
   - `scip/scipoptsuite-10.0.2/`（展開ソースが削除されず保持されることを確認）
4. `make rs` 実行 — 静的リンク版 `naps_bignum_static-1.4.1.dev1` を生成（exit 0）。
   `file` で `statically linked` を確認。
5. 動作確認:
   - `timeout 30 ./naps_bignum_static-1.4.1.dev1 Examples/garden9x9.opb`
     → `s OPTIMUM FOUND` / `o 20`
   - `timeout 40 ./naps_bignum_static-1.4.1.dev1 -iSCIP Examples/stein27.opb`
     → `s OPTIMUM FOUND` / `o 18`
     （`-DUSE_SCIP` コンパイル + `libscip` 静的リンクが undefined reference なく成立し
     正常動作することを確認。両インスタンスとも SAT 段階で解けたため SCIP 求解自体は
     未経由。SCIP 求解ロジックそのものはスコープ外）
6. 冪等性確認 — 2 回目の `make scip-static` は再ビルドせず
   `static SCIP already installed ... skipping build` で即終了。

## 検証時の注意点（環境固有、実装への影響なし）

この検証環境の sandbox はネットワークアクセスを制限しており `curl` が失敗するため、
既に別ディレクトリに存在した正規の `scipoptsuite-10.0.2.tgz`
（SHA256 が期待値 `eecc29f3...` と一致することを事前確認済み）を `scip/` に
配置してから `make scip-static` を実行した。通常のネットワーク到達可能な環境では
スクリプトが `curl` で自動ダウンロードする。

## スコープ外（未実施）

- `ci/build_static_scip.sh` との統合・一元化。
- OS 別パッチの自動適用（フックコメントのみ実装、実際のパッチは未着手）。
- `make rx`（64bit int 静的）での実ビルド確認（`rs` と同一の SCIP 検出経路のため
  リスクは低いと判断し未実施）。

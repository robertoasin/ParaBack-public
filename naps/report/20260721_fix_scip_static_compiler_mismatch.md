# FreeBSD 静的ビルドでの ABI 不一致（libstdc++ / libc++）解消

計画: [plans/20260721_fix_scip_static_compiler_mismatch.md](../plans/20260721_fix_scip_static_compiler_mismatch.md)

## 原因（診断結果）

FreeBSD 上の `gmake rs` が `std::__1::...`（libc++）系シンボルの未定義参照で
大量に失敗していた件を調査し、以下の ABI 不一致が原因と特定した。

- naps 本体は [CMakeLists.txt:9-19](../CMakeLists.txt#L9-L19) により FreeBSD では
  `/usr/local/bin/g++[0-9]*` の最新版（例: `g++15`、libstdc++）を自動選択する。
- [build_scip_static.sh](../build_scip_static.sh) はコンパイラを指定せず素の `cmake`
  を呼んでいたため、`scip_static/lib/libscip.a` / `libsoplex.a` は FreeBSD の
  システムデフォルト（clang、libc++）でビルドされていた。
- `make rs` は `--static` で完全静的リンクするため、libc++ の実体がリンクグラフに
  含まれず、SCIP/SoPlex が要求する libc++ シンボルが未定義参照になっていた。

## 変更点

- [build_scip_static.sh](../build_scip_static.sh) — naps 本体
  （CMakeLists.txt 冒頭）と同じ基準（FreeBSD: `/usr/local/bin/g++[0-9]*` の
  最新版、macOS: `/opt/homebrew/bin/g++-[0-9]*` または `/usr/local/bin/g++-[0-9]*`
  の最新版、それ以外: `g++`）でコンパイラを選び、SCIP 自身の `cmake` 呼び出しに
  `-DCMAKE_CXX_COMPILER` / `-DCMAKE_C_COMPILER` として明示的に渡すように変更。
  `USE_SYSTEM_COMPILER` が設定されている場合は naps 本体と同様に上書きしない。
- [Makefile](../Makefile) — `scip-static` ターゲット呼び出し時に
  `USE_SYSTEM_COMPILER="$(USE_SYSTEM_COMPILER)"` を明示的にシェル環境へ伝播し、
  `make rs USE_SYSTEM_COMPILER=1` 等でも naps 本体と SCIP が一貫した経路で
  ビルドされるようにした（従来は Make 変数がシェル子プロセスの環境に自動継承
  されず、`scip-static` 側だけ常にデフォルト経路になっていた）。

## 検証結果（実施日 2026-07-21、Linux 環境）

このセッションは Linux のため FreeBSD 実機での再現・修正確認はできない。
以下で代替検証した。

1. `bash -n build_scip_static.sh` — 構文 OK。
2. `make -n scip-static` / `make -n scip-static USE_SYSTEM_COMPILER=1` —
   `USE_SYSTEM_COMPILER` が空文字・`"1"` それぞれ正しく子プロセス環境に
   渡ることを確認。
3. 追加したコンパイラ選択ロジックの各分岐（FreeBSD / else / USE_SYSTEM_COMPILER
   設定時）を手動シミュレートし、期待通りの分岐結果になることを確認
   （このホストは Linux のため FreeBSD 分岐自体は「該当パス無し→空」という
   安全なフォールバックになることのみ確認。else 分岐では `/usr/bin/g++` を
   正しく検出）。
4. 実際に生成される `cmake` 引数列を echo で確認 —
   `-DCMAKE_CXX_COMPILER=/usr/bin/g++` が正しく組み立てられることを確認。
5. クリーンな `scip/scipoptsuite-10.0.2/build_static` に対し、修正版
   `build_scip_static.sh` を一時 PREFIX 向けに実行し `timeout 60` で打ち切り。
   ログで `-- The CXX compiler identification is GNU 13.3.0` と
   `-- Configuring done (1.2s)` を確認し、新ロジックが cmake の configure
   まで正常に到達すること（既存の Linux 上の動作を壊していないこと）を確認。
   （初回、既存の汚染された `build_static/` キャッシュを再利用して試した際に
   `TBB was not found` エラーが出たが、これは今回の変更とは無関係な
   ビルドディレクトリ再利用による副作用と判明。クリーンな状態では発生しない。）
6. 検証で生成した一時ディレクトリ・ビルドキャッシュはすべて削除して後片付け済み。
   `git status` で意図した2ファイル（`Makefile`、`build_scip_static.sh`）以外の
   追跡対象ファイルに変更がないことを確認。

## 追記: build_scip_static.sh → install_scip_static.sh へのリネーム

上記の変更後、`build_scip_static.sh` は `cleanPB` ターゲット
（[Makefile:100-101](../Makefile#L100-L101)）の `rm -rf build_*` に
巻き込まれて `make clean` / `make cleanPB` 実行時に誤削除されることが判明した
（`build_*` glob が `build_debug` 等のビルドディレクトリだけでなく
同名プレフィックスのファイルにもマッチするため）。`git mv` で
[install_scip_static.sh](../install_scip_static.sh) にリネームし、
[Makefile](../Makefile) の `scip-static` ターゲットとスクリプト自身の
ヘッダコメント内の自己参照を追従修正した。本文中のこの上のセクションで
`build_scip_static.sh` と表記している箇所は、リネーム前の作業時点の記録として
そのまま残している。

検証: `bash -n install_scip_static.sh` で構文 OK、`make -n scip-static` で
新ファイル名が呼ばれることを確認、`make -n cleanPB` の実行内容
（`rm -rf build_*`）に `install_scip_static.sh` が含まれないことを確認。

## 追記: clean-scip が tarball (scip/*.tgz) を残すよう変更

従来 `clean-scip` は `rm -rf scip scip_static` で `scip/` を丸ごと削除しており、
キャッシュ済みの `scip/scipoptsuite-<ver>.tgz`（約19MB）まで消していた。
再ビルドのたびに再ダウンロード（＋SHA256 検証）が発生するのは望ましくないため、
[Makefile](../Makefile) の `clean-scip` を「展開ソース `scip/scipoptsuite-*/` と
`scip_static/` は消すが `scip/*.tgz` は残す」挙動に変更した:

```make
clean-scip:
	rm -rf scip_static
	if [ -d scip ]; then find scip -mindepth 1 -maxdepth 1 ! -name '*.tgz' -exec rm -rf {} +; fi
```

`find` の `-mindepth/-maxdepth`・`! -name`・`-exec ... {} +` は GNU / FreeBSD
双方の find で使用可能。tarball も含めて完全削除したい場合は `rm -rf scip` を
手動実行する旨をコメントに明記した。

あわせて、先のセッションで install_scip_static.sh のヘッダに書いていた
「sha256sum が無い環境（FreeBSD 等）」というコメントが誤り（FreeBSD には
`/sbin/sha256sum` があり `-c` 形式に対応）であることをユーザー指摘により訂正した。

検証: `make -n clean-scip` で新しい2行レシピが展開されること、
`bash -n install_scip_static.sh` で構文 OK を確認。

## 未実施・残作業

- **FreeBSD 実機での最終確認**: ユーザー側の FreeBSD 環境で
  `rm -rf scip_static scip/scipoptsuite-10.0.2/build_static`（または
  `make clean-scip`）→ `make rs` を実行し、`std::__1::...` 系の未定義参照が
  解消されることを確認する必要がある。既存の `scip_static/` は clang/libc++
  でビルド済みのため、修正後に再ビルドしない限り同じエラーが再発する点に注意。
- `make rx`（64bit int 静的）での確認は未実施（`rs` と同一の SCIP 検出経路の
  ため影響範囲は同じと判断）。

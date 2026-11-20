# pynaps 1.4.1 本番 PyPI 初投入 完了レポート

日付: 2026-08-19
関連: [report/20260717_pypi_deployment_phase2.md](20260717_pypi_deployment_phase2.md)（TestPyPI 実証まで）、
[docs/versioning.md](../docs/versioning.md)
リポジトリ: gitlab.com:naps2040334/naps.git（Group「naps」配下、path は `naps2040334`）

## 結論

`pynaps 1.4.1`（cp39–cp313 × manylinux_2_28 の wheel 5点）を **本番 PyPI へ初めて公開した**。
Phase 2（[20260717_pypi_deployment_phase2.md](20260717_pypi_deployment_phase2.md)）で
TestPyPI までは実証済みだったが、本番 PyPI への投入は今回が初回。

## 経緯：タグ push してもパイプラインが起動しなかった問題

### 症状

`1.4.1.dev2` タグ（3週間前に作成・push 済み）に対し、ユーザーが改めて
`git push --tags` を行ったが、GitLab の Pipelines 一覧に何も現れなかった。

### 誤った仮説（切り分けて棄却したもの）

調査の過程で以下を疑ったが、いずれも原因ではなかった:

1. **Compute minutes（CI/CD 無料枠）の枯渇** — Group「naps」の Usage Quotas を
   確認したところ `0 / 400 compute minutes`（8月分は未消費）で枠は十分残っていた。
   個人プロフィール側の Usage Quotas に出た「Instance runners are disabled in all
   projects in this namespace」というバナーは、**中身が空の個人ネームスペース**を
   見ていただけで、実際にプロジェクトが属する Group「naps」とは無関係だった。
   個人ネームスペースと Group ネームスペースで compute minutes 集計場所が異なる点は
   紛らわしいので次回のためにメモしておく。
2. **`[skip ci]` 等のコミットメッセージによるスキップ** — 該当コミットのメッセージ
   （`added comment on versions`）にそのような指定はなく、該当なし。
3. **`.gitlab-ci.yml` の設定不備** — HEAD 時点・タグ時点のどちらでも内容は同一で
   問題なし。

### 真の原因

`git log -1 --format='%ci' 1.4.1.dev2` でタグの作成日時を見たところ **3週間前**
だった。つまりこのタグは**既にリモートに存在済み**で、今回の `git push --tags` は
ローカル・リモートで差分が無いため `Everything up-to-date` を返すだけの空振り
だった。GitLab は ref の**新規作成/更新イベント**を検知してパイプラインを作成する
ため、既存タグをそのまま push し直しても何もトリガーされない。

「4週間前の beta ブランチ → TestPyPI 公開成功」以降、`1.4.1.dev2` タグ自体には
一度もパイプラインが作成されていなかった（元々のタグ push 時点でも同様の
空振りだったか、あるいは実際には別の理由で作成されなかった経緯は未解明だが、
実害はないため深追いしていない）。

## 対応：`.dev` を経由せず正式版 `1.4.1` を直接タグ付け

当初は `1.4.1.dev3` を新規タグとして打ち直す案だったが、`docs/versioning.md` に
「本番 PyPI への初投入は正式版 `1.4.1` から（`.devN` は TestPyPI 検証専用）」と
明記されていることが判明。ユーザー判断で「まだ一度も本番投入できていない」
「不具合があれば `.post1` を出せばよい」として、**`.dev` を飛ばして `1.4.1` を
直接タグ付け**する方針に変更した。

### 実施内容（commit `1bb55ca`）

1. `Version.h` の `naps_version` を `1.4.1.dev3`（未コミットの書きかけ）から
   `1.4.1` に変更
2. `git commit -m "1.4.1"`（過去の版上げコミット `b29d9a8` の慣習に倣う）
3. `git tag 1.4.1`（軽量タグ、ローカル・リモートとも未使用であることを確認済み）
4. `git push origin master` → `git push origin refs/tags/1.4.1`
   → `* [new tag] 1.4.1 -> 1.4.1` で新規 ref として反映され、今度こそ
     GitLab 側でパイプラインが作成された

### CI 結果

- `build_wheels`: 成功。`wheelhouse/` に cp39〜cp313 × manylinux_2_28 の wheel
  5点が生成されたことを Job artifacts の Browse で確認済み
  （`pyproject.toml` の `[tool.cibuildwheel] build = ["cp39-*", ..., "cp313-*"]`
  と一致）
- `publish_pypi`（`when: manual`、Trusted Publishing / OIDC）: ユーザーが
  手動実行し、本番 PyPI への公開に成功

## 今後のための注意点

- **既存タグの再 push は何もトリガーしない**。パイプラインを起こしたいときは
  新しいタグ（新しい ref）を作る必要がある。同じ版をやり直したい場合は
  リモートタグの削除＋再作成（`git push origin :refs/tags/<tag>` →
  再 push）でも新規イベントにできるが、これは共有 remote への破壊的操作なので
  実行前に必ず確認すること。
- **Compute minutes の確認場所は namespace の種類で異なる**。Group 配下の
  プロジェクトなら Group の Settings → Usage Quotas → Pipelines
  （`https://gitlab.com/groups/<group>/-/usage_quotas#pipelines-quota-tab`）
  を見る。個人アカウントの Usage Quotas（avatar → Edit profile → Usage quotas）
  は個人ネームスペース専用で、Group 配下のプロジェクトとは無関係。
- `Version.h` が版番号の唯一の情報源（`setup.py` が正規表現で読み取り、
  `ci/check_version_tag.py` がタグとの一致を CI で検証）。他ファイルに
  版番号を二重管理していないため、リリース時の変更点は `Version.h` 一箇所で
  済む。
- `publish_pypi` は同一バージョン番号の再アップロード不可な不可逆操作。
  次回以降も `build_wheels` の成果物確認後、ユーザー自身の最終判断で
  実行する運用を継続する。

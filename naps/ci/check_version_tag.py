#!/usr/bin/env python3
"""git タグと Version.h のバージョン整合性チェック（CI 用）。

タグ名（例: v1.4.1 / 1.4.1.dev1）と Version.h の naps_version を PEP 440 で
正規化して比較し、不一致なら非ゼロ終了する。Version.h の更新を忘れたまま
タグを打ち、意図しないバージョンの wheel を PyPI へ公開する事故を防ぐ。
PyPI は一度使ったバージョン番号を再利用できないため、公開前に止めることが重要。
バージョン命名規則は docs/versioning.md を参照。

使い方: python ci/check_version_tag.py <tag>   (要 pip install packaging)
"""
import re
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    tag = sys.argv[1]

    version_h = Path(__file__).resolve().parent.parent / "Version.h"
    m = re.search(r'#define\s+naps_version\s+"([^"]+)"', version_h.read_text())
    if not m:
        print("エラー: Version.h から naps_version を読み取れませんでした", file=sys.stderr)
        return 1
    naps_version = m.group(1)

    try:
        tag_ver = Version(tag.removeprefix("v"))
        file_ver = Version(naps_version)
    except InvalidVersion as e:
        print(f"エラー: PEP 440 のバージョンとして解釈できません: {e}", file=sys.stderr)
        return 1

    if tag_ver != file_ver:
        print(
            f"エラー: タグ {tag} ({tag_ver}) と Version.h の naps_version "
            f'"{naps_version}" ({file_ver}) が一致しません。'
            "Version.h を更新してからタグを打ち直してください。",
            file=sys.stderr,
        )
        return 1

    print(f'OK: タグ {tag} は Version.h の "{naps_version}" (= {file_ver}) と一致')
    return 0


if __name__ == "__main__":
    sys.exit(main())

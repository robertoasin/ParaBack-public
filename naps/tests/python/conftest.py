import os
import sys

import pytest

# リポジトリルート（garden テストが Examples/ を相対パスで参照するため）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# PYTHONPATH 未設定でも build_python/ の naps モジュールを見つけられるようにする
_BUILD_DIR = os.path.join(REPO_ROOT, "build_python")
if _BUILD_DIR not in sys.path:
    sys.path.insert(0, _BUILD_DIR)


@pytest.fixture(autouse=True)
def _run_from_repo_root(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)

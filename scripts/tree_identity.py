"""输出当前提交与 Git tree，供单项 RV 证据绑定实现树。"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    """打印当前 commit 与 tree SHA。"""
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree_sha = subprocess.run(
        ["git", "show", "-s", "--format=%T", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    source_diff = subprocess.run(
        ["git", "diff", "--binary", "--", "src/backend", "tests"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    print(f"commit={commit_sha}")
    print(f"tree={tree_sha}")
    print(f"production_and_test_diff_sha256={hashlib.sha256(source_diff).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

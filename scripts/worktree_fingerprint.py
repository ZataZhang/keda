"""Calculate a deterministic digest of current non-evidence worktree changes."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def _git_paths(repository_root: Path, *arguments: str) -> set[str]:
    """Read NUL-separated repository paths from Git."""
    git_process = subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    return {
        path.decode("utf-8")
        for path in git_process.stdout.split(b"\0")
        if path
    }


def main() -> None:
    """Print a stable hash of changed tracked and untracked delivery files."""
    repository_root = Path(__file__).resolve().parents[4]
    changed_paths = _git_paths(repository_root, "diff", "--name-only", "-z", "HEAD")
    untracked_paths = _git_paths(
        repository_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    delivery_paths = sorted(
        path
        for path in changed_paths | untracked_paths
        if not path.startswith("tasks/evidence/")
    )
    content_digest = hashlib.sha256()
    for relative_path in delivery_paths:
        delivery_path = repository_root / relative_path
        content_digest.update(relative_path.encode("utf-8"))
        content_digest.update(b"\0")
        if delivery_path.is_file():
            content_digest.update(delivery_path.read_bytes())
        else:
            content_digest.update(b"<deleted>")
        content_digest.update(b"\0")
    print(f"WORKTREE_CONTENT_SHA256={content_digest.hexdigest()}")
    print(f"WORKTREE_DELIVERY_FILE_COUNT={len(delivery_paths)}")


if __name__ == "__main__":
    main()

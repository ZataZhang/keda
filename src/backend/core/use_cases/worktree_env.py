"""Propagate local env files into agent worktrees.

``git worktree add`` only materializes tracked files, so gitignored
``.env*`` files (secrets, local overrides) never reach a fresh worktree.
Agents that run ``just test`` or read ``DATABASE_URL`` from a worktree-local
``.env`` then fail or silently use the wrong configuration. This module
copies the missing env files from the main repository checkout into the
worktree, mirroring what the legacy ``scripts/worktree/create.sh`` did for
``just worktree``.

Design decisions:

- Only files that do **not** exist in the worktree are copied. Tracked
  ``.env*.example`` files are already materialized by git and stay
  untouched, and a worktree-local ``.env`` edited by a previous run is
  never overwritten — re-running on an existing worktree only heals gaps.
- ``.env.run-state`` is never copied (see
  :data:`ENV_COPY_EXCLUDED_FILE_NAMES`). It records the main checkout's
  ``just run`` port assignments, so inheriting it would point the worktree
  at the main repo's ports. The target repo's shared-database guard then
  sees a listener on that port and refuses to run tests, and ``just run``
  inside the worktree would clash with the main dev server. The worktree
  writes its own run-state (or none) instead.
- Unlike the legacy script there is no ``.env.example -> .env`` fallback:
  silently running an agent against example values is worse than a clear
  missing-config failure.
- Copying is best effort per file. A single unreadable file (broken
  symlink, permission error) is logged and skipped; it must never abort
  an agent run.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

_logger = logging.getLogger(__name__)

ENV_FILE_NAME_PREFIX = ".env"

# Env files that must never propagate into a worktree even though they match
# :data:`ENV_FILE_NAME_PREFIX`. ``.env.run-state`` holds the main checkout's
# ``just run`` backend/frontend port assignments: copying it would make the
# worktree reuse the main repo's ports, so the target repo's shared-database
# guard would see a listener there and block ``just test`` (false positive),
# and ``just run`` inside the worktree would clash with the main dev server.
# The worktree writes its own run-state (or none) instead.
ENV_COPY_EXCLUDED_FILE_NAMES = frozenset({".env.run-state"})

# Directories that must never contribute env files: VCS internals, package
# caches, build output, and — critically — embedded worktree containers,
# so one worktree's .env can never leak into another.
ENV_COPY_PRUNED_DIR_NAMES = frozenset(
    {
        ".git",
        ".iar-worktrees",
        ".venv",
        "venv",
        ".uv-cache",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "site",
    }
)

__all__ = [
    "ENV_COPY_EXCLUDED_FILE_NAMES",
    "ENV_COPY_PRUNED_DIR_NAMES",
    "ENV_FILE_NAME_PREFIX",
    "copy_missing_env_files",
]


def _is_inside(candidate_path: Path, container_path: Path) -> bool:
    """Return whether ``candidate_path`` is ``container_path`` or inside it."""
    try:
        candidate_path.relative_to(container_path)
    except ValueError:
        return False
    return True


def copy_missing_env_files(
    repo_root_path: Path,
    worktree_path: Path,
) -> list[Path]:
    """Copy ``.env*`` files from the main checkout into a worktree.

    Walks ``repo_root_path`` (pruning :data:`ENV_COPY_PRUNED_DIR_NAMES` and
    anything inside ``worktree_path`` itself), and copies every ``.env*``
    file whose relative path does not yet exist under ``worktree_path``.
    Existing files in the worktree are never overwritten, and names in
    :data:`ENV_COPY_EXCLUDED_FILE_NAMES` (``.env.run-state``) are skipped
    entirely.

    Args:
        repo_root_path: Absolute path to the main repository checkout that
            holds the canonical local env files.
        worktree_path: Absolute path to the target worktree.

    Returns:
        Repo-relative paths of the files that were actually copied, in
        walk order. Empty when nothing was missing or no env files exist.
    """
    repo_root_path = repo_root_path.resolve()
    worktree_path = worktree_path.resolve()
    if worktree_path == repo_root_path:
        return []

    copied_relative_paths: list[Path] = []
    for current_dir_text, child_dir_names, child_file_names in os.walk(repo_root_path):
        current_dir_path = Path(current_dir_text)
        child_dir_names[:] = [
            child_dir_name
            for child_dir_name in child_dir_names
            if child_dir_name not in ENV_COPY_PRUNED_DIR_NAMES
            and not _is_inside(current_dir_path / child_dir_name, worktree_path)
        ]
        for child_file_name in child_file_names:
            if not child_file_name.startswith(ENV_FILE_NAME_PREFIX):
                continue
            if child_file_name in ENV_COPY_EXCLUDED_FILE_NAMES:
                continue
            source_env_file_path = current_dir_path / child_file_name
            relative_env_file_path = source_env_file_path.relative_to(repo_root_path)
            target_env_file_path = worktree_path / relative_env_file_path
            # is_symlink() guards broken links: exists() follows the link
            # target, so a dangling symlink would otherwise look absent and
            # get clobbered.
            if target_env_file_path.exists() or target_env_file_path.is_symlink():
                continue
            try:
                target_env_file_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_env_file_path, target_env_file_path)
            except OSError as copy_error:
                _logger.warning(
                    "Skipped copying env file %s into worktree %s: %s",
                    source_env_file_path,
                    worktree_path,
                    copy_error,
                )
                continue
            copied_relative_paths.append(relative_env_file_path)
    return copied_relative_paths

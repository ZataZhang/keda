"""Derive preview deployment environment variables for CI usage.

This script reads the non-sensitive preview configuration from
``config.toml [preview]`` and prints shell-evaluable ``KEY=VALUE`` lines.
When running inside GitHub Actions, detected via the ``GITHUB_ENV`` environment
variable, the values are appended to that file for downstream steps.

Example:

    uv run python scripts/preview_env.py --pr 123 --sha deadbeefcafe
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from backend.core.use_cases.preview_deployment import render_preview_env
from backend.infrastructure.config.settings import config


def _read_repository_owner() -> str:
    """Read the repository owner from the GitHub Actions environment.

    `GITHUB_REPOSITORY_OWNER` 是 Actions 直接提供的 owner；退一步从
    `GITHUB_REPOSITORY`（`owner/repo` 形式）里切出 owner，便于在只设置了
    后者的本地或第三方 runner 上同样可用。

    Returns:
        仓库 owner；两个环境变量都缺失时返回空字符串。
    """
    repository_owner = os.getenv("GITHUB_REPOSITORY_OWNER", "").strip()
    if repository_owner:
        return repository_owner

    repository_slug = os.getenv("GITHUB_REPOSITORY", "").strip()
    return repository_slug.split("/", 1)[0] if "/" in repository_slug else ""


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Derive preview deployment environment variables.")
    parser.add_argument(
        "--pr",
        type=int,
        required=True,
        help="Pull request number.",
    )
    parser.add_argument(
        "--sha",
        type=str,
        required=True,
        help="Head commit SHA (full or shortened).",
    )
    return parser.parse_args()


def main() -> int:
    """Entrypoint for the preview environment derivation CLI."""
    args = _parse_args()
    preview = config.preview
    env_vars = render_preview_env(
        preview=preview,
        pr_number=args.pr,
        commit_sha=args.sha,
        repository_owner=_read_repository_owner(),
    )

    for key, value in env_vars.items():
        line = f"{key}={value}"
        print(line)

    github_env_path = os.getenv("GITHUB_ENV")
    if github_env_path:
        github_env_file = Path(github_env_path)
        with open(github_env_file, "a", encoding="utf-8") as env_file:
            for key, value in env_vars.items():
                env_file.write(f"{key}={value}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

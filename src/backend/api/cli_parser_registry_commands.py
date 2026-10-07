"""仓库注册表命令树（registry *）的 parser 构造。

从 :mod:`backend.api.cli_parser` 拆分而来：单文件超过 1000 非空行的 CI
硬上限后，按命令域把命令树搬到这里，行为逐字节不变。
"""

from __future__ import annotations

import argparse

from backend.api.cli_parser_options import (
    add_machine_output_options,
)


def add_registry_commands(subparsers: argparse._SubParsersAction) -> None:
    """注册仓库注册表命令树（registry *）。

    Args:
        subparsers: 顶层 parser 的 subparsers action（就地注册，不返回）。
    """
    registry_parser = subparsers.add_parser(
        "registry", help="Manage the repository registry in config.toml."
    )
    registry_subparsers = registry_parser.add_subparsers(dest="registry_command", required=True)
    registry_scan_parser = registry_subparsers.add_parser(
        "scan", help="Discover KedaCode-initialized git repositories under a path."
    )
    registry_scan_parser.add_argument(
        "scan_root",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory).",
    )
    registry_sync_parser = registry_subparsers.add_parser(
        "sync",
        help="Discover and register all KedaCode repositories under a path.",
    )
    registry_sync_parser.add_argument(
        "scan_root",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory).",
    )
    registry_sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates without writing to config.toml.",
    )

    registry_reinit_parser = registry_subparsers.add_parser(
        "reinit",
        help="Re-initialize an already registered repository's local config.",
    )
    registry_reinit_parser.add_argument(
        "--repo-id",
        required=True,
        help="Registry identifier of the repository to reinitialize.",
    )
    registry_reinit_parser.add_argument(
        "--remote",
        default="origin",
        help="Git remote name to write into .kedacode.toml (default: origin).",
    )
    registry_reinit_parser.add_argument(
        "--base-branch",
        default=None,
        help="Base branch to write into .kedacode.toml.",
    )
    registry_reinit_parser.add_argument(
        "--start-daemons",
        action="store_true",
        help="Restart daemon and review-daemon after reinitialization.",
    )

    registry_remove_parser = registry_subparsers.add_parser(
        "remove",
        help="Remove a repository from the registry and stop its daemons.",
    )
    registry_remove_parser.add_argument(
        "--repo-id",
        required=True,
        help="Registry identifier of the repository to remove.",
    )

    registry_list_parser = registry_subparsers.add_parser(
        "list",
        help="List registered repositories and their daemon status.",
    )
    add_machine_output_options(registry_list_parser)

    registry_start_parser = registry_subparsers.add_parser(
        "start",
        help="Start daemon and review-daemon for registered repositories.",
    )
    registry_start_group = registry_start_parser.add_mutually_exclusive_group(required=True)
    registry_start_group.add_argument(
        "--repo-id",
        help="Registry identifier of the repository to start daemons for.",
    )
    registry_start_group.add_argument(
        "--all",
        action="store_true",
        help="Start daemons for all enabled repositories.",
    )
    registry_start_parser.add_argument(
        "--no-review-daemon",
        action="store_true",
        help="Only start/stop the agent daemon, skip the review daemon.",
    )

    registry_stop_parser = registry_subparsers.add_parser(
        "stop",
        help="Stop daemon and review-daemon for registered repositories.",
    )
    registry_stop_group = registry_stop_parser.add_mutually_exclusive_group(required=True)
    registry_stop_group.add_argument(
        "--repo-id",
        help="Registry identifier of the repository to stop daemons for.",
    )
    registry_stop_group.add_argument(
        "--all",
        action="store_true",
        help="Stop daemons for all repositories with running processes.",
    )
    registry_stop_parser.add_argument(
        "--no-review-daemon",
        action="store_true",
        help="Only start/stop the agent daemon, skip the review daemon.",
    )

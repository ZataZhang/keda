"""``kc pr`` 命令树。"""

from __future__ import annotations

import argparse

from backend.api.cli_parser_options import add_common_options


def add_pr_commands(subparsers: argparse._SubParsersAction) -> None:
    """注册 PR 操作命令。

    Args:
        subparsers: 顶层 parser 的 subparsers action。
    """
    pr_parser = subparsers.add_parser("pr", help="Manage pull requests.")
    pr_subparsers = pr_parser.add_subparsers(dest="pr_command", required=True)
    aggregate_parser = pr_subparsers.add_parser(
        "aggregate",
        help="Combine completed Issue PRs into one verified Draft PR.",
    )
    aggregate_parser.set_defaults(command="pr aggregate")
    aggregate_parser.add_argument(
        "--issue",
        dest="issues",
        type=int,
        action="append",
        required=True,
        metavar="N",
        help="Source Issue number (repeat at least twice).",
    )
    aggregate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and show the source batch without creating branches or PRs.",
    )
    add_common_options(aggregate_parser)

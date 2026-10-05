"""仓库初始化与 Issue 相关命令树（init / labels / issue）的 parser 构造。

从 :mod:`backend.api.cli_parser` 拆分而来：单文件超过 1000 非空行的 CI
硬上限后，按命令域把命令树搬到这里，行为逐字节不变。
"""

from __future__ import annotations

import argparse

from backend.api.cli_parser_options import (
    add_all_repositories_option,
    add_common_options,
    add_machine_output_options,
    add_model_preset_options,
    agent_choices_with,
)


def add_setup_commands(subparsers: argparse._SubParsersAction) -> None:
    """注册仓库初始化与 Issue 相关命令树（init / labels / issue）。

    Args:
        subparsers: 顶层 parser 的 subparsers action（就地注册，不返回）。
    """
    init_parser = subparsers.add_parser("init", help="Create repository-local .iar.toml config.")
    init_parser.add_argument("--dry-run", action="store_true")
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument("--id", dest="repository_id")
    init_parser.add_argument("--display-name")
    init_parser.add_argument("--remote")
    init_parser.add_argument("--base-branch")
    init_parser.add_argument(
        "--no-update-gitignore",
        action="store_true",
        help=(
            "Do not add IAR runtime patterns (.iar/, .agent-runner/, "
            ".iar-worktrees/) to .gitignore. Default: managed by iar init."
        ),
    )

    labels_parser = subparsers.add_parser("labels", help="Manage GitHub labels.")
    labels_subparsers = labels_parser.add_subparsers(dest="labels_command", required=True)
    labels_sync_parser = labels_subparsers.add_parser(
        "sync", help="Sync standard labels to the repository."
    )
    add_common_options(labels_sync_parser)
    add_all_repositories_option(labels_sync_parser)

    issue_parser = subparsers.add_parser("issue", help="Create and manage GitHub Issues.")
    issue_subparsers = issue_parser.add_subparsers(dest="issue_command", required=True)
    issue_create_parser = issue_subparsers.add_parser(
        "create", help="Create GitHub Issues from one or more PRD files."
    )
    issue_create_parser.set_defaults(command="issue create")
    issue_create_parser.add_argument(
        "prd_paths",
        nargs="+",
        help="One or more PRD Markdown files or directories containing PRD files.",
    )
    issue_create_parser.add_argument(
        "--type", choices=("feature", "refactor", "bug"), default="feature"
    )
    issue_create_parser.add_argument("--title")
    issue_create_parser.add_argument(
        "--ready",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Add the ready label so a runner can pick the Issue up.",
    )
    issue_create_parser.add_argument(
        "--agent",
        choices=agent_choices_with(prefix=("auto",), suffix=("none",)),
        default="auto",
        help="Optional agent routing label to add to the Issue.",
    )
    issue_create_parser.add_argument(
        "--publish-prd",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Commit and push only the target PRD before adding the ready label "
            "(default: on; pass --no-publish-prd to defer publishing to the "
            "interactive prompt)."
        ),
    )
    issue_create_parser.add_argument("--force", action="store_true")
    issue_create_parser.add_argument(
        "--depends-on",
        action="append",
        type=int,
        default=[],
        help="Upstream Issue number this Issue depends on (repeatable).",
    )
    add_model_preset_options(issue_create_parser)
    add_machine_output_options(issue_create_parser)
    add_common_options(issue_create_parser)

    issue_list_parser = issue_subparsers.add_parser(
        "list", help="List Issues with linked Pull Request status."
    )
    issue_list_parser.set_defaults(command="issue list")
    issue_list_parser.add_argument(
        "--all-registered",
        action="store_true",
        help="Force multi-repository scan even when cwd is an iAR project repo.",
    )
    issue_list_parser.add_argument(
        "--state",
        choices=("open", "closed", "all"),
        default="all",
        help="Issue state filter (default: all).",
    )
    issue_list_parser.add_argument(
        "--label",
        default=None,
        help="Only show Issues carrying this label.",
    )
    issue_list_parser.add_argument(
        "--with-pr",
        action="store_true",
        default=False,
        help="Only show Issues with at least one linked PR.",
    )
    issue_list_parser.add_argument(
        "--without-pr",
        action="store_true",
        default=False,
        help="Only show Issues with no linked PRs.",
    )
    issue_list_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum Issues to fetch per repository (default: 100).",
    )
    add_machine_output_options(issue_list_parser)
    add_common_options(issue_list_parser)

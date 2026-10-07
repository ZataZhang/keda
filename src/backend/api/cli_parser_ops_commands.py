"""运维与调度命令树（workflow / container / takeover / loop / loop-daemon / backlog / config）的 parser 构造。

从 :mod:`backend.api.cli_parser` 拆分而来：单文件超过 1000 非空行的 CI
硬上限后，按命令域把命令树搬到这里，行为逐字节不变。
"""

from __future__ import annotations

import argparse

from backend.api.cli_parser_options import (
    add_common_options,
    add_machine_output_options,
)


def add_ops_commands(subparsers: argparse._SubParsersAction) -> None:
    """注册运维与调度命令树（workflow / container / takeover / loop / loop-daemon / backlog / config）。

    Args:
        subparsers: 顶层 parser 的 subparsers action（就地注册，不返回）。
    """
    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Install and manage bundled workflow templates.",
    )
    workflow_subparsers = workflow_parser.add_subparsers(dest="workflow_command", required=True)
    workflow_install_parser = workflow_subparsers.add_parser(
        "install",
        help="Install a bundled workflow template into the current repository.",
    )
    workflow_install_parser.set_defaults(command="workflow install")
    workflow_install_parser.add_argument("name", help="Workflow template name (e.g. 'preview').")
    workflow_install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing template files and [preview] section.",
    )
    workflow_install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the install plan without writing anything.",
    )
    add_common_options(workflow_install_parser)

    container_parser = subparsers.add_parser(
        "container",
        help="Manage the KedaCode runner container (auth import, up, down, logs).",
    )
    container_subparsers = container_parser.add_subparsers(dest="container_command", required=True)

    container_auth_parser = container_subparsers.add_parser(
        "auth",
        help="Manage the container-side authentication snapshot.",
    )
    container_auth_subparsers = container_auth_parser.add_subparsers(
        dest="container_auth_command", required=True
    )
    container_auth_import_parser = container_auth_subparsers.add_parser(
        "import",
        help=(
            "Snapshot the host's claude / codex / kimi CLI auth + skills into "
            "~/.kedacode/container-auth/ for the runner container to mount."
        ),
    )
    container_auth_import_parser.set_defaults(command="container auth import")

    container_up_parser = container_subparsers.add_parser(
        "up",
        help="Start the KedaCode runner container for the target repository.",
    )
    container_up_parser.set_defaults(command="container up")
    container_up_parser.add_argument(
        "--repo",
        default=None,
        help="Absolute path to the target Git repository to mount.",
    )
    container_up_parser.add_argument(
        "--repo-id",
        default=None,
        help=(
            "Repository registry id. When set, refuses to start if a host "
            "kc daemon is already serving this repo."
        ),
    )
    container_up_parser.add_argument(
        "--gh-token",
        default=None,
        help="GitHub token for the container's `gh` CLI. Falls back to $GH_TOKEN.",
    )
    container_up_parser.add_argument(
        "--build",
        action="store_true",
        help="Pass --build to `docker compose up` to rebuild the runner image.",
    )
    container_up_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the docker compose plan without invoking Docker.",
    )

    container_down_parser = container_subparsers.add_parser(
        "down",
        help="Stop and remove the KedaCode runner container.",
    )
    container_down_parser.set_defaults(command="container down")
    container_down_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the docker compose plan without invoking Docker.",
    )

    container_logs_parser = container_subparsers.add_parser(
        "logs",
        help="Stream the KedaCode runner container's logs to the terminal.",
    )
    container_logs_parser.set_defaults(command="container logs")
    container_logs_parser.add_argument(
        "--no-follow",
        action="store_true",
        help="Dump existing logs and exit instead of streaming.",
    )

    takeover_parser = subparsers.add_parser(
        "takeover",
        help="Take over GitHub repositories: clone, init, register, and start daemons.",
    )
    takeover_parser.add_argument(
        "--owner",
        default=None,
        help="GitHub user or organization whose repositories to list.",
    )
    takeover_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of repositories to fetch from GitHub (default: 100).",
    )
    takeover_parser.add_argument(
        "--clone-root",
        default=None,
        help="Directory where repositories will be cloned (default: ~/.kedacode/repos).",
    )
    takeover_parser.add_argument(
        "--repos",
        nargs="+",
        default=[],
        help="Non-interactive mode: list of owner/repo names to take over.",
    )
    takeover_parser.add_argument(
        "--no-start",
        action="store_true",
        help="Take over repositories without starting daemon processes.",
    )
    takeover_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the takeover plan without making changes.",
    )

    loop_parser = subparsers.add_parser(
        "loop",
        help="Register and manage recurring task generators.",
    )
    loop_subparsers = loop_parser.add_subparsers(dest="loop_command", required=True)

    loop_create_parser = loop_subparsers.add_parser(
        "create",
        help="Register a loop recipe as a persistent scheduler entry.",
    )
    loop_create_parser.set_defaults(command="loop create")
    loop_create_parser.add_argument(
        "loop_id",
        help="Short kebab-case identifier for the loop.",
    )
    loop_create_parser.add_argument(
        "--recipe",
        required=True,
        help="Path to the loop recipe Markdown file (with YAML frontmatter).",
    )
    loop_create_parser.add_argument(
        "--cron",
        default=None,
        help="5-field cron expression overriding the recipe's schedule.",
    )
    loop_create_parser.add_argument(
        "--every",
        default=None,
        help="Interval shorthand ('10m', '1h', '1d') overriding the schedule.",
    )
    loop_create_parser.add_argument(
        "--repo-id",
        dest="loop_repo_id",
        default=None,
        help="Override the recipe's repo_id when registering the loop.",
    )
    loop_create_parser.add_argument(
        "--repo",
        dest="loop_repo",
        default=None,
        help="Override the local path of the target repository.",
    )
    loop_create_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing loop entry with the same id.",
    )

    loop_list_parser = loop_subparsers.add_parser(
        "list",
        help="List all registered loops with their schedules and next fires.",
    )
    loop_list_parser.set_defaults(command="loop list")
    add_machine_output_options(loop_list_parser)

    loop_cancel_parser = loop_subparsers.add_parser(
        "cancel",
        help="Remove a loop entry from the local scheduler state.",
    )
    loop_cancel_parser.set_defaults(command="loop cancel")
    loop_cancel_parser.add_argument(
        "loop_id",
        help="Identifier of the loop to cancel.",
    )

    loop_run_parser = loop_subparsers.add_parser(
        "run",
        help="Trigger a loop manually for testing or recovery.",
    )
    loop_run_parser.set_defaults(command="loop run")
    loop_run_parser.add_argument(
        "--now",
        dest="now",
        action="store_true",
        required=True,
        help="Fire the loop once immediately.",
    )
    loop_run_parser.add_argument(
        "loop_id",
        help="Identifier of the loop to fire.",
    )
    loop_run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the PRD and report what would happen, no side effects.",
    )
    loop_run_parser.add_argument(
        "--repo-id",
        dest="loop_repo_id",
        default=None,
        help="Override the recipe's repo_id when running the loop.",
    )
    loop_run_parser.add_argument(
        "--repo",
        dest="loop_repo",
        default=None,
        help="Override the local path of the target repository.",
    )

    loop_daemon_parser = subparsers.add_parser(
        "loop-daemon",
        help="Run the loop scheduler continuously (polls ~/.kedacode/loop-state.json).",
    )
    loop_daemon_parser.set_defaults(command="loop-daemon")
    loop_daemon_parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Seconds between polling passes (default: 60 or $KEDACODE_LOOP_DAEMON_INTERVAL).",
    )
    loop_daemon_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect the next fire plan once and exit without writing anything.",
    )
    loop_daemon_parser.add_argument(
        "--repo-id",
        dest="loop_repo_id",
        default=None,
        help="Override the repository used to resolve all loop targets.",
    )
    loop_daemon_parser.add_argument(
        "--repo",
        dest="loop_repo",
        default=None,
        help="Override the local path of the target repository.",
    )

    backlog_parser = subparsers.add_parser(
        "backlog",
        help="Drive the backlog scheduler manually.",
    )
    backlog_subparsers = backlog_parser.add_subparsers(dest="backlog_command", required=True)

    backlog_advance_parser = backlog_subparsers.add_parser(
        "advance",
        help="Run one continuous-scheduling pass (reconcile + promote + discover).",
    )
    backlog_advance_parser.set_defaults(command="backlog advance")
    backlog_advance_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the plan for this pass without writing anything.",
    )
    add_common_options(backlog_advance_parser)

    config_parser = subparsers.add_parser(
        "config",
        help="Maintain local state: move ~/.iar, rename .iar.toml.",  # legacy-alias
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    config_migrate_parser = config_subparsers.add_parser(
        "migrate",
        help="Move ~/.iar to ~/.kedacode, rename .iar.toml, drop pinned values.",  # legacy-alias
    )
    config_migrate_parser.set_defaults(command="config migrate")
    config_migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the whole plan (state directory, rename, diff) without writing anything.",
    )
    add_common_options(config_migrate_parser)

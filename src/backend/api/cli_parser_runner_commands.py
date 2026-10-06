"""agent 运行与守护命令树（run / daemon / logs / review / review-daemon / recover / blocked-continue）的 parser 构造。

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


def add_runner_commands(subparsers: argparse._SubParsersAction) -> None:
    """注册agent 运行与守护命令树（run / daemon / logs / review / review-daemon / recover / blocked-continue）。

    Args:
        subparsers: 顶层 parser 的 subparsers action（就地注册，不返回）。
    """
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "prd_path",
        nargs="?",
        default=None,
        metavar="PRD_PATH",
        help="Target PRD path (resolved via its '- GitHub Issue:' link).",
    )
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument(
        "--issue",
        type=int,
        default=None,
        metavar="N",
        help="Target Issue number: only that Issue is processed this pass.",
    )
    run_parser.add_argument(
        "--all-ready",
        action="store_true",
        default=False,
        help="Process the ready queue by priority (the historical iar run behavior).",
    )
    run_parser.add_argument(
        "--takeover",
        action="store_true",
        default=False,
        help="Stop a running daemon gracefully, reclaim its in-flight Issues, then run.",
    )
    run_parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip the takeover confirmation prompt (required with --json).",
    )
    run_parser.add_argument(
        "--fast-merge",
        action="store_true",
        default=False,
        help=(
            "One-shot fast track for this run: skip the validation gates (rv "
            "re-exec + independent verifier) after the builder commits and "
            "publish the Draft PR with an unverified self-declaration. "
            "Rejected for stack-dependency Issues; requires a targeted run."
        ),
    )
    run_parser.add_argument(
        "--direct-pr",
        action="store_true",
        default=False,
        help=(
            "One-shot direct tier for this run: after the execution agent commits, "
            "only the mechanical steps remain (runner-controlled commit -> push -> "
            "Draft PR). Skips the pre-PR review agent, the runner verification "
            "commands and the validation gates; CI on the Draft PR becomes the gate. "
            "Only for Issues without a PRD anchor; mutually exclusive with "
            "--fast-merge; requires a targeted run."
        ),
    )
    run_parser.add_argument("--agent", choices=agent_choices_with(prefix=("auto",)), default="auto")
    run_parser.add_argument("--max-issues", type=int)
    add_model_preset_options(run_parser)
    add_machine_output_options(run_parser)
    add_common_options(run_parser)
    add_all_repositories_option(run_parser)

    daemon_run_options = argparse.ArgumentParser(add_help=False)
    daemon_run_options.add_argument("--interval", type=int, default=None)
    daemon_run_options.add_argument(
        "--agent", choices=agent_choices_with(prefix=("auto",)), default="auto"
    )
    daemon_run_options.add_argument("--max-issues", type=int)
    add_model_preset_options(daemon_run_options)
    daemon_run_options.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help=(
            "Number of Issues to process in parallel per pass. Defaults to "
            "[agent_runner.runner].max_concurrent_issues (1 = sequential). "
            ">1 shows a per-Issue live view on a TTY and writes per-Issue logs."
        ),
    )
    daemon_run_options.add_argument(
        "--autopilot",
        dest="autopilot_override",
        action="store_true",
        default=None,
        help="Enable the scheduling autopilot for this run (never arms auto-merge).",
    )
    daemon_run_options.add_argument(
        "--no-autopilot",
        dest="autopilot_override",
        action="store_false",
        help="Disable the scheduling autopilot for this daemon run.",
    )
    add_common_options(daemon_run_options)
    add_all_repositories_option(daemon_run_options)

    daemon_parser = subparsers.add_parser(
        "daemon",
        parents=[daemon_run_options],
        help=(
            "Run the agent runner continuously for the current initialized "
            "repository (or pass --all to target every enabled registry entry)."
        ),
    )
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command")

    daemon_subparsers.add_parser("run", parents=[daemon_run_options])

    daemon_status_parser = daemon_subparsers.add_parser("status")
    add_machine_output_options(daemon_status_parser)
    add_common_options(daemon_status_parser)
    add_all_repositories_option(daemon_status_parser)

    daemon_parser.set_defaults(daemon_command="run")

    logs_parser = subparsers.add_parser(
        "logs",
        help="Tail the daemon or review-daemon process log.",
    )
    logs_parser.add_argument(
        "--kind",
        choices=("daemon", "review_daemon"),
        default=None,
        help="Process kind (default: daemon).",
    )
    logs_parser.add_argument(
        "--lines",
        "-n",
        type=int,
        default=200,
        help="Number of lines to show from the tail (default: 200).",
    )
    logs_parser.add_argument(
        "--issue",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Read the per-Issue agent output log for the given Issue number "
            "(mutually exclusive with --kind)."
        ),
    )
    logs_parser.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="Follow the log in real time (Ctrl-C to stop).",
    )
    add_machine_output_options(logs_parser)
    add_common_options(logs_parser)

    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("--dry-run", action="store_true")
    review_parser.add_argument(
        "--agent", choices=agent_choices_with(prefix=("auto",)), default="auto"
    )
    review_parser.add_argument("--max-issues", type=int)
    add_model_preset_options(review_parser)
    add_common_options(review_parser)
    add_all_repositories_option(review_parser)

    review_daemon_parser = subparsers.add_parser(
        "review-daemon",
        help=(
            "Run supervisor review continuously for the current initialized "
            "repository (or pass --all to target every enabled registry entry)."
        ),
    )
    review_daemon_parser.add_argument("--interval", type=int, default=None)
    review_daemon_parser.add_argument(
        "--agent", choices=agent_choices_with(prefix=("auto",)), default="auto"
    )
    review_daemon_parser.add_argument("--max-issues", type=int)
    add_model_preset_options(review_daemon_parser)
    add_common_options(review_daemon_parser)
    add_all_repositories_option(review_daemon_parser)

    recover_parser = subparsers.add_parser(
        "recover",
        help="Resume a failed publish operation for an Issue.",
    )
    recover_parser.add_argument(
        "--issue",
        type=int,
        required=True,
        help="Issue number to recover publish for.",
    )
    recover_parser.add_argument(
        "--branch",
        default=None,
        help="Explicitly confirm the current branch name.",
    )
    add_common_options(recover_parser)

    blocked_continue_parser = subparsers.add_parser(
        "blocked-continue",
        help="Resume a blocked Issue after resolving forbidden paths.",
    )
    blocked_continue_parser.add_argument(
        "--issue",
        type=int,
        required=True,
        help="Issue number to continue.",
    )
    blocked_continue_parser.add_argument(
        "--agent",
        choices=agent_choices_with(prefix=("auto",)),
        default="auto",
        help="Agent runner to use.",
    )
    add_common_options(blocked_continue_parser)

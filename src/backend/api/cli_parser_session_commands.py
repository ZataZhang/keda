"""交互式会话与工作区命令树（ask / repl / deliberate / agent / worktree）的 parser 构造。

从 :mod:`backend.api.cli_parser` 拆分而来：单文件超过 1000 非空行的 CI
硬上限后，按命令域把命令树搬到这里，行为逐字节不变。
"""

from __future__ import annotations

import argparse

from backend.api.cli_parser_options import (
    add_common_options,
    add_machine_output_options,
    add_model_preset_options,
    agent_choices_with,
    registered_agent_names,
)


def add_session_commands(subparsers: argparse._SubParsersAction) -> None:
    """注册交互式会话与工作区命令树（ask / repl / deliberate / agent / worktree）。

    Args:
        subparsers: 顶层 parser 的 subparsers action（就地注册，不返回）。
    """
    ask_parser = subparsers.add_parser(
        "ask", help="Ask the agent runner to decide the next safe action."
    )
    ask_parser.add_argument("prompt", help="Natural language request.")
    ask_parser.add_argument(
        "--agent",
        choices=agent_choices_with(prefix=("auto",)),
        default="auto",
        help="Planner agent to use.",
    )
    ask_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only generate plan without executing.",
    )
    ask_parser.add_argument(
        "--execute",
        action="store_true",
        help="Allow execution after confirmation.",
    )
    ask_parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-confirm non-interactive execution.",
    )
    ask_parser.add_argument(
        "--output",
        default=None,
        help="Output directory for decision audit.",
    )
    add_model_preset_options(ask_parser)
    add_common_options(ask_parser)

    repl_parser = subparsers.add_parser(
        "repl",
        help="Run the interactive REPL session (equivalent to `kc` with no subcommand).",
    )
    repl_parser.add_argument(
        "--agent",
        choices=agent_choices_with(),
        default=None,
        help="Override the REPL agent (defaults to [agent_runner.repl].default_agent).",
    )
    add_common_options(repl_parser)

    deliberate_parser = subparsers.add_parser(
        "deliberate", help="Run a multi-agent deliberation session."
    )
    deliberate_parser.add_argument("prompt", help="The requirement or question to deliberate.")
    deliberate_parser.add_argument(
        "--agents",
        default="architect,skeptic,implementer",
        help="Comma-separated participant profile IDs.",
    )
    deliberate_parser.add_argument(
        "--rounds", type=int, default=None, help="Number of discussion rounds."
    )
    deliberate_parser.add_argument("--synthesizer", default=None, help="Agent to run synthesis.")
    deliberate_parser.add_argument(
        "--output",
        default=None,
        help="Output directory for deliberation files.",
    )
    deliberate_parser.add_argument(
        "--session-id", default=None, help="Optional session ID for reproducibility."
    )
    deliberate_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero exit code if any agent fails.",
    )
    add_common_options(deliberate_parser)

    agent_parser = subparsers.add_parser(
        "agent", help="Inspect registered agents and output protocols (read-only)."
    )
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_list_parser = agent_subparsers.add_parser(
        "list", help="List registered agents and their profiles."
    )
    agent_list_parser.set_defaults(command="agent list")
    add_machine_output_options(agent_list_parser)
    agent_doctor_parser = agent_subparsers.add_parser(
        "doctor",
        help="Parse and print each profile's full argv for the given agents.",
    )
    agent_doctor_parser.set_defaults(command="agent doctor")
    agent_doctor_parser.add_argument(
        "agent_names",
        nargs="*",
        choices=registered_agent_names(),
        help="One or more registered agent names (omit with --protocols).",
    )
    agent_doctor_parser.add_argument(
        "--all-profiles",
        action="store_true",
        help="Print every declared profile instead of only the run profile.",
    )
    agent_doctor_parser.add_argument(
        "--output",
        choices=("table", "json"),
        default="table",
        help="Output format: table|json.",
    )
    agent_doctor_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit stable sorted JSON (agent / profile / argv / prompt_delivery).",
    )
    agent_doctor_parser.add_argument(
        "--protocols",
        action="store_true",
        help="List all registered output protocol ids and exit.",
    )
    agent_doctor_parser.add_argument(
        "--prompt",
        default="golden-prompt",
        help="Sentinel prompt used when expanding argv (default: golden-prompt).",
    )
    agent_doctor_parser.add_argument(
        "--preset",
        default=None,
        help=(
            "Resolve argv as if a named model preset were applied "
            "(injects the preset's model / reasoning effort args)."
        ),
    )
    agent_doctor_parser.add_argument(
        "--model",
        default=None,
        help="One-shot model id override for --preset.",
    )
    agent_doctor_parser.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        default=None,
        help="One-shot reasoning effort override for --preset.",
    )
    agent_doctor_parser.add_argument(
        "--lifecycle",
        default=None,
        help=(
            "Print the resolved agent + argv for a lifecycle stage "
            "(nine-key closed set), applying any stage -> preset binding."
        ),
    )
    agent_presets_parser = agent_subparsers.add_parser(
        "presets",
        help="List defined model presets and their (agent, model, reasoning effort).",
    )
    agent_presets_parser.set_defaults(command="agent presets")
    add_machine_output_options(agent_presets_parser)

    worktree_parser = subparsers.add_parser(
        "worktree",
        help="Manage iAR-owned Git worktrees for the current repository.",
    )
    worktree_subparsers = worktree_parser.add_subparsers(dest="worktree_command", required=True)
    worktree_create_parser = worktree_subparsers.add_parser(
        "create", help="Create a worktree at .iar-worktrees/<branch>."
    )
    worktree_create_parser.add_argument("--branch", required=True, help="Branch name to create.")
    worktree_create_parser.add_argument(
        "--base-branch", required=True, help="Existing branch to fork from."
    )
    worktree_path_parser = worktree_subparsers.add_parser(
        "path", help="Print the absolute worktree path for a branch."
    )
    worktree_path_parser.add_argument("--branch", required=True, help="Branch name to resolve.")
    add_machine_output_options(worktree_path_parser)
    worktree_remove_parser = worktree_subparsers.add_parser(
        "remove", help="Remove a worktree and prune Git metadata."
    )
    worktree_remove_parser.add_argument(
        "--branch", required=True, help="Branch name whose worktree to remove."
    )
    worktree_cleanup_parser = worktree_subparsers.add_parser(
        "cleanup",
        help="Delete stale local issue branches whose Issue is closed.",
    )
    worktree_cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview cleanup without deleting anything.",
    )
    worktree_cleanup_parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete eligible branches and worktrees.",
    )
    worktree_cleanup_parser.add_argument(
        "--force",
        action="store_true",
        help="Also delete dirty or unmerged eligible branches.",
    )

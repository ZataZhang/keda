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

    # kc agent lifecycle / preset / fallback：生命周期统一设置的 argparse 门面。
    # list 是机读命令（--output 枚举），必须与 Typer 侧同口径挂 add_machine_output_options，
    # 才能通过 tests/test_cli_schema.py 的门面旗标对齐守卫；写命令只带 --json 别名。
    agent_lifecycle_parser = agent_subparsers.add_parser(
        "lifecycle", help="View and persist lifecycle stage → preset bindings."
    )
    agent_lifecycle_subparsers = agent_lifecycle_parser.add_subparsers(
        dest="agent_lifecycle_command", required=True
    )
    agent_lifecycle_list_parser = agent_lifecycle_subparsers.add_parser(
        "list", help="List nine lifecycle stages with effective agent/model/effort and sources."
    )
    agent_lifecycle_list_parser.set_defaults(command="agent lifecycle list")
    agent_lifecycle_list_parser.add_argument(
        "--scope",
        choices=("effective", "global", "repository"),
        default="effective",
        help="Read scope: effective|global|repository.",
    )
    add_common_options(agent_lifecycle_list_parser)
    add_machine_output_options(agent_lifecycle_list_parser)

    agent_lifecycle_set_parser = agent_lifecycle_subparsers.add_parser(
        "set", help="Bind a lifecycle stage to a named preset (persistent write)."
    )
    agent_lifecycle_set_parser.set_defaults(command="agent lifecycle set")
    agent_lifecycle_set_parser.add_argument("stage", help="Lifecycle stage key to bind.")
    agent_lifecycle_set_parser.add_argument(
        "--preset", required=True, help="Named preset to bind to the stage."
    )
    agent_lifecycle_set_parser.add_argument(
        "--scope", required=True, choices=("global", "repository"), help="Write scope."
    )
    agent_lifecycle_set_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias of --output json."
    )
    add_common_options(agent_lifecycle_set_parser)

    agent_lifecycle_unset_parser = agent_lifecycle_subparsers.add_parser(
        "unset", help="Remove a lifecycle stage's preset binding (persistent write)."
    )
    agent_lifecycle_unset_parser.set_defaults(command="agent lifecycle unset")
    agent_lifecycle_unset_parser.add_argument("stage", help="Lifecycle stage key to unbind.")
    agent_lifecycle_unset_parser.add_argument(
        "--scope", required=True, choices=("global", "repository"), help="Write scope."
    )
    agent_lifecycle_unset_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias of --output json."
    )
    add_common_options(agent_lifecycle_unset_parser)

    agent_preset_parser = agent_subparsers.add_parser(
        "preset", help="Upsert named model presets (agent / model / reasoning effort)."
    )
    agent_preset_subparsers = agent_preset_parser.add_subparsers(
        dest="agent_preset_command", required=True
    )
    agent_preset_set_parser = agent_preset_subparsers.add_parser(
        "set", help="Create or update a named model preset (upsert)."
    )
    agent_preset_set_parser.set_defaults(command="agent preset set")
    agent_preset_set_parser.add_argument("name", help="Preset name to create or update.")
    agent_preset_set_parser.add_argument("--agent", required=True, help="Agent the preset runs.")
    agent_preset_set_parser.add_argument(
        "--model", default=None, help="Model id the preset sets (omit = unset)."
    )
    agent_preset_set_parser.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        default=None,
        help="Reasoning effort the preset sets (omit = unset).",
    )
    agent_preset_set_parser.add_argument(
        "--scope", required=True, choices=("global", "repository"), help="Write scope."
    )
    agent_preset_set_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias of --output json."
    )
    add_common_options(agent_preset_set_parser)

    agent_fallback_parser = agent_subparsers.add_parser(
        "fallback", help="View and persist executor fallback candidates."
    )
    agent_fallback_subparsers = agent_fallback_parser.add_subparsers(
        dest="agent_fallback_command", required=True
    )
    agent_fallback_list_parser = agent_fallback_subparsers.add_parser(
        "list", help="List ordered executor fallback candidates with presets and budget."
    )
    agent_fallback_list_parser.set_defaults(command="agent fallback list")
    agent_fallback_list_parser.add_argument(
        "--scope",
        choices=("effective", "global", "repository"),
        default="effective",
        help="Read scope: effective|global|repository.",
    )
    add_common_options(agent_fallback_list_parser)
    add_machine_output_options(agent_fallback_list_parser)

    agent_candidate_parser = agent_fallback_subparsers.add_parser(
        "candidate", help="Edit the ordered executor fallback candidate list."
    )
    agent_candidate_subparsers = agent_candidate_parser.add_subparsers(
        dest="agent_fallback_candidate_command", required=True
    )
    agent_candidate_add_parser = agent_candidate_subparsers.add_parser(
        "add", help="Insert an executor fallback candidate (persistent write)."
    )
    agent_candidate_add_parser.set_defaults(command="agent fallback candidate add")
    agent_candidate_add_parser.add_argument("--agent", required=True, help="Candidate agent name.")
    agent_candidate_add_parser.add_argument(
        "--preset", default=None, help="Optional same-agent preset."
    )
    agent_candidate_add_parser.add_argument(
        "--position", type=int, default=None, help="1-based insert position (default: append)."
    )
    agent_candidate_add_parser.add_argument(
        "--scope", required=True, choices=("global", "repository"), help="Write scope."
    )
    agent_candidate_add_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias of --output json."
    )
    add_common_options(agent_candidate_add_parser)

    agent_candidate_remove_parser = agent_candidate_subparsers.add_parser(
        "remove", help="Remove the candidate at a 1-based position (persistent write)."
    )
    agent_candidate_remove_parser.set_defaults(command="agent fallback candidate remove")
    agent_candidate_remove_parser.add_argument(
        "position", type=int, help="1-based position of the candidate to remove."
    )
    agent_candidate_remove_parser.add_argument(
        "--scope", required=True, choices=("global", "repository"), help="Write scope."
    )
    agent_candidate_remove_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias of --output json."
    )
    add_common_options(agent_candidate_remove_parser)

    agent_candidate_move_parser = agent_candidate_subparsers.add_parser(
        "move", help="Move a candidate to a new 1-based position (persistent write)."
    )
    agent_candidate_move_parser.set_defaults(command="agent fallback candidate move")
    agent_candidate_move_parser.add_argument(
        "position", type=int, help="1-based position of the candidate to move."
    )
    agent_candidate_move_parser.add_argument(
        "--to", type=int, required=True, help="1-based destination position."
    )
    agent_candidate_move_parser.add_argument(
        "--scope", required=True, choices=("global", "repository"), help="Write scope."
    )
    agent_candidate_move_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias of --output json."
    )
    add_common_options(agent_candidate_move_parser)

    agent_candidate_preset_parser = agent_candidate_subparsers.add_parser(
        "preset", help="Bind or clear a named preset on one fallback candidate."
    )
    agent_candidate_preset_subparsers = agent_candidate_preset_parser.add_subparsers(
        dest="agent_fallback_candidate_preset_command", required=True
    )
    agent_candidate_preset_set_parser = agent_candidate_preset_subparsers.add_parser(
        "set", help="Bind a same-agent preset to a candidate (persistent write)."
    )
    agent_candidate_preset_set_parser.set_defaults(command="agent fallback candidate preset set")
    agent_candidate_preset_set_parser.add_argument(
        "position", type=int, help="1-based candidate position to bind."
    )
    agent_candidate_preset_set_parser.add_argument(
        "--preset", required=True, help="Same-agent preset to bind."
    )
    agent_candidate_preset_set_parser.add_argument(
        "--scope", required=True, choices=("global", "repository"), help="Write scope."
    )
    agent_candidate_preset_set_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias of --output json."
    )
    add_common_options(agent_candidate_preset_set_parser)

    agent_candidate_preset_unset_parser = agent_candidate_preset_subparsers.add_parser(
        "unset", help="Clear the preset binding on a candidate (persistent write)."
    )
    agent_candidate_preset_unset_parser.set_defaults(
        command="agent fallback candidate preset unset"
    )
    agent_candidate_preset_unset_parser.add_argument(
        "position", type=int, help="1-based candidate position to clear."
    )
    agent_candidate_preset_unset_parser.add_argument(
        "--scope", required=True, choices=("global", "repository"), help="Write scope."
    )
    agent_candidate_preset_unset_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias of --output json."
    )
    add_common_options(agent_candidate_preset_unset_parser)

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

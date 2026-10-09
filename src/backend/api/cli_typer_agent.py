"""Typer commands for the agent-driven decision flows and agent inspection.

Holds :func:`ask_command` (natural-language decision entrypoint),
:func:`repl_command` (interactive REPL),
:func:`deliberate_command` (multi-agent deliberation), and the
read-only ``kc agent list`` / ``kc agent doctor`` inspection commands.
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import (
    ConfigOption,
    JsonOutputOption,
    OutputFormat,
    OutputOption,
    RepoIdOption,
    RepoOption,
    RunAgentChoice,
    _HELP_CONTEXT,
    _enum_value,
    _run_typer_command,
    _run_typer_repository_command,
    _typer_selector_options,
    agent_app,
    app,
)

# 生命周期统一设置的命令子树（挂在只读的 ``agent`` 之下）：lifecycle / preset /
# fallback candidate 三类写命令复用同一 core 用例，写入目标由 --scope 决定。
lifecycle_app = typer.Typer(
    help="View and persist lifecycle stage → model preset bindings.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
preset_app = typer.Typer(
    help="Upsert named model presets (agent / model / reasoning effort).",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
fallback_app = typer.Typer(
    help="View and persist executor fallback candidates.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
candidate_app = typer.Typer(
    help="Edit the ordered executor fallback candidate list.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
candidate_preset_app = typer.Typer(
    help="Bind or clear a named preset on one executor fallback candidate.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
agent_app.add_typer(lifecycle_app, name="lifecycle")
agent_app.add_typer(preset_app, name="preset")
agent_app.add_typer(fallback_app, name="fallback")
fallback_app.add_typer(candidate_app, name="candidate")
candidate_app.add_typer(candidate_preset_app, name="preset")

ReadScopeOption = Annotated[
    str,
    typer.Option(
        "--scope",
        help="Read scope: effective (cwd-derived) | global | repository.",
    ),
]
WriteScopeOption = Annotated[
    str | None,
    typer.Option(
        "--scope",
        help="Write scope (required): global | repository; repository needs --repo-id or --repo.",
    ),
]


@app.command("ask")
def ask_command(
    ctx: typer.Context,
    prompt: Annotated[str, typer.Argument(help="Natural language request.")],
    agent: Annotated[
        RunAgentChoice,
        typer.Option("--agent", help="Planner agent to use."),
    ] = RunAgentChoice.auto,
    plan_only: Annotated[
        bool,
        typer.Option("--plan-only", help="Only generate plan without executing."),
    ] = False,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Allow execution after confirmation."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Auto-confirm non-interactive execution."),
    ] = False,
    output: Annotated[
        str | None,
        typer.Option("--output", help="Output directory for decision audit."),
    ] = None,
    preset: Annotated[
        str | None,
        typer.Option(
            "--preset",
            help="Anchor the planner stage to a named model preset (one-shot).",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="One-shot model id override for the preset / binding."),
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        typer.Option(
            "--reasoning-effort",
            help="One-shot reasoning effort override for the preset / binding.",
        ),
    ] = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Ask the agent runner to decide the next safe action."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "ask",
        **selector_options,
        prompt=prompt,
        agent=_enum_value(agent),
        plan_only=plan_only,
        execute=execute,
        yes=yes,
        output=output,
        preset=preset,
        model=model,
        reasoning_effort=reasoning_effort,
    )


@app.command("repl")
def repl_command(
    ctx: typer.Context,
    agent: Annotated[
        RunAgentChoice,
        typer.Option(
            "--agent",
            help="Override the REPL agent (defaults to [agent_runner.repl].default_agent).",
        ),
    ] = RunAgentChoice.claude,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Run the interactive REPL session."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "repl",
        **selector_options,
        agent=_enum_value(agent),
    )


@app.command("deliberate")
def deliberate_command(
    ctx: typer.Context,
    prompt: Annotated[str, typer.Argument(help="Requirement or question.")],
    agents: Annotated[
        str,
        typer.Option("--agents", help="Comma-separated participant profile IDs."),
    ] = "architect,skeptic,implementer",
    rounds: Annotated[
        int | None, typer.Option("--rounds", help="Number of discussion rounds.")
    ] = None,
    synthesizer: Annotated[
        str | None,
        typer.Option("--synthesizer", help="Agent to run synthesis."),
    ] = None,
    output: Annotated[str | None, typer.Option("--output", help="Output directory.")] = None,
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Optional session ID for reproducibility."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Return non-zero exit code if any agent fails."),
    ] = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Run a multi-agent deliberation session."""
    return _run_typer_repository_command(
        ctx,
        "deliberate",
        repo=repo,
        repo_id=repo_id,
        config=config,
        prompt=prompt,
        agents=agents,
        rounds=rounds,
        synthesizer=synthesizer,
        output=output,
        session_id=session_id,
        strict=strict,
    )


@agent_app.command("list")
def agent_list_command(
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """List registered agents and their profiles."""
    return _run_typer_command("agent list", output=_enum_value(output), as_json=as_json)


@agent_app.command("presets")
def agent_presets_command(
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """List defined model presets and their (agent, model, reasoning effort)."""
    return _run_typer_command("agent presets", output=_enum_value(output), as_json=as_json)


@agent_app.command("doctor")
def agent_doctor_command(
    agent_names: Annotated[
        list[str] | None,
        typer.Argument(help="One or more registered agent names (omit with --protocols)."),
    ] = None,
    all_profiles: Annotated[
        bool,
        typer.Option("--all-profiles", help="Print every declared profile instead of only run."),
    ] = False,
    output: OutputOption = OutputFormat.table,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit stable sorted JSON (agent / profile / argv / prompt_delivery).",
        ),
    ] = False,
    protocols: Annotated[
        bool,
        typer.Option("--protocols", help="List all registered output protocol ids and exit."),
    ] = False,
    prompt: Annotated[
        str,
        typer.Option(
            "--prompt",
            help="Sentinel prompt used when expanding argv (default: golden-prompt).",
        ),
    ] = "golden-prompt",
    preset: Annotated[
        str | None,
        typer.Option(
            "--preset",
            help="Resolve argv as if a named model preset were applied.",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="One-shot model id override for --preset."),
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        typer.Option("--reasoning-effort", help="One-shot reasoning effort override for --preset."),
    ] = None,
    lifecycle: Annotated[
        str | None,
        typer.Option(
            "--lifecycle",
            help="Print the resolved agent + argv for a lifecycle stage (nine-key closed set).",
        ),
    ] = None,
) -> int:
    """Parse and print each profile's full argv for the given agents."""
    return _run_typer_command(
        "agent doctor",
        agent_names=agent_names,
        all_profiles=all_profiles,
        output=_enum_value(output),
        as_json=as_json,
        protocols=protocols,
        prompt=prompt,
        preset=preset,
        model=model,
        reasoning_effort=reasoning_effort,
        lifecycle=lifecycle,
    )


__all__ = [
    "agent_doctor_command",
    "agent_list_command",
    "ask_command",
    "deliberate_command",
    "repl_command",
    "lifecycle_list_command",
    "lifecycle_set_command",
    "lifecycle_unset_command",
    "preset_set_command",
    "fallback_list_command",
    "fallback_candidate_add_command",
    "fallback_candidate_remove_command",
    "fallback_candidate_move_command",
    "fallback_candidate_preset_set_command",
    "fallback_candidate_preset_unset_command",
]


@lifecycle_app.command("list")
def lifecycle_list_command(
    ctx: typer.Context,
    scope: ReadScopeOption = "effective",
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """List the nine lifecycle stages with effective agent/model/effort and sources."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent lifecycle list",
        **selector_options,
        scope=scope,
        output=_enum_value(output),
        as_json=as_json,
    )


@lifecycle_app.command("set")
def lifecycle_set_command(
    ctx: typer.Context,
    stage: Annotated[str, typer.Argument(help="Lifecycle stage key to bind.")],
    preset: Annotated[str, typer.Option("--preset", help="Named preset to bind to the stage.")],
    scope: WriteScopeOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """Bind a lifecycle stage to a named model preset (persistent write)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent lifecycle set",
        **selector_options,
        scope=scope,
        stage=stage,
        preset=preset,
        as_json=as_json,
    )


@lifecycle_app.command("unset")
def lifecycle_unset_command(
    ctx: typer.Context,
    stage: Annotated[str, typer.Argument(help="Lifecycle stage key to unbind.")],
    scope: WriteScopeOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """Remove a lifecycle stage's preset binding in the target scope (persistent write)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent lifecycle unset",
        **selector_options,
        scope=scope,
        stage=stage,
        as_json=as_json,
    )


@preset_app.command("set")
def preset_set_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Preset name to create or update (upsert).")],
    agent: Annotated[str, typer.Option("--agent", help="Agent the preset runs.")],
    model: Annotated[
        str | None, typer.Option("--model", help="Model id the preset sets (omit = unset).")
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        typer.Option("--reasoning-effort", help="Reasoning effort the preset sets (omit = unset)."),
    ] = None,
    scope: WriteScopeOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """Upsert a named model preset's (agent, model, reasoning effort) triple."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent preset set",
        **selector_options,
        scope=scope,
        name=name,
        agent=agent,
        model=model,
        reasoning_effort=reasoning_effort,
        as_json=as_json,
    )


@fallback_app.command("list")
def fallback_list_command(
    ctx: typer.Context,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    scope: ReadScopeOption = "effective",
) -> int:
    """List ordered executor fallback candidates with per-candidate preset and sources."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent fallback list",
        **selector_options,
        scope=scope,
        output=_enum_value(output),
        as_json=as_json,
    )


@candidate_app.command("add")
def fallback_candidate_add_command(
    ctx: typer.Context,
    agent: Annotated[str, typer.Option("--agent", help="Candidate agent name.")],
    preset: Annotated[
        str | None, typer.Option("--preset", help="Optional same-agent preset.")
    ] = None,
    position: Annotated[
        int | None, typer.Option("--position", help="1-based insert position (default: append).")
    ] = None,
    scope: WriteScopeOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """Insert an executor fallback candidate into the ordered list (persistent write)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent fallback candidate add",
        **selector_options,
        scope=scope,
        agent=agent,
        preset=preset,
        position=position,
        as_json=as_json,
    )


@candidate_app.command("remove")
def fallback_candidate_remove_command(
    ctx: typer.Context,
    position: Annotated[int, typer.Argument(help="1-based position of the candidate to remove.")],
    scope: WriteScopeOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """Remove the executor fallback candidate at a 1-based position (persistent write)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent fallback candidate remove",
        **selector_options,
        scope=scope,
        position=position,
        as_json=as_json,
    )


@candidate_app.command("move")
def fallback_candidate_move_command(
    ctx: typer.Context,
    position: Annotated[int, typer.Argument(help="1-based position of the candidate to move.")],
    to: Annotated[int, typer.Option("--to", help="1-based destination position after the move.")],
    scope: WriteScopeOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """Reorder an executor fallback candidate to a new 1-based position (persistent write)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent fallback candidate move",
        **selector_options,
        scope=scope,
        position=position,
        to=to,
        as_json=as_json,
    )


@candidate_preset_app.command("set")
def fallback_candidate_preset_set_command(
    ctx: typer.Context,
    position: Annotated[int, typer.Argument(help="1-based candidate position to bind.")],
    preset: Annotated[str, typer.Option("--preset", help="Same-agent preset to bind.")],
    scope: WriteScopeOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """Bind a same-agent preset to an existing fallback candidate (persistent write)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent fallback candidate preset set",
        **selector_options,
        scope=scope,
        position=position,
        preset=preset,
        as_json=as_json,
    )


@candidate_preset_app.command("unset")
def fallback_candidate_preset_unset_command(
    ctx: typer.Context,
    position: Annotated[int, typer.Argument(help="1-based candidate position to clear.")],
    scope: WriteScopeOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    as_json: JsonOutputOption = False,
    config: ConfigOption = None,
) -> int:
    """Clear the preset binding on a fallback candidate (persistent write)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "agent fallback candidate preset unset",
        **selector_options,
        scope=scope,
        position=position,
        as_json=as_json,
    )

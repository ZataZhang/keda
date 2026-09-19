"""``iar ask`` / ``iar repl`` / ``iar deliberate`` / ``iar agent *`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
from pathlib import Path

from backend.api.cli_console import console, error_console

from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api import cli as _cli
from backend.core.shared.models.agent_spec import AGENT_PROFILES
from backend.core.use_cases.agent_invocation import (
    UnknownAgentError,
    build_agent_invocation,
    resolve_agent_spec,
)
from backend.core.use_cases.interactive_decision import run_interactive_decision
from backend.core.use_cases.lifecycle_agent_resolution import resolve_lifecycle_agent
from backend.api.agent_runner_views.live_terminal import create_output_view
from backend.core.shared.models.agent_deliberation import DeliberationSession
from backend.core.use_cases.agent_runner_factory import build_app_config, logger
from backend.core.use_cases.agent_runner_failure_resolver import AgentFailureResolver
from backend.core.use_cases.agent_runner_output_protocols import get_output_protocol_registry

# 黄金快照的哨兵提示词：doctor 的 argv 输出用它替代真实提示词，
# 使 `iar agent doctor --all-profiles --json` 可与改造前的快照逐字节 diff。
GOLDEN_SNAPSHOT_PROMPT = "golden-prompt"

# 沙箱/审批类参数的识别标记：非只读用途的 argv 不含任何标记时 doctor 给 WARN。
_SANDBOX_APPROVAL_MARKERS: tuple[str, ...] = (
    "--sandbox",
    "--ask-for-approval",
    "--dangerously-skip-permissions",
    "--approve",
)


def run_ask_command(ctx: ParsedCommandContext) -> int:
    """``iar ask``: natural-language decision entrypoint."""
    contexts = _cli._resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if len(contexts) != 1:
        logger.error(
            "ask requires exactly one target repository. Use --repo or --repo-id to specify."
        )
        return 1
    context = contexts[0]
    _cli._ensure_gh_auth_or_prompt(context.repo_path, ctx.process_runner)
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    planner_runner = _cli.create_planner_runner(ctx.process_runner, config=context.config)
    content_generator = _cli.create_content_generator(ctx.process_runner, config=context.config)
    agent = ctx.parsed.agent
    if agent == "auto":
        agent = resolve_lifecycle_agent("planner", context.config)
    output_dir = None
    if ctx.parsed.output:
        output_dir = Path(ctx.parsed.output)
    deliberation_config = context.config.deliberation
    transcript_runner = _cli.create_transcript_runner(config=context.config)
    output_view = create_output_view()
    event_sink = _cli.create_event_sink(
        Path(context.config.interactive_decision.default_output_dir),
        output_view,
    )
    return run_interactive_decision(
        user_prompt=ctx.parsed.prompt,
        context=context,
        config=context.config.interactive_decision,
        agent=agent,
        plan_only=ctx.parsed.plan_only,
        execute=ctx.parsed.execute,
        auto_confirm=ctx.parsed.yes,
        output_dir=output_dir,
        planner_runner=planner_runner,
        github_client=github_client,
        process_runner=ctx.process_runner,
        content_generator=content_generator,
        github_client_factory=ctx.github_client_factory,
        deliberation_deps={
            "config": deliberation_config,
            "transcript_runner": transcript_runner,
            "event_sink": event_sink,
            "output_view": output_view,
        },
    )


def run_repl_command(ctx: ParsedCommandContext) -> int:
    """``iar repl``: interactive REPL session."""
    contexts = _cli._resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if len(contexts) != 1:
        logger.error(
            "repl requires exactly one target repository. Use --repo or --repo-id to specify."
        )
        return 1
    context = contexts[0]
    _cli._ensure_gh_auth_or_prompt(context.repo_path, ctx.process_runner)
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    agent_override = getattr(ctx.parsed, "agent", None)
    if agent_override == "auto":
        logger.error(
            "`--agent auto` is not supported by the REPL entrypoint; "
            "use `claude`, `codex`, or `kimi`. "
            "Falling back to [agent_runner.repl].default_agent."
        )
        agent_override = None
    effective_agent = agent_override or context.config.repl.default_agent
    content_generator = _cli.create_content_generator(ctx.process_runner, read_only=False)
    command_executor = _cli.create_repl_command_executor(
        process_runner=ctx.process_runner,
        config=context.config.repl,
    )
    inputs = _cli.ReplSessionInputs(
        context=context,
        agent=effective_agent,
        config=context.config.repl,
    )
    deps = _cli.ReplSessionDeps(
        process_runner=ctx.process_runner,
        content_generator=content_generator,
        command_executor=command_executor,
        github_client=github_client,
    )
    return _cli.run_repl_session(inputs, deps)


def run_deliberate_command(ctx: ParsedCommandContext) -> int:
    """``iar deliberate``: multi-agent deliberation session."""
    contexts = _cli._resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    if len(contexts) != 1:
        logger.error(
            "deliberate requires exactly one target repository. Use --repo or --repo-id to specify."
        )
        return 1
    context = contexts[0]
    _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    deliberation_settings = context.config.deliberation
    output_dir = ctx.parsed.output or deliberation_settings.default_output_dir
    rounds = (
        ctx.parsed.rounds if ctx.parsed.rounds is not None else deliberation_settings.default_rounds
    )
    synthesizer = ctx.parsed.synthesizer or deliberation_settings.default_synthesizer
    agents = tuple(a.strip() for a in ctx.parsed.agents.split(",") if a.strip())
    session_id = ctx.parsed.session_id or _cli.create_default_session_id()
    output_path = Path(output_dir) / session_id
    request = _cli.DeliberationRequest(
        prompt=ctx.parsed.prompt,
        agents=agents,
        rounds=rounds,
        synthesizer=synthesizer,
        output_dir=str(output_path),
        session_id=session_id,
    )
    deliberation_config = context.config.deliberation
    transcript_runner = _cli.create_transcript_runner(config=context.config)
    output_path.mkdir(parents=True, exist_ok=True)
    output_view = create_output_view()
    event_sink = _cli.create_event_sink(output_path, output_view)
    resolver = AgentFailureResolver()
    result = _cli.run_agent_deliberation(
        request=request,
        config=deliberation_config,
        transcript_runner=transcript_runner,
        event_sink=event_sink,
        target_repo_path=context.repo_path,
        output_view=output_view,
        resolver=resolver.resolve,
    )
    selected_profile_ids = tuple(
        dict.fromkeys(
            profile_id for outputs in result.agent_outputs.values() for profile_id in outputs
        )
    )
    profiles_by_id = {profile.profile_id: profile for profile in deliberation_config.profiles}
    session_profiles = tuple(
        profiles_by_id[profile_id]
        for profile_id in selected_profile_ids
        if profile_id in profiles_by_id
    )
    session = DeliberationSession(
        session_id=result.session_id,
        prompt=result.prompt,
        profiles=session_profiles,
        rounds=request.rounds,
        synthesizer=request.synthesizer,
        output_dir=output_path,
        started_at=result.started_at,
        finished_at=result.finished_at,
    )
    _cli.write_deliberation_outputs(result, session, output_path)
    console.print(f"\n[green]Deliberation complete:[/] {output_path}")
    if result.failed_agents:
        for failure in result.failed_agents:
            logger.warning(
                "Deliberation agent failed: profile=%s attempted=%s fallback=%s reason=%s",
                failure.profile_id,
                failure.attempted_agent,
                failure.fallback_agent,
                failure.reason,
            )
            console.print(
                f"[yellow]Agent '{failure.profile_id}' failed "
                f"(attempted={failure.attempted_agent}, "
                f"fallback={failure.fallback_agent or 'none'}, "
                f"reason={failure.reason}).[/]"
            )
    strict_mode = ctx.parsed.strict or not deliberation_config.continue_on_agent_error
    all_participants_failed = len(selected_profile_ids) > 0 and all(
        profile_id in {f.profile_id for f in result.failed_agents}
        for profile_id in selected_profile_ids
    )
    synthesizer_failed = any(
        failure.profile_id == "synthesizer" for failure in result.failed_agents
    )
    if strict_mode and result.failed_agents:
        return 1
    if all_participants_failed:
        return 1
    if synthesizer_failed and not any(
        failure.profile_id != "synthesizer" for failure in result.failed_agents
    ):
        return 1
    return 0


def run_agent_list_command(ctx: ParsedCommandContext) -> int:
    """``iar agent list``: list registered agents and their profiles (read-only)."""
    del ctx  # list 只读全局注册表，不需要命令上下文
    config = build_app_config()
    for agent_name, agent_spec in config.agents.items():
        console.print(f"[cyan]{agent_name}[/] (bin: {agent_spec.bin}, label: {agent_spec.label})")
        for profile_name in AGENT_PROFILES:
            profile_spec = agent_spec.profiles.get(profile_name)
            if profile_spec is None:
                continue
            read_only_mark = "read-only" if profile_spec.read_only else "writable"
            console.print(
                f"  {profile_name}: delivery={profile_spec.prompt_delivery}, "
                f"protocol={profile_spec.output_protocol}, {read_only_mark}"
            )
    return 0


def _doctor_entry(
    agent_name: str,
    profile: str,
    prompt: str,
    cwd: Path,
    config,  # AppConfig（避免再引入 core 模型的运行时导入开销）
) -> tuple[dict[str, object] | None, list[str]]:
    """构造单个 agent/profile 的 doctor 记录与告警列表。

    Returns:
        ``(entry, warnings)``。任何失败（未注册 agent / 缺 profile /
        未知展开器 / 协议未注册或加载失败）由调用方统一转成报错，
        这里以 ``entry=None`` 表达。
    """
    warnings: list[str] = []
    try:
        agent_spec = resolve_agent_spec(agent_name, config)
    except UnknownAgentError as exc:
        error_console.print(f"[red]doctor failed:[/] {exc}", markup=False)
        return None, warnings
    if shutil.which(agent_spec.bin) is None:
        error_console.print(
            f"[red]doctor failed:[/] executable '{agent_spec.bin}' "
            f"(agent '{agent_name}') not found in PATH.",
            markup=False,
        )
        return None, warnings
    profile_spec = agent_spec.profiles.get(profile)
    if profile_spec is None:
        error_console.print(
            f"[red]doctor failed:[/] agent '{agent_name}' has no '{profile}' profile "
            f"(declared: {', '.join(agent_spec.profiles)}).",
            markup=False,
        )
        return None, warnings
    try:
        invocation = build_agent_invocation(agent_name, profile, prompt, cwd, config)
    except ValueError as exc:  # 未知展开器 / flag 缺失等构造错误
        error_console.print(f"[red]doctor failed:[/] {exc}", markup=False)
        return None, warnings
    registry = get_output_protocol_registry()
    try:
        registry.resolve(invocation.output_protocol)
    except Exception as exc:  # noqa: BLE001 - 协议加载失败必须显式失败，不降级
        error_console.print(f"[red]doctor failed:[/] {exc}", markup=False)
        return None, warnings
    if not profile_spec.read_only and not any(
        marker in (*profile_spec.args, *profile_spec.tail_args)
        for marker in _SANDBOX_APPROVAL_MARKERS
    ):
        warnings.append(
            f"agent '{agent_name}' profile '{profile}' is writable but declares no "
            "sandbox/approval flag; the agent runs without local sandboxing."
        )
    entry = {
        "agent": agent_name,
        "profile": profile,
        "argv": list(invocation.argv),
        "prompt_delivery": invocation.prompt_delivery,
    }
    return entry, warnings


def run_agent_doctor_command(ctx: ParsedCommandContext) -> int:
    """``iar agent doctor <name>...``: print resolved invocations (read-only)."""
    parsed: argparse.Namespace = ctx.parsed
    if getattr(parsed, "protocols", False):
        for protocol_id in get_output_protocol_registry().list_ids():
            print(protocol_id)
        return 0
    agent_names: list[str] = list(parsed.agent_names)
    if not agent_names:
        error_console.print("[red]doctor failed:[/] no agent name given.", markup=False)
        return 1
    config = build_app_config()
    prompt = getattr(parsed, "prompt", None) or GOLDEN_SNAPSHOT_PROMPT
    cwd = Path.cwd()
    profile_names = list(AGENT_PROFILES) if getattr(parsed, "all_profiles", False) else ["run"]
    entries: list[dict[str, object]] = []
    for agent_name in agent_names:
        for profile_name in profile_names:
            entry, warnings = _doctor_entry(agent_name, profile_name, prompt, cwd, config)
            if entry is None:
                return 1
            for warning in warnings:
                # WARN 走 stderr：`--json` 的 stdout 重定向（黄金快照 diff）不能被污染。
                error_console.print(f"[yellow]WARN:[/] {warning}", markup=False)
            entries.append(entry)
    entries.sort(key=lambda entry: (str(entry["agent"]), str(entry["profile"])))
    if getattr(parsed, "json_output", False):
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0
    for entry in entries:
        console.print(f"[cyan]{entry['agent']}[/] · {entry['profile']}")
        console.print(f"  argv: {shlex.join(str(arg) for arg in entry['argv'])}", markup=False)
        console.print(
            f"  prompt_delivery: {entry['prompt_delivery']}",
            markup=False,
        )
    return 0


__all__ = [
    "run_agent_doctor_command",
    "run_agent_list_command",
    "run_ask_command",
    "run_deliberate_command",
    "run_repl_command",
]

"""``kc ask`` / ``kc repl`` / ``kc deliberate`` / ``kc agent *`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from backend.api.cli_console import console, error_console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_helpers import require_single_repository_target
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    CliError,
    emit,
    emit_json,
)
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_parsed_commands.repository_context import (
    resolve_initialized_repository_target,
)
from backend.api import cli as _cli
from backend.core.shared.models.agent_spec import AGENT_PROFILES
from backend.core.use_cases.agent_invocation import (
    UnknownAgentError,
    UnknownProfileError,
    build_agent_invocation,
    resolve_agent_spec,
)
from backend.core.use_cases.agent_runner_factory import (
    build_app_config,
    create_foreground_session_launcher,
    logger,
    prepare_native_session_plan,
)
from backend.core.use_cases.interactive_decision import run_interactive_decision
from backend.core.use_cases.lifecycle_agent_resolution import resolve_lifecycle_agent
from backend.api.agent_runner_views.live_terminal import create_output_view
from backend.core.shared.models.agent_deliberation import DeliberationSession
from backend.core.use_cases.agent_runner_failure_resolver import AgentFailureResolver
from backend.core.use_cases.agent_runner_output_protocols import get_output_protocol_registry

# 黄金快照的哨兵提示词：doctor 的 argv 输出用它替代真实提示词，
# 使 `kc agent doctor --all-profiles --json` 可与改造前的快照逐字节 diff。
GOLDEN_SNAPSHOT_PROMPT = "golden-prompt"

# 沙箱/审批类参数的识别标记：非只读用途的 argv 不含任何标记时 doctor 给 WARN。
_SANDBOX_APPROVAL_MARKERS: tuple[str, ...] = (
    "--sandbox",
    "--ask-for-approval",
    "--dangerously-skip-permissions",
    "--approve",
)


def run_ask_command(ctx: ParsedCommandContext) -> int:
    """``kc ask``: natural-language decision entrypoint."""
    context = resolve_initialized_repository_target(ctx, "ask")
    _cli._ensure_gh_auth_or_prompt(context.repo_path, ctx.process_runner)
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    planner_runner = _cli.create_planner_runner(ctx.process_runner, config=context.config)
    content_generator = _cli.create_content_generator(ctx.process_runner, config=context.config)
    # CLI --preset 一次性锚定（planner 阶段）：把预设 + --model/--reasoning-effort
    # 覆盖折叠进本轮配置，后续解析链零改动。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset_to_config

    anchored_config = apply_cli_model_preset_to_config(
        context.config, ctx.parsed, anchored_stage="planner"
    )
    # planner 阶段绑定的模型选择：换人（显式 --agent）时丢弃并记日志。
    from backend.core.use_cases.lifecycle_agent_resolution import (
        resolve_lifecycle_model_selection,
    )
    from backend.core.use_cases.run_agent_once import drop_model_selection_for_agent

    agent = ctx.parsed.agent
    if agent == "auto":
        agent = resolve_lifecycle_agent("planner", anchored_config)
    planner_model_selection = drop_model_selection_for_agent(
        agent, resolve_lifecycle_model_selection("planner", anchored_config)
    )
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
        model_selection=planner_model_selection,
    )


def run_session_command(ctx: ParsedCommandContext) -> int:
    """``kc session``（即 TTY 下裸 ``kc``）：启动配置的原生执行器界面。

    与 ``kc repl`` 的分界是刻意的：本入口把整块终端交给 provider 自己的 TUI，
    KC 不接管对话、不注入权限旗标、也不启动任何项目服务；``kc repl`` 仍是原来
    的多轮 Keda REPL（白名单 + 确认）。

    Returns:
        执行器进程的退出码（原样转发，KC 不重新解释）。

    Raises:
        CliError: stdin 不是 TTY、执行器未注册、或该执行器未声明 interactive
            能力。三者都在**启动任何进程之前**返回，绝不静默回退到非交互跑法。
    """
    context = resolve_initialized_repository_target(ctx, "session")
    if not sys.stdin.isatty():
        raise CliError(
            "The native executor entrypoint needs an interactive terminal: stdin is not a TTY.",
            code=ExitCode.USAGE,
            suggestion=(
                "run `kc session` from a terminal, or use the non-interactive "
                "`kc run --agent <name>` for unattended execution"
            ),
            retryable=False,
        )
    try:
        preparation = prepare_native_session_plan(
            config=context.config,
            repo_root=context.repo_path,
            agent_override=getattr(ctx.parsed, "agent", None),
        )
    except (UnknownAgentError, UnknownProfileError) as profile_error:
        raise CliError(
            str(profile_error),
            code=ExitCode.USAGE,
            suggestion="kc agent doctor --all-profiles",
            retryable=False,
        ) from profile_error
    for notice in preparation.notices:
        console.print(notice, style="dim")
    console.print(
        f"Starting {preparation.plan.agent_name} in {preparation.plan.cwd} "
        "(KedaCode hands the terminal over; run `kc preview start` here only when "
        "the user asks to preview the project).",
        style="dim",
    )
    launcher = create_foreground_session_launcher()
    session_result = launcher.launch(preparation.plan)
    return session_result.exit_code


def run_repl_command(ctx: ParsedCommandContext) -> int:
    """``kc repl``: interactive REPL session."""
    contexts = _cli._resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    context = require_single_repository_target("repl", contexts)
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
    """``kc deliberate``: multi-agent deliberation session."""
    contexts = _cli._resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    context = require_single_repository_target("deliberate", contexts)
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
    """``kc agent list``: list registered agents and their profiles (read-only)."""
    config = build_app_config()
    entries: list[dict[str, Any]] = []
    for agent_name, agent_spec in config.agents.items():
        profiles = [
            {
                "profile": profile_name,
                "prompt_delivery": profile_spec.prompt_delivery,
                "output_protocol": profile_spec.output_protocol,
                "read_only": bool(profile_spec.read_only),
            }
            for profile_name in AGENT_PROFILES
            if (profile_spec := agent_spec.profiles.get(profile_name)) is not None
        ]
        entries.append(
            {
                "agent": agent_name,
                "bin": agent_spec.bin,
                "label": agent_spec.label,
                "profiles": profiles,
            }
        )

    def _render() -> None:
        """按既有人类格式打印条目（与 JSON 共用同一份数据，避免两处各算各的）。"""
        for entry in entries:
            console.print(
                f"[cyan]{entry['agent']}[/] (bin: {entry['bin']}, label: {entry['label']})"
            )
            for profile in entry["profiles"]:
                read_only_mark = "read-only" if profile["read_only"] else "writable"
                console.print(
                    f"  {profile['profile']}: delivery={profile['prompt_delivery']}, "
                    f"protocol={profile['output_protocol']}, {read_only_mark}"
                )

    emit(
        entries if ctx.output_format == OUTPUT_FORMAT_JSON else None,
        fmt=ctx.output_format,
        human_renderer=_render,
    )
    return 0


def _doctor_entry(
    agent_name: str,
    profile: str,
    prompt: str,
    cwd: Path,
    config,  # AppConfig（避免再引入 core 模型的运行时导入开销）
    model_selection=None,  # ModelSelection | None
) -> tuple[dict[str, object], list[str]]:
    """构造单个 agent/profile 的 doctor 记录与告警列表。

    Returns:
        ``(entry, warnings)``。

    Raises:
        CliError: 任何无法给出 argv 的情况——未注册 agent / 可执行文件缺失 /
            未声明该用途 / 展开器或模型模板缺失 / 协议未注册。类型分别为
            ``NOT_FOUND(3)``（查不到东西）与 ``USAGE(2)``（参数组合不成立），
            由中央调度点渲染，handler 不再自己打印后返回裸退出码。
    """
    warnings: list[str] = []
    try:
        agent_spec = resolve_agent_spec(agent_name, config)
    except UnknownAgentError as exc:
        raise CliError(
            str(exc),
            code=ExitCode.NOT_FOUND,
            suggestion="kc agent list",
        ) from exc
    if shutil.which(agent_spec.bin) is None:
        raise CliError(
            f"executable '{agent_spec.bin}' (agent '{agent_name}') not found in PATH.",
            code=ExitCode.NOT_FOUND,
            suggestion="kc agent list",
        )
    profile_spec = agent_spec.profiles.get(profile)
    if profile_spec is None:
        raise CliError(
            f"agent '{agent_name}' has no '{profile}' profile "
            f"(declared: {', '.join(agent_spec.profiles)}).",
            code=ExitCode.NOT_FOUND,
            suggestion=f"kc agent doctor {agent_name} --all-profiles",
        )
    try:
        invocation = build_agent_invocation(
            agent_name, profile, prompt, cwd, config, model_selection=model_selection
        )
    except ValueError as exc:  # 未知展开器 / flag 缺失 / 模型绑定模板缺失
        raise CliError(
            str(exc),
            code=ExitCode.USAGE,
            suggestion="kc agent presets",
        ) from exc
    registry = get_output_protocol_registry()
    try:
        registry.resolve(invocation.output_protocol)
    except Exception as exc:  # noqa: BLE001 - 协议加载失败必须显式失败，不降级
        raise CliError(
            str(exc),
            code=ExitCode.NOT_FOUND,
            suggestion="kc agent doctor --protocols",
        ) from exc
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
    if model_selection is not None:
        entry["preset"] = model_selection.preset_name or None
        entry["model"] = model_selection.model
        entry["reasoning_effort"] = model_selection.reasoning_effort
    return entry, warnings


def _doctor_lifecycle_profile(lifecycle: str) -> str:
    """生命周期阶段 -> doctor 打印用的用途名（与真实运行路径一致）。"""
    if lifecycle == "deliberate":
        return "deliberate"
    if lifecycle in ("planner", "content_generation"):
        return "generate"
    return "run"


def _doctor_lifecycle_entry(
    lifecycle: str,
    prompt: str,
    cwd: Path,
    config,
) -> tuple[dict[str, object], list[str]]:
    """按生命周期阶段视角构造 doctor 记录：解析 agent + 绑定的模型参数。

    fix / closeout 没有实现者上下文时如实返回 ``follows_implementation``
    标记（不打 argv，与 console 只读视图口径一致）。
    """
    from backend.core.use_cases.lifecycle_agent_resolution import (
        resolve_lifecycle_agent,
        resolve_lifecycle_model_selection,
    )
    from backend.core.use_cases.run_agent_once import drop_model_selection_for_agent

    warnings: list[str] = []
    agent_name = resolve_lifecycle_agent(lifecycle, config)
    model_selection = drop_model_selection_for_agent(
        agent_name, resolve_lifecycle_model_selection(lifecycle, config)
    )
    if lifecycle in ("fix", "closeout"):
        return {
            "lifecycle": lifecycle,
            "agent": agent_name,
            "follows_implementation": True,
            "model_selection": (
                {
                    "preset": model_selection.preset_name or None,
                    "model": model_selection.model,
                    "reasoning_effort": model_selection.reasoning_effort,
                }
                if model_selection is not None
                else None
            ),
        }, warnings
    profile_name = _doctor_lifecycle_profile(lifecycle)
    entry, warnings = _doctor_entry(
        agent_name, profile_name, prompt, cwd, config, model_selection=model_selection
    )
    return {"lifecycle": lifecycle, **entry}, warnings


def run_agent_doctor_command(ctx: ParsedCommandContext) -> int:
    """``kc agent doctor <name>...``: print resolved invocations (read-only)."""
    parsed: argparse.Namespace = ctx.parsed
    if getattr(parsed, "protocols", False):
        protocol_ids = list(get_output_protocol_registry().list_ids())
        if ctx.output_format == OUTPUT_FORMAT_JSON:
            emit_json(protocol_ids)
            return 0
        for protocol_id in protocol_ids:
            print(protocol_id)
        return 0
    config = build_app_config()
    prompt = getattr(parsed, "prompt", None) or GOLDEN_SNAPSHOT_PROMPT
    cwd = Path.cwd()
    lifecycle_key = getattr(parsed, "lifecycle", None)

    # --lifecycle 视角：按阶段解析 agent 与绑定并打印。
    if lifecycle_key is not None:
        from backend.core.shared.models.lifecycle_agent import LIFECYCLE_AGENT_KEYS

        if lifecycle_key not in LIFECYCLE_AGENT_KEYS:
            raise CliError(
                f"unknown lifecycle key '{lifecycle_key}'. "
                f"Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}.",
                code=ExitCode.USAGE,
                suggestion="kc agent doctor --lifecycle implementation",
            )
        entry, warnings = _doctor_lifecycle_entry(lifecycle_key, prompt, cwd, config)
        for warning in warnings:
            error_console.print(f"[yellow]WARN:[/] {warning}", markup=False)
        if ctx.output_format == OUTPUT_FORMAT_JSON:
            emit_json([entry])
            return 0
        console.print(f"[cyan]{entry['lifecycle']}[/] · {entry.get('agent')}")
        if entry.get("follows_implementation"):
            console.print("  follows implementation agent (no standalone argv)", markup=False)
        else:
            console.print(f"  argv: {shlex.join(str(arg) for arg in entry['argv'])}", markup=False)
            console.print(
                f"  prompt_delivery: {entry['prompt_delivery']}",
                markup=False,
            )
        if entry.get("preset") or entry.get("model"):
            console.print(
                f"  preset: {entry.get('preset') or '(inline)'} · "
                f"model: {entry.get('model') or '-'} · "
                f"reasoning_effort: {entry.get('reasoning_effort') or '-'}",
                markup=False,
            )
        return 0

    preset_name = getattr(parsed, "preset", None)
    if preset_name:
        # --preset 视角：按预设注入模型参数（doctor 是 what-if 工具，
        # 不做"执行 agent == 预设 agent"的丢弃判定，模板缺失时如实报错）。
        from backend.core.shared.models.agent_model_preset import resolve_model_selection

        try:
            preset_selection = resolve_model_selection(
                preset_name,
                config,
                model_override=getattr(parsed, "model", None),
                effort_override=getattr(parsed, "reasoning_effort", None),
            )
        except ValueError as exc:
            raise CliError(
                str(exc),
                code=ExitCode.USAGE,
                suggestion="kc agent presets",
            ) from exc
    else:
        if getattr(parsed, "model", None) or getattr(parsed, "reasoning_effort", None):
            raise CliError(
                "--model / --reasoning-effort require --preset.",
                code=ExitCode.USAGE,
                suggestion="kc agent doctor --preset <name> --model <id>",
            )
        preset_selection = None

    agent_names: list[str] = list(parsed.agent_names)
    if not agent_names:
        raise CliError(
            "no agent name given.",
            code=ExitCode.USAGE,
            suggestion="kc agent list",
        )
    profile_names = list(AGENT_PROFILES) if getattr(parsed, "all_profiles", False) else ["run"]
    entries: list[dict[str, object]] = []
    for agent_name in agent_names:
        for profile_name in profile_names:
            entry, warnings = _doctor_entry(
                agent_name,
                profile_name,
                prompt,
                cwd,
                config,
                model_selection=preset_selection,
            )
            for warning in warnings:
                # WARN 走 stderr：`--json` 的 stdout 重定向（黄金快照 diff）不能被污染。
                error_console.print(f"[yellow]WARN:[/] {warning}", markup=False)
            entries.append(entry)
    entries.sort(key=lambda entry: (str(entry["agent"]), str(entry["profile"])))
    if ctx.output_format == OUTPUT_FORMAT_JSON:
        emit_json(entries)
        return 0
    for entry in entries:
        console.print(f"[cyan]{entry['agent']}[/] · {entry['profile']}")
        console.print(f"  argv: {shlex.join(str(arg) for arg in entry['argv'])}", markup=False)
        console.print(
            f"  prompt_delivery: {entry['prompt_delivery']}",
            markup=False,
        )
    return 0


def run_agent_presets_command(ctx: ParsedCommandContext) -> int:
    """``kc agent presets``: list defined model presets (read-only)."""
    config = build_app_config()
    preset_entries = [
        {
            "preset": preset_name,
            "agent": preset.agent,
            "model": preset.model,
            "reasoning_effort": preset.reasoning_effort,
        }
        for preset_name, preset in config.agent_presets.items()
    ]

    def _render() -> None:
        if not preset_entries:
            console.print(
                "No model presets defined. Declare [agent_runner.presets.<name>] in "
                "config.toml / .kedacode.toml (fields: agent / model / reasoning_effort).",
                markup=False,
            )
            return
        for entry in preset_entries:
            console.print(
                f"[cyan]{entry['preset']}[/] · agent: {entry['agent']} · "
                f"model: {entry['model'] or '-'} · "
                f"reasoning_effort: {entry['reasoning_effort'] or '-'}",
                markup=False,
            )

    emit(
        preset_entries if ctx.output_format == OUTPUT_FORMAT_JSON else None,
        fmt=ctx.output_format,
        human_renderer=_render,
    )
    return 0


__all__ = [
    "run_agent_doctor_command",
    "run_agent_list_command",
    "run_agent_presets_command",
    "run_ask_command",
    "run_deliberate_command",
    "run_repl_command",
]

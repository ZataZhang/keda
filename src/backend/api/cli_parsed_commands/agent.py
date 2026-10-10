"""``kc ask`` / ``kc repl`` / ``kc deliberate`` / ``kc agent *`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher.
"""

from __future__ import annotations

import argparse
import dataclasses
import shlex
import shutil
from pathlib import Path
from typing import Any

from backend.api.cli_console import console, error_console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_helpers import repository_selector_error, require_single_repository_target
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    CliError,
    emit,
    emit_json,
)
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api import cli as _cli
from backend.core.shared.models.agent_spec import AGENT_PROFILES
from backend.core.shared.models.lifecycle_agent import LIFECYCLE_AGENT_KEYS
from backend.core.use_cases.agent_invocation import (
    UnknownAgentError,
    build_agent_invocation,
    resolve_agent_spec,
)
from backend.core.use_cases.interactive_decision import run_interactive_decision
from backend.core.use_cases.lifecycle_agent_resolution import resolve_lifecycle_agent
from backend.api.agent_runner_views.live_terminal import create_output_view
from backend.core.shared.models.agent_deliberation import DeliberationSession
from backend.core.use_cases.agent_runner_factory import (
    build_app_config,
    build_app_config_from_settings,
    create_lifecycle_settings_editor,
    load_fresh_agent_runner_settings,
    logger,
    resolve_repository_targets,
)
from backend.core.use_cases.agent_runner_failure_resolver import AgentFailureResolver
from backend.core.use_cases.agent_runner_output_protocols import get_output_protocol_registry
from backend.core.use_cases.lifecycle_agents_console import (
    SCOPE_EFFECTIVE,
    SCOPE_GLOBAL,
    SCOPE_REPOSITORY,
    LifecycleAgentsUpdateError,
    build_fallback_candidates_view,
    build_lifecycle_settings_view,
    validate_fallback_candidates_update,
    validate_lifecycle_preset_binding_update,
    validate_preset_update,
)

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
    contexts = _cli._resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    context = require_single_repository_target("ask", contexts)
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


# ─────────────────────────────────────────────────────────────────────────────
# 生命周期统一设置 CLI（lifecycle / preset / fallback candidate）
#
# 读写都走 core 聚合用例（build_lifecycle_settings_view / build_fallback_candidates_view
# / validate_*）与既有 lifecycle editor，绝不新建第二套解析或写入路径。每次读路径
# 都用 ``load_fresh_agent_runner_settings()`` 重新加载，写后复读才能看到磁盘新值。
# ─────────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class _LifecycleScopeTarget:
    """解析后的生命周期设置目标：生效 scope、用于校验/视图的配置与写回落点。"""

    scope: str  # SCOPE_GLOBAL | SCOPE_REPOSITORY（effective 已在上游归一）
    config: Any  # AppConfig：repository 时为"全局 + 仓库"合并视图
    repo_id: str | None
    repo_path: Path | None
    global_config: Any  # 独立加载的全局层配置，repository 视图据此区分预设来源

    def editor(self):
        """按本目标 scope 构造写回编辑器（global→config.toml，repository→仓库文件）。"""
        if self.scope == SCOPE_REPOSITORY:
            return create_lifecycle_settings_editor(SCOPE_REPOSITORY, self.repo_path)
        return create_lifecycle_settings_editor(SCOPE_GLOBAL)


def _resolve_repo_context(fresh_settings, repo_id: str | None, repo_override: str | None):
    """把仓库选择器解析成唯一 RepositoryRunContext，非法时归类为 :class:`CliError`。"""
    try:
        contexts = resolve_repository_targets(
            fresh_settings, repo_id=repo_id, repo_path_override=repo_override
        )
    except ValueError as exc:
        raise repository_selector_error(exc) from exc
    if len(contexts) != 1:
        raise CliError(
            "expected exactly one target repository; use --repo-id or --repo.",
            code=ExitCode.USAGE,
            suggestion="kc registry list",
        )
    return contexts[0]


def _resolve_lifecycle_scope(ctx: ParsedCommandContext, *, allow_effective: bool):
    """解析 ``--scope`` 与仓库选择器为 :class:`_LifecycleScopeTarget`。

    ``allow_effective`` 为真时（只读命令），``effective`` 依仓库选择器或 cwd 唯一命中
    自动落到 repository，否则落到 global；写命令要求显式 ``global`` / ``repository``，
    缺失或非法均在使用文件前以 ``USAGE(2)`` 退出。
    """
    fresh_settings = load_fresh_agent_runner_settings()
    global_config = build_app_config_from_settings(fresh_settings)
    scope = getattr(ctx.parsed, "scope", None)

    if allow_effective and scope in (None, SCOPE_EFFECTIVE):
        if ctx.repo_id is None and ctx.repo_override is None:
            detected = _cli._resolve_default_daemon_target()
            if detected.repo_id is None:
                return _LifecycleScopeTarget(SCOPE_GLOBAL, global_config, None, None, global_config)
            context = _resolve_repo_context(fresh_settings, detected.repo_id, None)
        else:
            context = _resolve_repo_context(fresh_settings, ctx.repo_id, ctx.repo_override)
        return _LifecycleScopeTarget(
            SCOPE_REPOSITORY, context.config, context.repo_id, context.repo_path, global_config
        )

    if scope is None:
        raise CliError(
            "writing lifecycle settings requires an explicit --scope (global or repository).",
            code=ExitCode.USAGE,
            suggestion="kc agent lifecycle list --scope global",
        )
    if scope == SCOPE_GLOBAL:
        # 全局层写的是机器级 config.toml，仓库选择器在此没有任何作用；容忍它
        # 只会让"我以为改了某个仓库"的误操作静默落到机器级配置上。
        if ctx.repo_id is not None or ctx.repo_override is not None:
            raise CliError(
                "--scope global targets the machine-level config; remove --repo-id/--repo "
                "or use --scope repository to write a repository.",
                code=ExitCode.USAGE,
                suggestion="kc agent lifecycle list --scope global",
            )
        return _LifecycleScopeTarget(SCOPE_GLOBAL, global_config, None, None, global_config)
    if scope == SCOPE_REPOSITORY:
        if ctx.repo_id is None and ctx.repo_override is None:
            raise CliError(
                "scope=repository requires --repo-id or --repo to pick the target repository.",
                code=ExitCode.USAGE,
                suggestion="kc registry list",
            )
        context = _resolve_repo_context(fresh_settings, ctx.repo_id, ctx.repo_override)
        return _LifecycleScopeTarget(
            SCOPE_REPOSITORY, context.config, context.repo_id, context.repo_path, global_config
        )
    raise CliError(
        f"unknown --scope '{scope}'. Use effective, global, or repository.",
        code=ExitCode.USAGE,
        suggestion="kc agent lifecycle list --scope global",
    )


def _render_fallback_body(fallback: dict[str, Any]) -> None:
    """人类模式打印有序回退候选段（视图级 scope 头由调用方负责）。"""
    console.print(
        f"fallback · max_agent_switches={fallback['max_agent_switches']} "
        f"budget_by_candidate_step={fallback['budget_by_candidate_step']}"
    )
    candidates = fallback["candidates"]
    if not candidates:
        console.print("  (no executor fallback candidates)", markup=False)
        return
    for candidate in candidates:
        console.print(
            f"  {candidate['position']}. agent={candidate['agent']} "
            f"preset={candidate['preset'] or '-'} model={candidate['model'] or '-'} "
            f"effort={candidate['reasoning_effort'] or '-'}",
            markup=False,
        )


def _render_lifecycle_settings(view: dict[str, Any]) -> None:
    """人类模式渲染聚合视图：九阶段、预设清单与回退候选三段。"""
    scope_note = f" · repo={view['repo_id']}" if view.get("repo_id") else ""
    console.print(f"[cyan]lifecycle settings[/] · scope={view['scope']}{scope_note}")
    console.print("stages:")
    for row in view["lifecycles"]:
        flags: list[str] = []
        if row["follows_implementation"]:
            flags.append("follows-implementation")
        if not row["model_supported"]:
            flags.append("model:unsupported")
        if not row["reasoning_effort_supported"]:
            flags.append("effort:unsupported")
        suffix = f"  [{' '.join(flags)}]" if flags else ""
        console.print(
            f"  {row['key']:<20} agent={row['effective_agent'] or '-'} "
            f"preset={row['preset_name'] or '-'} model={row['model'] or '-'} "
            f"effort={row['reasoning_effort'] or '-'}{suffix}",
            markup=False,
        )
    console.print(f"presets ({len(view['presets'])}):")
    for preset in view["presets"]:
        bound = ", ".join(preset["bound_stages"]) or "-"
        console.print(
            f"  {preset['name']:<16} agent={preset['agent']} model={preset['model'] or '-'} "
            f"effort={preset['reasoning_effort'] or '-'} source={preset['source']} bound=[{bound}]",
            markup=False,
        )
    _render_fallback_body(view["fallback"])


def _emit_lifecycle_view(ctx: ParsedCommandContext, *, allow_effective: bool) -> int:
    """重新解析 scope 并 emit 聚合生命周期视图（写后复读用同一 fresh 路径）。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=allow_effective)
    view = build_lifecycle_settings_view(
        target.config,
        scope=target.scope,
        repo_id=target.repo_id,
        global_config=target.global_config if target.scope == SCOPE_REPOSITORY else None,
    )
    emit(
        view if ctx.output_format == OUTPUT_FORMAT_JSON else None,
        fmt=ctx.output_format,
        human_renderer=lambda: _render_lifecycle_settings(view),
    )
    return 0


def _emit_fallback_view(ctx: ParsedCommandContext, *, allow_effective: bool) -> int:
    """重新解析 scope 并 emit 回退候选视图（候选写入后复读）。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=allow_effective)
    fallback = build_fallback_candidates_view(target.config)
    document = {"scope": target.scope, "repo_id": target.repo_id, **fallback}

    def _render() -> None:
        scope_note = f" · repo={document['repo_id']}" if document.get("repo_id") else ""
        console.print(f"[cyan]fallback[/] · scope={document['scope']}{scope_note}")
        _render_fallback_body(document)

    emit(
        document if ctx.output_format == OUTPUT_FORMAT_JSON else None,
        fmt=ctx.output_format,
        human_renderer=_render,
    )
    return 0


def _validate_lifecycle_stage(stage: str) -> None:
    """确认阶段名落在九键闭集内，否则 ``USAGE(2)``。"""
    if stage not in LIFECYCLE_AGENT_KEYS:
        raise CliError(
            f"unknown lifecycle stage '{stage}'. Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}.",
            code=ExitCode.USAGE,
            suggestion="kc agent lifecycle list --scope global",
        )


def _write_or_fail(operation, message_prefix: str = "config write failed"):
    """执行一次写回，把 OSError 归为可跑诊断的 :class:`CliError`（不吞栈）。"""
    try:
        operation()
    except OSError as exc:
        raise CliError(
            f"{message_prefix}: {exc}", code=ExitCode.GENERAL, suggestion="kc logs"
        ) from exc


def _current_candidate_entries(config) -> tuple[list[dict[str, Any]], int]:
    """取当前生效候选的 ``(agent, preset)`` 列表与切换预算（写回前的读基线）。"""
    view = build_fallback_candidates_view(config)
    entries = [
        {"agent": candidate["agent"], "preset": candidate["preset"]}
        for candidate in view["candidates"]
    ]
    return entries, int(view["max_agent_switches"])


def _persist_candidate_entries(
    target: _LifecycleScopeTarget, entries: list[dict[str, Any]], switches: int
) -> None:
    """校验完整期望候选数组后整体写回候选链与预算（校验失败不触达文件）。"""
    try:
        normalized, normalized_switches = validate_fallback_candidates_update(
            entries, switches, target.config
        )
    except LifecycleAgentsUpdateError as exc:
        raise CliError(str(exc), code=ExitCode.USAGE, suggestion="kc agent fallback list") from exc
    editor = target.editor()
    _write_or_fail(
        lambda: (
            editor.update_agent_fallback_candidates(normalized),
            editor.update_runner_keys({"max_agent_switches": normalized_switches}),
        )
    )


def _candidate_at(entries: list[dict[str, Any]], position: int, command: str) -> dict[str, Any]:
    """按 1-based 位置取候选，越界给 ``USAGE(2)``（写入前失败）。"""
    if position < 1 or position > len(entries):
        raise CliError(
            f"kc agent fallback candidate {command}: position {position} out of range "
            f"(valid: 1..{len(entries)}).",
            code=ExitCode.USAGE,
            suggestion="kc agent fallback list",
        )
    return entries[position - 1]


def run_agent_lifecycle_list_command(ctx: ParsedCommandContext) -> int:
    """``kc agent lifecycle list``：列出九阶段生效三元组、来源、预设与回退候选。"""
    return _emit_lifecycle_view(ctx, allow_effective=True)


def run_agent_lifecycle_set_command(ctx: ParsedCommandContext) -> int:
    """``kc agent lifecycle set <stage> --preset``：把阶段绑定到命名预设（持久化写）。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    stage = ctx.parsed.stage
    _validate_lifecycle_stage(stage)
    try:
        normalized = validate_lifecycle_preset_binding_update(
            {stage: ctx.parsed.preset}, target.config
        )
    except LifecycleAgentsUpdateError as exc:
        raise CliError(str(exc), code=ExitCode.USAGE, suggestion="kc agent presets") from exc
    editor = target.editor()
    _write_or_fail(lambda: editor.update_lifecycle_presets(normalized))
    return _emit_lifecycle_view(ctx, allow_effective=False)


def run_agent_lifecycle_unset_command(ctx: ParsedCommandContext) -> int:
    """``kc agent lifecycle unset <stage>``：删除该阶段在当前层的预设绑定（持久化写）。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    stage = ctx.parsed.stage
    _validate_lifecycle_stage(stage)
    editor = target.editor()
    _write_or_fail(lambda: editor.update_lifecycle_presets({stage: None}))
    return _emit_lifecycle_view(ctx, allow_effective=False)


def run_agent_preset_set_command(ctx: ParsedCommandContext) -> int:
    """``kc agent preset set <name> --agent``：upsert 命名预设三元组（持久化写）。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    # 校验与写回用同一个归一化名字：带首尾空白的预设名会写成一个绑定解析不到的预设。
    preset_name = ctx.parsed.name.strip()
    values = {
        "agent": ctx.parsed.agent,
        "model": ctx.parsed.model,
        "reasoning_effort": ctx.parsed.reasoning_effort,
    }
    try:
        normalized = validate_preset_update(preset_name, values, target.config)
    except LifecycleAgentsUpdateError as exc:
        raise CliError(str(exc), code=ExitCode.USAGE, suggestion="kc agent list") from exc
    editor = target.editor()
    _write_or_fail(lambda: editor.update_agent_preset(preset_name, normalized))
    return _emit_lifecycle_view(ctx, allow_effective=False)


def run_agent_fallback_list_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback list``：列出有序回退候选、每项预设与候选步数预算。"""
    return _emit_fallback_view(ctx, allow_effective=True)


def run_agent_fallback_candidate_add_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate add``：在有序候选链插入一个 (agent, preset) 候选。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    entries, switches = _current_candidate_entries(target.config)
    new_entry = {"agent": ctx.parsed.agent, "preset": ctx.parsed.preset}
    position = getattr(ctx.parsed, "position", None)
    if position is None:
        entries.append(new_entry)
    else:
        if position < 1 or position > len(entries) + 1:
            raise CliError(
                f"position {position} out of range (valid: 1..{len(entries) + 1} for append).",
                code=ExitCode.USAGE,
                suggestion="kc agent fallback list",
            )
        entries.insert(position - 1, new_entry)
    _persist_candidate_entries(target, entries, switches)
    return _emit_fallback_view(ctx, allow_effective=False)


def run_agent_fallback_candidate_remove_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate remove <position>``：删除指定位置的候选。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    entries, switches = _current_candidate_entries(target.config)
    _candidate_at(entries, ctx.parsed.position, "remove")
    del entries[ctx.parsed.position - 1]
    _persist_candidate_entries(target, entries, switches)
    return _emit_fallback_view(ctx, allow_effective=False)


def run_agent_fallback_candidate_move_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate move <position> --to``：把候选移动到新位置。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    entries, switches = _current_candidate_entries(target.config)
    _candidate_at(entries, ctx.parsed.position, "move")
    to = ctx.parsed.to
    if to < 1 or to > len(entries):
        raise CliError(
            f"--to {to} out of range (valid: 1..{len(entries)}).",
            code=ExitCode.USAGE,
            suggestion="kc agent fallback list",
        )
    moved = entries.pop(ctx.parsed.position - 1)
    entries.insert(to - 1, moved)
    _persist_candidate_entries(target, entries, switches)
    return _emit_fallback_view(ctx, allow_effective=False)


def run_agent_fallback_candidate_preset_set_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate preset set <position> --preset``：为候选绑定同 agent 预设。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    entries, switches = _current_candidate_entries(target.config)
    entry = _candidate_at(entries, ctx.parsed.position, "preset set")
    entry["preset"] = ctx.parsed.preset
    _persist_candidate_entries(target, entries, switches)
    return _emit_fallback_view(ctx, allow_effective=False)


def run_agent_fallback_candidate_preset_unset_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate preset unset <position>``：清除候选上的预设绑定。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    entries, switches = _current_candidate_entries(target.config)
    entry = _candidate_at(entries, ctx.parsed.position, "preset unset")
    entry["preset"] = None
    _persist_candidate_entries(target, entries, switches)
    return _emit_fallback_view(ctx, allow_effective=False)


__all__ = [
    "run_agent_doctor_command",
    "run_agent_list_command",
    "run_agent_presets_command",
    "run_agent_lifecycle_list_command",
    "run_agent_lifecycle_set_command",
    "run_agent_lifecycle_unset_command",
    "run_agent_preset_set_command",
    "run_agent_fallback_list_command",
    "run_agent_fallback_candidate_add_command",
    "run_agent_fallback_candidate_remove_command",
    "run_agent_fallback_candidate_move_command",
    "run_agent_fallback_candidate_preset_set_command",
    "run_agent_fallback_candidate_preset_unset_command",
    "run_ask_command",
    "run_deliberate_command",
    "run_repl_command",
]

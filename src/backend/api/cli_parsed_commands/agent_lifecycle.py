"""处理 Agent 生命周期、预设与回退候选的 CLI 命令。"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.api import cli as _cli
from backend.api.cli_console import console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_helpers import repository_selector_error
from backend.api.cli_output import OUTPUT_FORMAT_JSON, CliError, emit
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.core.shared.models.config_edit import (
    ConfigDocumentInvalidError,
    ConfigKeyMissingError,
    ConfigLockBusyError,
)
from backend.core.shared.models.lifecycle_agent import LIFECYCLE_AGENT_KEYS
from backend.core.use_cases.agent_runner_factory import (
    build_app_config_from_settings,
    create_lifecycle_settings_editor,
    load_fresh_agent_runner_settings,
    resolve_repository_targets,
)
from backend.core.use_cases.lifecycle_agents_console import (
    SCOPE_EFFECTIVE,
    SCOPE_GLOBAL,
    SCOPE_REPOSITORY,
    LifecycleAgentsUpdateError,
)
from backend.core.use_cases.lifecycle_settings_console import (
    build_fallback_candidates_view,
    build_lifecycle_settings_view,
    validate_fallback_candidates_update,
    validate_lifecycle_preset_binding_update,
    validate_preset_update,
)

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
    """执行一次写回，把失败归为可跑诊断的 :class:`CliError`（不吞栈）。

    写回是一次读-改-写，因此下面每条失败都保证**配置文件未被改动**：

    - 要删的预设 / 要解绑的阶段在本层根本没声明 → ``USAGE(2)``：这个操作不会生效，
      报成功等于让用户以为设置已改（该阶段仍按上一层的绑定运行）；
    - 磁盘上的配置不是合法 TOML → ``GENERAL(1)``，并给出"先修文件"的下一步；
    - 另一个写入者持有该配置的写锁且等满了有界超时 → ``CONFLICT(5)``：操作本身合法，
      原样重试即可，不该被混进"未分类失败"；
    - 纯 I/O 失败 → ``GENERAL(1)``。

    Args:
        operation: 无参写回回调。
        message_prefix: I/O 失败时的消息前缀。

    Raises:
        CliError: 上述任一失败，退出码与上文一致。
    """
    try:
        operation()
    except ConfigKeyMissingError as exc:
        raise CliError(str(exc), code=ExitCode.USAGE, suggestion="kc agent lifecycle list") from exc
    except ConfigDocumentInvalidError as exc:
        raise CliError(
            str(exc), code=ExitCode.GENERAL, suggestion="先修复该配置文件再重试写入"
        ) from exc
    except ConfigLockBusyError as exc:
        raise CliError(str(exc), code=ExitCode.CONFLICT, suggestion="重试本次写入") from exc
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


def _reload_scope_config(target: _LifecycleScopeTarget) -> Any:
    """在写锁内重取本 scope 的配置基线（磁盘现状可能已被并发写入者改过）。"""
    fresh_settings = load_fresh_agent_runner_settings()
    if target.scope == SCOPE_REPOSITORY:
        return _resolve_repo_context(fresh_settings, target.repo_id, None).config
    return build_app_config_from_settings(fresh_settings)


def _persist_candidate_entries(
    target: _LifecycleScopeTarget,
    mutate_entries: Callable[[list[dict[str, Any]]], None],
) -> None:
    """锁内取基线 → 应用本次改动 → 校验 → 整体写回候选链与预算。

    候选链是**整段替换**：基线若在锁外读，另一个写入者在"读—写"窗口里加进去的候选会被
    这次替换原样抹掉——写锁只保护落盘那一下，保护不了一份过期的基线。故基线读取、位置
    校验与写回全部圈进 :meth:`write_transaction`（同线程重入放行，不会自死锁）。
    校验不过（agent 未注册、预设不属于该 agent 等）同样发生在触达文件之前。
    """
    editor = target.editor()

    def _locked_write() -> None:
        with editor.write_transaction():
            baseline_config = _reload_scope_config(target)
            entries, switches = _current_candidate_entries(baseline_config)
            mutate_entries(entries)
            try:
                normalized, normalized_switches = validate_fallback_candidates_update(
                    entries, switches, baseline_config
                )
            except LifecycleAgentsUpdateError as exc:
                raise CliError(
                    str(exc), code=ExitCode.USAGE, suggestion="kc agent fallback list"
                ) from exc
            # 候选链与预算同批落盘：分两次 read-modify-write 时，第二次失败会留下
            # "链已换、预算还是旧的"的半套配置。
            editor.update_fallback_candidates_with_budget(
                normalized, max_agent_switches=normalized_switches
            )

    _write_or_fail(_locked_write)


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
    new_entry = {"agent": ctx.parsed.agent, "preset": ctx.parsed.preset}
    position = getattr(ctx.parsed, "position", None)

    def _insert(entries: list[dict[str, Any]]) -> None:
        if position is None:
            entries.append(new_entry)
            return
        if position < 1 or position > len(entries) + 1:
            raise CliError(
                f"position {position} out of range (valid: 1..{len(entries) + 1} for append).",
                code=ExitCode.USAGE,
                suggestion="kc agent fallback list",
            )
        entries.insert(position - 1, new_entry)

    _persist_candidate_entries(target, _insert)
    return _emit_fallback_view(ctx, allow_effective=False)


def run_agent_fallback_candidate_remove_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate remove <position>``：删除指定位置的候选。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)

    def _remove(entries: list[dict[str, Any]]) -> None:
        _candidate_at(entries, ctx.parsed.position, "remove")
        del entries[ctx.parsed.position - 1]

    _persist_candidate_entries(target, _remove)
    return _emit_fallback_view(ctx, allow_effective=False)


def run_agent_fallback_candidate_move_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate move <position> --to``：把候选移动到新位置。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)
    to = ctx.parsed.to

    def _move(entries: list[dict[str, Any]]) -> None:
        _candidate_at(entries, ctx.parsed.position, "move")
        if to < 1 or to > len(entries):
            raise CliError(
                f"--to {to} out of range (valid: 1..{len(entries)}).",
                code=ExitCode.USAGE,
                suggestion="kc agent fallback list",
            )
        moved = entries.pop(ctx.parsed.position - 1)
        entries.insert(to - 1, moved)

    _persist_candidate_entries(target, _move)
    return _emit_fallback_view(ctx, allow_effective=False)


def run_agent_fallback_candidate_preset_set_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate preset set <position> --preset``：为候选绑定同 agent 预设。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)

    def _bind(entries: list[dict[str, Any]]) -> None:
        _candidate_at(entries, ctx.parsed.position, "preset set")["preset"] = ctx.parsed.preset

    _persist_candidate_entries(target, _bind)
    return _emit_fallback_view(ctx, allow_effective=False)


def run_agent_fallback_candidate_preset_unset_command(ctx: ParsedCommandContext) -> int:
    """``kc agent fallback candidate preset unset <position>``：清除候选上的预设绑定。"""
    target = _resolve_lifecycle_scope(ctx, allow_effective=False)

    def _unbind(entries: list[dict[str, Any]]) -> None:
        _candidate_at(entries, ctx.parsed.position, "preset unset")["preset"] = None

    _persist_candidate_entries(target, _unbind)
    return _emit_fallback_view(ctx, allow_effective=False)

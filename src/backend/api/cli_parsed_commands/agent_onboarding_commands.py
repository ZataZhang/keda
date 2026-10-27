"""处理 Agent 安装引导与自定义注册 CLI 命令。"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from backend.api.cli_console import console, error_console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import OUTPUT_FORMAT_JSON, CliError, emit
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_parsed_commands.agent_lifecycle import _write_or_fail
from backend.core.use_cases.agent_onboarding import (
    SETUP_MENU_STAGES,
    AgentOnboardingError,
    agent_availability_entries,
    apply_add_plan,
    apply_fix_plan,
    apply_setup_plan,
    availability_available_agents,
    build_agent_add_plan,
    build_fix_plan,
    build_setup_plan,
    resolve_agent_availability,
)
from backend.core.use_cases.agent_runner_factory import (
    build_app_config,
    build_app_config_from_settings,
    create_lifecycle_settings_editor,
    load_fresh_agent_runner_settings,
)
from backend.core.use_cases.lifecycle_agents_console import SCOPE_GLOBAL


# ─────────────────────────────────────────────────────────────────────────────
# Agent 安装引导（kc agent setup）与自定义注册（kc agent add）
#
# 计划与校验全部在 core（agent_onboarding），触文件之前失败；写回复用全局层
# lifecycle editor 与同一把配置写锁。setup 的可用性判定与 agent list 共用
# shutil.which 事实源，不引入第二套探测。
# ─────────────────────────────────────────────────────────────────────────────


def _onboarding_error(exc: AgentOnboardingError) -> CliError:
    """把引导计划的失败归类为稳定退出码（usage→2，未安装→3）。"""
    code = ExitCode.NOT_FOUND if exc.kind == "not_found" else ExitCode.USAGE
    return CliError(str(exc), code=code, suggestion="kc agent setup --help")


def _confirm_global_write(parsed: argparse.Namespace, *, as_json: bool, question: str) -> bool:
    """写机器级配置前的一次交互确认。

    ``--yes``、机器输出、或没有终端时直接放行：前两者是调用方的显式意图，
    第三种没有可以回答的人，阻塞等待会把 CLI 挂死。
    """
    if bool(getattr(parsed, "yes", False)) or as_json:
        return True
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return True
    answer = input(f"{question} [Y/n] ").strip().lower()
    return answer not in {"n", "no"}


def _prompt_agent_setup_selection(global_config) -> tuple[str, str]:
    """交互式引导：只从本机可用的 agent 里收阶段与选择。

    Returns:
        ``(stage, agent_name)``；agent 一定在可用性视图里。

    Raises:
        CliError: 没有任何已注册 agent 在本机可执行（NOT_FOUND，指向 add）。
    """
    available = availability_available_agents(global_config.agents)
    if not available:
        raise CliError(
            "no registered agent is installed on this machine. Install one of "
            f"{', '.join(global_config.agents)}'s CLI, or register your own with "
            "`kc agent add <name> --bin <bin>`.",
            code=ExitCode.NOT_FOUND,
            suggestion="kc agent add --help",
        )
    console.print("Which lifecycle stage should we configure?", markup=False)
    for menu_stage in SETUP_MENU_STAGES:
        console.print(f"  {menu_stage}", markup=False)
    stage_input = input(f"stage [{SETUP_MENU_STAGES[0]}]: ").strip()
    stage = stage_input or SETUP_MENU_STAGES[0]
    if stage not in SETUP_MENU_STAGES:
        raise CliError(
            f"unknown stage '{stage}'. Choose one of: {', '.join(SETUP_MENU_STAGES)}.",
            code=ExitCode.USAGE,
            suggestion="kc agent setup --help",
        )
    console.print("Agents installed on this machine:", markup=False)
    for index, item in enumerate(available, start=1):
        console.print(f"  {index}. {item.agent} ({item.bin_path})", markup=False)
    agent_input = input("agent [1]: ").strip() or "1"
    try:
        selected_index = int(agent_input)
    except ValueError:
        selected_index = -1
    if not 1 <= selected_index <= len(available):
        raise CliError(
            f"invalid agent selection '{agent_input}'.",
            code=ExitCode.USAGE,
            suggestion="kc agent setup --help",
        )
    return stage, available[selected_index - 1].agent


def run_agent_setup_command(ctx: ParsedCommandContext) -> int:
    """``kc agent setup``：按本机可用性引导阶段选择（写入限全局层）。

    四种形态：``--fix`` 清理全局层指向不可用 agent 的绑定；带 ``--agent`` /
    ``--preset`` 落一次显式选择；``--dry-run`` / 无选择时只读列出可用性；
    TTY 交互模式下只从可用 agent 中给菜单。``--json`` / ``--output json``
    只改输出形态，不改变是否写入。
    """
    parsed = ctx.parsed
    dry_run = bool(getattr(parsed, "dry_run", False))
    as_json = ctx.output_format == OUTPUT_FORMAT_JSON
    fresh_settings = load_fresh_agent_runner_settings()
    global_config = build_app_config_from_settings(fresh_settings)
    editor = create_lifecycle_settings_editor(SCOPE_GLOBAL)

    if getattr(parsed, "fix", False):
        fix_entries = build_fix_plan(global_config)
        if not dry_run and fix_entries:
            _write_or_fail(lambda: apply_fix_plan(editor, fix_entries), "setup --fix write failed")

        def _render_fix() -> None:
            if not fix_entries:
                console.print(
                    "no global-layer stage bindings point at an unavailable agent.",
                    markup=False,
                )
                return
            console.print(
                f"{'would remove' if dry_run else 'removed'} "
                f"{len(fix_entries)} global-layer binding(s):",
                markup=False,
            )
            for fix_entry in fix_entries:
                console.print(
                    f"  {fix_entry.layer} · {fix_entry.stage} = {fix_entry.value!r} "
                    f"({fix_entry.reason})",
                    markup=False,
                )

        fix_document = {
            "action": "fix",
            "dry_run": dry_run,
            "removed": [dataclasses.asdict(entry) for entry in fix_entries],
            "config_path": str(editor.config_path),
        }
        emit(
            fix_document if as_json else None,
            fmt=ctx.output_format,
            human_renderer=_render_fix,
        )
        return 0

    stage = getattr(parsed, "stage", None) or "implementation"
    agent = getattr(parsed, "agent", None)
    preset = getattr(parsed, "preset", None)
    if agent is None and preset is None:
        # 无显式选择时：只读形态（--dry-run / 机器输出）列出可用性；TTY 走交互菜单；
        # 既非只读又非 TTY 则拒绝——没有终端就没法"只从可用 agent 里选"。
        if not (dry_run or as_json) and not (sys.stdin.isatty() and sys.stderr.isatty()):
            raise CliError(
                "setup needs --agent / --preset / --fix, or an interactive "
                "terminal to choose from.",
                code=ExitCode.USAGE,
                suggestion="kc agent setup --agent <name>",
            )
        if dry_run or as_json:
            # 与 Console 生命周期视图共用 core 的同一份序列化，避免同一个事实在
            # 两处发展出不同键名。
            availability_entries = agent_availability_entries(global_config.agents)

            def _render_readonly() -> None:
                console.print(f"agents on this machine · config={editor.config_path}", markup=False)
                for entry in availability_entries:
                    status = "available" if entry["available"] else "missing"
                    console.print(
                        f"  {entry['agent']:<12} {status:<9} {entry['bin_path'] or '-'}",
                        markup=False,
                    )
                console.print(
                    "next: kc agent setup --agent <available-name> "
                    "(stage menu: " + ", ".join(SETUP_MENU_STAGES) + ")",
                    markup=False,
                )

            listing_document = {
                "action": "setup",
                "dry_run": True,
                "availability": availability_entries,
                "available_agents": [
                    entry["agent"] for entry in availability_entries if entry["available"]
                ],
                "config_path": str(editor.config_path),
            }
            emit(
                listing_document if as_json else None,
                fmt=ctx.output_format,
                human_renderer=_render_readonly,
            )
            return 0
        stage, agent = _prompt_agent_setup_selection(global_config)

    try:
        plan = build_setup_plan(global_config, stage=stage, agent=agent, preset=preset)
    except AgentOnboardingError as exc:
        raise _onboarding_error(exc) from exc

    cancelled = False
    if not dry_run and not plan.already_satisfied:
        target_note = (
            f"preset '{plan.preset}'" if plan.preset is not None else f"agent '{plan.agent}'"
        )
        if not _confirm_global_write(
            parsed,
            as_json=as_json,
            question=(
                f"Write global stage '{plan.stage}' -> {target_note} " f"into {editor.config_path}?"
            ),
        ):
            cancelled = True
        else:
            _write_or_fail(lambda: apply_setup_plan(editor, plan), "setup write failed")

    setup_document = {
        "action": "setup",
        "dry_run": dry_run,
        "cancelled": cancelled,
        "stage": plan.stage,
        "agent": plan.agent,
        "preset": plan.preset,
        "unbind_stage_preset": plan.unbind_stage_preset,
        "already_satisfied": plan.already_satisfied,
        "config_path": str(editor.config_path),
    }

    def _render_setup() -> None:
        if cancelled:
            console.print("Skipped; nothing written.", markup=False)
            return
        chosen = plan.preset if plan.preset is not None else plan.agent
        table = "lifecycle_presets" if plan.preset is not None else "lifecycle_agents"
        if dry_run:
            console.print(
                f"dry-run · would write [{table}] {plan.stage} = {chosen!r}"
                + (" · and unbind the masking stage preset" if plan.unbind_stage_preset else ""),
                markup=False,
            )
        elif plan.already_satisfied:
            console.print(
                f"already configured · {plan.stage} = {chosen!r}; nothing written.",
                markup=False,
            )
        else:
            console.print(
                f"wrote [{table}] {plan.stage} = {chosen!r} -> {editor.config_path}"
                + (" · unbound the masking stage preset" if plan.unbind_stage_preset else ""),
                markup=False,
            )

    emit(
        setup_document if as_json else None,
        fmt=ctx.output_format,
        human_renderer=_render_setup,
    )
    return 0


def run_agent_add_command(ctx: ParsedCommandContext) -> int:
    """``kc agent add <name> --bin <bin>``：生成并校验最小 run 注册块。

    默认只预览 TOML；``--write`` 才经全局层 lifecycle editor 落盘。校验全部
    发生在触达文件之前（重名 / 非法协议 / 缺 prompt_flag / argv 组装失败均
    拒绝且配置逐字节不变）。
    """
    parsed = ctx.parsed
    as_json = ctx.output_format == OUTPUT_FORMAT_JSON
    config = build_app_config()
    try:
        plan = build_agent_add_plan(
            config,
            parsed.name.strip(),
            bin_name=parsed.bin,
            args=tuple(parsed.arg or ()),
            prompt_delivery=parsed.prompt_delivery,
            prompt_flag=parsed.prompt_flag,
            output_protocol=parsed.output_protocol,
            auth_home=parsed.auth_home,
            label=parsed.label,
        )
    except AgentOnboardingError as exc:
        code = ExitCode.NOT_FOUND if exc.kind == "not_found" else ExitCode.USAGE
        raise CliError(str(exc), code=code, suggestion="kc agent add --help") from exc

    write = bool(parsed.write)
    editor = create_lifecycle_settings_editor(SCOPE_GLOBAL)
    if write and not _confirm_global_write(
        parsed,
        as_json=as_json,
        question=f"Write global registration for '{plan.agent_name}' into {editor.config_path}?",
    ):
        write = False
    config_path: str | None = str(editor.config_path) if write else None
    if write:
        _write_or_fail(lambda: apply_add_plan(editor, plan), "agent registration write failed")
        availability = resolve_agent_availability(plan.agent_name, plan.agent_spec)
        if not availability.available:
            error_console.print(
                f"note: '{plan.agent_name}' is registered but bin '{plan.agent_spec.bin}' "
                "is not on PATH yet; install it or fix --bin."
            )

    add_document = {
        "action": "add",
        "agent": plan.agent_name,
        "written": write,
        "toml": plan.toml_preview,
        "config_path": config_path,
    }

    def _render_add() -> None:
        if write:
            console.print(f"registered agent '{plan.agent_name}' -> {config_path}", markup=False)
        console.print(plan.toml_preview, markup=False)

    emit(add_document if as_json else None, fmt=ctx.output_format, human_renderer=_render_add)
    return 0

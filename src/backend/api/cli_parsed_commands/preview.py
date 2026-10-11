"""``kc preview *`` handlers —— 对话中按需启动的项目预览。

Issue #256 的 FR-3。命令的三个动作都是**受限**的：start 只跑配置声明或用户逐字
确认过的精确 argv，status 只读，stop 只杀 KC 自己登记且身份可证实的进程组。判定
细节在 :mod:`backend.core.use_cases.agent_session_preview`，进程与所有权边界在
infrastructure 的端口实现；本模块只做仓库目标解析、人类/机器两路呈现和退出码归位。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.api.cli_console import console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError, emit
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_parsed_commands.repository_context import (
    resolve_initialized_repository_target,
)
from backend.core.use_cases.agent_runner_factory import create_preview_process_manager
from backend.core.use_cases.agent_session_preview import (
    PreviewCommandOutcome,
    run_preview_start,
    run_preview_status,
    run_preview_stop,
)


def _render_preview_result(
    ctx: ParsedCommandContext,
    *,
    command: str,
    outcome: PreviewCommandOutcome,
    repo_root: Path,
) -> None:
    """按输出形态打印一次预览结果（人类模式高亮 URL，机器模式只发 JSON）。"""
    resolution = outcome.resolution
    started_argv = list(resolution.argv) if resolution and resolution.argv else None
    payload: dict[str, Any] = {
        "command": command,
        "repo_root": str(repo_root),
        "state": outcome.state,
        "url": outcome.url,
        "message": outcome.message,
        "source": resolution.source if resolution else None,
        "argv": started_argv,
        "requires_confirmation": bool(
            resolution and resolution.requires_confirmation and resolution.argv is None
        ),
        "next_command": outcome.next_command,
    }

    def _render_human() -> None:
        console.print(outcome.message, markup=False, highlight=False, soft_wrap=True)
        if outcome.url:
            console.print(f"Preview URL: {outcome.url}", style="bold cyan", markup=False)

    emit(payload, fmt=ctx.output_format, human_renderer=_render_human)


def _preview_failure(command: str, outcome: PreviewCommandOutcome) -> CliError:
    """把"没达成意图"归成带语义退出码的 :class:`CliError`。

    未确认候选 / 候选不唯一是**用法**问题（``2``，并给出可直接执行的重跑命令）；
    进程已按批准 argv 启动却没能就绪是**运行**问题（``1``，可重试）；stop 被拒是
    所有权**冲突**（``5``，绝不因为想当然而对陌生 pid 发信号）。
    """
    suggestion = outcome.next_command or f"kc preview {command}"
    resolution = outcome.resolution
    if command == "start" and resolution is not None and resolution.argv is None:
        return CliError(
            outcome.message,
            code=ExitCode.USAGE,
            suggestion=suggestion,
            retryable=False,
        )
    if command == "stop":
        return CliError(
            outcome.message,
            code=ExitCode.CONFLICT,
            suggestion=suggestion,
            retryable=False,
        )
    return CliError(outcome.message, code=ExitCode.GENERAL, suggestion=suggestion, retryable=True)


def run_preview_start_command(ctx: ParsedCommandContext) -> int:
    """``kc preview start``：启动（或拒绝启动）仓库的本地预览进程。

    Returns:
        ``0``：预览进程已就绪并被 KC 登记。

    Raises:
        CliError: 需要用户逐字确认、候选不唯一、或进程未能在 ready 上限内就绪。
    """
    context = resolve_initialized_repository_target(ctx, "preview start")
    outcome = run_preview_start(
        config=context.config,
        repo_root=context.repo_path,
        confirmed_argv=getattr(ctx.parsed, "confirm", None),
        preview_manager=create_preview_process_manager(),
    )
    _render_preview_result(ctx, command="start", outcome=outcome, repo_root=context.repo_path)
    if outcome.exit_ok:
        return 0
    raise _preview_failure("start", outcome)


def run_preview_status_command(ctx: ParsedCommandContext) -> int:
    """``kc preview status``：只读回显当前预览状态，永不启动或终止进程。"""
    context = resolve_initialized_repository_target(ctx, "preview status")
    outcome = run_preview_status(
        repo_root=context.repo_path,
        preview_manager=create_preview_process_manager(),
    )
    _render_preview_result(ctx, command="status", outcome=outcome, repo_root=context.repo_path)
    return 0


def run_preview_stop_command(ctx: ParsedCommandContext) -> int:
    """``kc preview stop``：终止 KC 登记且身份可证实的预览进程组。"""
    context = resolve_initialized_repository_target(ctx, "preview stop")
    outcome = run_preview_stop(
        repo_root=context.repo_path,
        preview_manager=create_preview_process_manager(),
    )
    _render_preview_result(ctx, command="stop", outcome=outcome, repo_root=context.repo_path)
    if outcome.exit_ok:
        return 0
    raise _preview_failure("stop", outcome)


__all__ = [
    "run_preview_start_command",
    "run_preview_status_command",
    "run_preview_stop_command",
]

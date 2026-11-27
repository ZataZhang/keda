"""Agent 共用的 ``kc browser`` 命令入口。"""

from __future__ import annotations

import sys

from backend.api.cli_output import CliError, emit
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.core.use_cases.browser_runtime_composition import request_browser_command


def run_browser_command(ctx: ParsedCommandContext) -> int:
    """提交浏览器动作，成功转发结果，失败进入标准 CLI 错误通道。

    Args:
        ctx: 含浏览器参数与输出格式的 CLI 上下文。

    Returns:
        成功时返回 0。

    Raises:
        CliError: 宿主不可达、参数无效或浏览器动作失败。
    """
    try:
        outcome = request_browser_command(ctx.parsed.browser_args)
    except (OSError, ValueError) as exc:
        raise CliError(str(exc)) from exc
    if outcome["returncode"] != 0:
        raise CliError(outcome["stderr"] or outcome["stdout"] or "Browser action failed.")

    def render_human() -> None:
        sys.stdout.write(outcome["stdout"])
        sys.stderr.write(outcome["stderr"])

    emit(outcome, fmt=ctx.output_format, human_renderer=render_human)
    return 0

"""解析命令统一复用已初始化仓库目标的安全解析。"""

from __future__ import annotations

from backend.api import cli as _cli
from backend.api.cli_helpers import require_single_repository_target
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.core.shared.models.agent_runner import RepositoryRunContext


def resolve_initialized_repository_target(
    ctx: ParsedCommandContext, command_name: str
) -> RepositoryRunContext:
    """解析当前命令的唯一仓库目标，并要求它已有 KedaCode 配置。

    Args:
        ctx: 当前命令的解析参数与仓库上下文。
        command_name: 用于单仓库选择错误的命令名。

    Returns:
        唯一且已初始化的仓库运行上下文。
    """
    repository_contexts = _cli._resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for repository_context in repository_contexts:
        _cli.require_iar_repository_initialized(
            repository_context.repo_path,
            ctx.process_runner,
        )
    return require_single_repository_target(command_name, repository_contexts)


__all__ = ["resolve_initialized_repository_target"]

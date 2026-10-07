"""管理终端首屏「当前项目」解析。

``iar console`` 是一个多仓库面板：registry 里可以同时登记多个仓库，但用户在
某个仓库目录敲下 ``iar console`` 时，他期待面板打开就落在**这个仓库**上，而不是
registry 声明顺序最靠前的那个。本模块把「进程 cwd → registry 仓库 id」这一步推断
收敛成唯一实现，供 ``api/`` 层的 console 上下文端点调用。

推断分两步：先用 :func:`detect_git_repository_root` 把 cwd 归一到 git 仓库根，
再用 :func:`find_repository_match_for_path` 拿 git 根去匹配 registry 条目。两步
都可能落空（不在 git 仓库里 / 没登记 / 登记了但停用/ 匹配到多条），因此结果用
:attr:`ConsoleContext.status` 显式区分，而不是抛错——匹配不上是正常状态，不是故障。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.use_cases.agent_runner_factory import (
    find_repository_match_for_path,
    load_fresh_agent_runner_settings,
)
from backend.core.use_cases.agent_runner_repository_local import (
    detect_git_repository_root,
)


@dataclass(frozen=True)
class ConsoleContext:
    """console 进程 cwd 与 registry 的匹配结果。

    Attributes:
        cwd: 解析时的进程工作目录（已绝对化）。
        git_root: cwd 所属的 git 仓库根；不在 git 仓库内时为 None。
        repo_id: 唯一命中的 enabled 仓库 id；其余情况为 None。
        status: 匹配状态，取值为 ``matched`` / ``not_git_repo`` /
            ``not_registered`` / ``disabled`` / ``ambiguous``。
        candidates: 命中多条时的候选仓库 id（仅 ``ambiguous`` 非空）。
    """

    cwd: str
    git_root: str | None
    repo_id: str | None
    status: str
    candidates: tuple[str, ...] = ()


def resolve_console_context(cwd: Path) -> ConsoleContext:
    """推断 console 进程 cwd 对应的 registry 仓库。

    Args:
        cwd: console 进程的当前工作目录（通常是用户敲 ``iar console`` 时所在目录）。

    Returns:
        ConsoleContext: 匹配结果。任何落空情形都通过 ``status`` 表达，不抛异常。
    """
    resolved_cwd = cwd.expanduser().resolve()
    try:
        git_root = detect_git_repository_root(resolved_cwd)
    except ValueError:
        return ConsoleContext(
            cwd=str(resolved_cwd),
            git_root=None,
            repo_id=None,
            status="not_git_repo",
        )

    settings = load_fresh_agent_runner_settings()
    match = find_repository_match_for_path(settings, git_root)
    if match.is_unique_enabled:
        return ConsoleContext(
            cwd=str(resolved_cwd),
            git_root=str(git_root),
            repo_id=match.matched_repo_id,
            status="matched",
        )
    if match.is_disabled:
        return ConsoleContext(
            cwd=str(resolved_cwd),
            git_root=str(git_root),
            repo_id=None,
            status="disabled",
        )
    if match.is_ambiguous:
        return ConsoleContext(
            cwd=str(resolved_cwd),
            git_root=str(git_root),
            repo_id=None,
            status="ambiguous",
            candidates=tuple(repo_id for repo_id, _ in match.enabled_candidates),
        )
    return ConsoleContext(
        cwd=str(resolved_cwd),
        git_root=str(git_root),
        repo_id=None,
        status="not_registered",
    )


__all__ = ["ConsoleContext", "resolve_console_context"]

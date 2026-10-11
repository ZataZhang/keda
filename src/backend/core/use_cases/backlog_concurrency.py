"""自动执行并发上限（execution ceiling）的统一解析逻辑。

Backlog 的「并发」设置是仓库级策略，runner 配置的 ``max_concurrent_issues``
是引擎容量；两者过去各读各的，导致同一个 daemon 回合里补位闸门与认领闸门
可以按不同的上限放行。本模块把两者收敛为一个生效值：

    ceiling = min(policy, capacity)，且 policy 未设置时继承 capacity。

所有消费方（daemon 认领闸门、Backlog 补位、控制台「全局开始」、Autopilot
快照、设置读写）都必须调用这里的解析函数，不再各自读设置。
"""

from __future__ import annotations

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.interfaces.runner_console import IBacklogStore

CEILING_SOURCE_INHERITED = "inherited"
CEILING_SOURCE_POLICY = "policy"
CEILING_SOURCE_CAPPED_BY_CAPACITY = "capped_by_capacity"

_CEILING_SOURCES = (
    CEILING_SOURCE_INHERITED,
    CEILING_SOURCE_POLICY,
    CEILING_SOURCE_CAPPED_BY_CAPACITY,
)


def resolve_execution_ceiling(policy_max_parallel: int | None, runner_capacity: int) -> int:
    """把策略值与容量解析为单一生效并发上限。

    Args:
        policy_max_parallel: Backlog「并发」策略值；``None`` 表示从未设置
            （继承容量）。必须为正整数或 ``None``。
        runner_capacity: runner 侧容量（``max_concurrent_issues``），必须
            ``>= 1``。

    Returns:
        生效上限：策略缺失或超出容量时取容量，否则取策略值。

    Raises:
        ValueError: ``runner_capacity`` 小于 1 或策略值小于 1。
    """
    if runner_capacity < 1:
        raise ValueError(f"runner_capacity must be >= 1, got {runner_capacity}")
    if policy_max_parallel is None:
        return runner_capacity
    if policy_max_parallel < 1:
        raise ValueError(f"policy_max_parallel must be >= 1, got {policy_max_parallel}")
    return min(policy_max_parallel, runner_capacity)


def describe_ceiling_source(policy_max_parallel: int | None, effective_ceiling: int) -> str:
    """描述生效上限的来源，供 UI 与 API 展示。

    Args:
        policy_max_parallel: Backlog「并发」策略值，``None`` 表示未设置。
        effective_ceiling: 已解析的生效上限（来自
            :func:`resolve_execution_ceiling`）。

    Returns:
        ``"inherited"``（无策略）、``"policy"``（策略即生效值）或
        ``"capped_by_capacity"``（策略高于生效值，被容量压低）。
    """
    if policy_max_parallel is None:
        return CEILING_SOURCE_INHERITED
    if policy_max_parallel > effective_ceiling:
        return CEILING_SOURCE_CAPPED_BY_CAPACITY
    return CEILING_SOURCE_POLICY


def read_policy_max_parallel(store: IBacklogStore, repo_id: str) -> int | None:
    """读取仓库的 Backlog「并发」策略值；未落库时返回 ``None``。

    与历史的 ``get_or_create`` 语义不同：本函数只读，绝不为了读而写一行
    伪造默认值。「从未设置」在存储层就是「没有该行」。

    Args:
        store: Backlog 存储端口。
        repo_id: 仓库标识。

    Returns:
        已保存的策略值，或 ``None``（无设置行 / 值非法）。
    """
    entry = store.get_backlog_settings(repo_id)
    if entry is None:
        return None
    if entry.max_parallel < 1:
        return None
    return int(entry.max_parallel)


def count_live_running_issues(
    github_client: IGitHubClient,
    running_label: str,
    execution_ceiling: int,
) -> int:
    """读取仓库内 ``agent/running`` Issue 数，最多探测到上限之外一个。

    达到 ceiling 后，更多精确数量不会改变本轮预算（预算已经是 0）；额外探测
    一个用于区分「刚好占满」与「已超过上限」，让日志和补位报告不把下限伪装
    成精确计数。

    Args:
        github_client: 目标仓库 GitHub 客户端。
        running_label: 仓库配置的运行中标签。
        execution_ceiling: 本轮已解析的生效并发上限。

    Returns:
        运行中 Issue 数；超过上限时返回 ``execution_ceiling + 1``，表示至少
        有这么多。

    Raises:
        ValueError: ``execution_ceiling`` 小于 1。
        Exception: GitHub 查询失败时原样向上传播，由消费方决定 fail-closed 策略。
    """
    if execution_ceiling < 1:
        raise ValueError(f"execution_ceiling must be >= 1, got {execution_ceiling}")
    live_running_issues = github_client.list_issues_by_label(
        running_label,
        limit=execution_ceiling + 1,
        state="open",
    )
    return len(live_running_issues)

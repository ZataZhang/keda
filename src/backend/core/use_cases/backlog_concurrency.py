"""并行上限（execution ceiling）的统一解析逻辑。

每个仓库只有一个生效的并行上限：最多同时运行几个自动领取的 Issue。它有三个
入口，按优先级取第一个给出的值：

    kc daemon run --concurrency > Backlog 页面设置 > 配置文件 max_concurrent_issues

页面设置覆盖配置文件，不再与之取较小值（取较小值要求使用者同时记两个数字，
且页面调大不生效）。命令行覆盖只存在于 daemon 进程内，由 daemon 自己处理；
本模块解析其余两层。所有消费方（daemon 认领闸门、Backlog 补位、控制台「全局
开始」、Autopilot 快照、设置读写）都必须调用这里的解析函数，不再各自读设置。
"""

from __future__ import annotations

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.interfaces.runner_console import IBacklogStore

CEILING_SOURCE_POLICY = "policy"
CEILING_SOURCE_CONFIG = "config"
#: 只出现在 daemon 日志里：``kc daemon run --concurrency`` 覆盖了本进程的上限。
CEILING_SOURCE_CLI_OVERRIDE = "cli_override"


def resolve_execution_ceiling(policy_max_parallel: int | None, config_max_parallel: int) -> int:
    """把页面设置与配置文件解析为单一生效并行上限。

    Args:
        policy_max_parallel: Backlog 页面保存的并行上限；``None`` 表示从未保存。
            必须为正整数或 ``None``。
        config_max_parallel: 配置文件的 ``max_concurrent_issues``，必须 ``>= 1``。

    Returns:
        页面设置存在时取页面设置，否则取配置文件值。

    Raises:
        ValueError: 配置文件值或页面设置小于 1。
    """
    if config_max_parallel < 1:
        raise ValueError(f"config_max_parallel must be >= 1, got {config_max_parallel}")
    if policy_max_parallel is None:
        return config_max_parallel
    if policy_max_parallel < 1:
        raise ValueError(f"policy_max_parallel must be >= 1, got {policy_max_parallel}")
    return policy_max_parallel


def describe_ceiling_source(policy_max_parallel: int | None) -> str:
    """描述生效上限来自哪一层，供 UI、API 与日志展示。

    Args:
        policy_max_parallel: Backlog 页面保存的并行上限，``None`` 表示未保存。

    Returns:
        ``"policy"``（页面设置）或 ``"config"``（配置文件，含默认值 1）。
    """
    if policy_max_parallel is None:
        return CEILING_SOURCE_CONFIG
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

"""普通 agent daemon 的轮询节奏：额度暂停与空闲退避。

daemon 每个周期都会向 GitHub 发读请求。没活干的时候继续按固定间隔高频轮询，
只是在白白消耗所有工具共用的账号额度；额度见底时继续发请求更是只会把日志写满。
这里把两件事拆成纯函数，便于单测，也让主循环保持可读。
"""

from __future__ import annotations

from backend.core.shared.interfaces.github_api_budget import IGitHubApiBudget

#: 连续多少个空闲轮次之后才开始拉长间隔，避免刚忙完就立刻变慢。
IDLE_ROUNDS_BEFORE_BACKOFF = 2
#: 空闲时间隔最多拉长到基础间隔的多少倍（``1`` 表示关闭退避）。
IDLE_BACKOFF_MAX_FACTOR = 4
#: 多久记一条 GitHub API 用量汇总日志（秒）。
USAGE_LOG_INTERVAL_SECONDS = 3600.0
#: 汇总日志里最多列出几类调用。
USAGE_LOG_TOP_N = 8
#: 额度暂停期间每次最多睡多久就重新检查一次（别的进程可能已提前恢复）。
MAX_BUDGET_PAUSE_SLEEP_SECONDS = 300.0


def budget_pause_sleep_seconds(api_budget: IGitHubApiBudget | None) -> float:
    """额度处于暂停期时返回本轮应睡的秒数；可以正常轮询时返回 ``0.0``。"""
    if api_budget is None:
        return 0.0
    pause_seconds = api_budget.pause_remaining_seconds()
    if pause_seconds <= 0:
        return 0.0
    return min(pause_seconds + 1.0, MAX_BUDGET_PAUSE_SLEEP_SECONDS)


def next_poll_interval(base_interval: int, idle_streak: int, max_factor: int) -> int:
    """按连续空闲轮次计算下一次轮询前的睡眠秒数。

    前 ``IDLE_ROUNDS_BEFORE_BACKOFF`` 个空闲轮次保持基础间隔，之后每多一个空闲轮次
    翻倍，直到 ``max_factor`` 倍封顶；一旦有活（``idle_streak`` 归零）立即恢复。

    Args:
        base_interval: 基础轮询间隔（秒）。
        idle_streak: 连续空闲轮次数。
        max_factor: 最大放大倍数；小于等于 ``1`` 表示不退避。

    Returns:
        下一次轮询前应睡的秒数。
    """
    if max_factor <= 1 or idle_streak <= IDLE_ROUNDS_BEFORE_BACKOFF:
        return base_interval
    backoff_steps = idle_streak - IDLE_ROUNDS_BEFORE_BACKOFF
    return base_interval * min(2**backoff_steps, max_factor)


def format_usage_summary(api_budget: IGitHubApiBudget) -> str:
    """把本小时的用量账本压成一行日志：按调用次数降序列出前几类。

    账本由所有进程共用，所以这一行反映的是整台机器本小时各来源的消耗，
    不只是当前 daemon。
    """
    usage_counts = api_budget.usage_summary()
    if not usage_counts:
        return "no calls recorded"
    ranked_usage = sorted(usage_counts.items(), key=lambda item: item[1], reverse=True)
    top_usage = ", ".join(f"{label}={count}" for label, count in ranked_usage[:USAGE_LOG_TOP_N])
    return f"total={sum(usage_counts.values())}; {top_usage}"

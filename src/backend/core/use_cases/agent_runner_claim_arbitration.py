"""首次领取的并发仲裁：把 ``agent/ready`` → ``agent/running`` 的领取补成真 CAS。

GitHub 的 label 更新没有 compare-and-swap，两个并发领取者写的是同一个标签，所以
「谁先到」无法用标签判定。本模块改用**认领标记做见证**：先投递自己的认领评论
（投标），回读线程里同一轮的所有投标，按 ``started_at`` 最早者胜出、同刻按
``(host, pid)`` 字典序打破平局。赢家才把 Issue 切到 ``agent/running``；输家把自己
那条评论改写成撤销说明后退出，**不碰标签**（标签归赢家，动了会把赢家状态改写）。

守护进程与显式定向共用同一条领取路径，因此两种入口都受保护。

残余窗口：判据依赖「自己的评论已可见 + 对手的评论也已可见」。回读前的宽限
（:data:`CLAIM_ARBITRATION_GRACE_SECONDS`）就是用来覆盖这个可见性延迟的；GitHub
对同一 Issue 的评论列表是读己之写一致的，因此双盲窗口在实践中为空。
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_reclaim import (
    ClaimMarkerDetail,
    format_claim_marker,
    is_pid_alive,
    parse_claim_marker_detail,
)
from backend.core.use_cases.agent_runner_workflow import transition_issue_workflow_state

#: 投递投标后、回读前的宽限秒数：给并发领取者的评论留出可见时间。仲裁的一致性
#: 来自「每个进程对它看到的同一批投标算出同一个赢家」，宽限只是压缩「双方都还
#: 看不见对方评论」的传播窗口，不是判据本身，因此取 1 秒而非更大值。
CLAIM_ARBITRATION_GRACE_SECONDS = 1.0

#: 只有落在这个时间窗内的认领标记才算「同一轮的并发投标」。历史认领（同一个
#: Issue 的早先轮次、rework、发布恢复）必须被排除，否则一个旧的更早时间戳会让
#: 这个 Issue 永远无法被再次领取。
CONCURRENT_CLAIM_WINDOW_SECONDS = 120.0


class ClaimArbitrationLost(RuntimeError):
    """本次领取在仲裁中落败（或无法确认自己胜出），调用方应按 skip 处理。

    Attributes:
        winner: 胜出的投标；无法确认对手时（回读失败）为 ``None``。
    """

    def __init__(self, message: str, *, winner: "ClaimBid | None" = None) -> None:
        super().__init__(message)
        self.winner = winner


@dataclass(frozen=True)
class ClaimBid:
    """一次认领投标（对应线程里的一条认领评论）。

    Attributes:
        comment_id: 该投标所在评论的 ID；撤销时按它改写自己那条评论内容。
        host: 投标进程所在主机名。
        pid: 投标进程 PID。
        started_at: 投标时间（UTC，带时区）。
        agent: 投标时选定的 agent 注册名。
    """

    comment_id: int
    host: str
    pid: int
    started_at: datetime
    agent: str | None


def _as_aware_utc(value: datetime) -> datetime:
    """把可能不带时区的 ISO 时间归一成 UTC aware，供排序比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bid_sort_key(bid: ClaimBid) -> tuple[datetime, str, int]:
    """仲裁全序：先 ``started_at``，同刻按 ``(host, pid)``。

    每个进程对**它看到的同一批投标**计算出的最小值都相同，因此不会两边都以为
    自己赢——这正是把「谁先领取」变成可判定的那一步。
    """
    return (_as_aware_utc(bid.started_at), bid.host, bid.pid)


def format_claim_comment(bid: ClaimBid) -> str:
    """渲染投标即认领评论：人读归属信息 + 机器可读 ``iar:claim`` marker。

    文本与历史上的领取评论保持一致（对账与 reclaim 侧按该 marker 解析归属）。
    """
    marker = format_claim_marker(
        bid.host,
        bid.pid,
        started_at=bid.started_at,
        agent=bid.agent,
    )
    return (
        "## Agent Runner Claimed\n\n"
        f"- Host: `{bid.host}`\n"
        f"- PID: `{bid.pid}`\n"
        f"- Agent: `{bid.agent}`\n"
        f"- Started at: `{bid.started_at.isoformat()}`\n\n"
        f"{marker}"
    )


def format_withdrawn_claim_comment(loser: ClaimBid, winner: ClaimBid) -> str:
    """输家把自己的认领评论改写成撤销说明。

    撤销 marker 故意使用 ``iar:claim-withdrawn``：它不被 ``iar:claim`` 的解析器
    匹配，因此线程里剩下的「最近一次认领」自动指向赢家，reclaim 与对账都不会把
    输家当成持有者。
    """
    return (
        "## Agent Runner Claim Withdrawn\n\n"
        f"- Host: `{loser.host}`\n"
        f"- PID: `{loser.pid}`\n"
        f"- Agent: `{loser.agent}`\n"
        f"- Started at: `{loser.started_at.isoformat()}`\n\n"
        f"Lost first-claim arbitration to `{winner.host}` (PID {winner.pid}), "
        "which claimed this Issue earlier; this process did not run the Issue.\n\n"
        f'<!-- iar:claim-withdrawn host="{loser.host}" pid="{loser.pid}" '
        f'started_at="{loser.started_at.isoformat()}" -->'
    )


def _bid_from_comment(
    comment_id: int,
    detail: ClaimMarkerDetail,
) -> ClaimBid:
    """把解析出的 marker 明细组装成 :class:`ClaimBid`。"""
    return ClaimBid(
        comment_id=comment_id,
        host=detail.host,
        pid=detail.pid,
        started_at=_as_aware_utc(detail.started_at),
        agent=detail.agent,
    )


def concurrent_bids(
    comment_entries: Iterable[tuple[int, str]],
    *,
    this_host: str,
    now: datetime,
    pid_alive: Callable[[int], bool] = is_pid_alive,
    window_seconds: float = CONCURRENT_CLAIM_WINDOW_SECONDS,
) -> list[ClaimBid]:
    """筛出同一轮的并发投标。

    排除条件（都必须在仲裁前排除，否则领取会被历史标记永久挡住）：没有
    ``started_at`` 的老 marker（无法排序）、超出时间窗的历史认领、以及本机已死
    进程的投标（进程都不在了，不该继续占位）。跨机投标无法探活，按有效计入 ——
    宁可拒绝一次定向，也不与别人双跑同一个 Issue。

    Args:
        comment_entries: ``(comment_id, body)`` 序列，按线程顺序。
        this_host: 本机主机名。
        now: 当前时间（UTC）。
        pid_alive: PID 存活探针（测试可注入）。
        window_seconds: 并发窗口的秒数。

    Returns:
        参与本轮仲裁的投标列表（保持线程顺序）。
    """
    effective_now = _as_aware_utc(now)
    bids: list[ClaimBid] = []
    for comment_id, comment_body in comment_entries:
        detail = parse_claim_marker_detail(comment_body)
        if detail is None or detail.started_at is None:
            continue
        bid = _bid_from_comment(comment_id, detail)
        age_seconds = (effective_now - bid.started_at).total_seconds()
        if age_seconds > window_seconds:
            continue
        if bid.host == this_host and not pid_alive(bid.pid):
            continue
        bids.append(bid)
    return bids


def arbitrate_first_claim(
    *,
    issue_number: int,
    github_client: IGitHubClient,
    config: AppConfig,
    selected_agent: str,
    grace_seconds: float = CLAIM_ARBITRATION_GRACE_SECONDS,
    host: str | None = None,
    pid: int | None = None,
    started_at: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    pid_alive: Callable[[int], bool] = is_pid_alive,
    now: datetime | None = None,
    on_won: Callable[[], None] | None = None,
) -> ClaimBid:
    """投标 → 回读 → 仲裁 → 赢家切 running；输家撤销并抛出落败。

    赢家是**本进程自己的更早投标**时按重入处理（agent fallback 换 agent 会对同一
    Issue 再走一遍领取）：认领本就属于我们，继续执行而不是落败。

    Args:
        issue_number: 目标 Issue 编号。
        github_client: 目标仓库的 GitHub 客户端。
        config: 运行配置（提供 workflow 标签）。
        selected_agent: 本轮选定的 agent 注册名，写进 marker 供对账使用。
        grace_seconds: 投递后回读前的宽限秒数。
        host: 覆盖本机主机名（测试用）。
        pid: 覆盖 PID（测试用）。
        started_at: 覆盖投标时间（测试用）。
        sleeper: 等待函数（测试可注入，避免真实睡眠）。
        pid_alive: PID 存活探针（测试可注入）。
        now: 当前时间（测试可注入）。
        on_won: 赢家确认后、切换 Issue 标签前执行的互斥准入回调。

    Returns:
        胜出的投标（即调用方自己那条）。

    Raises:
        ClaimArbitrationLost: 自己不是最早认领者、回读不到自己的投标，或回读
            失败（无法证明就未赢，fail-closed 交给下一轮）。
    """
    this_host = host if host is not None else socket.gethostname()
    this_pid = pid if pid is not None else os.getpid()
    effective_now = _as_aware_utc(now if now is not None else datetime.now(timezone.utc))
    bid = ClaimBid(
        comment_id=0,
        host=this_host,
        pid=this_pid,
        started_at=_as_aware_utc(started_at if started_at is not None else effective_now),
        agent=selected_agent,
    )

    github_client.comment_issue(issue_number, format_claim_comment(bid))
    if grace_seconds > 0:
        sleeper(grace_seconds)

    try:
        entries = github_client.list_issue_comment_entries(issue_number)
    except Exception as exc:  # noqa: BLE001 - 无法回读就无法证明自己赢，本轮放弃
        raise ClaimArbitrationLost(
            f"Could not read back claim comments for Issue #{issue_number}: {exc}"
        ) from exc

    bids = concurrent_bids(entries, this_host=this_host, now=effective_now, pid_alive=pid_alive)
    own_bid = next(
        (
            candidate
            for candidate in bids
            if (candidate.host, candidate.pid, candidate.started_at)
            == (bid.host, bid.pid, bid.started_at)
        ),
        None,
    )
    if own_bid is None:
        raise ClaimArbitrationLost(
            f"Own claim marker for Issue #{issue_number} was not visible on read-back; "
            "refusing to run without proof of the earliest claim."
        )

    winner = min(bids, key=_bid_sort_key)
    if (winner.host, winner.pid) == (this_host, this_pid):
        # 赢家就是本进程更早的那次投标：这是**同一次领取的重入**（agent fallback
        # 换 agent 会再走一遍领取），不是竞争。继续执行，不撤销自己的认领。
        if on_won is not None:
            on_won()
        transition_issue_workflow_state(github_client, issue_number, config, config.labels.running)
        return own_bid
    if _bid_sort_key(winner) != _bid_sort_key(own_bid):
        github_client.edit_issue_comment(
            own_bid.comment_id,
            format_withdrawn_claim_comment(own_bid, winner),
        )
        raise ClaimArbitrationLost(
            f"Issue #{issue_number} was claimed earlier by {winner.host} "
            f"(PID {winner.pid}); withdrawing this claim.",
            winner=winner,
        )

    transition_issue_workflow_state(github_client, issue_number, config, config.labels.running)
    return own_bid


__all__ = [
    "CLAIM_ARBITRATION_GRACE_SECONDS",
    "CONCURRENT_CLAIM_WINDOW_SECONDS",
    "ClaimArbitrationLost",
    "ClaimBid",
    "arbitrate_first_claim",
    "concurrent_bids",
    "format_claim_comment",
    "format_withdrawn_claim_comment",
]

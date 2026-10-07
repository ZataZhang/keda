"""``iar run`` 显式定向的领取准入：目标 Issue 现在能不能被这个进程领走。

就绪标记（``agent/ready``）**只约束守护进程的自主挑选**：人显式点名一个 Issue
时不再要求它带该标记。放宽准入的代价是「领不领得到」这件事从队列规则转移到
认领状态上，因此定向到**不可领取状态**必须给出明确错误，而不是像守护进程那样
把不合条件的 Issue 静默跳过（守护进程的空队列仍返回 0，那是正常状态）。

判定与守护进程侧共用同一批既有事实源：workflow 标签来自 ``config.labels``，
认领归属来自认领评论里的 ``iar:claim`` marker（:mod:`agent_runner_reclaim`
的解析器，本模块只读不改），阻塞解除请求沿用同一套 event marker 判据。
"""

from __future__ import annotations

import socket
from typing import Callable

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_issue_handlers import (
    _guard_blocked_issue_has_resolution,
)
from backend.core.use_cases.agent_runner_reclaim import (
    ClaimMarkerDetail,
    is_pid_alive,
    parse_claim_marker_detail,
)
from backend.core.use_cases.agent_runner_workflow import workflow_state_labels

#: 不可领取的原因类别；CLI 层据此映射退出码，core 层不引用 ``ExitCode``。
TARGET_UNCLAIMABLE_NOT_FOUND = "not_found"
TARGET_UNCLAIMABLE_LIVE_CLAIM = "live_claim"
TARGET_UNCLAIMABLE_BLOCKED = "blocked"


class TargetNotClaimableError(ValueError):
    """显式定向的目标 Issue 当前不可领取。

    Attributes:
        reason: 原因类别（``not_found`` / ``live_claim`` / ``blocked``）。
        suggestion: 面向人的下一步命令文本。
        issue_number: 目标 Issue 编号。
        holder: 活跃认领者 ``(host, pid)``；仅 ``live_claim`` 时非 ``None``。
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        suggestion: str,
        issue_number: int | None = None,
        holder: tuple[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.suggestion = suggestion
        self.issue_number = issue_number
        self.holder = holder


def latest_claim(
    issue_number: int,
    github_client: IGitHubClient,
) -> ClaimMarkerDetail | None:
    """取该 Issue 评论线程里最后一条可解析的 ``iar:claim`` marker。

    Args:
        issue_number: Issue 编号。
        github_client: GitHub 客户端。

    Returns:
        最近一次认领的 marker 明细；线程里没有可解析的认领时返回 ``None``。
    """
    for comment_body in reversed(github_client.list_issue_comments(issue_number)):
        detail = parse_claim_marker_detail(comment_body)
        if detail is not None:
            return detail
    return None


def _claim_is_active(
    claim: ClaimMarkerDetail,
    *,
    this_host: str,
    pid_alive: Callable[[int], bool],
) -> bool:
    """该认领是否仍由一个存活的持有者占有。

    本机认领可以直接探 PID；**跨机器认领无法探测远端进程**，因此一律按有效处理
    （fail-closed）：抢跑别人正在执行的 Issue 是双跑，而拒绝一次定向只是让人等一等。
    """
    if claim.host != this_host:
        return True
    return pid_alive(claim.pid)


def _blocked_resolution_is_requested(
    issue: IssueSummary,
    github_client: IGitHubClient,
) -> bool:
    """blocked Issue 线程里是否有未被消费的解除请求 marker。

    直接复用派发通道的判据（:func:`_guard_blocked_issue_has_resolution`），避免
    准入与通道对「已请求解除」给出两种答案。
    """
    return _guard_blocked_issue_has_resolution(issue, github_client) is not None


def require_explicit_target_claimable(
    *,
    issue_number: int,
    github_client: IGitHubClient,
    config: AppConfig,
    host: str | None = None,
    pid_alive: Callable[[int], bool] = is_pid_alive,
) -> None:
    """确认显式定向的目标 Issue 可以被领取，否则抛出带原因的错误。

    判定顺序（先说最硬的）：

    1. 读不到、或不是 open 状态 → ``not_found``。
    2. 带 ``agent/running`` 且最近一次认领的持有者仍存活 → ``live_claim``，
       错误里点名持有者 ``host``/``PID``；持有者已死（本机 PID 不在）则放行，
       交给 running 通道的恢复路径（rework / 发布恢复）。
    3. 带 ``agent/blocked`` 且没有未消费的解除请求 marker → ``blocked``，
       提示先跑 ``iar blocked-continue``。
    4. 其余情况一律放行 —— 包括**没有任何 workflow 标签**的 Issue，这正是本次
       放宽的入口（显式定向不要求就绪标记）。

    Args:
        issue_number: 目标 Issue 编号。
        github_client: 目标仓库的 GitHub 客户端。
        config: 该仓库运行配置（提供 workflow 标签名）。
        host: 覆盖本机主机名（测试用）；默认 ``socket.gethostname()``。
        pid_alive: PID 存活探针（测试可注入）。

    Raises:
        TargetNotClaimableError: 目标不存在 / 被他人活跃认领 / blocked 未解除。
    """
    this_host = host if host is not None else socket.gethostname()

    try:
        issue = github_client.get_issue(issue_number)
    except Exception as exc:  # noqa: BLE001 - 读不到就是无法领取，原因要如实回报
        raise TargetNotClaimableError(
            f"Issue #{issue_number} could not be read: {exc}",
            reason=TARGET_UNCLAIMABLE_NOT_FOUND,
            issue_number=issue_number,
            suggestion="iar issue list  # 确认 Issue 编号与仓库访问",
        ) from exc

    if issue.state.upper() != "OPEN":
        raise TargetNotClaimableError(
            f"Issue #{issue_number} is {issue.state}, not open; there is nothing to run.",
            reason=TARGET_UNCLAIMABLE_NOT_FOUND,
            issue_number=issue_number,
            suggestion="iar issue list  # 选择一个 open 的 Issue，或重新开一个 Issue",
        )

    labels = set(issue.labels)
    if config.labels.running in labels:
        claim = latest_claim(issue_number, github_client)
        if claim is not None and _claim_is_active(claim, this_host=this_host, pid_alive=pid_alive):
            raise TargetNotClaimableError(
                f"Issue #{issue_number} is actively claimed by {claim.host} "
                f"(PID {claim.pid}); running it here would double-run the same Issue.",
                reason=TARGET_UNCLAIMABLE_LIVE_CLAIM,
                issue_number=issue_number,
                holder=(claim.host, claim.pid),
                suggestion=(
                    f"iar run --issue {issue_number} --takeover  # 持有者在本机："
                    "停掉本机守护进程并回收其在途 Issue"
                    if claim.host == this_host
                    else "iar issue list  # 持有者在另一台机器：等它结束，或换跑别的 Issue"
                    "（系统没有强制接管远端认领的入口）"
                ),
            )
        # 持有者已死，或线程里没有可解析的认领标记：不拒绝，交给 running 通道。

    if config.labels.blocked in labels:
        if not _blocked_resolution_is_requested(issue, github_client):
            raise TargetNotClaimableError(
                f"Issue #{issue_number} is {config.labels.blocked} with no unconsumed "
                "blocked_resolution_requested marker; a runner may not resume it.",
                reason=TARGET_UNCLAIMABLE_BLOCKED,
                issue_number=issue_number,
                suggestion=f"iar blocked-continue --issue {issue_number}",
            )


def has_non_ready_workflow_label(labels: tuple[str, ...], config: AppConfig) -> bool:
    """该 Issue 是否已带有 ``agent/ready`` 之外的 durable workflow 状态标签。

    显式定向的准入放宽只针对「没有状态标签 / 只带就绪标签」的 Issue：已经处于
    ``agent/review``、``agent/failed`` 等状态的 Issue 仍按原有通道处理，不因放宽
    而被当作新任务重跑。

    Args:
        labels: Issue 当前的标签集合。
        config: 运行配置（提供标签名与 durable 状态集合）。

    Returns:
        带有 ready 之外的 durable workflow 状态标签时返回 ``True``。
    """
    durable = set(workflow_state_labels(config))
    return bool(durable.intersection(labels) - {config.labels.ready})


__all__ = [
    "TargetNotClaimableError",
    "TARGET_UNCLAIMABLE_BLOCKED",
    "TARGET_UNCLAIMABLE_LIVE_CLAIM",
    "TARGET_UNCLAIMABLE_NOT_FOUND",
    "has_non_ready_workflow_label",
    "latest_claim",
    "require_explicit_target_claimable",
]

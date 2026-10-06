"""跨进程崩溃对账（StaleAttempt）：分类、处置判定与 comment 渲染。

从 :mod:`backend.core.use_cases.agent_runner_failure` 拆分而来：单文件非空行有 CI
硬上限，而这一整段（三出口判定 + 去重标记 + 对账 comment）是相对独立的对账域，
只被 :mod:`backend.core.use_cases.agent_runner_reconcile` 消费。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timezone
from enum import Enum

from backend.core.shared.models.agent_runner import FailureType
from backend.core.use_cases.agent_runner_failure import _ATTEMPT_START_MISSING_DISPLAY
from backend.core.use_cases.agent_runner_reclaim import ClaimMarkerDetail


# 跨进程崩溃对账（StaleAttempt）：分类、处置判定与 comment 渲染
# ---------------------------------------------------------------------------


class StaleAttemptDisposition(Enum):
    """僵尸 attempt 的对账处置出口（三出口 + 证据不足时的保守跳过）。

    Attributes:
        RESUME: 续传恢复——回 ``agent/ready``，下一轮 claim 以会话续传形态接续
            上轮上下文（session 记录留在 worktree 局部，claim 侧自行消费）。
        REENQUEUE: 重新入队——回 ``agent/ready``，全新会话重跑。
        FAIL: 判失败——转 ``agent/failed``，附对账报告。
        SKIP: 不触碰——证据不足（无 claim marker、跨机认领、或进程仍存活未过 TTL）。
    """

    RESUME = "resume-session"
    REENQUEUE = "re-enqueue"
    FAIL = "mark-failed"
    SKIP = "skip"


@dataclass(frozen=True)
class StaleAttemptEvidence:
    """对账判定的输入事实快照（纯数据，配合纯函数判定便于单测直接构造）。

    字段只允许来自四类证据源：本机活跃 attempt 状态（claim marker + PID 存活）、
    Issue label、comment attempt 历史、worktree 文件状态。任何"第二状态源"
    （库表 / 租约 / 心跳）都不得喂进这里。

    Attributes:
        worktree_resolvable: worktree 现场是否可解析（路径可得且 git 状态可读）。
        worktree_detail: 现场不可解析时的具体原因（进对账报告的"依据"）。
        claim_agent: 上轮认领该 Issue 的 agent 注册名；老 marker 缺字段为 ``None``。
        resume_capable: 该 agent 是否声明了会话续传能力（``supports_resume``）。
        session_id: worktree 局部记录里的上轮会话 id；无记录为 ``None``。
        stale_attempt_count: 本次之前该 Issue 已被对账处置的次数（取自 comment 历史）。
        max_recovery_attempts: 复用的既有恢复预算上限（``runner.max_recovery_attempts``）。
    """

    worktree_resolvable: bool
    worktree_detail: str = ""
    claim_agent: str | None = None
    resume_capable: bool = False
    session_id: str | None = None
    stale_attempt_count: int = 0
    max_recovery_attempts: int = 0


@dataclass(frozen=True)
class StaleAttemptDecision:
    """处置判定的结果：出口 + 一句话原因 + 依据清单。"""

    disposition: StaleAttemptDisposition
    reason: str
    basis: tuple[str, ...]


def decide_stale_attempt_disposition(evidence: StaleAttemptEvidence) -> StaleAttemptDecision:
    """僵尸 attempt 的三出口纯判定（无 I/O；判定逻辑集中在这一个函数）。

    判定阶梯（自上而下短路）：

    1. worktree 现场不可解析 → 判失败：没有可续跑 / 可重跑的载体，盲目重试只会
       产生不可解释的二次失败。
    2. 历史对账次数已达 ``max_recovery_attempts`` 预算 → 判失败：跨进程恢复与
       attempt 内 recovery 共用同一预算，不新增预算键。
    3. 上轮 agent 已知、声明了续传能力、且 worktree 留有 session 记录 → 续传恢复。
    4. 其余（agent 不可识别 / 不支持续传 / 无会话记录）→ 重新入队，全新会话。

    Args:
        evidence: 四类证据源汇聚出的事实快照。

    Returns:
        处置出口及其可读原因与依据清单。
    """
    basis = (
        "worktree: "
        + ("resolvable" if evidence.worktree_resolvable else "unresolvable")
        + (f" ({evidence.worktree_detail})" if evidence.worktree_detail else ""),
        f"claim agent: {evidence.claim_agent or 'unknown'}",
        f"resume capability: {'declared' if evidence.resume_capable else 'not declared'}",
        f"session record: {'present' if evidence.session_id else 'absent'}",
        (
            f"stale dispositions so far: {evidence.stale_attempt_count} "
            f"(budget {evidence.max_recovery_attempts})"
        ),
    )
    if not evidence.worktree_resolvable:
        return StaleAttemptDecision(
            StaleAttemptDisposition.FAIL,
            "The Issue worktree cannot be resolved, so there is no site to resume " "or re-run on.",
            basis,
        )
    if evidence.stale_attempt_count >= evidence.max_recovery_attempts:
        return StaleAttemptDecision(
            StaleAttemptDisposition.FAIL,
            (
                "The recovery budget is exhausted "
                f"({evidence.stale_attempt_count} stale dispositions already recorded, "
                f"max_recovery_attempts={evidence.max_recovery_attempts})."
            ),
            basis,
        )
    if evidence.resume_capable and evidence.session_id and evidence.claim_agent:
        return StaleAttemptDecision(
            StaleAttemptDisposition.RESUME,
            (
                f"The worktree is intact and agent '{evidence.claim_agent}' declares session "
                "resume with a recorded session id, so the next claim continues the original "
                "conversation."
            ),
            basis,
        )
    degrade_reason = (
        "unknown claiming agent"
        if not evidence.claim_agent
        else (
            "agent does not declare session resume"
            if not evidence.resume_capable
            else "no session record"
        )
    )
    return StaleAttemptDecision(
        StaleAttemptDisposition.REENQUEUE,
        (
            f"The worktree is intact but session resume is unavailable ({degrade_reason}), "
            "so the Issue returns to ready for a fresh attempt."
        ),
        basis,
    )


_RECONCILE_MARKER_PATTERN = re.compile(
    r'<!--\s*iar:reconcile\s+hash="(?P<hash>[0-9a-f]{8,64})"\s+seq="(?P<seq>\d+)"\s*-->'
)


def format_reconcile_marker(dedupe_hash: str, stale_attempt_seq: int) -> str:
    """渲染对账 comment 的隐藏去重标记（content hash + 本轮序号）。

    同一判定重放时 hash 不变：comment 已存在则不再追加，label 也已翻转，于是重放
    产生零新增评论与零重复状态变更（PRD FR-5）。
    """
    return f'<!-- iar:reconcile hash="{dedupe_hash}" seq="{stale_attempt_seq}" -->'


def parse_reconcile_marker(comment_body: str) -> tuple[str, int] | None:
    """返回 comment 体内最后一个对账标记的 ``(hash, seq)``；无标记为 ``None``。"""
    last_match = None
    for last_match in _RECONCILE_MARKER_PATTERN.finditer(comment_body):
        pass
    if last_match is None:
        return None
    return last_match.group("hash"), int(last_match.group("seq"))


@dataclass(frozen=True)
class StaleAttemptReport:
    """对账 comment 的渲染输入。

    Attributes:
        issue_number: 被对账的 Issue 编号。
        claim: 上轮 claim 的归属详情（host / pid / started_at / agent）。
        decision: :func:`decide_stale_attempt_disposition` 的判定结果。
        dedupe_hash: content hash（由 :func:`format_reconcile_marker` 写进标记）。
        stale_attempt_seq: 本轮对账序号（历史次数 + 1，从 1 起）。
        stale_reason: 僵尸检出原因（``dead_pid`` / ``ttl_expired``），即"中断原因
            分类"——运营者靠它区分"进程确实没了"和"claim 卡死超龄"。
        attempt_history_excerpt: 既有 Attempt History 表格摘录；无则为 ``None``。
    """

    issue_number: int
    claim: ClaimMarkerDetail | None
    decision: StaleAttemptDecision
    dedupe_hash: str
    stale_attempt_seq: int
    stale_reason: str = ""
    attempt_history_excerpt: str | None = None


def format_stale_attempt_comment(report: StaleAttemptReport) -> str:
    """渲染对账留痕 comment（四要素：中断时间 / 中断原因分类 / 处置结论 / 依据）。

    风格与 :func:`format_attempt_history` 协调：同一张两列表头 + Markdown 表格，
    依据用项目符号列出，末尾给出下一步动作说明，保证运营者只看 comment 就能知道
    daemon 对这台机器上死掉的 attempt 做了什么、为什么。
    """
    decision = report.decision
    claim = report.claim
    interrupted_at = (
        claim.started_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if claim is not None and claim.started_at is not None
        else _ATTEMPT_START_MISSING_DISPLAY
    )
    claim_owner = (
        f"`{claim.host}` / pid `{claim.pid}` / agent `{claim.agent or 'unknown'}`"
        if claim is not None
        else "-"
    )
    lines = [
        "## Stale Attempt Reconciled",
        "",
        format_reconcile_marker(report.dedupe_hash, report.stale_attempt_seq),
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Interrupted at (UTC) | {interrupted_at} |",
        f"| Classification | {FailureType.STALE_ATTEMPT.value} |",
        f"| Staleness detected by | {report.stale_reason or 'unspecified'} |",
        f"| Disposition | {decision.disposition.value} |",
        f"| Claim owner | {claim_owner} |",
        "",
        f"**Reason**: {decision.reason}",
        "",
        "### Basis",
        "",
    ]
    lines.extend(f"- {basis_entry}" for basis_entry in decision.basis)
    if report.attempt_history_excerpt:
        lines.extend(["", "### Attempt History (last recorded)", ""])
        lines.append(report.attempt_history_excerpt)
    lines.extend(["", _next_step_note(decision.disposition)])
    return "\n".join(lines)


def _next_step_note(disposition: StaleAttemptDisposition) -> str:
    """按处置出口给运营者的一句话下一步说明。"""
    if disposition is StaleAttemptDisposition.RESUME:
        return (
            "The Issue is back on `agent/ready`; the next claim resumes the recorded "
            "agent session instead of starting from scratch."
        )
    if disposition is StaleAttemptDisposition.REENQUEUE:
        return "The Issue is back on `agent/ready` and will be picked up by the next poll."
    if disposition is StaleAttemptDisposition.FAIL:
        return (
            "The Issue is marked `agent/failed`. Inspect the basis above, repair the "
            "worktree if needed, then relabel it `agent/ready` to retry."
        )
    return "No action was taken."

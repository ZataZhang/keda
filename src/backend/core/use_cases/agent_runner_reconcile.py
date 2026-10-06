"""崩溃对账：给 daemon 硬中断遗留的 ``agent/running`` 僵尸 attempt 一个明确终态。

SIGKILL / 崩溃 / 关机不会把 Issue 从 ``agent/running`` 退回，而 daemon 只认领
``agent/ready``，于是任务永久卡死、无人接手。本模块在 daemon 每轮开头（Phase -1）
扫描 ``agent/running`` Issue，把"归属进程已死（或 claim 超 TTL）"的僵尸交给
:func:`backend.core.use_cases.agent_runner_failure.decide_stale_attempt_disposition`
判定三出口之一——**续传恢复** / **重新入队** / **判失败**——每个出口都在 Issue
comment 里留下一条可审计的对账记录。

证据源闭集（PRD FR-2）：本机活跃 attempt 状态（claim marker + PID 存活）、Issue
label、Issue comment 的 attempt 历史、worktree 文件状态。**不引入第二状态源**
（无库表、无租约表、无心跳文件）；恢复预算复用
``runner.max_recovery_attempts``（FR-6），不新增预算键。

幂等（PRD FR-5）：对账 comment 携带 content-hash 隐藏标记，同一判定重放不再写
评论；label 变更走"读-检查-写"CAS，Issue 已被别的通路推进时本轮空转。跨机认领、
无 claim marker、进程仍存活且未过 TTL 的 Issue 一律保守不触碰（与既有 reclaim
同源同规则）。
"""

from __future__ import annotations

import hashlib
import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_failure import ATTEMPT_HISTORY_HEADING
from backend.core.use_cases.agent_runner_reclaim import (
    ClaimMarkerDetail,
    classify_claim_staleness,
    is_pid_alive,
    parse_claim_marker_detail,
)
from backend.core.use_cases.agent_runner_stale_attempt import (
    StaleAttemptDecision,
    StaleAttemptDisposition,
    StaleAttemptEvidence,
    StaleAttemptReport,
    decide_stale_attempt_disposition,
    format_stale_attempt_comment,
    parse_reconcile_marker,
)
from backend.core.use_cases.agent_runner_session_store import load_agent_session_record
from backend.core.use_cases.agent_runner_worktree_probe import _find_worktree_path_for_issue
from backend.core.use_cases.agent_runner_workflow import transition_issue_workflow_state

_logger = logging.getLogger(__name__)

#: 对账依据里 worktree 失败原因的最大长度（GitHub comment 可读性护栏）。
_WORKTREE_DETAIL_MAX_CHARS = 200


@dataclass(frozen=True)
class ReconcileOutcome:
    """单个 Issue 的一轮对账结果（daemon 日志与测试断言用）。

    Attributes:
        issue_number: 被对账的 Issue 编号。
        disposition: 判定出的处置出口。
        applied: 是否真的执行了评论 + label 变更（重放去重或 label 已变时为 ``False``）。
        reason: 人类可读的判定原因。
    """

    issue_number: int
    disposition: StaleAttemptDisposition
    applied: bool
    reason: str


@dataclass(frozen=True)
class _ReconcilePass:
    """一轮对账的共享上下文（避免实现细节里到处透传七个参数）。"""

    repo_path: Path
    config: AppConfig
    github_client: IGitHubClient
    process_runner: IProcessRunner
    host: str
    now: datetime
    pid_alive: Callable[[int], bool]
    ttl_seconds: int | None


def reconcile_stale_attempts(
    *,
    repo_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    limit: int = 50,
    host: str | None = None,
    pid_alive: Callable[[int], bool] = is_pid_alive,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> list[ReconcileOutcome]:
    """对账本仓库 ``agent/running`` 僵尸 attempt，逐个给出明确终态。

    Args:
        repo_path: 仓库根目录（worktree 路径解析的锚点）。
        config: 该仓库的合并后配置（label、agent 注册表、恢复预算）。
        github_client: 目标仓库的 GitHub 客户端。
        process_runner: worktree 现场探测用的进程执行器。
        limit: 单轮最多检查的 running Issue 数。
        host: 本机名覆盖（测试注入）；默认 ``socket.gethostname()``。
        pid_alive: 进程存活探针（测试注入）。
        ttl_seconds: claim 老化阈值；``None`` 表示只认"PID 已死"。
        now: 当前时间（测试可注入）；默认 UTC now。

    Returns:
        本轮产生判定的 Issue 结果列表（含被去重跳过的空转轮次）。
    """
    pass_context = _ReconcilePass(
        repo_path=repo_path,
        config=config,
        github_client=github_client,
        process_runner=process_runner,
        host=host if host is not None else socket.gethostname(),
        now=now if now is not None else datetime.now(timezone.utc),
        pid_alive=pid_alive,
        ttl_seconds=ttl_seconds,
    )
    outcomes: list[ReconcileOutcome] = []
    for issue in github_client.list_issues_by_label(config.labels.running, limit):
        if issue.state.upper() != "OPEN":
            continue
        try:
            outcome = _reconcile_one_issue(pass_context, issue)
        except Exception as exc:  # noqa: BLE001 - 单个 Issue 对账失败不得中断整轮扫描。
            _logger.error("Reconcile pass failed for Issue #%d: %s", issue.number, exc)
            continue
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def _reconcile_one_issue(
    pass_context: _ReconcilePass,
    issue: IssueSummary,
) -> ReconcileOutcome | None:
    """对单个 running Issue 走一遍"取证 → 判定 → 幂等执行"。

    Returns:
        该 Issue 的对账结果；**未形成僵尸判定**（无 marker、跨机认领、进程仍存活）
        时返回 ``None``，调用方据此不记录任何动作。
    """
    comments = list(pass_context.github_client.list_issue_comments(issue.number))
    claim = _latest_claim_detail(comments)
    if claim is None:
        # 拿不到归属信息就无法证明它死了，保守跳过（与既有 reclaim 同一护栏）。
        return None
    if claim.host != pass_context.host:
        return None
    stale_reason = classify_claim_staleness(
        claim_pid=claim.pid,
        claim_started_at=claim.started_at,
        effective_now=pass_context.now,
        ttl_seconds=pass_context.ttl_seconds,
        pid_alive=pass_context.pid_alive,
    )
    if stale_reason is None:
        return None

    evidence = _collect_stale_attempt_evidence(pass_context, issue, claim, comments)
    decision = decide_stale_attempt_disposition(evidence)
    dedupe_hash = _reconcile_content_hash(
        issue_number=issue.number,
        claim=claim,
        decision=decision,
    )
    if _marker_hash_present(comments, dedupe_hash):
        # 这个结论已经写过 comment：不再重复留痕，但**仍然**走一次落账，因为上一轮
        # 可能是「comment 写成功、label 变更失败」——只跳过评论就能让僵尸永远停在
        # agent/running。label 侧由 CAS 保证：Issue 已不在 running 时本轮空转。
        _logger.info(
            "Issue #%d reconcile comment already recorded (hash=%s, disposition=%s); "
            "re-applying the label transition only.",
            issue.number,
            dedupe_hash,
            decision.disposition.value,
        )
        return _apply_disposition(
            pass_context,
            issue=issue,
            claim=claim,
            decision=decision,
            stale_reason=stale_reason,
            dedupe_hash=dedupe_hash,
            stale_attempt_seq=evidence.stale_attempt_count,
            attempt_history_excerpt=None,
            skip_comment=True,
        )

    outcome = _apply_disposition(
        pass_context,
        issue=issue,
        claim=claim,
        decision=decision,
        stale_reason=stale_reason,
        dedupe_hash=dedupe_hash,
        stale_attempt_seq=evidence.stale_attempt_count + 1,
        attempt_history_excerpt=_latest_attempt_history_excerpt(comments),
    )
    if outcome.applied:
        _logger.info(
            "Reconciled stale Issue #%d (claim pid %d on %s, reason=%s) as %s.",
            issue.number,
            claim.pid,
            claim.host,
            stale_reason,
            decision.disposition.value,
        )
    return outcome


def _latest_claim_detail(comments: list[str]) -> ClaimMarkerDetail | None:
    """从评论尾部向前找出最近一次 claim 标记的完整归属信息。"""
    for comment_body in reversed(comments):
        parsed = parse_claim_marker_detail(comment_body)
        if parsed is not None:
            return parsed
    return None


def _collect_stale_attempt_evidence(
    pass_context: _ReconcilePass,
    issue: IssueSummary,
    claim: ClaimMarkerDetail,
    comments: list[str],
) -> StaleAttemptEvidence:
    """把四类证据源汇成判定输入快照（取证侧只做读取，不做决策）。"""
    worktree_resolvable, worktree_detail, worktree_path = _probe_worktree(pass_context, issue)
    resume_capable, session_id = _probe_session_resume(
        pass_context,
        claim_agent=claim.agent,
        worktree_path=worktree_path,
        issue_number=issue.number,
    )
    return StaleAttemptEvidence(
        worktree_resolvable=worktree_resolvable,
        worktree_detail=worktree_detail,
        claim_agent=claim.agent,
        resume_capable=resume_capable,
        session_id=session_id,
        stale_attempt_count=_count_reconcile_dispositions(comments),
        max_recovery_attempts=max(0, pass_context.config.runner.max_recovery_attempts),
    )


def _probe_worktree(
    pass_context: _ReconcilePass,
    issue: IssueSummary,
) -> tuple[bool, str, Path | None]:
    """探测 worktree 现场是否可解析（路径可得即可判定"完整"）。

    Returns:
        ``(可解析, 不可解析原因, worktree 路径)``；路径在不可解析时为 ``None``。
    """
    try:
        worktree_path = _find_worktree_path_for_issue(
            pass_context.repo_path,
            issue,
            pass_context.config,
            pass_context.process_runner,
        )
    except FileNotFoundError as exc:
        return False, _truncate_detail(str(exc)), None
    except Exception as exc:  # noqa: BLE001 - 探测异常一律归为"现场不可解析"，交由判失败出口。
        return False, _truncate_detail(f"{type(exc).__name__}: {exc}"), None
    # 路径存在但 ``.git`` 指针没了 = 现场还在、却不再是可用的 git 工作树：
    # 这种僵尸放回 ready 只会让下一轮在一个坏目录上白跑一次（PRD rv-2 要避免的
    # 正是"盲目重试"），因此与"路径不存在"同归不可解析。
    if worktree_path is not None and not (worktree_path / ".git").exists():
        return (
            False,
            _truncate_detail(
                f"worktree has no .git entry, so it is not a usable git worktree: {worktree_path}"
            ),
            None,
        )
    return True, "", worktree_path


def _probe_session_resume(
    pass_context: _ReconcilePass,
    *,
    claim_agent: str | None,
    worktree_path: Path | None,
    issue_number: int,
) -> tuple[bool, str | None]:
    """查该 agent 是否声明了续传能力，以及 worktree 局部是否留有**本 Issue 的**会话记录。

    记录按 agent 分文件，worktree 却可能被复用到别的 Issue（分支名换号重用、手工
    挪动）：拿别人的会话续传比不续更糟，因此编号不匹配一律按"无会话"处理。领取侧
    :func:`backend.core.use_cases.agent_runner_session_store.resolve_resumable_session_id`
    用同一道护栏，两处判据不分叉。
    """
    if claim_agent is None or worktree_path is None:
        return False, None
    agent_spec = pass_context.config.agents.get(claim_agent)
    if agent_spec is None or not agent_spec.supports_resume:
        return False, None
    record = load_agent_session_record(worktree_path, claim_agent)
    if record is None or (record.issue_number is not None and record.issue_number != issue_number):
        return True, None
    return True, record.session_id


def _count_reconcile_dispositions(comments: list[str]) -> int:
    """从 comment 历史里数出该 Issue 已被对账处置的次数（按去重标记的唯一 hash 计）。"""
    recorded_hashes: set[str] = set()
    for comment_body in comments:
        marker = parse_reconcile_marker(comment_body)
        if marker is not None:
            recorded_hashes.add(marker[0])
    return len(recorded_hashes)


def _latest_attempt_history_excerpt(comments: list[str]) -> str | None:
    """取最近一条 Attempt History 评论正文作为对账记录的摘录。"""
    for comment_body in reversed(comments):
        if ATTEMPT_HISTORY_HEADING in comment_body:
            visible_lines = [
                line for line in comment_body.splitlines() if not line.startswith("<!--")
            ]
            return "\n".join(visible_lines).strip()
    return None


def _reconcile_content_hash(
    *,
    issue_number: int,
    claim: ClaimMarkerDetail,
    decision: StaleAttemptDecision,
) -> str:
    """把本次判定的输入折成一个 16 位 content hash（FR-5 去重键）。

    同一僵尸（同一 claim）、同一结论 → 同一 hash → 重放不再写评论。**不把对账
    次数折进来**：comment 写成功但 label 变更失败时，下一轮次数已经 +1，若参与
    hash 就会把同一次中断再写一遍。现场或结论变化（例如人工补了 session 记录后
    改判续传）则 hash 变化，允许留下新的对账记录。
    """
    hash_source = "|".join(
        (
            str(issue_number),
            claim.host,
            str(claim.pid),
            claim.started_at.isoformat() if claim.started_at is not None else "",
            claim.agent or "",
            decision.disposition.value,
        )
    )
    return hashlib.sha256(hash_source.encode("utf-8")).hexdigest()[:16]


def _marker_hash_present(comments: list[str], dedupe_hash: str) -> bool:
    """该结论是否已经写过（重放检测）。"""
    return any(
        marker is not None and marker[0] == dedupe_hash
        for marker in map(parse_reconcile_marker, comments)
    )


def _apply_disposition(
    pass_context: _ReconcilePass,
    *,
    issue: IssueSummary,
    claim: ClaimMarkerDetail,
    decision: StaleAttemptDecision,
    stale_reason: str,
    dedupe_hash: str,
    stale_attempt_seq: int,
    attempt_history_excerpt: str | None,
    skip_comment: bool = False,
) -> ReconcileOutcome:
    """执行处置出口：先写对账 comment，再改 label（PRD §6.4 顺序）。

    ``skip_comment=True`` 用于重放：同一结论的 comment 已在 Issue 上，只补做 label
    变更，不再重复留痕（FR-5）。

    label 变更沿用"读-检查-写"CAS：Issue 已不在 ``agent/running`` 就说明别的通路
    推进了它，本轮空转并如实报告 ``applied=False``。
    """
    if decision.disposition is StaleAttemptDisposition.SKIP:
        return ReconcileOutcome(
            issue_number=issue.number,
            disposition=decision.disposition,
            applied=False,
            reason=decision.reason,
        )
    github_client = pass_context.github_client
    config = pass_context.config
    target_label = (
        config.labels.failed
        if decision.disposition is StaleAttemptDisposition.FAIL
        else config.labels.ready
    )
    issue_before = github_client.get_issue(issue.number)
    if config.labels.running not in issue_before.labels:
        return ReconcileOutcome(
            issue_number=issue.number,
            disposition=decision.disposition,
            applied=False,
            reason=f"{stale_reason}: {decision.reason} (no longer agent/running)",
        )

    if not skip_comment:
        comment_body = format_stale_attempt_comment(
            StaleAttemptReport(
                issue_number=issue.number,
                claim=claim,
                decision=decision,
                dedupe_hash=dedupe_hash,
                stale_attempt_seq=stale_attempt_seq,
                stale_reason=stale_reason,
                attempt_history_excerpt=attempt_history_excerpt,
            )
        )
        github_client.comment_issue(issue.number, comment_body)
    transition_issue_workflow_state(github_client, issue.number, config, target_label)
    reason_suffix = " (comment already recorded)" if skip_comment else ""
    return ReconcileOutcome(
        issue_number=issue.number,
        disposition=decision.disposition,
        applied=True,
        reason=f"{stale_reason}: {decision.reason}{reason_suffix}",
    )


def _truncate_detail(detail_text: str) -> str:
    """把 worktree 探测的失败原因压到可读长度。"""
    collapsed = " ".join(detail_text.split())
    if len(collapsed) <= _WORKTREE_DETAIL_MAX_CHARS:
        return collapsed
    return collapsed[: _WORKTREE_DETAIL_MAX_CHARS - 1] + "…"


__all__ = ["ReconcileOutcome", "reconcile_stale_attempts"]

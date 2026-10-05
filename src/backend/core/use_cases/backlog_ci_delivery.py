"""Backlog CI/CD 交付投影与自动修复策略用例。

职责边界：

- **投影**：把 GitHub PR context（原始 checks）与 Issue 上的 ``iar:event`` markers
  合成为一份只读 :class:`CiDelivery`。轮次、耗尽原因、最近一次放行结论都从这两处
  重建，不新增数据库表、轮询线程或第二套事件流。
- **策略**：``inherit / on / off`` 三态覆盖（存在对应 Issue 的 latest-wins marker
  里）与仓库全局值（``.iar.toml`` 的 ``post_pr_supervisor.auto_repair_ci``）合成
  唯一 effective bool。effective 值只在 :func:`compute_effective_auto_repair` 计算
  一次，API、CLI、daemon 三条入口都调用它，前端不得自行推断。
- **门禁**：只约束 repair 动作。checks 状态本身不是触发条件，Agent 选择的其它合法
  动作（wait / 人审 / 请求输入）不受本模块影响。

不做的事：不判断"CI 失败是否算代码失败"（那是 Supervisor Agent 的证据判断）；不
直接调用 repair helper 执行修复——自动与手动都走既有 rework 意图评论，由 run pass
在既有 worktree、脏改动、禁止路径与验证门禁下消费。
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.interfaces.runner_console import (
    IRepositoryAutopilotSettingsEditor,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    PullRequestContext,
    RepositoryRunContext,
    ReviewEventMarker,
)
from backend.core.shared.models.backlog import (
    CI_PROBLEM_KIND_AGGREGATE,
    CI_PROBLEM_KIND_CHECK_FAILURE,
    CI_PROBLEM_KIND_CHECK_PENDING,
    CI_PROBLEM_KIND_NOT_RUN,
    CI_PROBLEM_KIND_UNAVAILABLE,
    BacklogCiAutoRepairState,
    BacklogCiRepairPolicy,
    BacklogCiRepairResult,
    CiCheckProblem,
    CiDelivery,
    CiDeliveryStatus,
    CiRepairGateDecision,
)
from backend.core.use_cases.agent_runner_events import (
    build_ci_auto_repair_policy_comment,
    parse_event_markers,
    parse_latest_ci_auto_repair_policy_marker,
)
from backend.core.use_cases.agent_runner_monitor import _extract_pr_branch_from_issue
from backend.core.use_cases.pr_supervisor import build_rework_intent_comment

_logger = logging.getLogger(__name__)

#: 修复意图事件的 phase（由 ``pr_supervisor.build_rework_intent_comment`` 写入）。
REWORK_REQUEST_PHASE = "post_pr_rework_requested"
#: 受本策略约束的 supervisor 动作。
REPAIR_ACTION = "repair_pr_branch"
#: Supervisor 观察结论所在 phase（其 ``action=`` 字段是 Agent 的决定）。
SUPERVISOR_PHASE = "post_pr_supervisor"

#: ``checks_summary`` 行格式来自 infrastructure ``_check_summary_line``：
#: ``名称 (status=X, conclusion=Y) <url>``。Backlog 只在下面这一处解析它，
#: 避免每个视图各自解释 checks。
_CHECK_SUMMARY_LINE_PATTERN = re.compile(
    r"^\s*(?P<name>.+?)\s*(?:\((?P<detail>[^)]*)\))?\s*(?P<url>https?://\S+)?\s*$"
)


class BacklogCiError(ValueError):
    """CI 设置或修复请求无法处理：仓库不存在、无对应 Issue、配置不可写。"""


@dataclass(frozen=True)
class CiRepairGateOutcome:
    """一次 repair 放行判定结果。"""

    decision: CiRepairGateDecision
    failure_key: str
    repair_rounds: int
    max_repair_attempts: int
    effective_auto_repair: bool
    stored_policy: BacklogCiRepairPolicy
    detail: str

    @property
    def allowed(self) -> bool:
        """服务端是否应把这次 repair 交给既有修复路径。"""
        return self.decision is CiRepairGateDecision.ALLOWED


def _now_iso() -> str:
    """投影时刻（ISO8601，UTC）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_stored_ci_repair_policy(comments: Sequence[str]) -> BacklogCiRepairPolicy:
    """从 Issue 评论解析单 PRD 持久策略（无 marker 即 ``inherit``）。"""
    return parse_latest_ci_auto_repair_policy_marker(list(comments))


def compute_effective_auto_repair(
    *,
    stored_policy: BacklogCiRepairPolicy,
    global_auto_repair: bool,
) -> bool:
    """三态覆盖与仓库全局值合成生效值：``on -> True``、``off -> False``、其余跟随全局。"""
    if stored_policy is BacklogCiRepairPolicy.ON:
        return True
    if stored_policy is BacklogCiRepairPolicy.OFF:
        return False
    return bool(global_auto_repair)


def build_ci_failure_key(
    *,
    pr_number: int | None,
    head_sha: str,
    checks_summary: Sequence[str] = (),
) -> str:
    """PR number + head SHA + 失败摘要的稳定指纹。

    daemon 重启后同一输入必须得到同一 key（去重依赖它），因此摘要按原顺序拼接、
    不掺入时间或随机量。head SHA 为空表示还没解析到 PR，此时不给可用指纹。
    """
    if not head_sha:
        return ""
    payload = "\n".join(
        [str(pr_number if pr_number is not None else ""), head_sha, *checks_summary]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _repair_requests(markers: Sequence[ReviewEventMarker]) -> list[ReviewEventMarker]:
    """挑出历史上的 repair 意图事件。"""
    return [
        marker
        for marker in markers
        if marker.phase == REWORK_REQUEST_PHASE and marker.action == REPAIR_ACTION
    ]


def count_ci_repair_rounds(markers: Sequence[ReviewEventMarker]) -> int:
    """已获准的修复轮次：不同 head SHA 上的 repair 意图数量。

    以 head 去重而不是按评论条数计，是因为同一轮可能因重入产生多条相同意图；一次
    修复推送一个新 head，所以"多少个不同 head 被请求过修复"就是轮次。
    """
    heads = {marker.head_sha for marker in _repair_requests(markers) if marker.head_sha}
    if not heads:
        return 0
    return len(heads)


def repair_requested_for_failure(
    markers: Sequence[ReviewEventMarker],
    *,
    failure_key: str,
    head_sha: str,
) -> bool:
    """该 failure key 是否已经获准过一次修复（重启/重入/重复点击的去重依据）。

    本功能之前写下的 repair 意图不带 ``failure_digest``，对这类历史 marker 退回
    按 head SHA 匹配：同一 head 上宁可少修一次，也不要重复推送。
    """
    for marker in _repair_requests(markers):
        if marker.failure_digest and marker.failure_digest == failure_key:
            return True
        if not marker.failure_digest and head_sha and marker.head_sha == head_sha:
            return True
    return False


def evaluate_ci_repair_gate(
    *,
    pr_number: int | None,
    head_sha: str,
    checks_summary: Sequence[str],
    markers: Sequence[ReviewEventMarker],
    stored_policy: BacklogCiRepairPolicy,
    global_auto_repair: bool,
    max_repair_attempts: int,
    manual: bool,
) -> CiRepairGateOutcome:
    """对一次 repair 动作做放行判定（自动与手动共用同一份判定）。

    手动请求跳过策略门禁（操作者已显式表达意图），但幂等去重、修复上限与"必须能
    fresh 解析当前 PR/head"三条对两者一致生效。

    Args:
        pr_number: 当前 PR 号；``None`` 表示无法解析。
        head_sha: 当前 PR head SHA；为空表示没有可判定上下文。
        checks_summary: 原始失败/等待摘要，参与 failure key。
        markers: 该 Issue 全部评论里解析出的事件 markers。
        stored_policy: 单 PRD 持久策略。
        global_auto_repair: 仓库全局值（必须由调用方 fresh 读取）。
        max_repair_attempts: 既有 ``post_pr_supervisor.max_repair_attempts``。
        manual: 是否来自操作者的显式单次修复请求。

    Returns:
        放行结论与判定依据（轮次、effective 值、面向人的一句话说明）。
    """
    effective = compute_effective_auto_repair(
        stored_policy=stored_policy,
        global_auto_repair=global_auto_repair,
    )
    rounds = count_ci_repair_rounds(markers)
    failure_key = build_ci_failure_key(
        pr_number=pr_number,
        head_sha=head_sha,
        checks_summary=checks_summary,
    )
    if not failure_key:
        return CiRepairGateOutcome(
            decision=CiRepairGateDecision.UNRESOLVED,
            failure_key="",
            repair_rounds=rounds,
            max_repair_attempts=max_repair_attempts,
            effective_auto_repair=effective,
            stored_policy=stored_policy,
            detail="无法解析当前 PR 与 head SHA，未执行修复。",
        )
    if repair_requested_for_failure(markers, failure_key=failure_key, head_sha=head_sha):
        return CiRepairGateOutcome(
            decision=CiRepairGateDecision.DUPLICATE,
            failure_key=failure_key,
            repair_rounds=rounds,
            max_repair_attempts=max_repair_attempts,
            effective_auto_repair=effective,
            stored_policy=stored_policy,
            detail="该 head 的这轮失败已经请求过一次修复，重复请求不再产生副作用。",
        )
    if not manual and not effective:
        return CiRepairGateOutcome(
            decision=CiRepairGateDecision.POLICY_OFF,
            failure_key=failure_key,
            repair_rounds=rounds,
            max_repair_attempts=max_repair_attempts,
            effective_auto_repair=effective,
            stored_policy=stored_policy,
            detail="自动修复 CI/CD 未开启；问题保留在详情中，不会启动修复 Agent。",
        )
    if rounds >= max(0, max_repair_attempts):
        return CiRepairGateOutcome(
            decision=CiRepairGateDecision.BUDGET_EXHAUSTED,
            failure_key=failure_key,
            repair_rounds=rounds,
            max_repair_attempts=max_repair_attempts,
            effective_auto_repair=effective,
            stored_policy=stored_policy,
            detail=(
                f"已达修复上限 max_repair_attempts={max_repair_attempts}，"
                "停止自动副作用并保留失败状态；需要人工处理。"
            ),
        )
    return CiRepairGateOutcome(
        decision=CiRepairGateDecision.ALLOWED,
        failure_key=failure_key,
        repair_rounds=rounds,
        max_repair_attempts=max_repair_attempts,
        effective_auto_repair=effective,
        stored_policy=stored_policy,
        detail="已交给既有修复路径处理。",
    )


def _parse_check_summary_line(summary_line: str) -> CiCheckProblem:
    """把一行 ``checks_summary`` 还原成问题条目（不推断根因、不编造 job 名）。"""
    match = _CHECK_SUMMARY_LINE_PATTERN.match(summary_line)
    if match is None:  # pragma: no cover - 正则可匹配任意单行。
        return CiCheckProblem(name=summary_line.strip() or "(unknown)", detail=summary_line)
    name = (match.group("name") or "").strip()
    detail = (match.group("detail") or "").strip()
    url = (match.group("url") or "").strip() or None
    lowered = detail.lower()
    kind = CI_PROBLEM_KIND_CHECK_FAILURE
    if "pending" in lowered or "in_progress" in lowered or "queued" in lowered:
        kind = CI_PROBLEM_KIND_CHECK_PENDING
    if not name:
        name = "(aggregate)"
        kind = CI_PROBLEM_KIND_AGGREGATE
    return CiCheckProblem(name=name, detail=detail or summary_line, url=url, kind=kind)


def ci_problems_from_checks_summary(
    checks_summary: Sequence[str],
    *,
    head_sha: str,
    round_index: int,
) -> tuple[CiCheckProblem, ...]:
    """把原始 checks 摘要映射成问题列表（每条都对应 GitHub 上真实存在的一行）。"""
    problems: list[CiCheckProblem] = []
    for summary_line in checks_summary:
        problem = _parse_check_summary_line(summary_line)
        problems.append(
            CiCheckProblem(
                name=problem.name,
                detail=problem.detail,
                kind=problem.kind,
                url=problem.url,
                head_sha=head_sha or None,
                round_index=round_index,
            )
        )
    return tuple(problems)


def _latest_supervisor_marker(markers: Sequence[ReviewEventMarker]) -> ReviewEventMarker | None:
    """最近一条 supervisor 观察结论。"""
    for marker in reversed(markers):
        if marker.phase == SUPERVISOR_PHASE:
            return marker
    return None


def _reconstruct_last_decision(
    *,
    markers: Sequence[ReviewEventMarker],
    failure_key: str,
    head_sha: str,
    gate: CiRepairGateOutcome,
) -> CiRepairGateDecision | None:
    """从 markers 重建"最近一次 repair 放行结论"（读路径不制造新结论）。

    只有真的请求过修复才算 ``ALLOWED``；Agent 选了 repair 但没有对应意图评论时，
    按当前门禁给出它被拦下的原因，其余情况返回 ``None``（没有发生过判定）。
    """
    if failure_key and repair_requested_for_failure(
        markers, failure_key=failure_key, head_sha=head_sha
    ):
        return CiRepairGateDecision.DUPLICATE
    supervisor_marker = _latest_supervisor_marker(markers)
    if supervisor_marker is not None and supervisor_marker.action == REPAIR_ACTION:
        if not gate.effective_auto_repair:
            return CiRepairGateDecision.POLICY_OFF
        if gate.repair_rounds >= gate.max_repair_attempts:
            return CiRepairGateDecision.BUDGET_EXHAUSTED
        return CiRepairGateDecision.ALLOWED
    return None


def _context_field(pr_context: PullRequestContext | None, field_name: str) -> object | None:
    """安全读取 PR context 字段（读路径不能因单个 PRD 的上下文形状异常而整页失败）。"""
    if pr_context is None:
        return None
    return getattr(pr_context, field_name, None)


def build_ci_delivery(
    *,
    prd_path: str,
    pr_context: PullRequestContext | None,
    comments: Sequence[str],
    config: AppConfig,
    unavailable_reason: str = "",
    pr_branch: str = "",
) -> CiDelivery | None:
    """投影单个 PRD 的 CI/CD 交付状态。

    Args:
        prd_path: PRD 仓库相对路径（仅作视图标识，不作为策略 key）。
        pr_context: 当前 PR context；``None`` 表示没有 PR 或读不到。
        comments: 该 Issue 的评论正文列表（策略与轮次的事实源）。
        config: 目标仓库的 fresh 生效配置（提供全局值与修复上限）。
        unavailable_reason: 有 PR 分支却拿不到 context 时的原因，如实透出。
        pr_branch: 已解析到的 PR 分支名（无 context 时也尽量带上，便于界面定位）。

    Returns:
        :class:`CiDelivery`；没有任何关联 PR 分支时返回 ``None``（本功能不适用，
        保持原有流程，不伪造一个空状态）。
    """
    markers = parse_event_markers(list(comments))
    stored_policy = resolve_stored_ci_repair_policy(comments)
    global_value = bool(config.post_pr_supervisor.auto_repair_ci)
    max_attempts = int(config.post_pr_supervisor.max_repair_attempts)
    effective = compute_effective_auto_repair(
        stored_policy=stored_policy,
        global_auto_repair=global_value,
    )
    rounds = count_ci_repair_rounds(markers)
    supervisor_marker = _latest_supervisor_marker(markers)

    head_sha = str(_context_field(pr_context, "head_sha") or "")
    raw_checks_state = _context_field(pr_context, "checks_state")
    checks_state = str(raw_checks_state) if raw_checks_state else None
    raw_summary = _context_field(pr_context, "checks_summary")
    checks_summary: tuple[str, ...] = tuple(str(line) for line in raw_summary or ())
    pr_number_raw = _context_field(pr_context, "number")
    pr_number = int(pr_number_raw) if isinstance(pr_number_raw, int) else None
    failure_key = build_ci_failure_key(
        pr_number=pr_number,
        head_sha=head_sha,
        checks_summary=checks_summary,
    )
    gate = evaluate_ci_repair_gate(
        pr_number=pr_number,
        head_sha=head_sha,
        checks_summary=checks_summary,
        markers=markers,
        stored_policy=stored_policy,
        global_auto_repair=global_value,
        max_repair_attempts=max_attempts,
        manual=False,
    )
    last_decision = _reconstruct_last_decision(
        markers=markers,
        failure_key=failure_key,
        head_sha=head_sha,
        gate=gate,
    )

    if pr_context is None:
        if not pr_branch and not unavailable_reason:
            return None
        status = CiDeliveryStatus.UNAVAILABLE
        problems = (
            CiCheckProblem(
                name="checks",
                detail=unavailable_reason or "无法读取 PR 上下文。",
                kind=CI_PROBLEM_KIND_UNAVAILABLE,
            ),
        )
        detail = unavailable_reason or "PR 上下文不可用；不视为通过，也不启动修复。"
    elif checks_state == "SUCCESS":
        status = CiDeliveryStatus.PASSING
        problems = ()
        detail = "最新 head 的 checks 全部通过。"
    elif checks_state == "FAILURE":
        status = CiDeliveryStatus.FAILING
        problems = ci_problems_from_checks_summary(
            checks_summary, head_sha=head_sha, round_index=rounds + 1
        )
        if not problems:
            problems = (
                CiCheckProblem(
                    name="checks",
                    detail="GitHub 仅返回聚合失败结果，没有 job 明细。",
                    kind=CI_PROBLEM_KIND_AGGREGATE,
                    head_sha=head_sha or None,
                    round_index=rounds + 1,
                ),
            )
        detail = (
            "自动修复已开启，修复由 Supervisor Agent 决定的动作与上限约束。"
            if effective
            else "自动修复未开启；问题保留在详情中，不会启动修复 Agent。"
        )
    elif checks_state == "PENDING":
        status = CiDeliveryStatus.PENDING
        problems = ci_problems_from_checks_summary(
            checks_summary, head_sha=head_sha, round_index=rounds + 1
        )
        detail = "等待 CI/CD：checks 尚未全部完成。"
    else:
        status = CiDeliveryStatus.NOT_RUN
        problems = (
            CiCheckProblem(
                name="checks",
                detail="该 head 没有可读取的 checks 记录：CI 未执行或未验证。",
                kind=CI_PROBLEM_KIND_NOT_RUN,
                head_sha=head_sha or None,
                round_index=rounds + 1,
            ),
        )
        detail = "未运行任何检查任务，不得当作代码失败或通过。"

    if rounds >= max_attempts and max_attempts >= 0:
        detail = (
            f"已达修复上限 max_repair_attempts={max_attempts}；" f"当前为第 {rounds} 轮。{detail}"
        )

    return CiDelivery(
        prd_path=prd_path,
        status=status,
        pr_number=pr_number,
        pr_url=str(_context_field(pr_context, "pr_url") or ""),
        pr_branch=str(_context_field(pr_context, "branch") or "") or pr_branch,
        head_sha=head_sha,
        checks_state=checks_state,
        problems=problems,
        repair_rounds=rounds,
        max_repair_attempts=max_attempts,
        repair_exhausted=rounds >= max_attempts,
        stored_policy=stored_policy,
        global_auto_repair=global_value,
        effective_auto_repair=effective,
        policy_source=(
            stored_policy.value
            if stored_policy is not BacklogCiRepairPolicy.INHERIT
            else BacklogCiRepairPolicy.INHERIT.value
        ),
        last_decision=last_decision.value if last_decision is not None else None,
        failure_key=failure_key or None,
        supervisor_action=supervisor_marker.action if supervisor_marker else None,
        supervisor_summary=None,
        last_synced_at=_now_iso(),
        detail=detail,
    )


def _resolve_context(
    repo_id: str, contexts: Sequence[RepositoryRunContext]
) -> RepositoryRunContext:
    """从已解析的仓库上下文中取出目标仓库。"""
    for context in contexts:
        if context.repo_id == repo_id:
            return context
    raise BacklogCiError(f"仓库 '{repo_id}' 不存在或未启用。")


def _relative_source_name(config_source: Path, repo_root_path: Path) -> str:
    """把配置来源表示为仓库相对路径，越界时退回绝对路径。"""
    try:
        return str(Path(config_source).resolve().relative_to(Path(repo_root_path).resolve()))
    except ValueError:
        return str(config_source)


def load_ci_auto_repair_state(
    *,
    repo_id: str,
    contexts: Sequence[RepositoryRunContext],
    editor: IRepositoryAutopilotSettingsEditor,
) -> BacklogCiAutoRepairState:
    """聚合当前仓库的 CI 自动修复设置（每次调用都 fresh 读盘与读生效配置）。"""
    context = _resolve_context(repo_id, contexts)
    try:
        persisted = editor.read_auto_repair_ci(context.repo_path)
    except ValueError as exc:
        raise BacklogCiError(str(exc)) from exc
    return BacklogCiAutoRepairState(
        repo_id=repo_id,
        auto_repair_ci=bool(context.config.post_pr_supervisor.auto_repair_ci),
        max_repair_attempts=int(context.config.post_pr_supervisor.max_repair_attempts),
        config_source=_relative_source_name(
            editor.config_source_path(context.repo_path), context.repo_path
        ),
        persisted_auto_repair=persisted,
    )


def set_ci_auto_repair_enabled(
    *,
    repo_id: str,
    enabled: bool,
    editor: IRepositoryAutopilotSettingsEditor,
    contexts_loader: Callable[[], Sequence[RepositoryRunContext]],
) -> BacklogCiAutoRepairState:
    """写回仓库级 ``auto_repair_ci``，并以 fresh load 的生效配置作为成功判据。

    只改这一个键：``autopilot.enabled``、``safety.auto_merge`` 与
    ``runner.fix_agent_enabled`` 都不受牵连，也不从它们推断。
    """
    context = _resolve_context(repo_id, contexts_loader())
    try:
        editor.set_auto_repair_ci(context.repo_path, enabled)
    except ValueError as exc:
        raise BacklogCiError(str(exc)) from exc
    fresh_context = _resolve_context(repo_id, contexts_loader())
    if bool(fresh_context.config.post_pr_supervisor.auto_repair_ci) is not enabled:
        raise BacklogCiError(
            "CI 自动修复写回后重新加载的配置与请求值不一致，原文件可能未被正确替换。"
        )
    return load_ci_auto_repair_state(
        repo_id=repo_id,
        contexts=(fresh_context,),
        editor=editor,
    )


def set_prd_ci_repair_policy(
    *,
    github_client: IGitHubClient,
    issue_number: int,
    policy: BacklogCiRepairPolicy,
) -> BacklogCiRepairPolicy:
    """把单 PRD 策略写成 Issue 上的 latest-wins marker，并 fresh 读回确认。

    Returns:
        读回的持久策略（不是请求体回显）。

    Raises:
        BacklogCiError: 写入后从 Issue 评论读不回同一策略。
    """
    github_client.comment_issue(issue_number, build_ci_auto_repair_policy_comment(policy))
    stored = resolve_stored_ci_repair_policy(github_client.list_issue_comments(issue_number))
    if stored is not policy:
        raise BacklogCiError(f"策略写回后读回为 {stored.value}，与请求的 {policy.value} 不一致。")
    return stored


def build_prd_ci_delivery(
    *,
    prd_path: str,
    issue_number: int,
    github_client: IGitHubClient,
    config: AppConfig,
) -> CiDelivery | None:
    """单个 PRD 的 fresh CI 投影（API 详情标签与 CLI ``status`` 共用，不读缓存）。"""
    snapshot = _snapshot_issue(github_client, issue_number)
    pr_context = (
        github_client.get_pull_request_context(snapshot.pr_branch) if snapshot.pr_branch else None
    )
    return build_ci_delivery(
        prd_path=prd_path,
        pr_context=pr_context,
        comments=snapshot.comments,
        config=config,
        pr_branch=snapshot.pr_branch or "",
        unavailable_reason=(
            "PR 上下文不可用（GitHub 读取失败或 PR 状态异常）；不视为通过，也不启动修复。"
            if snapshot.pr_branch and pr_context is None
            else ""
        ),
    )


def resolve_prd_issue_number(
    repo_path: Path,
    prd_path: str,
) -> int | None:
    """按仓库相对路径定位 PRD 对应的 Issue 号（含归档目录）。

    以 PRD 文件里的 Issue 链接为准：PRD 路径只是入口标识，策略与轮次都挂在 Issue 上
    （路径会随归档改变，Issue 号不会）。
    """
    from backend.core.use_cases.backlog_prd_scanner import scan_backlog_prds

    scan_result = scan_backlog_prds(repo_path, include_archived=True)
    for prd in scan_result.prds:
        if prd.prd_path == prd_path:
            return prd.issue_number
    return None


def _existing_worktree_path(
    *,
    repo_path: Path,
    issue_number: int,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> Path | None:
    """按既有 ``worktree.path_command`` 解析该 Issue 的 worktree 目录（只解析，不创建）。

    修复动作最终由 run pass 在其 worktree 里执行；这里只做"该 Issue 是否已经有可用
    worktree"的前置判定，不复制 create/stash/禁止路径那套门禁。
    """
    from backend.core.use_cases.agent_runner_worktree_create import format_command
    from backend.core.use_cases.worktree_path_output import parse_worktree_path_stdout

    try:
        path_result = process_runner.run(
            format_command(config.worktree.path_command, issue_number=issue_number),
            cwd=repo_path,
        )
        raw_path = parse_worktree_path_stdout(path_result.stdout)
    except Exception as exc:  # noqa: BLE001 - 无法解析路径按不可用处理。
        _logger.info("Failed to resolve worktree path for issue #%s: %s", issue_number, exc)
        return None
    if path_result.return_code != 0:
        return None
    if not raw_path.is_absolute():
        raw_path = repo_path / raw_path
    return raw_path if raw_path.is_dir() else None


def request_manual_ci_repair(
    *,
    issue_number: int,
    repo_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    dry_run: bool = False,
) -> BacklogCiRepairResult:
    """操作者显式发起一次修复：fresh 解析当前 PR/head/failure 后走既有修复路径。

    与自动路径的差别只有一处：**不检查策略开关**（人已明确授权这一次）。幂等去重、
    ``max_repair_attempts``、"必须能读到当前 PR context 与 head"、"该 Issue 已有可用
    worktree"四条对两条路径共用同一份判定；脏改动、禁止路径与提交前验证仍由消费该
    意图的既有 run pass 执行，不在这里另立一套门禁。

    Args:
        dry_run: 只报告将要执行的结论，不写任何 Issue 副作用（CLI ``--dry-run``）。
            判定链路与真跑完全相同，因此结论可信；差别仅在最后两步不执行。

    Returns:
        放行或被拒的结论（被拒时 ``accepted=False`` 且没有任何副作用）。

    Raises:
        BacklogCiError: 无法解析 PR 分支、PR 上下文或 worktree（此时不产生副作用）。
    """
    from backend.core.use_cases.agent_runner_workflow import (
        transition_issue_workflow_state,
    )

    snapshot = _snapshot_issue(github_client, issue_number)
    comments = snapshot.comments
    markers = snapshot.markers
    prd_path = snapshot.prd_path

    if snapshot.pr_branch is None:
        raise BacklogCiError("该 PRD 的 Issue 上还没有可识别的 PR 分支，无法修复。")
    pr_context = github_client.get_pull_request_context(snapshot.pr_branch)
    if pr_context is None:
        raise BacklogCiError(
            f"无法读取分支 {snapshot.pr_branch} 的当前 PR 上下文；拒绝按过期状态修复。"
        )
    if (
        _existing_worktree_path(
            repo_path=repo_path,
            issue_number=issue_number,
            config=config,
            process_runner=process_runner,
        )
        is None
    ):
        raise BacklogCiError("该 Issue 还没有可用 worktree，无法执行修复；请先让 run pass 接管。")

    stored_policy = resolve_stored_ci_repair_policy(comments)
    gate = evaluate_ci_repair_gate(
        pr_number=pr_context.number,
        head_sha=pr_context.head_sha,
        checks_summary=pr_context.checks_summary,
        markers=markers,
        stored_policy=stored_policy,
        global_auto_repair=bool(config.post_pr_supervisor.auto_repair_ci),
        max_repair_attempts=int(config.post_pr_supervisor.max_repair_attempts),
        manual=True,
    )
    if not gate.allowed:
        return BacklogCiRepairResult(
            prd_path=prd_path,
            accepted=False,
            decision=gate.decision.value,
            failure_key=gate.failure_key,
            head_sha=pr_context.head_sha,
            detail=gate.detail,
        )

    round_label = f"第 {gate.repair_rounds + 1} 轮修复（上限 {gate.max_repair_attempts}）"
    if dry_run:
        return BacklogCiRepairResult(
            prd_path=prd_path,
            accepted=True,
            decision=gate.decision.value,
            failure_key=gate.failure_key,
            head_sha=pr_context.head_sha,
            detail=f"dry-run：将请求{round_label}，本次未写任何 Issue 副作用。",
        )

    github_client.comment_issue(
        issue_number,
        build_rework_intent_comment(
            action=REPAIR_ACTION,
            pr_branch=snapshot.pr_branch,
            head_sha=pr_context.head_sha,
            failure_digest=gate.failure_key,
            requested_by=f"operator:{repo_path.name}",
        ),
    )
    transition_issue_workflow_state(github_client, issue_number, config, config.labels.running)
    return BacklogCiRepairResult(
        prd_path=prd_path,
        accepted=True,
        decision=gate.decision.value,
        failure_key=gate.failure_key,
        head_sha=pr_context.head_sha,
        detail=f"已请求{round_label}；由下一次 run pass 执行。",
    )


def _pr_branch_for_issue(
    github_client: IGitHubClient,
    issue_number: int,
    issue_body: str,
    comments: Sequence[str],
) -> str | None:
    """复用监控层的分支解析规则（marker 优先，正文兜底），不另写一份。"""
    issue = IssueSummary(
        number=issue_number,
        title="",
        url="",
        body=issue_body,
        labels=(),
    )
    return _extract_pr_branch_from_issue(issue, github_client, list(comments))


@dataclass
class _IssueSnapshot:
    """从 Issue 上取到的一次性快照（评论 + PR 分支 + PRD 路径）。"""

    comments: list[str]
    markers: list[ReviewEventMarker]
    pr_branch: str | None
    prd_path: str


def _snapshot_issue(
    github_client: IGitHubClient,
    issue_number: int,
) -> _IssueSnapshot:
    """fresh 读取一个 Issue 的评论，并顺带解析 markers、PR 分支与 PRD 路径。

    只允许一次 ``list_issue_comments``：每多一条评论都会翻转
    ``issue_comments_count``，让下一轮 supervisor 误判"上下文已变化"。
    """
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path

    try:
        issue = github_client.get_issue(issue_number)
        issue_body = issue.body
    except Exception as exc:  # noqa: BLE001
        _logger.info("Failed to fetch issue #%s: %s", issue_number, exc)
        issue_body = ""
    comments = list(github_client.list_issue_comments(issue_number))
    return _IssueSnapshot(
        comments=comments,
        markers=parse_event_markers(comments),
        pr_branch=_pr_branch_for_issue(github_client, issue_number, issue_body, comments),
        prd_path=extract_prd_path(issue_body) or "",
    )


__all__ = [
    "BacklogCiError",
    "CiRepairGateOutcome",
    "REPAIR_ACTION",
    "REWORK_REQUEST_PHASE",
    "SUPERVISOR_PHASE",
    "build_ci_delivery",
    "build_ci_failure_key",
    "build_prd_ci_delivery",
    "ci_problems_from_checks_summary",
    "compute_effective_auto_repair",
    "count_ci_repair_rounds",
    "evaluate_ci_repair_gate",
    "load_ci_auto_repair_state",
    "repair_requested_for_failure",
    "request_manual_ci_repair",
    "resolve_prd_issue_number",
    "resolve_stored_ci_repair_policy",
    "set_ci_auto_repair_enabled",
    "set_prd_ci_repair_policy",
]

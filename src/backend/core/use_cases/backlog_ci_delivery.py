"""Backlog CI/CD 交付尾段用例：策略合成、状态投影与单次手动修复。

职责边界（刻意保守）：

- **读**：从 GitHub PR context 与既有 ``iar:event`` / ``iar:ci-auto-repair-policy``
  marker 投影出只读的 :class:`BacklogCiDelivery`；不持久化派生视图，每次调用
  fresh 读取。
- **写**：单 PRD 策略只写对应 Issue 的 latest-wins ``iar:ci-auto-repair-policy``
  marker；单次手动修复复用既有 ``post_pr_rework_requested`` rework 意图评论 +
  既有 run 侧 ``execute_repair``（上限 / worktree / 禁止路径门禁全部沿用）。

不做的事：不新增 CI 轮询线程、数据库表或第二套 repair 实现；不根据
``checks_state`` 聚合状态映射动作（Agent-led CI 决策契约）；仓库级全局开关
``post_pr_supervisor.auto_repair_ci`` 的写回属于受限配置编辑器端口，由调用方
（API / CLI）注入。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from backend.core.shared.models.agent_runner import PullRequestContext
from backend.core.shared.models.backlog import (
    BacklogCiDelivery,
    CiCheckProblem,
    CiDeliveryStatus,
    CiRepairPolicy,
)
from backend.core.use_cases.agent_runner_events import (
    iter_event_markers,
    format_ci_auto_repair_policy_marker,
    parse_latest_ci_auto_repair_policy,
)

if TYPE_CHECKING:
    from backend.core.shared.interfaces.agent_runner import IGitHubClient
    from backend.core.shared.models.agent_runner import AppConfig, IssueSummary

_REWORK_PHASE = "post_pr_rework_requested"
_REPAIR_ACTION = "repair_pr_branch"


class BacklogCiPolicyError(ValueError):
    """CI 自动修复策略读取/写回失败（仓库缺失、Issue 缺失或值非法）。"""


def compute_effective_auto_repair(stored_policy: str | None, global_enabled: bool) -> bool:
    """按 ``显式覆盖 ?? 仓库全局`` 计算最终生效值（服务端唯一计算点）。

    Args:
        stored_policy: 对应 Issue 最新 marker 的值；``None`` / ``inherit`` 表示
            未显式设置，跟随仓库全局。
        global_enabled: 仓库全局 ``post_pr_supervisor.auto_repair_ci`` fresh 值。

    Returns:
        最终生效布尔值。前端与 CLI 都不得自行推断。
    """
    if stored_policy == CiRepairPolicy.ON.value:
        return True
    if stored_policy == CiRepairPolicy.OFF.value:
        return False
    return global_enabled


def read_stored_policy(comments: list[str]) -> CiRepairPolicy:
    """读取 Issue 评论流中最新一条策略 marker；无 marker 即 :attr:`CiRepairPolicy.INHERIT`。"""
    value = parse_latest_ci_auto_repair_policy(comments)
    if value is None:
        return CiRepairPolicy.INHERIT
    return CiRepairPolicy(value)


def count_ci_repair_rounds(comments: list[str]) -> int:
    """统计已发生的自动修复轮数。

    轮次事实源是既有 ``post_pr_rework_requested`` marker 且 ``action`` 为
    ``repair_pr_branch`` 的评论次数；每个新 head SHA 一轮，不去重（同一 head
    的重复 marker 由写侧幂等保证不产生）。
    """
    return sum(
        1
        for marker in iter_event_markers(comments)
        if marker.phase == _REWORK_PHASE and marker.action == _REPAIR_ACTION
    )


def _status_from_checks(
    pr_context: PullRequestContext | None,
) -> tuple[CiDeliveryStatus, str | None]:
    """把 PR context 的原始 checks 观察映射为聚合状态（不映射动作）。"""
    if pr_context is None:
        return CiDeliveryStatus.NO_PR, None
    checks_state = pr_context.checks_state
    if checks_state is None or str(checks_state).upper() == "UNKNOWN":
        return CiDeliveryStatus.UNAVAILABLE, None
    normalized = str(checks_state).upper()
    if normalized == "SUCCESS":
        return CiDeliveryStatus.SUCCESS, None
    if normalized == "FAILURE":
        return CiDeliveryStatus.FAILURE, None
    if normalized == "PENDING":
        return CiDeliveryStatus.PENDING, None
    # 未知状态降级为不可用，按未验证呈现，不伪造通过或失败。
    return CiDeliveryStatus.UNAVAILABLE, None


def build_ci_delivery(
    *,
    prd_path: str,
    issue_number: int | None,
    comments: list[str],
    pr_context: PullRequestContext | None,
    global_enabled: bool,
    max_rounds: int,
    pr_url: str | None = None,
) -> BacklogCiDelivery:
    """从 PR context 与 marker 投影 CI/CD 交付尾段（每次 fresh 调用）。

    Args:
        prd_path: PRD 仓库相对路径（投影身份，不作存储键）。
        issue_number: 对应 GitHub Issue 编号；无 Issue 的 PRD 策略只能 inherit。
        comments: 该 Issue 的评论正文列表（含事件与策略 marker）。
        pr_context: 最新 PR context；``None`` 表示尚无 PR 或获取失败。
        global_enabled: 仓库全局自动修复开关（fresh 配置值）。
        max_rounds: 修复轮数上限（复用 ``max_repair_attempts``）。
        pr_url: GitHub PR 链接（问题卡跳转用）。

    Returns:
        只读投影。``status=NO_PR`` 时轮次/问题为空，但仍回显策略三态。
    """
    stored_policy = read_stored_policy(comments)
    effective_enabled = compute_effective_auto_repair(stored_policy.value, global_enabled)
    round_count = count_ci_repair_rounds(comments)
    status, _unused = _status_from_checks(pr_context)
    head_sha = pr_context.head_sha if pr_context is not None else None
    checks_summary = pr_context.checks_summary if pr_context is not None else ()
    problems: tuple[CiCheckProblem, ...] = ()
    if status is CiDeliveryStatus.FAILURE and checks_summary:
        # 问题卡以 GitHub checks_summary 为输入：只呈递原始摘要行，不推断
        # job 名、日志细节或根因；信息不足时保持汇总文本原样。
        problems = tuple(
            CiCheckProblem(name=line, summary=line, url=pr_url, round_number=round_count)
            for line in checks_summary
            if line.strip()
        )
    exhausted = round_count >= max_rounds
    exhausted_reason = (
        f"修复轮数已达上限（{round_count}/{max_rounds}）；自动修复停止，问题保留待人处理。"
        if exhausted
        else None
    )
    return BacklogCiDelivery(
        prd_path=prd_path,
        issue_number=issue_number,
        status=status,
        checks_state=pr_context.checks_state if pr_context is not None else None,
        checks_summary=checks_summary,
        pr_url=pr_url if pr_url else (pr_context.pr_url if pr_context is not None else None),
        head_sha=head_sha,
        round_count=round_count,
        max_rounds=max_rounds,
        problems=problems,
        stored_policy=stored_policy,
        global_enabled=global_enabled,
        effective_enabled=effective_enabled,
        exhausted=exhausted,
        exhausted_reason=exhausted_reason,
        last_synced_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def set_prd_ci_policy(
    *,
    github_client: IGitHubClient,
    issue_number: int,
    value: CiRepairPolicy | str,
) -> str:
    """把单 PRD 策略覆盖写成对应 Issue 的最新 marker（latest-wins）。

    写 ``inherit`` 表示清除显式覆盖：marker 仍存在但值等于"跟随全局"，符合
    PRD「清除覆盖并立即按当前仓库全局值计算」的语义。

    Returns:
        写入的评论正文（含 marker），供调用方回显与 fresh 验证。

    Raises:
        BacklogCiPolicyError: Issue 缺失或策略值非法。
    """
    policy_value = value.value if isinstance(value, CiRepairPolicy) else str(value)
    try:
        CiRepairPolicy(policy_value)
    except ValueError as exc:
        raise BacklogCiPolicyError(
            f"非法的 CI 自动修复策略值: {policy_value!r}（合法值: inherit/on/off）。"
        ) from exc
    marker = format_ci_auto_repair_policy_marker(policy_value)
    label = {
        CiRepairPolicy.INHERIT.value: "跟随全局",
        CiRepairPolicy.ON.value: "强制开启",
        CiRepairPolicy.OFF.value: "强制关闭",
    }[policy_value]
    comment_body = "\n".join(
        [
            marker,
            "",
            "## CI/CD 自动修复策略（本 PRD）",
            "",
            f"- Policy: `{policy_value}`（{label}）",
            "- 本 marker 为 latest-wins 覆盖；`inherit` 表示清除显式覆盖。",
        ]
    )
    github_client.comment_issue(issue_number, comment_body)
    return comment_body


def resolve_effective_policy_for_issue(
    *,
    config: AppConfig,
    github_client: IGitHubClient,
    issue_number: int,
) -> bool:
    """读取一个 Issue 的最终生效自动修复策略（core 唯一入口）。

    daemon（review_once）与 API/CLI 手动修复路径共用本函数，保证两侧不会
    各自实现 effective 计算而漂移。
    """
    comments = github_client.list_issue_comments(issue_number)
    stored = read_stored_policy(comments)
    return compute_effective_auto_repair(
        stored.value, bool(config.post_pr_supervisor.auto_repair_ci)
    )


def request_manual_ci_repair(
    *,
    issue: IssueSummary,
    pr_context: PullRequestContext,
    config: AppConfig,
    github_client: IGitHubClient,
) -> tuple[bool, str]:
    """显式请求一次修复（问题卡 / ``kc backlog ci repair`` 共用语义）。

    服务端 fresh 解析当前 PR head 并以 ``head SHA + action`` 为幂等键：同一
    失败轮次的重复请求零新增副作用。修复本身仍由既有 run 侧
    ``execute_repair`` 执行，保留上限、worktree 与禁止路径门禁。

    Returns:
        ``(requested, detail)``：``requested=False`` 表示幂等跳过或被拒绝。
    """
    from backend.core.use_cases.pr_supervisor import build_rework_intent_comment
    from backend.core.use_cases.agent_runner_workflow import transition_issue_workflow_state

    comments = github_client.list_issue_comments(issue.number)
    for marker in iter_event_markers(comments):
        if (
            marker.phase == _REWORK_PHASE
            and marker.action == _REPAIR_ACTION
            and marker.head_sha == pr_context.head_sha
        ):
            return False, (
                f"当前 head {pr_context.head_sha[:12]} 的修复请求已存在（幂等跳过，"
                "不新增副作用）。"
            )
    round_count = count_ci_repair_rounds(comments)
    max_rounds = max(0, config.post_pr_supervisor.max_repair_attempts)
    if round_count >= max_rounds:
        return False, (
            f"修复轮数已耗尽（{round_count}/{max_rounds}）；请先人工处理或调高 "
            "post_pr_supervisor.max_repair_attempts。"
        )
    github_client.comment_issue(
        issue.number,
        build_rework_intent_comment(
            action=_REPAIR_ACTION,
            pr_branch=pr_context.branch,
            head_sha=pr_context.head_sha,
        ),
    )
    transition_issue_workflow_state(github_client, issue.number, config, config.labels.running)
    return True, f"已请求一次修复（第 {round_count + 1} 轮，head {pr_context.head_sha[:12]}）。"


def gate_auto_repair_decision(
    *,
    config: AppConfig,
    github_client: IGitHubClient,
    issue_number: int,
    pr_context: PullRequestContext,
    pr_branch: str,
) -> tuple[bool, str]:
    """Supervisor Agent 选择 repair 后的策略门禁（review_once 调用）。

    按「Agent 决定 + 策略约束」执行：``checks_state`` 不触发动作，这里只在
    Agent 已选择 ``repair_pr_branch`` 后判断生效策略与剩余预算。返回
    ``(allowed, reason)``；不允许时调用方必须零副作用（不评论修复意图、
    不改 label、不 push）。
    """
    comments = github_client.list_issue_comments(issue_number)
    stored = read_stored_policy(comments)
    effective = compute_effective_auto_repair(
        stored.value, bool(config.post_pr_supervisor.auto_repair_ci)
    )
    if not effective:
        return False, (
            "CI/CD 自动修复未开启"
            + (
                "（本 PRD 策略：强制关闭）"
                if stored is CiRepairPolicy.OFF
                else "（仓库全局默认关闭）"
            )
            + "；失败检查保留在 Backlog 问题卡中，可显式请求一次修复。"
        )
    round_count = count_ci_repair_rounds(comments)
    max_rounds = max(0, config.post_pr_supervisor.max_repair_attempts)
    if round_count >= max_rounds:
        return False, f"自动修复轮数已耗尽（{round_count}/{max_rounds}）；停止新的自动修复。"
    return (
        True,
        f"自动修复允许（第 {round_count + 1}/{max_rounds} 轮，head {pr_context.head_sha[:12]}）。",
    )


def enqueue_approved_auto_repair(
    *,
    issue: IssueSummary,
    pr_context: PullRequestContext,
    config: AppConfig,
    github_client: IGitHubClient,
    pr_branch: str,
) -> tuple[bool, str]:
    """策略门禁通过后写入修复意图评论并切回 running（复用既有 rework 路径）。

    幂等键与手动修复一致：同一 head SHA 的 ``post_pr_rework_requested`` +
    ``repair_pr_branch`` marker 已存在时零新增副作用（daemon 重入、页面重试
    或崩溃恢复都不会重复修复同一轮）。
    """
    from backend.core.use_cases.pr_supervisor import build_rework_intent_comment
    from backend.core.use_cases.agent_runner_workflow import transition_issue_workflow_state

    comments = github_client.list_issue_comments(issue.number)
    for marker in iter_event_markers(comments):
        if (
            marker.phase == _REWORK_PHASE
            and marker.action == _REPAIR_ACTION
            and marker.head_sha == pr_context.head_sha
        ):
            return False, "同一 head 的修复请求已存在；跳过重复排队。"
    github_client.comment_issue(
        issue.number,
        build_rework_intent_comment(
            action=_REPAIR_ACTION,
            pr_branch=pr_branch,
            head_sha=pr_context.head_sha,
        ),
    )
    transition_issue_workflow_state(github_client, issue.number, config, config.labels.running)
    return True, "已排队自动修复（post_pr_rework_requested）。"


# 供 API/CLI 层把 dataclass 投影序列化时保持字段命名一致（复用 _serialize）。
__all__ = [
    "BacklogCiPolicyError",
    "build_ci_delivery",
    "compute_effective_auto_repair",
    "count_ci_repair_rounds",
    "enqueue_approved_auto_repair",
    "gate_auto_repair_decision",
    "read_stored_policy",
    "request_manual_ci_repair",
    "resolve_effective_policy_for_issue",
    "set_prd_ci_policy",
]

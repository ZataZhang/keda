"""Agent Runner workflow state helpers.

提供 phase-agnostic 的 workflow label 互斥转换和 marker history 判断，
供 CI rework recovery、forbidden blocked resolution 等后续恢复任务复用。
"""

from __future__ import annotations

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary, ReviewEventMarker
from backend.core.use_cases.agent_runner_direct_pr_label import can_auto_merge_issue
from backend.core.use_cases.agent_runner_events import _parse_event_marker

LEGACY_REVIEW_LABEL = "agent/review"


def workflow_state_labels(config: AppConfig) -> list[str]:
    """返回所有 durable workflow state labels。

    这些标签代表 Issue 的生命周期状态，状态切换时会从当前 labels 中
    移除其他 workflow labels，只保留目标标签。人工待办标签是独立维度，不在此集合中。
    """
    return [
        config.labels.ready,
        config.labels.running,
        config.labels.supervising,
        config.labels.review,
        LEGACY_REVIEW_LABEL,
        config.labels.failed,
        config.labels.blocked,
    ]


def issue_requires_human_review(issue: IssueSummary, config: AppConfig) -> bool:
    """判断自动审查通过后是否仍需要人工处理。

    Args:
        issue: 待判断的 GitHub Issue。
        config: 当前运行配置。

    Returns:
        未获自动合并授权时返回 True。
    """
    return not can_auto_merge_issue(issue, config)


def build_transition_labels(
    current_labels: tuple[str, ...],
    config: AppConfig,
    target_label: str,
    *,
    human_review_required: bool = False,
) -> list[str]:
    """计算状态切换后的 labels 列表。

    规则：
    - 添加 target_label
    - 移除所有其他 durable workflow labels
    - 根据目标状态更新人工待办标签
    - 保留其他非 workflow labels（如 agent routing labels、type labels）

    Args:
        current_labels: 当前 Issue 的所有 labels
        config: 应用配置
        target_label: 目标 workflow state label
        human_review_required: 目标为自动审查通过时，是否仍需人工处理

    Returns:
        切换后应设置的完整 labels 列表
    """
    durable = set(workflow_state_labels(config))
    preserved = [
        label
        for label in current_labels
        if label not in durable and label != config.labels.human_review
    ]
    if target_label not in preserved:
        preserved.append(target_label)
    if target_label == config.labels.review and human_review_required:
        preserved.append(config.labels.human_review)
    return preserved


def transition_issue_workflow_state(
    github_client: IGitHubClient,
    issue_number: int,
    config: AppConfig,
    target_label: str,
) -> tuple[bool, list[str]]:
    """将 Issue 切换到目标 workflow state，保持 label 互斥。

    先读取当前 labels，计算应保留的非 workflow labels，然后执行更新。

    Args:
        github_client: GitHub 客户端
        issue_number: Issue 编号
        config: 应用配置
        target_label: 目标 workflow state label

    Returns:
        (是否成功, 最终 labels 列表)。成功时返回更新后的 labels。
    """
    issue = github_client.get_issue(issue_number)
    human_review_required = target_label == config.labels.review and issue_requires_human_review(
        issue, config
    )
    new_labels = build_transition_labels(
        issue.labels,
        config,
        target_label,
        human_review_required=human_review_required,
    )
    github_client.edit_issue_labels(
        issue_number,
        add=new_labels,
        remove=[label for label in issue.labels if label not in new_labels],
    )
    return True, new_labels


def synchronize_human_review_label(
    github_client: IGitHubClient,
    issue: IssueSummary,
    config: AppConfig,
) -> bool:
    """策略需要人工处理时，确保自动审查通过的 Issue 带人工待办标签。

    Args:
        github_client: 用于读取和更新 Issue 标签的 GitHub 客户端。
        issue: review 候选 Issue。
        config: 当前运行配置。

    Returns:
        本次是否新增了人工待办标签。
    """
    current_issue = github_client.get_issue(issue.number)
    if current_issue.state.upper() != "OPEN" or config.labels.review not in current_issue.labels:
        return False

    label_present = config.labels.human_review in current_issue.labels
    label_required = issue_requires_human_review(current_issue, config)
    # 已存在的人工待办也可能来自 PRD 的 Human-Confirmed hold；自动合并授权
    # 不代表这类显式人工验收已完成，因此这里只补标签，不按策略清除已有标签。
    if not label_required or label_present:
        return False

    github_client.edit_issue_labels(
        issue.number,
        add=[config.labels.human_review],
    )
    return True


def find_latest_unconsumed_marker(
    comments: list[str],
    phase: str,
    completion_phases: set[str],
) -> ReviewEventMarker | None:
    """查找最近的、尚未被完成事件消费的指定 phase marker。

    从评论列表倒序扫描 event markers。如果找到目标 phase，则检查其后
    是否出现了 completion phases 中的任一 phase；若出现，则视为已消费。

    Args:
        comments: Issue 评论正文列表（按时间顺序）
        phase: 目标 marker phase（如 ``post_pr_rework_requested``）
        completion_phases: 表示消费完成的 phase 集合

    Returns:
        未消费的 marker，或 None
    """
    has_later_completion = False
    for comment_body in reversed(comments):
        marker = _parse_event_marker(comment_body)
        if marker is None:
            continue
        if marker.phase == phase:
            if has_later_completion:
                return None
            return marker
        if marker.phase in completion_phases:
            has_later_completion = True
    return None


def claim_blocked_issue(
    github_client: IGitHubClient,
    issue_number: int,
    config: AppConfig,
) -> bool:
    """尝试竞争认领一个 blocked Issue，将其切换到 running。

    采用读-检查-写模式（best-effort CAS）：先读取当前 labels，确认仍是
    blocked；然后执行 transition；最后重新读取 labels，确认已成功切换为
    running 且不再包含 blocked。二次确认失败时返回 False。

    .. note::
        这不是真正的 compare-and-swap。在极高并发下存在 TOCTOU 窗口：
        两个 runner 可能同时读取到 blocked 状态并同时写入 running。
        二次读取确认可将该窗口缩到极小，但无法彻底消除。后续应通过
        ``If-Match`` ETag 实现真正的原子 label 更新。

    Args:
        github_client: GitHub 客户端
        issue_number: Issue 编号
        config: 应用配置

    Returns:
        是否成功认领
    """
    issue_before = github_client.get_issue(issue_number)
    if config.labels.blocked not in issue_before.labels:
        return False

    transition_issue_workflow_state(github_client, issue_number, config, config.labels.running)

    issue_after = github_client.get_issue(issue_number)
    return (
        config.labels.running in issue_after.labels
        and config.labels.blocked not in issue_after.labels
    )

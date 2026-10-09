"""console 的仓库 Issue 读视图：全量列表（含未进入工作流的 Issue）。

监控面板原来只看得到带 agent 队列标签的 Issue，因此 GitHub 上存在、但还没进
工作流的 Issue 在网页上完全不可见，也就无法从网页把它送进工作流。本模块补上
「不过滤标签的列举」这一层，并给每条结果标注 ``monitored``——它表示该 Issue 是否
带有 :func:`backend.core.use_cases.agent_runner_workflow.workflow_state_labels`
定义的队列标签，即监控列表是否收录它。

标签集合来自配置派生的同一个函数，不另写一份清单，避免「网页说的监控中」与
「监控列表实际收录的」两套口径。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import IssueSummary, RepositoryRunContext
from backend.core.use_cases.agent_runner_workflow import workflow_state_labels

_logger = logging.getLogger(__name__)

#: 单次列举的默认上限；单仓库 open Issue 量级远小于此，超限由调用方显式提出。
DEFAULT_ISSUE_LIST_LIMIT = 200

#: 允许的上限天花板，防止客户端用一个巨大 limit 把 gh 拖成长请求。
MAX_ISSUE_LIST_LIMIT = 500


@dataclass(frozen=True)
class ConsoleIssueEntry:
    """仓库 Issue 列表里的一行（轻量：不含评论 / PR / worktree 等重组件）。

    Attributes:
        number: Issue 编号。
        title: Issue 标题。
        url: Issue 网页地址。
        state: GitHub 状态原值（``"OPEN"`` / ``"CLOSED"``）。
        labels: Issue 当前标签名。
        monitored: 是否带 agent 队列标签（``True`` 即监控列表会收录它）。
    """

    number: int
    title: str
    url: str
    state: str
    labels: tuple[str, ...]
    monitored: bool


def _to_entry(issue: IssueSummary, queue_labels: frozenset[str]) -> ConsoleIssueEntry:
    return ConsoleIssueEntry(
        number=issue.number,
        title=issue.title,
        url=issue.url,
        state=issue.state,
        labels=tuple(issue.labels),
        monitored=any(label in queue_labels for label in issue.labels),
    )


def list_repository_issues(
    *,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
    state: str = "open",
    limit: int = DEFAULT_ISSUE_LIST_LIMIT,
    label: str | None = None,
) -> list[ConsoleIssueEntry]:
    """列举仓库 Issue（默认不按标签过滤），并标注每条是否已被监控收录。

    Args:
        context: 已解析的仓库运行上下文（决定队列标签口径）。
        github_client: 该仓库的 GitHub 客户端。
        state: ``"open"`` / ``"closed"`` / ``"all"``。
        limit: 返回条数上限，落在 ``[1, 500]``。
        label: 可选标签过滤；``None`` 时返回全部（这正是「全部 Issue」视图
            相对监控列表的缺口补齐），给出时只保留带该标签的 Issue。

    Returns:
        :class:`ConsoleIssueEntry` 列表（GitHub 返回顺序，不做二次排序）。

    Raises:
        ValueError: ``state`` 不是允许的三个取值之一。
    """
    normalized_state = state.strip().lower()
    if normalized_state not in {"open", "closed", "all"}:
        raise ValueError(f"Unsupported issue state filter: {state!r}.")
    bounded_limit = min(max(limit, 1), MAX_ISSUE_LIST_LIMIT)
    queue_labels = frozenset(workflow_state_labels(context.config))
    listed_issues = github_client.list_issues_by_label(
        label.strip() if label and label.strip() else None, bounded_limit, state=normalized_state
    )
    return [_to_entry(issue, queue_labels) for issue in listed_issues]

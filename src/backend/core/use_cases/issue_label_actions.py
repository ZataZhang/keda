"""Issue 标签的查看与「集合内增删」用例（console 写路径）。

网页只允许增删仓库**已同步的标准标签**——集合定义与 ``kc labels sync`` 同源
（:mod:`backend.core.shared.models.agent_labels`），网页不创建新标签。理由：一个
集合外的标签（哪怕是拼错的 ``agent/redy``）会让 Issue 静默脱离监控口径，且事后
难以区分是网页还是终端改的。

写回之后一律以 GitHub 的 fresh read 结果作为响应，因此页面状态与仓库实际状态
不可能各说一套；校验失败的请求在写入之前就被拒绝，GitHub 侧零变化。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_labels import standard_label_names
from backend.core.shared.models.agent_runner import RepositoryRunContext

_logger = logging.getLogger(__name__)


class IssueLabelActionError(ValueError):
    """标签操作被拒绝（Issue 编号非法、参数为空或自相矛盾）。"""


class IssueLabelNotAllowedError(IssueLabelActionError):
    """请求的标签不在仓库已同步的标准标签集合内。"""


@dataclass(frozen=True)
class IssueLabelSnapshot:
    """一次标签读取的结果。

    Attributes:
        issue_number: 目标 Issue 编号。
        labels: Issue 当前在 GitHub 上的全部标签（含集合外的历史标签，只读展示）。
        allowed_labels: 本仓库允许通过网页增删的标签名（升序，稳定可断言）。
    """

    issue_number: int
    labels: tuple[str, ...]
    allowed_labels: tuple[str, ...]


def allowed_label_names(context: RepositoryRunContext) -> tuple[str, ...]:
    """返回该仓库「``kc labels sync`` 会同步的标签」生效名集合（升序）。

    Args:
        context: 已解析的仓库运行上下文（提供 ``config.labels`` 与 ``config.agents``）。

    Returns:
        允许写入的标签名元组；agent 路由标签按注册表派生，与同步路径同口径。
    """
    return tuple(sorted(standard_label_names(context.config.labels, context.config.agents)))


def _require_positive_issue(issue_number: int) -> None:
    if issue_number <= 0:
        raise IssueLabelActionError("issue_number 必须是正整数。")


def _normalized_names(label_names: Sequence[str], field_name: str) -> tuple[str, ...]:
    """去空白、去重并保持请求顺序；空白名视为非法输入。"""
    normalized: list[str] = []
    for raw_name in label_names:
        label_name = raw_name.strip()
        if not label_name:
            raise IssueLabelActionError(f"{field_name} 中包含空白标签名。")
        if label_name not in normalized:
            normalized.append(label_name)
    return tuple(normalized)


def read_issue_labels(
    *,
    issue_number: int,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
) -> IssueLabelSnapshot:
    """读取 Issue 的现有标签与允许写入的集合。

    Args:
        issue_number: 目标 Issue 编号。
        context: 仓库运行上下文（决定允许的标签集合）。
        github_client: 该仓库的 GitHub 客户端。

    Returns:
        :class:`IssueLabelSnapshot`。

    Raises:
        IssueLabelActionError: Issue 编号非法。
    """
    _require_positive_issue(issue_number)
    issue = github_client.get_issue(issue_number)
    return IssueLabelSnapshot(
        issue_number=issue_number,
        labels=tuple(issue.labels),
        allowed_labels=allowed_label_names(context),
    )


def update_issue_labels(
    *,
    issue_number: int,
    add: Sequence[str],
    remove: Sequence[str],
    context: RepositoryRunContext,
    github_client: IGitHubClient,
) -> IssueLabelSnapshot:
    """在已同步标签集内增删标签，写回后以 GitHub 的 fresh read 返回状态。

    Args:
        issue_number: 目标 Issue 编号。
        add: 需要添加的标签名序列。
        remove: 需要移除的标签名序列。
        context: 仓库运行上下文（决定允许的标签集合）。
        github_client: 该仓库的 GitHub 客户端。

    Returns:
        写入后的 :class:`IssueLabelSnapshot`。

    Raises:
        IssueLabelActionError: Issue 编号非法、参数含空白名或同一标签既加又删。
        IssueLabelNotAllowedError: 任一标签不在允许集合内（此时 GitHub 零变化）。
    """
    _require_positive_issue(issue_number)
    add_names = _normalized_names(add, "add")
    remove_names = _normalized_names(remove, "remove")

    conflicting = sorted(set(add_names) & set(remove_names))
    if conflicting:
        raise IssueLabelActionError(
            f"标签 {', '.join(conflicting)} 同时出现在 add 与 remove 中，请求自相矛盾。"
        )

    allowed = set(allowed_label_names(context))
    out_of_set = sorted((set(add_names) | set(remove_names)) - allowed)
    if out_of_set:
        raise IssueLabelNotAllowedError(
            f"标签不在本仓库已同步的标准标签集内：{', '.join(out_of_set)}。"
            "网页不创建新标签；确需新标签请先在终端执行 `kc labels sync`。"
        )

    if not add_names and not remove_names:
        return read_issue_labels(
            issue_number=issue_number, context=context, github_client=github_client
        )

    github_client.edit_issue_labels(issue_number, add=add_names, remove=remove_names)
    _logger.info("Issue #%s labels updated: +%s -%s", issue_number, add_names, remove_names)
    refreshed_issue = github_client.get_issue(issue_number)
    return IssueLabelSnapshot(
        issue_number=issue_number,
        labels=tuple(refreshed_issue.labels),
        allowed_labels=tuple(sorted(allowed)),
    )

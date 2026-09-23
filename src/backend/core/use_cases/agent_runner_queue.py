"""IAR ready Issue 的稳定队列选择规则。"""

from __future__ import annotations

from collections.abc import Sequence

from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.shared.priority import PRIORITY_ORDER, priority_rank


def issue_priority(issue: IssueSummary) -> str | None:
    """从 Issue 的规范 priority label 读取优先级。"""
    for priority_label in PRIORITY_ORDER:
        if f"priority/{priority_label}" in issue.labels:
            return priority_label
    return None


def sort_ready_issues(ready_issues: Sequence[IssueSummary]) -> list[IssueSummary]:
    """按 P0→P3、缺省优先级最后、Issue 编号升序返回候选。"""
    return sorted(
        ready_issues,
        key=lambda ready_issue: (
            priority_rank(issue_priority(ready_issue)),
            ready_issue.number,
        ),
    )

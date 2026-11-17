"""Issue 单次续跑执行器覆盖的可选本地存储能力。"""

from __future__ import annotations

from typing import cast

from backend.core.shared.interfaces.runner_console import IIssueRetryOverrideStore


def resolve_issue_retry_override_store(store: object | None) -> IIssueRetryOverrideStore | None:
    """从 Console SQLite 读写对象中识别单次覆盖能力。"""
    if store is None:
        return None
    required_methods = (
        "set_issue_retry_override",
        "get_issue_retry_override",
        "pop_issue_retry_override",
        "clear_issue_retry_override",
    )
    if all(callable(getattr(store, method_name, None)) for method_name in required_methods):
        return cast(IIssueRetryOverrideStore, store)
    return None

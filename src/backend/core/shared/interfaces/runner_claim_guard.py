"""当前 runner 操作的认领写入保护端口。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

_ACTIVE_RUNNER_CLAIM_GUARD: ContextVar[Callable[[], None] | None] = ContextVar(
    "active_runner_claim_guard",
    default=None,
)


def set_active_runner_claim_guard(guard: Callable[[], None]) -> None:
    """设置当前执行上下文在每次受控写入前调用的认领核验。"""
    _ACTIVE_RUNNER_CLAIM_GUARD.set(guard)


def clear_active_runner_claim_guard() -> None:
    """清除当前执行上下文的认领核验，避免复用线程泄漏到下一任务。"""
    _ACTIVE_RUNNER_CLAIM_GUARD.set(None)


def assert_active_runner_claim_owned() -> None:
    """若当前 runner 持有认领，则在外部写入前重新核验。"""
    guard = _ACTIVE_RUNNER_CLAIM_GUARD.get()
    if guard is not None:
        guard()

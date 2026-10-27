"""Agent daemon 的进程级停机状态。"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.shared.interfaces.runner_live_view import IRunnerLiveView

_logger = logging.getLogger(__name__)

_AGENT_DAEMON_STOPPING = threading.Event()


class AgentDaemonShutdownInterrupt(BaseException):
    """daemon 停机期间中断 Issue 处理，绕过失败恢复逻辑。"""


def mark_agent_daemon_stopping() -> None:
    """标记 daemon 已开始停机。"""
    _AGENT_DAEMON_STOPPING.set()


def reset_agent_daemon_stopping() -> None:
    """为新启动的 daemon 清除上次进程内的停机标记。"""
    _AGENT_DAEMON_STOPPING.clear()


def is_agent_daemon_stopping() -> bool:
    """返回 daemon 是否正在停机。"""
    return _AGENT_DAEMON_STOPPING.is_set()


def raise_if_agent_daemon_stopping() -> None:
    """停机期间中断当前 Issue，避免将主动终止记作执行失败。"""
    if is_agent_daemon_stopping():
        raise AgentDaemonShutdownInterrupt("Agent daemon is stopping.")


def _skip_issue_on_daemon_shutdown(
    issue_number: int,
    output_view: IRunnerLiveView,
) -> bool:
    """停机期间跳过 Issue 失败处置，并保留远端工作流状态。"""
    if not is_agent_daemon_stopping():
        return False
    _logger.info("Daemon is stopping; leaving Issue #%d in its current state.", issue_number)
    output_view.update_status(issue_number, "skipped")
    return True

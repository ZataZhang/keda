"""Agent daemon 的进程级停机状态。"""

from __future__ import annotations

import threading

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

"""跨平台进程探活 :func:`is_process_alive` 的真实进程测试。

不打桩 psutil：用真实启动、真实退出的子进程验证「死进程判死」。Windows 上
旧的 ``os.kill(pid, 0)`` 对不存在的进程抛 ``OSError(winerror=87)``，被调用方
当成存活，这正是本用例要守住的回归。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from backend.core.shared.models.platform_support import is_process_alive


def test_current_process_is_alive() -> None:
    """本进程一定存活。"""
    assert is_process_alive(os.getpid()) is True


@pytest.mark.parametrize("non_positive_pid", [0, -1])
def test_non_positive_pid_is_not_alive(non_positive_pid: int) -> None:
    """0 与负数不是可探测的进程号，一律判死（不能落到「本进程组 / 全部进程」语义）。"""
    assert is_process_alive(non_positive_pid) is False


def test_exited_child_process_is_not_alive() -> None:
    """真实子进程退出并被回收后，其进程号判为不存活。"""
    finished_child = subprocess.Popen([sys.executable, "-c", "pass"])
    finished_child.wait(timeout=30)

    assert is_process_alive(finished_child.pid) is False


def test_running_child_process_is_alive_until_it_exits() -> None:
    """运行中的子进程判活；结束后判死。"""
    running_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert is_process_alive(running_child.pid) is True
    finally:
        running_child.kill()
        running_child.wait(timeout=30)

    assert is_process_alive(running_child.pid) is False

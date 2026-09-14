"""Run a command under a wall-clock timeout and reap its whole process group.

Unlike ``timeout(1)`` this helper always verifies that the command's process
group is empty before returning, on both the timeout path and the normal-exit
path. That is the behaviour ``just test`` needs: its real failure mode is the
top-level process exiting while a lint or pytest subtree stays alive forever.

It is also why this is a Python script rather than ``timeout``: macOS ships
neither ``timeout`` nor ``gtimeout``, so a bare ``timeout`` call in a shared
recipe would silently work on CI and break on every developer laptop.

用法::

    python3 scripts/shared/just/with_timeout.py <秒数> <命令> [参数...]

环境变量：
    WITH_TIMEOUT_GRACE   SIGTERM 到 SIGKILL 的宽限秒数（默认 10）
    WITH_TIMEOUT_QUIET   设为 1 时不打印回收日志

退出码：透传被包装命令的退出码；超时则为 124（沿用 GNU timeout 约定）。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

TIMEOUT_EXIT_CODE = 124
USAGE_EXIT_CODE = 2
COMMAND_NOT_FOUND_EXIT_CODE = 127
DEFAULT_GRACE_SECONDS = 10.0
GROUP_POLL_INTERVAL_SECONDS = 0.1
# 转发给子进程组的信号：子进程在独立 session 里收不到终端信号，必须由本进程
# 代为转发，否则 Ctrl-C 和关窗口都打不到被包装的命令。
FORWARDED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def log(message: str) -> None:
    """Write a reaper log line to stderr unless silenced.

    Args:
        message: Message body appended after the ``with-timeout:`` prefix.
    """
    if os.environ.get("WITH_TIMEOUT_QUIET") == "1":
        return
    sys.stderr.write(f"with-timeout: {message}\n")
    sys.stderr.flush()


def is_signalable_group(process_group_id: int) -> bool:
    """Report whether a process group may be signalled as a whole.

    Process group 0 means "the caller's own group" to ``killpg`` and group 1 is
    init/launchd; neither can ever be a group created by our child, whose group
    id always equals its own pid. Signalling them is how a runner kills itself.

    Args:
        process_group_id: Candidate process group id.

    Returns:
        bool: True when the group is safe to signal.
    """
    if process_group_id in (0, 1):
        return False
    return process_group_id != os.getpgrp()


def group_alive(process_group_id: int) -> bool:
    """Report whether any member of a process group is still alive.

    Args:
        process_group_id: Process group to probe with signal 0.

    Returns:
        bool: True when at least one member survives.
    """
    try:
        os.killpg(process_group_id, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def reap_group(process_group_id: int, grace_seconds: float, reason: str) -> None:
    """Ensure a process group is empty, escalating SIGTERM to SIGKILL.

    Args:
        process_group_id: Process group to clear.
        grace_seconds: Seconds to wait after SIGTERM before sending SIGKILL.
        reason: Short Chinese phrase describing why the sweep runs; it is
            prefixed to the log line.
    """
    if not is_signalable_group(process_group_id):
        log(f"拒绝对进程组 {process_group_id} 发信号（0/1/自身组），跳过回收。")
        return
    if not group_alive(process_group_id):
        return

    log(f"{reason}，进程组 {process_group_id} 仍有存活成员，发送 SIGTERM。")
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not group_alive(process_group_id):
            log(f"进程组 {process_group_id} 已在宽限期内退出。")
            return
        time.sleep(GROUP_POLL_INTERVAL_SECONDS)

    log(f"宽限 {grace_seconds:g}s 后仍未退出，对进程组 {process_group_id} 发送 SIGKILL。")
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _install_signal_forwarding(process_group_id: int) -> dict[int, object]:
    """Forward terminal signals to the child's detached process group.

    Args:
        process_group_id: Process group that should receive forwarded signals.

    Returns:
        dict[int, object]: Previous handlers, keyed by signal number.
    """

    def forward(signal_number: int, _frame: object) -> None:
        if is_signalable_group(process_group_id):
            try:
                os.killpg(process_group_id, signal_number)
            except (ProcessLookupError, PermissionError):
                pass

    previous_handlers: dict[int, object] = {}
    for signal_number in FORWARDED_SIGNALS:
        previous_handlers[signal_number] = signal.signal(signal_number, forward)
    return previous_handlers


def run(timeout_seconds: float, command: list[str], grace_seconds: float) -> int:
    """Run a command with a timeout, then guarantee its process group is empty.

    Args:
        timeout_seconds: Wall-clock budget before the command is torn down.
        command: Argument vector to execute.
        grace_seconds: Seconds between SIGTERM and SIGKILL when reaping.

    Returns:
        int: The command's exit code, or 124 when the timeout fired.
    """
    try:
        # start_new_session 让子进程成为新 session 的组长（pgid == 其 pid），整棵
        # 子树因此落在一个可被精确回收的组里，不会误伤同终端的其它进程。
        child = subprocess.Popen(command, start_new_session=True)
    except FileNotFoundError:
        sys.stderr.write(f"with-timeout: 找不到命令: {command[0]}\n")
        return COMMAND_NOT_FOUND_EXIT_CODE

    child_group_id = child.pid
    previous_handlers = _install_signal_forwarding(child_group_id)

    timed_out = False
    try:
        exit_code = child.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        log(f"命令超过 {timeout_seconds:g}s 未结束: {' '.join(command)}")
        reap_group(child_group_id, grace_seconds, "超时")
        try:
            exit_code = child.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            exit_code = TIMEOUT_EXIT_CODE
    finally:
        for signal_number, handler in previous_handlers.items():
            signal.signal(signal_number, handler)

    # 正常退出也要复查：本工具要兜的正是"顶层退出、子树还在"这一种泄漏。
    reap_group(child_group_id, grace_seconds, "命令已退出")

    if timed_out:
        return TIMEOUT_EXIT_CODE
    if exit_code < 0:
        # 被信号打死：沿用 shell 的 128+signo 约定。
        return 128 - exit_code
    return exit_code


def main(argv: list[str]) -> int:
    """Parse arguments and delegate to :func:`run`.

    Args:
        argv: Full argument vector, including the script name.

    Returns:
        int: Process exit code.
    """
    if len(argv) < 3:
        sys.stderr.write(__doc__ or "")
        return USAGE_EXIT_CODE

    try:
        timeout_seconds = float(argv[1])
    except ValueError:
        sys.stderr.write(f"with-timeout: 超时秒数不是数字: {argv[1]}\n")
        return USAGE_EXIT_CODE

    grace_seconds = float(os.environ.get("WITH_TIMEOUT_GRACE", DEFAULT_GRACE_SECONDS))
    return run(timeout_seconds, argv[2:], grace_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

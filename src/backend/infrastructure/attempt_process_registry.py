"""活跃 attempt 的实时子进程登记簿（停滞监督的所有权证据来源）。

停滞监督要能"精确停掉自己那一个 writer"，前提是当场读得到那个进程的
``pid`` / 进程组，并且能确认它仍然是登记时的那个进程（pid 复用是最典型的
误杀场景）。``subprocess.run`` 拿不到句柄，因此只有走 ``Popen`` 的调用路径
（stream / PTY / captured 三条 agent 路径）会在册；不在册的 attempt 由监督器
按"归属不可证实"交班，绝不发信号。

登记动作由 :mod:`backend.infrastructure.process_runner` 在创建 ``Popen`` 处完成
（它持有进程句柄与既有的 ``_terminate_process_tree``），本模块只维护键→句柄的
映射与身份复核。**击杀手段一律由调用方以 ``terminator`` 回调注入**：本模块若
直接 import ``process_runner`` 会形成循环依赖，也让"用哪棵进程树去杀"这件事
散落到两个模块里。
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from backend.core.shared.models.agent_stall import AttemptOwnership, StallCancelOutcome
from backend.infrastructure.process_identity import process_start_time

#: 终止信号发出后轮询存活的间隔（秒）。
_CANCEL_POLL_INTERVAL_SECONDS = 0.05

#: 终止信号发出后等待回收的秒数上限（主线程仍在 ``communicate``，退出后自会收割）。
_CANCEL_REAP_WAIT_SECONDS = 10.0

_LOCK = threading.RLock()
_LIVE_ATTEMPTS: dict[str, "_LiveAttempt"] = {}


@dataclass
class _LiveAttempt:
    """一个在册的活跃子进程句柄。

    Attributes:
        process: 实际 ``Popen``（用于确认存活与终止后等待回收）。
        pid: 登记时的子进程 pid。
        process_group: 登记时读到的进程组 id（``os.getpgid``）。
        started_at_mono: 登记时刻（单调时钟）。
        label: 命令摘要（只含命令名，不含参数，避免把提示词写进日志）。
        terminator: 收到已复核的组与创建时刻后尝试终止该进程组的回调。
    """

    process: subprocess.Popen[str]
    pid: int
    process_group: int
    process_started_at: float
    started_at_mono: float
    label: str
    terminator: Callable[[int, float], bool]


@dataclass(frozen=True)
class LiveAttemptProcess:
    """对外暴露的在册进程视图（不含句柄与回调，不可用于直接操作进程）。

    Attributes:
        attempt_key: 登记键（一次 agent attempt 的身份）。
        pid: 登记时的子进程 pid。
        process_group: 登记时的进程组 id。
        alive: 读取时刻该进程是否仍存活。
        detail: 人读摘要（命令名 + 存活时长），不含提示词或参数。
    """

    attempt_key: str
    pid: int
    process_group: int
    alive: bool
    detail: str


def _process_group_of(process: subprocess.Popen[str]) -> int | None:
    """读取子进程当前进程组；进程已消失或系统不允许时返回 ``None``（不登记）。"""
    try:
        return os.getpgid(process.pid)
    except (OSError, AttributeError):
        return None


def register_live_attempt(
    *,
    attempt_key: str,
    process: subprocess.Popen[str],
    label: str,
    terminator: Callable[[int, float], bool],
) -> None:
    """登记一个活跃 attempt 的子进程；读不到组或创建时刻时不登记。

    不登记是安全的：监督器随后会把"不在册"读成归属不可证实并交班，而不是猜测。
    """
    process_group = _process_group_of(process)
    if process_group is None:
        return
    process_started_at = process_start_time(process.pid)
    if process_started_at is None:
        return
    with _LOCK:
        _LIVE_ATTEMPTS[attempt_key] = _LiveAttempt(
            process=process,
            pid=process.pid,
            process_group=process_group,
            process_started_at=process_started_at,
            started_at_mono=time.monotonic(),
            label=label,
            terminator=terminator,
        )


def unregister_live_attempt(*, attempt_key: str, pid: int) -> None:
    """注销登记：仅当在册条目就是 ``pid`` 指向的那个进程时才移除。"""
    with _LOCK:
        live_attempt = _LIVE_ATTEMPTS.get(attempt_key)
        if live_attempt is not None and live_attempt.pid == pid:
            del _LIVE_ATTEMPTS[attempt_key]


def lookup_live_attempt(attempt_key: str) -> LiveAttemptProcess | None:
    """返回在册进程的视图；不在册（含从未登记与已注销）时返回 ``None``。"""
    with _LOCK:
        live_attempt = _LIVE_ATTEMPTS.get(attempt_key)
        if live_attempt is None:
            return None
        return _as_view(attempt_key, live_attempt)


def _as_view(attempt_key: str, live_attempt: _LiveAttempt) -> LiveAttemptProcess:
    """把内部条目投影成只读视图（存活判定用 ``poll()``，不阻塞、不收割）。"""
    alive = live_attempt.process.poll() is None
    return LiveAttemptProcess(
        attempt_key=attempt_key,
        pid=live_attempt.pid,
        process_group=live_attempt.process_group,
        alive=alive,
        detail=(
            f"{live_attempt.label} (pid {live_attempt.pid}, group {live_attempt.process_group}) "
            f"alive for {round(time.monotonic() - live_attempt.started_at_mono)}s"
        ),
    )


def probe_attempt_ownership(*, attempt_key: str, host_label: str) -> AttemptOwnership:
    """把"这个 attempt 的 writer 是否还在、归属是否可证实"读成一份证据。

    复核口径：在册 + 仍存活 + 实时 ``getpgid`` 与登记值一致。任一条不成立都返回
    ``confirmed=False`` 并写明原因，监督器据此交班。

    Args:
        attempt_key: 一次 agent attempt 的身份键。
        host_label: 本机标识（进证据，供跨主机误处置的审计辨认）。

    Returns:
        :class:`AttemptOwnership`：不在册时 ``child_process_group`` 为 ``None``。
    """
    with _LOCK:
        live_attempt = _LIVE_ATTEMPTS.get(attempt_key)
        if live_attempt is None:
            return AttemptOwnership(
                confirmed=False,
                reason=f"no live process registered for attempt {attempt_key!r}",
                host_label=host_label,
            )
        if live_attempt.process.poll() is not None:
            return AttemptOwnership(
                confirmed=False,
                reason=(
                    f"attempt {attempt_key!r} process already exited (rc "
                    f"{live_attempt.process.returncode})"
                ),
                host_label=host_label,
                process_pid=live_attempt.pid,
            )
        current_group = _process_group_of(live_attempt.process)
        if current_group != live_attempt.process_group:
            return AttemptOwnership(
                confirmed=False,
                reason=(
                    f"attempt {attempt_key!r} process group changed since registration "
                    f"({live_attempt.process_group} -> {current_group})"
                ),
                host_label=host_label,
                process_pid=live_attempt.pid,
            )
        current_start_time = process_start_time(live_attempt.pid)
        if current_start_time != live_attempt.process_started_at:
            return AttemptOwnership(
                confirmed=False,
                reason=(
                    f"attempt {attempt_key!r} process creation identity changed since registration"
                ),
                host_label=host_label,
                process_pid=live_attempt.pid,
                child_process_group=current_group,
                process_started_at=current_start_time,
            )
        return AttemptOwnership(
            confirmed=True,
            host_label=host_label,
            process_pid=live_attempt.pid,
            child_process_group=current_group,
            process_started_at=current_start_time,
        )


def terminate_attempt(
    *,
    attempt_key: str,
    expected: AttemptOwnership,
    wait_seconds: float = _CANCEL_REAP_WAIT_SECONDS,
) -> StallCancelOutcome:
    """按登记身份精确终止一个活跃 attempt 的进程组。

    任何身份不符（键不在册、pid 变了、进程组变了）都拒绝发信号；这与
    ``_terminate_process_tree`` 自身的"0/1 号组与调用者所在组一律不碰"叠加，
    构成本特性的两道击杀护栏。

    Args:
        attempt_key: 待终止的 attempt 键。
        expected: 监督器在诊断后**重新读取**的那份所有权证据（必须与在册条目一致）。
        wait_seconds: 发出终止后等待回收的秒数上限。

    Returns:
        :class:`StallCancelOutcome`：``cancelled`` 为真仅当信号由本次调用发出。
    """
    with _LOCK:
        live_attempt = _LIVE_ATTEMPTS.get(attempt_key)
        if live_attempt is None:
            return StallCancelOutcome(reason=f"attempt {attempt_key!r} is no longer registered")
        if not expected.confirmed:
            return StallCancelOutcome(
                reason="ownership evidence is not confirmed; refusing to signal"
            )
        if expected.host_label != socket.gethostname():
            return StallCancelOutcome(
                reason=(
                    f"ownership evidence belongs to host {expected.host_label!r}, not this host; "
                    "refusing to signal"
                )
            )
        if (
            live_attempt.pid != expected.process_pid
            or live_attempt.process_group != expected.child_process_group
            or live_attempt.process_started_at != expected.process_started_at
        ):
            return StallCancelOutcome(
                reason=(
                    f"attempt {attempt_key!r} identity moved (registered pid/group "
                    f"{live_attempt.pid}/{live_attempt.process_group}, verdict saw "
                    f"{expected.process_pid}/{expected.child_process_group}); refusing to signal"
                ),
            )
        if live_attempt.process.poll() is not None:
            return StallCancelOutcome(
                reason=f"attempt {attempt_key!r} process exited before cancellation"
            )
        current_group = _process_group_of(live_attempt.process)
        current_start_time = process_start_time(live_attempt.pid)
        if (
            current_group != live_attempt.process_group
            or current_start_time != live_attempt.process_started_at
        ):
            return StallCancelOutcome(
                reason=(
                    f"attempt {attempt_key!r} live process identity changed before cancellation "
                    f"(group {current_group}, started_at {current_start_time}); refusing to signal"
                )
            )
    # 锁外执行终止与等待：等待最长阻塞 wait_seconds，绝不能把锁一起带走，
    # 否则主线程的注销/查询会被同一次击杀堵住。用 ``poll()`` 轮询而不是
    # ``wait(timeout=)``：主线程此刻正停在 ``communicate()`` 里收割同一个句柄，
    # 谁先读到退出都会把 returncode 缓存进句柄，两边不会重复 waitpid。
    if not live_attempt.terminator(
        live_attempt.process_group,
        live_attempt.process_started_at,
    ):
        return StallCancelOutcome(
            reason=(
                f"attempt {attempt_key!r} live process identity changed at signal time; "
                "refusing to signal"
            )
        )
    exit_deadline_mono = time.monotonic() + wait_seconds
    while live_attempt.process.poll() is None and time.monotonic() < exit_deadline_mono:
        time.sleep(_CANCEL_POLL_INTERVAL_SECONDS)
    if live_attempt.process.poll() is None:
        return StallCancelOutcome(
            cancelled=True,
            exited=False,
            process_group=live_attempt.process_group,
            reason=(
                f"termination signalled but process group {live_attempt.process_group} "
                f"did not exit within {int(wait_seconds)}s"
            ),
        )
    unregister_live_attempt(attempt_key=attempt_key, pid=live_attempt.pid)
    return StallCancelOutcome(
        cancelled=True,
        exited=True,
        process_group=live_attempt.process_group,
        reason="",
    )


__all__ = [
    "LiveAttemptProcess",
    "lookup_live_attempt",
    "probe_attempt_ownership",
    "register_live_attempt",
    "terminate_attempt",
    "unregister_live_attempt",
]

"""``kc run --takeover`` 的接管编排（engines 层实现）。

接管是显式破坏性动作：优雅停掉同仓 daemon → 终止其 agent 子进程树 →
reclaim 在途 Issue → 随后由调用方执行定向 run。实现放在 engines 层，
因为它同时要触碰 core 用例（reclaim）与 infrastructure 监管器（pidfile
registry 的托管停止）；api 层经 importlib 薄转发调用（四层依赖方向
允许 engines 依赖 core 与 infrastructure，api 只依赖 core）。

停止语义与 :class:`PidfileProcessSupervisor` 一致：SIGTERM → 等待
超时 → 仍存活才 SIGKILL；daemon 侧的 SIGTERM 钩子负责先整组终止在途
agent 子进程树。托管路径之外（未托管的手动 ``kc daemon``）按锁文件
记录的 PID 直接停，停后补扫 daemon 的后代进程组，确保无孤儿 agent。
"""

from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_reclaim import (
    reclaim_stale_running_issues,
)

_logger = logging.getLogger(__name__)

#: 托管/未托管路径共用的默认停止超时秒数（SIGTERM → 等待 → SIGKILL）。
DEFAULT_STOP_TIMEOUT_SECONDS = 30.0

#: 停 daemon 后补扫残留 agent 进程组的等待秒数。
_ORPHAN_TERM_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class DaemonTakeoverResult:
    """一次接管的结果（停止方式、是否有孤儿清理、reclaim 的 Issue）。"""

    repo_id: str
    daemon_pid: int
    #: daemon 是否来自托管注册表（pidfile registry）。
    managed: bool
    #: daemon 最终收到的信号路径："sigterm"（优雅退出）或 "sigkill"（超时升级）。
    final_signal: str
    #: 接管后被 reclaim 回 ``agent/ready`` 的在途 Issue 编号。
    reclaimed_issues: tuple[int, ...]


def _import_psutil() -> Any:
    """按需导入 psutil；缺失时返回 ``None``（孤儿补扫降级为跳过）。"""
    try:
        import psutil
    except Exception:  # noqa: BLE001 - psutil 是可选依赖。
        return None
    return psutil


def collect_descendant_group_ids(pid: int) -> set[int]:
    """收集 ``pid`` 全部后代进程的进程组 ID（排除 init 组与调用方自己的组）。

    在停 daemon **之前**快照：daemon 退出后无法再从它枚举后代。agent 子进程
    位于独立进程组（``process_group=0``），停 daemon 后按组补扫即可保证
    无孤儿 agent。
    """
    psutil = _import_psutil()
    if psutil is None:
        return set()
    try:
        own_group_id = os.getpgid(0)
        descendants = psutil.Process(pid).children(recursive=True)
    except Exception:  # noqa: BLE001 - daemon 可能刚好退出，快照为空即可。
        return set()
    group_ids: set[int] = set()
    for descendant in descendants:
        try:
            descendant_group_id = os.getpgid(descendant.pid)
        except OSError:
            continue
        if descendant_group_id > 1 and descendant_group_id != own_group_id:
            group_ids.add(descendant_group_id)
    return group_ids


def _pid_alive(pid: int) -> bool:
    """探活 ``pid``（信号 0；无法判定时保守视为存活）。

    psutil 可用时额外把 zombie 态判为已退出：SIGTERM 后 daemon 已死但父进程
    尚未 ``wait()`` 回收时，``os.kill(pid, 0)`` 仍会命中僵尸，会把"已优雅退出"
    误判成"需升级 SIGKILL"。
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    try:
        import psutil
    except Exception:  # noqa: BLE001 - psutil 是可选依赖。
        return True
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return True
    except Exception:  # noqa: BLE001 - 探测失败时保守视为存活。
        return True


def _sweep_orphan_groups(group_ids: set[int]) -> None:
    """对快照的进程组做 SIGTERM → 等待 → SIGKILL 的收尾清扫。"""
    remaining = {group_id for group_id in group_ids if group_id > 1}
    if not remaining:
        return
    for group_id in sorted(remaining):
        try:
            os.killpg(group_id, signal.SIGTERM)
        except OSError:
            remaining.discard(group_id)
    deadline = time.monotonic() + _ORPHAN_TERM_GRACE_SECONDS
    while remaining and time.monotonic() < deadline:
        for group_id in sorted(remaining):
            try:
                os.killpg(group_id, 0)
            except OSError:
                remaining.discard(group_id)
        if remaining:
            time.sleep(0.1)
    for group_id in remaining:
        try:
            os.killpg(group_id, signal.SIGKILL)
        except OSError:
            pass


def _stop_managed_daemon(
    *,
    supervisor: Any,
    repo_id: str,
    daemon_pid: int,
    stop_timeout_seconds: float,
) -> str | None:
    """尝试走托管注册表停 daemon。

    Returns:
        停止结果信号路径（``"sigterm"`` / ``"sigkill"``），daemon 不在托管
        注册表中（或已不在运行）时返回 ``None``。
    """
    for record in supervisor.list_processes():
        if (
            record.repo_id == repo_id
            and str(record.kind) == "daemon"
            and record.status == "running"
            and int(record.pid) == daemon_pid
        ):
            stopped = supervisor.stop(record.process_id, timeout_seconds=int(stop_timeout_seconds))
            return "sigkill" if stopped.status == "killed" else "sigterm"
    return None


def _stop_unmanaged_daemon(*, daemon_pid: int, stop_timeout_seconds: float) -> str:
    """按 PID 直接停未托管 daemon：SIGTERM → 等待 → SIGKILL。"""
    try:
        os.kill(daemon_pid, signal.SIGTERM)
    except ProcessLookupError:
        return "sigterm"
    except OSError as exc:
        _logger.warning("SIGTERM to daemon pid %d failed: %s", daemon_pid, exc)
    deadline = time.monotonic() + max(stop_timeout_seconds, 1)
    while time.monotonic() < deadline:
        if not _pid_alive(daemon_pid):
            return "sigterm"
        time.sleep(0.2)
    try:
        os.kill(daemon_pid, signal.SIGKILL)
    except OSError:
        pass
    return "sigkill"


def take_over_daemon(
    *,
    repo_id: str,
    daemon_pid: int,
    config: AppConfig,
    github_client: IGitHubClient,
    process_registry_path: str | Path,
    process_log_dir: str | Path,
    stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS,
) -> DaemonTakeoverResult:
    """优雅停同仓 daemon、清理其 agent 子进程树并 reclaim 在途 Issue。

    Args:
        repo_id: 目标仓库 ID。
        daemon_pid: 锁文件记录的 daemon PID。
        config: 目标仓库的应用配置（reclaim 需要 workflow 标签）。
        github_client: 目标仓库的 GitHub 客户端。
        process_registry_path: 托管进程注册表路径（pidfile registry）。
        process_log_dir: 托管进程日志目录。
        stop_timeout_seconds: SIGTERM 后等待退出的秒数，超时升级 SIGKILL。

    Returns:
        :class:`DaemonTakeoverResult`。
    """
    # 快照 daemon 的后代进程组：agent 子进程整组回收依赖这份停前快照。
    descendant_groups = collect_descendant_group_ids(daemon_pid)

    from backend.infrastructure.console.process_supervisor import PidfileProcessSupervisor

    supervisor = PidfileProcessSupervisor(
        registry_path=process_registry_path, log_dir=process_log_dir
    )
    managed_stop_signal = _stop_managed_daemon(
        supervisor=supervisor,
        repo_id=repo_id,
        daemon_pid=daemon_pid,
        stop_timeout_seconds=stop_timeout_seconds,
    )
    if managed_stop_signal is not None:
        final_signal = managed_stop_signal
        _logger.info(
            "Stopped managed daemon for '%s' (pid %d) via process registry.",
            repo_id,
            daemon_pid,
        )
    else:
        final_signal = _stop_unmanaged_daemon(
            daemon_pid=daemon_pid, stop_timeout_seconds=stop_timeout_seconds
        )

    # SIGKILL 兜底会留下 daemon 来不及清理的 agent 子进程树，按停前快照补扫。
    _sweep_orphan_groups(descendant_groups)

    reclaimed = reclaim_stale_running_issues(config=config, github_client=github_client)
    if reclaimed:
        _logger.info(
            "Takeover reclaimed %d in-flight Issue(s) for '%s': %s",
            len(reclaimed),
            repo_id,
            reclaimed,
        )
    return DaemonTakeoverResult(
        repo_id=repo_id,
        daemon_pid=daemon_pid,
        managed=managed_stop_signal is not None,
        final_signal=final_signal,
        reclaimed_issues=tuple(reclaimed),
    )


__all__ = [
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "DaemonTakeoverResult",
    "collect_descendant_group_ids",
    "take_over_daemon",
]

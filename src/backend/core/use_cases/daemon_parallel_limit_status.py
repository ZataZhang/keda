"""daemon 实际生效的并行上限：daemon 每次轮询写出，管理终端与 ``kc daemon status`` 读取。

并行上限有三个入口（``kc daemon run --concurrency`` > Backlog 页面设置 > 配置文件
``max_concurrent_issues``）。其中命令行参数只存在于 daemon 进程里，管理终端无从
知道；不把它报告出来，页面就会显示页面设置的 4，而 daemon 实际按命令行的 5 在跑。

daemon 每次轮询把它对每个仓库解析出的值写到单实例锁旁边的状态文件
（``<daemon-locks>/<repo_id>.parallel-limit.json``）。读取方只在文件里的 PID 正是
该仓库单实例锁的存活持有者时才采信，daemon 被 ``kill -9`` 留下的旧文件因此不会
被当成实时值。文件只在本机，不跨机器。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.core.use_cases.daemon_single_instance import daemon_lock_path, find_live_daemon_pid

_logger = logging.getLogger(__name__)

_STATUS_FILE_SUFFIX = ".parallel-limit.json"


@dataclass(frozen=True)
class DaemonParallelLimitStatus:
    """某个仓库的 daemon 在最近一次轮询里实际使用的并行上限。

    Attributes:
        repo_id: 仓库标识。
        pid: 写出该状态的 daemon 进程 PID。
        parallel_limit: 实际生效的并行上限。
        source: 取值来源：``cli_override`` / ``policy`` / ``config``。
        in_flight: 该轮询时本机正在执行的 Issue 数。
        updated_at: 写出时间（UTC ISO 8601，带偏移）。
    """

    repo_id: str
    pid: int
    parallel_limit: int
    source: str
    in_flight: int
    updated_at: str


def daemon_parallel_limit_status_path(lock_dir: Path, repo_id: str) -> Path:
    """返回某仓库状态文件的路径（与该仓库的单实例锁同目录、同名前缀）。"""
    lock_path = daemon_lock_path(lock_dir, repo_id)
    return lock_path.with_name(lock_path.stem + _STATUS_FILE_SUFFIX)


def write_daemon_parallel_limit_status(lock_dir: Path, status: DaemonParallelLimitStatus) -> None:
    """原子写出状态文件；写失败只告警，绝不影响 daemon 的调度。"""
    status_path = daemon_parallel_limit_status_path(lock_dir, status.repo_id)
    temporary_path = status_path.with_name(f"{status_path.name}.{os.getpid()}.tmp")
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(asdict(status)), encoding="utf-8")
        temporary_path.replace(status_path)
    except OSError as exc:
        _logger.warning(
            "Could not record the parallel limit for repository '%s' (%s); the console "
            "will fall back to the page setting / config value.",
            status.repo_id,
            exc,
        )
        temporary_path.unlink(missing_ok=True)


def read_daemon_parallel_limit_status(
    lock_dir: Path, repo_id: str
) -> DaemonParallelLimitStatus | None:
    """读取某仓库存活 daemon 的实时并行上限。

    Returns:
        状态文件存在、格式正确、且写出它的 PID 正是该仓库单实例锁的存活持有者时
        返回状态；否则（没有 daemon、daemon 已退出或被强杀、文件损坏）返回 ``None``。
    """
    status_path = daemon_parallel_limit_status_path(lock_dir, repo_id)
    try:
        raw_status = json.loads(status_path.read_text(encoding="utf-8"))
        status = DaemonParallelLimitStatus(**raw_status)
    except (OSError, ValueError, TypeError):
        return None
    if status.repo_id != repo_id or find_live_daemon_pid(lock_dir, repo_id) != status.pid:
        return None
    return status


def clear_daemon_parallel_limit_status(lock_dir: Path, repo_ids: list[str]) -> None:
    """daemon 退出时删除自己写出的状态文件（只删 PID 是本进程的那些）。"""
    current_pid = os.getpid()
    for repo_id in repo_ids:
        status_path = daemon_parallel_limit_status_path(lock_dir, repo_id)
        try:
            written_pid = json.loads(status_path.read_text(encoding="utf-8")).get("pid")
        except (OSError, ValueError, AttributeError):
            continue
        if written_pid == current_pid:
            status_path.unlink(missing_ok=True)


__all__ = [
    "DaemonParallelLimitStatus",
    "clear_daemon_parallel_limit_status",
    "daemon_parallel_limit_status_path",
    "read_daemon_parallel_limit_status",
    "write_daemon_parallel_limit_status",
]

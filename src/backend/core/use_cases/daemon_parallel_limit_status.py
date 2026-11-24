"""daemon 实际生效的并行上限：daemon 每次轮询写出，管理终端与 ``kc daemon status`` 读取。

并行上限有三个入口（``kc daemon run --concurrency`` > Backlog 页面设置 > 配置文件
``max_concurrent_issues``）。其中命令行参数只存在于 daemon 进程里，管理终端无从
知道；不把它报告出来，页面就会显示页面设置的 4，而 daemon 实际按命令行的 5 在跑。

daemon 每次轮询把它对每个仓库解析出的值写到单实例锁旁边的状态文件
（``<daemon-locks>/<repo_id>.parallel-limit.json``）。读取方只在文件里的 PID 正是
该仓库单实例锁的存活持有者时才采信，daemon 被 ``kill -9`` 留下的旧文件因此不会
被当成实时值。文件只在本机，不跨机器。

同一文件还带上 daemon **启动时**加载的 KedaCode 版本（``loaded_version``）。
daemon 一直按启动那一刻的代码运行，升级 kc 后不重启就会继续跑旧代码；
``kc daemon status`` 据此与当前已安装版本对比并提示重启。版本由 daemon 启动时
解析一次后原样写出，不能在轮询中重新读磁盘——那样升级后会谎报成新版本。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Literal

from backend.core.use_cases.daemon_single_instance import daemon_lock_path, find_live_daemon_pid

_logger = logging.getLogger(__name__)

_STATUS_FILE_SUFFIX = ".parallel-limit.json"

#: 已安装版本取不到发行版元数据时的回退值（与 ``kc --version`` 的回退一致）。
UNKNOWN_INSTALLED_VERSION = "0.0.0+unknown"

DaemonVersionStatus = Literal["current", "mismatch", "unknown"]


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
        loaded_version: daemon 启动时加载的 KedaCode 版本，运行期间不变；
            本字段上线前启动的旧 daemon 不写，读出为 ``None``。
    """

    repo_id: str
    pid: int
    parallel_limit: int
    source: str
    in_flight: int
    updated_at: str
    loaded_version: str | None = None


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
    known_field_names = {status_field.name for status_field in fields(DaemonParallelLimitStatus)}
    try:
        raw_status = json.loads(status_path.read_text(encoding="utf-8"))
        # 只取已知字段：更新版本的 daemon 多写的键不应让本读取方整份丢弃。
        status = DaemonParallelLimitStatus(
            **{key: value for key, value in raw_status.items() if key in known_field_names}
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if status.repo_id != repo_id or find_live_daemon_pid(lock_dir, repo_id) != status.pid:
        return None
    return status


def parallel_limit_status_fields(
    limit_status: DaemonParallelLimitStatus | None,
) -> dict[str, object]:
    """机器可读输出（如 ``kc daemon status --json``）的并行上限字段；读不到时全部为 null。"""
    return {
        "parallel_limit": limit_status.parallel_limit if limit_status else None,
        "parallel_limit_source": limit_status.source if limit_status else None,
        "in_flight": limit_status.in_flight if limit_status else None,
    }


def describe_parallel_limit_status(limit_status: DaemonParallelLimitStatus | None) -> str:
    """人读表格里的一格，例如 ``5 (cli_override), in flight 1``；读不到时为 ``-``。"""
    if limit_status is None:
        return "-"
    return (
        f"{limit_status.parallel_limit} ({limit_status.source}), "
        f"in flight {limit_status.in_flight}"
    )


@dataclass(frozen=True)
class DaemonVersionCheck:
    """daemon 运行版本与已安装版本的比对结果。

    Attributes:
        loaded_version: daemon 启动时加载的版本；未上报时为 ``None``。
        installed_version: 执行查询的 kc 解析到的已安装版本。
        status: ``current`` 一致；``mismatch`` 不一致；``unknown`` 无法判断。
        restart_recommended: 是否应提示用户重启 daemon。版本不一致，或 daemon
            未上报版本（可能是升级前启动的旧进程）时为真；已安装版本本身不可知时
            不提示，避免误报。
    """

    loaded_version: str | None
    installed_version: str
    status: DaemonVersionStatus
    restart_recommended: bool


def check_daemon_version(
    limit_status: DaemonParallelLimitStatus | None, installed_version: str
) -> DaemonVersionCheck:
    """比对 daemon 上报的启动版本与当前已安装版本。

    比较规则是字符串不相等即 ``mismatch``，不区分升级与降级：两种情况都说明
    daemon 跑的不是已安装的那一版。

    Args:
        limit_status: 读到的 daemon 状态；读不到（旧进程、未完成首轮轮询）时为 ``None``。
        installed_version: 当前已安装版本。

    Returns:
        比对结果。
    """
    loaded_version = limit_status.loaded_version if limit_status else None
    if loaded_version is None:
        return DaemonVersionCheck(None, installed_version, "unknown", restart_recommended=True)
    if installed_version == UNKNOWN_INSTALLED_VERSION:
        return DaemonVersionCheck(
            loaded_version, installed_version, "unknown", restart_recommended=False
        )
    if loaded_version == installed_version:
        return DaemonVersionCheck(
            loaded_version, installed_version, "current", restart_recommended=False
        )
    return DaemonVersionCheck(
        loaded_version, installed_version, "mismatch", restart_recommended=True
    )


def daemon_version_fields(
    version_check: DaemonVersionCheck | None, installed_version: str
) -> dict[str, object]:
    """机器可读输出的版本字段；非 daemon 行（无比对结果）的运行版本与状态为 null。"""
    return {
        "loaded_version": version_check.loaded_version if version_check else None,
        "installed_version": installed_version,
        "version_status": version_check.status if version_check else None,
    }


def describe_daemon_version(version_check: DaemonVersionCheck | None) -> str:
    """人读表格里的一格，例如 ``0.9.0 (installed 0.9.1)``；非 daemon 行为 ``-``。"""
    if version_check is None:
        return "-"
    if version_check.loaded_version is None:
        return "unknown"
    if version_check.status == "mismatch":
        return f"{version_check.loaded_version} (installed {version_check.installed_version})"
    return version_check.loaded_version


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
    "UNKNOWN_INSTALLED_VERSION",
    "DaemonParallelLimitStatus",
    "DaemonVersionCheck",
    "DaemonVersionStatus",
    "check_daemon_version",
    "clear_daemon_parallel_limit_status",
    "daemon_parallel_limit_status_path",
    "daemon_version_fields",
    "describe_daemon_version",
    "describe_parallel_limit_status",
    "parallel_limit_status_fields",
    "read_daemon_parallel_limit_status",
    "write_daemon_parallel_limit_status",
]

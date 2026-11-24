"""``kc registry`` / ``kc daemon status`` 的进程人读展示辅助。

把进程类型显示名、可执行文件路径解析与 daemon 版本重启提示集中在这里，
``cli_registry`` 只负责命令编排。
"""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.interfaces.runner_console import (
    RunnerProcessKind,
    RunnerProcessRecord,
)
from backend.core.shared.models import product_identity
from backend.core.use_cases.daemon_parallel_limit_status import DaemonVersionCheck

# ``ProcessSupervisor`` 把 ``kind`` 存成枚举成员的字符串值，这里按字符串值映射显示名。
_PROCESS_KIND_DISPLAY_NAMES = {
    RunnerProcessKind.DAEMON.value: "daemon",
    RunnerProcessKind.REVIEW_DAEMON.value: "supervise-daemon",
}


def display_process_kind(process_kind: RunnerProcessKind | str) -> str:
    """把稳定的内部进程类型转换为面向用户的显示名称。

    Args:
        process_kind: 进程类型枚举或其稳定字符串值。

    Returns:
        面向用户的进程名称；未知类型原样返回。
    """
    kind_name = process_kind if isinstance(process_kind, str) else process_kind.value
    return _PROCESS_KIND_DISPLAY_NAMES.get(kind_name, kind_name)


def resolve_executable_from_command(command: tuple[str, ...]) -> str:
    """Return the executable script path from a process command line.

    For direct invocations such as ``/path/bin/kc daemon ...`` the first
    argument is returned. For Python wrapper invocations such as
    ``/path/python /path/bin/kc daemon ...`` the KedaCode script path is returned.
    """
    if not command:
        return ""
    first = command[0]
    if len(command) >= 2 and ("python" in Path(first).name.lower()):
        second = command[1]
        if product_identity.is_own_command_name(second):
            return second
    return first


def daemon_restart_hint(
    record: RunnerProcessRecord, version_check: DaemonVersionCheck, *, is_managed: bool
) -> str:
    """生成某个 daemon 的重启提示：说明版本问题，并按托管 / 手动给出对应命令。"""
    if version_check.loaded_version is None:
        reason = (
            f"无法确认 daemon ({record.repo_id}, pid {record.pid}) 运行的版本，"
            "可能是升级前启动的旧进程，建议重启："
        )
    else:
        reason = (
            f"daemon ({record.repo_id}, pid {record.pid}) 运行的版本 "
            f"{version_check.loaded_version} 与已安装版本 "
            f"{version_check.installed_version} 不一致，请重启："
        )
    if is_managed:
        command = (
            f"kc registry stop --repo-id {record.repo_id} && "
            f"kc registry start --repo-id {record.repo_id}"
        )
    else:
        command = "结束该进程后重新运行 kc daemon"
    return f"[yellow]提示：{reason}[/]\n  {command}"


__all__ = [
    "daemon_restart_hint",
    "display_process_kind",
    "resolve_executable_from_command",
]

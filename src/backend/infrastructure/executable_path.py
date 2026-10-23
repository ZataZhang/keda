"""进程可执行目录补齐（GUI 启动导致的 PATH 缺失）。

macOS 上由菜单栏 App、Dock、launchd 等 GUI 上下文启动的进程，往往只继承
`launchd` 的最小 PATH（如 `/usr/bin:/bin:/usr/sbin:/sbin`），拿不到
Homebrew（`/opt/homebrew/bin`）与用户级工具目录。后果是 console 的监控
扫描找不到 `gh`，子进程调用返回 `FileNotFoundError`，看板只能把任务
显示成「状态未知」。

本模块在进程启动时把平台常见的可执行目录追加进 `PATH`：只追加真实存在
且尚未出现的目录，保留调用方原有顺序，重复调用无副作用。
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

from backend.infrastructure.logging.logger import logger


def _candidate_directories() -> tuple[Path, ...]:
    """返回当前平台需要补齐的可执行目录（按优先级排列）。

    Returns:
        候选目录元组；非 POSIX 平台返回空元组，不猜测 Windows 的 PATH 语义。
    """
    if os.name != "posix":
        return ()
    candidates = [Path.home() / ".local/bin"]
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/opt/homebrew/bin"),
                Path("/opt/homebrew/sbin"),
                Path("/usr/local/bin"),
                Path("/usr/local/sbin"),
                Path("/opt/local/bin"),
            ]
        )
    else:
        candidates.extend(
            [
                Path("/usr/local/bin"),
                Path("/usr/local/sbin"),
                Path("/snap/bin"),
            ]
        )
    return tuple(candidates)


def _normalized(path_entry: str) -> str:
    """返回用于去重比较的 PATH 条目规范形式。"""
    return os.path.normcase(os.path.normpath(path_entry))


def ensure_standard_executable_path(
    environ: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """把平台常见的可执行目录追加到 `PATH`，修复 GUI 启动环境缺失工具。

    只追加真实存在、且尚未出现在 `PATH` 中的目录；原有条目与顺序保持不变，
    因此重复调用不会产生重复项。调用方通常是 CLI 入口与 uvicorn 进程启动点。

    Args:
        environ: 待修改的环境映射；默认使用 :data:`os.environ`，测试可注入副本。

    Returns:
        本次实际追加的目录元组；无可追加目录时为空元组。
    """
    target_environ = os.environ if environ is None else environ
    current_entries = [entry for entry in target_environ.get("PATH", "").split(os.pathsep) if entry]
    seen = {_normalized(entry) for entry in current_entries}
    appended: list[str] = []
    for candidate in _candidate_directories():
        if not candidate.is_dir():
            continue
        candidate_text = str(candidate)
        normalized = _normalized(candidate_text)
        if normalized in seen:
            continue
        seen.add(normalized)
        appended.append(candidate_text)
    if appended:
        target_environ["PATH"] = os.pathsep.join([*current_entries, *appended])
        logger.info("executable PATH augmented with: %s", ", ".join(appended))
    return tuple(appended)

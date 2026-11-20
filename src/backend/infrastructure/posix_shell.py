"""按平台选择执行 POSIX shell 命令的 bash。

验证命令、pre-commit、证据复跑、浏览器 E2E 与 loop 前置命令都以
``bash -lc <命令>`` / ``/bin/sh -c <命令>`` 执行，命令字符串本身是 POSIX
shell 写法（``KEY=value cmd``、``&&``、管道）。

- macOS / Linux：argv 原样返回，行为与之前完全一致。
- Windows：PATH 上的 ``bash`` 往往是 WSL 占位程序（``System32\\bash.exe`` /
  ``WindowsApps\\bash.exe``），未装发行版时直接报错；是否能跑取决于 kc 从哪个
  终端启动。这里改为从 git 安装目录定位 Git for Windows 自带的
  ``<Git 根目录>\\bin\\bash.exe``——它会自行设置 PATH，登录与非登录模式都能找到
  ``sh`` 等工具（同目录下的 ``usr\\bin\\bash.exe`` 非登录模式找不到 ``sh``，不采用）。
  git 是 kc 的必需依赖，因此 Windows 上通常都能找到；找不到时抛出带安装指引的错误。
"""

from __future__ import annotations

import functools
import os
import shutil
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath

from backend.infrastructure.logging.logger import logger

#: 视为「请求 POSIX shell」的 argv[0]；Windows 上统一换成 Git Bash。
_POSIX_SHELL_COMMAND_NAMES = frozenset({"bash", "sh", "/bin/bash", "/bin/sh"})

#: 从 git 可执行文件向上查找 Git 根目录的最大层数
#: （``<root>\cmd\git.exe`` 需 1 层，``<root>\mingw64\bin\git.exe`` 需 2 层）。
_GIT_ROOT_SEARCH_DEPTH = 3


class PosixShellNotFoundError(FileNotFoundError):
    """Windows 上找不到可用的 Git Bash。

    继承 :class:`FileNotFoundError`，与「命令不存在」的既有处理路径保持一致。
    """


def _git_bash_under(git_root: Path) -> Path | None:
    """``git_root`` 是 Git for Windows 根目录时返回其 ``bin\\bash.exe``。"""
    git_bash_path = git_root / "bin" / "bash.exe"
    if git_bash_path.is_file() and (git_root / "usr" / "bin").is_dir():
        return git_bash_path
    return None


def _is_wsl_launcher(bash_path: str) -> bool:
    """PATH 上的 bash 是否为 WSL 占位程序（System32 / WindowsApps 下的 bash.exe）。"""
    path_parts = {path_part.casefold() for path_part in PureWindowsPath(bash_path).parts}
    return "system32" in path_parts or "windowsapps" in path_parts


@functools.lru_cache(maxsize=1)
def _find_windows_git_bash() -> str | None:
    """依次从 PATH 上的 git、常见安装目录、非 WSL 的 PATH bash 中定位 Git Bash。"""
    git_executable = shutil.which("git")
    if git_executable is not None:
        git_executable_path = Path(git_executable).resolve()
        for git_root in list(git_executable_path.parents)[:_GIT_ROOT_SEARCH_DEPTH]:
            git_bash_path = _git_bash_under(git_root)
            if git_bash_path is not None:
                return str(git_bash_path)
    for install_root_variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        install_root = os.environ.get(install_root_variable)
        if install_root:
            git_bash_path = _git_bash_under(Path(install_root) / "Git")
            if git_bash_path is not None:
                return str(git_bash_path)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        git_bash_path = _git_bash_under(Path(local_app_data) / "Programs" / "Git")
        if git_bash_path is not None:
            return str(git_bash_path)
    path_bash = shutil.which("bash")
    if path_bash is not None and not _is_wsl_launcher(path_bash):
        return path_bash
    return None


def with_platform_posix_shell(command: Sequence[str]) -> list[str]:
    """Windows 上把请求 POSIX shell 的 argv[0] 换成 Git Bash；其他平台原样返回。

    Args:
        command: 待执行的命令与参数。

    Returns:
        可能替换了 argv[0] 的命令列表；其余参数逐字不动。

    Raises:
        PosixShellNotFoundError: Windows 上请求了 POSIX shell 但找不到 Git Bash。
    """
    command_parts = list(command)
    if os.name != "nt" or not command_parts:
        return command_parts
    if command_parts[0] not in _POSIX_SHELL_COMMAND_NAMES:
        return command_parts
    git_bash = _find_windows_git_bash()
    if git_bash is None:
        raise PosixShellNotFoundError(
            "Running POSIX shell commands on Windows requires Git for Windows "
            "(its bin\\bash.exe); the bash.exe in System32/WindowsApps is a WSL "
            "launcher and is not used. Install Git for Windows from "
            "https://git-scm.com/download/win and make sure git is on PATH."
        )
    if command_parts[0] != git_bash:
        logger.debug("Using Git Bash for %s: %s", command_parts[0], git_bash)
    command_parts[0] = git_bash
    return command_parts


__all__ = ["PosixShellNotFoundError", "with_platform_posix_shell"]

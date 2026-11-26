"""读取源码安装的 Console 启动提交与当前 checkout 提交。"""

from pathlib import Path
from subprocess import SubprocessError
from typing import TypedDict

from backend.api.cli_entry import editable_source_directory
from backend.core.use_cases.agent_runner_factory import create_process_runner


class ConsoleSourceVersion(TypedDict):
    """可编辑安装下的源码提交信息。"""

    startup_commit: str | None
    checkout_commit: str | None


def _read_checkout_commit(source_root: Path) -> str | None:
    # 校验仓库根，避免源码归档意外继承外层 Git 仓库的提交。
    if not (source_root / ".git").exists():
        return None
    try:
        git_result = create_process_runner().run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=source_root, check=False, timeout=1
        )
    except (OSError, SubprocessError):
        return None
    if git_result.return_code != 0:
        return None
    return git_result.stdout.strip() or None


_SOURCE_DIRECTORY = editable_source_directory()
_SOURCE_ROOT = Path(_SOURCE_DIRECTORY) if _SOURCE_DIRECTORY else None
_STARTUP_COMMIT = _read_checkout_commit(_SOURCE_ROOT) if _SOURCE_ROOT else None


def read_console_source_version() -> ConsoleSourceVersion | None:
    """返回当前源码提交和启动时记录的提交，发行版安装返回 None。"""
    if _SOURCE_ROOT is None:
        return None
    return {
        "startup_commit": _STARTUP_COMMIT,
        "checkout_commit": _read_checkout_commit(_SOURCE_ROOT),
    }

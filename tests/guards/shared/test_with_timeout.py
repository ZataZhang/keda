"""守护 `just test` 超时兜底脚本的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

重点守两条不变量：

1. **0 / 1 / 自身进程组永不可被整组发信号。** 这不是防御性冗余：曾有 watchdog
   把 ``MagicMock.__index__``（默认返回 1）当成进程组，真的对 1 号组发了 SIGKILL
   把 runner 自己打没。去掉这层守卫会重演同一事故。
2. **命令正常退出后仍要清空进程组。** `just test` 的真实死法就是顶层退出、
   lint/pytest 子树留着，只杀顶层等于没杀。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# with_timeout.py 不是包的一部分，import 前需把它所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
if str(_JUST_SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(_JUST_SCRIPTS_PATH))

import with_timeout  # noqa: E402

_GRACE_SECONDS = 5.0


def _pid_alive(pid: int) -> bool:
    """用 signal 0 判断单个进程是否存活。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_refuses_to_signal_reserved_process_groups() -> None:
    """0 号组（调用者自身）与 1 号组（init）必须被拒绝。"""
    assert with_timeout.is_signalable_group(0) is False
    assert with_timeout.is_signalable_group(1) is False


def test_refuses_to_signal_own_process_group() -> None:
    """对自己所在的进程组发信号等于自杀，必须被拒绝。"""
    assert with_timeout.is_signalable_group(os.getpgrp()) is False


def test_accepts_a_foreign_process_group() -> None:
    """正常的子进程组（组 id == 子进程 pid）应当放行，守卫不能误伤真实场景。"""
    foreign_group_id = os.getpgrp() + 10_000
    assert with_timeout.is_signalable_group(foreign_group_id) is True


def test_propagates_child_exit_code() -> None:
    """未超时时必须原样透传被包装命令的退出码。"""
    exit_code = with_timeout.run(
        30.0, [sys.executable, "-c", "raise SystemExit(3)"], _GRACE_SECONDS
    )
    assert exit_code == 3


def test_returns_timeout_exit_code_when_command_hangs() -> None:
    """超时必须返回 124（GNU timeout 约定），而不是被挂住。"""
    hang_command = [sys.executable, "-c", "import time; time.sleep(30)"]
    exit_code = with_timeout.run(1.0, hang_command, _GRACE_SECONDS)
    assert exit_code == with_timeout.TIMEOUT_EXIT_CODE


def test_reaps_subtree_left_behind_after_normal_exit(tmp_path: Path) -> None:
    """顶层命令正常退出但留下子进程时，整组仍必须被清空。"""
    pid_file = tmp_path / "orphan.pid"
    spawn_then_exit = (
        "import pathlib, subprocess, sys;"
        "orphan = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(orphan.pid), encoding='utf-8')"
    )

    exit_code = with_timeout.run(30.0, [sys.executable, "-c", spawn_then_exit], _GRACE_SECONDS)

    assert exit_code == 0
    orphan_pid = int(pid_file.read_text(encoding="utf-8"))
    assert not _pid_alive(orphan_pid), f"子进程 {orphan_pid} 在顶层退出后仍存活，回收失效"


def test_reports_missing_command_without_raising() -> None:
    """命令不存在时返回 127，不能把异常抛给 recipe。"""
    exit_code = with_timeout.run(5.0, ["/nonexistent/command"], _GRACE_SECONDS)
    assert exit_code == with_timeout.COMMAND_NOT_FOUND_EXIT_CODE

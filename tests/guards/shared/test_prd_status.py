"""守护 PRD 状态看板 worktree 信号的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/just/prd_status.py`` 的 ACTIVITY 列。核心不变量：

1. **无锁但存在分支名匹配的 worktree 时必须亮 ``⚠ unlocked`` 警告。** 互斥依赖
   执行锁；绕过 ``just prd start`` / ``just implement`` 直接拿 worktree 开工
   （裸 ``git worktree add`` 或旧版脚本路径）不会产生锁，看板若静默显示 ``-``，
   "同一条 PRD 两个会话撞车"的漏洞就被掩盖——必须把互斥未生效摆到台面上。
2. **心跳过期不等于会话已死。** 锁归属 worktree 在过期窗口内仍有文件改动时，
   ACTIVITY 仍按 RUNNING 渲染；只有心跳过期且 worktree 无活性佐证时才显示
   STALE。否则长会话每 30 分钟翻红一次，看板可信度会被狼来了磨光。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# prd_status.py / prd_lock.py 不是包的一部分，import 前需把它们所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
if str(_JUST_SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(_JUST_SCRIPTS_PATH))

import prd_status  # noqa: E402

_FIXTURE_PRD_NAME = "P2-FEAT-20260101-000000-avatar-upload"
_FIXTURE_PRD_RELATIVE_PATH = f"tasks/pending/{_FIXTURE_PRD_NAME}.md"
_PLAIN_PALETTE = prd_status.Palette(enabled=False)


def _run_git(repo_path: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    """在指定目录执行 git 命令并返回结果。"""
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_main_repo(repo_path: Path) -> Path:
    """初始化一个带 tasks 结构与一次提交的真实 git 仓库。"""
    repo_path.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "init", "-b", "main")
    _run_git(repo_path, "config", "user.email", "guard@example.com")
    _run_git(repo_path, "config", "user.name", "guard-test")
    prd_file_path = repo_path / _FIXTURE_PRD_RELATIVE_PATH
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    prd_file_path.write_text(
        "# fixture PRD\n\n## Acceptance Checklist\n\n- [ ] item one\n",
        encoding="utf-8",
    )
    _run_git(repo_path, "add", ".")
    _run_git(repo_path, "commit", "-m", "init")
    return repo_path


def _add_linked_worktree(main_repo_path: Path, branch_name: str, worktree_name: str) -> Path:
    """为主仓库创建一个真实 linked worktree 并返回其路径。"""
    worktree_path = main_repo_path.parent / worktree_name
    _run_git(main_repo_path, "worktree", "add", "-b", branch_name, str(worktree_path))
    return worktree_path


def _write_lock(
    repo_path: Path,
    *,
    heartbeat_at: datetime,
    holder_worktree: str,
    holder_tool: str = "claude",
    holder_branch: str = "feat/avatar-upload",
) -> Path:
    """直接写入一把指定状态的锁，构造看板的 fresh / stale 场景。"""
    lock_path = repo_path / "tasks" / "evidence" / _FIXTURE_PRD_NAME / "active.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_metadata = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "worktree": holder_worktree,
        "started_at": (heartbeat_at - timedelta(minutes=10)).isoformat(),
        "heartbeat_at": heartbeat_at.isoformat(),
        "ai_tool": holder_tool,
        "branch": holder_branch,
    }
    lock_path.write_text(json.dumps(lock_metadata), encoding="utf-8")
    return lock_path


def _render_activity_cell(repo_path: Path) -> str:
    """读取 fixture PRD 并渲染 ACTIVITY 单元格（无颜色）。"""
    prd_record = prd_status.collect_prd_record(
        repo_path / _FIXTURE_PRD_RELATIVE_PATH, repo_path / "tasks" / "evidence"
    )
    return prd_status.format_activity_cell(
        prd_record, repo_path, repo_path / "tasks" / "evidence", _PLAIN_PALETTE
    )


def test_no_lock_with_matching_worktree_branch_shows_unlocked_warning(tmp_path: Path) -> None:
    """无锁 + 分支最后一段匹配 slug 的 worktree：显示 ⚠ unlocked @<分支全名>。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-avatar")

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert "unlocked" in raw_cell_text
    assert "@feat/avatar-upload" in raw_cell_text


def test_no_lock_without_matching_worktree_falls_back_to_dash(tmp_path: Path) -> None:
    """无锁 + worktree 分支与 slug 不匹配：不亮警告，回落到 ``-``。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _add_linked_worktree(main_repo_path, "feat/unrelated-thing", "wt-unrelated")
    # 把 PRD 文件 mtime 拨到弱信号窗口外，隔离 ⚡ active 对断言的干扰。
    idle_mtime_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(
        main_repo_path / _FIXTURE_PRD_RELATIVE_PATH,
        (idle_mtime_timestamp, idle_mtime_timestamp),
    )

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert raw_cell_text == "-"


def test_stale_lock_with_active_worktree_renders_running(tmp_path: Path) -> None:
    """心跳过期但归属 worktree 近期仍有文件改动：ACTIVITY 仍按 RUNNING 渲染。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-active")
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
    )
    (linked_worktree_path / "recent-edit.py").write_text("# active\n", encoding="utf-8")

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("RUNNING")
    assert "@feat/avatar-upload" in raw_cell_text


def test_stale_lock_with_idle_worktree_renders_stale(tmp_path: Path) -> None:
    """心跳过期且归属 worktree 无近期改动（mtime 均在窗口外）：显示 STALE。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-idle")
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
    )
    idle_mtime_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    for existing_file_path in linked_worktree_path.rglob("*"):
        if existing_file_path.is_file():
            os.utime(existing_file_path, (idle_mtime_timestamp, idle_mtime_timestamp))

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("STALE")


def test_fresh_lock_renders_running_without_worktree_activity(tmp_path: Path) -> None:
    """新鲜锁不需要活性佐证：即使归属 worktree 已删除也按 RUNNING 渲染。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree="../wt-long-gone",
    )

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("RUNNING")

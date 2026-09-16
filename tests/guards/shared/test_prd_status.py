"""守护 PRD 状态看板 worktree 信号的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/just/prd_status.py`` 的 ACTIVITY 与 DEPS 列。核心不变量：

1. **无锁但存在分支名匹配的 worktree、且其中未归档该 PRD 时必须亮 ``⚠ unlocked``
   警告。** 互斥依赖执行锁；绕过 ``just prd start`` / ``just implement`` 直接拿
   worktree 开工（裸 ``git worktree add`` 或旧版脚本路径）不会产生锁，看板若
   静默显示 ``-``，"同一条 PRD 两个会话撞车"的漏洞就被掩盖——必须把互斥未
   生效摆到台面上。
2. **分支上已归档的 PRD 不得继续按 ``⚠ unlocked`` 报警。** 匹配 worktree 的
   ``tasks/archive`` 里已有该 PRD 时，说明收尾已在分支完成、只差合并回主线；
   继续亮 ⚠ 会误导人重新执行一个已完成的 PRD。ACTIVITY 应显示绿色
   ``✔ branch-archived @<branch> · <n>/<m> · awaiting merge``（清单进度取
   归档副本的真实勾选）。
3. **心跳过期不等于会话已死。** 锁归属 worktree 在过期窗口内仍有文件改动时，
   ACTIVITY 仍按 RUNNING 渲染；只有心跳过期且 worktree 无活性佐证时才显示
   STALE。否则长会话每 30 分钟翻红一次，看板可信度会被狼来了磨光。
4. **hard gate 且依赖仍在 ``tasks/pending`` 时必须亮 ``⛔ blocked``，不得静默
   显示 ``-``。** DEPS 列是开工前判断交付顺序的唯一入口；依赖被隐藏时，下游
   PRD 会被误当成可直接开工，顺序违反（先做下游、上游还没有交付）只能等到
   交付阶段才暴露。``none`` gate 与无 §8 章节保持 ``-``，不给无依赖的 PRD
   制造噪声；本地无法判定的引用（Issue 号、悬空路径）用 ``?`` 显式标出而不是
   猜一个结论。
"""

from __future__ import annotations

import json
import os
import re
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

_DEFAULT_FIXTURE_PRD_TEXT = "# fixture PRD\n\n## Acceptance Checklist\n\n- [ ] item one\n"
_DEPENDENCY_PRD_TEMPLATE = (
    "# fixture PRD\n"
    "\n"
    "## 8. Delivery Dependencies\n"
    "\n"
    "- Group: none\n"
    "- Depends on tasks/issues:\n"
    "  - {dependency_ref}\n"
    "- Gate type: {gate_type}\n"
    "\n"
    "## 9. Acceptance Checklist\n"
    "\n"
    "- [ ] item one\n"
)
_UPSTREAM_PRD_NAME = "P1-FEAT-20260101-000001-upstream-task"
_UPSTREAM_PRD_SLUG = "upstream-task"


def _run_git(repo_path: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    """在指定目录执行 git 命令并返回结果。"""
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_main_repo(repo_path: Path, prd_text: str = _DEFAULT_FIXTURE_PRD_TEXT) -> Path:
    """初始化一个带 tasks 结构与一次提交的真实 git 仓库。

    Args:
        repo_path (Path): 待初始化的仓库目录。
        prd_text (str): fixture PRD 的正文；依赖列用例传入含 §8 的模板。
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "init", "-b", "main")
    _run_git(repo_path, "config", "user.email", "guard@example.com")
    _run_git(repo_path, "config", "user.name", "guard-test")
    prd_file_path = repo_path / _FIXTURE_PRD_RELATIVE_PATH
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    prd_file_path.write_text(prd_text, encoding="utf-8")
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


def _render_deps_cell(repo_path: Path) -> str:
    """读取 fixture PRD 并渲染 DEPS 单元格（无颜色）。"""
    prd_record = prd_status.collect_prd_record(
        repo_path / _FIXTURE_PRD_RELATIVE_PATH, repo_path / "tasks" / "evidence"
    )
    return prd_status.format_deps_cell(
        prd_record,
        repo_path / "tasks" / "pending",
        repo_path / "tasks" / "archive",
        _PLAIN_PALETTE,
    )


def _init_dependency_repo(repo_path: Path, dependency_ref: str, gate_type: str) -> Path:
    """初始化 §8 声明了指定依赖引用与 gate 类型的 fixture 仓库。"""
    return _init_main_repo(
        repo_path,
        prd_text=_DEPENDENCY_PRD_TEMPLATE.format(
            dependency_ref=dependency_ref, gate_type=gate_type
        ),
    )


def _write_upstream_prd(repo_path: Path, bucket: str) -> Path:
    """在 ``tasks/pending`` 或 ``tasks/archive`` 下写入上游 fixture PRD。"""
    upstream_path = repo_path / "tasks" / bucket / f"{_UPSTREAM_PRD_NAME}.md"
    upstream_path.parent.mkdir(parents=True, exist_ok=True)
    upstream_path.write_text("# upstream PRD\n", encoding="utf-8")
    return upstream_path


def test_no_lock_with_matching_worktree_branch_shows_unlocked_warning(tmp_path: Path) -> None:
    """无锁 + 匹配 worktree 且其中未归档该 PRD：显示 ⚠ unlocked @<分支全名>。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-avatar")

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert "unlocked" in raw_cell_text
    assert "@feat/avatar-upload" in raw_cell_text


def test_no_lock_with_worktree_archived_prd_renders_branch_archived(tmp_path: Path) -> None:
    """无锁 + PRD 已在匹配 worktree 内归档：显示 branch-archived 而非 ⚠ unlocked。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-done")
    # worktree 内完成收尾：PRD 移入 tasks/archive 且清单全勾；主线 pending 副本
    # 原样保留，看板仍会列出该 PRD——这正是要修的盲区场景。
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{_FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text(
        "# fixture PRD\n\n## Acceptance Checklist\n\n- [x] item one\n- [x] item two\n",
        encoding="utf-8",
    )

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert "✔ branch-archived @feat/avatar-upload" in raw_cell_text
    assert "2/2" in raw_cell_text
    assert "awaiting merge" in raw_cell_text
    assert "unlocked" not in raw_cell_text


def test_branch_archived_without_checklist_omits_progress(tmp_path: Path) -> None:
    """归档副本没有清单小节时不显示 n/m 进度，branch-archived 与合并提示保留。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-plain")
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{_FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text("# fixture PRD\n\n无清单小节。\n", encoding="utf-8")

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert "✔ branch-archived @feat/avatar-upload" in raw_cell_text
    assert "awaiting merge" in raw_cell_text
    assert not re.search(r"\d+/\d+", raw_cell_text)


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


def test_hard_gate_with_pending_dependency_shows_blocked(tmp_path: Path) -> None:
    """hard gate + 依赖仍在 tasks/pending：显示 ⛔ blocked by <上游 slug>。"""
    main_repo_path = _init_dependency_repo(
        tmp_path / "repo",
        dependency_ref=f"tasks/pending/{_UPSTREAM_PRD_NAME}.md",
        gate_type="hard",
    )
    _write_upstream_prd(main_repo_path, "pending")

    raw_cell_text = _render_deps_cell(main_repo_path)

    assert raw_cell_text == f"⛔ blocked by {_UPSTREAM_PRD_SLUG}"


def test_hard_gate_with_archived_dependency_shows_deps_ok(tmp_path: Path) -> None:
    """hard gate + 依赖已归档：显示 ✔ deps ok，不再制造阻塞告警。"""
    main_repo_path = _init_dependency_repo(
        tmp_path / "repo",
        dependency_ref=f"tasks/archive/{_UPSTREAM_PRD_NAME}.md",
        gate_type="hard",
    )
    _write_upstream_prd(main_repo_path, "archive")

    raw_cell_text = _render_deps_cell(main_repo_path)

    assert raw_cell_text == "✔ deps ok"


def test_hard_gate_with_dangling_dependency_shows_unknown(tmp_path: Path) -> None:
    """hard gate + 依赖引用的 PRD 两处都不存在：显示 ? <slug>，不猜结论。"""
    main_repo_path = _init_dependency_repo(
        tmp_path / "repo",
        dependency_ref="P1-FEAT-20260101-000001-ghost-task",
        gate_type="hard",
    )

    raw_cell_text = _render_deps_cell(main_repo_path)

    assert raw_cell_text == "? ghost-task"


def test_hard_gate_with_issue_ref_shows_unknown(tmp_path: Path) -> None:
    """hard gate + Issue 号引用：本地判不了远端状态，显示 ? #<编号>。"""
    main_repo_path = _init_dependency_repo(
        tmp_path / "repo",
        dependency_ref="#12345",
        gate_type="hard",
    )

    raw_cell_text = _render_deps_cell(main_repo_path)

    assert raw_cell_text == "? #12345"


def test_no_dependency_section_falls_back_to_dash(tmp_path: Path) -> None:
    """无 §8 章节（gate 视为 none）：DEPS 回落 ``-``，不给无依赖 PRD 制造噪声。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")

    raw_cell_text = _render_deps_cell(main_repo_path)

    assert raw_cell_text == "-"


def test_soft_gate_with_pending_dependency_shows_soft_hint(tmp_path: Path) -> None:
    """soft gate + 依赖未交付：暗色 soft → <slug> 提示顺序，不按 hard 阻塞告警。"""
    main_repo_path = _init_dependency_repo(
        tmp_path / "repo",
        dependency_ref=f"tasks/pending/{_UPSTREAM_PRD_NAME}.md",
        gate_type="soft",
    )
    _write_upstream_prd(main_repo_path, "pending")

    raw_cell_text = _render_deps_cell(main_repo_path)

    assert raw_cell_text == f"soft → {_UPSTREAM_PRD_SLUG}"


def test_render_prd_table_header_includes_deps_column(tmp_path: Path, capsys) -> None:
    """表头必须含 DEPS 列——依赖可见性的唯一入口，不得被静默移除。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    prd_record = prd_status.collect_prd_record(
        main_repo_path / _FIXTURE_PRD_RELATIVE_PATH, main_repo_path / "tasks" / "evidence"
    )

    prd_status.render_prd_table(
        [prd_record],
        _PLAIN_PALETTE,
        main_repo_path,
        main_repo_path / "tasks" / "evidence",
        main_repo_path / "tasks" / "pending",
        main_repo_path / "tasks" / "archive",
    )
    raw_output_text = capsys.readouterr().out

    assert "DEPS" in raw_output_text
    assert "ACTIVITY" in raw_output_text

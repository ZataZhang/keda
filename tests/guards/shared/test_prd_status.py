"""守护 PRD 状态看板 worktree 信号的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/just/prd_status.py`` 的 CHECKLIST / EVIDENCE / ACTIVITY /
DEPS 列。核心不变量：

1. **无锁但存在分支名匹配的 worktree、且其中未归档该 PRD 时必须亮 ``⚠ unlocked``
   警告。** 互斥依赖执行锁；绕过 ``just prd start`` / ``just implement`` 直接拿
   worktree 开工（裸 ``git worktree add`` 或旧版脚本路径）不会产生锁，看板若
   静默显示 ``-``，"同一条 PRD 两个会话撞车"的漏洞就被掩盖——必须把互斥未
   生效摆到台面上。
2. **分支上已归档的 PRD 优先于一切锁信号。** 匹配 worktree 的 ``tasks/archive``
   里已有该 PRD 时，说明收尾已在分支完成、只差合并回主线。ACTIVITY 必须显示绿色
   ``✔ branch-archived @<branch> · awaiting merge``，不得因残留锁渲染成 RUNNING /
   STALE，也不得按 ``⚠ unlocked`` 报警——三种渲染都会误导人重新执行一个已完成
   的 PRD。清单进度不在这里重复携带：它由同样读分支副本的 CHECKLIST 列承担。
3. **心跳过期不等于会话已死。** 锁归属 worktree 在过期窗口内仍有文件改动时，
   ACTIVITY 仍按 RUNNING 渲染；只有心跳过期且 worktree 无活性佐证时才显示
   STALE。否则长会话每 30 分钟翻红一次，看板可信度会被狼来了磨光。
4. **hard gate 且依赖仍在 ``tasks/pending`` 时必须亮 ``⛔ blocked``，不得静默
   显示 ``-``。** DEPS 列是开工前判断交付顺序的唯一入口；依赖被隐藏时，下游
   PRD 会被误当成可直接开工，顺序违反（先做下游、上游还没有交付）只能等到
   交付阶段才暴露。``none`` gate 与无 §8 章节保持 ``-``，不给无依赖的 PRD
   制造噪声；本地无法判定的引用（Issue 号、悬空路径）用 ``?`` 显式标出而不是
   猜一个结论。
5. **ACTIVITY 的位置标签绝不显示并不存在的分支。** 锁归属主仓库（``worktree``
   为空）时必须显示 ``@主仓库``；归属 worktree 目录已消失且锁里的分支也无处检出时
   显示归属标签本身。锁里的 ``branch`` 只是领锁瞬间的快照——``just implement``
   在主仓库领锁、worktree 还没建出来时就会写入分支名，照抄它会让用户拿着
   ``just worktree -o`` 去打一个根本打不开的名字。
6. **清单进度与证据包取分支副本。** 执行发生在 worktree 里，主仓库的
   ``tasks/pending`` 副本与证据目录要等合并回主线才更新；只读它们会让"分支上早已
   勾完、看板仍显示 0/21"长期挂着（keda 的真实案例）。worktree 内按
   ``tasks/archive`` → ``tasks/pending`` 取清单副本，证据每个槽位单独兜底。
   只认 slug 匹配到的那个 worktree——每个 worktree 都带一份未改动的同名 pending
   副本，拿错副本会显示别的分支的陈旧进度。
7. **``--detail`` 摘要与清单进度同源。** 摘要解析必须吃与 CHECKLIST 相同的
   分支副本正文，且只做有界截断——看板定位是"快速定位，最终判断以 PRD 原文
   为准"，摘要是行下定位辅助而非正文替代。
8. **AWAITING HUMAN 分区只认 PRD 头部的验收状态横幅。** 横幅是 §9 验收清单的
   唯一投影，看板读它同样走分支副本（执行方在 worktree 里翻的状态才算数）。
   只接受引用块行（``>`` 开头）里带 ``验收状态`` / ``Acceptance Status`` 的声明，
   并取标记之后**最先出现**的状态词——模板括号里的"完成时改为 🧍 待人工验收"
   不得抢占行首的 ``⬜ 未开工``。没有横幅或认不出状态词时按未开工处理、留在
   PENDING：按 §9 未勾项结构反推会漏判真实的人工项（常挂在 ``Human-Confirmed``
   之外的小节下），也会把尚未完工的 PRD 误报成"等你验收"。待人工记录仍留在
   ``tasks/pending``（规范只允许 ``✅ 可归档`` 移入 archive），分区只做呈现。
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

import prd_detail  # noqa: E402
import prd_lock  # noqa: E402
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
    return prd_status.format_activity_cell(
        _collect_fixture_record(repo_path),
        repo_path,
        repo_path / "tasks" / "evidence",
        _PLAIN_PALETTE,
    )


def _collect_fixture_record(repo_path: Path) -> prd_status.PrdRecord:
    """收集 fixture PRD 的看板记录，工作树列表按仓库现状实时获取。"""
    return prd_status.collect_prd_record(
        repo_path / _FIXTURE_PRD_RELATIVE_PATH,
        repo_path / "tasks" / "evidence",
        prd_lock.list_linked_worktree_branches(repo_path),
    )


def _render_checklist_cell(repo_path: Path) -> str:
    """渲染 CHECKLIST 单元格（无颜色）。"""
    return prd_status.format_checklist_cell(_collect_fixture_record(repo_path), _PLAIN_PALETTE)


def _render_evidence_cell(repo_path: Path) -> str:
    """渲染 EVIDENCE 单元格（无颜色）。"""
    return prd_status.format_evidence_cell(_collect_fixture_record(repo_path), _PLAIN_PALETTE)


def _checklist_prd_text(checked_item_count: int, unchecked_item_count: int) -> str:
    """生成带指定勾选数量的 fixture PRD 正文，用于构造分支副本与主仓库副本的差异。"""
    checked_items_text = "".join(
        f"- [x] item {item_index}\n" for item_index in range(1, checked_item_count + 1)
    )
    unchecked_items_text = "".join(
        f"- [ ] item {item_index}\n"
        for item_index in range(
            checked_item_count + 1, checked_item_count + unchecked_item_count + 1
        )
    )
    return f"# fixture PRD\n\n## Acceptance Checklist\n\n{checked_items_text}{unchecked_items_text}"


def _render_deps_cell(repo_path: Path) -> str:
    """读取 fixture PRD 并渲染 DEPS 单元格（无颜色）。"""
    return prd_status.format_deps_cell(
        _collect_fixture_record(repo_path),
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
    assert "awaiting merge" in raw_cell_text
    assert "unlocked" not in raw_cell_text
    # 进度不再由 ACTIVITY 重复携带，CHECKLIST 列直接取分支归档副本的真实勾选。
    assert not re.search(r"\d+/\d+", raw_cell_text)
    assert _render_checklist_cell(main_repo_path) == "2/2"


def test_branch_archived_copy_without_checklist_shows_dash_in_checklist(tmp_path: Path) -> None:
    """分支归档副本没有清单小节：CHECKLIST 显示 ``-``，ACTIVITY 仍报 branch-archived。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-plain")
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{_FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text("# fixture PRD\n\n无清单小节。\n", encoding="utf-8")

    raw_activity_text = _render_activity_cell(main_repo_path)

    assert "✔ branch-archived @feat/avatar-upload" in raw_activity_text
    assert "awaiting merge" in raw_activity_text
    assert not re.search(r"\d+/\d+", raw_activity_text)
    # 分支副本是执行现场的真相：归档副本没有清单小节时显示 -，不回退主仓库副本。
    assert _render_checklist_cell(main_repo_path) == "-"


def test_stale_lock_with_archived_branch_renders_branch_archived(tmp_path: Path) -> None:
    """过期锁（无活性佐证）遇到分支已归档：archive 优先，不得继续报 STALE。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(
        main_repo_path, "feat/avatar-upload", "wt-done-idle"
    )
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
    )
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{_FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text(
        "# fixture PRD\n\n## Acceptance Checklist\n\n- [x] item one\n",
        encoding="utf-8",
    )
    # 归档动作本身会刷新 mtime；把 worktree 内文件全部拨到过期窗口外，确保锁真的
    # 落在 STALE 分支（无活性佐证），证明是归档优先级压过 STALE 而非撞上 RUNNING。
    idle_mtime_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    for existing_file_path in linked_worktree_path.rglob("*"):
        if existing_file_path.is_file():
            os.utime(existing_file_path, (idle_mtime_timestamp, idle_mtime_timestamp))

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("✔ branch-archived @feat/avatar-upload")
    assert "awaiting merge" in raw_cell_text
    assert "STALE" not in raw_cell_text
    assert "RUNNING" not in raw_cell_text


def test_fresh_lock_with_archived_branch_renders_branch_archived(tmp_path: Path) -> None:
    """新鲜锁遇到分支已归档：archive 无条件优先，RUNNING 也不得盖住归档提示。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(
        main_repo_path, "feat/avatar-upload", "wt-done-live"
    )
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
    )
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{_FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text(
        "# fixture PRD\n\n## Acceptance Checklist\n\n- [x] item one\n- [x] item two\n",
        encoding="utf-8",
    )

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("✔ branch-archived @feat/avatar-upload")
    assert "awaiting merge" in raw_cell_text
    assert "RUNNING" not in raw_cell_text
    assert not re.search(r"\d+/\d+", raw_cell_text)
    assert _render_checklist_cell(main_repo_path) == "2/2"


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


def test_fresh_lock_held_from_main_repo_shows_main_repo_location(tmp_path: Path) -> None:
    """锁归属主仓库（worktree 为空）：位置显示 @主仓库，不显示尚不存在的分支。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree="",
        holder_branch="fcl-sam-to-mex-platform-skill-sync",
    )

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("RUNNING")
    assert raw_cell_text.endswith("@主仓库")
    assert "fcl-sam-to-mex-platform-skill-sync" not in raw_cell_text


def test_fresh_lock_with_missing_worktree_does_not_advertise_branch(tmp_path: Path) -> None:
    """归属 worktree 目录已消失且分支无处检出：显示归属标签，不显示该分支。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree="../wt-gone",
        holder_branch="feat/avatar-upload",
    )

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("RUNNING")
    assert "@../wt-gone" in raw_cell_text
    assert "@feat/avatar-upload" not in raw_cell_text


def test_fresh_lock_location_prefers_actual_worktree_branch(tmp_path: Path) -> None:
    """归属 worktree 仍存在时显示它当前实际检出的分支，而不是锁里的过期分支字段。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-live")
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
        holder_branch="main",
    )

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert "@feat/avatar-upload" in raw_cell_text


def test_fresh_lock_location_uses_branch_checked_out_elsewhere(tmp_path: Path) -> None:
    """归属目录已消失但锁里的分支确实检出在某个 worktree：显示那个真实分支。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-moved")
    _write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree="../wt-old-path",
        holder_branch="feat/avatar-upload",
    )

    raw_cell_text = _render_activity_cell(main_repo_path)

    assert "@feat/avatar-upload" in raw_cell_text


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
    prd_record = _collect_fixture_record(main_repo_path)

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


def test_checklist_progress_prefers_branch_pending_copy(tmp_path: Path) -> None:
    """分支 pending 副本已勾完、主仓库副本仍是 0：CHECKLIST 显示分支副本的进度。"""
    main_repo_path = _init_main_repo(tmp_path / "repo", prd_text=_checklist_prd_text(0, 2))
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-progress")
    (linked_worktree_path / _FIXTURE_PRD_RELATIVE_PATH).write_text(
        _checklist_prd_text(2, 0), encoding="utf-8"
    )

    assert _render_checklist_cell(main_repo_path) == "2/2"


def test_checklist_progress_prefers_branch_archive_copy_over_pending(tmp_path: Path) -> None:
    """分支内同时存在 archive 与 pending 副本时，收尾态（archive）优先。"""
    main_repo_path = _init_main_repo(tmp_path / "repo", prd_text=_checklist_prd_text(0, 2))
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-both")
    (linked_worktree_path / _FIXTURE_PRD_RELATIVE_PATH).write_text(
        _checklist_prd_text(1, 1), encoding="utf-8"
    )
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{_FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text(_checklist_prd_text(2, 0), encoding="utf-8")

    assert _render_checklist_cell(main_repo_path) == "2/2"


def test_checklist_progress_ignores_unrelated_worktree_copy(tmp_path: Path) -> None:
    """每个 worktree 都带一份未改动的同名副本；只有 slug 匹配的 worktree 才算分支副本。"""
    main_repo_path = _init_main_repo(tmp_path / "repo", prd_text=_checklist_prd_text(0, 2))
    _add_linked_worktree(main_repo_path, "feat/unrelated-thing", "wt-unrelated-copy")
    # 主仓库副本在 worktree 建好之后才被勾选；无关 worktree 里那份仍是 0/2。
    (main_repo_path / _FIXTURE_PRD_RELATIVE_PATH).write_text(
        _checklist_prd_text(2, 0), encoding="utf-8"
    )

    assert _render_checklist_cell(main_repo_path) == "2/2"


def test_evidence_cell_reads_branch_evidence_dir(tmp_path: Path) -> None:
    """证据写在分支证据目录、主仓库还没有时，EVIDENCE 必须把它显示出来。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-evidence")
    branch_evidence_dir = linked_worktree_path / "tasks" / "evidence" / _FIXTURE_PRD_NAME
    branch_evidence_dir.mkdir(parents=True, exist_ok=True)
    (branch_evidence_dir / f"{_FIXTURE_PRD_NAME}.verification-plan.md").write_text(
        "# plan\n", encoding="utf-8"
    )
    (branch_evidence_dir / f"{_FIXTURE_PRD_NAME}.evidence-report.md").write_text(
        "# report\n", encoding="utf-8"
    )

    raw_cell_text = _render_evidence_cell(main_repo_path)

    assert "plan✓" in raw_cell_text
    assert "report✓" in raw_cell_text
    assert "verifier✗" in raw_cell_text


def test_evidence_cell_falls_back_to_main_per_slot(tmp_path: Path) -> None:
    """分支只写了 plan、report 仍在主仓库：两个槽位各自命中，不整体丢弃主仓库文件。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-split")
    main_evidence_dir = main_repo_path / "tasks" / "evidence" / _FIXTURE_PRD_NAME
    main_evidence_dir.mkdir(parents=True, exist_ok=True)
    (main_evidence_dir / f"{_FIXTURE_PRD_NAME}.evidence-report.md").write_text(
        "# report\n", encoding="utf-8"
    )
    branch_evidence_dir = linked_worktree_path / "tasks" / "evidence" / _FIXTURE_PRD_NAME
    branch_evidence_dir.mkdir(parents=True, exist_ok=True)
    (branch_evidence_dir / f"{_FIXTURE_PRD_NAME}.verification-plan.md").write_text(
        "# plan\n", encoding="utf-8"
    )

    raw_cell_text = _render_evidence_cell(main_repo_path)

    assert "plan✓" in raw_cell_text
    assert "report✓" in raw_cell_text


def test_extract_prd_title_and_summary_reads_numbered_introduction() -> None:
    """编号写法的 Introduction & Goals 章节正文作为摘要来源，标题取首个一级标题。"""
    prd_text = (
        "# P1-FEAT-20260901-000000 Demo\n"
        "\n"
        "## 1. Introduction & Goals\n"
        "\n"
        "第一段描述。\n"
        "第二段描述。\n"
        "\n"
        "## 2. Requirement Shape\n"
        "\n"
        "不属于摘要的正文。\n"
    )

    parsed_title_text, parsed_summary_lines = prd_detail.extract_prd_title_and_summary(prd_text)

    assert parsed_title_text == "P1-FEAT-20260901-000000 Demo"
    assert parsed_summary_lines == ("第一段描述。", "第二段描述。")


def test_extract_prd_title_and_summary_falls_back_to_preamble() -> None:
    """无 Introduction 章节时退化为一级标题之后、首个小节标题之前的引言。"""
    prd_text = (
        "# fixture PRD\n\n引言第一行。\n引言第二行。\n\n## Acceptance Checklist\n\n- [ ] item\n"
    )

    parsed_title_text, parsed_summary_lines = prd_detail.extract_prd_title_and_summary(prd_text)

    assert parsed_title_text == "fixture PRD"
    assert parsed_summary_lines == ("引言第一行。", "引言第二行。")


def test_extract_prd_title_and_summary_truncates_overflow_lines() -> None:
    """摘要超过行数上限时只取前 N 行，且末行补省略号披露后续仍有正文。"""
    prd_text = "# fixture PRD\n\n## 1. Introduction & Goals\n\n" + "".join(
        f"第 {index} 行。\n" for index in range(1, 7)
    )

    _, parsed_summary_lines = prd_detail.extract_prd_title_and_summary(prd_text)

    assert len(parsed_summary_lines) == prd_detail.DESCRIPTION_MAX_LINES
    assert parsed_summary_lines[0] == "第 1 行。"
    assert parsed_summary_lines[-1].endswith("…")


def test_extract_prd_title_and_summary_truncates_overwide_line() -> None:
    """单行超过宽度上限时就地截断并以省略号结尾，不产生超宽摘要行。"""
    overwide_line_text = "长" * (prd_detail.DESCRIPTION_LINE_MAX_WIDTH + 10)
    prd_text = f"# fixture PRD\n\n## 1. Introduction & Goals\n\n{overwide_line_text}\n"

    _, parsed_summary_lines = prd_detail.extract_prd_title_and_summary(prd_text)

    assert len(parsed_summary_lines) == 1
    assert parsed_summary_lines[0].endswith("…")
    assert len(parsed_summary_lines[0]) == prd_detail.DESCRIPTION_LINE_MAX_WIDTH


def test_render_prd_table_with_detail_prints_summary_under_row(tmp_path: Path, capsys) -> None:
    """--detail 开启时在行下打印标题与摘要块；默认关闭时不打印。"""
    detailed_prd_text = (
        "# P2-FEAT-20260101-000000-avatar-upload\n"
        "\n"
        "## 1. Introduction & Goals\n"
        "\n"
        "为头像上传引入分片上传。\n"
        "\n"
        "## Acceptance Checklist\n"
        "\n"
        "- [ ] item one\n"
    )
    main_repo_path = _init_main_repo(tmp_path / "repo", prd_text=detailed_prd_text)
    prd_record = _collect_fixture_record(main_repo_path)
    render_arguments = (
        [prd_record],
        _PLAIN_PALETTE,
        main_repo_path,
        main_repo_path / "tasks" / "evidence",
        main_repo_path / "tasks" / "pending",
        main_repo_path / "tasks" / "archive",
    )

    prd_status.render_prd_table(*render_arguments, show_detail=True)
    detailed_output_text = capsys.readouterr().out

    # 摘要块不携带章节标题，标题取一级标题行而非文件名
    assert "## 1. Introduction & Goals" not in detailed_output_text
    assert "P2-FEAT-20260101-000000-avatar-upload" in detailed_output_text
    assert "为头像上传引入分片上传。" in detailed_output_text

    prd_status.render_prd_table(*render_arguments)
    plain_output_text = capsys.readouterr().out

    assert "为头像上传引入分片上传。" not in plain_output_text


_AWAITING_PRD_NAME = "P2-FEAT-20260101-000002-awaiting-review"
_AWAITING_PRD_SLUG = "awaiting-review"


def _prd_text_with_acceptance_banner(banner_line: str) -> str:
    """生成带指定验收状态横幅行的 fixture PRD 正文。

    横幅行只给状态本身，第二行沿用规范要求的"§9 投影"说明；清单里机器项已勾、
    Human-Confirmed 项未勾，即 ``🧍 待人工验收`` 的真实形态。
    """
    return (
        "# fixture PRD\n"
        "\n"
        f"> {banner_line}\n"
        "> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。\n"
        "\n"
        "## 9. Acceptance Checklist\n"
        "\n"
        "#### Human-Confirmed\n"
        "\n"
        "- [x] machine item\n"
        "- [ ] human item\n"
    )


def test_acceptance_status_banner_reads_first_state_after_marker() -> None:
    """三态各取标记之后最先出现的状态词；模板括号里的备注不得抢占行首状态。"""
    assert (
        prd_status.parse_acceptance_status(
            _prd_text_with_acceptance_banner(
                "🧍 **验收状态**：待人工验收 — 仅剩 4 项 Human-Confirmed 未确认。"
            )
        )
        == prd_status.ACCEPTANCE_STATUS_AWAITING_HUMAN
    )
    assert (
        prd_status.parse_acceptance_status(
            _prd_text_with_acceptance_banner(
                "⬜ **验收状态**：未开工。（实现与自动验证完成、仅剩 Human-Confirmed 项时"
                "改为 🧍 待人工验收；§9 全部勾选后改为 ✅ 可归档）"
            )
        )
        == prd_status.ACCEPTANCE_STATUS_NOT_STARTED
    )
    assert (
        prd_status.parse_acceptance_status(
            _prd_text_with_acceptance_banner(
                "✅ **Acceptance Status**：可归档 — 验收清单已全部完成。"
            )
        )
        == prd_status.ACCEPTANCE_STATUS_ARCHIVE_READY
    )


def test_acceptance_status_ignores_mentions_outside_banner_line() -> None:
    """正文与决策日志里对该横幅的讨论不是状态声明：非引用块行一律不认。"""
    raw_decision_log_text = (
        "# fixture PRD\n"
        "\n"
        "## 13. Decision Log\n"
        "\n"
        "- 收尾时把验收状态横幅翻成 🧍 待人工验收。\n"
    )

    assert prd_status.parse_acceptance_status(raw_decision_log_text) == ""
    assert prd_status.parse_acceptance_status(_DEFAULT_FIXTURE_PRD_TEXT) == ""


def test_awaiting_human_banner_moves_prd_into_own_section(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """横幅 🧍 的 pending PRD 移出 PENDING：单独成段，未开工的那条仍留在 PENDING。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    (main_repo_path / "tasks" / "pending" / f"{_AWAITING_PRD_NAME}.md").write_text(
        _prd_text_with_acceptance_banner("🧍 **验收状态**：待人工验收 — 仅剩 1 项未确认。"),
        encoding="utf-8",
    )
    monkeypatch.chdir(main_repo_path)
    monkeypatch.setattr(sys, "argv", ["prd_status.py", "status"])

    prd_status.main()
    raw_output_text = capsys.readouterr().out

    pending_section_index = raw_output_text.index("PENDING (1)")
    awaiting_section_index = raw_output_text.index("AWAITING HUMAN (1)")
    assert raw_output_text.index("avatar-upload") < awaiting_section_index
    assert (
        pending_section_index < awaiting_section_index < raw_output_text.index(_AWAITING_PRD_SLUG)
    )


def test_pending_prd_without_banner_is_not_inferred_as_awaiting(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """没有横幅时不按 §9 结构反推：只剩 Human-Confirmed 未勾也留在 PENDING。"""
    main_repo_path = _init_main_repo(
        tmp_path / "repo",
        prd_text=(
            "# fixture PRD\n"
            "\n"
            "## Acceptance Checklist\n"
            "\n"
            "#### Human-Confirmed\n"
            "\n"
            "- [x] machine item\n"
            "- [ ] human item\n"
        ),
    )
    monkeypatch.chdir(main_repo_path)
    monkeypatch.setattr(sys, "argv", ["prd_status.py", "status"])

    prd_status.main()
    raw_output_text = capsys.readouterr().out

    assert "PENDING (1)" in raw_output_text
    assert "AWAITING HUMAN" not in raw_output_text


def test_acceptance_status_banner_prefers_branch_copy(tmp_path: Path) -> None:
    """横幅同样取分支副本：主仓库副本还是 ⬜，分支上已翻 🧍 的记录按待人工判定。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-review")
    (linked_worktree_path / _FIXTURE_PRD_RELATIVE_PATH).write_text(
        _prd_text_with_acceptance_banner("🧍 **验收状态**：待人工验收 — 仅剩 1 项未确认。"),
        encoding="utf-8",
    )

    assert _collect_fixture_record(main_repo_path).awaits_human_review
    # 主仓库副本（未开工）不该被分支副本污染判断之外的东西：同一条记录只认一个来源。
    assert (
        prd_status.parse_acceptance_status(
            (main_repo_path / _FIXTURE_PRD_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        == ""
    )

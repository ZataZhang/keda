"""守护 PRD 状态看板 worktree 信号的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/just/prd_status.py`` 的 CHECKLIST / EVIDENCE 列，以及随看板
拆出的 ``prd_activity.py``（ACTIVITY 列）、``prd_deps.py``（DEPS 列）。验收轴（横幅与
AWAITING HUMAN 分区）见 ``test_prd_status_acceptance.py``。核心不变量：

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
   副本，拿错副本会显示别的分支的陈旧进度。**例外：记录本身已来自主仓库
   ``tasks/archive`` 时不看分支。** 归档是终态，分支副本按定义已过时；同名
   worktree 往往还压着合并前的 pending 快照，继续采信会把一条已归档的 PRD
   显示成合并前的进度，并给它报一个早已不存在的 "awaiting merge"。
7. **``--detail`` 摘要与清单进度同源。** 摘要解析必须吃与 CHECKLIST 相同的
   分支副本正文，且只做有界截断——看板定位是"快速定位，最终判断以 PRD 原文
   为准"，摘要是行下定位辅助而非正文替代。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# prd_*.py 脚本与共用 fixtures 不是包的一部分，import 前需把它们所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
_SHARED_GUARDS_PATH = Path(__file__).resolve().parent
for _import_path in (_JUST_SCRIPTS_PATH, _SHARED_GUARDS_PATH):
    if str(_import_path) not in sys.path:
        sys.path.insert(0, str(_import_path))

import prd_detail  # noqa: E402
import prd_status  # noqa: E402
from prd_status_fixtures import (  # noqa: E402
    FIXTURE_PRD_ARCHIVE_RELATIVE_PATH,
    FIXTURE_PRD_NAME,
    FIXTURE_PRD_RELATIVE_PATH,
    PLAIN_PALETTE,
    UPSTREAM_PRD_NAME,
    UPSTREAM_PRD_SLUG,
    add_linked_worktree,
    checklist_prd_text,
    collect_fixture_record,
    init_dependency_repo,
    init_main_repo,
    render_activity_cell,
    render_checklist_cell,
    render_deps_cell,
    render_evidence_cell,
    write_lock,
    write_upstream_prd,
)


def test_no_lock_with_matching_worktree_branch_shows_unlocked_warning(tmp_path: Path) -> None:
    """无锁 + 匹配 worktree 且其中未归档该 PRD：显示 ⚠ unlocked @<分支全名>。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-avatar")

    raw_cell_text = render_activity_cell(main_repo_path)

    assert "unlocked" in raw_cell_text
    assert "@feat/avatar-upload" in raw_cell_text


def test_no_lock_with_worktree_archived_prd_renders_branch_archived(tmp_path: Path) -> None:
    """无锁 + PRD 已在匹配 worktree 内归档：显示 branch-archived 而非 ⚠ unlocked。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-done")
    # worktree 内完成收尾：PRD 移入 tasks/archive 且清单全勾；主线 pending 副本
    # 原样保留，看板仍会列出该 PRD——这正是要修的盲区场景。
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text(
        "# fixture PRD\n\n## Acceptance Checklist\n\n- [x] item one\n- [x] item two\n",
        encoding="utf-8",
    )

    raw_cell_text = render_activity_cell(main_repo_path)

    assert "✔ branch-archived @feat/avatar-upload" in raw_cell_text
    assert "awaiting merge" in raw_cell_text
    assert "unlocked" not in raw_cell_text
    # 进度不再由 ACTIVITY 重复携带，CHECKLIST 列直接取分支归档副本的真实勾选。
    assert not re.search(r"\d+/\d+", raw_cell_text)
    assert render_checklist_cell(main_repo_path) == "2/2"


def test_branch_archived_copy_without_checklist_shows_dash_in_checklist(tmp_path: Path) -> None:
    """分支归档副本没有清单小节：CHECKLIST 显示 ``-``，ACTIVITY 仍报 branch-archived。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-plain")
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text("# fixture PRD\n\n无清单小节。\n", encoding="utf-8")

    raw_activity_text = render_activity_cell(main_repo_path)

    assert "✔ branch-archived @feat/avatar-upload" in raw_activity_text
    assert "awaiting merge" in raw_activity_text
    assert not re.search(r"\d+/\d+", raw_activity_text)
    # 分支副本是执行现场的真相：归档副本没有清单小节时显示 -，不回退主仓库副本。
    assert render_checklist_cell(main_repo_path) == "-"


def test_main_archived_record_ignores_branch_for_activity(tmp_path: Path) -> None:
    """记录本身已来自主仓库 ``tasks/archive`` 时，ACTIVITY 不看分支。

    合并早已完成，「等合并」是假待办；分支上残留的 archive 副本不能把一条已归档
    的 PRD 渲染成还要在分支收尾，也不能拿它报 ``⚠ unlocked``。
    """
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(
        main_repo_path, "feat/avatar-upload", "wt-post-merge"
    )
    branch_archive_prd_path = linked_worktree_path / "tasks" / "archive" / f"{FIXTURE_PRD_NAME}.md"
    branch_archive_prd_path.parent.mkdir(parents=True, exist_ok=True)
    branch_archive_prd_path.write_text(checklist_prd_text(1, 1), encoding="utf-8")
    main_archive_prd_path = main_repo_path / FIXTURE_PRD_ARCHIVE_RELATIVE_PATH
    main_archive_prd_path.parent.mkdir(parents=True, exist_ok=True)
    main_archive_prd_path.write_text(checklist_prd_text(1, 1), encoding="utf-8")

    raw_activity_text = render_activity_cell(main_repo_path, FIXTURE_PRD_ARCHIVE_RELATIVE_PATH)

    assert "branch-archived" not in raw_activity_text
    assert "awaiting merge" not in raw_activity_text
    assert "unlocked" not in raw_activity_text


def test_stale_lock_with_archived_branch_renders_branch_archived(tmp_path: Path) -> None:
    """过期锁（无活性佐证）遇到分支已归档：archive 优先，不得继续报 STALE。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-done-idle")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
    )
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{FIXTURE_PRD_NAME}.md"
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

    raw_cell_text = render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("✔ branch-archived @feat/avatar-upload")
    assert "awaiting merge" in raw_cell_text
    assert "STALE" not in raw_cell_text
    assert "RUNNING" not in raw_cell_text


def test_fresh_lock_with_archived_branch_renders_branch_archived(tmp_path: Path) -> None:
    """新鲜锁遇到分支已归档：archive 无条件优先，RUNNING 也不得盖住归档提示。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-done-live")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
    )
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text(
        "# fixture PRD\n\n## Acceptance Checklist\n\n- [x] item one\n- [x] item two\n",
        encoding="utf-8",
    )

    raw_cell_text = render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("✔ branch-archived @feat/avatar-upload")
    assert "awaiting merge" in raw_cell_text
    assert "RUNNING" not in raw_cell_text
    assert not re.search(r"\d+/\d+", raw_cell_text)
    assert render_checklist_cell(main_repo_path) == "2/2"


def test_no_lock_without_matching_worktree_falls_back_to_dash(tmp_path: Path) -> None:
    """无锁 + worktree 分支与 slug 不匹配：不亮警告，回落到 ``-``。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    add_linked_worktree(main_repo_path, "feat/unrelated-thing", "wt-unrelated")
    # 把 PRD 文件 mtime 拨到弱信号窗口外，隔离 ⚡ active 对断言的干扰。
    idle_mtime_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(
        main_repo_path / FIXTURE_PRD_RELATIVE_PATH,
        (idle_mtime_timestamp, idle_mtime_timestamp),
    )

    raw_cell_text = render_activity_cell(main_repo_path)

    assert raw_cell_text == "-"


def test_stale_lock_with_active_worktree_renders_running(tmp_path: Path) -> None:
    """心跳过期但归属 worktree 近期仍有文件改动：ACTIVITY 仍按 RUNNING 渲染。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-active")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
    )
    (linked_worktree_path / "recent-edit.py").write_text("# active\n", encoding="utf-8")

    raw_cell_text = render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("RUNNING")
    assert "@feat/avatar-upload" in raw_cell_text


def test_stale_lock_with_idle_worktree_renders_stale(tmp_path: Path) -> None:
    """心跳过期且归属 worktree 无近期改动（mtime 均在窗口外）：显示 STALE。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-idle")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
    )
    idle_mtime_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    for existing_file_path in linked_worktree_path.rglob("*"):
        if existing_file_path.is_file():
            os.utime(existing_file_path, (idle_mtime_timestamp, idle_mtime_timestamp))

    raw_cell_text = render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("STALE")


def test_fresh_lock_renders_running_without_worktree_activity(tmp_path: Path) -> None:
    """新鲜锁不需要活性佐证：即使归属 worktree 已删除也按 RUNNING 渲染。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree="../wt-long-gone",
    )

    raw_cell_text = render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("RUNNING")


def test_fresh_lock_held_from_main_repo_shows_main_repo_location(tmp_path: Path) -> None:
    """锁归属主仓库（worktree 为空）：位置显示 @主仓库，不显示尚不存在的分支。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree="",
        holder_branch="fcl-sam-to-mex-platform-skill-sync",
    )

    raw_cell_text = render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("RUNNING")
    assert raw_cell_text.endswith("@主仓库")
    assert "fcl-sam-to-mex-platform-skill-sync" not in raw_cell_text


def test_fresh_lock_with_missing_worktree_does_not_advertise_branch(tmp_path: Path) -> None:
    """归属 worktree 目录已消失且分支无处检出：显示归属标签，不显示该分支。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree="../wt-gone",
        holder_branch="feat/avatar-upload",
    )

    raw_cell_text = render_activity_cell(main_repo_path)

    assert raw_cell_text.startswith("RUNNING")
    assert "@../wt-gone" in raw_cell_text
    assert "@feat/avatar-upload" not in raw_cell_text


def test_fresh_lock_location_prefers_actual_worktree_branch(tmp_path: Path) -> None:
    """归属 worktree 仍存在时显示它当前实际检出的分支，而不是锁里的过期分支字段。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-live")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree=os.path.relpath(linked_worktree_path, main_repo_path),
        holder_branch="main",
    )

    raw_cell_text = render_activity_cell(main_repo_path)

    assert "@feat/avatar-upload" in raw_cell_text


def test_fresh_lock_location_uses_branch_checked_out_elsewhere(tmp_path: Path) -> None:
    """归属目录已消失但锁里的分支确实检出在某个 worktree：显示那个真实分支。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-moved")
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_worktree="../wt-old-path",
        holder_branch="feat/avatar-upload",
    )

    raw_cell_text = render_activity_cell(main_repo_path)

    assert "@feat/avatar-upload" in raw_cell_text


def test_hard_gate_with_pending_dependency_shows_blocked(tmp_path: Path) -> None:
    """hard gate + 依赖仍在 tasks/pending：显示 ⛔ blocked by <上游 slug>。"""
    main_repo_path = init_dependency_repo(
        tmp_path / "repo",
        dependency_ref=f"tasks/pending/{UPSTREAM_PRD_NAME}.md",
        gate_type="hard",
    )
    write_upstream_prd(main_repo_path, "pending")

    raw_cell_text = render_deps_cell(main_repo_path)

    assert raw_cell_text == f"⛔ blocked by {UPSTREAM_PRD_SLUG}"


def test_hard_gate_with_archived_dependency_shows_deps_ok(tmp_path: Path) -> None:
    """hard gate + 依赖已归档：显示 ✔ deps ok，不再制造阻塞告警。"""
    main_repo_path = init_dependency_repo(
        tmp_path / "repo",
        dependency_ref=f"tasks/archive/{UPSTREAM_PRD_NAME}.md",
        gate_type="hard",
    )
    write_upstream_prd(main_repo_path, "archive")

    raw_cell_text = render_deps_cell(main_repo_path)

    assert raw_cell_text == "✔ deps ok"


def test_hard_gate_with_dangling_dependency_shows_unknown(tmp_path: Path) -> None:
    """hard gate + 依赖引用的 PRD 两处都不存在：显示 ? <slug>，不猜结论。"""
    main_repo_path = init_dependency_repo(
        tmp_path / "repo",
        dependency_ref="P1-FEAT-20260101-000001-ghost-task",
        gate_type="hard",
    )

    raw_cell_text = render_deps_cell(main_repo_path)

    assert raw_cell_text == "? ghost-task"


def test_hard_gate_with_issue_ref_shows_unknown(tmp_path: Path) -> None:
    """hard gate + Issue 号引用：本地判不了远端状态，显示 ? #<编号>。"""
    main_repo_path = init_dependency_repo(
        tmp_path / "repo",
        dependency_ref="#12345",
        gate_type="hard",
    )

    raw_cell_text = render_deps_cell(main_repo_path)

    assert raw_cell_text == "? #12345"


def test_no_dependency_section_falls_back_to_dash(tmp_path: Path) -> None:
    """无 §8 章节（gate 视为 none）：DEPS 回落 ``-``，不给无依赖 PRD 制造噪声。"""
    main_repo_path = init_main_repo(tmp_path / "repo")

    raw_cell_text = render_deps_cell(main_repo_path)

    assert raw_cell_text == "-"


def test_soft_gate_with_pending_dependency_shows_soft_hint(tmp_path: Path) -> None:
    """soft gate + 依赖未交付：暗色 soft → <slug> 提示顺序，不按 hard 阻塞告警。"""
    main_repo_path = init_dependency_repo(
        tmp_path / "repo",
        dependency_ref=f"tasks/pending/{UPSTREAM_PRD_NAME}.md",
        gate_type="soft",
    )
    write_upstream_prd(main_repo_path, "pending")

    raw_cell_text = render_deps_cell(main_repo_path)

    assert raw_cell_text == f"soft → {UPSTREAM_PRD_SLUG}"


def test_render_prd_table_header_includes_deps_column(tmp_path: Path, capsys) -> None:
    """表头必须含 DEPS 列——依赖可见性的唯一入口，不得被静默移除。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    prd_record = collect_fixture_record(main_repo_path)

    prd_status.render_prd_table(
        [prd_record],
        PLAIN_PALETTE,
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
    main_repo_path = init_main_repo(tmp_path / "repo", prd_text=checklist_prd_text(0, 2))
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-progress")
    (linked_worktree_path / FIXTURE_PRD_RELATIVE_PATH).write_text(
        checklist_prd_text(2, 0), encoding="utf-8"
    )

    assert render_checklist_cell(main_repo_path) == "2/2"


def test_checklist_progress_prefers_branch_archive_copy_over_pending(tmp_path: Path) -> None:
    """分支内同时存在 archive 与 pending 副本时，收尾态（archive）优先。"""
    main_repo_path = init_main_repo(tmp_path / "repo", prd_text=checklist_prd_text(0, 2))
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-both")
    (linked_worktree_path / FIXTURE_PRD_RELATIVE_PATH).write_text(
        checklist_prd_text(1, 1), encoding="utf-8"
    )
    archived_prd_path = linked_worktree_path / "tasks" / "archive" / f"{FIXTURE_PRD_NAME}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text(checklist_prd_text(2, 0), encoding="utf-8")

    assert render_checklist_cell(main_repo_path) == "2/2"


def test_checklist_progress_ignores_unrelated_worktree_copy(tmp_path: Path) -> None:
    """每个 worktree 都带一份未改动的同名副本；只有 slug 匹配的 worktree 才算分支副本。"""
    main_repo_path = init_main_repo(tmp_path / "repo", prd_text=checklist_prd_text(0, 2))
    add_linked_worktree(main_repo_path, "feat/unrelated-thing", "wt-unrelated-copy")
    # 主仓库副本在 worktree 建好之后才被勾选；无关 worktree 里那份仍是 0/2。
    (main_repo_path / FIXTURE_PRD_RELATIVE_PATH).write_text(
        checklist_prd_text(2, 0), encoding="utf-8"
    )

    assert render_checklist_cell(main_repo_path) == "2/2"


def test_checklist_progress_prefers_main_archive_over_branch_pending(tmp_path: Path) -> None:
    """主仓库已归档、分支还压着合并前的 pending 副本：CHECKLIST 取主仓库归档副本。

    真实踩过的坑：PR 已合并（PRD 已进主线 ``tasks/archive``），同名分支 worktree
    还留着合并前那份未勾选的 pending 副本，归档汇总因此显示 ``0/24`` 而非真实的
    ``3/24``。归档是终态，分支副本按定义已过时。
    """
    main_repo_path = init_main_repo(tmp_path / "repo", prd_text=checklist_prd_text(0, 4))
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-merged")
    # 分支副本停留在合并前：同一份 PRD 在这里仍是 0/4。
    (linked_worktree_path / FIXTURE_PRD_RELATIVE_PATH).write_text(
        checklist_prd_text(0, 4), encoding="utf-8"
    )
    main_archive_prd_path = main_repo_path / FIXTURE_PRD_ARCHIVE_RELATIVE_PATH
    main_archive_prd_path.parent.mkdir(parents=True, exist_ok=True)
    main_archive_prd_path.write_text(checklist_prd_text(3, 1), encoding="utf-8")

    assert render_checklist_cell(main_repo_path, FIXTURE_PRD_ARCHIVE_RELATIVE_PATH) == "3/4"


def test_evidence_cell_reads_branch_evidence_dir(tmp_path: Path) -> None:
    """证据写在分支证据目录、主仓库还没有时，EVIDENCE 必须把它显示出来。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-evidence")
    branch_evidence_dir = linked_worktree_path / "tasks" / "evidence" / FIXTURE_PRD_NAME
    branch_evidence_dir.mkdir(parents=True, exist_ok=True)
    (branch_evidence_dir / f"{FIXTURE_PRD_NAME}.verification-plan.md").write_text(
        "# plan\n", encoding="utf-8"
    )
    (branch_evidence_dir / f"{FIXTURE_PRD_NAME}.evidence-report.md").write_text(
        "# report\n", encoding="utf-8"
    )

    raw_cell_text = render_evidence_cell(main_repo_path)

    assert "plan✓" in raw_cell_text
    assert "report✓" in raw_cell_text
    assert "verifier✗" in raw_cell_text


def test_evidence_cell_falls_back_to_main_per_slot(tmp_path: Path) -> None:
    """分支只写了 plan、report 仍在主仓库：两个槽位各自命中，不整体丢弃主仓库文件。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-split")
    main_evidence_dir = main_repo_path / "tasks" / "evidence" / FIXTURE_PRD_NAME
    main_evidence_dir.mkdir(parents=True, exist_ok=True)
    (main_evidence_dir / f"{FIXTURE_PRD_NAME}.evidence-report.md").write_text(
        "# report\n", encoding="utf-8"
    )
    branch_evidence_dir = linked_worktree_path / "tasks" / "evidence" / FIXTURE_PRD_NAME
    branch_evidence_dir.mkdir(parents=True, exist_ok=True)
    (branch_evidence_dir / f"{FIXTURE_PRD_NAME}.verification-plan.md").write_text(
        "# plan\n", encoding="utf-8"
    )

    raw_cell_text = render_evidence_cell(main_repo_path)

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
    main_repo_path = init_main_repo(tmp_path / "repo", prd_text=detailed_prd_text)
    prd_record = collect_fixture_record(main_repo_path)
    render_arguments = (
        [prd_record],
        PLAIN_PALETTE,
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

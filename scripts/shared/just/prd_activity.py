#!/usr/bin/env python3
"""PRD 看板 ACTIVITY 列：执行锁、worktree 与归档态合成的运行态单元格。

ACTIVITY 回答"现在有谁在做这条 PRD"——执行锁（新鲜 / 过期 / 带活性佐证）、分支名
匹配的 worktree、分支是否已归档、近期文件改动，以及已归档待人工验收的记录等了多久。
这些信号来自锁文件、git 与文件系统，和看板的数据模型无关，因此独立成模块；渲染优先级
（分支归档 > 活跃锁 > 待人工 waiting > 过期锁 > 无锁 worktree > 弱信号）是本列的核心
约定，由守卫测试逐条钉死。

本模块不 import ``prd_status``（它依赖本模块）；``PrdRecord`` / ``Palette`` 只在类型标注里
出现，走 ``TYPE_CHECKING``，与 ``prd_detail.py`` 同一做法。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import prd_impact_tree
import prd_locator
import prd_lock

if TYPE_CHECKING:
    from prd_status import Palette, PrdRecord

RECENT_TOUCH_WINDOW_MINUTES = 15


def resolve_lock_location_text(
    worktree_branches_list: list[tuple[str, Path]],
    main_repo_root: Path,
    lock_metadata: dict,
) -> str:
    """把锁归属渲染成 ACTIVITY 列 ``@`` 后的位置文本，绝不显示并不存在的分支。

    主仓库归属（``worktree`` 为空）显示 ``主仓库``；归属 worktree 目录仍存在时显示
    该目录**当前实际检出**的分支；目录已消失但锁里的 ``branch`` 确实还检出在某个
    worktree 上时显示那个分支；两者都不成立时退回展示 ``worktree`` 归属标签。
    锁里的 ``branch`` 只是领锁瞬间的快照（``just implement`` 在主仓库领锁时就会
    写入推导出的分支名），照抄它会给看板用户一个打不开的 ``just worktree -o`` 名称。

    Args:
        worktree_branches_list (list[tuple[str, Path]]): ``(分支名, 目录路径)`` 列表，
            来自 ``prd_lock.list_linked_worktree_branches``。
        main_repo_root (Path): 主仓库根目录。
        lock_metadata (dict): 锁 JSON 内容。

    Returns:
        str: ``@`` 后的位置文本。
    """
    worktree_label_text = str(lock_metadata.get("worktree") or "")
    if not worktree_label_text:
        return "主仓库"

    lock_worktree_path = prd_lock.resolve_lock_worktree_path(main_repo_root, worktree_label_text)
    if lock_worktree_path is not None:
        for branch_name_text, worktree_path in worktree_branches_list:
            if Path(worktree_path).resolve() == lock_worktree_path:
                return branch_name_text

    recorded_branch_text = str(lock_metadata.get("branch") or "")
    if recorded_branch_text:
        recorded_branch_basename_text = recorded_branch_text.rsplit("/", 1)[-1]
        for branch_name_text, _ in worktree_branches_list:
            if branch_name_text == recorded_branch_text or (
                branch_name_text.rsplit("/", 1)[-1] == recorded_branch_basename_text
            ):
                return branch_name_text

    return worktree_label_text


def format_duration_text(elapsed_seconds: float) -> str:
    """把秒数渲染成紧凑时长文本，例如 ``12m`` / ``1h5m`` / ``2d3h``。

    Args:
        elapsed_seconds (float): 时长秒数，负数按 0 处理。

    Returns:
        str: 紧凑时长文本。
    """
    remaining_minutes = max(0, int(elapsed_seconds // 60))
    days_count, remaining_minutes = divmod(remaining_minutes, 60 * 24)
    hours_count, minutes_count = divmod(remaining_minutes, 60)
    if days_count:
        return f"{days_count}d{hours_count}h"
    if hours_count:
        return f"{hours_count}h{minutes_count}m"
    return f"{minutes_count}m"


def detect_recent_touch_minutes(prd_record: PrdRecord, evidence_root: Path) -> int | None:
    """无锁弱信号：PRD 文件或证据目录在最近 15 分钟内有改动时返回距今分钟数。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录。
        evidence_root (Path): ``tasks/evidence`` 目录。

    Returns:
        int | None: 距今分钟数（向下取整）；无近期改动时返回 ``None``。
    """
    candidate_paths_list: list[Path] = [prd_record.prd_path]
    evidence_dir_path = evidence_root / prd_record.prd_path.stem
    if evidence_dir_path.is_dir():
        candidate_paths_list.extend(
            child_path for child_path in evidence_dir_path.rglob("*") if child_path.is_file()
        )

    now_timestamp = datetime.now(timezone.utc).timestamp()
    newest_mtime_timestamp = 0.0
    for candidate_path in candidate_paths_list:
        try:
            newest_mtime_timestamp = max(newest_mtime_timestamp, candidate_path.stat().st_mtime)
        except OSError:
            continue
    if newest_mtime_timestamp <= 0:
        return None

    elapsed_minutes = int((now_timestamp - newest_mtime_timestamp) // 60)
    if elapsed_minutes <= RECENT_TOUCH_WINDOW_MINUTES:
        return elapsed_minutes
    return None


def resolve_archive_landed_timestamp(prd_path: Path) -> float | None:
    """推断一条已归档 PRD 落进 ``tasks/archive`` 的时间点，度量"等人验收多久了"。

    优先取把该文件加到此路径的那次提交的提交时间：rebase / squash 合并都会把它刷成
    合并时刻，且不受检出或 touch 影响，比 mtime 稳。文件未被 git 追踪、git 不可用或查不
    到时回退到文件 mtime。

    Args:
        prd_path (Path): 主仓库 ``tasks/archive`` 下的 PRD 文件路径。

    Returns:
        float | None: Unix 时间戳；git 与文件系统都取不到时返回 ``None``。
    """
    landed_timestamp_lines = prd_impact_tree.run_git_lines(
        prd_path.parent, "log", "-1", "--diff-filter=A", "--format=%ct", "--", prd_path.name
    )
    if landed_timestamp_lines and landed_timestamp_lines[0].isdigit():
        return float(landed_timestamp_lines[0])
    try:
        return prd_path.stat().st_mtime
    except OSError:
        return None


def format_waiting_text(prd_record: PrdRecord) -> str | None:
    """已在主仓库归档、横幅为 ``🧍 待人工验收`` 的记录：渲染"等人验收了多久"。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录。

    Returns:
        str | None: 形如 ``🧍 waiting 3d0h`` 的文本；记录不是"已在主仓库归档且待人工"，
        或归档落地时间取不到时返回 ``None``，调用方继续按其余信号渲染。
    """
    if not (
        prd_locator.is_main_repo_archived(prd_record.prd_path) and prd_record.awaits_human_review
    ):
        return None
    archive_landed_timestamp = resolve_archive_landed_timestamp(prd_record.prd_path)
    if archive_landed_timestamp is None:
        return None
    waiting_seconds = datetime.now(timezone.utc).timestamp() - archive_landed_timestamp
    return f"🧍 waiting {format_duration_text(waiting_seconds)}"


def format_activity_cell(
    prd_record: PrdRecord, main_repo_root: Path, evidence_root: Path, palette: Palette
) -> str:
    """格式化运行态列（ACTIVITY）。

    分支归档优先于一切锁信号：匹配 worktree 的 ``tasks/archive`` 里已有该 PRD 时，
    无论锁是新鲜、过期还是带活性佐证的 RUNNING，一律显示绿色
    ``✔ branch-archived @<branch> · awaiting merge``——收尾已在分支完成，缺的只是
    合并回主线，残留锁把已完成的 PRD 渲染成 RUNNING / STALE 会误导人重新执行。
    清单进度不在这里重复携带：它由 CHECKLIST 列呈现，那一列同样读分支副本。

    **但记录本身已来自主仓库 ``tasks/archive`` 时不看分支**：合并早已完成，
    ``awaiting merge`` 指向一个不存在的待办，``⚠ unlocked`` 也只是噪声。
    其余情况按锁状态渲染：新鲜锁 → 黄色
    ``RUNNING <tool> <时长> @<位置>``，位置由 ``resolve_lock_location_text`` 按实际
    状态解析（主仓库归属显示 ``主仓库``，归属 worktree 显示其当前实际检出的分支，
    目录已消失且分支无处检出时显示归属标签），不照抄锁里的 ``branch`` 快照；过期锁但归属
    worktree 仍有近期改动 → 同样按 RUNNING 渲染（心跳只是兜底信号，活性佐证说明
    会话仍在执行）；过期锁且无活性佐证 → 红色 ``STALE <最后心跳>``；无锁但存在
    分支名匹配的 worktree 且其中未归档该 PRD → 黄色 ``⚠ unlocked @<branch>``
    （互斥未生效的漏洞必须摆到台面上，而不是静默显示 ``-``）；无锁但 PRD 文件或
    证据目录近期有改动 → 暗色 ``⚡ active <n>m ago``；其余 ``-``。

    已在主仓库归档、横幅为 ``🧍 待人工验收`` 的记录：执行侧早已交付，验收轴上只剩"等人
    确认"，因此在没有活跃锁时显示黄色 ``🧍 waiting <时长>``——自归档落地起算，让积压
    多久一眼可见；有活跃锁时仍显示锁状态（有人正在重开或回填，比"在等"更要紧）。过期锁
    （无活性佐证）不盖住"在等"：渲染成 ``🧍 waiting <时长> · stale lock``，否则验收积压了
    几天的记录在看板上只剩一个红色的 STALE，欠人的那笔账就看不见了。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录。
        main_repo_root (Path): 主仓库根目录。
        evidence_root (Path): ``tasks/evidence`` 目录。
        palette (Palette): 颜色包装器。

    Returns:
        str: 运行态单元格文本。
    """
    # worktree 列表由本函数自行获取（而不是塞进 PrdRecord）：resolve_lock_location_text
    # 需要完整列表来判断锁里的分支是否真的检出在某处，PrdRecord 只该承载单条 PRD 的
    # 事实。看板一次渲染只有个位数 pending 行，多一次 git 调用的代价可忽略。
    linked_worktree_branches_list = prd_lock.list_linked_worktree_branches(main_repo_root)
    matched_worktree = (
        None
        if prd_locator.is_main_repo_archived(prd_record.prd_path)
        else prd_locator.match_worktree_for_prd(
            linked_worktree_branches_list, prd_record.slug, prd_record.prd_path
        )
    )
    if matched_worktree is not None:
        branch_name_text, worktree_path = matched_worktree
        worktree_archive_prd_path = worktree_path / "tasks" / "archive" / prd_record.prd_path.name
        if worktree_archive_prd_path.is_file():
            # worktree 内 tasks/archive 已有这条 PRD：收尾已在分支上完成，主线缺的
            # 只是合并动作。归档是比锁更强的交付事实，判定因此排在锁之前：残留锁
            # （无论新鲜还是过期）把已完成的 PRD 渲染成 RUNNING / STALE，都会误导
            # 人重新执行一个已完成的任务。
            return palette.green(f"✔ branch-archived @{branch_name_text} · awaiting merge")

    lock_snapshot = prd_lock.inspect_prd_lock(main_repo_root, prd_record.prd_path.stem)
    if lock_snapshot.state in ("fresh", "stale"):
        lock_metadata = lock_snapshot.metadata
        lock_is_active = lock_snapshot.state == "fresh"
        if not lock_is_active:
            lock_is_active = prd_lock.lock_worktree_has_recent_activity(
                main_repo_root, lock_metadata
            )
        if lock_is_active:
            raw_tool_text = str(lock_metadata.get("ai_tool") or "unknown")
            raw_location_text = resolve_lock_location_text(
                linked_worktree_branches_list, main_repo_root, lock_metadata
            )
            raw_started_text = lock_metadata.get("started_at")
            started_moment = prd_lock.parse_lock_timestamp(raw_started_text)
            if started_moment is not None:
                elapsed_seconds = (datetime.now(timezone.utc) - started_moment).total_seconds()
            else:
                elapsed_seconds = 0.0
            raw_duration_text = format_duration_text(elapsed_seconds)
            raw_running_text = f"RUNNING {raw_tool_text} {raw_duration_text} @{raw_location_text}"
            return palette.yellow(raw_running_text)
        waiting_text = format_waiting_text(prd_record)
        if waiting_text is not None:
            return palette.yellow(f"{waiting_text} · stale lock")
        raw_heartbeat_text = str(lock_metadata.get("heartbeat_at") or "?")
        return palette.red(f"STALE {raw_heartbeat_text}")

    waiting_text = format_waiting_text(prd_record)
    if waiting_text is not None:
        return palette.yellow(waiting_text)

    if matched_worktree is not None:
        branch_name_text, worktree_path = matched_worktree
        worktree_activity_minutes = prd_lock.detect_worktree_activity_minutes(
            worktree_path, RECENT_TOUCH_WINDOW_MINUTES
        )
        raw_activity_suffix_text = (
            f" · active {worktree_activity_minutes}m ago"
            if worktree_activity_minutes is not None
            else ""
        )
        return palette.yellow(f"⚠ unlocked @{branch_name_text}{raw_activity_suffix_text}")

    recent_touch_minutes = detect_recent_touch_minutes(prd_record, evidence_root)
    if recent_touch_minutes is not None:
        return palette.dim(f"⚡ active {recent_touch_minutes}m ago")
    return palette.dim("-")

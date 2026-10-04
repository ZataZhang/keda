#!/usr/bin/env python3
"""PRD 状态看板。

扫描 ``tasks/pending`` 与 ``tasks/archive``，汇总每条 PRD 的优先级、类型、
创建时间、验收清单勾选进度、影响树触达进度（FILES 列）、证据包状态、交付依赖
（DEPS 列）与执行锁运行态（ACTIVITY 列），供开工前判断哪些尚未交付、哪些被上游
依赖挡住、哪些正被其他会话执行。

模块分工（一列一个关注点，本文件只管记录模型、CHECKLIST / FILES / EVIDENCE 三列、
表格渲染与分区）：

- ``prd_locator.py``：文件名解析、仓库根、worktree 匹配、分支副本与证据文件定位；
- ``prd_acceptance.py``：验收状态横幅与清单勾选计数（prd skill 契约解析的同步副本）；
- ``prd_deps.py``：DEPS 列；``prd_activity.py``：ACTIVITY 列；
- ``prd_impact_tree.py``：FILES 列；``prd_detail.py``：``--detail`` 摘要块。

目录轴与验收轴相互正交：``tasks/archive`` 只代表执行侧交付已完成，人工验收可以晚于
归档。因此看板在 ``PENDING``（未开工 / 进行中）与 ``ARCHIVE`` 之外另列一段
``AWAITING HUMAN``——看板读到的那份 PRD 副本已在 ``tasks/archive``（主仓库里，或分支
worktree 里已归档、只差合并），且验收状态横幅为 ``🧍 待人工验收``，才算"机器证据齐备、
只等人确认"，并从各自的目录分区里摘出，免得归档月视图把它们尚未勾选的
Human-Confirmed 项当成异常。分区只做呈现，不搬文件。

**落在 ``tasks/pending`` 的副本即使横幅写着 🧍 也不进这一段。** 按生命周期，归档与横幅
翻转在同一次交付里完成；pending 里的 🧍 只可能是重开后忘了复位横幅、或翻了横幅还没
``git mv``——此时执行侧的活可能还没做完，必须留在 PENDING 里被看见，不能让一个陈旧的横幅
把它从待办里摘走。同名 PRD（按文件 stem）在 pending 与 archive 并存时以 pending 副本为准，
archive 那份不入该段、留在 ARCHIVE（月视图把它没勾完的空框报成 ⚠ 异常）。

进度与证据按**分支副本优先**读取：执行发生在 worktree 里，主仓库的
``tasks/pending`` 副本与证据目录要等合并回主线才更新；存在分支名匹配的 worktree
时取它内部的 ``tasks/archive`` → ``tasks/pending`` 副本，无匹配时回落主仓库副本。
横幅同源，同样读分支副本。

**但主仓库已是归档副本时不看分支**：``tasks/archive`` 是终态，说明收尾早已在主线上
完成，分支里那份 ``tasks/pending`` 只是合并前的残留快照。若继续让它遮蔽，一条已归档
的 PRD 会被显示成合并前的进度（常见为 ``0/N``），ACTIVITY 还会报一个早已不存在的
``awaiting merge``。

用法::

    python3 scripts/shared/just/prd_status.py [all|pending|archive] [--detail]

``--detail`` 在每个 PRD 行下追加标题与摘要块（解析见 ``prd_detail.py``）。

清单进度、影响树触达进度、依赖满足与 verifier 结论均为从文件内容推断的启发式
结果，看板只用于快速定位，最终判断以 PRD 正文与证据文件原文为准。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import prd_acceptance
import prd_activity
import prd_deps
import prd_detail
import prd_impact_tree
import prd_locator
import prd_lock

STRONG_VERDICT_PATTERN = re.compile(
    r"^\s*[-*>#\s]*(?:VERDICT|最终结论|结论)\s*[:：]\s*\**\s*(PASS|REJECT)\b",
    re.MULTILINE,
)
STANDALONE_VERDICT_PATTERN = re.compile(
    r"^\s*[-*>#\s]*\**\s*(PASS|REJECT)\**\s*(?:[（(].*)?$",
    re.MULTILINE,
)
ANY_VERDICT_PATTERN = re.compile(r"\b(PASS|REJECT)\b")


@dataclass
class PrdRecord:
    """单条 PRD 的看板信息。

    Attributes:
        prd_path (Path): PRD 文件路径。
        bucket (str): 所在分组，``pending`` 或 ``archive``。
        priority (str): 优先级标签，例如 ``P1``；旧式命名无前缀时为空串。
        kind (str): 类型标签，例如 ``FEAT``；旧式命名无类型时为空串。
        created (str): 创建日期 ``YYYY-MM-DD``，无法从文件名解析时为空串。
        slug (str): 文件名去掉前缀与扩展名后的短标识，用于快速定位文件。
        checklist_checked (int): 已勾选验收项数量。
        checklist_total (int): 验收项总数；无清单时为 0。
        has_verification_plan (bool): 是否存在验证计划文件。
        has_evidence_report (bool): 是否存在证据报告文件。
        verifier_verdict (str): verifier 结论，``PASS`` / ``REJECT`` / 空串。
        verifier_verdict_certain (bool): 结论是否来自显式结论行而非散文推断。
        dependency_gate (str): §8 声明的 gate 类型，``none`` / ``soft`` / ``hard``
            / ``""``（无 §8 章节或未声明）。
        dependency_refs (tuple[str, ...]): §8 ``Depends on tasks/issues`` 下的
            原始引用 token（``none`` 会被解析阶段剔除）。
        impact_progress (prd_impact_tree.ImpactProgress | None): Change Impact Tree
            的分支触达进度；无分支 worktree 可比对或 PRD 没写影响树时为 ``None``。
        acceptance_status (str): 验收状态横幅的投影值，``"not_started"`` /
            ``"awaiting_human"`` / ``"accepted"``；无横幅或状态词无法识别时为空串。
        source_is_archived (bool): 看板读取内容的那份副本是否位于 ``tasks/archive``——
            主仓库里的归档副本，或分支 worktree 里已归档、只差合并的副本。它与 ``bucket``
            （主仓库里的目录）是两回事：``bucket`` 为 ``pending`` 的记录，其分支副本
            可能已经归档。
        title (str): PRD 一级标题（首个 ``# `` 行），无标题时为空串。
        summary_lines (tuple[str, ...]): ``--detail`` 摘要行（已截断）；与清单进度
            同源的分支副本正文，无可用正文时为空元组。
    """

    prd_path: Path
    bucket: str
    priority: str
    kind: str
    created: str
    slug: str
    checklist_checked: int
    checklist_total: int
    has_verification_plan: bool
    has_evidence_report: bool
    verifier_verdict: str
    verifier_verdict_certain: bool
    dependency_gate: str
    dependency_refs: tuple[str, ...]
    impact_progress: prd_impact_tree.ImpactProgress | None
    acceptance_status: str
    source_is_archived: bool
    title: str
    summary_lines: tuple[str, ...]

    @property
    def checklist_complete(self) -> bool:
        """验收清单是否已全部勾选；无清单时视为不完整。"""
        return self.checklist_total > 0 and self.checklist_checked == self.checklist_total

    @property
    def awaits_human_review(self) -> bool:
        """是否"机器证据齐备、只等人确认"：读到的副本已归档，且横幅为 ``🧍 待人工验收``。

        两个条件缺一不可。归档与翻横幅在同一次交付里完成，所以 pending 副本上的 🧍
        是陈旧信号（重开后忘了复位，或翻了横幅还没归档），执行侧的活可能还没做完，
        不能据此把它从待办里摘走。
        """
        return (
            self.source_is_archived
            and self.acceptance_status == prd_acceptance.ACCEPTANCE_STATUS_AWAITING_HUMAN
        )


def parse_verifier_verdict(verifier_report_text: str) -> tuple[str, bool]:
    """从 verifier 报告中推断最终结论。

    报告存在"先 REJECT、修复后复审 PASS"的多段结构，因此取最后一次出现的结论。
    显式结论行（``VERDICT:`` / ``结论：``）优先，其次整行独立的 ``PASS``/``REJECT``，
    最后才回退到散文中的任意出现。

    Args:
        verifier_report_text (str): verifier 报告全文。

    Returns:
        tuple[str, bool]: ``(结论, 是否来自显式结论行)``；无任何匹配时返回
        ``("", False)``。
    """
    for verdict_pattern in (STRONG_VERDICT_PATTERN, STANDALONE_VERDICT_PATTERN):
        matched_verdicts_list = verdict_pattern.findall(verifier_report_text)
        if matched_verdicts_list:
            return matched_verdicts_list[-1].upper(), True

    fallback_verdicts_list = ANY_VERDICT_PATTERN.findall(verifier_report_text)
    if fallback_verdicts_list:
        return fallback_verdicts_list[-1].upper(), False
    return "", False


def collect_prd_record(
    prd_path: Path, evidence_root: Path, worktree_branches_list: list[tuple[str, Path]]
) -> PrdRecord:
    """读取单个 PRD 文件与对应证据目录，生成看板记录。

    进度、依赖与证据都优先取**分支副本**：执行发生在 worktree 里，主仓库的
    ``tasks/pending`` 副本与证据目录要等合并回主线才会更新，只读它们会让"分支上
    早已勾完、看板仍显示 0/21"的状态长期挂着。worktree 按 slug 与分支名匹配
    （``match_worktree_by_slug``）；每个 worktree 都带一份未改动的同名 pending
    副本，因此无匹配时绝不能拿别的 worktree 的副本充数。

    例外是**本记录已经来自主仓库 ``tasks/archive``**：归档是终态，分支副本按定义
    已过时（同名 worktree 往往还压着合并前的 pending 快照，继续采信会把它显示成
    ``0/N``）。此时统一回主仓库副本，清单、横幅、依赖、证据与影响树同源。

    Args:
        prd_path (Path): 主仓库内的 PRD 文件路径。
        evidence_root (Path): 主仓库 ``tasks/evidence`` 目录。
        worktree_branches_list (list[tuple[str, Path]]): ``(分支名, 目录路径)`` 列表，
            来自 ``prd_lock.list_linked_worktree_branches``。调用方必须显式传入，
            不留默认值——漏传会让看板静默退回主仓库副本，正是要修的那个 bug。

    Returns:
        PrdRecord: 该 PRD 的看板信息。
    """
    raw_priority_text, raw_kind_text, formatted_created_date, raw_slug_text = (
        prd_locator.parse_prd_filename(prd_path)
    )
    matched_worktree = prd_locator.match_worktree_for_prd(
        worktree_branches_list, raw_slug_text, prd_path
    )
    worktree_path = matched_worktree[1] if matched_worktree is not None else None
    # 主仓库已有归档副本 ⇒ 收尾已在主线完成，脏掉分支匹配，让下文所有取数统一
    # 回主仓库副本。分支上残留的 pending 快照若继续遮蔽，看板会把一条已归档的
    # PRD 显示成合并前的进度（真实案例：显示 0/24，实际 3/24）。
    if prd_locator.is_main_repo_archived(prd_path):
        worktree_path = None

    source_prd_path = prd_locator.resolve_branch_prd_path(worktree_path, prd_path.name) or prd_path
    raw_prd_text = source_prd_path.read_text(encoding="utf-8")
    checked_item_count, checklist_item_count = prd_acceptance.count_checklist_items(raw_prd_text)
    acceptance_status_text = prd_acceptance.parse_acceptance_status(raw_prd_text)
    raw_gate_text, raw_dependency_refs_tuple = prd_deps.parse_delivery_dependencies(raw_prd_text)
    parsed_title_text, parsed_summary_lines = prd_detail.extract_prd_title_and_summary(raw_prd_text)

    evidence_dirs_list = prd_locator.resolve_evidence_dirs(
        evidence_root / prd_path.stem, worktree_path
    )
    verification_plan_path = prd_locator.find_evidence_file_in_dirs(
        evidence_dirs_list, "verification-plan"
    )
    evidence_report_path = prd_locator.find_evidence_file_in_dirs(
        evidence_dirs_list, "evidence-report"
    )
    verifier_report_path = prd_locator.find_evidence_file_in_dirs(
        evidence_dirs_list, "verifier-report"
    )

    verifier_verdict_text = ""
    is_verdict_certain = False
    if verifier_report_path is not None:
        verifier_verdict_text, is_verdict_certain = parse_verifier_verdict(
            verifier_report_path.read_text(encoding="utf-8")
        )

    # 触达进度要和分支的实际改动比对，只有匹配到 worktree 时才谈得上测量；
    # 主仓库副本永远是"开工前的样子"，在那里比对只会恒等于 0。
    impact_progress = (
        prd_impact_tree.measure_branch_impact_progress(raw_prd_text, worktree_path)
        if worktree_path is not None
        else None
    )

    return PrdRecord(
        prd_path=prd_path,
        bucket=prd_path.parent.name,
        priority=raw_priority_text,
        kind=raw_kind_text,
        created=formatted_created_date,
        slug=raw_slug_text,
        checklist_checked=checked_item_count,
        checklist_total=checklist_item_count,
        has_verification_plan=verification_plan_path is not None,
        has_evidence_report=evidence_report_path is not None,
        verifier_verdict=verifier_verdict_text,
        verifier_verdict_certain=is_verdict_certain,
        dependency_gate=raw_gate_text,
        dependency_refs=raw_dependency_refs_tuple,
        impact_progress=impact_progress,
        acceptance_status=acceptance_status_text,
        source_is_archived=source_prd_path.parent.name == prd_locator.ARCHIVE_BUCKET_DIR_NAME,
        title=parsed_title_text,
        summary_lines=parsed_summary_lines,
    )


class Palette:
    """终端颜色包装；非交互输出或设置 ``NO_COLOR`` 时全部退化为原字符串。"""

    def __init__(self, enabled: bool) -> None:
        """初始化调色板。

        Args:
            enabled (bool): 是否启用 ANSI 颜色。
        """
        self._enabled = enabled

    def wrap(self, raw_text: str, ansi_code: str) -> str:
        """按需为文本套上 ANSI 颜色码。

        Args:
            raw_text (str): 原始文本。
            ansi_code (str): ANSI 颜色码，例如 ``"32"``。

        Returns:
            str: 启用颜色时返回带颜色码的文本，否则原样返回。
        """
        if not self._enabled:
            return raw_text
        return f"\033[{ansi_code}m{raw_text}\033[0m"

    def green(self, raw_text: str) -> str:
        """绿色，用于完成状态。"""
        return self.wrap(raw_text, "32")

    def yellow(self, raw_text: str) -> str:
        """黄色，用于需要注意的未完成状态。"""
        return self.wrap(raw_text, "33")

    def red(self, raw_text: str) -> str:
        """红色，用于明确的问题状态。"""
        return self.wrap(raw_text, "31")

    def dim(self, raw_text: str) -> str:
        """灰色，用于缺失或占位信息。"""
        return self.wrap(raw_text, "2")

    def bold(self, raw_text: str) -> str:
        """加粗，用于分组标题。"""
        return self.wrap(raw_text, "1")


def format_checklist_cell(prd_record: PrdRecord, palette: Palette) -> str:
    """格式化验收清单进度列。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录。
        palette (Palette): 颜色包装器。

    Returns:
        str: 形如 ``3/12`` 的进度文本；无清单时返回 ``-``。
    """
    if prd_record.checklist_total == 0:
        return palette.dim("-")

    raw_progress_text = f"{prd_record.checklist_checked}/{prd_record.checklist_total}"
    if prd_record.checklist_complete:
        return palette.green(raw_progress_text)
    return palette.yellow(raw_progress_text)


def format_impact_cell(prd_record: PrdRecord, palette: Palette) -> str:
    """格式化影响树触达进度列。

    ``~`` 前缀与"永不转绿"是刻意的：触达进度只说明分支碰过影响树里的哪些文件，
    既不证明改对了，也不因为影响树自称"起点而非穷尽清单"而能代表全部工作量。
    看板上的强信号链是 CHECKLIST → EVIDENCE → verifier，本列不参与其中，用绿色
    会让人误读成"已完成"。``?n`` 后缀披露本地无法判定、因而未计入分母的节点数
    （跨仓库路径、花括号展开、通配符），宁可少算也不编一个好看的分母。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录。
        palette (Palette): 颜色包装器。

    Returns:
        str: 形如 ``~7/12`` 或 ``~7/12?2`` 的进度文本；无分支可比对、PRD 没写影响树
        或树内没有可判定节点时返回 ``-``。
    """
    impact_progress = prd_record.impact_progress
    if impact_progress is None or impact_progress.judgeable_total == 0:
        return palette.dim("-")

    raw_progress_text = f"~{impact_progress.touched_total}/{impact_progress.judgeable_total}"
    if impact_progress.unresolvable_total:
        raw_progress_text += f"?{impact_progress.unresolvable_total}"
    if impact_progress.touched_total == 0:
        return palette.dim(raw_progress_text)
    return palette.yellow(raw_progress_text)


def format_evidence_cell(prd_record: PrdRecord, palette: Palette) -> str:
    """格式化证据包状态列。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录。
        palette (Palette): 颜色包装器。

    Returns:
        str: 形如 ``plan✓ report✓ verifier:PASS`` 的状态文本；证据目录为空时返回 ``-``。
    """
    if not (
        prd_record.has_verification_plan
        or prd_record.has_evidence_report
        or prd_record.verifier_verdict
    ):
        return palette.dim("-")

    raw_evidence_parts_list: list[str] = []
    raw_evidence_parts_list.append(
        f"plan{palette.green('✓') if prd_record.has_verification_plan else palette.dim('✗')}"
    )
    raw_evidence_parts_list.append(
        f"report{palette.green('✓') if prd_record.has_evidence_report else palette.dim('✗')}"
    )
    if prd_record.verifier_verdict:
        raw_verdict_suffix_text = "" if prd_record.verifier_verdict_certain else "?"
        raw_verdict_text = f"verifier:{prd_record.verifier_verdict}{raw_verdict_suffix_text}"
        raw_evidence_parts_list.append(
            palette.red(raw_verdict_text)
            if prd_record.verifier_verdict == "REJECT"
            else palette.green(raw_verdict_text)
        )
    else:
        raw_evidence_parts_list.append(f"verifier{palette.dim('✗')}")
    return " ".join(raw_evidence_parts_list)


ANSI_ESCAPE_PATTERN = re.compile(r"\033\[[0-9;]*m")


def visible_width(raw_text: str) -> int:
    """计算忽略 ANSI 颜色码后的显示宽度。

    Args:
        raw_text (str): 可能含颜色码的文本。

    Returns:
        int: 不含颜色码时的字符长度。
    """
    return len(ANSI_ESCAPE_PATTERN.sub("", raw_text))


def pad_to_width(raw_text: str, target_width: int) -> str:
    """按显示宽度右侧补空格。

    Args:
        raw_text (str): 待补齐文本。
        target_width (int): 目标显示宽度。

    Returns:
        str: 补齐后的文本。
    """
    return raw_text + " " * max(0, target_width - visible_width(raw_text))


def render_prd_table(
    prd_records_list: list[PrdRecord],
    palette: Palette,
    main_repo_root: Path,
    evidence_root: Path,
    pending_dir: Path,
    archive_dir: Path,
    *,
    show_detail: bool = False,
) -> None:
    """输出逐条 PRD 的对齐表格（含 DEPS 依赖列与 ACTIVITY 运行态列）。

    Args:
        prd_records_list (list[PrdRecord]): 待输出的 PRD 记录。
        palette (Palette): 颜色包装器。
        main_repo_root (Path): 主仓库根目录，用于查询执行锁。
        evidence_root (Path): ``tasks/evidence`` 目录，用于弱信号探测。
        pending_dir (Path): ``tasks/pending`` 目录，用于依赖交付状态判定。
        archive_dir (Path): ``tasks/archive`` 目录，用于依赖交付状态判定。
        show_detail (bool): 为真时在每个 PRD 行下方追加标题与描述摘要块。
    """
    if not prd_records_list:
        print(palette.dim("  (无)"))
        return

    raw_priority_cells_list = [prd_record.priority or "-" for prd_record in prd_records_list]
    raw_kind_cells_list = [prd_record.kind or "-" for prd_record in prd_records_list]
    raw_created_cells_list = [prd_record.created or "-" for prd_record in prd_records_list]
    raw_slug_cells_list = [prd_record.slug for prd_record in prd_records_list]
    raw_activity_cells_list = [
        prd_activity.format_activity_cell(prd_record, main_repo_root, evidence_root, palette)
        for prd_record in prd_records_list
    ]
    # FILES 单元格已带颜色，宽度要按可见宽度算；节点多的 PRD 会出现 ``~14/24?12``
    # 这种 9 字符形态，写死列宽会把右边的 EVIDENCE 列挤歪。
    raw_impact_cells_list = [
        format_impact_cell(prd_record, palette) for prd_record in prd_records_list
    ]

    priority_column_width = max(len("PR"), *(len(cell) for cell in raw_priority_cells_list))
    kind_column_width = max(len("TYPE"), *(len(cell) for cell in raw_kind_cells_list))
    created_column_width = max(len("CREATED"), *(len(cell) for cell in raw_created_cells_list))
    slug_column_width = max(len("PRD"), *(len(cell) for cell in raw_slug_cells_list))
    impact_column_width = max(
        len("FILES"), *(visible_width(cell) for cell in raw_impact_cells_list)
    )

    raw_header_text = "  " + "  ".join(
        [
            pad_to_width("PR", priority_column_width),
            pad_to_width("TYPE", kind_column_width),
            pad_to_width("CREATED", created_column_width),
            pad_to_width("PRD", slug_column_width),
            "CHECKLIST",
            pad_to_width("FILES", impact_column_width),
            "EVIDENCE",
            "DEPS",
            "ACTIVITY",
        ]
    )
    print(palette.dim(raw_header_text))

    for (
        prd_record,
        raw_priority_cell,
        raw_kind_cell,
        raw_created_cell,
        raw_slug_cell,
        raw_impact_cell,
        raw_activity_cell,
    ) in zip(
        prd_records_list,
        raw_priority_cells_list,
        raw_kind_cells_list,
        raw_created_cells_list,
        raw_slug_cells_list,
        raw_impact_cells_list,
        raw_activity_cells_list,
    ):
        raw_row_text = "  " + "  ".join(
            [
                pad_to_width(palette.bold(raw_priority_cell), priority_column_width),
                pad_to_width(raw_kind_cell, kind_column_width),
                pad_to_width(raw_created_cell, created_column_width),
                pad_to_width(raw_slug_cell, slug_column_width),
                pad_to_width(format_checklist_cell(prd_record, palette), 9),
                pad_to_width(raw_impact_cell, impact_column_width),
                format_evidence_cell(prd_record, palette),
                prd_deps.format_deps_cell(prd_record, pending_dir, archive_dir, palette),
                raw_activity_cell,
            ]
        )
        print(raw_row_text.rstrip())
        if show_detail:
            prd_detail.print_prd_detail_block(prd_record, palette)


def render_archive_months(archive_records_list: list[PrdRecord], palette: Palette) -> None:
    """按月份折叠输出 archive 概览。

    Args:
        archive_records_list (list[PrdRecord]): archive 分组下的 PRD 记录。
        palette (Palette): 颜色包装器。
    """
    records_by_month_dict: dict[str, list[PrdRecord]] = {}
    for prd_record in archive_records_list:
        raw_month_text = prd_record.created[:7] if prd_record.created else "未知日期"
        records_by_month_dict.setdefault(raw_month_text, []).append(prd_record)

    for raw_month_text in sorted(records_by_month_dict, reverse=True):
        monthly_records_list = records_by_month_dict[raw_month_text]
        with_checklist_records_list = [
            prd_record for prd_record in monthly_records_list if prd_record.checklist_total > 0
        ]
        incomplete_records_list = [
            prd_record
            for prd_record in with_checklist_records_list
            if not prd_record.checklist_complete
        ]
        evidenced_records_list = [
            prd_record
            for prd_record in monthly_records_list
            if prd_record.has_evidence_report or prd_record.verifier_verdict
        ]
        reject_records_list = [
            prd_record
            for prd_record in monthly_records_list
            if prd_record.verifier_verdict == "REJECT"
        ]

        raw_summary_parts_list = [
            f"{len(monthly_records_list)} 条",
            f"清单完成 {len(with_checklist_records_list) - len(incomplete_records_list)}"
            f"/{len(with_checklist_records_list)}",
        ]
        if evidenced_records_list:
            raw_summary_parts_list.append(f"有证据包 {len(evidenced_records_list)}")
        if reject_records_list:
            raw_summary_parts_list.append(
                palette.red(f"verifier REJECT {len(reject_records_list)}")
            )

        raw_month_line_text = f"  {raw_month_text}  " + " · ".join(raw_summary_parts_list)
        if incomplete_records_list:
            print(palette.yellow(raw_month_line_text + "  ⚠ 有未勾完的清单"))
            for prd_record in incomplete_records_list:
                print(
                    palette.yellow(
                        "      - "
                        f"{prd_record.slug}  "
                        f"{prd_record.checklist_checked}/{prd_record.checklist_total}"
                    )
                )
        else:
            print(palette.green(raw_month_line_text))


def print_bucket_section(
    raw_title_text: str,
    bucket_records_list: list[PrdRecord],
    raw_relative_dir_text: str,
    palette: Palette,
    collapsed_months: bool,
    main_repo_root: Path,
    evidence_root: Path,
    pending_dir: Path,
    archive_dir: Path,
    *,
    show_detail: bool = False,
) -> None:
    """输出单个分组的小标题与内容。

    Args:
        raw_title_text (str): 分组名称，例如 ``PENDING``。
        bucket_records_list (list[PrdRecord]): 该分组的 PRD 记录。
        raw_relative_dir_text (str): 该分组对应的目录，例如 ``tasks/pending``。
        palette (Palette): 颜色包装器。
        collapsed_months (bool): 为真时 archive 按月折叠，为假时逐条列出。
        main_repo_root (Path): 主仓库根目录，用于查询执行锁。
        evidence_root (Path): ``tasks/evidence`` 目录，用于弱信号探测。
        pending_dir (Path): ``tasks/pending`` 目录，用于依赖交付状态判定。
        archive_dir (Path): ``tasks/archive`` 目录，用于依赖交付状态判定。
        show_detail (bool): 为真时在每个 PRD 行下方追加标题与描述摘要块。
    """
    print(
        palette.bold(f"{raw_title_text} ({len(bucket_records_list)})")
        + palette.dim(f"  — {raw_relative_dir_text}")
    )
    print()
    if collapsed_months:
        render_archive_months(bucket_records_list, palette)
    else:
        render_prd_table(
            bucket_records_list,
            palette,
            main_repo_root,
            evidence_root,
            pending_dir,
            archive_dir,
            show_detail=show_detail,
        )
    print()


def split_awaiting_human_records(
    pending_records_list: list[PrdRecord], archive_records_list: list[PrdRecord]
) -> tuple[list[PrdRecord], list[PrdRecord], list[PrdRecord]]:
    """把两个目录的记录按验收轴分流：待人工验收单列一段，其余各归各的目录。

    目录轴（pending / archive）与验收轴（横幅）相互正交：归档只代表执行侧交付完成，
    人工验收可以晚于归档。待人工验收的记录因此从两个目录里汇总成一段待办——它既是
    「等你看」，又不该淹没可开工项，也不该让归档月视图把还没勾的 Human-Confirmed 项
    当成异常报 ⚠。入段后不再出现在各自的目录分区里。

    同名 PRD（按文件 stem）在 pending 与 archive 并存本身就是异常：以 pending 副本为准，
    archive 那份不入 AWAITING HUMAN、留在 ARCHIVE（月视图会把它没勾完的空框报成 ⚠），
    免得去验收一条已经重开的 PRD。因此一个 stem 在该段里至多出现一次。

    Args:
        pending_records_list (list[PrdRecord]): ``tasks/pending`` 下的记录。
        archive_records_list (list[PrdRecord]): ``tasks/archive`` 下的记录。

    Returns:
        tuple[list[PrdRecord], list[PrdRecord], list[PrdRecord]]:
        ``(待人工验收, 其余 pending, 其余 archive)``；待人工验收段按文件名排序（优先级
        前缀在前），与 PENDING 的排序口径一致。
    """
    pending_stems_set = {prd_record.prd_path.stem for prd_record in pending_records_list}
    awaiting_review_records_list = sorted(
        [
            *(prd_record for prd_record in pending_records_list if prd_record.awaits_human_review),
            *(
                prd_record
                for prd_record in archive_records_list
                if prd_record.awaits_human_review
                and prd_record.prd_path.stem not in pending_stems_set
            ),
        ],
        key=lambda prd_record: prd_record.prd_path.name,
    )
    claimed_paths_set = {prd_record.prd_path for prd_record in awaiting_review_records_list}
    actionable_pending_records_list = [
        prd_record
        for prd_record in pending_records_list
        if prd_record.prd_path not in claimed_paths_set
    ]
    settled_archive_records_list = [
        prd_record
        for prd_record in archive_records_list
        if prd_record.prd_path not in claimed_paths_set
    ]
    return (
        awaiting_review_records_list,
        actionable_pending_records_list,
        settled_archive_records_list,
    )


def main() -> int:
    """命令行入口。

    Returns:
        int: 进程退出码。
    """
    raw_argument_parser = argparse.ArgumentParser(
        description="PRD 状态看板：汇总 pending / archive 的清单进度与证据包状态。"
    )
    raw_argument_parser.add_argument(
        "scope",
        nargs="?",
        default="status",
        choices=["status", "all", "pending", "archive"],
        help=(
            "status（默认，archive 按月折叠）/ all（展开 archive 每条）/ pending / archive；"
            "横幅为待人工验收的 PRD 在任何范围都单列 AWAITING HUMAN 段"
        ),
    )
    raw_argument_parser.add_argument(
        "--detail", action="store_true", help="在每个 PRD 行下方打印标题与描述摘要块"
    )
    raw_parsed_arguments = raw_argument_parser.parse_args()

    repo_root_path = prd_locator.resolve_repo_root()
    pending_dir_path = repo_root_path / "tasks" / "pending"
    archive_dir_path = repo_root_path / "tasks" / "archive"
    evidence_root_path = repo_root_path / "tasks" / "evidence"
    # 锁的唯一事实源在主仓库：linked worktree 内查看看板时仍读主仓库的锁视图。
    main_repo_root_path = prd_lock.resolve_main_repo_root()

    is_color_enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    palette = Palette(is_color_enabled)

    # worktree 列表只取一次，两批记录共用：进度与证据都要按它定位分支副本。
    worktree_branches_list = prd_lock.list_linked_worktree_branches(main_repo_root_path)

    pending_records_list = [
        collect_prd_record(prd_path, evidence_root_path, worktree_branches_list)
        for prd_path in sorted(pending_dir_path.glob("*.md"))
    ]
    archive_records_list = [
        collect_prd_record(prd_path, evidence_root_path, worktree_branches_list)
        for prd_path in sorted(archive_dir_path.glob("*.md"), reverse=True)
    ]

    awaiting_review_records_list, actionable_pending_records_list, settled_archive_records_list = (
        split_awaiting_human_records(pending_records_list, archive_records_list)
    )

    raw_scope_text = raw_parsed_arguments.scope
    # (标题, 记录, 目录说明, 是否按月折叠)；空的 AWAITING HUMAN 不打印，免得每次多出一段
    # 没有信息量的 (无)，但只要有就在任何范围都露出——它是验收欠账，不能因为查看范围
    # 选了 archive 就悄悄消失。
    sections_list: list[tuple[str, list[PrdRecord], str, bool]] = []
    if raw_scope_text in ("status", "pending"):
        sections_list.append(("PENDING", actionable_pending_records_list, "tasks/pending", False))
    if awaiting_review_records_list:
        sections_list.append(
            (
                "AWAITING HUMAN",
                awaiting_review_records_list,
                "已归档 · 🧍 待人工验收（执行侧已完成，等你确认）",
                False,
            )
        )
    if raw_scope_text in ("status", "archive", "all"):
        sections_list.append(
            ("ARCHIVE", settled_archive_records_list, "tasks/archive", raw_scope_text != "all")
        )

    print()
    for (
        raw_title_text,
        bucket_records_list,
        raw_relative_dir_text,
        is_month_collapsed,
    ) in sections_list:
        print_bucket_section(
            raw_title_text,
            bucket_records_list,
            raw_relative_dir_text,
            palette,
            collapsed_months=is_month_collapsed,
            main_repo_root=main_repo_root_path,
            evidence_root=evidence_root_path,
            pending_dir=pending_dir_path,
            archive_dir=archive_dir_path,
            show_detail=raw_parsed_arguments.detail,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

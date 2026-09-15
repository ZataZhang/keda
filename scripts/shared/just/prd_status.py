#!/usr/bin/env python3
"""PRD 状态看板。

扫描 ``tasks/pending`` 与 ``tasks/archive``，汇总每条 PRD 的优先级、类型、
创建时间、验收清单勾选进度、证据包状态与执行锁运行态（ACTIVITY 列），
供开工前判断哪些尚未交付、哪些正被其他会话执行。

用法::

    python3 scripts/shared/just/prd_status.py [all|pending|archive]

清单进度与 verifier 结论均为从文件内容推断的启发式结果，看板只用于快速定位，
最终判断以 PRD 正文与证据文件原文为准。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import prd_lock

PRD_FILENAME_PATTERN = re.compile(
    r"^(?:P(?P<priority>\d+)-)?(?:(?P<kind>[A-Z]+)-)?"
    r"(?P<date>\d{8})-(?:(?P<time>\d{6})-)?(?P<slug>.+)\.md$"
)

CHECKLIST_HEADING_PATTERN = re.compile(
    r"^##\s+(?:\d+[.、]\s*)?(?:Acceptance Checklist|验收清单)\s*$"
)
CHECKED_ITEM_PATTERN = re.compile(r"^\s*[-*]\s*\[[xX]\]")
UNCHECKED_ITEM_PATTERN = re.compile(r"^\s*[-*]\s*\[\s*\]")

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

    @property
    def checklist_complete(self) -> bool:
        """验收清单是否已全部勾选；无清单时视为不完整。"""
        return self.checklist_total > 0 and self.checklist_checked == self.checklist_total


def parse_prd_filename(prd_path: Path) -> tuple[str, str, str, str]:
    """从 PRD 文件名解析优先级、类型、创建日期与短标识。

    Args:
        prd_path (Path): PRD 文件路径。

    Returns:
        tuple[str, str, str, str]: ``(priority, kind, created, slug)``，任何一项
        无法解析时返回空串；旧式命名（无 ``P{n}-{TYPE}`` 前缀）只解析日期与标识。
    """
    filename_match = PRD_FILENAME_PATTERN.match(prd_path.name)
    if not filename_match:
        return "", "", "", prd_path.stem

    raw_priority_text = filename_match.group("priority") or ""
    raw_kind_text = filename_match.group("kind") or ""
    raw_date_text = filename_match.group("date") or ""
    if len(raw_date_text) != 8:
        formatted_created_date = ""
    else:
        formatted_created_date = f"{raw_date_text[0:4]}-{raw_date_text[4:6]}-{raw_date_text[6:8]}"

    return (
        f"P{raw_priority_text}" if raw_priority_text else "",
        raw_kind_text,
        formatted_created_date,
        filename_match.group("slug") or prd_path.stem,
    )


def count_checklist_items(prd_text: str) -> tuple[int, int]:
    """统计验收清单的勾选情况。

    Args:
        prd_text (str): PRD 文件全文。

    Returns:
        tuple[int, int]: ``(已勾选数量, 总数)``；未找到清单标题时返回 ``(0, 0)``。
    """
    raw_lines_list = prd_text.splitlines()
    checklist_start_index = -1
    for line_index, raw_line_text in enumerate(raw_lines_list):
        if CHECKLIST_HEADING_PATTERN.match(raw_line_text):
            checklist_start_index = line_index + 1
            break

    if checklist_start_index < 0:
        return 0, 0

    checked_item_count = 0
    unchecked_item_count = 0
    for raw_line_text in raw_lines_list[checklist_start_index:]:
        if raw_line_text.startswith("## "):
            break
        if CHECKED_ITEM_PATTERN.match(raw_line_text):
            checked_item_count += 1
        elif UNCHECKED_ITEM_PATTERN.match(raw_line_text):
            unchecked_item_count += 1

    return checked_item_count, checked_item_count + unchecked_item_count


def find_named_evidence_file(evidence_dir: Path, stem_suffix: str) -> Path | None:
    """在证据目录中查找 ``<prd-basename>.<suffix>.md`` 或 ``<suffix>.md``。

    Args:
        evidence_dir (Path): 该 PRD 的证据目录。
        stem_suffix (str): 文件种类后缀，例如 ``verification-plan``。

    Returns:
        Path | None: 命中的文件路径，均不存在时返回 ``None``。
    """
    if not evidence_dir.is_dir():
        return None

    for evidence_filename in (
        f"{evidence_dir.name}.{stem_suffix}.md",
        f"{stem_suffix}.md",
    ):
        candidate_evidence_path = evidence_dir / evidence_filename
        if candidate_evidence_path.is_file():
            return candidate_evidence_path
    return None


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


def collect_prd_record(prd_path: Path, evidence_root: Path) -> PrdRecord:
    """读取单个 PRD 文件与对应证据目录，生成看板记录。

    Args:
        prd_path (Path): PRD 文件路径。
        evidence_root (Path): ``tasks/evidence`` 目录。

    Returns:
        PrdRecord: 该 PRD 的看板信息。
    """
    raw_priority_text, raw_kind_text, formatted_created_date, raw_slug_text = parse_prd_filename(
        prd_path
    )
    raw_prd_text = prd_path.read_text(encoding="utf-8")
    checked_item_count, checklist_item_count = count_checklist_items(raw_prd_text)

    evidence_dir = evidence_root / prd_path.stem
    verification_plan_path = find_named_evidence_file(evidence_dir, "verification-plan")
    evidence_report_path = find_named_evidence_file(evidence_dir, "evidence-report")
    verifier_report_path = find_named_evidence_file(evidence_dir, "verifier-report")

    verifier_verdict_text = ""
    is_verdict_certain = False
    if verifier_report_path is not None:
        verifier_verdict_text, is_verdict_certain = parse_verifier_verdict(
            verifier_report_path.read_text(encoding="utf-8")
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
    )


def resolve_repo_root() -> Path:
    """定位仓库根目录。

    Returns:
        Path: 优先使用 git 仓库根；不在 git 仓库内时，从当前目录向上查找含
        ``tasks/`` 的目录；仍找不到时回退到当前工作目录。
    """
    try:
        raw_git_root_text = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if raw_git_root_text:
            return Path(raw_git_root_text)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    for candidate_root_path in [Path.cwd(), *Path.cwd().parents]:
        if (candidate_root_path / "tasks").is_dir():
            return candidate_root_path
    return Path.cwd()


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


RECENT_TOUCH_WINDOW_MINUTES = 15


def match_worktree_by_slug(
    worktree_branches_list: list[tuple[str, Path]], slug_text: str
) -> tuple[str, Path] | None:
    """在 worktree 列表中找分支名（或其最后一段）与 PRD slug 相等的条目。

    Args:
        worktree_branches_list (list[tuple[str, Path]]): ``(分支名, worktree 路径)``
            列表，来自 ``prd_lock.list_linked_worktree_branches``。
        slug_text (str): PRD 文件名解析出的 slug。

    Returns:
        tuple[str, Path] | None: 命中的 ``(分支名, worktree 路径)``；无匹配为 ``None``。
    """
    for branch_name_text, worktree_path in worktree_branches_list:
        branch_basename_text = branch_name_text.rsplit("/", 1)[-1]
        if slug_text == branch_name_text or slug_text == branch_basename_text:
            return branch_name_text, worktree_path
    return None


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


def format_activity_cell(
    prd_record: PrdRecord, main_repo_root: Path, evidence_root: Path, palette: Palette
) -> str:
    """格式化运行态列（ACTIVITY）。

    新鲜锁 → 黄色 ``RUNNING <tool> <时长> @<branch>``（branch 缺失回退 worktree）；
    过期锁但归属 worktree 仍有近期改动 → 同样按 RUNNING 渲染（心跳只是兜底信号，
    活性佐证说明会话仍在执行）；过期锁且无活性佐证 → 红色 ``STALE <最后心跳>``；
    无锁但存在分支名匹配的 worktree → 黄色 ``⚠ unlocked @<branch>``（互斥未生效
    的漏洞必须摆到台面上，而不是静默显示 ``-``）；无锁但 PRD 文件或证据目录
    近期有改动 → 暗色 ``⚡ active <n>m ago``；其余 ``-``。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录。
        main_repo_root (Path): 主仓库根目录。
        evidence_root (Path): ``tasks/evidence`` 目录。
        palette (Palette): 颜色包装器。

    Returns:
        str: 运行态单元格文本。
    """
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
            raw_location_text = (
                str(lock_metadata.get("branch") or "")
                or str(lock_metadata.get("worktree") or "")
                or "主仓库"
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
        raw_heartbeat_text = str(lock_metadata.get("heartbeat_at") or "?")
        return palette.red(f"STALE {raw_heartbeat_text}")

    matched_worktree = match_worktree_by_slug(
        prd_lock.list_linked_worktree_branches(main_repo_root), prd_record.slug
    )
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
) -> None:
    """输出逐条 PRD 的对齐表格（含 ACTIVITY 运行态列）。

    Args:
        prd_records_list (list[PrdRecord]): 待输出的 PRD 记录。
        palette (Palette): 颜色包装器。
        main_repo_root (Path): 主仓库根目录，用于查询执行锁。
        evidence_root (Path): ``tasks/evidence`` 目录，用于弱信号探测。
    """
    if not prd_records_list:
        print(palette.dim("  (无)"))
        return

    raw_priority_cells_list = [prd_record.priority or "-" for prd_record in prd_records_list]
    raw_kind_cells_list = [prd_record.kind or "-" for prd_record in prd_records_list]
    raw_created_cells_list = [prd_record.created or "-" for prd_record in prd_records_list]
    raw_slug_cells_list = [prd_record.slug for prd_record in prd_records_list]
    raw_activity_cells_list = [
        format_activity_cell(prd_record, main_repo_root, evidence_root, palette)
        for prd_record in prd_records_list
    ]

    priority_column_width = max(len("PR"), *(len(cell) for cell in raw_priority_cells_list))
    kind_column_width = max(len("TYPE"), *(len(cell) for cell in raw_kind_cells_list))
    created_column_width = max(len("CREATED"), *(len(cell) for cell in raw_created_cells_list))
    slug_column_width = max(len("PRD"), *(len(cell) for cell in raw_slug_cells_list))

    raw_header_text = "  " + "  ".join(
        [
            pad_to_width("PR", priority_column_width),
            pad_to_width("TYPE", kind_column_width),
            pad_to_width("CREATED", created_column_width),
            pad_to_width("PRD", slug_column_width),
            "CHECKLIST",
            "EVIDENCE",
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
        raw_activity_cell,
    ) in zip(
        prd_records_list,
        raw_priority_cells_list,
        raw_kind_cells_list,
        raw_created_cells_list,
        raw_slug_cells_list,
        raw_activity_cells_list,
    ):
        raw_row_text = "  " + "  ".join(
            [
                pad_to_width(palette.bold(raw_priority_cell), priority_column_width),
                pad_to_width(raw_kind_cell, kind_column_width),
                pad_to_width(raw_created_cell, created_column_width),
                pad_to_width(raw_slug_cell, slug_column_width),
                pad_to_width(format_checklist_cell(prd_record, palette), 9),
                format_evidence_cell(prd_record, palette),
                raw_activity_cell,
            ]
        )
        print(raw_row_text.rstrip())


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
    """
    print(
        palette.bold(f"{raw_title_text} ({len(bucket_records_list)})")
        + palette.dim(f"  — {raw_relative_dir_text}")
    )
    print()
    if collapsed_months:
        render_archive_months(bucket_records_list, palette)
    else:
        render_prd_table(bucket_records_list, palette, main_repo_root, evidence_root)
    print()


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
        help="status（默认，archive 按月折叠）/ all（展开 archive 每条）/ pending / archive",
    )
    raw_parsed_arguments = raw_argument_parser.parse_args()

    repo_root_path = resolve_repo_root()
    pending_dir_path = repo_root_path / "tasks" / "pending"
    archive_dir_path = repo_root_path / "tasks" / "archive"
    evidence_root_path = repo_root_path / "tasks" / "evidence"
    # 锁的唯一事实源在主仓库：linked worktree 内查看看板时仍读主仓库的锁视图。
    main_repo_root_path = prd_lock.resolve_main_repo_root()

    is_color_enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    palette = Palette(is_color_enabled)

    pending_records_list = [
        collect_prd_record(prd_path, evidence_root_path)
        for prd_path in sorted(pending_dir_path.glob("*.md"))
    ]
    archive_records_list = [
        collect_prd_record(prd_path, evidence_root_path)
        for prd_path in sorted(archive_dir_path.glob("*.md"), reverse=True)
    ]

    raw_scope_text = raw_parsed_arguments.scope
    print()
    if raw_scope_text in ("status", "pending"):
        print_bucket_section(
            "PENDING",
            pending_records_list,
            "tasks/pending",
            palette,
            collapsed_months=False,
            main_repo_root=main_repo_root_path,
            evidence_root=evidence_root_path,
        )
    if raw_scope_text in ("status", "archive", "all"):
        print_bucket_section(
            "ARCHIVE",
            archive_records_list,
            "tasks/archive",
            palette,
            collapsed_months=raw_scope_text != "all",
            main_repo_root=main_repo_root_path,
            evidence_root=evidence_root_path,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

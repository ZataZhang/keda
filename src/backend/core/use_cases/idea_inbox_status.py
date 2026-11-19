"""根据想法关联的 PRD 文件位置推导 Idea Inbox 生命周期状态。"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from backend.core.shared.models.idea_inbox import (
    IdeaEntry,
    IdeaStatus,
    PrdDraftStatus,
    PrdDraftSummary,
)

_logger = logging.getLogger(__name__)
IDEA_HEADER_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"
    r"(?:\s+·\s+(?P<tag>.*?))?"
    r"(?:\s+<!--\s*idea-status:\s*(?P<status>pending|in_progress|completed|abandoned)\s*-->)?\s*$"
)
_PRD_IDEA_REFS_RE = re.compile(r"^- Source Idea Refs:\s*(?P<refs>.*)$")


@dataclass(frozen=True)
class IdeaStatusProjectionInput:
    """状态投影所需的仓库、想法和草稿。"""

    repo_path: Path
    idea_entries: Sequence[IdeaEntry]
    prd_drafts: Sequence[PrdDraftSummary]


def _read_prd_idea_refs(prd_path: Path) -> tuple[str, ...]:
    try:
        with prd_path.open("r", encoding="utf-8") as prd_file:
            for line_number, prd_line in enumerate(prd_file):
                if line_number >= 40:
                    break
                match = _PRD_IDEA_REFS_RE.match(prd_line.strip())
                if match:
                    return tuple(
                        idea_ref.strip()
                        for idea_ref in match.group("refs").split(",")
                        if idea_ref.strip()
                    )
    except OSError as exc:
        _logger.debug("Unable to read PRD references from %s: %s", prd_path, exc)
    return ()


def resolve_idea_statuses(
    projection_input: IdeaStatusProjectionInput,
) -> list[IdeaEntry]:
    """根据标题标记、关联草稿和 PRD 路径推导想法状态。

    Args:
        projection_input (IdeaStatusProjectionInput): 状态投影上下文。

    Returns:
        list[IdeaEntry]: 带有效生命周期状态的想法条目。
    """
    active_ids: set[str] = set()
    completed_ids: set[str] = set()
    for draft in projection_input.prd_drafts:
        if draft.metadata.status is PrdDraftStatus.PENDING_REVIEW:
            active_ids.update(draft.metadata.source_idea_refs)
    task_sources = (
        (projection_input.repo_path / "tasks", IdeaStatus.IN_PROGRESS),
        (projection_input.repo_path / "tasks" / "pending", IdeaStatus.IN_PROGRESS),
        (projection_input.repo_path / "tasks" / "hold", IdeaStatus.IN_PROGRESS),
        (projection_input.repo_path / "tasks" / "archive", IdeaStatus.COMPLETED),
    )
    for task_directory, task_status in task_sources:
        if not task_directory.exists():
            continue
        target_ids = completed_ids if task_status is IdeaStatus.COMPLETED else active_ids
        for prd_path in task_directory.glob("*.md"):
            target_ids.update(_read_prd_idea_refs(prd_path))
    resolved_entries: list[IdeaEntry] = []
    for idea_entry in projection_input.idea_entries:
        if idea_entry.status in {IdeaStatus.ABANDONED, IdeaStatus.COMPLETED}:
            effective_status = idea_entry.status
        elif idea_entry.entry_id in active_ids:
            effective_status = IdeaStatus.IN_PROGRESS
        elif idea_entry.entry_id in completed_ids:
            effective_status = IdeaStatus.COMPLETED
        else:
            effective_status = IdeaStatus.PENDING
        resolved_entries.append(replace(idea_entry, status=effective_status))
    return resolved_entries


def _has_idea_quote(section_lines: Sequence[str]) -> bool:
    quoted_lines: list[str] = []
    for section_line in section_lines:
        line_text = section_line.rstrip("\r\n")
        if line_text.startswith(">"):
            quoted_lines.append(line_text[1:].lstrip())
        elif not line_text.strip():
            quoted_lines.append("")
        else:
            break
    return bool("\n".join(quoted_lines).strip())


def rewrite_idea_status_markers(
    ideas_text: str,
    idea_entries: Sequence[IdeaEntry],
) -> str:
    """只更新想法标题中的状态元数据，保留原话与背景内容。

    Args:
        ideas_text (str): 当前 ``ideas.md`` 全文。
        idea_entries (Sequence[IdeaEntry]): 已解析并完成状态推导的条目。

    Returns:
        str: 更新状态标记后的 Markdown 全文。
    """
    source_lines = ideas_text.splitlines(keepends=True)
    header_indices = [
        line_index
        for line_index, source_line in enumerate(source_lines)
        if IDEA_HEADER_RE.match(source_line.strip())
    ]
    entry_index = 0
    for header_offset, header_index in enumerate(header_indices):
        next_header_index = (
            header_indices[header_offset + 1]
            if header_offset + 1 < len(header_indices)
            else len(source_lines)
        )
        if not _has_idea_quote(source_lines[header_index + 1 : next_header_index]):
            continue
        if entry_index >= len(idea_entries):
            break
        idea_entry = idea_entries[entry_index]
        entry_index += 1
        original_heading = source_lines[header_index]
        line_ending = (
            "\r\n"
            if original_heading.endswith("\r\n")
            else "\n"
            if original_heading.endswith("\n")
            else ""
        )
        heading_text = original_heading[: -len(line_ending)] if line_ending else original_heading
        header_match = IDEA_HEADER_RE.match(heading_text.strip())
        if header_match is None:
            continue
        tag_text = header_match.group("tag") or ""
        current_status = (
            IdeaStatus(header_match.group("status"))
            if header_match.group("status") is not None
            else IdeaStatus.PENDING
        )
        clean_tag = tag_text.strip()
        entry_id_match = re.search(r"\(idea-[^)]+\)", clean_tag)
        current_entry_id = entry_id_match.group(0)[1:-1] if entry_id_match else ""
        if current_status is idea_entry.status and current_entry_id == idea_entry.entry_id:
            continue
        if not current_entry_id:
            if clean_tag:
                clean_tag = f"{clean_tag} ({idea_entry.entry_id})"
            else:
                clean_tag = f"manual · anonymous ({idea_entry.entry_id})"
        updated_heading = (
            f"## {header_match.group(1)} · {clean_tag} "
            f"<!-- idea-status: {idea_entry.status.value} -->{line_ending}"
        )
        source_lines[header_index] = updated_heading
    return "".join(source_lines)

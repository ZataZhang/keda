"""Shared pure helper for parsing PRD Acceptance Checklist state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


ACCEPTANCE_CHECKLIST_HEADING_RE = re.compile(
    r"^##\s+(?:\d+\.\s+)?(?:Acceptance Checklist\b.*|验收清单.*)\s*$"
)
TOP_LEVEL_HEADING_RE = re.compile(r"^##\s+")
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s*(?P<label>.*)$")
CODE_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


@dataclass(frozen=True)
class PrdChecklistResult:
    """Result of parsing a PRD's Acceptance Checklist section.

    Attributes:
        section_found: Whether an Acceptance Checklist section was located.
        unchecked_items: List of unchecked items as (1-based line number, line text).
        checked_items: List of ticked items as (1-based line number, line text).
            用于区分"被勾上"与"被删掉"——只看 ``unchecked_items`` 变小的话，
            删除一个条目和勾上它无法区分。
        human_pending_items: ``Human-Confirmed`` 小节里仍待人工确认的空框或后置门禁。
    """

    section_found: bool
    unchecked_items: list[tuple[int, str]]
    checked_items: list[tuple[int, str]] = field(default_factory=list)
    human_pending_items: list[tuple[int, str]] = field(default_factory=list)

    @property
    def execution_unchecked_items(self) -> list[tuple[int, str]]:
        """Return unchecked items that an executor can resolve before PR review."""
        human_lines = {line_number for line_number, _ in self.human_pending_items}
        return [entry for entry in self.unchecked_items if entry[0] not in human_lines]

    @property
    def is_complete(self) -> bool:
        """Return True when the section exists and all items are checked."""
        return self.section_found and not self.unchecked_items and not self.human_pending_items


def parse_prd_checklist(file_content: str) -> PrdChecklistResult:
    """Parse a PRD markdown string and return its Acceptance Checklist state.

    Only checkboxes inside the Acceptance Checklist section are considered.
    Checkboxes inside fenced code blocks are ignored.  The section ends at
    the next top-level ``##`` heading or end of file.

    Args:
        file_content: Raw markdown content of the PRD file.

    Returns:
        PrdChecklistResult with section_found and unchecked_items.
    """
    lines = file_content.splitlines()

    start_index: int | None = None
    for line_index, line in enumerate(lines):
        if ACCEPTANCE_CHECKLIST_HEADING_RE.match(line):
            start_index = line_index
            break

    if start_index is None:
        return PrdChecklistResult(section_found=False, unchecked_items=[])

    end_index = len(lines)
    for line_index in range(start_index + 1, len(lines)):
        if TOP_LEVEL_HEADING_RE.match(lines[line_index]):
            end_index = line_index
            break

    unchecked_items: list[tuple[int, str]] = []
    checked_items: list[tuple[int, str]] = []
    human_pending_items: list[tuple[int, str]] = []
    in_code_block = False
    human_heading_depth: int | None = None

    for line_index in range(start_index + 1, end_index):
        line = lines[line_index]
        if CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        heading_match = re.match(r"^(#{3,6})\s+(.+?)\s*$", line)
        if heading_match:
            heading_depth = len(heading_match.group(1))
            if human_heading_depth is not None and heading_depth <= human_heading_depth:
                human_heading_depth = None
            if heading_match.group(2).strip().casefold() == "human-confirmed":
                human_heading_depth = heading_depth
            continue

        checkbox_match = CHECKBOX_RE.match(line)
        if not checkbox_match:
            if human_heading_depth is not None and re.match(r"^\s*[-*+]\s+\[~\]", line):
                human_pending_items.append((line_index + 1, line.rstrip()))
            continue
        if checkbox_match.group("mark") == " ":
            unchecked_items.append((line_index + 1, line.rstrip()))
            if human_heading_depth is not None:
                human_pending_items.append((line_index + 1, line.rstrip()))
        else:
            checked_items.append((line_index + 1, line.rstrip()))

    return PrdChecklistResult(
        section_found=True,
        unchecked_items=unchecked_items,
        checked_items=checked_items,
        human_pending_items=human_pending_items,
    )

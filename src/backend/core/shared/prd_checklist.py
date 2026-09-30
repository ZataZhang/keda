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
HEADING_RE = re.compile(r"^(#{3,6})\s+(.+?)\s*$")
# 整行加粗的分组标签（如 `**Human-Confirmed**`）。要求整行只有加粗内容，
# 因此行内强调（`**Human-Confirmed（2026-09-23）：** 用户确认…`）不会命中。
BOLD_GROUP_LABEL_RE = re.compile(r"^\s*(?:\*\*|__)\s*(?P<label>.+?)\s*(?:\*\*|__)\s*$")
HUMAN_CONFIRMED_LABEL_PREFIX = "human-confirmed"
# 粗体分组标签没有标题层级，按三级处理，复用"遇到同级或更高级标题即关闭"的规则。
BOLD_GROUP_DEPTH = 3
# 清单区内是否"提到过" Human-Confirmed（不限定书写形式），用于诊断分组未被识别的情况。
HUMAN_CONFIRMED_MENTION_RE = re.compile(r"human[-\s_]?confirmed", re.IGNORECASE)


def is_human_confirmed_group_label(label: str) -> bool:
    """判断分组标签是否指代 Human-Confirmed 小节。

    标题文本常带说明后缀（prd skill 模板写作
    ``Human-Confirmed (来自 Part A 风险地图)``），因此按前缀匹配而不是精确相等。

    Args:
        label: 标题或粗体分组的标签文本（不含 ``#`` / ``**`` 标记）。

    Returns:
        标签归一化后以 ``human-confirmed`` 开头时为 True。
    """
    return label.strip().casefold().startswith(HUMAN_CONFIRMED_LABEL_PREFIX)


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
            小节标题既可以是 ``### Human-Confirmed`` 这类标题，也可以是整行加粗的
            ``**Human-Confirmed**`` 分组标签。
        human_group_found: 是否识别出了 Human-Confirmed 分组（识别到即认定该分组存在，
            与组内是否残留待确认条目无关）。
        human_confirmed_mentioned: 清单区内是否出现过 ``Human-Confirmed`` 字样（不限定
            书写形式）。``human_confirmed_mentioned`` 为真而 ``human_group_found`` 为假，
            说明分组写法没被识别，调用方据此给出可行动的诊断而不是让 agent 自己猜。
    """

    section_found: bool
    unchecked_items: list[tuple[int, str]]
    checked_items: list[tuple[int, str]] = field(default_factory=list)
    human_pending_items: list[tuple[int, str]] = field(default_factory=list)
    human_group_found: bool = False
    human_confirmed_mentioned: bool = False

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
    human_group_found = False
    human_confirmed_mentioned = False

    for line_index in range(start_index + 1, end_index):
        line = lines[line_index]
        if CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if HUMAN_CONFIRMED_MENTION_RE.search(line):
            human_confirmed_mentioned = True

        heading_match = HEADING_RE.match(line)
        if heading_match:
            heading_depth = len(heading_match.group(1))
            if human_heading_depth is not None and heading_depth <= human_heading_depth:
                human_heading_depth = None
            if is_human_confirmed_group_label(heading_match.group(2)):
                human_heading_depth = heading_depth
                human_group_found = True
            continue

        bold_group_match = BOLD_GROUP_LABEL_RE.match(line)
        if bold_group_match:
            if human_heading_depth is not None and BOLD_GROUP_DEPTH <= human_heading_depth:
                human_heading_depth = None
            if is_human_confirmed_group_label(bold_group_match.group("label")):
                human_heading_depth = BOLD_GROUP_DEPTH
                human_group_found = True
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
        human_group_found=human_group_found,
        human_confirmed_mentioned=human_confirmed_mentioned,
    )

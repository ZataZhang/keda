"""PRD Acceptance Checklist 的**适配层**。

解析（章节边界、分组、复选框标记）由 prd skill 的 ``scripts/prd_contract.py``
承担，本模块只把它的 JSON 映射成 keda 各处沿用的 :class:`PrdChecklistResult`：

- 契约文本与真正执行的解析只有一份实现，不会再各自漂移；
- 门禁语义（哪些未勾项该拦、人属项该放行）仍留在 keda，按契约取用结构字段。

保留 :data:`CHECKBOX_RE` 是给"读一行、判断是不是复选框"这类**行级**判断用的
（见 ``agent_runner_closeout`` 重写清单行的场景）；它不是章节解析的一部分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.core.shared.prd_contract_client import parse_prd_contract

CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s*(?P<label>.*)$")
"""行级复选框匹配；``[~]`` 刻意不是复选框（见 Machine Contract §2）。"""

_UNCHECKED_MARK = " "
_CHECKED_MARK = "x"
_RESOLVED_MARK = "~"


@dataclass(frozen=True)
class PrdChecklistResult:
    """Result of parsing a PRD's Acceptance Checklist section.

    Attributes:
        section_found: Whether an Acceptance Checklist section was located.
        unchecked_items: List of unchecked items as (1-based line number, line text).
        checked_items: List of ticked items as (1-based line number, line text).
            用于区分"被勾上"与"被删掉"——只看 ``unchecked_items`` 变小的话，
            删除一个条目和勾上它无法区分。
        human_pending_items: ``Human-Confirmed`` 分组里仍待人回答的空框，以及组内
            ``[~]`` 后置门禁。分组既可以是 ``### Human-Confirmed`` 这类标题，也可以
            是整行加粗的 ``**Human-Confirmed**``（后者是契约外的兼容输入）。
        human_group_found: 是否识别出了 Human-Confirmed 分组（识别到即认定该分组存在，
            与组内是否残留待确认条目无关）。
        human_confirmed_mentioned: 清单区内是否出现过 ``Human-Confirmed`` 字样（不限定
            书写形式）。``human_confirmed_mentioned`` 为真而 ``human_group_found`` 为假，
            说明分组写法没被识别，调用方据此给出可行动的诊断而不是让 agent 自己猜。
        human_unchecked_items: ``Human-Confirmed`` 分组里仍是 ``[ ]`` 的空框（不含
            ``[~]``），与 skill 检查器决定横幅应有状态时的口径一致。v3/v4 skill 不报告
            该字段，此时为空。
        acceptance_status: 验收状态横幅的状态（``not_started`` / ``awaiting_human`` /
            ``accepted``；横幅缺失或认不出时为 ``""``），由 skill 解析。``None`` 表示
            本机 skill 是 v3/v4、根本不报告横幅，调用方应跳过横幅检查。
    """

    section_found: bool
    unchecked_items: list[tuple[int, str]]
    checked_items: list[tuple[int, str]] = field(default_factory=list)
    human_pending_items: list[tuple[int, str]] = field(default_factory=list)
    human_group_found: bool = False
    human_confirmed_mentioned: bool = False
    human_unchecked_items: list[tuple[int, str]] = field(default_factory=list)
    acceptance_status: str | None = None

    @property
    def execution_unchecked_items(self) -> list[tuple[int, str]]:
        """Return unchecked items that an executor can resolve before PR review."""
        human_lines = {line_number for line_number, _ in self.human_pending_items}
        return [entry for entry in self.unchecked_items if entry[0] not in human_lines]

    @property
    def is_complete(self) -> bool:
        """Return True when the section exists and all items are checked."""
        return self.section_found and not self.unchecked_items and not self.human_pending_items


def _item_line_text(item: dict[str, Any]) -> tuple[int, str]:
    """把解析脚本返回的单条条目映射成 ``(行号, 原文)``。"""
    return int(item["line"]), str(item["text"])


def parse_prd_checklist(file_content: str) -> PrdChecklistResult:
    """Parse a PRD markdown string and return its Acceptance Checklist state.

    解析委托给 prd skill 的 ``prd_contract.py``（格式的唯一实现），本函数只做映射。
    章节缺失、围栏代码块忽略、分组归属等规则都归那边定义。

    Args:
        file_content: Raw markdown content of the PRD file.

    Returns:
        PrdChecklistResult with section_found, unchecked_items and the
        acceptance status banner reported by the skill.

    Raises:
        PrdContractError: prd skill 的解析脚本不可用（缺失 / 执行失败 / 输出非法）。
    """
    contract = parse_prd_contract(file_content)
    checklist = contract["checklist"]
    items: list[dict[str, Any]] = checklist.get("items", [])

    unchecked_items = [
        _item_line_text(item) for item in items if item.get("mark") == _UNCHECKED_MARK
    ]
    checked_items = [_item_line_text(item) for item in items if item.get("mark") == _CHECKED_MARK]
    human_pending_items = [
        _item_line_text(item)
        for item in items
        if item.get("in_human_group") and item.get("mark") in (_UNCHECKED_MARK, _RESOLVED_MARK)
    ]

    return PrdChecklistResult(
        section_found=bool(checklist.get("section_found")),
        unchecked_items=unchecked_items,
        checked_items=checked_items,
        human_pending_items=human_pending_items,
        human_group_found=bool(checklist.get("human_group_found")),
        human_confirmed_mentioned=bool(checklist.get("human_confirmed_mentioned")),
        human_unchecked_items=[
            _item_line_text(item) for item in checklist.get("human_unchecked", [])
        ],
        acceptance_status=contract.get("acceptance_status"),
    )

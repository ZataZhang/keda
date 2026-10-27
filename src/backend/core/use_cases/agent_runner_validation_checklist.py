"""PR body 里的 Realistic Validation 勾选清单区块。

从 :mod:`agent_runner_validation` 拆出的第三块（"gate" 的输入侧）：构造、定位、
读取与重置 PR 描述中的勾选清单。软门禁本身（label 维护、head 漂移重置）在
:mod:`agent_runner_validation_gate`,它消费这里解析出的
:class:`ValidationChecklistState`。

这里同时提供 :func:`find_unrecognized_validation_block_marker`：签收面（CI 工作流、
daemon 软门禁、合并队列自动勾选）都只认当前 marker 契约，正文里残留旧前缀或残缺的
区块会让它们一致地"当成没有清单"从而放行。该函数把这种区块抓出来，供消费方
fail closed，而不是让门禁静默空转。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.core.shared.markdown_code_fence import strip_code_fences

_CHECKLIST_START_PATTERN = re.compile(
    r"<!--\s*kc:realistic-validation\s+version=(?P<version>\d+)\s+total=(?P<total>\d+)\s*-->"
)
_CHECKLIST_END_MARKER = "<!-- kc:realistic-validation-end -->"
_CHECKED_ITEM_PATTERN = re.compile(r"^\s*[-*] \[[xX]\] ")
_UNCHECKED_ITEM_PATTERN = re.compile(r"^\s*[-*] \[ \] ")
# 任何命名空间 / 任何形态的签收区块起始 marker（``-end`` 结束 marker 排除在外），
# 用于识别"看起来是清单、但当前契约读不懂"的残留区块。
_ANY_CHECKLIST_START_PATTERN = re.compile(
    r"<!--\s*(?P<marker>[a-z0-9_-]+:realistic-validation(?![\w-])[^>]*)-->",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ValidationChecklistState:
    """Parsed state of the PR body Realistic Validation checklist."""

    total: int
    checked_count: int
    unchecked_count: int


# ---------------------------------------------------------------------------
# PR body 勾选清单区块
# ---------------------------------------------------------------------------


def build_validation_checklist_block(checklist_items: list[str]) -> str:
    """构造 PR 正文中由标记包裹的验证签收清单。

    Args:
        checklist_items: 需要签收的验证项文案。

    Returns:
        带 Realistic Validation 契约标记的清单文本。
    """
    return "\n".join(
        [
            f"<!-- kc:realistic-validation version=1 total={len(checklist_items)} -->",
            "## Realistic Validation sign-off",
            "",
            "Complete each item only after its evidence has been verified. "
            "The configured review or authorized auto-sign-off path may complete this checklist:",
            "",
            *checklist_items,
            "",
            _CHECKLIST_END_MARKER,
        ]
    )


def find_unrecognized_validation_block_marker(pr_body: str) -> str | None:
    """返回正文中当前契约读不懂的签收区块起始 marker；不存在时返回 ``None``。

    只认 runner 当前写入的 ``kc:realistic-validation version=N total=N``。改名前的旧前缀
    或形态不完整的 marker 会被签收入口一致地当成"没有清单"，于是 CI 门禁放行、自动勾选
    空转，而 PR 看上去确实带了一份清单。围栏代码块里的示例不算声明，因此先剥离再判定。

    Args:
        pr_body: PR 正文。

    Returns:
        第一个无法识别的 marker 文本（去掉 ``<!--`` / ``-->`` 与首尾空白）；
        全部可识别时 ``None``。
    """
    scannable_body = strip_code_fences(pr_body)
    for marker_match in _ANY_CHECKLIST_START_PATTERN.finditer(scannable_body):
        if not _CHECKLIST_START_PATTERN.match(marker_match.group(0)):
            return marker_match.group("marker").strip()
    return None


def _find_checklist_block(pr_body: str) -> tuple[int, int, int] | None:
    """Locate the checklist block. Returns (start, end, declared_total)."""
    start_match = _CHECKLIST_START_PATTERN.search(pr_body)
    if not start_match:
        return None
    end_index = pr_body.find(_CHECKLIST_END_MARKER, start_match.end())
    if end_index == -1:
        end_index = len(pr_body)
    return start_match.start(), end_index, int(start_match.group("total"))


def parse_validation_checklist_state(pr_body: str) -> ValidationChecklistState | None:
    """Parse checkbox state inside the marker-wrapped PR body block."""
    block_location = _find_checklist_block(pr_body)
    if block_location is None:
        return None
    block_start, block_end, declared_total = block_location
    block_text = pr_body[block_start:block_end]
    checked_count = 0
    unchecked_count = 0
    for block_line in block_text.splitlines():
        if _CHECKED_ITEM_PATTERN.match(block_line):
            checked_count += 1
        elif _UNCHECKED_ITEM_PATTERN.match(block_line):
            unchecked_count += 1
    return ValidationChecklistState(
        total=max(declared_total, checked_count + unchecked_count),
        checked_count=checked_count,
        unchecked_count=unchecked_count,
    )


def reset_validation_checklist(pr_body: str) -> str:
    """Return the PR body with all block checkboxes reset to unchecked."""
    block_location = _find_checklist_block(pr_body)
    if block_location is None:
        return pr_body
    block_start, block_end, _declared_total = block_location
    block_text = pr_body[block_start:block_end]
    reset_lines = [
        re.sub(r"^(\s*[-*] )\[[xX]\] ", r"\1[ ] ", block_line)
        for block_line in block_text.splitlines()
    ]
    return pr_body[:block_start] + "\n".join(reset_lines) + pr_body[block_end:]

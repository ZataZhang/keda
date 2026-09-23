"""PRD 与 Issue 队列共享的优先级规则。"""

from __future__ import annotations

import re

PRIORITY_ORDER: tuple[str, ...] = ("P0", "P1", "P2", "P3")
"""显式优先级的最高优先到最低优先顺序。"""


_PRD_FILENAME_PRIORITY_PATTERN = re.compile(r"^(P[0-3])-")


def priority_from_prd_filename(filename: str) -> str | None:
    """从 PRD 文件名前缀读取显式 P0–P3 优先级。"""
    match = _PRD_FILENAME_PRIORITY_PATTERN.match(filename)
    return match.group(1) if match else None


def priority_rank(priority: str | None) -> int:
    """返回优先级排序位置；缺省或未知优先级排在显式 P3 之后。"""
    try:
        return PRIORITY_ORDER.index(priority or "")
    except ValueError:
        return len(PRIORITY_ORDER)

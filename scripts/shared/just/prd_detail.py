#!/usr/bin/env python3
"""PRD 看板 ``--detail`` 的摘要解析与行下摘要块渲染。

``prd_status.py`` 已接近单文件非空行数上限，标题与描述摘要的解析、渲染拆到
本模块。摘要与 CHECKLIST 列同源：调用方传入**分支副本优先**解析出的 PRD 正文，
分支上改过的描述不会以主仓库旧文本呈现。

摘要优先取 ``Introduction & Goals`` 章节正文（兼容 ``## 1. Introduction & Goals``
编号写法）；章节缺失时退化为一级标题之后、首个 ``## `` 小节之前的引言。
看板定位是"快速定位，最终判断以 PRD 原文为准"，因此摘要只做有界截断，
不试图覆盖全文。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prd_status import Palette, PrdRecord

PRD_TITLE_PATTERN = re.compile(r"^#\s+(?P<title>(?!!).+?)\s*$")
INTRODUCTION_HEADING_PATTERN = re.compile(
    r"^##\s+(?:\d+[.、]\s*)?Introduction(?:\s*&\s*Goals)?\s*$"
)

DESCRIPTION_MAX_LINES = 4
DESCRIPTION_LINE_MAX_WIDTH = 110
DETAIL_BLOCK_INDENT = "      "


def extract_prd_title_and_summary(prd_text: str) -> tuple[str, tuple[str, ...]]:
    """提取 PRD 的一级标题与描述摘要行。

    摘要优先取 ``Introduction & Goals`` 章节正文，缺章节时退化为一级标题之后、
    首个小节标题之前的引言；连一级标题都没有时从文件头扫起。摘要最多
    ``DESCRIPTION_MAX_LINES`` 行；单行超过 ``DESCRIPTION_LINE_MAX_WIDTH`` 就地
    截断并以省略号结尾，取满行数上限仍有剩余正文时同样给末行补省略号披露截断。

    Args:
        prd_text (str): PRD 文件全文。

    Returns:
        tuple[str, tuple[str, ...]]: ``(一级标题, 摘要行元组)``；无一级标题时
        标题为空串，无可用正文时摘要为空元组。
    """
    raw_lines_list = prd_text.splitlines()

    parsed_title_text = ""
    title_line_index = -1
    for line_index, raw_line_text in enumerate(raw_lines_list):
        title_line_match = PRD_TITLE_PATTERN.match(raw_line_text)
        if title_line_match:
            parsed_title_text = title_line_match.group("title")
            title_line_index = line_index
            break

    introduction_start_index = -1
    for line_index, raw_line_text in enumerate(raw_lines_list):
        if INTRODUCTION_HEADING_PATTERN.match(raw_line_text):
            introduction_start_index = line_index + 1
            break

    body_start_index = (
        introduction_start_index if introduction_start_index >= 0 else title_line_index + 1
    )
    body_end_index = len(raw_lines_list)
    for line_index in range(body_start_index, len(raw_lines_list)):
        scanned_body_text = raw_lines_list[line_index]
        if scanned_body_text.startswith("## ") or scanned_body_text.startswith("# "):
            body_end_index = line_index
            break

    nonempty_body_texts = [
        stripped_body_text
        for raw_body_text in raw_lines_list[body_start_index:body_end_index]
        if (stripped_body_text := raw_body_text.strip())
    ]

    summary_line_texts: list[str] = []
    for body_line_offset, body_line_text in enumerate(nonempty_body_texts[:DESCRIPTION_MAX_LINES]):
        has_overflow_after_limit = (
            body_line_offset == DESCRIPTION_MAX_LINES - 1
            and len(nonempty_body_texts) > DESCRIPTION_MAX_LINES
        )
        display_line_text = body_line_text
        if len(display_line_text) > DESCRIPTION_LINE_MAX_WIDTH:
            display_line_text = display_line_text[: DESCRIPTION_LINE_MAX_WIDTH - 1] + "…"
        elif has_overflow_after_limit:
            display_line_text += "…"
        summary_line_texts.append(display_line_text)

    return parsed_title_text, tuple(summary_line_texts)


def print_prd_detail_block(prd_record: PrdRecord, palette: Palette) -> None:
    """在表格行下方打印该 PRD 的标题与描述摘要块。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录，须已由 ``collect_prd_record``
            填充 ``title`` 与 ``summary_lines``。
        palette (Palette): 颜色包装器。
    """
    if prd_record.title:
        print(DETAIL_BLOCK_INDENT + palette.bold(prd_record.title))
    for summary_line_text in prd_record.summary_lines:
        print(DETAIL_BLOCK_INDENT + palette.dim("▏ ") + summary_line_text)

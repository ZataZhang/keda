#!/usr/bin/env python3
"""PRD 验收状态的读取：验收状态横幅与验收清单勾选计数。

PRD 头部的验收状态横幅（⬜ 未开工 / 🧍 待人工验收 / ✅ 已验收）与 §9 验收清单的勾选
进度，是看板判断"这条 PRD 现在欠谁的活"的两个原始信号；执行锁的进度提示也读同一份计数。

本模块是 prd skill 契约解析（``skills/prd/scripts/prd_contract.py``）的**随模板同步副本**：
skill 是可选安装的、不随 sync 分发，本模块却要随模板同步到每个派生项目，二者不能互相
import，只能各带一份读法。读法一旦漂移，已归档却仍待人确认的 PRD 就会从看板里消失，或者
清单进度与门禁说的不是同一回事——因此模板仓库内有一份守卫测试
（``tests/guards/test_prd_skill_checker.py``）逐例比对两份实现，含对仓库真实 ``tasks/``
语料的差分。改这里的读法必须同步改契约解析与那份测试。

本模块是叶子：只依赖标准库，谁都可以安全 import（``prd_lock.py`` 读清单进度时不会再与
看板脚本互相 import 成环；它仍按延迟 import 使用本模块，单独被拷走时静默跳过进度提示）。
"""

from __future__ import annotations

import re

# 验收清单章节：``## Acceptance Checklist`` / ``## 验收清单``，允许编号前缀与双语后缀；
# 章节止于下一个 ``## `` 标题。与 prd_contract.ACCEPTANCE_CHECKLIST_HEADING_RE 逐字等价。
CHECKLIST_HEADING_PATTERN = re.compile(
    r"^##\s+(?:\d+\.\s+)?(?:Acceptance Checklist\b.*|验收清单.*)\s*$"
)
TOP_LEVEL_HEADING_PATTERN = re.compile(r"^##\s+")
# 只认 ``- [ ]`` / ``- [x]`` / ``- [X]``（``-`` ``*`` ``+`` 三种列表符，标记与括号之间不留空）。
# ``[~]`` 是 runner 门禁的占位，既不算已勾也不算未勾，因此刻意不在此列。
CHECKBOX_PATTERN = re.compile(r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]")
CODE_FENCE_PATTERN = re.compile(r"^\s*(?:```|~~~)")

# 验收状态横幅（Acceptance Status Banner）：PRD 头部紧接交付前置横幅的引用块，
# 是 §9 验收清单的投影，只有 ⬜ 未开工 / 🧍 待人工验收 / ✅ 已验收 三个状态。
# ``可归档`` 是 ``已验收`` 的旧称（归档曾经要等人工验收完成），存量 PRD 一律按已验收读。
ACCEPTANCE_STATUS_BANNER_PATTERN = re.compile(
    r"^\s*>\s*.*?(?:验收状态|Acceptance Status)", re.IGNORECASE
)
ACCEPTANCE_STATUS_NOT_STARTED = "not_started"
ACCEPTANCE_STATUS_AWAITING_HUMAN = "awaiting_human"
ACCEPTANCE_STATUS_ACCEPTED = "accepted"
ACCEPTANCE_STATUS_TOKENS = (
    (ACCEPTANCE_STATUS_NOT_STARTED, "未开工"),
    (ACCEPTANCE_STATUS_AWAITING_HUMAN, "待人工验收"),
    (ACCEPTANCE_STATUS_ACCEPTED, "已验收"),
    (ACCEPTANCE_STATUS_ACCEPTED, "可归档"),
)


def count_checklist_items(prd_text: str) -> tuple[int, int]:
    """统计验收清单的勾选情况。

    只数清单章节内的复选框，围栏代码块里的内容不算；``[~]`` 门禁项不计入。分组（含
    ``Human-Confirmed``）不影响计数——进度是"全部复选框里勾了几个"，谁欠这些空框由横幅
    与归档门禁表达，不由本函数推断。

    Args:
        prd_text (str): PRD 文件全文。

    Returns:
        tuple[int, int]: ``(已勾选数量, 总数)``；未找到清单标题时返回 ``(0, 0)``。
    """
    raw_lines_list = prd_text.splitlines()
    section_start_index = next(
        (
            line_index
            for line_index, raw_line_text in enumerate(raw_lines_list)
            if CHECKLIST_HEADING_PATTERN.match(raw_line_text)
        ),
        None,
    )
    if section_start_index is None:
        return 0, 0

    section_end_index = next(
        (
            line_index
            for line_index in range(section_start_index + 1, len(raw_lines_list))
            if TOP_LEVEL_HEADING_PATTERN.match(raw_lines_list[line_index])
        ),
        len(raw_lines_list),
    )
    checked_item_count = 0
    checklist_item_count = 0
    is_in_code_block = False
    for raw_line_text in raw_lines_list[section_start_index + 1 : section_end_index]:
        if CODE_FENCE_PATTERN.match(raw_line_text):
            is_in_code_block = not is_in_code_block
            continue
        if is_in_code_block:
            continue
        checkbox_match = CHECKBOX_PATTERN.match(raw_line_text)
        if checkbox_match is None:
            continue
        checklist_item_count += 1
        if checkbox_match.group("mark") in ("x", "X"):
            checked_item_count += 1

    return checked_item_count, checklist_item_count


def parse_acceptance_status(prd_text: str) -> str:
    """解析 PRD 头部验收状态横幅的三态。

    横幅是 §9 验收清单的投影，只有 ``⬜ 未开工`` / ``🧍 待人工验收`` /
    ``✅ 已验收`` 三个状态（旧称 ``✅ 可归档`` 按 ``已验收`` 读），且带可 grep 的
    字面量 ``验收状态``（或 ``Acceptance Status``）。只在引用块行（``>`` 开头）里
    查找，避免把正文或决策日志里对该横幅的讨论误当成状态声明；命中首行横幅后取
    标记之后**最先出现**的状态词，模板自带的括号说明（``未开工（…改为 🧍 待人工
    验收…）``）因此不会把默认态误判成待人工。看板只认这一处显式声明：PRD 没写
    横幅或状态词无法识别时返回空串，不按 §9 未勾项的结构反推——真实的待人工项常挂
    在 ``Human-Confirmed`` 之外的小节下（手动执行的 probe、交付回复要求），
    结构推断会漏判，也会把尚未完工的 PRD 误报成"等你验收"。

    Args:
        prd_text (str): PRD 文件全文。

    Returns:
        str: ``"not_started"`` / ``"awaiting_human"`` / ``"accepted"``；
        无横幅或状态词无法识别时返回空串。
    """
    for raw_line_text in prd_text.splitlines():
        banner_match = ACCEPTANCE_STATUS_BANNER_PATTERN.match(raw_line_text)
        if banner_match is None:
            continue
        raw_remainder_text = raw_line_text[banner_match.end() :]
        matched_state_offsets_list = [
            (raw_remainder_text.find(raw_token_text), raw_state_text)
            for raw_state_text, raw_token_text in ACCEPTANCE_STATUS_TOKENS
            if raw_token_text in raw_remainder_text
        ]
        if not matched_state_offsets_list:
            return ""
        return min(matched_state_offsets_list)[1]
    return ""

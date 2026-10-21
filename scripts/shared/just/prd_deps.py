#!/usr/bin/env python3
"""PRD 看板 DEPS 列：§8 Delivery Dependencies 的解析与渲染。

``prd_status.py`` 把每条 PRD 的 §8 解析成 ``(gate, refs)`` 存进记录，渲染时再按依赖
所在目录判定交付状态。依赖可见性是开工前判断交付顺序的唯一入口：被挡住的下游
PRD 不能静默显示 ``-``，所以三态（``⛔ blocked`` / ``? 无法判定`` / ``✔ deps ok``）
的判定与渲染都收在这里，由守卫测试逐态钉死。

本模块不 import ``prd_status``（它依赖本模块）；``PrdRecord`` / ``Palette`` 只在类型标注里
出现，走 ``TYPE_CHECKING``，与 ``prd_detail.py`` 同一做法，避免循环依赖与脚本以
``__main__`` 运行时的二次导入。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import prd_locator

if TYPE_CHECKING:
    from prd_status import Palette, PrdRecord

DEPENDENCY_SECTION_PATTERN = re.compile(
    r"^##\s+(?:\d+[.、]\s*)?(?:Delivery Dependencies|交付依赖)\s*$"
)
DEPENDENCY_GATE_PATTERN = re.compile(
    r"^[-*]\s*Gate type\s*[:：]\s*(?P<gate>.+?)\s*$", re.IGNORECASE
)
DEPENDENCY_REFS_HEADING_PATTERN = re.compile(
    r"^[-*]\s*Depends on tasks/issues\s*[:：]\s*$", re.IGNORECASE
)
DEPENDENCY_NESTED_REF_PATTERN = re.compile(r"^\s+[-*]\s+(?P<ref>.+?)\s*$")

DEPENDENCY_SLUG_MAX_WIDTH = 26


def parse_delivery_dependencies(prd_text: str) -> tuple[str, tuple[str, ...]]:
    """解析 §8 Delivery Dependencies 的 gate 类型与任务/Issue 引用。

    只消费 ``Gate type`` 与 ``Depends on tasks/issues`` 两个字段；组依赖
    （``Depends on groups``）需要展开成员，不参与本地判定，直接忽略。

    Args:
        prd_text (str): PRD 文件全文。

    Returns:
        tuple[str, tuple[str, ...]]: ``(gate 类型, 引用 token 元组)``；gate 统一
        小写，未声明时为空串；``none`` 引用在解析阶段剔除，无 §8 章节时返回
        ``("", ())``。
    """
    raw_lines_list = prd_text.splitlines()
    section_start_index = -1
    for line_index, raw_line_text in enumerate(raw_lines_list):
        if DEPENDENCY_SECTION_PATTERN.match(raw_line_text):
            section_start_index = line_index + 1
            break
    if section_start_index < 0:
        return "", ()

    gate_text = ""
    dependency_refs_list: list[str] = []
    is_collecting_refs = False
    for raw_line_text in raw_lines_list[section_start_index:]:
        if raw_line_text.startswith("## "):
            break
        gate_match = DEPENDENCY_GATE_PATTERN.match(raw_line_text)
        if gate_match:
            gate_text = gate_match.group("gate").strip().strip("`").lower()
            is_collecting_refs = False
            continue
        if DEPENDENCY_REFS_HEADING_PATTERN.match(raw_line_text):
            is_collecting_refs = True
            continue
        if is_collecting_refs:
            ref_match = DEPENDENCY_NESTED_REF_PATTERN.match(raw_line_text)
            if not ref_match:
                is_collecting_refs = False
                continue
            raw_ref_text = ref_match.group("ref").strip().strip("`").strip()
            if raw_ref_text and raw_ref_text.lower() != "none":
                dependency_refs_list.append(raw_ref_text)

    return gate_text, tuple(dependency_refs_list)


def classify_dependency_ref(raw_ref: str, pending_dir: Path, archive_dir: Path) -> tuple[str, str]:
    """判定单条依赖引用的交付状态。

    引用可能是 ``tasks/pending/xxx.md`` 完整路径、裸文件名或 stem、或 ``#123``
    Issue 引用。PRD 引用按所在目录判定：``tasks/pending`` 下 = 未交付；
    ``tasks/archive`` 下 = 已交付；两处都无 = 悬空引用，无法判定。Issue 引用
    需要远端状态，本地一律视为无法判定。

    Args:
        raw_ref (str): 依赖引用原文（已去反引号与首尾空白）。
        pending_dir (Path): ``tasks/pending`` 目录。
        archive_dir (Path): ``tasks/archive`` 目录。

    Returns:
        tuple[str, str]: ``(kind, display)``；kind 为 ``"pending"`` /
        ``"delivered"`` / ``"unknown"``，display 为展示用 slug（Issue 引用
        返回 ``#<编号>``）。
    """
    if raw_ref.startswith("#") or raw_ref.isdigit():
        return "unknown", f"#{raw_ref.lstrip('#')}"

    raw_stem_text = Path(raw_ref).name
    if raw_stem_text.endswith(".md"):
        raw_stem_text = raw_stem_text[: -len(".md")]
    _, _, _, raw_slug_text = prd_locator.parse_prd_filename(Path(f"{raw_stem_text}.md"))

    if (pending_dir / f"{raw_stem_text}.md").is_file():
        return "pending", raw_slug_text
    if (archive_dir / f"{raw_stem_text}.md").is_file():
        return "delivered", raw_slug_text
    return "unknown", raw_slug_text


def shorten_dependency_slug(slug_text: str) -> str:
    """把依赖 slug 截断到表格可容纳的宽度，超宽时以省略号结尾。"""
    if len(slug_text) <= DEPENDENCY_SLUG_MAX_WIDTH:
        return slug_text
    return slug_text[: DEPENDENCY_SLUG_MAX_WIDTH - 1] + "…"


def format_deps_cell(
    prd_record: PrdRecord, pending_dir: Path, archive_dir: Path, palette: Palette
) -> str:
    """格式化交付依赖列（DEPS）。

    ``hard`` gate 三态：存在仍在 ``tasks/pending`` 的依赖 → 红色
    ``⛔ blocked by <slug>``（多个追加 ``+N``）；无未交付依赖但含本地无法判定的
    引用（Issue 号、悬空路径）→ 黄色 ``? <slug>``；全部满足 → 绿色
    ``✔ deps ok``。``soft`` gate 仅在存在未交付依赖时以暗色 ``soft → <slug>``
    提示。``none`` gate 或无 §8 章节 → ``-``（无需关注的默认态）。

    Args:
        prd_record (PrdRecord): 单条 PRD 记录。
        pending_dir (Path): ``tasks/pending`` 目录。
        archive_dir (Path): ``tasks/archive`` 目录。
        palette (Palette): 颜色包装器。

    Returns:
        str: 依赖列单元格文本。
    """
    raw_gate_text = prd_record.dependency_gate
    if not raw_gate_text or raw_gate_text == "none":
        return palette.dim("-")

    blocked_slugs_list: list[str] = []
    unknown_slugs_list: list[str] = []
    for raw_ref_text in prd_record.dependency_refs:
        ref_kind, ref_display_text = classify_dependency_ref(raw_ref_text, pending_dir, archive_dir)
        if ref_kind == "pending":
            blocked_slugs_list.append(ref_display_text)
        elif ref_kind == "unknown":
            unknown_slugs_list.append(ref_display_text)

    if raw_gate_text == "hard":
        if blocked_slugs_list:
            raw_blocked_text = shorten_dependency_slug(blocked_slugs_list[0])
            if len(blocked_slugs_list) > 1:
                raw_blocked_text += f" +{len(blocked_slugs_list) - 1}"
            return palette.red(f"⛔ blocked by {raw_blocked_text}")
        if unknown_slugs_list:
            raw_unknown_text = ", ".join(
                shorten_dependency_slug(slug_text) for slug_text in unknown_slugs_list[:2]
            )
            if len(unknown_slugs_list) > 2:
                raw_unknown_text += f" +{len(unknown_slugs_list) - 2}"
            return palette.yellow(f"? {raw_unknown_text}")
        return palette.green("✔ deps ok")

    if blocked_slugs_list:
        return palette.dim(f"soft → {shorten_dependency_slug(blocked_slugs_list[0])}")
    return palette.dim("-")

"""PRD 结构化变更记录（Change Log）的**适配层**。

解析由 prd skill 的 ``scripts/prd_contract.py`` 承担（Machine Contract §1：章节
标题、``###`` 条目、六个必填字段），本模块只把它的 JSON 映射成 keda 各处沿用的
:class:`PrdChangeLogResult`，使"契约文本 vs 执行实现"不会再各自漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.core.shared.prd_contract_client import parse_prd_contract


@dataclass(frozen=True)
class PrdChangeLogResult:
    """PRD Change Log 的解析结果。

    Attributes:
        section_found: 是否存在顶级 Change Log/变更记录章节。
        entry_count: 章节中记录到的变更条数。
        incomplete_entry_fields: 每条不完整记录缺少的字段，键为 1-based 条目序号。
        entry_titles: 各条记录的 ``###`` 标题文本，顺序与 ``entry_count`` 一致；
            供 runner 算出"本轮新增了哪条 Change Log"。
    """

    section_found: bool
    entry_count: int
    incomplete_entry_fields: dict[int, tuple[str, ...]]
    entry_titles: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """返回 Change Log 是否包含至少一条完整记录。"""
        return self.section_found and self.entry_count > 0 and not self.incomplete_entry_fields


def parse_prd_change_log(file_content: str) -> PrdChangeLogResult:
    """解析 PRD 的结构化 Change Log。

    Change Log 与 Acceptance Checklist 有不同职责：前者记录需求本身为何
    演进，后者只记录已经完成的验收项。每条 Change Log 必须包含类型、原文、
    变更后、原因、影响和审核字段。

    Args:
        file_content: PRD Markdown 原文。

    Returns:
        Change Log 的章节、条目数和字段完整性信息。

    Raises:
        PrdContractError: prd skill 的解析脚本不可用（缺失 / 执行失败 / 输出非法）。
    """
    change_log = parse_prd_contract(file_content)["change_log"]
    entries: list[dict[str, Any]] = change_log.get("entries", [])
    incomplete_entry_fields = {
        entry_number: tuple(str(field) for field in entry.get("missing_fields", ()))
        for entry_number, entry in enumerate(entries, start=1)
        if entry.get("missing_fields")
    }
    return PrdChangeLogResult(
        section_found=bool(change_log.get("section_found")),
        entry_count=int(change_log.get("entry_count", len(entries))),
        incomplete_entry_fields=incomplete_entry_fields,
        entry_titles=tuple(str(entry.get("title", "")) for entry in entries),
    )


def extract_prd_change_log_entry_count(file_content: str) -> int:
    """返回 PRD Change Log 的条目数量，供 runner 检查本轮是否追加记录。

    Args:
        file_content: PRD Markdown 原文。

    Returns:
        ``## Change Log`` 中的三级标题条目数；没有章节时返回 0。

    Raises:
        PrdContractError: prd skill 的解析脚本不可用（缺失 / 执行失败 / 输出非法）。
    """
    return parse_prd_change_log(file_content).entry_count

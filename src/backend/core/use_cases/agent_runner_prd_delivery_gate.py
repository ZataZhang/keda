"""PRD delivery gating: acceptance-checklist and change-log validation.

Extracted from :mod:`backend.core.use_cases.agent_runner_feedback` to keep that
module under the repository's 1000-non-empty-line hard limit. Behavior is
unchanged: the helpers are imported back into ``agent_runner_feedback`` so the
existing call sites and the public ``PrdDeliveryError`` re-export keep working.
"""

from __future__ import annotations

from backend.core.shared.models.agent_runner import (
    DeliveryGateError,
    DeliveryGateFailureKind,
)
from backend.core.shared.prd_change_log import (
    extract_prd_change_log_entry_count,
    parse_prd_change_log,
)
from backend.core.shared.prd_checklist import PrdChecklistResult, parse_prd_checklist
from backend.core.shared.prd_machine_contract import PRD_MACHINE_CONTRACT_POINTER


class PrdDeliveryError(DeliveryGateError):
    """Raised when the canonical PRD is not ready for delivery.

    每个 ``raise`` 处显式声明 ``kind``；不声明时按
    :attr:`DeliveryGateFailureKind.SUBSTANTIVE` 处理（整轮重跑）。
    """


def _format_unchecked_items(
    unchecked_items: list[tuple[int, str]],
) -> str:
    """Format unchecked checklist items for error messages."""
    return "\n".join(f"  - L{line}: {text}" for line, text in unchecked_items)


def _human_group_missing_hint(checklist_result: PrdChecklistResult) -> str:
    """清单提到 Human-Confirmed 却没识别出该分组时，补一句可行动的诊断。

    只在"提到过 Human-Confirmed 但分组没被识别"时追加：这两种信号同时成立，
    说明这些条目多半本就属于人属项、只是分组写法没被解析，而不是 executor 漏勾。
    把病因直接写进报错，agent 才可能去修结构，而不是在不可满足的指令之间空转
    （实证：ai-assistant Issue #53 的 closeout/repair 循环）。

    刻意**不给出具体写法**：分组该写成什么形式由 prd skill 的 Machine Contract
    定义，runner 只负责报出"没识别到分组"这个事实并指向契约（见
    ``_build_prd_closeout_instruction`` 的同一条纪律）。在这里复述写法会把格式
    规则变成 keda 的第二出处——甚至可能发明契约里并不存在的变体。

    Args:
        checklist_result: 本次清单解析结果。

    Returns:
        需要提示时返回以空行开头的诊断段落，否则返回空串。
    """
    if checklist_result.human_group_found or not checklist_result.human_confirmed_mentioned:
        return ""
    return (
        "\n\nSuspected checklist-structure problem: this section mentions `Human-Confirmed` "
        "but no Human-Confirmed group was recognized, so those items are being treated as "
        "executor items. A human-owned item must stay `- [ ]` — do not tick it and do not "
        "rewrite it as `- [~]` here. Re-read the group's formatting against the contract "
        f"instead of guessing: {PRD_MACHINE_CONTRACT_POINTER}"
    )


def _validate_prd_checklist(
    file_content: str,
    prd_relative_path: str,
) -> bool:
    """Validate executor items and report whether human review remains.

    Args:
        file_content: PRD file text.
        prd_relative_path: Relative path used in error messages.

    Returns:
        ``True`` 表示执行项已完成，但 PR 仍需人工确认。

    Raises:
        PrdDeliveryError: When the checklist section is missing or has
            unchecked items.
    """
    checklist_result = parse_prd_checklist(file_content)
    if not checklist_result.section_found:
        # 章节整体缺失说明 PRD 结构有问题，不是"漏勾了几个框"——保持真失败类。
        raise PrdDeliveryError(f"Acceptance Checklist section missing in {prd_relative_path}")
    if checklist_result.execution_unchecked_items:
        unchecked_summary = _format_unchecked_items(checklist_result.execution_unchecked_items)
        raise PrdDeliveryError(
            f"Acceptance Checklist has unchecked items in {prd_relative_path}:\n"
            f"{unchecked_summary}{_human_group_missing_hint(checklist_result)}",
            kind=DeliveryGateFailureKind.CHECKLIST_UNCHECKED,
        )
    return bool(checklist_result.human_pending_items)


def _validate_prd_change_log(
    *,
    file_content: str,
    baseline_content: str | None,
    prd_relative_path: str,
) -> None:
    """当本轮修改 canonical PRD 时，要求附带完整 Change Log。"""
    if baseline_content is None or file_content == baseline_content:
        return
    baseline_entry_count = extract_prd_change_log_entry_count(baseline_content)
    change_log_result = parse_prd_change_log(file_content)
    if not change_log_result.section_found:
        raise PrdDeliveryError(
            f"Canonical PRD changed without a Change Log section: {prd_relative_path}",
            kind=DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE,
        )
    if change_log_result.entry_count == 0:
        raise PrdDeliveryError(
            f"Canonical PRD changed without a Change Log entry: {prd_relative_path} "
            "(a `## Change Log` section exists but no entry was parsed; entries must be "
            "`###` headings with bullet fields — Markdown table rows are not counted)",
            kind=DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE,
        )
    if change_log_result.entry_count <= baseline_entry_count:
        raise PrdDeliveryError(
            f"Canonical PRD changed without appending a Change Log entry: {prd_relative_path}",
            kind=DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE,
        )
    if change_log_result.incomplete_entry_fields:
        missing_by_entry = "; ".join(
            f"entry {entry_number}: {', '.join(missing_fields)}"
            for entry_number, missing_fields in change_log_result.incomplete_entry_fields.items()
        )
        raise PrdDeliveryError(
            f"Canonical PRD Change Log is incomplete in {prd_relative_path}: {missing_by_entry}",
            kind=DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE,
        )

"""交付门禁失败后的短命收尾修复（closeout pass）。

从 :mod:`backend.core.use_cases.run_agent_execution_loop` 拆分而来：单文件非空行
有 CI 硬上限，而这里是相对独立的一件事——用一次受限范围的 agent 收尾尝试接住交付
门禁失败，失败则连同它对 PRD 的编辑一起回滚，并把"上轮试过什么"回传给下一次
attempt。attempt 记录复用 :mod:`backend.core.use_cases.agent_runner_attempt_recording`。
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.core.shared.models.agent_runner import DeliveryGateError, FailureType
from backend.core.use_cases.agent_runner_attempt_recording import (
    _emit_agent_usage,
    _record_attempt,
)
from backend.core.use_cases.agent_runner_closeout import (
    CloseoutPromptContext,
    CloseoutSnapshot,
    build_closeout_allowed_scope,
    capture_closeout_snapshot,
    find_closeout_scope_violations,
    format_closeout_attempt_detail,
    restore_prd_from_snapshot,
    run_closeout_agent,
    summarize_closeout_changes,
)
from backend.core.use_cases.agent_runner_validation import (
    ensure_no_misplaced_evidence_helpers,
    ensure_validation_commands_pass,
    ensure_validation_evidence_ready,
)
from backend.core.use_cases.lifecycle_agent_resolution import (
    resolve_lifecycle_agent,
    resolve_lifecycle_model_selection,
)
from backend.core.use_cases.run_agent_once import _logger, ensure_prd_delivery_ready

if TYPE_CHECKING:
    from backend.core.use_cases.agent_runner_attempt_recording import _AttemptRecordContext
    from backend.core.use_cases.run_agent_execution_loop import AgentExecutionRequest


@dataclass(frozen=True)
class _DeliveryCloseoutContext:
    """一次交付收尾所需的上下文：attempt 记录上下文 + 门禁失败 + PRD 基线。"""

    record: _AttemptRecordContext
    gate_failure: DeliveryGateError
    prd_baseline_content: str | None

    @property
    def request(self) -> AgentExecutionRequest:
        """Return the execution request this closeout belongs to."""
        return self.record.request


@dataclass(frozen=True)
class _CloseoutAttemptResult:
    """一次交付收尾 pass 的结果。

    Attributes:
        revalidated: 收尾 pass 未越界、且门禁链重跑通过时为 True。
        discarded_detail: 收尾判失败时，被撤销掉的那一轮收尾改动明细（runner 自行
            比对得出，非 agent 自述）。收尾改动一律回滚，但明细会转交给下一次
            attempt——否则重跑只能从零猜起，既白做一遍又拿不到"上轮试过什么"。
    """

    revalidated: bool
    discarded_detail: str = ""


def _render_discarded_closeout_note(result: _CloseoutAttemptResult) -> str:
    """把被撤销的收尾改动渲染成给下一次 attempt 的上下文。

    Args:
        result: 本次收尾 pass 的结果。

    Returns:
        需要提示时返回以空行开头的段落，否则返回空串。
    """
    if not result.discarded_detail:
        return ""
    return (
        "\n\nA delivery-closeout pass already ran for this failure and its checklist edits "
        "were rolled back (keep that in mind; do not assume they are still applied). "
        "Runner-measured record of what it did:\n" + result.discarded_detail
    )


def _revert_failed_closeout(
    context: _DeliveryCloseoutContext,
    before_snapshot: CloseoutSnapshot,
) -> None:
    """收尾判失败后撤销它对 canonical PRD 的编辑。

    失败的收尾一个字节都不该留下：它可能已经勾上了举不出证据的验收条目，而清单
    这道门禁只问"还有没有未勾项"，留着就等于让随后的完整重跑把那个凭空的勾当成
    既成事实收下。撤销只针对 PRD——证据目录不进代码 diff 且会被独立重判，越界写入
    的其他文件按设计交给完整重跑处理。

    Args:
        context: 本次收尾的上下文。
        before_snapshot: 收尾前采集的快照，提供还原用的 PRD 原文。
    """
    if restore_prd_from_snapshot(
        context.request.issue, context.request.worktree_path, before_snapshot
    ):
        _logger.info(
            "Reverted the failed closeout's PRD edits for Issue #%d.",
            context.request.issue.number,
        )


def _attempt_delivery_closeout(
    context: _DeliveryCloseoutContext,
    *,
    prd_overrides: Mapping[str, str] | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> _CloseoutAttemptResult:
    """尝试用一次短命的收尾修复接住交付门禁失败。

    只接住被抛出点标记为收尾类的失败；真失败与收尾层被关闭时立刻返回未通过，
    调用方走本层落地前的整轮重跑路径。``revalidated`` 为 True 表示收尾 pass
    没有越界、完整门禁链已重跑通过，本轮可以继续原流程。

    Args:
        context: 本次收尾的执行请求、attempt 计时与 PRD 基线。
        prd_overrides: PRD 文件头部 lifecycle_agents 覆盖（最高优先级）。
        prd_preset_overrides: PRD 文件头部 / CLI 传入的阶段 -> 预设绑定。

    Returns:
        :class:`_CloseoutAttemptResult`；``revalidated`` 仅在门禁链重跑通过时为
        True，其余一律 False，并尽量带上被回滚的收尾改动明细。
    """
    request = context.request
    config = request.config
    issue = request.issue
    gate_failure = context.gate_failure
    if not gate_failure.kind.is_closeout_eligible:
        return _CloseoutAttemptResult(revalidated=False)
    if not config.runner.closeout_agent_enabled:
        _logger.info(
            "Closeout Agent disabled for Issue #%d; escalating %s gate failure to full recovery.",
            issue.number,
            gate_failure.kind.value,
        )
        return _CloseoutAttemptResult(revalidated=False)

    worktree_path = request.worktree_path
    process_runner = request.process_runner
    allowed_scope = build_closeout_allowed_scope(issue, config)
    before_snapshot = capture_closeout_snapshot(issue, worktree_path, config, process_runner)
    try:
        with context.record.attempt_phases.measure("closeout"):
            closeout_agent_result = run_closeout_agent(
                resolve_lifecycle_agent(
                    "closeout",
                    config,
                    issue=issue,
                    selected_agent=request.selected_agent,
                    prd_overrides=prd_overrides,
                    prd_preset_overrides=prd_preset_overrides,
                ),
                config,
                process_runner,
                prompt_context=CloseoutPromptContext(
                    issue=issue,
                    worktree_path=worktree_path,
                    gate_failure_message=str(gate_failure),
                    kind=gate_failure.kind,
                    allowed_scope=allowed_scope,
                ),
                # closeout 未自绑预设时继承实现者的绑定；换人丢弃在 resilient 层。
                model_selection=(
                    resolve_lifecycle_model_selection(
                        "closeout",
                        config,
                        issue=issue,
                        prd_preset_overrides=prd_preset_overrides,
                    )
                    or context.record.effective_model_selection
                ),
            )
        # 发射点 agent 名与调用点同源（resolve_lifecycle_agent 纯查找、幂等；AST 守卫要求内联形态）。
        _emit_agent_usage(
            request,
            "closeout",
            resolve_lifecycle_agent(
                "closeout",
                config,
                issue=issue,
                selected_agent=request.selected_agent,
                prd_overrides=prd_overrides,
            ),
            closeout_agent_result.token_usage,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        _logger.warning("Closeout Agent failed for Issue #%d: %s", issue.number, exc)
        _revert_failed_closeout(context, before_snapshot)
        return _CloseoutAttemptResult(revalidated=False)

    after_snapshot = capture_closeout_snapshot(issue, worktree_path, config, process_runner)
    scope_violations = find_closeout_scope_violations(
        before_snapshot, after_snapshot, allowed_scope
    )
    if scope_violations:
        _logger.warning(
            "Closeout Agent modified out-of-scope files for Issue #%d (%s); "
            "escalating to full recovery.",
            issue.number,
            ", ".join(scope_violations),
        )
        _revert_failed_closeout(context, before_snapshot)
        return _CloseoutAttemptResult(revalidated=False)

    try:
        ensure_prd_delivery_ready(
            issue,
            worktree_path,
            process_runner,
            prd_baseline_content=context.prd_baseline_content,
        )
        ensure_validation_evidence_ready(issue, worktree_path, config, process_runner)
        ensure_no_misplaced_evidence_helpers(worktree_path, config, process_runner)
        ensure_validation_commands_pass(issue, worktree_path, config, process_runner)
    except DeliveryGateError as exc:
        # 收尾改动一律回滚（勾选门禁没有独立验证源，留着凭空的勾就成了既成事实），
        # 但它到底做了什么由 runner 自行比对得出，转交给下一次 attempt——
        # 否则重跑只能从零猜起，白做一遍还拿不到"上轮试过什么"。
        discarded_summary = summarize_closeout_changes(before_snapshot, after_snapshot)
        _logger.warning(
            "Delivery gates still fail after closeout for Issue #%d; "
            "escalating to full recovery: %s",
            issue.number,
            exc,
        )
        _revert_failed_closeout(context, before_snapshot)
        return _CloseoutAttemptResult(
            revalidated=False,
            discarded_detail=format_closeout_attempt_detail(discarded_summary),
        )

    # 门禁链已在收尾流程内整体重跑，PRD 可能刚被归档，因此留痕用的"收尾后"快照
    # 必须重取一次，否则新归档路径下的 PRD 文本会被当成"消失了"。
    final_snapshot = capture_closeout_snapshot(issue, worktree_path, config, process_runner)
    closeout_summary = summarize_closeout_changes(before_snapshot, final_snapshot)
    _record_attempt(
        context.record,
        failure_type=FailureType.DELIVERY_CLOSEOUT,
        detail=format_closeout_attempt_detail(closeout_summary),
        recovered=True,
    )
    _logger.info(
        "Closeout Agent repaired the %s gate failure for Issue #%d; continuing this attempt.",
        gate_failure.kind.value,
        issue.number,
    )
    return _CloseoutAttemptResult(revalidated=True)

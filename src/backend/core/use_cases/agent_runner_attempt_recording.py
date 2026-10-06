"""Agent 执行循环里的 attempt 记录与失败分类。

从 :mod:`backend.core.use_cases.run_agent_execution_loop` 拆分而来：单文件非空行
有 CI 硬上限，而主循环要保留的是状态机本身——每条失败分支都要重复的"记一条
attempt + 通知增量持久化 + 写短期记忆"样板搬到这里集中承载，行为不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import (
    AttemptResult,
    CommandResult,
    FailureType,
    TokenUsage,
)
from backend.core.use_cases.agent_runner_memory import _persist_short_term_memory
from backend.core.use_cases.run_agent_once import (
    AttemptPhaseTimer,
    _append_attempt_and_notify,
    _logger,
    _make_attempt_result,
    classify_failure,
    get_head_sha,
)

if TYPE_CHECKING:
    from backend.core.use_cases.run_agent_execution_loop import AgentExecutionRequest


@dataclass(frozen=True)
class _AttemptRecordContext:
    """一次 attempt 的记录上下文。

    执行循环里每个失败分支都要"记一条 attempt + 通知增量持久化 + 写短期记忆"，
    这三步共用同一组 attempt 级状态；逐个参数展开会在每个分支复制一段二十多行的
    样板，因此收敛成一个上下文对象由 :func:`_record_attempt` 消费。
    """

    request: AgentExecutionRequest
    attempt_index: int
    attempt_phases: AttemptPhaseTimer
    attempt_started_mono: float
    attempt_started_iso: str
    attempt_results: list[AttemptResult]
    repo_id: str
    #: Phase 1 agent 调用自报的 token 用量；调用失败（无结果对象）时为 None。
    token_usage: TokenUsage | None = None
    #: 本 attempt 生效的模型选择（已按执行 agent 校验；绑定被丢弃后为 ``None``）。
    effective_model_selection: ModelSelection | None = None


def _emit_agent_usage(
    request: AgentExecutionRequest,
    flow: str,
    agent_name: str,
    usage: TokenUsage | None,
) -> None:
    """旁路发出一次非 attempt 主体的 agent 调用用量；任何失败都不阻断主流程。"""
    if request.on_agent_usage is None or usage is None:
        return
    try:
        request.on_agent_usage(flow, agent_name, usage)
    except Exception:  # noqa: BLE001 - observation must not break the main flow.
        _logger.warning("Agent usage observation callback failed (flow=%s).", flow, exc_info=True)


def _record_attempt(
    context: _AttemptRecordContext,
    *,
    failure_type: FailureType,
    detail: str,
    recovered: bool = False,
) -> AttemptResult:
    """记一条 attempt，通知增量持久化回调，并写入短期记忆。

    Args:
        context: 当前 attempt 的记录上下文。
        failure_type: 本次 attempt 的分类结果。
        detail: 写进 attempt 历史与 Issue 评论的诊断文本。
        recovered: 本次 attempt 是否从先前的失败中恢复。

    Returns:
        刚刚记录的 :class:`AttemptResult`。
    """
    effective_selection = context.effective_model_selection
    _append_attempt_and_notify(
        context.attempt_results,
        _make_attempt_result(
            attempt_number=context.attempt_index + 1,
            failure_type=failure_type,
            recovered=recovered,
            detail=detail,
            agent=context.request.selected_agent,
            started_mono=context.attempt_started_mono,
            started_iso=context.attempt_started_iso,
            phase_durations=context.attempt_phases.snapshot(),
            token_usage=context.token_usage,
            preset=(effective_selection.preset_name if effective_selection is not None else ""),
            model=effective_selection.model if effective_selection is not None else "",
        ),
        context.request.on_attempt_recorded,
    )
    recorded_attempt = context.attempt_results[-1]
    _persist_short_term_memory(
        config=context.request.config,
        issue=context.request.issue,
        worktree_path=context.request.worktree_path,
        attempt=recorded_attempt,
        repo_id=context.repo_id,
    )
    return recorded_attempt


def _classify_and_record_gate_failure(
    context: _AttemptRecordContext,
    *,
    detail: str,
    verification_results: list[CommandResult],
    exc: BaseException | None,
) -> FailureType:
    """给一次"代码已在 worktree、尚未提交"的失败分类并记一条 attempt。

    Phase 2 验证、Phase 3 PRD 交付、Phase 3.5 证据门禁、Phase 4 暂存后验证四处的
    失败形状完全一致（读一次 HEAD、按未提交状态分类、记一条 attempt），差别只在
    诊断文本与传给分类器的上下文，因此共用本函数而不是各写一遍。

    Args:
        context: 当前 attempt 的记录上下文。
        detail: 写进 attempt 历史的诊断文本。
        verification_results: 传给分类器的验证结果。
        exc: 触发本次失败的异常；``None`` 表示失败由验证结果本身表达。

    Returns:
        分类结果，供调用方决定 recovery 措辞与升级路径。
    """
    request = context.request
    failure_type = classify_failure(
        before_sha=request.before_sha,
        after_sha=get_head_sha(request.worktree_path, request.process_runner),
        has_uncommitted=False,
        agent_result=CommandResult(("",), 0, "", ""),
        verification_results=verification_results,
        exc=exc,
    )
    _record_attempt(context, failure_type=failure_type, detail=detail)
    return failure_type

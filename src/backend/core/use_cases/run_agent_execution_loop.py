"""Agent 执行、验证与 recovery 状态机。"""

from __future__ import annotations
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    AttemptResult,
    CommandResult,
    DeliveryGateError,
    FailureType,
    IssueSummary,
    TokenUsage,
)
from backend.core.use_cases.run_agent_once import (
    AttemptPhaseTimer,
    AgentUnavailableError,
    MaxRetriesExceededError,
    PrdDeliveryError,
    ProviderCapacityError,
    UnrecoverableError,
    _append_attempt_and_notify,
    _logger,
    _make_attempt_result,
    _persist_short_term_memory,
    _resolve_memory_stores,
    _resolve_repo_id,
    build_recovery_prompt,
    classify_failure,
    commit_requested_changes,
    ensure_prd_delivery_ready,
    ensure_verification_passed,
    extract_prd_path,
    failed_verification_results,
    format_agent_execution_failure,
    format_recovery_failure_summary,
    get_head_sha,
    has_changes,
    run_agent,
    run_agent_with_prompt_resilient,
    run_fix_agent,
    run_verification,
    unstage_changes,
    wait_before_recovery_attempt,
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
from backend.core.use_cases.agent_runner_failure import (
    ForbiddenBlockedError,
    is_recoverable_commit_request_error,
)
from backend.core.use_cases.agent_runner_feedback import (
    VerificationFailedError,
    format_prd_delivery_detail,
    format_prd_delivery_failure,
)
from backend.core.use_cases.agent_runner_structured_evidence import ValidationEvidenceError
from backend.core.use_cases.agent_runner_validation import (
    ensure_no_misplaced_evidence_helpers,
    ensure_validation_commands_pass,
    ensure_validation_evidence_ready,
    format_validation_evidence_detail,
    format_validation_evidence_failure,
    resolve_issue_evidence_relpath,
    warn_legacy_evidence_helpers,
)
from backend.core.use_cases.lifecycle_agent_resolution import (
    PRD_OVERRIDE_BLOCK_PRESETS,
    effective_prd_overrides,
    effective_prd_preset_overrides,
    parse_prd_lifecycle_overrides,
    resolve_lifecycle_agent,
    resolve_lifecycle_model_selection,
)
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.use_cases.run_agent_once import drop_model_selection_for_agent


@dataclass(frozen=True)
class AgentExecutionRequest:
    """Agent recovery 状态机的一次执行请求。"""

    selected_agent: str
    issue: IssueSummary
    worktree_path: Path
    config: AppConfig
    process_runner: IProcessRunner
    before_sha: str
    expected_branch: str
    prompt_override: str | None = None
    on_attempt_recorded: Callable[[AttemptResult, list[AttemptResult]], None] | None = None
    #: 旁路观测回调：本 Issue 处理期间每次非 attempt 主体的 agent 调用
    #: （fix / closeout / verifier）产出可用 usage 时以 ``(flow, agent, usage)``
    #: 回调；agent 名允许与主 agent 不同（如 verifier 走了候选回退）。
    on_agent_usage: Callable[[str, str, TokenUsage], None] | None = None
    #: 实现阶段绑定的模型选择（阶段 -> 预设解析结果）；``None`` 表示无绑定。
    #: fix / closeout 未自绑预设时继承它（同一 agent，同一模型命名空间）。
    model_selection: ModelSelection | None = None


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
        # 观测发射点的 agent 名与调用点同源：resolve_lifecycle_agent 是纯查找
        # （幂等），重解析一次以保持调用点内联形态（AST 守卫约定）。
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


def run_agent_until_committed(request: AgentExecutionRequest) -> AgentCommitResult:
    """Run the agent, recover failed verification, and return final checks.

    这是一个带 recovery 重试的状态机循环。每次尝试包含以下阶段：
    1. 运行 agent（首次）或发送 recovery prompt（重试）
    2. 运行验证命令（lint / test）
    3. 检查 PRD 交付状态（归档 pending PRD）
    4. 通过 commit proxy 提交变更

    任意阶段失败后，如果还有剩余重试次数，会构造 recovery prompt
    让 agent 在下一次尝试中修复问题。所有尝试记录都写入 attempt_results，
    最终随失败评论一起发布到 GitHub Issue。

    Args:
        selected_agent: 使用的 AI agent 名称（claude / kimi / codex）。
        issue: 当前处理的 Issue。
        worktree_path: agent 工作的 git worktree 路径。
        config: Agent Runner 配置。
        process_runner: 命令执行器。
        before_sha: 循环开始前的 HEAD SHA，用于检测是否有新提交。
        expected_branch: 期望的分支名，防止 agent 切换分支。

    Returns:
        AgentCommitResult，包含最终验证结果和尝试历史。

    Raises:
        UnrecoverableError: 遇到安全违规（forbidden paths、分支异常）不可恢复。
        MaxRetriesExceededError: 所有重试次数耗尽仍未成功。
    """
    selected_agent = request.selected_agent
    issue = request.issue
    worktree_path = request.worktree_path
    config = request.config
    process_runner = request.process_runner
    before_sha = request.before_sha
    expected_branch = request.expected_branch
    prompt_override = request.prompt_override
    max_recovery_attempts = max(0, config.runner.max_recovery_attempts)
    recovery_retry_delay_seconds = max(0, config.runner.recovery_retry_delay_seconds)
    prd_relative_path = extract_prd_path(issue.body)
    prd_baseline_content = (
        (worktree_path / prd_relative_path).read_text(encoding="utf-8")
        if prd_relative_path is not None and (worktree_path / prd_relative_path).exists()
        else None
    )
    # PRD 文件头部 lifecycle_agents 覆盖块（PRD 级，最高优先级）。优先用 Issue 上
    # 已回填的那份（编排入口从 PRD 解析）；缺失时回退到就地解析上面读出的文本。
    prd_overrides = effective_prd_overrides(issue, None)
    if not prd_overrides and prd_baseline_content is not None:
        prd_overrides = parse_prd_lifecycle_overrides(
            prd_baseline_content, prd_path=prd_relative_path
        )
    # PRD 文件头部 lifecycle_presets 覆盖块（阶段 -> 预设绑定，PRD 级）。合并与
    # 回退策略与上面同一套。
    prd_preset_overrides = effective_prd_preset_overrides(issue, None)
    if not prd_preset_overrides and prd_baseline_content is not None:
        prd_preset_overrides = parse_prd_lifecycle_overrides(
            prd_baseline_content,
            prd_path=prd_relative_path,
            block_name=PRD_OVERRIDE_BLOCK_PRESETS,
        )
    # 实现阶段绑定的模型选择：显式换人（CLI --agent / 回退候选）时在这里丢弃，
    # 保证后续 fix / closeout 继承的一定是"执行 agent == 预设 agent"的有效选择。
    model_selection = drop_model_selection_for_agent(selected_agent, request.model_selection)
    recovery_failure_summary = ""
    recovery_failure_type: str = "verification_failed"
    final_verification_results: list[CommandResult] = []
    attempt_results: list[AttemptResult] = []
    verifier_verdict = None  # set after the commit proxy fixes the reviewed tree

    # Recovery 重试循环：第 0 次是正常执行，后续是 recovery
    for attempt_index in range(max_recovery_attempts + 1):
        if attempt_index > 0:
            wait_before_recovery_attempt(
                issue.number,
                recovery_attempt=attempt_index,
                max_recovery_attempts=max_recovery_attempts,
                delay_seconds=recovery_retry_delay_seconds,
            )

        attempt_started_mono = time.monotonic()
        attempt_phases = AttemptPhaseTimer()
        attempt_started_iso = datetime.now(timezone.utc).isoformat()
        repo_id = _resolve_repo_id(issue, worktree_path)
        attempt_record_context = _AttemptRecordContext(
            request=request,
            attempt_index=attempt_index,
            attempt_phases=attempt_phases,
            attempt_started_mono=attempt_started_mono,
            attempt_started_iso=attempt_started_iso,
            attempt_results=attempt_results,
            repo_id=repo_id,
            effective_model_selection=model_selection,
        )

        # Phase 1: 运行 agent 或 recovery prompt
        agent_command_result: CommandResult | None = None
        try:
            if attempt_index == 0:
                if prompt_override is not None:
                    with attempt_phases.measure("agent"):
                        agent_command_result = run_agent_with_prompt_resilient(
                            selected_agent,
                            prompt_override,
                            worktree_path,
                            process_runner,
                            config=config,
                            issue=issue,
                            transient_retry_attempts=(config.runner.transient_retry_attempts),
                            transient_retry_delay_seconds=(
                                config.runner.transient_retry_delay_seconds
                            ),
                            timeout_seconds=config.runner.timeout_seconds,
                            inactivity_timeout_seconds=config.runner.inactivity_timeout_seconds,
                            model_selection=model_selection,
                        )
                else:
                    with attempt_phases.measure("agent"):
                        agent_command_result = run_agent(
                            selected_agent,
                            issue,
                            worktree_path,
                            config,
                            process_runner,
                            timeout_seconds=config.runner.timeout_seconds,
                            inactivity_timeout_seconds=config.runner.inactivity_timeout_seconds,
                            model_selection=model_selection,
                        )
            else:
                long_term_store, skill_store = _resolve_memory_stores(worktree_path, config.memory)
                recovery_prompt = build_recovery_prompt(
                    issue,
                    worktree_path,
                    recovery_attempt=attempt_index,
                    max_recovery_attempts=max_recovery_attempts,
                    failure_summary=recovery_failure_summary,
                    verification_results=final_verification_results,
                    memory_config=config.memory,
                    failure_type=recovery_failure_type,
                    long_term_store=long_term_store,
                    skill_store=skill_store,
                    evidence_dir=resolve_issue_evidence_relpath(config, issue),
                )
                recovery_timeout = (
                    config.runner.recovery_timeout_seconds or config.runner.timeout_seconds
                )
                with attempt_phases.measure("agent"):
                    agent_command_result = run_agent_with_prompt_resilient(
                        selected_agent,
                        recovery_prompt,
                        worktree_path,
                        process_runner,
                        config=config,
                        issue=issue,
                        transient_retry_attempts=config.runner.transient_retry_attempts,
                        transient_retry_delay_seconds=(config.runner.transient_retry_delay_seconds),
                        timeout_seconds=recovery_timeout,
                        inactivity_timeout_seconds=config.runner.inactivity_timeout_seconds,
                        model_selection=model_selection,
                    )
        except AgentUnavailableError:
            # The agent CLI could not be launched; let the cross-agent fallback
            # skip to the next candidate instead of burning recovery attempts.
            raise
        except (
            RuntimeError,
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            failure_type = classify_failure(
                before_sha=before_sha,
                after_sha=before_sha,
                has_uncommitted=False,
                agent_result=CommandResult(("",), 0, "", ""),
                verification_results=[],
                exc=exc,
                detect_provider_errors=True,
            )
            _record_attempt(
                attempt_record_context,
                failure_type=failure_type,
                detail=format_agent_execution_failure(exc),
            )
            if failure_type == FailureType.UNRECOVERABLE:
                raise UnrecoverableError(str(exc), attempt_results) from exc
            if failure_type == FailureType.FORBIDDEN_BLOCKED:
                raise ForbiddenBlockedError(str(exc), attempt_results) from exc
            if failure_type == FailureType.PROVIDER_CAPACITY:
                # The same provider will keep failing until its window resets;
                # escalate so the fallback chain can switch to another agent.
                raise ProviderCapacityError(str(exc), attempt_results) from exc
            if attempt_index >= max_recovery_attempts:
                raise MaxRetriesExceededError(attempt_results) from exc
            recovery_failure_summary = format_agent_execution_failure(exc)
            recovery_failure_type = failure_type.value
            recovery_failure_summary = format_agent_execution_failure(exc)
            _logger.warning(
                "Agent command failed for Issue #%d; asking agent to recover (%d/%d).",
                issue.number,
                attempt_index + 1,
                max_recovery_attempts,
            )
            continue

        # Phase 1 成功结束：把 agent 自报的 token 用量回填进 attempt 记录上下文，
        # 供后续任何分支（成功或失败）记录 attempt 时一并落账。
        attempt_record_context = replace(
            attempt_record_context,
            token_usage=(
                agent_command_result.token_usage if agent_command_result is not None else None
            ),
        )

        # Phase 2: 验证 agent 产出的代码（staging 之前）
        with attempt_phases.measure("verification"):
            verification_results = run_verification(worktree_path, config, process_runner)
        final_verification_results = verification_results
        try:
            ensure_verification_passed(verification_results)
        except VerificationFailedError as exc:
            failure_type = _classify_and_record_gate_failure(
                attempt_record_context,
                detail=format_recovery_failure_summary(
                    "Verification before staging failed.",
                    exc.verification_results,
                ),
                verification_results=exc.verification_results,
                exc=None,
            )
            if attempt_index >= max_recovery_attempts:
                raise MaxRetriesExceededError(attempt_results) from exc
            recovery_failure_type = failure_type.value
            recovery_failure_summary = format_recovery_failure_summary(
                "Verification before staging failed.",
                exc.verification_results,
            )
            _logger.warning(
                "Verification failed for Issue #%d; asking agent to recover (%d/%d).",
                issue.number,
                attempt_index + 1,
                max_recovery_attempts,
            )
            continue

        # Phase 3: 检查 PRD 交付（归档已完成 PRD）
        # 收尾流程会整体重跑完整门禁链；重跑通过后 Phase 3.5 不必再跑一遍相同的
        # 三道门禁（RV 命令复跑在脏工作区下不走缓存，跑两遍是纯浪费）。
        delivery_gates_revalidated = False
        try:
            with attempt_phases.measure("prd_delivery"):
                ensure_prd_delivery_ready(
                    issue,
                    worktree_path,
                    process_runner,
                    prd_baseline_content=prd_baseline_content,
                )
        except PrdDeliveryError as exc:
            closeout_result = _attempt_delivery_closeout(
                _DeliveryCloseoutContext(
                    record=attempt_record_context,
                    gate_failure=exc,
                    prd_baseline_content=prd_baseline_content,
                ),
                prd_overrides=prd_overrides,
                prd_preset_overrides=prd_preset_overrides,
            )
            delivery_gates_revalidated = closeout_result.revalidated
            if not delivery_gates_revalidated:
                failure_type = _classify_and_record_gate_failure(
                    attempt_record_context,
                    detail=format_prd_delivery_detail(str(exc)),
                    verification_results=verification_results,
                    exc=exc,
                )
                if attempt_index >= max_recovery_attempts:
                    raise MaxRetriesExceededError(attempt_results) from exc
                recovery_failure_summary = format_prd_delivery_failure(str(exc))
                recovery_failure_summary += _render_discarded_closeout_note(closeout_result)
                recovery_failure_type = failure_type.value
                _logger.warning(
                    "PRD delivery check failed for Issue #%d; asking agent to recover (%d/%d).",
                    issue.number,
                    attempt_index + 1,
                    max_recovery_attempts,
                )
                continue

        # Phase 3.5: 提交前检查证据与脚本位置。RV 命令留到 commit proxy 后
        # 对固定的代码树运行一次，避免在脏工作区反复执行。
        evidence_gate_failure: ValidationEvidenceError | None = None
        evidence_closeout_note = ""
        try:
            if not delivery_gates_revalidated:
                with attempt_phases.measure("evidence"):
                    ensure_validation_evidence_ready(issue, worktree_path, config, process_runner)
                    ensure_no_misplaced_evidence_helpers(worktree_path, config, process_runner)
                # 存量违规只告警不阻塞：前瞻守卫只看本次变更，历史交付留在主干里的
                # 取证脚本否则永远不可见。
                warn_legacy_evidence_helpers(worktree_path, config, process_runner)
        except ValidationEvidenceError as exc:
            evidence_closeout_result = _attempt_delivery_closeout(
                _DeliveryCloseoutContext(
                    record=attempt_record_context,
                    gate_failure=exc,
                    prd_baseline_content=prd_baseline_content,
                ),
                prd_overrides=prd_overrides,
                prd_preset_overrides=prd_preset_overrides,
            )
            if evidence_closeout_result.revalidated:
                delivery_gates_revalidated = True
            else:
                evidence_gate_failure = exc
                evidence_closeout_note = _render_discarded_closeout_note(evidence_closeout_result)

        if evidence_gate_failure is not None:
            exc = evidence_gate_failure
            failure_type = _classify_and_record_gate_failure(
                attempt_record_context,
                detail=format_validation_evidence_detail(str(exc)),
                verification_results=verification_results,
                exc=exc,
            )
            if attempt_index >= max_recovery_attempts:
                raise MaxRetriesExceededError(attempt_results) from exc
            recovery_failure_summary = format_validation_evidence_failure(
                str(exc), resolve_issue_evidence_relpath(config, issue)
            )
            recovery_failure_summary += evidence_closeout_note
            recovery_failure_type = failure_type.value
            _logger.warning(
                "Validation evidence check failed for Issue #%d; asking agent to recover (%d/%d).",
                issue.number,
                attempt_index + 1,
                max_recovery_attempts,
            )
            continue

        # Phase 4: Commit proxy — agent 通过 commit-request 文件请求提交
        if has_changes(worktree_path, process_runner):
            _logger.warning(
                "Agent left uncommitted changes for Issue #%d; runner processing commit request.",
                issue.number,
            )
            try:
                with attempt_phases.measure("commit"):
                    final_verification_results = commit_requested_changes(
                        issue,
                        worktree_path,
                        config,
                        process_runner,
                        expected_branch=expected_branch,
                    )
            except VerificationFailedError as exc:
                # staging 后验证失败：runner autofix 已在 commit_requested_changes
                # 内部尝试过。先 unstage，再交给 Fix Agent 处理简单局部失败。
                unstage_changes(worktree_path, process_runner)
                fix_succeeded = False
                if not config.runner.fix_agent_enabled:
                    _logger.info(
                        "Fix Agent disabled for Issue #%d; escalating staged "
                        "verification failure to full recovery.",
                        issue.number,
                    )
                else:
                    try:
                        fix_agent_result = run_fix_agent(
                            resolve_lifecycle_agent(
                                "fix",
                                config,
                                issue=issue,
                                selected_agent=selected_agent,
                                prd_overrides=prd_overrides,
                                prd_preset_overrides=prd_preset_overrides,
                            ),
                            issue,
                            worktree_path,
                            config,
                            process_runner,
                            verification_results=exc.verification_results,
                            # fix 未自绑预设时继承实现者的绑定；执行 agent 与预设
                            # 声明不一致时由 resilient 层丢弃并记日志。
                            model_selection=(
                                resolve_lifecycle_model_selection(
                                    "fix",
                                    config,
                                    issue=issue,
                                    prd_preset_overrides=prd_preset_overrides,
                                )
                                or model_selection
                            ),
                        )
                        # 观测发射点的 agent 名与调用点同源（纯查找，幂等），
                        # 重解析一次以保持调用点内联形态（AST 守卫约定）。
                        _emit_agent_usage(
                            request,
                            "fix",
                            resolve_lifecycle_agent(
                                "fix",
                                config,
                                issue=issue,
                                selected_agent=selected_agent,
                                prd_overrides=prd_overrides,
                            ),
                            fix_agent_result.token_usage,
                        )
                        if fix_agent_result.return_code != 0:
                            raise RuntimeError(
                                f"Fix Agent exited with code {fix_agent_result.return_code}"
                            )
                        post_fix_verification = run_verification(
                            worktree_path, config, process_runner
                        )
                        if failed_verification_results(post_fix_verification):
                            raise VerificationFailedError(post_fix_verification)
                        with attempt_phases.measure("commit"):
                            final_verification_results = commit_requested_changes(
                                issue,
                                worktree_path,
                                config,
                                process_runner,
                                expected_branch=expected_branch,
                            )
                        fix_succeeded = True
                    except (
                        RuntimeError,
                        subprocess.CalledProcessError,
                        VerificationFailedError,
                    ) as fix_exc:
                        _logger.warning(
                            "Fix Agent failed for Issue #%d: %s",
                            issue.number,
                            fix_exc,
                        )
                if fix_succeeded:
                    # Fix Agent repaired the failure and the runner committed it.
                    # Fall through to Phase 5 to record success.
                    _logger.info(
                        "Fix Agent repaired staged verification failure for "
                        "Issue #%d; runner committed the fix.",
                        issue.number,
                    )
                else:
                    failure_type = _classify_and_record_gate_failure(
                        attempt_record_context,
                        detail=format_recovery_failure_summary(
                            "Verification after runner staged changes with git add -A failed.",
                            exc.verification_results,
                        ),
                        verification_results=exc.verification_results,
                        exc=None,
                    )
                    if attempt_index >= max_recovery_attempts:
                        raise MaxRetriesExceededError(attempt_results) from exc
                    recovery_failure_type = failure_type.value
                    recovery_failure_summary = format_recovery_failure_summary(
                        "Verification after runner staged changes with git add -A failed.",
                        exc.verification_results,
                    )
                    _logger.warning(
                        "Staged verification failed for Issue #%d; "
                        "asking agent to recover (%d/%d).",
                        issue.number,
                        attempt_index + 1,
                        max_recovery_attempts,
                    )
                    continue
            except (RuntimeError, subprocess.CalledProcessError) as exc:
                after_sha = get_head_sha(worktree_path, process_runner)
                # 对于不可恢复的 commit 错误（如分支切换、无 commit request），
                # 或者已耗尽重试次数，直接失败。
                # CalledProcessError（如 pre-commit hook 失败）则视为可恢复。
                if (
                    attempt_index >= max_recovery_attempts
                    or not is_recoverable_commit_request_error(exc)
                ):
                    failure_type = classify_failure(
                        before_sha=before_sha,
                        after_sha=after_sha,
                        has_uncommitted=True,
                        agent_result=CommandResult(("",), 0, "", ""),
                        verification_results=final_verification_results,
                        exc=exc,
                    )
                    _record_attempt(
                        attempt_record_context,
                        failure_type=failure_type,
                        detail=str(exc),
                    )
                    if failure_type == FailureType.UNRECOVERABLE:
                        raise UnrecoverableError(str(exc), attempt_results) from exc
                    if failure_type == FailureType.FORBIDDEN_BLOCKED:
                        raise ForbiddenBlockedError(str(exc), attempt_results) from exc
                    if attempt_index >= max_recovery_attempts:
                        raise MaxRetriesExceededError(attempt_results) from exc
                    raise
                failure_type = classify_failure(
                    before_sha=before_sha,
                    after_sha=after_sha,
                    has_uncommitted=True,
                    agent_result=CommandResult(("",), 0, "", ""),
                    verification_results=final_verification_results,
                    exc=None,
                )
                _record_attempt(
                    attempt_record_context,
                    failure_type=failure_type,
                    detail=f"The runner could not process the commit request.\n{exc}",
                )
                if attempt_index >= max_recovery_attempts:
                    raise MaxRetriesExceededError(attempt_results) from exc
                recovery_failure_type = failure_type.value
                recovery_failure_summary = "\n".join(
                    [
                        "The runner could not process the commit request.",
                        str(exc),
                        "Fix the worktree and write a valid commit request JSON.",
                    ]
                )
                _logger.warning(
                    "Commit request failed for Issue #%d; asking agent to recover (%d/%d).",
                    issue.number,
                    attempt_index + 1,
                    max_recovery_attempts,
                )
                continue

        # Phase 4.5: commit proxy 已固定代码树，先重验 RV，再做独立复验。
        # RED 仍回到同一条 bounded recovery 循环，避免把未提交工作树当作
        # builder SHA 对应的交付物。
        try:
            from backend.core.use_cases.run_verifier_agent import run_verifier_gate

            with attempt_phases.measure("rv_reexec"):
                ensure_validation_evidence_ready(issue, worktree_path, config, process_runner)
                ensure_validation_commands_pass(issue, worktree_path, config, process_runner)
            with attempt_phases.measure("verifier"):
                verifier_verdict = run_verifier_gate(
                    issue,
                    worktree_path,
                    config,
                    process_runner,
                    selected_agent,
                    prd_overrides=prd_overrides,
                    prd_preset_overrides=prd_preset_overrides,
                )
            if verifier_verdict is not None:
                _emit_agent_usage(
                    request, "verify", verifier_verdict.agent, verifier_verdict.token_usage
                )
        except ValidationEvidenceError as exc:
            failure_type = _classify_and_record_gate_failure(
                attempt_record_context,
                detail=format_validation_evidence_detail(str(exc)),
                verification_results=final_verification_results,
                exc=exc,
            )
            if attempt_index >= max_recovery_attempts:
                raise MaxRetriesExceededError(attempt_results) from exc
            recovery_failure_summary = format_validation_evidence_failure(
                str(exc), resolve_issue_evidence_relpath(config, issue)
            )
            recovery_failure_type = failure_type.value
            _logger.warning(
                "Independent verifier failed for Issue #%d at committed HEAD; "
                "asking agent to recover (%d/%d).",
                issue.number,
                attempt_index + 1,
                max_recovery_attempts,
            )
            continue

        # Phase 5: 检查 agent 是否实际产生了 commit
        after_sha = get_head_sha(worktree_path, process_runner)
        if before_sha != after_sha:
            _record_attempt(
                attempt_record_context,
                failure_type=FailureType.SUCCESS,
                detail="Agent produced commits and passed verification.",
                recovered=attempt_index > 0,
            )
            return AgentCommitResult(final_verification_results, attempt_results, verifier_verdict)

        # Agent 没有产生任何变更：进入 recovery 要求实际修改代码
        has_uncommitted = has_changes(worktree_path, process_runner)
        failure_type = classify_failure(
            before_sha=before_sha,
            after_sha=after_sha,
            has_uncommitted=has_uncommitted,
            agent_result=CommandResult(("",), 0, "", ""),
            verification_results=verification_results,
            exc=None,
        )
        _record_attempt(
            attempt_record_context,
            failure_type=failure_type,
            detail="Agent produced no git commits.",
        )
        if attempt_index >= max_recovery_attempts:
            raise MaxRetriesExceededError(attempt_results)
        recovery_failure_type = failure_type.value
        recovery_failure_summary = "\n".join(
            [
                "The previous attempt produced no git commits.",
                "Make the requested code changes and write a valid commit request JSON.",
            ]
        )
        _logger.warning(
            "Agent produced no git commits for Issue #%d; asking agent to recover (%d/%d).",
            issue.number,
            attempt_index + 1,
            max_recovery_attempts,
        )

    raise MaxRetriesExceededError(attempt_results)

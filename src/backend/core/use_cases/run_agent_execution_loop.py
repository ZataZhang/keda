"""Agent 执行、验证与 recovery 状态机。"""

from __future__ import annotations
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
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


def _attempt_delivery_closeout(context: _DeliveryCloseoutContext) -> bool:
    """尝试用一次短命的收尾修复接住交付门禁失败。

    只接住被抛出点标记为收尾类的失败；真失败与收尾层被关闭时立刻返回 ``False``，
    调用方走本层落地前的整轮重跑路径。返回 ``True`` 表示收尾 pass 没有越界、
    完整门禁链已重跑通过，本轮可以继续原流程。

    Args:
        context: 本次收尾的执行请求、attempt 计时与 PRD 基线。

    Returns:
        收尾成功且门禁链重跑通过时为 ``True``，其余一律 ``False``。
    """
    request = context.request
    config = request.config
    issue = request.issue
    gate_failure = context.gate_failure
    if not gate_failure.kind.is_closeout_eligible:
        return False
    if not config.runner.closeout_agent_enabled:
        _logger.info(
            "Closeout Agent disabled for Issue #%d; escalating %s gate failure to full recovery.",
            issue.number,
            gate_failure.kind.value,
        )
        return False

    worktree_path = request.worktree_path
    process_runner = request.process_runner
    allowed_scope = build_closeout_allowed_scope(issue, config)
    before_snapshot = capture_closeout_snapshot(issue, worktree_path, config, process_runner)
    try:
        with context.record.attempt_phases.measure("closeout"):
            run_closeout_agent(
                request.selected_agent,
                config,
                process_runner,
                prompt_context=CloseoutPromptContext(
                    issue=issue,
                    worktree_path=worktree_path,
                    gate_failure_message=str(gate_failure),
                    kind=gate_failure.kind,
                    allowed_scope=allowed_scope,
                ),
            )
    except (RuntimeError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        _logger.warning("Closeout Agent failed for Issue #%d: %s", issue.number, exc)
        _revert_failed_closeout(context, before_snapshot)
        return False

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
        return False

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
        _logger.warning(
            "Delivery gates still fail after closeout for Issue #%d; "
            "escalating to full recovery: %s",
            issue.number,
            exc,
        )
        _revert_failed_closeout(context, before_snapshot)
        return False

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
    return True


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
    recovery_failure_summary = ""
    recovery_failure_type: str = "verification_failed"
    final_verification_results: list[CommandResult] = []
    attempt_results: list[AttemptResult] = []
    verifier_verdict = None  # set by Phase 3.6 when the independent verifier runs

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
        )

        # Phase 1: 运行 agent 或 recovery prompt
        try:
            if attempt_index == 0:
                if prompt_override is not None:
                    with attempt_phases.measure("agent"):
                        run_agent_with_prompt_resilient(
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
                        )
                else:
                    with attempt_phases.measure("agent"):
                        run_agent(
                            selected_agent,
                            issue,
                            worktree_path,
                            config,
                            process_runner,
                            timeout_seconds=config.runner.timeout_seconds,
                            inactivity_timeout_seconds=config.runner.inactivity_timeout_seconds,
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
                    run_agent_with_prompt_resilient(
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
            delivery_gates_revalidated = _attempt_delivery_closeout(
                _DeliveryCloseoutContext(
                    record=attempt_record_context,
                    gate_failure=exc,
                    prd_baseline_content=prd_baseline_content,
                )
            )
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
                recovery_failure_type = failure_type.value
                _logger.warning(
                    "PRD delivery check failed for Issue #%d; asking agent to recover (%d/%d).",
                    issue.number,
                    attempt_index + 1,
                    max_recovery_attempts,
                )
                continue

        # Phase 3.5: Realistic Validation 证据门禁（要求验证且无豁免时）
        # 收尾成功时门禁链已在收尾流程内整体重跑通过，这里不再跑第二遍：脏工作区
        # 下 RV 复跑不走缓存，重复执行纯属浪费。
        evidence_gate_failure: ValidationEvidenceError | None = None
        try:
            if not delivery_gates_revalidated:
                with attempt_phases.measure("evidence"):
                    ensure_validation_evidence_ready(issue, worktree_path, config, process_runner)
                    ensure_no_misplaced_evidence_helpers(worktree_path, config, process_runner)
                # 存量违规只告警不阻塞：前瞻守卫只看本次变更，历史交付留在主干里的
                # 取证脚本否则永远不可见。
                warn_legacy_evidence_helpers(worktree_path, config, process_runner)
                with attempt_phases.measure("rv_reexec"):
                    ensure_validation_commands_pass(issue, worktree_path, config, process_runner)
        except ValidationEvidenceError as exc:
            if _attempt_delivery_closeout(
                _DeliveryCloseoutContext(
                    record=attempt_record_context,
                    gate_failure=exc,
                    prd_baseline_content=prd_baseline_content,
                )
            ):
                delivery_gates_revalidated = True
            else:
                evidence_gate_failure = exc

        # Phase 3.6: independent verifier (pre-PR; red -> this same recovery
        # loop auto-repairs, bounded; escalates to a human only on exhaustion).
        # 独立复验的红灯永远是真失败，因此刻意不经过收尾层。
        if evidence_gate_failure is None:
            try:
                # Local import breaks the run_agent_once <-> run_verifier_agent cycle.
                from backend.core.use_cases.run_verifier_agent import run_verifier_gate

                with attempt_phases.measure("verifier"):
                    verifier_verdict = run_verifier_gate(
                        issue, worktree_path, config, process_runner, selected_agent
                    )
            except ValidationEvidenceError as exc:
                evidence_gate_failure = exc

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
                            selected_agent,
                            issue,
                            worktree_path,
                            config,
                            process_runner,
                            verification_results=exc.verification_results,
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

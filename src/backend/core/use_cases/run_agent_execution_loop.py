"""Agent 执行、验证与 recovery 状态机。"""

from __future__ import annotations
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    AttemptResult,
    CommandResult,
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
    _logger,
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
from backend.core.use_cases.agent_runner_attempt_recording import (
    _AttemptRecordContext,
    _classify_and_record_gate_failure,
    _emit_agent_usage,
    _record_attempt,
)
from backend.core.use_cases.agent_runner_delivery_closeout import (
    _DeliveryCloseoutContext,
    _attempt_delivery_closeout,
    _render_discarded_closeout_note,
)
from backend.core.use_cases.agent_runner_failure import (
    ForbiddenBlockedError,
    is_recoverable_commit_request_error,
)
from backend.core.use_cases.agent_runner_memory import _resolve_memory_stores
from backend.core.use_cases.agent_runner_feedback import (
    VerificationFailedError,
    format_prd_delivery_detail,
    format_prd_delivery_failure,
)
from backend.core.use_cases.agent_runner_structured_evidence import ValidationEvidenceError
from backend.core.use_cases.agent_runner_session_store import resolve_resumable_session_id
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
from backend.core.shared.models.publish_stage import PublishStage
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
    #: 实现阶段绑定的模型选择；``None`` 表示无绑定。fix / closeout 未自绑时继承它（同一 agent）。
    model_selection: ModelSelection | None = None
    #: 首轮要续传的原会话 id（崩溃对账判定为「可续传」时由领取侧从 worktree 局部
    #: 会话记录读出并注入）；``None`` 表示全新会话。仅对声明了续传能力的 agent 生效。
    resume_session_id: str | None = None
    #: 发布档位（normal / fast / direct）：决定 Phase 2 起各门禁是否旁路。默认 normal
    #: = 与历史行为完全一致。
    publish_stage: PublishStage = PublishStage.NORMAL
    #: 兼容旧调用面的快速通道布尔：``kc run --fast-merge`` 的等价写法。唯一事实源是
    #: :attr:`publish_stage`，本字段在 ``__post_init__`` 里被归一化为该档位的派生视图，
    #: 因此不可能出现「stage=direct 且 fast_merge=True」这类矛盾状态。
    fast_merge: bool | None = None

    def __post_init__(self) -> None:
        """把 ``fast_merge`` 布尔收进 :attr:`publish_stage`，并回填其派生视图。"""
        if self.fast_merge and self.publish_stage is PublishStage.NORMAL:
            object.__setattr__(self, "publish_stage", PublishStage.FAST)
        object.__setattr__(self, "fast_merge", self.publish_stage is PublishStage.FAST)


def _attempt_resume_session_id(
    request: AgentExecutionRequest,
    *,
    attempt_index: int,
) -> str | None:
    """本轮该续传哪个会话 id；``None`` 表示按全新会话起跑。

    - **首轮**只认领取侧注入的 :attr:`AgentExecutionRequest.resume_session_id`
      （崩溃对账判定「可续传」后重新入队的那次领取才会给），因此健康的新任务
      绝不会被一段历史会话污染。
    - **recovery 轮次**读回 worktree 局部记录里上一条自报的会话：刚死掉的那轮
      已经把进度聊在里面，续它才是断点续传。记录被删 / 从未写过 → ``None`` →
      全新会话（PRD rv-4 的负控路径）。
    """
    if attempt_index == 0:
        return request.resume_session_id
    return resolve_resumable_session_id(
        request.worktree_path,
        config=request.config,
        agent_name=request.selected_agent,
        issue_number=request.issue.number,
    )


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
    # PRD 头部 lifecycle_presets 覆盖块（阶段 -> 预设，PRD 级）；合并/回退同上面一套。
    prd_preset_overrides = effective_prd_preset_overrides(issue, None)
    if not prd_preset_overrides and prd_baseline_content is not None:
        prd_preset_overrides = parse_prd_lifecycle_overrides(
            prd_baseline_content,
            prd_path=prd_relative_path,
            block_name=PRD_OVERRIDE_BLOCK_PRESETS,
        )
    # 实现阶段绑定的模型选择：显式换人（CLI --agent / 回退）时在此丢弃，保证 fix/closeout 继承有效选择。
    model_selection = drop_model_selection_for_agent(selected_agent, request.model_selection)
    publish_stage = request.publish_stage
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
        # 本轮要续传的会话（None = 全新会话）。声明了续传能力的 agent 才拿得到，
        # 其余一律 None，argv 与本特性之前逐字节一致。
        resume_session_id = _attempt_resume_session_id(request, attempt_index=attempt_index)
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
                            resume_session_id=resume_session_id,
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
                            resume_session_id=resume_session_id,
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
                        resume_session_id=resume_session_id,
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
        # 直发档（--direct-pr）连 runner 自己的验证命令也不跑：结果恒为空，下面的
        # 失败分支自然不触发，门禁转移到 PR 上的 CI。
        if publish_stage.skips_review_and_repo_verification:
            _logger.info(
                "Direct-pr (origin: --direct-pr run flag): skipping runner verification "
                "commands for Issue #%d at attempt %d; CI on the Draft PR is the gate.",
                issue.number,
                attempt_index + 1,
            )
            verification_results = []
        else:
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
        if publish_stage.skips_review_and_repo_verification:
            # 直发档：即使 Issue 正文带了验收段，证据门禁也一并跳过（FR-15）。
            _logger.info(
                "Direct-pr (origin: --direct-pr run flag): skipping the validation "
                "evidence gate for Issue #%d at attempt %d.",
                issue.number,
                attempt_index + 1,
            )
        elif not delivery_gates_revalidated:
            try:
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
                    evidence_closeout_note = _render_discarded_closeout_note(
                        evidence_closeout_result
                    )

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
                        publish_stage=publish_stage,
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
                            # fix 未自绑时继承实现者绑定；不一致由 resilient 层丢弃并记日志。
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
                                publish_stage=publish_stage,
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
        # 旁路档位：--fast-merge 跳过这两道验证门禁，--direct-pr 同样跳过（其跳过
        # 范围严格更大）；builder 失败 / 恢复循环照常。跳过必须留一行含旗标来源的审计日志。
        if publish_stage.skips_independent_verification:
            verifier_verdict = None
            bypass_label = (
                ("Fast-merge", "--fast-merge", "fast-track")
                if publish_stage is PublishStage.FAST
                else ("Direct-pr", "--direct-pr", "direct")
            )
            stage_name, flag_name, annotation_name = bypass_label
            _logger.info(
                "%s (origin: %s run flag): skipping rv_reexec and verifier gates "
                "for Issue #%d at attempt %d; the PR will carry the unverified "
                "%s annotation.",
                stage_name,
                flag_name,
                issue.number,
                attempt_index + 1,
                annotation_name,
            )
        else:
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

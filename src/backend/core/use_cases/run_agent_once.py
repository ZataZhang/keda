"""Local Issue queue runner — single polling pass.

本模块是 Agent Runner 的核心执行层，负责：
1. 为单个 Issue 创建或复用 git worktree
2. 调用 AI Agent（Claude / Kimi / Codex）执行代码变更
3. 运行验证命令（lint / test）
4. 通过受限 commit proxy 将 agent 变更提交到本地分支
5. 管理 recovery 重试循环：当验证或 commit 失败时，给 agent 发送 recovery prompt

Commit Proxy 机制：
agent 不直接执行 `git commit`，而是将 commit message 写入
`.agent-runner/commit-request.json`。runner 读取该文件后执行 commit，
这样可以确保在 commit 前运行验证、检查 forbidden paths、控制分支安全。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    AGENT_SESSION_ID_ATTR_NAME,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    AttemptResult,
    CommandResult,
    IssueSummary,
    TokenUsage,
    describe_verification_command,
)
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_DELIBERATE,
    AGENT_PROFILE_GENERATE,
    AGENT_PROFILE_RUN,
    PROMPT_DELIVERY_STDIN,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_invocation import (
    UnknownAgentError,
    build_agent_invocation,
    resolve_profile_spec,
)
from backend.core.use_cases.agent_invocation_tracing import (
    PHASE_FIX,
    PHASE_IMPLEMENTATION,
    PHASE_REVIEW,
    PHASE_SUPERVISOR,
    PHASE_UNSPECIFIED,
    RETRY_REASON_RESUME_NOT_STARTED,
    RETRY_REASON_TIMEOUT_RESUME as RETRY_REASON_TIMEOUT_RESUME,
    RETRY_REASON_TRANSIENT as RETRY_REASON_TRANSIENT,
    InvocationStartRequest,
    active_invocation_trace_context,
    finish_invocation,
    link_next_invocation,
    start_invocation,
)
from backend.core.use_cases.agent_model_selection import (
    drop_model_selection_for_agent as drop_model_selection_for_agent,
    resolve_implementation_model_selection,
)
from backend.core.use_cases.agent_response_text import extract_agent_response_text
from backend.core.use_cases.agent_runner_attempt import (
    AttemptPhaseTimer,
    _append_attempt_and_notify,
    _make_attempt_result,
    wait_before_recovery_attempt,
)
from backend.core.use_cases.agent_runner_commit import (
    EmptyCommitRequestError,
    checkpoint_uncommitted_progress,
    commit_requested_changes,
    sanitize_commit_message,
    unstage_changes,
)
from backend.core.use_cases.agent_runner_failure import (
    AgentRunnerAttemptError,
    AgentUnavailableError,
    MaxRetriesExceededError,
    ProviderCapacityError,
    PublishFailureError,
    UnrecoverableError,
    classify_failure,
    detect_usage_limit_root_cause,
    format_agent_execution_failure,
    format_attempt_history,
    format_failure_comment,
    format_minimal_failure_comment,
    format_publish_failure_comment,
    format_recovery_failure_summary,
    is_transient_failure as is_transient_failure,
)
from backend.core.use_cases.agent_runner_feedback import (
    PrdDeliveryError,
    VerificationFailedError,
    build_fix_prompt,
    build_prd_review_reference,
    build_progress_continuation_prompt,
    build_prompt,
    build_recovery_prompt,
    ensure_prd_delivery_ready,
    ensure_verification_passed,
    extract_prd_path,
    failed_verification_results,
    format_prd_delivery_failure,
    format_result_for_recovery,
    format_verification_failure,
    resolve_prd_archive_path,
    truncate_recovery_output,
)
from backend.core.use_cases.agent_runner_git import (
    get_active_rebase_target,
    get_current_branch,
    get_head_sha,
    has_changes,
    is_detached_head,
    list_changed_paths,
    list_git_remotes,
    list_stageable_paths,
    run_verification,
)
from backend.core.use_cases.agent_runner_memory import _resolve_memory_stores
from backend.core.use_cases.agent_runner_publish import (
    publish_changes,
    run_preflight_checks,
    validate_publish_remote,
    validate_safe_changes,
)
from backend.core.use_cases.agent_runner_session_store import (
    AgentSessionScope,
    consume_agent_timeout_resume_attempt as consume_agent_timeout_resume_attempt,
    load_agent_session_record,
    resolve_resumable_session_id,
    save_agent_session_record,
)
from backend.core.use_cases.agent_runner_stall_supervision import (
    StallDiagnosisRequest,
    StallSupervisionRequest,
    build_supervision_attempt_key,
    supervised_agent_invocation,
)
from backend.core.use_cases.agent_runner_validation import (
    build_validation_prompt_line,
    resolve_issue_evidence_relpath,
)
from backend.core.use_cases.agent_runner_worktree_branch import (
    _ensure_worktree_branch,
    _reconcile_worktree_with_remote_branch,
)
from backend.core.use_cases.agent_runner_worktree_create import (
    _resolve_repo_id,
    create_or_reuse_worktree,
    format_command,
)

from backend.core.use_cases.agent_runner_agent_selection import (
    REPAIR_AGENT_EXECUTOR,
    REPAIR_AGENT_SELF,
    choose_agent,
    repair_agent_is_self,
    resolve_agent_fallback_candidate_specs,
    resolve_repair_agent,
    resolve_reviewer_agent,
    resolve_supervisor_agent,
)

from backend.core.use_cases.agent_runner_resilient_invocation import (
    run_agent_with_prompt_resilient as run_agent_with_prompt_resilient,
)

_logger = logging.getLogger(__name__)

_TIMEOUT_CONTINUATION_PROMPT = (
    "The previous agent process was stopped by the runner's execution timeout. "
    "Continue the existing task from this session and the current worktree state. "
    "Do not repeat completed work; continue with the next incomplete step."
)


__all__ = [
    "REPAIR_AGENT_EXECUTOR",
    "REPAIR_AGENT_SELF",
    "AgentRunnerAttemptError",
    "AgentUnavailableError",
    "AttemptPhaseTimer",
    "EmptyCommitRequestError",
    "MaxRetriesExceededError",
    "PrdDeliveryError",
    "ProviderCapacityError",
    "PublishFailureError",
    "UnknownAgentError",
    "UnrecoverableError",
    "VerificationFailedError",
    "_append_attempt_and_notify",
    "_ensure_worktree_branch",
    "_make_attempt_result",
    "_reconcile_worktree_with_remote_branch",
    "_resolve_repo_id",
    "build_blocked_continuation_prompt",
    "build_fix_prompt",
    "build_prd_review_reference",
    "build_progress_continuation_prompt",
    "build_prompt",
    "build_recovery_prompt",
    "checkpoint_uncommitted_progress",
    "choose_agent",
    "classify_failure",
    "commit_requested_changes",
    "create_or_reuse_worktree",
    "detect_usage_limit_root_cause",
    "drop_model_selection_for_agent",
    "ensure_prd_delivery_ready",
    "ensure_verification_passed",
    "extract_agent_response_text",
    "extract_prd_path",
    "failed_verification_results",
    "format_agent_execution_failure",
    "format_attempt_history",
    "format_command",
    "format_failure_comment",
    "format_minimal_failure_comment",
    "format_prd_delivery_failure",
    "format_publish_failure_comment",
    "format_recovery_failure_summary",
    "format_result_for_recovery",
    "format_verification_failure",
    "get_active_rebase_target",
    "get_current_branch",
    "get_head_sha",
    "has_changes",
    "is_detached_head",
    "list_changed_paths",
    "list_git_remotes",
    "list_stageable_paths",
    "publish_changes",
    "repair_agent_is_self",
    "resolve_agent_fallback_candidate_specs",
    "resolve_implementation_model_selection",
    "resolve_prd_archive_path",
    "resolve_repair_agent",
    "resolve_reviewer_agent",
    "resolve_supervisor_agent",
    "RETRY_REASON_TIMEOUT_RESUME",
    "RETRY_REASON_TRANSIENT",
    "consume_agent_timeout_resume_attempt",
    "is_transient_failure",
    "run_agent",
    "run_agent_until_committed",
    "run_agent_with_prompt",
    "run_agent_with_prompt_resilient",
    "run_fix_agent",
    "run_once",
    "run_preflight_checks",
    "run_verification",
    "sanitize_commit_message",
    "truncate_recovery_output",
    "unstage_changes",
    "validate_publish_remote",
    "validate_safe_changes",
    "wait_before_recovery_attempt",
]


def build_blocked_continuation_prompt(
    issue: IssueSummary,
    worktree_path: Path,
    blocked_paths: tuple[str, ...],
) -> str:
    """Build a continuation prompt for a blocked Issue that has been resolved.

    Args:
        issue: Issue being processed.
        worktree_path: Path to the agent worktree.
        blocked_paths: The forbidden paths that were previously blocked.

    Returns:
        A prompt instructing the agent to continue the remaining work.
    """
    lines = [
        f"Continue working on Issue #{issue.number}: {issue.title}",
        f"Issue URL: {issue.url}",
        f"Worktree: {worktree_path}",
        "",
        "The following files were previously blocked by forbidden-path rules and have now been resolved by a human operator.",
        "Please continue to complete the remaining tasks without modifying these files again unless explicitly required:",
        "",
    ]
    for path in blocked_paths:
        lines.append(f"- {path}")
    lines.extend(
        [
            "",
            "Proceed with the remaining implementation, verification, and commit as normal.",
        ]
    )
    return "\n".join(lines)


def _build_verification_commands_summary(
    config: AppConfig,
) -> str:
    """Return a human-readable list of configured verification commands."""
    commands = config.runner.verification_commands
    if not commands:
        return "No verification commands configured."
    return "\n".join(f"- `{describe_verification_command(command)}`" for command in commands)


def run_agent(
    agent_name: str,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    *,
    timeout_seconds: int | None = None,
    inactivity_timeout_seconds: int | None = None,
    model_selection: ModelSelection | None = None,
    resume_session_id: str | None = None,
    invocation_attempt: int | None = None,
) -> CommandResult:
    """Run Codex or Claude Code in non-interactive mode."""
    long_term_store, skill_store = _resolve_memory_stores(worktree_path, config.memory)
    prompt = build_prompt(
        issue,
        worktree_path,
        config.prompts,
        phase="execution",
        validation_line=build_validation_prompt_line(
            issue, config, evidence_dir=resolve_issue_evidence_relpath(config, issue)
        ),
        verification_commands_summary=_build_verification_commands_summary(config),
        memory_config=config.memory,
        long_term_store=long_term_store,
        skill_store=skill_store,
    )
    return run_agent_with_prompt_resilient(
        agent_name,
        prompt,
        worktree_path,
        process_runner,
        config=config,
        issue=issue,
        transient_retry_attempts=config.runner.transient_retry_attempts,
        transient_retry_delay_seconds=config.runner.transient_retry_delay_seconds,
        timeout_seconds=timeout_seconds,
        inactivity_timeout_seconds=inactivity_timeout_seconds,
        model_selection=model_selection,
        resume_session_id=resume_session_id,
        invocation_phase=PHASE_IMPLEMENTATION,
        invocation_attempt=invocation_attempt,
    )


def run_fix_agent(
    agent_name: str,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    verification_results: list[CommandResult],
    model_selection: ModelSelection | None = None,
    invocation_attempt: int | None = None,
) -> CommandResult:
    """Run a focused Fix Agent for simple local verification failures.

    The Fix Agent prompt only contains the current verification failure and
    constraints; it does not ask the agent to update PRD checklists, evidence,
    or other global deliverables.

    Args:
        agent_name: Agent to invoke.
        issue: Current Issue.
        worktree_path: Agent worktree path.
        config: Agent Runner configuration.
        process_runner: Command executor.
        verification_results: Failed verification results to repair.
        model_selection: 阶段绑定的模型选择（fix 自身绑定或继承实现者）；
            执行 agent 与预设 agent 不一致时在 resilient 层丢弃并记日志。
        invocation_attempt: 所属 recovery 轮次（1 起）；调用观测用它把修复调用
            归到正确的那一轮。

    Returns:
        The Fix Agent command result.
    """
    prompt = build_fix_prompt(
        issue,
        worktree_path,
        verification_results=verification_results,
        verification_commands_summary=_build_verification_commands_summary(config),
    )
    fix_timeout = config.runner.fix_timeout_seconds or config.runner.timeout_seconds
    _logger.info(
        "Starting Fix Agent for Issue #%d (timeout=%ss).",
        issue.number,
        fix_timeout,
    )
    return run_agent_with_prompt_resilient(
        agent_name,
        prompt,
        worktree_path,
        process_runner,
        config=config,
        issue=issue,
        transient_retry_attempts=config.runner.transient_retry_attempts,
        transient_retry_delay_seconds=config.runner.transient_retry_delay_seconds,
        timeout_seconds=fix_timeout,
        inactivity_timeout_seconds=config.runner.inactivity_timeout_seconds,
        model_selection=model_selection,
        invocation_phase=PHASE_FIX,
        invocation_attempt=invocation_attempt,
    )


def run_agent_with_prompt(
    agent_name: str,
    prompt: str,
    worktree_path: Path,
    process_runner: IProcessRunner,
    *,
    config: AppConfig | None = None,
    capture_output: bool = False,
    timeout_seconds: int | None = None,
    inactivity_timeout_seconds: int | None = None,
    issue: IssueSummary | None = None,
    profile: str = AGENT_PROFILE_RUN,
    model_selection: ModelSelection | None = None,
    resume_session_id: str | None = None,
    invocation_phase: str = PHASE_UNSPECIFIED,
    invocation_attempt: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> CommandResult:
    """Run an agent with a prepared prompt.

    命令行与输出协议全部来自 :func:`build_agent_invocation`（``profile``
    默认 ``"run"``）；调用方传入的 ``process_runner`` 只负责执行与中继。
    只读审核等场景可传 ``profile="deliberate"``，让支持沙箱的 agent 走声明式
    只读形态。``model_selection`` 非空时按该 agent 的声明式模板把模型/推理档
    参数注入 argv；agent 未声明模板时 fail-fast（绝不静默忽略）。
    ``resume_session_id`` 非空且该 agent 声明了会话续传能力时，按声明式
    ``resume_args`` 模板注入续传参数；agent 未声明时**静默回落为全新会话**
    （FR-4：续传失败不得丢失整轮 recovery）。

    本函数是**唯一**的实际进程调用边界，因此也是调用观测（Issue #242）的落点：
    每次 ``process_runner.run`` 前后各发一条事实，回退/重跑新建 invocation 而不是
    合并成一条成功记录。

    Args:
        invocation_phase: 本次调用所属阶段（取
            :data:`~backend.core.use_cases.agent_invocation_tracing.KNOWN_PHASES`
            之一）；只有调用点知道，因此显式传入，不在此处猜测。
        invocation_attempt: recovery 轮次（1 起）；非 attempt 主体调用为 ``None``。
        cancel_check: 可选人工接管取消探针（Issue #278）。非 ``None`` 时透传给
            ``process_runner.run``：探针为真即终止本次 agent 进程树并抛
            ``InterruptedError``；默认 ``None`` 时 run_kwargs 与本参数之前一致。

    停滞监督（Issue #256）在**这里**接入，因为本函数是唯一真实进程边界：只有
    ``profile="run"`` 且带 Issue 身份、且 ``[agent_runner.stall_supervisor].enabled``
    为真时，才把这次调用交给
    :func:`~backend.core.use_cases.agent_runner_stall_supervision.supervised_agent_invocation`
    并给子进程登记 attempt 键。诊断自身走 ``generate`` 只读 profile，因此不会
    再被监督（不存在递归 observer）。开关为假时组装上下文直接返回 ``None``：
    不起线程、不登记进程、不调用模型，argv 与本特性之前逐字节一致。

    重试/回退关联不通过参数表达：调用方在发起替代之前调
    :func:`~backend.core.use_cases.agent_invocation_tracing.link_next_invocation`
    声明原因，观测侧据此把新 invocation 以 ``retry_of`` 挂到上一次，两次调用各自
    独立成记录，绝不合并成一条成功记录。
    """
    if issue is not None:
        _logger.info(
            "Starting agent for Issue #%d: %s",
            issue.number,
            issue.url,
        )
    label = f"Issue #{issue.number}: {issue.url}" if issue is not None else None
    effective_config = config or AppConfig()

    def _run_stall_diagnosis(diagnosis: StallDiagnosisRequest) -> str:
        """跑一次只读诊断：走被诊断 agent 声明的 generate profile，取回作答文本。

        这里刻意不套 ``profile=run``：监督器不能写工作区，也不能把自己变成下一个
        被监督的 attempt 主体（``run_agent_with_prompt`` 只在 run profile 起 observer）。
        """
        diagnosis_result = run_agent_with_prompt(
            diagnosis.agent_name,
            diagnosis.prompt,
            diagnosis.worktree_path,
            process_runner,
            config=effective_config,
            capture_output=True,
            timeout_seconds=diagnosis.timeout_seconds,
            inactivity_timeout_seconds=diagnosis.inactivity_timeout_seconds,
            profile=AGENT_PROFILE_GENERATE,
            issue=issue,
            invocation_phase=PHASE_SUPERVISOR,
        )
        return extract_agent_response_text(diagnosis_result)

    def _build_stall_supervision_request() -> StallSupervisionRequest | None:
        """按本次真实调用组装停滞监督上下文；不适用时返回 ``None``。

        返回 ``None`` 的三种情况都必须**一次模型都不调用**：开关关闭（默认）、
        没有 Issue 身份（``kc ask`` / REPL / 辩论不是 attempt 主体）、非 run profile。
        此外诊断通道不可用时也返回 ``None``——起一个注定失败的 observer 比不监督更糟。
        """
        supervisor_config = effective_config.stall_supervisor
        if not supervisor_config.enabled or issue is None or profile != AGENT_PROFILE_RUN:
            return None
        # 局部导入：lifecycle_agent_resolution 与本模块互相依赖（同 resolve_verifier_agent）。
        from backend.core.use_cases.lifecycle_agent_resolution import (
            resolve_lifecycle_agent,
        )

        try:
            supervisor_agent = resolve_lifecycle_agent(
                # 生命周期矩阵的 ``supervisor`` 键（九键之一，见 LIFECYCLE_AGENT_KEYS）。
                "supervisor",
                effective_config,
                issue=issue,
                selected_agent=agent_name,
                override_agent=supervisor_config.agent,
            )
            diagnosis_profile_spec = resolve_profile_spec(
                supervisor_agent, AGENT_PROFILE_GENERATE, effective_config
            )
        except ValueError as exc:
            _logger.warning(
                "Issue #%d: stall supervisor disabled for this attempt (%s); "
                "no diagnosis will be called.",
                issue.number,
                exc,
            )
            return None
        if not diagnosis_profile_spec.read_only:
            _logger.warning(
                "Issue #%d: stall supervisor disabled because agent '%s' declares a "
                "non-read-only '%s' profile; no diagnosis will be called.",
                issue.number,
                supervisor_agent,
                AGENT_PROFILE_GENERATE,
            )
            return None
        return StallSupervisionRequest(
            config=supervisor_config,
            process_runner=process_runner,
            worktree_path=worktree_path,
            attempt_key=build_supervision_attempt_key(
                issue_number=issue.number,
                invocation_phase=invocation_phase,
                invocation_attempt=invocation_attempt,
            ),
            writer_agent=agent_name,
            supervisor_agent=supervisor_agent,
            issue_number=issue.number,
            invocation_phase=invocation_phase,
            invocation_attempt=invocation_attempt,
            # ContextVar 不随新线程继承：在主线程读出来，显式交给 observer。
            trace_context=active_invocation_trace_context(),
            diagnose=_run_stall_diagnosis,
        )

    session_scope = AgentSessionScope(
        issue_number=issue.number if issue is not None else None,
        phase=invocation_phase,
    )

    def _persist_session_id(
        session_id: str | None,
        *,
        continuation_session_id: str | None = None,
    ) -> None:
        """把本次调用自报的会话 id 落在 worktree 局部记录里。

        只记 implementation（含 recovery）和 deliberate review 会话，并按阶段记录作用域。
        fix、closeout、verifier 等独立对话即使使用 ``profile="run"`` 也不覆盖可恢复会话，
        避免恢复时续错对象或污染独立 verifier 的洁净树门禁。落盘是旁路，失败只记日志——
        续传是增益路径，不能反过来把正常执行拖下水。
        """
        can_persist_session = (
            profile == AGENT_PROFILE_RUN
            and invocation_phase in (PHASE_IMPLEMENTATION, PHASE_UNSPECIFIED)
        ) or (profile == AGENT_PROFILE_DELIBERATE and invocation_phase == PHASE_REVIEW)
        if not session_id or not can_persist_session:
            return
        previous_record = load_agent_session_record(
            worktree_path,
            agent_name,
            phase=invocation_phase,
        )
        if previous_record is None and invocation_phase not in (None, PHASE_IMPLEMENTATION):
            previous_record = load_agent_session_record(worktree_path, agent_name)
        carried_timeout_resume_count = (
            previous_record.timeout_resume_count
            if previous_record is not None and previous_record.session_id == continuation_session_id
            else None
        )
        try:
            save_agent_session_record(
                worktree_path,
                agent_name=agent_name,
                session_id=session_id,
                scope=session_scope,
                timeout_resume_count=carried_timeout_resume_count,
            )
        except (OSError, ValueError) as exc:
            _logger.warning(
                "Agent session record skipped at %s (agent '%s'): %s",
                worktree_path,
                agent_name,
                exc,
            )

    def _invoke(resume_id: str | None) -> CommandResult:
        """按给定的续传会话 id 组装并执行一次调用。

        每次进入本函数就是一次**实际进程调用**，因此单独发一对 started/finished
        观测事件：续传没起跑后改跑全新会话的那次是第二条记录，以 ``retry_of``
        关联第一条，而不是把两条压成一条。
        """
        attempt_invocation = build_agent_invocation(
            agent_name,
            profile,
            prompt,
            worktree_path,
            effective_config,
            model_selection=model_selection,
            resume_session_id=resume_id,
        )
        if resume_id is not None and attempt_invocation.resumed_session_id is None:
            # 请求了续传但 agent 没声明这个能力：静默回落全新会话。
            _logger.info(
                "Session resume requested for agent '%s' but it declares no resume "
                "capability; falling back to a fresh session.",
                agent_name,
            )
        observation = start_invocation(
            InvocationStartRequest(
                agent_name=agent_name,
                phase=invocation_phase,
                profile=profile,
                attempt_number=invocation_attempt,
                requested_model=(model_selection.model if model_selection is not None else None),
                requested_reasoning_effort=(
                    model_selection.reasoning_effort if model_selection is not None else None
                ),
                resumed_session_id=attempt_invocation.resumed_session_id,
            )
        )
        run_kwargs: dict[str, object] = {
            "command": list(attempt_invocation.argv),
            "cwd": worktree_path,
            "capture_output": capture_output,
            "timeout": timeout_seconds,
            "label": label,
            "output_protocol": attempt_invocation.output_protocol,
        }
        if inactivity_timeout_seconds is not None:
            run_kwargs["inactivity_timeout"] = inactivity_timeout_seconds
        if cancel_check is not None:
            # 条件传递：与 inactivity_timeout 同理，老 FakeProcessRunner 签名里没有
            # 这个参数，默认调用路径的 argv 与 run_kwargs 必须逐字节不变。
            run_kwargs["cancel_check"] = cancel_check
        if attempt_invocation.prompt_delivery == PROMPT_DELIVERY_STDIN:
            run_kwargs["input_text"] = prompt
        # 监督上下文每次进入都新建：attempt_key 唯一到"这一次真实进程调用"，
        # 因此遗留 observer 不可能在后续 recovery 轮次里找到可取消的同名进程。
        stall_request = _build_stall_supervision_request()
        if stall_request is not None:
            run_kwargs["attempt_key"] = stall_request.attempt_key

        def _run_attempt() -> CommandResult:
            try:
                return process_runner.run(**run_kwargs)
            except FileNotFoundError as exc:
                # 进程边界兜底：PATH 的判定权留给 claim 之前的门禁与用户可见的
                # ``kc agent setup``，这里只在真实起跑失败时把阶段、用途与可执行文件名
                # 补进异常，让日志与失败评论不再只剩一句 command not found。不在起跑前
                # 主动查 PATH——注入的 runner 与本机 PATH 无关，提前查会让 CI（不安装任何
                # agent CLI）里的真实流程测试全线变红。
                raise AgentUnavailableError(
                    agent_name,
                    context=(
                        f"launching bin '{attempt_invocation.argv[0]}' for phase "
                        f"'{invocation_phase}' with profile '{profile}'"
                    ),
                ) from exc

        try:
            attempt_result = (
                _run_attempt()
                if stall_request is None
                else supervised_agent_invocation(stall_request, _run_attempt)
            )
        except Exception as exc:
            finish_invocation(observation, exc=exc)
            _persist_session_id(
                _session_id_from_exception(exc),
                continuation_session_id=resume_id,
            )
            raise
        finish_invocation(observation, result=attempt_result)
        _persist_session_id(
            attempt_result.session_id,
            continuation_session_id=resume_id,
        )
        return attempt_result

    def _invoke_resumable(resume_id: str) -> CommandResult:
        """先按续传形态跑；确认 CLI 没认下这个会话时，同一轮内改跑全新会话。

        判据只看"这轮到底起跑没有"：CLI 认下 ``--resume`` 就会先广播一个新的会话
        id，因此**没观测到任何会话 id** 且以非零码收场（或抛出等价失败异常）才降级
        重跑。起跑之后再失败（跑一半崩、超时被杀）必须原样交给上层 recovery，否则
        白扔掉已经取得的进度——也不能把一次续传失败烧成一个废弃的 recovery 轮次
        （PRD FR-4）。
        """
        failure_detail: str
        try:
            resumed_result = _invoke(resume_id)
        except Exception as exc:
            if _session_id_from_exception(exc) is not None:
                raise
            exit_code = getattr(exc, "returncode", None)
            if not isinstance(exit_code, int) or exit_code == 0:
                raise
            failure_detail = f"exit_code={exit_code} ({type(exc).__name__})"
        else:
            if not _resumed_run_never_started(resumed_result):
                return resumed_result
            failure_detail = f"exit_code={resumed_result.return_code}"
        _logger.warning(
            "Resume of session %s did not start (%s); re-running this attempt with a "
            "fresh session.",
            resume_id,
            failure_detail,
        )
        # 重跑是**新的一次实际进程调用**：先声明替代关系，观测侧才会把它以
        # retry_of 挂到刚才那次没起跑的续传调用上。
        link_next_invocation(RETRY_REASON_RESUME_NOT_STARTED)
        return _invoke(None)

    result = (
        _invoke(resume_session_id)
        if resume_session_id is None
        else _invoke_resumable(resume_session_id)
    )
    if issue is not None:
        _logger.info(
            "Agent finished for Issue #%d: %s (exit_code=%d)",
            issue.number,
            issue.url,
            result.return_code,
        )
    return result


def _session_id_from_exception(exc: BaseException) -> str | None:
    """从执行端挂回的失败异常上读出抛错前观测到的会话 id。"""
    captured = getattr(exc, AGENT_SESSION_ID_ATTR_NAME, None)
    return captured if isinstance(captured, str) and captured else None


def _resumed_run_never_started(result: CommandResult) -> bool:
    """续传调用是否"根本没起跑"：非零退出且输出流里没自报过任何会话 id。

    JSONL agent 在会话启动时报告新 id，因此"无会话 id + 非零退出"可用来识别
    未起跑；Kimi 的 id 可能只在退出提示中出现。超时没有可判定的退出码，始终交给
    上层恢复处理，不能改跑全新会话，否则会白扔掉已经取得的进度。
    """
    return result.return_code != 0 and result.session_id is None


def run_agent_until_committed(
    *,
    selected_agent: str,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    before_sha: str,
    expected_branch: str,
    prompt_override: str | None = None,
    on_attempt_recorded: Callable[[AttemptResult, list[AttemptResult]], None] | None = None,
    on_agent_usage: Callable[[str, str, TokenUsage], None] | None = None,
    before_verifier_callback: Callable[[], None] | None = None,
    model_selection: ModelSelection | None = None,
    publish_stage: PublishStage = PublishStage.NORMAL,
    repo_id: str | None = None,
) -> AgentCommitResult:
    """运行 Agent recovery 状态机并返回最终提交结果；可在 verifier 前回调发布 Draft PR。"""
    from backend.core.use_cases.run_agent_execution_loop import (
        AgentExecutionRequest,
    )
    from backend.core.use_cases.run_agent_execution_loop import (
        run_agent_until_committed as run_execution_loop,
    )

    return run_execution_loop(
        AgentExecutionRequest(
            selected_agent=selected_agent,
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            before_sha=before_sha,
            expected_branch=expected_branch,
            prompt_override=prompt_override,
            on_attempt_recorded=on_attempt_recorded,
            on_agent_usage=on_agent_usage,
            before_verifier_callback=before_verifier_callback,
            model_selection=model_selection,
            repo_id=repo_id,
            # 首轮续传：worktree 里留有本 Issue 上一轮的会话记录就说明这是"接着跑"
            # （崩溃对账重新入队 / 部分进度续作），而不是新任务被历史会话污染。
            resume_session_id=resolve_resumable_session_id(
                worktree_path,
                config=config,
                agent_name=selected_agent,
                scope=AgentSessionScope(issue_number=issue.number),
            ),
            publish_stage=publish_stage,
        )
    )


def run_once(
    *,
    repo_path: Path,
    config: AppConfig,
    dry_run: bool,
    agent: str,
    max_issues: int,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
) -> int:
    """Compatibility entry point for the orchestrated single-pass runner."""
    from backend.core.use_cases.agent_runner_orchestrate import run_once as _run_once

    return _run_once(
        repo_path=repo_path,
        config=config,
        dry_run=dry_run,
        agent=agent,
        max_issues=max_issues,
        github_client=github_client,
        process_runner=process_runner,
    )

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
import subprocess
from collections.abc import Callable, Mapping
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
    AGENT_PROFILE_GENERATE,
    AGENT_PROFILE_RUN,
    PROMPT_DELIVERY_STDIN,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_response_text import extract_agent_response_text
from backend.core.use_cases.agent_invocation import (
    UnknownAgentError,
    build_agent_invocation,
    resolve_agent_spec,
    resolve_profile_spec,
    resolve_registered_agents,
)
from backend.core.use_cases.agent_invocation_tracing import (
    PHASE_FIX,
    PHASE_IMPLEMENTATION,
    PHASE_SUPERVISOR,
    PHASE_UNSPECIFIED,
    RETRY_REASON_RESUME_NOT_STARTED,
    RETRY_REASON_TRANSIENT,
    InvocationStartRequest,
    active_invocation_trace_context,
    finish_invocation,
    link_next_invocation,
    start_invocation,
)
from backend.core.use_cases.agent_runner_attempt import (
    AttemptPhaseTimer,
    _append_attempt_and_notify,
    _make_attempt_result,
    wait_before_recovery_attempt,
)
from backend.core.use_cases.agent_runner_stall_supervision import (
    StallDiagnosisRequest,
    StallSupervisionRequest,
    build_supervision_attempt_key,
    supervised_agent_invocation,
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
    is_transient_failure,
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
    resolve_resumable_session_id,
    save_agent_session_record,
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

_logger = logging.getLogger(__name__)

REPAIR_AGENT_SELF = "self"
"""``repair_agent`` 取值：本阶段的审核者 / supervisor 自己修（默认，历史行为）。"""

REPAIR_AGENT_EXECUTOR = "executor"
"""``repair_agent`` 取值：把修复交回本次实现者。"""

__all__ = [
    "AgentRunnerAttemptError",
    "AgentUnavailableError",
    "MaxRetriesExceededError",
    "PrdDeliveryError",
    "ProviderCapacityError",
    "PublishFailureError",
    "EmptyCommitRequestError",
    "REPAIR_AGENT_EXECUTOR",
    "REPAIR_AGENT_SELF",
    "UnknownAgentError",
    "UnrecoverableError",
    "VerificationFailedError",
    "AttemptPhaseTimer",
    "_ensure_worktree_branch",
    "_append_attempt_and_notify",
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
    "resolve_agent_fallback_order",
    "resolve_prd_archive_path",
    "resolve_repair_agent",
    "resolve_reviewer_agent",
    "resolve_supervisor_agent",
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


def choose_agent(issue: IssueSummary, config: AppConfig, override_agent: str) -> str:
    """Choose an AI agent for the Issue.

    优先级：显式 ``override_agent``（CLI / loop recipe）> 阶段预设绑定
    （``lifecycle_presets``，预设整体决定 agent）> Issue 上的 agent 标签路由
    > 生命周期矩阵 ``implementation`` 显式声明 > ``runner.default_agent`` >
    内置默认（``claude``）。未绑定预设时标签路由与 loop recipe 仍是更高
    优先级——矩阵只替换 ``default_agent`` 这一回落层。
    """
    if override_agent != "auto":
        return override_agent
    bound_agent = _resolve_bound_lifecycle_agent("implementation", config, issue=issue)
    if bound_agent is not None:
        return bound_agent
    for agent_name, label in config.labels.agent_labels.items():
        if label in issue.labels:
            return agent_name
    declared_agent = _resolve_declared_lifecycle_agent(
        "implementation", config, issue=issue, override_agent=override_agent
    )
    if declared_agent is not None:
        return declared_agent
    return config.runner.default_agent if config.runner.default_agent != "auto" else "claude"


def resolve_agent_fallback_order(
    issue: IssueSummary,
    config: AppConfig,
    override_agent: str,
) -> list[str]:
    """Return the ordered list of agents to try for an Issue.

    The first entry is the primary agent resolved by :func:`choose_agent`.
    Subsequent entries come from ``config.runner.agent_fallback_order`` with the
    primary agent and duplicates removed, preserving configured order. When no
    fallback order is configured the list contains only the primary agent, so
    the escalation ladder behaves exactly like single-agent runs.

    Args:
        issue: Issue being processed.
        config: Agent Runner configuration.
        override_agent: The ``--agent`` override (``"auto"`` routes by label).

    Returns:
        Ordered, de-duplicated agent names to attempt.
    """
    primary_agent = choose_agent(issue, config, override_agent)
    fallback_order = [primary_agent]
    for candidate_agent in config.runner.agent_fallback_order:
        normalized_agent = candidate_agent.strip()
        if normalized_agent and normalized_agent not in fallback_order:
            fallback_order.append(normalized_agent)
    return fallback_order


def resolve_repair_agent(
    raw_setting: str,
    *,
    issue: IssueSummary,
    config: AppConfig,
    reviewing_agent: str,
    executor_agent: str | None = None,
) -> str:
    """解析某阶段的 ``repair_agent`` 配置为实际执行修复的 agent 名。

    ``self`` 返回本阶段的审核者 / supervisor；``executor`` 返回本次真正执行
    实现的 agent，调用方不知道它时回落为按 Issue 标签路由的结果并在日志中
    注明来源；其它取值必须是已注册 agent，未注册时抛
    :class:`UnknownAgentError`（fail-fast，不静默回落到别人）。

    Args:
        raw_setting: 配置里的原始取值。
        issue: 当前 Issue，``executor`` 回落时用于标签路由。
        config: 应用配置，注册表取自 ``config.agents``。
        reviewing_agent: 本阶段的审核者 / supervisor。
        executor_agent: 本次真正执行实现的 agent；``None`` 表示调用方不知道，
            此时按 Issue 标签回落。

    Returns:
        执行修复的 agent 名。

    Raises:
        UnknownAgentError: 取值既不是 ``self`` / ``executor``，也不在注册表中。
    """
    normalized_setting = _normalize_repair_agent_setting(raw_setting)
    if normalized_setting == REPAIR_AGENT_SELF:
        return reviewing_agent
    if normalized_setting == REPAIR_AGENT_EXECUTOR:
        if executor_agent is not None:
            return executor_agent
        fallback_agent = choose_agent(issue, config, "auto")
        _logger.info(
            "repair_agent='executor' for Issue #%d: the executor of this run is "
            "unknown here, falling back to Issue-label routing -> '%s'.",
            issue.number,
            fallback_agent,
        )
        return fallback_agent
    resolve_agent_spec(normalized_setting, config)
    return normalized_setting


def repair_agent_is_self(raw_setting: str) -> bool:
    """``repair_agent`` 是否表示"本阶段的审核者 / supervisor 自己修"。"""
    return _normalize_repair_agent_setting(raw_setting) == REPAIR_AGENT_SELF


def _normalize_repair_agent_setting(raw_setting: str) -> str:
    """把 ``repair_agent`` 配置值规范化成小写、去空白的裸值。"""
    return (raw_setting or "").strip().lower() or REPAIR_AGENT_SELF


def resolve_supervisor_agent(
    issue: IssueSummary,
    config: AppConfig,
    override_agent: str,
    *,
    fallback_agent: str | None = None,
    prd_overrides: Mapping[str, str] | None = None,
) -> str:
    """解析 post-PR supervisor：命令行 ``--agent`` > 配置 > 回落。

    生命周期矩阵（或 PRD 覆盖）显式声明了具体 agent 时用它；``fallback_agent``
    描述"配置为 ``auto`` 时用谁"：发布路径传本次实现者（保持历史行为），
    ``kc review`` 不传，回落到按 Issue 标签路由。

    Args:
        issue: 当前 Issue。
        config: 应用配置。
        override_agent: 命令行 ``--agent``；``"auto"`` 表示未指定。
        fallback_agent: 配置为 ``auto`` 时的回落 agent；``None`` 表示按标签路由。
        prd_overrides: PRD 文件头部覆盖（最高优先级），可选。

    Returns:
        supervisor agent 名。
    """
    if override_agent != "auto":
        return override_agent
    declared_agent = _resolve_declared_lifecycle_agent(
        "supervisor",
        config,
        issue=issue,
        selected_agent=fallback_agent,
        prd_overrides=prd_overrides,
    )
    if declared_agent is not None:
        return declared_agent
    configured_agent = config.post_pr_supervisor.supervisor_agent
    if configured_agent != "auto":
        return configured_agent
    if fallback_agent is not None:
        return fallback_agent
    return choose_agent(issue, config, "auto")


def _resolve_bound_lifecycle_agent(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> str | None:
    """返回阶段预设绑定声明的 agent；未绑定时返回 ``None``。

    绑定整体决定 (agent, 模型, 推理档)，因此 agent 也取预设声明值；未知
    预设名 / 未注册 agent 的 fail-fast 语义与 :func:`resolve_lifecycle_agent`
    一致。局部导入 :mod:`lifecycle_agent_resolution` 以打破循环依赖。
    """
    from backend.core.use_cases.lifecycle_agent_resolution import (
        declared_lifecycle_preset,
    )

    bound_preset_name = declared_lifecycle_preset(
        lifecycle, config, issue=issue, prd_preset_overrides=prd_preset_overrides
    )
    if bound_preset_name is None:
        return None
    from backend.core.shared.models.agent_model_preset import resolve_model_selection
    from backend.core.use_cases.lifecycle_agent_resolution import _validate_registered

    bound_selection = resolve_model_selection(bound_preset_name, config)
    return _validate_registered(bound_selection.agent, lifecycle=lifecycle, config=config)


def _resolve_declared_lifecycle_agent(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    selected_agent: str | None = None,
    override_agent: str = "auto",
    prd_overrides: Mapping[str, str] | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> str | None:
    """返回矩阵 / PRD 覆盖 / 预设绑定**显式声明**的具体 agent；纯 auto 或全未声明时返回 ``None``。

    这是各阶段既有解析函数接入生命周期矩阵与预设绑定的统一入口：只有"显式
    换人"才短路，声明 ``auto`` 且无绑定时交回各函数原有的 auto 语义，绝不改变
    既有行为。阶段绑定预设时预设整体声明该阶段的 agent（遮蔽矩阵同键声明）。

    局部导入 :mod:`lifecycle_agent_resolution` 是为了打破它与本模块的循环依赖
    （解析函数需要 ``choose_agent`` / ``resolve_registered_agents``）。
    """
    from backend.core.shared.models.lifecycle_agent import (
        LIFECYCLE_AGENT_AUTO,
        normalize_lifecycle_agent_value,
    )
    from backend.core.use_cases.lifecycle_agent_resolution import (
        declared_lifecycle_preset,
        effective_prd_overrides,
        resolve_lifecycle_agent,
    )

    merged_overrides = effective_prd_overrides(issue, prd_overrides)
    declared_value = merged_overrides.get(lifecycle)
    if declared_value is None:
        declared_value = config.lifecycle_agents.declared_value(lifecycle)
    bound_preset_name = declared_lifecycle_preset(
        lifecycle, config, issue=issue, prd_preset_overrides=prd_preset_overrides
    )
    if declared_value is None and bound_preset_name is None:
        return None
    if bound_preset_name is None and normalize_lifecycle_agent_value(declared_value) == (
        LIFECYCLE_AGENT_AUTO
    ):
        return None
    return resolve_lifecycle_agent(
        lifecycle,
        config,
        issue=issue,
        selected_agent=selected_agent,
        override_agent=override_agent,
        prd_overrides=merged_overrides,
        prd_preset_overrides=prd_preset_overrides,
    )


def resolve_reviewer_agent(
    issue: IssueSummary,
    config: AppConfig,
    selected_agent: str,
    *,
    prd_overrides: Mapping[str, str] | None = None,
) -> str:
    """解析 pre-PR review 的审核者。

    生命周期矩阵（或 PRD 覆盖）显式声明了具体 agent 时用它；声明 ``auto`` 或
    两层都未声明时沿用既有语义：显式 ``pre_pr_review.review_agent`` 优先；
    ``auto`` 时在 ``allow_same_agent`` 为真时沿用实现者，为假时从 agent 注册表
    里取第一个不等于实现者的 agent（不再硬编码 ``codex``）。注册表里只有实现者
    一个 agent 时保持实现者并 WARN。

    Args:
        issue: 当前 Issue。
        config: 应用配置。
        selected_agent: 本次实现者。
        prd_overrides: PRD 文件头部覆盖（最高优先级），可选。

    Returns:
        审核者 agent 名。
    """
    declared_agent = _resolve_declared_lifecycle_agent(
        "review",
        config,
        issue=issue,
        selected_agent=selected_agent,
        prd_overrides=prd_overrides,
    )
    if declared_agent is not None:
        return declared_agent
    review_config = config.pre_pr_review
    if review_config.review_agent != "auto":
        return review_config.review_agent
    if review_config.allow_same_agent:
        return selected_agent
    for registered_agent in resolve_registered_agents(config):
        if registered_agent != selected_agent:
            return registered_agent
    _logger.warning(
        "pre_pr_review.allow_same_agent=false for Issue #%d but the agent "
        "registry only contains '%s'; keeping it as the reviewer.",
        issue.number,
        selected_agent,
    )
    return selected_agent


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
        from backend.core.use_cases.lifecycle_agent_resolution import (  # noqa: PLC0415
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

    def _persist_session_id(session_id: str | None) -> None:
        """把本次调用自报的会话 id 落在 worktree 局部记录里。

        只记 run 用途的会话：fix / closeout / verifier 是另一段独立对话，写进同一个
        per-agent 记录会把主实现会话覆盖掉，恢复时续错对象。落盘是旁路，失败只记
        日志——续传是增益路径，不能反过来把正常执行拖下水。
        """
        if not session_id or profile != AGENT_PROFILE_RUN:
            return
        try:
            save_agent_session_record(
                worktree_path,
                agent_name=agent_name,
                session_id=session_id,
                issue_number=issue.number if issue is not None else None,
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
        if attempt_invocation.prompt_delivery == PROMPT_DELIVERY_STDIN:
            run_kwargs["input_text"] = prompt
        # 监督上下文每次进入都新建：attempt_key 唯一到"这一次真实进程调用"，
        # 因此遗留 observer 不可能在后续 recovery 轮次里找到可取消的同名进程。
        stall_request = _build_stall_supervision_request()
        if stall_request is not None:
            run_kwargs["attempt_key"] = stall_request.attempt_key

        def _run_attempt() -> CommandResult:
            return process_runner.run(**run_kwargs)

        try:
            attempt_result = (
                _run_attempt()
                if stall_request is None
                else supervised_agent_invocation(stall_request, _run_attempt)
            )
        except Exception as exc:  # noqa: BLE001 - 旁路落盘后原样抛出，不改变失败语义。
            finish_invocation(observation, exc=exc)
            _persist_session_id(_session_id_from_exception(exc))
            raise
        finish_invocation(observation, result=attempt_result)
        _persist_session_id(attempt_result.session_id)
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
        except Exception as exc:  # noqa: BLE001 - 判据成立才降级，其余原样上抛。
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

    CLI 认下了 ``--resume`` 就会先广播一个新会话 id（claude 的 ``system/init``），
    因此"无会话 id + 非零退出"是"参数没被接受 / 会话不存在"的可靠信号；反过来，
    起跑后再失败（超时、跑到一半崩）不能重跑，否则会白扔掉已经取得的进度。
    """
    return result.return_code != 0 and result.session_id is None


def drop_model_selection_for_agent(
    agent_name: str,
    model_selection: ModelSelection | None,
) -> ModelSelection | None:
    """执行 agent 与预设声明 agent 不一致时丢弃模型绑定并记日志。

    不同 CLI 的模型命名空间不同（codebuddy 的 ``--settings`` 对 claude 无意义），
    绝不把 A CLI 的模型参数塞给 B CLI。回退换人与显式 ``--agent`` 换人都走
    这里——两种情形都表现为"执行 agent != 预设 agent"。

    Returns:
        匹配时原样返回 ``model_selection``；不匹配（或入参为空）时返回 ``None``。
    """
    if model_selection is None:
        return None
    if model_selection.agent == agent_name:
        return model_selection
    _logger.warning(
        "model binding dropped on agent switch: preset declares agent '%s' but this "
        "attempt runs '%s'; the model/effort flags are NOT applied (each CLI has its "
        "own model namespace).",
        model_selection.agent,
        agent_name,
    )
    return None


def run_agent_with_prompt_resilient(
    agent_name: str,
    prompt: str,
    worktree_path: Path,
    process_runner: IProcessRunner,
    *,
    transient_retry_attempts: int = 2,
    transient_retry_delay_seconds: int = 10,
    **agent_call_options: object,
) -> CommandResult:
    """Run an agent, retrying transient network/transport failures in place.

    Level 1 of the escalation ladder. Only :func:`is_transient_failure` errors
    (dropped sockets, connection resets, gateway timeouts, 5xx) are retried with
    the same agent, because re-issuing the request usually succeeds. A missing
    agent CLI is surfaced as :class:`AgentUnavailableError` so the orchestration
    layer can skip to the next agent; every other error propagates unchanged so
    the recovery loop or the cross-agent fallback can handle it.

    ``model_selection``（若传入）先经 :func:`drop_model_selection_for_agent`
    校验：跨 agent 回退或显式 ``--agent`` 换人时丢弃绑定并记日志，本函数内
    的原地瞬态重试不换 agent，绑定保持有效。

    Args:
        agent_name: Agent to invoke (claude / codex / kimi).
        prompt: Prepared prompt text.
        worktree_path: Worktree the agent runs in.
        process_runner: Command executor.
        transient_retry_attempts: Extra retries granted to transient failures.
        transient_retry_delay_seconds: Backoff between transient retries.
        agent_call_options: 原样透传给 :func:`run_agent_with_prompt` 的关键字参数
            （`config` / `capture_output` / `timeout_seconds` /
            `inactivity_timeout_seconds` / `issue` / `profile` /
            `model_selection`），两个入口共用同一份参数契约，避免逐字段重复
            声明而漂移。

    Returns:
        The successful :class:`CommandResult`.

    Raises:
        AgentUnavailableError: The agent CLI could not be launched.
        Exception: The original error when it is not transient or retries are
            exhausted.
    """
    max_retries = max(0, transient_retry_attempts)
    forwarded_options = dict(agent_call_options)
    raw_model_selection = forwarded_options.get("model_selection")
    if raw_model_selection is not None:
        forwarded_options["model_selection"] = drop_model_selection_for_agent(
            agent_name,
            raw_model_selection if isinstance(raw_model_selection, ModelSelection) else None,
        )
    agent_call_issue = forwarded_options.get("issue")
    issue_number = agent_call_issue.number if isinstance(agent_call_issue, IssueSummary) else 0
    for retry_index in range(max_retries + 1):
        if retry_index > 0:
            # 原地瞬态重试是**新的一次实际进程调用**：新建 invocation 并用 retry_of
            # 关联上一次，绝不合并成一条记录（否则"重试了三次才成功"会消失）。
            link_next_invocation(RETRY_REASON_TRANSIENT)
        try:
            return run_agent_with_prompt(
                agent_name,
                prompt,
                worktree_path,
                process_runner,
                **forwarded_options,
            )
        except FileNotFoundError as exc:
            raise AgentUnavailableError(agent_name) from exc
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            if retry_index >= max_retries or not is_transient_failure(exc):
                raise
            _logger.warning(
                "Transient error from agent '%s' for Issue #%d; retrying (%d/%d): %s",
                agent_name,
                issue_number,
                retry_index + 1,
                max_retries,
                exc,
            )
            wait_before_recovery_attempt(
                issue_number,
                recovery_attempt=retry_index + 1,
                max_recovery_attempts=max_retries,
                delay_seconds=transient_retry_delay_seconds,
            )
    raise RuntimeError("unreachable: resilient agent retry loop exited")


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
    model_selection: ModelSelection | None = None,
    publish_stage: PublishStage = PublishStage.NORMAL,
) -> AgentCommitResult:
    """运行 Agent recovery 状态机并返回最终提交结果。"""
    from backend.core.use_cases.run_agent_execution_loop import (
        AgentExecutionRequest,
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
            model_selection=model_selection,
            # 首轮续传：worktree 里留有本 Issue 上一轮的会话记录就说明这是"接着跑"
            # （崩溃对账重新入队 / 部分进度续作），而不是新任务被历史会话污染。
            resume_session_id=resolve_resumable_session_id(
                worktree_path,
                config=config,
                agent_name=selected_agent,
                issue_number=issue.number,
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

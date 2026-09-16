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

import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from backend.core.agent.memory import (
    save_short_term_memory,
)
from backend.core.shared.interfaces.agent_output_protocol import (
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
)
from backend.core.shared.interfaces.agent_runner import (
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    AttemptResult,
    CommandResult,
    IssueSummary,
)
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_RUN,
    PROMPT_DELIVERY_STDIN,
)
from backend.core.use_cases.agent_invocation import build_agent_invocation
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
from backend.core.use_cases.agent_runner_publish import (
    publish_changes,
    run_preflight_checks,
    validate_publish_remote,
    validate_safe_changes,
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

__all__ = [
    "AgentRunnerAttemptError",
    "AgentUnavailableError",
    "MaxRetriesExceededError",
    "PrdDeliveryError",
    "ProviderCapacityError",
    "PublishFailureError",
    "EmptyCommitRequestError",
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
    "resolve_agent_fallback_order",
    "resolve_prd_archive_path",
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
    """Choose an AI agent for the Issue."""
    if override_agent != "auto":
        return override_agent
    for agent_name, label in config.labels.agent_labels.items():
        if label in issue.labels:
            return agent_name
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
    return "\n".join(f"- `{command}`" for command in commands)


def run_agent(
    agent_name: str,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    *,
    timeout_seconds: int | None = None,
    inactivity_timeout_seconds: int | None = None,
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
    )


def run_fix_agent(
    agent_name: str,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    verification_results: list[CommandResult],
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
) -> CommandResult:
    """Run an agent with a prepared prompt.

    命令行与输出协议全部来自 :func:`build_agent_invocation`（profile
    ``"run"``）；调用方传入的 ``process_runner`` 只负责执行与中继。
    """
    if issue is not None:
        _logger.info(
            "Starting agent for Issue #%d: %s",
            issue.number,
            issue.url,
        )
    invocation = build_agent_invocation(
        agent_name,
        AGENT_PROFILE_RUN,
        prompt,
        worktree_path,
        config or AppConfig(),
    )
    label = f"Issue #{issue.number}: {issue.url}" if issue is not None else None
    run_kwargs: dict[str, object] = {
        "command": list(invocation.argv),
        "cwd": worktree_path,
        "capture_output": capture_output,
        "timeout": timeout_seconds,
        "label": label,
        "output_protocol": invocation.output_protocol,
    }
    if inactivity_timeout_seconds is not None:
        run_kwargs["inactivity_timeout"] = inactivity_timeout_seconds
    if invocation.prompt_delivery == PROMPT_DELIVERY_STDIN:
        run_kwargs["input_text"] = prompt
    result = process_runner.run(**run_kwargs)
    if issue is not None:
        _logger.info(
            "Agent finished for Issue #%d: %s (exit_code=%d)",
            issue.number,
            issue.url,
            result.return_code,
        )
    return result


def run_agent_with_prompt_resilient(
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
    transient_retry_attempts: int = 2,
    transient_retry_delay_seconds: int = 10,
) -> CommandResult:
    """Run an agent, retrying transient network/transport failures in place.

    Level 1 of the escalation ladder. Only :func:`is_transient_failure` errors
    (dropped sockets, connection resets, gateway timeouts, 5xx) are retried with
    the same agent, because re-issuing the request usually succeeds. A missing
    agent CLI is surfaced as :class:`AgentUnavailableError` so the orchestration
    layer can skip to the next agent; every other error propagates unchanged so
    the recovery loop or the cross-agent fallback can handle it.

    Args:
        agent_name: Agent to invoke (claude / codex / kimi).
        prompt: Prepared prompt text.
        worktree_path: Worktree the agent runs in.
        process_runner: Command executor.
        config: 应用配置；注册表取自 ``config.agents``，``None`` 时使用
            内置默认注册表。
        capture_output: Whether to capture stdout/stderr.
        timeout_seconds: Optional per-invocation timeout.
        inactivity_timeout_seconds: Optional no-output timeout.
        issue: Optional Issue for logging context.
        transient_retry_attempts: Extra retries granted to transient failures.
        transient_retry_delay_seconds: Backoff between transient retries.

    Returns:
        The successful :class:`CommandResult`.

    Raises:
        AgentUnavailableError: The agent CLI could not be launched.
        Exception: The original error when it is not transient or retries are
            exhausted.
    """
    max_retries = max(0, transient_retry_attempts)
    issue_number = issue.number if issue is not None else 0
    for retry_index in range(max_retries + 1):
        try:
            return run_agent_with_prompt(
                agent_name,
                prompt,
                worktree_path,
                process_runner,
                config=config,
                capture_output=capture_output,
                timeout_seconds=timeout_seconds,
                inactivity_timeout_seconds=inactivity_timeout_seconds,
                issue=issue,
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


def extract_agent_response_text(result: CommandResult) -> str:
    """Return assistant response text from direct stdout or Claude stream-json.

    Claude 使用 `--output-format stream-json` 时，每行输出是一个 JSON 事件，
    包含 stream_event（文本增量）、assistant（完整消息）或 result（最终结果）。
    本函数按优先级提取有效文本；非流式协议的结果直接返回原始 stdout。

    注意：流式协议会把事件流渲染成纯文本再返回，此时 stdout 已不是原始
    事件流；若仍逐行重解析，恰好构成合法 JSON 标量的行（如数组末尾不带
    逗号的字符串元素）会被静默丢弃，破坏其中的 JSON 内容。因此只有
    ``output_protocol`` 确实是流式协议时才走事件提取，否则原样返回。
    """
    if not result.stdout:
        return ""
    if result.output_protocol != CLAUDE_STREAM_JSON_PROTOCOL_ID:
        return result.stdout

    stream_text_parts: list[str] = []
    assistant_text_parts: list[str] = []
    result_parts: list[str] = []
    saw_stream_json_event = False
    for output_line in result.stdout.splitlines():
        try:
            event_payload = json.loads(output_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event_payload, dict):
            continue
        event_type = event_payload.get("type")
        if event_type == "stream_event":
            saw_stream_json_event = True
            _append_claude_stream_event_text(event_payload, stream_text_parts)
        elif event_type == "assistant":
            saw_stream_json_event = True
            _append_claude_assistant_text(event_payload, assistant_text_parts)
        elif event_type == "result":
            saw_stream_json_event = True
            result_text = str(event_payload.get("result") or "").strip()
            if result_text:
                result_parts.append(result_text)

    if not saw_stream_json_event:
        return result.stdout
    if stream_text_parts:
        return "".join(stream_text_parts)
    if assistant_text_parts:
        return "".join(assistant_text_parts)
    if result_parts:
        return "\n".join(result_parts)
    return result.stdout


def _append_claude_stream_event_text(
    event_payload: dict[str, object],
    text_parts: list[str],
) -> None:
    event = event_payload.get("event")
    if not isinstance(event, dict):
        return
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return
    if delta.get("type") == "text_delta":
        text_parts.append(str(delta.get("text", "")))


def _append_claude_assistant_text(
    event_payload: dict[str, object],
    text_parts: list[str],
) -> None:
    message = event_payload.get("message")
    if not isinstance(message, dict):
        return
    content_blocks = message.get("content", [])
    if not isinstance(content_blocks, list):
        return
    for content_block in content_blocks:
        if not isinstance(content_block, dict):
            continue
        if content_block.get("type") == "text":
            text_parts.append(str(content_block.get("text", "")))


def _resolve_memory_stores(worktree_path: Path, memory_config):
    """Construct the long-term + skill stores for prompt injection.

    Returns ``(None, None)`` when memory is disabled so callers can fall
    back to non-injecting behaviour without sprinkling the same guard
    everywhere. The actual composition lives in
    ``core/agent/memory/_composition.py`` which dynamically loads the
    ``infrastructure/`` implementations, preserving the strict
    ``core -> infrastructure`` ban.
    """
    from backend.core.agent.memory._composition import (
        build_default_memory_services,
    )

    services = build_default_memory_services(worktree_path, memory_config)
    return services.long_term, services.skill


def _persist_short_term_memory(
    *,
    config: AppConfig,
    issue: IssueSummary,
    worktree_path: Path,
    attempt: AttemptResult,
    repo_id: str,
) -> None:
    """Best-effort save of a single attempt into the short-term memory store."""
    if not config.memory.enabled:
        return
    try:
        from backend.core.agent.memory._composition import (
            build_default_memory_services,
        )

        services = build_default_memory_services(worktree_path, config.memory)
        if services.short_term is None:
            return
        save_short_term_memory(
            repo_id=repo_id,
            issue=issue,
            attempt_result=attempt,
            worktree_path=worktree_path,
            memory_config=config.memory,
            store=services.short_term,
        )
    except Exception as exc:  # noqa: BLE001 - memory side-channel must not break runner.
        _logger.warning(
            "Failed to record short-term memory for Issue #%d attempt %d: %s",
            issue.number,
            attempt.attempt_number,
            exc,
        )


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

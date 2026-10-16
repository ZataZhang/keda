"""Failure classification and exception types for the agent runner.

评论与 attempt history 的 Markdown 渲染已拆至
:mod:`backend.core.use_cases.agent_runner_failure_format`（单文件非空行有 CI
硬上限）；本模块再导出其公共名，消费方入口保持不变。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from backend.core.shared.models.agent_runner import (
    AttemptResult,
    CommandResult,
    FailureType,
    PublishFailureCategory,
)
from backend.core.use_cases.agent_runner_failure_format import (
    ATTEMPT_HISTORY_HEADING,
    build_publish_failure_comment_body,
    detect_usage_limit_root_cause,
    format_agent_execution_failure,
    format_attempt_duration,
    format_attempt_history,
    format_blocked_failure_comment,
    format_failure_comment,
    format_failure_context_comment,
    format_minimal_failure_comment,
    format_publish_failure_comment,
    format_recovery_failure_summary,
    summarize_failure_exception,
)

__all__ = [
    "ATTEMPT_HISTORY_HEADING",
    "AgentExecutionError",
    "AgentRunnerAttemptError",
    "AgentUnavailableError",
    "ForbiddenBlockedError",
    "MaxRetriesExceededError",
    "ProviderCapacityError",
    "PublishFailureError",
    "UnrecoverableError",
    "build_publish_failure_comment_body",
    "classify_failure",
    "detect_usage_limit_root_cause",
    "format_agent_execution_failure",
    "format_attempt_duration",
    "format_attempt_history",
    "format_blocked_failure_comment",
    "format_failure_comment",
    "format_failure_context_comment",
    "format_minimal_failure_comment",
    "format_publish_failure_comment",
    "format_recovery_failure_summary",
    "is_provider_capacity_failure",
    "is_recoverable_commit_request_error",
    "is_transient_failure",
    "summarize_failure_exception",
]


class AgentRunnerAttemptError(RuntimeError):
    """Base error that carries attempt history."""

    def __init__(
        self,
        message: str,
        attempt_results: list[AttemptResult],
    ) -> None:
        super().__init__(message)
        self.attempt_results = attempt_results


class MaxRetriesExceededError(AgentRunnerAttemptError):
    """Raised when all recovery attempts are exhausted."""

    def __init__(self, attempt_results: list[AttemptResult]) -> None:
        super().__init__(
            f"Failed after {len(attempt_results)} attempts.",
            attempt_results,
        )


class UnrecoverableError(AgentRunnerAttemptError):
    """Raised when an unrecoverable failure is encountered."""

    def __init__(
        self,
        message: str,
        attempt_results: list[AttemptResult],
    ) -> None:
        super().__init__(message, attempt_results)


class ForbiddenBlockedError(AgentRunnerAttemptError):
    """Raised when forbidden paths block the agent and require human intervention."""

    def __init__(
        self,
        message: str,
        attempt_results: list[AttemptResult],
    ) -> None:
        super().__init__(message, attempt_results)


class ProviderCapacityError(AgentRunnerAttemptError):
    """Raised when the agent's model provider is at capacity or rate limited.

    Capacity failures (429 usage limit, 529 overloaded) keep failing on the
    same agent until the provider's usage window resets, so the escalation
    ladder switches to a different agent instead of retrying in place.
    """


class AgentExecutionError(AgentRunnerAttemptError):
    """Raised when an agent attempt fails in a way another agent may survive.

    Catch-all for execution-side accidents that cannot be classified more
    precisely — wall-clock / inactivity timeouts, agent process crashes, and
    launch I/O errors. The escalation ladder switches agents on this error
    (same treatment as provider capacity) so a single flaky attempt cannot
    sink the whole Issue. Security / branch violations stay
    :class:`UnrecoverableError` and are never retried on another agent.
    """


class AgentUnavailableError(AgentRunnerAttemptError):
    """Raised when an agent CLI cannot be launched (command not found).

    Treated as agent-specific rather than a business failure: the escalation
    ladder skips the unavailable agent and tries the next one in the
    configured fallback order.
    """

    def __init__(
        self,
        agent: str,
        attempt_results: list[AttemptResult] | None = None,
    ) -> None:
        super().__init__(
            f"Agent '{agent}' is unavailable (command not found).",
            attempt_results or [],
        )
        self.agent = agent


class PublishFailureError(RuntimeError):
    """Raised when the publish phase fails after a local commit exists."""

    def __init__(
        self,
        message: str,
        *,
        worktree_path: Path | None = None,
        failure_category: PublishFailureCategory = PublishFailureCategory.UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.worktree_path = worktree_path
        self.failure_category = failure_category


def is_recoverable_commit_request_error(exc: BaseException) -> bool:
    """Return whether the agent can repair a commit request protocol error.

    CalledProcessError（如 pre-commit hook 失败、git commit 被拒绝）也视为可恢复，
    因为 agent 通常可以通过修改代码来修复这类问题。
    """
    # subprocess 命令失败（pre-commit、git 错误等）通常可由 agent 修复代码后重试
    if isinstance(exc, subprocess.CalledProcessError):
        return True
    message = str(exc)
    return message.startswith(
        (
            "Agent left uncommitted changes without a commit request.",
            "Commit request must be valid JSON.",
            "Commit request must be a JSON object.",
            "Agent requested a commit but produced no file changes.",
        )
    )


def classify_failure(
    *,
    before_sha: str,
    after_sha: str,
    has_uncommitted: bool,
    agent_result: CommandResult,
    verification_results: list[CommandResult],
    exc: BaseException | None = None,
    detect_provider_errors: bool = False,
) -> FailureType:
    """Classify the failure type of an agent execution attempt.

    Priority:
    1. UNRECOVERABLE (security/branch violations)
    2. UNCOMMITTED_CHANGES
    3. PROVIDER_CAPACITY / TRANSIENT (only when ``detect_provider_errors``)
    4. NO_COMMITS
    5. VERIFICATION_FAILED
    6. AGENT_ERROR
    7. SUCCESS

    Args:
        before_sha: SHA before the agent run.
        after_sha: SHA after the agent run.
        has_uncommitted: Whether the worktree has uncommitted changes.
        agent_result: Result of the agent CLI invocation.
        verification_results: Results of verification commands.
        exc: Optional exception raised during the attempt.
        detect_provider_errors: When ``True``, inspect ``exc`` for provider
            capacity / transient network signatures. Only the agent-invocation
            call site sets this; commit and verification failures must not be
            reclassified as transient just because their output mentions a
            network word.

    Returns:
        The classified failure type.
    """
    if exc is not None:
        if isinstance(exc, RuntimeError):
            exc_message = str(exc)
            if "Refusing to publish forbidden paths" in exc_message:
                return FailureType.FORBIDDEN_BLOCKED
            if "Refusing to commit on unexpected branch" in exc_message:
                return FailureType.UNRECOVERABLE
            if is_recoverable_commit_request_error(exc):
                return FailureType.UNCOMMITTED_CHANGES
        if detect_provider_errors:
            if is_provider_capacity_failure(exc):
                return FailureType.PROVIDER_CAPACITY
            if is_transient_failure(exc):
                return FailureType.TRANSIENT
        return FailureType.AGENT_ERROR

    if has_uncommitted:
        return FailureType.UNCOMMITTED_CHANGES

    if before_sha == after_sha:
        return FailureType.NO_COMMITS

    if any(result.return_code != 0 for result in verification_results):
        return FailureType.VERIFICATION_FAILED

    if agent_result.return_code != 0:
        return FailureType.AGENT_ERROR

    return FailureType.SUCCESS


#: Provider capacity / rate-limit signatures. A superset of the usage-limit
#: hint above: these mean the same agent will keep failing until the provider
#: window resets, so the runner should switch agents rather than retry.
#:
#: ``usage limit`` **不加** "exceeded/reached" 限定：codex 的原文是
#: "You've hit your usage limit."，旧写法认不出它，于是同一句额度耗尽在 builder
#: 路径里被判成普通 AGENT_ERROR（只重试不换 agent）、在 verifier 路径里直接把
#: Issue 判死。判定只作用于 agent 调用的异常文本（``detect_provider_errors``
#: 仅在该调用点开启），所以放宽到裸 "usage limit" 不会误伤验证输出。
_PROVIDER_CAPACITY_HINT_PATTERN = re.compile(
    r"usage limit"
    r"|daily count exceeded"
    r"|insufficient_quota"
    r"|request rejected \(429\)"
    r"|\b429\b"
    r"|too many requests"
    r"|rate.?limit"
    r"|overloaded"
    r"|\b529\b",
    re.IGNORECASE,
)

#: Transient network / transport signatures. These are worth retrying with the
#: same agent because re-issuing the request frequently succeeds.
_TRANSIENT_HINT_PATTERN = re.compile(
    r"socket connection (?:was )?closed"
    r"|connection reset"
    r"|connection closed"
    r"|connection refused"
    r"|connection error"
    r"|econnreset"
    r"|broken pipe"
    r"|bad gateway"
    r"|service unavailable"
    r"|gateway time-?out"
    r"|temporarily unavailable"
    r"|\b50[234]\b"
    r"|\b400\b"
    r"|invalid params"
    r"|invalid parameter"
    r"|invalid request"
    r"|bad request"
    r"|malformed request"
    r"|input should be a valid dictionary"
    r"|timed out"
    r"|read timeout"
    r"|network error",
    re.IGNORECASE,
)


def _failure_text(exc: BaseException) -> str:
    """Return searchable text for an exception, including subprocess output.

    ``CommandFailedError`` (and other ``CalledProcessError`` subclasses) carry
    the agent's captured stdout/stderr on ``output`` / ``stderr``; the failure
    signature usually lives there rather than in ``str(exc)``.
    """
    parts = [str(exc)]
    for attribute_name in ("output", "stderr"):
        attribute_value = getattr(exc, attribute_name, None)
        if attribute_value:
            parts.append(str(attribute_value))
    return "\n".join(parts)


def is_provider_capacity_failure(exc: BaseException) -> bool:
    """Return whether ``exc`` indicates the provider is at capacity.

    Capacity failures (429 usage limit, 529 overloaded, rate limiting) keep
    failing on the same agent until the provider window resets. The escalation
    ladder treats this as a signal to switch agents.

    Args:
        exc: The exception raised by an agent invocation.

    Returns:
        ``True`` when the exception text matches a provider-capacity signature.
    """
    return _PROVIDER_CAPACITY_HINT_PATTERN.search(_failure_text(exc)) is not None


def is_transient_failure(exc: BaseException) -> bool:
    """Return whether ``exc`` looks like a transient network/transport error.

    Transient failures (dropped sockets, connection resets, gateway timeouts,
    5xx, and occasional provider-side 400 / invalid-parameter responses) often
    succeed on a simple retry with the same agent. Provider-capacity failures
    are intentionally excluded so capacity errors escalate instead of being
    retried in place.

    Args:
        exc: The exception raised by an agent invocation.

    Returns:
        ``True`` when the exception text matches a transient signature and is
        not a provider-capacity failure.
    """
    if is_provider_capacity_failure(exc):
        return False
    return _TRANSIENT_HINT_PATTERN.search(_failure_text(exc)) is not None

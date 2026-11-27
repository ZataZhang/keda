"""Runner 副作用账本的稳定身份与发送/对账策略。"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable

from backend.core.shared.interfaces.agent_runner import (
    GitHubApiRateLimitError,
    GitHubApiUnavailableError,
    GitHubOperationInProgressError,
    GitHubOperationOutcomeUnknownError,
)
from backend.core.shared.interfaces.runner_recovery import IRunnerRecoveryStore
from backend.core.shared.interfaces.runner_claim_guard import assert_active_runner_claim_owned
from backend.core.shared.models.runner_recovery import OperationIntent, OperationState
from backend.core.shared.models.runner_recovery_policy import classify_recovery_failure

_MAX_WRITE_ATTEMPTS = 5
_WRITE_RETRY_BUDGET_SECONDS = 60


def operation_marker(operation_key: str) -> str:
    """返回可随远端对象核对的隐藏操作标记。"""
    return f"<!-- kc:runner-operation id={operation_key} -->"


def execute_journaled_write(
    *,
    recovery_store: IRunnerRecoveryStore | None,
    intent: OperationIntent,
    send: Callable[[], str | None],
    reconcile: Callable[[], str | None],
    safe_to_retry_after_confirmed_absence: bool = False,
    can_retry_after_absence: Callable[[], bool] | None = None,
    reconcile_after_absence: Callable[[], str | None] | None = None,
) -> str | None:
    """严格记账、争抢本机发送权，并在未知结果时只做只读对账。

    Args:
        recovery_store: 严格恢复账本；未装配时保留低层适配器的旧行为。
        intent: 写操作稳定身份。
        send: 执行一次外部写请求的函数。
        reconcile: 查询远端事实并在确认已执行时返回引用。
        safe_to_retry_after_confirmed_absence: 目标可收敛且新鲜读取证明未执行时是否允许补发。
        can_retry_after_absence: 可选的额外补发安全检查；失败时保留未知。
        reconcile_after_absence: 可选的二次只读核验，发现迟到对象时直接确认。

    Returns:
        写请求的远端引用，或操作本身没有可返回引用时为 ``None``。

    Raises:
        GitHubApiRateLimitError: GitHub 限流，账本保留可稍后再试的状态。
        GitHubOperationInProgressError: 同一操作仍由本机另一进程发送。
        GitHubOperationOutcomeUnknownError: 远端事实仍不能证明写入结果。
        RuntimeError: 账本出现拒绝或身份冲突。
    """
    if recovery_store is None:
        assert_active_runner_claim_owned()
        return send()

    operation_record = recovery_store.begin_operation(intent)
    retry_deadline = time.monotonic() + _WRITE_RETRY_BUDGET_SECONDS
    for attempt_number in range(1, _MAX_WRITE_ATTEMPTS + 1):
        operation_record = recovery_store.get_operation(intent.operation_key) or operation_record
        if operation_record.state is OperationState.CONFIRMED:
            return operation_record.remote_reference
        if operation_record.state is OperationState.REJECTED:
            if (
                classify_recovery_failure(
                    diagnostic_text=operation_record.last_error or "",
                    is_write=True,
                ).category.value
                == "auth"
            ):
                reset_rejected = recovery_store.transition_operation(
                    intent.operation_key,
                    [OperationState.REJECTED],
                    OperationState.PENDING,
                    last_error="Previously rejected for authorization; retrying after access recovery.",
                )
                if reset_rejected:
                    continue
            raise RuntimeError(
                f"GitHub operation {intent.operation_key} was previously rejected; "
                "a changed payload requires a new operation identity."
            )
        if operation_record.state is OperationState.IN_FLIGHT:
            raise GitHubOperationInProgressError(
                f"GitHub operation {intent.operation_key} is still in flight on this machine."
            )
        if operation_record.state is OperationState.UNKNOWN:
            try:
                remote_reference = reconcile()
            except Exception as reconcile_error:  # noqa: BLE001 - 只读对账失败仍是未知。
                remote_reference = None
                reconcile_diagnostic = _sanitize_diagnostic(str(reconcile_error))
            else:
                reconcile_diagnostic = "远端尚未暴露可确认的对象。"
            if remote_reference:
                recovery_store.transition_operation(
                    intent.operation_key,
                    [OperationState.UNKNOWN],
                    OperationState.CONFIRMED,
                    remote_reference=remote_reference,
                )
                return remote_reference
            should_retry, confirmed_reference = _confirm_absence(
                recovery_store=recovery_store,
                reconcile_after_absence=reconcile_after_absence,
                can_retry_after_absence=(
                    can_retry_after_absence
                    if safe_to_retry_after_confirmed_absence
                    else lambda: False
                ),
            )
            if confirmed_reference:
                recovery_store.transition_operation(
                    intent.operation_key,
                    [OperationState.UNKNOWN],
                    OperationState.CONFIRMED,
                    remote_reference=confirmed_reference,
                )
                return confirmed_reference
            if safe_to_retry_after_confirmed_absence and should_retry:
                recovery_store.transition_operation(
                    intent.operation_key,
                    [OperationState.UNKNOWN],
                    OperationState.PENDING,
                    last_error=reconcile_diagnostic,
                )
                continue
            raise GitHubOperationOutcomeUnknownError(
                "GitHub write outcome remains unknown after read-only reconciliation; "
                f"operation {intent.operation_key} was not resent. {reconcile_diagnostic}"
            )
        if operation_record.state is OperationState.PENDING:
            assert_active_runner_claim_owned()
            claimed = recovery_store.transition_operation(
                intent.operation_key,
                [OperationState.PENDING],
                OperationState.IN_FLIGHT,
            )
            if not claimed:
                continue
            try:
                assert_active_runner_claim_owned()
            except Exception:
                recovery_store.transition_operation(
                    intent.operation_key,
                    [OperationState.IN_FLIGHT],
                    OperationState.PENDING,
                    last_error="Claim ownership changed before the external request was sent.",
                )
                raise
            try:
                remote_reference = send()
            except Exception as write_error:  # noqa: BLE001 - 保留故障类别并严格记账。
                raw_diagnostic = str(write_error)
                safe_error = _sanitize_diagnostic(raw_diagnostic)
                failure_decision = classify_recovery_failure(
                    diagnostic_text=raw_diagnostic,
                    is_write=True,
                )
                if isinstance(write_error, GitHubApiRateLimitError):
                    recovery_store.transition_operation(
                        intent.operation_key,
                        [OperationState.IN_FLIGHT],
                        OperationState.PENDING,
                        last_error=safe_error,
                    )
                    raise
                if failure_decision.result_certainty == "not_sent":
                    recovery_store.transition_operation(
                        intent.operation_key,
                        [OperationState.IN_FLIGHT],
                        OperationState.PENDING,
                        last_error=safe_error,
                    )
                    if attempt_number < _MAX_WRITE_ATTEMPTS and time.monotonic() < retry_deadline:
                        time.sleep(_write_retry_delay(attempt_number, retry_deadline))
                        continue
                    raise GitHubApiUnavailableError(
                        "GitHub write was not sent after "
                        f"{attempt_number} bounded attempts: {safe_error}"
                    ) from write_error
                if failure_decision.result_certainty == "rejected":
                    recovery_store.transition_operation(
                        intent.operation_key,
                        [OperationState.IN_FLIGHT],
                        OperationState.REJECTED,
                        last_error=safe_error,
                    )
                    raise
                recovery_store.transition_operation(
                    intent.operation_key,
                    [OperationState.IN_FLIGHT],
                    OperationState.UNKNOWN,
                    last_error=safe_error,
                )
                try:
                    remote_reference = reconcile()
                except Exception as reconcile_error:  # noqa: BLE001 - 未知结果不能盲目补发。
                    raise GitHubOperationOutcomeUnknownError(
                        "GitHub write outcome is unknown and read-only reconciliation failed; "
                        f"operation {intent.operation_key} was not resent. "
                        f"{_sanitize_diagnostic(str(reconcile_error))}"
                    ) from write_error
                if remote_reference:
                    recovery_store.transition_operation(
                        intent.operation_key,
                        [OperationState.UNKNOWN],
                        OperationState.CONFIRMED,
                        remote_reference=remote_reference,
                    )
                    return remote_reference
                should_retry, confirmed_reference = _confirm_absence(
                    recovery_store=recovery_store,
                    reconcile_after_absence=reconcile_after_absence,
                    can_retry_after_absence=(
                        can_retry_after_absence
                        if safe_to_retry_after_confirmed_absence
                        else lambda: False
                    ),
                )
                if confirmed_reference:
                    recovery_store.transition_operation(
                        intent.operation_key,
                        [OperationState.UNKNOWN],
                        OperationState.CONFIRMED,
                        remote_reference=confirmed_reference,
                    )
                    return confirmed_reference
                if safe_to_retry_after_confirmed_absence and should_retry:
                    recovery_store.transition_operation(
                        intent.operation_key,
                        [OperationState.UNKNOWN],
                        OperationState.PENDING,
                        last_error="Fresh read proved the desired state is absent.",
                    )
                    continue
                raise GitHubOperationOutcomeUnknownError(
                    "GitHub write outcome remains unknown after read-only reconciliation; "
                    f"operation {intent.operation_key} was not resent."
                ) from write_error
            confirmed = recovery_store.transition_operation(
                intent.operation_key,
                [OperationState.IN_FLIGHT],
                OperationState.CONFIRMED,
                remote_reference=remote_reference,
            )
            if confirmed:
                return remote_reference
            current_operation = recovery_store.get_operation(intent.operation_key)
            if (
                current_operation is not None
                and current_operation.state is OperationState.CONFIRMED
            ):
                return current_operation.remote_reference
            raise GitHubOperationOutcomeUnknownError(
                "GitHub write completed but its local confirmation conflicted; "
                f"operation {intent.operation_key} requires reconciliation."
            )

    raise GitHubOperationInProgressError(
        f"Could not claim GitHub operation {intent.operation_key} after concurrent updates."
    )


def _confirm_absence(
    *,
    recovery_store: IRunnerRecoveryStore,
    reconcile_after_absence: Callable[[], str | None] | None,
    can_retry_after_absence: Callable[[], bool] | None,
) -> tuple[bool, str | None]:
    """重复核对远端状态，并应用写操作专用的补发安全检查。"""
    try:
        if reconcile_after_absence is not None:
            remote_reference = reconcile_after_absence()
            if remote_reference:
                return False, remote_reference
        retry_is_safe = can_retry_after_absence() if can_retry_after_absence is not None else True
        return retry_is_safe, None
    except Exception:  # noqa: BLE001 - 安全不确定时必须 fail closed。
        return False, None


def _write_retry_delay(attempt_number: int, retry_deadline: float) -> float:
    """计算有界退避并为并发 runner 加入抖动。"""
    remaining_seconds = max(0.0, retry_deadline - time.monotonic())
    base_delay_seconds = min(0.5 * (2 ** (attempt_number - 1)), 8.0)
    return min(base_delay_seconds * random.uniform(0.75, 1.25), remaining_seconds)


def _sanitize_diagnostic(diagnostic_text: str) -> str:
    """脱敏并限制账本中的诊断文本长度。"""
    import re

    sanitized_text = re.sub(
        r"(?i)(authorization:\s*bearer\s+|(?:gh[pousr]_|github_pat_))[A-Za-z0-9_./-]+",
        "[REDACTED]",
        diagnostic_text,
    )
    return sanitized_text[:1000]


def build_operation_intent(
    *,
    repository_id: str,
    issue_number: int,
    operation_kind: str,
    target: str,
    payload_text: str,
    payload_revision: int = 1,
) -> OperationIntent:
    """根据明确的业务语义构造稳定副作用身份。

    Args:
        repository_id: 稳定仓库身份。
        issue_number: 关联 Issue 或 PR 编号；创建 Issue 时使用 ``0``。
        operation_kind: 调用方显式声明的写操作类别。
        target: 远端资源及预期状态。
        payload_text: 规范化后的载荷；仅计算摘要，不写入账本。
        payload_revision: 载荷修订号。

    Returns:
        同一工作内容跨重启保持一致的操作意图。
    """
    payload_digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    identity_json = json.dumps(
        [repository_id, issue_number, operation_kind, target, payload_digest, payload_revision],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    operation_key = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
    run_key = f"repo:{repository_id}:issue:{issue_number}"
    return OperationIntent(
        operation_key=operation_key,
        run_key=run_key,
        repository_id=repository_id,
        issue_number=issue_number,
        operation_kind=operation_kind,
        target=target,
        payload_digest=payload_digest,
        payload_revision=payload_revision,
    )

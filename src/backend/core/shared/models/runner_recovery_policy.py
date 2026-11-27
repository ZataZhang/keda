"""Agent Runner 故障归类与有界恢复策略。"""

from __future__ import annotations

import re

from backend.core.shared.models.runner_recovery import (
    RecoveryAction,
    RecoveryDecision,
    RecoveryFailureCategory,
)

_RATE_LIMIT_PATTERN = re.compile(
    r"(?:api\s+rate\s+limit|secondary\s+rate\s+limit|rate\s+limit\s+exceeded|"
    r"abuse detection|\b429\b)",
    re.IGNORECASE,
)
_NOT_SENT_NETWORK_PATTERN = re.compile(
    r"(?:no such host|temporary failure in name resolution|name or service not known|"
    r"tls handshake timeout|connection refused|failed to connect|dial tcp[^\n]*refused)",
    re.IGNORECASE,
)
_UNKNOWN_NETWORK_PATTERN = re.compile(
    r"(?:unexpected\s+eof|\beof\b|connection reset|broken pipe|operation timed out|"
    r"read tcp[^\n]*timed out|context deadline exceeded|outcome remains unknown|"
    r"read-only reconciliation failed)",
    re.IGNORECASE,
)
_AUTH_PATTERN = re.compile(
    r"(?:http\s+401|http\s+403|bad credentials|resource not accessible|"
    r"permission denied|forbidden|must have .*permission)",
    re.IGNORECASE,
)
_HTTP_STATUS_PATTERN = re.compile(
    r"(?:http\s+|non-200 OK status code:\s*)(\d{3})\b",
    re.IGNORECASE,
)
_FATAL_STATE_PATTERN = re.compile(
    r"(?:database is locked|database disk image is malformed|recovery record conflict|"
    r"no such table|readonly database|disk i/o error|database or disk is full|"
    r"unable to open database file)",
    re.IGNORECASE,
)


def classify_recovery_failure(
    *,
    diagnostic_text: str,
    status_code: int | None = None,
    is_write: bool = False,
) -> RecoveryDecision:
    """根据结构化状态与窄诊断特征选择安全恢复动作。

    Args:
        diagnostic_text: 已脱敏的异常或命令诊断。
        status_code: 外部服务返回的 HTTP 状态码；未知时为 ``None``。
        is_write: 本次命令是否会产生外部副作用。

    Returns:
        故障类别、结果确定性与下一步动作。
    """
    status_match = _HTTP_STATUS_PATTERN.search(diagnostic_text)
    effective_status_code = status_code or (
        int(status_match.group(1)) if status_match is not None else None
    )
    if _FATAL_STATE_PATTERN.search(diagnostic_text):
        return RecoveryDecision(
            RecoveryFailureCategory.FATAL_STATE,
            RecoveryAction.STOP,
            "unknown",
            "恢复状态不可安全读取或写入。",
        )

    if _RATE_LIMIT_PATTERN.search(diagnostic_text) or effective_status_code == 429:
        return RecoveryDecision(
            RecoveryFailureCategory.RATE_LIMITED,
            RecoveryAction.WAIT,
            "rejected",
            "服务端要求等待限流恢复时间。",
        )

    if effective_status_code in {401, 403} or _AUTH_PATTERN.search(diagnostic_text):
        return RecoveryDecision(
            RecoveryFailureCategory.AUTH,
            RecoveryAction.HANDOFF,
            "rejected",
            "需要人工检查凭据或仓库权限。",
        )

    if _NOT_SENT_NETWORK_PATTERN.search(diagnostic_text):
        return RecoveryDecision(
            RecoveryFailureCategory.TRANSIENT_NETWORK,
            RecoveryAction.RETRY,
            "not_sent",
            "连接在请求发送前失败，可以安全重试。",
        )

    if effective_status_code == 499:
        if is_write:
            return RecoveryDecision(
                RecoveryFailureCategory.OUTCOME_UNKNOWN,
                RecoveryAction.RECONCILE,
                "unknown",
                "客户端或边缘节点提前断开；写请求是否已提交尚不确定。",
            )
        return RecoveryDecision(
            RecoveryFailureCategory.TRANSIENT_NETWORK,
            RecoveryAction.RETRY,
            "rejected",
            "读取请求遇到客户端或边缘节点提前断开。",
        )

    if effective_status_code is not None and 500 <= effective_status_code <= 599:
        if is_write:
            return RecoveryDecision(
                RecoveryFailureCategory.OUTCOME_UNKNOWN,
                RecoveryAction.RECONCILE,
                "unknown",
                "写请求收到服务端错误，远端是否提交尚不确定。",
            )
        return RecoveryDecision(
            RecoveryFailureCategory.TRANSIENT_NETWORK,
            RecoveryAction.RETRY,
            "rejected",
            "读取请求遇到暂时的服务端故障。",
        )

    if _UNKNOWN_NETWORK_PATTERN.search(diagnostic_text):
        if is_write:
            return RecoveryDecision(
                RecoveryFailureCategory.OUTCOME_UNKNOWN,
                RecoveryAction.RECONCILE,
                "unknown",
                "连接在请求结果返回前中断，必须先对账远端事实。",
            )
        return RecoveryDecision(
            RecoveryFailureCategory.TRANSIENT_NETWORK,
            RecoveryAction.RETRY,
            "not_sent",
            "只读请求连接中断，可以安全重试。",
        )

    if effective_status_code is not None and 400 <= effective_status_code <= 499:
        return RecoveryDecision(
            RecoveryFailureCategory.SEMANTIC_REJECTED,
            RecoveryAction.STOP,
            "rejected",
            "服务端明确拒绝了请求，需要修正请求内容或前提。",
        )

    return RecoveryDecision(
        RecoveryFailureCategory.SEMANTIC_REJECTED,
        RecoveryAction.STOP,
        "unknown" if is_write else "rejected",
        "故障不匹配自动重试规则。",
    )

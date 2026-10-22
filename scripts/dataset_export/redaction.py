"""对导出文本做基于模式的脱敏。

会话记录里的用户首句常常夹带密钥、令牌、邮箱和本机路径。这里只做**模式匹配**：
能抓住常见的密钥形态，但不保证抓全——所以导出时会带上脱敏计数并标注未经人工复核，
发布数据集之前仍必须人工抽查。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    """脱敏后的文本与命中次数。"""

    text: str
    redaction_count: int


_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)

# (模式, 替换文本)。顺序有意义：先整块私钥，再具名令牌，最后通用的"键=值"赋值。
_TOKEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "<redacted-api-key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<redacted-github-token>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "<redacted-github-token>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted-aws-key>"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "<redacted-slack-token>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"), "Bearer <redacted-token>"),
    (
        re.compile(r"\b(?:eyJ[A-Za-z0-9_-]{8,})\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "<redacted-jwt>",
    ),
)

# ``api_key = xxx`` / ``"password": "xxx"`` 这类赋值：保留键名，抹掉值。
_ASSIGNMENT_PATTERN = re.compile(
    r"""(?P<key>["']?[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passwd|password)["']?\s*[:=]\s*)"""
    r"""(?!<redacted)(?P<value>["'][^"'\s]{6,}["']|[^\s"',;]{6,})""",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_HOME_PATH_PATTERN = re.compile(r"(?:/Users|/home)/[^/\s\"']+")


def _redact_assignment(match: re.Match[str]) -> str:
    """保留赋值左侧的键名，把值替换成占位符。"""
    return f"{match.group('key')}<redacted>"


def redact_text(text: str) -> RedactionResult:
    """脱敏文本中的私钥、常见令牌、密钥赋值、邮箱和家目录路径。"""
    redaction_count = 0
    redacted = text

    redacted, count = _PRIVATE_KEY_PATTERN.subn("<redacted-private-key>", redacted)
    redaction_count += count
    for pattern, replacement in _TOKEN_PATTERNS:
        redacted, count = pattern.subn(replacement, redacted)
        redaction_count += count
    redacted, count = _ASSIGNMENT_PATTERN.subn(_redact_assignment, redacted)
    redaction_count += count
    redacted, count = _EMAIL_PATTERN.subn("<redacted-email>", redacted)
    redaction_count += count
    redacted, count = _HOME_PATH_PATTERN.subn("~", redacted)
    redaction_count += count
    return RedactionResult(text=redacted, redaction_count=redaction_count)

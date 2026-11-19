"""托管 Console 的秘密摘要、口令哈希与 provider 目录（纯 stdlib 实现）。

这些函数被 core 用例与 infrastructure 存储共用，因此放在 ``core/shared/security``
而不是某个具体层的实现里：摘要算法、口令参数和 provider 映射是跨层契约，
不是可替换的基础设施细节。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

from backend.core.shared.models.hosted_console import (
    HOSTED_BOOTSTRAP_TOKEN_BYTES,
    HostedCredentialRejectionCode,
)

#: 管理员口令哈希参数：PBKDF2-HMAC-SHA256，随机盐，常数时间比较。
_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_SALT_BYTES = 16
_PBKDF2_HASH_BYTES = 32

#: 运行凭据的必需仓库权限（D-03 首期最小集）。
REQUIRED_GITHUB_REPO_PERMISSIONS: tuple[str, ...] = (
    "Metadata: read",
    "Contents: read/write",
    "Issues: read/write",
    "Pull requests: read/write",
)

#: 托管 Console 会话 cookie 名（API 路由与前端守卫共用同一标识）。
SESSION_COOKIE_NAME = "kc_console_session"

#: 首期托管部署的最低管理员口令长度。
MIN_ADMIN_PASSWORD_LENGTH = 12


def digest_secret(value: str) -> str:
    """Return the hex SHA-256 digest used to store a secret reference.

    引导码、会话令牌都只以摘要落盘：泄漏存储文件不等于泄漏可用凭据。

    Args:
        value: 需要摘要的原始字符串。

    Returns:
        十六进制摘要。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_bootstrap_code() -> str:
    """Return a fresh 256-bit CSPRNG bootstrap code as hex text.

    原码只在签发命令的输出里出现一次，服务端仅保存 ``digest_secret`` 结果。
    """
    return secrets.token_hex(HOSTED_BOOTSTRAP_TOKEN_BYTES)


def hash_admin_password(password: str) -> str:
    """Return a salted PBKDF2 record for the Console admin password.

    Args:
        password: 客户首次引导时设置的管理员口令明文（不落盘）。

    Returns:
        ``pbkdf2_sha256$iterations$salt_hex$hash_hex`` 形式的记录。

    Raises:
        ValueError: 口令长度低于托管部署的最低要求。
    """
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Hosted Console admin password must be at least {MIN_ADMIN_PASSWORD_LENGTH} characters"
        )
    salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGORITHM, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, _PBKDF2_HASH_BYTES
    )
    return f"pbkdf2_{_PBKDF2_ALGORITHM}${_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_admin_password(password: str, stored_record: str) -> bool:
    """Return whether a password matches the stored PBKDF2 record."""
    try:
        scheme, iterations, salt_hex, hash_hex = stored_record.split("$")
        expected = bytes.fromhex(hash_hex)
        salt = bytes.fromhex(salt_hex)
        algorithm = scheme.removeprefix("pbkdf2_")
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac(
        algorithm, password.encode("utf-8"), salt, int(iterations), len(expected)
    )
    return hmac.compare_digest(derived, expected)


def generate_session_token() -> str:
    """Return a fresh opaque session token (only its digest is persisted)."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class HostedAgentProviderSpec:
    """受支持 Agent provider 的目录项（格式校验 + 上游官方入口）。"""

    name: str
    cli_name: str
    api_key_prefixes: tuple[str, ...]
    display_name: str
    billing_prerequisite: str
    create_key_url: str
    revoke_key_url: str
    auth_docs_url: str
    probe_url: str

    def matches_key_format(self, api_key: str) -> bool:
        """Return whether the pasted key looks like this provider's API key."""
        if not api_key or api_key.strip() != api_key:
            return False
        return any(api_key.startswith(prefix) for prefix in self.api_key_prefixes)


#: 首期固定映射：仅 runner 镜像预装的三个 CLI；不接受 OAuth/session/自定义端点。
HOSTED_AGENT_PROVIDERS: dict[str, HostedAgentProviderSpec] = {
    spec.name: spec
    for spec in (
        HostedAgentProviderSpec(
            name="claude",
            cli_name="claude",
            api_key_prefixes=("sk-ant-api",),
            display_name="Claude Code（Anthropic Console）",
            billing_prerequisite="需要 Anthropic Console 已开通 API 计费",
            create_key_url="https://console.anthropic.com/settings/keys",
            revoke_key_url="https://console.anthropic.com/settings/keys",
            auth_docs_url="https://code.claude.com/docs/en/authentication",
            probe_url="https://api.anthropic.com/v1/messages",
        ),
        HostedAgentProviderSpec(
            name="codex",
            cli_name="codex",
            api_key_prefixes=("sk-", "sk-proj-"),
            display_name="Codex CLI（OpenAI API Platform）",
            billing_prerequisite="需要 OpenAI 平台 API 账户有可用额度",
            create_key_url="https://platform.openai.com/api-keys",
            revoke_key_url="https://platform.openai.com/api-keys",
            auth_docs_url="https://learn.chatgpt.com/docs/auth",
            probe_url="https://api.openai.com/v1/chat/completions",
        ),
        HostedAgentProviderSpec(
            name="kimi",
            cli_name="kimi",
            api_key_prefixes=("sk-kimi-", "sk-"),
            display_name="Kimi Code CLI（Kimi Platform）",
            billing_prerequisite="需要 Kimi 平台 API 账户有可用额度",
            create_key_url="https://platform.kimi.com/console/api-keys",
            revoke_key_url="https://platform.kimi.com/console/api-keys",
            auth_docs_url="https://moonshotai.github.io/kimi-code/en/guides/getting-started.html",
            probe_url="https://api.moonshot.cn/v1/chat/completions",
        ),
    )
}

#: 首期 GitHub 凭据只接受 fine-grained PAT；经典 token 与 OAuth token 明确拒绝。
_FINE_GRAINED_PAT_RE = re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$")
_LEGACY_CLASSIC_PAT_RE = re.compile(r"^(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}$")
_GITHUB_PAT_CREATE_URL = "https://github.com/settings/personal-access-tokens/new"
_GITHUB_PAT_DOCS_URL = (
    "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/"
    "managing-your-personal-access-tokens"
)
_GITHUB_PAT_REVOKE_URL = "https://github.com/settings/personal-access-tokens"

_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def classify_github_token(token: str) -> HostedCredentialRejectionCode | None:
    """Return the format-level rejection for a pasted GitHub token, or ``None``.

    Args:
        token: 客户粘贴的 GitHub 凭据。

    Returns:
        格式不接受时给出原因码；格式可接受时返回 ``None``。
    """
    if not token or token.strip() != token:
        return HostedCredentialRejectionCode.GITHUB_TOKEN_INVALID
    if _LEGACY_CLASSIC_PAT_RE.match(token):
        return HostedCredentialRejectionCode.GITHUB_TOKEN_INVALID
    if token.startswith("gh"):
        return HostedCredentialRejectionCode.GITHUB_TOKEN_INVALID
    if not _FINE_GRAINED_PAT_RE.match(token):
        return HostedCredentialRejectionCode.GITHUB_TOKEN_INVALID
    return None


def validate_owner_repo(owner_repo: str) -> bool:
    """Return whether the value looks like a single ``owner/repo`` target."""
    return bool(_OWNER_REPO_RE.match(owner_repo or ""))


def github_pat_guidance() -> dict[str, object]:
    """Return the Console guidance payload for creating a single-repo PAT."""
    return {
        "kind": "github",
        "provider": "github",
        "create_url": _GITHUB_PAT_CREATE_URL,
        "revoke_url": _GITHUB_PAT_REVOKE_URL,
        "docs_url": _GITHUB_PAT_DOCS_URL,
        "token_kind": "fine_grained_pat",
        "repository_scope": "只勾选目标仓库（Repository access → Only select repositories）",
        "required_permissions": list(REQUIRED_GITHUB_REPO_PERMISSIONS),
        "billing_prerequisite": "不需要模型费用；由客户在 GitHub 账户内自行创建与撤销",
        "accepts_oauth_or_session": False,
        "accepts_auth_file_upload": False,
    }


def agent_provider_guidance() -> list[dict[str, object]]:
    """Return per-provider Console guidance (source, billing, revoke, cost warning)."""
    return [
        {
            "kind": "agent",
            "provider": spec.name,
            "cli_name": spec.cli_name,
            "display_name": spec.display_name,
            "create_url": spec.create_key_url,
            "revoke_url": spec.revoke_key_url,
            "docs_url": spec.auth_docs_url,
            "billing_prerequisite": spec.billing_prerequisite,
            "test_cost_warning": "测试连接会向供应商发起一次最小真实模型请求，可能产生 API 费用",
            "accepts_oauth_or_session": False,
            "accepts_custom_base_url": False,
        }
        for spec in HOSTED_AGENT_PROVIDERS.values()
    ]


def hosted_https_origin_ok(base_url: str) -> bool:
    """Return whether a configured public Console origin is HTTPS-capable.

    托管模式要求真实 HTTPS 入口；明文 http 只允许 localhost 场景（反向代理本机自检）。
    """
    parsed = urlparse(base_url)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}

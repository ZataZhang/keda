"""托管 Console 的身份、会话与凭据状态契约。

本模块只描述**不含秘密值**的记录：秘密（GitHub PAT、供应商 API key）永远留在
infrastructure 的加密存储里，不进入这些跨层记录、API 响应或日志。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

#: 托管 Console 的唯一管理员标识；首期不开放注册，也不建多用户目录。
HOSTED_CONSOLE_ADMIN_USER_ID = "console-admin"

#: 引导码固定 24 小时有效（D-03 推荐值）。
HOSTED_BOOTSTRAP_DEFAULT_TTL_SECONDS = 24 * 60 * 60

#: 引导码熵：256-bit CSPRNG 随机字节数。
HOSTED_BOOTSTRAP_TOKEN_BYTES = 32


class HostedCredentialKind(StrEnum):
    """托管 Console 可保存的运行凭据类别（闭集）。"""

    GITHUB = "github"
    AGENT = "agent"


class HostedCredentialState(StrEnum):
    """凭据在 KedaCode 侧的可用性状态。"""

    #: 已保存但未通过验证：对应 runner 不得领取任务。
    UNVERIFIED = "unverified"
    #: 已通过验证，runner 可用。
    ACTIVE = "active"
    #: 最近一次验证失败（权限不足、key 失效、组织待审批等）。
    REJECTED = "rejected"


class HostedCredentialRejectionCode(StrEnum):
    """验证失败原因码，用于 UI 给出可操作提示（不含秘密值）。"""

    PROVIDER_UNSUPPORTED = "provider_unsupported"
    KEY_FORMAT_INVALID = "key_format_invalid"
    GITHUB_TOKEN_INVALID = "github_token_invalid"
    GITHUB_REPO_INACCESSIBLE = "github_repo_inaccessible"
    GITHUB_PERMISSIONS_INSUFFICIENT = "github_permissions_insufficient"
    GITHUB_ORG_APPROVAL_PENDING = "github_org_approval_pending"
    AGENT_MODEL_CALL_FAILED = "agent_model_call_failed"
    COST_CONFIRMATION_REQUIRED = "cost_confirmation_required"
    RUNNER_UNAVAILABLE = "runner_unavailable"


@dataclass(frozen=True)
class BootstrapTokenRecord:
    """一次性引导码的服务端记录：只保存哈希，绝不保存原码。

    Attributes:
        token_hash: 引导码的 SHA-256 十六进制摘要。
        deployment_id: 部署绑定值；不同 VM 的码互不可用。
        issued_at_utc: 签发时间。
        expires_at_utc: 过期时间（签发 + TTL）。
        consumed_at_utc: 消费时间；``None`` 表示尚未使用。
    """

    token_hash: str
    deployment_id: str
    issued_at_utc: datetime
    expires_at_utc: datetime
    consumed_at_utc: datetime | None = None

    def is_usable_at(self, now_utc: datetime) -> bool:
        """Return whether the record still accepts a first-time use."""
        return self.consumed_at_utc is None and now_utc < self.expires_at_utc


@dataclass(frozen=True)
class HostedAdminRecord:
    """Console 管理员登录凭据（只认证 Console，不交给 runner）。"""

    password_hash: str
    set_at_utc: datetime


@dataclass(frozen=True)
class HostedSessionRecord:
    """服务端会话记录：只保存会话令牌的摘要。"""

    session_hash: str
    user_id: str
    created_at_utc: datetime
    expires_at_utc: datetime

    def is_valid_at(self, now_utc: datetime) -> bool:
        """Return whether the session is still alive."""
        return now_utc < self.expires_at_utc


@dataclass(frozen=True)
class HostedSessionPrincipal:
    """已通过认证的使用者视图（前端会话守卫消费）。"""

    user_id: str
    display_name: str
    email: str


@dataclass(frozen=True)
class HostedCredentialStatus:
    """单个仓库单项凭据的可展示状态；不含任何秘密值。

    Attributes:
        repo_id: 目标仓库标识。
        owner_repo: GitHub ``owner/repo``。
        kind: 凭据类别。
        provider: Agent provider 名（GitHub 凭据为 ``github``）。
        state: 当前状态。
        created_at_utc: 首次保存时间。
        verified_at_utc: 最近一次验证通过时间。
        expires_at_hint: 上游凭据到期提醒（由客户提供，无则为空）。
        last_rejection: 最近一次失败原因码。
        secret_length: 秘密字符长度，仅用于确认"已录入"，不回显内容。
    """

    repo_id: str
    owner_repo: str
    kind: HostedCredentialKind
    provider: str
    state: HostedCredentialState
    github_login: str = ""
    created_at_utc: datetime | None = None
    verified_at_utc: datetime | None = None
    expires_at_hint: str = ""
    last_rejection: HostedCredentialRejectionCode | None = None
    secret_length: int = 0

    @property
    def runner_may_run(self) -> bool:
        """Return whether this credential authorises the runner to take work."""
        return self.state is HostedCredentialState.ACTIVE


@dataclass(frozen=True)
class GitHubTokenCheck:
    """GitHub fine-grained PAT 只读校验结果。

    Attributes:
        accepted: 是否允许启用该仓库 runner。
        login: token 身份（GitHub 用户名）；失败时为空。
        repository_visible: 目标仓库对该 token 是否可见。
        granted_permissions: 实际探测到的仓库权限名。
        rejection: 失败原因码。
    """

    accepted: bool
    login: str = ""
    repository_visible: bool = False
    granted_permissions: tuple[str, ...] = ()
    rejection: HostedCredentialRejectionCode | None = None


@dataclass(frozen=True)
class RunnerGitHubCredential:
    """runner 的 GitHub 原生认证所需凭据与已验证账户名。"""

    token: str
    login: str


@dataclass(frozen=True)
class AgentKeyProbeResult:
    """最小真实模型调用结果（只在客户显式确认费用后发起）。"""

    accepted: bool
    rejection: HostedCredentialRejectionCode | None = None
    detail: str = ""


@dataclass(frozen=True)
class HostedCredentialWriteResult:
    """写入/轮换凭据的结果视图。

    Attributes:
        status: 写入后的状态记录。
        kept_previous: 校验失败时是否保留了旧值。
        runner_stopped: 本次写入是否需要/已经停止对应 runner 以换入新凭据。
    """

    status: HostedCredentialStatus
    kept_previous: bool = False
    runner_stopped: bool = False


@dataclass(frozen=True)
class HostedCredentialDeleteResult:
    """删除 KedaCode 本地副本的结果。

    Attributes:
        removed: 是否已删除本地密文。
        runner_stopped: 是否已停止对应 runner。
        tmpfs_erased: 是否已清除该仓库的运行时明文目录。
        upstream_revoked: 恒为 ``False`` —— KedaCode 不代替客户撤销上游 key。
        notes: 给 UI 展示的边界说明。
    """

    removed: bool
    runner_stopped: bool
    tmpfs_erased: bool
    upstream_revoked: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

"""托管 Console 的跨层抽象契约。

core 只依赖这些接口；身份存储与加密凭据存储由 infrastructure 实现，runner 原生
认证文件的 tmpfs 交付与 runner 生命周期由 engines 实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from backend.core.shared.models.hosted_console import (
    AgentKeyProbeResult,
    BootstrapTokenRecord,
    GitHubTokenCheck,
    HostedAdminRecord,
    HostedCredentialKind,
    HostedCredentialStatus,
    RunnerGitHubCredential,
    HostedSessionRecord,
)


class HostedConsoleIdentityStore(ABC):
    """托管 Console 身份与会话的持久化契约（只存摘要，不存明文码/密码）。"""

    @abstractmethod
    def get_bootstrap(self) -> BootstrapTokenRecord | None:
        """Return the currently issued bootstrap record, if any."""

    @abstractmethod
    def put_bootstrap(self, record: BootstrapTokenRecord) -> None:
        """Persist a freshly issued bootstrap record, replacing any previous one."""

    @abstractmethod
    def consume_bootstrap(self, token_hash: str, now_utc: datetime) -> bool:
        """Atomically mark an unexpired bootstrap record as consumed.

        Args:
            token_hash: 提交上来的引导码摘要。
            now_utc: 判定过期用的当前 UTC 时间。

        Returns:
            仅当记录存在、未消费且未过期并成功写入消费标记时为 ``True``。
        """

    @abstractmethod
    def get_admin(self) -> HostedAdminRecord | None:
        """Return the admin password record, or ``None`` before first bootstrap."""

    @abstractmethod
    def put_admin(self, record: HostedAdminRecord) -> None:
        """Persist the admin password record."""

    @abstractmethod
    def consume_login_attempt(
        self,
        client_hash: str,
        attempted_at_utc: datetime,
        window_seconds: int,
        max_attempts: int,
    ) -> int | None:
        """原子预留一次登录尝试；达到上限时返回剩余等待秒数。"""

    @abstractmethod
    def clear_login_attempts(self, client_hash: str) -> None:
        """认证成功后清除此来源的登录尝试窗口。"""

    @abstractmethod
    def put_session(self, record: HostedSessionRecord) -> None:
        """Persist a session record."""

    @abstractmethod
    def get_session(self, session_hash: str) -> HostedSessionRecord | None:
        """Return a session record by its digest."""

    @abstractmethod
    def drop_session(self, session_hash: str) -> None:
        """Remove a session record (logout)."""

    @abstractmethod
    def prune_sessions_expired_before(self, now_utc: datetime) -> int:
        """Delete expired sessions and return how many were removed."""


class HostedCredentialRepository(ABC):
    """按仓库隔离的加密凭据存储契约。"""

    @abstractmethod
    def get_status(self, repo_id: str, kind: HostedCredentialKind) -> HostedCredentialStatus | None:
        """Return the stored status view for one repository credential."""

    @abstractmethod
    def list_statuses(self) -> Sequence[HostedCredentialStatus]:
        """Return status views for every stored credential."""

    @abstractmethod
    def put_credential(
        self,
        status: HostedCredentialStatus,
        secret: str,
    ) -> None:
        """Atomically store the encrypted secret together with its status view."""

    @abstractmethod
    def update_status(self, status: HostedCredentialStatus) -> None:
        """Replace only the status metadata of an existing credential."""

    @abstractmethod
    def read_secret(self, repo_id: str, kind: HostedCredentialKind) -> str | None:
        """Decrypt and return one credential secret for runtime handoff."""

    @abstractmethod
    def delete_credential(self, repo_id: str, kind: HostedCredentialKind) -> bool:
        """Remove the encrypted credential; return whether something was deleted."""


class GitHubTokenVerifier(ABC):
    """GitHub fine-grained PAT 的只读校验契约（不得创建 Issue/PR）。"""

    @abstractmethod
    def verify(self, token: str, owner_repo: str) -> GitHubTokenCheck:
        """Check token identity, repository visibility and required permissions."""


class AgentApiKeyProber(ABC):
    """供应商 API key 的最小连通性探测契约（会产生上游费用）。"""

    @abstractmethod
    def probe(self, provider: str, api_key: str) -> AgentKeyProbeResult:
        """Issue exactly one minimal real model request for the given provider."""


class RunnerCredentialHandoff(ABC):
    """runner 运行时凭据交付契约（宿主侧 tmpfs 原生配置文件）。"""

    @abstractmethod
    def materialize(
        self,
        repo_id: str,
        github_credential: RunnerGitHubCredential | None,
        agent_provider: str | None,
        agent_api_key: str | None,
    ) -> list[str]:
        """Write this runner's native auth files and return the written paths.

        只允许写入该仓库专属目录；其它仓库/客户的 runner 不得读到这些文件。
        """

    @abstractmethod
    def erase(self, repo_id: str) -> bool:
        """Remove the runtime credential directory for one repository."""

    @abstractmethod
    def host_directory(self, repo_id: str) -> str:
        """Return the host path mounted for this repository's runner."""


class HostedRunnerControl(ABC):
    """托管宿主对单仓库 runner 的生命周期控制契约。"""

    @abstractmethod
    def stop_runner(self, repo_id: str) -> bool:
        """Stop the repository runner so credential changes take effect."""

    @abstractmethod
    def start_runner(self, repo_id: str, owner_repo: str) -> bool:
        """仅启动固定配置仓库的 runner，并使用其已准备好的原生认证文件。"""

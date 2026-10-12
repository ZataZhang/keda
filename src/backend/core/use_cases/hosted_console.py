"""托管 Console 的编排用例（Issue #265，FR-2/FR-5/FR-12）。

core 只依赖 ``core.shared`` 的接口、模型与 security 纯函数，具体身份存储 /
加密凭据库 / GitHub 只读校验 / Agent 探测 / tmpfs 交付 / runner 控制由
composition root（``engines/agent_runner/hosted_console_composition.py``）装配后
注入。本用例负责编排规则：

- 引导码签发/消费：只存哈希、24 小时过期、单次消费、部署绑定；重放拒绝。
- 管理员口令：仅认证 Console，绝不交给 runner。
- 凭据生命周期：GitHub 保存即只读校验；Agent 保存不调模型，仅显式确认费用
  后最小真实探测；验证失败保留旧值、成功后原子替换并重启对应 runner。
- secret 只进加密库与内存，绝不进入返回视图、日志或 runner 之外的路径。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from backend.core.shared.interfaces.hosted_console import (
    AgentApiKeyProber,
    GitHubTokenVerifier,
    HostedConsoleIdentityStore,
    HostedCredentialRepository,
    HostedRunnerControl,
    RunnerCredentialHandoff,
)
from backend.core.shared.models.hosted_console import (
    HOSTED_BOOTSTRAP_DEFAULT_TTL_SECONDS,
    HOSTED_CONSOLE_ADMIN_USER_ID,
    BootstrapTokenRecord,
    HostedCredentialDeleteResult,
    HostedCredentialKind,
    HostedCredentialRejectionCode,
    HostedCredentialState,
    HostedCredentialStatus,
    HostedCredentialWriteResult,
    RunnerGitHubCredential,
    HostedSessionPrincipal,
    HostedSessionRecord,
)
from backend.core.shared.security import hosted_secrets as secrets_util

_logger = logging.getLogger(__name__)

_LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60
_LOGIN_ATTEMPT_MAXIMUM = 5


class HostedLoginRateLimited(PermissionError):
    """托管 Console 登录来源已达到短时尝试上限。"""

    def __init__(self, retry_after_seconds: int) -> None:
        """保存客户端可重试前需要等待的秒数。"""
        self.retry_after_seconds = retry_after_seconds
        super().__init__("too many hosted login attempts")


def _as_rejection_code(value: object) -> HostedCredentialRejectionCode | None:
    """把校验器返回的字符串原因码归一到枚举；未知值折叠为格式非法。"""
    if value is None:
        return None
    if isinstance(value, HostedCredentialRejectionCode):
        return value
    try:
        return HostedCredentialRejectionCode(str(value))
    except ValueError:
        return HostedCredentialRejectionCode.KEY_FORMAT_INVALID


def _now_utc() -> datetime:
    """返回带时区的当前 UTC 时间。"""
    return datetime.now(UTC)


class HostedConsoleService:
    """托管 Console 身份、会话与凭据生命周期的编排器。

    Args:
        identity_store: 引导码 / 管理员 / 会话的持久化（只存摘要）。
        credential_repo: 按仓库加密的凭据库。
        github_verifier: GitHub fine-grained PAT 只读校验器。
        agent_prober: Agent provider 最小模型探测（产生上游费用）。
        handoff: runner 运行时 tmpfs 凭据交付器。
        runner_control: 对应 runner 的生命周期控制（先停再换凭据）。
        deployment_id: 本部署绑定值，防止跨 VM 复用引导码。
        bootstrap_ttl_seconds: 引导码有效期（默认 24 小时）。
        session_ttl_seconds: Console 会话有效期。
    """

    def __init__(
        self,
        *,
        identity_store: HostedConsoleIdentityStore,
        credential_repo: HostedCredentialRepository,
        github_verifier: GitHubTokenVerifier,
        agent_prober: AgentApiKeyProber,
        handoff: RunnerCredentialHandoff,
        runner_control: HostedRunnerControl,
        deployment_id: str,
        bootstrap_ttl_seconds: int = HOSTED_BOOTSTRAP_DEFAULT_TTL_SECONDS,
        session_ttl_seconds: int = 8 * 60 * 60,
    ) -> None:
        """保存注入的端口与部署参数。"""
        self._identity = identity_store
        self._credentials = credential_repo
        self._github_verifier = github_verifier
        self._agent_prober = agent_prober
        self._handoff = handoff
        self._runner_control = runner_control
        self._deployment_id = deployment_id
        self._bootstrap_ttl_seconds = bootstrap_ttl_seconds
        self._session_ttl_seconds = session_ttl_seconds

    # ── 首次引导与管理员 ──────────────────────────────────────────────────

    def issue_bootstrap_code(self) -> str:
        """签发一次性引导码；原码只在此返回一次，服务端仅存哈希。"""
        raw_code = secrets_util.generate_bootstrap_code()
        issued_at = _now_utc()
        record = BootstrapTokenRecord(
            token_hash=secrets_util.digest_secret(raw_code),
            deployment_id=self._deployment_id,
            issued_at_utc=issued_at,
            expires_at_utc=issued_at + timedelta(seconds=self._bootstrap_ttl_seconds),
        )
        self._identity.put_bootstrap(record)
        return raw_code

    def is_bootstrapped(self) -> bool:
        """Return whether an admin has already been provisioned (首次引导是否完成）。"""
        return self._identity.get_admin() is not None

    def bootstrap_and_set_admin(self, raw_code: str, admin_password: str) -> str:
        """用引导码换取首个管理员会话；无效/过期/重放都拒绝。

        Returns:
            新会话令牌（仅在此与登录返回值中出现，服务端只存摘要）。

        Raises:
            PermissionError: 引导码无效、已消费、已过期或管理员已存在。
        """
        if self._identity.get_admin() is not None:
            raise PermissionError("hosted admin already provisioned; bootstrap disabled")
        token_hash = secrets_util.digest_secret(raw_code)
        now = _now_utc()
        current = self._identity.get_bootstrap()
        if current is None or current.deployment_id != self._deployment_id:
            raise PermissionError("bootstrap code not issued for this deployment")
        if not self._identity.consume_bootstrap(token_hash, now):
            raise PermissionError("bootstrap code invalid, expired, or already consumed")
        self._identity.put_admin(
            _make_admin_record(secrets_util.hash_admin_password(admin_password), now)
        )
        return self._create_session(now)

    def login(self, admin_password: str, client_identity: str = "unknown") -> str:
        """管理员口令登录；按来源限制尝试次数，口令错误抛 PermissionError。"""
        client_hash = secrets_util.digest_secret(client_identity)
        retry_after_seconds = self._identity.consume_login_attempt(
            client_hash,
            _now_utc(),
            _LOGIN_ATTEMPT_WINDOW_SECONDS,
            _LOGIN_ATTEMPT_MAXIMUM,
        )
        if retry_after_seconds is not None:
            raise HostedLoginRateLimited(retry_after_seconds)
        admin = self._identity.get_admin()
        if admin is None:
            raise PermissionError("hosted console not bootstrapped yet")
        if not secrets_util.verify_admin_password(admin_password, admin.password_hash):
            raise PermissionError("invalid hosted credentials")
        self._identity.clear_login_attempts(client_hash)
        return self._create_session(_now_utc())

    def resolve_session(self, session_token: str) -> HostedSessionPrincipal | None:
        """把会话令牌解析为已认证 principal；过期或未知返回 ``None``。"""
        record = self._identity.get_session(secrets_util.digest_secret(session_token))
        if record is None or not record.is_valid_at(_now_utc()):
            return None
        return HostedSessionPrincipal(
            user_id=record.user_id,
            display_name=HOSTED_CONSOLE_ADMIN_USER_ID,
            email=f"{record.user_id}@hosted.local",
        )

    def logout(self, session_token: str) -> None:
        """丢弃会话（登出）。"""
        self._identity.drop_session(secrets_util.digest_secret(session_token))

    def _create_session(self, now: datetime) -> str:
        """生成并持久化一条新会话，返回原始令牌。"""
        token = secrets_util.generate_session_token()
        self._identity.put_session(
            HostedSessionRecord(
                session_hash=secrets_util.digest_secret(token),
                user_id=HOSTED_CONSOLE_ADMIN_USER_ID,
                created_at_utc=now,
                expires_at_utc=now + timedelta(seconds=self._session_ttl_seconds),
            )
        )
        return token

    # ── GitHub 凭据 ──────────────────────────────────────────────────────

    def save_github_credential(self, repo_id: str, owner_repo: str, token: str):
        """保存并只读校验 GitHub fine-grained PAT；失败保留旧值、不启用 runner。"""
        if not secrets_util.validate_owner_repo(owner_repo):
            return self._rejected_write(
                repo_id,
                owner_repo,
                HostedCredentialKind.GITHUB,
                "github",
                HostedCredentialRejectionCode.GITHUB_REPO_INACCESSIBLE,
            )
        format_rejection = secrets_util.classify_github_token(token)
        if format_rejection is not None:
            return self._rejected_write(
                repo_id, owner_repo, HostedCredentialKind.GITHUB, "github", format_rejection
            )
        check = self._github_verifier.verify(token, owner_repo)
        if not check.accepted or not check.login:
            rejection = _as_rejection_code(check.rejection) or (
                HostedCredentialRejectionCode.GITHUB_TOKEN_INVALID
            )
            return self._rejected_write(
                repo_id, owner_repo, HostedCredentialKind.GITHUB, "github", rejection
            )
        status = HostedCredentialStatus(
            repo_id=repo_id,
            owner_repo=owner_repo,
            kind=HostedCredentialKind.GITHUB,
            provider="github",
            state=HostedCredentialState.ACTIVE,
            github_login=check.login,
            created_at_utc=_now_utc(),
            verified_at_utc=_now_utc(),
            secret_length=len(token),
        )
        if not self._stop_runner_before_credential_update(repo_id):
            return self._rejected_write(
                repo_id,
                owner_repo,
                HostedCredentialKind.GITHUB,
                "github",
                HostedCredentialRejectionCode.RUNNER_UNAVAILABLE,
            )
        self._credentials.put_credential(status, token)
        return HostedCredentialWriteResult(status=status, kept_previous=False)

    # ── Agent 凭据 ──────────────────────────────────────────────────────

    def save_agent_credential(self, repo_id: str, owner_repo: str, provider: str, api_key: str):
        """保存 Agent API key 但不调用模型；仅做 provider/格式匹配。"""
        spec = secrets_util.HOSTED_AGENT_PROVIDERS.get(provider)
        if spec is None:
            return self._rejected_write(
                repo_id,
                owner_repo,
                HostedCredentialKind.AGENT,
                provider,
                HostedCredentialRejectionCode.PROVIDER_UNSUPPORTED,
            )
        if not spec.matches_key_format(api_key):
            return self._rejected_write(
                repo_id,
                owner_repo,
                HostedCredentialKind.AGENT,
                provider,
                HostedCredentialRejectionCode.KEY_FORMAT_INVALID,
            )
        status = HostedCredentialStatus(
            repo_id=repo_id,
            owner_repo=owner_repo,
            kind=HostedCredentialKind.AGENT,
            provider=provider,
            state=HostedCredentialState.UNVERIFIED,
            created_at_utc=_now_utc(),
            secret_length=len(api_key),
        )
        previous = self._credentials.get_status(repo_id, HostedCredentialKind.AGENT)
        if previous is not None and str(previous.state) == HostedCredentialState.ACTIVE.value:
            return HostedCredentialWriteResult(status=status, kept_previous=True)
        self._credentials.put_credential(status, api_key)
        return HostedCredentialWriteResult(status=status, kept_previous=False)

    def test_agent_credential(
        self, repo_id: str, owner_repo: str, provider: str, api_key: str, *, cost_confirmed: bool
    ):
        """仅在客户显式确认费用后发起一条最小真实模型请求并据结果换凭据。"""
        if not cost_confirmed:
            status = HostedCredentialStatus(
                repo_id=repo_id,
                owner_repo=owner_repo,
                kind=HostedCredentialKind.AGENT,
                provider=provider,
                state=HostedCredentialState.UNVERIFIED,
                created_at_utc=_now_utc(),
                last_rejection=HostedCredentialRejectionCode.COST_CONFIRMATION_REQUIRED,
                secret_length=len(api_key),
            )
            return HostedCredentialWriteResult(status=status, kept_previous=True)
        probe = self._agent_prober.probe(provider, api_key)
        if not probe.accepted:
            rejection = _as_rejection_code(
                probe.rejection or HostedCredentialRejectionCode.AGENT_MODEL_CALL_FAILED
            )
            return self._rejected_write(
                repo_id, owner_repo, HostedCredentialKind.AGENT, provider, rejection
            )
        status = HostedCredentialStatus(
            repo_id=repo_id,
            owner_repo=owner_repo,
            kind=HostedCredentialKind.AGENT,
            provider=provider,
            state=HostedCredentialState.ACTIVE,
            created_at_utc=_now_utc(),
            verified_at_utc=_now_utc(),
            secret_length=len(api_key),
        )
        if not self._stop_runner_before_credential_update(repo_id):
            return self._rejected_write(
                repo_id,
                owner_repo,
                HostedCredentialKind.AGENT,
                provider,
                HostedCredentialRejectionCode.RUNNER_UNAVAILABLE,
            )
        self._credentials.put_credential(status, api_key)
        return HostedCredentialWriteResult(status=status, kept_previous=False)

    # ── 删除与运行时交付 ────────────────────────────────────────────────

    def delete_credential(self, repo_id: str, kind: HostedCredentialKind):
        """删除 KedaCode 副本：停 runner → 清密文 → 清 tmpfs；上游 key 不因此失效。"""
        runner_stopped = self._runner_control.stop_runner(repo_id)
        if not runner_stopped:
            return HostedCredentialDeleteResult(
                removed=False,
                runner_stopped=False,
                tmpfs_erased=False,
                notes=("runner 未能安全停止；凭据仍保留，请检查 VM 后重试。",),
            )
        try:
            tmpfs_erased = self._handoff.erase(repo_id)
        except (OSError, RuntimeError, ValueError) as erase_error:
            _logger.error(
                "Could not erase hosted runner credentials for repository '%s' (%s).",
                repo_id,
                type(erase_error).__name__,
            )
            return HostedCredentialDeleteResult(
                removed=False,
                runner_stopped=True,
                tmpfs_erased=False,
                notes=("runner 已停止，但 tmpfs 凭据目录未能清理；密文仍保留，请检查 VM。",),
            )
        if not tmpfs_erased:
            return HostedCredentialDeleteResult(
                removed=False,
                runner_stopped=True,
                tmpfs_erased=False,
                notes=("runner 已停止，但 tmpfs 凭据目录仍存在；密文仍保留，请检查 VM。",),
            )
        removed = self._credentials.delete_credential(repo_id, kind)
        return HostedCredentialDeleteResult(
            removed=removed,
            runner_stopped=runner_stopped,
            tmpfs_erased=tmpfs_erased,
            upstream_revoked=False,
            notes=(
                "KedaCode 保存的本地副本已删除；GitHub/模型供应商侧的原始 key 仍有效，"
                "需客户自行在上游平台 revoke。",
            ),
        )

    def list_credential_statuses(self):
        """列出全部凭据状态视图（不含任何 secret）。"""
        return list(self._credentials.list_statuses())

    def start_runner(self, repo_id: str) -> bool:
        """仅在该仓库的 GitHub 与 Agent 凭据均已验证后启动配置 runner。

        Args:
            repo_id: Console 中保存凭据时使用的仓库标识。

        Returns:
            runner 已由宿主控制器启动时为 ``True``；配置不匹配或宿主拒绝时为 ``False``。

        Raises:
            ValueError: 两类凭据缺失、未通过验证或指向不同仓库时。
        """
        github_status = self._credentials.get_status(repo_id, HostedCredentialKind.GITHUB)
        agent_status = self._credentials.get_status(repo_id, HostedCredentialKind.AGENT)
        if (
            github_status is None
            or agent_status is None
            or str(github_status.state) != HostedCredentialState.ACTIVE.value
            or str(agent_status.state) != HostedCredentialState.ACTIVE.value
            or github_status.owner_repo.casefold() != agent_status.owner_repo.casefold()
        ):
            raise ValueError("both GitHub and Agent credentials must be active for this repository")

        try:
            self._refresh_runner_credentials(repo_id)
            started = self._runner_control.start_runner(repo_id, github_status.owner_repo)
        except (OSError, RuntimeError, ValueError) as start_error:
            self._erase_runner_credentials_after_failed_start(repo_id, start_error)
            return False
        if not started:
            self._erase_runner_credentials_after_failed_start(repo_id)
        return started

    def stop_runner(self, repo_id: str) -> bool:
        """停止托管仓库 runner 并清除该仓库的 tmpfs 明文认证目录。"""
        return self._runner_control.stop_runner(repo_id)

    def _stop_runner_before_credential_update(self, repo_id: str) -> bool:
        """凭据替换前先停 runner 并清理旧 tmpfs，避免活动容器继续使用旧值。"""
        try:
            return self._runner_control.stop_runner(repo_id)
        except (OSError, RuntimeError, ValueError) as stop_error:
            _logger.warning(
                "Hosted runner stop failed before credential update for '%s' (%s).",
                repo_id,
                type(stop_error).__name__,
            )
            return False

    def _erase_runner_credentials_after_failed_start(
        self, repo_id: str, start_error: Exception | None = None
    ) -> None:
        """启动失败时尽力清除 tmpfs 明文，并只记录异常类型。"""
        if start_error is not None:
            _logger.warning(
                "Hosted runner start failed for repository '%s' (%s).",
                repo_id,
                type(start_error).__name__,
            )
        try:
            self._handoff.erase(repo_id)
        except (OSError, RuntimeError, ValueError) as erase_error:
            _logger.error(
                "Could not erase hosted runner credentials for repository '%s' (%s).",
                repo_id,
                type(erase_error).__name__,
            )

    def _refresh_runner_credentials(self, repo_id: str) -> None:
        """把该仓库当前 ACTIVE 的 GitHub/Agent 凭据重新交付到 runner tmpfs 目录。"""
        github_status = self._credentials.get_status(repo_id, HostedCredentialKind.GITHUB)
        github_token = self._active_secret(repo_id, HostedCredentialKind.GITHUB)
        github_credential = (
            RunnerGitHubCredential(token=github_token, login=github_status.github_login)
            if github_token and github_status and github_status.github_login
            else None
        )
        agent_status = next(
            (
                view
                for view in self._credentials.list_statuses()
                if view.repo_id == repo_id
                and str(view.kind) == HostedCredentialKind.AGENT.value
                and str(view.state) == HostedCredentialState.ACTIVE.value
            ),
            None,
        )
        agent_key = (
            self._credentials.read_secret(repo_id, HostedCredentialKind.AGENT)
            if agent_status
            else None
        )
        self._handoff.materialize(
            repo_id,
            github_credential,
            agent_status.provider if agent_status else None,
            agent_key,
        )

    def _active_secret(self, repo_id: str, kind: HostedCredentialKind) -> str | None:
        """仅当该类别凭据处于 ACTIVE 状态时返回解密明文，否则 ``None``。"""
        view = self._credentials.get_status(repo_id, kind)
        if view is None or str(view.state) != HostedCredentialState.ACTIVE.value:
            return None
        return self._credentials.read_secret(repo_id, kind)

    def _rejected_write(
        self,
        repo_id: str,
        owner_repo: str,
        kind: HostedCredentialKind,
        provider: str,
        rejection: HostedCredentialRejectionCode,
    ):
        """记录一次验证失败的状态视图并保留旧值，不改动 runner 运行凭据。"""
        previous = self._credentials.get_status(repo_id, kind)
        status = HostedCredentialStatus(
            repo_id=repo_id,
            owner_repo=owner_repo,
            kind=kind,
            provider=provider,
            state=HostedCredentialState.REJECTED,
            created_at_utc=previous.created_at_utc if previous else _now_utc(),
            verified_at_utc=previous.verified_at_utc if previous else None,
            last_rejection=rejection,
            secret_length=previous.secret_length if previous else 0,
        )
        return HostedCredentialWriteResult(status=status, kept_previous=previous is not None)


def _make_admin_record(password_hash: str, set_at_utc: datetime):
    """构造管理员口令哈希记录（core 侧 HostedAdminRecord）。"""
    from backend.core.shared.models.hosted_console import HostedAdminRecord

    return HostedAdminRecord(password_hash=password_hash, set_at_utc=set_at_utc)

"""托管 Console 的 composition root（Issue #265，FR-2/FR-5）。

按四层依赖方向，core 用例只声明端口、不静态 import engines/infrastructure；
本模块在 engines 层把具体实现装配成一个可注入 api 层的
:class:`~backend.core.use_cases.hosted_console.HostedConsoleService`：

- 身份与会话：``HostedConsoleStore``（SQLite，只存摘要）。
- 加密运行凭据：``HostedCredentialVault``（Fernet，密文文件 + 单独数据密钥）。
- 上游校验：``GitHubReadOnlyVerifier`` / ``MinimalAgentKeyProber``（stdlib urllib）。
- 运行时交付：``TmpfsRunnerCredentialHandoff``（宿主 tmpfs 原生配置目录）。
- runner 控制：``ComposeRunnerControl``（受认证 Console 启动固定单仓库容器，轮换/删除时停止）。

部署身份（bootstrap 绑定值）取自本安装的数据密钥指纹：密钥文件在首次使用时
生成且从不跨机拷贝，因此换 VM 后指纹不同，引导码与密文都无法在别的机器上复用
（fail-closed）。
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from pathlib import Path

from backend.core.shared.interfaces.container_runner import ContainerUpRequest
from backend.core.shared.models import product_identity
from backend.core.use_cases.hosted_console import HostedConsoleService
from backend.engines.agent_runner.container_ops import (
    ContainerOpsController,
    resolve_packaged_runner_assets,
    run_container_down,
)
from backend.engines.agent_runner.runner_credential_handoff import (
    TmpfsRunnerCredentialHandoff,
    is_tmpfs_directory,
)
from backend.infrastructure.config.agent_runner_settings import AgentRunnerHostedSettings
from backend.infrastructure.http_clients.hosted_credential_checks import (
    GitHubReadOnlyVerifier,
    MinimalAgentKeyProber,
)
from backend.infrastructure.persistence.hosted_console_store import HostedConsoleStore
from backend.infrastructure.persistence.hosted_credential_vault import (
    HostedCredentialVault,
)

_logger = logging.getLogger(__name__)


class ComposeRunnerControl:
    """启动和停止 VM 配置绑定的单仓库 hosted runner。

    Args:
        compose_file: 托管 runner 的 Compose 文件路径。
        hosted_settings: 托管模式设置，包含唯一 repo id、checkout 与 tmpfs 路径。
    """

    def __init__(
        self,
        *,
        compose_file: Path,
        hosted_settings: AgentRunnerHostedSettings,
    ) -> None:
        """保存托管设置与 compose 文件，供单仓库启停使用。"""
        self._compose_file = Path(compose_file)
        self._hosted_settings = hosted_settings
        self._runner_auth_root = Path(hosted_settings.runner_auth_root)

    def stop_runner(self, repo_id: str) -> bool:
        """停止 runner 后清除该仓库 tmpfs 明文；任一操作失败即返回 ``False``。"""
        if repo_id != self._hosted_settings.repository_id:
            _logger.warning("Hosted runner stop refused for unconfigured repository '%s'.", repo_id)
            return False
        try:
            auth_handoff = TmpfsRunnerCredentialHandoff(self._runner_auth_root)
            auth_directory = Path(auth_handoff.host_directory(repo_id))
            repository_path = self._hosted_settings.repository_path
            if not repository_path:
                _logger.warning(
                    "Hosted runner stop refused because its checkout is not configured."
                )
                return False
            compose_environment = product_identity.build_child_env_aliases({"REPO_ID": repo_id})
            compose_environment.update(
                {
                    "REPO_PATH": str(repository_path),
                    "KEDACODE_RUNNER_AUTH_DIR": str(auth_directory),
                    "KEDACODE_DESKTOP_ENABLED": str(self._hosted_settings.desktop_enabled).lower(),
                    "KEDACODE_RUNNER_PROFILE": (
                        "desktop" if self._hosted_settings.desktop_enabled else "latest"
                    ),
                }
            )
            run_container_down(
                self._compose_file,
                env_overrides=compose_environment,
            )
            if auth_directory.exists() and not auth_handoff.erase(repo_id):
                raise OSError("repo-specific tmpfs auth directory could not be erased")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as down_error:
            _logger.warning(
                "Could not stop runner or erase its auth directory for '%s' (%s).",
                repo_id,
                type(down_error).__name__,
            )
            return False
        return True

    def start_runner(self, repo_id: str, owner_repo: str) -> bool:
        """校验固定 checkout 与 tmpfs 后启动唯一配置的 hosted runner。"""
        configured_repo_path = self._hosted_settings.repository_path
        if (
            not configured_repo_path
            or repo_id != self._hosted_settings.repository_id
            or not _github_remote_matches(Path(configured_repo_path), owner_repo)
        ):
            _logger.warning("Hosted runner start refused for repository '%s'.", repo_id)
            return False

        try:
            auth_dir = Path(
                TmpfsRunnerCredentialHandoff(self._runner_auth_root).host_directory(repo_id)
            )
        except (OSError, ValueError):
            _logger.warning("Hosted runner auth root is unavailable for '%s'.", repo_id)
            return False
        if not is_tmpfs_directory(auth_dir) or not _has_native_auth_files(auth_dir):
            _logger.warning("Hosted runner auth directory is unavailable for '%s'.", repo_id)
            return False

        desktop_enabled = self._hosted_settings.desktop_enabled
        hosted_request = ContainerUpRequest(
            repo_path=Path(configured_repo_path),
            repo_id=repo_id,
            hosted=True,
            runner_auth_dir=str(auth_dir),
            extra_env={
                "KEDACODE_DESKTOP_ENABLED": str(desktop_enabled).lower(),
                "KEDACODE_RUNNER_PROFILE": "desktop" if desktop_enabled else "latest",
            },
            build=desktop_enabled,
        )
        try:
            ContainerOpsController().run_container_up(
                self._compose_file,
                hosted_request,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as start_error:
            _logger.warning(
                "Could not start hosted runner for repository '%s' (%s).",
                repo_id,
                type(start_error).__name__,
            )
            self.stop_runner(repo_id)
            return False
        return True


def _github_remote_matches(repository_path: Path, owner_repo: str) -> bool:
    """检查固定 checkout 的 origin 是否正好指向获准 GitHub 仓库。"""
    if not repository_path.is_dir():
        return False
    try:
        remote_result = subprocess.run(
            ["git", "-C", str(repository_path), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    remote_url = remote_result.stdout.strip()
    remote_match = re.fullmatch(
        r"(?:(?:https|ssh)://(?:git@)?|git@)?github\.com[:/]([^/:]+/[^/:]+?)(?:\.git)?/?",
        remote_url,
        flags=re.IGNORECASE,
    )
    return bool(remote_match and remote_match.group(1).casefold() == owner_repo.casefold())


def _has_native_auth_files(auth_directory: Path) -> bool:
    """要求 tmpfs 中同时存在 GitHub 与至少一个 Agent 原生认证文件。"""
    github_auth_file = auth_directory / "gh" / "hosts.yml"
    agent_auth_files = (
        auth_directory / "claude" / "settings.json",
        auth_directory / "codex" / "auth.json",
        auth_directory / "kimi-code" / "config.toml",
    )
    return github_auth_file.is_file() and any(path.is_file() for path in agent_auth_files)


def _deployment_id_from_data_key(data_key_file: Path) -> str:
    """读取（若缺失则由 vault 首建后读取）数据密钥，取其摘要作为部署绑定 id。"""
    key_bytes = Path(data_key_file).read_bytes().strip()
    return hashlib.sha256(key_bytes).hexdigest()[:32]


def build_hosted_console_service(
    hosted_settings,
    *,
    compose_file: Path,
    bootstrap_ttl_seconds: int | None = None,
) -> HostedConsoleService:
    """依据托管配置装配完整的 ``HostedConsoleService``。

    Args:
        hosted_settings: ``AgentRunnerSettings.hosted`` 段实例。
        compose_file: 换凭据时用于 ``docker compose down`` 的 runner Compose 文件。
        bootstrap_ttl_seconds: 覆盖引导码有效期（CLI ``--expires-in`` 用）；``None``
            时用 core 默认的 24 小时。

    Returns:
        已注入全部端口实现的编排服务，供 api 层路由与 CLI 消费。
    """
    vault = HostedCredentialVault(
        credentials_file=Path(hosted_settings.credentials_file),
        data_key_file=Path(hosted_settings.data_key_file),
    )
    identity_store = HostedConsoleStore(Path(hosted_settings.console_db_path))
    handoff = TmpfsRunnerCredentialHandoff(Path(hosted_settings.runner_auth_root))
    service_kwargs = {}
    if bootstrap_ttl_seconds is not None:
        service_kwargs["bootstrap_ttl_seconds"] = bootstrap_ttl_seconds
    return HostedConsoleService(
        identity_store=identity_store,
        credential_repo=vault,
        github_verifier=GitHubReadOnlyVerifier(),
        agent_prober=MinimalAgentKeyProber(),
        handoff=handoff,
        runner_control=ComposeRunnerControl(
            compose_file=compose_file,
            hosted_settings=hosted_settings,
        ),
        deployment_id=_deployment_id_from_data_key(Path(hosted_settings.data_key_file)),
        session_ttl_seconds=hosted_settings.session_ttl_seconds,
        **service_kwargs,
    )


def hosted_console_enabled(settings) -> bool:
    """Return whether hosted Console mode is active for the given agent-runner settings."""
    return bool(settings.hosted.enabled)


def issue_hosted_bootstrap_code(settings, *, bootstrap_ttl_seconds: int) -> str:
    """VM 本地签发一次性引导码（CLI 入口，不要求 Docker 资产存在）。

    只装配身份存储所需的端口；签发不触碰 runner 控制或 tmpfs 交付，故传入占位
    compose 路径与空交付即可。原码只在本函数返回值出现一次，服务端仅存哈希。
    """
    identity_store = HostedConsoleStore(Path(settings.hosted.console_db_path))
    vault = HostedCredentialVault(
        credentials_file=Path(settings.hosted.credentials_file),
        data_key_file=Path(settings.hosted.data_key_file),
    )
    service = HostedConsoleService(
        identity_store=identity_store,
        credential_repo=vault,
        github_verifier=GitHubReadOnlyVerifier(),
        agent_prober=MinimalAgentKeyProber(),
        handoff=TmpfsRunnerCredentialHandoff(Path(settings.hosted.runner_auth_root)),
        runner_control=ComposeRunnerControl(
            compose_file=Path("-"),
            hosted_settings=settings.hosted,
        ),
        deployment_id=_deployment_id_from_data_key(Path(settings.hosted.data_key_file)),
        bootstrap_ttl_seconds=bootstrap_ttl_seconds,
        session_ttl_seconds=settings.hosted.session_ttl_seconds,
    )
    return service.issue_bootstrap_code()


def create_hosted_console_service(settings) -> HostedConsoleService:
    """从 ``AgentRunnerSettings`` 解析托管段并装配服务（api 层唯一入口）。

    Args:
        settings: ``load_fresh_agent_runner_settings()`` 返回的聚合设置。

    Returns:
        已装配的 :class:`HostedConsoleService`。

    Raises:
        RuntimeError: 托管段未启用时调用（调用方应先判 :func:`hosted_console_enabled`）。
    """
    if not settings.hosted.enabled:
        raise RuntimeError("hosted console mode is not enabled")
    compose_file = resolve_packaged_runner_assets(hosted=True).compose_file
    return build_hosted_console_service(settings.hosted, compose_file=compose_file)


__all__ = [
    "ComposeRunnerControl",
    "build_hosted_console_service",
    "create_hosted_console_service",
    "hosted_console_enabled",
    "issue_hosted_bootstrap_code",
]

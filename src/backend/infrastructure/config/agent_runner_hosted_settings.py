"""托管 Console 与凭据运行配置。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from backend.core.shared.models import product_identity

_HOSTED_CONSOLE_DB_NAME = "hosted-console.db"
_HOSTED_CREDENTIALS_FILE_NAME = "hosted-credentials.json"
_HOSTED_DATA_KEY_NAME = "hosted-data.key"


class AgentRunnerHostedSettings(BaseModel):
    """托管 Console 模式配置（Issue #265；默认关闭，本地路径零变化）。

    ``enabled`` 启用 Console 真实认证与按仓库加密凭据；Console 只可启动
    ``repository_id`` / ``repository_path`` 指定的单仓库 runner。
    监听地址不在这里配置：``kc console`` 无论何模式都只绑 ``127.0.0.1``，
    对外 HTTPS 入口由运营者的反向代理承担（见 docs/guides/agent-runner.md）。
    """

    enabled: bool = False
    #: 客户可见的公网 Console origin；用于 cookie Secure 判定。
    public_base_url: str = ""
    #: 宿主侧 repo 专属运行时凭据目录的根（Linux 上应为 tmpfs 挂载点）。
    runner_auth_root: str = "/run/kedacode/runner-auth"
    #: 本部署唯一允许启动的仓库 id；为空时 Console 仍可设置凭据但不能启动 runner。
    repository_id: str = ""
    #: 上述仓库在 VM 上由运营者预先准备的 checkout 绝对路径。
    repository_path: str = ""
    #: 在该 Hosted Runner 容器内创建虚拟 Linux 桌面；默认关闭。
    desktop_enabled: bool = False
    #: 身份与会话 SQLite（只存摘要，不存任何凭据明文）。
    console_db_path: str = Field(
        default_factory=lambda: str(product_identity.state_home() / _HOSTED_CONSOLE_DB_NAME)
    )
    #: 密文凭据文件（专用凭据文件；不写入 console SQLite）。
    credentials_file: str = Field(
        default_factory=lambda: str(product_identity.state_home() / _HOSTED_CREDENTIALS_FILE_NAME)
    )
    #: 数据密钥文件，与密文分目录保存，OS 权限 0600。
    data_key_file: str = Field(
        default_factory=lambda: str(product_identity.state_home() / _HOSTED_DATA_KEY_NAME)
    )
    session_ttl_seconds: int = 8 * 60 * 60

    @model_validator(mode="after")
    def _validate_hosted_fields(self) -> "AgentRunnerHostedSettings":
        """归一化本地路径并校验会话 TTL、入口地址与单仓库 runner 配置。"""
        for path_field in ("console_db_path", "credentials_file", "data_key_file"):
            setattr(
                self,
                path_field,
                str(
                    product_identity.normalize_state_path(
                        getattr(self, path_field),
                        home_path=Path.home(),
                    )
                ),
            )
        if self.session_ttl_seconds < 60:
            raise ValueError("hosted.session_ttl_seconds must be at least 60 seconds")
        if bool(self.repository_id) != bool(self.repository_path):
            raise ValueError(
                "hosted.repository_id and hosted.repository_path must be configured together"
            )
        runner_auth_root = Path(self.runner_auth_root).expanduser()
        if not runner_auth_root.is_absolute():
            raise ValueError("hosted.runner_auth_root must be an absolute path")
        object.__setattr__(self, "runner_auth_root", str(runner_auth_root.resolve()))
        if self.repository_path:
            repository_path = Path(self.repository_path).expanduser()
            if not repository_path.is_absolute():
                raise ValueError("hosted.repository_path must be an absolute path")
            object.__setattr__(self, "repository_path", str(repository_path.resolve()))
        if self.enabled and not self.public_base_url:
            raise ValueError(
                "hosted.public_base_url is required when hosted mode is enabled "
                "(the Console is customer-facing behind an HTTPS reverse proxy)"
            )
        if self.enabled:
            try:
                public_url = urlparse(self.public_base_url)
                public_hostname = public_url.hostname
            except ValueError as invalid_public_url:
                raise ValueError(
                    "hosted.public_base_url must be HTTPS; plain HTTP is only allowed "
                    "for localhost reverse-proxy checks"
                ) from invalid_public_url
            secure_public_origin = public_url.scheme == "https" and bool(public_hostname)
            local_http_origin = public_url.scheme == "http" and public_hostname in {
                "127.0.0.1",
                "localhost",
            }
            if not (secure_public_origin or local_http_origin):
                raise ValueError(
                    "hosted.public_base_url must be HTTPS; plain HTTP is only allowed "
                    "for localhost reverse-proxy checks"
                )
        return self

"""Gitea 实例登记的机器级配置模型。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_ENVIRONMENT_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1"})


class GiteaInstanceSettings(BaseModel):
    """一条本机 Gitea 实例与访问令牌环境变量登记。"""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    token_env: str

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        """去除地址两端空白和尾部斜杠。"""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("agent_runner.gitea_instances.base_url must be a non-empty URL.")
        return value.strip().rstrip("/")

    @field_validator("token_env")
    @classmethod
    def _validate_token_environment_variable(cls, value: str) -> str:
        """确保 token_env 是环境变量名而不是令牌值。"""
        normalized_value = value.strip()
        if not _ENVIRONMENT_VARIABLE_PATTERN.fullmatch(normalized_value):
            raise ValueError(
                "agent_runner.gitea_instances.token_env must be an environment variable name."
            )
        return normalized_value

    @model_validator(mode="after")
    def _validate_base_url_transport(self) -> "GiteaInstanceSettings":
        """拒绝非本机 HTTP、凭据、查询参数与无效地址。"""
        parsed_url = urlsplit(self.base_url)
        hostname = (parsed_url.hostname or "").casefold()
        try:
            parsed_url.port
        except ValueError as exc:
            raise ValueError("agent_runner.gitea_instances.base_url has an invalid port.") from exc
        if (
            not hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "agent_runner.gitea_instances.base_url must be an HTTP(S) origin without "
                "embedded credentials, query, or fragment."
            )
        if parsed_url.scheme == "https":
            return self
        if parsed_url.scheme == "http" and hostname in _LOCAL_HTTP_HOSTS:
            return self
        raise ValueError(
            "agent_runner.gitea_instances.base_url must use https://; http:// is allowed "
            "only for localhost or 127.0.0.1."
        )

    @property
    def hostname(self) -> str:
        """返回用于与 Git remote 主机名匹配的小写 hostname。"""
        return (urlsplit(self.base_url).hostname or "").casefold()


def validate_unique_gitea_hosts(
    instances: list[GiteaInstanceSettings],
) -> list[GiteaInstanceSettings]:
    """拒绝同一 hostname 的多条登记，避免实例选择不确定。"""
    registered_hosts: set[str] = set()
    for instance_settings in instances:
        if instance_settings.hostname in registered_hosts:
            raise ValueError(
                "agent_runner.gitea_instances must contain at most one entry per host; "
                f"duplicate host {instance_settings.hostname!r}."
            )
        registered_hosts.add(instance_settings.hostname)
    return instances


__all__ = ["GiteaInstanceSettings", "validate_unique_gitea_hosts"]

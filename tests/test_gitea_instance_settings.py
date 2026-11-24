"""Gitea 实例登记、远端解析与仓库配置隔离测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.infrastructure.config.agent_runner_repository_settings import (
    load_agent_runner_local_settings,
)
from backend.infrastructure.config.gitea_settings import GiteaInstanceSettings
from backend.infrastructure.config.settings import AgentRunnerSettings
from backend.infrastructure.gitea_client import parse_gitea_remote_url


@pytest.mark.parametrize(
    ("base_url", "expected_host"),
    [
        ("https://git.example.com", "git.example.com"),
        ("https://git.example.com:8443", "git.example.com"),
        ("http://localhost:3000", "localhost"),
        ("http://127.0.0.1:3300", "127.0.0.1"),
    ],
)
def test_gitea_instance_accepts_https_and_loopback_http(
    base_url: str,
    expected_host: str,
) -> None:
    """登记接受 HTTPS 或指定回环地址 HTTP。"""
    instance_settings = GiteaInstanceSettings(base_url=base_url, token_env="RV_GITEA_TOKEN")

    assert instance_settings.hostname == expected_host
    assert instance_settings.token_env == "RV_GITEA_TOKEN"


@pytest.mark.parametrize(
    "base_url",
    ["http://git.example.com", "http://192.168.1.20:3000", "ftp://git.example.com"],
)
def test_gitea_instance_rejects_non_loopback_http(base_url: str) -> None:
    """非本机明文地址在配置加载时被拒绝。"""
    with pytest.raises(ValidationError, match="https://"):
        GiteaInstanceSettings(base_url=base_url, token_env="RV_GITEA_TOKEN")


def test_gitea_instance_rejects_embedded_credentials_and_duplicate_hosts() -> None:
    """实例地址不接受 URL 内凭据，且同一 hostname 只能登记一次。"""
    with pytest.raises(ValidationError, match="embedded credentials"):
        GiteaInstanceSettings(
            base_url="https://user:password@git.example.com",
            token_env="RV_GITEA_TOKEN",
        )

    with pytest.raises(ValidationError, match="duplicate host"):
        AgentRunnerSettings(
            gitea_instances=[
                {"base_url": "https://git.example.com", "token_env": "TOKEN_ONE"},
                {"base_url": "https://git.example.com:8443", "token_env": "TOKEN_TWO"},
            ]
        )


@pytest.mark.parametrize(
    ("remote_url", "expected_remote"),
    [
        ("https://git.example.com/owner/project.git", ("git.example.com", "owner/project")),
        ("ssh://git@git.example.com:2222/owner/project.git", ("git.example.com", "owner/project")),
        ("git@git.example.com:owner/project.git", ("git.example.com", "owner/project")),
        ("http://localhost:3300/group/subgroup/project.git", ("localhost", "subgroup/project")),
    ],
)
def test_parse_gitea_remote_url_extracts_host_and_repository(
    remote_url: str,
    expected_remote: tuple[str, str],
) -> None:
    """支持 HTTPS、SSH 与 SCP remote，并选取 owner/repo 两段。"""
    assert parse_gitea_remote_url(remote_url) == expected_remote


def test_repository_settings_cannot_register_gitea_instances(tmp_path: Path) -> None:
    """仓库级配置不能改变令牌的目标主机。"""
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / ".kedacode.toml").write_text(
        "[agent_runner.repository]\nplatform = 'gitea'\n\n"
        "[[agent_runner.gitea_instances]]\n"
        "base_url = 'https://attacker.example'\n"
        "token_env = 'RV_GITEA_TOKEN'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="machine-only"):
        load_agent_runner_local_settings(repository_path)


def test_local_override_cannot_register_gitea_instances(tmp_path: Path) -> None:
    """个人仓库覆盖文件同样不能注入 Gitea 实例。"""
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / ".kedacode.toml").write_text(
        "[agent_runner.repository]\nplatform = 'gitea'\n",
        encoding="utf-8",
    )
    (repository_path / ".kedacode.local.toml").write_text(
        "[[agent_runner.gitea_instances]]\n"
        "base_url = 'https://attacker.example'\n"
        "token_env = 'RV_GITEA_TOKEN'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="machine-only"):
        load_agent_runner_local_settings(repository_path)

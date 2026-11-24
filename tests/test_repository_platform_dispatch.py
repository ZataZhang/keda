"""仓库平台声明的校验、传递与客户端分发测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.core.shared.models.repository_platform import (
    DEFAULT_REPOSITORY_PLATFORM,
    GITEA_PLATFORM,
    GITHUB_PLATFORM,
    RepositoryPlatformNotImplementedError,
)
from backend.engines.agent_runner import factories
from backend.infrastructure.config.agent_runner_repository_settings import (
    AgentRunnerRepositoryMetadataSettings,
    AgentRunnerRepositorySettings,
    load_agent_runner_local_settings,
)
from tests.conftest import FakeProcessRunner


@pytest.mark.parametrize("platform_value", [GITHUB_PLATFORM, GITEA_PLATFORM])
def test_repository_platform_values_are_accepted(platform_value: str) -> None:
    """配置身份模型与仓库设置模型接受词表中的平台。"""
    metadata_settings = AgentRunnerRepositoryMetadataSettings(platform=platform_value)
    repository_settings = AgentRunnerRepositorySettings(
        path="/tmp/repo",
        platform=platform_value,
    )

    assert metadata_settings.platform == platform_value
    assert repository_settings.platform == platform_value


@pytest.mark.parametrize("platform_value", ["gitlab", "GitHub", " github ", ""])
def test_repository_platform_values_reject_unknown_spellings(platform_value: str) -> None:
    """未知、大小写不符、带空格与空字符串均在配置读取时被拒绝。"""
    with pytest.raises(ValidationError) as error_info:
        AgentRunnerRepositoryMetadataSettings(platform=platform_value)

    error_message = str(error_info.value)
    assert "repository.platform" in error_message
    assert repr(platform_value) in error_message
    assert repr(GITHUB_PLATFORM) in error_message
    assert repr(GITEA_PLATFORM) in error_message

    with pytest.raises(ValidationError) as repository_error_info:
        AgentRunnerRepositorySettings(path="/tmp/repo", platform=platform_value)

    repository_error_message = str(repository_error_info.value)
    assert "repository.platform" in repository_error_message
    assert repr(platform_value) in repository_error_message
    assert repr(GITHUB_PLATFORM) in repository_error_message
    assert repr(GITEA_PLATFORM) in repository_error_message


def test_repository_platform_defaults_to_github() -> None:
    """省略平台时身份模型与仓库设置均使用 GitHub。"""
    assert DEFAULT_REPOSITORY_PLATFORM == GITHUB_PLATFORM
    assert AgentRunnerRepositoryMetadataSettings().platform == GITHUB_PLATFORM
    assert AgentRunnerRepositorySettings(path="/tmp/repo").platform == GITHUB_PLATFORM


def test_local_settings_propagate_repository_platform(tmp_path: Path) -> None:
    """仓库配置中的平台声明会传递到加载结果。"""
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / ".kedacode.toml").write_text(
        '[agent_runner.repository]\nid = "platform-test"\nplatform = "gitea"\n',
        encoding="utf-8",
    )

    repository_settings = load_agent_runner_local_settings(repository_path)

    assert repository_settings is not None
    assert repository_settings.platform == GITEA_PLATFORM


@pytest.mark.parametrize("config_platform", [None, GITHUB_PLATFORM])
def test_create_github_client_uses_github_for_default_and_explicit_platforms(
    tmp_path: Path,
    config_platform: str | None,
) -> None:
    """未声明与显式 GitHub 均返回原有 GitHub CLI 客户端。"""
    repository_path = tmp_path / (config_platform or "default-repository")
    repository_path.mkdir()
    if config_platform is not None:
        (repository_path / ".kedacode.toml").write_text(
            f'[agent_runner.repository]\nid = "platform-test"\nplatform = "{config_platform}"\n',
            encoding="utf-8",
        )
    process_runner = FakeProcessRunner()

    client = factories.create_github_client(repository_path, process_runner)

    assert isinstance(client, factories.GitHubCliClient)
    assert process_runner.calls == []


def test_create_github_client_stops_before_process_for_gitea(tmp_path: Path) -> None:
    """Gitea 声明在构造客户端及执行 gh 命令前抛出可操作错误。"""
    repository_path = tmp_path / "gitea-repository"
    repository_path.mkdir()
    config_path = repository_path / ".kedacode.toml"
    config_path.write_text(
        '[agent_runner.repository]\nid = "platform-test"\nplatform = "gitea"\n',
        encoding="utf-8",
    )
    process_runner = FakeProcessRunner()

    with pytest.raises(RepositoryPlatformNotImplementedError) as error_info:
        factories.create_github_client(repository_path, process_runner)

    error_message = str(error_info.value)
    assert error_info.value.platform_name == GITEA_PLATFORM
    assert error_info.value.config_path == config_path
    assert GITEA_PLATFORM in error_message
    assert "platform" in error_message
    assert "not implemented" in error_message
    assert GITHUB_PLATFORM in error_message
    assert "set platform = 'github'" in error_message
    assert process_runner.calls == []

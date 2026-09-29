"""Tests for the deprecation warning on ``generated_content`` ``mode = "template"`` pins."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.infrastructure.config.agent_runner_settings import (
    AgentRunnerRepositorySettings,
    load_agent_runner_local_settings,
)

SETTINGS_LOGGER_NAME = "backend.infrastructure.config.agent_runner_settings"


def _load_local_generated_content_settings(
    repo_path: Path, generated_content_toml: str
) -> AgentRunnerRepositorySettings:
    """把 ``generated_content_toml`` 写成仓库根的 ``.iar.toml`` 并经加载器读回。"""
    (repo_path / ".iar.toml").write_text(
        '[agent_runner.repository]\nid = "pin-test"\n' + generated_content_toml,
        encoding="utf-8",
    )
    repository_settings = load_agent_runner_local_settings(repo_path)
    assert repository_settings is not None
    return repository_settings


def test_local_config_warns_about_pinned_template_mode(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """显式钉成 ``mode = "template"`` 的 target 在加载时记弃用警告，配置仍被接受。"""
    with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER_NAME):
        repository_settings = _load_local_generated_content_settings(
            tmp_path,
            "[agent_runner.generated_content.issue_from_prd]\n"
            'mode = "template"\n'
            "[agent_runner.generated_content.draft_pr]\n"
            'mode = "template"\n'
            "[agent_runner.generated_content.prd_from_issue]\n"
            'mode = "agent"\n',
        )

    assert repository_settings.generated_content.draft_pr.mode == "template"
    assert "issue_from_prd, draft_pr" in caplog.text
    assert "prd_from_issue" not in caplog.text
    assert "template mode is deprecated" in caplog.text
    assert "iar config migrate" in caplog.text


def test_local_config_without_template_pins_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """没有钉 template（缺省或显式 agent）时不打扰。"""
    with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER_NAME):
        _load_local_generated_content_settings(
            tmp_path,
            '[agent_runner.generated_content.draft_pr]\nmode = "agent"\ntimeout_seconds = 300\n',
        )

    assert caplog.text == ""


def test_template_pin_warning_is_logged_once_per_process(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """daemon 每轮都会重新加载 ``.iar.toml``：同一份配置的弃用提示只出现一次。"""
    pinned_toml = '[agent_runner.generated_content.draft_pr]\nmode = "template"\n'

    with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER_NAME):
        for _ in range(3):
            _load_local_generated_content_settings(tmp_path, pinned_toml)

    assert caplog.text.count("template mode is deprecated") == 1

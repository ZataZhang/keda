"""Tests for repository-local daemon override (crash-reconcile round-trip)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.engines.agent_runner.repository_local import (
    settings_to_toml_string,
)
from backend.infrastructure.config.settings import (
    AgentRunnerDaemonSettings,
    AgentRunnerRunnerSettings,
    AgentRunnerSettings,
    AgentRunnerLocalSettings as LiveAgentRunnerLocalSettings,
    AgentRunnerRepositoryMetadataSettings,
    load_agent_runner_local_settings,
)


def test_local_settings_round_trip_daemon_override(tmp_path: Path) -> None:
    """写入 .kedacode.toml 含 [agent_runner.daemon] 字段 → load 出来字段值正确。

    验证:用户取消 _IAR_DAEMON_EXAMPLE 注释修改 reclaim_ttl_seconds 后,
    load_agent_runner_local_settings 能正确读出并保持其他字段默认。
    """
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    iar_toml = repo / ".kedacode.toml"
    iar_toml.write_text(
        "[agent_runner.repository]\n"
        'id = "fake-repo"\n'
        "[agent_runner.daemon]\n"
        "reclaim_ttl_seconds = 600\n"
        "reconcile_stale_attempts = false\n",
        encoding="utf-8",
    )

    loaded = load_agent_runner_local_settings(repo)
    assert loaded is not None
    assert loaded.daemon is not None
    assert loaded.daemon.reclaim_ttl_seconds == 600
    assert loaded.daemon.reconcile_stale_attempts is False
    # 未在 .kedacode.toml 写出的字段 → 继承全局默认(来自 AgentRunnerDaemonSettings)
    assert loaded.daemon.review_interval_seconds == 120


def test_local_settings_without_daemon_override(tmp_path: Path) -> None:
    """不写 [agent_runner.daemon] → loaded.daemon is None(由 merge 层兜底全局默认)。"""
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    iar_toml = repo / ".kedacode.toml"
    iar_toml.write_text(
        "[agent_runner.repository]\n" 'id = "fake-repo"\n',
        encoding="utf-8",
    )

    loaded = load_agent_runner_local_settings(repo)
    assert loaded is not None
    assert loaded.daemon is None


def test_init_renders_daemon_default_fields() -> None:
    """init 模板渲染结果包含可直接编辑的 daemon 默认配置。"""
    settings = LiveAgentRunnerLocalSettings(
        repository=AgentRunnerRepositoryMetadataSettings(id="fake-repo", enabled=True),
        daemon=AgentRunnerDaemonSettings(),
    )
    rendered = settings_to_toml_string(settings)

    assert "[agent_runner.daemon]" in rendered
    assert "reclaim_ttl_seconds" in rendered
    assert "reconcile_stale_attempts" in rendered
    # 全 5 个 daemon 字段都在示例里
    assert "review_interval_seconds" in rendered
    assert "run_interval_seconds" in rendered
    assert "max_deliberation_issues" in rendered
    # 示例默认 5 小时(18000)出现在注释里
    assert "18000" in rendered
    # daemon section 注释存在
    assert "Daemon 轮询与崩溃对账配置" in rendered


def test_global_daemon_default_exceeds_agent_timeout() -> None:
    """默认 reclaim_ttl_seconds = 18000(5 小时)，且严格大于默认 agent 超时。"""
    from backend.infrastructure.config.settings import AgentRunnerDaemonSettings

    settings = AgentRunnerDaemonSettings()
    assert settings.reclaim_ttl_seconds == 18000
    assert settings.reclaim_ttl_seconds > AgentRunnerRunnerSettings().timeout_seconds


def test_merge_repository_config_applies_daemon_reconcile_override(
    tmp_path: Path,
) -> None:
    """仓库层 ``[agent_runner.daemon]`` 只对账相关两键进 AppConfig，轮询键不越层。

    daemon 可一次服务多仓，轮询间隔在 CLI 边界按全局设置与旗标解析；因此合并层
    必须只搬运 :class:`DaemonConfig` 实际消费的键，配置视图里不留无人消费的字段。
    """
    from backend.core.shared.models.agent_runner import AppConfig, DaemonConfig
    from backend.engines.agent_runner.factory_config_merge import merge_repository_config

    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / ".kedacode.toml").write_text(
        "[agent_runner.repository]\n"
        'id = "fake-repo"\n'
        "[agent_runner.daemon]\n"
        "reconcile_stale_attempts = false\n"
        "reclaim_ttl_seconds = 600\n"
        "run_interval_seconds = 7\n",
        encoding="utf-8",
    )
    local_settings = load_agent_runner_local_settings(repo)

    merged = merge_repository_config(AppConfig(), local_settings)

    assert merged.daemon == DaemonConfig(
        reconcile_stale_attempts=False,
        reclaim_ttl_seconds=600,
    )


def test_merge_repository_config_keeps_daemon_unset_when_absent(tmp_path: Path) -> None:
    """未写 daemon 段 → 两键保持 None（沿用调用方全局默认，行为零变化）。"""
    from backend.core.shared.models.agent_runner import AppConfig, DaemonConfig
    from backend.engines.agent_runner.factory_config_merge import merge_repository_config

    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / ".kedacode.toml").write_text(
        '[agent_runner.repository]\nid = "fake-repo"\n',
        encoding="utf-8",
    )
    local_settings = load_agent_runner_local_settings(repo)

    merged = merge_repository_config(AppConfig(daemon=DaemonConfig()), local_settings)

    assert merged.daemon.reconcile_stale_attempts is None
    assert merged.daemon.reclaim_ttl_seconds is None


def test_reclaim_ttl_not_above_agent_timeout_warns(caplog: pytest.LogCaptureFixture) -> None:
    """TTL 不大于 agent 超时会误回收正常运行的 attempt，加载时必须告警。"""
    # 该配置类里 init 参数优先级最低（会被 config.toml 盖掉），故构造后改字段再复跑校验。
    runner_settings = AgentRunnerSettings()
    runner_settings.runner = AgentRunnerRunnerSettings(timeout_seconds=14400)
    runner_settings.daemon = AgentRunnerDaemonSettings(reclaim_ttl_seconds=10800)
    with caplog.at_level(logging.WARNING):
        runner_settings._warn_reclaim_ttl_not_above_agent_timeout()

    assert "reclaim_ttl_seconds=10800" in caplog.text


def test_default_reclaim_ttl_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """默认配置自洽，不应产生 TTL 告警。"""
    with caplog.at_level(logging.WARNING):
        AgentRunnerSettings()

    assert "reclaim_ttl_seconds" not in caplog.text

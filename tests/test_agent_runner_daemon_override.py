"""Tests for repository-local daemon override (crash-reconcile round-trip)."""

from __future__ import annotations

from pathlib import Path

from backend.engines.agent_runner.repository_local import (
    settings_to_toml_string,
)
from backend.infrastructure.config.settings import (
    AgentRunnerDaemonSettings,
    AgentRunnerLocalSettings as LiveAgentRunnerLocalSettings,
    AgentRunnerRepositoryMetadataSettings,
    load_agent_runner_local_settings,
)


def test_local_settings_round_trip_daemon_override(tmp_path: Path) -> None:
    """写入 .iar.toml 含 [agent_runner.daemon] 字段 → load 出来字段值正确。

    验证:用户取消 _IAR_DAEMON_EXAMPLE 注释修改 reclaim_ttl_seconds 后,
    load_agent_runner_local_settings 能正确读出并保持其他字段默认。
    """
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    iar_toml = repo / ".iar.toml"
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
    # 未在 .iar.toml 写出的字段 → 继承全局默认(来自 AgentRunnerDaemonSettings)
    assert loaded.daemon.review_interval_seconds == 120


def test_local_settings_without_daemon_override(tmp_path: Path) -> None:
    """不写 [agent_runner.daemon] → loaded.daemon is None(由 merge 层兜底全局默认)。"""
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    iar_toml = repo / ".iar.toml"
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
    # 示例默认 3 小时(10800)出现在注释里
    assert "10800" in rendered
    # daemon section 注释存在
    assert "Daemon 轮询与崩溃对账配置" in rendered


def test_global_daemon_default_is_3_hours() -> None:
    """默认 reclaim_ttl_seconds = 10800(3 小时)"""
    from backend.infrastructure.config.settings import AgentRunnerDaemonSettings

    settings = AgentRunnerDaemonSettings()
    assert settings.reclaim_ttl_seconds == 10800


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
    (repo / ".iar.toml").write_text(
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
    (repo / ".iar.toml").write_text(
        '[agent_runner.repository]\nid = "fake-repo"\n',
        encoding="utf-8",
    )
    local_settings = load_agent_runner_local_settings(repo)

    merged = merge_repository_config(AppConfig(daemon=DaemonConfig()), local_settings)

    assert merged.daemon.reconcile_stale_attempts is None
    assert merged.daemon.reclaim_ttl_seconds is None

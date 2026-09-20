"""针对仓库本地配置（``.iar.toml``）受限 Autopilot 写回的保真测试。

关注点：``set_enabled`` 只能改动一个布尔键——注释、同级键、未知键/子表必须逐字
保留；失败时原文件不变；原子替换为同目录临时文件 + ``os.replace``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.infrastructure.config.repository_settings_editor import (
    RepositorySettingsEditError,
    TomlRepositoryAutopilotSettingsEditor,
)

# 本仓真实 .iar.toml 的 [agent_runner.autopilot] 同级键 + 注释 + 一个未知子表，
# 用于断言写回保真（PRD §7 Executor Drift Guard 要求的两类样本）。
_FIDELITY_SAMPLE = """# 仓库本地配置
[agent_runner]
# 注释必须保留
unknown_key = "keep-me"

[agent_runner.autopilot]
# 是否启用自动推进
enabled = false
merge_method = "squash"
require_verifier_pass = true
auto_sign_off = false
merge_check_timeout_seconds = 1800

# 尾部注释也要保留
[agent_runner.some_unknown_table]
nested = 1
"""

_MINIMAL_SAMPLE = """[agent_runner.autopilot]
enabled = false
"""


@pytest.fixture
def editor() -> TomlRepositoryAutopilotSettingsEditor:
    return TomlRepositoryAutopilotSettingsEditor()


def _write_config(repo_root: Path, text: str) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    config_path = repo_root / ".iar.toml"
    config_path.write_text(text, encoding="utf-8")
    return config_path


def test_read_enabled_returns_persisted_value(tmp_path: Path, editor) -> None:
    """read_enabled 应返回文件中的显式值。"""
    repo_root = tmp_path / "repo"
    _write_config(repo_root, _MINIMAL_SAMPLE)
    assert editor.read_enabled(repo_root) is False

    _write_config(repo_root, _MINIMAL_SAMPLE.replace("false", "true"))
    assert editor.read_enabled(repo_root) is True


def test_read_enabled_returns_none_when_missing(tmp_path: Path, editor) -> None:
    """缺文件或缺键时返回 None（生效值此时来自全局配置默认值）。"""
    assert editor.read_enabled(tmp_path / "no-config") is None

    repo_root = tmp_path / "no-key"
    _write_config(repo_root, "[agent_runner]\n")
    assert editor.read_enabled(repo_root) is None


def test_set_enabled_changes_only_the_target_key(tmp_path: Path, editor) -> None:
    """写回后除 enabled 之外必须逐行一致（含注释、同级键、未知键与子表）。"""
    repo_root = tmp_path / "repo"
    config_path = _write_config(repo_root, _FIDELITY_SAMPLE)
    before_lines = config_path.read_text(encoding="utf-8").splitlines()

    editor.set_enabled(repo_root, True)

    after_lines = config_path.read_text(encoding="utf-8").splitlines()
    assert editor.read_enabled(repo_root) is True
    assert len(after_lines) == len(before_lines)
    changed = [
        (before, after) for before, after in zip(before_lines, after_lines) if before != after
    ]
    assert changed == [("enabled = false", "enabled = true")]
    # 注释、未知键与同级键仍在（逐行保留）
    after_text = "\n".join(after_lines)
    assert "# 注释必须保留" in after_text
    assert 'unknown_key = "keep-me"' in after_text
    assert "[agent_runner.some_unknown_table]" in after_text
    assert "merge_method" in after_text and "merge_check_timeout_seconds = 1800" in after_text


def test_set_enabled_toggles_back_without_drift(tmp_path: Path, editor) -> None:
    """来回切换不该丢失任何内容（幂等 round-trip）。"""
    repo_root = tmp_path / "repo"
    config_path = _write_config(repo_root, _FIDELITY_SAMPLE)
    baseline = config_path.read_text(encoding="utf-8")

    editor.set_enabled(repo_root, True)
    editor.set_enabled(repo_root, False)

    assert config_path.read_text(encoding="utf-8") == baseline
    assert editor.read_enabled(repo_root) is False


def test_set_enabled_rejects_missing_file(tmp_path: Path, editor) -> None:
    """目标仓没有 .iar.toml 时必须报错，而不是凭空创建配置。"""
    with pytest.raises(RepositorySettingsEditError):
        editor.set_enabled(tmp_path / "empty-repo", True)


def test_set_enabled_rejects_missing_agent_runner_section(tmp_path: Path, editor) -> None:
    """缺少 [agent_runner] 段时不写回。"""
    repo_root = tmp_path / "repo"
    config_path = _write_config(repo_root, "# only a comment\n")
    with pytest.raises(RepositorySettingsEditError):
        editor.set_enabled(repo_root, True)
    assert config_path.read_text(encoding="utf-8") == "# only a comment\n"


def test_set_enabled_leaves_original_intact_on_invalid_toml(tmp_path: Path, editor) -> None:
    """解析失败时原文件保持不变。"""
    repo_root = tmp_path / "repo"
    broken_text = "[agent_runner\nenabled = false\n"
    config_path = _write_config(repo_root, broken_text)
    with pytest.raises(RepositorySettingsEditError):
        editor.set_enabled(repo_root, True)
    assert config_path.read_text(encoding="utf-8") == broken_text


def test_config_source_path_points_to_local_file(tmp_path: Path, editor) -> None:
    """config_source_path 始终指向目标仓的 .iar.toml（存在与否都成立）。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    assert editor.config_source_path(repo_root) == (repo_root / ".iar.toml").resolve()

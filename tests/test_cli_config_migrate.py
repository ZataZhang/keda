"""Tests for the ``iar config migrate`` CLI command."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.api.cli import main
from backend.api.cli_parser import build_parser
from backend.api.cli_typer_app import app, config_app

LEGACY_PINNED_CONFIG = """\
[agent_runner.repository]
id = "target-local"

# GitHub Issue / PR 内容生成（面向人类阅读，不影响实现 Agent）
[agent_runner.generated_content]
# 是否启用 AI 生成 Issue / PR 正文
enabled = true
# 生成失败时的回退方式（当前仅支持 template）
fallback = "template"

# 从 commit 信息生成 Draft PR 的模板
[agent_runner.generated_content.draft_pr]
# 生成模式：template（模板渲染）或 agent（调用 AI）
mode = "template"
# 输出格式：json / markdown
output = "json"
# 生成超时秒数
timeout_seconds = 120
"""

CONFIG_WITHOUT_PINS = """\
[agent_runner.repository]
id = "target-local"
"""


@pytest.fixture(autouse=True)
def _isolated_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 入口会加载全局配置：指到一个空的 config.toml，避免读到开发机上的。"""
    isolated_config_path = tmp_path / "isolated-config.toml"
    isolated_config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv("IAR_CONFIG", str(isolated_config_path))


def _init_git_repository(tmp_path: Path, name: str, config_text: str | None = None) -> Path:
    repo_path = tmp_path / name
    repo_path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    if config_text is not None:
        (repo_path / ".iar.toml").write_text(config_text, encoding="utf-8")
    return repo_path


def test_config_migrate_dry_run_prints_the_plan_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """dry-run 列出将清掉的钉子与 diff，退出码 0，文件不变。"""
    repo_path = _init_git_repository(tmp_path, "target", LEGACY_PINNED_CONFIG)

    assert main(["config", "migrate", "--dry-run", "--repo", str(repo_path)]) == 0

    captured_output = capsys.readouterr().out
    assert "Would remove 5 value(s)" in captured_output
    # 表名里的方括号必须原样出现，不能被 rich 当成样式标签吞掉。
    assert "[agent_runner.generated_content.draft_pr]" in captured_output
    assert '    mode = "template"' in captured_output
    assert '-mode = "template"' in captured_output
    assert "Dry run: nothing was written." in captured_output
    assert (repo_path / ".iar.toml").read_text(encoding="utf-8") == LEGACY_PINNED_CONFIG


def test_config_migrate_rewrites_the_config_and_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """真实迁移写回文件；再跑一次提示无事可做，文件不再变化。"""
    repo_path = _init_git_repository(tmp_path, "target", LEGACY_PINNED_CONFIG)
    config_path = repo_path / ".iar.toml"

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 0

    first_output = capsys.readouterr().out
    assert "Removed 5 value(s)" in first_output
    assert "Wrote migrated config:" in first_output
    assert config_path.read_text(encoding="utf-8") == CONFIG_WITHOUT_PINS

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 0

    assert "Nothing to migrate" in capsys.readouterr().out
    assert config_path.read_text(encoding="utf-8") == CONFIG_WITHOUT_PINS


def test_config_migrate_defaults_to_the_current_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不带 --repo 时迁移 cwd 所在的 Git 仓库（子目录里也一样）。"""
    repo_path = _init_git_repository(tmp_path, "target", LEGACY_PINNED_CONFIG)
    nested_path = repo_path / "src" / "pkg"
    nested_path.mkdir(parents=True)
    monkeypatch.chdir(nested_path)

    assert main(["config", "migrate"]) == 0

    assert (repo_path / ".iar.toml").read_text(encoding="utf-8") == CONFIG_WITHOUT_PINS


def test_config_migrate_reports_pins_it_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """值与旧脚手架相同但有意保留的钉子：报告里带上原因，配置里原样留着。"""
    repo_path = _init_git_repository(
        tmp_path,
        "target",
        """\
[agent_runner.repository]
id = "target-local"

[agent_runner.generated_content.draft_pr]
mode = "template"
body_template = "custom body"
timeout_seconds = 120
""",
    )

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 0

    captured_output = capsys.readouterr().out
    assert "Kept 1 value(s)" in captured_output
    assert "custom title_template / body_template" in captured_output
    migrated_text = (repo_path / ".iar.toml").read_text(encoding="utf-8")
    assert 'mode = "template"' in migrated_text
    assert "timeout_seconds" not in migrated_text


def test_config_migrate_requires_an_initialized_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """没有 .iar.toml：退出码 1，并给出统一的 `iar init` 提示。"""
    repo_path = _init_git_repository(tmp_path, "target")

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 1

    error_output = capsys.readouterr().err
    assert "not initialized" in error_output
    assert "iar init" in error_output


def test_config_migrate_fails_outside_a_git_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--repo 指向非 Git 目录：退出码 1，报出原因。"""
    plain_directory = tmp_path / "plain"
    plain_directory.mkdir()

    assert main(["config", "migrate", "--repo", str(plain_directory)]) == 1

    assert "not inside a Git repository" in capsys.readouterr().err


def test_config_migrate_rejects_repo_id(capsys: pytest.CaptureFixture[str]) -> None:
    """迁移只针对单个仓库的 .iar.toml，不接受注册表 ID。"""
    assert main(["config", "migrate", "--repo-id", "target"]) == 1

    assert "--repo-id is not supported" in capsys.readouterr().err


def test_config_migrate_reports_invalid_toml_without_touching_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """配置不是合法 TOML：退出码 1，文件不动，错误正文里的方括号不吞字。"""
    broken_text = "[agent_runner\nbroken = "
    repo_path = _init_git_repository(tmp_path, "target", broken_text)

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 1

    assert "not valid TOML" in capsys.readouterr().err
    assert (repo_path / ".iar.toml").read_text(encoding="utf-8") == broken_text


def test_config_command_is_registered_on_typer_and_argparse() -> None:
    """Typer 与遗留 argparse 解析器都注册了 `config migrate`，分发键一致。"""
    registered_subapps = {info.typer_instance for info in app.registered_groups}
    assert config_app in registered_subapps
    assert [command.name for command in config_app.registered_commands] == ["migrate"]

    parsed_arguments = build_parser().parse_args(["config", "migrate", "--dry-run"])
    assert parsed_arguments.command == "config migrate"
    assert parsed_arguments.dry_run is True

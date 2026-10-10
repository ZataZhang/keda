"""Tests for the ``kc config migrate`` CLI command.

覆盖两步：本机状态目录搬迁（``~/.iar`` → ``~/.kedacode``）与仓库配置文件改名 +
旧钉住值清理。家目录被隔离到临时目录，CLI 测试把进程扫描明确注入为空闲结果；占用与
scanner unavailable 场景分别用锁文件和专门的负控覆盖，不依赖宿主机进程枚举权限。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.api.cli import main
from backend.api.cli_parser import build_parser
from backend.api.cli_typer_app import app, config_app
from backend.core.shared.models import product_identity
from backend.engines.agent_runner import state_home_migration
from tests.support.agent_runner import init_git_repo

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
def _isolated_global_config_and_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离配置、家目录和进程扫描，真实机器状态不会影响 CLI 断言。"""
    isolated_config_path = tmp_path / "isolated-config.toml"
    isolated_config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv(product_identity.LEGACY_ENV_PREFIX + "CONFIG", str(isolated_config_path))
    fake_home_path = tmp_path / "home"
    fake_home_path.mkdir()
    monkeypatch.setenv("HOME", str(fake_home_path))
    monkeypatch.setattr(state_home_migration, "_pids_from_command_scan", lambda _home: frozenset())
    return fake_home_path


def _init_git_repository(tmp_path: Path, name: str, config_text: str | None = None) -> Path:
    repo_path = init_git_repo(tmp_path / name)
    if config_text is not None:
        (repo_path / product_identity.LEGACY_REPOSITORY_CONFIG_FILENAME).write_text(
            config_text, encoding="utf-8"
        )
    return repo_path


def _make_legacy_state_dir(home_path: Path) -> Path:
    """在隔离家目录里造一个有内容的旧状态目录。"""
    legacy_state_path = home_path / product_identity.LEGACY_STATE_DIR_NAME
    legacy_state_path.mkdir(parents=True, exist_ok=True)
    (legacy_state_path / "config.toml").write_text(
        '[agent_runner.console]\nhistory_db_path = "~/.iar/console.db"\n', encoding="utf-8"
    )
    return legacy_state_path


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
    assert "would rename .iar.toml to .kedacode.toml" in captured_output
    assert "Dry run: nothing was written." in captured_output
    assert (repo_path / ".iar.toml").read_text(encoding="utf-8") == LEGACY_PINNED_CONFIG
    assert not (repo_path / ".kedacode.toml").exists()


def test_config_migrate_renames_the_config_and_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """真实迁移把配置文件改名并写回；再跑一次报告已是新名、无事可做，文件不再变化。"""
    repo_path = _init_git_repository(tmp_path, "target", LEGACY_PINNED_CONFIG)
    legacy_config_path = repo_path / ".iar.toml"
    new_config_path = repo_path / ".kedacode.toml"

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 0

    first_output = capsys.readouterr().out
    assert "Removed 5 value(s)" in first_output
    assert "renamed .iar.toml to .kedacode.toml" in first_output
    assert "commit the rename" in first_output
    # 长路径不能被折行：测试临时目录本身就比 80 列宽。
    assert f"Wrote migrated config: {new_config_path.resolve()}" in first_output
    assert not legacy_config_path.exists()
    assert new_config_path.read_text(encoding="utf-8") == CONFIG_WITHOUT_PINS

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 0

    second_output = capsys.readouterr().out
    assert "already named .kedacode.toml" in second_output
    assert "Nothing to migrate" in second_output
    assert new_config_path.read_text(encoding="utf-8") == CONFIG_WITHOUT_PINS


def test_config_migrate_defaults_to_the_current_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不带 --repo 时迁移 cwd 所在的 Git 仓库（子目录里也一样）。"""
    repo_path = _init_git_repository(tmp_path, "target", LEGACY_PINNED_CONFIG)
    nested_path = repo_path / "src" / "pkg"
    nested_path.mkdir(parents=True)
    monkeypatch.chdir(nested_path)

    assert main(["config", "migrate"]) == 0

    assert not (repo_path / ".iar.toml").exists()
    assert (repo_path / ".kedacode.toml").read_text(encoding="utf-8") == CONFIG_WITHOUT_PINS


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
    migrated_text = (repo_path / ".kedacode.toml").read_text(encoding="utf-8")
    assert 'mode = "template"' in migrated_text
    assert "timeout_seconds" not in migrated_text


@pytest.mark.parametrize("dry_run", [False, True])
def test_config_migrate_skips_an_uninitialized_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], dry_run: bool
) -> None:
    """没有仓库本地配置时跳过仓库清理，本机步骤成功仍正常退出。"""
    repo_path = _init_git_repository(tmp_path, "target")
    command_args = ["config", "migrate", "--repo", str(repo_path)]
    if dry_run:
        command_args.append("--dry-run")

    assert main(command_args) == 0

    captured = capsys.readouterr()
    assert "Skipping repository cleanup" in captured.out
    assert "kc init" in captured.out
    assert "not initialized" not in captured.err
    assert not (repo_path / ".kedacode.toml").exists()


def test_config_migrate_fails_outside_a_git_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--repo 指向非 Git 目录：退出码 3（找不到目标），报出原因。"""
    plain_directory = tmp_path / "plain"
    plain_directory.mkdir()

    assert main(["config", "migrate", "--repo", str(plain_directory)]) == 3

    assert "not inside a Git repository" in capsys.readouterr().err


def test_config_migrate_outside_a_repository_handles_the_local_step_only(
    tmp_path: Path,
    _isolated_global_config_and_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """不在仓库里隐式执行：只搬本机状态目录，退出码 0 并说明仓库步骤留待以后。"""
    legacy_state_path = _make_legacy_state_dir(_isolated_global_config_and_home)
    outside_path = tmp_path / "outside"
    outside_path.mkdir()
    monkeypatch.chdir(outside_path)

    assert main(["config", "migrate"]) == 0

    captured_output = capsys.readouterr().out
    assert "Not inside a repository" in captured_output
    new_state_path = _isolated_global_config_and_home / product_identity.STATE_DIR_NAME
    assert new_state_path.is_dir()
    assert legacy_state_path.is_symlink()


def test_config_migrate_rejects_repo_id(capsys: pytest.CaptureFixture[str]) -> None:
    """迁移只针对单个仓库的 .kedacode.toml 加本机状态目录，不接受注册表 ID（用法错误 2）。"""
    assert main(["config", "migrate", "--repo-id", "target"]) == 2

    # readouterr() 会清空缓冲，一次读完再断言。
    error_output = capsys.readouterr().err
    assert "--repo-id is not supported" in error_output
    assert "kc config migrate --repo ." in error_output


def test_config_migrate_reports_invalid_toml_without_touching_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """配置不是合法 TOML：退出码 1，文件不动，错误正文里的方括号不吞字。"""
    broken_text = "[agent_runner\nbroken = "
    repo_path = _init_git_repository(tmp_path, "target", broken_text)

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 1

    assert "not valid TOML" in capsys.readouterr().err
    assert (repo_path / ".kedacode.toml").read_text(encoding="utf-8") == broken_text


def test_config_migrate_moves_the_state_home_and_leaves_a_link(
    tmp_path: Path,
    _isolated_global_config_and_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """仓库内执行时两步都做：状态目录搬走、旧位置成链接、仓库配置改名。"""
    home_path = _isolated_global_config_and_home
    legacy_state_path = _make_legacy_state_dir(home_path)
    repo_path = _init_git_repository(tmp_path, "target", CONFIG_WITHOUT_PINS)

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 0

    new_state_path = home_path / product_identity.STATE_DIR_NAME
    assert new_state_path.is_dir() and not new_state_path.is_symlink()
    assert legacy_state_path.is_symlink()
    assert os.readlink(legacy_state_path) == product_identity.STATE_DIR_NAME
    assert (new_state_path / "config.toml").read_text(encoding="utf-8").count("~/.kedacode") == 1
    assert (repo_path / ".kedacode.toml").is_file()
    captured_output = capsys.readouterr().out
    assert "moved" in captured_output
    assert "renamed .iar.toml to .kedacode.toml" in captured_output


def test_config_migrate_refuses_while_a_runner_is_alive(
    tmp_path: Path,
    _isolated_global_config_and_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """锁文件里存活的本机进程：退出码 5，列出 PID，状态目录与仓库配置都不动。

    预演与正式执行共用同一套预检，因此两种模式下结论一致。
    """
    home_path = _isolated_global_config_and_home
    legacy_state_path = _make_legacy_state_dir(home_path)
    lock_dir_path = legacy_state_path / "daemon-locks"
    lock_dir_path.mkdir()
    owner_pid = os.getpid()
    (lock_dir_path / "demo.lock").write_text(f"{owner_pid}\n", encoding="utf-8")
    repo_path = _init_git_repository(tmp_path, "target", CONFIG_WITHOUT_PINS)

    for arguments in (["--dry-run"], []):
        assert main(["config", "migrate", "--repo", str(repo_path), *arguments]) == 5

        captured_output = capsys.readouterr().out
        assert "Refusing to migrate the local state directory" in captured_output
        assert str(owner_pid) in captured_output

    assert legacy_state_path.is_dir() and not legacy_state_path.is_symlink()
    assert not (home_path / product_identity.STATE_DIR_NAME).exists()
    assert (repo_path / ".iar.toml").is_file()


def test_config_migrate_refuses_when_process_scanner_is_unavailable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scanner unavailable 必须从真实 CLI 入口 fail-closed，且不改仓库配置。"""
    monkeypatch.setattr(state_home_migration, "_pids_from_command_scan", lambda _home: None)
    repo_path = _init_git_repository(tmp_path, "target", CONFIG_WITHOUT_PINS)

    exit_code = main(["config", "migrate", "--dry-run", "--repo", str(repo_path)])

    assert exit_code == 1
    assert "process scanner unavailable" in capsys.readouterr().out
    assert (repo_path / ".iar.toml").is_file()
    assert not (repo_path / ".kedacode.toml").exists()


def test_config_migrate_refuses_when_both_config_names_exist(
    tmp_path: Path,
    _isolated_global_config_and_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """仓库里新旧两个配置并存：冲突 5，两份都不动，本机状态目录也不搬。"""
    home_path = _isolated_global_config_and_home
    legacy_state_path = _make_legacy_state_dir(home_path)
    repo_path = _init_git_repository(tmp_path, "target", CONFIG_WITHOUT_PINS)
    (repo_path / ".kedacode.toml").write_text(
        '[agent_runner.repository]\nid = "from-new"\n', encoding="utf-8"
    )

    assert main(["config", "migrate", "--repo", str(repo_path)]) == 5

    captured_output = capsys.readouterr().out
    assert "Refusing to migrate" in captured_output
    assert "both .iar.toml and .kedacode.toml exist" in captured_output
    assert legacy_state_path.is_dir() and not legacy_state_path.is_symlink()
    assert (repo_path / ".iar.toml").read_text(encoding="utf-8") == CONFIG_WITHOUT_PINS


def test_config_command_is_registered_on_typer_and_argparse() -> None:
    """Typer 与遗留 argparse 解析器都注册了 `config migrate`，分发键一致。"""
    registered_subapps = {info.typer_instance for info in app.registered_groups}
    assert config_app in registered_subapps
    assert [command.name for command in config_app.registered_commands] == ["migrate"]

    parsed_arguments = build_parser().parse_args(["config", "migrate", "--dry-run"])
    assert parsed_arguments.command == "config migrate"
    assert parsed_arguments.dry_run is True

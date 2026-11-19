"""个人覆盖 ``.kedacode.local.toml`` 的加载、合并与 worktree 回落测试。

配置值一律来自真实 TOML 文件，经与生产相同的加载器 / 仓库解析入口读回，
不直接构造合并结果。
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from backend.engines.agent_runner.factory import resolve_repository_targets
from backend.engines.agent_runner.repository_gitignore import (
    GitignoreSyncOptions,
    ensure_gitignore_entries,
)
from backend.infrastructure.config import settings_sources
from backend.infrastructure.config.agent_runner_settings import load_agent_runner_local_settings
from backend.infrastructure.config.settings import AgentRunnerSettings

TEAM_CONFIG_TOML = """
[agent_runner.repository]
id = "team-repo"

[agent_runner.runner]
agent_fallback_order = ["claude", "kimi", "codex"]
max_agent_switches = 2

[agent_runner.validation]
verifier_enabled = true
verifier_agent = "codex"

[agent_runner.pre_pr_review]
review_agent = "codex"
"""

PERSONAL_OVERRIDE_TOML = """
[agent_runner.runner]
agent_fallback_order = ["claude", "qoder"]

[agent_runner.validation]
verifier_agent = "claude"
"""


def _run_git(repo_path: Path, *git_args: str) -> None:
    subprocess.run(["git", *git_args], cwd=repo_path, check=True, capture_output=True)


def _init_committed_repository(tmp_path: Path) -> Path:
    """建一个带首个提交的 git 仓库，``.kedacode.toml`` 已提交。"""
    repo_path = tmp_path / "main-checkout"
    repo_path.mkdir()
    _run_git(repo_path, "init", "-b", "main")
    (repo_path / ".kedacode.toml").write_text(TEAM_CONFIG_TOML, encoding="utf-8")
    _run_git(repo_path, "add", ".kedacode.toml")
    _run_git(
        repo_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-m",
        "init",
    )
    return repo_path


@pytest.fixture
def isolated_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把全局 config.toml 指向空的 tmp 文件，隔离开发者本机配置。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")

    def _load_section(section_name: str) -> dict:
        return tomllib.loads(config_path.read_text(encoding="utf-8")).get(section_name, {})

    monkeypatch.setattr(settings_sources, "_load_toml_section_data", _load_section)
    monkeypatch.setattr(settings_sources, "_load_registry_toml_section_data", lambda _name: {})


def test_local_override_merges_tables_and_replaces_arrays(tmp_path: Path) -> None:
    """表按键合并（未覆盖的兄弟键保留），数组整体替换。"""
    (tmp_path / ".kedacode.toml").write_text(TEAM_CONFIG_TOML, encoding="utf-8")
    (tmp_path / ".kedacode.local.toml").write_text(PERSONAL_OVERRIDE_TOML, encoding="utf-8")

    repository_settings = load_agent_runner_local_settings(tmp_path)

    assert repository_settings is not None
    assert repository_settings.validation.verifier_agent == "claude"
    assert repository_settings.validation.verifier_enabled is True
    assert repository_settings.runner.agent_fallback_order == ["claude", "qoder"]
    assert repository_settings.runner.max_agent_switches == 2
    assert repository_settings.pre_pr_review.review_agent == "codex"
    assert repository_settings.id == "team-repo"


def test_local_override_cannot_change_repository_identity(tmp_path: Path) -> None:
    """个人覆盖改写 ``[agent_runner.repository]`` 身份段时 fail-fast。"""
    (tmp_path / ".kedacode.toml").write_text(TEAM_CONFIG_TOML, encoding="utf-8")
    (tmp_path / ".kedacode.local.toml").write_text(
        '[agent_runner.repository]\nid = "hijacked"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="identity cannot be overridden locally"):
        load_agent_runner_local_settings(tmp_path)


def test_local_override_without_team_config_is_ignored(tmp_path: Path) -> None:
    """未初始化（没有 ``.kedacode.toml``）的目录不因个人覆盖而被当成已初始化仓库。"""
    (tmp_path / ".kedacode.local.toml").write_text(PERSONAL_OVERRIDE_TOML, encoding="utf-8")

    assert load_agent_runner_local_settings(tmp_path) is None


def test_linked_worktree_falls_back_to_main_checkout_override(tmp_path: Path) -> None:
    """worktree 内没有个人覆盖时回落主检出的同名文件；worktree 自带的优先。"""
    main_checkout_path = _init_committed_repository(tmp_path)
    worktree_path = tmp_path / "linked-worktree"
    _run_git(main_checkout_path, "worktree", "add", "-b", "feature", str(worktree_path))
    (main_checkout_path / ".kedacode.local.toml").write_text(
        PERSONAL_OVERRIDE_TOML, encoding="utf-8"
    )

    inherited_settings = load_agent_runner_local_settings(worktree_path)
    assert inherited_settings is not None
    assert inherited_settings.validation.verifier_agent == "claude"

    (worktree_path / ".kedacode.local.toml").write_text(
        '[agent_runner.validation]\nverifier_agent = "kimi"\n', encoding="utf-8"
    )
    worktree_own_settings = load_agent_runner_local_settings(worktree_path)
    assert worktree_own_settings is not None
    assert worktree_own_settings.validation.verifier_agent == "kimi"


@pytest.mark.usefixtures("isolated_global_config")
def test_resolved_repository_context_applies_personal_override(tmp_path: Path) -> None:
    """经 ``resolve_repository_targets`` 真实入口：有覆盖时生效，删掉覆盖即回到团队值。"""
    repo_path = _init_committed_repository(tmp_path)

    team_config = resolve_repository_targets(
        AgentRunnerSettings(), repo_path_override=str(repo_path)
    )[0].config
    assert team_config.validation.verifier_agent == "codex"

    (repo_path / ".kedacode.local.toml").write_text(PERSONAL_OVERRIDE_TOML, encoding="utf-8")
    personal_config = resolve_repository_targets(
        AgentRunnerSettings(), repo_path_override=str(repo_path)
    )[0].config
    assert personal_config.validation.verifier_agent == "claude"
    assert personal_config.pre_pr_review.review_agent == "codex"


def test_kc_init_gitignore_block_ignores_personal_override(tmp_path: Path) -> None:
    """``kc init`` 的托管 ``.gitignore`` 块包含个人覆盖文件，避免误提交。"""
    ensure_gitignore_entries(GitignoreSyncOptions(repo_root_path=tmp_path))

    gitignore_lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".kedacode.local.toml" in gitignore_lines

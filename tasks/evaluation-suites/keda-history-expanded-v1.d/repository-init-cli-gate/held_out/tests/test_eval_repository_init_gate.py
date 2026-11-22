"""通过真实 iar CLI 和 Git 仓库验证初始化门槛。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.keda_eval_historical_cli import _run

pytest_plugins = ("tests.keda_eval_historical_cli",)


@pytest.mark.parametrize(
    "command",
    (
        ("labels", "sync"),
        ("run", "--dry-run"),
        ("issue", "create", "task.md"),
        ("worktree", "create", "--branch", "sandbox-check", "--base-branch", "main"),
    ),
)
def test_uninitialized_commands_explain_setup(
    isolated_repo: tuple[Path, dict[str, str]], command: tuple[str, ...]
) -> None:
    repo, environment = isolated_repo
    code, output = _run(repo, environment, *command)
    assert code != 0
    assert "iar init" in output.lower(), output
    assert "traceback" not in output.lower()
    assert "unexpected gh call" not in output
    assert not (repo / ".iar.toml").exists()


def test_init_dry_run_and_help_remain_reachable(isolated_repo: tuple[Path, dict[str, str]]) -> None:
    repo, environment = isolated_repo
    for args in (("--help",), ("init", "--dry-run"), ("run", "--help")):
        code, output = _run(repo, environment, *args)
        assert code == 0, output
    assert not (repo / ".iar.toml").exists()


def test_initialized_repository_selected_at_root_does_not_fail_initialization(
    isolated_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, environment = isolated_repo
    code, output = _run(repo, environment, "init")
    assert code == 0, output
    code, output = _run(
        repo.parent, environment, "--repo", str(repo), "run", "--dry-run", "--agent", "codex"
    )
    assert code == 0, output

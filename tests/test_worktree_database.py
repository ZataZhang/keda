"""Tests for daemon worktree database provisioning."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.worktree_database import (
    WorktreeDatabaseProvisionRequest,
    provision_worktree_database,
)


class FakeProcessRunner:
    """记录数据库准备命令并返回预设结果。"""

    def __init__(self, command_results: list[CommandResult]) -> None:
        self._command_results = command_results
        self.commands: list[list[str]] = []
        self.working_directories: list[Path] = []

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        check: bool = True,
        **_: object,
    ) -> CommandResult:
        self.commands.append(command)
        self.working_directories.append(cwd)
        return self._command_results.pop(0)


def _command_result(return_code: int = 0) -> CommandResult:
    return CommandResult(command=(), return_code=return_code, stdout="output", stderr="error")


def _make_repo_with_script(tmp_path: Path, issue_number: int) -> tuple[Path, Path]:
    """构造带建库脚本与 alembic.ini 的仓库/worktree 骨架。"""
    repository_path = tmp_path / "keda"
    worktree_path = repository_path / ".iar-worktrees" / f"issue-{issue_number}"
    database_script_path = repository_path / "scripts" / "shared" / "template"
    database_script_path.mkdir(parents=True)
    (database_script_path / "setup_copied_database.py").write_text("", encoding="utf-8")
    worktree_path.mkdir(parents=True)
    (worktree_path / "alembic.ini").write_text("", encoding="utf-8")
    return repository_path, worktree_path


def test_provision_worktree_database_runs_setup_and_migration(tmp_path: Path) -> None:
    """Daemon provisioning creates the isolated database before Alembic migration."""
    repository_path, worktree_path = _make_repo_with_script(tmp_path, 42)
    database_script_path = repository_path / "scripts" / "shared" / "template"
    process_runner = FakeProcessRunner([_command_result(), _command_result()])

    provision_worktree_database(
        WorktreeDatabaseProvisionRequest(repository_path, worktree_path, issue_number=42),
        process_runner,
    )

    assert process_runner.commands[0][:4] == [
        "uv",
        "run",
        "python",
        str(database_script_path / "setup_copied_database.py"),
    ]
    assert process_runner.commands[0][-1] == "--strict"
    assert process_runner.commands[1] == ["uv", "run", "alembic", "upgrade", "head"]


def test_provision_worktree_database_skips_when_template_script_missing(tmp_path: Path) -> None:
    """缺 synced helper 时回退:跳过建库,不阻断 worktree 创建(不 raise)。"""
    repository_path = tmp_path / "keda"
    worktree_path = repository_path / ".iar-worktrees" / "issue-7"
    worktree_path.mkdir(parents=True)
    process_runner = FakeProcessRunner([])

    provision_worktree_database(
        WorktreeDatabaseProvisionRequest(repository_path, worktree_path, issue_number=7),
        process_runner,
    )

    assert process_runner.commands == []


def test_provision_worktree_database_skips_when_setup_fails(tmp_path: Path) -> None:
    """建库脚本失败时回退:不 raise、不执行迁移。"""
    repository_path, worktree_path = _make_repo_with_script(tmp_path, 9)
    process_runner = FakeProcessRunner([_command_result(return_code=1)])

    provision_worktree_database(
        WorktreeDatabaseProvisionRequest(repository_path, worktree_path, issue_number=9),
        process_runner,
    )

    assert len(process_runner.commands) == 1
    assert process_runner.commands[0][:3] == ["uv", "run", "python"]


def test_configured_worktree_migration_reuses_setup_identifier(tmp_path: Path) -> None:
    """自定义命令在建库后取得同一标识，且可用于无 Alembic 配置的项目。"""
    repository_path, worktree_path = _make_repo_with_script(tmp_path, 42)
    (worktree_path / "alembic.ini").unlink()
    process_runner = FakeProcessRunner([_command_result(), _command_result()])

    provision_worktree_database(
        WorktreeDatabaseProvisionRequest(
            repository_path,
            worktree_path,
            issue_number=42,
            migration_command=("migration", "{database_identifier}", "one argument"),
        ),
        process_runner,
    )

    digest = sha256(str(repository_path.resolve()).encode("utf-8")).hexdigest()[:8]
    assert process_runner.commands[0][4] == f"keda_kc_issue_42_{digest}"
    assert process_runner.commands[1] == [
        "migration",
        process_runner.commands[0][4],
        "one argument",
    ]
    assert process_runner.working_directories == [worktree_path, worktree_path]


@pytest.mark.parametrize(
    ("exit_codes", "failure_message"),
    [
        ([7], "database setup failed"),
        ([0, 9], "database migration failed"),
    ],
)
def test_configured_worktree_migration_stops_preparation_on_failure(
    tmp_path: Path, exit_codes: list[int], failure_message: str
) -> None:
    """显式配置的仓库在建库或迁移失败时停止，异常只呈递阶段与退出码。"""
    repository_path, worktree_path = _make_repo_with_script(tmp_path, 42)
    process_runner = FakeProcessRunner([_command_result(code) for code in exit_codes])

    with pytest.raises(RuntimeError, match=f"{failure_message}.*exit code {exit_codes[-1]}"):
        provision_worktree_database(
            WorktreeDatabaseProvisionRequest(
                repository_path, worktree_path, issue_number=42, migration_command=("migration",)
            ),
            process_runner,
        )

    assert len(process_runner.commands) == len(exit_codes)

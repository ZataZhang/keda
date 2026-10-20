"""Tests for the CLI argument parsing and dispatch logic."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

import backend.api.cli_registry as cli_registry
from backend.api.cli import _expand_prd_paths, main
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import OUTPUT_FORMAT_TABLE, CliError
from backend.api.cli_parsed_commands.agent import (
    run_agent_fallback_candidate_add_command,
    run_agent_lifecycle_set_command,
    run_agent_lifecycle_unset_command,
)
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_schema import build_command_schema
from backend.api.cli_parser import build_parser
from backend.api.cli_parsed_commands.runner import _validate_aggregate_run_options
from backend.api.cli_typer_app import app as typer_app
from backend.core.use_cases.agent_runner_batch_aggregate import (
    validate_aggregate_candidate_count,
    validate_aggregate_issue_numbers,
)
from backend.core.shared.interfaces.runner_console import (
    RunnerProcessKind,
    RunnerProcessRecord,
)
from backend.core.shared.models.agent_spec import BUILTIN_AGENT_SPECS
from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
from backend.engines.agent_runner.repository_local import (
    IARRepositoryNotInitializedError,
)
from backend.infrastructure.console.process_supervisor import PidfileProcessSupervisor
from backend.infrastructure.logging.logger import Logger


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    """Remove terminal color/control sequences from captured CLI output."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _reset_logger_singleton() -> None:
    """Reset Logger singleton state for test isolation."""
    if Logger._logger is not None:
        for handler in Logger._logger.handlers[:]:
            handler.close()
            Logger._logger.removeHandler(handler)
    Logger._instance = None
    Logger._logger = None


def test_cli_parser_labels_sync() -> None:
    """labels sync subcommand should be recognized."""
    parser = build_parser()
    parsed = parser.parse_args(["labels", "sync"])
    assert parsed.command == "labels"
    assert parsed.labels_command == "sync"


def test_cli_parser_aggregate_pr_is_opt_in_and_requires_all_ready() -> None:
    """argparse parser 注册默认关闭的聚合开关与手动入口。"""
    parser = build_parser()
    default_run = parser.parse_args(["run", "--all-ready"])
    aggregate_run = parser.parse_args(["run", "--all-ready", "--aggregate-pr", "--max-issues", "2"])
    manual_aggregate = parser.parse_args(["pr", "aggregate", "--issue", "101", "--issue", "102"])
    assert default_run.aggregate_pr is False
    assert aggregate_run.aggregate_pr is True
    assert aggregate_run.all_ready is True
    assert manual_aggregate.command == "pr aggregate"
    assert manual_aggregate.issues == [101, 102]


def test_typer_schema_exposes_aggregate_commands() -> None:
    """机器 schema 从 Typer 命令树导出两种入口及其默认 / 多选语义。"""
    schema = build_command_schema(typer_app)
    command_by_name = {command["name"]: command for command in schema["commands"]}
    run_options = {option["name"]: option for option in command_by_name["run"]["options"]}
    aggregate_options = {
        option["name"]: option for option in command_by_name["pr aggregate"]["options"]
    }
    assert run_options["--aggregate-pr"]["default"] is False
    assert aggregate_options["--issue"]["multiple"] is True
    assert aggregate_options["--issue"]["required"] is True


def test_aggregate_cli_local_validation_rejects_invalid_combinations() -> None:
    """無倉庫或外部 client 的本地驗證拒絕 bypass 與單 Issue 配額。"""
    parser = build_parser()
    valid = parser.parse_args(["run", "--all-ready", "--aggregate-pr", "--max-issues", "2"])
    _validate_aggregate_run_options(valid, configured_max_issues=1)
    for arguments in (
        ["run", "--aggregate-pr", "--max-issues", "2"],
        ["run", "--all-ready", "--aggregate-pr", "--fast-merge", "--max-issues", "2"],
        ["run", "--all-ready", "--aggregate-pr", "--direct-pr", "--max-issues", "2"],
        ["run", "--all-ready", "--aggregate-pr", "--max-issues", "1"],
    ):
        parsed = parser.parse_args(arguments)
        with pytest.raises(CliError):
            _validate_aggregate_run_options(parsed, configured_max_issues=1)


def test_aggregate_issue_and_candidate_count_validation_is_local() -> None:
    """手动集合与自动实际候选数的纯本地校验都拒绝单成员批次。"""
    assert validate_aggregate_issue_numbers([101, 102]) == (101, 102)
    with pytest.raises(ValueError, match="at least 2"):
        validate_aggregate_issue_numbers([101])
    assert validate_aggregate_candidate_count(2) == 2
    with pytest.raises(ValueError, match="at least 2"):
        validate_aggregate_candidate_count(1)


def test_cli_parser_init() -> None:
    """init should accept repository-local config options."""
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "init",
            "--dry-run",
            "--force",
            "--id",
            "target",
            "--display-name",
            "Target",
            "--remote",
            "upstream",
            "--base-branch",
            "develop",
        ]
    )
    assert parsed.command == "init"
    assert parsed.dry_run is True
    assert parsed.force is True
    assert parsed.repository_id == "target"
    assert parsed.display_name == "Target"
    assert parsed.remote == "upstream"
    assert parsed.base_branch == "develop"


def test_cli_parser_issue_create_defaults() -> None:
    """issue create should have sensible defaults."""
    parser = build_parser()
    parsed = parser.parse_args(["issue", "create", "tasks/example.md"])
    assert parsed.command == "issue create"
    assert parsed.issue_command == "create"
    assert parsed.prd_paths == ["tasks/example.md"]
    assert parsed.type == "feature"
    # Issue 默认入队（--ready 为默认），用户可用 --no-ready 显式退出。
    assert parsed.ready is True
    assert parsed.agent == "auto"
    # --publish-prd / --force 是三态：None 表示「未给出」，由 handler 归一为默认值。
    # `--from-prompt` 需要区分「没提」与「显式给出」（后者是矛盾指令，必须报错）。
    assert parsed.publish_prd is None
    assert parsed.force is None
    assert parsed.from_prompt is None
    assert parsed.require_validation is False


def test_cli_parser_issue_create_multiple_paths() -> None:
    """issue create should accept multiple PRD paths."""
    parser = build_parser()
    parsed = parser.parse_args(["issue", "create", "tasks/a.md", "tasks/b.md", "--ready"])
    assert parsed.command == "issue create"
    assert parsed.issue_command == "create"
    assert parsed.prd_paths == ["tasks/a.md", "tasks/b.md"]
    assert parsed.ready is True


def test_cli_parser_issue_create_publish_prd() -> None:
    """issue create should expose explicit PRD publishing."""
    parser = build_parser()
    parsed = parser.parse_args(
        ["issue", "create", "tasks/example.md", "--publish-prd", "--no-ready"]
    )
    assert parsed.command == "issue create"
    assert parsed.issue_command == "create"
    assert parsed.publish_prd is True
    assert parsed.ready is False


def test_cli_parser_issue_create_no_publish_prd() -> None:
    """issue create should allow opting out of the default PRD publishing."""
    parser = build_parser()
    parsed = parser.parse_args(["issue", "create", "tasks/example.md", "--no-publish-prd"])
    assert parsed.command == "issue create"
    assert parsed.issue_command == "create"
    assert parsed.publish_prd is False


def test_cli_parser_issue_create_dependency_options() -> None:
    """issue create should accept dependency gate options."""
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "issue",
            "create",
            "tasks/example.md",
            "--depends-on",
            "42",
            "--depends-on",
            "43",
        ]
    )
    assert parsed.depends_on == [42, 43]


def test_cli_parser_issue_create_accepts_directory() -> None:
    """issue create should accept a directory as a PRD path argument."""
    parser = build_parser()
    parsed = parser.parse_args(["issue", "create", "tasks/pending"])
    assert parsed.command == "issue create"
    assert parsed.issue_command == "create"
    assert parsed.prd_paths == ["tasks/pending"]


def test_expand_prd_paths_directory(tmp_path: Path) -> None:
    """_expand_prd_paths should expand a directory to its *.md files sorted."""
    (tmp_path / "tasks" / "pending").mkdir(parents=True)
    (tmp_path / "tasks" / "pending" / "b.md").write_text("# B", encoding="utf-8")
    (tmp_path / "tasks" / "pending" / "a.md").write_text("# A", encoding="utf-8")
    (tmp_path / "tasks" / "pending" / ".gitkeep").write_text("", encoding="utf-8")

    expanded, skipped = _expand_prd_paths(tmp_path, ["tasks/pending"])

    assert expanded == ["tasks/pending/a.md", "tasks/pending/b.md"]
    assert skipped == []


def test_expand_prd_paths_mixed_file_and_directory(tmp_path: Path) -> None:
    """_expand_prd_paths should preserve input order when mixing files and dirs."""
    (tmp_path / "tasks" / "pending").mkdir(parents=True)
    (tmp_path / "tasks" / "pending" / "a.md").write_text("# A", encoding="utf-8")
    (tmp_path / "tasks" / "root.md").write_text("# Root", encoding="utf-8")

    expanded, skipped = _expand_prd_paths(tmp_path, ["tasks/root.md", "tasks/pending"])

    assert expanded == ["tasks/root.md", "tasks/pending/a.md"]
    assert skipped == []


def test_expand_prd_paths_deduplicates(tmp_path: Path) -> None:
    """_expand_prd_paths should deduplicate repeated files."""
    (tmp_path / "tasks" / "pending").mkdir(parents=True)
    (tmp_path / "tasks" / "pending" / "a.md").write_text("# A", encoding="utf-8")

    expanded, skipped = _expand_prd_paths(tmp_path, ["tasks/pending", "tasks/pending/a.md"])

    assert expanded == ["tasks/pending/a.md"]
    assert skipped == []


def test_expand_prd_paths_empty_directory_rejected(tmp_path: Path) -> None:
    """_expand_prd_paths should reject a directory with no *.md files."""
    (tmp_path / "tasks" / "pending").mkdir(parents=True)

    with pytest.raises(ValueError, match="contains no PRD Markdown files"):
        _expand_prd_paths(tmp_path, ["tasks/pending"])


def test_expand_prd_paths_missing_path_passed_through(tmp_path: Path) -> None:
    """_expand_prd_paths should pass through non-existent paths unchanged."""
    expanded, skipped = _expand_prd_paths(tmp_path, ["tasks/missing.md"])

    assert expanded == ["tasks/missing.md"]
    assert skipped == []


def test_expand_prd_paths_non_md_file_rejected(tmp_path: Path) -> None:
    """_expand_prd_paths should reject an existing non-Markdown file."""
    (tmp_path / "tasks").mkdir(parents=True)
    (tmp_path / "tasks" / "readme.txt").write_text("txt", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a Markdown file"):
        _expand_prd_paths(tmp_path, ["tasks/readme.txt"])


def test_expand_prd_paths_skips_linked_files_in_directory(
    tmp_path: Path,
) -> None:
    """Directory expansion should skip PRDs that already have an Issue link."""
    (tmp_path / "tasks" / "pending").mkdir(parents=True)
    (tmp_path / "tasks" / "pending" / "linked.md").write_text(
        "# Linked\n- GitHub Issue: https://github.com/org/repo/issues/1\n",
        encoding="utf-8",
    )
    (tmp_path / "tasks" / "pending" / "new.md").write_text("# New", encoding="utf-8")

    expanded, skipped = _expand_prd_paths(tmp_path, ["tasks/pending"])

    assert expanded == ["tasks/pending/new.md"]
    assert skipped == ["tasks/pending/linked.md"]


def test_expand_prd_paths_keeps_linked_file_when_explicitly_passed(
    tmp_path: Path,
) -> None:
    """Explicitly passed files with Issue links are not skipped."""
    (tmp_path / "tasks" / "pending").mkdir(parents=True)
    (tmp_path / "tasks" / "pending" / "linked.md").write_text(
        "# Linked\n- GitHub Issue: https://github.com/org/repo/issues/1\n",
        encoding="utf-8",
    )

    expanded, skipped = _expand_prd_paths(tmp_path, ["tasks/pending/linked.md"])

    assert expanded == ["tasks/pending/linked.md"]
    assert skipped == []


def test_expand_prd_paths_all_linked_in_directory_returns_empty(
    tmp_path: Path,
) -> None:
    """Directory with only linked PRDs returns empty expanded list."""
    (tmp_path / "tasks" / "pending").mkdir(parents=True)
    (tmp_path / "tasks" / "pending" / "linked.md").write_text(
        "# Linked\n- GitHub Issue: https://github.com/org/repo/issues/1\n",
        encoding="utf-8",
    )

    expanded, skipped = _expand_prd_paths(tmp_path, ["tasks/pending"])

    assert expanded == []
    assert skipped == ["tasks/pending/linked.md"]


def test_cli_parser_run() -> None:
    """run should accept dry-run and agent flags."""
    parser = build_parser()
    parsed = parser.parse_args(["run", "--dry-run", "--agent", "claude", "--max-issues", "5"])
    assert parsed.command == "run"
    assert parsed.dry_run is True
    assert parsed.agent == "claude"
    assert parsed.max_issues == 5


def test_cli_parser_worktree_cleanup() -> None:
    """worktree cleanup should expose dry-run, yes, and force flags."""
    parser = build_parser()
    parsed = parser.parse_args(["worktree", "cleanup", "--dry-run", "--force"])
    assert parsed.command == "worktree"
    assert parsed.worktree_command == "cleanup"
    assert parsed.dry_run is True
    assert parsed.yes is False
    assert parsed.force is True


def test_cli_parser_registry_scan() -> None:
    """registry scan should accept an optional scan root."""
    parser = build_parser()
    parsed = parser.parse_args(["registry", "scan", "/Users/me/code"])
    assert parsed.command == "registry"
    assert parsed.registry_command == "scan"
    assert parsed.scan_root == "/Users/me/code"


def test_cli_parser_registry_sync_defaults() -> None:
    """registry sync should default to current directory and not dry-run."""
    parser = build_parser()
    parsed = parser.parse_args(["registry", "sync"])
    assert parsed.command == "registry"
    assert parsed.registry_command == "sync"
    assert parsed.scan_root == "."
    assert parsed.dry_run is False


def test_cli_parser_registry_sync_dry_run() -> None:
    """registry sync should accept --dry-run."""
    parser = build_parser()
    parsed = parser.parse_args(["registry", "sync", "--dry-run", "/tmp"])
    assert parsed.command == "registry"
    assert parsed.registry_command == "sync"
    assert parsed.dry_run is True
    assert parsed.scan_root == "/tmp"


def test_cli_parser_registry_reinit() -> None:
    """registry reinit should accept repo-id, remote, base-branch, and start-daemons."""
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "registry",
            "reinit",
            "--repo-id",
            "zata-zhangtao-fsense",
            "--remote",
            "upstream",
            "--base-branch",
            "develop",
            "--start-daemons",
        ]
    )
    assert parsed.command == "registry"
    assert parsed.registry_command == "reinit"
    assert parsed.repo_id == "zata-zhangtao-fsense"
    assert parsed.remote == "upstream"
    assert parsed.base_branch == "develop"
    assert parsed.start_daemons is True


def test_cli_parser_registry_reinit_defaults() -> None:
    """registry reinit should default remote to origin and not start daemons."""
    parser = build_parser()
    parsed = parser.parse_args(["registry", "reinit", "--repo-id", "foo-bar"])
    assert parsed.remote == "origin"
    assert parsed.base_branch is None
    assert parsed.start_daemons is False


def test_cli_parser_registry_remove() -> None:
    """registry remove should accept repo-id and reject the dropped --delete flag."""
    parser = build_parser()
    parsed = parser.parse_args(["registry", "remove", "--repo-id", "zata-zhangtao-fsense"])
    assert parsed.command == "registry"
    assert parsed.registry_command == "remove"
    assert parsed.repo_id == "zata-zhangtao-fsense"
    assert not hasattr(parsed, "delete")
    with pytest.raises(SystemExit):
        parser.parse_args(["registry", "remove", "--repo-id", "zata-zhangtao-fsense", "--delete"])


def test_cli_parser_daemon() -> None:
    """daemon should accept interval and max-issues and default to run subcommand."""
    parser = build_parser()
    parsed = parser.parse_args(["daemon", "--interval", "300", "--max-issues", "2"])
    assert parsed.command == "daemon"
    assert parsed.daemon_command == "run"
    assert parsed.interval == 300
    assert parsed.max_issues == 2


def test_cli_parser_daemon_run_explicit() -> None:
    """daemon run should accept the same options as the legacy daemon form."""
    parser = build_parser()
    parsed = parser.parse_args(["daemon", "run", "--interval", "300", "--max-issues", "2"])
    assert parsed.command == "daemon"
    assert parsed.daemon_command == "run"
    assert parsed.interval == 300
    assert parsed.max_issues == 2


def test_cli_parser_daemon_status() -> None:
    """daemon status should accept repository selectors."""
    parser = build_parser()
    parsed = parser.parse_args(["daemon", "status", "--repo-id", "keda"])
    assert parsed.command == "daemon"
    assert parsed.daemon_command == "status"
    assert parsed.repo_id == "keda"


def test_cli_parser_daemon_default_interval_is_none() -> None:
    """daemon --interval should default to None so config supplies the value."""
    parser = build_parser()
    parsed = parser.parse_args(["daemon"])
    assert parsed.daemon_command == "run"
    assert parsed.interval is None


def test_cli_parser_review_daemon_default_interval_is_none() -> None:
    """review-daemon --interval should default to None so config supplies the value."""
    parser = build_parser()
    parsed = parser.parse_args(["review-daemon"])
    assert parsed.interval is None


def test_main_daemon_default_interval_uses_config(monkeypatch) -> None:
    """不传 --interval 时 daemon 使用配置的 120 秒默认值。"""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.run_agent_daemon") as mock_daemon,
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        # This test covers CLI config routing; single-instance locking has
        # dedicated tests and must not touch the host user's daemon directory.
        patch("backend.api.cli.acquire_daemon_locks", return_value=[]),
        patch("backend.api.cli.release_daemon_locks"),
    ):
        exit_code = main(["daemon", "--all"])

    assert exit_code == 0
    assert mock_daemon.call_args.kwargs["interval"] == 120


def test_main_review_daemon_default_interval_uses_config(monkeypatch) -> None:
    """Typer review-daemon without --interval should use the configured 120s default."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.run_review_daemon") as mock_daemon,
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["review-daemon", "--all"])

    assert exit_code == 0
    assert mock_daemon.call_args.kwargs["interval"] == 120


def test_main_daemon_interval_override(monkeypatch) -> None:
    """daemon 的 --interval 覆盖配置默认值。"""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.run_agent_daemon") as mock_daemon,
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch("backend.api.cli.acquire_daemon_locks", return_value=[]),
        patch("backend.api.cli.release_daemon_locks"),
    ):
        exit_code = main(["daemon", "--all", "--interval", "300"])

    assert exit_code == 0
    assert mock_daemon.call_args.kwargs["interval"] == 300


def test_main_daemon_status_shows_managed_and_unmanaged(capsys, monkeypatch) -> None:
    """daemon status should list running managed and unmanaged processes."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    managed_record = RunnerProcessRecord(
        process_id="abc123",
        repo_id="repo",
        kind=RunnerProcessKind.DAEMON,
        pid=1234,
        status="running",
        exit_code=None,
        log_path="/tmp/log",
        command=("kc", "daemon", "--repo-id", "repo"),
        started_at="2026-06-23T10:00:00Z",
        stopped_at=None,
    )
    unmanaged_record = RunnerProcessRecord(
        process_id="unmanaged-5678",
        repo_id="repo",
        kind=RunnerProcessKind.REVIEW_DAEMON,
        pid=5678,
        status="running",
        exit_code=None,
        log_path="",
        command=("/usr/bin/kc", "review-daemon", "--repo-id", "repo"),
        started_at="2026-06-23T11:00:00Z",
        stopped_at=None,
    )

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = [managed_record]
    mock_supervisor.list_unmanaged_processes.return_value = [unmanaged_record]

    mock_editor = MagicMock()
    mock_editor.list_repositories.return_value = []

    monkeypatch.setenv("COLUMNS", "200")

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
        patch(
            "backend.api.cli_registry.create_registry_editor",
            return_value=mock_editor,
        ),
    ):
        exit_code = main(["daemon", "status", "--repo-id", "repo"])

    captured = capsys.readouterr()
    output = _strip_ansi(captured.out)
    assert exit_code == 0
    assert "abc123" in output
    assert "managed running" in output
    assert "unmanaged-5678" in output
    assert "unmanaged running" in output
    assert "1234" in output
    assert "5678" in output
    assert "/usr/bin/kc" in output
    assert "log_path" in output
    assert "/tmp/log" in output


def test_main_daemon_status_shows_dash_for_unmanaged_log_path(capsys, monkeypatch) -> None:
    """Unmanaged processes with empty log_path should show '-' in the table."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    unmanaged_record = RunnerProcessRecord(
        process_id="unmanaged-9999",
        repo_id="repo",
        kind=RunnerProcessKind.DAEMON,
        pid=9999,
        status="running",
        exit_code=None,
        log_path="",
        command=("/usr/bin/kc", "daemon", "--repo-id", "repo"),
        started_at="2026-06-23T11:00:00Z",
        stopped_at=None,
    )

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = []
    mock_supervisor.list_unmanaged_processes.return_value = [unmanaged_record]

    mock_editor = MagicMock()
    mock_editor.list_repositories.return_value = []

    monkeypatch.setenv("COLUMNS", "200")

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
        patch(
            "backend.api.cli_registry.create_registry_editor",
            return_value=mock_editor,
        ),
    ):
        exit_code = main(["daemon", "status", "--repo-id", "repo"])

    captured = capsys.readouterr()
    output = _strip_ansi(captured.out)
    assert exit_code == 0
    assert "log_path" in output
    assert "-" in output


def test_main_daemon_status_empty(capsys, monkeypatch) -> None:
    """daemon status should report when no processes are running."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = []
    mock_supervisor.list_unmanaged_processes.return_value = []

    mock_editor = MagicMock()
    mock_editor.list_repositories.return_value = []

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
        patch(
            "backend.api.cli_registry.create_registry_editor",
            return_value=mock_editor,
        ),
    ):
        exit_code = main(["daemon", "status", "--repo-id", "repo"])

    captured = capsys.readouterr()
    output = _strip_ansi(captured.out)
    assert exit_code == 0
    assert "No running daemon processes" in output


def test_cli_parser_repo_id() -> None:
    """--repo-id should be accepted on subcommands."""
    parser = build_parser()
    parsed = parser.parse_args(["run", "--repo-id", "keda"])
    assert parsed.repo_id == "keda"


def test_cli_parser_all_repositories() -> None:
    """--all should be accepted by multi-target commands."""
    parser = build_parser()
    parsed = parser.parse_args(["run", "--all"])
    assert parsed.all_repositories is True


def test_cli_parser_repo_and_repo_id_individually_parseable() -> None:
    """--repo and --repo-id should each be parseable individually."""
    parser = build_parser()
    parsed_repo = parser.parse_args(["run", "--repo", "/tmp/repo"])
    assert parsed_repo.repo == "/tmp/repo"
    assert parsed_repo.repo_id is None

    parsed_id = parser.parse_args(["run", "--repo-id", "keda"])
    assert parsed_id.repo_id == "keda"
    assert parsed_id.repo is None


def test_main_no_args_shows_help_without_traceback(monkeypatch, capsys) -> None:
    """No-argument Typer entrypoint should show help without leaking internals."""
    from backend.api.cli import main

    # In non-TTY mode (CI / pipe) we now exit non-zero after showing
    # help, mirroring the PRD's "non-TTY → non-zero exit" requirement.
    # The TTY-mode no-args case is covered by ``test_main_no_args_tty_*``.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    exit_code = main([])
    captured = capsys.readouterr()
    combined_output = _strip_ansi(f"{captured.out}\n{captured.err}")

    assert exit_code == 1
    assert "Usage: kc" in combined_output or "Commands" in combined_output
    assert "Traceback" not in combined_output
    assert "NoArgsIsHelpError" not in combined_output


def test_main_top_level_help_alias_h(capsys) -> None:
    """Top-level -h should behave like --help."""
    from backend.api.cli import main

    exit_code = main(["-h"])
    captured = capsys.readouterr()
    combined_output = _strip_ansi(f"{captured.out}\n{captured.err}")

    assert exit_code == 0
    assert "Usage: kc" in combined_output
    assert "Commands" in combined_output


def test_main_worktree_help_alias_h(capsys) -> None:
    """Subcommand group worktree -h should behave like --help."""
    from backend.api.cli import main

    exit_code = main(["worktree", "-h"])
    captured = capsys.readouterr()
    combined_output = _strip_ansi(f"{captured.out}\n{captured.err}")

    assert exit_code == 0
    assert "Usage: kc worktree" in combined_output
    assert "create" in combined_output


def test_main_worktree_create_help_alias_h(capsys) -> None:
    """Leaf subcommand worktree create -h should behave like --help."""
    from backend.api.cli import main

    exit_code = main(["worktree", "create", "-h"])
    captured = capsys.readouterr()
    combined_output = _strip_ansi(f"{captured.out}\n{captured.err}")

    assert exit_code == 0
    assert "Usage: kc worktree create" in combined_output
    assert "--branch" in combined_output


def _patch_console_script_entry_points(monkeypatch) -> None:
    """将 console script 元数据扫描替换为确定性的别名入口集合。"""
    from importlib.metadata import EntryPoint

    from backend.api import cli_completion

    def fake_entry_points(**_selection_kwargs):
        return [
            EntryPoint(name="iar", value="backend.api.cli:main", group="console_scripts"),
            EntryPoint(name="kedacode", value="backend.api.cli:main", group="console_scripts"),
            EntryPoint(name="unrelated", value="other.cli:app", group="console_scripts"),
        ]

    monkeypatch.setattr(cli_completion, "entry_points", fake_entry_points)


def test_alias_command_names_includes_only_current_commands(monkeypatch) -> None:
    """补全只为当前支持的 console script 入口生成命令名。"""
    from importlib.metadata import EntryPoint

    from backend.api import cli_completion

    def fake_entry_points(**_selection_kwargs):
        return [
            EntryPoint(name="iar", value="backend.api.cli:main", group="console_scripts"),
            EntryPoint(name="kedacode", value="backend.api.cli:main", group="console_scripts"),
            # 残留的旧发行版会重复声明同一入口，应被去重
            EntryPoint(name="iar", value="backend.api.cli:main", group="console_scripts"),
            EntryPoint(name="unrelated", value="other.cli:app", group="console_scripts"),
        ]

    monkeypatch.setattr(cli_completion, "entry_points", fake_entry_points)

    assert cli_completion.alias_command_names() == ("kedacode",)


def test_main_completion_show_zsh_outputs_script(capsys, monkeypatch) -> None:
    """completion show should print scripts for the current command names."""
    from backend.api.cli import main

    _patch_console_script_entry_points(monkeypatch)
    exit_code = main(["completion", "show", "--shell", "zsh"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "#compdef kc" in captured.out
    assert "#compdef iar" not in captured.out
    assert "_KC_COMPLETE=complete_zsh" in captured.out
    assert "compdef _kedacode_completion kedacode" in captured.out
    assert "_IAR_COMPLETE" not in captured.out


def test_main_completion_install_zsh_writes_user_files(tmp_path, monkeypatch) -> None:
    """completion install should write zsh completion under the user's home."""
    from backend.api.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))
    _patch_console_script_entry_points(monkeypatch)

    exit_code = main(["completion", "install", "--shell", "zsh"])

    completion_path = tmp_path / ".zsh" / "completions" / "_kc"
    zshrc_path = tmp_path / ".zshrc"
    assert exit_code == 0
    completion_text = completion_path.read_text(encoding="utf-8")
    assert "#compdef kc" in completion_text
    assert "#compdef iar" not in completion_text
    assert "compdef _kedacode_completion kedacode" in completion_text
    zshrc_text = zshrc_path.read_text(encoding="utf-8")
    assert "autoload -Uz compinit && compinit" in zshrc_text
    assert f'[ -f "{completion_path}" ] && source "{completion_path}"' in zshrc_text


def test_main_completion_protocol_matches_issue_prefix(capsys, monkeypatch) -> None:
    """Shell completion protocol should complete kc is<Tab> to issue commands."""
    from backend.api.cli import main

    monkeypatch.setenv("_KC_COMPLETE", "complete_bash")
    monkeypatch.setenv("COMP_WORDS", "kc is")
    monkeypatch.setenv("COMP_CWORD", "1")

    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "issue" in captured.out.splitlines()


def test_main_completion_protocol_matches_issue_prefix_via_alias(capsys, monkeypatch) -> None:
    """Shell completion protocol should complete kedacode is<Tab> to issue commands."""
    from backend.api.cli import main

    monkeypatch.setenv("_KC_COMPLETE", "complete_bash")
    monkeypatch.setenv("COMP_WORDS", "kedacode is")
    monkeypatch.setenv("COMP_CWORD", "1")

    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "issue" in captured.out.splitlines()


def test_main_returns_error_on_abort(capsys, monkeypatch) -> None:
    """main 应在 Typer app 抛出 Abort 时打印 Aborted 并返回 1。"""
    import typer

    from backend.api import cli_typer_app
    from backend.api.cli import main

    def _aborting_app(*_app_args, **_app_kwargs):
        raise typer.Abort()

    monkeypatch.setattr(cli_typer_app, "app", _aborting_app)

    exit_code = main(["issue"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Aborted." in f"{captured.out}{captured.err}"


def test_main_rejects_repo_and_repo_id_together() -> None:
    """main should exit 2 (usage) when both --repo and --repo-id are given."""
    from backend.api.cli import main

    exit_code = main(["run", "--repo", "/tmp/repo", "--repo-id", "keda"])
    assert exit_code == int(ExitCode.USAGE)


def test_main_rejects_unknown_repo_id() -> None:
    """main should exit 1 when repo-id does not exist in config."""
    from backend.api.cli import main

    with patch(
        "backend.api.cli_helpers.resolve_repository_targets",
        side_effect=ValueError("not found"),
    ):
        exit_code = main(["run", "--all-ready", "--repo-id", "nonexistent"])
        assert exit_code == 1


def test_main_passes_all_repositories_selector() -> None:
    """main should pass --all to repository target resolution."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_repositories_once", return_value=0),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["run", "--all-ready", "--all", "--dry-run"])

    assert exit_code == 0
    assert mock_resolve.call_args.kwargs["all_repositories"] is True


def test_main_run_passes_all_repositories_selector() -> None:
    """run command should dispatch to run_agent_repositories_once with same selectors."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_repositories_once", return_value=0) as mock_run,
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["run", "--all-ready", "--all", "--dry-run", "--agent", "codex"])

    assert exit_code == 0
    assert mock_resolve.call_args.kwargs["all_repositories"] is True
    assert mock_run.call_args.kwargs["dry_run"] is True
    assert mock_run.call_args.kwargs["agent"] == "codex"


def test_main_daemon_cwd_matches_enabled_single_repo(monkeypatch) -> None:
    """daemon without selectors should target cwd when it matches one enabled repo."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "keda-main"
    mock_context.display_name = "Keda Main"

    settings = MagicMock()
    settings.repositories = {
        "keda-main": MagicMock(path="/tmp/repo", enabled=True),
    }

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_daemon"),
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "backend.api.cli_helpers.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
        patch(
            # Isolate from the real ``~/.kedacode/daemon-locks`` so the test is
            # deterministic regardless of any daemon actually running on the
            # machine; this test checks cwd repo resolution, not
            # single-instance locking.
            "backend.api.cli.acquire_daemon_locks",
            return_value=[],
        ),
        patch("backend.api.cli.release_daemon_locks"),
    ):
        exit_code = main(["daemon"])

    assert exit_code == 0
    assert mock_resolve.call_args.kwargs["repo_id"] == "keda-main"
    assert mock_resolve.call_args.kwargs["all_repositories"] is False


def test_main_review_daemon_cwd_matches_enabled_single_repo(monkeypatch) -> None:
    """review-daemon without selectors should target cwd when it matches one enabled repo."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "keda-main"
    mock_context.display_name = "Keda Main"

    settings = MagicMock()
    settings.repositories = {
        "keda-main": MagicMock(path="/tmp/repo", enabled=True),
    }

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ) as mock_resolve,
        patch("backend.api.cli.run_review_daemon"),
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "backend.api.cli_helpers.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
    ):
        exit_code = main(["review-daemon"])

    assert exit_code == 0
    assert mock_resolve.call_args.kwargs["repo_id"] == "keda-main"
    assert mock_resolve.call_args.kwargs["all_repositories"] is False


def test_main_daemon_cwd_disabled_repo_rejected(monkeypatch) -> None:
    """daemon should reject cwd when it matches a disabled repository."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    settings = MagicMock()
    settings.repositories = {
        "keda-main": MagicMock(path="/tmp/repo", enabled=False),
    }

    with (
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "backend.api.cli_helpers.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
    ):
        exit_code = main(["daemon"])

    assert exit_code == int(ExitCode.PERMISSION)


def test_main_daemon_cwd_ambiguous_repo_rejected(monkeypatch) -> None:
    """daemon should reject cwd when it matches multiple enabled repositories."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    settings = MagicMock()
    settings.repositories = {
        "keda-main": MagicMock(path="/tmp/repo", enabled=True),
        "keda-alias": MagicMock(path="/tmp/repo", enabled=True),
    }

    with (
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "backend.api.cli_helpers.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
    ):
        exit_code = main(["daemon"])

    assert exit_code == int(ExitCode.USAGE)


def test_main_daemon_cwd_no_match_rejected(monkeypatch) -> None:
    """daemon should reject cwd when it matches no registry entry."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    settings = MagicMock()
    settings.repositories = {
        "other-repo": MagicMock(path="/tmp/other", enabled=True),
    }

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_daemon"),
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "backend.api.cli_helpers.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
    ):
        exit_code = main(["daemon"])

    assert exit_code == int(ExitCode.NOT_FOUND)
    mock_resolve.assert_not_called()


def test_main_review_daemon_cwd_no_match_rejected(monkeypatch) -> None:
    """review-daemon should reject cwd when it matches no registry entry."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    settings = MagicMock()
    settings.repositories = {
        "other-repo": MagicMock(path="/tmp/other", enabled=True),
    }

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
        ) as mock_resolve,
        patch("backend.api.cli.run_review_daemon"),
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "backend.api.cli_helpers.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
    ):
        exit_code = main(["review-daemon"])

    assert exit_code == int(ExitCode.NOT_FOUND)
    mock_resolve.assert_not_called()


def test_main_daemon_cwd_not_git_rejected(monkeypatch) -> None:
    """daemon should reject cwd when it is not inside a git repository."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_daemon"),
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            side_effect=ValueError("not a git repository"),
        ),
    ):
        exit_code = main(["daemon"])

    assert exit_code == int(ExitCode.NOT_FOUND)
    mock_resolve.assert_not_called()


def test_main_daemon_cwd_uninitialized_repo_rejected(monkeypatch) -> None:
    """daemon should reject cwd when the unique matched repo is not initialized."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    settings = MagicMock()
    settings.repositories = {
        "keda-main": MagicMock(path="/tmp/repo", enabled=True),
    }

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_daemon"),
        patch(
            "backend.api.cli_helpers.require_iar_repository_initialized",
            side_effect=IARRepositoryNotInitializedError(
                Path("/tmp/repo"), Path("/tmp/repo/.kedacode.toml")
            ),
        ),
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "backend.api.cli_helpers.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
    ):
        exit_code = main(["daemon"])

    assert exit_code == int(ExitCode.NOT_FOUND)
    mock_resolve.assert_not_called()


def test_main_daemon_explicit_all_targets_all_repositories(monkeypatch) -> None:
    """daemon --all 仍选择所有已启用的仓库条目。"""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    settings = MagicMock()
    settings.repositories = {
        "other-repo": MagicMock(path="/tmp/other", enabled=True),
    }

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_daemon"),
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch("backend.api.cli.acquire_daemon_locks", return_value=[]),
        patch("backend.api.cli.release_daemon_locks"),
        patch(
            "backend.api.cli_helpers.detect_git_repository_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "backend.api.cli_helpers.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
        patch("backend.api.cli.acquire_daemon_locks", return_value=[]),
        patch("backend.api.cli.release_daemon_locks"),
    ):
        exit_code = main(["daemon", "--all"])

    assert exit_code == 0
    assert mock_resolve.call_args.kwargs["all_repositories"] is True


def test_main_daemon_with_repo_id_does_not_default_to_all(monkeypatch) -> None:
    """daemon with --repo-id should still target only the specified repository."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "keda"
    mock_context.display_name = "Keda"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_daemon"),
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            # 隔离真实 ``~/.kedacode/daemon-locks``：本机常驻的 keda daemon 持有同
            # 名锁，否则本测试结果随宿主机进程状态漂移。锁的获取/释放语义由
            # daemon_single_instance 与单实例冲突测试覆盖，这里只断言仓库选择。
            "backend.api.cli.acquire_daemon_locks",
            return_value=[],
        ),
        patch("backend.api.cli.release_daemon_locks"),
    ):
        exit_code = main(["daemon", "--repo-id", "keda"])

    assert exit_code == 0
    assert mock_resolve.call_args.kwargs["repo_id"] == "keda"
    assert mock_resolve.call_args.kwargs["all_repositories"] is False


def test_main_typer_top_level_repo_selector_is_honored() -> None:
    """Typer entrypoint should accept repository selectors before the command."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ) as mock_resolve,
        patch("backend.api.cli.run_agent_repositories_once", return_value=0),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli_helpers.require_iar_repository_initialized"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["--repo", "/tmp/repo", "run", "--all-ready", "--dry-run"])

    assert exit_code == 0
    assert mock_resolve.call_args.kwargs["repo_path_override"] == "/tmp/repo"


def test_main_labels_sync_iterates_multiple_repos() -> None:
    """labels sync without selector should call sync_labels for each repo."""
    from backend.api.cli import main

    mock_context_a = MagicMock()
    mock_context_a.repo_path = Path("/tmp/repo-a")
    mock_context_a.config.labels = MagicMock()
    mock_context_b = MagicMock()
    mock_context_b.repo_path = Path("/tmp/repo-b")
    mock_context_b.config.labels = MagicMock()

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context_a, mock_context_b],
        ),
        patch("backend.api.cli.sync_labels") as mock_sync,
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["labels", "sync"])
        assert exit_code == 0
        assert mock_sync.call_count == 2


def test_main_issue_create_defaults_to_cwd() -> None:
    """issue create without --repo or --repo-id should resolve to cwd."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_issue_from_prd",
            return_value="https://github.com/example/issues/1",
        ),
        patch("backend.api.cli._prompt_and_publish_prd_if_needed", return_value=False),
    ):
        exit_code = main(["issue", "create", "tasks/example.md"])
        assert exit_code == 0


def test_main_issue_create_uses_prd_issue_workflow() -> None:
    """issue create should use the existing PRD issue workflow."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_issue_from_prd",
            return_value="https://github.com/example/issues/1",
        ) as mock_create,
        patch("backend.api.cli._prompt_and_publish_prd_if_needed") as mock_prompt,
    ):
        exit_code = main(["issue", "create", "tasks/example.md", "--publish-prd", "--ready"])

    assert exit_code == 0
    assert mock_create.call_args.kwargs["request"].prd_path == Path("tasks/example.md")
    assert mock_create.call_args.kwargs["request"].queue_ready is True
    mock_prompt.assert_not_called()


def test_main_issue_create_failure_prints_command_output(capsys) -> None:
    """issue create failures should include captured command stdout and stderr."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    commit_error = subprocess.CalledProcessError(
        1,
        [
            "git",
            "commit",
            "-m",
            "docs(prd): publish example",
            "--",
            "tasks/example.md",
        ],
        output="pre-commit stdout\n",
        stderr="trailing whitespace\n",
    )

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_issue_from_prd",
            return_value="https://github.com/example/issues/1",
        ),
        patch(
            "backend.api.cli._prompt_and_publish_prd_if_needed",
            side_effect=commit_error,
        ),
    ):
        exit_code = main(["issue", "create", "tasks/example.md", "--no-publish-prd"])

    captured = capsys.readouterr()
    combined_output = f"{captured.out}\n{captured.err}"

    assert exit_code == 1
    assert "Failed to create Issue from tasks/example.md:" in combined_output
    assert "Command: git commit -m 'docs(prd): publish example' -- tasks/example.md" in (
        combined_output
    )
    assert "Exit code: 1" in combined_output
    assert "stdout:" in combined_output
    assert "pre-commit stdout" in combined_output
    assert "stderr:" in combined_output
    assert "trailing whitespace" in combined_output


def test_main_issue_create_ready_without_publish_defers_label() -> None:
    """--ready without --publish-prd should not ready the Issue until PRD is pushed.

    时序说明：
    ┌─────────────────────────────────────────────────────────┐
    │ cli.py                                                 │
    │   queue_ready_for_request = False  # --no-publish-prd   │
    │   create_issue_from_prd(queue_ready=False)  → Issue不含ready │
    │   _prompt_and_publish_prd_if_needed(queue_ready=True)   │
    │     └─ 用户确认push → edit_issue_labels add ready      │
    └─────────────────────────────────────────────────────────┘

    关键断言：
    1. create_issue_from_prd 收到 queue_ready=False（Issue 创建时不带 ready）
    2. _prompt_and_publish_prd_if_needed 收到 queue_ready=True（交互 prompt 可以在 push 成功后补 ready）
    """
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_issue_from_prd",
            return_value="https://github.com/example/issues/1",
        ) as mock_create,
        patch(
            "backend.api.cli._prompt_and_publish_prd_if_needed", return_value=False
        ) as mock_prompt,
    ):
        exit_code = main(["issue", "create", "tasks/example.md", "--ready", "--no-publish-prd"])
        assert exit_code == 0
        # create_issue_from_prd should be called with queue_ready=False
        assert mock_create.call_args.kwargs["request"].queue_ready is False
        # prompt should still receive queue_ready=True so it can add the label after push
        assert mock_prompt.call_args.kwargs["queue_ready"] is True


def test_main_issue_create_ready_with_publish_keeps_label() -> None:
    """--ready with --publish-prd should let create_issue_from_prd handle ready gating.

    时序说明：
    ┌─────────────────────────────────────────────────────────┐
    │ cli.py                                                 │
    │   queue_ready_for_request = True   # 有 --publish-prd   │
    │   create_issue_from_prd(queue_ready=True)  → Issue含ready │
    │   # 不进入 _prompt_and_publish_prd_if_needed 分支       │
    │   # （ready 由 core 内部在 push 成功后添加）            │
    └─────────────────────────────────────────────────────────┘

    关键断言：
    1. create_issue_from_prd 收到 queue_ready=True（core 内部处理 ready gating）
    2. _prompt_and_publish_prd_if_needed 不被调用（--publish-prd 时走非交互路径）
    """
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_issue_from_prd",
            return_value="https://github.com/example/issues/1",
        ) as mock_create,
        patch("backend.api.cli._prompt_and_publish_prd_if_needed") as mock_prompt,
    ):
        exit_code = main(["issue", "create", "tasks/example.md", "--publish-prd", "--ready"])
        assert exit_code == 0
        assert mock_create.call_args.kwargs["request"].queue_ready is True
        # prompt should not be called when --publish-prd is used
        mock_prompt.assert_not_called()


def test_main_issue_create_multiple_prds() -> None:
    """issue create should create an Issue for each supplied PRD path."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_issue_from_prd",
            return_value="https://github.com/example/issues/1",
        ) as mock_create,
        patch("backend.api.cli._prompt_and_publish_prd_if_needed", return_value=False),
    ):
        exit_code = main(["issue", "create", "tasks/a.md", "tasks/b.md", "--ready"])

    assert exit_code == 0
    assert mock_create.call_count == 2
    created_prd_paths = [call.kwargs["request"].prd_path for call in mock_create.call_args_list]
    assert created_prd_paths == [Path("tasks/a.md"), Path("tasks/b.md")]


def test_main_issue_create_multiple_prds_rejects_shared_title() -> None:
    """--title cannot be shared across multiple PRD-created Issues."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client") as mock_client,
        patch("backend.api.cli.create_issue_from_prd") as mock_create,
    ):
        exit_code = main(
            [
                "issue",
                "create",
                "tasks/a.md",
                "tasks/b.md",
                "--title",
                "Shared Title",
            ]
        )

    assert exit_code == int(ExitCode.USAGE)
    mock_client.assert_not_called()
    mock_create.assert_not_called()


def test_main_issue_create_multiple_prds_continues_on_failure() -> None:
    """A failure for one PRD should not stop creation for the remaining PRDs."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    def _fake_create(*, request, **kwargs):
        if request.prd_path.name == "bad.md":
            raise ValueError("bad PRD")
        return "https://github.com/example/issues/1"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.create_issue_from_prd", side_effect=_fake_create) as mock_create,
        patch("backend.api.cli._prompt_and_publish_prd_if_needed", return_value=False),
    ):
        exit_code = main(["issue", "create", "tasks/bad.md", "tasks/good.md", "--ready"])

    assert exit_code == 1
    assert mock_create.call_count == 2
    created_prd_paths = [
        call.kwargs["request"].prd_path.name for call in mock_create.call_args_list
    ]
    assert created_prd_paths == ["bad.md", "good.md"]


def test_main_issue_create_directory_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue create should expand a directory argument to its *.md files."""
    from backend.api.cli import main

    pending_dir = tmp_path / "tasks" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "b.md").write_text("# B", encoding="utf-8")
    (pending_dir / "a.md").write_text("# A", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    mock_context = MagicMock()
    mock_context.repo_path = tmp_path
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_issue_from_prd",
            return_value="https://github.com/example/issues/1",
        ) as mock_create,
        patch("backend.api.cli._prompt_and_publish_prd_if_needed", return_value=False),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["issue", "create", "tasks/pending", "--ready"])

    assert exit_code == 0
    assert mock_create.call_count == 2
    created_prd_paths = [call.kwargs["request"].prd_path for call in mock_create.call_args_list]
    assert created_prd_paths == [
        Path("tasks/pending/a.md"),
        Path("tasks/pending/b.md"),
    ]


def test_main_issue_create_directory_skips_linked_prds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """issue create should skip PRDs with existing Issue links in a directory."""
    from backend.api.cli import main

    pending_dir = tmp_path / "tasks" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "linked.md").write_text(
        "# Linked\n- GitHub Issue: https://github.com/org/repo/issues/1\n",
        encoding="utf-8",
    )
    (pending_dir / "new.md").write_text("# New", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    mock_context = MagicMock()
    mock_context.repo_path = tmp_path
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_issue_from_prd",
            return_value="https://github.com/example/issues/2",
        ) as mock_create,
        patch("backend.api.cli._prompt_and_publish_prd_if_needed", return_value=False),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["issue", "create", "tasks/pending", "--ready"])

    assert exit_code == 0
    assert mock_create.call_count == 1
    created_prd_paths = [call.kwargs["request"].prd_path for call in mock_create.call_args_list]
    assert created_prd_paths == [Path("tasks/pending/new.md")]

    captured = capsys.readouterr()
    assert "Skipped PRD with existing Issue: tasks/pending/linked.md" in captured.out


def test_main_issue_create_all_directory_prds_linked_returns_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue create succeeds when all PRDs in a directory already have Issues."""
    from backend.api.cli import main

    pending_dir = tmp_path / "tasks" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "linked.md").write_text(
        "# Linked\n- GitHub Issue: https://github.com/org/repo/issues/1\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    mock_context = MagicMock()
    mock_context.repo_path = tmp_path
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"

    with (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.create_issue_from_prd") as mock_create,
        patch("backend.api.cli._prompt_and_publish_prd_if_needed", return_value=False),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["issue", "create", "tasks/pending"])

    assert exit_code == 0
    mock_create.assert_not_called()


def test_cli_parser_deliberate_defaults() -> None:
    """deliberate should have sensible defaults."""
    parser = build_parser()
    parsed = parser.parse_args(["deliberate", "test prompt"])
    assert parsed.command == "deliberate"
    assert parsed.prompt == "test prompt"
    assert parsed.agents == "architect,skeptic,implementer"
    assert parsed.rounds is None
    assert parsed.synthesizer is None
    assert parsed.output is None
    assert parsed.session_id is None


def test_cli_parser_deliberate_custom_agents() -> None:
    """deliberate should accept custom agents and rounds."""
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "deliberate",
            "test prompt",
            "--agents",
            "architect,skeptic",
            "--rounds",
            "3",
            "--synthesizer",
            "kimi",
            "--output",
            "/tmp/out",
            "--session-id",
            "sid-1",
            "--strict",
        ]
    )
    assert parsed.command == "deliberate"
    assert parsed.prompt == "test prompt"
    assert parsed.agents == "architect,skeptic"
    assert parsed.rounds == 3
    assert parsed.synthesizer == "kimi"
    assert parsed.output == "/tmp/out"
    assert parsed.session_id == "sid-1"
    assert parsed.strict is True


def test_cli_parser_deliberate_strict_default_false() -> None:
    """deliberate --strict should default to False."""
    parser = build_parser()
    parsed = parser.parse_args(["deliberate", "test prompt"])
    assert parsed.strict is False


def test_main_deliberate_strict_returns_nonzero_on_failure(tmp_path) -> None:
    """deliberate --strict should return 1 when an agent fails."""
    from backend.api.cli import main
    from backend.core.shared.models.agent_deliberation import (
        DeliberationAgentFailure,
        DeliberationAgentProfile,
        DeliberationConfig,
        DeliberationResult,
    )
    from backend.core.shared.models.agent_runner import AppConfig

    mock_config = AppConfig(
        deliberation=DeliberationConfig(
            default_output_dir=str(tmp_path),
            default_rounds=1,
            default_synthesizer="claude",
            profiles=(
                DeliberationAgentProfile(
                    profile_id="architect",
                    agent="claude",
                    role="architect",
                    behavior_prompt="be an architect",
                ),
            ),
        )
    )
    mock_context = MagicMock()
    mock_context.repo_path = tmp_path / "repo"
    mock_context.config = mock_config

    def fake_run_agent_deliberation(**kwargs):
        request = kwargs["request"]
        return DeliberationResult(
            session_id=request.session_id,
            prompt=request.prompt,
            recommendation="",
            consensus="",
            disagreements="",
            risks="",
            next_actions="",
            events=(),
            agent_outputs={"round_1": {"architect": ""}},
            output_dir=request.output_dir,
            started_at="2026-05-23T00:00:00+00:00",
            finished_at="2026-05-23T00:01:00+00:00",
            failed_agents=(
                DeliberationAgentFailure(
                    profile_id="architect",
                    attempted_agent="claude",
                    fallback_agent=None,
                    reason="exit=1",
                ),
            ),
        )

    with (
        patch("backend.api.cli.create_process_runner"),
        patch("backend.api.cli.get_agent_runner_settings"),
        patch(
            "backend.engines.agent_runner.factory.build_app_config_from_settings",
            return_value=mock_config,
        ),
        patch(
            "backend.api.cli._resolve_cli_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.create_transcript_runner"),
        patch("backend.api.cli.create_event_sink") as mock_event_sink,
        patch(
            "backend.api.cli.run_agent_deliberation",
            side_effect=fake_run_agent_deliberation,
        ),
        patch("backend.api.cli.write_deliberation_outputs"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        mock_event_sink.return_value = MagicMock()

        exit_code = main(["deliberate", "test prompt", "--strict"])

    assert exit_code == 1


def test_main_deliberate_default_returns_zero_with_failed_agents(
    tmp_path,
) -> None:
    """deliberate without --strict should return 0 when at least one agent succeeds."""
    from backend.api.cli import main
    from backend.core.shared.models.agent_deliberation import (
        DeliberationAgentFailure,
        DeliberationAgentProfile,
        DeliberationConfig,
        DeliberationResult,
    )
    from backend.core.shared.models.agent_runner import AppConfig

    mock_config = AppConfig(
        deliberation=DeliberationConfig(
            default_output_dir=str(tmp_path),
            default_rounds=1,
            default_synthesizer="claude",
            profiles=(
                DeliberationAgentProfile(
                    profile_id="architect",
                    agent="claude",
                    role="architect",
                    behavior_prompt="be an architect",
                ),
                DeliberationAgentProfile(
                    profile_id="skeptic",
                    agent="kimi",
                    role="skeptic",
                    behavior_prompt="be a skeptic",
                ),
            ),
        )
    )
    mock_context = MagicMock()
    mock_context.repo_path = tmp_path / "repo"
    mock_context.config = mock_config

    def fake_run_agent_deliberation(**kwargs):
        request = kwargs["request"]
        return DeliberationResult(
            session_id=request.session_id,
            prompt=request.prompt,
            recommendation="do it",
            consensus="agree",
            disagreements="none",
            risks="low",
            next_actions="next",
            events=(),
            agent_outputs={"round_1": {"architect": "architect out", "skeptic": ""}},
            output_dir=request.output_dir,
            started_at="2026-05-23T00:00:00+00:00",
            finished_at="2026-05-23T00:01:00+00:00",
            failed_agents=(
                DeliberationAgentFailure(
                    profile_id="skeptic",
                    attempted_agent="kimi",
                    fallback_agent=None,
                    reason="exit=1",
                ),
            ),
        )

    with (
        patch("backend.api.cli.create_process_runner"),
        patch("backend.api.cli.get_agent_runner_settings"),
        patch(
            "backend.engines.agent_runner.factory.build_app_config_from_settings",
            return_value=mock_config,
        ),
        patch(
            "backend.api.cli._resolve_cli_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.create_transcript_runner"),
        patch("backend.api.cli.create_event_sink") as mock_event_sink,
        patch(
            "backend.api.cli.run_agent_deliberation",
            side_effect=fake_run_agent_deliberation,
        ),
        patch("backend.api.cli.write_deliberation_outputs"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        mock_event_sink.return_value = MagicMock()

        exit_code = main(["deliberate", "test prompt"])

    assert exit_code == 0


def test_main_deliberate_returns_one_when_all_agents_fail(tmp_path) -> None:
    """deliberate should return 1 when all participants fail."""
    from backend.api.cli import main
    from backend.core.shared.models.agent_deliberation import (
        DeliberationAgentFailure,
        DeliberationAgentProfile,
        DeliberationConfig,
        DeliberationResult,
    )
    from backend.core.shared.models.agent_runner import AppConfig

    mock_config = AppConfig(
        deliberation=DeliberationConfig(
            default_output_dir=str(tmp_path),
            default_rounds=1,
            default_synthesizer="claude",
            profiles=(
                DeliberationAgentProfile(
                    profile_id="architect",
                    agent="claude",
                    role="architect",
                    behavior_prompt="be an architect",
                ),
            ),
        )
    )
    mock_context = MagicMock()
    mock_context.repo_path = tmp_path / "repo"
    mock_context.config = mock_config

    def fake_run_agent_deliberation(**kwargs):
        request = kwargs["request"]
        return DeliberationResult(
            session_id=request.session_id,
            prompt=request.prompt,
            recommendation="",
            consensus="",
            disagreements="",
            risks="",
            next_actions="",
            events=(),
            agent_outputs={"round_1": {"architect": ""}},
            output_dir=request.output_dir,
            started_at="2026-05-23T00:00:00+00:00",
            finished_at="2026-05-23T00:01:00+00:00",
            failed_agents=(
                DeliberationAgentFailure(
                    profile_id="architect",
                    attempted_agent="claude",
                    fallback_agent=None,
                    reason="exit=1",
                ),
            ),
        )

    with (
        patch("backend.api.cli.create_process_runner"),
        patch("backend.api.cli.get_agent_runner_settings"),
        patch(
            "backend.engines.agent_runner.factory.build_app_config_from_settings",
            return_value=mock_config,
        ),
        patch(
            "backend.api.cli._resolve_cli_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.create_transcript_runner"),
        patch("backend.api.cli.create_event_sink") as mock_event_sink,
        patch(
            "backend.api.cli.run_agent_deliberation",
            side_effect=fake_run_agent_deliberation,
        ),
        patch("backend.api.cli.write_deliberation_outputs"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        mock_event_sink.return_value = MagicMock()

        exit_code = main(["deliberate", "test prompt"])

    assert exit_code == 1


def test_main_deliberate_uses_single_session_output_path(tmp_path) -> None:
    """deliberate should pass the finalized session directory to all writers."""
    from backend.api.cli import main
    from backend.core.shared.models.agent_deliberation import (
        DeliberationAgentProfile,
        DeliberationConfig,
        DeliberationResult,
    )
    from backend.core.shared.models.agent_runner import AppConfig

    output_root = tmp_path / "deliberations"
    expected_output_path = output_root / "sid-1"
    captured = {}

    def fake_run_agent_deliberation(**kwargs):
        request = kwargs["request"]
        captured["request"] = request
        return DeliberationResult(
            session_id=request.session_id,
            prompt=request.prompt,
            recommendation="do it",
            consensus="agree",
            disagreements="none",
            risks="low",
            next_actions="next",
            events=(),
            agent_outputs={
                "round_1": {
                    "skeptic": "skeptic out",
                    "architect": "architect out",
                }
            },
            output_dir=request.output_dir,
            started_at="2026-05-23T00:00:00+00:00",
            finished_at="2026-05-23T00:01:00+00:00",
        )

    mock_config = AppConfig(
        deliberation=DeliberationConfig(
            default_output_dir=str(output_root),
            default_rounds=2,
            default_synthesizer="claude",
            profiles=(
                DeliberationAgentProfile(
                    profile_id="architect",
                    agent="claude",
                    role="architect",
                    behavior_prompt="be an architect",
                ),
                DeliberationAgentProfile(
                    profile_id="skeptic",
                    agent="kimi",
                    role="skeptic",
                    behavior_prompt="be a skeptic",
                ),
            ),
        )
    )
    mock_context = MagicMock()
    mock_context.repo_path = tmp_path / "repo"
    mock_context.config = mock_config

    with (
        patch("backend.api.cli.create_process_runner"),
        patch("backend.api.cli.get_agent_runner_settings"),
        patch(
            "backend.engines.agent_runner.factory.build_app_config_from_settings",
            return_value=mock_config,
        ),
        patch(
            "backend.api.cli._resolve_cli_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.create_transcript_runner"),
        patch("backend.api.cli.create_event_sink") as mock_event_sink,
        patch(
            "backend.api.cli.run_agent_deliberation",
            side_effect=fake_run_agent_deliberation,
        ),
        patch("backend.api.cli.write_deliberation_outputs") as mock_write,
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        mock_event_sink.return_value = MagicMock()

        exit_code = main(["deliberate", "test prompt", "--session-id", "sid-1"])

    assert exit_code == 0
    assert captured["request"].session_id == "sid-1"
    assert captured["request"].output_dir == str(expected_output_path)
    mock_event_sink.assert_called_once_with(expected_output_path, ANY)
    mock_write.assert_called_once()
    assert mock_write.call_args.args[2] == expected_output_path
    assert tuple(profile.profile_id for profile in mock_write.call_args.args[1].profiles) == (
        "skeptic",
        "architect",
    )


def test_main_review_dispatches_review_workflow() -> None:
    """review should dispatch to the review workflow."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.config = MagicMock()
    mock_context.repo_id = "repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client") as mock_client,
        patch("backend.api.cli.review_once", return_value=0) as mock_review,
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["review", "--dry-run", "--agent", "claude"])

    assert exit_code == 0
    mock_client.assert_called_with(mock_context.repo_path, ANY)
    assert mock_review.call_args.kwargs["dry_run"] is True
    assert mock_review.call_args.kwargs["agent"] == "claude"


def test_cli_parser_recover_required_args() -> None:
    """recover should require --issue."""
    parser = build_parser()
    parsed = parser.parse_args(["recover", "--issue", "5"])
    assert parsed.command == "recover"
    assert parsed.issue == 5
    assert parsed.branch is None


def test_cli_parser_recover_with_branch() -> None:
    """recover should accept optional --branch."""
    parser = build_parser()
    parsed = parser.parse_args(["recover", "--issue", "5", "--branch", "feature-xyz"])
    assert parsed.command == "recover"
    assert parsed.issue == 5
    assert parsed.branch == "feature-xyz"


def test_cli_parser_recover_missing_issue() -> None:
    """recover should fail without --issue."""
    import pytest as _pytest

    parser = build_parser()
    with _pytest.raises(SystemExit):
        parser.parse_args(["recover"])


def test_cli_parser_blocked_continue_required_args() -> None:
    """blocked-continue should require --issue."""
    parser = build_parser()
    parsed = parser.parse_args(["blocked-continue", "--issue", "7"])
    assert parsed.command == "blocked-continue"
    assert parsed.issue == 7
    assert parsed.agent == "auto"


def test_cli_parser_blocked_continue_with_agent() -> None:
    """blocked-continue should accept --agent override."""
    parser = build_parser()
    parsed = parser.parse_args(["blocked-continue", "--issue", "7", "--agent", "claude"])
    assert parsed.command == "blocked-continue"
    assert parsed.issue == 7
    assert parsed.agent == "claude"


def test_cli_parser_blocked_continue_missing_issue() -> None:
    """blocked-continue should fail without --issue."""
    import pytest as _pytest

    parser = build_parser()
    with _pytest.raises(SystemExit):
        parser.parse_args(["blocked-continue"])


def test_main_blocked_continue_success(capsys) -> None:
    """blocked-continue should print success when claimed."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.config = MagicMock()
    mock_context.config.labels.blocked = "agent/blocked"

    with (
        patch(
            "backend.api.cli.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.core.use_cases.blocked_continue.blocked_continue_issue",
            return_value=True,
        ) as mock_blocked,
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["blocked-continue", "--issue", "42"])

    assert exit_code == 0
    mock_blocked.assert_called_once()
    assert mock_blocked.call_args.kwargs["issue_number"] == 42
    captured = capsys.readouterr()
    assert "Issue #42 resumed successfully" in captured.out


def test_main_blocked_continue_already_claimed(capsys) -> None:
    """blocked-continue should print skip message when another runner claimed it."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.config = MagicMock()

    with (
        patch(
            "backend.api.cli.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.core.use_cases.blocked_continue.blocked_continue_issue",
            return_value=False,
        ) as mock_blocked,
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["blocked-continue", "--issue", "42"])

    assert exit_code == 0
    mock_blocked.assert_called_once()
    captured = capsys.readouterr()
    assert "Issue #42 was claimed by another runner" in captured.out


def test_main_blocked_continue_failure_prints_error(capsys) -> None:
    """blocked-continue should print a concise error on BlockedContinueError."""
    from backend.api.cli import main
    from backend.core.use_cases.blocked_continue import BlockedContinueError

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.config = MagicMock()

    with (
        patch(
            "backend.api.cli.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.core.use_cases.blocked_continue.blocked_continue_issue",
            side_effect=BlockedContinueError("Worktree has uncommitted changes."),
        ),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["blocked-continue", "--issue", "42"])

    assert exit_code == 1
    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"
    assert "blocked-continue failed" in combined
    assert "Worktree has uncommitted changes" in combined


def test_main_run_rebase_conflict_detached_head() -> None:
    """kc run should dispatch without error for rework rebase with detached HEAD."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.config = MagicMock()
    mock_context.repo_id = "repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.run_agent_repositories_once", return_value=0) as mock_run,
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["run", "--all-ready", "--dry-run", "--agent", "claude"])

    assert exit_code == 0
    mock_run.assert_called_once()


def test_cli_parser_ask_defaults() -> None:
    """ask should have sensible defaults."""
    parser = build_parser()
    parsed = parser.parse_args(["ask", "what should I do"])
    assert parsed.command == "ask"
    assert parsed.prompt == "what should I do"
    assert parsed.agent == "auto"
    assert parsed.plan_only is False
    assert parsed.execute is False
    assert parsed.yes is False
    assert parsed.output is None


def test_cli_parser_ask_with_options() -> None:
    """ask should accept all defined options."""
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "ask",
            "create issue from prd",
            "--agent",
            "codex",
            "--plan-only",
            "--execute",
            "--yes",
            "--output",
            "/tmp/out",
            "--repo",
            "/tmp/repo",
        ]
    )
    assert parsed.command == "ask"
    assert parsed.prompt == "create issue from prd"
    assert parsed.agent == "codex"
    assert parsed.plan_only is True
    assert parsed.execute is True
    assert parsed.yes is True
    assert parsed.output == "/tmp/out"
    assert parsed.repo == "/tmp/repo"


def test_main_ask_plan_only_writes_audit(tmp_path, monkeypatch) -> None:
    """ask --plan-only should write audit files without executing."""
    from backend.api.cli import main

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    (repo_path / "tasks").mkdir()
    (repo_path / "tasks" / "pending").mkdir()

    fake_planner_stdout = (
        '{"decision_id": "dec-test-123", '
        '"user_prompt": "test", '
        '"intent_summary": "Do nothing", '
        '"risk_level": "low", '
        '"actions": [{'
        '"action_id": "A1", '
        '"action_type": "no_op", '
        '"title": "No action", '
        '"rationale": "Nothing to do", '
        '"parameters": {}, '
        '"writes_external_state": false, '
        '"confirmation_required": false'
        "}], "
        '"assumptions": [], '
        '"warnings": [], '
        '"requires_confirmation": false}'
    )

    mock_context = MagicMock()
    mock_context.repo_path = repo_path
    mock_context.config = MagicMock()
    mock_context.config.interactive_decision.default_agent = "codex"
    # 决策（planner）阶段现在经生命周期矩阵解析并对未注册 agent fail-fast，因此
    # MagicMock 形态的 config 必须给出真实的矩阵声明与 agent 注册表默认值。
    mock_context.config.lifecycle_agents = LifecycleAgentsConfig()
    mock_context.config.agents = dict(BUILTIN_AGENT_SPECS)
    mock_context.config.interactive_decision.default_output_dir = str(tmp_path / "decisions")
    mock_context.config.labels = MagicMock()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"
    mock_context.config.prompts.default_phase = "execution"

    mock_planner = MagicMock()
    mock_planner.generate.return_value = MagicMock(
        return_code=0,
        stdout=fake_planner_stdout,
    )

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_planner_runner",
            return_value=mock_planner,
        ),
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(
            [
                "ask",
                "what should I do",
                "--plan-only",
                "--repo",
                str(repo_path),
                "--output",
                str(tmp_path / "decisions"),
            ]
        )

    assert exit_code == 0
    decision_dir = tmp_path / "decisions" / "dec-test-123"
    assert (decision_dir / "plan.json").exists()
    assert (decision_dir / "plan.md").exists()
    assert (decision_dir / "context-summary.json").exists()


def test_main_ask_rejects_unknown_action() -> None:
    """ask should return non-zero when planner returns unknown action."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.config = MagicMock()
    mock_context.config.interactive_decision.default_agent = "codex"
    # 决策（planner）阶段现在经生命周期矩阵解析并对未注册 agent fail-fast，因此
    # MagicMock 形态的 config 必须给出真实的矩阵声明与 agent 注册表默认值。
    mock_context.config.lifecycle_agents = LifecycleAgentsConfig()
    mock_context.config.agents = dict(BUILTIN_AGENT_SPECS)
    mock_context.config.interactive_decision.default_output_dir = "logs/decisions"
    mock_context.config.labels = MagicMock()

    fake_planner_stdout = (
        '{"decision_id": "dec-test-456", '
        '"user_prompt": "test", '
        '"intent_summary": "Bad action", '
        '"risk_level": "low", '
        '"actions": [{'
        '"action_id": "A1", '
        '"action_type": "git_push", '
        '"title": "Push", '
        '"rationale": "Bad", '
        '"parameters": {}, '
        '"writes_external_state": true, '
        '"confirmation_required": true'
        "}], "
        '"assumptions": [], '
        '"warnings": [], '
        '"requires_confirmation": false}'
    )

    mock_planner = MagicMock()
    mock_planner.generate.return_value = MagicMock(
        return_code=0,
        stdout=fake_planner_stdout,
    )

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.api.cli.create_planner_runner",
            return_value=mock_planner,
        ),
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
    ):
        exit_code = main(["ask", "push to main", "--plan-only", "--repo", "/tmp/repo"])

    assert exit_code == int(ExitCode.GENERAL)


def test_main_ask_run_once_dry_run_dispatches_existing_use_case() -> None:
    """ask --execute for run_once_dry_run should dispatch run_agent_repositories_once."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"
    mock_context.config = MagicMock()
    mock_context.config.interactive_decision.default_agent = "codex"
    # 决策（planner）阶段现在经生命周期矩阵解析并对未注册 agent fail-fast，因此
    # MagicMock 形态的 config 必须给出真实的矩阵声明与 agent 注册表默认值。
    mock_context.config.lifecycle_agents = LifecycleAgentsConfig()
    mock_context.config.agents = dict(BUILTIN_AGENT_SPECS)
    mock_context.config.interactive_decision.default_output_dir = "logs/decisions"
    mock_context.config.labels = MagicMock()
    mock_context.config.prompts.default_phase = "execution"

    fake_planner_stdout = (
        '{"decision_id": "dec-test-789", '
        '"user_prompt": "test", '
        '"intent_summary": "Dry run", '
        '"risk_level": "low", '
        '"actions": [{'
        '"action_id": "A1", '
        '"action_type": "run_once_dry_run", '
        '"title": "Dry run", '
        '"rationale": "Preview", '
        '"parameters": {"agent": "auto", "max_issues": 1}, '
        '"writes_external_state": false, '
        '"confirmation_required": false'
        "}], "
        '"assumptions": [], '
        '"warnings": [], '
        '"requires_confirmation": false}'
    )

    mock_planner = MagicMock()
    mock_planner.generate.return_value = MagicMock(
        return_code=0,
        stdout=fake_planner_stdout,
    )

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch(
            "backend.api.cli.create_planner_runner",
            return_value=mock_planner,
        ),
        patch(
            "backend.core.use_cases.interactive_decision.run_agent_repositories_once",
            return_value=0,
        ) as mock_run,
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(
            [
                "ask",
                "dry run",
                "--execute",
                "--yes",
                "--repo",
                "/tmp/repo",
            ]
        )

    assert exit_code == 0
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["dry_run"] is True


def test_main_ask_execute_confirmation_required_for_write_action() -> None:
    """ask --execute for create_issue_from_prd requires confirmation and fails without TTY."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"
    mock_context.config = MagicMock()
    mock_context.config.interactive_decision.default_agent = "codex"
    # 决策（planner）阶段现在经生命周期矩阵解析并对未注册 agent fail-fast，因此
    # MagicMock 形态的 config 必须给出真实的矩阵声明与 agent 注册表默认值。
    mock_context.config.lifecycle_agents = LifecycleAgentsConfig()
    mock_context.config.agents = dict(BUILTIN_AGENT_SPECS)
    mock_context.config.interactive_decision.default_output_dir = "logs/decisions"
    mock_context.config.labels = MagicMock()
    mock_context.config.prompts.default_phase = "execution"

    fake_planner_stdout = (
        '{"decision_id": "dec-test-abc", '
        '"user_prompt": "test", '
        '"intent_summary": "Create issue", '
        '"risk_level": "medium", '
        '"actions": [{'
        '"action_id": "A1", '
        '"action_type": "create_issue_from_prd", '
        '"title": "Create issue", '
        '"rationale": "PRD is ready", '
        '"parameters": {"prd_path": "tasks/pending/example.md", "ready": false}, '
        '"writes_external_state": true, '
        '"confirmation_required": true'
        '}], "assumptions": [], "warnings": ["This will create a GitHub Issue."], '
        '"requires_confirmation": true}'
    )

    mock_planner = MagicMock()
    mock_planner.generate.return_value = MagicMock(
        return_code=0,
        stdout=fake_planner_stdout,
    )

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.api.cli.create_planner_runner",
            return_value=mock_planner,
        ),
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
    ):
        exit_code = main(
            [
                "ask",
                "create issue from PRD",
                "--execute",
                "--repo",
                "/tmp/repo",
            ]
        )

    assert exit_code == int(ExitCode.GENERAL)


def test_main_ask_execute_confirmation_wrong_input_skips_action(monkeypatch) -> None:
    """ask --execute skips write action when user confirmation input does not match."""
    from backend.api.cli import main

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"
    mock_context.config = MagicMock()
    mock_context.config.interactive_decision.default_agent = "codex"
    # 决策（planner）阶段现在经生命周期矩阵解析并对未注册 agent fail-fast，因此
    # MagicMock 形态的 config 必须给出真实的矩阵声明与 agent 注册表默认值。
    mock_context.config.lifecycle_agents = LifecycleAgentsConfig()
    mock_context.config.agents = dict(BUILTIN_AGENT_SPECS)
    mock_context.config.interactive_decision.default_output_dir = "logs/decisions"
    mock_context.config.labels = MagicMock()
    mock_context.config.prompts.default_phase = "execution"

    fake_planner_stdout = (
        '{"decision_id": "dec-test-wrong", '
        '"user_prompt": "test", '
        '"intent_summary": "Create issue", '
        '"risk_level": "medium", '
        '"actions": [{'
        '"action_id": "A1", '
        '"action_type": "create_issue_from_prd", '
        '"title": "Create issue", '
        '"rationale": "PRD is ready", '
        '"parameters": {"prd_path": "tasks/pending/example.md", "ready": false}, '
        '"writes_external_state": true, '
        '"confirmation_required": true'
        '}], "assumptions": [], "warnings": [], "requires_confirmation": true}'
    )

    mock_planner = MagicMock()
    mock_planner.generate.return_value = MagicMock(
        return_code=0,
        stdout=fake_planner_stdout,
    )

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "wrong-confirmation")

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.api.cli.create_planner_runner",
            return_value=mock_planner,
        ),
        patch("backend.core.use_cases.interactive_decision.create_issue_from_prd") as mock_create,
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
    ):
        exit_code = main(
            [
                "ask",
                "create issue from PRD",
                "--execute",
                "--repo",
                "/tmp/repo",
            ]
        )

    assert exit_code == int(ExitCode.GENERAL)
    mock_create.assert_not_called()


def _init_bare_git_repository(tmp_path: Path, name: str) -> Path:
    """Create a throwaway Git repository without .kedacode.toml."""
    repo_path = tmp_path / name
    repo_path.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    (repo_path / "README.md").write_text("placeholder", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    return repo_path


def test_main_labels_sync_fails_when_repository_not_initialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc labels sync` should fail fast without .kedacode.toml."""
    repo_path = _init_bare_git_repository(tmp_path, "uninitialized")
    monkeypatch.chdir(repo_path)

    exit_code = main(["labels", "sync"])
    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"

    assert exit_code == int(ExitCode.NOT_FOUND)
    assert "Repository is not initialized for KedaCode" in _strip_ansi(combined)
    assert "kc init" in _strip_ansi(combined)
    assert ".kedacode.toml" in _strip_ansi(combined)


def test_main_run_fails_when_repository_not_initialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc run --dry-run` should fail fast without .kedacode.toml."""
    repo_path = _init_bare_git_repository(tmp_path, "uninitialized")
    monkeypatch.chdir(repo_path)

    exit_code = main(["run", "--all-ready", "--dry-run"])
    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"

    assert exit_code == int(ExitCode.NOT_FOUND)
    assert "Repository is not initialized for KedaCode" in _strip_ansi(combined)
    assert "kc init" in _strip_ansi(combined)


def test_main_issue_create_fails_when_repository_not_initialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc issue create` should fail fast without .kedacode.toml."""
    repo_path = _init_bare_git_repository(tmp_path, "uninitialized")
    prd_path = repo_path / "tasks" / "pending" / "test.md"
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text("# Test PRD\n\n## Summary\n\nTest.\n", encoding="utf-8")
    monkeypatch.chdir(repo_path)

    exit_code = main(["issue", "create", str(prd_path)])
    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"

    assert exit_code == int(ExitCode.NOT_FOUND)
    assert "Repository is not initialized for KedaCode" in _strip_ansi(combined)
    assert "kc init" in _strip_ansi(combined)


def test_main_worktree_create_fails_when_repository_not_initialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc worktree create` should fail fast without .kedacode.toml."""
    repo_path = _init_bare_git_repository(tmp_path, "uninitialized")
    monkeypatch.chdir(repo_path)

    exit_code = main(["worktree", "create", "--branch", "feature-x", "--base-branch", "main"])
    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"

    assert exit_code == int(ExitCode.NOT_FOUND)
    assert "Repository is not initialized for KedaCode" in _strip_ansi(combined)
    assert "kc init" in _strip_ansi(combined)


def test_main_init_succeeds_when_repository_not_initialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`kc init` must be exempt from the initialization gate."""
    repo_path = _init_bare_git_repository(tmp_path, "uninitialized")
    monkeypatch.chdir(repo_path)

    exit_code = main(["init", "--dry-run"])

    assert exit_code == 0


def _write_iar_toml(repo_root: Path, repo_id: str) -> None:
    """Write a minimal .kedacode.toml for CLI discovery tests."""
    iar_toml = repo_root / ".kedacode.toml"
    iar_toml.write_text(
        "[agent_runner]\n" "[agent_runner.repository]\n" f'id = "{repo_id}"\n',
        encoding="utf-8",
    )


def test_main_registry_scan_lists_discovered_repos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry scan` should print discovered repositories."""
    scan_root = tmp_path / "code"
    scan_root.mkdir()
    repo_path = scan_root / "foo"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    _write_iar_toml(repo_path, "foo")

    exit_code = main(["registry", "scan", str(scan_root)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[foo]" in _strip_ansi(captured.out)
    assert "(new)" in _strip_ansi(captured.out)


def test_main_registry_sync_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry sync --dry-run` should not write config.toml."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    scan_root = tmp_path / "code"
    scan_root.mkdir()
    repo_path = scan_root / "bar"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    _write_iar_toml(repo_path, "bar")

    exit_code = main(["registry", "sync", "--dry-run", str(scan_root)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Would register" in _strip_ansi(captured.out)
    assert "[agent_runner.repositories.bar]" not in config_path.read_text(encoding="utf-8")


def test_main_registry_sync_registers_new_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry sync` should write discovered repos to config.toml."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    scan_root = tmp_path / "code"
    scan_root.mkdir()
    repo_path = scan_root / "baz"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    _write_iar_toml(repo_path, "baz")

    exit_code = main(["registry", "sync", str(scan_root)])

    assert exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert "[agent_runner.repositories.baz]" in config_text


def test_main_registry_reinit_updates_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry reinit` should rewrite .kedacode.toml with the given remote."""
    monkeypatch.chdir(tmp_path)
    repo_path = _init_bare_git_repository(tmp_path, "fsense")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n[agent_runner.repositories.zata-zhangtao-fsense]\n"
        f'path = "{repo_path}"\nenabled = true\ndisplay_name = "fsense"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    _write_iar_toml(repo_path, "zata-zhangtao-fsense")

    exit_code = main(["registry", "reinit", "--repo-id", "zata-zhangtao-fsense"])
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert "Reinitialized" in _strip_ansi(captured.out)
    config_text = config_path.read_text(encoding="utf-8")
    assert "[agent_runner.repositories.zata-zhangtao-fsense]" in config_text
    iar_toml_text = (repo_path / ".kedacode.toml").read_text(encoding="utf-8")
    assert 'remote = "origin"' in iar_toml_text


def test_main_registry_reinit_missing_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry reinit` should fail when repo_id is not in registry."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")

    exit_code = main(["registry", "reinit", "--repo-id", "no-such-repo"])
    captured = capsys.readouterr()

    assert exit_code == int(ExitCode.NOT_FOUND)
    assert "not found in registry" in _strip_ansi(captured.out + captured.err)


def test_main_registry_reinit_start_daemons_uses_config_directory_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry reinit --start-daemons` should spawn daemons from the directory containing the effective config.toml so they read the same registry as the parent CLI."""
    monkeypatch.chdir(tmp_path)
    repo_path = _init_bare_git_repository(tmp_path, "fsense")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n[agent_runner.repositories.zata-zhangtao-fsense]\n"
        f'path = "{repo_path}"\nenabled = true\ndisplay_name = "fsense"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    _write_iar_toml(repo_path, "zata-zhangtao-fsense")

    fake_context = MagicMock()
    fake_context.repo_id = "zata-zhangtao-fsense"

    fake_settings = MagicMock()
    fake_settings.console.runner_command = ["kc"]

    from backend.infrastructure.console.process_supervisor import RunnerProcessRecord

    def _fake_start(
        *,
        repo_id,
        kind,
        contexts,
        supervisor,
        runner_command,
        spawn_cwd,
        issue_number=None,
    ):
        return RunnerProcessRecord(
            process_id=f"fake-{kind.value}",
            repo_id=repo_id,
            kind=kind.value,
            pid=1,
            status="running",
            exit_code=None,
            log_path="/tmp/fake.log",
            command=tuple(runner_command) + (kind.value, "--repo-id", repo_id),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        )

    with (
        patch("backend.api.cli_registry.initialize_repository_local_config") as mock_init,
        patch(
            "backend.api.cli_registry.load_fresh_agent_runner_settings",
            return_value=fake_settings,
        ),
        patch(
            "backend.api.cli_registry.resolve_repository_targets_with_diagnostics",
            return_value=([fake_context], []),
        ),
        patch("backend.api.cli_registry.create_process_supervisor") as mock_create_supervisor,
        patch(
            "backend.api.cli_registry.start_runner_process",
            side_effect=_fake_start,
        ) as mock_start,
        patch(
            "backend.api.cli_registry.resolve_registry_config_toml_path",
            return_value=config_path,
        ) as mock_resolve_registry_config,
    ):
        mock_supervisor = MagicMock()
        mock_supervisor.list_processes.return_value = []
        mock_create_supervisor.return_value = mock_supervisor

        exit_code = main(
            [
                "registry",
                "reinit",
                "--repo-id",
                "zata-zhangtao-fsense",
                "--start-daemons",
            ]
        )
        captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert "Reinitialized" in _strip_ansi(captured.out)
    assert "Started daemon" in _strip_ansi(captured.out)
    assert "Started supervise-daemon" in _strip_ansi(captured.out)

    # The local config initializer was invoked as part of reinit.
    mock_init.assert_called_once()

    # spawn_cwd must come from the registry config.toml directory, not the repository path.
    mock_resolve_registry_config.assert_called_once()
    assert mock_start.call_count == 2
    for call in mock_start.call_args_list:
        assert call.kwargs["spawn_cwd"] == config_path.parent
        assert call.kwargs["spawn_cwd"] != repo_path
        assert isinstance(call.kwargs["kind"], RunnerProcessKind)


def test_main_registry_remove_deletes_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry remove` should drop the entry and never touch the repository."""
    monkeypatch.chdir(tmp_path)
    repo_path = _init_bare_git_repository(tmp_path, "fsense")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n[agent_runner.repositories.zata-zhangtao-fsense]\n"
        f'path = "{repo_path}"\nenabled = true\ndisplay_name = "fsense"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))
    # 用临时 pidfile 隔离，避免测试停掉真实 ~/.kedacode/processes.json 里的进程。
    monkeypatch.setattr(
        cli_registry,
        "create_process_supervisor",
        lambda: PidfileProcessSupervisor(
            registry_path=tmp_path / "processes.json",
            log_dir=tmp_path / "process-logs",
        ),
    )

    exit_code = main(["registry", "remove", "--repo-id", "zata-zhangtao-fsense"])
    captured = _strip_ansi(capsys.readouterr().out)

    assert exit_code == 0
    assert "Removed" in captured
    assert "left untouched" in captured
    config_text = config_path.read_text(encoding="utf-8")
    assert "[agent_runner.repositories.zata-zhangtao-fsense]" not in config_text
    assert (repo_path / ".git").exists()


def test_cli_parser_registry_list() -> None:
    """registry list should be recognized as a subcommand."""
    parser = build_parser()
    parsed = parser.parse_args(["registry", "list"])
    assert parsed.command == "registry"
    assert parsed.registry_command == "list"


def test_main_registry_list_shows_repositories_and_daemon_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry list` should print registered repos and daemon status."""
    monkeypatch.chdir(tmp_path)
    repo_a = _init_bare_git_repository(tmp_path, "repo-a")
    repo_b = _init_bare_git_repository(tmp_path, "repo-b")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n"
        "[agent_runner.repositories.repo-a]\n"
        f'path = "{repo_a}"\nenabled = true\ndisplay_name = "Repo A"\n'
        "[agent_runner.repositories.repo-b]\n"
        f'path = "{repo_b}"\nenabled = true\ndisplay_name = "Repo B"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    from backend.infrastructure.console.process_supervisor import RunnerProcessRecord

    records = [
        RunnerProcessRecord(
            process_id="daemon-1",
            repo_id="repo-a",
            kind="daemon",
            pid=123,
            status="running",
            exit_code=None,
            log_path="/tmp/d1.log",
            command=("kc", "daemon"),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        ),
        RunnerProcessRecord(
            process_id="review-1",
            repo_id="repo-a",
            kind="review_daemon",
            pid=124,
            status="running",
            exit_code=None,
            log_path="/tmp/r1.log",
            command=("kc", "review-daemon"),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        ),
    ]

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = records
    mock_supervisor.list_unmanaged_processes.return_value = []

    with patch(
        "backend.api.cli_registry.create_process_supervisor",
        return_value=mock_supervisor,
    ):
        exit_code = main(["registry", "list"])

    captured = capsys.readouterr()
    output = _strip_ansi(captured.out)

    assert exit_code == 0, captured.err
    assert "repo-a" in output
    assert "Repo A" in output
    assert repo_a.name in output
    assert "repo-b" in output
    assert "Repo B" in output
    assert repo_b.name in output
    assert "running" in output
    assert "daemon-1" in output
    assert "review-1" in output
    assert "stopped" in output


def test_main_registry_list_empty_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry list` should succeed with an empty registry."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = []
    mock_supervisor.list_unmanaged_processes.return_value = []

    with patch(
        "backend.api.cli_registry.create_process_supervisor",
        return_value=mock_supervisor,
    ):
        exit_code = main(["registry", "list"])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "Registered repositories" in _strip_ansi(captured.out)


def test_cli_parser_registry_start() -> None:
    """registry start should accept --repo-id, --all, and --no-review-daemon."""
    parser = build_parser()
    parsed = parser.parse_args(
        ["registry", "start", "--repo-id", "keda-main", "--no-review-daemon"]
    )
    assert parsed.command == "registry"
    assert parsed.registry_command == "start"
    assert parsed.repo_id == "keda-main"
    assert parsed.all is False
    assert parsed.no_review_daemon is True

    parsed_all = parser.parse_args(["registry", "start", "--all"])
    assert parsed_all.all is True
    assert parsed_all.repo_id is None


def test_cli_parser_registry_stop() -> None:
    """registry stop should accept --repo-id, --all, and --no-review-daemon."""
    parser = build_parser()
    parsed = parser.parse_args(["registry", "stop", "--repo-id", "keda-main", "--no-review-daemon"])
    assert parsed.command == "registry"
    assert parsed.registry_command == "stop"
    assert parsed.repo_id == "keda-main"
    assert parsed.all is False
    assert parsed.no_review_daemon is True

    parsed_all = parser.parse_args(["registry", "stop", "--all"])
    assert parsed_all.all is True
    assert parsed_all.repo_id is None


def test_main_registry_start_single_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry start --repo-id` should start daemon + review-daemon."""
    monkeypatch.chdir(tmp_path)
    repo_path = _init_bare_git_repository(tmp_path, "keda")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n"
        "[agent_runner.repositories.keda-main]\n"
        f'path = "{repo_path}"\n'
        "enabled = true\n"
        'display_name = "Keda Main"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    fake_context = MagicMock()
    fake_context.repo_id = "keda-main"

    fake_settings = MagicMock()
    fake_settings.console.runner_command = ["kc"]
    fake_settings.repositories = {
        "keda-main": MagicMock(path=str(repo_path), enabled=True),
    }

    from backend.infrastructure.console.process_supervisor import RunnerProcessRecord

    def _fake_start(
        *,
        repo_id,
        kind,
        contexts,
        supervisor,
        runner_command,
        spawn_cwd,
        issue_number=None,
    ):
        return RunnerProcessRecord(
            process_id=f"fake-{kind.value}",
            repo_id=repo_id,
            kind=kind.value,
            pid=1,
            status="running",
            exit_code=None,
            log_path="/tmp/fake.log",
            command=tuple(runner_command) + (kind.value, "--repo-id", repo_id),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        )

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = []

    with (
        patch(
            "backend.api.cli_registry.load_fresh_agent_runner_settings",
            return_value=fake_settings,
        ),
        patch(
            "backend.api.cli_registry.resolve_repository_targets_with_diagnostics",
            return_value=([fake_context], []),
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
        patch(
            "backend.api.cli_registry.start_runner_process",
            side_effect=_fake_start,
        ) as mock_start,
    ):
        exit_code = main(["registry", "start", "--repo-id", "keda-main"])
        captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert mock_start.call_count == 2
    kinds = {call.kwargs["kind"] for call in mock_start.call_args_list}
    assert kinds == {RunnerProcessKind.DAEMON, RunnerProcessKind.REVIEW_DAEMON}
    for call in mock_start.call_args_list:
        assert call.kwargs["repo_id"] == "keda-main"
        assert call.kwargs["spawn_cwd"] == config_path.parent
        assert call.kwargs["spawn_cwd"] != repo_path


def test_main_registry_start_no_review_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry start --repo-id --no-review-daemon` should only start daemon."""
    monkeypatch.chdir(tmp_path)
    repo_path = _init_bare_git_repository(tmp_path, "keda")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n"
        "[agent_runner.repositories.keda-main]\n"
        f'path = "{repo_path}"\n'
        "enabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    fake_settings = MagicMock()
    fake_settings.console.runner_command = ["kc"]
    fake_settings.repositories = {
        "keda-main": MagicMock(path=str(repo_path), enabled=True),
    }

    from backend.infrastructure.console.process_supervisor import RunnerProcessRecord

    def _fake_start(
        *,
        repo_id,
        kind,
        contexts,
        supervisor,
        runner_command,
        spawn_cwd,
        issue_number=None,
    ):
        return RunnerProcessRecord(
            process_id=f"fake-{kind.value}",
            repo_id=repo_id,
            kind=kind.value,
            pid=1,
            status="running",
            exit_code=None,
            log_path="/tmp/fake.log",
            command=tuple(runner_command) + (kind.value, "--repo-id", repo_id),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        )

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = []

    with (
        patch(
            "backend.api.cli_registry.load_fresh_agent_runner_settings",
            return_value=fake_settings,
        ),
        patch(
            "backend.api.cli_registry.resolve_repository_targets_with_diagnostics",
            return_value=([], []),
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
        patch(
            "backend.api.cli_registry.start_runner_process",
            side_effect=_fake_start,
        ) as mock_start,
    ):
        exit_code = main(["registry", "start", "--repo-id", "keda-main", "--no-review-daemon"])
        captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert mock_start.call_count == 1
    assert mock_start.call_args.kwargs["kind"] == RunnerProcessKind.DAEMON


def test_main_registry_start_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry start --all` should start daemons for all enabled repos."""
    monkeypatch.chdir(tmp_path)
    repo_a = _init_bare_git_repository(tmp_path, "repo-a")
    repo_b = _init_bare_git_repository(tmp_path, "repo-b")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n"
        f'[agent_runner.repositories.repo-a]\npath = "{repo_a}"\nenabled = true\n'
        f'[agent_runner.repositories.repo-b]\npath = "{repo_b}"\nenabled = true\n'
        "[agent_runner.repositories.disabled-repo]\n"
        f'path = "{tmp_path / "disabled"}"\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    fake_settings = MagicMock()
    fake_settings.console.runner_command = ["kc"]
    fake_settings.repositories = {
        "repo-a": MagicMock(path=str(repo_a), enabled=True),
        "repo-b": MagicMock(path=str(repo_b), enabled=True),
        "disabled-repo": MagicMock(path=str(tmp_path / "disabled"), enabled=False),
    }

    from backend.infrastructure.console.process_supervisor import RunnerProcessRecord

    def _fake_start(
        *,
        repo_id,
        kind,
        contexts,
        supervisor,
        runner_command,
        spawn_cwd,
        issue_number=None,
    ):
        return RunnerProcessRecord(
            process_id=f"fake-{repo_id}-{kind.value}",
            repo_id=repo_id,
            kind=kind.value,
            pid=1,
            status="running",
            exit_code=None,
            log_path="/tmp/fake.log",
            command=tuple(runner_command) + (kind.value, "--repo-id", repo_id),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        )

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = []

    with (
        patch(
            "backend.api.cli_registry.load_fresh_agent_runner_settings",
            return_value=fake_settings,
        ),
        patch(
            "backend.api.cli_registry.resolve_repository_targets_with_diagnostics",
            return_value=([], []),
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
        patch(
            "backend.api.cli_registry.start_runner_process",
            side_effect=_fake_start,
        ) as mock_start,
    ):
        exit_code = main(["registry", "start", "--all"])
        captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert mock_start.call_count == 4
    started_repo_ids = {call.kwargs["repo_id"] for call in mock_start.call_args_list}
    assert started_repo_ids == {"repo-a", "repo-b"}


def test_main_registry_stop_single_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry stop --repo-id` should stop daemon + review-daemon."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    from backend.infrastructure.console.process_supervisor import RunnerProcessRecord

    records = [
        RunnerProcessRecord(
            process_id="daemon-1",
            repo_id="keda-main",
            kind="daemon",
            pid=123,
            status="running",
            exit_code=None,
            log_path="/tmp/d1.log",
            command=("kc", "daemon", "--repo-id", "keda-main"),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        ),
        RunnerProcessRecord(
            process_id="review-1",
            repo_id="keda-main",
            kind="review_daemon",
            pid=124,
            status="running",
            exit_code=None,
            log_path="/tmp/r1.log",
            command=("kc", "review-daemon", "--repo-id", "keda-main"),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        ),
        RunnerProcessRecord(
            process_id="daemon-2",
            repo_id="fsense",
            kind="daemon",
            pid=125,
            status="running",
            exit_code=None,
            log_path="/tmp/d2.log",
            command=("kc", "daemon", "--repo-id", "fsense"),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        ),
    ]

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = records

    with (
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
        patch(
            "backend.api.cli_registry.stop_runner_process",
        ) as mock_stop,
    ):
        exit_code = main(["registry", "stop", "--repo-id", "keda-main"])
        captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    stopped_ids = {call.kwargs["process_id"] for call in mock_stop.call_args_list}
    assert stopped_ids == {"daemon-1", "review-1"}


def test_main_registry_stop_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry stop --all` should stop all running daemon processes."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    from backend.infrastructure.console.process_supervisor import RunnerProcessRecord

    records = [
        RunnerProcessRecord(
            process_id="daemon-1",
            repo_id="keda-main",
            kind="daemon",
            pid=123,
            status="running",
            exit_code=None,
            log_path="/tmp/d1.log",
            command=("kc", "daemon", "--repo-id", "keda-main"),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        ),
        RunnerProcessRecord(
            process_id="review-1",
            repo_id="keda-main",
            kind="review_daemon",
            pid=124,
            status="running",
            exit_code=None,
            log_path="/tmp/r1.log",
            command=("kc", "review-daemon", "--repo-id", "keda-main"),
            started_at="2026-06-22T00:00:00+00:00",
            stopped_at=None,
        ),
    ]

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = records

    with (
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
        patch(
            "backend.api.cli_registry.stop_runner_process",
        ) as mock_stop,
    ):
        exit_code = main(["registry", "stop", "--all"])
        captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert mock_stop.call_count == 2


def test_main_registry_start_rejects_disabled_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`kc registry start --repo-id` should fail for disabled repositories."""
    monkeypatch.chdir(tmp_path)
    repo_path = _init_bare_git_repository(tmp_path, "keda")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n"
        "[agent_runner.repositories.keda-main]\n"
        f'path = "{repo_path}"\n'
        "enabled = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))

    fake_settings = MagicMock()
    fake_settings.repositories = {
        "keda-main": MagicMock(path=str(repo_path), enabled=False),
    }

    with patch(
        "backend.api.cli_registry.load_fresh_agent_runner_settings",
        return_value=fake_settings,
    ):
        exit_code = main(["registry", "start", "--repo-id", "keda-main"])
        captured = capsys.readouterr()

    assert exit_code == int(ExitCode.PERMISSION), captured.err
    assert "disabled" in _strip_ansi(captured.out + captured.err)


def test_cli_parser_workflow_install() -> None:
    """workflow install should expose name + force + dry-run + common flags."""
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "workflow",
            "install",
            "preview",
            "--force",
            "--dry-run",
            "--repo",
            "/tmp/skip",
        ]
    )
    assert parsed.command == "workflow install"
    assert parsed.workflow_command == "install"
    assert parsed.name == "preview"
    assert parsed.force is True
    assert parsed.dry_run is True
    assert parsed.repo == "/tmp/skip"


def test_cli_parser_workflow_install_minimal() -> None:
    """workflow install without flags should default force/dry-run to False."""
    parser = build_parser()
    parsed = parser.parse_args(["workflow", "install", "preview"])
    assert parsed.command == "workflow install"
    assert parsed.name == "preview"
    assert parsed.force is False
    assert parsed.dry_run is False
    assert getattr(parsed, "repo", None) is None


def test_main_workflow_install_unknown_name_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unknown workflow names must exit non-zero without writing files."""
    monkeypatch.chdir(tmp_path)
    _write_iar_toml(tmp_path, "demo")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")

    with (
        patch("backend.api.cli_helpers.detect_git_repository_root", return_value=tmp_path),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["workflow", "install", "missing"])

    assert exit_code == 1
    assert not (tmp_path / "deploy").exists()


def test_main_workflow_install_rejects_global_repo_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receiving --repo must reject and not write any template file."""
    monkeypatch.chdir(tmp_path)
    _write_iar_toml(tmp_path, "demo")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")

    exit_code = main(["workflow", "install", "preview", "--repo", str(tmp_path)])

    assert exit_code == int(ExitCode.USAGE)
    assert not (tmp_path / "deploy").exists()
    assert not (tmp_path / "scripts").exists()


def test_main_workflow_install_rejects_global_config_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receiving --config must reject and not write any template file."""
    monkeypatch.chdir(tmp_path)
    _write_iar_toml(tmp_path, "demo")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")

    exit_code = main(["workflow", "install", "preview", "--config", "/tmp/cfg"])

    assert exit_code == int(ExitCode.USAGE)
    assert not (tmp_path / "deploy").exists()


def test_main_workflow_install_rejects_global_repo_id_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receiving --repo-id must reject and not write any template file."""
    monkeypatch.chdir(tmp_path)
    _write_iar_toml(tmp_path, "demo")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")

    exit_code = main(["workflow", "install", "preview", "--repo-id", "demo"])

    assert exit_code == int(ExitCode.USAGE)
    assert not (tmp_path / "deploy").exists()


def test_cli_parser_takeover_defaults() -> None:
    """takeover should have sensible defaults."""
    parser = build_parser()
    parsed = parser.parse_args(["takeover"])
    assert parsed.command == "takeover"
    assert parsed.owner is None
    assert parsed.limit == 100
    assert parsed.clone_root is None
    assert parsed.repos == []
    assert parsed.no_start is False
    assert parsed.dry_run is False


def test_cli_parser_takeover_with_options() -> None:
    """takeover should accept all defined options."""
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "takeover",
            "--owner",
            "myorg",
            "--limit",
            "50",
            "--clone-root",
            "/tmp/repos",
            "--repos",
            "owner/repo-a",
            "owner/repo-b",
            "--no-start",
            "--dry-run",
        ]
    )
    assert parsed.command == "takeover"
    assert parsed.owner == "myorg"
    assert parsed.limit == 50
    assert parsed.clone_root == "/tmp/repos"
    assert parsed.repos == ["owner/repo-a", "owner/repo-b"]
    assert parsed.no_start is True
    assert parsed.dry_run is True


def test_main_takeover_rejects_unauthenticated_gh(capsys) -> None:
    """takeover should fail gracefully when gh is not authenticated."""
    from backend.api.cli import main

    auth_client = MagicMock()
    auth_client.check_auth_status.return_value = MagicMock(
        authenticated=False,
        failure_reason="not logged in",
        account=None,
    )

    with patch(
        "backend.api.cli_takeover.create_github_client",
        return_value=auth_client,
    ):
        exit_code = main(["takeover"])

    assert exit_code == 1
    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"
    assert "GitHub CLI" in combined
    assert "gh auth login" in combined


def test_main_takeover_noninteractive_repos(capsys, monkeypatch) -> None:
    """takeover --repos should bypass interactive selection and execute."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    auth_client = MagicMock()
    auth_client.check_auth_status.return_value = MagicMock(
        authenticated=True,
        failure_reason=None,
        account="user",
    )

    mock_result = MagicMock()
    mock_result.attempted = 1
    mock_result.succeeded = 1
    mock_result.started_daemons = 0
    mock_result.started_review_daemons = 0
    mock_result.repositories = (
        MagicMock(
            full_name="owner/repo-a",
            repo_id="owner-repo-a",
            repo_path=Path("/tmp/repos/owner/repo-a"),
            error=None,
        ),
    )

    with (
        patch(
            "backend.api.cli_takeover.create_github_client",
            return_value=auth_client,
        ),
        patch(
            "backend.api.cli_takeover.select_repositories_interactive",
            side_effect=AssertionError("should not be called in non-interactive mode"),
        ),
        patch(
            "backend.api.cli_takeover.execute_takeover",
            return_value=mock_result,
        ) as mock_execute,
    ):
        exit_code = main(
            [
                "takeover",
                "--repos",
                "owner/repo-a",
                "--clone-root",
                "/tmp/repos",
                "--no-start",
            ]
        )

    assert exit_code == 0
    mock_execute.assert_called_once()
    captured = capsys.readouterr()
    assert "Takeover complete" in captured.out


def test_cli_parser_logs() -> None:
    """`kc logs` should accept --repo-id, --kind, --lines, --follow."""
    parser = build_parser()
    parsed = parser.parse_args(
        ["logs", "--repo-id", "keda", "--kind", "review_daemon", "--lines", "50", "-f"]
    )
    assert parsed.command == "logs"
    assert parsed.repo_id == "keda"
    assert parsed.kind == "review_daemon"
    assert parsed.lines == 50
    assert parsed.follow is True


def test_cli_parser_logs_defaults() -> None:
    """`kc logs` should default --kind=None (normalized to daemon downstream), --lines=200, --follow=False.

    The parser keeps ``kind=None`` when the flag is absent so the logs handler
    can distinguish "user explicitly passed --kind" (mutually exclusive with
    --issue) from the daemon default.
    """
    parser = build_parser()
    parsed = parser.parse_args(["logs", "--repo-id", "keda"])
    assert parsed.command == "logs"
    assert parsed.kind is None
    assert parsed.lines == 200
    assert parsed.follow is False


def test_cli_parser_logs_issue_flag() -> None:
    """`kc logs --issue N` should parse an issue number without touching --kind."""
    parser = build_parser()
    parsed = parser.parse_args(["logs", "--repo-id", "keda", "--issue", "42"])
    assert parsed.issue == 42
    assert parsed.kind is None


def test_main_logs_prints_last_n_lines(capsys, monkeypatch) -> None:
    """`kc logs` should print the last N lines of a managed process log."""
    from backend.api.cli import main
    from backend.core.shared.interfaces.runner_console import ProcessLogChunk

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    tmp_log = Path("/tmp/iar-test-logs-output.log")
    log_lines = [f"line {i}\n" for i in range(1, 31)]
    tmp_log.write_text("".join(log_lines), encoding="utf-8")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"

    record = RunnerProcessRecord(
        process_id="proc-logs",
        repo_id="repo",
        kind=RunnerProcessKind.DAEMON,
        pid=1111,
        status="running",
        exit_code=None,
        log_path=str(tmp_log),
        command=("kc", "daemon", "--repo-id", "repo"),
        started_at="2026-06-23T10:00:00Z",
        stopped_at=None,
    )

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = [record]
    mock_supervisor.list_unmanaged_processes.return_value = []
    mock_supervisor.get_process.return_value = record

    def _fake_read_log(process_id, *, offset, max_bytes):
        with open(tmp_log, "rb") as fh:
            fh.seek(offset)
            data = fh.read(max_bytes)
        return ProcessLogChunk(
            content=data.decode("utf-8", errors="replace"),
            next_offset=offset + len(data),
            eof=True,
        )

    mock_supervisor.read_log.side_effect = _fake_read_log

    try:
        with (
            patch(
                "backend.api.cli_registry.resolve_repository_targets",
                return_value=[mock_context],
            ),
            patch(
                "backend.api.cli_registry.create_process_supervisor",
                return_value=mock_supervisor,
            ),
        ):
            exit_code = main(["logs", "--repo-id", "repo", "--lines", "5"])

        captured = capsys.readouterr()
        output = _strip_ansi(captured.out)
        assert exit_code == 0
        assert "line 26" in output
        assert "line 27" in output
        assert "line 28" in output
        assert "line 29" in output
        assert "line 30" in output
    finally:
        if tmp_log.exists():
            tmp_log.unlink()


def test_main_logs_fallback_no_running_process(capsys, monkeypatch) -> None:
    """`kc logs` should print fallback guidance when no running process exists."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = []
    mock_supervisor.list_unmanaged_processes.return_value = []

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
    ):
        exit_code = main(["logs", "--repo-id", "repo"])

    captured = capsys.readouterr()
    output = _strip_ansi(captured.out)
    assert exit_code == 0
    assert "No running" in output or "app-" in output
    assert "Traceback" not in output


def test_main_logs_fallback_with_historical_log(capsys, monkeypatch) -> None:
    """`kc logs` should fall back to historical log path when no running process."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"

    historical_record = RunnerProcessRecord(
        process_id="hist-proc",
        repo_id="repo",
        kind=RunnerProcessKind.DAEMON,
        pid=2222,
        status="exited",
        exit_code=0,
        log_path="/var/log/kc/historical.log",
        command=("kc", "daemon", "--repo-id", "repo"),
        started_at="2026-06-20T10:00:00Z",
        stopped_at="2026-06-20T11:00:00Z",
    )

    mock_supervisor = MagicMock()
    mock_supervisor.list_processes.return_value = [historical_record]
    mock_supervisor.list_unmanaged_processes.return_value = []

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=mock_supervisor,
        ),
    ):
        exit_code = main(["logs", "--repo-id", "repo"])

    captured = capsys.readouterr()
    output = _strip_ansi(captured.out)
    assert exit_code == 0
    assert "Traceback" not in output


# ---------------------------------------------------------------------------
# Phase 0 deliberation wiring tests
# ---------------------------------------------------------------------------


def test_main_run_passes_transcript_runner_factory(monkeypatch) -> None:
    """``kc run`` should inject a transcript runner factory so Phase 0 runs."""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.run_agent_repositories_once", return_value=0) as mock_run,
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["run", "--all-ready", "--all"])

    assert exit_code == 0
    factory = mock_run.call_args.kwargs["transcript_runner_factory"]
    assert callable(factory), "transcript_runner_factory must be callable"


def test_main_daemon_passes_transcript_runner_factory(monkeypatch) -> None:
    """daemon 入口也会注入 transcript runner factory。"""
    from backend.api.cli import main

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    mock_context = MagicMock()
    mock_context.repo_path = Path("/tmp/repo")
    mock_context.repo_id = "repo"
    mock_context.display_name = "Repo"

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli.run_agent_daemon") as mock_daemon,
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch("backend.api.cli.acquire_daemon_locks", return_value=[]),
        patch("backend.api.cli.release_daemon_locks"),
    ):
        exit_code = main(["daemon", "--all"])

    assert exit_code == 0
    factory = mock_daemon.call_args.kwargs["transcript_runner_factory"]
    assert callable(factory)


def test_main_run_phase0_deliberation_real_entry_point(monkeypatch, tmp_path: Path) -> None:
    """A ``agent/deliberate`` Issue encountered by ``kc run`` gets a question list."""
    from backend.api.cli import main
    from backend.core.shared.models.agent_runner import (
        AppConfig,
        CommandResult,
        IssueSummary,
        RepositoryRunContext,
    )
    from tests.conftest import FakeGitHubClient

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    issue = IssueSummary(
        number=42,
        title="Discuss async X",
        url="https://github.com/example/repo/issues/42",
        body="Need to design X carefully",
        labels=("agent/deliberate",),
    )
    fake_github = FakeGitHubClient()

    def _seed(label: str, limit: int, state: str = "all") -> list[IssueSummary]:
        fake_github.calls.append(
            {
                "method": "list_issues_by_label",
                "label": label,
                "limit": limit,
                "state": state,
            }
        )
        return [issue] if label == "agent/deliberate" else []

    fake_github.list_issues_by_label = _seed  # type: ignore[assignment]

    config = AppConfig()
    mock_context = RepositoryRunContext(
        repo_id="repo",
        display_name="Repo",
        repo_path=tmp_path,
        config=config,
    )

    question_text = (
        "## 范围边界\n- Q1\n\n## 约束\n- Q2\n\n## 验收标准\n- Q3\n\n"
        "## 技术选型\n- Q4\n\n## 风险\n- Q5"
    )

    class _StubTranscript:
        def run(
            self,
            agent_name: str,
            prompt: str,
            *,
            cwd: Path,
            event_sink,
            output_sink=None,
            display_sink=None,
            model_selection=None,
        ) -> CommandResult:
            return CommandResult(
                command=(agent_name,),
                return_code=0,
                stdout=question_text,
                stderr="",
            )

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client", return_value=fake_github),
        patch("backend.api.cli.create_github_client", return_value=fake_github),
        patch("backend.api.cli.create_content_generator"),
        patch("backend.api.cli.create_transcript_runner", return_value=_StubTranscript()),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        # Exit code may be non-zero (downstream phases may fail in this
        # sandbox), but Phase 0 should still have posted its comment.
        main(["run", "--all-ready", "--all"])

    comments = fake_github.list_issue_comments(42)
    assert len(comments) >= 1, "Phase 0 should post at least one comment"
    question_comment = comments[-1]
    assert "## 范围边界" in question_comment
    assert "phase=deliberation_question_posted" in question_comment
    assert "cycle=1" in question_comment


def test_main_run_dry_run_skips_deliberation_phase(monkeypatch, tmp_path: Path) -> None:
    """``kc run --dry-run`` must not actually run Phase 0 deliberation."""
    from backend.api.cli import main
    from backend.core.shared.models.agent_runner import (
        AppConfig,
        IssueSummary,
        RepositoryRunContext,
    )
    from tests.conftest import FakeGitHubClient

    monkeypatch.setenv("KEDACODE_SKIP_GH_AUTH_CHECK", "1")

    issue = IssueSummary(
        number=77,
        title="Discuss async Y",
        url="https://github.com/example/repo/issues/77",
        body="Body",
        labels=("agent/deliberate",),
    )
    fake_github = FakeGitHubClient()

    def _seed(label: str, limit: int, state: str = "all") -> list[IssueSummary]:
        fake_github.calls.append(
            {
                "method": "list_issues_by_label",
                "label": label,
                "limit": limit,
                "state": state,
            }
        )
        return [issue] if label == "agent/deliberate" else []

    fake_github.list_issues_by_label = _seed  # type: ignore[assignment]

    config = AppConfig()
    mock_context = RepositoryRunContext(
        repo_id="repo",
        display_name="Repo",
        repo_path=tmp_path,
        config=config,
    )

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[mock_context],
        ),
        patch("backend.api.cli_helpers.create_github_client", return_value=fake_github),
        patch("backend.api.cli.create_github_client", return_value=fake_github),
        patch("backend.api.cli.create_content_generator"),
        patch("backend.api.cli.create_transcript_runner"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        exit_code = main(["run", "--all-ready", "--all", "--dry-run"])

    assert exit_code == 0
    comments = fake_github.list_issue_comments(77)
    assert comments == [], "dry-run must not post any Issue comments"


# ---------------------------------------------------------------------------
# REPL (`kc` no args / `kc repl`)
# ---------------------------------------------------------------------------


def _init_iar_repo(tmp_path: Path) -> Path:
    """Create a bare git repository and seed ``.kedacode.toml``."""
    from tests.test_agent_runner_cli import _init_bare_git_repository

    repo_path = _init_bare_git_repository(tmp_path, "repl-test-repo")
    (repo_path / ".kedacode.toml").write_text(
        "[agent_runner]\n"
        "[agent_runner.repository]\n"
        'id = "repl-test-repo"\n'
        'display_name = "REPL Test"\n',
        encoding="utf-8",
    )
    return repo_path


def test_main_repl_subcommand_starts_session(monkeypatch, tmp_path: Path) -> None:
    """`kc repl --repo <path>` should drive the REPL use case once."""
    from backend.api.cli import main
    from backend.core.use_cases.repl_session import (
        EXIT_COMMAND,
        IAR_EXEC_CLOSE_MARKER,
        IAR_EXEC_OPEN_MARKER,
    )

    repo_path = _init_iar_repo(tmp_path)

    user_inputs = iter(["sync labels", EXIT_COMMAND])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))

    with (
        patch(
            "backend.api.cli.resolve_repository_targets",
        ) as mock_resolve,
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.create_content_generator") as mock_content,
        patch("backend.api.cli.create_repl_command_executor") as mock_executor_factory,
    ):
        mock_context = MagicMock()
        mock_context.repo_path = repo_path
        mock_context.repo_id = "repl-test-repo"
        mock_context.display_name = "REPL Test"
        mock_context.config.repl.default_agent = "claude"
        mock_resolve.return_value = [mock_context]

        mock_content.return_value.generate.return_value = MagicMock(
            return_code=0,
            stdout=(
                "I'll sync the labels.\n"
                f"{IAR_EXEC_OPEN_MARKER} kc labels sync --dry-run "
                f"{IAR_EXEC_CLOSE_MARKER}"
            ),
            stderr="",
        )
        mock_executor = MagicMock()
        mock_executor.execute.return_value = MagicMock(
            argv=("labels", "sync", "--dry-run"),
            return_code=0,
            stdout="Labels synced",
            stderr="",
            rejected=False,
            rejection_reason="",
            confirmation_prompted=False,
            confirmation_granted=None,
        )
        mock_executor_factory.return_value = mock_executor

        with patch(
            "backend.api.cli.run_repl_session",
            return_value=0,
        ) as mock_run:
            exit_code = main(["repl", "--repo", str(repo_path)])

    assert exit_code == 0
    mock_run.assert_called_once()
    inputs_obj = mock_run.call_args.args[0]
    deps_obj = mock_run.call_args.args[1]
    assert inputs_obj.agent == "claude"
    assert deps_obj.github_client is not None
    assert deps_obj.command_executor is mock_executor


def test_main_repl_rejects_auto_agent_override(monkeypatch, tmp_path: Path, capsys) -> None:
    """`kc repl --agent auto` should fall back to the config default."""
    from backend.api.cli import main

    repo_path = _init_iar_repo(tmp_path)

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "/exit")

    with (
        patch(
            "backend.api.cli.resolve_repository_targets",
        ) as mock_resolve,
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.create_content_generator"),
        patch("backend.api.cli.create_repl_command_executor") as mock_executor_factory,
        patch(
            "backend.core.use_cases.repl_session.run_repl_session",
            return_value=0,
        ) as mock_run,
    ):
        mock_context = MagicMock()
        mock_context.repo_path = repo_path
        mock_context.repo_id = "repl-test-repo"
        mock_context.display_name = "REPL Test"
        mock_context.config.repl.default_agent = "claude"
        mock_resolve.return_value = [mock_context]
        mock_executor_factory.return_value = MagicMock()

        with patch(
            "backend.api.cli.run_repl_session",
            return_value=0,
        ) as mock_run:
            exit_code = main(["repl", "--agent", "auto", "--repo", str(repo_path)])

    assert exit_code == 0
    inputs = mock_run.call_args.args[0]
    assert inputs.agent == "claude"


def test_main_no_args_non_tty_shows_help_and_exits_nonzero(monkeypatch, capsys) -> None:
    """`kc` with no args in non-TTY mode prints help and exits non-zero."""
    from backend.api.cli import main

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    combined = captured.out + captured.err
    assert "Usage:" in combined or "kc" in combined


def test_main_no_args_tty_launches_native_executor(monkeypatch, tmp_path: Path) -> None:
    """`kc` with no args in TTY mode hands the terminal to the native executor.

    Issue #256 改了这条路由：TTY 裸入口不再是 Keda REPL，而是配置的执行器原生
    TUI（``kc repl`` 保留旧 REPL，见 ``test_main_repl_dispatches_repl_session``）。
    这里断言的是**路由**：走到 session 入口、把执行器进程的退出码原样交回，
    且旧 REPL 一次都没被调用。
    """
    from backend.api.cli import main

    repo_path = _init_iar_repo(tmp_path)

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    fake_plan = SimpleNamespace(agent_name="claude", cwd=repo_path)
    preparation = SimpleNamespace(plan=fake_plan, notices=[])
    launcher = MagicMock()
    launcher.launch.return_value = SimpleNamespace(exit_code=7)

    with (
        patch(
            "backend.api.cli.resolve_repository_targets",
        ) as mock_resolve,
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.create_content_generator"),
        patch("backend.api.cli.run_repl_session") as mock_repl,
        patch(
            "backend.api.cli_parsed_commands.agent.prepare_native_session_plan",
            return_value=preparation,
        ) as mock_prepare,
        patch(
            "backend.api.cli_parsed_commands.agent.create_foreground_session_launcher",
            return_value=launcher,
        ),
    ):
        mock_context = MagicMock()
        mock_context.repo_path = repo_path
        mock_context.repo_id = "session-test-repo"
        mock_context.display_name = "Session Test"
        mock_resolve.return_value = [mock_context]

        exit_code = main(["--repo", str(repo_path)])

    assert exit_code == 7
    mock_repl.assert_not_called()
    mock_prepare.assert_called_once()
    launcher.launch.assert_called_once_with(fake_plan)


def test_main_repl_requires_initialized_repo(monkeypatch, tmp_path: Path, capsys) -> None:
    """Uninitialized repos must show a friendly error."""
    from backend.api.cli import main
    from backend.engines.agent_runner.repository_local import (
        IARRepositoryNotInitializedError,
    )

    repo_path = _init_iar_repo(tmp_path)
    (repo_path / ".kedacode.toml").unlink()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "/exit")

    with (
        patch(
            "backend.api.cli.resolve_repository_targets",
        ) as mock_resolve,
        patch("backend.api.cli.create_process_runner"),
    ):
        mock_context = MagicMock()
        mock_context.repo_path = repo_path
        mock_resolve.return_value = [mock_context]
        # Force require_iar_repository_initialized to raise
        with patch(
            "backend.api.cli.require_iar_repository_initialized",
            side_effect=IARRepositoryNotInitializedError(
                repo_root_path=repo_path,
                config_path=repo_path / ".kedacode.toml",
            ),
        ):
            exit_code = main(["repl", "--repo", str(repo_path)])

    captured = capsys.readouterr()
    assert exit_code == int(ExitCode.NOT_FOUND)
    combined = (captured.out + captured.err).lower()
    assert "init" in combined


# ---------------------------------------------------------------------------
# kc agent lifecycle / preset / fallback：写入前置校验（负控，不触达磁盘）
# ---------------------------------------------------------------------------


def _stub_lifecycle_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """把配置发现桩成内存 AppConfig，隔离开发者本机 config.toml。

    这些负控用例断言"写文件之前就失败"，因此只需 scope 解析与字段校验这两步真实，
    落盘与 fresh 读回由 tests/test_lifecycle_agents_console_api.py 覆盖。
    """
    from backend.core.shared.models.agent_runner import AppConfig

    monkeypatch.setattr(
        "backend.api.cli_parsed_commands.agent_lifecycle.load_fresh_agent_runner_settings",
        lambda: MagicMock(),
    )
    monkeypatch.setattr(
        "backend.api.cli_parsed_commands.agent_lifecycle.build_app_config_from_settings",
        lambda _settings: AppConfig(),
    )


def _lifecycle_ctx(
    *,
    repo_id: str | None = None,
    repo_override: str | None = None,
    **parsed_kwargs: object,
) -> ParsedCommandContext:
    """按写命令最小旗标构造 handler 上下文（其余注入项不触达）。"""
    import argparse

    defaults: dict[str, object] = {"scope": None, "stage": "review", "preset": "hot"}
    defaults.update(parsed_kwargs)
    return ParsedCommandContext(
        parsed=argparse.Namespace(**defaults),
        process_runner=None,
        runner_settings=None,
        repo_id=repo_id,
        repo_override=repo_override,
        github_client_factory=None,
        output_format=OUTPUT_FORMAT_TABLE,
    )


def test_agent_lifecycle_set_requires_explicit_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """写命令缺 --scope 时 USAGE 退出，绝不默认写全局。"""
    _stub_lifecycle_config(monkeypatch)
    with pytest.raises(CliError) as exc_info:
        run_agent_lifecycle_set_command(_lifecycle_ctx(scope=None))
    assert exc_info.value.code == ExitCode.USAGE
    assert "--scope" in str(exc_info.value)


def test_agent_lifecycle_set_repository_requires_repo_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scope=repository 未给仓库选择器时 USAGE 退出。"""
    _stub_lifecycle_config(monkeypatch)
    with pytest.raises(CliError) as exc_info:
        run_agent_lifecycle_set_command(_lifecycle_ctx(scope="repository"))
    assert exc_info.value.code == ExitCode.USAGE
    assert "repo" in str(exc_info.value).lower()


def test_agent_lifecycle_set_rejects_unknown_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """未知阶段键在使用文件前 USAGE 退出并列出合法闭集。"""
    _stub_lifecycle_config(monkeypatch)
    with pytest.raises(CliError) as exc_info:
        run_agent_lifecycle_set_command(_lifecycle_ctx(scope="global", stage="bogus"))
    assert exc_info.value.code == ExitCode.USAGE
    assert "unknown lifecycle stage" in str(exc_info.value)


def test_agent_fallback_candidate_add_requires_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选写命令缺 --scope 时 USAGE 退出。"""
    import argparse

    _stub_lifecycle_config(monkeypatch)
    ctx = ParsedCommandContext(
        parsed=argparse.Namespace(scope=None, agent="claude", preset=None, position=None),
        process_runner=None,
        runner_settings=None,
        repo_id=None,
        repo_override=None,
        github_client_factory=None,
        output_format=OUTPUT_FORMAT_TABLE,
    )
    with pytest.raises(CliError) as exc_info:
        run_agent_fallback_candidate_add_command(ctx)
    assert exc_info.value.code == ExitCode.USAGE


def test_agent_lifecycle_set_rejects_repo_selector_with_global_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--scope global 与仓库选择器互斥：误用在使用文件前 USAGE 退出，不静默写机器级配置。"""
    _stub_lifecycle_config(monkeypatch)
    for selector_kwargs in ({"repo_id": "keda-main"}, {"repo_override": "/tmp/somewhere"}):
        with pytest.raises(CliError) as exc_info:
            run_agent_lifecycle_set_command(_lifecycle_ctx(scope="global", **selector_kwargs))
        assert exc_info.value.code == ExitCode.USAGE
        assert "--scope global" in str(exc_info.value)


def test_agent_fallback_candidate_repository_scope_lands_in_repo_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """候选写命令 ``--scope repository`` 的落点是该仓库配置文件，机器级文件逐字节不变。

    回退候选数组表可按仓库声明，写后该仓库以自身数组整体接管机器级候选链；这条
    落点契约只由本测试钉住（HTTP API 恒写 ``config.toml``，页面回退区同样只写机器级），
    因此 ``docs/guides/model-presets.md`` §7.2 与随包 kedacode-operator skill 必须按同一
    口径描述，不得把它写成"CLI 也恒写全局"。
    """
    import tomllib

    repo_path = _init_bare_git_repository(tmp_path, "fbrepo")
    (repo_path / ".kedacode.toml").write_text("[agent_runner]\n", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n\n"
        "[agent_runner.repositories.fbrepo]\n"
        f'path = "{repo_path.resolve()}"\n'
        "enabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))
    global_bytes_before = config_path.read_bytes()

    exit_code = run_agent_fallback_candidate_add_command(
        _lifecycle_ctx(
            scope="repository", repo_id="fbrepo", agent="qoder", preset=None, position=None
        )
    )
    capsys.readouterr()

    assert exit_code == 0
    repository_config = tomllib.loads((repo_path / ".kedacode.toml").read_text(encoding="utf-8"))
    written_candidates = repository_config["agent_runner"]["runner"]["agent_fallback_candidates"]
    assert written_candidates[-1] == {"agent": "qoder"}
    assert config_path.read_bytes() == global_bytes_before


def _global_config_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> Path:
    """把机器级配置指向临时文件（真实发现、真实 TOML，不 stub）。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))
    return config_path


def test_agent_lifecycle_unset_requires_binding_in_target_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``lifecycle unset`` 解绑本层没声明的阶段：USAGE(2) 且文件字节不变，不能报成功。

    旧行为是 exit 0 + 零字节写入，调用方以为解绑生效，实际该阶段仍按上一层的绑定运行
    （PRD：不能生效的操作必须失败并保留原配置）。本层确有绑定的阶段仍能正常解绑。
    """
    config_path = _global_config_env(
        tmp_path,
        monkeypatch,
        '[agent_runner]\n[agent_runner.lifecycle_presets]\nreview = "hot"\n'
        '[agent_runner.presets.hot]\nagent = "claude"\n',
    )
    before = config_path.read_bytes()

    with pytest.raises(CliError) as exc_info:
        run_agent_lifecycle_unset_command(_lifecycle_ctx(scope="global", stage="planner"))
    assert exc_info.value.code == ExitCode.USAGE
    assert config_path.read_bytes() == before

    capsys.readouterr()
    assert run_agent_lifecycle_unset_command(_lifecycle_ctx(scope="global", stage="review")) == 0
    assert 'review = "hot"' not in config_path.read_text(encoding="utf-8")


def test_agent_lifecycle_unset_on_broken_config_fails_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目标配置不是合法 TOML：CLI 归成可诊断的 :class:`CliError`，原字节一字不动。

    没有这层映射时 ``tomlkit`` 的解析错误会一路冒泡成 traceback；有了它，命令给出
    "先修文件"的失败并保留原文件（写回在落盘前就停止）。
    """
    corrupt_text = "[agent_runner.lifecycle_presets\nreview = \n"
    config_path = _global_config_env(tmp_path, monkeypatch, corrupt_text)

    with pytest.raises(CliError) as exc_info:
        run_agent_lifecycle_unset_command(_lifecycle_ctx(scope="global", stage="review"))

    assert exc_info.value.code == ExitCode.GENERAL
    assert "不是合法的 TOML" in str(exc_info.value)
    assert config_path.read_text(encoding="utf-8") == corrupt_text


def test_agent_fallback_candidate_add_reads_baseline_under_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """候选链整段替换：读基线 → 校验 → 落盘必须同持一把写锁，否则丢并发更新。

    锁只保护 ``os.replace`` 那一下，保护不了一份过期的数组快照：基线在锁外读时，另一个
    写入者在那个窗口里 append 的候选会被本次替换原样抹掉，而命令仍返回成功。探针在基线
    读取时抢一次 ``LOCK_EX | LOCK_NB``——抢不到才说明整段操作被同一把锁覆盖。
    """
    import fcntl

    from backend.api.cli_parsed_commands import agent_lifecycle as agent_lifecycle_commands

    config_path = _global_config_env(tmp_path, monkeypatch, "[agent_runner]\n")
    lock_path = config_path.with_name(config_path.name + ".lock")
    lock_held_at_baseline: list[bool] = []
    original_reader = agent_lifecycle_commands._current_candidate_entries

    def _probe(config):
        with open(lock_path, "a", encoding="utf-8") as probe_handle:
            try:
                fcntl.flock(probe_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock_held_at_baseline.append(True)
            else:
                fcntl.flock(probe_handle.fileno(), fcntl.LOCK_UN)
                lock_held_at_baseline.append(False)
        return original_reader(config)

    monkeypatch.setattr(
        "backend.api.cli_parsed_commands.agent_lifecycle._current_candidate_entries", _probe
    )

    assert (
        run_agent_fallback_candidate_add_command(
            _lifecycle_ctx(scope="global", agent="qoder", preset=None, position=None)
        )
        == 0
    )
    capsys.readouterr()

    assert lock_held_at_baseline == [True]

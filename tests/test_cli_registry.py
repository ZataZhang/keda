"""Tests for ``iar registry`` and ``iar logs`` subcommand implementations."""

from __future__ import annotations

import time
from importlib import import_module
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


import pytest

from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError
from backend.api.cli_registry import (
    _run_daemon_status_command,
    _run_logs_command,
    _run_registry_list_command,
    _run_registry_start_command,
)
from backend.api.cli_takeover import _start_daemons_for_repo
from backend.core.shared.interfaces.runner_console import (
    ProcessLogChunk,
    RunnerProcessKind,
)
from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.shared.models.agent_spec import AGENT_PROFILE_RUN
from backend.core.use_cases.agent_invocation_tracing import (
    INVOCATION_COVERAGE_INCOMPLETE_MARKER,
    INVOCATION_END_MARKER,
    INVOCATION_START_MARKER,
    PHASE_IMPLEMENTATION,
    InvocationStartRequest,
    bound_invocation_trace_context,
    build_invocation_trace_context,
    finish_invocation,
    start_invocation,
)
from backend.core.use_cases.agent_runner_output_routing import issue_output_routing
from backend.core.use_cases.issue_logs import ATTEMPT_END_MARKER
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

#: 日文件名由日志实现模块的时钟产出；``backend.infrastructure.logging`` 包的 ``logger``
#: 属性已被重绑成 ``Logger`` 单例，所以按模块对象取。
logger_impl_module = import_module("backend.infrastructure.logging.logger")


class _FakeArgs:
    """Lightweight namespace for CLI argument tests."""

    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def _make_record(
    *,
    process_id: str,
    repo_id: str,
    kind: str,
    status: str,
) -> MagicMock:
    record = MagicMock()
    record.process_id = process_id
    record.repo_id = repo_id
    record.kind = kind
    record.status = status
    return record


def _render_table(table) -> str:
    """Render a Rich Table to a plain string for assertions."""
    from rich.console import Console

    console = Console(force_terminal=False, width=200)
    with console.capture() as capture:
        console.print(table)
    return capture.get()


def test_registry_list_skips_non_running_records() -> None:
    """Exited/stopped records must not be displayed as running."""
    editor = MagicMock()
    editor.list_repositories.return_value = [
        MagicMock(
            repo_id="keda-main",
            display_name="Keda Main",
            path="/Users/zata/code/keda",
        )
    ]

    supervisor = MagicMock()
    supervisor.list_processes.return_value = [
        _make_record(
            process_id="abc123",
            repo_id="keda-main",
            kind=RunnerProcessKind.DAEMON.value,
            status="exited",
        ),
        _make_record(
            process_id="def456",
            repo_id="keda-main",
            kind=RunnerProcessKind.REVIEW_DAEMON.value,
            status="stopped",
        ),
    ]
    supervisor.list_unmanaged_processes.return_value = []

    captured_table = None

    def _capture_print(value) -> None:
        nonlocal captured_table
        captured_table = value

    with (
        patch("backend.api.cli_registry.create_registry_editor", return_value=editor),
        patch("backend.api.cli_registry.create_process_supervisor", return_value=supervisor),
        patch("backend.api.cli_registry.console.print", side_effect=_capture_print),
    ):
        exit_code = _run_registry_list_command(MagicMock())

    assert exit_code == 0
    assert captured_table is not None
    output = _render_table(captured_table)
    assert "stopped" in output
    assert "running" not in output
    assert "abc123" not in output
    assert "def456" not in output


def test_registry_start_uses_config_directory_as_spawn_cwd(tmp_path: Path) -> None:
    """Daemon processes must be spawned from the config.toml directory."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    config_dir = config_path.parent

    repo_path = tmp_path / "cloned-repo"
    repo_path.mkdir()

    settings = MagicMock()
    settings.repositories = {
        "zata-zhangtao-keda": MagicMock(path=str(repo_path), enabled=True),
    }
    settings.console.runner_command = ["iar"]

    context = MagicMock()
    context.repo_id = "zata-zhangtao-keda"

    start_mock = MagicMock()
    start_mock.return_value = MagicMock(process_id="proc123")

    parsed = _FakeArgs(
        repo_id="zata-zhangtao-keda",
        all=False,
        no_review_daemon=True,
    )

    with (
        patch(
            "backend.api.cli_registry.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
        patch("backend.api.cli_registry.create_process_supervisor"),
        patch(
            "backend.api.cli_registry.resolve_repository_targets_with_diagnostics",
            return_value=([context], []),
        ),
        patch(
            "backend.api.cli_registry.resolve_registry_config_toml_path",
            return_value=config_path,
        ),
        patch("backend.api.cli_registry.start_runner_process", new=start_mock),
    ):
        exit_code = _run_registry_start_command(parsed, MagicMock())

    assert exit_code == 0
    assert start_mock.call_count == 1
    call_kwargs = start_mock.call_args.kwargs
    assert call_kwargs["repo_id"] == "zata-zhangtao-keda"
    assert call_kwargs["spawn_cwd"] == config_dir
    assert call_kwargs["spawn_cwd"] != repo_path


def test_registry_start_rejects_missing_repo_path(tmp_path: Path) -> None:
    """Starting a daemon for a missing repository path should fail gracefully."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")

    missing_path = tmp_path / "does-not-exist"
    settings = MagicMock()
    settings.repositories = {
        "ghost-repo": MagicMock(path=str(missing_path), enabled=True),
    }
    settings.console.runner_command = ["iar"]

    start_mock = MagicMock()
    parsed = _FakeArgs(
        repo_id="ghost-repo",
        all=False,
        no_review_daemon=True,
    )

    with (
        patch(
            "backend.api.cli_registry.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
        patch("backend.api.cli_registry.create_process_supervisor"),
        patch(
            "backend.api.cli_registry.resolve_repository_targets_with_diagnostics",
            return_value=([], []),
        ),
        patch(
            "backend.api.cli_registry.resolve_registry_config_toml_path",
            return_value=config_path,
        ),
        patch("backend.api.cli_registry.start_runner_process", new=start_mock),
    ):
        exit_code = _run_registry_start_command(parsed, MagicMock())

    assert exit_code == 1
    start_mock.assert_not_called()


def test_registry_list_includes_running_records() -> None:
    """Running records must still appear as running."""
    editor = MagicMock()
    editor.list_repositories.return_value = [
        MagicMock(
            repo_id="keda-main",
            display_name="Keda Main",
            path="/Users/zata/code/keda",
        )
    ]

    supervisor = MagicMock()
    supervisor.list_processes.return_value = [
        _make_record(
            process_id="abc123",
            repo_id="keda-main",
            kind=RunnerProcessKind.DAEMON.value,
            status="running",
        ),
    ]
    supervisor.list_unmanaged_processes.return_value = []

    captured_table = None

    def _capture_print(value) -> None:
        nonlocal captured_table
        captured_table = value

    with (
        patch("backend.api.cli_registry.create_registry_editor", return_value=editor),
        patch("backend.api.cli_registry.create_process_supervisor", return_value=supervisor),
        patch("backend.api.cli_registry.console.print", side_effect=_capture_print),
    ):
        exit_code = _run_registry_list_command(MagicMock())

    assert exit_code == 0
    assert captured_table is not None
    output = _render_table(captured_table)
    assert "running (abc123)" in output


def test_registry_list_shows_unmanaged_running() -> None:
    """Unmanaged running processes must be shown as running (unmanaged)."""
    editor = MagicMock()
    editor.list_repositories.return_value = [
        MagicMock(
            repo_id="keda-main",
            display_name="Keda Main",
            path="/Users/zata/code/keda",
        )
    ]

    supervisor = MagicMock()
    supervisor.list_processes.return_value = []
    supervisor.list_unmanaged_processes.return_value = [
        _make_record(
            process_id="unmanaged-12345",
            repo_id="keda-main",
            kind=RunnerProcessKind.DAEMON.value,
            status="running",
        ),
    ]

    captured_table = None

    def _capture_print(value) -> None:
        nonlocal captured_table
        captured_table = value

    with (
        patch("backend.api.cli_registry.create_registry_editor", return_value=editor),
        patch("backend.api.cli_registry.create_process_supervisor", return_value=supervisor),
        patch("backend.api.cli_registry.console.print", side_effect=_capture_print),
    ):
        exit_code = _run_registry_list_command(MagicMock())

    assert exit_code == 0
    assert captured_table is not None
    output = _render_table(captured_table)
    assert "running" in output
    assert "unmanaged" in output
    assert "unmanaged-12345" not in output


def test_registry_list_prefers_managed_over_unmanaged() -> None:
    """When both managed and unmanaged processes exist, show managed status."""
    editor = MagicMock()
    editor.list_repositories.return_value = [
        MagicMock(
            repo_id="keda-main",
            display_name="Keda Main",
            path="/Users/zata/code/keda",
        )
    ]

    supervisor = MagicMock()
    supervisor.list_processes.return_value = [
        _make_record(
            process_id="managed-abc",
            repo_id="keda-main",
            kind=RunnerProcessKind.DAEMON.value,
            status="running",
        ),
    ]
    supervisor.list_unmanaged_processes.return_value = [
        _make_record(
            process_id="unmanaged-12345",
            repo_id="keda-main",
            kind=RunnerProcessKind.DAEMON.value,
            status="running",
        ),
    ]

    captured_table = None

    def _capture_print(value) -> None:
        nonlocal captured_table
        captured_table = value

    with (
        patch("backend.api.cli_registry.create_registry_editor", return_value=editor),
        patch("backend.api.cli_registry.create_process_supervisor", return_value=supervisor),
        patch("backend.api.cli_registry.console.print", side_effect=_capture_print),
    ):
        exit_code = _run_registry_list_command(MagicMock())

    assert exit_code == 0
    assert captured_table is not None
    output = _render_table(captured_table)
    assert "managed-abc" in output
    assert "unmanaged" not in output


def test_takeover_start_daemons_uses_config_directory_as_spawn_cwd(
    tmp_path: Path,
) -> None:
    """Takeover daemon start must spawn from the config.toml directory."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    config_dir = config_path.parent

    repo_path = tmp_path / "cloned-repo"
    repo_path.mkdir()

    settings = MagicMock()
    settings.console.runner_command = ["iar"]

    context = MagicMock()
    context.repo_id = "zata-zhangtao-keda"

    start_mock = MagicMock()
    start_mock.return_value = MagicMock(process_id="proc456")

    with (
        patch(
            "backend.api.cli_takeover.load_fresh_agent_runner_settings",
            return_value=settings,
        ),
        patch("backend.api.cli_takeover.create_process_supervisor"),
        patch(
            "backend.api.cli_takeover.resolve_repository_targets_with_diagnostics",
            return_value=([context], []),
        ),
        patch(
            "backend.api.cli_takeover.resolve_registry_config_toml_path",
            return_value=config_path,
        ),
        patch("backend.api.cli_takeover.start_runner_process", new=start_mock),
    ):
        _start_daemons_for_repo("zata-zhangtao-keda", repo_path)

    assert start_mock.call_count == 2
    for call in start_mock.call_args_list:
        assert call.kwargs["spawn_cwd"] == config_dir
        assert call.kwargs["spawn_cwd"] != repo_path


# ---------------------------------------------------------------------------
# iar logs tests
# ---------------------------------------------------------------------------


def _make_process_record(
    *,
    process_id: str,
    repo_id: str,
    kind: str,
    log_path: str,
    status: str = "running",
    started_at: str = "2026-06-23T00:00:00+00:00",
    pid: int = 1234,
) -> MagicMock:
    record = MagicMock()
    record.process_id = process_id
    record.repo_id = repo_id
    record.kind = kind
    record.pid = pid
    record.status = status
    record.exit_code = None
    record.log_path = log_path
    record.command = ("iar", f"{kind}", "--repo-id", repo_id)
    record.started_at = started_at
    record.stopped_at = None
    return record


def _make_log_file(parent: Path, name: str, lines: int) -> Path:
    """Write ``lines`` numbered log lines into a file and return its path."""
    log_file = parent / name
    with log_file.open("w", encoding="utf-8") as file_handle:
        for index in range(lines):
            file_handle.write(f"line {index:04d}: daemon step {index}\n")
    return log_file


def test_logs_command_prints_tail_lines(tmp_path: Path, capsys) -> None:
    """`iar logs --lines 5` prints the last 5 lines from the daemon log file."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = _make_log_file(log_dir, "daemon-abc123.log", 50)
    log_size = log_file.stat().st_size
    record = _make_process_record(
        process_id="abc123",
        repo_id="fixture-repo",
        kind=RunnerProcessKind.DAEMON.value,
        log_path=str(log_file),
    )

    context = MagicMock(repo_id="fixture-repo")
    supervisor = MagicMock()
    supervisor.list_processes.return_value = [record]
    tail_mock = MagicMock(
        side_effect=[
            ProcessLogChunk(
                content=log_file.read_text(encoding="utf-8")[-min(log_size, 64 * 1024) :],
                next_offset=log_size,
                eof=False,
            )
        ]
    )

    parsed = _FakeArgs(
        kind=RunnerProcessKind.DAEMON.value,
        lines=5,
        follow=False,
        repo_id="fixture-repo",
    )

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=supervisor,
        ),
        patch("backend.api.cli_registry.tail_runner_log", new=tail_mock),
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="fixture-repo",
            repo_override=None,
        )

    assert exit_code == 0
    output = capsys.readouterr().out
    out_lines = [line for line in output.splitlines() if line]
    assert out_lines[-1] == "line 0049: daemon step 49"
    assert len(out_lines) <= 5
    assert tail_mock.call_count == 1
    assert tail_mock.call_args.kwargs["process_id"] == "abc123"


def test_logs_command_fallback_when_no_records(tmp_path: Path) -> None:
    """No managed records prints the global app log fallback path.

    回退提示里的日文件名来自日志模块共享的 ``daily_log_path``，因此测试拨动的是
    日志模块的时钟而不是 CLI 自己的时间串。
    """
    context = MagicMock(repo_id="no-process-repo")
    supervisor = MagicMock()
    supervisor.list_processes.return_value = []

    parsed = _FakeArgs(
        kind=RunnerProcessKind.DAEMON.value,
        lines=10,
        follow=False,
        repo_id="no-process-repo",
    )

    printed_chunks: list[str] = []

    def _capture_console_print(*args, **kwargs) -> None:
        printable = args[0] if args else kwargs.get("__rich_object__", "")
        printed_chunks.append(str(printable))

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=supervisor,
        ),
        patch("backend.api.cli_registry.tail_runner_log") as tail_mock,
        patch.object(logger_impl_module, "datetime") as daily_clock_mock,
        patch("backend.api.cli_registry.console.print", side_effect=_capture_console_print),
    ):
        daily_clock_mock.now.return_value.strftime.return_value = "2026-06-24"
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="no-process-repo",
            repo_override=None,
        )

    assert exit_code == 0
    tail_mock.assert_not_called()
    assert any("logs/app-2026-06-24.log" in chunk for chunk in printed_chunks)


def test_logs_command_fallback_when_log_file_missing(tmp_path: Path) -> None:
    """If the per-process log file is gone, fall back to guidance."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    stale_log = log_dir / "daemon-stale.log"
    stale_log.write_text("only here for stat() before being unlinked\n")
    record = _make_process_record(
        process_id="stale-1",
        repo_id="stale-repo",
        kind=RunnerProcessKind.DAEMON.value,
        log_path=str(stale_log),
    )

    context = MagicMock(repo_id="stale-repo")
    supervisor = MagicMock()
    supervisor.list_processes.return_value = [record]
    stale_log.unlink()

    parsed = _FakeArgs(
        kind=RunnerProcessKind.DAEMON.value,
        lines=10,
        follow=False,
        repo_id="stale-repo",
    )

    printed_chunks: list[str] = []

    def _capture_console_print(*args, **kwargs) -> None:
        printable = args[0] if args else kwargs.get("__rich_object__", "")
        printed_chunks.append(str(printable))

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=supervisor,
        ),
        patch("backend.api.cli_registry.tail_runner_log") as tail_mock,
        patch.object(logger_impl_module, "datetime") as daily_clock_mock,
        patch("backend.api.cli_registry.console.print", side_effect=_capture_console_print),
    ):
        daily_clock_mock.now.return_value.strftime.return_value = "2026-06-24"
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="stale-repo",
            repo_override=None,
        )

    assert exit_code == 0
    tail_mock.assert_not_called()
    assert any("logs/app-2026-06-24.log" in chunk for chunk in printed_chunks)


def test_logs_command_follows_new_content_then_sigint(tmp_path: Path, capsys) -> None:
    """`--follow` keeps polling; KeyboardInterrupt exits with 0."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = _make_log_file(log_dir, "daemon-follow.log", 3)
    record = _make_process_record(
        process_id="follow-1",
        repo_id="follow-repo",
        kind=RunnerProcessKind.DAEMON.value,
        log_path=str(log_file),
    )

    context = MagicMock(repo_id="follow-repo")
    supervisor = MagicMock()
    supervisor.list_processes.return_value = [record]
    supervisor.get_process.return_value = record

    initial_size = log_file.stat().st_size

    chunks = [
        ProcessLogChunk(content="initial-tail\n", next_offset=initial_size, eof=False),
        ProcessLogChunk(content="appended-line\n", next_offset=initial_size + 14, eof=False),
    ]
    tail_mock = MagicMock(side_effect=chunks)

    # Append to the real file so stat() in next loop sees a stable value.
    log_file.write_text(
        log_file.read_text(encoding="utf-8") + "appended-line\n",
        encoding="utf-8",
    )

    sleeps = [0.0]

    def _fake_sleep(_seconds: float) -> None:
        sleeps[0] += 1
        # Trigger KeyboardInterrupt after the second poll completes.
        if sleeps[0] >= 2:
            raise KeyboardInterrupt

    parsed = _FakeArgs(
        kind=RunnerProcessKind.DAEMON.value,
        lines=2,
        follow=True,
        repo_id="follow-repo",
    )

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=supervisor,
        ),
        patch("backend.api.cli_registry.tail_runner_log", new=tail_mock),
        patch("backend.api.cli_registry.time.sleep", side_effect=_fake_sleep),
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="follow-repo",
            repo_override=None,
        )

    assert exit_code == 0
    assert tail_mock.call_count >= 2
    output = capsys.readouterr().out
    assert "appended-line" in output


def test_logs_command_kind_review_daemon(tmp_path: Path, capsys) -> None:
    """--kind review_daemon is forwarded to the supervisor filter."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = _make_log_file(log_dir, "review-daemon-xyz.log", 4)
    record = _make_process_record(
        process_id="xyz",
        repo_id="mixed-repo",
        kind=RunnerProcessKind.REVIEW_DAEMON.value,
        log_path=str(log_file),
    )

    context = MagicMock(repo_id="mixed-repo")
    supervisor = MagicMock()
    supervisor.list_processes.return_value = [
        _make_process_record(
            process_id="daemon-only",
            repo_id="mixed-repo",
            kind=RunnerProcessKind.DAEMON.value,
            log_path="",
        ),
        record,
    ]

    parsed = _FakeArgs(
        kind=RunnerProcessKind.REVIEW_DAEMON.value,
        lines=2,
        follow=False,
        repo_id="mixed-repo",
    )

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=supervisor,
        ),
        patch(
            "backend.api.cli_registry.tail_runner_log",
            return_value=ProcessLogChunk(
                content="line 0002: daemon step 2\nline 0003: daemon step 3\n",
                next_offset=log_file.stat().st_size,
                eof=False,
            ),
        ),
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="mixed-repo",
            repo_override=None,
        )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "line 0003: daemon step 3" in output


def test_logs_command_omitted_lines_uses_default(tmp_path: Path, capsys) -> None:
    """Default --lines 200 keeps all current content when below threshold."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = _make_log_file(log_dir, "daemon-default.log", 3)
    record = _make_process_record(
        process_id="default-1",
        repo_id="default-repo",
        kind=RunnerProcessKind.DAEMON.value,
        log_path=str(log_file),
    )

    context = MagicMock(repo_id="default-repo")
    supervisor = MagicMock()
    supervisor.list_processes.return_value = [record]

    parsed = _FakeArgs(
        kind=RunnerProcessKind.DAEMON.value,
        lines=None,  # simulate argparse default-missing
        follow=False,
        repo_id="default-repo",
    )

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=supervisor,
        ),
        patch(
            "backend.api.cli_registry.tail_runner_log",
            return_value=ProcessLogChunk(
                content=log_file.read_text(encoding="utf-8"),
                next_offset=log_file.stat().st_size,
                eof=False,
            ),
        ),
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="default-repo",
            repo_override=None,
        )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "line 0002: daemon step 2" in output


# ---------------------------------------------------------------------------
# iar daemon status log_path column
# ---------------------------------------------------------------------------


def test_daemon_status_table_includes_log_path_column(tmp_path: Path) -> None:
    """`iar daemon status` must add a `log_path` column for managed records."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "daemon-abc.log"
    log_file.write_text("hello", encoding="utf-8")

    managed = _make_process_record(
        process_id="managed-1",
        repo_id="daemon-repo",
        kind=RunnerProcessKind.DAEMON.value,
        log_path=str(log_file),
    )
    unmanaged = _make_process_record(
        process_id="unmanaged-1",
        repo_id="daemon-repo",
        kind=RunnerProcessKind.DAEMON.value,
        log_path="",
    )

    editor = MagicMock()
    editor.list_repositories.return_value = []
    context = MagicMock(repo_id="daemon-repo")
    supervisor = MagicMock()
    supervisor.list_processes.return_value = [managed]
    supervisor.list_unmanaged_processes.return_value = [unmanaged]

    parsed = _FakeArgs(repo_id="daemon-repo", all_repositories=False)

    captured_table = None

    def _capture_print(value) -> None:
        nonlocal captured_table
        captured_table = value

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.api.cli_registry.create_process_supervisor",
            return_value=supervisor,
        ),
        patch(
            "backend.api.cli_registry.create_registry_editor",
            return_value=editor,
        ),
        patch("backend.api.cli_registry.console.print", side_effect=_capture_print),
    ):
        exit_code = _run_daemon_status_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="daemon-repo",
            repo_override=None,
        )

    assert exit_code == 0
    assert captured_table is not None
    column_names = [column.header for column in captured_table.columns]
    assert "log_path" in column_names

    rendered = _render_table(captured_table)
    assert "log_path" in rendered
    # Unmanaged record has empty log_path and must render as "-" placeholder.
    assert " - " in rendered or rendered.endswith("-")


# ── `iar logs --issue`（按 Issue 读取实时输出） ──────────────────────────────


def _make_issue_attempt(repo_dir: Path, repo_id: str, issue_number: int, text: str) -> Path:
    """在测试仓库下写一个符合命名约定的 Issue 尝试日志。"""
    log_dir = repo_dir / "logs" / "agent-runner" / "issues" / repo_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"issue-{issue_number}-20260929-120000.log"
    path.write_text(text, encoding="utf-8")
    return path


def test_logs_command_issue_prints_tail_and_exits_without_follow(tmp_path: Path, capsys) -> None:
    """`iar logs --issue N`（不带 --follow）打印尾部窗口后立即退出，不轮询。"""
    repo_dir = tmp_path / "repo"
    _make_issue_attempt(repo_dir, "fixture-repo", 42, "line-1\nline-2\nline-3\n")

    context = MagicMock(repo_id="fixture-repo", repo_path=repo_dir)
    parsed = _FakeArgs(
        kind=None,
        lines=2,
        follow=False,
        repo_id="fixture-repo",
        issue=42,
    )

    with patch(
        "backend.api.cli_registry.resolve_repository_targets",
        return_value=[context],
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="fixture-repo",
            repo_override=None,
        )

    assert exit_code == 0
    output = capsys.readouterr().out
    # 尾部窗口按 --lines 截行：只看到最后两行。
    assert "line-2" in output
    assert "line-3" in output
    assert "line-1" not in output


def test_logs_command_issue_tail_window_skips_large_history(tmp_path: Path, capsys) -> None:
    """大于尾部窗口的历史内容不会被从头倾泻出来。"""
    repo_dir = tmp_path / "repo"
    content = "old-line\n" * 9000 + "tail-marker\n"
    _make_issue_attempt(repo_dir, "fixture-repo", 7, content)

    context = MagicMock(repo_id="fixture-repo", repo_path=repo_dir)
    parsed = _FakeArgs(
        kind=None,
        lines=200,
        follow=False,
        repo_id="fixture-repo",
        issue=7,
    )

    with patch(
        "backend.api.cli_registry.resolve_repository_targets",
        return_value=[context],
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="fixture-repo",
            repo_override=None,
        )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "tail-marker" in output
    # 输出被限制在尾部窗口内，而不是整份 9000 行日志。
    assert len(output.splitlines()) <= 200


def test_logs_command_issue_no_attempt_is_clean_exit(tmp_path: Path, capsys) -> None:
    """没有任何尝试日志时给明确空态并返回 0，不回退到进程日志。"""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    context = MagicMock(repo_id="fixture-repo", repo_path=repo_dir)
    parsed = _FakeArgs(
        kind=None,
        lines=50,
        follow=False,
        repo_id="fixture-repo",
        issue=99,
    )

    with patch(
        "backend.api.cli_registry.resolve_repository_targets",
        return_value=[context],
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="fixture-repo",
            repo_override=None,
        )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Issue #99" in output
    assert "暂无可用输出" in output


def test_logs_command_issue_and_kind_are_mutually_exclusive(tmp_path: Path) -> None:
    """`--issue` 与显式非默认 `--kind` 互斥，抛用法错误的 :class:`CliError`。"""
    context = MagicMock(repo_id="fixture-repo", repo_path=tmp_path)
    parsed = _FakeArgs(
        kind="review_daemon",
        lines=50,
        follow=False,
        repo_id="fixture-repo",
        issue=3,
    )

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[context],
        ),
        pytest.raises(CliError) as exc_info,
    ):
        _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="fixture-repo",
            repo_override=None,
        )

    assert exc_info.value.code is ExitCode.USAGE


def _run_issue_follow(repo_dir: Path, issue_number: int):
    """Run ``iar logs --issue N --follow`` against a fixture repo."""
    context = MagicMock(repo_id="fixture-repo", repo_path=repo_dir)
    parsed = _FakeArgs(
        kind=None,
        lines=200,
        follow=True,
        repo_id="fixture-repo",
        issue=issue_number,
    )
    with patch(
        "backend.api.cli_registry.resolve_repository_targets",
        return_value=[context],
    ):
        return _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="fixture-repo",
            repo_override=None,
        )


class _VirtualFollowClock:
    """假时钟：``monotonic`` / ``sleep`` 让时间随轮询推进，不随宿主速度漂移。

    只替换跟随逻辑真正用到的两个函数，其余属性透传给真实 ``time`` 模块——
    整个模块换成一个只有两个方法的对象，将来这条路径一旦用到 ``time.time()``
    就会以「假时钟缺属性」这种与业务无关的方式炸掉，报错还指不到真正的原因。
    """

    def __init__(self, real_time_module: ModuleType) -> None:
        """用真实 ``time`` 模块做兜底代理，并从零开始累计虚拟时间。"""
        self._real_time_module = real_time_module
        self._now = 0.0

    def monotonic(self) -> float:
        """Return the virtual elapsed seconds."""
        return self._now

    def sleep(self, seconds: float) -> None:
        """Advance the virtual clock by ``seconds``."""
        self._now += seconds

    def __getattr__(self, name: str) -> object:
        """未定义的属性（``time`` / ``perf_counter`` 等）交回真实模块。"""
        return getattr(self._real_time_module, name)


def test_logs_command_issue_follow_does_not_exit_on_bare_eof(tmp_path: Path, capsys) -> None:
    """回归：裸 EOF 不等于运行结束。

    旧实现一遇到 EOF 就 ``return``，于是 Agent 两次写入之间或重试间隔里的正常
    停顿会让 ``--follow`` 提前退出、丢掉后续输出。没有终态标记时它必须继续轮询，
    只在长时间无增长（旧日志兜底）后才收尾。
    """
    repo_dir = tmp_path / "repo"
    _make_issue_attempt(repo_dir, "fixture-repo", 42, "line-1\nline-2\n")

    # 空闲兜底走假时钟：真实 sleep 配几十毫秒的阈值等于测机器速度——testmon /
    # coverage 插桩或整套并发跑时，单次轮询就能超过阈值，"仍在跟随"的提示还没
    # 打印就已经落到兜底退出。时间只按轮询次数推进后，判定与宿主负载无关。
    follow_clock = _VirtualFollowClock(time)
    with (
        patch("backend.api.cli_registry.time", follow_clock),
        patch("backend.api.cli_registry._LOGS_POLL_INTERVAL_SECONDS", 1.0),
        patch("backend.api.cli_registry._FOLLOW_IDLE_EXIT_SECONDS", 5.0),
    ):
        exit_code = _run_issue_follow(repo_dir, 42)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "line-1" in output
    # 关键断言：它没有在首次 EOF 就退出，而是明确表示仍在跟随。
    assert "still following attempt" in output
    assert "without an end marker" in output


def test_logs_command_issue_follow_exits_on_attempt_end_marker(tmp_path: Path, capsys) -> None:
    """读到本尝试的终态标记即收尾——而不是靠空闲超时，更不是靠 EOF。"""
    repo_dir = tmp_path / "repo"
    _make_issue_attempt(
        repo_dir,
        "fixture-repo",
        42,
        f"line-1\n{ATTEMPT_END_MARKER}\n",
    )

    follow_clock = _VirtualFollowClock(time)
    with (
        patch("backend.api.cli_registry.time", follow_clock),
        patch("backend.api.cli_registry._LOGS_POLL_INTERVAL_SECONDS", 1.0),
        # 空闲兜底故意设得比首次读标记晚：若标记判定失效，退出会走兜底路径，
        # 下面的断言即失败。
        patch("backend.api.cli_registry._FOLLOW_IDLE_EXIT_SECONDS", 5.0),
    ):
        exit_code = _run_issue_follow(repo_dir, 42)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "finished; tail ends here" in output
    assert "without an end marker" not in output


def test_logs_command_issue_follow_prints_content_written_while_polling(
    tmp_path: Path, capsys
) -> None:
    """轮询期间新增的内容必须被送达，标记随后才结束跟随。"""
    repo_dir = tmp_path / "repo"
    attempt_path = _make_issue_attempt(repo_dir, "fixture-repo", 42, "early-line\n")
    sleep_calls: list[float] = []

    def _grow_then_return(_seconds: float) -> None:
        sleep_calls.append(_seconds)
        if len(sleep_calls) == 1:
            with attempt_path.open("a", encoding="utf-8") as handle:
                handle.write(f"late-line\n{ATTEMPT_END_MARKER}\n")

    with (
        patch("backend.api.cli_registry.time.sleep", side_effect=_grow_then_return),
        patch("backend.api.cli_registry._FOLLOW_IDLE_EXIT_SECONDS", 0.2),
    ):
        exit_code = _run_issue_follow(repo_dir, 42)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "early-line" in output
    assert "late-line" in output
    assert "finished; tail ends here" in output


# ── Agent 调用标记经既有 `iar logs --issue` 入口可见（Issue #242）─────────────


def _produce_invocation_attempt_log(
    repo_dir: Path,
    issue_number: int,
    *,
    run_history_store: object | None = None,
) -> Path:
    """用真输出路由 + 真观测模块产出一份带调用标记的 per-Issue 日志。

    标记行由生产代码写出而不是测试手写：手写字符串只能证明"读文件读得出
    字符串"，证明不了调用记录真的流进了既有入口。

    Args:
        repo_dir: 测试仓库根目录（日志落在 ``<repo_dir>/logs``）。
        issue_number: Issue 号。
        run_history_store: 旁路账本；传不具备该能力的对象即可复现"账本不可用"。

    Returns:
        本次产出的唯一 per-Issue 日志路径。
    """
    store = (
        SqliteConsoleStore(repo_dir.parent / "console.db")
        if run_history_store is None
        else run_history_store
    )
    with issue_output_routing(
        repo_id="fixture-repo",
        issue_number=issue_number,
        log_base=repo_dir / "logs",
        output_view=NoOpRunnerLiveView(),
    ) as sink:
        sink("agent is working\n")
        with bound_invocation_trace_context(
            build_invocation_trace_context(
                repo_id="fixture-repo",
                issue_number=issue_number,
                run_history_store=store,
            )
        ):
            observation = start_invocation(
                InvocationStartRequest(
                    agent_name="claude",
                    phase=PHASE_IMPLEMENTATION,
                    profile=AGENT_PROFILE_RUN,
                    attempt_number=1,
                    requested_model="claude-sonnet-4-5",
                )
            )
            finish_invocation(
                observation,
                result=CommandResult(
                    command=("claude",),
                    return_code=0,
                    stdout="",
                    stderr="",
                    reported_model="claude-sonnet-4-5-20250929",
                ),
            )
    log_dir = repo_dir / "logs" / "agent-runner" / "issues" / "fixture-repo"
    attempts = sorted(log_dir.glob(f"issue-{issue_number}-*.log"))
    assert len(attempts) == 1
    return attempts[0]


def _marker_field(marker_line: str, field_name: str) -> str:
    """从标记行里取出 ``field=value`` 的 value（标记行带日志前缀，不能整行比对）。"""
    for token in marker_line.split():
        if token.startswith(f"{field_name}="):
            return token[len(field_name) + 1 :]
    raise AssertionError(f"field {field_name!r} missing from marker line: {marker_line}")


def test_logs_command_issue_surfaces_invocation_markers(tmp_path: Path, capsys) -> None:
    """既有 `iar logs --issue N` 原样带出调用起止标记——入口没有任何新增。"""
    repo_dir = tmp_path / "repo"
    attempt_path = _produce_invocation_attempt_log(repo_dir, 42)

    context = MagicMock(repo_id="fixture-repo", repo_path=repo_dir)
    parsed = _FakeArgs(
        kind=None,
        lines=200,
        follow=False,
        repo_id="fixture-repo",
        issue=42,
    )
    with patch(
        "backend.api.cli_registry.resolve_repository_targets",
        return_value=[context],
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="fixture-repo",
            repo_override=None,
        )

    assert exit_code == 0
    printed_lines = capsys.readouterr().out.splitlines()
    start_lines = [line for line in printed_lines if INVOCATION_START_MARKER in line]
    end_lines = [line for line in printed_lines if INVOCATION_END_MARKER in line]
    assert len(start_lines) == 1
    assert len(end_lines) == 1

    # 身份、阶段与实际执行器在既有入口就能读到，不需要新命令或新 JSON 汇总。
    start_line = start_lines[0]
    assert _marker_field(start_line, "run").startswith("fixture-repo#issue-42#")
    assert _marker_field(start_line, "issue") == "42"
    assert _marker_field(start_line, "attempt") == "1"
    assert _marker_field(start_line, "phase") == PHASE_IMPLEMENTATION
    assert _marker_field(start_line, "role") == "implementer"
    assert _marker_field(start_line, "executor") == "claude"
    assert _marker_field(start_line, "model_requested") == "claude-sonnet-4-5"
    # log= 给的是相对日志根的定位串，可直接拼回本次真实写盘的那个文件。
    assert (repo_dir / "logs" / _marker_field(start_line, "log")) == attempt_path

    # 结束标记给出结局与模型三态：请求的、执行器自报的、以及来源。
    end_line = end_lines[0]
    assert _marker_field(end_line, "outcome") == "ok"
    assert _marker_field(end_line, "exit_code") == "0"
    assert _marker_field(end_line, "model_requested") == "claude-sonnet-4-5"
    assert _marker_field(end_line, "model_reported") == "claude-sonnet-4-5-20250929"
    assert _marker_field(end_line, "model_source") == "executor_report"

    # 起止靠同一个 invocation id 配对，而不是靠"上下相邻"推断。
    assert _marker_field(start_line, "invocation") == _marker_field(end_line, "invocation")


def test_logs_command_issue_follow_still_exits_on_attempt_end_with_markers(
    tmp_path: Path, capsys
) -> None:
    """调用标记不干扰 `--follow` 的终态判定：仍只认 `[iar-attempt-end]`。"""
    repo_dir = tmp_path / "repo"
    _produce_invocation_attempt_log(repo_dir, 42)

    follow_clock = _VirtualFollowClock(time)
    with (
        patch("backend.api.cli_registry.time", follow_clock),
        patch("backend.api.cli_registry._LOGS_POLL_INTERVAL_SECONDS", 1.0),
        # 空闲兜底故意设得比首次读标记晚：若终态判定被新标记带偏，退出会走兜底
        # 路径，下面"without an end marker"的断言即失败。
        patch("backend.api.cli_registry._FOLLOW_IDLE_EXIT_SECONDS", 5.0),
    ):
        exit_code = _run_issue_follow(repo_dir, 42)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert INVOCATION_START_MARKER in output
    assert INVOCATION_END_MARKER in output
    assert "finished; tail ends here" in output
    assert "without an end marker" not in output


def test_logs_command_issue_surfaces_coverage_incomplete_disclosure(tmp_path: Path, capsys) -> None:
    """账本不可用时，既有入口同样带出"调用清单可能不完整"的披露标记。"""
    repo_dir = tmp_path / "repo"
    # 传一个不具备调用事件账本能力的对象：观测降级，但日志标记照写。
    _produce_invocation_attempt_log(repo_dir, 42, run_history_store=object())

    context = MagicMock(repo_id="fixture-repo", repo_path=repo_dir)
    parsed = _FakeArgs(
        kind=None,
        lines=200,
        follow=False,
        repo_id="fixture-repo",
        issue=42,
    )
    with patch(
        "backend.api.cli_registry.resolve_repository_targets",
        return_value=[context],
    ):
        exit_code = _run_logs_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="fixture-repo",
            repo_override=None,
        )

    assert exit_code == 0
    printed_lines = capsys.readouterr().out.splitlines()
    coverage_lines = [
        line for line in printed_lines if INVOCATION_COVERAGE_INCOMPLETE_MARKER in line
    ]
    assert len(coverage_lines) == 1
    assert "reason=store_unavailable" in coverage_lines[0]
    # 披露降级不等于业务失败：起止标记仍然齐全。
    assert any(INVOCATION_START_MARKER in line for line in printed_lines)
    assert any(INVOCATION_END_MARKER in line for line in printed_lines)

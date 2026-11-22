"""使用真实终端和管道验证旧 iar 的交互入口。"""

from __future__ import annotations

import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.keda_eval_historical_cli import _run

pytest_plugins = ("tests.keda_eval_historical_cli",)

pty = pytest.importorskip("pty")


def _pty_run(
    repo: Path,
    environment: dict[str, str],
    arguments: tuple[str, ...],
    user_input: bytes,
) -> tuple[int, str]:
    executable = Path(sys.executable).with_name("iar")
    parent_fd, child_fd = pty.openpty()
    try:
        with (
            subprocess.Popen(
                [str(executable), *arguments],
                cwd=repo,
                env=environment,
                stdin=child_fd,
                stdout=child_fd,
                stderr=child_fd,
            ) as process,
            selectors.DefaultSelector() as readable,
        ):
            os.close(child_fd)
            child_fd = -1
            if user_input and process.poll() is None:
                os.write(parent_fd, user_input)
            readable.register(parent_fd, selectors.EVENT_READ)
            captured = bytearray()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not readable.select(timeout=0.25):
                    if process.poll() is not None:
                        break
                    continue
                try:
                    chunk = os.read(parent_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                captured.extend(chunk)
            if process.poll() is None and time.monotonic() >= deadline:
                process.kill()
            code = process.wait(timeout=5)
            return code, captured.decode("utf-8", errors="replace")
    finally:
        if child_fd >= 0:
            os.close(child_fd)
        os.close(parent_fd)


def test_no_terminal_shows_help_without_traceback(
    isolated_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, environment = isolated_repo
    code, output = _run(repo, environment)
    assert code != 0, output
    assert "usage" in output.lower() and "traceback" not in output.lower(), output


def test_tty_bare_entry_opens_session(isolated_repo: tuple[Path, dict[str, str]]) -> None:
    repo, environment = isolated_repo
    code, output = _run(repo, environment, "init")
    assert code == 0, output
    code, output = _pty_run(repo, environment, (), b"/exit\n")
    assert code == 0, output
    assert "traceback" not in output.lower()
    assert "session" in output.lower(), output


def test_named_repl_accepts_explicit_agent(isolated_repo: tuple[Path, dict[str, str]]) -> None:
    repo, environment = isolated_repo
    code, output = _run(repo, environment, "init")
    assert code == 0, output
    code, output = _pty_run(repo, environment, ("repl", "--agent", "codex"), b"/exit\n")
    assert code == 0, output
    assert "codex" in output.lower(), output


def test_named_help_still_works(isolated_repo: tuple[Path, dict[str, str]]) -> None:
    repo, environment = isolated_repo
    code, output = _run(repo, environment, "run", "--help")
    assert code == 0 and "dry-run" in output.lower(), output


def test_repl_explains_missing_repository_initialization(
    isolated_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, environment = isolated_repo
    code, output = _pty_run(repo, environment, ("repl",), b"/exit\n")
    assert code != 0, output
    assert "iar init" in output.lower(), output


def test_repl_auto_is_rejected_or_falls_back(isolated_repo: tuple[Path, dict[str, str]]) -> None:
    repo, environment = isolated_repo
    code, output = _run(repo, environment, "init")
    assert code == 0, output
    _, output = _pty_run(repo, environment, ("repl", "--agent", "auto"), b"/exit\n")
    assert "auto" in output.lower(), output
    assert (
        "not supported" in output.lower() or "invalid" in output.lower() or "fall" in output.lower()
    )
    assert "agent=auto" not in output.lower()

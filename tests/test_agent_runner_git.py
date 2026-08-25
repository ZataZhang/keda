"""Real-git tests for agent runner git utilities.

``list_changed_paths`` feeds the forbidden-path safety gate
(``validate_safe_changes``), so these tests use real ``git`` subprocesses:
the bug class being locked down here is git's ``core.quotePath`` C-quoting
of non-ASCII paths, which mock-based tests cannot reproduce.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    RunnerConfig,
)
from backend.core.use_cases.agent_runner_git import (
    list_changed_paths,
    list_stageable_paths,
    run_verification,
)
from backend.core.use_cases.agent_runner_publish import validate_safe_changes
from backend.infrastructure.process_runner import SubprocessRunner
from tests.support.agent_runner import run_git


def _init_git_repository(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_git(repo_path, "init", "--initial-branch=main")
    run_git(repo_path, "config", "user.email", "test@example.com")
    run_git(repo_path, "config", "user.name", "Test User")
    (repo_path / "README.md").write_text("placeholder\n", encoding="utf-8")
    run_git(repo_path, "add", "README.md")
    run_git(repo_path, "commit", "-m", "init")
    return repo_path


def test_list_changed_paths_returns_non_ascii_paths_verbatim(
    tmp_path: Path,
) -> None:
    """Non-ASCII paths must come back unquoted.

    With default ``core.quotePath=true``, plain ``--porcelain`` output
    C-quotes such paths (``"secrets/\\345\\257\\206..."``); the quoted text
    would never match forbidden-path patterns.
    """
    repo_path = _init_git_repository(tmp_path)
    secret_file_path = repo_path / "secrets" / "密钥.txt"
    secret_file_path.parent.mkdir()
    secret_file_path.write_text("token\n", encoding="utf-8")
    run_git(repo_path, "add", "secrets")

    changed_paths = list_changed_paths(repo_path, SubprocessRunner())

    assert "secrets/密钥.txt" in changed_paths
    assert all('"' not in changed_path for changed_path in changed_paths)


def test_list_changed_paths_includes_rename_source_and_target(
    tmp_path: Path,
) -> None:
    """A staged rename must report both the new and the original path."""
    repo_path = _init_git_repository(tmp_path)
    run_git(repo_path, "mv", "README.md", "说明.md")

    changed_paths = list_changed_paths(repo_path, SubprocessRunner())

    assert "说明.md" in changed_paths
    assert "README.md" in changed_paths


def test_list_stageable_paths_drops_staged_rename_source(tmp_path: Path) -> None:
    """A staged rename's source path must not reach a ``git add`` pathspec.

    ``git mv`` removes the source from both the working tree and the index, so
    ``git add -- <source>`` aborts the *whole* command with exit 128. The
    rename itself is already in the index, so dropping it loses nothing.
    """
    repo_path = _init_git_repository(tmp_path)
    (repo_path / "src").mkdir()
    (repo_path / "src" / "feature.py").write_text("value = 1\n", encoding="utf-8")
    run_git(repo_path, "mv", "README.md", "说明.md")

    stageable_paths = list_stageable_paths(repo_path, SubprocessRunner())

    assert "README.md" not in stageable_paths
    assert "说明.md" in stageable_paths
    assert "src/" in stageable_paths
    # The whole point of the filter: git must accept every path it returns.
    run_git(repo_path, "add", "--", *stageable_paths)


def test_list_stageable_paths_drops_already_staged_deletion(tmp_path: Path) -> None:
    """An already-staged deletion (``D `` status) is unmatched by ``git add`` too."""
    repo_path = _init_git_repository(tmp_path)
    (repo_path / "doomed.txt").write_text("bye\n", encoding="utf-8")
    run_git(repo_path, "add", "doomed.txt")
    run_git(repo_path, "commit", "-m", "add doomed")
    run_git(repo_path, "rm", "doomed.txt")

    stageable_paths = list_stageable_paths(repo_path, SubprocessRunner())

    assert "doomed.txt" not in stageable_paths
    assert "doomed.txt" in list_changed_paths(repo_path, SubprocessRunner())


def test_list_stageable_paths_keeps_unstaged_deletion(tmp_path: Path) -> None:
    """An unstaged deletion must stay: ``git add`` still stages it from the index.

    Filtering purely on on-disk existence would drop it and leave the
    checkpoint commit inconsistent (e.g. a moved module committed as an
    addition while the original file lives on).
    """
    repo_path = _init_git_repository(tmp_path)
    (repo_path / "README.md").unlink()

    stageable_paths = list_stageable_paths(repo_path, SubprocessRunner())

    assert "README.md" in stageable_paths
    run_git(repo_path, "add", "--", *stageable_paths)
    status_after_add = run_git(repo_path, "status", "--porcelain")
    assert status_after_add.startswith("D ")


def test_validate_safe_changes_blocks_non_ascii_forbidden_path(
    tmp_path: Path,
) -> None:
    """The forbidden-path gate must catch non-ASCII paths under secrets/*."""
    repo_path = _init_git_repository(tmp_path)
    secret_file_path = repo_path / "secrets" / "密钥.txt"
    secret_file_path.parent.mkdir()
    secret_file_path.write_text("token\n", encoding="utf-8")
    run_git(repo_path, "add", "secrets")

    with pytest.raises(RuntimeError, match="Refusing to publish forbidden paths"):
        validate_safe_changes(repo_path, AppConfig(), SubprocessRunner())


def test_run_verification_shell_expands_command_substitution(tmp_path: Path) -> None:
    """Command substitution ``$(...)`` must be expanded by the shell.

    Without shell expansion the verification runner treated
    ``$(find src -name '*.py')`` as a literal argv element and passed it to
    the underlying tool as a positional argument, producing an argparse
    parse error instead of running the real check. Wrapping in
    ``bash -lc`` keeps the old behavior for plain commands and adds real
    shell expansion for substitution / glob / pipe / variable forms.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    probe_script = scripts_dir / "probe.sh"
    probe_script_body = (
        "#!/usr/bin/env bash\n" "set -euo pipefail\n" 'echo "count=$#"\n' 'echo "first=$1"\n'
    )
    probe_script.write_text(probe_script_body, encoding="utf-8")
    probe_script.chmod(0o755)

    command = f"bash {probe_script} $(find {scripts_dir} -name '*.sh')"
    config = AppConfig(
        runner=RunnerConfig(verification_commands=(command,)),
    )

    results = run_verification(tmp_path, config, SubprocessRunner())

    assert len(results) == 1
    assert results[0].return_code == 0
    assert "count=1" in results[0].stdout
    assert probe_script.name in results[0].stdout


def test_run_verification_simple_command_still_works(tmp_path: Path) -> None:
    """Plain ``git diff --check`` style commands must still pass.

    Pre-fix behavior (shlex.split → direct argv) and post-fix behavior
    (bash -lc) both succeed for plain commands; this test guards against
    regressions where the wrapping changes the exit code or output.
    """
    repo_path = _init_git_repository(tmp_path)
    config = AppConfig(
        runner=RunnerConfig(verification_commands=("git diff --check",)),
    )

    results = run_verification(repo_path, config, SubprocessRunner())

    assert len(results) == 1
    assert results[0].return_code == 0
    assert results[0].stderr == ""


def test_run_verification_short_circuits_on_first_failure(tmp_path: Path) -> None:
    """The runner must stop on the first non-zero command, not run the rest."""
    sentinel_path = tmp_path / "sentinel"
    config = AppConfig(
        runner=RunnerConfig(
            verification_commands=(
                "false",
                f"touch {sentinel_path}",
            ),
        ),
    )

    results = run_verification(tmp_path, config, SubprocessRunner())

    assert len(results) == 1
    assert results[0].return_code != 0
    assert not sentinel_path.exists(), (
        "Second command must not execute after the first one failed; "
        "otherwise the short-circuit guarantee is broken."
    )


def test_run_verification_records_each_command_stdout_and_stderr(
    tmp_path: Path,
) -> None:
    """Both stdout and stderr of the underlying shell must be captured.

    Capturing both lets the recovery prompt surface the real failure mode
    (e.g. a linter's stderr) rather than a swallowed parse error.
    """
    config = AppConfig(
        runner=RunnerConfig(
            verification_commands=("echo OUT-MARKER; echo ERR-MARKER 1>&2; exit 7",),
        ),
    )

    results = run_verification(tmp_path, config, SubprocessRunner())

    assert len(results) == 1
    assert results[0].return_code == 7
    assert "OUT-MARKER" in results[0].stdout
    assert "ERR-MARKER" in results[0].stderr

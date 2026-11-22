"""通过真实 iar CLI 和 Git 仓库验证初始化门槛。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@pytest.fixture
def isolated_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "target"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh_stub = fake_bin / "gh"
    gh_stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = auth ] && [ "$2" = status ]; then printf "✓ Logged in to github.com as local-fixture\\n"; exit 0; fi\n'
        'if [ "$1" = issue ] && [ "$2" = list ]; then printf "[]\\n"; exit 0; fi\n'
        'printf "unexpected gh call: %s\\n" "$*" >&2\nexit 1\n',
        encoding="utf-8",
    )
    gh_stub.chmod(0o755)
    for agent_name in ("claude", "codex", "kimi"):
        agent_stub = fake_bin / agent_name
        agent_stub.write_text(
            f'#!/bin/sh\nprintf "fixture session for {agent_name}\\n"\n',
            encoding="utf-8",
        )
        agent_stub.chmod(0o755)
    environment = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PATH": os.pathsep.join((str(fake_bin), os.defpath)),
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
    subprocess.run(
        ["git", "init", "--quiet", "-b", "main", str(repo)],
        env=environment,
        capture_output=True,
        check=True,
        timeout=10,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "Fixture",
        ],
        cwd=repo,
        env=environment,
        capture_output=True,
        check=True,
        timeout=10,
    )
    (repo / "task.md").write_text(
        "# Example task\n\nKeep CLI entry points stable.\n", encoding="utf-8"
    )
    return repo, environment


def _run(repo: Path, env: dict[str, str], *arguments: str) -> tuple[int, str]:
    executable = Path(sys.executable).with_name("iar.exe" if os.name == "nt" else "iar")
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=repo,
        env=env,
        check=False,
        timeout=20,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.returncode, _ANSI_ESCAPE.sub("", completed.stdout + completed.stderr)

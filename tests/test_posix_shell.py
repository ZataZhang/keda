"""按平台选择 POSIX shell（:mod:`backend.infrastructure.posix_shell`）的测试。

macOS / Linux 上 argv 必须原样返回；Windows 上 ``bash`` / ``sh`` 换成 Git for
Windows 的 ``bin\\bash.exe``，不使用 System32 / WindowsApps 下的 WSL 占位程序。
平台用模块级 ``os`` 替身切换，Git 安装目录在 tmp 里按真实布局搭建。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.infrastructure import posix_shell
from backend.infrastructure.posix_shell import PosixShellNotFoundError, with_platform_posix_shell


@pytest.fixture(autouse=True)
def _fresh_git_bash_cache() -> None:
    """每个用例重新定位 Git Bash，避免缓存串用上一个用例的结果。"""
    posix_shell._find_windows_git_bash.cache_clear()
    yield
    posix_shell._find_windows_git_bash.cache_clear()


def _use_platform(
    monkeypatch: pytest.MonkeyPatch, os_name: str, environ: dict[str, str] | None = None
) -> None:
    """把模块看到的平台与环境变量换成给定值。"""
    monkeypatch.setattr(posix_shell, "os", SimpleNamespace(name=os_name, environ=environ or {}))


def _make_git_install(git_root: Path) -> Path:
    """按 Git for Windows 布局在 ``git_root`` 下建出 bin\\bash.exe 与 usr\\bin。"""
    (git_root / "bin").mkdir(parents=True)
    (git_root / "usr" / "bin").mkdir(parents=True)
    (git_root / "cmd").mkdir()
    (git_root / "mingw64" / "bin").mkdir(parents=True)
    git_bash_path = git_root / "bin" / "bash.exe"
    git_bash_path.write_bytes(b"")
    return git_bash_path


def _fake_which(mapping: dict[str, str | None]):
    """返回按命令名查表的 ``shutil.which`` 替身。"""
    return lambda command_name: mapping.get(command_name)


@pytest.mark.parametrize("os_name", ["posix"])
@pytest.mark.parametrize(
    "command",
    [
        ["bash", "-lc", "SKIP=x uv run pytest"],
        ["/bin/sh", "-c", "echo hi"],
        ["git", "status"],
    ],
)
def test_posix_platforms_keep_argv_unchanged(
    monkeypatch: pytest.MonkeyPatch, os_name: str, command: list[str]
) -> None:
    """macOS / Linux 上不做任何替换，行为与之前一致。"""
    _use_platform(monkeypatch, os_name)
    monkeypatch.setattr(posix_shell.shutil, "which", _fake_which({}))

    assert with_platform_posix_shell(command) == command


@pytest.mark.parametrize("git_relative_path", [("cmd", "git.exe"), ("mingw64", "bin", "git.exe")])
@pytest.mark.parametrize("shell_name", ["bash", "sh", "/bin/sh", "/bin/bash"])
def test_windows_shell_resolves_to_git_bash_from_git_on_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_relative_path: tuple[str, ...],
    shell_name: str,
) -> None:
    """Windows 上从 PATH 上的 git 反推 Git 根目录，用其 bin\\bash.exe，其余参数不动。"""
    git_root = tmp_path / "Git"
    git_bash_path = _make_git_install(git_root)
    git_executable = git_root.joinpath(*git_relative_path)
    git_executable.write_bytes(b"")
    _use_platform(monkeypatch, "nt")
    monkeypatch.setattr(
        posix_shell.shutil,
        "which",
        _fake_which({"git": str(git_executable), "bash": r"C:\Windows\System32\bash.exe"}),
    )

    resolved_command = with_platform_posix_shell([shell_name, "-lc", "SKIP=x just test"])

    assert resolved_command == [str(git_bash_path), "-lc", "SKIP=x just test"]


def test_windows_falls_back_to_program_files_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """git 不在 PATH 上时，从 Program Files 下的标准安装目录找 Git Bash。"""
    git_bash_path = _make_git_install(tmp_path / "Program Files" / "Git")
    _use_platform(monkeypatch, "nt", {"ProgramFiles": str(tmp_path / "Program Files")})
    monkeypatch.setattr(posix_shell.shutil, "which", _fake_which({}))

    assert with_platform_posix_shell(["bash", "-c", "true"])[0] == str(git_bash_path)


@pytest.mark.parametrize(
    "wsl_launcher",
    [
        r"C:\Windows\System32\bash.exe",
        r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe",
    ],
)
def test_windows_never_uses_wsl_launcher_and_explains_missing_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wsl_launcher: str
) -> None:
    """只有 WSL 占位 bash 时不使用它，抛出带 Git for Windows 安装指引的错误。"""
    _use_platform(monkeypatch, "nt", {"ProgramFiles": str(tmp_path / "missing")})
    monkeypatch.setattr(posix_shell.shutil, "which", _fake_which({"bash": wsl_launcher}))

    with pytest.raises(PosixShellNotFoundError, match="Git for Windows"):
        with_platform_posix_shell(["bash", "-lc", "true"])


def test_windows_uses_non_wsl_bash_on_path_as_last_resort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有 Git 安装目录、但 PATH 上的 bash 不是 WSL 占位程序时直接使用它（如 MSYS2）。"""
    msys_bash = r"C:\msys64\usr\bin\bash.exe"
    _use_platform(monkeypatch, "nt")
    monkeypatch.setattr(posix_shell.shutil, "which", _fake_which({"bash": msys_bash}))

    assert with_platform_posix_shell(["bash", "-lc", "true"])[0] == msys_bash


def test_windows_non_shell_commands_are_not_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    """argv[0] 不是 POSIX shell 时原样返回，也不触发 Git Bash 定位。"""
    _use_platform(monkeypatch, "nt")

    def _forbidden_which(command_name: str) -> str | None:
        raise AssertionError(f"must not look up {command_name}")

    monkeypatch.setattr(posix_shell.shutil, "which", _forbidden_which)

    assert with_platform_posix_shell(["git", "diff", "--check"]) == ["git", "diff", "--check"]


def test_subprocess_runner_executes_with_resolved_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """进程执行器入口统一适配：调用方仍传 ``bash -lc``，实际启动的是 Git Bash。"""
    from backend.infrastructure import process_runner

    git_root = tmp_path / "Git"
    git_bash_path = _make_git_install(git_root)
    (git_root / "cmd" / "git.exe").write_bytes(b"")
    _use_platform(monkeypatch, "nt", dict(os.environ))
    monkeypatch.setattr(
        posix_shell.shutil, "which", _fake_which({"git": str(git_root / "cmd" / "git.exe")})
    )
    launched_commands: list[list[str]] = []

    def _fake_subprocess_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        launched_commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(process_runner.subprocess, "run", _fake_subprocess_run)

    command_result = process_runner.SubprocessRunner().run(
        ["bash", "-lc", "git diff --check"], cwd=tmp_path, check=False
    )

    assert launched_commands == [[str(git_bash_path), "-lc", "git diff --check"]]
    assert command_result.return_code == 0

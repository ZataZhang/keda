"""可执行目录补齐（GUI 启动 PATH 缺失）的单元测试。"""

from __future__ import annotations

import os

from backend.infrastructure import executable_path


def test_appends_existing_candidates_and_preserves_order(tmp_path, monkeypatch):
    """只追加真实存在且未出现的目录，并保持原有顺序。"""
    homebrew_bin = tmp_path / "homebrew-bin"
    homebrew_bin.mkdir()
    missing_bin = tmp_path / "missing-bin"
    monkeypatch.setattr(
        executable_path, "_candidate_directories", lambda: (homebrew_bin, missing_bin)
    )

    env = {"PATH": os.pathsep.join(["/usr/bin", str(tmp_path)])}
    appended = executable_path.ensure_standard_executable_path(env)

    assert appended == (str(homebrew_bin),)
    assert env["PATH"].split(os.pathsep) == ["/usr/bin", str(tmp_path), str(homebrew_bin)]


def test_skips_directories_already_on_path(tmp_path, monkeypatch):
    """带尾斜杠的重复条目也按同一目录处理，不重复追加。"""
    homebrew_bin = tmp_path / "homebrew-bin"
    homebrew_bin.mkdir()
    monkeypatch.setattr(executable_path, "_candidate_directories", lambda: (homebrew_bin,))

    env = {"PATH": os.pathsep.join(["/usr/bin", f"{homebrew_bin}{os.sep}"])}
    appended = executable_path.ensure_standard_executable_path(env)

    assert appended == ()
    assert env["PATH"] == os.pathsep.join(["/usr/bin", f"{homebrew_bin}{os.sep}"])


def test_repeated_calls_are_idempotent(tmp_path, monkeypatch):
    """重复调用不产生重复目录。"""
    homebrew_bin = tmp_path / "homebrew-bin"
    homebrew_bin.mkdir()
    monkeypatch.setattr(executable_path, "_candidate_directories", lambda: (homebrew_bin,))
    env = {"PATH": "/usr/bin"}

    first = executable_path.ensure_standard_executable_path(env)
    second = executable_path.ensure_standard_executable_path(env)

    assert first == (str(homebrew_bin),)
    assert second == ()
    assert env["PATH"].count(str(homebrew_bin)) == 1


def test_missing_path_is_initialized_from_candidates(tmp_path, monkeypatch):
    """PATH 完全缺失时也能得到可用目录，而不是留空。"""
    homebrew_bin = tmp_path / "homebrew-bin"
    homebrew_bin.mkdir()
    monkeypatch.setattr(executable_path, "_candidate_directories", lambda: (homebrew_bin,))

    env: dict[str, str] = {}
    appended = executable_path.ensure_standard_executable_path(env)

    assert appended == (str(homebrew_bin),)
    assert env["PATH"] == str(homebrew_bin)

"""本机状态目录迁移测试（``kc config migrate`` 的本机步骤）。

覆盖 PRD FR-5 的判定表：占用拒绝、新旧两目录互斥、搬迁 + 相对链接、幂等重跑、
跨文件系统与建链失败、只改写状态目录 ``config.toml`` 里的旧路径取值。

占用检测一律通过 ``occupancy_probe`` 注入，测试不去扫描真实机器上的进程；
探测函数自身的行为单独用 ``_pids_from_*`` 直接验证。
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from backend.core.shared.models import product_identity
from backend.engines.agent_runner import state_home_migration
from backend.engines.agent_runner.state_home_migration import (
    OccupancyReport,
    migrate_state_home,
    rewrite_legacy_state_path_text,
)

_IDLE = OccupancyReport(scanner_available=True, pid_set=frozenset())


def _idle_probe(_candidates: tuple[Path, ...], _home: Path) -> OccupancyReport:
    """空闲（无自有进程）的占用探测。"""
    return _IDLE


def _occupied_probe(_candidates: tuple[Path, ...], _home: Path, pid: int) -> OccupancyReport:
    return OccupancyReport(scanner_available=True, pid_set=frozenset({pid}))


def _make_legacy_state_dir(home_path: Path) -> Path:
    """在临时家目录下造一个有内容的旧状态目录。"""
    legacy_path = home_path / product_identity.LEGACY_STATE_DIR_NAME
    legacy_path.mkdir(parents=True)
    (legacy_path / "config.toml").write_text(
        '[agent_runner.console]\nhistory_db_path = "~/.iar/console.db"\n', encoding="utf-8"
    )
    (legacy_path / "daemon-locks").mkdir()
    return legacy_path


# ---------------------------------------------------------------------------
# 判定表：形状 → 结论
# ---------------------------------------------------------------------------


def test_only_legacy_dir_moves_and_links(tmp_path: Path) -> None:
    """只有旧真实目录时搬到新位置，旧路径变成指向新目录的相对链接。"""
    legacy_path = _make_legacy_state_dir(tmp_path)

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert result.outcome == "migrated"
    new_state_path = tmp_path / product_identity.STATE_DIR_NAME
    assert new_state_path.is_dir()
    assert not new_state_path.is_symlink()
    assert legacy_path.is_symlink()
    assert os.readlink(legacy_path) == product_identity.STATE_DIR_NAME
    assert (new_state_path / "config.toml").is_file()
    assert (new_state_path / "daemon-locks").is_dir()


def test_second_run_is_idempotent_and_changes_nothing(tmp_path: Path) -> None:
    """重复执行报告「已迁移」且磁盘零改动。"""
    _make_legacy_state_dir(tmp_path)
    first = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)
    assert first.outcome == "migrated"
    new_state_path = tmp_path / product_identity.STATE_DIR_NAME
    snapshot_before = sorted(
        (str(path.relative_to(tmp_path)), path.is_symlink())
        for path in tmp_path.rglob("*")
        if path != tmp_path
    )

    second = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert second.outcome == "already_migrated"
    assert "already migrated" in second.message
    snapshot_after = sorted(
        (str(path.relative_to(tmp_path)), path.is_symlink())
        for path in tmp_path.rglob("*")
        if path != tmp_path
    )
    assert snapshot_after == snapshot_before
    assert new_state_path.is_dir()


def test_only_new_dir_reports_already_migrated(tmp_path: Path) -> None:
    """只有新目录时同样判为已迁移，不去动任何东西。"""
    (tmp_path / product_identity.STATE_DIR_NAME).mkdir()

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert result.outcome == "already_migrated"


def test_neither_dir_reports_nothing_to_do(tmp_path: Path) -> None:
    """两个位置都不存在时不需要搬迁，也不提前创建目录。"""
    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert result.outcome == "nothing_to_do"
    assert not (tmp_path / product_identity.STATE_DIR_NAME).exists()


def test_separate_directories_are_refused(tmp_path: Path) -> None:
    """新旧两个独立真实目录并存时拒绝合并，磁盘保持原样。"""
    _make_legacy_state_dir(tmp_path)
    new_state_path = tmp_path / product_identity.STATE_DIR_NAME
    new_state_path.mkdir()
    (new_state_path / "own.txt").write_text("keep\n", encoding="utf-8")

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert result.outcome == "separate_directories"
    assert result.outcome in state_home_migration.CONFLICT_OUTCOMES
    assert (tmp_path / product_identity.LEGACY_STATE_DIR_NAME / "config.toml").is_file()
    assert (new_state_path / "own.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (tmp_path / product_identity.LEGACY_STATE_DIR_NAME).is_symlink()


def test_legacy_symlink_to_elsewhere_is_refused(tmp_path: Path) -> None:
    """旧位置是指向别处的链接时不搬，避免把用户的重定向踩掉。"""
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    (tmp_path / product_identity.LEGACY_STATE_DIR_NAME).symlink_to(elsewhere)

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert result.outcome == "legacy_link_in_way"
    assert result.outcome in state_home_migration.CONFLICT_OUTCOMES
    assert (tmp_path / product_identity.LEGACY_STATE_DIR_NAME).is_symlink()
    assert not (tmp_path / product_identity.STATE_DIR_NAME).exists()


# ---------------------------------------------------------------------------
# 占用检测
# ---------------------------------------------------------------------------


def test_running_process_blocks_migration_with_zero_changes(tmp_path: Path) -> None:
    """有自有进程在跑时拒绝，磁盘完全不碰。"""
    legacy_path = _make_legacy_state_dir(tmp_path)

    result = migrate_state_home(
        home_path=tmp_path,
        occupancy_probe=lambda _c, _h: _occupied_probe(_c, _h, 4242),
    )

    assert result.outcome == "occupied"
    assert result.outcome in state_home_migration.CONFLICT_OUTCOMES
    assert result.blocking_pid_set == (4242,)
    assert "4242" in result.message
    assert legacy_path.is_dir() and not legacy_path.is_symlink()
    assert not (tmp_path / product_identity.STATE_DIR_NAME).exists()


def test_occupied_checked_before_dry_run_plan(tmp_path: Path) -> None:
    """预演也跑同一套预检：占用时预演同样拒绝，不给出可执行的计划。"""
    _make_legacy_state_dir(tmp_path)

    result = migrate_state_home(
        home_path=tmp_path,
        dry_run=True,
        occupancy_probe=lambda _c, _h: _occupied_probe(_c, _h, 7),
    )

    assert result.outcome == "occupied"
    assert result.plan_lines == ()


def test_scanner_unavailable_refuses_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """进程扫描不可用时不赌安全，直接失败（退出码由 api 归到未分类失败）。"""
    _make_legacy_state_dir(tmp_path)
    monkeypatch.setattr(
        state_home_migration,
        "_pids_from_command_scan",
        lambda _home: None,
    )

    result = migrate_state_home(home_path=tmp_path)

    assert result.outcome == "scanner_unavailable"
    assert result.outcome in state_home_migration.FAILURE_OUTCOMES


def test_lock_file_and_registry_pids_are_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁文件与 ``processes.json`` 里存活的 PID 都算占用；已退出的记录不算。"""
    state_dir_path = tmp_path / product_identity.LEGACY_STATE_DIR_NAME
    state_dir_path.mkdir(parents=True)
    lock_dir_path = state_dir_path / "daemon-locks"
    lock_dir_path.mkdir()
    (lock_dir_path / "demo.lock").write_text("111\n", encoding="utf-8")
    (lock_dir_path / "stale.lock").write_text("222\n", encoding="utf-8")
    (state_dir_path / "processes.json").write_text(
        json.dumps({"a": {"pid": 333}, "b": {"pid": 444}, "junk": {"pid": "x"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(state_home_migration, "_is_pid_alive", lambda pid: pid in {111, 333})
    monkeypatch.setattr(state_home_migration, "_pids_from_command_scan", lambda _home: frozenset())

    assert state_home_migration._pids_from_lock_files(state_dir_path) == frozenset({111})
    assert state_home_migration._pids_from_process_registry(state_dir_path) == frozenset({333})

    report = state_home_migration._detect_occupancy((state_dir_path,))
    assert report.scanner_available is True
    assert report.pid_set == frozenset({111, 333})


# ---------------------------------------------------------------------------
# 搬迁失败分支
# ---------------------------------------------------------------------------


def test_cross_device_move_is_refused_without_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨文件系统时拒绝复制，旧目录保持原样。"""
    legacy_path = _make_legacy_state_dir(tmp_path)

    def _exdev(_src: str, _dst: str) -> None:
        raise OSError(errno.EXDEV, "cross-device link not permitted")

    monkeypatch.setattr(os, "rename", _exdev)

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert result.outcome == "cross_device"
    assert result.outcome in state_home_migration.FAILURE_OUTCOMES
    assert legacy_path.is_dir()
    assert not (tmp_path / product_identity.STATE_DIR_NAME).exists()


def test_move_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """改名被拒绝（权限等）时报通用失败，不复用跨盘结论。"""
    _make_legacy_state_dir(tmp_path)

    def _denied(_src: str, _dst: str) -> None:
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(os, "rename", _denied)

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert result.outcome == "move_failed"
    assert result.outcome in state_home_migration.FAILURE_OUTCOMES


def test_symlink_failure_keeps_the_move_and_gives_manual_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """搬迁成功但建链失败：不回滚，给出手工补建命令。"""
    _make_legacy_state_dir(tmp_path)
    real_rename = os.rename
    monkeypatch.setattr(os, "rename", real_rename)

    def _no_link(_src: str, _dst: str) -> None:
        raise OSError(errno.EPERM, "symlinks are disabled")

    monkeypatch.setattr(os, "symlink", _no_link)

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    new_state_path = tmp_path / product_identity.STATE_DIR_NAME
    assert result.outcome == "symlink_failed"
    assert new_state_path.is_dir()
    assert not (tmp_path / product_identity.LEGACY_STATE_DIR_NAME).exists()
    manual_command = result.manual_symlink_command or ""
    assert manual_command.startswith("ln -s .kedacode ")
    assert str(tmp_path / product_identity.LEGACY_STATE_DIR_NAME) in manual_command


# ---------------------------------------------------------------------------
# 旧路径改写
# ---------------------------------------------------------------------------


def test_rewrite_hits_only_legacy_state_prefixes() -> None:
    """只改写以旧状态目录为前缀的路径，路径中间出现 ``.iar`` 不算命中。"""
    home_path = Path("/home/dev")
    assert (
        rewrite_legacy_state_path_text("~/.iar/console.db", home_path) == "~/.kedacode/console.db"
    )
    assert rewrite_legacy_state_path_text("~/.iar", home_path) == "~/.kedacode"
    assert (
        rewrite_legacy_state_path_text("$HOME/.iar/logs/x", home_path) == "$HOME/.kedacode/logs/x"
    )
    assert (
        rewrite_legacy_state_path_text("${HOME}/.iar/processes.json", home_path)
        == "${HOME}/.kedacode/processes.json"
    )
    assert (
        rewrite_legacy_state_path_text("/home/dev/.iar/console.db", home_path)
        == "/home/dev/.kedacode/console.db"
    )
    assert rewrite_legacy_state_path_text("~/.iarc/config.toml", home_path) is None
    assert rewrite_legacy_state_path_text("/srv/app/.iar.toml", home_path) is None
    assert rewrite_legacy_state_path_text("~/.config/kc/config.toml", home_path) is None


def test_move_rewrites_config_toml_and_lists_other_files(tmp_path: Path) -> None:
    """搬迁后改写状态目录 ``config.toml`` 的旧路径取值，其他文件只统计不改动。"""
    legacy_path = tmp_path / product_identity.LEGACY_STATE_DIR_NAME
    legacy_path.mkdir()
    home_text = str(tmp_path)
    (legacy_path / "config.toml").write_text(
        "# 注释里的 ~/.iar/config.toml 不是取值，不该被改\n"
        "[agent_runner.console]\n"
        'history_db_path = "~/.iar/console.db"\n'
        f'process_registry_path = "{home_text}/.iar/processes.json"\n'
        'log_dirs = ["~/.iar/logs/agent-runner", "~/.kedacode/already-new"]\n'
        "max_concurrent_issues = 3\n"
        "[agent_runner.runner]\n"
        'worktree_root = "~/.iar/worktrees"\n',
        encoding="utf-8",
    )
    (legacy_path / "notes.md").write_text(
        f"legacy path lives in {home_text}/.iar/config.toml\n", encoding="utf-8"
    )

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    new_config_text = (tmp_path / product_identity.STATE_DIR_NAME / "config.toml").read_text(
        encoding="utf-8"
    )
    assert result.outcome == "migrated"
    assert result.rewritten_value_count == 4
    # 取值里不再出现旧前缀（引号开头才算取值，注释不受影响）。
    assert '"~/.iar' not in new_config_text
    assert f'"{home_text}/.iar' not in new_config_text
    assert 'history_db_path = "~/.kedacode/console.db"' in new_config_text
    assert (
        'log_dirs = ["~/.kedacode/logs/agent-runner", "~/.kedacode/already-new"]' in new_config_text
    )
    assert "max_concurrent_issues = 3" in new_config_text
    # 注释原样保留：改写只作用于取值，不重排文件，也不清理注释里的旧路径。
    assert "# 注释里的 ~/.iar/config.toml 不是取值，不该被改" in new_config_text
    assert result.legacy_path_file_count == 1
    assert (
        (tmp_path / product_identity.STATE_DIR_NAME / "notes.md")
        .read_text(encoding="utf-8")
        .startswith("legacy path lives in ")
    )


def test_dry_run_reports_plan_without_writing(tmp_path: Path) -> None:
    """预演给出计划与将被改写的取值条数，磁盘完全不变。"""
    legacy_path = _make_legacy_state_dir(tmp_path)

    result = migrate_state_home(home_path=tmp_path, dry_run=True, occupancy_probe=_idle_probe)

    assert result.outcome == "migrated"
    assert result.rewritten_value_count == 1
    assert result.plan_lines
    assert legacy_path.is_dir() and not legacy_path.is_symlink()
    assert not (tmp_path / product_identity.STATE_DIR_NAME).exists()
    assert "dry run" in result.message


def test_unparsable_config_toml_does_not_block_move(tmp_path: Path) -> None:
    """状态目录里的 ``config.toml`` 损坏时照样搬迁，只是不改写取值。"""
    legacy_path = tmp_path / product_identity.LEGACY_STATE_DIR_NAME
    legacy_path.mkdir()
    (legacy_path / "config.toml").write_text("not = = toml\n", encoding="utf-8")

    result = migrate_state_home(home_path=tmp_path, occupancy_probe=_idle_probe)

    assert result.outcome == "migrated"
    assert result.rewritten_value_count == 0
    assert result.rewritten_config_path is None


# ---------------------------------------------------------------------------
# 进程扫描：按自有名字命中，并按家目录划界
# ---------------------------------------------------------------------------


class _FakeProcess:
    """只暴露 ``info`` 与 ``environ()`` 的假进程句柄。"""

    def __init__(self, pid: int, cmdline: list[str], home_text: str | None) -> None:
        self.info = {"pid": pid, "cmdline": cmdline}
        self._home_text = home_text

    def environ(self) -> dict[str, str]:
        if self._home_text is None:
            raise PermissionError("not readable")
        return {"HOME": self._home_text}


class _FakePsutilModule:
    """替换 ``psutil`` 模块，让函数内部的延迟 import 拿到假进程表。"""

    def __init__(self, process_list: list[_FakeProcess]) -> None:
        self._process_list = process_list

    def process_iter(self, _attrs: list[str]) -> list[_FakeProcess]:
        return self._process_list


def _install_fake_psutil(monkeypatch: pytest.MonkeyPatch, process_list: list[_FakeProcess]) -> None:
    monkeypatch.setitem(__import__("sys").modules, "psutil", _FakePsutilModule(process_list))


def test_command_scan_only_counts_processes_serving_the_target_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """另一个家目录里的自有进程写不到待迁移的状态目录，因此不阻止搬迁。

    家目录读不到的进程按同家目录处理（宁可拒绝），自身与祖先进程始终排除。
    """
    target_home = tmp_path
    _install_fake_psutil(
        monkeypatch,
        [
            _FakeProcess(2001, ["kc", "daemon"], str(target_home)),
            _FakeProcess(2002, ["kc", "console"], "/home/someone-else"),
            _FakeProcess(2003, ["/Users/x/.local/bin/kedacode", "run"], None),
            _FakeProcess(2004, ["vim", "kc"], str(target_home)),
            _FakeProcess(os.getpid(), ["kc", "config", "migrate"], str(target_home)),
        ],
    )

    assert state_home_migration._pids_from_command_scan(target_home) == frozenset({2001, 2003})


def test_command_scan_requires_the_own_name_to_be_the_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """只有作为可执行文件出现的自有名字才算占用，参数位置上的裸词不算。"""
    _install_fake_psutil(
        monkeypatch,
        [
            _FakeProcess(3001, ["vim", "kc"], str(tmp_path)),
            _FakeProcess(3002, ["tail", "-f", "kc"], str(tmp_path)),
            _FakeProcess(3003, ["sh", "-c", "kedacode/x"], str(tmp_path)),
        ],
    )

    assert state_home_migration._pids_from_command_scan(tmp_path) == frozenset()


def test_command_scan_treats_a_broken_iteration_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """psutil 遍历本身抛错时按「扫不动」处理，不能让异常穿透成命令崩溃。

    macOS 上进程高频生灭时 ``proc_cmdline`` 会抛出内部错误；此时既不能当作「没有占用」
    继续搬迁，也不该把栈抛给用户，只能走 scanner_unavailable 这条拒绝分支。
    """

    class _RaisingPsutilModule:
        """``process_iter`` 走到一半抛错的假 psutil 模块。"""

        def process_iter(self, _attrs: list[str]):
            yield _FakeProcess(4001, ["kc", "daemon"], str(tmp_path))
            raise SystemError(
                "<built-in function proc_cmdline> returned a result with an exception set"
            )

    monkeypatch.setitem(__import__("sys").modules, "psutil", _RaisingPsutilModule())
    assert state_home_migration._pids_from_command_scan(tmp_path) is None

    report = state_home_migration._detect_occupancy((tmp_path,), tmp_path)
    assert report.scanner_available is False
    refusal = state_home_migration.OccupancyReport(scanner_available=False, pid_set=frozenset())
    result = state_home_migration.migrate_state_home(
        home_path=tmp_path, occupancy_probe=lambda *_: refusal
    )
    assert result.outcome == "scanner_unavailable"

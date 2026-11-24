"""daemon 实际生效并行上限状态文件的测试（PRD P1-REFACTOR-20261024-232740 跟进）。

命令行 ``--concurrency`` 只有 daemon 进程知道；daemon 每次轮询把实际值写到单实例锁
旁边，管理终端与 ``kc daemon status`` 读取。读取方只采信锁的存活持有者写出的状态。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases.daemon_parallel_limit_status import (
    UNKNOWN_INSTALLED_VERSION,
    DaemonParallelLimitStatus,
    check_daemon_version,
    clear_daemon_parallel_limit_status,
    daemon_parallel_limit_status_path,
    read_daemon_parallel_limit_status,
    write_daemon_parallel_limit_status,
)
from backend.core.use_cases.daemon_single_instance import daemon_lock_path

REPO_ID = "keda-test"
#: 一个不会存在的 PID，用于复现"写状态的 daemon 已被强杀"。
DEAD_PID = 2_147_483_646


def _status(pid: int, *, parallel_limit: int = 5, source: str = "cli_override"):
    return DaemonParallelLimitStatus(
        repo_id=REPO_ID,
        pid=pid,
        parallel_limit=parallel_limit,
        source=source,
        in_flight=2,
        updated_at="2026-10-25T00:00:00+00:00",
    )


def _hold_lock_as(lock_dir: Path, owner_pid: int) -> None:
    """模拟另一个存活进程持有该仓库的 daemon 单实例锁（读取方与 daemon 不是同一进程）。"""
    daemon_lock_path(lock_dir, REPO_ID).write_text(f"{owner_pid}\n", encoding="utf-8")


def test_status_is_read_back_while_the_writer_holds_the_daemon_lock(tmp_path: Path) -> None:
    """锁的存活持有者写出的状态可以读回，字段原样保留。"""
    daemon_pid = os.getppid()
    _hold_lock_as(tmp_path, daemon_pid)
    write_daemon_parallel_limit_status(tmp_path, _status(daemon_pid))

    assert read_daemon_parallel_limit_status(tmp_path, REPO_ID) == _status(daemon_pid)


def test_status_of_a_dead_daemon_is_ignored(tmp_path: Path) -> None:
    """daemon 已退出（没有锁，或锁的持有者不是写状态的 PID）时不采信旧文件。"""
    write_daemon_parallel_limit_status(tmp_path, _status(DEAD_PID))
    assert read_daemon_parallel_limit_status(tmp_path, REPO_ID) is None

    # 新 daemon 已接手这把锁，状态文件却是被强杀的旧 daemon 留下的。
    _hold_lock_as(tmp_path, os.getppid())
    assert read_daemon_parallel_limit_status(tmp_path, REPO_ID) is None


def test_clear_removes_only_files_written_by_this_process(tmp_path: Path) -> None:
    """退出清理只删本进程写的状态，不碰别的 daemon 留下的文件。"""
    write_daemon_parallel_limit_status(tmp_path, _status(os.getpid()))
    other_status = replace(_status(DEAD_PID), repo_id="other-repo")
    write_daemon_parallel_limit_status(tmp_path, other_status)

    clear_daemon_parallel_limit_status(tmp_path, [REPO_ID, "other-repo"])

    assert not daemon_parallel_limit_status_path(tmp_path, REPO_ID).exists()
    assert daemon_parallel_limit_status_path(tmp_path, "other-repo").exists()


def test_daemon_records_the_cli_override_it_actually_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--concurrency 5`` 启动、页面设置 4：daemon 写出的是它实际在用的 5 与来源。"""
    import backend.core.use_cases.run_agent_daemon as daemon_module
    from tests.conftest import FakeBacklogStore

    recorded_status: dict = {}

    def _stop_after_one_poll(_seconds: float) -> None:
        status_path = daemon_parallel_limit_status_path(tmp_path, REPO_ID)
        recorded_status.update(json.loads(status_path.read_text(encoding="utf-8")))
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon_module, "process_prd_rework_issues", lambda **_kwargs: None)
    monkeypatch.setattr(daemon_module, "run_once", lambda **_kwargs: 0)
    monkeypatch.setattr(daemon_module.time, "sleep", _stop_after_one_poll)
    config = AppConfig()
    config = replace(config, runner=replace(config.runner, max_concurrent_issues=2))

    with pytest.raises(KeyboardInterrupt):
        daemon_module.run_agent_daemon(
            contexts=[
                RepositoryRunContext(
                    repo_id=REPO_ID, display_name="Keda Test", repo_path=tmp_path, config=config
                )
            ],
            interval=1,
            agent="auto",
            concurrency=5,
            process_runner=MagicMock(),
            github_client_factory=lambda repo_path: MagicMock(),
            backlog_store_factory=lambda: FakeBacklogStore(repo_id=REPO_ID, max_parallel=4),
            parallel_limit_status_dir=tmp_path,
        )

    assert recorded_status["parallel_limit"] == 5
    assert recorded_status["source"] == "cli_override"
    assert recorded_status["pid"] == os.getpid()
    # daemon 退出时删除自己写的状态，避免管理终端读到过期值。
    assert not daemon_parallel_limit_status_path(tmp_path, REPO_ID).exists()


# ── daemon 启动版本（PRD P1-FEAT-20261120-145339）────────────────────────────


def test_status_without_loaded_version_reads_back_as_unknown(tmp_path: Path) -> None:
    """本功能上线前启动的旧 daemon 写的文件没有版本字段，仍可读出，版本为 None。"""
    daemon_pid = os.getppid()
    _hold_lock_as(tmp_path, daemon_pid)
    legacy_payload = {
        "repo_id": REPO_ID,
        "pid": daemon_pid,
        "parallel_limit": 3,
        "source": "config",
        "in_flight": 0,
        "updated_at": "2026-10-25T00:00:00+00:00",
    }
    daemon_parallel_limit_status_path(tmp_path, REPO_ID).write_text(
        json.dumps(legacy_payload), encoding="utf-8"
    )

    status = read_daemon_parallel_limit_status(tmp_path, REPO_ID)

    assert status is not None
    assert status.parallel_limit == 3
    assert status.loaded_version is None


def test_status_with_unknown_extra_keys_is_still_read(tmp_path: Path) -> None:
    """更新版本的 daemon 多写的键不应让读取方整份丢弃。"""
    daemon_pid = os.getppid()
    _hold_lock_as(tmp_path, daemon_pid)
    payload = {
        **asdict(_status(daemon_pid)),
        "loaded_version": "0.9.0",
        "field_from_the_future": True,
    }
    daemon_parallel_limit_status_path(tmp_path, REPO_ID).write_text(
        json.dumps(payload), encoding="utf-8"
    )

    status = read_daemon_parallel_limit_status(tmp_path, REPO_ID)

    assert status is not None
    assert status.loaded_version == "0.9.0"


@pytest.mark.parametrize(
    ("loaded_version", "installed_version", "expected_status", "expected_restart"),
    [
        ("0.9.1", "0.9.1", "current", False),
        ("0.9.0", "0.9.1", "mismatch", True),
        ("0.9.2", "0.9.1", "mismatch", True),
        (None, "0.9.1", "unknown", True),
        ("0.9.0", UNKNOWN_INSTALLED_VERSION, "unknown", False),
    ],
)
def test_check_daemon_version_classifies_loaded_against_installed(
    loaded_version: str | None,
    installed_version: str,
    expected_status: str,
    expected_restart: bool,
) -> None:
    """不相等即 mismatch；未上报版本提示重启；已安装版本不可知时不误报。"""
    limit_status = replace(_status(DEAD_PID), loaded_version=loaded_version)

    version_check = check_daemon_version(limit_status, installed_version)

    assert version_check.status == expected_status
    assert version_check.restart_recommended is expected_restart


def test_check_daemon_version_without_status_recommends_restart() -> None:
    """读不到状态文件（旧进程或尚未完成首轮轮询）按未知处理并提示重启。"""
    version_check = check_daemon_version(None, "0.9.1")

    assert version_check.status == "unknown"
    assert version_check.loaded_version is None
    assert version_check.restart_recommended is True


def test_daemon_writes_the_loaded_version_it_was_started_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon 原样写出启动时传入的版本，而不是轮询时重新解析的磁盘版本。"""
    import backend.core.use_cases.run_agent_daemon as daemon_module

    recorded_status: dict = {}

    def _stop_after_one_poll(_seconds: float) -> None:
        status_path = daemon_parallel_limit_status_path(tmp_path, REPO_ID)
        recorded_status.update(json.loads(status_path.read_text(encoding="utf-8")))
        raise KeyboardInterrupt

    # 版本只在启动时解析：即使此刻磁盘上已是新版本，daemon 也不得采用。
    import backend.api.version_info as version_info_module

    monkeypatch.setattr(version_info_module, "resolve_keda_version", lambda: "0.9.1")
    # 本测试只验证状态写出，不涉及进程组回收；绕过 POSIX 守卫以便在 Windows 上也能运行。
    monkeypatch.setattr(daemon_module, "require_posix_process_groups", lambda _feature: None)
    monkeypatch.setattr(daemon_module, "process_prd_rework_issues", lambda **_kwargs: None)
    monkeypatch.setattr(daemon_module, "run_once", lambda **_kwargs: 0)
    monkeypatch.setattr(daemon_module.time, "sleep", _stop_after_one_poll)

    with pytest.raises(KeyboardInterrupt):
        daemon_module.run_agent_daemon(
            contexts=[
                RepositoryRunContext(
                    repo_id=REPO_ID,
                    display_name="Keda Test",
                    repo_path=tmp_path,
                    config=AppConfig(),
                )
            ],
            interval=1,
            agent="auto",
            process_runner=MagicMock(),
            github_client_factory=lambda repo_path: MagicMock(),
            parallel_limit_status_dir=tmp_path,
            loaded_version="0.9.0",
        )

    assert recorded_status["loaded_version"] == "0.9.0"

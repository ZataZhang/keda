"""daemon 实际生效并行上限状态文件的测试（PRD P1-REFACTOR-20261024-232740 跟进）。

命令行 ``--concurrency`` 只有 daemon 进程知道；daemon 每次轮询把实际值写到单实例锁
旁边，管理终端与 ``kc daemon status`` 读取。读取方只采信锁的存活持有者写出的状态。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases.daemon_parallel_limit_status import (
    DaemonParallelLimitStatus,
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

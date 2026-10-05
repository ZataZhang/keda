"""Tests for the agent runner daemon main loop, focused on the stale-attempt reconcile switch."""

from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    RepositoryRunContext,
)
from backend.core.use_cases.agent_runner_reclaim import format_claim_marker
from backend.core.use_cases.agent_runner_session_store import save_agent_session_record
from backend.core.use_cases.run_agent_daemon import run_agent_daemon
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import worktree_site, worktree_site_response

#: 一个足够老的 claim 时间戳：与系统当前时间的差值远大于下面注入的 TTL，
#: 因此僵尸判定必然走 ``ttl_expired``（PID 仍活但 claim 超龄）分支。
ANCIENT_CLAIM_START = datetime(2026, 7, 4, 8, 0, 0, tzinfo=timezone.utc)


def _build_context(
    repo_id: str, repo_path: Path, *, config: AppConfig | None = None
) -> RepositoryRunContext:
    """Build a minimal RepositoryRunContext for daemon tests."""
    return RepositoryRunContext(
        repo_id=repo_id,
        repo_path=repo_path,
        display_name=repo_id,
        config=config or AppConfig(),
    )


def _stop_after_first_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 daemon 跑完第一轮就退出 ``while True``，且后续阶段什么都不做。"""
    from backend.core.use_cases import run_agent_daemon as daemon_module

    def fake_sleep(_seconds: float) -> None:
        raise StopIteration

    def boom(*_args, **_kwargs):  # noqa: ANN001 - signature mirrors run_once / rework
        raise RuntimeError("stop after the reconcile phase for this test")

    monkeypatch.setattr(daemon_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(daemon_module, "run_once", boom)
    monkeypatch.setattr(daemon_module, "process_prd_rework_issues", boom)


def _seed_zombie_claim(
    github: FakeGitHubClient,
    config: AppConfig,
    *,
    agent: str | None = None,
) -> None:
    """把 Issue #7 造成「本机认领、claim 超 TTL、停在 agent/running」的僵尸现场。"""
    github.edit_issue_labels(7, add=[config.labels.running])
    github.comment_issue(
        7,
        "## Agent Runner Claimed\n"
        + format_claim_marker(
            socket.gethostname(),
            os.getpid(),
            started_at=ANCIENT_CLAIM_START,
            agent=agent,
        ),
    )
    github.set_list_issues_by_label_result([github.get_issue(7)])


def _run_one_daemon_pass(
    tmp_path: Path,
    github: FakeGitHubClient,
    *,
    reconcile_stale_attempts: bool,
    config: AppConfig | None = None,
    reclaim_ttl_seconds: int = 3600,
) -> None:
    """跑一轮 daemon（只做对账，不执行 Issue），以 ``StopIteration`` 收尾。"""
    with pytest.raises(StopIteration):
        run_agent_daemon(
            contexts=[_build_context("reconcile-test", tmp_path, config=config)],
            interval=1,
            agent="claude",
            max_issues=1,
            # worktree 现场探测用假执行器：默认 path_command 必须解析到一个存在的
            # 目录，否则对账会判成「现场不可解析」→ 判失败，而不是重新入队。
            process_runner=FakeProcessRunner(worktree_site_response(tmp_path, 7)),
            github_client_factory=lambda _repo_path: github,
            reconcile_stale_attempts=reconcile_stale_attempts,
            reclaim_ttl_seconds=reclaim_ttl_seconds,
        )


def _only_reconcile_comment(github: FakeGitHubClient) -> str:
    """取出 daemon 写下的唯一一条对账 comment（缺失或重复都判失败）。"""
    comments = [
        comment_body
        for comment_body in github.list_issue_comments(7)
        if "## Stale Attempt Reconciled" in comment_body
    ]
    assert len(comments) == 1, comments
    return comments[0]


def test_daemon_reconcile_ttl_expired_reenqueues_with_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """开关打开 + 老 claim（PID 仍活）→ 判「重新入队」，回 ready 并四要素留痕。"""
    _stop_after_first_pass(monkeypatch)
    config = AppConfig()
    github = FakeGitHubClient()
    # marker 不带 agent（旧格式）→ 续传能力无从确认 → 降级为全新会话重跑。
    _seed_zombie_claim(github, config)

    _run_one_daemon_pass(tmp_path, github, reconcile_stale_attempts=True)

    labels = github.get_issue(7).labels
    assert config.labels.ready in labels
    assert config.labels.running not in labels
    comment = _only_reconcile_comment(github)
    assert "| Interrupted at (UTC) | 2026-07-04 08:00:00 |" in comment
    assert "| Classification | stale_attempt |" in comment
    assert "| Staleness detected by | ttl_expired |" in comment
    assert "| Disposition | re-enqueue |" in comment
    assert "- worktree: resolvable" in comment


def test_daemon_reconcile_disabled_leaves_zombie_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """rv-1 的单元级负控：``reconcile_stale_attempts=False`` 时僵尸保持 agent/running。"""
    _stop_after_first_pass(monkeypatch)
    config = AppConfig()
    github = FakeGitHubClient()
    _seed_zombie_claim(github, config)

    _run_one_daemon_pass(tmp_path, github, reconcile_stale_attempts=False)

    labels = github.get_issue(7).labels
    assert config.labels.running in labels
    assert config.labels.ready not in labels
    assert all(
        "Stale Attempt Reconciled" not in comment_body
        for comment_body in github.list_issue_comments(7)
    )


def test_daemon_reconcile_enabled_resumes_recorded_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """claim 带 claude + worktree 留有会话记录 → 判「续传恢复」，同样回 ready。"""
    _stop_after_first_pass(monkeypatch)
    config = AppConfig()
    github = FakeGitHubClient()
    _seed_zombie_claim(github, config, agent="claude")
    # path_command 回 "." → worktree 解析到 repo_path 本身，会话记录也就落在那里。
    save_agent_session_record(
        worktree_site(tmp_path, 7),
        agent_name="claude",
        session_id="sess-abc",
        issue_number=7,
    )

    _run_one_daemon_pass(tmp_path, github, reconcile_stale_attempts=True)

    assert config.labels.ready in github.get_issue(7).labels
    comment = _only_reconcile_comment(github)
    assert "| Disposition | resume-session |" in comment
    assert "- session record: present" in comment


def test_repo_level_daemon_switch_overrides_caller_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """仓库层写了 ``reconcile_stale_attempts = false`` 时，该仓不对账，即使调用方默认打开。

    这是 rv-1 负控的代码侧对应物：daemon 可同时服务多仓，开关必须按仓判定；
    rv-1 用真实沙箱仓库 + 真实 kill -9 验证同一规则。
    """
    from backend.core.shared.models.agent_runner import DaemonConfig

    _stop_after_first_pass(monkeypatch)
    config = AppConfig(daemon=DaemonConfig(reconcile_stale_attempts=False))
    github = FakeGitHubClient()
    _seed_zombie_claim(github, config)

    _run_one_daemon_pass(
        tmp_path,
        github,
        reconcile_stale_attempts=True,
        config=config,
    )

    labels = github.get_issue(7).labels
    assert config.labels.running in labels
    assert config.labels.ready not in labels
    assert not [
        body for body in github.list_issue_comments(7) if "Stale Attempt Reconciled" in body
    ]


def test_repo_level_ttl_overrides_caller_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """仓库层 ``reclaim_ttl_seconds`` 生效：调用方给 3600，仓库给 10 → 老 claim 判 stale。"""
    from backend.core.shared.models.agent_runner import DaemonConfig

    _stop_after_first_pass(monkeypatch)
    # TTL 由仓库层给一个必然被 ANCIENT_CLAIM_START 超出的小值；调用方默认值刻意设得
    # 比 claim 年龄更大，若仓库层不生效则僵尸不会被判定 stale。
    config = AppConfig(daemon=DaemonConfig(reclaim_ttl_seconds=60))
    github = FakeGitHubClient()
    _seed_zombie_claim(github, config)

    _run_one_daemon_pass(
        tmp_path,
        github,
        reconcile_stale_attempts=True,
        config=config,
        # 调用方默认 10 年，远大于 claim 年龄：仓库层的 60 若不生效，僵尸必然判不出
        # stale，本测试就会变红。
        reclaim_ttl_seconds=315_360_000,
    )

    assert config.labels.ready in github.get_issue(7).labels

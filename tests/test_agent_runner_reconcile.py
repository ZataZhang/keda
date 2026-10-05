"""崩溃对账引擎（Phase -1）的三出口判定、留痕与幂等单元测试。

覆盖 :mod:`backend.core.use_cases.agent_runner_reconcile` 的整条"取证 → 判定 →
执行"链路，以及 :mod:`backend.core.use_cases.agent_runner_failure` 里的纯判定
阶梯与对账 comment 渲染。daemon 侧的开关接线见
``tests/test_agent_runner_daemon_ttl.py``。
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_failure import (
    StaleAttemptDecision,
    StaleAttemptDisposition,
    StaleAttemptEvidence,
    StaleAttemptReport,
    decide_stale_attempt_disposition,
    format_reconcile_marker,
    format_stale_attempt_comment,
    parse_reconcile_marker,
)
from backend.core.use_cases.agent_runner_reclaim import (
    ClaimMarkerDetail,
    format_claim_marker,
)
from backend.core.use_cases.agent_runner_reconcile import reconcile_stale_attempts
from backend.core.use_cases.agent_runner_session_store import save_agent_session_record
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import worktree_site, worktree_site_response

#: 一个不会存在的 PID，用于稳定复现"认领进程已死"。
DEAD_PID = 2_147_483_646

#: 两小时前的 claim：不设 TTL 时靠 PID 死亡判 stale，设 TTL 时也能命中超龄分支。
OLD_CLAIM_START = datetime.now(timezone.utc) - timedelta(hours=2)


def _seed_running_claim(
    github: FakeGitHubClient,
    config: AppConfig,
    *,
    pid: int = DEAD_PID,
    started_at: datetime | None = OLD_CLAIM_START,
    agent: str | None = None,
    host: str | None = None,
) -> None:
    """把 Issue #7 造成 ``agent/running`` + 一条指定归属的 claim marker。"""
    github.edit_issue_labels(7, add=[config.labels.running])
    github.comment_issue(
        7,
        "## Agent Runner Claimed\n"
        + format_claim_marker(
            host if host is not None else socket.gethostname(),
            pid,
            started_at=started_at,
            agent=agent,
        ),
    )
    github.set_list_issues_by_label_result([github.get_issue(7)])


def _run_reconcile(
    tmp_path: Path,
    github: FakeGitHubClient,
    config: AppConfig,
    **overrides: object,
) -> list:
    """对 Issue #7 跑一轮对账（worktree 现场默认可解析、PID 探针走真实系统）。"""
    call_kwargs: dict[str, object] = {
        "repo_path": tmp_path,
        "config": config,
        "github_client": github,
    }
    # 只在调用方不指定 process_runner 时才造默发现场：造现场会写 ``.git`` 指针，
    # 无条件构造会把测试刚删掉的指针复活。
    if "process_runner" not in overrides:
        call_kwargs["process_runner"] = FakeProcessRunner(worktree_site_response(tmp_path, 7))
    call_kwargs.update(overrides)
    return reconcile_stale_attempts(**call_kwargs)  # type: ignore[arg-type]


def _reconcile_comments(github: FakeGitHubClient) -> list[str]:
    """取出所有对账留痕 comment。"""
    return [
        comment_body
        for comment_body in github.list_issue_comments(7)
        if "## Stale Attempt Reconciled" in comment_body
    ]


class TestDecideStaleAttemptDisposition:
    """三出口判定阶梯（纯函数，无 I/O）。"""

    def test_unresolvable_worktree_fails(self) -> None:
        """现场不可解析优先判失败：没有可续跑 / 可重跑的载体。"""
        decision = decide_stale_attempt_disposition(
            StaleAttemptEvidence(worktree_resolvable=False, worktree_detail="path missing")
        )
        assert decision.disposition is StaleAttemptDisposition.FAIL
        assert "path missing" in decision.basis[0]

    def test_exhausted_budget_fails(self) -> None:
        """对账次数已达 ``max_recovery_attempts`` → 判失败（FR-6 复用既有预算）。"""
        decision = decide_stale_attempt_disposition(
            StaleAttemptEvidence(
                worktree_resolvable=True,
                claim_agent="claude",
                resume_capable=True,
                session_id="sess-1",
                stale_attempt_count=5,
                max_recovery_attempts=5,
            )
        )
        assert decision.disposition is StaleAttemptDisposition.FAIL
        assert "budget is exhausted" in decision.reason

    def test_recorded_session_resumes(self) -> None:
        """agent 声明续传 + 有会话记录 → 续传恢复。"""
        decision = decide_stale_attempt_disposition(
            StaleAttemptEvidence(
                worktree_resolvable=True,
                claim_agent="claude",
                resume_capable=True,
                session_id="sess-1",
                max_recovery_attempts=5,
            )
        )
        assert decision.disposition is StaleAttemptDisposition.RESUME

    def test_missing_session_reenqueues(self) -> None:
        """现场完整但没有会话记录 → 重新入队，全新会话（FR-4 降级）。"""
        decision = decide_stale_attempt_disposition(
            StaleAttemptEvidence(
                worktree_resolvable=True,
                claim_agent="claude",
                resume_capable=True,
                max_recovery_attempts=5,
            )
        )
        assert decision.disposition is StaleAttemptDisposition.REENQUEUE
        assert "no session record" in decision.reason

    def test_agent_without_capability_reenqueues(self) -> None:
        """agent 没声明续传能力时，即便留有会话 id 也不续（不硬编码特定 CLI）。"""
        decision = decide_stale_attempt_disposition(
            StaleAttemptEvidence(
                worktree_resolvable=True,
                claim_agent="codex",
                resume_capable=False,
                session_id="sess-1",
                max_recovery_attempts=5,
            )
        )
        assert decision.disposition is StaleAttemptDisposition.REENQUEUE
        assert "does not declare session resume" in decision.reason

    def test_unknown_agent_reenqueues(self) -> None:
        """老 marker 不带 agent → 无法确认能力，降级为重新入队。"""
        decision = decide_stale_attempt_disposition(
            StaleAttemptEvidence(worktree_resolvable=True, max_recovery_attempts=5)
        )
        assert decision.disposition is StaleAttemptDisposition.REENQUEUE
        assert "unknown claiming agent" in decision.reason


class TestStaleAttemptCommentRendering:
    """对账 comment 的四要素留痕与去重标记往返。"""

    def _report(self, disposition: StaleAttemptDisposition) -> StaleAttemptReport:
        """构造一份最小渲染输入（claim 归属与时间已知）。"""
        return StaleAttemptReport(
            issue_number=7,
            claim=ClaimMarkerDetail(
                host="host-a",
                pid=1234,
                started_at=datetime(2026, 10, 1, 8, 30, 0, tzinfo=timezone.utc),
                agent="claude",
            ),
            decision=StaleAttemptDecision(
                disposition,
                "reason under test",
                ("worktree: resolvable", "session record: present"),
            ),
            dedupe_hash="abcdef0123456789",
            stale_attempt_seq=1,
            stale_reason="dead_pid",
        )

    @pytest.mark.parametrize(
        "disposition",
        [
            StaleAttemptDisposition.RESUME,
            StaleAttemptDisposition.REENQUEUE,
            StaleAttemptDisposition.FAIL,
        ],
    )
    def test_comment_carries_four_elements(self, disposition: StaleAttemptDisposition) -> None:
        """每个出口都留下中断时间 / 原因分类 / 处置结论 / 依据四要素。"""
        comment = format_stale_attempt_comment(self._report(disposition))
        assert "| Interrupted at (UTC) | 2026-10-01 08:30:00 |" in comment
        assert "| Classification | stale_attempt |" in comment
        assert "| Staleness detected by | dead_pid |" in comment
        assert f"| Disposition | {disposition.value} |" in comment
        assert "### Basis" in comment
        assert "- session record: present" in comment

    def test_missing_claim_timestamp_is_visible(self) -> None:
        """老 marker 没有时间戳时不能渲染成空白，必须显式标注缺失。"""
        report = self._report(StaleAttemptDisposition.REENQUEUE)
        comment = format_stale_attempt_comment(
            StaleAttemptReport(
                issue_number=report.issue_number,
                claim=ClaimMarkerDetail(host="host-a", pid=1, started_at=None, agent=None),
                decision=report.decision,
                dedupe_hash=report.dedupe_hash,
                stale_attempt_seq=report.stale_attempt_seq,
                stale_reason=report.stale_reason,
            )
        )
        assert "| Interrupted at (UTC) | - |" in comment

    def test_reconcile_marker_round_trip(self) -> None:
        """隐藏标记写进去能读回来，供重放去重与预算计数使用。"""
        marker = format_reconcile_marker("abcdef0123456789", 3)
        assert parse_reconcile_marker(marker) == ("abcdef0123456789", 3)
        assert parse_reconcile_marker("no marker here") is None


class TestReconcileEngine:
    """引擎侧的证据护栏、处置落账与幂等。"""

    def test_dead_pid_reenqueues_with_one_comment(self, tmp_path: Path) -> None:
        """PID 已死 + 现场可解析 + 无会话记录 → 回 ready 并留一条对账 comment。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config)

        outcomes = _run_reconcile(tmp_path, github, config)

        assert [outcome.disposition for outcome in outcomes] == [StaleAttemptDisposition.REENQUEUE]
        assert outcomes[0].applied is True
        assert config.labels.ready in github.get_issue(7).labels
        assert len(_reconcile_comments(github)) == 1
        assert "dead_pid" in _reconcile_comments(github)[0]

    def test_replay_writes_no_second_comment(self, tmp_path: Path) -> None:
        """同一结论重放：不再追加评论，label 也不再抖动（FR-5）。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config)
        _run_reconcile(tmp_path, github, config)
        labels_after_first = github.get_issue(7).labels
        # 首轮已把 Issue 拨回 ready，所以扫描本身不会再命中；拨回 running 复现
        # "同一 claim、同一结论再来一轮"（例如首轮 comment 写成功但 label 没改成）。
        github.edit_issue_labels(7, add=[config.labels.running])
        github.set_list_issues_by_label_result([github.get_issue(7)])

        second_round = _run_reconcile(tmp_path, github, config)

        assert len(_reconcile_comments(github)) == 1
        assert github.get_issue(7).labels == labels_after_first
        assert second_round[0].applied is True
        assert "comment already recorded" in second_round[0].reason

    def test_cross_host_claim_untouched(self, tmp_path: Path) -> None:
        """别的机器认领的僵尸不在本机回收范围内（保守护栏）。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config, host="some-other-machine")

        outcomes = _run_reconcile(tmp_path, github, config)

        assert outcomes == []
        assert config.labels.running in github.get_issue(7).labels
        assert _reconcile_comments(github) == []

    def test_claim_without_marker_untouched(self, tmp_path: Path) -> None:
        """没有 claim marker 就无法证明归属与死活，一律不触碰。"""
        config = AppConfig()
        github = FakeGitHubClient()
        github.edit_issue_labels(7, add=[config.labels.running])
        github.comment_issue(7, "## Agent Runner Claimed\nno marker")
        github.set_list_issues_by_label_result([github.get_issue(7)])

        assert _run_reconcile(tmp_path, github, config) == []
        assert config.labels.running in github.get_issue(7).labels

    def test_live_claim_within_ttl_untouched(self, tmp_path: Path) -> None:
        """PID 仍活且未过 TTL 的在途运行不得被对账打扰。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config, pid=os.getpid(), started_at=datetime.now(timezone.utc))

        outcomes = _run_reconcile(tmp_path, github, config, ttl_seconds=3600)

        assert outcomes == []
        assert config.labels.running in github.get_issue(7).labels

    def test_live_claim_past_ttl_is_reconciled(self, tmp_path: Path) -> None:
        """PID 仍活但 claim 超龄 → 按 ``ttl_expired`` 对账（卡死兜底）。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config, pid=os.getpid(), started_at=OLD_CLAIM_START)

        outcomes = _run_reconcile(tmp_path, github, config, ttl_seconds=3600)

        assert [outcome.disposition for outcome in outcomes] == [StaleAttemptDisposition.REENQUEUE]
        assert "ttl_expired" in outcomes[0].reason

    def test_unresolvable_worktree_marks_failed(self, tmp_path: Path) -> None:
        """现场不可解析（path_command 空 stdout）→ 判失败并转 agent/failed。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config)

        outcomes = _run_reconcile(tmp_path, github, config, process_runner=FakeProcessRunner())

        assert [outcome.disposition for outcome in outcomes] == [StaleAttemptDisposition.FAIL]
        assert config.labels.failed in github.get_issue(7).labels
        assert config.labels.running not in github.get_issue(7).labels
        assert "- worktree: unresolvable" in _reconcile_comments(github)[0]

    def test_worktree_without_git_pointer_marks_failed(self, tmp_path: Path) -> None:
        """现场目录还在但 ``.git`` 指针被删 → 同判不可解析，不能放回 ready 白跑一轮。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config)
        responses = worktree_site_response(tmp_path, 7)
        worktree_site(tmp_path, 7).joinpath(".git").unlink()

        outcomes = _run_reconcile(
            tmp_path, github, config, process_runner=FakeProcessRunner(responses)
        )

        assert [outcome.disposition for outcome in outcomes] == [StaleAttemptDisposition.FAIL]
        assert config.labels.failed in github.get_issue(7).labels
        comment = _reconcile_comments(github)[0]
        assert "- worktree: unresolvable" in comment
        assert "no .git entry" in comment

    def test_budget_exhaustion_marks_failed(self, tmp_path: Path) -> None:
        """已有 ``max_recovery_attempts`` 条对账记录 → 判失败，不再重跑。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config)
        for seq in range(1, config.runner.max_recovery_attempts + 1):
            github.comment_issue(
                7,
                "## Stale Attempt Reconciled\n" + format_reconcile_marker(f"{seq:016x}", seq),
            )

        outcomes = _run_reconcile(tmp_path, github, config)

        assert [outcome.disposition for outcome in outcomes] == [StaleAttemptDisposition.FAIL]
        assert "budget is exhausted" in outcomes[0].reason
        assert config.labels.failed in github.get_issue(7).labels

    def test_recorded_session_resumes_next_claim(self, tmp_path: Path) -> None:
        """claim 带 agent + worktree 留有本 Issue 会话记录 → 判续传恢复（同样回 ready）。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config, agent="claude")
        save_agent_session_record(
            worktree_site(tmp_path, 7),
            agent_name="claude",
            session_id="sess-abc",
            issue_number=7,
        )

        outcomes = _run_reconcile(tmp_path, github, config)

        assert [outcome.disposition for outcome in outcomes] == [StaleAttemptDisposition.RESUME]
        assert config.labels.ready in github.get_issue(7).labels

    def test_session_of_other_issue_does_not_resume(self, tmp_path: Path) -> None:
        """worktree 复用到别的 Issue 时，历史会话不得污染本轮判定。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config, agent="claude")
        save_agent_session_record(
            worktree_site(tmp_path, 7),
            agent_name="claude",
            session_id="sess-other",
            issue_number=999,
        )

        outcomes = _run_reconcile(tmp_path, github, config)

        assert [outcome.disposition for outcome in outcomes] == [StaleAttemptDisposition.REENQUEUE]
        assert "- session record: absent" in _reconcile_comments(github)[0]

    def test_closed_issue_untouched(self, tmp_path: Path) -> None:
        """已关闭的 Issue 不参与对账（僵尸判定只针对在跑的开放任务）。"""
        config = AppConfig()
        github = FakeGitHubClient()
        _seed_running_claim(github, config)
        github.set_list_issues_by_label_result(
            [
                IssueSummary(
                    number=7,
                    title="closed zombie",
                    url="https://example.invalid/7",
                    body="",
                    labels=(config.labels.running,),
                    state="CLOSED",
                )
            ]
        )

        assert _run_reconcile(tmp_path, github, config) == []
        assert config.labels.running in github.get_issue(7).labels

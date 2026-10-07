"""显式定向准入与 daemon mutex 收窄的 focused 测试（PRD P1-FEAT-20261006-122336 FR-21/22/24）。

覆盖三件事：定向不再要求就绪标记（core 判定）、不可领取目标给出响亮错误与正确
退出码（CLI 映射）、以及互斥只挡队列轮询（显式单目标与守护进程共存）。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import backend.api.cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_exit_codes import ExitCode
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimBid, format_claim_comment
from backend.core.use_cases.agent_runner_events import format_event_marker
from backend.core.use_cases.run_target_admission import (
    TARGET_UNCLAIMABLE_BLOCKED,
    TARGET_UNCLAIMABLE_LIVE_CLAIM,
    TARGET_UNCLAIMABLE_NOT_FOUND,
    TargetNotClaimableError,
    has_non_ready_workflow_label,
    require_explicit_target_claimable,
)
from tests.conftest import FakeGitHubClient

HOST = "runner-local"
NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = AppConfig()


def _client(
    *,
    labels: tuple[str, ...] = (),
    state: str = "OPEN",
    claims: tuple[tuple[str, int, datetime], ...] = (),
    extra_comments: tuple[str, ...] = (),
) -> FakeGitHubClient:
    """构造带指定标签、状态与认领历史的 Fake 客户端。"""
    client = FakeGitHubClient()
    client._issue_labels[7] = labels
    client._issue_states[7] = state
    for comment in extra_comments:
        client.comment_issue(7, comment)
    for host, pid, started_at in claims:
        client.comment_issue(7, format_claim_comment(ClaimBid(0, host, pid, started_at, "claude")))
    return client


def _check(client: FakeGitHubClient, *, host: str = HOST, alive: set[int] | None = None) -> None:
    """以固定本机身份跑一次准入判定。"""
    alive_pids = {111} if alive is None else alive
    require_explicit_target_claimable(
        issue_number=7,
        github_client=client,
        config=CONFIG,
        host=host,
        pid_alive=lambda pid: pid in alive_pids,
    )


def _error(
    client: FakeGitHubClient,
    *,
    host: str = HOST,
    alive: set[int] | None = None,
) -> TargetNotClaimableError:
    """断言准入被拒绝并返回该错误。"""
    with pytest.raises(TargetNotClaimableError) as exc_info:
        _check(client, host=host, alive=alive)
    return exc_info.value


# ─────────────────────────────────────────────────────────────────────────────
# core：准入判定
# ─────────────────────────────────────────────────────────────────────────────


def test_bare_issue_without_workflow_label_is_claimable() -> None:
    """没有任何 workflow 标签的 Issue 可被显式定向领取（FR-6 的放宽入口）。"""
    _check(_client(labels=("type/feature", "status/backlog")))


def test_ready_issue_is_claimable() -> None:
    """带就绪标记的 Issue 仍可领取（放宽没有把原有路径堵掉）。"""
    _check(_client(labels=(CONFIG.labels.ready,)))


def test_closed_issue_is_not_found() -> None:
    """已关闭的 Issue 报 not_found，而不是静默跑一轮空队列。"""
    error = _error(_client(state="CLOSED"))
    assert error.reason == TARGET_UNCLAIMABLE_NOT_FOUND


def test_unreadable_issue_is_not_found() -> None:
    """读不到目标时报 not_found（错误如实回报，不假装成功）。"""
    client = _client()

    def _fail(_issue_number: int) -> IssueSummary:
        raise RuntimeError("gh: Issue Not Found")

    client.get_issue = _fail  # type: ignore[method-assign]
    error = _error(client)
    assert error.reason == TARGET_UNCLAIMABLE_NOT_FOUND


def test_live_foreign_claim_is_rejected_naming_the_holder() -> None:
    """他人活跃认领 → live_claim，错误里点名持有者 host/PID。"""
    error = _error(
        _client(labels=(CONFIG.labels.running,), claims=(("runner-remote", 555, NOW),)),
        alive=set(),
    )
    assert error.reason == TARGET_UNCLAIMABLE_LIVE_CLAIM
    assert error.holder == ("runner-remote", 555)
    assert "runner-remote" in str(error) and "555" in str(error)
    assert "takeover" not in error.suggestion


def test_live_local_claim_suggests_takeover() -> None:
    """本机守护进程持有的目标，建议指向 --takeover。"""
    error = _error(
        _client(labels=(CONFIG.labels.running,), claims=((HOST, 555, NOW),)),
        alive={555},
    )
    assert error.reason == TARGET_UNCLAIMABLE_LIVE_CLAIM
    assert "--takeover" in error.suggestion


def test_dead_local_claim_falls_through_to_the_recovery_channel() -> None:
    """持有者进程已死时不拒绝，交给 running 通道的恢复路径。"""
    _check(_client(labels=(CONFIG.labels.running,), claims=((HOST, 555, NOW),)), alive=set())


def test_running_issue_without_any_claim_marker_is_claimable() -> None:
    """只有标签没有认领归属时不臆造冲突（归属信息缺失不构成本次拒绝理由）。"""
    _check(_client(labels=(CONFIG.labels.running,)))


def test_blocked_issue_without_resolution_marker_is_rejected() -> None:
    """blocked 且无解除标记 → 拒绝并提示 iar blocked-continue。"""
    error = _error(_client(labels=(CONFIG.labels.blocked,)))
    assert error.reason == TARGET_UNCLAIMABLE_BLOCKED
    assert "iar blocked-continue --issue 7" in error.suggestion


def test_blocked_issue_with_unconsumed_resolution_marker_is_claimable() -> None:
    """已请求解除（未消费的 marker）的 blocked Issue 可以定向跑。"""
    _check(
        _client(
            labels=(CONFIG.labels.blocked,),
            extra_comments=(format_event_marker(phase="blocked_resolution_requested", cycle=1),),
        )
    )


def test_blocked_issue_with_consumed_resolution_marker_stays_rejected() -> None:
    """解除请求已被完成事件消费掉时，仍按 blocked 拒绝。"""
    error = _error(
        _client(
            labels=(CONFIG.labels.blocked,),
            extra_comments=(
                format_event_marker(phase="blocked_resolution_requested", cycle=1),
                format_event_marker(phase="draft_pr_created", cycle=1),
            ),
        )
    )
    assert error.reason == TARGET_UNCLAIMABLE_BLOCKED


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ((), False),
        (("type/feature", "agent/claude"), False),
        ((CONFIG.labels.ready,), False),
        ((CONFIG.labels.running,), True),
        ((CONFIG.labels.review,), True),
        ((CONFIG.labels.failed,), True),
    ],
)
def test_non_ready_workflow_label_detection(labels: tuple[str, ...], expected: bool) -> None:
    """放宽只覆盖「无状态 / 就绪」两类，其他 durable 状态仍走原通道。"""
    assert has_non_ready_workflow_label(labels, CONFIG) is expected


# ─────────────────────────────────────────────────────────────────────────────
# CLI：退出码映射与 daemon 共存
# ─────────────────────────────────────────────────────────────────────────────


def _run_context(repo_path: Path) -> SimpleNamespace:
    """最小仓库上下文（CLI 分发只读这三个字段）。"""
    return SimpleNamespace(
        repo_path=repo_path,
        repo_id="keda-test",
        display_name="Keda Test",
        config=CONFIG,
    )


@contextmanager
def _cli_patches(client: FakeGitHubClient, *, live_daemon_pid: int | None):
    """把 ``iar run`` 的分发打桩到 Fake 客户端与假 daemon 上，并记录是否真正开跑。"""
    runs: list[dict] = []

    def _fake_run(**kwargs: object) -> int:
        runs.append(kwargs)
        return 0

    with (
        patch("backend.api.cli_helpers.resolve_repository_targets") as resolve_targets,
        patch("backend.api.cli.create_github_client", return_value=client),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.core.use_cases.daemon_single_instance.find_live_daemon_pid",
            return_value=live_daemon_pid,
        ),
        patch("backend.api.cli.run_agent_repositories_once", side_effect=_fake_run),
    ):
        resolve_targets.return_value = [_run_context(Path("/tmp/repo"))]
        yield runs


def _main(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    """跑一次真实 CLI 分发（不联网：仓库目标与 GitHub 客户端均已打桩）。"""
    monkeypatch.setenv("IAR_SKIP_GH_AUTH_CHECK", "1")
    from backend.api.cli import main

    return main(argv)


def test_cli_run_on_foreign_live_claim_exits_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """定向到他人活跃认领的 Issue → CONFLICT(5)，且不进入执行。"""
    client = _client(labels=(CONFIG.labels.running,), claims=(("runner-remote", 555, NOW),))
    with _cli_patches(client, live_daemon_pid=None) as runs:
        exit_code = _main(["run", "--issue", "7"], monkeypatch)

    assert exit_code == int(ExitCode.CONFLICT)
    assert runs == []


def test_cli_run_on_closed_issue_exits_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """定向到已关闭的 Issue → NOT_FOUND(3)。"""
    with _cli_patches(_client(state="CLOSED"), live_daemon_pid=None) as runs:
        exit_code = _main(["run", "--issue", "7"], monkeypatch)

    assert exit_code == int(ExitCode.NOT_FOUND)
    assert runs == []


def test_cli_run_on_blocked_issue_suggests_blocked_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """定向到 blocked 且无解除标记 → 非零退出并提示 iar blocked-continue。"""
    client = _client(labels=(CONFIG.labels.blocked,))
    with _cli_patches(client, live_daemon_pid=None) as runs:
        exit_code = _main(["run", "--issue", "7"], monkeypatch)

    assert exit_code == int(ExitCode.CONFLICT)
    assert runs == []


def test_cli_explicit_target_coexists_with_a_live_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """守护进程在跑时，显式单目标不再被 mutex 拒绝（FR-24 ①）。"""
    with _cli_patches(_client(), live_daemon_pid=4242) as runs:
        exit_code = _main(["run", "--issue", "7"], monkeypatch)

    assert exit_code == 0
    assert runs[0]["target_issue"] == 7


def test_cli_all_ready_is_still_blocked_by_the_daemon_mutex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """队列轮询仍被 mutex 拦住（负控锚点：mutex 整体拿掉时这条必须失败）。"""
    with _cli_patches(_client(), live_daemon_pid=4242) as runs:
        exit_code = _main(["run", "--all-ready"], monkeypatch)

    assert exit_code == int(ExitCode.CONFLICT)
    assert runs == []


def test_cli_prd_path_target_also_bypasses_the_mutex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD 路径目标等价于显式单目标，同样与守护进程共存。"""
    prd_path = tmp_path / "tasks" / "pending" / "P1-FEAT-20260101-000000-x.md"
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text(
        "# PRD\n\n- GitHub Issue: https://github.com/example/repo/issues/7\n",
        encoding="utf-8",
    )
    client = _client()
    with (
        patch("backend.api.cli_helpers.resolve_repository_targets") as resolve_targets,
        patch("backend.api.cli.create_github_client", return_value=client),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch(
            "backend.core.use_cases.daemon_single_instance.find_live_daemon_pid",
            return_value=4242,
        ),
        patch("backend.api.cli.run_agent_repositories_once", return_value=0) as mock_run,
    ):
        context = _run_context(tmp_path)
        resolve_targets.return_value = [context]
        monkeypatch.chdir(tmp_path)
        exit_code = _main(["run", "tasks/pending/P1-FEAT-20260101-000000-x.md"], monkeypatch)

    assert exit_code == 0
    assert mock_run.call_args.kwargs["target_issue"] == 7


def test_cli_takeover_skips_the_admission_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--takeover 是强制回收入口：不被准入拦下（语义不变）。"""
    from backend.api.cli_run_takeover import DaemonTakeoverResult

    client = _client(labels=(CONFIG.labels.running,), claims=((HOST, 555, NOW),))
    takeover_result = DaemonTakeoverResult(
        repo_id="keda-test",
        daemon_pid=4242,
        managed=False,
        final_signal="sigterm",
        reclaimed_issues=(7,),
    )
    with (
        _cli_patches(client, live_daemon_pid=4242) as runs,
        patch(
            "backend.api.cli_run_takeover.take_over_daemon",
            return_value=takeover_result,
        ),
    ):
        exit_code = _main(["run", "--issue", "7", "--takeover", "--yes"], monkeypatch)

    assert exit_code == 0
    assert runs[0]["target_issue"] == 7

"""值守看板执行机器标签的测试（PRD dashboard-task-executor-machine 的 rv-2 / rv-3）。

链路全部走真实实现：``build_repository_overview`` 构建快照 → 路由同款序列化 →
``persist_monitoring_result`` 写真实 SQLite → 新开 store 经 HTTP 路由读取任务摘要。
只在 GitHub 端口用替身（按作者模拟可信过滤），本机身份经注入点固定。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.api.routes.agent_runner import _serialize_monitoring
from backend.core.shared.interfaces.runner_console import RunRecord
from backend.core.shared.models.agent_runner import AppConfig, IssueCommentRecord, IssueSummary
from backend.core.use_cases import runtime_dashboard
from backend.core.use_cases.agent_runner_claim_lease import format_claim_lease_marker
from backend.core.use_cases.agent_runner_monitor import build_repository_overview
from backend.core.use_cases.agent_runner_reclaim import format_claim_marker
from backend.core.use_cases.monitor_snapshots import persist_monitoring_result
from backend.core.use_cases.runtime_dashboard import LocalMachineIdentity
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeGitHubClient, FakeProcessRunner

REPO_ID = "keda"
LOCAL = LocalMachineIdentity(machine_id="machine-a", host="MacBook-Pro.local")
CLAIM_TIME = datetime.now(timezone.utc) - timedelta(minutes=30)
TRUSTED_AUTHOR = "maintainer"


class _TrustAwareGitHubClient(FakeGitHubClient):
    """按作者模拟 ``trusted_only`` 过滤，并记录认领读取调用。"""

    def __init__(self) -> None:
        super().__init__()
        self.issues: list[IssueSummary] = []
        self.comment_records: dict[int, list[IssueCommentRecord]] = {}
        self.record_calls: list[tuple[int, bool, str | None]] = []
        self.failing_issue_numbers: set[int] = set()

    def list_ready_issues(self, ready_label: str, limit: int) -> list[IssueSummary]:
        return [issue for issue in self.issues if ready_label in issue.labels][:limit]

    def list_issue_comment_records(
        self,
        issue_number: int,
        *,
        trusted_only: bool = False,
        body_contains: str | None = None,
        require_success: bool = False,
    ) -> list[IssueCommentRecord]:
        self.record_calls.append((issue_number, trusted_only, body_contains))
        if issue_number in self.failing_issue_numbers:
            raise RuntimeError("gh api 502")
        return [
            record
            for record in self.comment_records.get(issue_number, [])
            if (not trusted_only or record.author == TRUSTED_AUTHOR)
            and (body_contains is None or body_contains in record.body)
        ]

    def add_issue(self, number: int, label: str) -> None:
        self.issues.append(
            IssueSummary(
                number=number,
                title=f"Issue {number}",
                url=f"https://example.test/issues/{number}",
                body="",
                labels=(label,),
            )
        )

    def add_claim(
        self,
        number: int,
        *,
        host: str,
        machine_id: str | None,
        author: str = TRUSTED_AUTHOR,
    ) -> None:
        body = "Agent Runner Claimed\n" + format_claim_marker(host, 4242, started_at=CLAIM_TIME)
        if machine_id is not None:
            body += "\n" + format_claim_lease_marker(
                machine_id=machine_id, ttl_seconds=1200, renewed_at=CLAIM_TIME
            )
        records = self.comment_records.setdefault(number, [])
        records.append(
            IssueCommentRecord(
                comment_id=number * 100 + len(records),
                body=body,
                updated_at=CLAIM_TIME,
                author=author,
            )
        )


def _sync_and_read_tasks(
    tmp_path: Path,
    client: _TrustAwareGitHubClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    local_run_offsets: dict[int, timedelta] | None = None,
) -> dict[int, dict]:
    """执行一次真实同步写库，再经 HTTP 路由用新 store 读取任务摘要。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import agent_runner_console_runtime as runtime_routes

    overview = build_repository_overview(
        repo_id=REPO_ID,
        display_name="Keda",
        enabled=True,
        config=AppConfig(),
        github_client=client,
        process_runner=FakeProcessRunner(),
        repo_path=tmp_path,
    )
    db_path = tmp_path / "console.db"
    report = persist_monitoring_result(
        SqliteConsoleStore(db_path), {"repositories": [_serialize_monitoring(overview)]}
    )
    assert report.ok
    for issue_number, offset in (local_run_offsets or {}).items():
        started_at = CLAIM_TIME + offset
        SqliteConsoleStore(db_path).append_run(
            RunRecord(
                repo_id=REPO_ID,
                repo_path=str(tmp_path),
                issue_number=issue_number,
                trigger="cli_daemon",
                agent="codex",
                outcome="failed",
                error_summary="本机旧失败",
                started_at=started_at.isoformat(),
                finished_at=(started_at + timedelta(minutes=12)).isoformat(),
                duration_seconds=720.0,
            )
        )

    monkeypatch.setattr(runtime_routes, "_enabled_repo_ids", lambda: [REPO_ID])
    monkeypatch.setattr(runtime_routes, "create_console_store", lambda: SqliteConsoleStore(db_path))
    monkeypatch.setattr(runtime_dashboard, "_current_local_identity", lambda: LOCAL)
    runtime_app = FastAPI()
    runtime_app.include_router(runtime_routes.router)
    with TestClient(runtime_app) as runtime_client:
        response = runtime_client.get("/agent-runner/console/runtime/tasks")
    assert response.status_code == 200
    return {task["issue_number"]: task for task in response.json()["tasks"]}


def test_executor_machine_flows_from_trusted_claims_to_tasks_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名主机按机器编号区分、旧版认领按主机名、伪造认领不采信、排队不读评论。"""
    client = _TrustAwareGitHubClient()
    client.add_issue(12, "agent/running")
    client.add_claim(12, host=LOCAL.host, machine_id=LOCAL.machine_id)
    client.add_issue(15, "agent/running")
    client.add_claim(15, host=LOCAL.host, machine_id="machine-b")
    client.add_issue(16, "agent/failed")
    client.add_claim(16, host=LOCAL.host, machine_id=None)
    client.add_issue(19, "agent/blocked")
    client.add_claim(19, host="MacBook-B.local", machine_id=None)
    client.add_issue(17, "agent/failed")
    client.add_claim(17, host=LOCAL.host, machine_id=LOCAL.machine_id, author="drive-by")
    client.add_issue(18, "agent/ready")
    client.add_claim(18, host=LOCAL.host, machine_id=LOCAL.machine_id)

    tasks = _sync_and_read_tasks(tmp_path, client, monkeypatch)

    assert tasks[12]["executor_host"] == LOCAL.host
    assert tasks[12]["executor_is_local"] is True
    assert tasks[12]["executor_basis"] == "machine_id"
    assert tasks[12]["executor_role"] == "running"
    # 主机名相同但机器编号不同：必须判为其他机器。
    assert tasks[15]["executor_is_local"] is False
    assert tasks[15]["executor_basis"] == "machine_id"
    assert tasks[16]["executor_is_local"] is True
    assert tasks[16]["executor_basis"] == "hostname"
    assert tasks[16]["executor_role"] == "last_claim"
    assert tasks[19]["executor_is_local"] is False
    assert tasks[19]["executor_basis"] == "hostname"
    assert tasks[17]["executor_host"] is None
    assert tasks[18]["executor_host"] is None

    read_issue_numbers = {issue_number for issue_number, _, _ in client.record_calls}
    assert read_issue_numbers == {12, 15, 16, 17, 19}
    assert all(trusted for _, trusted, _ in client.record_calls)
    assert all(needle == "<!-- kc:claim " for _, _, needle in client.record_calls)


def test_claim_read_failure_only_drops_executor_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """认领读取失败不进入同步错误，只让该任务缺机器字段。"""
    client = _TrustAwareGitHubClient()
    client.add_issue(21, "agent/running")
    client.add_claim(21, host="MacBook-B.local", machine_id="machine-b")
    client.failing_issue_numbers.add(21)

    tasks = _sync_and_read_tasks(tmp_path, client, monkeypatch)

    assert tasks[21]["status"] == "running"
    assert tasks[21]["sync_error"] in (None, "")
    assert tasks[21]["executor_host"] is None


def test_unknown_local_machine_id_is_not_reported_as_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本机编号读不到时显示为无法判断，不默认当成本机或远端。"""
    client = _TrustAwareGitHubClient()
    client.add_issue(22, "agent/running")
    client.add_claim(22, host=LOCAL.host, machine_id=LOCAL.machine_id)
    tasks = _sync_and_read_tasks(tmp_path, client, monkeypatch)
    assert tasks[22]["executor_is_local"] is True

    summary = runtime_dashboard.build_runtime_task_summary(
        snapshot_overview=_snapshot_overview(tmp_path),
        run_history_store=None,
        prd_lifecycle_store=None,
        enabled_repo_ids=[REPO_ID],
        local_identity=LocalMachineIdentity(machine_id=None, host=LOCAL.host),
    )
    unknown_task = next(task for task in summary.tasks if task.issue_number == 22)
    assert unknown_task.executor_is_local is None
    assert unknown_task.executor_host == LOCAL.host


def _snapshot_overview(tmp_path: Path):
    """用真实快照读路径打开同一个库。"""
    from backend.core.use_cases.monitor_snapshots import get_snapshot_overview

    return get_snapshot_overview(
        SqliteConsoleStore(tmp_path / "console.db"), enabled_repo_ids=[REPO_ID]
    )


def test_local_run_superseded_by_remote_claim_clears_local_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """他人认领晚于本机运行时清空本机运行字段；本机运行更晚或是本机认领时保留。"""
    client = _TrustAwareGitHubClient()
    client.add_issue(141, "agent/failed")
    client.add_claim(141, host="MacBook-B.local", machine_id="machine-b")
    client.add_issue(142, "agent/failed")
    client.add_claim(142, host="MacBook-B.local", machine_id="machine-b")
    client.add_issue(143, "agent/failed")
    client.add_claim(143, host=LOCAL.host, machine_id=LOCAL.machine_id)

    tasks = _sync_and_read_tasks(
        tmp_path,
        client,
        monkeypatch,
        local_run_offsets={
            141: -timedelta(days=1),
            142: timedelta(minutes=5),
            143: -timedelta(days=1),
        },
    )

    superseded = tasks[141]
    assert superseded["local_run_superseded"] is True
    for field_name in ("agent", "started_at", "duration_seconds", "last_error_summary"):
        assert superseded[field_name] is None, field_name
    assert superseded["failure_reason"] is None
    assert superseded["executor_is_local"] is False

    assert tasks[142]["local_run_superseded"] is False
    assert tasks[142]["agent"] == "codex"
    assert tasks[143]["local_run_superseded"] is False
    assert tasks[143]["agent"] == "codex"

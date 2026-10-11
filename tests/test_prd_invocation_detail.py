"""PRD 调用明细读取组装：会话/尝试关联、三类降级与未闭合语义（rv-2 / rv-3 oracle）。

写入侧使用**真实**进程调用观测 API（``start_invocation`` / ``finish_invocation``）
把事件落进真实临时 SQLite，读取侧再经真实 FastAPI HTTP 客户端取回，因此断言的
字段形状来自账本实际写入的内容，而不是手写 DTO 反推。

覆盖三类必须显式披露的缺口（不可用 / 无归属 / 截断）与「未闭合不得虚构结束时间」
的诚实性约束。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_backlog as backlog_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    RepositoryRunContext,
    TokenUsage,
)
from backend.core.shared.models.backlog import PrdInvocationDetail
from backend.core.use_cases.agent_invocation_tracing import (
    PHASE_IMPLEMENTATION,
    PHASE_VERIFICATION,
    RETRY_REASON_TRANSIENT,
    InvocationStartRequest,
    InvocationTraceContext,
    bound_invocation_trace_context,
    build_invocation_timeline,
    finish_invocation,
    start_invocation,
)
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    build_prd_lifecycle_detail,
    record_lifecycle_event,
)
from backend.core.use_cases.prd_invocation_detail import (
    DEFAULT_INVOCATION_READ_LIMIT,
    build_prd_invocation_detail,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

http_client = TestClient(app)

_REPO_ID = "keda-main"
_PRD_PATH = "tasks/pending/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.md"
_ISSUE_NUMBER = 273
_SESSION_A_RUN_ID = "keda-main#issue-273#session-a"
_SESSION_B_RUN_ID = "keda-main#issue-273#session-b"


@dataclass
class _InvocationSeed:
    """一次模拟进程调用的声明，形状对齐真实调用点入参。

    Attributes:
        phase: 调用阶段（闭集）。
        agent: 实际执行器注册名。
        attempt_number: recovery 轮次；非 attempt 主体调用为 ``None``。
        requested_model: 实际下发的模型 id；未下发为 ``None``。
        reported_model: 执行器自报的模型 id；未上报为 ``None``。
        return_code: 进程退出码；非 0 记为失败调用。
        token_usage: 自报用量；``None`` 表示无数据（≠ 0）。
        unclosed: 只写开始事件（模拟被强制 kill，走不到 ``finally``）。
        retries_previous: 本次调用是否声明为替代上一次（重试链）。
    """

    phase: str = PHASE_IMPLEMENTATION
    agent: str = "codex"
    attempt_number: int | None = 1
    requested_model: str | None = None
    reported_model: str | None = None
    return_code: int = 0
    token_usage: TokenUsage | None = None
    unclosed: bool = False
    retries_previous: bool = False


class _UnreadableInvocationStore(SqliteConsoleStore):
    """读端抛异常的存储替身（模拟账本读取故障，非 sqlite3.Error 内部降级路径）。"""

    def list_issue_invocation_events(self, **kwargs: object) -> list:  # type: ignore[override]
        raise RuntimeError("invocation ledger read exploded")


class _NoIssueReaderStore:
    """只具备 run 级读端、缺少 Issue 级跨 run 读端的存储。

    能力探测必须把它判为「不可用」，而不是退化成「账本为空」——两者的页面文案不同。
    """

    def append_invocation_event(self, event_record: object) -> bool:
        return True

    def list_invocation_events(self, *, run_id: str) -> list:
        return []


def _store(tmp_path: Path) -> SqliteConsoleStore:
    return SqliteConsoleStore(tmp_path / "console.db")


def _record_invocations(
    store: SqliteConsoleStore,
    *,
    run_id: str,
    issue_number: int | None,
    invocation_seeds: list[_InvocationSeed],
) -> list[str]:
    """在绑定观测上下文的前提下按顺序写入调用事件，返回 invocation id 列表。

    走真实观测 API，因此落库 detail 的形状与生产逐字节一致。
    """
    trace_context = InvocationTraceContext(
        repo_id=_REPO_ID,
        run_id=run_id,
        issue_number=issue_number,
        store=store,
    )
    invocation_ids: list[str] = []
    with bound_invocation_trace_context(trace_context):
        for invocation_seed in invocation_seeds:
            if invocation_seed.retries_previous:
                trace_context.link_next_invocation(RETRY_REASON_TRANSIENT)
            observation = start_invocation(
                InvocationStartRequest(
                    agent_name=invocation_seed.agent,
                    phase=invocation_seed.phase,
                    profile="run",
                    attempt_number=invocation_seed.attempt_number,
                    requested_model=invocation_seed.requested_model,
                )
            )
            assert observation is not None
            invocation_ids.append(observation.invocation_id)
            if invocation_seed.unclosed:
                continue
            finish_invocation(
                observation,
                result=CommandResult(
                    command=("fixture",),
                    return_code=invocation_seed.return_code,
                    stdout="",
                    stderr="",
                    token_usage=invocation_seed.token_usage,
                    reported_model=invocation_seed.reported_model,
                ),
            )
    return invocation_ids


def _assembled_detail(
    store: object,
    *,
    issue_number: int | None = _ISSUE_NUMBER,
    limit: int = DEFAULT_INVOCATION_READ_LIMIT,
) -> PrdInvocationDetail:
    """调用组装器的简写入口：默认按本 Issue 的关联编号读取全量账本。"""
    return build_prd_invocation_detail(
        store=store, repo_id=_REPO_ID, issue_number=issue_number, limit=limit
    )


def _ledger_events(store: SqliteConsoleStore) -> list:
    """独立 fresh read：直接读账本原始事件，作为组装结果的对照事实源。"""
    return store.list_issue_invocation_events(
        repo_id=_REPO_ID, issue_number=_ISSUE_NUMBER, limit=DEFAULT_INVOCATION_READ_LIMIT
    )


def _seed_lifecycle_event(
    store: SqliteConsoleStore,
    *,
    event_type: LifecycleEventType,
    occurred_at: str,
    issue_number: int | None = _ISSUE_NUMBER,
    detail: dict | None = None,
) -> None:
    record_lifecycle_event(
        store=store,
        repo_id=_REPO_ID,
        prd_path=_PRD_PATH,
        issue_number=issue_number,
        trigger="cli_run",
        event_type=event_type,
        actor="runner",
        occurred_at=occurred_at,
        event_key=f"{event_type.value}@{occurred_at}",
        detail=detail,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 关联完整性：会话标识、尝试编号、字段逐条一致
# ─────────────────────────────────────────────────────────────────────────────


def test_invocation_detail_carries_session_and_attempt_ids(tmp_path: Path) -> None:
    """每个调用行都带所属执行会话 run id 与尝试编号，供前端确定性归组。

    负控：若组装不回填 ``run_id`` / ``attempt_number`` 而改由时间窗口猜测归属，
    下面的逐行等值断言立即变红。
    """
    store = _store(tmp_path)
    session_a_ids = _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[
            _InvocationSeed(attempt_number=1),
            _InvocationSeed(phase=PHASE_VERIFICATION, agent="verifier", attempt_number=1),
        ],
    )
    session_b_ids = _record_invocations(
        store,
        run_id=_SESSION_B_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[_InvocationSeed(attempt_number=2)],
    )

    invocation_detail = _assembled_detail(store)

    assert invocation_detail.available is True
    assert invocation_detail.truncated is False
    assert [row.invocation_id for row in invocation_detail.rows] == (session_a_ids + session_b_ids)
    assert [row.run_id for row in invocation_detail.rows] == (
        [_SESSION_A_RUN_ID] * 2 + [_SESSION_B_RUN_ID]
    )
    assert [row.attempt_number for row in invocation_detail.rows] == [1, 1, 2]


def test_invocation_detail_matches_ledger_field_by_field(tmp_path: Path) -> None:
    """调用行的阶段/执行器/结果/耗时/模型三态与账本解读结果逐条一致。"""
    store = _store(tmp_path)
    _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[
            _InvocationSeed(
                requested_model="gpt-5",
                reported_model="gpt-5-mini",
                token_usage=TokenUsage(
                    input_tokens=100,
                    output_tokens=20,
                    cache_read_input_tokens=7,
                    cache_creation_input_tokens=3,
                ),
            ),
            _InvocationSeed(
                phase=PHASE_VERIFICATION,
                agent="claude",
                attempt_number=1,
                return_code=1,
                retries_previous=True,
            ),
        ],
    )

    invocation_detail = _assembled_detail(store)
    expected_rows = build_invocation_timeline(_ledger_events(store))

    assert len(invocation_detail.rows) == len(expected_rows) == 2
    for actual_row, ledger_row in zip(invocation_detail.rows, expected_rows, strict=True):
        assert actual_row.invocation_id == ledger_row.invocation_id
        assert actual_row.phase == ledger_row.phase
        assert actual_row.role == ledger_row.role
        assert actual_row.executor == ledger_row.executor
        assert actual_row.outcome == ledger_row.outcome
        assert actual_row.duration_seconds == ledger_row.duration_seconds
        assert actual_row.started_at == ledger_row.started_at
        assert actual_row.finished_at == ledger_row.finished_at
        assert actual_row.retry_of == ledger_row.retry_of

    ok_row, failed_row = invocation_detail.rows
    assert ok_row.outcome == "ok"
    assert ok_row.requested_model == "gpt-5"
    assert ok_row.reported_model == "gpt-5-mini"
    assert ok_row.model_source == "executor_report"
    assert ok_row.token_usage == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 3,
    }
    assert failed_row.outcome == "failed"
    assert failed_row.failure_category == "nonzero_exit"
    assert failed_row.exit_code == 1
    assert failed_row.retry_of == ok_row.invocation_id
    assert failed_row.retry_reason == RETRY_REASON_TRANSIENT
    # 未上报模型时三态必须区分「未下发」与「未提供」，不得拿配置值冒充。
    assert failed_row.requested_model is None
    assert failed_row.reported_model is None
    assert failed_row.model_source == "unknown"
    assert failed_row.token_usage is None


def test_invocation_detail_keeps_unclosed_without_fabricating_end(tmp_path: Path) -> None:
    """只有开始事件的调用记为 unclosed，且绝不虚构结束时间与耗时。"""
    store = _store(tmp_path)
    unclosed_id = _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[_InvocationSeed(unclosed=True)],
    )[0]

    unclosed_row = _assembled_detail(store).rows[0]

    assert unclosed_row.invocation_id == unclosed_id
    assert unclosed_row.outcome == "unclosed"
    assert unclosed_row.finished_at is None
    assert unclosed_row.duration_seconds is None
    assert unclosed_row.failure_category is None


def test_invocation_detail_keeps_failure_and_fallback_rows_separate(tmp_path: Path) -> None:
    """失败与回退调用各自成行，不得被合并成一次成功。"""
    store = _store(tmp_path)
    _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[
            _InvocationSeed(agent="codex", return_code=1),
            _InvocationSeed(agent="claude", retries_previous=True),
        ],
    )

    assembled_rows = _assembled_detail(store).rows

    assert [row.outcome for row in assembled_rows] == ["failed", "ok"]
    assert assembled_rows[1].retry_of == assembled_rows[0].invocation_id


def test_invocation_detail_orders_rows_by_event_stream_not_timestamp_string(tmp_path: Path) -> None:
    """行顺序沿用事件流出场顺序，与账本原始顺序一致。"""
    store = _store(tmp_path)
    invocation_ids = _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[_InvocationSeed(attempt_number=index) for index in range(1, 5)],
    )

    assert [row.invocation_id for row in _assembled_detail(store).rows] == invocation_ids


# ─────────────────────────────────────────────────────────────────────────────
# 三类降级：不可用 / 无归属 / 截断
# ─────────────────────────────────────────────────────────────────────────────


def test_invocation_detail_unavailable_when_read_fails(tmp_path: Path) -> None:
    """账本读取异常时 available=False，而不是抛错或假装空账本。"""
    failing_store = _UnreadableInvocationStore(tmp_path / "console.db")
    _record_invocations(
        failing_store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[_InvocationSeed()],
    )

    invocation_detail = _assembled_detail(failing_store)

    assert invocation_detail.available is False
    assert invocation_detail.rows == []
    assert invocation_detail.truncated is False


def test_invocation_detail_unavailable_when_reader_capability_missing() -> None:
    """存储不具备 Issue 级读端时判为不可用（能力探测，而非静默空账本）。"""
    assert _assembled_detail(_NoIssueReaderStore()).available is False


def test_invocation_detail_unavailable_without_issue_number(tmp_path: Path) -> None:
    """PRD 未关联 Issue 时无从归属：不猜、不生成假调用行。"""
    store = _store(tmp_path)
    _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[_InvocationSeed()],
    )

    invocation_detail = _assembled_detail(store, issue_number=None)

    assert invocation_detail.available is False
    assert invocation_detail.rows == []


def test_invocation_detail_truncates_at_read_limit(tmp_path: Path) -> None:
    """返回条数达到读取上限即披露已截断，不谎称历史完整。"""
    store = _store(tmp_path)
    _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[_InvocationSeed() for _ in range(3)],
    )

    truncated_detail = _assembled_detail(store, limit=4)
    untouched_detail = _assembled_detail(store, limit=50)

    # 6 条事件（3 次调用 × 起止）触顶 limit=4 -> 截断；limit=50 未触顶。
    assert truncated_detail.truncated is True
    assert len(truncated_detail.rows) == 2
    assert untouched_detail.truncated is False
    assert len(untouched_detail.rows) == 3


def test_invocation_detail_available_but_empty_for_legacy_ledger(tmp_path: Path) -> None:
    """账本可读但没有该 Issue 的调用记录：available=True + 空行（区分于不可用）。"""
    invocation_detail = _assembled_detail(_store(tmp_path))

    assert invocation_detail.available is True
    assert invocation_detail.rows == []
    assert invocation_detail.truncated is False


# ─────────────────────────────────────────────────────────────────────────────
# 详情挂接与端点契约（真实 HTTP 读回）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def api_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把生命周期详情端点接到真实临时 SQLite 与假仓库上下文。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    repo_dir = tmp_path / "repo"
    (repo_dir / "tasks" / "pending").mkdir(parents=True)
    monkeypatch.setattr(backlog_routes, "create_backlog_store", lambda: store)
    monkeypatch.setattr(
        backlog_routes,
        "_resolve_contexts",
        lambda: [
            RepositoryRunContext(
                repo_id=_REPO_ID,
                display_name="Keda Main",
                repo_path=repo_dir,
                config=AppConfig(),
            )
        ],
    )
    return store


def test_lifecycle_detail_mounts_invocations(tmp_path: Path) -> None:
    """详情组装把调用明细挂到 PrdLifecycleDetail.invocations，与会话事件同源。"""
    store = _store(tmp_path)
    _seed_lifecycle_event(
        store, event_type=LifecycleEventType.STARTED, occurred_at="2026-10-10T01:00:00+00:00"
    )
    _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[_InvocationSeed()],
    )

    lifecycle_detail = build_prd_lifecycle_detail(store=store, repo_id=_REPO_ID, prd_path=_PRD_PATH)

    assert lifecycle_detail.issue_number == _ISSUE_NUMBER
    assert lifecycle_detail.invocations is not None
    assert lifecycle_detail.invocations.available is True
    assert [row.executor for row in lifecycle_detail.invocations.rows] == ["codex"]


def test_lifecycle_detail_without_issue_number_omits_invocations(tmp_path: Path) -> None:
    """无关联 Issue 的 PRD 照常返回详情，调用明细不挂接（页面按不可归属披露）。"""
    store = _store(tmp_path)
    _seed_lifecycle_event(
        store,
        event_type=LifecycleEventType.QUEUED,
        occurred_at="2026-10-10T01:00:00+00:00",
        issue_number=None,
    )

    lifecycle_detail = build_prd_lifecycle_detail(store=store, repo_id=_REPO_ID, prd_path=_PRD_PATH)

    assert lifecycle_detail.has_data is True
    assert lifecycle_detail.invocations is None


def test_lifecycle_detail_survives_invocation_read_failure(tmp_path: Path) -> None:
    """调用账本读取失败只降级调用明细区，不得把整个详情变成空态。"""
    store = _UnreadableInvocationStore(tmp_path / "console.db")
    _seed_lifecycle_event(
        store, event_type=LifecycleEventType.ATTEMPT, occurred_at="2026-10-10T01:00:00+00:00"
    )

    lifecycle_detail = build_prd_lifecycle_detail(store=store, repo_id=_REPO_ID, prd_path=_PRD_PATH)

    assert lifecycle_detail.has_data is True
    assert len(lifecycle_detail.events) == 1
    assert lifecycle_detail.invocations is not None
    assert lifecycle_detail.invocations.available is False


def test_lifecycle_endpoint_exposes_invocations(api_environment) -> None:
    """端点响应新增 invocations 字段，且与账本 fresh read 逐条一致（rv-2）。"""
    store = api_environment
    invocation_ids = _record_invocations(
        store,
        run_id=_SESSION_A_RUN_ID,
        issue_number=_ISSUE_NUMBER,
        invocation_seeds=[
            _InvocationSeed(attempt_number=1, reported_model="gpt-5"),
            _InvocationSeed(phase=PHASE_VERIFICATION, agent="verifier", attempt_number=1),
        ],
    )
    _seed_lifecycle_event(
        store, event_type=LifecycleEventType.ATTEMPT, occurred_at="2026-10-10T01:18:00+00:00"
    )

    encoded = backlog_routes._encode_prd_path(_PRD_PATH)
    response = http_client.get(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/lifecycle",
        params={"repo_id": _REPO_ID},
    )

    assert response.status_code == 200
    invocation_payload = response.json()["invocations"]
    assert invocation_payload["available"] is True
    assert invocation_payload["truncated"] is False
    assert [row["invocation_id"] for row in invocation_payload["rows"]] == invocation_ids
    assert [row["attempt_number"] for row in invocation_payload["rows"]] == [1, 1]
    assert [row["run_id"] for row in invocation_payload["rows"]] == [_SESSION_A_RUN_ID] * 2
    assert invocation_payload["rows"][0]["reported_model"] == "gpt-5"
    assert invocation_payload["rows"][1]["role"] == "verifier"

    # fresh read 对照：HTTP 响应里的行序等于账本独立解读结果。
    ledger_rows = build_invocation_timeline(_ledger_events(store))
    assert [row["invocation_id"] for row in invocation_payload["rows"]] == [
        ledger_row.invocation_id for ledger_row in ledger_rows
    ]


def test_lifecycle_endpoint_reports_empty_ledger_for_legacy_db(api_environment) -> None:
    """调用账本表不存在（schema v9 之前的旧库）时端点仍 200 且明细可读为空。"""
    store = api_environment
    _seed_lifecycle_event(
        store, event_type=LifecycleEventType.QUEUED, occurred_at="2026-10-10T01:00:00+00:00"
    )
    raw_connection = sqlite3.connect(store._db_path)  # noqa: SLF001 - 构造旧库形态
    raw_connection.execute("DROP TABLE agent_invocation_events")
    raw_connection.commit()
    raw_connection.close()

    encoded = backlog_routes._encode_prd_path(_PRD_PATH)
    response = http_client.get(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/lifecycle",
        params={"repo_id": _REPO_ID},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_data"] is True
    assert payload["invocations"]["available"] is True
    assert payload["invocations"]["rows"] == []


def test_lifecycle_endpoint_events_carry_invocation_run(api_environment) -> None:
    """runner 执行期内的事件 detail 带会话标识；上下文之外的事件不带。"""
    store = api_environment
    _seed_lifecycle_event(
        store, event_type=LifecycleEventType.QUEUED, occurred_at="2026-10-10T00:00:00+00:00"
    )
    with bound_invocation_trace_context(
        InvocationTraceContext(
            repo_id=_REPO_ID,
            run_id=_SESSION_A_RUN_ID,
            issue_number=_ISSUE_NUMBER,
            store=store,
        )
    ):
        _seed_lifecycle_event(
            store, event_type=LifecycleEventType.ATTEMPT, occurred_at="2026-10-10T01:00:00+00:00"
        )

    encoded = backlog_routes._encode_prd_path(_PRD_PATH)
    response = http_client.get(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/lifecycle",
        params={"repo_id": _REPO_ID},
    )

    events_by_type = {event["event_type"]: event["detail"] for event in response.json()["events"]}
    assert "invocation_run" not in events_by_type["queued"]
    assert events_by_type["attempt"]["invocation_run"] == _SESSION_A_RUN_ID

"""调用用量缺失、全零与历史回退的真实 SQLite 集成验证。"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_console as console_routes
from backend.api.app import app
from backend.core.shared.interfaces.runner_console import (
    InvocationEventRecord,
    PrdLifecycleRunRecord,
)
from backend.core.use_cases.agent_runner_invocation_token_stats import (
    build_invocation_token_usage_summary,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore


def test_usage_gaps_are_counted_without_inventing_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无用量与零用量分开披露，起始事件及时间窗外事件不重复计数。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    run_date = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    run_record = PrdLifecycleRunRecord(
        run_id="lifecycle",
        repo_id="repo",
        prd_path="tasks/demo.md",
        issue_number=1,
        trigger="manual",
        started_at=f"{run_date}T01:00:00+00:00",
        finished_at=f"{run_date}T02:00:00+00:00",
        outcome="completed",
        history_complete=True,
    )
    store.upsert_lifecycle_run(run_record)
    for event_key, event_type, agent_name, usage_payload, occurred_at in (
        ("start", "invocation_started", "codex", None, "01:01:00"),
        ("missing", "invocation_finished", "codex", None, "01:02:00"),
        ("zero", "invocation_finished", "qoder", {"input_tokens": 0}, "01:03:00"),
        ("valid", "invocation_finished", "claude", {"input_tokens": 100}, "01:04:00"),
        ("outside", "invocation_finished", "codex", None, "03:00:00"),
    ):
        store.append_invocation_event(
            InvocationEventRecord(
                run_id="invocation-run",
                event_key=event_key,
                event_type=event_type,
                invocation_id=event_key,
                repo_id="repo",
                issue_number=1,
                phase="implementation",
                role="implementer",
                agent=agent_name,
                occurred_at=f"{run_date}T{occurred_at}+00:00",
                detail_json=json.dumps(
                    {"requested_model": agent_name, "token_usage": usage_payload}
                ),
            )
        )
    summary = build_invocation_token_usage_summary(store=store, run_records=[run_record])
    assert summary.token_usage_by_model.missing_usage_by_agent == {"codex": 1}
    assert summary.token_usage_by_model.zero_usage_by_model == {"qoder": 1}
    assert summary.token_usage.by_flow["implement"].total_tokens == 100
    assert summary.token_usage.by_flow["implement"].usage_count == 2
    assert "codex" not in summary.token_usage_by_model.by_model
    # 新连接穿过真实 HTTP 路由与序列化边界，证明页面能读到完整性字段。
    monkeypatch.setattr(
        console_routes,
        "create_console_store",
        lambda: SqliteConsoleStore(tmp_path / "console.db"),
    )
    with TestClient(app) as api_client:
        response = api_client.get(
            "/api/v1/agent-runner/console/stats/prd-lifecycle",
            params={"repo_id": "repo"},
        )
    assert response.status_code == 200
    model_stats = response.json()["token_usage_by_model"]
    assert model_stats["missing_usage_by_agent"] == {"codex": 1}
    assert model_stats["zero_usage_by_model"] == {"qoder": 1}

    # 优先执行器报告与明确下发；仅具体配置可回退，别名保留在 unknown。
    for index, (configured_model, is_alias, reported_model, requested_model) in enumerate(
        (
            ("gpt-6-luna", False, None, None),
            ("gpt-6-luna", False, "reported-v2", "requested-v1"),
            ("gpt-6-luna", False, None, "requested-v1"),
            ("sonnet", True, None, None),
        )
    ):
        store.append_invocation_event(
            InvocationEventRecord(
                run_id="config-run",
                event_key=f"config-{index}",
                event_type="invocation_finished",
                invocation_id=f"config-{index}",
                repo_id="repo",
                issue_number=1,
                phase="implementation",
                role="implementer",
                agent="codex",
                occurred_at=f"{run_date}T01:05:00+00:00",
                detail_json=json.dumps(
                    {
                        "reported_model": reported_model,
                        "requested_model": requested_model,
                        "token_usage": {"input_tokens": 10},
                        "model_config_snapshot": {
                            "model_id": configured_model,
                            "source": "tool:config",
                            "is_alias": is_alias,
                        },
                    }
                ),
            )
        )
    with TestClient(app) as api_client:
        config_response = api_client.get(
            "/api/v1/agent-runner/console/stats/prd-lifecycle",
            params={"repo_id": "repo"},
        )
    config_stats = config_response.json()["token_usage_by_model"]
    assert config_stats["configuration_fallback_counts"] == {"gpt-6-luna": 1}
    assert config_stats["unresolved_model_alias_counts"] == {"sonnet": 1}
    for expected_model in ("gpt-6-luna", "reported-v2", "requested-v1", "unknown"):
        assert config_stats["by_model"][expected_model]["total_tokens"] == 10

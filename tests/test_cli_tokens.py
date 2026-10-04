"""Token 用量 CLI 命令（``iar tokens``）的测试。

覆盖：有数据表格与 ``--json`` 同构输出、参数越界收敛、空态、账本不可用
错误语义；数值断言以 :func:`build_prd_lifecycle_stats` 聚合为对照。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from backend.api import cli_typer_tokens as tokens_module
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    build_attempt_event_detail,
    build_prd_lifecycle_stats,
    record_lifecycle_event,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

runner = CliRunner()

# 用局部 Typer 应用承载被测命令，避免拉起完整 iar 装配。
_invoker_app = typer.Typer()
_invoker_app.command()(tokens_module.tokens)


def _invoke(args: list[str]):
    return runner.invoke(_invoker_app, args)


_REPO_ID = "keda-main"
_PRD_PATH = "tasks/pending/P1-FEAT-20261004-174104-agent-token-usage-cli.md"

_FIXED_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 340,
    "cache_read_input_tokens": 800,
    "cache_creation_input_tokens": 120,
}


def _store(tmp_path: Path) -> SqliteConsoleStore:
    return SqliteConsoleStore(tmp_path / "console.db")


def _seed_attempt(
    store: SqliteConsoleStore,
    *,
    issue_number: int = 7,
    prd_path: str = _PRD_PATH,
    agent: str = "claude",
    usage_tokens: dict | None = None,
) -> None:
    """经真实写入路径落一条带 usage 的 attempt 事件（rv 链路的账本侧）。"""
    from backend.core.shared.models.agent_runner import AttemptResult, FailureType, TokenUsage

    result = AttemptResult(
        attempt_number=1,
        failure_type=FailureType.SUCCESS,
        recovered=False,
        detail="seed",
        agent=agent,
        started_at="2026-10-04T10:00:00+00:00",
        finished_at="2026-10-04T10:01:00+00:00",
        duration_seconds=60.0,
        token_usage=TokenUsage(**(usage_tokens or _FIXED_USAGE)),
    )
    record_lifecycle_event(
        store=store,
        repo_id=_REPO_ID,
        prd_path=prd_path,
        issue_number=issue_number,
        trigger="cli_run",
        event_type=LifecycleEventType.ATTEMPT,
        actor="runner",
        occurred_at=result.started_at,
        event_key=f"attempt:{result.agent}:{result.attempt_number}",
        detail=build_attempt_event_detail(result),
    )


def _fixed_token_usage():
    from backend.core.shared.models.agent_runner import TokenUsage

    return TokenUsage(
        input_tokens=1200,
        output_tokens=340,
        cache_read_input_tokens=800,
        cache_creation_input_tokens=120,
    )


@pytest.fixture()
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """提供种子账本并劫持 create_console_store 指向它。"""
    store = _store(tmp_path)
    monkeypatch.setattr(tokens_module, "create_console_store", lambda: store)
    return store


def test_tokens_table_matches_ledger_aggregation(
    cli_env: SqliteConsoleStore,
) -> None:
    """有数据时表格数值与账本聚合逐字段一致（FR-1）。"""
    _seed_attempt(cli_env)
    result = _invoke(["--repo-id", _REPO_ID, "--days", "30"])
    assert result.exit_code == 0
    assert "按流程" in result.output and "按 agent" in result.output
    assert "实现" in result.output and "claude" in result.output
    assert "2.5k" in result.output  # 总量 2460
    assert "38%" in result.output  # 命中率 800/2100

    expected = build_prd_lifecycle_stats(
        store=cli_env,
        repo_id=_REPO_ID,
        days=30,
    ).token_usage
    assert expected.by_flow["implement"].total_tokens == 2460


def test_tokens_json_matches_endpoint_shape(cli_env: SqliteConsoleStore) -> None:
    """--json 输出与 stats 端点 token_usage 同构（FR-4）。"""
    _seed_attempt(cli_env)
    result = _invoke(["--repo-id", _REPO_ID, "--days", "30", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["repo_id"] == _REPO_ID
    assert payload["days"] == 30
    implement = payload["token_usage"]["by_flow"]["implement"]
    assert implement["input_tokens"] == 1200
    assert implement["total_tokens"] == 2460
    assert implement["usage_count"] == 1
    assert set(payload["token_usage"]["by_agent"]) == {"claude"}


@pytest.mark.parametrize("days", [0, 9999])
def test_tokens_days_are_clamped(cli_env: SqliteConsoleStore, days: int) -> None:
    """天数越界收敛到 1–365，不抛参数错误（FR-3）。"""
    _seed_attempt(cli_env)
    result = _invoke(["--days", str(days), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert 1 <= payload["days"] <= 365


def test_tokens_empty_ledger_renders_empty_state(tmp_path: Path) -> None:
    """空账本输出明确空态文案，退出码 0（FR-5）。"""
    store = _store(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tokens_module, "create_console_store", lambda: store)
        result = _invoke([])
    assert result.exit_code == 0
    assert "暂无 token 用量数据" in result.output


def test_tokens_unavailable_ledger_exits_with_single_line_error(tmp_path: Path) -> None:
    """账本不可用：非零码 + 单行错误文案，无 traceback（FR-5）。"""

    class BrokenStore:
        def list_lifecycle_runs(self, *, repo_id, since):
            raise RuntimeError("corrupt ledger")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tokens_module, "create_console_store", lambda: BrokenStore())
        result = _invoke([])
    assert result.exit_code == 1
    assert "账本不可用" in result.output
    assert "Traceback" not in result.output


def test_tokens_broken_store_construction_degrades_gracefully(tmp_path: Path) -> None:
    """坏库路径（构造即抛）：单行错误退出码 1，traceback 不逃逸（verifier MEDIUM 负控）。"""

    class BrokenFactoryStore:
        def __init__(self):
            raise RuntimeError("cannot open database file")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tokens_module, "create_console_store", lambda: BrokenFactoryStore())
        result = _invoke([])
    assert result.exit_code == 1
    assert "账本不可用" in result.output
    assert "cannot open database file" in result.output
    assert "Traceback" not in result.output


_PRD_PATH_B = "tasks/archive/P1-FEAT-20260930-212702-agent-token-usage-stats.md"
_ISSUE_9_USAGE = {
    "input_tokens": 500,
    "output_tokens": 100,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
}


def _seed_two_prds(store: SqliteConsoleStore) -> None:
    """落两个 PRD（Issue #7 claude / Issue #9 codex）各一条带 usage 的 attempt。"""
    _seed_attempt(store)
    _seed_attempt(
        store,
        issue_number=9,
        prd_path=_PRD_PATH_B,
        agent="codex",
        usage_tokens=_ISSUE_9_USAGE,
    )


def test_tokens_prd_table_lists_each_issue(cli_env: SqliteConsoleStore) -> None:
    """默认输出含「按 PRD」表：每个 Issue 一行，总量/命中率与明细一致（FR-6）。"""
    _seed_two_prds(cli_env)
    result = _invoke(["--repo-id", _REPO_ID, "--days", "30"])
    assert result.exit_code == 0
    assert "按 PRD（Issue）" in result.output
    assert "#7" in result.output and "#9" in result.output
    # Issue #9 显式上报缓存读 0：命中率 0% 是真实数据，不是「—」降级。
    assert "2.5k" in result.output and "600" in result.output
    assert "0%" in result.output


def test_tokens_issue_drilldown_scopes_all_tables(cli_env: SqliteConsoleStore) -> None:
    """--issue 下钻：三张表全部收窄到该 Issue，其他 Issue 不出现（FR-7）。"""
    _seed_two_prds(cli_env)
    result = _invoke(["--repo-id", _REPO_ID, "--days", "30", "--issue", "7"])
    assert result.exit_code == 0
    assert "Issue #7" in result.output
    assert "#9" not in result.output
    assert "codex" not in result.output
    assert "claude" in result.output and "2.5k" in result.output


def test_tokens_json_includes_by_prd(cli_env: SqliteConsoleStore) -> None:
    """--json 输出 by_prd 维度；--issue 时三张表口径同步收窄（FR-6/FR-7）。"""
    _seed_two_prds(cli_env)
    result = _invoke(["--repo-id", _REPO_ID, "--days", "30", "--json"])
    payload = json.loads(result.output)
    assert len(payload["by_prd"]) == 2
    entry_by_issue = {entry["issue_number"]: entry for entry in payload["by_prd"]}
    assert entry_by_issue[7]["totals"]["total_tokens"] == 2460
    assert entry_by_issue[9]["totals"]["total_tokens"] == 600
    assert entry_by_issue[7]["prd_path"].endswith("agent-token-usage-cli.md")
    assert entry_by_issue[7]["run_count"] == 1

    scoped = _invoke(["--repo-id", _REPO_ID, "--days", "30", "--issue", "9", "--json"])
    scoped_payload = json.loads(scoped.output)
    assert scoped_payload["issue_number"] == 9
    assert [entry["issue_number"] for entry in scoped_payload["by_prd"]] == [9]
    assert set(scoped_payload["token_usage"]["by_agent"]) == {"codex"}


def test_tokens_issue_without_usage_renders_empty_state(cli_env: SqliteConsoleStore) -> None:
    """--issue 指向无数据 Issue：空态文案退出码 0（FR-7 边界）。"""
    _seed_attempt(cli_env)
    result = _invoke(["--repo-id", _REPO_ID, "--days", "30", "--issue", "999"])
    assert result.exit_code == 0
    assert "暂无 token 用量数据" in result.output

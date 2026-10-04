"""token 用量采集 → attempt 落账 全链路 RV 测试（rv-1 / rv-6）。

被测边界（真实）：假 agent 子进程 stdout → ``StreamUsageCollector`` →
``CommandResult.token_usage`` → 执行循环 ``AttemptResult`` →
``build_attempt_event_detail`` → ``record_lifecycle_event`` 落 SQLite 账本 →
fresh 新开的 store 读回与 Stats 聚合。

被替代的只有 agent CLI 本身：输出可控的假脚本按 claude stream-json 信封
写 ``result`` 事件。执行循环里与断言无关的重量级门禁按
``test_lifecycle_agent_routing`` 的既有模式替身为 no-op。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    RunnerConfig,
)
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_RUN,
    AgentProfileSpec,
    AgentSpec,
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
)
from backend.core.use_cases import run_agent_execution_loop as execution_loop_module
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    build_attempt_event_detail,
    record_lifecycle_event,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from backend.infrastructure.process_runner import SubprocessRunner

_REPO_ID = "keda-main"
_PRD_PATH = "tasks/pending/P1-FEAT-20260930-212702-agent-token-usage-stats.md"


def _store(tmp_path: Path) -> SqliteConsoleStore:
    """构造账本 store（写入侧；读侧 fresh 断言用新实例模拟独立会话）。"""
    return SqliteConsoleStore(tmp_path / "console.db")


_FIXED_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 340,
    "cache_read_input_tokens": 800,
    "cache_creation_input_tokens": 120,
}


def _fake_agent_script(worktree: Path, *, name: str, usage: object) -> str:
    """生成一个按 claude stream-json 信封输出 result 事件的假 agent 脚本。"""
    events: list[dict[str, object]] = [
        {"type": "stream_event", "event": {"type": "message_stop"}},
        {"type": "result", "result": "done"},
    ]
    if usage is not None:
        events[-1]["usage"] = usage
    lines = "".join(f"sys.stdout.write(json.dumps({event!r}) + '\\n')\n" for event in events)
    script = worktree / f"fake_agent_{name}.py"
    script.write_text("import json, sys\n" + lines, encoding="utf-8")
    return str(script)


def _config_with_fake_agent(script_path: str) -> AppConfig:
    """把内置 claude agent 的 run profile 指向假脚本（真 stream-json 协议）。"""
    from backend.core.shared.models.agent_spec import BUILTIN_AGENT_SPECS

    claude_spec = BUILTIN_AGENT_SPECS["claude"]
    fake_spec = AgentSpec(
        bin="uv",
        label=claude_spec.label,
        label_color=claude_spec.label_color,
        label_description=claude_spec.label_description,
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=("run", "python", script_path),
                prompt_delivery="argv_tail",
                output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
            )
        },
    )
    return AppConfig(
        runner=RunnerConfig(max_recovery_attempts=0),
        agents={**BUILTIN_AGENT_SPECS, "claude": fake_spec},
    )


def _patch_loop_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """把与断言无关的门禁替身为 no-op（沿用 test_lifecycle_agent_routing 模式）。"""
    monkeypatch.setattr(
        execution_loop_module, "run_verification", lambda worktree, config, runner: []
    )
    monkeypatch.setattr(execution_loop_module, "has_changes", lambda worktree, runner: False)
    monkeypatch.setattr(execution_loop_module, "ensure_prd_delivery_ready", lambda *a, **k: None)
    monkeypatch.setattr(
        execution_loop_module, "ensure_validation_evidence_ready", lambda *a, **k: None
    )
    monkeypatch.setattr(
        execution_loop_module, "ensure_no_misplaced_evidence_helpers", lambda *a, **k: None
    )
    monkeypatch.setattr(
        execution_loop_module, "ensure_validation_commands_pass", lambda *a, **k: None
    )
    monkeypatch.setattr(execution_loop_module, "warn_legacy_evidence_helpers", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.core.use_cases.run_verifier_agent.run_verifier_gate", lambda *a, **k: None
    )
    # 无本地 commit（has_changes=False）→ 走"无 commit"失败分支前先断言成功路径：
    # 让 before/after SHA 不一致被当成成功（fake agent 不产生 git 变更）。
    monkeypatch.setattr(execution_loop_module, "commit_requested_changes", lambda *a, **k: [])


def _run_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store: SqliteConsoleStore,
    *,
    name: str,
    usage: object,
) -> None:
    """驱动一次真实执行循环，把 attempt 经真实链路落进账本。"""
    _patch_loop_gates(monkeypatch)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    # worktree 必须是真 git 仓库：Phase 5 会真实执行 `git rev-parse HEAD`。
    import subprocess as subprocess_module

    subprocess_module.run(["git", "init", "-q"], cwd=worktree, check=True, capture_output=True)
    (worktree / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess_module.run(["git", "add", "seed.txt"], cwd=worktree, check=True, capture_output=True)
    subprocess_module.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    script_path = _fake_agent_script(worktree, name=name, usage=usage)
    config = _config_with_fake_agent(script_path)
    issue = IssueSummary(
        number=7,
        title="t",
        url="https://example/7",
        body=f"PRD: {_PRD_PATH}",
        labels=(),
    )

    def on_attempt_recorded(result, attempt_results) -> None:
        # 与编排层 _on_attempt_recorded 相同的 detail 构造（唯一事实源）＋真实落账。
        record_lifecycle_event(
            store=store,
            repo_id=_REPO_ID,
            prd_path=_PRD_PATH,
            issue_number=issue.number,
            trigger="cli_run",
            event_type=LifecycleEventType.ATTEMPT,
            actor="runner",
            occurred_at=result.started_at or None,
            event_key=f"attempt:{result.agent}:{result.attempt_number}:{result.started_at}",
            detail=build_attempt_event_detail(result),
        )

    execution_loop_module.run_agent_until_committed(
        execution_loop_module.AgentExecutionRequest(
            selected_agent="claude",
            issue=issue,
            worktree_path=worktree,
            config=config,
            process_runner=SubprocessRunner(),
            before_sha="0" * 40,
            expected_branch="issue-7",
            on_attempt_recorded=on_attempt_recorded,
        )
    )


class TestTokenUsageFlow:
    """rv-1 / rv-6：采集入库全链路与缺失降级。"""

    def test_fake_agent_usage_reaches_ledger_and_stats(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rv-1：固定 usage 数值逐字段抵达账本 attempt detail 与 Stats 聚合。"""
        store = _store(tmp_path)
        _run_loop(tmp_path, monkeypatch, store, name="usage", usage=_FIXED_USAGE)

        # fresh 读：新开 store 连接（模拟读端点的独立会话）。
        fresh_store = SqliteConsoleStore(tmp_path / "console.db")
        run = fresh_store.get_latest_lifecycle_run(repo_id=_REPO_ID, prd_path=_PRD_PATH)
        assert run is not None
        events = fresh_store.list_lifecycle_events(run_id=run.run_id)
        attempt_events = [e for e in events if e.event_type == "attempt"]
        assert len(attempt_events) == 1
        detail = json.loads(attempt_events[0].detail_json)
        assert detail["token_usage"] == _FIXED_USAGE

        # Stats 聚合：attempt usage 归入 implement 流程。
        from backend.core.use_cases.agent_runner_lifecycle import build_prd_lifecycle_stats

        stats = build_prd_lifecycle_stats(
            store=fresh_store,
            repo_id=_REPO_ID,
            days=30,
            now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
        )
        implement = stats.token_usage.by_flow["implement"]
        assert implement.usage_count == 1
        assert implement.input_tokens == 1200
        assert implement.output_tokens == 340
        assert implement.cache_read_input_tokens == 800
        assert implement.cache_creation_input_tokens == 120
        assert implement.total_tokens == 2460
        assert stats.token_usage.by_agent["claude"].usage_count == 1

    def test_missing_usage_degrades_without_token_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rv-6：无 usage 的 agent 正常完成 attempt，事件 detail 无 token_usage。"""
        store = _store(tmp_path)
        _run_loop(tmp_path, monkeypatch, store, name="missing", usage=None)

        fresh_store = SqliteConsoleStore(tmp_path / "console.db")
        run = fresh_store.get_latest_lifecycle_run(repo_id=_REPO_ID, prd_path=_PRD_PATH)
        assert run is not None
        events = fresh_store.list_lifecycle_events(run_id=run.run_id)
        attempt_events = [e for e in events if e.event_type == "attempt"]
        assert len(attempt_events) == 1
        detail = json.loads(attempt_events[0].detail_json)
        assert "token_usage" not in detail

    def test_malformed_usage_degrades_without_breaking_attempt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rv-6 负控：畸形 usage（字符串）不炸 attempt，也不产生 token_usage。"""
        store = _store(tmp_path)
        _run_loop(tmp_path, monkeypatch, store, name="malformed", usage="oops")

        fresh_store = SqliteConsoleStore(tmp_path / "console.db")
        run = fresh_store.get_latest_lifecycle_run(repo_id=_REPO_ID, prd_path=_PRD_PATH)
        assert run is not None
        attempt_events = [
            e
            for e in fresh_store.list_lifecycle_events(run_id=run.run_id)
            if e.event_type == "attempt"
        ]
        assert len(attempt_events) == 1
        detail = json.loads(attempt_events[0].detail_json)
        assert "token_usage" not in detail

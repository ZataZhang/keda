"""token 用量聚合单元测试。

覆盖 :func:`aggregate_token_usage` 的口径：attempt 族事件归为实现流程、
观测事件按 detail.flow 分组、缺失 usage 排除、畸形 detail 容错、
总量等于四项之和、命中率输入侧口径在 Stats 层的形状（by_flow/by_agent）。
"""

import json

from backend.core.shared.interfaces.runner_console import PrdLifecycleEventRecord
from backend.core.use_cases.agent_runner_token_stats import aggregate_token_usage

_FIXED_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 340,
    "cache_read_input_tokens": 800,
    "cache_creation_input_tokens": 120,
}


def _event(event_type: str, detail: dict, event_key: str = "k") -> PrdLifecycleEventRecord:
    """构造一条内存事件记录（不落库）。"""
    return PrdLifecycleEventRecord(
        run_id="r1",
        event_key=event_key,
        event_type=event_type,
        phase="executing",
        actor="runner",
        occurred_at="2026-09-30T10:00:00+00:00",
        detail_json=json.dumps(detail, ensure_ascii=False),
    )


class TestAggregateTokenUsage:
    """aggregate_token_usage 的分组与容错。"""

    def test_attempt_events_grouped_as_implement(self) -> None:
        events = [
            _event(
                "attempt",
                {"agent": "claude", "token_usage": _FIXED_USAGE},
                event_key="a1",
            ),
            _event(
                "recovered",
                {"agent": "claude", "token_usage": _FIXED_USAGE},
                event_key="a2",
            ),
        ]
        stats = aggregate_token_usage(events)
        implement = stats.by_flow["implement"]
        assert implement.input_tokens == 2400
        assert implement.total_tokens == 4920
        assert implement.usage_count == 2
        assert stats.by_agent["claude"].usage_count == 2

    def test_observation_events_keep_detail_flow(self) -> None:
        events = [
            _event(
                "agent_token_usage",
                {"flow": "verify", "agent": "codex", "token_usage": _FIXED_USAGE},
                event_key="v1",
            ),
            _event(
                "agent_token_usage",
                {"flow": "supervise", "agent": "claude", "token_usage": _FIXED_USAGE},
                event_key="s1",
            ),
        ]
        stats = aggregate_token_usage(events)
        assert set(stats.by_flow) == {"verify", "supervise"}
        assert set(stats.by_agent) == {"codex", "claude"}
        assert stats.by_flow["verify"].usage_count == 1

    def test_missing_usage_excluded(self) -> None:
        events = [
            _event("attempt", {"agent": "claude"}, event_key="a1"),
            _event(
                "agent_token_usage",
                {"flow": "verify", "agent": "codex"},
                event_key="v1",
            ),
        ]
        stats = aggregate_token_usage(events)
        assert stats.by_flow == {}
        assert stats.by_agent == {}

    def test_malformed_usage_excluded_not_crash(self) -> None:
        # 畸形 usage（字符串 / 负数 / bool）按缺失处理，不计入也不抛异常。
        events = [
            _event(
                "attempt",
                {"agent": "claude", "token_usage": "oops"},
                event_key="a1",
            ),
            _event(
                "agent_token_usage",
                {
                    "flow": "verify",
                    "agent": "codex",
                    "token_usage": {"input_tokens": -1, "output_tokens": True},
                },
                event_key="v1",
            ),
        ]
        stats = aggregate_token_usage(events)
        assert stats.by_flow == {}

    def test_corrupted_detail_json_tolerated(self) -> None:
        bad_event = PrdLifecycleEventRecord(
            run_id="r1",
            event_key="a1",
            event_type="attempt",
            phase="executing",
            actor="runner",
            occurred_at="2026-09-30T10:00:00+00:00",
            detail_json="{not-json",
        )
        stats = aggregate_token_usage([bad_event])
        assert stats.by_flow == {}

    def test_other_event_types_ignored(self) -> None:
        events = [_event("started", {"agent": "claude"}, event_key="s1")]
        stats = aggregate_token_usage(events)
        assert stats.by_flow == {}

    def test_total_equals_sum_of_four_fields(self) -> None:
        events = [
            _event(
                "attempt",
                {"agent": "claude", "token_usage": _FIXED_USAGE},
                event_key="a1",
            ),
        ]
        stats = aggregate_token_usage(events)
        row = stats.by_flow["implement"]
        assert row.total_tokens == (
            row.input_tokens
            + row.output_tokens
            + row.cache_read_input_tokens
            + row.cache_creation_input_tokens
        )

    def test_empty_events_yield_empty_tables(self) -> None:
        stats = aggregate_token_usage([])
        assert stats.by_flow == {}
        assert stats.by_agent == {}


class TestBuildTokenUsageByPrd:
    """按 PRD（Issue）维度分组；口径与 aggregate_token_usage 单源。"""

    @staticmethod
    def _seed(store, *, issue_number: int, prd_path: str, usage: dict) -> None:
        """经真实写入路径落一条带 usage 的 attempt 事件。"""
        from backend.core.shared.models.agent_runner import (
            AttemptResult,
            FailureType,
            TokenUsage,
        )
        from backend.core.use_cases.agent_runner_lifecycle import (
            LifecycleEventType,
            build_attempt_event_detail,
            record_lifecycle_event,
        )

        result = AttemptResult(
            attempt_number=1,
            failure_type=FailureType.SUCCESS,
            recovered=False,
            detail="seed",
            agent="claude",
            started_at="2026-10-04T10:00:00+00:00",
            finished_at="2026-10-04T10:01:00+00:00",
            duration_seconds=60.0,
            token_usage=TokenUsage(**usage),
        )
        record_lifecycle_event(
            store=store,
            repo_id="keda-main",
            prd_path=prd_path,
            issue_number=issue_number,
            trigger="cli_run",
            event_type=LifecycleEventType.ATTEMPT,
            actor="runner",
            occurred_at=result.started_at,
            event_key=f"attempt:{issue_number}:1",
            detail=build_attempt_event_detail(result),
        )

    def _store(self, tmp_path):
        from backend.infrastructure.persistence.console_store import SqliteConsoleStore

        return SqliteConsoleStore(tmp_path / "console.db")

    def test_groups_by_prd_and_excludes_missing_usage(self, tmp_path) -> None:
        """两个 PRD 各成一组；缺 usage 的 run 不计入且不报错。"""
        from datetime import datetime, timezone

        from backend.core.use_cases.agent_runner_token_stats import build_token_usage_by_prd

        store = self._store(tmp_path)
        self._seed(store, issue_number=7, prd_path="tasks/a.md", usage=_FIXED_USAGE)
        self._seed(
            store,
            issue_number=9,
            prd_path="tasks/b.md",
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        )
        # 无 usage 的事件：不产生任何分组。
        from backend.core.use_cases.agent_runner_lifecycle import (
            LifecycleEventType,
            record_lifecycle_event,
        )

        record_lifecycle_event(
            store=store,
            repo_id="keda-main",
            prd_path="tasks/c.md",
            issue_number=11,
            trigger="cli_run",
            event_type=LifecycleEventType.ATTEMPT,
            actor="runner",
            occurred_at="2026-10-04T10:00:00+00:00",
            event_key="attempt:11:1",
            detail={"agent": "claude"},
        )

        entries = build_token_usage_by_prd(
            store=store,
            repo_id="keda-main",
            days=30,
            now=datetime(2026, 10, 5, tzinfo=timezone.utc),
        )
        assert [entry.issue_number for entry in entries] == [7, 9]
        assert entries[0].totals.total_tokens == 2460
        assert entries[0].run_count == 1
        assert entries[1].totals.total_tokens == 120

    def test_issue_filter_scopes_entries(self, tmp_path) -> None:
        """issue_number 过滤：只返回该 Issue 的分组。"""
        from datetime import datetime, timezone

        from backend.core.use_cases.agent_runner_token_stats import build_token_usage_by_prd

        store = self._store(tmp_path)
        self._seed(store, issue_number=7, prd_path="tasks/a.md", usage=_FIXED_USAGE)
        self._seed(store, issue_number=9, prd_path="tasks/b.md", usage=_FIXED_USAGE)

        entries = build_token_usage_by_prd(
            store=store,
            repo_id="keda-main",
            days=30,
            now=datetime(2026, 10, 5, tzinfo=timezone.utc),
            issue_number=9,
        )
        assert [entry.issue_number for entry in entries] == [9]
        assert entries[0].totals.total_tokens == 2460

    def test_stats_for_issue_scopes_dimensions(self, tmp_path) -> None:
        """build_token_usage_stats_for_issue：by_flow/by_agent 只含该 Issue 的事件。"""
        from datetime import datetime, timezone

        from backend.core.use_cases.agent_runner_token_stats import (
            build_token_usage_stats_for_issue,
        )

        store = self._store(tmp_path)
        self._seed(store, issue_number=7, prd_path="tasks/a.md", usage=_FIXED_USAGE)
        self._seed(store, issue_number=9, prd_path="tasks/b.md", usage=_FIXED_USAGE)

        scoped = build_token_usage_stats_for_issue(
            store=store,
            repo_id="keda-main",
            days=30,
            issue_number=9,
            now=datetime(2026, 10, 5, tzinfo=timezone.utc),
        )
        assert set(scoped.by_flow) == {"implement"}
        assert set(scoped.by_agent) == {"claude"}
        assert scoped.by_flow["implement"].usage_count == 1

    def test_same_prd_multiple_runs_merge(self, tmp_path) -> None:
        """同一 PRD 的多次 run 合并为一行：run_count 累加、四字段求和（LOW-3 直接断言）。"""
        from datetime import datetime, timezone

        from backend.core.shared.interfaces.runner_console import (
            PrdLifecycleEventRecord,
            PrdLifecycleRunRecord,
        )
        from backend.core.use_cases.agent_runner_token_stats import build_token_usage_by_prd
        from backend.infrastructure.persistence.console_store import SqliteConsoleStore

        store = SqliteConsoleStore(tmp_path / "console.db")
        usages = [
            {
                "input_tokens": 100,
                "output_tokens": 10,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 0,
            },
            {
                "input_tokens": 200,
                "output_tokens": 20,
                "cache_read_input_tokens": 60,
                "cache_creation_input_tokens": 5,
            },
        ]
        for index, usage in enumerate(usages, start=1):
            run_id = f"keda-main#7r{index}"
            store.upsert_lifecycle_run(
                PrdLifecycleRunRecord(
                    run_id=run_id,
                    repo_id="keda-main",
                    prd_path="tasks/a.md",
                    issue_number=7,
                    trigger="cli_run",
                    started_at="2026-10-04T10:00:00+00:00",
                    finished_at="2026-10-04T10:05:00+00:00",
                    outcome="completed",
                    history_complete=True,
                )
            )
            store.append_lifecycle_event(
                PrdLifecycleEventRecord(
                    run_id=run_id,
                    event_key=f"attempt:{index}",
                    event_type="attempt",
                    phase="executing",
                    actor="runner",
                    occurred_at="2026-10-04T10:01:00+00:00",
                    detail_json=json.dumps({"agent": "claude", "token_usage": usage}),
                )
            )

        entries = build_token_usage_by_prd(
            store=store,
            repo_id="keda-main",
            days=30,
            now=datetime(2026, 10, 5, tzinfo=timezone.utc),
        )
        assert len(entries) == 1
        assert entries[0].run_count == 2
        assert entries[0].issue_number == 7
        assert entries[0].totals.input_tokens == 300
        assert entries[0].totals.cache_read_input_tokens == 100
        assert entries[0].totals.cache_creation_input_tokens == 5
        assert entries[0].totals.total_tokens == 435
        assert entries[0].totals.usage_count == 2

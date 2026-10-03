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

"""CLI 人读输出的时区显示（Issue #239）。

机器边界（JSON 输出、账本、API）保持 UTC 原文；人读表格/文案必须按执行
机器本地时区换算。这些测试锁定 ``format_local_display_time`` 与其在
lifecycle / loop 渲染路径上的接线，并覆盖跨日与夏令时回拨。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from backend.api import cli_typer_lifecycle
from backend.api.cli_loop import _format_task_row
from backend.api.cli_utils import format_local_display_time
from backend.core.shared.models.backlog import (
    PrdLifecycleDetail,
    PrdLifecycleDurations,
    PrdLifecycleEventView,
)
from backend.core.shared.models.loop import LoopSchedule, LoopScheduleKind, LoopTask


@contextmanager
def _machine_timezone(monkeypatch: pytest.MonkeyPatch, tz_name: str):
    """临时把进程时区切到 ``tz_name``，退出时恢复。"""
    monkeypatch.setenv("TZ", tz_name)
    time.tzset()
    try:
        yield
    finally:
        time.tzset()


def test_utc_offset_timestamp_converts_to_machine_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """带 ``+00:00`` 的账本时间按执行机器本地时区渲染，并保留本地偏移。"""
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        assert format_local_display_time("2026-06-23T00:00:00+00:00") == "2026-06-23T08:00:00+08:00"


def test_z_suffix_crosses_day_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Z`` 后缀同样按 UTC 解析；跨日时本地日期随之进位。"""
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        assert format_local_display_time("2026-06-23T23:30:00Z") == "2026-06-24T07:30:00+08:00"


def test_utc_instant_converts_under_other_zones(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一瞬间在 UTC 与东二区显示不同墙钟，但都指向同一真实瞬间。"""
    with _machine_timezone(monkeypatch, "UTC"):
        assert format_local_display_time("2026-07-01T12:00:00+00:00") == "2026-07-01T12:00:00+00:00"
    with _machine_timezone(monkeypatch, "Europe/Berlin"):
        assert format_local_display_time("2026-07-01T12:00:00+00:00") == "2026-07-01T14:00:00+02:00"


def test_dst_fallback_keeps_distinct_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    """夏令时回拨导致同一本地小时出现两次是合法行为；偏移必须区分两个瞬间。"""
    with _machine_timezone(monkeypatch, "America/New_York"):
        # 2026-11-01 回拨：01:30 出现两次（EDT 与 EST）。
        assert format_local_display_time("2026-11-01T05:30:00+00:00") == "2026-11-01T01:30:00-04:00"
        assert format_local_display_time("2026-11-01T06:30:00+00:00") == "2026-11-01T01:30:00-05:00"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("2026-06-23 08:00", "2026-06-23 08:00"),
        ("2026-06-23", "2026-06-23"),
        ("—", "—"),
        ("not-a-time", "not-a-time"),
        ("", ""),
    ],
)
def test_naive_or_unparsable_values_pass_through(raw_value: str, expected: str) -> None:
    """无时区的墙钟标签与无法解析的值原样返回，不做时区猜测。"""
    assert format_local_display_time(raw_value) == expected


def test_none_and_empty_return_empty_string() -> None:
    """空值返回空串，由调用方决定占位符。"""
    assert format_local_display_time(None) == ""
    assert format_local_display_time("") == ""


def test_loop_task_row_renders_next_fire_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    """``kc loop list`` 行内的 next_fire 按本地时区显示。"""
    task = LoopTask(
        id="demo",
        recipe_path=Path("tasks/loops/demo.md"),
        repo_id="keda",
        schedule=LoopSchedule(kind=LoopScheduleKind.CRON, expression="0 8 * * *"),
        next_fire_at="2026-06-23T00:00:00+00:00",
    )
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        row = _format_task_row(task)
    assert "next_fire=2026-06-23T08:00:00+08:00" in row


def _make_lifecycle_detail(occurred_at: str) -> PrdLifecycleDetail:
    durations = PrdLifecycleDurations(
        end_to_end_seconds=60.0,
        active_seconds=60.0,
        waiting_seconds=0.0,
        blocked_seconds=0.0,
    )
    event = PrdLifecycleEventView(
        event_type="execution_started",
        phase="executing",
        status="started",
        actor="runner",
        occurred_at=occurred_at,
        detail={},
    )
    return PrdLifecycleDetail(
        repo_id="keda",
        prd_path="tasks/pending/demo.md",
        run_id="run-1",
        issue_number=239,
        trigger="manual",
        current_phase="executing",
        in_progress=True,
        outcome=None,
        history_complete=True,
        started_at=occurred_at,
        finished_at=None,
        durations=durations,
        events=[event],
        has_data=True,
    )


def test_lifecycle_table_renders_event_time_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    """``kc lifecycle`` 摘要表的「更新时间」列按本地时区渲染。"""
    detail = _make_lifecycle_detail("2026-06-23T00:00:00+00:00")
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        table = cli_typer_lifecycle._render_lifecycle_table([detail])
        table_capture = StringIO()
        detail_capture = StringIO()
        monkeypatch.setattr(
            cli_typer_lifecycle,
            "_rich_console",
            Console(file=detail_capture, width=200),
        )
        Console(file=table_capture, width=200).print(table)
        cli_typer_lifecycle._render_lifecycle_detail([detail])
    assert "2026-06-23T08:00:00+08:00" in table_capture.getvalue()
    assert "2026-06-23T08:00:00+08:00" in detail_capture.getvalue()
    # 原始 UTC 文本不得再出现在人读输出里。
    assert "+00:00" not in detail_capture.getvalue()


def test_lifecycle_detail_falls_back_for_naive_started_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无时区的 started_at（墙钟标签）原样展示，不做偏移猜测。"""
    detail = _make_lifecycle_detail("2026-06-23 08:00")
    with _machine_timezone(monkeypatch, "UTC"):
        table = cli_typer_lifecycle._render_lifecycle_table([detail])
        table_capture = StringIO()
        Console(file=table_capture, width=200).print(table)
    assert "2026-06-23 08:00" in table_capture.getvalue()

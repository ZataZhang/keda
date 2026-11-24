"""CLI 人读输出的时区显示（Issue #239）。

机器边界（JSON 输出、账本、API）保持 UTC 原文；人读表格/文案必须按执行
机器本地时区换算。这些测试锁定 ``format_local_display_time`` 与其在
lifecycle / loop / daemon 渲染路径上的接线，覆盖跨日与夏令时回拨，并钉住
「JSON 分支不得经过该函数」这条契约。
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.table import Table
from typer.testing import CliRunner

from backend.api import cli_loop, cli_typer_lifecycle
from backend.api.cli_loop import _format_task_row
from backend.api.cli_registry import _run_daemon_status_command
from backend.api.cli_utils import format_local_display_time
from backend.core.shared.interfaces.runner_console import RunnerProcessKind
from backend.core.shared.models.backlog import (
    PrdLifecycleDetail,
    PrdLifecycleDurations,
    PrdLifecycleEventView,
)
from backend.core.shared.models.loop import LoopSchedule, LoopScheduleKind, LoopTask

#: 账本里的 UTC 瞬间，人读侧在 Asia/Shanghai 下应渲染成 ``_LOCAL_SAME_INSTANT``。
_UTC_INSTANT = "2026-06-23T00:00:00+00:00"
_LOCAL_SAME_INSTANT = "2026-06-23T08:00:00+08:00"


@contextmanager
def _machine_timezone(monkeypatch: pytest.MonkeyPatch, tz_name: str):
    """临时把进程时区切到 ``tz_name``，退出时恢复。"""
    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", tz_name)
    time.tzset()
    try:
        yield
    finally:
        # 必须在 monkeypatch 回滚之前显式恢复并 tzset，否则 C 库会带着
        # 本用例的时区泄漏到后续测试。
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()


def _render_rich_table(table: object, *, width: int = 200) -> str:
    """把 Rich Table 渲染成纯文本供断言（不落终端）。"""
    render_console = Console(force_terminal=False, width=width)
    with render_console.capture() as capture:
        render_console.print(table)
    return capture.get()


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


def _make_loop_task(next_fire_at: str) -> LoopTask:
    """构造一条 loop 任务，只填渲染路径读到的字段。"""
    return LoopTask(
        id="demo",
        recipe_path=Path("tasks/loops/demo.md"),
        repo_id="keda",
        schedule=LoopSchedule(kind=LoopScheduleKind.CRON, expression="0 8 * * *"),
        next_fire_at=next_fire_at,
    )


def test_loop_task_row_renders_next_fire_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    """``kc loop list`` 行内的 next_fire 按本地时区显示。"""
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        row = _format_task_row(_make_loop_task(_UTC_INSTANT))
    assert f"next_fire={_LOCAL_SAME_INSTANT}" in row


def _make_lifecycle_detail(
    started_at: str,
    event_occurred_at: str | None = None,
) -> PrdLifecycleDetail:
    """构造一条带单个事件的生命周期详情；``event_occurred_at`` 缺省跟随 started_at。"""
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
        occurred_at=started_at if event_occurred_at is None else event_occurred_at,
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
        started_at=started_at,
        finished_at=None,
        durations=durations,
        events=[event],
        has_data=True,
    )


def test_lifecycle_table_renders_event_time_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    """``kc lifecycle`` 摘要表的「更新时间」列按本地时区渲染。"""
    detail = _make_lifecycle_detail(_UTC_INSTANT)
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        table = cli_typer_lifecycle._render_lifecycle_table([detail])
        detail_capture = StringIO()
        monkeypatch.setattr(
            cli_typer_lifecycle,
            "_rich_console",
            Console(file=detail_capture, width=200),
        )
        table_text = _render_rich_table(table)
        cli_typer_lifecycle._render_lifecycle_detail([detail])
    assert _LOCAL_SAME_INSTANT in table_text
    assert _LOCAL_SAME_INSTANT in detail_capture.getvalue()
    # 原始 UTC 文本不得再出现在人读输出里。
    assert "+00:00" not in detail_capture.getvalue()
    assert "+00:00" not in table_text


def test_lifecycle_detail_falls_back_for_naive_started_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无时区的 started_at（墙钟标签）原样展示，不做偏移猜测。"""
    detail = _make_lifecycle_detail("2026-06-23 08:00")
    with _machine_timezone(monkeypatch, "UTC"):
        table = cli_typer_lifecycle._render_lifecycle_table([detail])
        table_text = _render_rich_table(table)
    assert "2026-06-23 08:00" in table_text


def test_lifecycle_table_shows_placeholder_for_empty_event_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """账本事件时间为空时，摘要表与时间线都落到占位符而不是空白。"""
    detail = _make_lifecycle_detail(_UTC_INSTANT, event_occurred_at="")
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        table_text = _render_rich_table(cli_typer_lifecycle._render_lifecycle_table([detail]))
        event_table_capture = StringIO()
        monkeypatch.setattr(
            cli_typer_lifecycle,
            "_rich_console",
            Console(file=event_table_capture, width=200),
        )
        cli_typer_lifecycle._render_lifecycle_detail([detail])
    assert "—" in table_text
    assert "—" in event_table_capture.getvalue()


# ── 机器契约：JSON 分支不得经过本地换算 ───────────────────────────────────────


def test_loop_list_json_keeps_utc_source_under_local_timezone(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``kc loop list --json`` 原样输出账本 UTC，不因本机时区改写机器契约。"""
    task = _make_loop_task(_UTC_INSTANT)
    monkeypatch.setattr(cli_loop, "list_loops", lambda **_kwargs: [task])
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        exit_code = cli_loop.run_loop_list_command(
            state_store_factory=lambda: MagicMock(),
            fmt="json",
        )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload[0]["next_fire_at"] == _UTC_INSTANT


def test_lifecycle_json_keeps_utc_source_under_local_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kc lifecycle --json`` 的 started_at / occurred_at 仍是账本 UTC 原文。"""
    detail = _make_lifecycle_detail(_UTC_INSTANT)
    monkeypatch.setattr(cli_typer_lifecycle, "create_console_store", lambda: MagicMock())
    monkeypatch.setattr(
        cli_typer_lifecycle,
        "list_issue_lifecycle_details",
        lambda **_kwargs: [detail],
    )
    # 间谍替换：真实改绑会动到全局 logging handler，与本断言目标无关。
    monkeypatch.setattr(cli_typer_lifecycle, "route_logs_to_stderr", lambda: None)
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        result = CliRunner().invoke(cli_typer_lifecycle.app, ["lifecycle", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    lifecycle_payload = payload["lifecycles"][0]
    assert lifecycle_payload["started_at"] == _UTC_INSTANT
    assert lifecycle_payload["events"][0]["occurred_at"] == _UTC_INSTANT
    assert _LOCAL_SAME_INSTANT not in result.stdout


def _make_daemon_record(started_at: str) -> MagicMock:
    """构造一条 running daemon 进程记录，只填 daemon status 渲染到的字段。"""
    record = MagicMock()
    record.process_id = "abc123"
    record.repo_id = "daemon-repo"
    record.kind = RunnerProcessKind.DAEMON.value
    record.status = "running"
    record.pid = 1234
    record.log_path = "/tmp/daemon-abc123.log"
    record.command = ("kc", "daemon", "run", "--repo-id", "daemon-repo")
    record.started_at = started_at
    return record


def _run_daemon_status(fmt: str) -> str:
    """以给定形态跑一次 ``kc daemon status``，返回落到 stdout 的文本。

    ``cli_registry`` 与 ``cli_output`` 共用同一个 Rich console，所以两种形态的
    输出都经同一个 print 捕获：人类模式捕获到 Table 对象（再渲染成文本），
    机器模式捕获到 JSON 字符串本身。
    """
    parsed = MagicMock()
    parsed.output = fmt
    parsed.as_json = False
    parsed.all_repositories = False
    supervisor = MagicMock()
    supervisor.list_processes.return_value = [_make_daemon_record(_UTC_INSTANT)]
    supervisor.list_unmanaged_processes.return_value = []
    editor = MagicMock()
    editor.list_repositories.return_value = []
    printed_payloads: list[object] = []

    def _capture_print(value: object, *_args: object, **_kwargs: object) -> None:
        printed_payloads.append(value)

    with (
        patch(
            "backend.api.cli_registry.resolve_repository_targets",
            return_value=[MagicMock(repo_id="daemon-repo")],
        ),
        patch("backend.api.cli_registry.create_process_supervisor", return_value=supervisor),
        patch("backend.api.cli_registry.create_registry_editor", return_value=editor),
        patch("backend.api.cli_registry.console.print", side_effect=_capture_print),
    ):
        exit_code = _run_daemon_status_command(
            parsed=parsed,
            process_runner=MagicMock(),
            runner_settings=MagicMock(),
            repo_id="daemon-repo",
            repo_override=None,
        )
    assert exit_code == 0
    assert len(printed_payloads) == 1, "两种形态都只应输出一次结果"
    printed_payload = printed_payloads[0]
    return (
        _render_rich_table(printed_payload)
        if isinstance(printed_payload, Table)
        else str(printed_payload)
    )


def test_daemon_status_table_renders_started_at_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kc daemon status`` 表格的 started_at 按执行机器本地时区渲染。"""
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        table_text = _run_daemon_status("table")
    assert _LOCAL_SAME_INSTANT in table_text
    assert "+00:00" not in table_text


def test_daemon_status_json_keeps_utc_source_under_local_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kc daemon status --json`` 的 started_at 保持 UTC 原文，不经本地换算。"""
    with _machine_timezone(monkeypatch, "Asia/Shanghai"):
        payload = json.loads(_run_daemon_status("json"))
    assert payload[0]["started_at"] == _UTC_INSTANT

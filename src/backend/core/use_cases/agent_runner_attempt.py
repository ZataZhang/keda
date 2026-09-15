"""Attempt 阶段计时与结果构造。

本模块承接原先落在 ``run_agent_once.py`` 的 attempt 记账职责：

- :class:`AttemptPhaseTimer` —— 按阶段累计一个 attempt 内部耗时，供"卡了很久"
  定位到具体环节（agent 思考 / ``just test`` 挂住 / 某条 RV 命令超时）。
- :func:`wait_before_recovery_attempt` —— 按配置的 retry delay 等待。
- :func:`_make_attempt_result` —— 现在时刻补齐 wall-clock 时间，构造 ``AttemptResult``。
- :func:`_append_attempt_and_notify` —— 追加结果并通知增量持久化回调；回调失败
  不得打断运行（side-channel）。

调用方为 :mod:`backend.core.use_cases.run_agent_execution_loop`；``run_agent_once``
保留同名 re-export 以维持既有 import 路径。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from backend.core.shared.models.agent_runner import (
    AttemptResult,
    FailureType,
    PhaseDuration,
)

_logger = logging.getLogger(__name__)


def wait_before_recovery_attempt(
    issue_number: int,
    *,
    recovery_attempt: int,
    max_recovery_attempts: int,
    delay_seconds: int,
) -> None:
    """Wait before a recovery attempt when retry delay is configured."""
    if delay_seconds <= 0:
        return
    _logger.info(
        "Waiting %d seconds before recovery attempt %d/%d for Issue #%d.",
        delay_seconds,
        recovery_attempt,
        max_recovery_attempts,
        issue_number,
    )
    time.sleep(delay_seconds)


class AttemptPhaseTimer:
    """按阶段累计一个 attempt 内部的耗时。

    只记一个 attempt 总时长时，"卡了很久"定位不到环节——几千秒可能是 agent 自己
    在想，也可能是 ``just test`` 挂住、或某条 RV 命令一路超时。同一阶段被多次进入
    时累加（例如 recovery 循环内多次 verification）。
    """

    def __init__(self) -> None:
        """初始化空的阶段累计表。"""
        self._accumulated_seconds: dict[str, float] = {}

    @contextmanager
    def measure(self, phase_name: str) -> Iterator[None]:
        """计时一个阶段；即使阶段内抛异常也照常累计。

        Args:
            phase_name: 阶段名，进入 ``PhaseDuration.name``。

        Yields:
            None。
        """
        phase_started_mono: float = time.monotonic()
        try:
            yield
        finally:
            elapsed_seconds: float = time.monotonic() - phase_started_mono
            self._accumulated_seconds[phase_name] = (
                self._accumulated_seconds.get(phase_name, 0.0) + elapsed_seconds
            )

    def snapshot(self) -> tuple[PhaseDuration, ...]:
        """按耗时降序返回当前累计结果。"""
        ordered_phases = sorted(
            self._accumulated_seconds.items(),
            key=lambda phase_entry: phase_entry[1],
            reverse=True,
        )
        return tuple(
            PhaseDuration(name=phase_name, seconds=round(seconds, 3))
            for phase_name, seconds in ordered_phases
        )


def _make_attempt_result(
    *,
    attempt_number: int,
    failure_type: FailureType,
    recovered: bool,
    detail: str,
    agent: str,
    started_mono: float,
    started_iso: str,
    phase_durations: tuple[PhaseDuration, ...] = (),
) -> AttemptResult:
    """Build an ``AttemptResult`` with wall-clock timing filled in now."""
    finished_mono = time.monotonic()
    finished_iso = datetime.now(timezone.utc).isoformat()
    return AttemptResult(
        attempt_number=attempt_number,
        failure_type=failure_type,
        recovered=recovered,
        detail=detail,
        agent=agent,
        started_at=started_iso,
        finished_at=finished_iso,
        duration_seconds=round(finished_mono - started_mono, 3),
        phase_durations=phase_durations,
    )


def _append_attempt_and_notify(
    attempt_results: list[AttemptResult],
    result: AttemptResult,
    on_attempt_recorded: Callable[[AttemptResult, list[AttemptResult]], None] | None,
) -> None:
    """Append a result and notify the incremental persistence callback."""
    attempt_results.append(result)
    if on_attempt_recorded is not None:
        try:
            on_attempt_recorded(result, list(attempt_results))
        except Exception:  # noqa: BLE001 - persistence side-channel must not break runs
            _logger.warning(
                "Attempt persistence callback failed for attempt %d; continuing.",
                result.attempt_number,
                exc_info=True,
            )

"""``kc run --progress`` 的机器可读进度输出。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from backend.api.cli_output import emit_progress_event
from backend.core.shared.models.agent_runner_progress import RunProgressEvent

_HEARTBEAT_INTERVAL_SECONDS = 30
_logger = logging.getLogger(__name__)


class RunProgressStream:
    """将前台 run 的阶段、心跳与终态事件写到标准错误流。"""

    def __init__(self, *, target_issue: int | None) -> None:
        """创建一次前台 run 的通知流。

        Args:
            target_issue: 定向执行的 Issue 编号；队列执行时为 ``None``。
        """
        self._task_id = uuid.uuid4().hex
        self._target_issue = target_issue
        self._started_monotonic = 0.0
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        self._heartbeat_thread: threading.Thread | None = None
        self._enabled = True

    def start(self) -> None:
        """输出启动事件并开始周期性心跳。

        Returns:
            None: 事件直接写入 stderr。
        """
        self._started_monotonic = time.monotonic()
        self._emit(
            {
                "event": "run.started",
                "task_id": self._task_id,
                "target_issue": self._target_issue,
                "status": "running",
                "message": "kc run 已开始",
                "occurred_at": self._utc_timestamp(),
            }
        )
        self._heartbeat_thread = threading.Thread(
            target=self._emit_heartbeats,
            name=f"kc-run-progress-{self._task_id[:8]}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def emit_lifecycle_event(self, progress_event: RunProgressEvent) -> None:
        """输出 runner 记录的阶段变化，不携带生命周期事件的 detail。

        Args:
            progress_event: Issue 生命周期阶段变化的实时投影。

        Returns:
            None: 事件直接写入 stderr。
        """
        self._emit(
            {
                "event": "run.lifecycle",
                "task_id": self._task_id,
                "repo_id": progress_event.repo_id,
                "issue_number": progress_event.issue_number,
                "lifecycle_event": progress_event.lifecycle_event,
                "phase": progress_event.phase,
                "status": progress_event.status,
                "occurred_at": progress_event.occurred_at,
            }
        )

    def finish(self, *, status: str, exit_code: int | None) -> None:
        """停止心跳并输出本次 ``kc run`` 的最终结果。

        Args:
            status: 本次命令的终态（成功、失败或中断）。
            exit_code: runner 的最终退出码；未知时为 ``None``。

        Returns:
            None: 事件直接写入 stderr。
        """
        self._stop_event.set()
        if self._heartbeat_thread is not None:
            try:
                self._heartbeat_thread.join(timeout=1)
            except RuntimeError:
                pass
        self._emit(
            {
                "event": "run.finished",
                "task_id": self._task_id,
                "target_issue": self._target_issue,
                "status": status,
                "exit_code": exit_code,
                "elapsed_seconds": self._elapsed_seconds(),
                "message": self._finish_message(status),
                "occurred_at": self._utc_timestamp(),
            }
        )

    def _emit_heartbeats(self) -> None:
        while not self._stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
            self._emit(
                {
                    "event": "run.progress",
                    "task_id": self._task_id,
                    "target_issue": self._target_issue,
                    "status": "running",
                    "elapsed_seconds": self._elapsed_seconds(),
                    "message": "任务仍在执行",
                    "occurred_at": self._utc_timestamp(),
                }
            )

    def _emit(self, event_payload: dict[str, object]) -> None:
        if not self._enabled:
            return
        try:
            with self._write_lock:
                emit_progress_event({"schema_version": 1, **event_payload})
        except Exception as exc:  # noqa: BLE001 - 进度是旁路输出，不应中断实际任务。
            # 进度是旁路输出；调用方关闭 stderr 时不能中断实际任务。
            self._enabled = False
            _logger.warning(
                "Failed to emit kc run progress event; disabling progress stream: %s", exc
            )

    def _elapsed_seconds(self) -> int:
        return max(0, int(time.monotonic() - self._started_monotonic))

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _finish_message(status: str) -> str:
        if status == "succeeded":
            return "kc run 已结束"
        if status == "interrupted":
            return "kc run 已中断"
        return "kc run 失败"

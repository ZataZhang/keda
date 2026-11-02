"""生命周期事件的进程内实时通知旁路。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from backend.core.shared.interfaces.runner_console import PrdLifecycleEventRecord

_logger = logging.getLogger(__name__)

_LIFECYCLE_PROGRESS_SINK: ContextVar[Callable[[PrdLifecycleEventRecord], None] | None] = ContextVar(
    "kc_lifecycle_progress_sink", default=None
)


def has_lifecycle_progress_sink() -> bool:
    """判断当前上下文是否订阅了生命周期阶段事件。

    Returns:
        bool: 当前上下文存在实时订阅者时为真。
    """
    return _LIFECYCLE_PROGRESS_SINK.get() is not None


@contextmanager
def lifecycle_progress_scope(
    progress_sink: Callable[[PrdLifecycleEventRecord], None] | None,
) -> Iterator[None]:
    """将生命周期阶段事件投递到当前 Issue 的可选实时通知端口。

    Args:
        progress_sink: 接收阶段事件的回调；为 ``None`` 时不启用实时通知。

    Yields:
        None: 退出作用域后恢复此前的通知回调。
    """
    sink_token = _LIFECYCLE_PROGRESS_SINK.set(progress_sink)
    try:
        yield
    finally:
        _LIFECYCLE_PROGRESS_SINK.reset(sink_token)


def emit_lifecycle_progress(event_record: PrdLifecycleEventRecord) -> None:
    """把一条生命周期事件送到当前实时订阅者，失败时只记日志。

    Args:
        event_record: runner 刚生成的生命周期事件。

    Returns:
        None: 失败仅记日志，不向执行流程抛出异常。
    """
    progress_sink = _LIFECYCLE_PROGRESS_SINK.get()
    if progress_sink is None:
        return
    try:
        progress_sink(event_record)
    except Exception as exc:  # noqa: BLE001 - 进度输出不能阻断 runner。
        _logger.warning(
            "Failed to stream lifecycle progress for %s: %s",
            event_record.event_type,
            exc,
        )

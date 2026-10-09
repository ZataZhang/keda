"""轮询等待共享 helper：后台线程语义的测试断言不要在每个文件里各抄一份。"""

from __future__ import annotations

import time
from collections.abc import Callable


def wait_until(predicate: Callable[[], bool], *, timeout_seconds: float = 5.0) -> bool:
    """轮询 ``predicate`` 直到成立或超时；返回最终判定结果。

    Args:
        predicate: 需要变为真的条件。
        timeout_seconds: 最长等待秒数。

    Returns:
        bool: 条件在超时前成立时为 ``True``。
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()

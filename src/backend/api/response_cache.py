"""路由层共享的 TTL 响应缓存。

管理终端与只读监控端点共享同一套"未命中即构建、命中即在 TTL 内直接
返回"的缓存写法，避免平行维护第二套缓存实现。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any


class TTLResponseCache:
    """按 key 缓存构建结果的简易 TTL 缓存（线程安全）。

    Args:
        ttl_seconds: 缓存条目有效期（秒）；过期后下一次读取会重新构建。
    """

    def __init__(self, *, ttl_seconds: float) -> None:
        """初始化空缓存。"""
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, Future[Any]] = {}

    def get_or_build(self, cache_key: str, builder: Callable[[], Any]) -> Any:
        """返回缓存条目；未命中或已过期时调用 ``builder`` 重建。

        Args:
            cache_key: 缓存键；同一缓存实例内不同过滤条件使用不同键，
                避免单仓库刷新与全量请求互相覆盖。
            builder: 未命中时调用的构建函数。

        Returns:
            缓存或新建的构建结果。
        """
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry is not None and (time.monotonic() - entry["timestamp"]) < self._ttl_seconds:
                return entry["payload"]
            pending = self._pending.get(cache_key)
            is_builder = pending is None
            if pending is None:
                pending = Future()
                self._pending[cache_key] = pending
        if not is_builder:
            return pending.result()
        try:
            # 同键请求共享构建结果，不同键仍可并发；失败不写入缓存。
            payload = builder()
            with self._lock:
                if self._pending.get(cache_key) is pending:
                    self._entries[cache_key] = {
                        "payload": payload,
                        "timestamp": time.monotonic(),
                    }
            pending.set_result(payload)
            return payload
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                if self._pending.get(cache_key) is pending:
                    self._pending.pop(cache_key)

    def clear(self) -> None:
        """清空全部缓存条目（测试或手动失效用）。"""
        with self._lock:
            self._entries.clear()
            self._pending.clear()

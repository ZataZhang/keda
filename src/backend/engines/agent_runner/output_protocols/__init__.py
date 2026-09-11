"""输出协议注册表：经 entry point group 发现协议实现。

内置三个协议（``plain`` / ``claude-stream-json`` / ``pi-json-lines``）
与第三方插件走**同一条** entry point 注册路径（group
``iar.agent_output_protocols``），避免出现零消费者的平行抽象。

加载失败（import 抛错、加载对象不满足协议接口）直接抛
:class:`OutputProtocolLoadError`，绝不静默回落到 ``plain``——
回退会把结构化事件流当纯文本中继，错误只在运行期才现形。
"""

from __future__ import annotations

import threading
from importlib.metadata import entry_points

from backend.core.shared.interfaces.agent_output_protocol import (
    IAgentOutputProtocol,
    IAgentOutputProtocolRegistry,
)

ENTRY_POINT_GROUP = "iar.agent_output_protocols"
"""entry point group 名：第三方包与内置协议都在此组注册输出协议。"""


class OutputProtocolLoadError(RuntimeError):
    """输出协议解析或加载失败。"""


class EntryPointProtocolRegistry(IAgentOutputProtocolRegistry):
    """基于 importlib entry points 的协议注册表。

    ``list_ids`` 只枚举元数据、不触发加载；``resolve`` 首次解析成功后
    缓存实例。加载失败抛 :class:`OutputProtocolLoadError`，不降级。
    """

    def __init__(self) -> None:
        """初始化空缓存。"""
        self._cache: dict[str, IAgentOutputProtocol] = {}
        self._lock = threading.Lock()

    def list_ids(self) -> tuple[str, ...]:
        """按 entry point 声明顺序列出全部协议 id（不加载）。"""
        return tuple(entry_point.name for entry_point in entry_points(group=ENTRY_POINT_GROUP))

    def resolve(self, protocol_id: str) -> IAgentOutputProtocol:
        """按 id 解析协议实现。

        Args:
            protocol_id: 协议 id（如 ``"claude-stream-json"``）。

        Returns:
            协议实现实例（带 ``relay`` 方法）。

        Raises:
            OutputProtocolLoadError: id 未注册、entry point 加载失败或
                加载对象不满足协议接口。
        """
        with self._lock:
            cached_protocol = self._cache.get(protocol_id)
        if cached_protocol is not None:
            return cached_protocol
        for entry_point in entry_points(group=ENTRY_POINT_GROUP):
            if entry_point.name != protocol_id:
                continue
            try:
                loaded_object = entry_point.load()
            except Exception as exc:  # noqa: BLE001 - 任何加载失败都不降级
                raise OutputProtocolLoadError(
                    f"Output protocol plugin '{protocol_id}' failed to load "
                    f"from entry point '{entry_point.value}': {exc}"
                ) from exc
            protocol_instance = (
                loaded_object() if isinstance(loaded_object, type) else loaded_object
            )
            if not callable(getattr(protocol_instance, "relay", None)):
                raise OutputProtocolLoadError(
                    f"Output protocol plugin '{protocol_id}' "
                    f"({entry_point.value}) does not provide a relay() method."
                )
            with self._lock:
                self._cache[protocol_id] = protocol_instance
            return protocol_instance  # type: ignore[return-value]
        raise OutputProtocolLoadError(
            f"Unknown output protocol '{protocol_id}'. "
            f"Registered protocols: {', '.join(self.list_ids()) or '(none)'}."
        )


_registry_lock = threading.Lock()
_registry_instance: EntryPointProtocolRegistry | None = None


def get_output_protocol_registry() -> EntryPointProtocolRegistry:
    """返回进程级共享的协议注册表单例。"""
    global _registry_instance
    with _registry_lock:
        if _registry_instance is None:
            _registry_instance = EntryPointProtocolRegistry()
        return _registry_instance


__all__ = [
    "ENTRY_POINT_GROUP",
    "EntryPointProtocolRegistry",
    "OutputProtocolLoadError",
    "get_output_protocol_registry",
]

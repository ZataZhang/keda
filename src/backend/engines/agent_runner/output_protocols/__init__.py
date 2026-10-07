"""输出协议注册表：经 entry point group 发现协议实现。

内置三个协议（``plain`` / ``claude-stream-json`` / ``pi-json-lines``）
与第三方插件走**同一条** entry point 注册路径（group
``kedacode.agent_output_protocols``），避免出现零消费者的平行抽象。

已按旧分组注册的第三方包继续被发现：两个分组都扫描，同一协议 id 同时
出现在两个分组时以新分组为准。

加载失败（import 抛错、加载对象不满足协议接口）直接抛
:class:`OutputProtocolLoadError`，绝不静默回落到 ``plain``——
回退会把结构化事件流当纯文本中继，错误只在运行期才现形。
"""

from __future__ import annotations

import threading
from importlib.metadata import EntryPoint, entry_points

from backend.core.shared.interfaces.agent_output_protocol import (
    IAgentOutputProtocol,
    IAgentOutputProtocolRegistry,
)
from backend.core.shared.models import product_identity

ENTRY_POINT_GROUP = product_identity.OUTPUT_PROTOCOL_ENTRY_POINT_GROUP
"""entry point group 名：第三方包与内置协议都在此组注册输出协议。"""

LEGACY_ENTRY_POINT_GROUP = product_identity.LEGACY_OUTPUT_PROTOCOL_ENTRY_POINT_GROUP
"""旧 entry point group 名，永久保留：第三方插件可能仍注册在这里。"""

#: 扫描顺序即优先级：同一协议 id 先命中的分组胜出，因此新分组优先。
_DISCOVERY_GROUP_ORDER: tuple[str, ...] = (ENTRY_POINT_GROUP, LEGACY_ENTRY_POINT_GROUP)


class OutputProtocolLoadError(RuntimeError):
    """输出协议解析或加载失败。"""


def _discover_entry_points() -> dict[str, EntryPoint]:
    """按分组优先级收集协议 entry point，同一 id 保留先命中的那个。

    Returns:
        协议 id 到 entry point 的映射；插入顺序即 ``list_ids`` 的输出顺序。
    """
    discovered_entry_points: dict[str, EntryPoint] = {}
    for group_name in _DISCOVERY_GROUP_ORDER:
        for discovered_entry_point in entry_points(group=group_name):
            discovered_entry_points.setdefault(discovered_entry_point.name, discovered_entry_point)
    return discovered_entry_points


class EntryPointProtocolRegistry(IAgentOutputProtocolRegistry):
    """基于 importlib entry points 的协议注册表。

    新旧两个分组都会扫描，同一 id 以新分组为准。
    ``list_ids`` 只枚举元数据、不触发加载；``resolve`` 首次解析成功后
    缓存实例。加载失败抛 :class:`OutputProtocolLoadError`，不降级。
    """

    def __init__(self) -> None:
        """初始化空缓存。"""
        self._cache: dict[str, IAgentOutputProtocol] = {}
        self._lock = threading.Lock()

    def list_ids(self) -> tuple[str, ...]:
        """按 entry point 声明顺序列出全部协议 id（不加载）。"""
        return tuple(_discover_entry_points())

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
        entry_point = _discover_entry_points().get(protocol_id)
        if entry_point is None:
            raise OutputProtocolLoadError(
                f"Unknown output protocol '{protocol_id}'. "
                f"Registered protocols: {', '.join(self.list_ids()) or '(none)'}."
            )
        try:
            loaded_object = entry_point.load()
        except Exception as exc:  # noqa: BLE001 - 任何加载失败都不降级
            raise OutputProtocolLoadError(
                f"Output protocol plugin '{protocol_id}' failed to load "
                f"from entry point '{entry_point.value}': {exc}"
            ) from exc
        protocol_instance = loaded_object() if isinstance(loaded_object, type) else loaded_object
        if not callable(getattr(protocol_instance, "relay", None)):
            raise OutputProtocolLoadError(
                f"Output protocol plugin '{protocol_id}' "
                f"({entry_point.value}) does not provide a relay() method."
            )
        with self._lock:
            self._cache[protocol_id] = protocol_instance
        return protocol_instance  # type: ignore[return-value]


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
    "LEGACY_ENTRY_POINT_GROUP",
    "EntryPointProtocolRegistry",
    "OutputProtocolLoadError",
    "get_output_protocol_registry",
]

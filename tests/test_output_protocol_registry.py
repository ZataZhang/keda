"""输出协议注册表测试（entry point 机制 + 内置三协议）。

覆盖：entry point 发现（内置协议与第三方插件走同一条路）、
加载失败**不静默降级**（直接抛 :class:`OutputProtocolLoadError`）、
加载对象不满足协议接口时报错、进程级单例与缓存行为。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.engines.agent_runner.output_protocols import (
    ENTRY_POINT_GROUP,
    LEGACY_ENTRY_POINT_GROUP,
    EntryPointProtocolRegistry,
    OutputProtocolLoadError,
    get_output_protocol_registry,
)

BUILTIN_PROTOCOL_IDS = ("plain", "claude-stream-json", "pi-json-lines")


class _StubEntryPoint:
    """注册表契约只依赖 ``name`` / ``value`` 与 ``load()``，用桩对象隔离 importlib 细节。"""

    def __init__(self, name: str, loader: Any, group: str = ENTRY_POINT_GROUP) -> None:
        self.name = name
        self.value = f"{group}:{name}"
        self._loader = loader

    def load(self) -> Any:
        result = self._loader()
        if isinstance(result, Exception):
            raise result
        return result


def _fake_entry_points(
    entries: list[tuple[str, Any]],
    group: str = ENTRY_POINT_GROUP,
) -> Any:
    """构造可 monkeypatch 的 entry points 假数据源（只命中指定分组）。"""
    entries_by_group = {group: list(entries)}

    def list_entry_points(group: str) -> list[_StubEntryPoint]:
        return [
            _StubEntryPoint(name=name, loader=loader, group=group)
            for name, loader in entries_by_group.get(group, [])
        ]

    return list_entry_points


def _fake_grouped_entry_points(
    entries_by_group: dict[str, list[tuple[str, Any]]],
) -> Any:
    """按分组返回假 entry points，用于验证新旧两个分组的双读发现。"""

    def list_entry_points(group: str) -> list[_StubEntryPoint]:
        return [
            _StubEntryPoint(name=name, loader=loader, group=group)
            for name, loader in entries_by_group.get(group, [])
        ]

    return list_entry_points


# ---------------------------------------------------------------------------
# 内置协议发现与解析
# ---------------------------------------------------------------------------


def test_builtin_protocols_registered_via_entry_points() -> None:
    """内置三协议经由 entry point group 注册（与第三方插件同一条路）。"""
    registry = EntryPointProtocolRegistry()
    assert set(BUILTIN_PROTOCOL_IDS) <= set(registry.list_ids())


def test_builtin_protocols_resolvable_and_relayable() -> None:
    """三个内置协议都可解析，且实例带 relay 方法。"""
    registry = EntryPointProtocolRegistry()
    for protocol_id in BUILTIN_PROTOCOL_IDS:
        protocol = registry.resolve(protocol_id)
        assert callable(getattr(protocol, "relay", None)), protocol_id


def test_unknown_protocol_lists_registered_ids() -> None:
    """未注册的协议 id 报错时列出全部已注册 id，便于排障。"""
    registry = EntryPointProtocolRegistry()
    with pytest.raises(OutputProtocolLoadError) as exc_info:
        registry.resolve("no-such-protocol")
    message = str(exc_info.value)
    for protocol_id in BUILTIN_PROTOCOL_IDS:
        assert protocol_id in message


def test_resolve_caches_protocol_instance() -> None:
    """同一协议 id 重复 resolve 返回缓存的同一实例。"""
    registry = EntryPointProtocolRegistry()
    first = registry.resolve("plain")
    second = registry.resolve("plain")
    assert first is second


def _relay_returning(marker: str) -> Any:
    """构造 relay 返回固定标记的协议类，用于区分 entry point 来自哪个分组。"""

    class _MarkerProtocol:
        def relay(self, *args: object, **kwargs: object) -> str:
            return marker

    return _MarkerProtocol


# ---------------------------------------------------------------------------
# 新旧两个分组的双读发现
# ---------------------------------------------------------------------------


def test_legacy_group_protocols_are_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    """只注册在旧分组里的第三方插件仍然被发现（改名不让既有插件消失）。"""
    monkeypatch.setattr(
        "backend.engines.agent_runner.output_protocols.entry_points",
        _fake_grouped_entry_points(
            {
                ENTRY_POINT_GROUP: [],
                LEGACY_ENTRY_POINT_GROUP: [("legacy-only", _relay_returning("legacy"))],
            }
        ),
    )
    registry = EntryPointProtocolRegistry()
    assert "legacy-only" in registry.list_ids()
    assert registry.resolve("legacy-only").relay() == "legacy"


def test_new_group_wins_on_duplicate_protocol_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 id 在两个分组都注册时以新分组为准，且该 id 只枚举一次。"""
    monkeypatch.setattr(
        "backend.engines.agent_runner.output_protocols.entry_points",
        _fake_grouped_entry_points(
            {
                ENTRY_POINT_GROUP: [("shared", _relay_returning("new"))],
                LEGACY_ENTRY_POINT_GROUP: [
                    ("shared", _relay_returning("legacy")),
                    ("legacy-only", _relay_returning("legacy")),
                ],
            }
        ),
    )
    registry = EntryPointProtocolRegistry()
    listed_ids = list(registry.list_ids())
    assert listed_ids.count("shared") == 1
    assert registry.resolve("shared").relay() == "new"
    assert "legacy-only" in listed_ids


# ---------------------------------------------------------------------------
# 加载失败不降级
# ---------------------------------------------------------------------------


def test_import_failure_does_not_fall_back_to_plain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """插件 import 失败直接报错，绝不静默回落 plain。"""
    monkeypatch.setattr(
        "backend.engines.agent_runner.output_protocols.entry_points",
        _fake_entry_points([("broken", ImportError("module not found"))]),
    )
    registry = EntryPointProtocolRegistry()
    with pytest.raises(OutputProtocolLoadError, match="broken"):
        registry.resolve("broken")


def test_object_without_relay_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """加载对象没有 relay 方法时报错（不满足协议接口）。"""
    monkeypatch.setattr(
        "backend.engines.agent_runner.output_protocols.entry_points",
        _fake_entry_points([("bad-shape", object)]),
    )
    registry = EntryPointProtocolRegistry()
    with pytest.raises(OutputProtocolLoadError, match="relay"):
        registry.resolve("bad-shape")


def test_class_entry_point_is_instantiated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """entry point 指向类时自动实例化；指向实例时直接使用。"""

    class FakeProtocol:
        def relay(self, *args: object, **kwargs: object) -> object:
            return None

    fake_instance = FakeProtocol()
    monkeypatch.setattr(
        "backend.engines.agent_runner.output_protocols.entry_points",
        _fake_entry_points(
            [
                ("as-class", FakeProtocol),
                ("as-instance", lambda: fake_instance),
            ]
        ),
    )
    registry = EntryPointProtocolRegistry()
    assert callable(registry.resolve("as-class").relay)
    assert callable(registry.resolve("as-instance").relay)


def test_failed_load_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """加载失败不写缓存：修复插件后再次 resolve 走重新加载。"""
    load_calls: list[int] = []

    def flaky_loader() -> object:
        load_calls.append(1)
        if len(load_calls) == 1:
            return ImportError("first attempt fails")
        return type("FixedProtocol", (), {"relay": staticmethod(lambda *a, **k: None)})

    monkeypatch.setattr(
        "backend.engines.agent_runner.output_protocols.entry_points",
        _fake_entry_points([("flaky", flaky_loader)]),
    )
    registry = EntryPointProtocolRegistry()
    with pytest.raises(OutputProtocolLoadError):
        registry.resolve("flaky")
    protocol = registry.resolve("flaky")
    assert callable(protocol.relay)
    assert len(load_calls) == 2


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------


def test_get_output_protocol_registry_is_singleton() -> None:
    """进程级共享同一注册表实例（执行层各处拿到的解析结果一致）。"""
    assert get_output_protocol_registry() is get_output_protocol_registry()

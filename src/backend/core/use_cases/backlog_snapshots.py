"""Backlog 列表快照的 core 读写编排：落盘、读取、过期判定与视图变体约定。

本模块是"Backlog 列表读本地快照、后台按节奏重扫"这一需求的全部编排逻辑：

- :func:`persist_backlog_snapshot` 是**唯一**的写库入口，只持久化调用方已经
  构建好的列表响应，绝不为了写库再次调用 GitHub。
- :func:`read_backlog_snapshot` 是列表读路径：单行读取 + stale 判定，损坏或
  形状不符的快照一律视同缺失，绝不把坏数据下发给前端。
- :func:`list_missing_pending_repo_ids` 把"哪些启用仓库还没有默认视图快照"
  这条判定收在一处，供启动预取使用；已禁用/已删除仓库的历史行不会回流。
- :func:`build_backlog_task_key` / :func:`parse_backlog_task_key` 定义协调器
  任务键与 ``(repo_id, include_archived)`` 变体的互转，是去重粒度的唯一来源。

设计约束与 :mod:`backend.core.use_cases.monitor_snapshots` 一致：core 不依赖
FastAPI、不依赖具体 SQLite 实现，存储由调用方以 ``IBacklogSnapshotStore`` 端口
注入；扫描动作留在 api 层，因为它要装配 GitHub client 与仓库上下文。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.core.shared.interfaces.runner_console import (
    BacklogSnapshotEntry,
    IBacklogSnapshotStore,
)

_logger = logging.getLogger(__name__)

#: 快照视为"过期"的阈值（秒）。沿用被删除的内存缓存 TTL 语义，是前端把轮询
#: 缩短到 3 秒的唯一触发条件，因此不能比它更宽松。
BACKLOG_SNAPSHOT_STALE_TTL_SECONDS = 30

#: 协调器任务键里标记"显示已归档"变体的后缀；默认视图直接用 ``repo_id``。
BACKLOG_ARCHIVED_TASK_KEY_SUFFIX = "::archived"


def build_backlog_task_key(repo_id: str, *, include_archived: bool) -> str:
    """把 ``(repo_id, include_archived)`` 变体编码成协调器任务键。

    Args:
        repo_id: 目标仓库 ID。
        include_archived: 是否为"显示已归档"视图变体。

    Returns:
        str: 默认视图返回 ``repo_id``，归档视图返回 ``repo_id::archived``。
    """
    if not repo_id:
        raise ValueError("repo_id must be a non-empty string.")
    if not include_archived:
        return repo_id
    return f"{repo_id}{BACKLOG_ARCHIVED_TASK_KEY_SUFFIX}"


def parse_backlog_task_key(backlog_task_key: str) -> tuple[str, bool]:
    """把协调器任务键还原成 ``(repo_id, include_archived)``。

    Args:
        backlog_task_key: :func:`build_backlog_task_key` 产出的任务键。

    Returns:
        tuple[str, bool]: 仓库 ID 与该键对应的视图变体。

    Raises:
        ValueError: 任务键为空，或剥掉后缀后仓库 ID 为空。
    """
    if not backlog_task_key:
        raise ValueError("backlog task key must be a non-empty string.")
    if not backlog_task_key.endswith(BACKLOG_ARCHIVED_TASK_KEY_SUFFIX):
        return backlog_task_key, False
    repo_id = backlog_task_key.removesuffix(BACKLOG_ARCHIVED_TASK_KEY_SUFFIX)
    if not repo_id:
        raise ValueError(f"Backlog task key '{backlog_task_key}' carries no repo_id.")
    return repo_id, True


def _now_iso(now_reference: datetime | None) -> str:
    """Return a coarse ISO-8601 timestamp, honouring an injected clock."""
    clock_reference = now_reference or datetime.now(timezone.utc)
    return clock_reference.isoformat(timespec="seconds")


def _parse_scanned_at(scanned_at_text: str) -> datetime | None:
    """把快照时间解析成 aware datetime；无法解析时返回 ``None``。"""
    try:
        parsed_scanned_at = datetime.fromisoformat(scanned_at_text)
    except ValueError:
        return None
    if parsed_scanned_at.tzinfo is None:
        return parsed_scanned_at.replace(tzinfo=timezone.utc)
    return parsed_scanned_at


def is_backlog_snapshot_stale(
    scanned_at_text: str | None,
    *,
    now_reference: datetime | None = None,
    ttl_seconds: int = BACKLOG_SNAPSHOT_STALE_TTL_SECONDS,
) -> bool:
    """判断一份快照时间是否已过期。

    时间缺失或无法解析都按"过期"处理：读路径宁可返回空态并触发重建，也不能
    把一份来历不明的数据当成实时值展示。

    Args:
        scanned_at_text: 快照构建时间（ISO-8601），无快照时为 ``None``。
        now_reference: 注入的当前时间，用于测试与固定判定；省略时取 UTC now。
        ttl_seconds: 过期阈值（秒）。

    Returns:
        bool: 需要后台重建时为 ``True``。
    """
    if scanned_at_text is None:
        return True
    parsed_scanned_at = _parse_scanned_at(scanned_at_text)
    if parsed_scanned_at is None:
        return True
    clock_reference = now_reference or datetime.now(timezone.utc)
    return (clock_reference - parsed_scanned_at).total_seconds() > ttl_seconds


@dataclass(frozen=True)
class BacklogSnapshotView:
    """一次列表读取的快照视图。

    Attributes:
        payload: 可直接作为响应体的列表数据；无可用快照时是空态骨架。
        scanned_at: 快照构建时间，无可用快照时为 ``None``。
        stale: 本次读取是否需要后台重扫（缺失、损坏或超过 TTL 均为 ``True``）。
    """

    payload: dict[str, Any]
    scanned_at: str | None
    stale: bool


def _empty_backlog_payload(repo_id: str, *, include_archived: bool) -> dict[str, Any]:
    """构造"没有可用快照"时的空态响应骨架（不伪造 scanned_at）。"""
    return {
        "prds": [],
        "skipped": [],
        "repo_id": repo_id,
        "include_archived": include_archived,
        "scanned_at": None,
    }


def _is_renderable_backlog_payload(
    payload_value: Any,
    repo_id: str,
    include_archived: bool,
) -> bool:
    """判断快照内容是否是可交给前端渲染的列表响应。

    只做最低限度结构校验（``prds`` 是列表、仓库与视图变体对得上）；形状不符
    一律视同缺失——把坏数据下发给前端会让卡片渲染期抛错，比空态难排查得多。
    """
    if not isinstance(payload_value, dict):
        return False
    if not isinstance(payload_value.get("prds"), list):
        return False
    if str(payload_value.get("repo_id") or "") != repo_id:
        return False
    return bool(payload_value.get("include_archived")) is include_archived


def persist_backlog_snapshot(
    store: IBacklogSnapshotStore,
    *,
    repo_id: str,
    include_archived: bool,
    payload: Mapping[str, Any],
) -> BacklogSnapshotEntry:
    """把已构建好的列表响应写入本地快照。

    本函数是快照的唯一写入入口：**不会**为了写库重新扫描 GitHub。写库失败直接
    抛给调用方，旧快照因此原样保留——把写库失败伪装成刷新成功会让页面长期停在
    一份无人知晓的陈旧数据上。

    Args:
        store: 快照存储端口实现。
        repo_id: 目标仓库 ID。
        include_archived: 视图变体。
        payload: ``GET /agent-runner/backlog/prds`` 同构的响应体。

    Returns:
        BacklogSnapshotEntry: 实际落盘的快照条目（含最终 ``scanned_at``）。
    """
    persisted_entry = BacklogSnapshotEntry(
        repo_id=repo_id,
        include_archived=include_archived,
        payload_json=json.dumps(payload, ensure_ascii=False),
        scanned_at=str(payload.get("scanned_at") or _now_iso(None)),
    )
    store.upsert_backlog_snapshot(persisted_entry)
    return persisted_entry


def read_backlog_snapshot(
    store: IBacklogSnapshotStore,
    *,
    repo_id: str,
    include_archived: bool,
    now_reference: datetime | None = None,
    ttl_seconds: int = BACKLOG_SNAPSHOT_STALE_TTL_SECONDS,
) -> BacklogSnapshotView:
    """读取一个视图变体的快照并判定新鲜度，全程不发生网络调用。

    Args:
        store: 快照存储端口实现。
        repo_id: 目标仓库 ID。
        include_archived: 视图变体。
        now_reference: 注入的当前时间，用于 stale 判定。
        ttl_seconds: 过期阈值（秒）。

    Returns:
        BacklogSnapshotView: 有可用快照时是其原样 payload，否则为空态骨架；
            两者都带上如实的 ``stale`` 标记。
    """
    stored_entry = store.get_backlog_snapshot(repo_id=repo_id, include_archived=include_archived)
    if stored_entry is None:
        return BacklogSnapshotView(
            payload=_empty_backlog_payload(repo_id, include_archived=include_archived),
            scanned_at=None,
            stale=True,
        )
    try:
        parsed_payload = json.loads(stored_entry.payload_json)
    except ValueError as exc:
        _logger.warning("Discarding corrupt backlog snapshot for %s: %s", repo_id, exc)
        return BacklogSnapshotView(
            payload=_empty_backlog_payload(repo_id, include_archived=include_archived),
            scanned_at=None,
            stale=True,
        )
    if not _is_renderable_backlog_payload(parsed_payload, repo_id, include_archived):
        _logger.warning("Discarding malformed backlog snapshot for %s", repo_id)
        return BacklogSnapshotView(
            payload=_empty_backlog_payload(repo_id, include_archived=include_archived),
            scanned_at=None,
            stale=True,
        )
    return BacklogSnapshotView(
        payload=parsed_payload,
        scanned_at=stored_entry.scanned_at,
        stale=is_backlog_snapshot_stale(
            stored_entry.scanned_at, now_reference=now_reference, ttl_seconds=ttl_seconds
        ),
    )


def list_missing_pending_repo_ids(
    store: IBacklogSnapshotStore,
    *,
    enabled_repo_ids: Iterable[str],
) -> tuple[str, ...]:
    """返回启用但还没有默认视图快照的仓库 ID（启动预取目标）。

    归档变体按需构建，不参与预取判定；已禁用/已删除仓库的历史行也不会被当成
    "已有快照"。

    Args:
        store: 快照存储端口实现。
        enabled_repo_ids: 当前 registry 中启用的仓库 ID。

    Returns:
        tuple[str, ...]: 缺失默认视图快照的仓库 ID，保持入参顺序。
    """
    pending_snapshot_repo_ids = {
        snapshot_entry.repo_id
        for snapshot_entry in store.list_backlog_snapshots()
        if not snapshot_entry.include_archived
    }
    enabled_order = [str(repo_id) for repo_id in enabled_repo_ids]
    return tuple(repo_id for repo_id in enabled_order if repo_id not in pending_snapshot_repo_ids)


__all__ = [
    "BACKLOG_ARCHIVED_TASK_KEY_SUFFIX",
    "BACKLOG_SNAPSHOT_STALE_TTL_SECONDS",
    "BacklogSnapshotView",
    "build_backlog_task_key",
    "is_backlog_snapshot_stale",
    "list_missing_pending_repo_ids",
    "parse_backlog_task_key",
    "persist_backlog_snapshot",
    "read_backlog_snapshot",
]

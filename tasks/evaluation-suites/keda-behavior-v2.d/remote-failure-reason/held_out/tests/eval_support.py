"""从冻结基线抽取的测试夹具；来源见 suite provenance.json。"""

from __future__ import annotations


import json


from datetime import datetime, timezone


from backend.core.shared.interfaces.runner_console import (
    MonitorSnapshotEntry,
)


from backend.core.use_cases.monitor_snapshots import get_snapshot_overview


from backend.core.use_cases.runtime_dashboard import (
    build_runtime_task_summary,
)


from backend.infrastructure.persistence.console_store import SqliteConsoleStore


REFERENCE_TIME = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)


def _iso(minutes_offset: int) -> str:
    """相对参考时间生成 ISO-8601 UTC 串，避免测试里散落硬编码时间戳。"""
    from datetime import timedelta

    return (REFERENCE_TIME + timedelta(minutes=minutes_offset)).isoformat(timespec="seconds")


def _seed_snapshot(
    store: SqliteConsoleStore,
    repo_id: str,
    issues: list[dict],
    *,
    tracked_issues: list[dict] | None = None,
    scanned_offset: int = -5,
) -> None:
    """把一份 per-repo overview 写入真实快照表。"""
    payload = {
        "repo_id": repo_id,
        "display_name": repo_id.upper(),
        "issues": issues,
        "anomaly_count": 0,
    }
    if tracked_issues is not None:
        payload["tracked_issues"] = tracked_issues
    store.upsert_monitor_snapshot(
        MonitorSnapshotEntry(
            repo_id=repo_id,
            payload_json=json.dumps(payload),
            scanned_at=_iso(scanned_offset),
        )
    )


def _store(tmp_path) -> SqliteConsoleStore:
    """返回一个独立的真实 SQLite store。"""
    return SqliteConsoleStore(tmp_path / "console.db")


def _summary(store: SqliteConsoleStore, repo_ids: list[str], **kwargs):
    """按当前 registry 读取快照并组装摘要，测试里省去重复的胶水参数。"""
    return build_runtime_task_summary(
        snapshot_overview=get_snapshot_overview(store, enabled_repo_ids=repo_ids),
        run_history_store=store,
        prd_lifecycle_store=store,
        enabled_repo_ids=repo_ids,
        now=REFERENCE_TIME,
        **kwargs,
    )

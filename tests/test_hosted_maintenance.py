"""托管 Issue 原始日志清理的范围与保留期测试。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.use_cases.hosted_maintenance import (
    DiskWatermarkAdmissionGate,
    cleanup_expired_issue_logs,
)


def test_cleanup_expired_issue_logs_removes_only_old_regular_logs(tmp_path: Path) -> None:
    """只删除 14 天以前的普通 .log 文件，保留新日志与其它文件。"""
    now_utc = datetime(2026, 10, 10, tzinfo=UTC)
    issue_log_dir = tmp_path / "logs" / "agent-runner" / "issues" / "repo-a"
    issue_log_dir.mkdir(parents=True)
    expired_log_path = issue_log_dir / "issue-1-old.log"
    recent_log_path = issue_log_dir / "issue-2-recent.log"
    unrelated_path = issue_log_dir / "notes.txt"
    expired_log_path.write_text("expired", encoding="utf-8")
    recent_log_path.write_text("recent", encoding="utf-8")
    unrelated_path.write_text("keep", encoding="utf-8")
    os.utime(expired_log_path, (now_utc.timestamp() - 15 * 86400,) * 2)
    os.utime(recent_log_path, (now_utc.timestamp() - 2 * 86400,) * 2)

    cleanup_result = cleanup_expired_issue_logs(tmp_path, "repo-a", now_utc=now_utc)

    assert cleanup_result.scanned_count == 3
    assert cleanup_result.eligible_count == 1
    assert cleanup_result.deleted_count == 1
    assert cleanup_result.skipped_count == 2
    assert cleanup_result.failed_count == 0
    assert not expired_log_path.exists()
    assert recent_log_path.exists()
    assert unrelated_path.exists()


def test_cleanup_expired_issue_logs_rejects_repository_path_escape(tmp_path: Path) -> None:
    """registry id 不能把日志清理范围带出 issues 根目录。"""
    outside_log_path = tmp_path / "logs" / "agent-runner" / "issues-elsewhere" / "old.log"
    outside_log_path.parent.mkdir(parents=True)
    outside_log_path.write_text("keep", encoding="utf-8")
    os.utime(outside_log_path, (datetime.now(UTC).timestamp() - 20 * 86400,) * 2)

    cleanup_result = cleanup_expired_issue_logs(
        tmp_path,
        "../issues-elsewhere",
        now_utc=datetime.now(UTC),
    )

    assert cleanup_result.deleted_count == 0
    assert cleanup_result.skipped_count == 1
    assert outside_log_path.exists()


def test_cleanup_expired_issue_logs_refuses_symlinked_log_root(tmp_path: Path) -> None:
    """符号链接的 logs 根目录不会把删除作用到仓库外。"""
    external_log_dir = tmp_path / "external"
    external_log_dir.mkdir()
    external_old_log = external_log_dir / "issue-1-old.log"
    external_old_log.write_text("keep", encoding="utf-8")
    os.utime(external_old_log, (datetime.now(UTC).timestamp() - 20 * 86400,) * 2)
    (tmp_path / "logs").symlink_to(external_log_dir, target_is_directory=True)

    cleanup_result = cleanup_expired_issue_logs(tmp_path, "repo-a")

    assert cleanup_result.deleted_count == 0
    assert cleanup_result.skipped_count == 1
    assert external_old_log.exists()


def test_disk_watermark_admission_uses_recovery_hysteresis(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """低水位暂停后，只有越过恢复高水位才会重新领取任务。"""
    usage_snapshots = iter(
        [
            SimpleNamespace(total=1000, used=901, free=99),
            SimpleNamespace(total=1000, used=850, free=150),
            SimpleNamespace(total=1000, used=800, free=200),
            SimpleNamespace(total=1000, used=850, free=150),
        ]
    )
    monkeypatch.setattr(
        "backend.core.use_cases.hosted_maintenance.shutil.disk_usage",
        lambda _path: next(usage_snapshots),
    )
    disk_admission_gate = DiskWatermarkAdmissionGate(
        repo_path=tmp_path,
        low_watermark_bytes=100,
        resume_watermark_bytes=200,
    )

    assert disk_admission_gate() is False
    assert disk_admission_gate() is False
    assert disk_admission_gate() is True
    assert disk_admission_gate() is True

"""托管 daemon 的有界日志与临时资源清理规则。"""

from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_logger = logging.getLogger(__name__)
_ISSUE_LOG_RETENTION_DAYS = 14


@dataclass(frozen=True)
class IssueLogCleanupResult:
    """单个仓库 Issue 原始日志清理的分类计数。"""

    scanned_count: int
    eligible_count: int
    deleted_count: int
    skipped_count: int
    failed_count: int


@dataclass(frozen=True)
class _DiskSpaceSnapshot:
    """仓库所在文件系统的容量快照。"""

    total_bytes: int
    used_bytes: int
    free_bytes: int


class DiskWatermarkAdmissionGate:
    """带恢复高水位的线程安全磁盘准入门。

    Args:
        repo_path: 用于探测宿主文件系统容量的仓库路径。
        low_watermark_bytes: 低于该可用空间时暂停新 Issue。
        resume_watermark_bytes: 达到该可用空间时恢复领取。
    """

    def __init__(
        self,
        *,
        repo_path: Path,
        low_watermark_bytes: int,
        resume_watermark_bytes: int,
    ) -> None:
        """保存容量阈值与暂停状态。"""
        self._repo_path = repo_path
        self._low_watermark_bytes = low_watermark_bytes
        self._resume_watermark_bytes = resume_watermark_bytes
        self._paused = False
        self._lock = threading.Lock()

    def __call__(self) -> bool:
        """检查磁盘空间并决定是否可开始另一个 Issue。"""
        with self._lock:
            if (
                self._low_watermark_bytes < 0
                or self._resume_watermark_bytes <= self._low_watermark_bytes
            ):
                _logger.error(
                    "Hosted disk admission is misconfigured for '%s': low=%d resume=%d; "
                    "refusing new Issues.",
                    self._repo_path,
                    self._low_watermark_bytes,
                    self._resume_watermark_bytes,
                )
                return False
            try:
                disk_usage = shutil.disk_usage(self._repo_path)
            except OSError as exc:
                _logger.error(
                    "Hosted disk admission cannot inspect '%s': %s; refusing new Issues.",
                    self._repo_path,
                    exc,
                )
                return False

            snapshot = _DiskSpaceSnapshot(
                total_bytes=disk_usage.total,
                used_bytes=disk_usage.used,
                free_bytes=disk_usage.free,
            )
            if self._paused:
                if snapshot.free_bytes < self._resume_watermark_bytes:
                    return False
                self._paused = False
                _logger.info(
                    "Hosted disk admission resumed for '%s': used=%d free=%d total=%d bytes; "
                    "free space reached recovery watermark=%d bytes.",
                    self._repo_path,
                    snapshot.used_bytes,
                    snapshot.free_bytes,
                    snapshot.total_bytes,
                    self._resume_watermark_bytes,
                )
                return True
            if snapshot.free_bytes < self._low_watermark_bytes:
                self._paused = True
                _logger.warning(
                    "Hosted disk admission paused for '%s': used=%d free=%d total=%d bytes; "
                    "new Issues resume when free space reaches %d bytes.",
                    self._repo_path,
                    snapshot.used_bytes,
                    snapshot.free_bytes,
                    snapshot.total_bytes,
                    self._resume_watermark_bytes,
                )
                return False
            return True


def cleanup_expired_issue_logs(
    repo_path: Path,
    repo_id: str,
    *,
    now_utc: datetime | None = None,
) -> IssueLogCleanupResult:
    """删除托管仓库中超过 14 天的普通 Issue 日志文件。

    只检查 ``logs/agent-runner/issues/<repo_id>`` 的直接子项，不跟随符号链接，
    也不触碰其它仓库、非日志文件、目录或仓库外路径。

    Args:
        repo_path: 已解析的客户仓库根目录。
        repo_id: registry 中的仓库标识。
        now_utc: 可选的 UTC 当前时间，供确定性验证使用。

    Returns:
        各类候选数量；路径不存在时返回全零结果。
    """
    if not repo_id or repo_id in {".", ".."} or "/" in repo_id or "\\" in repo_id:
        _logger.warning("Hosted Issue log cleanup rejected invalid repository id: %s", repo_id)
        return _empty_cleanup_result(skipped_count=1)
    current_time = now_utc or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    cutoff_epoch = (current_time - timedelta(days=_ISSUE_LOG_RETENTION_DAYS)).timestamp()
    repository_root = repo_path.resolve()
    issues_root = repository_root / "logs" / "agent-runner" / "issues"
    repository_log_dir = issues_root / repo_id

    for protected_path in (
        repository_root / "logs",
        repository_root / "logs" / "agent-runner",
        issues_root,
    ):
        if protected_path.is_symlink():
            _logger.warning(
                "Hosted Issue log cleanup skipped symlink directory: %s", protected_path
            )
            return _empty_cleanup_result(skipped_count=1)
    if repository_log_dir.is_symlink():
        _logger.warning(
            "Hosted Issue log cleanup skipped symlink repository directory: %s", repo_id
        )
        return _empty_cleanup_result(skipped_count=1)
    try:
        repository_log_dir.resolve(strict=False).relative_to(issues_root.resolve(strict=False))
    except ValueError:
        _logger.warning(
            "Hosted Issue log cleanup rejected repository path outside log root: %s", repo_id
        )
        return _empty_cleanup_result(skipped_count=1)
    if not repository_log_dir.is_dir():
        return _empty_cleanup_result()

    scanned_count = eligible_count = deleted_count = skipped_count = failed_count = 0
    try:
        directory_entries = list(repository_log_dir.iterdir())
    except OSError as exc:
        _logger.warning("Hosted Issue log cleanup could not list %s: %s", repo_id, exc)
        return _empty_cleanup_result(failed_count=1)

    for log_path in directory_entries:
        scanned_count += 1
        try:
            if (
                log_path.is_symlink()
                or not log_path.name.endswith(".log")
                or not log_path.is_file()
            ):
                skipped_count += 1
                _logger.debug(
                    "Hosted Issue log cleanup skipped %s: not a regular log file", log_path.name
                )
                continue
            if log_path.stat().st_mtime >= cutoff_epoch:
                skipped_count += 1
                _logger.debug(
                    "Hosted Issue log cleanup skipped %s: within 14-day retention", log_path.name
                )
                continue
            eligible_count += 1
            log_path.unlink()
            deleted_count += 1
        except FileNotFoundError:
            skipped_count += 1
            _logger.debug("Hosted Issue log cleanup skipped %s: already removed", log_path.name)
        except OSError as exc:
            failed_count += 1
            _logger.warning("Hosted Issue log cleanup failed for %s: %s", log_path.name, exc)

    return IssueLogCleanupResult(
        scanned_count=scanned_count,
        eligible_count=eligible_count,
        deleted_count=deleted_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
    )


def _empty_cleanup_result(
    *, skipped_count: int = 0, failed_count: int = 0
) -> IssueLogCleanupResult:
    """构造尚未扫描目录时的清理计数。"""
    return IssueLogCleanupResult(
        scanned_count=0,
        eligible_count=0,
        deleted_count=0,
        skipped_count=skipped_count,
        failed_count=failed_count,
    )

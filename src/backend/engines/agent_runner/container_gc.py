"""宿主 Docker runner 资源清理引擎。

仅调用 Docker CLI；预览与执行共享同一候选筛选，执行时对每项再次采用安全删除命令。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.core.shared.interfaces.container_runner import (
    ContainerGcEntry,
    ContainerGcRequest,
    ContainerGcResult,
    DockerRunnerCallable,
)
from backend.infrastructure.child_env import build_sanitized_child_env

_DOCKER_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_BUILDX_CACHE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_BUILDX_RELATIVE_AGE_PATTERN = re.compile(
    r"^(?P<quantity>\d+|a|an) (?P<unit>seconds?|minutes?|hours?|days?|weeks?|months?|years?) ago$"
)


def run_container_gc(
    request: ContainerGcRequest,
    *,
    runner: DockerRunnerCallable | None = None,
    now: datetime | None = None,
) -> ContainerGcResult:
    """预览或删除宿主 Docker 中安全的悬空镜像与过期 build cache。

    镜像删除不使用 ``--force``，且会先检查是否有容器引用。build cache 先由
    Buildx JSON 清单筛选为可回收且超过保留期的记录，再按记录 ID 逐条删除；
    任何无法识别的记录都失败关闭并保留。

    Args:
        request: 是否实际删除及 build cache 保留天数。
        runner: 可注入的 Docker CLI 执行器，供测试观察命令。
        now: 清理时钟，供边界测试注入；默认使用 UTC 当前时间。

    Returns:
        包含逐项判定、计数和宿主磁盘容量的结果。

    Raises:
        FileNotFoundError: Docker CLI 不在 PATH 中。
        ValueError: build cache 保留期不是正整数。
    """
    if request.cache_retention_days < 1:
        raise ValueError("cache_retention_days must be at least 1")
    if not shutil.which("docker") and runner is None:
        raise FileNotFoundError("docker CLI not found on PATH. Install Docker Engine first.")

    effective_runner = runner if runner is not None else _default_gc_runner
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    else:
        current_time = current_time.astimezone(UTC)
    cutoff_time = current_time - timedelta(days=request.cache_retention_days)
    gc_entries: list[ContainerGcEntry] = []

    image_scan_result = _run_gc_command(
        effective_runner,
        ["docker", "image", "ls", "--filter", "dangling=true", "--quiet", "--no-trunc"],
    )
    if image_scan_result.returncode != 0:
        gc_entries.append(_gc_failure("image-scan", "scan", image_scan_result.stderr.strip()))
    else:
        for image_id in sorted(set(image_scan_result.stdout.splitlines())):
            normalized_image_id = image_id.strip()
            if not normalized_image_id:
                continue
            if _DOCKER_IMAGE_ID_PATTERN.fullmatch(normalized_image_id) is None:
                gc_entries.append(
                    _gc_failure("image", normalized_image_id, "Docker returned an invalid image ID")
                )
                continue
            image_usage_result = _run_gc_command(
                effective_runner,
                [
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"ancestor={normalized_image_id}",
                ],
            )
            if image_usage_result.returncode != 0:
                gc_entries.append(
                    _gc_failure(
                        "image",
                        normalized_image_id,
                        image_usage_result.stderr.strip() or "Could not check container references",
                    )
                )
                continue
            if image_usage_result.stdout.strip():
                gc_entries.append(
                    ContainerGcEntry(
                        resource_type="image",
                        resource_id=normalized_image_id,
                        status="skipped",
                        reason="referenced by a container",
                    )
                )
                continue
            if request.apply:
                image_remove_result = _run_gc_command(
                    effective_runner,
                    ["docker", "image", "rm", normalized_image_id],
                )
                if image_remove_result.returncode != 0:
                    gc_entries.append(
                        _gc_failure(
                            "image",
                            normalized_image_id,
                            image_remove_result.stderr.strip() or "Docker refused to remove image",
                            is_eligible=True,
                        )
                    )
                    continue
                image_status = "deleted"
                image_reason = "dangling image removed without force"
            else:
                image_status = "eligible"
                image_reason = "dangling image is not referenced by a container"
            gc_entries.append(
                ContainerGcEntry(
                    resource_type="image",
                    resource_id=normalized_image_id,
                    status=image_status,
                    reason=image_reason,
                    is_eligible=True,
                )
            )

    cache_scan_result = _run_gc_command(
        effective_runner,
        ["docker", "buildx", "du", "--format=json"],
    )
    if cache_scan_result.returncode != 0:
        gc_entries.append(
            _gc_failure(
                "build-cache-scan",
                "scan",
                cache_scan_result.stderr.strip() or "Docker Buildx cache scan failed",
            )
        )
    else:
        for cache_line in cache_scan_result.stdout.splitlines():
            if not cache_line.strip():
                continue
            try:
                cache_record = json.loads(cache_line)
                cache_id = str(cache_record["ID"])
                last_used_at = _parse_cache_timestamp(
                    cache_record["LastUsedAt"], reference_time=current_time
                )
                is_reclaimable = cache_record["Reclaimable"] is True
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                gc_entries.append(
                    _gc_failure("build-cache", "unknown", f"Invalid Buildx record: {exc}")
                )
                continue
            if _BUILDX_CACHE_ID_PATTERN.fullmatch(cache_id) is None:
                gc_entries.append(_gc_failure("build-cache", cache_id, "Invalid Buildx cache ID"))
                continue
            if not is_reclaimable:
                gc_entries.append(
                    ContainerGcEntry(
                        resource_type="build-cache",
                        resource_id=cache_id,
                        status="skipped",
                        reason="Buildx marks this cache record as not reclaimable",
                        last_used_at=last_used_at,
                    )
                )
                continue
            if last_used_at >= cutoff_time:
                retention_reason = (
                    "age is not clearly beyond the retention boundary"
                    if last_used_at == cutoff_time
                    else f"last used within {request.cache_retention_days} days"
                )
                gc_entries.append(
                    ContainerGcEntry(
                        resource_type="build-cache",
                        resource_id=cache_id,
                        status="skipped",
                        reason=retention_reason,
                        last_used_at=last_used_at,
                    )
                )
                continue
            if request.apply:
                cache_remove_result = _run_gc_command(
                    effective_runner,
                    [
                        "docker",
                        "buildx",
                        "prune",
                        "--filter",
                        f"id={cache_id}",
                        "--filter",
                        "inuse=false",
                        "--force",
                    ],
                )
                if cache_remove_result.returncode != 0:
                    gc_entries.append(
                        _gc_failure(
                            "build-cache",
                            cache_id,
                            cache_remove_result.stderr.strip() or "Buildx refused to prune cache",
                            last_used_at=last_used_at,
                            is_eligible=True,
                        )
                    )
                    continue
                cache_status = "deleted"
                cache_reason = (
                    f"reclaimable cache older than {request.cache_retention_days} days removed"
                )
            else:
                cache_status = "eligible"
                cache_reason = f"reclaimable cache older than {request.cache_retention_days} days"
            gc_entries.append(
                ContainerGcEntry(
                    resource_type="build-cache",
                    resource_id=cache_id,
                    status=cache_status,
                    reason=cache_reason,
                    last_used_at=last_used_at,
                    is_eligible=True,
                )
            )

    disk_usage = shutil.disk_usage(Path("/"))
    return ContainerGcResult(
        request=request,
        entries=tuple(gc_entries),
        disk_total_bytes=disk_usage.total,
        disk_used_bytes=disk_usage.used,
        disk_free_bytes=disk_usage.free,
        scanned_count=sum(entry.resource_type in {"image", "build-cache"} for entry in gc_entries),
        eligible_count=sum(entry.is_eligible for entry in gc_entries),
        deleted_count=sum(entry.status == "deleted" for entry in gc_entries),
        skipped_count=sum(entry.status == "skipped" for entry in gc_entries),
        failed_count=sum(entry.status == "failed" for entry in gc_entries),
    )


def _default_gc_runner(
    argv: list[str], *, env: dict[str, str], cwd: Path, check: bool
) -> subprocess.CompletedProcess[str]:
    """捕获 Docker CLI 输出并明确使用 UTF-8 解码。"""
    return subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _run_gc_command(
    runner: Callable[..., object], argv: list[str]
) -> subprocess.CompletedProcess[str]:
    """执行只捕获输出的 Docker GC 命令。"""
    response = runner(argv, env=build_sanitized_child_env(), cwd=Path.cwd(), check=False)
    if not isinstance(response, subprocess.CompletedProcess):
        raise TypeError("Docker runner must return subprocess.CompletedProcess")
    return response


def _parse_cache_timestamp(timestamp_text: object, *, reference_time: datetime) -> datetime:
    """解析 Buildx 时间戳或相对时间，并对相对粒度采用保守下界。"""
    if not isinstance(timestamp_text, str) or not timestamp_text:
        raise ValueError("LastUsedAt is missing")
    normalized_timestamp = timestamp_text.replace("Z", "+00:00")
    try:
        parsed_timestamp = datetime.fromisoformat(normalized_timestamp)
    except ValueError:
        if normalized_timestamp in {"just now", "less than a minute ago"}:
            return reference_time
        relative_age_match = _BUILDX_RELATIVE_AGE_PATTERN.fullmatch(normalized_timestamp)
        if relative_age_match is None:
            raise
        quantity_text = relative_age_match.group("quantity")
        relative_quantity = 1 if quantity_text in {"a", "an"} else int(quantity_text)
        relative_unit = relative_age_match.group("unit").rstrip("s")
        unit_durations = {
            "second": timedelta(seconds=relative_quantity),
            "minute": timedelta(minutes=relative_quantity),
            "hour": timedelta(hours=relative_quantity),
            "day": timedelta(days=relative_quantity),
            "week": timedelta(weeks=relative_quantity),
            # 以每月 30 天保守换算；较粗粒度的边界候选因此会被跳过。
            "month": timedelta(days=30 * relative_quantity),
            "year": timedelta(days=365 * relative_quantity),
        }
        if relative_unit not in unit_durations:
            raise ValueError(f"Unsupported relative time unit: {relative_unit}")
        return reference_time - unit_durations[relative_unit]
    if parsed_timestamp.tzinfo is None:
        return parsed_timestamp.replace(tzinfo=UTC)
    return parsed_timestamp.astimezone(UTC)


def _gc_failure(
    resource_type: str,
    resource_id: str,
    reason: str,
    *,
    last_used_at: datetime | None = None,
    is_eligible: bool = False,
) -> ContainerGcEntry:
    """创建不泄漏子进程环境的 GC 失败摘要。"""
    return ContainerGcEntry(
        resource_type=resource_type,
        resource_id=resource_id,
        status="failed",
        reason=reason or "Docker command failed without an error message",
        last_used_at=last_used_at,
        is_eligible=is_eligible,
    )

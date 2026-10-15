"""基于 JSON 文件的 GitHub API 额度预算（跨进程共享）。

状态落在 ``<本机状态目录>/github-api-budget.json``，更新时持有旁路 ``.lock`` 文件的
排他锁、持锁后重新读盘再合并，所以 daemon、Monitor、CLI 同时写也不会互相覆盖。
读取不加锁：写入是 ``os.replace`` 原子替换，读者永远看到完整文件。

文件内容::

    {"version": 1,
     "blocked_until": {"graphql": "<UTC ISO>"},
     "last_probe_at": "<UTC ISO>",
     "remaining": {"graphql": {"remaining": 4940, "limit": 5000, "reset_at": "<UTC ISO>"}},
     "usage": {"2026-10-15T22": {"graphql:issue list": 12}}}
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.core.shared.interfaces.agent_runner import GitHubApiRateLimitError
from backend.core.shared.interfaces.github_api_budget import IGitHubApiBudget
from backend.core.shared.models import product_identity

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 POSIX 平台退化为无锁
    fcntl = None  # type: ignore[assignment]

_BUDGET_FILENAME = "github-api-budget.json"
# 测试与特殊部署可用环境变量把账本指到别处，避免写进真实的本机状态目录。
BUDGET_PATH_ENV_VAR = "KC_GITHUB_API_BUDGET_PATH"

_USAGE_RETENTION = timedelta(hours=24)
_DEFAULT_PROBE_INTERVAL_SECONDS = 300.0
# 剩余额度低于 max(绝对下限, 比例 × 上限) 就整体暂停到重置时间，给手动操作留余量。
_LOW_REMAINING_FLOOR = 50
_LOW_REMAINING_RATIO = 0.05
_DEFAULT_FLUSH_EVERY_CALLS = 10
_DEFAULT_FLUSH_INTERVAL_SECONDS = 10.0


def resolve_github_api_budget_path() -> Path:
    """返回额度账本的位置；环境变量优先，其次本机状态目录。"""
    override_path = os.environ.get(BUDGET_PATH_ENV_VAR, "").strip()
    if override_path:
        return Path(override_path)
    return product_identity.state_home() / _BUDGET_FILENAME


def _parse_utc(value: object) -> datetime | None:
    """解析账本里的 ISO 时间；格式不对按缺失处理。"""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class JsonGitHubApiBudget(IGitHubApiBudget):
    """跨进程共享的额度账本与暂停闸门。"""

    def __init__(
        self,
        state_path: Path | None = None,
        *,
        source: str | None = None,
        now: Callable[[], datetime] | None = None,
        probe_interval_seconds: float = _DEFAULT_PROBE_INTERVAL_SECONDS,
        flush_every_calls: int = _DEFAULT_FLUSH_EVERY_CALLS,
        flush_interval_seconds: float = _DEFAULT_FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._state_path = (state_path or resolve_github_api_budget_path()).resolve()
        # 来源标签（如 ``kc daemon`` / ``kc console``）：多个进程共用一份账本，靠它回答“谁用的”。
        self._source = source
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._probe_interval = timedelta(seconds=probe_interval_seconds)
        self._flush_every_calls = flush_every_calls
        self._flush_interval_seconds = flush_interval_seconds
        self._pending_usage: dict[tuple[str, str, str], int] = {}
        self._pending_lock = threading.Lock()
        self._last_flush_monotonic = time.monotonic()
        atexit.register(self.flush)

    @property
    def state_path(self) -> Path:
        """账本文件的磁盘位置。"""
        return self._state_path

    # --- 读 ---------------------------------------------------------------

    def _read_state(self) -> dict[str, Any]:
        """读取账本；文件缺失或损坏时按空账本处理，不阻断 gh 调用。"""
        try:
            with open(self._state_path, encoding="utf-8") as state_file:
                payload = json.load(state_file)
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _blocked_until(self, state: dict[str, Any], api_kind: str) -> datetime | None:
        blocked_map = state.get("blocked_until")
        if not isinstance(blocked_map, dict):
            return None
        return _parse_utc(blocked_map.get(api_kind))

    def ensure_available(self, api_kind: str) -> None:
        """暂停期内直接抛限流异常，不让请求发出去。"""
        blocked_until = self._blocked_until(self._read_state(), api_kind)
        if blocked_until is not None and blocked_until > self._now():
            raise GitHubApiRateLimitError(
                f"GitHub {api_kind} API budget is paused until "
                f"{blocked_until.astimezone().strftime('%H:%M:%S')}; request deferred."
            )

    def pause_remaining_seconds(self) -> float:
        """所有类型里最长的剩余暂停秒数。"""
        state = self._read_state()
        blocked_map = state.get("blocked_until")
        if not isinstance(blocked_map, dict):
            return 0.0
        now = self._now()
        longest_pause = 0.0
        for raw_until in blocked_map.values():
            blocked_until = _parse_utc(raw_until)
            if blocked_until is not None:
                longest_pause = max(longest_pause, (blocked_until - now).total_seconds())
        return longest_pause

    def probe_due(self) -> bool:
        """上一次探测（任一进程）距今超过探测间隔。"""
        last_probe_at = _parse_utc(self._read_state().get("last_probe_at"))
        return last_probe_at is None or self._now() - last_probe_at >= self._probe_interval

    def usage_summary(self) -> dict[str, int]:
        """本小时按 ``<kind>:<operation>`` 聚合的调用次数（含尚未落盘的）。"""
        self.flush()
        usage_map = self._read_state().get("usage")
        if not isinstance(usage_map, dict):
            return {}
        hour_counts = usage_map.get(self._hour_key(self._now()))
        if not isinstance(hour_counts, dict):
            return {}
        return {str(label): int(count) for label, count in hour_counts.items()}

    # --- 写 ---------------------------------------------------------------

    @staticmethod
    def _hour_key(moment: datetime) -> str:
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        """持有账本的排他锁；平台不支持 ``fcntl`` 时不加锁。"""
        if fcntl is None:
            yield
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._state_path.with_suffix(self._state_path.suffix + ".lock")
        with open(lock_path, "a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _update(self, mutate: Callable[[dict[str, Any]], None]) -> None:
        """持锁重读账本、合并待落盘的用量、应用修改后原子写回。"""
        with self._pending_lock:
            drained_usage = self._pending_usage
            self._pending_usage = {}
        self._last_flush_monotonic = time.monotonic()
        with self._write_lock():
            state = self._read_state()
            state["version"] = 1
            self._merge_usage(state, drained_usage)
            mutate(state)
            self._write_state(state)

    def _merge_usage(
        self, state: dict[str, Any], drained_usage: dict[tuple[str, str, str], int]
    ) -> None:
        usage_map = state.get("usage")
        if not isinstance(usage_map, dict):
            usage_map = {}
        cutoff_key = self._hour_key(self._now() - _USAGE_RETENTION)
        usage_map = {hour: counts for hour, counts in usage_map.items() if hour >= cutoff_key}
        if drained_usage:
            hour_counts = usage_map.setdefault(self._hour_key(self._now()), {})
            for (api_kind, operation, source_name), call_count in drained_usage.items():
                label = f"{api_kind}:{operation}"
                if source_name:
                    label = f"{source_name} {label}"
                hour_counts[label] = int(hour_counts.get(label, 0)) + call_count
        state["usage"] = usage_map

    def _write_state(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as tmp_file:
            json.dump(state, tmp_file, ensure_ascii=False, indent=2, sort_keys=True)
            tmp_file.write("\n")
        os.replace(tmp_path, self._state_path)
        try:
            os.chmod(self._state_path, 0o600)
        except OSError:
            pass

    def flush(self) -> None:
        """把内存里攒着的用量计数落盘；没有待落盘内容时什么都不做。"""
        with self._pending_lock:
            has_pending = bool(self._pending_usage)
        if has_pending:
            self._update(lambda _state: None)

    def record_call(self, api_kind: str, operation: str) -> None:
        """计数先攒在内存里，达到次数或时间阈值再落盘，避免每次调用都写文件。"""
        with self._pending_lock:
            call_key = (api_kind, operation, self._source or "")
            self._pending_usage[call_key] = self._pending_usage.get(call_key, 0) + 1
            pending_total = sum(self._pending_usage.values())
        elapsed_seconds = time.monotonic() - self._last_flush_monotonic
        if (
            pending_total >= self._flush_every_calls
            or elapsed_seconds >= self._flush_interval_seconds
        ):
            self.flush()

    def record_rate_limited(self, api_kind: str, resume_at: datetime) -> None:
        """标记该类型额度耗尽；已有更晚的暂停截止时间时保留更晚的。"""

        def _mutate(state: dict[str, Any]) -> None:
            self._extend_block(state, api_kind, resume_at)

        self._update(_mutate)

    def record_probe(
        self,
        api_kind: str,
        *,
        remaining: int,
        limit: int,
        reset_at: datetime,
    ) -> None:
        """记录探测结果：见底则进入暂停期；额度充足则撤销旧的暂停标记。"""
        probed_at = self._now()

        def _mutate(state: dict[str, Any]) -> None:
            state["last_probe_at"] = probed_at.isoformat()
            remaining_map = state.get("remaining")
            if not isinstance(remaining_map, dict):
                remaining_map = {}
            remaining_map[api_kind] = {
                "remaining": remaining,
                "limit": limit,
                "reset_at": reset_at.isoformat(),
            }
            state["remaining"] = remaining_map
            low_threshold = max(_LOW_REMAINING_FLOOR, int(limit * _LOW_REMAINING_RATIO))
            if remaining <= low_threshold:
                self._extend_block(state, api_kind, reset_at)
                return
            # 探测结果以服务器为准：额度已经充足，旧的暂停标记不该继续拦人。
            blocked_map = state.get("blocked_until")
            if isinstance(blocked_map, dict):
                blocked_map.pop(api_kind, None)

        self._update(_mutate)

    @staticmethod
    def _extend_block(state: dict[str, Any], api_kind: str, resume_at: datetime) -> None:
        blocked_map = state.get("blocked_until")
        if not isinstance(blocked_map, dict):
            blocked_map = {}
        existing_until = _parse_utc(blocked_map.get(api_kind))
        if existing_until is None or resume_at > existing_until:
            blocked_map[api_kind] = resume_at.isoformat()
        state["blocked_until"] = blocked_map

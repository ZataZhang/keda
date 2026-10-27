"""短期记忆文件存储实现。

短期记忆按仓库身份与 Issue 分区。上下文 JSON 只保留有界摘要和相对详情引用；完整的
attempt detail 放在同一 Issue 目录下的 ``attempt-details/`` 日志文件中。读取时会安全
地加载日志并还原完整 detail，供 Skill 蒸馏等消费者使用。旧格式仍可读取。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from ._atomic_io import atomic_write_text

_SUMMARY_MAX_LENGTH = 240


@dataclass
class ShortTermAttemptRecord:
    """短期记忆中一次尝试的记录。"""

    attempt_number: int
    failure_type: str
    detail: str
    recovered: bool = False
    detail_log_path: str = ""
    detail_summary: str = ""


@dataclass
class ShortTermMemoryContext:
    """短期记忆上下文。"""

    repo_id: str
    issue_number: int
    issue_title: str
    issue_url: str
    summary: str = ""
    attempts: list[ShortTermAttemptRecord] = field(default_factory=list)
    final_solution: str = ""
    key_files: tuple[str, ...] = ()
    updated_at: str = ""
    final_solution_log_path: str = ""
    final_solution_summary: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict for on-disk persistence."""
        payload = asdict(self)
        payload["attempts"] = [
            {
                "attempt_number": record.attempt_number,
                "failure_type": record.failure_type,
                "detail": record.detail,
                "recovered": record.recovered,
                "detail_log_path": record.detail_log_path,
            }
            for record in self.attempts
        ]
        payload["key_files"] = list(self.key_files)
        payload.pop("final_solution_summary", None)
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "ShortTermMemoryContext":
        """Rehydrate a current or legacy persisted dict."""
        attempts_raw = payload.get("attempts", [])
        attempts = [
            ShortTermAttemptRecord(
                attempt_number=int(record.get("attempt_number", 0)),
                failure_type=str(record.get("failure_type", "")),
                detail=str(record.get("detail", "")),
                recovered=bool(record.get("recovered", False)),
                detail_log_path=str(record.get("detail_log_path", "")),
                detail_summary=(
                    str(record.get("detail", "")) if record.get("detail_log_path") else ""
                ),
            )
            for record in attempts_raw
            if isinstance(record, dict)
        ]
        key_files = tuple(payload.get("key_files", ()))
        final_solution_log_path = str(payload.get("final_solution_log_path", ""))
        return cls(
            repo_id=str(payload.get("repo_id", "")),
            issue_number=int(payload.get("issue_number", 0)),
            issue_title=str(payload.get("issue_title", "")),
            issue_url=str(payload.get("issue_url", "")),
            summary=str(payload.get("summary", "")),
            attempts=attempts,
            final_solution=str(payload.get("final_solution", "")),
            key_files=key_files,
            updated_at=str(payload.get("updated_at", "")),
            final_solution_log_path=final_solution_log_path,
            final_solution_summary=(
                str(payload.get("final_solution", "")) if final_solution_log_path else ""
            ),
        )


class ShortTermMemoryStore:
    """基于本地文件系统的短期记忆读写器。"""

    def __init__(
        self,
        base_dir: str | Path,
        *,
        repository_identity: str | None = None,
        legacy_base_dirs: Iterable[str | Path] = (),
    ) -> None:
        self._base_dir = Path(base_dir)
        self._repository_identity = repository_identity
        self._legacy_base_dirs = tuple(Path(directory) for directory in legacy_base_dirs)

    @property
    def base_dir(self) -> Path:
        """Return the configured base directory."""
        return self._base_dir

    def _context_path(
        self,
        repository_key: str,
        issue_number: int,
        *,
        base_dir: Path | None = None,
    ) -> Path:
        selected_base_dir = self._base_dir if base_dir is None else base_dir
        return (
            selected_base_dir
            / "short_term"
            / _safe_segment(repository_key)
            / str(int(issue_number))
            / "context.json"
        )

    def save(
        self,
        repo_id: str,
        issue_number: int,
        memory_context: ShortTermMemoryContext,
    ) -> Path:
        """持久化 Issue 的短期记忆并返回文件路径。

        每次写入都会刷新 ``updated_at``。完整 attempt detail 以内容哈希命名的 UTF-8
        日志独立保存；上下文 JSON 只写摘要和相对引用，并通过共享原子写入器落盘。
        """
        path = self._context_path(
            self._repository_identity or repo_id,
            issue_number,
        )
        issue_dir = path.parent
        payload = memory_context.to_dict()
        payload["summary"] = _compact_summary(memory_context.summary)
        payload["attempts"] = [
            self._attempt_payload(issue_dir, attempt) for attempt in memory_context.attempts
        ]
        final_solution_log_path, final_solution_summary = self._detail_payload(
            issue_dir,
            detail=memory_context.final_solution,
            prior_log_path=memory_context.final_solution_log_path,
            prior_summary=memory_context.final_solution_summary,
        )
        payload["final_solution"] = final_solution_summary
        payload["final_solution_log_path"] = final_solution_log_path
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
        return path

    def _attempt_payload(
        self,
        issue_dir: Path,
        attempt_record: ShortTermAttemptRecord,
    ) -> dict[str, object]:
        """持久化单条 attempt 的短摘要和完整详情引用。"""
        detail_log_path, detail_summary = self._detail_payload(
            issue_dir,
            detail=attempt_record.detail,
            prior_log_path=attempt_record.detail_log_path,
            prior_summary=attempt_record.detail_summary,
        )
        return {
            "attempt_number": attempt_record.attempt_number,
            "failure_type": attempt_record.failure_type,
            "detail": detail_summary,
            "recovered": attempt_record.recovered,
            "detail_log_path": detail_log_path,
        }

    def _detail_payload(
        self,
        issue_dir: Path,
        *,
        detail: str,
        prior_log_path: str,
        prior_summary: str,
    ) -> tuple[str, str]:
        """写入完整详情，或保留不可用日志的安全摘要与引用。"""
        detail_summary = _compact_summary(prior_summary or detail)
        if prior_summary and detail == prior_summary:
            safe_reference = _safe_detail_reference(issue_dir, prior_log_path)
            return (prior_log_path if safe_reference is not None else ""), detail_summary
        if not detail:
            safe_reference = _safe_detail_reference(issue_dir, prior_log_path)
            return (prior_log_path if safe_reference is not None else ""), detail_summary
        detail_path = _write_detail_log(issue_dir, detail)
        relative_reference = detail_path.relative_to(issue_dir).as_posix()
        return relative_reference, _compact_summary(detail)

    def load(self, repo_id: str, issue_number: int) -> ShortTermMemoryContext | None:
        """按仓库身份读取短期记忆，并兼容仓库内旧路径与旧分区键。"""
        path = self._find_context_path(repo_id, issue_number)
        if path is None:
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        memory_context = ShortTermMemoryContext.from_dict(payload)
        memory_context.repo_id = repo_id
        self._hydrate_details(path.parent, memory_context)
        return memory_context

    def _find_context_path(self, repo_id: str, issue_number: int) -> Path | None:
        """查找新分区或允许读取的旧仓库内分区。"""
        repository_key = self._repository_identity or repo_id
        primary_path = self._context_path(repository_key, issue_number)
        if primary_path.is_file():
            return primary_path

        if self._repository_identity is None:
            legacy_issue_key_path = self._context_path(f"issue-{issue_number}", issue_number)
            return legacy_issue_key_path if legacy_issue_key_path.is_file() else None

        legacy_repository_keys = (repo_id, f"issue-{issue_number}")
        for legacy_base_dir in self._legacy_base_dirs:
            for legacy_repository_key in legacy_repository_keys:
                legacy_path = self._context_path(
                    legacy_repository_key,
                    issue_number,
                    base_dir=legacy_base_dir,
                )
                if legacy_path.is_file():
                    return legacy_path
        return None

    def _hydrate_details(
        self,
        issue_dir: Path,
        memory_context: ShortTermMemoryContext,
    ) -> None:
        """安全地读取详情引用；缺失或越界时保留 JSON 摘要。"""
        for attempt_record in memory_context.attempts:
            stored_summary = attempt_record.detail
            had_reference = bool(attempt_record.detail_log_path)
            detail_text, normalized_reference = _read_detail_log(
                issue_dir,
                attempt_record.detail_log_path,
                stored_summary,
            )
            attempt_record.detail = detail_text
            attempt_record.detail_log_path = normalized_reference
            attempt_record.detail_summary = stored_summary if had_reference else ""

        stored_solution_summary = memory_context.final_solution
        had_solution_reference = bool(memory_context.final_solution_log_path)
        final_solution_text, normalized_reference = _read_detail_log(
            issue_dir,
            memory_context.final_solution_log_path,
            stored_solution_summary,
        )
        memory_context.final_solution = final_solution_text
        memory_context.final_solution_log_path = normalized_reference
        memory_context.final_solution_summary = (
            stored_solution_summary if had_solution_reference else ""
        )


def _read_detail_log(
    issue_dir: Path,
    detail_log_reference: str,
    fallback_summary: str,
) -> tuple[str, str]:
    """读取安全的相对日志引用，失败时返回原摘要。"""
    if not detail_log_reference:
        return fallback_summary, ""
    detail_path = _safe_detail_reference(issue_dir, detail_log_reference)
    if detail_path is None:
        return fallback_summary, ""
    try:
        detail_text = detail_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return fallback_summary, detail_log_reference
    return detail_text, detail_log_reference


def _safe_detail_reference(issue_dir: Path, detail_log_reference: str) -> Path | None:
    """解析相对日志引用，并拒绝目录穿越或符号链接越界。"""
    if not detail_log_reference or "\\" in detail_log_reference:
        return None
    relative_path = PurePosixPath(detail_log_reference)
    if relative_path.is_absolute() or any(part in ("", ".", "..") for part in relative_path.parts):
        return None
    detail_path = issue_dir.joinpath(*relative_path.parts)
    try:
        resolved_base_dir = issue_dir.parents[2].resolve()
        resolved_issue_dir = issue_dir.resolve()
        resolved_detail_path = detail_path.resolve(strict=False)
    except OSError:
        return None
    if not resolved_issue_dir.is_relative_to(resolved_base_dir):
        return None
    if not resolved_detail_path.is_relative_to(resolved_issue_dir):
        return None
    return detail_path


def _write_detail_log(issue_dir: Path, detail_text: str) -> Path:
    """原子写入内容寻址的 UTF-8 attempt detail 日志。"""
    detail_digest = hashlib.sha256(detail_text.encode("utf-8")).hexdigest()
    relative_reference = f"attempt-details/{detail_digest}.log"
    detail_path = _safe_detail_reference(issue_dir, relative_reference)
    if detail_path is None:
        raise OSError("Attempt detail log path escaped its Issue directory.")
    atomic_write_text(detail_path, detail_text)
    return detail_path


def _compact_summary(detail_text: str) -> str:
    """从详情中选择首条有效诊断行，并限制摘要长度。"""
    summary_line = next(
        (line.strip() for line in detail_text.splitlines() if _is_summary_line(line)),
        "",
    )
    if len(summary_line) > _SUMMARY_MAX_LENGTH:
        return summary_line[: _SUMMARY_MAX_LENGTH - 1] + "…"
    return summary_line


def _is_summary_line(line: str) -> bool:
    """跳过空行、代码围栏和 stdout/stderr 标签。"""
    normalized_line = line.strip()
    if not normalized_line or normalized_line.startswith("```"):
        return False
    return normalized_line.casefold() not in {"stdout:", "stderr:"}


def _safe_segment(value: str) -> str:
    """Coerce a string to a safe filesystem segment."""
    cleaned = (value or "").strip() or "default"
    if cleaned in {".", ".."}:
        cleaned = cleaned.replace(".", "_")
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in cleaned)


__all__ = [
    "ShortTermAttemptRecord",
    "ShortTermMemoryContext",
    "ShortTermMemoryStore",
]

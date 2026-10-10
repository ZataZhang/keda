"""短期记忆文件存储实现。

短期记忆按 ``<base_dir>/short_term/<repo_id>/<issue_number>/context.json`` 组织；
文件内容是结构化 JSON 上下文，包含任务摘要、尝试轮次、最终成功方案、关键文件路径。
所有文件 I/O 显式使用 ``encoding="utf-8"``，写入操作通过共享的
``infrastructure/memory/_atomic_io.atomic_write_text`` 完成，避免
并发场景下产生半写损坏文件。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ._atomic_io import atomic_write_text


@dataclass
class ShortTermAttemptRecord:
    """短期记忆中一次尝试的精简记录。"""

    attempt_number: int
    failure_type: str
    detail: str
    recovered: bool = False


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

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict for on-disk persistence."""
        payload = asdict(self)
        payload["attempts"] = [asdict(record) for record in self.attempts]
        payload["key_files"] = list(self.key_files)
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "ShortTermMemoryContext":
        """Rehydrate from a persisted dict."""
        attempts_raw = payload.get("attempts", [])
        attempts = tuple(ShortTermAttemptRecord(**record) for record in attempts_raw)
        key_files = tuple(payload.get("key_files", ()))
        return cls(
            repo_id=str(payload.get("repo_id", "")),
            issue_number=int(payload.get("issue_number", 0)),
            issue_title=str(payload.get("issue_title", "")),
            issue_url=str(payload.get("issue_url", "")),
            summary=str(payload.get("summary", "")),
            attempts=list(attempts),
            final_solution=str(payload.get("final_solution", "")),
            key_files=key_files,
            updated_at=str(payload.get("updated_at", "")),
        )


class ShortTermMemoryStore:
    """基于本地文件系统的短期记忆读写器。"""

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    @property
    def base_dir(self) -> Path:
        """Return the configured base directory."""
        return self._base_dir

    def _context_path(self, repo_id: str, issue_number: int) -> Path:
        return (
            self._base_dir
            / "short_term"
            / _safe_segment(repo_id)
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

        每次写入都会刷新 ``updated_at``。文件通过 ``tmp + os.replace`` 原子落盘：
        先在同一父目录写入临时文件，再替换目标路径，避免并发写入留下半个文件；
        并发冲突时采用最后写入者生效。
        """
        path = self._context_path(repo_id, issue_number)
        payload = memory_context.to_dict()
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
        return path

    def load(self, repo_id: str, issue_number: int) -> ShortTermMemoryContext | None:
        """按 registry id 读取短期记忆，并兼容旧版 worktree 分区键。"""
        path = self._context_path(repo_id, issue_number)
        if not path.is_file():
            # 旧版把 worktree 名 issue-N 当作 repo_id；后续 save 会写入稳定仓库分区。
            legacy_repo_id = f"issue-{int(issue_number)}"
            legacy_path = self._context_path(legacy_repo_id, issue_number)
            if not legacy_path.is_file():
                return None
            path = legacy_path
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        memory_context = ShortTermMemoryContext.from_dict(payload)
        memory_context.repo_id = repo_id
        return memory_context


def _safe_segment(value: str) -> str:
    """Coerce a string to a safe filesystem segment."""
    cleaned = (value or "").strip() or "default"
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in cleaned)


__all__ = [
    "ShortTermAttemptRecord",
    "ShortTermMemoryContext",
    "ShortTermMemoryStore",
]

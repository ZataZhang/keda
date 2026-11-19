"""Agent Runner 记忆层的抽象端口（Protocols）。

具体实现位于 ``infrastructure/memory/``；本模块只定义 ``core``
层业务规则依赖的协议。``infrastructure/memory/`` 中的具体类
由 use case 在 composition root 注入到业务规则中。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol


@dataclass
class ShortTermAttempt:
    """Attempt 精简记录（与 ``ShortTermAttemptRecord`` 等价的轻量协议类型）。"""

    attempt_number: int
    failure_type: str
    detail: str
    recovered: bool = False
    detail_log_path: str = ""
    detail_summary: str = ""


@dataclass
class ShortTermContextPayload:
    """持久化用的短期记忆上下文载荷。"""

    repo_id: str
    issue_number: int
    issue_title: str
    issue_url: str
    summary: str = ""
    attempts: list[ShortTermAttempt] = field(default_factory=list)
    final_solution: str = ""
    key_files: tuple[str, ...] = ()
    updated_at: str = ""
    final_solution_log_path: str = ""
    final_solution_summary: str = ""


@dataclass
class LongTermFactRecord:
    """长期记忆文件解析后的轻量记录。"""

    topic: str
    category: str
    content: str
    tags: tuple[str, ...]
    path: Path


@dataclass
class SkillRecord:
    """Skill 文件解析后的轻量记录。"""

    name: str
    description: str
    tags: tuple[str, ...]
    version: str
    draft: bool
    updated: str
    usage_count: int
    success_count: int
    path: Path
    body: str = ""


class IShortTermMemoryStore(Protocol):
    """短期记忆读写协议。"""

    def save(
        self,
        repo_id: str,
        issue_number: int,
        payload: ShortTermContextPayload,
    ) -> Path:
        """持久化某个 Issue 的短期记忆并返回文件路径。"""

    def load(self, repo_id: str, issue_number: int) -> ShortTermContextPayload | None:
        """读取某个 Issue 的短期记忆；不存在时返回 ``None``。"""


class ILongTermMemoryStore(Protocol):
    """长期记忆读写协议。"""

    def append_fact(
        self,
        *,
        category: str,
        topic: str,
        content: str,
        tags: Iterable[str] = (),
    ) -> Path:
        """追加或新建一条长期事实并返回文件路径。"""

    def load_all(self) -> list[LongTermFactRecord]:
        """读取全部长期事实。"""


class ISkillStore(Protocol):
    """Skill 草稿与已晋升 skill 持久化协议。"""

    def save_draft(
        self,
        *,
        name: str,
        description: str,
        tags: tuple[str, ...],
        body: str,
        version: str = "1.0.0",
        draft: bool = True,
        usage_count: int = 0,
        success_count: int = 0,
    ) -> Path:
        """把新的或更新后的 skill 草稿写入草稿目录并返回文件路径。"""

    def find_similar_draft(
        self,
        *,
        name: str,
        tags: Iterable[str],
        description: str = "",
        similarity_threshold: float = 0.8,
    ) -> SkillRecord | None:
        """按名称、标签与描述查找最接近的已有草稿；没有达到阈值的返回 ``None``。"""

    def update_draft(
        self,
        existing: SkillRecord,
        *,
        name: str,
        description: str,
        tags: tuple[str, ...],
        body: str,
        usage_count: int = 0,
        success_count: int = 0,
        version: str | None = None,
        draft: bool = True,
    ) -> Path:
        """把更新内容合并进已有草稿并返回其新路径。"""

    def reset_usage_metrics(self, draft: SkillRecord) -> Path:
        """清空草稿未经验证的使用计数，草稿内容保持不变。"""

    def load_promoted_skills(self, skills_dirs: Iterable[str | Path]) -> list[SkillRecord]:
        """从给定目录读取全部已晋升（非草稿）的 skill。"""

    def promote_draft(
        self,
        draft: SkillRecord,
        target_dirs: Iterable[str | Path],
    ) -> Path | None:
        """把草稿移到第一个可写的目标目录并清除草稿标记；没有可写目录时返回 ``None``。"""


__all__ = [
    "IShortTermMemoryStore",
    "ILongTermMemoryStore",
    "ISkillStore",
    "LongTermFactRecord",
    "ShortTermAttempt",
    "ShortTermContextPayload",
    "SkillRecord",
]

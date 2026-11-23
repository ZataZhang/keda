"""Gitea API 调用与 PR 创建的值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class GiteaRequestOptions:
    """HTTP 请求的可选 JSON、查询和缺失资源处理参数。"""

    json_body: dict[str, Any] | None = None
    params: dict[str, str | int | bool] | None = None
    allow_not_found: bool = False


@dataclass(frozen=True, slots=True)
class GiteaDraftPullRequest:
    """创建草稿 PR 所需的内容与本地检出目录。"""

    title: str
    body: str
    base_branch: str
    cwd: Path

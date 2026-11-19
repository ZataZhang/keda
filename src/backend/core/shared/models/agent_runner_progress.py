"""Agent Runner 传递给实时调用方的阶段进度契约。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunProgressEvent:
    """一次 Issue 生命周期阶段变化的实时投影。

    Attributes:
        repo_id: 发生阶段变化的仓库标识。
        issue_number: 发生阶段变化的 Issue 编号。
        lifecycle_event: 原始生命周期事件类型。
        phase: 生命周期粗粒度阶段。
        status: 生命周期事件语义状态。
        occurred_at: 阶段变化的 UTC ISO 时间。
    """

    repo_id: str
    issue_number: int
    lifecycle_event: str
    phase: str
    status: str
    occurred_at: str

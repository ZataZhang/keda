"""store 行 <-> core 记录适配器与通用小工具（engines 内部共享）。

infrastructure 层的镜像 dataclass 与 core 冻结记录字段名一致，这里用
``dataclasses.asdict`` 做无损转换；execution / service 共用，避免相互 import
形成环。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any

from backend.core.shared.models.model_evaluation import (
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationScoreRecord,
)
from backend.engines.agent_runner.factory import create_console_store


def now_iso() -> str:
    """UTC ISO-8601 时间戳（带偏移标记的机器可读口径）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def store() -> Any:
    """返回 Console SQLite 账本实例（含评测 mixin）。"""
    return create_console_store()


def core_run(row: Any) -> EvaluationRunRecord | None:
    """store 行 -> core run 记录；``None`` 原样传递。"""
    return EvaluationRunRecord(**dataclasses.asdict(row)) if row is not None else None


def core_result(row: Any) -> EvaluationResultRecord:
    """store 行 -> core 候选结果记录。"""
    return EvaluationResultRecord(**dataclasses.asdict(row))


def core_score(row: Any) -> EvaluationScoreRecord:
    """store 行 -> core 评分记录。"""
    return EvaluationScoreRecord(**dataclasses.asdict(row))

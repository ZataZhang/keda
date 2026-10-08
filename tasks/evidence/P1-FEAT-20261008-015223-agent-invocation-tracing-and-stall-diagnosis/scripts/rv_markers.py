"""从既有 ``kc logs --issue`` 的输出里解析调用标记（Issue #242 的 RV oracle 共用）。

解析口径与生产代码逐字一致：标记常量、字段名都直接从
``backend.core.use_cases.agent_invocation_tracing`` 取，避免 oracle 自己抄一份
然后与实现漂移。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "src"))

from backend.core.use_cases.agent_invocation_tracing import (  # noqa: E402
    INVOCATION_COVERAGE_INCOMPLETE_MARKER,
    INVOCATION_END_MARKER,
    INVOCATION_START_MARKER,
)

_FIELD_NAMES = (
    "invocation",
    "run",
    "issue",
    "attempt",
    "phase",
    "role",
    "executor",
    "model_requested",
    "retry_of",
    "retry_reason",
    "log",
    "outcome",
    "exit_code",
    "duration_s",
    "model_reported",
    "model_source",
    "failure_category",
)


@dataclass(frozen=True)
class MarkerLine:
    """一行调用标记的解析结果。"""

    kind: str
    raw: str
    fields: dict[str, str]

    def field(self, name: str) -> str | None:
        """取字段值；``-`` 归一为 ``None``（生产侧就是用它表示"没有"）。"""
        value = self.fields.get(name)
        return None if value in (None, "-") else value


def parse_markers(text: str) -> list[MarkerLine]:
    """按出现顺序解析全部调用标记行。"""
    parsed: list[MarkerLine] = []
    for raw_line in text.splitlines():
        for kind, marker in (
            ("start", INVOCATION_START_MARKER),
            ("end", INVOCATION_END_MARKER),
            ("coverage", INVOCATION_COVERAGE_INCOMPLETE_MARKER),
        ):
            if marker not in raw_line:
                continue
            body = raw_line.split(marker, 1)[1].strip()
            fields: dict[str, str] = {}
            for token in body.split():
                for name in _FIELD_NAMES:
                    prefix = f"{name}="
                    if token.startswith(prefix) and name not in fields:
                        fields[name] = token[len(prefix) :]
            parsed.append(MarkerLine(kind=kind, raw=raw_line, fields=fields))
            break
    return parsed


@dataclass(frozen=True)
class Invocation:
    """一次调用的起止配对结果。"""

    invocation_id: str
    start: MarkerLine
    end: MarkerLine | None

    @property
    def executor(self) -> str | None:
        """实际执行器名。"""
        return self.start.field("executor")

    @property
    def phase(self) -> str | None:
        """调用阶段。"""
        return self.start.field("phase")

    @property
    def retry_of(self) -> str | None:
        """被本次调用替代的上一次调用 id。"""
        return self.start.field("retry_of")

    @property
    def retry_reason(self) -> str | None:
        """替代原因的闭集取值。"""
        return self.start.field("retry_reason")

    @property
    def outcome(self) -> str | None:
        """结束标记给出的结局；未闭合时为 ``None``。"""
        return self.end.field("outcome") if self.end is not None else None

    @property
    def model_source(self) -> str | None:
        """模型来源；未闭合时为 ``None``。"""
        return self.end.field("model_source") if self.end is not None else None

    @property
    def closed(self) -> bool:
        """是否已闭合（有对应的结束标记）。"""
        return self.end is not None


def pair_invocations(markers: list[MarkerLine]) -> list[Invocation]:
    """按 ``invocation=`` 把起止标记配对（不靠相邻行推断）。"""
    starts: dict[str, MarkerLine] = {}
    ends: dict[str, MarkerLine] = {}
    order: list[str] = []
    for marker in markers:
        if marker.kind == "coverage":
            continue
        invocation_id = marker.field("invocation")
        if invocation_id is None:
            continue
        if marker.kind == "start":
            if invocation_id not in starts:
                order.append(invocation_id)
            starts[invocation_id] = marker
        else:
            ends[invocation_id] = marker
    return [
        Invocation(invocation_id=key, start=starts[key], end=ends.get(key))
        for key in order
        if key in starts
    ]


def summarize(invocations: list[Invocation]) -> list[dict[str, Any]]:
    """把配对结果压成可断言、可打印的字典列表。"""
    return [
        {
            "invocation_id": item.invocation_id,
            "run": item.start.field("run"),
            "issue": item.start.field("issue"),
            "attempt": item.start.field("attempt"),
            "phase": item.phase,
            "role": item.start.field("role"),
            "executor": item.executor,
            "model_requested": item.end.field("model_requested")
            if item.end is not None
            else item.start.field("model_requested"),
            "model_reported": item.end.field("model_reported") if item.end is not None else None,
            "model_source": item.model_source,
            "outcome": item.outcome,
            "exit_code": item.end.field("exit_code") if item.end is not None else None,
            "duration_s": item.end.field("duration_s") if item.end is not None else None,
            "failure_category": item.end.field("failure_category") if item.end is not None else None,
            "retry_of": item.retry_of,
            "retry_reason": item.retry_reason,
            "closed": item.closed,
        }
        for item in invocations
    ]


__all__ = [
    "Invocation",
    "MarkerLine",
    "pair_invocations",
    "parse_markers",
    "summarize",
]

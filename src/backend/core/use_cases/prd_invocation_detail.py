"""PRD 调用明细的读取组装：把已落库的进程调用事实解释成可归组的展示行。

本模块是「执行过程」视图**唯一**的调用账本读取出口，职责边界如下：

- **只解读，不采集**：展示字段全部来自 ``agent_invocation_events`` 已有事件——
  身份取事件列，其余取起止事件的 ``detail_json``；不新增采集点、不改用量统计
  口径，也不把调用事实复制进生命周期事件。
- **只披露，不推测**：「账本不可读」与「可读但没有记录」是两件事。读端能力缺失、
  读取异常、PRD 未关联 Issue 都判 :attr:`PrdInvocationDetail.available` 为
  ``False``；未闭合的调用保持 ``unclosed`` 语义，**绝不**虚构结束时间或伪造调用行。
- **旁路语义**：任何读取故障都降级为显式缺口，绝不向调用方抛出，页面其余区域
  照常渲染（与调用观测写侧、生命周期账本同一口径）。
- **写读同源**：生命周期事件用同一个 :data:`INVOCATION_RUN_DETAIL_KEY` 记录所属
  执行会话，读侧与前端据此确定性归组，不做任何时间窗口推断。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.core.shared.interfaces.runner_console import InvocationEventRecord
from backend.core.shared.models.backlog import PrdInvocationDetail, PrdInvocationRow
from backend.core.use_cases.agent_invocation_tracing import (
    EVENT_INVOCATION_FINISHED,
    EVENT_INVOCATION_STARTED,
    active_invocation_trace_context,
    build_invocation_timeline,
    resolve_invocation_store,
)

_logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_INVOCATION_READ_LIMIT",
    "INVOCATION_RUN_DETAIL_KEY",
    "build_prd_invocation_detail",
    "with_invocation_run_detail",
]

#: 生命周期事件 detail 里「所属执行会话」的键名。
#: 写侧注入与读侧归组共用这一个事实源：改这里就是改契约。
INVOCATION_RUN_DETAIL_KEY = "invocation_run"

#: 单次读取的调用事件上限，沿用调用账本既有口径（本视图不做分页/过滤/搜索）。
DEFAULT_INVOCATION_READ_LIMIT = 500


def _unavailable_invocation_detail() -> PrdInvocationDetail:
    """返回「调用明细不可用」的固定形状（不可读与无从归属共用）。"""
    return PrdInvocationDetail(available=False, truncated=False, rows=[])


def with_invocation_run_detail(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    """为 runner 执行期内产生的生命周期事件补上所属执行会话标识。

    只在当前执行流绑定了调用观测上下文（即处于一次 Issue 处理会话内）时才写；
    roadmap 侧事件（promote / merged 等）在上下文之外，保持不带该键的现状。
    调用方已显式提供同名键时原样返回，**不覆盖**——调用方比本函数更清楚语义。

    Args:
        detail: 调用方传入的结构化摘要，可能为 ``None``。

    Returns:
        需要注入时返回**新字典**（不就地修改入参，避免污染调用方复用同一个
        detail 对象的其他写入）；其余情况原样返回入参。
    """
    trace_context = active_invocation_trace_context()
    if trace_context is None:
        return detail
    if detail is not None and INVOCATION_RUN_DETAIL_KEY in detail:
        return detail
    injected_detail = dict(detail or {})
    injected_detail[INVOCATION_RUN_DETAIL_KEY] = trace_context.run_id
    return injected_detail


def _resolve_issue_invocation_reader(store: object | None) -> Any | None:
    """探测存储是否具备「按 Issue 跨 run 读取调用事件」的能力。

    复用 :func:`~backend.core.use_cases.agent_invocation_tracing.resolve_invocation_store`
    的账本识别，再单独确认读端存在：只实现 run 级读端的旧存储必须判为不可用，
    否则页面会把「读不到」误报成「没有调用记录」。
    """
    invocation_store = resolve_invocation_store(store)
    if invocation_store is None:
        return None
    reader = getattr(invocation_store, "list_issue_invocation_events", None)
    return reader if callable(reader) else None


def _parse_event_detail(detail_json: str) -> dict[str, Any]:
    """解析调用事件的 ``detail_json``；损坏行降级为空字典而不是让整批读取失败。"""
    try:
        parsed_detail = json.loads(detail_json)
    except (TypeError, ValueError):
        return {}
    return parsed_detail if isinstance(parsed_detail, dict) else {}


def _display_field(
    finish_detail: dict[str, Any],
    start_detail: dict[str, Any],
    field_name: str,
) -> Any:
    """取一个展示字段：结束事件优先，未闭合的调用回落开始事件。

    起止两条事件共用同一份身份字段，但 ``failure_category`` / ``exit_code`` /
    ``token_usage`` 这类终态字段只在结束事件里有值；只写过开始事件的调用
    （被强制 kill）必须仍能从开始事件里拿到阶段与重试关联。
    """
    finish_value = finish_detail.get(field_name)
    return finish_value if finish_value is not None else start_detail.get(field_name)


def _as_optional_int(raw_value: Any) -> int | None:
    """把 detail 里的整数字段收敛成 ``int | None``；``bool`` 与脏值都视为缺失。"""
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        return None
    return raw_value


def _as_optional_str(raw_value: Any) -> str | None:
    """把 detail 里的字符串字段收敛成 ``str | None``，非字符串一律视为缺失。"""
    return raw_value if isinstance(raw_value, str) and raw_value else None


def _as_optional_mapping(raw_value: Any) -> dict | None:
    """把 token 用量这类映射字段收敛成 ``dict | None``（无用量 ≠ 零用量）。"""
    return raw_value if isinstance(raw_value, dict) and raw_value else None


def _build_invocation_rows(
    invocation_events: list[InvocationEventRecord],
) -> list[PrdInvocationRow]:
    """把追加式调用事件流组装成按发生顺序排列的展示行。

    起止配对、``unclosed`` / ``incomplete`` 语义与耗时一律复用
    :func:`~backend.core.use_cases.agent_invocation_tracing.build_invocation_timeline`
    （同一份解读口径，不在本模块重实现），本函数只补它没透出的展示字段。
    """
    timeline_rows = build_invocation_timeline(invocation_events)
    start_event_by_invocation: dict[str, InvocationEventRecord] = {}
    start_details: dict[str, dict[str, Any]] = {}
    finish_details: dict[str, dict[str, Any]] = {}
    for invocation_event in invocation_events:
        if invocation_event.event_type == EVENT_INVOCATION_STARTED:
            start_event_by_invocation.setdefault(invocation_event.invocation_id, invocation_event)
            start_details.setdefault(
                invocation_event.invocation_id, _parse_event_detail(invocation_event.detail_json)
            )
        elif invocation_event.event_type == EVENT_INVOCATION_FINISHED:
            finish_details.setdefault(
                invocation_event.invocation_id, _parse_event_detail(invocation_event.detail_json)
            )

    display_rows: list[PrdInvocationRow] = []
    for timeline_row in timeline_rows:
        start_event = start_event_by_invocation.get(timeline_row.invocation_id)
        start_detail = start_details.get(timeline_row.invocation_id, {})
        finish_detail = finish_details.get(timeline_row.invocation_id, {})
        display_rows.append(
            PrdInvocationRow(
                invocation_id=timeline_row.invocation_id,
                # 会话标识直接取事件列（账本身份字段），不从 detail 反推。
                run_id=(start_event.run_id if start_event else "") or "",
                attempt_number=_as_optional_int(
                    _display_field(finish_detail, start_detail, "attempt_number")
                ),
                phase=timeline_row.phase,
                role=timeline_row.role,
                executor=timeline_row.executor,
                outcome=timeline_row.outcome,
                failure_category=_as_optional_str(
                    _display_field(finish_detail, start_detail, "failure_category")
                ),
                started_at=timeline_row.started_at,
                finished_at=timeline_row.finished_at,
                duration_seconds=timeline_row.duration_seconds,
                requested_model=timeline_row.requested_model,
                reported_model=timeline_row.reported_model,
                model_source=timeline_row.model_source,
                model_config_snapshot=_as_optional_mapping(
                    _display_field(finish_detail, start_detail, "model_config_snapshot")
                ),
                token_usage=_as_optional_mapping(
                    _display_field(finish_detail, start_detail, "token_usage")
                ),
                retry_of=timeline_row.retry_of,
                retry_reason=_as_optional_str(
                    _display_field(finish_detail, start_detail, "retry_reason")
                ),
                exit_code=_as_optional_int(
                    _display_field(finish_detail, start_detail, "exit_code")
                ),
                log_locator=_as_optional_str(
                    _display_field(finish_detail, start_detail, "log_locator")
                ),
            )
        )
    return display_rows


def build_prd_invocation_detail(
    *,
    store: object | None,
    repo_id: str,
    issue_number: int | None,
    limit: int = DEFAULT_INVOCATION_READ_LIMIT,
) -> PrdInvocationDetail:
    """读取并组装某个 Issue 的调用明细展示集（跨执行会话）。

    Args:
        store: 任意旁路存储对象；不具备 Issue 级调用读端时判为不可用。
        repo_id: 仓库标识（调用账本的读取维度之一）。
        issue_number: 关联 Issue 编号；``None`` 表示该 PRD 无从归属调用事实，
            此时判为不可用而**不是**返回空账本。
        limit: 读取的调用事件上限；达到上限即判 ``truncated``。

    Returns:
        PrdInvocationDetail: ``available`` / ``truncated`` 与按发生顺序排列的
        ``rows``。读取异常只降级为 ``available=False``，绝不向调用方抛出。
    """
    reader = _resolve_issue_invocation_reader(store)
    if reader is None or issue_number is None:
        return _unavailable_invocation_detail()
    bounded_limit = max(1, limit)
    try:
        invocation_events: list[InvocationEventRecord] = reader(
            repo_id=repo_id, issue_number=issue_number, limit=bounded_limit
        )
    except Exception as read_error:  # noqa: BLE001 - 旁路观测读取不得影响详情组装。
        _logger.warning(
            "Failed to read agent invocation events for repo %s issue %s: %s",
            repo_id,
            issue_number,
            read_error,
        )
        return _unavailable_invocation_detail()
    return PrdInvocationDetail(
        available=True,
        truncated=len(invocation_events) >= bounded_limit,
        rows=_build_invocation_rows(invocation_events),
    )

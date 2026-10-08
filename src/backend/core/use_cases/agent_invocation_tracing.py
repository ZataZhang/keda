"""Agent 顶层调用的旁路观测：身份、开始/结束事件与既有 Issue 日志关联。

本模块是 FR-1~FR-4 的唯一实现点：在真实进程调用边界（``run_agent_once``）
前后各发一条事实，并把可 grep 的标记写进**既有**的 per-Issue 日志文件。

三条不可让步的约束：

1. **旁路语义**——观测写入失败只告警并把该 run 的 coverage 标为不完整，
   绝不改变业务返回值、绝不额外触发实现重跑。
2. **不采自由文本**——事件与标记里只有闭集枚举、我们自己生成的 id、整数
   退出码和经过白名单校验的模型名。提示词、命令参数、环境变量值、异常
   message 一律不落库（``subprocess.CalledProcessError`` 的 message 会内嵌
   完整 argv，而 ``argv_tail`` 投递方式下 argv 就含整段提示词）。
3. **不虚构终态**——强制 kill 走不到 ``finally``，此时只保留未闭合的开始
   事件。:func:`build_invocation_timeline` 默认报 ``unclosed``，只有在调用方
   已确认进程退出时才升级为 ``incomplete``；永不从 TTL 反推结束时间。

身份口径：``run_id`` 在处理入口生成，**不依赖 PRD**（无 PRD 的 Issue 同样
完整关联）；每次实际进程调用一个 ``invocation_id``，回退/重试新建 invocation
并以 ``retry_of`` 关联，不合并成一条"成功"记录。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from backend.core.shared.interfaces.agent_runner import AGENT_REPORTED_MODEL_ATTR_NAME
from backend.core.shared.interfaces.runner_console import (
    IInvocationEventStore,
    InvocationEventRecord,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.agent_runner_output_routing import active_issue_log_binding

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 闭集常量（标记文本与结构化字段共用，改这里就是改协议）
# ---------------------------------------------------------------------------

#: 既有 Issue 日志里的调用开始标记（与 ``[iar-attempt-end]`` 同族，可精确 grep）。
INVOCATION_START_MARKER = "[iar-invocation-start]"
#: 既有 Issue 日志里的调用结束标记。
INVOCATION_END_MARKER = "[iar-invocation-end]"
#: 观测自身降级时写入既有 Issue 日志的覆盖不完整标记。
INVOCATION_COVERAGE_INCOMPLETE_MARKER = "[iar-invocation-coverage-incomplete]"

EVENT_INVOCATION_STARTED = "invocation_started"
EVENT_INVOCATION_FINISHED = "invocation_finished"

PHASE_IMPLEMENTATION = "implementation"
PHASE_FIX = "fix"
PHASE_REVIEW = "review"
PHASE_REVIEW_REPAIR = "review_repair"
PHASE_VERIFICATION = "verification"
PHASE_VERIFICATION_RECOVERY = "verification_recovery"
PHASE_REBASE_RECOVERY = "rebase_recovery"
PHASE_CLOSEOUT = "closeout"
PHASE_SUPERVISOR = "supervisor"
PHASE_SUPERVISOR_REPAIR = "supervisor_repair"
PHASE_CONTENT_GENERATION = "content_generation"
PHASE_UNSPECIFIED = "unspecified"

#: 阶段闭集；未知阶段一律归到 :data:`PHASE_UNSPECIFIED`，不猜测语义。
KNOWN_PHASES: frozenset[str] = frozenset(
    {
        PHASE_IMPLEMENTATION,
        PHASE_FIX,
        PHASE_REVIEW,
        PHASE_REVIEW_REPAIR,
        PHASE_VERIFICATION,
        PHASE_VERIFICATION_RECOVERY,
        PHASE_REBASE_RECOVERY,
        PHASE_CLOSEOUT,
        PHASE_SUPERVISOR,
        PHASE_SUPERVISOR_REPAIR,
        PHASE_CONTENT_GENERATION,
        PHASE_UNSPECIFIED,
    }
)

#: 阶段 -> 角色（"谁在跑"）。角色由阶段确定性推导，避免给每个调用点再加一个参数。
_PHASE_ROLE: dict[str, str] = {
    PHASE_IMPLEMENTATION: "implementer",
    PHASE_FIX: "fixer",
    PHASE_REVIEW: "reviewer",
    PHASE_REVIEW_REPAIR: "implementer",
    PHASE_VERIFICATION: "verifier",
    PHASE_VERIFICATION_RECOVERY: "fixer",
    PHASE_REBASE_RECOVERY: "implementer",
    PHASE_CLOSEOUT: "implementer",
    PHASE_SUPERVISOR: "supervisor",
    PHASE_SUPERVISOR_REPAIR: "implementer",
    PHASE_CONTENT_GENERATION: "content_generator",
    PHASE_UNSPECIFIED: "unspecified",
}

OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_ERROR = "error"
#: 进程已确认退出、但只有开始事件（被强制 kill / 宿主崩溃）。
OUTCOME_INCOMPLETE = "incomplete"
#: 只有开始事件、进程是否还活着未知。**不能**据此断言已中断。
OUTCOME_UNCLOSED = "unclosed"

MODEL_SOURCE_EXECUTOR_REPORT = "executor_report"
MODEL_SOURCE_UNKNOWN = "unknown"
#: 执行器没有可信报告实际模型时的展示值（结构化字段为 ``null``）。
MODEL_UNREPORTED = "未提供"
#: 本次调用没有下发任何模型参数（无预设绑定，或回退换人后绑定被丢弃）。
MODEL_NOT_REQUESTED = "未下发"

#: 内部子 Agent 覆盖度：当前没有任何执行器提供可信的内部事件，一律未观测。
INTERNAL_AGENT_COVERAGE_UNOBSERVED = "unobserved"

FAILURE_TIMEOUT = "timeout"
FAILURE_NONZERO_EXIT = "nonzero_exit"
FAILURE_AGENT_UNAVAILABLE = "agent_unavailable"
FAILURE_OS_ERROR = "os_error"
FAILURE_RUNTIME_ERROR = "runtime_error"
FAILURE_UNKNOWN = "unknown"

COVERAGE_REASON_STORE_WRITE_FAILED = "store_write_failed"
COVERAGE_REASON_STORE_UNAVAILABLE = "store_unavailable"
COVERAGE_REASON_LOG_LOCATOR_OUT_OF_ROOT = "log_locator_out_of_root"

#: 原地瞬态重试（同一执行器再发一次请求）。
RETRY_REASON_TRANSIENT = "transient_failure"
#: 续传没被 CLI 认下，同一轮改跑全新会话。
RETRY_REASON_RESUME_NOT_STARTED = "resume_not_started"
#: 上一个执行器跑不起来（CLI 缺失 / 额度耗尽 / 超时 / 崩溃），换到下一个候选。
RETRY_REASON_EXECUTOR_FALLBACK = "executor_fallback"

#: 允许写进事件/标记的标识符形状：agent 名、模型名、会话 id 都过这一关。
#: 不匹配即视为"未提供"，绝不把原文透出去（自由文本可能携带提示词或凭据）。
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+=-]{0,127}$")


class InvocationLogLocatorError(ValueError):
    """日志相对定位越出批准的日志根目录（含 ``..``、绝对路径与符号链接逃逸）。"""


# ---------------------------------------------------------------------------
# 脱敏与校验
# ---------------------------------------------------------------------------


def sanitize_identifier(raw_value: object, *, fallback: str) -> str:
    """把外部来源的字符串收敛成白名单标识符；不匹配时返回 ``fallback``。

    这是"不采自由文本"约束的执行点：模型名、agent 名、会话 id 都可能来自
    执行器的 stdout，形状不可信，因此先校验再落库。
    """
    if not isinstance(raw_value, str):
        return fallback
    candidate = raw_value.strip()
    if not candidate or not _SAFE_IDENTIFIER_PATTERN.match(candidate):
        return fallback
    return candidate


def normalize_reported_model(raw_model: object) -> str | None:
    """校验执行器自报的模型名；不可信或缺失时返回 ``None``（= 未提供）。

    ``None`` 与"执行器报告了一个空模型"是同一件事：都只说明**没有可信报告值**，
    因此展示为 :data:`MODEL_UNREPORTED`，结构化字段留 ``null``，
    ``model_source`` 记 :data:`MODEL_SOURCE_UNKNOWN`。绝不拿配置值冒充。
    """
    normalized = sanitize_identifier(raw_model, fallback="")
    return normalized or None


def classify_invocation_failure(exc: BaseException) -> str:
    """把失败异常归类到闭集失败类别；**不**读取异常文本。

    异常 message 可能内嵌完整 argv（``argv_tail`` 投递时即整段提示词）与捕获
    输出，因此只按类型归类。
    """
    if isinstance(exc, subprocess.TimeoutExpired):
        return FAILURE_TIMEOUT
    if isinstance(exc, FileNotFoundError):
        return FAILURE_AGENT_UNAVAILABLE
    if isinstance(exc, subprocess.CalledProcessError):
        return FAILURE_NONZERO_EXIT
    if isinstance(exc, OSError):
        return FAILURE_OS_ERROR
    if isinstance(exc, RuntimeError):
        return FAILURE_RUNTIME_ERROR
    return FAILURE_UNKNOWN


def resolve_phase_role(phase: str) -> str:
    """返回阶段对应的角色；未知阶段归到 ``unspecified``。"""
    return _PHASE_ROLE.get(phase, _PHASE_ROLE[PHASE_UNSPECIFIED])


def build_log_locator(log_root: Path, log_path: Path) -> str | None:
    """把 Issue 日志绝对路径渲染成相对日志根的 POSIX 定位串。

    Returns:
        相对定位串；``log_path`` 不在 ``log_root`` 之内时返回 ``None``（调用方
        据此把 coverage 标为不完整，而不是写一个可能越界的定位）。
    """
    try:
        relative = log_path.resolve().relative_to(log_root.resolve())
    except (OSError, ValueError):
        return None
    return relative.as_posix()


def resolve_invocation_log_path(log_root: Path, locator: str) -> Path:
    """把事件里的相对定位解析回绝对路径，并拒绝任何越界形态。

    读取侧的唯一入口：``locator`` 来自持久化事件（可能被人工编辑或旧版本写入），
    因此按不可信输入处理。

    Raises:
        InvocationLogLocatorError: 绝对路径、含 ``..`` 的相对路径、解析后越出
            ``log_root``（含符号链接逃逸），或定位为空。
    """
    candidate = Path(locator)
    if not locator or candidate.is_absolute():
        raise InvocationLogLocatorError(f"Invocation log locator must be relative: {locator!r}")
    resolved_root = log_root.resolve()
    try:
        resolved_target = (resolved_root / candidate).resolve()
        resolved_target.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise InvocationLogLocatorError(
            f"Invocation log locator escapes the approved log root: {locator!r}"
        ) from exc
    return resolved_target


# ---------------------------------------------------------------------------
# run / invocation 身份
# ---------------------------------------------------------------------------


def build_invocation_run_id(repo_id: str, issue_number: int | None) -> str:
    """生成一次 Issue run 的观测身份；**不依赖 PRD**。

    形状 ``<repo_id>#issue-<n>#<UTC 时间戳>-<随机 12 位>``：跨机器不碰撞，
    本地查询只覆盖本机存储（不宣称已聚合其他电脑的日志）。
    """
    safe_repo_id = sanitize_identifier(repo_id, fallback="unknown-repo")
    issue_part = f"issue-{issue_number}" if issue_number is not None else "issue-none"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe_repo_id}#{issue_part}#{stamp}-{uuid.uuid4().hex[:12]}"


def new_invocation_id() -> str:
    """生成一次实际进程调用的唯一 id。"""
    return f"inv-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 跨调用上下文
# ---------------------------------------------------------------------------


@dataclass
class InvocationTraceContext:
    """一次 Issue run 内共享的调用观测上下文（可变：承载降级状态与重试关联）。

    用对象承载跨调用上下文，而不是给 13 个调用点各加四五个位置参数。通过
    :func:`bound_invocation_trace_context` 绑到 contextvar 后，真实进程边界
    （``run_agent_once``）无需任何新参数即可读到它。

    Attributes:
        repo_id: 仓库标识（日志子目录名）。
        run_id: 本次 Issue run 的观测身份。
        issue_number: Issue 编号；非 Issue 场景为 ``None``。
        store: 旁路事件账本端口；``None`` 表示存储不可用（只写日志标记）。
        log_root: 批准的日志根目录；``None`` 表示当前不在输出路由作用域内。
        log_locator: 本次 run 的 Issue 日志相对定位；越界或不可用时为 ``None``。
        internal_agent_coverage: 内部子 Agent 覆盖度，恒为 ``unobserved``。
        coverage_complete: 观测覆盖是否完整；任何一次写入失败即置 ``False``。
        last_started_invocation_id: 本上下文最近一次**已开始**的调用 id，
            重试/回退关联（``retry_of``）以它为锚。
        pending_retry_of: 下一次调用要关联的上一次 invocation id；由
            :meth:`link_next_invocation` 声明、:func:`start_invocation` 消费。
        pending_retry_reason: 与 :attr:`pending_retry_of` 配套的闭集重试原因。
    """

    repo_id: str
    run_id: str
    issue_number: int | None
    store: IInvocationEventStore | None
    log_root: Path | None = None
    log_locator: str | None = None
    internal_agent_coverage: str = INTERNAL_AGENT_COVERAGE_UNOBSERVED
    coverage_complete: bool = True
    last_started_invocation_id: str | None = None
    pending_retry_of: str | None = None
    pending_retry_reason: str | None = None

    def link_next_invocation(self, reason: str) -> None:
        """声明"下一次实际进程调用是对最近那次的替代"（原地重试或换执行器回退）。

        替代关系必须显式声明而不是由本模块猜测：同一阶段连续两次调用既可能是重试，
        也可能是两件不相干的事。声明后由 :func:`start_invocation` 消费一次即清空，
        因此不会把关联误传给更后面的调用。

        没有"最近一次调用"可关联时静默跳过——那说明上一次连进程都没起起来
        （例如 argv 组装阶段就 fail-fast），此时不存在可关联的调用事实。

        Args:
            reason: 闭集重试原因（见 ``RETRY_REASON_*``）。**不接受**自由文本。
        """
        if self.last_started_invocation_id is None:
            return
        self.pending_retry_of = self.last_started_invocation_id
        self.pending_retry_reason = reason

    def mark_coverage_incomplete(self, reason: str) -> None:
        """把本 run 的观测覆盖标为不完整，并在状态翻转时写一条日志标记。

        Args:
            reason: 闭集降级原因（见 ``COVERAGE_REASON_*``）。**不接受**自由文本。
        """
        if not self.coverage_complete:
            return
        self.coverage_complete = False
        _logger.warning(
            "%s run=%s issue=%s reason=%s detail=观测事件写入降级，业务结果不受影响；"
            "本次 run 的调用清单可能不完整。",
            INVOCATION_COVERAGE_INCOMPLETE_MARKER,
            self.run_id,
            self.issue_number,
            reason,
        )


_ACTIVE_TRACE_CONTEXT: ContextVar[InvocationTraceContext | None] = ContextVar(
    "iar_invocation_trace_context", default=None
)


def resolve_invocation_store(store: object | None) -> IInvocationEventStore | None:
    """从任意旁路存储对象中识别调用事件账本能力（鸭子类型，失败返回 ``None``）。

    与 :func:`backend.core.use_cases.agent_runner_lifecycle.resolve_lifecycle_store`
    同一手法：runner 侧传入的是 ``IRunHistoryStore``，但实现对象同时提供本端口，
    因此用能力探测而非新增构造参数，避免把观测泄漏进既有调用链。
    """
    if store is None:
        return None
    if hasattr(store, "append_invocation_event") and hasattr(store, "list_invocation_events"):
        return cast(IInvocationEventStore, store)
    return None


def build_invocation_trace_context(
    *,
    repo_id: str,
    issue_number: int | None,
    run_history_store: object | None,
) -> InvocationTraceContext:
    """在 Issue 处理入口组装观测上下文（含 run_id 与日志定位）。

    必须在 :func:`~backend.core.use_cases.agent_runner_output_routing.issue_output_routing`
    作用域内调用，才能读到本次 run 的日志定位；作用域外调用同样可用，只是
    ``log_locator`` 为 ``None``。
    """
    invocation_store = resolve_invocation_store(run_history_store)
    log_root: Path | None = None
    log_locator: str | None = None
    log_binding = active_issue_log_binding()
    if log_binding is not None:
        log_root = log_binding.log_root
        log_locator = build_log_locator(log_binding.log_root, log_binding.log_path)
    context = InvocationTraceContext(
        repo_id=repo_id,
        run_id=build_invocation_run_id(repo_id, issue_number),
        issue_number=issue_number,
        store=invocation_store,
        log_root=log_root,
        log_locator=log_locator,
    )
    if invocation_store is None:
        # 存储不可用：日志标记照写，但明确披露调用清单不完整（不静默假装已记录）。
        context.mark_coverage_incomplete(COVERAGE_REASON_STORE_UNAVAILABLE)
    elif log_locator is None and log_binding is not None:
        context.mark_coverage_incomplete(COVERAGE_REASON_LOG_LOCATOR_OUT_OF_ROOT)
    return context


@contextmanager
def bound_invocation_trace_context(
    context: InvocationTraceContext | None,
) -> Iterator[InvocationTraceContext | None]:
    """把观测上下文绑到当前执行流；作用域结束后原样恢复。

    ``None`` 也是合法入参：非 Issue 场景（``kc ask`` / REPL / 辩论）不绑定上下文，
    此时 :func:`start_invocation` 全程空转，argv 与业务行为与本特性之前逐字节一致。
    """
    token = _ACTIVE_TRACE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_TRACE_CONTEXT.reset(token)


def active_invocation_trace_context() -> InvocationTraceContext | None:
    """返回当前执行流绑定的观测上下文；未绑定时为 ``None``。"""
    return _ACTIVE_TRACE_CONTEXT.get()


# ---------------------------------------------------------------------------
# 单次调用的开始 / 结束
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvocationStartRequest:
    """一次实际进程调用的观测起点声明（收敛参数，避免逐字段位置传参）。

    Attributes:
        agent_name: **实际执行器**注册名（回退换人后就是换到的那个）。
        phase: 调用阶段，取 :data:`KNOWN_PHASES` 之一。
        profile: 声明式 argv 用途（run / deliberate / generate / repl）。
        attempt_number: recovery 轮次（1 起）；非 attempt 主体调用为 ``None``。
        requested_model: 实际下发的模型参数值；未下发任何模型时为 ``None``。
        requested_reasoning_effort: 实际下发的推理档；未下发时为 ``None``。
        resumed_session_id: 实际注入的续传会话 id；未续传为 ``None``。

    重试/回退关联（``retry_of`` / ``retry_reason``）不在这里声明：它描述的是
    "本次调用与**上一次**调用的关系"，属于执行流状态而非本次调用的属性，因此由
    :meth:`InvocationTraceContext.link_next_invocation` 在发起替代之前声明。
    """

    agent_name: str
    phase: str
    profile: str
    attempt_number: int | None = None
    requested_model: str | None = None
    requested_reasoning_effort: str | None = None
    resumed_session_id: str | None = None


@dataclass(frozen=True)
class InvocationObservation:
    """:func:`start_invocation` 返回的观测句柄，交给 :func:`finish_invocation`。

    Attributes:
        invocation_id: 本次调用的唯一 id。
        context: 所属 run 的观测上下文。
        request: 起点声明（结束时原样复用，保证两条事件字段一致）。
        started_at: 开始时刻 ISO8601 UTC。
        started_mono: 开始时刻的单调时钟读数，用于计算墙钟耗时。
        recorded: 开始事件是否已成功落库；``False`` 表示只写了日志标记。
        retry_of: 本次调用替代的上一次 invocation id；非重试为 ``None``。
        retry_reason: 与 :attr:`retry_of` 配套的闭集重试原因。
    """

    invocation_id: str
    context: InvocationTraceContext
    request: InvocationStartRequest
    started_at: str
    started_mono: float
    recorded: bool
    retry_of: str | None = None
    retry_reason: str | None = None


def _append_event(
    context: InvocationTraceContext,
    *,
    invocation_id: str,
    event_type: str,
    request: InvocationStartRequest,
    occurred_at: str,
    detail: dict[str, Any],
) -> bool:
    """落一条观测事件；存储故障降级为告警 + coverage 不完整，绝不外抛。

    Returns:
        ``True`` 表示事件已进存储（含幂等命中）；``False`` 表示未能落库。
    """
    if context.store is None:
        return False
    executor_name = sanitize_identifier(request.agent_name, fallback="unknown")
    try:
        context.store.append_invocation_event(
            InvocationEventRecord(
                run_id=context.run_id,
                event_key=f"{invocation_id}:{event_type}",
                event_type=event_type,
                invocation_id=invocation_id,
                repo_id=context.repo_id,
                issue_number=context.issue_number,
                phase=request.phase,
                role=resolve_phase_role(request.phase),
                agent=executor_name,
                occurred_at=occurred_at,
                detail_json=json.dumps(detail, ensure_ascii=False, sort_keys=True),
            )
        )
    except Exception:  # noqa: BLE001 - 旁路观测：任何存储故障都不得影响业务结果。
        _logger.warning(
            "Failed to persist agent invocation event (run=%s invocation=%s type=%s).",
            context.run_id,
            invocation_id,
            event_type,
            exc_info=True,
        )
        context.mark_coverage_incomplete(COVERAGE_REASON_STORE_WRITE_FAILED)
        return False
    return True


def _identity_fields(
    context: InvocationTraceContext,
    invocation_id: str,
    request: InvocationStartRequest,
) -> dict[str, Any]:
    """两条事件与两条日志标记共用的身份字段（唯一事实源，避免起止漂移）。"""
    return {
        "invocation_id": invocation_id,
        "run_id": context.run_id,
        "repo_id": context.repo_id,
        "issue_number": context.issue_number,
        "attempt_number": request.attempt_number,
        "phase": request.phase,
        "role": resolve_phase_role(request.phase),
        "executor": sanitize_identifier(request.agent_name, fallback="unknown"),
        "profile": request.profile,
    }


def _marker_text(value: object) -> str:
    """标记行字段的空值占位：一律渲染成 ``-``，与 ``retry_of=-`` 等约定一致。

    直接内插 ``None`` 会让日志里出现 ``attempt=None`` 这种"看起来像值"的文本，
    grep / 按 ``-`` 归一的读取方都会把它当成字符串 ``"None"``。
    """
    return "-" if value is None else str(value)


def _log_identity_text(
    context: InvocationTraceContext,
    invocation_id: str,
    request: InvocationStartRequest,
) -> str:
    """渲染标记行里可 grep 的身份前缀；并发场景靠它归属，不靠相邻文本推断。"""
    return (
        f"invocation={invocation_id} run={context.run_id} "
        f"issue={_marker_text(context.issue_number)} "
        f"attempt={_marker_text(request.attempt_number)} "
        f"phase={request.phase} role={resolve_phase_role(request.phase)} "
        f"executor={sanitize_identifier(request.agent_name, fallback='unknown')}"
    )


def start_invocation(request: InvocationStartRequest) -> InvocationObservation | None:
    """记录一次实际进程调用的开始事件与日志标记。

    没有绑定观测上下文（非 Issue 场景）时返回 ``None`` 且完全空转：不写库、
    不写日志、不产生任何副作用。

    Returns:
        观测句柄，或 ``None``（当前执行流不在观测范围内）。
    """
    context = active_invocation_trace_context()
    if context is None:
        return None
    phase = request.phase if request.phase in KNOWN_PHASES else PHASE_UNSPECIFIED
    effective_request = request if phase == request.phase else replace(request, phase=phase)
    # 消费一次即清空：替代关系只对紧接着的这一次调用成立，不能顺延给后面的调用。
    retry_of = context.pending_retry_of
    retry_reason = context.pending_retry_reason
    context.pending_retry_of = None
    context.pending_retry_reason = None
    invocation_id = new_invocation_id()
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    detail: dict[str, Any] = {
        **_identity_fields(context, invocation_id, effective_request),
        "parent_invocation_id": None,
        "retry_of": retry_of,
        "retry_reason": retry_reason,
        "requested_model": normalize_reported_model(effective_request.requested_model),
        "requested_reasoning_effort": sanitize_identifier(
            effective_request.requested_reasoning_effort, fallback=""
        )
        or None,
        "reported_model": None,
        "model_source": MODEL_SOURCE_UNKNOWN,
        "resumed_session_id": sanitize_identifier(effective_request.resumed_session_id, fallback="")
        or None,
        "started_at": started_at,
        "finished_at": None,
        "duration_seconds": None,
        "outcome": None,
        "exit_code": None,
        "failure_category": None,
        "token_usage": None,
        "token_usage_source": None,
        "internal_agent_coverage": context.internal_agent_coverage,
        "log_locator": context.log_locator,
        # 前后 commit/tree 本次交付恒为 null：取它们要在"绝不能失败"的观测路径里
        # 额外起 git 子进程，且会污染被输出路由的 Issue 日志（偏差已记入 PRD
        # Change Log；§7.1 允许取不到时为 null）。
        "before_commit": None,
        "after_commit": None,
    }
    recorded = _append_event(
        context,
        invocation_id=invocation_id,
        event_type=EVENT_INVOCATION_STARTED,
        request=effective_request,
        occurred_at=started_at,
        detail=detail,
    )
    context.last_started_invocation_id = invocation_id
    _logger.info(
        "%s %s model_requested=%s retry_of=%s retry_reason=%s log=%s",
        INVOCATION_START_MARKER,
        _log_identity_text(context, invocation_id, effective_request),
        detail["requested_model"] or MODEL_NOT_REQUESTED,
        retry_of or "-",
        retry_reason or "-",
        context.log_locator or "-",
    )
    return InvocationObservation(
        invocation_id=invocation_id,
        context=context,
        request=effective_request,
        started_at=started_at,
        started_mono=time.monotonic(),
        recorded=recorded,
        retry_of=retry_of,
        retry_reason=retry_reason,
    )


def link_next_invocation(reason: str) -> None:
    """声明当前执行流的下一次实际进程调用是对最近那次的替代（重试 / 换执行器）。

    没有绑定观测上下文时静默空转——非 Issue 场景本来就不产生调用记录。

    Args:
        reason: 闭集重试原因（见 ``RETRY_REASON_*``）。
    """
    context = active_invocation_trace_context()
    if context is not None:
        context.link_next_invocation(reason)


def finish_invocation(
    observation: InvocationObservation | None,
    *,
    result: CommandResult | None = None,
    exc: BaseException | None = None,
) -> None:
    """记录一次调用的结束事件与日志标记（``finally`` 路径调用，覆盖异常与超时）。

    ``observation`` 为 ``None`` 时完全空转。耗时用单调时钟差计算，不采信执行器
    自报的 ``duration_seconds``（那是子进程视角，且合成结果里为 0）。

    Args:
        observation: :func:`start_invocation` 返回的句柄。
        result: 正常返回的命令结果；与 ``exc`` 互斥，两者都缺时按 ``error`` 记。
        exc: 调用抛出的异常；只按类型归类，绝不读取其文本。
    """
    if observation is None:
        return
    context = observation.context
    request = observation.request
    finished_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    duration_seconds = round(time.monotonic() - observation.started_mono, 3)
    failure_category: str | None = None
    exit_code: int | None = None
    if exc is not None:
        failure_category = classify_invocation_failure(exc)
        outcome = OUTCOME_TIMEOUT if failure_category == FAILURE_TIMEOUT else OUTCOME_ERROR
        exit_code = getattr(exc, "returncode", None)
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            exit_code = None
    elif result is not None:
        exit_code = result.return_code
        outcome = OUTCOME_OK if result.return_code == 0 else OUTCOME_FAILED
        if outcome == OUTCOME_FAILED:
            failure_category = FAILURE_NONZERO_EXIT
    else:
        outcome = OUTCOME_ERROR
        failure_category = FAILURE_UNKNOWN
    reported_model = normalize_reported_model(
        getattr(result, "reported_model", None)
        if result is not None
        else getattr(exc, AGENT_REPORTED_MODEL_ATTR_NAME, None)
    )
    model_source = (
        MODEL_SOURCE_EXECUTOR_REPORT if reported_model is not None else MODEL_SOURCE_UNKNOWN
    )
    token_usage = result.token_usage if result is not None else None
    detail: dict[str, Any] = {
        **_identity_fields(context, observation.invocation_id, request),
        "parent_invocation_id": None,
        "retry_of": observation.retry_of,
        "retry_reason": observation.retry_reason,
        "requested_model": normalize_reported_model(request.requested_model),
        "requested_reasoning_effort": sanitize_identifier(
            request.requested_reasoning_effort, fallback=""
        )
        or None,
        "reported_model": reported_model,
        "model_source": model_source,
        "resumed_session_id": sanitize_identifier(request.resumed_session_id, fallback="") or None,
        "started_at": observation.started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "outcome": outcome,
        "exit_code": exit_code,
        "failure_category": failure_category,
        "token_usage": (
            {
                "input_tokens": token_usage.input_tokens,
                "output_tokens": token_usage.output_tokens,
                "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
            }
            if token_usage is not None
            else None
        ),
        # 用量只可能来自执行器自报的输出流；拿不到就是没有，不估算。
        "token_usage_source": (MODEL_SOURCE_EXECUTOR_REPORT if token_usage is not None else None),
        "internal_agent_coverage": context.internal_agent_coverage,
        "log_locator": context.log_locator,
        "before_commit": None,
        "after_commit": None,
    }
    _append_event(
        context,
        invocation_id=observation.invocation_id,
        event_type=EVENT_INVOCATION_FINISHED,
        request=request,
        occurred_at=finished_at,
        detail=detail,
    )
    _logger.info(
        "%s %s outcome=%s exit_code=%s duration_s=%s model_requested=%s "
        "model_reported=%s model_source=%s retry_of=%s failure_category=%s",
        INVOCATION_END_MARKER,
        _log_identity_text(context, observation.invocation_id, request),
        outcome,
        _marker_text(exit_code),
        duration_seconds,
        detail["requested_model"] or MODEL_NOT_REQUESTED,
        reported_model or MODEL_UNREPORTED,
        model_source,
        observation.retry_of or "-",
        failure_category or "-",
    )


# ---------------------------------------------------------------------------
# 时间线解读（未闭合 vs 已中断）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvocationTimelineRow:
    """一次调用在时间线上的解读结果。

    Attributes:
        invocation_id: 调用 id。
        phase: 阶段。
        role: 角色。
        executor: 实际执行器。
        retry_of: 被替代的上一次调用 id；非重试为 ``None``。
        started_at: 开始时刻（ISO8601）。
        finished_at: 结束时刻；未闭合/中断时为 ``None``（**不虚构**）。
        duration_seconds: 墙钟耗时；未闭合/中断时为 ``None``。
        outcome: 终态；只有开始事件时按 ``process_confirmed_exited`` 取
            :data:`OUTCOME_INCOMPLETE` 或 :data:`OUTCOME_UNCLOSED`。
    """

    invocation_id: str
    phase: str
    role: str
    executor: str
    retry_of: str | None
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    outcome: str


def build_invocation_timeline(
    events: Sequence[InvocationEventRecord],
    *,
    process_confirmed_exited: bool = False,
) -> list[InvocationTimelineRow]:
    """把追加式事件流解读成每次调用一行的时间线。

    只有开始事件的调用**不**被赋予终态时间：默认报 :data:`OUTCOME_UNCLOSED`
    （活动调用同样可能尚未结束，仅凭未闭合记录不能断言进程已中断），只有调用方
    明确已确认进程退出（``process_confirmed_exited=True``）才升级为
    :data:`OUTCOME_INCOMPLETE`。

    Args:
        events: 某次 run 的事件，需按发生顺序（存储侧已排序）。
        process_confirmed_exited: 宿主进程是否已确认退出。

    Returns:
        按开始时刻排序的时间线行；无法解读的 detail 降级为最小行而不是抛错。
    """
    starts: dict[str, InvocationEventRecord] = {}
    finishes: dict[str, InvocationEventRecord] = {}
    for event in events:
        if event.event_type == EVENT_INVOCATION_STARTED:
            starts.setdefault(event.invocation_id, event)
        elif event.event_type == EVENT_INVOCATION_FINISHED:
            finishes.setdefault(event.invocation_id, event)
    rows: list[InvocationTimelineRow] = []
    for invocation_id, start_event in starts.items():
        start_detail = _parse_detail(start_event.detail_json)
        finish_event = finishes.get(invocation_id)
        finish_detail = _parse_detail(finish_event.detail_json) if finish_event else {}
        if finish_event is not None:
            outcome = str(finish_detail.get("outcome") or OUTCOME_ERROR)
            finished_at = finish_event.occurred_at
            raw_duration = finish_detail.get("duration_seconds")
            duration_seconds = raw_duration if isinstance(raw_duration, float | int) else None
        else:
            outcome = OUTCOME_INCOMPLETE if process_confirmed_exited else OUTCOME_UNCLOSED
            finished_at = None
            duration_seconds = None
        rows.append(
            InvocationTimelineRow(
                invocation_id=invocation_id,
                phase=start_event.phase,
                role=start_event.role,
                executor=start_event.agent,
                retry_of=(str(start_detail["retry_of"]) if start_detail.get("retry_of") else None),
                started_at=start_event.occurred_at,
                finished_at=finished_at,
                duration_seconds=duration_seconds,
                outcome=outcome,
            )
        )
    rows.sort(key=lambda row: (row.started_at or "", row.invocation_id))
    return rows


def describe_unclosed_invocations(rows: Sequence[InvocationTimelineRow]) -> list[str]:
    """返回时间线里未闭合的调用 id（``unclosed`` / ``incomplete``）。"""
    return [
        row.invocation_id for row in rows if row.outcome in (OUTCOME_UNCLOSED, OUTCOME_INCOMPLETE)
    ]


def _parse_detail(detail_json: str) -> dict[str, Any]:
    """解析事件 detail；损坏行降级为空字典，不让整条时间线失败。"""
    try:
        parsed = json.loads(detail_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "COVERAGE_REASON_LOG_LOCATOR_OUT_OF_ROOT",
    "COVERAGE_REASON_STORE_UNAVAILABLE",
    "COVERAGE_REASON_STORE_WRITE_FAILED",
    "EVENT_INVOCATION_FINISHED",
    "EVENT_INVOCATION_STARTED",
    "INTERNAL_AGENT_COVERAGE_UNOBSERVED",
    "INVOCATION_COVERAGE_INCOMPLETE_MARKER",
    "INVOCATION_END_MARKER",
    "INVOCATION_START_MARKER",
    "InvocationLogLocatorError",
    "InvocationObservation",
    "InvocationStartRequest",
    "InvocationTimelineRow",
    "InvocationTraceContext",
    "KNOWN_PHASES",
    "MODEL_NOT_REQUESTED",
    "MODEL_SOURCE_EXECUTOR_REPORT",
    "MODEL_SOURCE_UNKNOWN",
    "MODEL_UNREPORTED",
    "OUTCOME_ERROR",
    "OUTCOME_FAILED",
    "OUTCOME_INCOMPLETE",
    "OUTCOME_OK",
    "OUTCOME_TIMEOUT",
    "OUTCOME_UNCLOSED",
    "PHASE_CLOSEOUT",
    "PHASE_CONTENT_GENERATION",
    "PHASE_FIX",
    "PHASE_IMPLEMENTATION",
    "PHASE_REBASE_RECOVERY",
    "PHASE_REVIEW",
    "PHASE_REVIEW_REPAIR",
    "PHASE_SUPERVISOR",
    "PHASE_SUPERVISOR_REPAIR",
    "PHASE_UNSPECIFIED",
    "PHASE_VERIFICATION",
    "PHASE_VERIFICATION_RECOVERY",
    "RETRY_REASON_EXECUTOR_FALLBACK",
    "RETRY_REASON_RESUME_NOT_STARTED",
    "RETRY_REASON_TRANSIENT",
    "active_invocation_trace_context",
    "bound_invocation_trace_context",
    "build_invocation_run_id",
    "build_invocation_trace_context",
    "build_invocation_timeline",
    "build_log_locator",
    "classify_invocation_failure",
    "describe_unclosed_invocations",
    "finish_invocation",
    "link_next_invocation",
    "new_invocation_id",
    "normalize_reported_model",
    "resolve_invocation_log_path",
    "resolve_invocation_store",
    "resolve_phase_role",
    "sanitize_identifier",
    "start_invocation",
]

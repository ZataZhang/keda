"""候选执行适配器：用现有 Agent Runner 在隔离 worktree 里跑一个 case。

复用 :func:`backend.core.use_cases.run_agent_once.run_agent_with_prompt`
作为唯一进程边界（含停滞监督关闭、输出协议与 usage 自报），因此评测候选
与正式 runner 调用共享同一执行器语义，但**不进入 Issue lifecycle、不写
run_records、不碰主工作区**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.shared.models.agent_model_preset import resolve_model_selection
from backend.core.use_cases.agent_runner_factory import (
    build_app_config_from_settings,
    create_process_runner,
)
from backend.core.use_cases.run_agent_once import run_agent_with_prompt

_logger = logging.getLogger(__name__)

#: 评测调用的 invocation phase：与 attempt 主体区分，观测侧可据此过滤。
EVALUATION_PHASE = "evaluation"


@dataclass(frozen=True)
class CandidateAgentOutcome:
    """一次候选 Agent 执行的边界结果。

    Attributes:
        ok: 进程正常结束且退出码为 0。
        timed_out: 是否由执行器超时击杀。
        summary: 供 payload 与页面展示的简短摘要（截断后），不含完整 transcript。
        exit_code: 子进程退出码；无法取得时 ``None``。
        duration_seconds: wall clock 时长。
        usage_summary: 自报 usage 或 ``None``（≠ 零消耗）。
        error: 失败原因摘要（成功时 ``None``）。
    """

    ok: bool
    timed_out: bool
    summary: str | None
    exit_code: int | None
    duration_seconds: float
    usage_summary: dict[str, Any] | None
    error: str | None


def run_candidate(
    *,
    preset_name: str,
    prompt: str,
    worktree_path: Path,
    settings: Any,
    timeout_seconds: int,
) -> CandidateAgentOutcome:
    """按执行预设在一个隔离 worktree 中运行一次候选 Agent。

    Args:
        preset_name: 已冻结在执行快照中的预设名（在 settings.presets 里解析）。
        prompt: suite 声明的任务提示词（原样投递，不注入评测侧指令）。
        worktree_path: 该候选专属的干净 worktree。
        settings: 新鲜加载的 Agent Runner settings（配置装配的唯一事实源）。
        timeout_seconds: 本次编排加载的 `evaluation.agent_timeout_seconds`
            （同一 run 的全部候选共用同一个值，不是逐 run 落库快照）。

    Returns:
        CandidateAgentOutcome: 执行边界结果；任何异常都被收敛为失败状态。

    Notes:
        预设缺失或 agent 未声明模型模板时**不静默回退**：直接返回带原因的
        失败结果，由编排层写入 result 状态。
    """
    preset = settings.presets.get(preset_name)
    if preset is None:
        return CandidateAgentOutcome(
            ok=False,
            timed_out=False,
            summary=None,
            exit_code=None,
            duration_seconds=0.0,
            usage_summary=None,
            error=f"执行预设 '{preset_name}' 在当前配置中已不存在",
        )
    try:
        config = build_app_config_from_settings(settings)
        model_selection = resolve_model_selection(
            preset_name, config, model_override=None, effort_override=None
        )
        result = run_agent_with_prompt(
            preset.agent,
            prompt,
            worktree_path,
            create_process_runner(),
            config=config,
            capture_output=True,
            timeout_seconds=timeout_seconds,
            profile="run",
            model_selection=model_selection,
            invocation_phase=EVALUATION_PHASE,
        )
    except Exception as exc:  # noqa: BLE001 - 边界失败必须转成状态而不是打断矩阵
        _logger.warning("评测候选 '%s' 执行异常: %s", preset_name, exc)
        timed_out = "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()
        return CandidateAgentOutcome(
            ok=False,
            timed_out=timed_out,
            summary=None,
            exit_code=None,
            duration_seconds=0.0,
            usage_summary=None,
            error=str(exc)[:400],
        )
    usage = usage_of(result)
    summary = _summary_of(result)
    return CandidateAgentOutcome(
        ok=getattr(result, "return_code", 1) == 0,
        timed_out=False,
        summary=summary,
        exit_code=getattr(result, "return_code", None),
        duration_seconds=float(getattr(result, "duration_seconds", 0.0) or 0.0),
        usage_summary=usage,
        error=None if getattr(result, "return_code", 1) == 0 else summary or "Agent 非零退出",
    )


def usage_of(result: Any) -> dict[str, Any] | None:
    """把自报 token usage 收敛成 JSON 友好的摘要；无数据返回 ``None``。"""
    usage = getattr(result, "token_usage", None)
    if usage is None:
        return None
    payload: dict[str, Any] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "total_tokens",
    ):
        value = getattr(usage, key, None)
        if value is not None:
            payload[key] = value
    return payload or None


def _summary_of(result: Any) -> str | None:
    """取 Agent 输出的简短尾部摘要（不落完整 transcript）。"""
    stdout = (getattr(result, "stdout", "") or "").strip()
    stderr = (getattr(result, "stderr", "") or "").strip()
    text = stdout or stderr
    if not text:
        return None
    return text[-1200:]

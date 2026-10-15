"""评审（judge）调用边界：复用现有 Agent 执行器的 ``judge`` 形态打分。

评审不再另起一套 provider/密钥体系：评审预设就是现有的执行预设
（``[agent_runner.presets.*]``），调用走与候选执行同一个进程边界
:func:`backend.core.use_cases.run_agent_once.run_agent_with_prompt`。隔离靠两层：

1. agent 的 ``judge`` profile 声明**禁用全部内置工具**（如 ``--tools ""`` /
   ``--no-tools``）；没有该声明的 agent 不能当评审（探针返回不可用）。
2. 进程 cwd 是每次新建的空临时目录，评审既看不到候选 worktree，也看不到主仓库。

投递的 prompt 与预览/授权的 payload **逐字节一致**（payload 文档自带评分任务说明，
这里不再追加任何前后缀）。本模块不读取、不落盘任何密钥；agent CLI 自己的认证
由其既有配置（如 ``~/.claude``）承担。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.shared.models.agent_model_preset import resolve_model_selection
from backend.core.shared.models.agent_spec import AGENT_PROFILE_JUDGE
from backend.core.shared.models.model_evaluation import EvaluationScoreStatus
from backend.core.use_cases.agent_runner_factory import (
    build_app_config_from_settings,
    create_process_runner,
)
from backend.core.use_cases.run_agent_once import run_agent_with_prompt
from backend.engines.model_evaluation import agent_adapter

_logger = logging.getLogger(__name__)

#: 评审 stderr/响应文本写入错误字段前的截断上限（字符）。
_ERROR_TEXT_LIMIT = 500


@dataclass(frozen=True)
class JudgeAvailability:
    """评审预设的启动前可用性探针结果。"""

    available: bool
    agent: str | None
    model: str | None
    reason: str | None


@dataclass(frozen=True)
class JudgeInvokeOutcome:
    """一次 judge 调用的原始结果。

    ``error_status`` 为 ``None`` 表示拿到了响应文本（schema 是否合规由 core
    解析层裁决）；为 timeout / provider_error 时对应调用侧的失败状态。
    """

    error_status: EvaluationScoreStatus | None
    response_text: str | None
    duration_seconds: float
    usage_summary: dict[str, Any] | None


def probe_judge_availability(preset_name: str, settings: Any) -> JudgeAvailability:
    """探针：该预设的 agent 是否声明了 ``judge`` 形态且 CLI 在本机可执行。

    只做本地检查（配置 + PATH），不发起任何进程调用或网络请求。

    Args:
        preset_name: 执行预设名（评审与候选共用同一份预设清单）。
        settings: 新鲜加载的 Agent Runner settings。

    Returns:
        JudgeAvailability: ``available=False`` 时 ``reason`` 说明缺失环节。
    """
    preset = settings.presets.get(preset_name)
    if preset is None:
        return JudgeAvailability(False, None, None, f"预设 '{preset_name}' 不存在")
    agent_spec = build_app_config_from_settings(settings).agents.get(preset.agent)
    if agent_spec is None:
        return JudgeAvailability(
            False, preset.agent, preset.model, f"agent '{preset.agent}' 未注册"
        )
    if AGENT_PROFILE_JUDGE not in agent_spec.profiles:
        return JudgeAvailability(
            False,
            preset.agent,
            preset.model,
            f"agent '{preset.agent}' 未声明可禁用工具的 judge 形态，无法保证评审只读 payload",
        )
    if shutil.which(agent_spec.bin) is None:
        return JudgeAvailability(
            False, preset.agent, preset.model, f"未找到可执行文件 '{agent_spec.bin}'"
        )
    return JudgeAvailability(True, preset.agent, preset.model, None)


def invoke_judge(
    *,
    preset_name: str,
    payload_text: str,
    settings: Any,
    timeout_seconds: int,
) -> JudgeInvokeOutcome:
    """对单个评审预设执行一次空目录、禁工具的文本调用。

    Args:
        preset_name: 已冻结在评审快照中的执行预设名（在 settings.presets 里解析）。
        payload_text: 与预览逐字节一致的待评审 payload 文本，原样作为 prompt。
        settings: 新鲜加载的 Agent Runner settings（配置装配的唯一事实源）。
        timeout_seconds: 单次评审调用的超时。

    Returns:
        JudgeInvokeOutcome: 进程失败映射为 timeout / provider_error 状态；
        成功时只带原始文本，schema 判定留给 core 解析层。
    """
    started = time.monotonic()
    preset = settings.presets.get(preset_name)
    if preset is None:
        return _error_outcome(f"预设 '{preset_name}' 在当前配置中已不存在", started)
    try:
        config = build_app_config_from_settings(settings)
        model_selection = resolve_model_selection(
            preset_name, config, model_override=None, effort_override=None
        )
        with tempfile.TemporaryDirectory(prefix="kc-judge-") as empty_cwd:
            result = run_agent_with_prompt(
                preset.agent,
                payload_text,
                Path(empty_cwd),
                create_process_runner(),
                config=config,
                capture_output=True,
                timeout_seconds=timeout_seconds,
                profile=AGENT_PROFILE_JUDGE,
                model_selection=model_selection,
                invocation_phase=agent_adapter.EVALUATION_PHASE,
            )
    except Exception as exc:  # noqa: BLE001 - 进程/配置错误都要留状态而不是抛出
        _logger.warning("评审预设 '%s' 调用异常: %s", preset_name, exc)
        return _error_outcome(exc, started)
    duration_seconds = time.monotonic() - started
    if getattr(result, "return_code", 1) != 0:
        failure_text = (result.stderr or result.stdout or "评审进程非零退出").strip()
        return _error_outcome(failure_text, started)
    return JudgeInvokeOutcome(
        error_status=None,
        response_text=(result.stdout or "").strip(),
        duration_seconds=duration_seconds,
        usage_summary=agent_adapter.usage_of(result),
    )


def _error_outcome(cause: Exception | str, started: float) -> JudgeInvokeOutcome:
    """把异常/失败文本映射为 timeout 或 provider_error 状态（超时不折算 0 分）。"""
    cause_text = str(cause)
    lowered_name = type(cause).__name__.lower() if isinstance(cause, Exception) else ""
    lowered_text = cause_text.lower()
    if "timeout" in lowered_name or "timed out" in lowered_text or "timeout" in lowered_text:
        status = EvaluationScoreStatus.timeout
    else:
        status = EvaluationScoreStatus.provider_error
    return JudgeInvokeOutcome(
        error_status=status,
        response_text=cause_text[:_ERROR_TEXT_LIMIT],
        duration_seconds=time.monotonic() - started,
        usage_summary=None,
    )

"""首次交互式使用时的 Agent 可用性提示（FR-4）。

判定完全复用 :mod:`backend.core.use_cases.agent_onboarding` 的可用性事实源
（``shutil.which``），本模块只做**呈现与一次性**：写机器级 marker 文件，保证
同一台机器上只提示一次，且**绝不阻断**当前命令。

与 shell 补全提示（:func:`backend.api.cli_completion.maybe_prompt_for_completion_setup`）
共用同一套门禁：非 TTY、补全协议、机器输出、``--help`` / ``--version`` 一律不提示，
避免把提示文字混进 agent 消费的 stdout/stderr 数据流。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from backend.api.cli_output import machine_output_requested
from backend.core.shared.models import product_identity

#: 补全协议环境变量名：click 正在跑补全时不能往 stdout side 写任何东西。
_COMPLETION_ENV_VAR = product_identity.COMPLETION_ENV_VAR_NAME
#: 一次性提示 marker 前缀（与补全提示同目录、同命名风格）。
_AGENT_SETUP_HINT_MARKER = ".agent-setup-hint-shown"


def _hint_marker_path() -> Path | None:
    """状态目录里的一次性 marker 路径；无法确定主目录时返回 ``None``。"""
    try:
        return product_identity.state_home() / _AGENT_SETUP_HINT_MARKER
    except (OSError, RuntimeError):
        return None


def _mark_hint_shown(marker_path: Path | None) -> None:
    """持久化提示状态；写入失败不能阻断 CLI。"""
    if marker_path is None:
        return
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("shown\n", encoding="utf-8")
    except OSError:
        return


def _hint_text(reason: str, agent_name: str | None, bin_name: str | None) -> str:
    """按判定结果给出点名式的提示文案（不猜、不替用户改配置）。"""
    if reason == "unavailable" and agent_name is not None:
        detail = f"'{agent_name}'" + (f" (bin '{bin_name}')" if bin_name else "")
        return (
            f"note: the configured agent {detail} is not installed on this machine. "
            "Run `kc agent setup` to choose an available one, or `kc agent list` to see them."
        )
    return (
        "note: no agent selection is configured for this machine yet. "
        "Run `kc agent setup` to pick one of the agents installed here."
    )


def maybe_prompt_for_agent_setup(raw_cli_arguments: Sequence[str]) -> None:
    """首次交互式使用时提示一次"先确认可用的 Agent"，永不阻断命令。

    Args:
        raw_cli_arguments: 用户传给 CLI 的原始参数（用于识别机器输出与补全协议）。
    """
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        return
    if _COMPLETION_ENV_VAR in os.environ or machine_output_requested(raw_cli_arguments):
        return
    if any(argument in {"-h", "--help", "-V", "--version"} for argument in raw_cli_arguments):
        return
    # agent 子命令本身就在处理这件事，不再叠一层提示。
    if raw_cli_arguments and raw_cli_arguments[0] in {"agent", "completion"}:
        return

    marker_path = _hint_marker_path()
    if marker_path is not None and marker_path.exists():
        return

    try:
        from backend.core.use_cases.agent_onboarding import resolve_setup_hint
        from backend.core.use_cases.agent_runner_factory import build_app_config

        hint = resolve_setup_hint(build_app_config())
    except Exception:  # noqa: BLE001 - 提示是附加价值，任何配置异常都不该影响本轮命令
        return
    if not hint.needed:
        return

    product_identity.emit_notice_once(
        _hint_text(hint.reason, hint.agent, hint.bin),
    )
    _mark_hint_shown(marker_path)


__all__ = ["maybe_prompt_for_agent_setup"]

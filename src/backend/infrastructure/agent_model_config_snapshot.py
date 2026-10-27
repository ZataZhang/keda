"""只读采集本机 Agent CLI 的默认模型配置，不修改启动参数与用户配置。"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import Any

from backend.core.shared.interfaces.agent_output_protocol import OutputRelayRequest
from backend.core.shared.models.agent_model_observation import (
    AGENT_MODEL_CONFIG_SNAPSHOT_ATTR,
    AgentModelConfigSnapshot,
    is_concrete_configured_model,
)
from backend.core.shared.models.agent_runner_execution import CommandResult

# 这些参数可能覆盖默认模型、配置源或续传旧会话；不猜测其合并与恢复语义。
_MODEL_OVERRIDE_FLAGS = frozenset(
    {
        "--model",
        "-m",
        "--settings",
        "--setting-sources",
        "--config",
        "--config-file",
        "-c",
        "--profile",
        "--resume",
        "--continue",
        "-r",
        "resume",
        "--ignore-user-config",
        "--settings-file",
        "--fallback-model",
        "--model-name",
        "--provider",
    }
)
_CLI_CONFIG_DIRS = {
    "claude": ".claude",
    "codebuddy": ".codebuddy",
    "qodercli": ".qoder",
    "qoder": ".qoder",
    "qodercn": ".qoder-cn",
}


def read_agent_model_config_snapshot(
    command: Sequence[str],
    cwd: Path,
) -> AgentModelConfigSnapshot | None:
    """在启动进程前读取白名单工具的默认模型，解析不确定时放弃推测。

    Args:
        command: 直接执行的 argv，shell/env 包装器不解析。
        cwd: 工具运行目录。

    Returns:
        仅含模型名、来源与别名标志的快照；未知工具、覆盖参数、损坏配置返回 None。
    """
    if not command:
        return None
    executable_name = Path(command[0]).name
    if any(
        part.split("=", 1)[0] in _MODEL_OVERRIDE_FLAGS
        or (part.startswith(("-m", "-c", "-r")) and not part.startswith("--"))
        or (executable_name == "codex" and part.startswith("-p") and not part.startswith("--"))
        for part in command[1:]
    ):
        return None
    try:
        if executable_name in _CLI_CONFIG_DIRS:
            if executable_name == "claude" and any(
                config_path.exists()
                for config_path in (
                    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
                    Path("/etc/claude-code/managed-settings.json"),
                )
            ):
                return None
            config_dir_name = _CLI_CONFIG_DIRS[executable_name]
            user_config_dir = (
                Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / config_dir_name)))
                if executable_name == "claude"
                else Path.home() / config_dir_name
            )
            if not user_config_dir.is_absolute():
                return None
            candidates = [user_config_dir / "settings.json"]
            # 与 cwd 到仓库根之间任意未处理的父级配置存在时放弃，不猜测项目发现规则。
            for parent_dir in cwd.resolve().parents:
                if parent_dir == Path.home() or parent_dir == Path("/"):
                    break
                if (parent_dir / config_dir_name / "settings.json").exists() or (
                    parent_dir / config_dir_name / "settings.local.json"
                ).exists():
                    return None
            candidates.extend(
                (
                    cwd / config_dir_name / "settings.json",
                    cwd / config_dir_name / "settings.local.json",
                )
            )
            selected_model: object = None
            selected_source = ""
            configured_env: dict[str, object] = {}
            for config_path in candidates:
                if not config_path.exists():
                    continue
                config_payload = json.loads(config_path.read_text(encoding="utf-8"))
                if not isinstance(config_payload, dict):
                    return None
                if "model" in config_payload:
                    selected_model = config_payload["model"]
                    selected_source = f"{executable_name}:{'user' if config_path == candidates[0] else 'project'}:{config_path.name}"
                env_payload = config_payload.get("env", {})
                if isinstance(env_payload, dict):
                    configured_env.update(env_payload)
            if isinstance(selected_model, dict) and executable_name in {
                "qodercli",
                "qoder",
                "qodercn",
            }:
                selected_model = selected_model.get("name")
            if executable_name == "claude":
                # 只观察模型变量，不读取或记录任何认证变量。
                inherited_model = os.environ.get("ANTHROPIC_MODEL")
                settings_model = configured_env.get("ANTHROPIC_MODEL")
                if inherited_model and settings_model and inherited_model != settings_model:
                    return None
                env_model = inherited_model or settings_model
                if env_model:
                    selected_model, selected_source = env_model, "claude:env:ANTHROPIC_MODEL"
        elif executable_name == "codex":
            config_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            if not config_root.is_absolute():
                return None
            if any(
                (parent / ".codex/config.toml").exists()
                for parent in (cwd, *cwd.parents)
                if parent != Path.home()
            ):
                return None
            config_path = config_root / "config.toml"
            config_payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
            if config_payload.get("profile"):
                return None
            selected_model, selected_source = config_payload.get("model"), "codex:user:config.toml"
        elif executable_name == "kimi":
            # 新旧 CLI 的默认目录不同；同时存在时不猜当前二进制使用哪一份。
            config_paths = [Path.home() / name / "config.toml" for name in (".kimi", ".kimi-code")]
            if os.environ.get("KIMI_CODE_HOME"):
                config_paths = [Path(os.environ["KIMI_CODE_HOME"]) / "config.toml"]
                if not config_paths[0].is_absolute():
                    return None
            existing_paths = [config_path for config_path in config_paths if config_path.exists()]
            if len(existing_paths) != 1 or any(key.startswith("KIMI_MODEL_") for key in os.environ):
                return None
            config_payload = tomllib.loads(existing_paths[0].read_text(encoding="utf-8"))
            default_model = config_payload.get("default_model")
            model_rows = config_payload.get("models", {})
            model_config = (
                model_rows.get(default_model, {})
                if isinstance(model_rows, dict) and isinstance(default_model, str)
                else {}
            )
            selected_model = model_config.get("model") if isinstance(model_config, dict) else None
            selected_source = "kimi:user:config.toml:models"
        else:
            return None
        if not isinstance(selected_model, str) or not selected_model.strip():
            return None
        model_id = selected_model.strip()
        # 限定为标识符，避免把意外配置内容或凭据写入观测账本。
        if len(model_id) > 200 or not all(
            character.isalnum() or character in "-_.:/[]" for character in model_id
        ):
            return None
        return AgentModelConfigSnapshot(
            model_id=model_id,
            source=selected_source,
            is_alias=not is_concrete_configured_model(model_id),
        )
    except (OSError, ValueError, TypeError):
        return None


def _observe_model_config_call(
    command: Sequence[str],
    cwd: Path,
    operation: Callable[[], CommandResult],
) -> CommandResult:
    """在执行前捕获配置，并通过正常结果或失败异常传递同一份快照。"""
    try:
        snapshot = read_agent_model_config_snapshot(command, cwd)
    except Exception:  # 旁路观测失败不得影响实际进程调用。
        snapshot = None
    try:
        command_result = operation()
    except BaseException as exc:
        if snapshot is not None:
            setattr(exc, AGENT_MODEL_CONFIG_SNAPSHOT_ATTR, snapshot)
        raise
    return replace(command_result, model_config_snapshot=snapshot)


def observe_relay_model_config(relay: Callable[..., CommandResult]) -> Callable[..., CommandResult]:
    """为输出协议中继统一添加默认模型旁路观测。

    Args:
        relay: 接受 OutputRelayRequest 的协议实例方法。

    Returns:
        保留原方法签名的观测包装器。
    """

    @wraps(relay)
    def observed_relay(protocol: object, request: OutputRelayRequest) -> CommandResult:
        return _observe_model_config_call(
            request.argv, request.cwd, lambda: relay(protocol, request)
        )

    return observed_relay


def observe_process_model_config(run: Callable[..., CommandResult]) -> Callable[..., CommandResult]:
    """仅为声明 output_protocol 的 Agent 进程采集默认模型，普通命令不读配置。

    Args:
        run: 进程执行器的实例方法。

    Returns:
        保留原方法签名的观测包装器。
    """

    @wraps(run)
    def observed_run(runner: object, command: Sequence[str], **kwargs: Any) -> CommandResult:
        if kwargs.get("output_protocol") is None:
            return run(runner, command, **kwargs)
        return _observe_model_config_call(
            command, kwargs["cwd"], lambda: run(runner, command, **kwargs)
        )

    return observed_run

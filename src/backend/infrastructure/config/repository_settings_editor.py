"""仓库本地配置（``.iar.toml``）的受限 Autopilot 写回实现。

round-trip 与原子替换**不再在本模块实现**：写回一律委托给
:func:`backend.infrastructure.config.toml_section_editor.update_toml_table_keys`
——它是仓库内唯一的 tomlkit round-trip / ``os.replace`` 原语，``config.toml``
（机器级）与 ``.iar.toml``（仓库级）共用同一份语义（见该模块 docstring）。

本模块只负责把写回面收敛到 ``[agent_runner.autopilot].enabled`` 单个布尔键，
并在委托前补上 PRD 要求的前置校验：

- 目标仓库必须**已有** ``.iar.toml``：缺文件时拒绝，不凭空创建配置文件；
- ``[agent_runner]`` 段必须存在，且**写前**就能通过 ``AgentRunnerLocalSettings``
  模型校验——只有写前合法，才能保证"只改一个布尔键之后仍然合法"，从而在不
  触碰原文件的前提下满足「完整加载校验、失败时原文件不变」；
- ``safety.auto_merge`` 是既有第二道危险动作门禁，不由本端口联动修改；
- 通用 TOML PATCH 会把写权限扩大到全部配置键，破坏上述双门禁边界，因此不提供。

与 ``P1-FEAT-20260918-110027-lifecycle-agent-matrix`` 的关系：该 PRD 先落地了
共享原语 ``toml_section_editor.update_toml_table_keys`` 与
``engines/agent_runner/lifecycle_editor.py``；本模块是"同一原语 + 自己的窄接口"
的第二个调用方，全仓仍然只有一份 tomlkit 写回实现。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from backend.infrastructure.config.agent_runner_settings import (
    IAR_REPOSITORY_CONFIG_FILENAME,
    AgentRunnerLocalSettings,
)
from backend.infrastructure.config.toml_section_editor import update_toml_table_keys

_AGENT_RUNNER_KEY = "agent_runner"
_AUTOPILOT_KEY = "autopilot"
_ENABLED_KEY = "enabled"

#: 写回目标表路径；``enabled`` 是这张表里唯一允许被本端口触碰的键。
_AUTOPILOT_TABLE_PATH: tuple[str, ...] = (_AGENT_RUNNER_KEY, _AUTOPILOT_KEY)


class RepositorySettingsEditError(ValueError):
    """仓库本地配置写回被拒绝：文件缺失、结构非法或模型校验不通过。"""


class TomlRepositoryAutopilotSettingsEditor:
    """``IRepositoryAutopilotSettingsEditor`` 端口的实现（鸭子类型）。

    只触碰 ``[agent_runner.autopilot].enabled``；文件中的注释、顺序、未知键与
    未知子表由共享原语在 round-trip 后逐字保留。
    """

    def config_source_path(self, repo_root_path: Path) -> Path:
        """返回目标仓库本地配置文件的绝对路径（文件不存在时也返回规范路径）。"""
        resolved_repo_root = Path(repo_root_path).expanduser().resolve()
        return resolved_repo_root / IAR_REPOSITORY_CONFIG_FILENAME

    def read_enabled(self, repo_root_path: Path) -> bool | None:
        """读取 ``autopilot.enabled``；文件缺失或键未设置时返回 ``None``。"""
        config_path = self.config_source_path(repo_root_path)
        if not config_path.is_file():
            return None
        document = self._parse(config_path)
        autopilot_table = document.get(_AGENT_RUNNER_KEY, {})
        if not isinstance(autopilot_table, dict):
            raise RepositorySettingsEditError(
                f"{config_path} 的 [{_AGENT_RUNNER_KEY}] 段不是合法的表。"
            )
        autopilot_section = autopilot_table.get(_AUTOPILOT_KEY, {})
        if not isinstance(autopilot_section, dict):
            raise RepositorySettingsEditError(
                f"[{_AGENT_RUNNER_KEY}.{_AUTOPILOT_KEY}] 不是合法的表。"
            )
        value = autopilot_section.get(_ENABLED_KEY)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise RepositorySettingsEditError(
                f"{config_path} 的 [{_AGENT_RUNNER_KEY}.{_AUTOPILOT_KEY}].{_ENABLED_KEY} 不是布尔值。"
            )
        return value

    def set_enabled(self, repo_root_path: Path, enabled: bool) -> None:
        """仅修改 ``[agent_runner.autopilot].enabled``（写回由共享原语原子替换）。"""
        config_path = self.config_source_path(repo_root_path)
        if not config_path.is_file():
            raise RepositorySettingsEditError(
                f"目标仓库没有 {IAR_REPOSITORY_CONFIG_FILENAME}（{config_path}），无法写回 Autopilot 设置。"
            )
        # 写前校验：现有配置必须已经合法，因此"只改一个 bool"之后必然仍合法。
        self._validate_existing_config(config_path)
        update_toml_table_keys(config_path, _AUTOPILOT_TABLE_PATH, {_ENABLED_KEY: enabled})

    # ── 内部实现 ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse(config_path: Path) -> dict:
        """读取并解析本地配置文件（不保留格式，仅供读取与校验）。"""
        try:
            raw_text = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RepositorySettingsEditError(f"读取 {config_path} 失败: {exc}") from exc
        try:
            return tomllib.loads(raw_text)
        except tomllib.TOMLDecodeError as exc:
            raise RepositorySettingsEditError(f"解析 {config_path} 失败: {exc}") from exc

    @staticmethod
    def _validate_existing_config(config_path: Path) -> None:
        """校验目标仓库现有配置：必须有 ``[agent_runner]`` 且能通过既有模型。"""
        parsed_data = TomlRepositoryAutopilotSettingsEditor._parse(config_path)
        agent_runner_section = parsed_data.get(_AGENT_RUNNER_KEY)
        if not isinstance(agent_runner_section, dict):
            raise RepositorySettingsEditError(
                f"{IAR_REPOSITORY_CONFIG_FILENAME} 缺少 [{_AGENT_RUNNER_KEY}] 段，无法写回 Autopilot 设置。"
            )
        try:
            AgentRunnerLocalSettings(**agent_runner_section)
        except ValidationError as exc:
            raise RepositorySettingsEditError(f"{config_path} 的配置未通过模型校验: {exc}") from exc

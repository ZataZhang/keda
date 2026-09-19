"""仓库本地配置（``.iar.toml``）的受限 Autopilot 写回实现。

这是全仓唯一的仓库级 ``.iar.toml`` round-trip / 原子替换原语（与
:class:`backend.infrastructure.config.registry_editor.TomlRegistryEditor` 针对
全局 ``config.toml`` 的职责相对）：后续需要写仓库本地配置的 PRD（例如
``P1-FEAT-20260918-110027-lifecycle-agent-matrix`` 的 agent 阶段配置）应复用
本模块的 tomlkit round-trip 与原子替换能力，各自只追加窄接口，不得再写第二份
注释保留 / ``os.replace`` 逻辑。

写回范围被刻意限制为 ``[agent_runner.autopilot].enabled`` 单个布尔键：

- ``safety.auto_merge`` 是既有第二道危险动作门禁，不由本端口联动修改；
- 通用 TOML PATCH 会把写权限扩大到全部配置键，破坏上述双门禁边界。

写入策略为「同目录临时文件 → 完整加载校验 → ``os.replace`` 原子替换」，
任何一步失败都不会破坏源文件。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import tomllib
from pathlib import Path

import tomlkit
from pydantic import ValidationError
from tomlkit.container import Container

from backend.infrastructure.config.agent_runner_settings import (
    IAR_REPOSITORY_CONFIG_FILENAME,
    AgentRunnerLocalSettings,
)

_logger = logging.getLogger(__name__)

_AUTOPILOT_KEY = "autopilot"
_ENABLED_KEY = "enabled"
_AGENT_RUNNER_KEY = "agent_runner"


class RepositorySettingsEditError(ValueError):
    """仓库本地配置写回被拒绝：文件缺失、结构非法或写后校验不通过。"""


class TomlRepositoryAutopilotSettingsEditor:
    """``IRepositoryAutopilotSettingsEditor`` 端口的 tomlkit 实现（鸭子类型）。

    只触碰 ``[agent_runner.autopilot].enabled``；文件中的注释、顺序、未知键与
    未知子表在 round-trip 后逐字保留。
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
        value = self._autopilot_table(document, create=False).get(_ENABLED_KEY)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise RepositorySettingsEditError(
                f"{config_path} 的 [agent_runner.autopilot].enabled 不是布尔值。"
            )
        return value

    def set_enabled(self, repo_root_path: Path, enabled: bool) -> None:
        """仅修改 ``[agent_runner.autopilot].enabled`` 并原子替换文件。"""
        config_path = self.config_source_path(repo_root_path)
        if not config_path.is_file():
            raise RepositorySettingsEditError(
                f"目标仓库没有 {IAR_REPOSITORY_CONFIG_FILENAME}（{config_path}），无法写回 Autopilot 设置。"
            )
        document = self._parse(config_path)
        autopilot_table = self._autopilot_table(document, create=True)
        autopilot_table[_ENABLED_KEY] = enabled

        dumped_text = tomlkit.dumps(document)
        self._validate_written_config(dumped_text, config_path)
        self._atomic_replace(config_path, dumped_text)

    # ── 内部实现 ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse(config_path: Path) -> tomlkit.TOMLDocument:
        """把本地配置文件解析为保留注释的 tomlkit 文档。"""
        try:
            raw_text = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RepositorySettingsEditError(f"读取 {config_path} 失败: {exc}") from exc
        try:
            return tomlkit.parse(raw_text)
        except Exception as exc:  # noqa: BLE001 - tomlkit 的解析异常类型不统一
            raise RepositorySettingsEditError(f"解析 {config_path} 失败: {exc}") from exc

    @staticmethod
    def _autopilot_table(document: tomlkit.TOMLDocument, *, create: bool) -> Container:
        """取得（或按需创建）``agent_runner.autopilot`` 表。

        ``create=False`` 时用于只读场景：任一层缺失都返回空 mapping，绝不新建；
        ``create=True`` 时按需补齐父表，已存在的表连同其注释整体复用。
        """
        agent_runner_table = document.get(_AGENT_RUNNER_KEY)
        if agent_runner_table is None or not isinstance(agent_runner_table, dict):
            if not create:
                return {}
            raise RepositorySettingsEditError(
                f"{IAR_REPOSITORY_CONFIG_FILENAME} 缺少 [{_AGENT_RUNNER_KEY}] 段，无法写回 Autopilot 设置。"
            )
        autopilot_table = agent_runner_table.get(_AUTOPILOT_KEY)
        if autopilot_table is None:
            if not create:
                return {}
            autopilot_table = tomlkit.table()
            agent_runner_table[_AUTOPILOT_KEY] = autopilot_table
        if not isinstance(autopilot_table, dict):
            raise RepositorySettingsEditError(
                f"[{_AGENT_RUNNER_KEY}.{_AUTOPILOT_KEY}] 不是合法的表。"
            )
        return autopilot_table

    @staticmethod
    def _validate_written_config(dumped_text: str, config_path: Path) -> None:
        """对写回结果做完整加载校验：语法 + 既有 pydantic 模型。

        只校验文本、不触碰源文件——这样即使模型校验失败，磁盘上的配置仍是原文。
        """
        try:
            parsed_data = tomllib.loads(dumped_text)
        except tomllib.TOMLDecodeError as exc:
            raise RepositorySettingsEditError(f"写回后的配置不是合法 TOML: {exc}") from exc

        agent_runner_section = parsed_data.get(_AGENT_RUNNER_KEY)
        if not isinstance(agent_runner_section, dict):
            raise RepositorySettingsEditError(f"写回后的配置缺少 [{_AGENT_RUNNER_KEY}] 段。")
        try:
            AgentRunnerLocalSettings(**agent_runner_section)
        except ValidationError as exc:
            raise RepositorySettingsEditError(
                f"写回后的配置未通过 {config_path} 的模型校验: {exc}"
            ) from exc

    @staticmethod
    def _atomic_replace(config_path: Path, dumped_text: str) -> None:
        """同目录临时文件 → 继承原文件权限 → ``os.replace`` 原子替换。"""
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f"{config_path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temp_path = Path(temp_file.name)
        try:
            with temp_file:
                temp_file.write(dumped_text)
            try:
                shutil.copymode(config_path, temp_path)
            except OSError:  # noqa: BLE001 - 权限对齐失败不应阻断写回
                _logger.debug("无法继承原配置权限: %s", config_path)
            os.replace(temp_path, config_path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:  # noqa: BLE001 - 清理失败不影响主异常语义
                _logger.debug("清理临时配置文件失败: %s", temp_path)
            raise

"""生命周期矩阵 / agent 回退顺序 / agent 标签的保留式 TOML 写回。

三处编辑各自写不同的段，但共用同一套"只动点名的键"语义（见
:mod:`backend.infrastructure.config.toml_section_editor`）：

- 生命周期矩阵 -> ``[agent_runner.lifecycle_agents]``（``config.toml`` 或
  仓库 ``.iar.toml``，由 scope 决定）；
- agent 回退顺序 -> ``[agent_runner.runner]`` 的 ``agent_fallback_order`` /
  ``max_agent_switches``（``config.toml``）；
- agent 标签 -> ``[agent_runner.agents.<name>]`` 的 ``label`` /
  ``label_color`` / ``label_description``（``config.toml``）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from backend.infrastructure.config.settings_sources import (
    IAR_REPOSITORY_CONFIG_FILENAME,
    resolve_config_toml_path,
)
from backend.infrastructure.config.toml_section_editor import update_toml_table_keys

_LIFECYCLE_AGENTS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "lifecycle_agents")
_RUNNER_TABLE_PATH: tuple[str, ...] = ("agent_runner", "runner")
_AGENTS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "agents")


class TomlLifecycleSettingsEditor:
    """面向生命周期相关段的保留式 TOML 编辑器。"""

    def __init__(self, config_path: str | Path) -> None:
        """初始化编辑器。

        Args:
            config_path: ``config.toml`` 或仓库 ``.iar.toml`` 的路径。
        """
        self._config_path = Path(config_path).expanduser()

    @property
    def config_path(self) -> Path:
        """返回本编辑器写入的目标文件路径。"""
        return self._config_path

    def update_lifecycle_agents(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.lifecycle_agents]``（值为 ``None`` 表示删键）。"""
        update_toml_table_keys(self._config_path, _LIFECYCLE_AGENTS_TABLE_PATH, values)

    def update_runner_keys(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.runner]`` 里点名的键（保留其余键）。"""
        update_toml_table_keys(self._config_path, _RUNNER_TABLE_PATH, values)

    def update_agent_label(self, agent_name: str, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.agents.<name>]`` 的标签字段（保留其余字段）。"""
        update_toml_table_keys(
            self._config_path,
            (*_AGENTS_TABLE_PATH, agent_name),
            values,
        )


def create_lifecycle_settings_editor(
    scope: str,
    repo_path: str | Path | None = None,
) -> TomlLifecycleSettingsEditor:
    """按 scope 构造编辑器：全局写 ``config.toml``，仓库写该仓库 ``.iar.toml``。

    Args:
        scope: ``global`` 或 ``repository``。
        repo_path: 仓库根目录（``scope=repository`` 时必填）。

    Returns:
        指向对应配置文件的 :class:`TomlLifecycleSettingsEditor`。

    Raises:
        ValueError: ``scope=repository`` 却未提供 ``repo_path``，或 scope 非法。
    """
    if scope == "repository":
        if repo_path is None:
            raise ValueError("repo_path is required for scope='repository'.")
        return TomlLifecycleSettingsEditor(Path(repo_path) / IAR_REPOSITORY_CONFIG_FILENAME)
    if scope == "global":
        return TomlLifecycleSettingsEditor(resolve_config_toml_path())
    raise ValueError(f"Unknown scope '{scope}'. Valid scopes: global, repository.")


__all__ = [
    "TomlLifecycleSettingsEditor",
    "create_lifecycle_settings_editor",
]

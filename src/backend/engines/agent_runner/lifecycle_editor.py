"""生命周期矩阵 / agent 回退顺序 / agent 标签的保留式 TOML 写回。

三处编辑各自写不同的段，但共用同一套"只动点名的键"语义（见
:mod:`backend.infrastructure.config.toml_section_editor`）：

- 生命周期矩阵 -> ``[agent_runner.lifecycle_agents]``（``config.toml`` 或
  仓库本地配置，由 scope 决定）；
- 生命周期预设绑定 -> ``[agent_runner.lifecycle_presets]``（同 scope 语义）；
- 模型预设定义 -> ``[agent_runner.presets.<name>]``（同 scope 语义）；
- 有序回退候选 -> ``[[agent_runner.runner.agent_fallback_candidates]]``
  （数组表，整体替换，走 :func:`update_toml_array_of_tables`）；
- agent 回退顺序 -> ``[agent_runner.runner]`` 的 ``agent_fallback_order`` /
  ``max_agent_switches``（``config.toml``）；
- agent 标签 -> ``[agent_runner.agents.<name>]`` 的 ``label`` /
  ``label_color`` / ``label_description``（``config.toml``）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from backend.core.shared.models import product_identity
from backend.infrastructure.config.settings_sources import resolve_config_toml_path
from backend.infrastructure.config.toml_section_editor import (
    update_toml_array_of_tables,
    update_toml_table_keys,
)

_LIFECYCLE_AGENTS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "lifecycle_agents")
_LIFECYCLE_PRESETS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "lifecycle_presets")
_PRESETS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "presets")
_RUNNER_TABLE_PATH: tuple[str, ...] = ("agent_runner", "runner")
_AGENTS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "agents")
_FALLBACK_CANDIDATES_KEY: str = "agent_fallback_candidates"


class TomlLifecycleSettingsEditor:
    """面向生命周期相关段的保留式 TOML 编辑器。"""

    def __init__(self, config_path: str | Path) -> None:
        """初始化编辑器。

        Args:
            config_path: ``config.toml`` 或仓库本地配置的路径。
        """
        self._config_path = Path(config_path).expanduser()

    @property
    def config_path(self) -> Path:
        """返回本编辑器写入的目标文件路径。"""
        return self._config_path

    def update_lifecycle_agents(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.lifecycle_agents]``（值为 ``None`` 表示删键）。"""
        update_toml_table_keys(self._config_path, _LIFECYCLE_AGENTS_TABLE_PATH, values)

    def update_lifecycle_presets(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.lifecycle_presets]`` 的阶段绑定（``None`` 删键）。"""
        update_toml_table_keys(self._config_path, _LIFECYCLE_PRESETS_TABLE_PATH, values)

    def update_agent_preset(self, preset_name: str, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.presets.<name>]`` 的字段（``None`` 删键，删空即删表）。

        作为 ``kc agent preset set`` 的 upsert 底座：给出的字段写入 / 覆盖，值为
        ``None`` 的字段删除；当三段字段（``agent`` / ``model`` /
        ``reasoning_effort``）都被删空时，:func:`update_toml_table_keys` 的剪枝会
        连带移除整个预设子表（预设名不再存在）。
        """
        update_toml_table_keys(
            self._config_path,
            (*_PRESETS_TABLE_PATH, preset_name),
            values,
        )

    def update_runner_keys(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.runner]`` 里点名的键（保留其余键）。"""
        update_toml_table_keys(self._config_path, _RUNNER_TABLE_PATH, values)

    def update_agent_fallback_candidates(self, entries: list[dict[str, object]]) -> None:
        """整体替换 ``[[agent_runner.runner.agent_fallback_candidates]]`` 有序数组表。

        Args:
            entries: 有序候选，每项形如 ``{"agent": str, "preset": str | None}``；
                ``preset`` 为 ``None`` 时省略该字段。空列表清空并移除该键。
        """
        update_toml_array_of_tables(
            self._config_path,
            _RUNNER_TABLE_PATH,
            _FALLBACK_CANDIDATES_KEY,
            entries,
        )

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
    """按 scope 构造编辑器：全局写 ``config.toml``，仓库写该仓库 ``.kedacode.toml``。

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
        return TomlLifecycleSettingsEditor(
            product_identity.effective_repository_config_path(Path(repo_path))
        )
    if scope == "global":
        return TomlLifecycleSettingsEditor(resolve_config_toml_path())
    raise ValueError(f"Unknown scope '{scope}'. Valid scopes: global, repository.")


__all__ = [
    "TomlLifecycleSettingsEditor",
    "create_lifecycle_settings_editor",
]

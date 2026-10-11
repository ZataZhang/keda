"""生命周期矩阵 / agent 回退顺序 / agent 标签的保留式 TOML 写回。

三处编辑各自写不同的段，但共用同一套"只动点名的键"语义（见
:mod:`backend.infrastructure.config.toml_section_editor`）：

- 生命周期矩阵 -> ``[agent_runner.lifecycle_agents]``（``config.toml`` 或
  仓库本地配置，由 scope 决定）；
- 生命周期预设绑定 -> ``[agent_runner.lifecycle_presets]``（同 scope 语义）；
- 模型预设定义 -> ``[agent_runner.presets.<name>]``（同 scope 语义）；
- 有序回退候选 -> ``[[agent_runner.runner.agent_fallback_candidates]]``
  （数组表，整体替换，与 ``max_agent_switches`` 同批落盘）；
- agent 回退顺序 -> ``[agent_runner.runner]`` 的 ``agent_fallback_order`` /
  ``max_agent_switches``（``config.toml``）；
- agent 标签 -> ``[agent_runner.agents.<name>]`` 的 ``label`` /
  ``label_color`` / ``label_description``（``config.toml``）。

一次用户操作若同时改多个段（预设 + 绑定、候选链 + 预算、多个 agent 的标签），
走本模块的 ``update_*`` **批量**方法：它们把这些段收敛成
:func:`backend.infrastructure.config.toml_section_editor.apply_toml_updates`
的**一次**读-改-写。逐段各写一次时，第二次写入失败会留下前半已落盘的半套配置；
收敛成一次落盘后，任一阶段校验不过都发生在写盘之前。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from backend.core.shared.models import product_identity
from backend.infrastructure.config.settings_sources import resolve_config_toml_path
from backend.infrastructure.config.toml_section_editor import (
    ArrayTableUpdate,
    TableKeyUpdate,
    apply_toml_updates,
    config_write_lock,
    update_toml_table_keys,
)

_LIFECYCLE_AGENTS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "lifecycle_agents")
_LIFECYCLE_PRESETS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "lifecycle_presets")
_PRESETS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "presets")
_RUNNER_TABLE_PATH: tuple[str, ...] = ("agent_runner", "runner")
_AGENTS_TABLE_PATH: tuple[str, ...] = ("agent_runner", "agents")
_FALLBACK_CANDIDATES_KEY: str = "agent_fallback_candidates"
_MAX_AGENT_SWITCHES_KEY: str = "max_agent_switches"

#: 删除一个预设 = 把它的三个字段全部删空（叶子表随之被剪掉）。
_PRESET_FIELD_NAMES: tuple[str, ...] = ("agent", "model", "reasoning_effort")


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

    @contextmanager
    def write_transaction(self) -> Iterator[None]:
        """把"读基线 → 校验 → 落盘"整段圈进本文件的写锁。

        ``update_*`` 方法自己持锁完成一次读-改-写，但**整段替换**（候选数组）的基线是
        调用方在锁外算出来的：并发写入者在那个窗口里加进去的候选会被本次替换原样抹掉。
        调用方用本上下文把基线读取与校验也放进去，就只剩一把锁覆盖完整的一次操作；
        同线程内后续调用 ``update_*`` 会重入放行，不会自死锁。

        Raises:
            ConfigLockBusyError: 有界等待内没拿到写锁（配置未被改动）。
            OSError: 锁文件无法创建或打开。
        """
        with config_write_lock(self._config_path):
            yield

    def update_lifecycle_agents(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.lifecycle_agents]``（值为 ``None`` 表示删键）。"""
        update_toml_table_keys(self._config_path, _LIFECYCLE_AGENTS_TABLE_PATH, values)

    def update_lifecycle_presets(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.lifecycle_presets]`` 的阶段绑定（``None`` 删键）。

        解绑要求该阶段在**当前层**确有绑定：本层没声明过的绑定删不出任何东西，
        报成功等于欺骗调用方（该阶段仍按上一层的绑定生效）。这类请求抛
        :class:`~backend.core.shared.models.config_edit.ConfigKeyMissingError`
        且不改文件；绑定新预设（值为预设名）不受该存在性要求约束。
        """
        apply_toml_updates(
            self._config_path,
            [
                TableKeyUpdate(
                    _LIFECYCLE_PRESETS_TABLE_PATH,
                    values,
                    presence="keys",
                )
            ],
        )

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

    def update_presets_and_bindings(
        self,
        *,
        preset_updates: Mapping[str, Mapping[str, object] | None],
        binding_updates: Mapping[str, object],
    ) -> None:
        """一次读-改-写同时落预设定义与阶段绑定（聚合设置页保存的写回面）。

        ``preset_updates`` 值为 ``None`` 表示删除该预设，并要求该预设在本层
        **已声明**（在仓库范围删除只有全局层声明的预设删不出任何东西，必须失败
        而非报成功）；``binding_updates`` 值为 ``None`` 的解绑同样要求本层有绑定。
        任一存在性校验不过 → 整批不落盘，配置文件保持原样。

        Args:
            preset_updates: 预设名 -> 字段映射（``None`` = 删除该预设）。
            binding_updates: 生命周期阶段 -> 预设名（``None`` = 删除本层绑定）。

        Raises:
            OSError: 读取或替换目标文件失败。
            ConfigDocumentInvalidError: 现有文件不是合法 TOML。
            ConfigKeyMissingError: 要删的预设 / 要解绑的阶段在本层不存在。
        """
        updates: list[TableKeyUpdate] = []
        for preset_name, preset_fields in preset_updates.items():
            table_path = (*_PRESETS_TABLE_PATH, preset_name)
            if preset_fields is None:
                updates.append(
                    TableKeyUpdate(
                        table_path,
                        dict.fromkeys(_PRESET_FIELD_NAMES),
                        presence="table",
                    )
                )
            else:
                updates.append(TableKeyUpdate(table_path, preset_fields))
        if binding_updates:
            updates.append(
                TableKeyUpdate(
                    _LIFECYCLE_PRESETS_TABLE_PATH,
                    binding_updates,
                    presence="keys",
                )
            )
        if updates:
            apply_toml_updates(self._config_path, updates)

    def update_runner_keys(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.runner]`` 里点名的键（保留其余键）。"""
        update_toml_table_keys(self._config_path, _RUNNER_TABLE_PATH, values)

    def update_fallback_candidates_with_budget(
        self,
        entries: Sequence[Mapping[str, object]],
        *,
        max_agent_switches: int,
    ) -> None:
        """一次落盘候选链**与**候选步数预算（保存这两者的唯一写回面）。

        候选数组与 ``max_agent_switches`` 是分开的两次 read-modify-write 时，
        第二次失败会留下"链已换、预算还是旧的"的半套配置。收敛成
        :func:`apply_toml_updates` 的一次读-改-写后，失败即整批不落盘。

        Args:
            entries: 有序候选，每项形如 ``{"agent": str, "preset": str | None}``；
                ``preset`` 为 ``None`` 时省略该字段，空列表清空该键。
            max_agent_switches: 写后的候选步数预算。

        Raises:
            OSError: 读取或替换目标文件失败。
            ConfigDocumentInvalidError: 现有文件不是合法 TOML。
        """
        apply_toml_updates(
            self._config_path,
            [
                ArrayTableUpdate(_RUNNER_TABLE_PATH, _FALLBACK_CANDIDATES_KEY, entries),
                TableKeyUpdate(_RUNNER_TABLE_PATH, {_MAX_AGENT_SWITCHES_KEY: max_agent_switches}),
            ],
        )

    def update_agent_labels(self, labels: Mapping[str, Mapping[str, object]]) -> None:
        """一次落盘多个 agent 的标签（逐 agent 各写一次会留下半套标签）。

        Args:
            labels: agent 名 -> 该 agent 的标签字段映射。

        Raises:
            OSError: 读取或替换目标文件失败。
            ConfigDocumentInvalidError: 现有文件不是合法 TOML。
        """
        apply_toml_updates(
            self._config_path,
            [
                TableKeyUpdate((*_AGENTS_TABLE_PATH, agent_name), label_fields)
                for agent_name, label_fields in labels.items()
            ],
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

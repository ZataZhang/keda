"""生命周期模型设置的跨引用一致性校验。"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from backend.core.shared.models.agent_model_preset import AgentModelPreset
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_KEYS,
    LifecycleAgentsConfig,
)
from backend.core.use_cases.agent_candidate_fallback import effective_fallback_candidates
from backend.core.use_cases.lifecycle_agents_console import (
    SCOPE_GLOBAL,
    SCOPE_REPOSITORY,
    LifecycleAgentsUpdateError,
)


def _presets_after_write(
    config: AppConfig,
    preset_updates: Mapping[str, AgentModelPreset | None],
) -> dict[str, AgentModelPreset]:
    """把本次预设改动并入（对象=upsert、``None``=删除），得到写后的预设清单。"""
    presets_after = dict(config.agent_presets)
    for preset_name, payload in preset_updates.items():
        if payload is None:
            presets_after.pop(preset_name, None)
        else:
            presets_after[preset_name] = payload
    return presets_after


def _bindings_after_write(
    config: AppConfig,
    scope: str,
    binding_updates: Mapping[str, str | None],
) -> LifecycleAgentsConfig:
    """把本次阶段绑定改动并入目标层（``None``=删键），得到写后的两层绑定视图。"""
    presets_config = config.lifecycle_presets
    global_layer = dict(presets_config.global_layer)
    repository_layer = dict(presets_config.repository_layer)
    target_layer = repository_layer if scope == SCOPE_REPOSITORY else global_layer
    for lifecycle_key, preset_name in binding_updates.items():
        if preset_name is None:
            target_layer.pop(lifecycle_key, None)
        else:
            target_layer[lifecycle_key] = preset_name
    return LifecycleAgentsConfig(
        global_layer=global_layer,
        repository_layer=repository_layer,
    )


def find_dangling_preset_references(config: AppConfig) -> list[dict[str, str]]:
    """列出该视图里引用了不存在预设的阶段绑定与回退候选（悬空引用）。

    写前拒绝与写后诊断共用同一判据：绑定 / 候选记录的预设名若不在 ``agent_presets``
    即为悬空，读取端（页面 / CLI）将无法解析其生效三元组。
    """
    presets_present = config.agent_presets
    dangling: list[dict[str, str]] = []
    for lifecycle_key in LIFECYCLE_AGENT_KEYS:
        bound_preset = config.lifecycle_presets.declared_value(lifecycle_key)
        if bound_preset is not None and bound_preset not in presets_present:
            dangling.append(
                {
                    "kind": "lifecycle_binding",
                    "location": f"lifecycle_presets.{lifecycle_key}",
                    "preset": bound_preset,
                }
            )
    for candidate in effective_fallback_candidates(config):
        if candidate.preset is not None and candidate.preset not in presets_present:
            dangling.append(
                {
                    "kind": "fallback_candidate",
                    "location": f"agent_fallback_candidates[{candidate.agent}]",
                    "preset": candidate.preset,
                }
            )
    return dangling


def _format_dangling_message(offenders: list[dict[str, str]], *, repo_id: str | None = None) -> str:
    """把悬空引用整理成可操作错误：点名受影响阶段 / 候选与预设，提示先解绑。"""
    scope_label = f"仓库 '{repo_id}'" if repo_id else "当前配置范围"
    details = "；".join(f"{item['location']} 仍引用预设 '{item['preset']}'" for item in offenders)
    return (
        f"无法删除预设：删除后在{scope_label}内仍有引用——{details}。"
        "请先解绑这些阶段 / 候选（或同批请求里一并解绑），再删除该预设。"
    )


def validate_lifecycle_settings_reference_integrity(
    config: AppConfig,
    scope: str,
    preset_updates: Mapping[str, AgentModelPreset | None],
    binding_updates: Mapping[str, str | None],
    *,
    repository_configs: Mapping[str, AppConfig] | None = None,
    repository_own_presets: Mapping[str, Mapping[str, AgentModelPreset]] | None = None,
    global_presets: Mapping[str, AgentModelPreset] | None = None,
) -> None:
    """写前拒绝悬空：删除预设时若其仍被写后可见的绑定 / 候选引用则报错、不改文件。

    绑定校验只保证**本批新增**绑定指向存在的预设，这里兜住未被本批触及的**既有**引用：

    - 目标层：并入本次预设与绑定改动后复查是否仍指向被删预设。``scope=repository``
      的删除只移除本层预设表，全局同名预设写后仍解析——``global_presets`` 提供时把
      这些名字保留进写后集合，"删除仓库覆盖、回到全局预设"不被误拒，错误信息也不
      会与事实不符。
    - 跨层（仅 ``scope=global`` 删除）：全局预设若被某仓库自身绑定引用、而该仓库并未
      自带同名预设，删全局会让该仓库视图悬空；逐一按仓库写后预设集合复查并拒绝。
      仓库自带同名预设（哪怕与全局字节相同）则仓库副本继续生效、删全局安全。

    Args:
        repository_configs: ``scope=global`` 删除时参与跨层复查的仓库合并配置
            （repo_id -> 该仓库"全局 + 仓库层"合并后的 AppConfig）。
        repository_own_presets: 各仓库**自身声明**的预设（repo_id -> 预设名 ->
            三元组），来自各仓库本地配置文件。提供时以"删后全局预设 + 仓库自有
            预设"重建写后可用集合——仓库自带的同名副本（含与全局字节相同的副本）
            留在集合内，绑定仍可解析；未提供（或某仓库缺失）时回退到合并视图近似
            （合并视图里与被删全局预设字节相同的条目视为纯继承、随删除消失）。
        global_presets: 独立加载的全局层预设清单（``scope=repository`` 删除时
            必填语义才有差别）：删除的预设名若仍在全局层声明，写后引用继续解析到
            全局预设，不算悬空；未提供时按合并视图近似（名字随删除消失）。

    Raises:
        LifecycleAgentsUpdateError: 存在指向被删预设的悬空引用（信息含具体阶段 / 候选与预设名）。
    """
    deleted_presets = {
        preset_name for preset_name, payload in preset_updates.items() if payload is None
    }
    if not deleted_presets:
        return

    presets_after_write = _presets_after_write(config, preset_updates)
    if scope == SCOPE_REPOSITORY and global_presets is not None:
        # 仓库层删除只移除本层预设表；全局同名预设写后仍解析，引用不算悬空。
        for preset_name in deleted_presets:
            global_preset = global_presets.get(preset_name)
            if global_preset is not None:
                presets_after_write[preset_name] = global_preset
    target_after = dataclasses.replace(
        config,
        agent_presets=presets_after_write,
        lifecycle_presets=_bindings_after_write(config, scope, binding_updates),
    )
    offenders = [
        reference
        for reference in find_dangling_preset_references(target_after)
        if reference["preset"] in deleted_presets
    ]
    if offenders:
        raise LifecycleAgentsUpdateError(_format_dangling_message(offenders))

    if scope != SCOPE_GLOBAL or not repository_configs:
        return
    # 全局写只改全局文件的绑定层；每个仓库继承"更新后的全局绑定层"，仓库自身绑定层不变。
    # 据此判断"删全局预设"后仓库视图是否仍指向被删预设（仓库自带的 repository 层绑定、或本批
    # 未解绑的全局继承绑定，都算悬空）。
    global_layer_after = _bindings_after_write(config, SCOPE_GLOBAL, binding_updates).global_layer
    global_presets_before = config.agent_presets
    global_presets_after = target_after.agent_presets
    for repo_id, repo_config in repository_configs.items():
        own_presets = (
            repository_own_presets.get(repo_id) if repository_own_presets is not None else None
        )
        if own_presets is not None:
            # 以"删后全局预设 + 仓库自有声明"重建写后可用预设（与合并语义同序：
            # 仓库层同键整体替换全局、新键追加）。仓库自带的同名副本哪怕与全局
            # 字节相同也留在集合里，绑定仍可解析——不再被误当成纯继承而误拒删除。
            repo_presets_after = dict(global_presets_after)
            repo_presets_after.update(own_presets)
        else:
            # 无仓库自有声明信息时的近似：合并视图里与被删全局预设字节相同的条目
            # 视为纯继承（随全局删除消失）。
            repo_presets_after = {
                preset_name: preset
                for preset_name, preset in repo_config.agent_presets.items()
                if not (
                    preset_name in deleted_presets
                    and global_presets_before.get(preset_name) == preset
                )
            }
        repo_bindings_after = LifecycleAgentsConfig(
            global_layer=global_layer_after,
            repository_layer=dict(repo_config.lifecycle_presets.repository_layer),
        )
        repo_after = dataclasses.replace(
            repo_config,
            agent_presets=repo_presets_after,
            lifecycle_presets=repo_bindings_after,
        )
        repo_offenders = [
            reference
            for reference in find_dangling_preset_references(repo_after)
            if reference["preset"] in deleted_presets
        ]
        if repo_offenders:
            raise LifecycleAgentsUpdateError(
                _format_dangling_message(repo_offenders, repo_id=repo_id)
            )

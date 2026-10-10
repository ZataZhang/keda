"""生命周期 Agent 矩阵 / agent 回退顺序 / agent 标签 / PRD 覆盖 的 console API。

三层各自写各自的文件：

- 全局层（统一设置页选「全局」）-> ``config.toml``；
- 仓库层（统一设置页选「仓库」，Backlog 仓库行齿轮直达该页）-> 该仓库 ``.kedacode.toml``；
- PRD 层（PRD 原文页）-> 该 PRD 文件头部 ``lifecycle_agents`` 块。

路由层只做 HTTP 映射与 4xx 转换，生效值计算与入参校验收敛在 core 用例
:mod:`backend.core.use_cases.lifecycle_agents_console`。
"""

from __future__ import annotations

import base64
import dataclasses
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.shared.models.agent_model_preset import AgentModelPreset
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.shared.models.lifecycle_agent import LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS
from backend.core.use_cases.agent_runner_factory import (
    build_app_config_from_settings,
    collect_repository_own_presets,
    create_lifecycle_settings_editor,
    load_fresh_agent_runner_settings,
    resolve_repository_targets_with_diagnostics,
)
from backend.core.use_cases.lifecycle_agent_resolution import (
    parse_prd_lifecycle_overrides,
    upsert_prd_lifecycle_overrides,
)
from backend.core.use_cases.lifecycle_agents_console import (
    SCOPE_GLOBAL,
    SCOPE_REPOSITORY,
    LifecycleAgentsUpdateError,
    build_agent_fallback_order_view,
    build_agent_labels_view,
    build_fallback_candidates_view,
    build_lifecycle_agents_view,
    build_lifecycle_settings_view,
    validate_agent_fallback_order_update,
    validate_agent_labels_update,
    validate_fallback_candidates_update,
    validate_lifecycle_agents_update,
    validate_lifecycle_preset_binding_update,
    validate_lifecycle_settings_reference_integrity,
    validate_preset_update,
)
from backend.core.use_cases.prd_content_reader import (
    PrdContentError,
    read_prd_content,
    write_prd_content,
)

router = APIRouter(tags=["agent-runner-lifecycle-agents"])


def _resolve_contexts() -> list[RepositoryRunContext]:
    """加载最新 settings 并解析出全部启用的仓库上下文。"""
    settings = load_fresh_agent_runner_settings()
    contexts, _failures = resolve_repository_targets_with_diagnostics(settings)
    return contexts


def _context_for(repo_id: str) -> RepositoryRunContext:
    """按 repo_id 找到仓库上下文；不存在时报 400。"""
    for context in _resolve_contexts():
        if context.repo_id == repo_id:
            return context
    raise HTTPException(status_code=400, detail=f"仓库 '{repo_id}' 不存在或未启用。")


def _global_config() -> AppConfig:
    """构建仅含全局层的应用配置。"""
    return build_app_config_from_settings(load_fresh_agent_runner_settings())


def _decode_prd_path(encoded_path: str) -> str:
    """解码 URL-safe base64 编码的 PRD 相对路径。"""
    try:
        return base64.urlsafe_b64decode(encoded_path.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"PRD 路径编码非法: {exc}") from exc


def _select_scope_config(scope: str, repo_id: str | None) -> tuple[AppConfig, Any]:
    """按 scope 返回（用于计算生效值的配置, 用于写回的编辑器）。"""
    if scope == SCOPE_GLOBAL:
        return _global_config(), create_lifecycle_settings_editor(SCOPE_GLOBAL)
    if scope == SCOPE_REPOSITORY:
        if not repo_id:
            raise HTTPException(status_code=400, detail="scope=repository 需要 repo_id。")
        context = _context_for(repo_id)
        return context.config, create_lifecycle_settings_editor(SCOPE_REPOSITORY, context.repo_path)
    raise HTTPException(status_code=400, detail=f"未知 scope '{scope}'。")


# ─────────────────────────────────────────────────────────────────────────────
# 生命周期 Agent 矩阵
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agent-runner/lifecycle-agents")
def get_lifecycle_agents(scope: str = SCOPE_GLOBAL, repo_id: str | None = None) -> dict:
    """返回某视角下的生命周期矩阵生效视图（每键带来源层标注）。"""
    if scope == SCOPE_GLOBAL:
        config = _global_config()
        return build_lifecycle_agents_view(config, scope=SCOPE_GLOBAL)
    if scope != SCOPE_REPOSITORY:
        raise HTTPException(status_code=400, detail=f"未知 scope '{scope}'。")
    if not repo_id:
        raise HTTPException(status_code=400, detail="scope=repository 需要 repo_id。")
    context = _context_for(repo_id)
    return build_lifecycle_agents_view(context.config, scope=SCOPE_REPOSITORY, repo_id=repo_id)


class UpdateLifecycleAgentsRequest(BaseModel):
    """生命周期矩阵写回请求体。"""

    scope: str = Field(default=SCOPE_GLOBAL, pattern="^(global|repository)$")
    repo_id: str | None = None
    values: dict[str, str | None] = Field(default_factory=dict)


@router.put("/agent-runner/lifecycle-agents")
def update_lifecycle_agents(request: UpdateLifecycleAgentsRequest) -> dict:
    """保留式写回矩阵：只写请求里显式给出的键（``null`` 表示删除该键）。"""
    config, editor = _select_scope_config(request.scope, request.repo_id)
    try:
        normalized_values = validate_lifecycle_agents_update(request.values, config)
    except LifecycleAgentsUpdateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        editor.update_lifecycle_agents(normalized_values)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"配置写入失败: {exc}") from exc
    # 写后重读，返回与磁盘一致的生效视图。
    refreshed_config, _editor = _select_scope_config(request.scope, request.repo_id)
    return build_lifecycle_agents_view(
        refreshed_config, scope=request.scope, repo_id=request.repo_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# 聚合生命周期设置（矩阵 + 预设 + 回退候选）
# ─────────────────────────────────────────────────────────────────────────────


def _aggregate_view(config: AppConfig, scope: str, repo_id: str | None) -> dict:
    """构建聚合视图；``scope=repository`` 时带上独立加载的全局配置以区分预设来源层。"""
    global_config = _global_config() if scope == SCOPE_REPOSITORY else None
    return build_lifecycle_settings_view(
        config, scope=scope, repo_id=repo_id, global_config=global_config
    )


@router.get("/agent-runner/lifecycle-settings")
def get_lifecycle_settings(scope: str = SCOPE_GLOBAL, repo_id: str | None = None) -> dict:
    """返回某视角下的聚合生命周期设置视图（九阶段三元组 + 预设 + 回退候选）。"""
    config, _editor = _select_scope_config(scope, repo_id)
    return _aggregate_view(config, scope, repo_id)


class PresetPayload(BaseModel):
    """单个命名预设的 upsert 字段（``model`` / ``reasoning_effort`` 为 ``None`` 表示不设置）。"""

    agent: str
    model: str | None = None
    reasoning_effort: str | None = None


class UpdateLifecycleSettingsRequest(BaseModel):
    """聚合写回请求体：只提交用户改动过的预设与阶段绑定。

    ``presets`` 值给对象表示 upsert、给 ``null`` 表示删除该预设；``bindings`` 是
    阶段 -> 预设名的期望增量（``null`` 删除当前层绑定）。同一次提交可既新建预设又
    把某阶段绑上去——校验时把本次预设并入工作副本，保证绑定能引用到同批新预设。
    """

    scope: str = Field(default=SCOPE_GLOBAL, pattern="^(global|repository)$")
    repo_id: str | None = None
    presets: dict[str, PresetPayload | None] = Field(default_factory=dict)
    bindings: dict[str, str | None] = Field(default_factory=dict)


@router.patch("/agent-runner/lifecycle-settings")
def update_lifecycle_settings(request: UpdateLifecycleSettingsRequest) -> dict:
    """先完整校验（含同批新建预设的引用），再 sparse 写回预设与阶段绑定。"""
    config, editor = _select_scope_config(request.scope, request.repo_id)

    # 工作副本：把本次预设改动并入，使绑定校验能看到同批新建的预设（不落盘）。
    merged_presets = dict(config.agent_presets)
    preset_updates: dict[str, AgentModelPreset | None] = {}
    for raw_preset_name, payload in request.presets.items():
        # 预设名在 API 边界归一化：校验、写回键与绑定引用必须用同一个名字，
        # 否则能写出一个绑定永远解析不到的带空白预设。
        preset_name = raw_preset_name.strip()
        if not preset_name or preset_name in preset_updates:
            raise HTTPException(
                status_code=422,
                detail=f"预设名非法或在本次请求里重复：'{raw_preset_name}'。",
            )
        if payload is None:
            merged_presets.pop(preset_name, None)
            preset_updates[preset_name] = None
        else:
            preset_updates[preset_name] = AgentModelPreset(
                agent=payload.agent,
                model=payload.model,
                reasoning_effort=payload.reasoning_effort,
            )
            merged_presets[preset_name] = preset_updates[preset_name]
    working_config = dataclasses.replace(config, agent_presets=merged_presets)

    try:
        normalized_presets: dict[str, dict[str, str | None] | None] = {}
        for raw_preset_name, payload in request.presets.items():
            preset_name = raw_preset_name.strip()
            if payload is None:
                normalized_presets[preset_name] = None
                continue
            normalized_presets[preset_name] = validate_preset_update(
                preset_name, payload.model_dump(), working_config
            )
        normalized_bindings = validate_lifecycle_preset_binding_update(
            request.bindings, working_config
        )
        # 删除预设前先确认不会留下悬空引用：目标层内未被本次解绑触及的既有绑定 / 候选，
        # 以及（全局删除时）仍引用该全局预设、自身却未自带同名覆盖的仓库绑定，都会让读取端
        # 无法解析生效值。此类操作必须失败且不改文件（FR-7），而非写成功后让页面 / CLI 崩。
        deleted_names = {name for name, payload in preset_updates.items() if payload is None}
        repository_configs: dict[str, AppConfig] | None = None
        repository_own_presets: dict[str, dict[str, AgentModelPreset]] | None = None
        if deleted_names and request.scope == SCOPE_GLOBAL:
            repository_configs = {
                context.repo_id: context.config for context in _resolve_contexts()
            }
            # 各仓库自身声明的预设（含与全局字节相同的副本）：让校验能区分"纯继承
            # 全局"与"仓库自带同名副本"，后者不阻挡全局删除。
            repository_own_presets = collect_repository_own_presets(
                load_fresh_agent_runner_settings()
            )
        validate_lifecycle_settings_reference_integrity(
            config,
            request.scope,
            preset_updates,
            normalized_bindings,
            repository_configs=repository_configs,
            repository_own_presets=repository_own_presets,
        )
    except LifecycleAgentsUpdateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        for preset_name, normalized_preset in normalized_presets.items():
            if normalized_preset is None:
                editor.update_agent_preset(
                    preset_name, {"agent": None, "model": None, "reasoning_effort": None}
                )
            else:
                editor.update_agent_preset(preset_name, normalized_preset)
        if normalized_bindings:
            editor.update_lifecycle_presets(normalized_bindings)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"配置写入失败: {exc}") from exc

    refreshed_config, _editor = _select_scope_config(request.scope, request.repo_id)
    return _aggregate_view(refreshed_config, request.scope, request.repo_id)


# ─────────────────────────────────────────────────────────────────────────────
# agent 回退顺序
# ─────────────────────────────────────────────────────────────────────────────


def _fallback_order_config(repo_id: str | None) -> AppConfig:
    """回退顺序视图所用的配置：给了 repo_id 用合并配置，否则用全局。"""
    if repo_id:
        return _context_for(repo_id).config
    return _global_config()


@router.get("/agent-runner/agent-fallback-order")
def get_agent_fallback_order(repo_id: str | None = None) -> dict:
    """返回 ``[agent_runner.runner]`` 的跨 agent 回退顺序与最大切换次数。"""
    return build_agent_fallback_order_view(_fallback_order_config(repo_id))


class UpdateFallbackOrderRequest(BaseModel):
    """agent 回退顺序写回请求体。"""

    agent_fallback_order: list[str] = Field(default_factory=list)
    max_agent_switches: int = Field(default=2, ge=0)
    repo_id: str | None = None


@router.put("/agent-runner/agent-fallback-order")
def update_agent_fallback_order(request: UpdateFallbackOrderRequest) -> dict:
    """写回 ``[agent_runner.runner]`` 的 ``agent_fallback_order`` / ``max_agent_switches``。

    这两个键是**机器级**配置（PRD FR-10：Settings 页编辑的就是全局 ``config.toml``），
    因此无论 ``repo_id`` 是否给出，写入目标恒为 ``config.toml``；``repo_id`` 只用于
    选取校验视角（仓库级注册的 agent 也会被认可）。
    """
    config = _fallback_order_config(request.repo_id)
    try:
        normalized_order, normalized_switches = validate_agent_fallback_order_update(
            request.agent_fallback_order, request.max_agent_switches, config
        )
    except LifecycleAgentsUpdateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    editor = create_lifecycle_settings_editor(SCOPE_GLOBAL)
    try:
        editor.update_runner_keys(
            {
                "agent_fallback_order": normalized_order,
                "max_agent_switches": normalized_switches,
            }
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"配置写入失败: {exc}") from exc
    return build_agent_fallback_order_view(_fallback_order_config(request.repo_id))


class FallbackCandidateEntry(BaseModel):
    """单个回退候选：agent + 可选的同 agent 预设。"""

    agent: str = Field(min_length=1)
    preset: str | None = None


class UpdateFallbackCandidatesRequest(BaseModel):
    """有序回退候选写回请求体（完整期望数组）。"""

    candidates: list[FallbackCandidateEntry] = Field(default_factory=list)
    max_agent_switches: int = Field(default=2, ge=0)
    repo_id: str | None = None


@router.get("/agent-runner/agent-fallback-candidates")
def get_agent_fallback_candidates(repo_id: str | None = None) -> dict:
    """返回 ``[[agent_runner.runner.agent_fallback_candidates]]`` 有序候选视图。"""
    return build_fallback_candidates_view(_fallback_order_config(repo_id))


@router.put("/agent-runner/agent-fallback-candidates")
def update_agent_fallback_candidates(request: UpdateFallbackCandidatesRequest) -> dict:
    """整体替换有序回退候选数组并写回候选步数预算。

    与 ``agent_fallback_order`` 同理：候选是**机器级**配置，写入目标恒为全局
    ``config.toml``；``repo_id`` 只用于选取校验视角（认可仓库级注册的 agent 与预设）。
    """
    config = _fallback_order_config(request.repo_id)
    candidate_entries = [entry.model_dump() for entry in request.candidates]
    try:
        normalized_candidates, normalized_switches = validate_fallback_candidates_update(
            candidate_entries, request.max_agent_switches, config
        )
    except LifecycleAgentsUpdateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    editor = create_lifecycle_settings_editor(SCOPE_GLOBAL)
    try:
        editor.update_agent_fallback_candidates(normalized_candidates)
        editor.update_runner_keys({"max_agent_switches": normalized_switches})
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"配置写入失败: {exc}") from exc
    return build_fallback_candidates_view(_fallback_order_config(request.repo_id))


# ─────────────────────────────────────────────────────────────────────────────
# agent 标签
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agent-runner/agent-labels")
def get_agent_labels(repo_id: str | None = None) -> dict:
    """返回各已注册 agent 的路由标签名 / 颜色 / 描述。"""
    return {"labels": build_agent_labels_view(_fallback_order_config(repo_id))}


class AgentLabelEntry(BaseModel):
    """单个 agent 的标签配置。"""

    agent: str = Field(min_length=1)
    label: str = Field(min_length=1)
    label_color: str = ""
    label_description: str = ""


class UpdateAgentLabelsRequest(BaseModel):
    """agent 标签写回请求体。"""

    labels: list[AgentLabelEntry] = Field(default_factory=list)
    repo_id: str | None = None


@router.put("/agent-runner/agent-labels")
def update_agent_labels(request: UpdateAgentLabelsRequest) -> dict:
    """保留式写回各 agent 注册块的 ``label`` / ``label_color`` / ``label_description``。

    与回退顺序同理：agent 路由标签是**机器级**配置（PRD FR-12 的「Agent 标签设置」在
    Settings 页），写入目标恒为 ``config.toml``；``repo_id`` 只用于选取校验视角。
    """
    config = _fallback_order_config(request.repo_id)
    label_entries = [entry.model_dump() for entry in request.labels]
    try:
        normalized_labels = validate_agent_labels_update(label_entries, config)
    except LifecycleAgentsUpdateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    editor = create_lifecycle_settings_editor(SCOPE_GLOBAL)
    try:
        for agent_name, label_fields in normalized_labels.items():
            editor.update_agent_label(agent_name, label_fields)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"配置写入失败: {exc}") from exc
    return {"labels": build_agent_labels_view(_fallback_order_config(request.repo_id))}


# ─────────────────────────────────────────────────────────────────────────────
# PRD 级覆盖
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agent-runner/backlog/prds/{encoded_path}/agent-overrides")
def get_prd_agent_overrides(encoded_path: str, repo_id: str) -> dict:
    """读取某 PRD 文件头部的 ``lifecycle_agents`` 覆盖。

    只呈递 :data:`LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS` 里的行——``planner`` 没有
    PRD 消费点，UI 不提供（写回同样拒绝）。
    """
    prd_path = _decode_prd_path(encoded_path)
    context = _context_for(repo_id)
    try:
        prd_text = read_prd_content(context.repo_path, prd_path)
    except PrdContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        overrides = parse_prd_lifecycle_overrides(prd_text, prd_path=prd_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    inherited_view = build_lifecycle_agents_view(
        context.config, scope=SCOPE_REPOSITORY, repo_id=repo_id
    )
    return {
        "repo_id": repo_id,
        "prd_path": prd_path,
        "overrides": overrides,
        "agents": list(context.config.agents),
        "lifecycles": [
            lifecycle_row
            for lifecycle_row in inherited_view["lifecycles"]
            if lifecycle_row["key"] in LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS
        ],
        # 与矩阵视图同源的分组：PRD 覆盖抽屉沿用同一分组渲染（仍不提供 planner 行）。
        "entry_groups": inherited_view["entry_groups"],
    }


class UpdatePrdOverridesRequest(BaseModel):
    """PRD 覆盖写回请求体（完整期望集合）。"""

    repo_id: str = Field(min_length=1)
    overrides: dict[str, str | None] = Field(default_factory=dict)


@router.patch("/agent-runner/backlog/prds/{encoded_path}/agent-overrides")
def update_prd_agent_overrides(encoded_path: str, request: UpdatePrdOverridesRequest) -> dict:
    """把 PRD 头部覆盖块整体重写为请求里的期望集合（``null`` 表示移除该项）。"""
    prd_path = _decode_prd_path(encoded_path)
    unsupported_keys = sorted(
        key for key in request.overrides if key not in LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS
    )
    if unsupported_keys:
        raise HTTPException(
            status_code=422,
            detail=(
                f"PRD 覆盖不支持以下生命周期键: {', '.join(unsupported_keys)}。"
                f"可用键: {', '.join(LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS)}。"
            ),
        )
    context = _context_for(request.repo_id)
    try:
        prd_text = read_prd_content(context.repo_path, prd_path)
    except PrdContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        # 写回前先校验并规范化（小写、去空白），保证文件里的取值与解析函数的期望一致。
        normalized_overrides = validate_lifecycle_agents_update(
            {
                lifecycle_key: value
                for lifecycle_key, value in request.overrides.items()
                if value is not None
            },
            context.config,
        )
        desired_overrides = {
            lifecycle_key: value
            for lifecycle_key, value in normalized_overrides.items()
            if value is not None
        }
        updated_text = upsert_prd_lifecycle_overrides(prd_text, desired_overrides)
    except (LifecycleAgentsUpdateError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        write_prd_content(context.repo_path, prd_path, updated_text)
    except PrdContentError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "repo_id": request.repo_id,
        "prd_path": prd_path,
        "overrides": parse_prd_lifecycle_overrides(updated_text, prd_path=prd_path),
    }


__all__ = ["router"]

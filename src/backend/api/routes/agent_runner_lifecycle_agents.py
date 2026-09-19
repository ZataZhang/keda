"""生命周期 Agent 矩阵 / agent 回退顺序 / agent 标签 / PRD 覆盖 的 console API。

三层各自写各自的文件：

- 全局层（Settings 页）-> ``config.toml``；
- 仓库层（Roadmap 仓库行齿轮）-> 该仓库 ``.iar.toml``；
- PRD 层（PRD 原文页）-> 该 PRD 文件头部 ``lifecycle_agents`` 块。

路由层只做 HTTP 映射与 4xx 转换，生效值计算与入参校验收敛在 core 用例
:mod:`backend.core.use_cases.lifecycle_agents_console`。
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases.agent_runner_factory import (
    build_app_config_from_settings,
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
    build_lifecycle_agents_view,
    validate_agent_fallback_order_update,
    validate_agent_labels_update,
    validate_lifecycle_agents_update,
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


@router.get("/agent-runner/roadmap/prds/{encoded_path}/agent-overrides")
def get_prd_agent_overrides(encoded_path: str, repo_id: str) -> dict:
    """读取某 PRD 文件头部的 ``lifecycle_agents`` 覆盖。"""
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
    return {
        "repo_id": repo_id,
        "prd_path": prd_path,
        "overrides": overrides,
        "agents": list(context.config.agents),
        "lifecycles": build_lifecycle_agents_view(
            context.config, scope=SCOPE_REPOSITORY, repo_id=repo_id
        )["lifecycles"],
    }


class UpdatePrdOverridesRequest(BaseModel):
    """PRD 覆盖写回请求体（完整期望集合）。"""

    repo_id: str = Field(min_length=1)
    overrides: dict[str, str | None] = Field(default_factory=dict)


@router.patch("/agent-runner/roadmap/prds/{encoded_path}/agent-overrides")
def update_prd_agent_overrides(encoded_path: str, request: UpdatePrdOverridesRequest) -> dict:
    """把 PRD 头部覆盖块整体重写为请求里的期望集合（``null`` 表示移除该项）。"""
    prd_path = _decode_prd_path(encoded_path)
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

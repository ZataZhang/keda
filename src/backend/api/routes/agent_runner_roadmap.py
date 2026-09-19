"""Roadmap API for PRD orchestration.

Provides read endpoints for PRD state/dependencies and write endpoints for
single/global start actions.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from backend.core.shared.interfaces.runner_console import AuditEntry, IRoadmapStore
from backend.core.shared.models.roadmap import (
    RoadmapDependency,
    RoadmapDependencyKind,
    RoadmapPrd,
    RoadmapSettingsEntry,
)
from backend.core.use_cases.agent_runner_factory import (
    create_github_client,
    create_process_runner,
    create_process_supervisor,
    create_repository_autopilot_settings_editor,
    create_roadmap_store,
    load_fresh_agent_runner_settings,
    resolve_console_spawn_cwd,
    resolve_repository_targets_with_diagnostics,
)
from backend.core.use_cases.prd_content_reader import PrdContentError, read_prd_content
from backend.core.use_cases.roadmap_actions import (
    RoadmapActionError,
    get_or_create_roadmap_settings,
    start_global_roadmap,
    start_prd,
    stop_global_roadmap,
)
from backend.core.use_cases.roadmap_autopilot_settings import (
    RoadmapAutopilotError,
    load_autopilot_state,
    set_autopilot_enabled,
)
from backend.core.use_cases.roadmap_dependencies import evaluate_roadmap_dependencies
from backend.core.use_cases.roadmap_prd_evidence import (
    RoadmapPrdEvidenceError,
    build_evidence_manifest,
    decode_artifact_token,
    read_evidence_artifact,
    read_evidence_artifact_text,
)
from backend.core.use_cases.roadmap_prd_scanner import scan_roadmap_prds
from backend.core.use_cases.roadmap_state_resolver import resolve_roadmap_states

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-runner-roadmap"])

_ROADMAP_CACHE: dict[str, Any] = {}
_ROADMAP_CACHE_TTL_SECONDS = 30
_cache_lock = threading.Lock()


def _serialize(value: Any) -> Any:
    """递归地把 dataclass / Enum 转成 JSON 友好结构。"""
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value


def _resolve_contexts():
    """Resolve enabled repository contexts with diagnostics."""
    settings = load_fresh_agent_runner_settings()
    contexts, _failures = resolve_repository_targets_with_diagnostics(settings)
    return contexts


def _resolve_context(repo_id: str):
    """Return a single enabled repository context."""
    for context in _resolve_contexts():
        if context.repo_id == repo_id:
            return context
    raise HTTPException(status_code=400, detail=f"仓库 '{repo_id}' 不存在或未启用。")


def _encode_prd_path(prd_path: str) -> str:
    """Encode a relative PRD path for safe URL use."""
    return base64.urlsafe_b64encode(prd_path.encode("utf-8")).decode("ascii")


def _decode_prd_path(encoded_path: str) -> str:
    """Decode a URL-safe base64 PRD path."""
    try:
        return base64.urlsafe_b64decode(encoded_path.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="非法的 PRD 路径编码。") from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(
    store: IRoadmapStore,
    *,
    action: str,
    repo_id: str,
    prd_path: str,
    issue_number: int | None,
    result: str,
    detail: str,
) -> None:
    """Best-effort audit logging for roadmap actions."""
    try:
        store.append_audit(
            AuditEntry(
                occurred_at=_now_iso(),
                actor="roadmap",
                action=action,
                repo_id=repo_id,
                issue_number=issue_number,
                params_json=f'{{"prd_path": "{prd_path}"}}',
                result=result,
                detail=detail,
            )
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Failed to audit roadmap action %s: %s", action, exc)


def _build_roadmap_response(
    repo_id: str,
    include_archived: bool,
) -> dict:
    """Scan PRDs and resolve live GitHub state."""
    context = _resolve_context(repo_id)
    github_client = create_github_client(context.repo_path)
    scan_result = scan_roadmap_prds(context.repo_path, include_archived=include_archived)
    prds = scan_result.prds
    block_reasons = evaluate_roadmap_dependencies(
        prds,
        github_client=github_client,
        labels_config=context.config.labels,
    )
    resolved = resolve_roadmap_states(
        prds,
        github_client=github_client,
        config=context.config,
        block_reasons=block_reasons,
    )
    # Enrich dependency targets with current issue numbers for the UI.
    prd_issue_map = {prd.prd_path: prd.issue_number for prd in resolved}
    enriched: list[RoadmapPrd] = []
    for prd in resolved:
        deps: list[RoadmapDependency] = []
        for dep in prd.delivery_dependencies:
            if dep.kind is RoadmapDependencyKind.PRD:
                issue_number = prd_issue_map.get(dep.to_path)
                detail = f"{dep.to_path}"
                if issue_number:
                    detail += f" (#{issue_number})"
                deps.append(
                    RoadmapDependency(
                        from_path=dep.from_path,
                        to_path=dep.to_path,
                        kind=dep.kind,
                        detail=detail,
                    )
                )
            else:
                deps.append(dep)
        enriched.append(
            RoadmapPrd(
                prd_path=prd.prd_path,
                title=prd.title,
                status=prd.status,
                priority=prd.priority,
                issue_url=prd.issue_url,
                issue_number=prd.issue_number,
                state=prd.state,
                acceptance_total=prd.acceptance_total,
                acceptance_checked=prd.acceptance_checked,
                delivery_dependencies=tuple(deps),
                updated_at=prd.updated_at,
                block_reason=prd.block_reason,
                next_action=prd.next_action,
            )
        )
    return {
        "prds": [_serialize(p) for p in enriched],
        "skipped": [_serialize(s) for s in scan_result.skipped],
        "repo_id": repo_id,
        "include_archived": include_archived,
        "scanned_at": _now_iso(),
    }


def _get_cached_roadmap_response(repo_id: str, include_archived: bool) -> dict:
    """Return cached roadmap response or rebuild it."""
    cache_key = f"{repo_id}:archived={include_archived}"
    now = time.time()
    with _cache_lock:
        entry = _ROADMAP_CACHE.get(cache_key)
        if entry and (now - entry["timestamp"]) < _ROADMAP_CACHE_TTL_SECONDS:
            return entry["payload"]
    payload = _build_roadmap_response(repo_id, include_archived)
    with _cache_lock:
        _ROADMAP_CACHE[cache_key] = {"payload": payload, "timestamp": now}
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Read endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agent-runner/roadmap/prds")
def list_roadmap_prds(repo_id: str, include_archived: bool = False) -> dict:
    """列出 PRD 路线图，包含依赖与状态。"""
    return _get_cached_roadmap_response(repo_id, include_archived)


@router.get("/agent-runner/roadmap/settings")
def get_roadmap_settings(repo_id: str) -> dict:
    """读取 roadmap 设置。"""
    store = create_roadmap_store()
    settings = get_or_create_roadmap_settings(store, repo_id)
    return _serialize(settings)


@router.get(
    "/agent-runner/roadmap/prds/{encoded_path}/content",
    response_class=PlainTextResponse,
)
def get_roadmap_prd_content(encoded_path: str, repo_id: str) -> PlainTextResponse:
    """返回单个 PRD 的 Markdown 原文。

    路径编码复用 ``POST /roadmap/prds/{encoded_path}/start`` 的 base64url 约定；
    目录白名单与后缀校验由 core 用例负责，本层只做 HTTP 映射与 4xx 转换。
    """
    prd_path = _decode_prd_path(encoded_path)
    context = _resolve_context(repo_id)
    try:
        prd_content_text = read_prd_content(context.repo_path, prd_path)
    except PrdContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlainTextResponse(prd_content_text, media_type="text/plain; charset=utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Autopilot（仓库级）与验收证据
#
# 这两组端点刻意不复用 `_ROADMAP_CACHE`：Autopilot 状态必须是写后 fresh 读回，
# 证据列表必须每次重新读盘，否则用户会看到陈旧值（rv-2 / rv-3 的验收基点）。
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_max_parallel(repo_id: str) -> int:
    """读取该仓库 Roadmap 并发上限（既有 roadmap settings，不新增存储）。"""
    store = create_roadmap_store()
    roadmap_settings = get_or_create_roadmap_settings(store, repo_id)
    return roadmap_settings.max_parallel


@router.get("/agent-runner/roadmap/autopilot")
def get_roadmap_autopilot(repo_id: str) -> dict:
    """读取当前仓库的 Autopilot 完整闭环状态。

    返回生效的 ``autopilot.enabled``、``safety.auto_merge``、daemon 是否在跑、
    Roadmap 并发上限与配置来源，供页面如实展示是否具备全自动闭环条件。
    """
    context = _resolve_context(repo_id)
    try:
        state = load_autopilot_state(
            repo_id=repo_id,
            contexts=(context,),
            supervisor=create_process_supervisor(),
            max_parallel=_resolve_max_parallel(repo_id),
            editor=create_repository_autopilot_settings_editor(),
        )
    except RoadmapAutopilotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(state)


class UpdateAutopilotRequest(BaseModel):
    """切换当前仓库的 Autopilot 自动推进。"""

    repo_id: str = Field(min_length=1)
    enabled: bool


@router.patch("/agent-runner/roadmap/autopilot")
def update_roadmap_autopilot(request: UpdateAutopilotRequest) -> dict:
    """只修改目标仓库 `.iar.toml` 的 ``autopilot.enabled``。

    成功响应体来自写后 fresh load 的生效配置，不回显请求体；``safety.auto_merge``
    保持原样（第二道危险动作门禁，页面只读展示）。写回失败时不替换原文件。
    """
    contexts = _resolve_contexts()
    if not any(context.repo_id == request.repo_id for context in contexts):
        raise HTTPException(status_code=400, detail=f"仓库 '{request.repo_id}' 不存在或未启用。")
    try:
        state = set_autopilot_enabled(
            repo_id=request.repo_id,
            enabled=request.enabled,
            editor=create_repository_autopilot_settings_editor(),
            contexts_loader=_resolve_contexts,
            supervisor=create_process_supervisor(),
            max_parallel=_resolve_max_parallel(request.repo_id),
        )
    except RoadmapAutopilotError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        create_roadmap_store(),
        action="autopilot_toggle",
        repo_id=request.repo_id,
        prd_path="",
        issue_number=None,
        result="accepted",
        detail=f"autopilot.enabled={request.enabled}",
    )
    return _serialize(state)


@router.get("/agent-runner/roadmap/prds/{encoded_path}/evidence")
def get_roadmap_prd_evidence(encoded_path: str, repo_id: str) -> dict:
    """列出某个 PRD 在仓库中仍保留的验收证据文件（每次请求重新读盘）。"""
    prd_path = _decode_prd_path(encoded_path)
    context = _resolve_context(repo_id)
    try:
        manifest = build_evidence_manifest(
            repo_path=context.repo_path,
            config=context.config,
            prd_path=prd_path,
        )
    except RoadmapPrdEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(manifest)


@router.get("/agent-runner/roadmap/prds/{encoded_path}/evidence/{artifact_token}")
def read_roadmap_prd_evidence_artifact(
    encoded_path: str, artifact_token: str, repo_id: str
) -> Response:
    """受限读取单个证据文件：文本内联、图片预览、其它类型下载。

    文件名以 base64url 传递，解码后必须是纯 basename 且为该 PRD 证据目录的
    直接子文件；越界、符号链接逃逸与超限文件一律 4xx，不泄露仓外内容。
    """
    prd_path = _decode_prd_path(encoded_path)
    context = _resolve_context(repo_id)
    try:
        artifact_name = decode_artifact_token(artifact_token)
        _content_bytes, media_type, file_name = read_evidence_artifact(
            context.repo_path, context.config, prd_path, artifact_name
        )
    except RoadmapPrdEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if media_type.startswith("text/") or media_type in {"application/json"}:
        try:
            text = read_evidence_artifact_text(
                context.repo_path, context.config, prd_path, artifact_name
            )
        except RoadmapPrdEvidenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PlainTextResponse(
            text,
            media_type=f"{media_type}; charset=utf-8",
            headers=_artifact_headers(file_name, disposition="inline"),
        )

    content = read_evidence_artifact(context.repo_path, context.config, prd_path, artifact_name)[0]
    disposition = "inline" if media_type.startswith("image/") else "attachment"
    return Response(
        content=content,
        media_type=media_type,
        headers=_artifact_headers(file_name, disposition=disposition),
    )


def _artifact_headers(file_name: str, *, disposition: str) -> dict[str, str]:
    """证据下载的保守响应头：显式 disposition + no-sniff，避免浏览器误执行。"""
    safe_name = file_name.replace('"', "'")
    return {
        "Content-Disposition": f'{disposition}; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Write endpoints
# ─────────────────────────────────────────────────────────────────────────────


class UpdateSettingsRequest(BaseModel):
    """更新 roadmap 用户设置。"""

    max_parallel: int = Field(default=2, ge=1, le=10)
    default_view: str = Field(default="list", pattern="^(timeline|list)$")


@router.patch("/agent-runner/roadmap/settings")
def update_roadmap_settings(repo_id: str, request: UpdateSettingsRequest) -> dict:
    """更新 roadmap 并发数与默认视图。"""
    store = create_roadmap_store()
    settings = RoadmapSettingsEntry(
        repo_id=repo_id,
        max_parallel=request.max_parallel,
        default_view=request.default_view,
        updated_at=_now_iso(),
    )
    try:
        store.save_roadmap_settings(settings)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"保存设置失败: {exc}") from exc
    return _serialize(settings)


class StartPrdRequest(BaseModel):
    """单个 PRD 开始请求。"""

    repo_id: str = Field(min_length=1)


@router.post("/agent-runner/roadmap/prds/{encoded_path}/start")
def start_roadmap_prd(encoded_path: str, request: StartPrdRequest) -> dict:
    """开始单个 PRD：创建 Issue（若需要）、添加 ready、启动 runner。"""
    prd_path = _decode_prd_path(encoded_path)
    settings = load_fresh_agent_runner_settings()
    contexts = _resolve_contexts()
    store = create_roadmap_store()
    try:
        spawn_cwd = resolve_console_spawn_cwd(request.repo_id, contexts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = start_prd(
            prd_path=prd_path,
            repo_id=request.repo_id,
            contexts=contexts,
            github_client=create_github_client(_resolve_context(request.repo_id).repo_path),
            supervisor=create_process_supervisor(),
            store=store,
            runner_command=settings.console.runner_command,
            spawn_cwd=spawn_cwd,
            process_runner=create_process_runner(),
        )
    except RoadmapActionError as exc:
        _audit(
            store,
            action="start_prd",
            repo_id=request.repo_id,
            prd_path=prd_path,
            issue_number=None,
            result="error",
            detail=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Invalidate cache for this repo so the next read reflects the new state.
    _ROADMAP_CACHE.pop(f"{request.repo_id}:archived=False", None)
    return _serialize(result)


class StartGlobalRequest(BaseModel):
    """全局开始请求。"""

    repo_id: str = Field(min_length=1)
    max_parallel: int = Field(default=2, ge=1, le=10)


@router.post("/agent-runner/roadmap/start-global")
def start_roadmap_global(request: StartGlobalRequest) -> dict:
    """按并发上限批量开始无依赖的 pending PRD。"""
    settings = load_fresh_agent_runner_settings()
    contexts = _resolve_contexts()
    store = create_roadmap_store()
    try:
        spawn_cwd = resolve_console_spawn_cwd(request.repo_id, contexts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = start_global_roadmap(
            repo_id=request.repo_id,
            max_parallel=request.max_parallel,
            contexts=contexts,
            github_client_factory=create_github_client,
            supervisor=create_process_supervisor(),
            store=store,
            runner_command=settings.console.runner_command,
            spawn_cwd=spawn_cwd,
            process_runner=create_process_runner(),
        )
    except RoadmapActionError as exc:
        _audit(
            store,
            action="start_global",
            repo_id=request.repo_id,
            prd_path="",
            issue_number=None,
            result="error",
            detail=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _ROADMAP_CACHE.pop(f"{request.repo_id}:archived=False", None)
    return _serialize(result)


class StopGlobalRequest(BaseModel):
    """停止全局调度请求。"""

    repo_id: str = Field(min_length=1)


@router.post("/agent-runner/roadmap/stop-global")
def stop_roadmap_global(request: StopGlobalRequest) -> dict:
    """清空 roadmap 队列，已运行的不中断。"""
    store = create_roadmap_store()
    try:
        return stop_global_roadmap(repo_id=request.repo_id, store=store)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"停止全局调度失败: {exc}") from exc

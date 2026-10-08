"""Backlog API for PRD orchestration.

Provides read endpoints for PRD state/dependencies and write endpoints for
single/global start actions.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from backend.api.backlog_sync import ensure_fresh_backlog_snapshot, request_backlog_resync
from backend.core.shared.interfaces.runner_console import AuditEntry, IBacklogStore
from backend.core.shared.models.backlog import (
    BacklogDependency,
    BacklogDependencyKind,
    BacklogPrd,
    BacklogSettingsEntry,
)
from backend.core.use_cases.agent_runner_factory import (
    create_github_client,
    create_process_runner,
    create_process_supervisor,
    create_repository_autopilot_settings_editor,
    create_backlog_store,
    load_fresh_agent_runner_settings,
    resolve_console_spawn_cwd,
    resolve_repository_targets_with_diagnostics,
)
from backend.core.use_cases.prd_content_reader import PrdContentError, read_prd_content
from backend.core.use_cases.agent_runner_lifecycle import build_prd_lifecycle_detail
from backend.core.use_cases.backlog_actions import (
    BacklogActionError,
    get_or_create_backlog_settings,
    start_global_backlog,
    start_prd,
    stop_global_backlog,
)
from backend.core.use_cases.backlog_autopilot_settings import (
    BacklogAutopilotError,
    load_autopilot_state,
    set_autopilot_enabled,
)
from backend.core.use_cases.backlog_ci_delivery import (
    BacklogCiPolicyError,
    build_ci_delivery,
    request_manual_ci_repair,
    set_prd_ci_policy,
)
from backend.core.use_cases.backlog_dependencies import evaluate_backlog_dependencies
from backend.core.use_cases.backlog_prd_evidence import (
    BacklogPrdEvidenceError,
    build_evidence_manifest,
    decode_artifact_token,
    read_evidence_artifact,
    read_evidence_artifact_text,
)
from backend.core.use_cases.backlog_prd_scanner import scan_backlog_prds
from backend.core.use_cases.backlog_state_resolver import (
    BacklogStateResolutionContext,
    resolve_backlog_states,
)
from backend.core.use_cases.review_once import _extract_pr_branch_from_comments

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-runner-backlog"])


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
    store: IBacklogStore,
    *,
    action: str,
    repo_id: str,
    prd_path: str,
    issue_number: int | None,
    result: str,
    detail: str,
) -> None:
    """Best-effort audit logging for backlog actions."""
    try:
        store.append_audit(
            AuditEntry(
                occurred_at=_now_iso(),
                actor="backlog",
                action=action,
                repo_id=repo_id,
                issue_number=issue_number,
                params_json=f'{{"prd_path": "{prd_path}"}}',
                result=result,
                detail=detail,
            )
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Failed to audit backlog action %s: %s", action, exc)


def _build_backlog_response(
    repo_id: str,
    include_archived: bool,
) -> dict:
    """Scan PRDs and resolve live GitHub state."""
    context = _resolve_context(repo_id)
    github_client = create_github_client(context.repo_path)
    scan_result = scan_backlog_prds(context.repo_path, include_archived=include_archived)
    prds = scan_result.prds
    block_reasons = evaluate_backlog_dependencies(
        prds,
        github_client=github_client,
        fail_on_github_error=True,
    )
    resolved = resolve_backlog_states(
        prds,
        github_client=github_client,
        context=BacklogStateResolutionContext(
            config=context.config,
            block_reasons=block_reasons,
            fail_on_github_error=True,
        ),
    )
    # Enrich dependency targets with current issue numbers for the UI.
    prd_issue_map = {prd.prd_path: prd.issue_number for prd in resolved}
    enriched: list[BacklogPrd] = []
    for prd in resolved:
        deps: list[BacklogDependency] = []
        for dep in prd.delivery_dependencies:
            if dep.kind is BacklogDependencyKind.PRD:
                issue_number = prd_issue_map.get(dep.to_path)
                detail = f"{dep.to_path}"
                if issue_number:
                    detail += f" (#{issue_number})"
                deps.append(
                    BacklogDependency(
                        from_path=dep.from_path,
                        to_path=dep.to_path,
                        kind=dep.kind,
                        detail=detail,
                    )
                )
            else:
                deps.append(dep)
        enriched.append(
            BacklogPrd(
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


# ─────────────────────────────────────────────────────────────────────────────
# Read endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agent-runner/backlog/prds")
def list_backlog_prds(repo_id: str, include_archived: bool = False) -> dict:
    """列出 PRD 待办队列：立即返回本地快照，缺失/过期时在后台重扫。

    请求线程不做任何 GitHub 调用，也不等待重建结果——列表秒开的代价就是响应
    可能短暂陈旧，因此响应里用 ``stale`` 与 ``scanned_at`` 把新鲜度如实交代给
    前端。仓库不存在或已禁用仍返回 400，其历史快照不会回流到任何页面。
    """
    _resolve_context(repo_id)
    snapshot_view = ensure_fresh_backlog_snapshot(repo_id, include_archived=include_archived)
    return {
        **snapshot_view.payload,
        "scanned_at": snapshot_view.scanned_at,
        "stale": snapshot_view.stale,
    }


@router.get("/agent-runner/backlog/settings")
def get_backlog_settings(repo_id: str) -> dict:
    """读取 backlog 设置。"""
    store = create_backlog_store()
    settings = get_or_create_backlog_settings(store, repo_id)
    return _serialize(settings)


@router.get(
    "/agent-runner/backlog/prds/{encoded_path}/content",
    response_class=PlainTextResponse,
)
def get_backlog_prd_content(encoded_path: str, repo_id: str) -> PlainTextResponse:
    """返回单个 PRD 的 Markdown 原文。

    路径编码复用 ``POST /backlog/prds/{encoded_path}/start`` 的 base64url 约定；
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
# 这两组端点刻意不读列表快照：Autopilot 状态必须是写后 fresh 读回，
# 证据列表必须每次重新读盘，否则用户会看到陈旧值（rv-2 / rv-3 的验收基点）。
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_max_parallel(repo_id: str) -> int:
    """读取该仓库 Backlog 并发上限（既有 backlog settings，不新增存储）。"""
    store = create_backlog_store()
    backlog_settings = get_or_create_backlog_settings(store, repo_id)
    return backlog_settings.max_parallel


@router.get("/agent-runner/backlog/autopilot")
def get_backlog_autopilot(repo_id: str) -> dict:
    """读取当前仓库的 Autopilot 完整闭环状态。

    返回生效的 ``autopilot.enabled``、``safety.auto_merge``、daemon 是否在跑、
    Backlog 并发上限与配置来源，供页面如实展示是否具备全自动闭环条件。
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
    except BacklogAutopilotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(state)


class UpdateAutopilotRequest(BaseModel):
    """切换当前仓库的 Autopilot 自动推进。"""

    repo_id: str = Field(min_length=1)
    enabled: bool


@router.patch("/agent-runner/backlog/autopilot")
def update_backlog_autopilot(request: UpdateAutopilotRequest) -> dict:
    """只修改目标仓库 `.kedacode.toml` 的 ``autopilot.enabled``。

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
    except BacklogAutopilotError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        create_backlog_store(),
        action="autopilot_toggle",
        repo_id=request.repo_id,
        prd_path="",
        issue_number=None,
        result="accepted",
        detail=f"autopilot.enabled={request.enabled}",
    )
    return _serialize(state)


@router.get("/agent-runner/backlog/prds/{encoded_path}/lifecycle")
def get_backlog_prd_lifecycle(encoded_path: str, repo_id: str) -> dict:
    """返回单个 PRD 的生命周期详情（当前阶段、耗时拆分与有序事件）。

    刻意不读列表快照：生命周期账本必须在事件写入后 fresh 读回，
    否则“运行中 → 刚失败”这种切换会被后台刷新节奏掩盖，页面显示陈旧进度。
    无任何 lifecycle run/event 时返回 ``has_data=False`` 空态，而不是 404——
    “还没开始执行”是正常状态，不是错误。
    """
    prd_path = _decode_prd_path(encoded_path)
    _resolve_context(repo_id)
    store = create_backlog_store()
    detail = build_prd_lifecycle_detail(store=store, repo_id=repo_id, prd_path=prd_path)
    return _serialize(detail)


@router.get("/agent-runner/backlog/prds/{encoded_path}/evidence")
def get_backlog_prd_evidence(encoded_path: str, repo_id: str) -> dict:
    """列出某个 PRD 在仓库中仍保留的验收证据文件（每次请求重新读盘）。"""
    prd_path = _decode_prd_path(encoded_path)
    context = _resolve_context(repo_id)
    try:
        manifest = build_evidence_manifest(
            repo_path=context.repo_path,
            config=context.config,
            prd_path=prd_path,
        )
    except BacklogPrdEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(manifest)


@router.get("/agent-runner/backlog/prds/{encoded_path}/evidence/{artifact_token}")
def read_backlog_prd_evidence_artifact(
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
    except BacklogPrdEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if media_type.startswith("text/") or media_type in {"application/json"}:
        try:
            text = read_evidence_artifact_text(
                context.repo_path, context.config, prd_path, artifact_name
            )
        except BacklogPrdEvidenceError as exc:
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
# CI/CD 交付尾段（状态投影 / 策略 / 单次手动修复）
#
# 与 Autopilot 端点同一原则：不读列表快照。ci_delivery 是从 GitHub
# PR context 与 Issue marker 派生的运行时投影，必须 fresh 读取；策略与修复
# 写回后也要 fresh 读回 Issue 评论流作为成功判据。
# ─────────────────────────────────────────────────────────────────────────────


def _find_backlog_prd(repo_id: str, prd_path: str):
    """fresh 扫描并返回目标 PRD（无缓存；找不到时抛 404）。"""
    context = _resolve_context(repo_id)
    scan_result = scan_backlog_prds(context.repo_path, include_archived=True)
    for prd in scan_result.prds:
        if prd.prd_path == prd_path:
            return context, prd
    raise HTTPException(status_code=404, detail=f"PRD '{prd_path}' 不在仓库 '{repo_id}' 中。")


def _resolve_prd_ci_context(repo_id: str, prd_path: str):
    """解析 PRD 的 Issue、评论流与 PR context（供状态投影与手动修复共用）。"""
    context, prd = _find_backlog_prd(repo_id, prd_path)
    if prd.issue_number is None:
        raise HTTPException(
            status_code=400,
            detail=f"PRD '{prd_path}' 没有对应的 GitHub Issue，无法操作 CI/CD 交付尾段。",
        )
    github_client = create_github_client(context.repo_path)
    issue_comments = github_client.list_issue_comments(prd.issue_number)
    pr_branch = _extract_pr_branch_from_comments(issue_comments)
    pr_context = None
    if pr_branch is not None:
        pr_context = github_client.get_pull_request_context(pr_branch)
    return context, prd, github_client, issue_comments, pr_context


@router.get("/agent-runner/backlog/prds/{encoded_path}/ci")
def get_backlog_prd_ci(encoded_path: str, repo_id: str) -> dict:
    """返回单个 PRD 的 CI/CD 交付尾段投影（每次请求 fresh 读取）。"""
    prd_path = _decode_prd_path(encoded_path)
    context, prd, _github_client, issue_comments, pr_context = _resolve_prd_ci_context(
        repo_id, prd_path
    )
    delivery = build_ci_delivery(
        prd_path=prd_path,
        issue_number=prd.issue_number,
        comments=issue_comments,
        pr_context=pr_context,
        global_enabled=bool(context.config.post_pr_supervisor.auto_repair_ci),
        max_rounds=max(0, context.config.post_pr_supervisor.max_repair_attempts),
    )
    return _serialize(delivery)


@router.get("/agent-runner/backlog/ci-repair-global")
def get_backlog_ci_repair_global(repo_id: str) -> dict:
    """读取当前仓库的全局 CI/CD 自动修复开关（fresh load）。"""
    context = _resolve_context(repo_id)
    return {
        "repo_id": repo_id,
        "global_enabled": bool(context.config.post_pr_supervisor.auto_repair_ci),
        "max_rounds": max(0, context.config.post_pr_supervisor.max_repair_attempts),
    }


class UpdateCiRepairGlobalRequest(BaseModel):
    """切换当前仓库的全局 CI/CD 自动修复开关。"""

    repo_id: str = Field(min_length=1)
    enabled: bool


@router.patch("/agent-runner/backlog/ci-repair-global")
def update_backlog_ci_repair_global(request: UpdateCiRepairGlobalRequest) -> dict:
    """只修改目标仓库 `.kedacode.toml` 的 ``post_pr_supervisor.auto_repair_ci``。

    成功响应体来自写后 fresh load 的生效配置；该开关与 ``autopilot.enabled``、
    ``safety.auto_merge``、``runner.fix_agent_enabled`` 语义独立，互不联动。
    """
    editor = create_repository_autopilot_settings_editor()
    contexts = _resolve_contexts()
    target = next((context for context in contexts if context.repo_id == request.repo_id), None)
    if target is None:
        raise HTTPException(status_code=400, detail=f"仓库 '{request.repo_id}' 不存在或未启用。")
    try:
        editor.set_auto_repair_ci(target.repo_path, request.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    fresh_context = _resolve_context(request.repo_id)
    if bool(fresh_context.config.post_pr_supervisor.auto_repair_ci) is not request.enabled:
        raise HTTPException(
            status_code=409,
            detail="写回后重新加载的配置与请求值不一致，原文件可能未被正确替换。",
        )
    _audit(
        create_backlog_store(),
        action="ci_repair_global_toggle",
        repo_id=request.repo_id,
        prd_path="",
        issue_number=None,
        result="accepted",
        detail=f"auto_repair_ci={request.enabled}",
    )
    return {
        "repo_id": request.repo_id,
        "global_enabled": bool(fresh_context.config.post_pr_supervisor.auto_repair_ci),
        "max_rounds": max(0, fresh_context.config.post_pr_supervisor.max_repair_attempts),
    }


class UpdateCiPolicyRequest(BaseModel):
    """设置单个 PRD 的 CI/CD 自动修复策略（三态覆盖）。"""

    repo_id: str = Field(min_length=1)
    value: str = Field(pattern="^(inherit|on|off)$")


@router.patch("/agent-runner/backlog/prds/{encoded_path}/ci-policy")
def update_backlog_prd_ci_policy(encoded_path: str, request: UpdateCiPolicyRequest) -> dict:
    """写对应 Issue 的最新 ``iar:ci-auto-repair-policy`` marker 并 fresh 回读。"""
    prd_path = _decode_prd_path(encoded_path)
    _context, prd, github_client, issue_comments, pr_context = _resolve_prd_ci_context(
        request.repo_id, prd_path
    )
    try:
        set_prd_ci_policy(
            github_client=github_client,
            issue_number=prd.issue_number,
            value=request.value,
        )
    except BacklogCiPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 写后 fresh 回读评论流，以 marker 而不是请求体作为成功判据。
    fresh_comments = github_client.list_issue_comments(prd.issue_number)
    delivery = build_ci_delivery(
        prd_path=prd_path,
        issue_number=prd.issue_number,
        comments=fresh_comments,
        pr_context=pr_context,
        global_enabled=bool(_context.config.post_pr_supervisor.auto_repair_ci),
        max_rounds=max(0, _context.config.post_pr_supervisor.max_repair_attempts),
    )
    if delivery.stored_policy.value != request.value:
        raise HTTPException(
            status_code=409,
            detail="策略 marker 写回后 fresh 读取与请求值不一致。",
        )
    _audit(
        create_backlog_store(),
        action="ci_policy_set",
        repo_id=request.repo_id,
        prd_path=prd_path,
        issue_number=prd.issue_number,
        result="accepted",
        detail=f"policy={request.value}",
    )
    return _serialize(delivery)


class ManualCiRepairRequest(BaseModel):
    """显式请求一次 CI/CD 修复。"""

    repo_id: str = Field(min_length=1)


@router.post("/agent-runner/backlog/prds/{encoded_path}/ci-repair")
def request_backlog_prd_ci_repair(encoded_path: str, request: ManualCiRepairRequest) -> dict:
    """问题卡「立即修复」：显式请求一次修复（幂等、受既有门禁约束）。"""
    prd_path = _decode_prd_path(encoded_path)
    _context, prd, github_client, _issue_comments, pr_context = _resolve_prd_ci_context(
        request.repo_id, prd_path
    )
    if pr_context is None:
        raise HTTPException(
            status_code=409,
            detail="无法获取当前 PR context，不能在旧 head 上发起修复。",
        )
    # 从扫描结果重建最小 IssueSummary（fresh，不缓存）。
    from backend.core.shared.models.agent_runner import IssueSummary

    issue = IssueSummary(
        number=prd.issue_number,
        title=prd.title,
        body="",
        url=prd.issue_url or "",
        labels=(),
    )
    requested, detail = request_manual_ci_repair(
        issue=issue,
        pr_context=pr_context,
        config=_context.config,
        github_client=github_client,
    )
    _audit(
        create_backlog_store(),
        action="ci_manual_repair",
        repo_id=request.repo_id,
        prd_path=prd_path,
        issue_number=prd.issue_number,
        result="accepted" if requested else "noop",
        detail=detail,
    )
    return {"requested": requested, "detail": detail, "head_sha": pr_context.head_sha}


# ─────────────────────────────────────────────────────────────────────────────
# Write endpoints
# ─────────────────────────────────────────────────────────────────────────────


class UpdateSettingsRequest(BaseModel):
    """更新 backlog 用户设置。"""

    max_parallel: int = Field(default=2, ge=1, le=10)
    default_view: str = Field(default="list", pattern="^(timeline|list)$")


@router.patch("/agent-runner/backlog/settings")
def update_backlog_settings(repo_id: str, request: UpdateSettingsRequest) -> dict:
    """更新 backlog 并发数与默认视图。"""
    store = create_backlog_store()
    settings = BacklogSettingsEntry(
        repo_id=repo_id,
        max_parallel=request.max_parallel,
        default_view=request.default_view,
        updated_at=_now_iso(),
    )
    try:
        store.save_backlog_settings(settings)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"保存设置失败: {exc}") from exc
    return _serialize(settings)


class StartPrdRequest(BaseModel):
    """单个 PRD 开始请求。"""

    repo_id: str = Field(min_length=1)


@router.post("/agent-runner/backlog/prds/{encoded_path}/start")
def start_backlog_prd(encoded_path: str, request: StartPrdRequest) -> dict:
    """开始单个 PRD：创建 Issue（若需要）、添加 ready、启动 runner。"""
    prd_path = _decode_prd_path(encoded_path)
    settings = load_fresh_agent_runner_settings()
    contexts = _resolve_contexts()
    store = create_backlog_store()
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
    except BacklogActionError as exc:
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

    # 弹缓存换成"立即派一次后台重扫"：读侧仍先拿到上一份快照，但新状态会在
    # 扫描落地后的下一次读取里出现，不需要有人在请求线程里等扫描。
    request_backlog_resync(request.repo_id)
    return _serialize(result)


class StartGlobalRequest(BaseModel):
    """全局开始请求。"""

    repo_id: str = Field(min_length=1)
    max_parallel: int = Field(default=2, ge=1, le=10)


@router.post("/agent-runner/backlog/start-global")
def start_backlog_global(request: StartGlobalRequest) -> dict:
    """按并发上限批量开始无依赖的 pending PRD。"""
    settings = load_fresh_agent_runner_settings()
    contexts = _resolve_contexts()
    store = create_backlog_store()
    try:
        spawn_cwd = resolve_console_spawn_cwd(request.repo_id, contexts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = start_global_backlog(
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
    except BacklogActionError as exc:
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

    request_backlog_resync(request.repo_id)
    return _serialize(result)


class StopGlobalRequest(BaseModel):
    """停止全局调度请求。"""

    repo_id: str = Field(min_length=1)


@router.post("/agent-runner/backlog/stop-global")
def stop_backlog_global(request: StopGlobalRequest) -> dict:
    """清空 backlog 队列，已运行的不中断。"""
    store = create_backlog_store()
    try:
        return stop_global_backlog(repo_id=request.repo_id, store=store)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"停止全局调度失败: {exc}") from exc

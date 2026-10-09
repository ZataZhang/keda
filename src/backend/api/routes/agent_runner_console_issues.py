"""Console 仓库 Issue 操作端点（全量列表、标签读写、一句话建 Issue）。

从 ``agent_runner_console.py`` 按行数红线拆出的新模块：本文件只做参数校验、
上下文解析与用例调用，业务语义全部在 core 用例（``console_issues`` /
``issue_label_actions`` / ``console_issue_creation``）。

安全边界与 console 其余写端点一致：标签写入仅限 ``kc labels sync`` 同源的
标准标签集合（校验在 core，越界集合在写入前即被拒绝，GitHub 零变化）；
建 Issue 复用 CLI ``kc issue create --from-prompt`` 的同一个用例。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.shared.interfaces.runner_console import AuditEntry
from backend.core.shared.models.agent_runner import RepositoryRunContext
from backend.core.use_cases.agent_runner_factory import (
    create_console_store,
    create_github_client,
    create_process_runner,
    load_fresh_agent_runner_settings,
    resolve_repository_targets_with_diagnostics,
)
from backend.core.use_cases.console_issue_creation import (
    ConsoleCreatedIssue,
    create_issue_from_prompt_for_console,
)
from backend.core.use_cases.console_issues import (
    DEFAULT_ISSUE_LIST_LIMIT,
    MAX_ISSUE_LIST_LIMIT,
    list_repository_issues,
)
from backend.core.use_cases.issue_label_actions import (
    IssueLabelActionError,
    IssueLabelNotAllowedError,
    read_issue_labels,
    update_issue_labels,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-runner-console-issues"])


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


def _resolve_enabled_context(repo_id: str) -> RepositoryRunContext:
    """按 repo_id 解析 enabled 仓库上下文，未注册/停用返回 404。"""
    settings = load_fresh_agent_runner_settings()
    contexts, _failures = resolve_repository_targets_with_diagnostics(settings)
    for context in contexts:
        if context.repo_id == repo_id:
            return context
    raise HTTPException(
        status_code=404,
        detail=f"Repository '{repo_id}' is not registered or not enabled.",
    )


def _audit_issue_write(
    *,
    action: str,
    repo_id: str,
    issue_number: int | None,
    detail: str,
    result: str = "accepted",
) -> None:
    """Issue 写操作的审计落库（best effort，失败不阻断响应）。

    被拒绝的尝试同样落一条 ``result="rejected"``：文档承诺「所有写操作（含被拒绝的）
    都会写入审计日志」，而「谁试着往 Issue 上写集合外 / 敏感标签」正是这条记录要回答的
    ——只审计成功路径等于对被拒尝试零留痕。
    """
    try:
        create_console_store().append_audit(
            AuditEntry(
                occurred_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                actor="console",
                action=action,
                repo_id=repo_id,
                issue_number=issue_number,
                params_json="{}",
                result=result,
                detail=detail,
            )
        )
    except Exception as exc:  # noqa: BLE001 - audit must not break the action.
        _logger.warning("Failed to audit issue write %s: %s", action, exc)


#: 审计 detail 里需求原文的截断长度：保证可追溯又不把整段 prompt 塞进审计页。
_AUDIT_PROMPT_MAX_CHARS = 120


def _truncate_prompt(prompt_text: str) -> str:
    """把需求原文收敛成适合写入审计 detail 的单行片段。"""
    collapsed = " ".join(prompt_text.split())
    if len(collapsed) <= _AUDIT_PROMPT_MAX_CHARS:
        return collapsed
    return f"{collapsed[:_AUDIT_PROMPT_MAX_CHARS]}…"


# ─────────────────────────────────────────────────────────────────────────────
# 全量 Issue 列表（FR-4，只读）
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agent-runner/console/repositories/{repo_id}/issues")
def list_repository_all_issues(
    repo_id: str,
    state: str = "open",
    label: str | None = None,
    limit: int = DEFAULT_ISSUE_LIST_LIMIT,
) -> dict:
    """列举仓库 Issue（默认不过滤标签），每条标注是否已被监控收录。"""
    if limit < 1 or limit > MAX_ISSUE_LIST_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {MAX_ISSUE_LIST_LIMIT}.",
        )
    context = _resolve_enabled_context(repo_id)
    github_client = create_github_client(context.repo_path, create_process_runner())
    try:
        entries = list_repository_issues(
            context=context, github_client=github_client, state=state, limit=limit, label=label
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"repo_id": repo_id, "issues": [_serialize(entry) for entry in entries]}


# ─────────────────────────────────────────────────────────────────────────────
# Issue 标签读与「集合内增删」写（FR-5）
# ─────────────────────────────────────────────────────────────────────────────


class UpdateIssueLabelsRequest(BaseModel):
    """标签增删请求体：``add`` / ``remove`` 都必须落在已同步标准标签集内。"""

    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


@router.get("/agent-runner/console/repositories/{repo_id}/issues/{issue_number}/labels")
def get_console_issue_labels(repo_id: str, issue_number: int) -> dict:
    """读取 Issue 当前标签与允许通过网页增删的标准标签集合。"""
    if issue_number <= 0:
        raise HTTPException(status_code=400, detail="issue_number must be a positive integer.")
    context = _resolve_enabled_context(repo_id)
    github_client = create_github_client(context.repo_path, create_process_runner())
    try:
        snapshot = read_issue_labels(
            issue_number=issue_number, context=context, github_client=github_client
        )
    except IssueLabelActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(snapshot)


@router.put("/agent-runner/console/repositories/{repo_id}/issues/{issue_number}/labels")
def put_console_issue_labels(
    repo_id: str, issue_number: int, request: UpdateIssueLabelsRequest
) -> dict:
    """在已同步标准标签集内增删标签；写回后以 GitHub fresh read 返回状态。

    集合外标签在写入前被拒绝（422，GitHub 零变化）；网页不创建新标签，
    确需新标签先在终端执行 ``kc labels sync``。成功与被拒都写审计日志。
    """
    attempt_detail = f"labels add=[{', '.join(request.add)}] remove=[{', '.join(request.remove)}]"

    def _audit_write(*, result: str, reason: str = "") -> None:
        _audit_issue_write(
            action="update_issue_labels",
            repo_id=repo_id,
            issue_number=issue_number,
            result=result,
            detail=f"{attempt_detail}; {result}: {reason}" if reason else attempt_detail,
        )

    if issue_number <= 0:
        _audit_write(result="rejected", reason="issue_number must be a positive integer.")
        raise HTTPException(status_code=400, detail="issue_number must be a positive integer.")
    context = _resolve_enabled_context(repo_id)
    github_client = create_github_client(context.repo_path, create_process_runner())
    try:
        snapshot = update_issue_labels(
            issue_number=issue_number,
            add=request.add,
            remove=request.remove,
            context=context,
            github_client=github_client,
        )
    except IssueLabelNotAllowedError as exc:
        _audit_write(result="rejected", reason=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IssueLabelActionError as exc:
        _audit_write(result="rejected", reason=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit_write(result="accepted")
    return _serialize(snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# 启动高级选项的候选清单（FR-7 选项 sheet 的下拉数据，只读）
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agent-runner/console/repositories/{repo_id}/launch-options")
def get_console_launch_options(repo_id: str) -> dict:
    """返回该仓库可选项：agent 名单（含 auto 别名）与已定义模型预设名。

    只给「有什么可选」，不做任何启动语义——取值最终仍按 ``kc run`` 同名旗标
    由 core 的 ``RunnerLaunchOptions`` 校验与折算。
    """
    context = _resolve_enabled_context(repo_id)
    agent_names = sorted(context.config.agents)
    preset_names = sorted(context.config.agent_presets)
    return {"repo_id": repo_id, "agents": agent_names, "presets": preset_names}


# ─────────────────────────────────────────────────────────────────────────────
# 一句话建 Issue（FR-8）
# ─────────────────────────────────────────────────────────────────────────────


class CreateIssueFromPromptRequest(BaseModel):
    """一句话建 Issue 请求体（复用 CLI ``kc issue create --from-prompt`` 用例）。"""

    prompt_text: str = Field(min_length=1)
    issue_type: str = Field(default="feature", min_length=1)


@router.post("/agent-runner/console/repositories/{repo_id}/issues", status_code=201)
def create_console_issue_from_prompt(repo_id: str, request: CreateIssueFromPromptRequest) -> dict:
    """用一句话需求创建 GitHub Issue；建完停在未入队态，不自动打就绪标签。

    成功与被拒的写入尝试都进审计日志。
    """
    attempt_detail = (
        f"issue_type={request.issue_type} prompt={_truncate_prompt(request.prompt_text)}"
    )

    def _audit_write(*, issue_number: int | None, result: str, reason: str) -> None:
        _audit_issue_write(
            action="create_issue_from_prompt",
            repo_id=repo_id,
            issue_number=issue_number,
            result=result,
            detail=f"{attempt_detail}; {result}: {reason}",
        )

    context = _resolve_enabled_context(repo_id)
    try:
        created: ConsoleCreatedIssue = create_issue_from_prompt_for_console(
            context=context,
            process_runner=create_process_runner(),
            prompt_text=request.prompt_text,
            issue_type=request.issue_type,
        )
    except ValueError as exc:
        _audit_write(issue_number=None, result="rejected", reason=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit_write(issue_number=created.number, result="accepted", reason=f"url={created.issue_url}")
    return _serialize(created)

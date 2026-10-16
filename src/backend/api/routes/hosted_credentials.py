"""托管 Console 的运行凭据管理路由（Issue #265，FR-5）。

全部端点要求已认证会话（HttpOnly cookie 解析出的 principal），否则 401。
只提供状态视图与引导信息，**任何响应都不回显 secret**；GitHub 保存即只读校验，
Agent key 保存不调模型，模型连通性测试必须显式带 ``cost_confirmed`` 才发起真实
最小请求。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from backend.core.shared.models.hosted_console import HostedCredentialKind
from backend.core.shared.security import hosted_secrets as secrets_util
from backend.core.use_cases.agent_runner_factory import (
    create_hosted_console_service,
    load_fresh_agent_runner_settings,
)

router = APIRouter(prefix="/hosted/credentials", tags=["hosted-credentials"])


def _build_service():
    """按当前生效设置装配托管 Console 服务。"""
    return create_hosted_console_service(load_fresh_agent_runner_settings())


def _require_session(session_token: str | None):
    """解析会话 cookie；缺失或过期返回 401。"""
    if not session_token:
        raise HTTPException(status_code=401, detail="authentication required")
    principal = _build_service().resolve_session(session_token)
    if principal is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    return principal


class GitHubCredentialPayload(BaseModel):
    """新增/轮换 GitHub fine-grained PAT 的请求体（token 只在内存流转）。"""

    repo_id: str = Field(min_length=1, max_length=128)
    owner_repo: str
    token: str


class AgentCredentialPayload(BaseModel):
    """新增/保存 Agent provider API key 的请求体（保存不调模型）。"""

    repo_id: str = Field(min_length=1, max_length=128)
    owner_repo: str
    provider: str
    api_key: str


class AgentCredentialTestPayload(BaseModel):
    """显式确认费用后的最小模型连通性测试请求体。"""

    repo_id: str = Field(min_length=1, max_length=128)
    owner_repo: str
    provider: str
    api_key: str
    cost_confirmed: bool = False


def _status_to_dict(status) -> dict:
    """把凭据状态转成 API dict（不含 secret；datetime 转 ISO）。"""
    payload = asdict(status)
    for field_name in ("created_at_utc", "verified_at_utc"):
        value = payload.get(field_name)
        if value is not None:
            payload[field_name] = value.isoformat()
    payload["kind"] = str(status.kind)
    payload["state"] = str(status.state)
    payload["last_rejection"] = (
        str(status.last_rejection) if status.last_rejection is not None else None
    )
    return payload


@router.get("/guidance")
def credential_guidance(kc_console_session: str | None = Cookie(default=None)) -> dict:
    """返回 PAT 来源/权限清单与三种 Agent provider 的创建/计费/撤销/费用提示。"""
    _require_session(kc_console_session)
    return {
        "github": secrets_util.github_pat_guidance(),
        "agents": secrets_util.agent_provider_guidance(),
    }


@router.get("")
def list_credentials(kc_console_session: str | None = Cookie(default=None)) -> dict:
    """列出全部凭据状态（不含任何 secret）。"""
    _require_session(kc_console_session)
    statuses = _build_service().list_credential_statuses()
    return {"credentials": [_status_to_dict(status) for status in statuses]}


@router.post("/github")
def save_github(
    payload: GitHubCredentialPayload,
    kc_console_session: str | None = Cookie(default=None),
) -> dict:
    """保存并只读校验 GitHub PAT；失败保留旧值且不启用 runner。"""
    _require_session(kc_console_session)
    result = _build_service().save_github_credential(
        payload.repo_id, payload.owner_repo, payload.token
    )
    return {"kept_previous": result.kept_previous, "status": _status_to_dict(result.status)}


@router.post("/agent")
def save_agent(
    payload: AgentCredentialPayload,
    kc_console_session: str | None = Cookie(default=None),
) -> dict:
    """保存 Agent key（仅 provider/格式校验，不调模型）；状态为未验证。"""
    _require_session(kc_console_session)
    result = _build_service().save_agent_credential(
        payload.repo_id, payload.owner_repo, payload.provider, payload.api_key
    )
    return {"kept_previous": result.kept_previous, "status": _status_to_dict(result.status)}


@router.post("/agent/test")
def test_agent(
    payload: AgentCredentialTestPayload,
    kc_console_session: str | None = Cookie(default=None),
) -> dict:
    """仅在 ``cost_confirmed`` 为真时发起一次最小真实模型请求并据结果换凭据。"""
    _require_session(kc_console_session)
    result = _build_service().test_agent_credential(
        payload.repo_id,
        payload.owner_repo,
        payload.provider,
        payload.api_key,
        cost_confirmed=payload.cost_confirmed,
    )
    return {"kept_previous": result.kept_previous, "status": _status_to_dict(result.status)}


@router.post("/runner/{repo_id}/start")
def start_hosted_runner(
    repo_id: str,
    kc_console_session: str | None = Cookie(default=None),
) -> dict:
    """从托管 Console 启动宿主预先绑定的单仓库 runner。"""
    _require_session(kc_console_session)
    try:
        started = _build_service().start_runner(repo_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not started:
        raise HTTPException(status_code=503, detail="configured hosted runner could not be started")
    return {"repo_id": repo_id, "started": True}


@router.post("/runner/{repo_id}/stop")
def stop_hosted_runner(
    repo_id: str,
    kc_console_session: str | None = Cookie(default=None),
) -> dict:
    """通过受认证 Console 停止 runner 并清除其 tmpfs 明文目录。"""
    _require_session(kc_console_session)
    if not _build_service().stop_runner(repo_id):
        raise HTTPException(status_code=503, detail="hosted runner could not be stopped safely")
    return {"repo_id": repo_id, "stopped": True}


@router.delete("/{repo_id}/{kind}", status_code=200)
def delete_credential(
    repo_id: str,
    kind: str,
    kc_console_session: str | None = Cookie(default=None),
) -> dict:
    """删除 KedaCode 本地副本（停 runner→清密文→清 tmpfs）；提示上游 key 仍有效。"""
    _require_session(kc_console_session)
    try:
        credential_kind = HostedCredentialKind(kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown credential kind") from exc
    result = _build_service().delete_credential(repo_id, credential_kind)
    if not result.runner_stopped or not result.tmpfs_erased:
        raise HTTPException(
            status_code=503, detail="hosted credential deletion could not finish safely"
        )
    return {
        "removed": result.removed,
        "runner_stopped": result.runner_stopped,
        "tmpfs_erased": result.tmpfs_erased,
        "upstream_revoked": result.upstream_revoked,
        "notes": list(result.notes),
    }


__all__ = ["router"]

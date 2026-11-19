"""托管 Console 的真实认证路由（Issue #265，FR-2）。

仅当 ``[agent_runner.hosted].enabled`` 为真时由应用挂载，替代本机
``local_auth`` 的 no-op 会话：引导码换管理员、口令登录/登出、会话解析全部走
:class:`~backend.core.use_cases.hosted_console.HostedConsoleService`。

安全边界：

- 会话令牌是 ``HttpOnly`` cookie（``SameSite=Strict``、托管 HTTPS origin 下
  ``Secure``），前端 JS 读不到，绝不写进 URL、localStorage 或日志。
- 引导与登录失败一律返回 401 且不带任何内部细节，避免枚举。
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Cookie, HTTPException, Request, Response

from backend.core.shared.security.hosted_secrets import (
    SESSION_COOKIE_NAME,
    hosted_https_origin_ok,
)
from backend.core.use_cases.agent_runner_factory import (
    create_hosted_console_service,
    load_fresh_agent_runner_settings,
)
from backend.core.use_cases.hosted_console import HostedLoginRateLimited

router = APIRouter(tags=["hosted-auth"])


def _build_service():
    """按当前生效设置装配托管 Console 服务（每请求重载，配置可热改）。"""
    return create_hosted_console_service(load_fresh_agent_runner_settings())


def _cookie_kwargs(public_base_url: str) -> dict:
    """按 public origin 决定 cookie 的 Secure 标志（HTTPS 才 Secure）。"""
    return {
        "key": SESSION_COOKIE_NAME,
        "httponly": True,
        "samesite": "strict",
        "secure": hosted_https_origin_ok(public_base_url)
        and urlparse(public_base_url).scheme == "https",
        "path": "/",
    }


@router.get("/hosted/status")
def hosted_status() -> dict:
    """返回托管 Console 是否已完成首次引导（供前端决定进登录页还是引导页）。"""
    service = _build_service()
    return {"bootstrapped": service.is_bootstrapped()}


@router.post("/hosted/bootstrap")
def bootstrap(
    response: Response,
    payload: dict,
) -> dict:
    """用一次性引导码设置首个管理员并下发会话 cookie。"""
    service = _build_service()
    raw_code = str(payload.get("bootstrap_code", ""))
    admin_password = str(payload.get("admin_password", ""))
    try:
        session_token = service.bootstrap_and_set_admin(raw_code, admin_password)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    public_base_url = load_fresh_agent_runner_settings().hosted.public_base_url
    response.set_cookie(
        value=session_token, max_age=_session_ttl(), **_cookie_kwargs(public_base_url)
    )
    return {"user_id": "console-admin", "authenticated": True}


@router.post("/auth/login")
def login(request: Request, response: Response, payload: dict) -> dict:
    """管理员口令登录；成功下发会话 cookie。"""
    service = _build_service()
    admin_password = str(payload.get("password", ""))
    client_identity = request.client.host if request.client is not None else "unknown"
    try:
        session_token = service.login(admin_password, client_identity)
    except HostedLoginRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail=f"登录尝试过多，请在 {exc.retry_after_seconds} 秒后重试",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="invalid hosted credentials") from exc
    public_base_url = load_fresh_agent_runner_settings().hosted.public_base_url
    response.set_cookie(
        value=session_token, max_age=_session_ttl(), **_cookie_kwargs(public_base_url)
    )
    return {"user_id": "console-admin", "display_name": "console-admin", "email": ""}


@router.post("/auth/logout", status_code=204)
def logout(
    response: Response,
    kc_console_session: str | None = Cookie(default=None),
) -> Response:
    """丢弃当前会话并清除 cookie。"""
    if kc_console_session:
        _build_service().logout(kc_console_session)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@router.get("/auth/me")
def me(kc_console_session: str | None = Cookie(default=None)) -> dict:
    """解析当前会话；未认证返回 401（不再退化为 no-op 会话）。"""
    if not kc_console_session:
        raise HTTPException(status_code=401, detail="authentication required")
    principal = _build_service().resolve_session(kc_console_session)
    if principal is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    return {
        "user_id": principal.user_id,
        "display_name": principal.display_name,
        "email": principal.email,
    }


def _session_ttl() -> int:
    """当前会话有效期秒数（写入 cookie 的 max-age）。"""
    return load_fresh_agent_runner_settings().hosted.session_ttl_seconds


__all__ = ["router"]

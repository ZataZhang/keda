"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api import static as static_assets
from backend.api.backlog_sync import (
    start_backlog_scheduler,
    stop_backlog_scheduler,
)
from backend.api.monitor_sync import (
    start_monitor_scheduler,
    stop_monitor_scheduler,
)
from backend.api.routes import (
    agent_runner,
    agent_runner_console,
    agent_runner_console_evaluation,
    agent_runner_console_issues,
    agent_runner_console_runtime,
    agent_runner_console_runs,
    agent_runner_idea_inbox,
    agent_runner_lifecycle_agents,
    agent_runner_backlog,
    hosted_credentials,
    local_auth,
)
from backend.core.shared.security.hosted_secrets import SESSION_COOKIE_NAME
from backend.core.use_cases.agent_runner_factory import (
    create_hosted_console_service,
    hosted_console_enabled,
    load_fresh_agent_runner_settings,
)

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start/stop the long-running dashboard and Backlog sync loops with the app.

    周期同步是唯一的长期后台任务，必须绑定应用生命周期：路由模块 import 不
    得产生线程，服务关闭时要有界 join，避免残留幽灵线程。两条循环共享同一份
    全局同步设置，但各自持有独立的协调器与线程，互不阻塞。
    """
    start_monitor_scheduler()
    start_backlog_scheduler()
    try:
        yield
    finally:
        stop_backlog_scheduler()
        stop_monitor_scheduler()


#: 托管模式下允许匿名访问的路径前缀：CLI 复用探测、登录/引导入口与静态资源。
#: 其余全部 ``/api/*`` 必须携带有效会话 cookie，否则 401。
_HOSTED_PUBLIC_PATHS: tuple[str, ...] = (
    "/api/v1/agent-runner/console/version",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/hosted/status",
    "/api/hosted/bootstrap",
)


def _install_hosted_auth(app: FastAPI) -> None:
    """挂载托管认证中间件：未认证访问受保护 API 一律 401。

    仅当 ``[agent_runner.hosted].enabled`` 为真时调用。本机 no-op 会话不安装本
    中间件，保持 PRD 要求的本机模式零变化。
    """

    @app.middleware("http")
    async def _hosted_auth_middleware(request: Request, call_next):
        path = request.url.path
        is_public = not path.startswith("/api") or any(
            path == public or path.startswith(f"{public}/") for public in _HOSTED_PUBLIC_PATHS
        )
        if is_public or request.method == "OPTIONS":
            return await call_next(request)
        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        authenticated = False
        if session_token:
            try:
                service = create_hosted_console_service(load_fresh_agent_runner_settings())
                authenticated = service.resolve_session(session_token) is not None
            except Exception:  # noqa: BLE001 - auth must fail closed on any store error.
                _logger.exception("Hosted session resolution failed; denying request")
                authenticated = False
        if not authenticated:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        return await call_next(request)


def create_app() -> FastAPI:
    """按当前生效设置构建应用（本机模式与托管模式的唯一装配点）。

    托管模式（``[agent_runner.hosted].enabled``）用真实认证路由替换本机
    ``local_auth`` no-op，并安装未认证即 401 的中间件；本机模式保持原样。
    """
    application = FastAPI(title="keda backend", lifespan=lifespan)
    application.include_router(agent_runner.router, prefix="/api/v1")
    application.include_router(agent_runner_console.router, prefix="/api/v1")
    application.include_router(agent_runner_console_issues.router, prefix="/api/v1")
    application.include_router(agent_runner_console_runtime.router, prefix="/api/v1")
    application.include_router(agent_runner_console_runs.router, prefix="/api/v1")
    application.include_router(agent_runner_console_evaluation.router, prefix="/api/v1")
    application.include_router(agent_runner_backlog.router, prefix="/api/v1")
    application.include_router(agent_runner_lifecycle_agents.router, prefix="/api/v1")
    application.include_router(agent_runner_idea_inbox.router, prefix="/api/v1")
    settings = load_fresh_agent_runner_settings()
    if hosted_console_enabled(settings):
        from backend.api.routes import hosted_auth

        application.include_router(hosted_auth.router, prefix="/api")
        application.include_router(hosted_credentials.router, prefix="/api")
        _install_hosted_auth(application)
        _logger.info("Hosted console mode enabled: real authentication installed")
    else:
        application.include_router(local_auth.router, prefix="/api")
    return application


def _mount_console_static(application: FastAPI) -> None:
    """挂载随 wheel 分发的前端静态产物，源码模式缺产物时保留 API 服务。

    必须在全部 ``include_router`` 之后调用：``/`` 挂载是兜底路由，先注册
    的 ``/api/*`` 路由优先匹配，不会被静态文件遮蔽。

    判定用 :func:`backend.api.static.console_panel_dir_if_available`，与
    ``kc console`` 的启动预检同源：入口文件不在就不挂 ``/``，免得一个只同步
    了一半的产物目录被当成就绪，用户拿到的还是 404。
    """
    console_panel_dir = static_assets.console_panel_dir_if_available()
    if console_panel_dir is None:
        _logger.warning(
            "Console static assets are missing or incomplete at "
            "backend.api.static/console; the dashboard is unavailable. From a "
            "source checkout, run `just console-sync` from the repository root, "
            "or install a release wheel with the assets bundled."
        )
        return
    application.mount(
        "/",
        StaticFiles(directory=str(console_panel_dir), html=True),
        name="console",
    )


app = create_app()
_mount_console_static(app)

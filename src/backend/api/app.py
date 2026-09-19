"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.api.monitor_sync import (
    start_monitor_scheduler,
    stop_monitor_scheduler,
)
from backend.api.routes import (
    agent_runner,
    agent_runner_console,
    agent_runner_idea_inbox,
    agent_runner_lifecycle_agents,
    agent_runner_roadmap,
    local_auth,
)

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start/stop the long-running dashboard sync loop with the app.

    周期同步是唯一的长期后台任务，必须绑定应用生命周期：路由模块 import 不
    得产生线程，服务关闭时要有界 join，避免残留幽灵线程。
    """
    start_monitor_scheduler()
    try:
        yield
    finally:
        stop_monitor_scheduler()


app = FastAPI(title="keda backend", lifespan=lifespan)
app.include_router(agent_runner.router, prefix="/api/v1")
app.include_router(agent_runner_console.router, prefix="/api/v1")
app.include_router(agent_runner_roadmap.router, prefix="/api/v1")
app.include_router(agent_runner_lifecycle_agents.router, prefix="/api/v1")
app.include_router(agent_runner_idea_inbox.router, prefix="/api/v1")
app.include_router(local_auth.router, prefix="/api")


def _mount_console_static() -> None:
    """挂载随 wheel 分发的前端静态产物（源码模式缺产物时安静跳过）。

    必须在全部 ``include_router`` 之后调用：``/`` 挂载是兜底路由，先注册
    的 ``/api/*`` 路由优先匹配，不会被静态文件遮蔽。
    """
    from importlib.resources import files

    console_dir = files("backend.api.static").joinpath("console")
    if not console_dir.is_dir():
        _logger.warning(
            "Console static assets not found at backend.api.static/console; "
            "serving API-only mode. Use a release wheel (assets bundled) or "
            "build frontend-public first: pnpm --filter frontend-public build "
            "&& copy out/ into src/backend/api/static/console/."
        )
        return
    app.mount("/", StaticFiles(directory=str(console_dir), html=True), name="console")


_mount_console_static()

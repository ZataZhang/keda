"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
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
    local_auth,
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


app = FastAPI(title="keda backend", lifespan=lifespan)
app.include_router(agent_runner.router, prefix="/api/v1")
app.include_router(agent_runner_console.router, prefix="/api/v1")
app.include_router(agent_runner_console_issues.router, prefix="/api/v1")
app.include_router(agent_runner_console_runtime.router, prefix="/api/v1")
app.include_router(agent_runner_console_runs.router, prefix="/api/v1")
app.include_router(agent_runner_console_evaluation.router, prefix="/api/v1")
app.include_router(agent_runner_backlog.router, prefix="/api/v1")
app.include_router(agent_runner_lifecycle_agents.router, prefix="/api/v1")
app.include_router(agent_runner_idea_inbox.router, prefix="/api/v1")
app.include_router(local_auth.router, prefix="/api")


def _mount_console_static() -> None:
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
    app.mount(
        "/",
        StaticFiles(directory=str(console_panel_dir), html=True),
        name="console",
    )


_mount_console_static()

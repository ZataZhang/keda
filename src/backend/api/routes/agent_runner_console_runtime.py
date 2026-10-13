"""统一运行时看板与桌面 companion 的 Console 端点。

从 ``agent_runner_console.py`` 按行数红线拆出的新模块（与
``agent_runner_console_issues.py`` / ``agent_runner_console_runs.py`` 同一先例）：
本文件只做来源校验、参数解析与用例调用，聚合语义全部在
:mod:`backend.core.use_cases.runtime_dashboard`。

两条边界写在这里：

- **任务列表只来自已注册仓库的 Console 观测路径**（监控快照 + 运行/attempt/
  生命周期账本），不掺托管进程日志，也不新增数据源；
- **桌面窗口只允许当前 loopback Console 的同源请求启动**：绑定地址与端口取自
  ASGI ``scope["server"]``（由 uvicorn 写入，客户端改不了，伪造 ``Host`` 也改不了），
  再要求 ``Origin`` 与该绑定同 scheme、同端口、host 属于回环别名且不带路径；
  因此浏览器扩展、其他本地页面、DNS rebinding 都拿不到启动系统窗口的能力。
  校验通过后**返回绑定侧 origin**，窗口内容固定为
  ``{绑定 origin}/app/companion/``，不接受调用方给的 URL、路径、命令，也不回显
  请求头里的 host。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from backend.core.shared.interfaces.desktop_companion import (
    DesktopCompanionLauncher,
    DesktopCompanionSnapshot,
)
from backend.core.use_cases.agent_runner_factory import (
    create_console_store,
    load_fresh_agent_runner_settings,
    resolve_repository_targets_with_diagnostics,
)
from backend.core.use_cases.desktop_companion import create_desktop_companion_launcher
from backend.core.use_cases.monitor_snapshots import get_snapshot_overview
from backend.core.use_cases.runtime_dashboard import build_runtime_task_summary

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-runner-console-runtime"])

#: 本 Console 进程的身份：helper 用它识别「上一个 Console 留下的孤儿窗口」。
#: 每次进程启动重新生成，因此重启 Console 一定会让旧窗口自行退出。
CONSOLE_SESSION = uuid.uuid4().hex

#: 允许的 loopback 主机名：``localhost`` 与 ``127.0.0.1`` 指向同一个 Console。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _enabled_repo_ids() -> list[str]:
    """取当前 registry 中启用仓库的标识列表，作为摘要的展示白名单。"""
    settings = load_fresh_agent_runner_settings()
    contexts, _failures = resolve_repository_targets_with_diagnostics(settings)
    return [context.repo_id for context in contexts]


def _format_loopback_origin(host: str, port: int) -> str:
    """拼出回环 origin；IPv6 字面量必须加方括号，否则不是合法 URL。"""
    hostname = f"[{host}]" if ":" in host else host
    return f"http://{hostname}:{port}"


def _bound_loopback_server(request: Request) -> tuple[str, int]:
    """取 uvicorn 实际绑定的回环 ``host/port``（不读 ``Host`` 头）。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ``tuple[str, int]``：绑定的主机与端口。

    Raises:
        HTTPException: 503——ASGI ``scope["server"]`` 缺失或形状异常；403——绑定
            地址不是回环。
    """
    server = request.scope.get("server")
    if not server or len(server) != 2:
        raise HTTPException(status_code=503, detail="无法确定 Console 的绑定地址。")
    bound_host, bound_port = str(server[0]), int(server[1])
    if bound_host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="桌面窗口仅允许本机回环 Console 启动。")
    return bound_host, bound_port


def _trusted_console_origin(request: Request) -> str:
    """校验请求来自当前 Console 的同源页面，返回窗口加载用的权威 origin。

    ``Origin`` 头只用于**判定同源**，不会成为窗口地址：回环别名（``localhost`` 与
    ``127.0.0.1``）指向同一个 Console，但 helper 的 argv 与 WebView 加载的 URL 必须
    写后端自己绑定的那个 origin，客户端改不了。

    Args:
        request: FastAPI 请求对象。

    Returns:
        由绑定地址推出的、形如 ``http://127.0.0.1:8313`` 的已校验 origin。

    Raises:
        HTTPException: 403 或 503——见 :func:`_bound_loopback_server`，以及缺少
            ``Origin``、origin 与当前 Console 的 scheme/host 别名/端口不一致。
    """
    bound_host, bound_port = _bound_loopback_server(request)

    raw_origin = (request.headers.get("origin") or "").strip()
    if not raw_origin:
        raise HTTPException(status_code=403, detail="缺少 Origin，无法确认同源，拒绝启动桌面窗口。")
    parsed = urlsplit(raw_origin)
    try:
        request_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        # 端口写成非数字或越界时 urlsplit 直接抛错：按「无法确认同源」拒绝，
        # 不能让伪造的 Origin 头把请求打成 500。
        raise HTTPException(
            status_code=403,
            detail=f"Origin {raw_origin!r} 端口无法解析，拒绝启动桌面窗口。",
        ) from exc
    if (
        parsed.scheme != "http"
        or (parsed.hostname or "") not in _LOOPBACK_HOSTS
        or request_port != bound_port
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Origin {raw_origin!r} 与当前本机 Console 不一致，拒绝启动桌面窗口。",
        )
    return _format_loopback_origin(bound_host, bound_port)


def _trusted_bound_origin(request: Request) -> str:
    """由 ASGI 绑定地址推出 Console origin，供只读 GET 使用；非回环时返回空串。"""
    server = request.scope.get("server")
    if not server or len(server) != 2:
        return ""
    bound_host, bound_port = str(server[0]), int(server[1])
    if bound_host not in _LOOPBACK_HOSTS:
        return ""
    return _format_loopback_origin(bound_host, bound_port)


def _companion_launcher() -> DesktopCompanionLauncher:
    """取本进程共享的 companion launcher（工单计数必须跨请求保持）。"""
    return create_desktop_companion_launcher(console_session=CONSOLE_SESSION)


def _companion_payload(snapshot: DesktopCompanionSnapshot, console_origin: str) -> dict[str, Any]:
    """把 companion 快照转成前端与 helper 共用的 JSON 载荷。"""
    payload = asdict(snapshot)
    payload["hint"] = snapshot.hint
    payload["window_exists"] = snapshot.window_exists
    # 只有确实存在窗口时才给出加载地址，避免前端把「可以开」画成「已经开着」。
    payload["companion_url"] = (
        f"{console_origin}/app/companion/" if snapshot.window_exists else None
    )
    return payload


@router.get("/agent-runner/console/runtime/tasks")
def get_runtime_tasks() -> dict[str, Any]:
    """返回统一运行看板任务摘要：状态分桶、身份、阶段、执行者与耗时。

    改动树触达读数**不在本响应里**：唯一来源仍是现有
    ``GET /agent-runner/backlog/prds/{encoded_path}/progress`` 契约，前端按
    ``prd_path`` 取数，避免出现第二份影响树检测逻辑。
    """
    store = create_console_store()
    enabled_repo_ids = _enabled_repo_ids()
    snapshot_overview = get_snapshot_overview(store, enabled_repo_ids=enabled_repo_ids)
    summary = build_runtime_task_summary(
        snapshot_overview=snapshot_overview,
        run_history_store=store,
        prd_lifecycle_store=store,
        enabled_repo_ids=enabled_repo_ids,
    )
    payload = asdict(summary)
    payload["tasks"] = [{**asdict(task), "task_key": task.task_key} for task in summary.tasks]
    return payload


@router.get("/agent-runner/console/desktop-companion/status")
def get_desktop_companion_status(request: Request) -> dict[str, Any]:
    """返回桌面 companion 状态；helper 与网页看板共用这个只读探针。

    GET 不要求 ``Origin``（浏览器对同源 GET 可能不带该头），窗口动作端点才做
    同源校验。
    """
    console_origin = _trusted_bound_origin(request)
    snapshot = _companion_launcher().snapshot(console_origin=console_origin)
    return _companion_payload(snapshot, console_origin)


@router.post("/agent-runner/console/desktop-companion/window")
def open_desktop_companion_window(request: Request) -> dict[str, Any]:
    """开启或聚焦桌面小窗：仅接受当前 loopback Console 的同源 POST。"""
    console_origin = _trusted_console_origin(request)
    snapshot = _companion_launcher().open_window(console_origin=console_origin)
    return _companion_payload(snapshot, console_origin)


@router.post("/agent-runner/console/desktop-companion/window/close")
def close_desktop_companion_window(request: Request) -> dict[str, Any]:
    """请求桌面小窗自行关窗退出；同样要求同源，避免跨页面触发。"""
    console_origin = _trusted_console_origin(request)
    snapshot = _companion_launcher().close_window()
    return _companion_payload(snapshot, console_origin)


__all__ = [
    "CONSOLE_SESSION",
    "close_desktop_companion_window",
    "get_desktop_companion_status",
    "get_runtime_tasks",
    "open_desktop_companion_window",
    "router",
]

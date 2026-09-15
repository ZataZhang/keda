"""Typer commands under ``iar console``.

提供 :func:`console_callback`：前台启动 FastAPI 后端并托管随 wheel
分发的前端静态产物。端口解析、浏览器拉起都收敛在本模块内的纯函数上，
便于单测直接覆盖；命令本身不触碰 ``~/.iar/processes.json``，与面板
托管的 runner 进程互不干扰。
"""

from __future__ import annotations

import socket
import threading
import webbrowser
from typing import Annotated

import typer
import uvicorn

from backend.api.cli_typer_app import console_app, error_console
from backend.core.use_cases.agent_runner_factory import load_fresh_agent_runner_settings

#: 管理终端的监听地址，**硬编码为回环地址、不可配置**。
#:
#: 面板带写操作而认证是空实现，监听地址即这套系统事实上的唯一访问控制。
#: 这里刻意不做成 CLI 参数或 ``[agent_runner.console]`` 配置项：任何一个
#: 逃生口都意味着一行配置就能把无认证、可写的面板暴露给整个网段。需要远程
#: 访问请走 SSH 端口转发（``ssh -L 8313:127.0.0.1:8313 <host>``）。
CONSOLE_HOST = "127.0.0.1"

#: 未显式指定 ``--port`` 时，从配置默认端口起最多顺延探测的端口数。
_PORT_SCAN_WINDOW = 20

#: 拉起浏览器前等待 uvicorn 完成监听的秒数。
_BROWSER_OPEN_DELAY_SECONDS = 1.0


class ConsolePortUnavailableError(RuntimeError):
    """显式指定或扫描窗口内没有任何可用端口时抛出。"""


def _port_is_available(host: str, port: int) -> bool:
    """探测 ``host:port`` 当前是否可以绑定监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        try:
            probe_socket.bind((host, port))
        except OSError:
            return False
    return True


def resolve_console_port(*, host: str, explicit_port: int | None, default_port: int) -> int:
    """解析 ``iar console`` 实际监听的端口。

    Args:
        host: 监听地址（配置项，固定 ``127.0.0.1``）。
        explicit_port: 用户显式传入的 ``--port``；被占用时直接报错，
            不做顺延（显式指定意味着用户对端口号有明确预期）。
        default_port: 配置里的默认端口；未显式指定时从它开始顺延探测。

    Returns:
        可绑定监听的端口号。

    Raises:
        ConsolePortUnavailableError: 显式端口被占用，或扫描窗口内没有
            任何可用端口。
    """
    if explicit_port is not None:
        if _port_is_available(host, explicit_port):
            return explicit_port
        raise ConsolePortUnavailableError(
            f"Port {explicit_port} on {host} is already in use; "
            "omit --port to auto-pick a free port."
        )
    for candidate_port in range(default_port, default_port + _PORT_SCAN_WINDOW):
        if _port_is_available(host, candidate_port):
            return candidate_port
    raise ConsolePortUnavailableError(
        f"No free port found in {default_port}-{default_port + _PORT_SCAN_WINDOW - 1} "
        f"on {host}; free one up or pass --port explicitly."
    )


def launch_console(*, host: str, port: int, open_browser: bool) -> None:
    """前台阻塞启动 console 服务，按需延迟拉起浏览器。

    Args:
        host: 监听地址。
        port: 已解析的监听端口。
        open_browser: 是否在 uvicorn 完成监听前用 ``webbrowser.open``
            预约打开管理终端首页。
    """
    console_url = f"http://{host}:{port}/"
    typer.echo(f"iar console listening on {console_url} (Ctrl+C to stop)")
    if open_browser:
        browser_timer = threading.Timer(
            _BROWSER_OPEN_DELAY_SECONDS, webbrowser.open, args=(console_url,)
        )
        browser_timer.daemon = True
        browser_timer.start()
    uvicorn.run("backend.api.app:app", host=host, port=port)


@console_app.callback(invoke_without_command=True)
def console_callback(
    ctx: typer.Context,
    port: Annotated[
        int | None,
        typer.Option(
            "--port",
            help=(
                "Port to listen on. Omit to auto-pick starting from "
                "[agent_runner.console].port; explicit ports never shift."
            ),
        ),
    ] = None,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Do not open the console in a browser tab."),
    ] = False,
) -> None:
    """启动 iar 管理终端（API + 内置前端面板），前台运行。"""
    if ctx.invoked_subcommand is not None:
        return
    console_settings = load_fresh_agent_runner_settings().console
    try:
        resolved_port = resolve_console_port(
            host=CONSOLE_HOST,
            explicit_port=port,
            default_port=console_settings.port,
        )
    except ConsolePortUnavailableError as exc:
        error_console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    launch_console(
        host=CONSOLE_HOST,
        port=resolved_port,
        open_browser=not no_browser,
    )


__all__ = [
    "CONSOLE_HOST",
    "ConsolePortUnavailableError",
    "console_callback",
    "launch_console",
    "resolve_console_port",
]

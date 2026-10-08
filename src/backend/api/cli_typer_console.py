"""Typer commands under ``kc console``.

提供 :func:`console_callback`：前台启动 FastAPI 后端并托管随 wheel
分发的前端静态产物。端口解析、现有实例探测、浏览器拉起都收敛在本模块内的
纯函数上，便于单测直接覆盖；命令本身不触碰 ``~/.kedacode/processes.json``，
与面板托管的 runner 进程互不干扰。

重复调用不再起第二个实例：启动前先在候选端口上探测是否已有 console 在服务，
命中则直接打开它当前监听的 URL 并退出，避免默认端口被自己占住时顺延开第二个。
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import urllib.request
import webbrowser
from collections.abc import Iterable
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

#: 判定“这个端口上是不是已经在跑 kc console”的探测路径：console 后端自带的
#: 版本查询路由，返回 ``{"version": ...}``。选它是因为它随面板同源存在，
#: 不必为 CLI 探测再新增一层对外 HTTP 表面。
_CONSOLE_PROBE_PATH = "/api/v1/agent-runner/console/version"

#: 单个端口探测的超时秒数。回环上的正常响应是毫秒级，这个值只给“端口被一个
#: 连得上但不回数据的本机服务占住”的异常场景兜底，避免 CLI 卡死。
_CONSOLE_PROBE_TIMEOUT_SECONDS = 0.5


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


def console_url_for(host: str, port: int) -> str:
    """拼出 console 面板首页地址。"""
    return f"http://{host}:{port}/"


def _is_running_console(host: str, port: int) -> bool:
    """探测 ``host:port`` 上是否已有 ``kc console`` 在提供服务。

    端口绑不上只能说明“有东西在听”，无法区分我们自己的 console 和本机另一个
    占端口的服务，因此用版本端点做身份判定：能取回带 ``version`` 键的 JSON
    才算命中。任何连接失败、超时、协议错乱（对端回了非 HTTP 字节）或非 JSON
    响应都判为未命中。

    Args:
        host: 监听地址（固定回环）。
        port: 已知被占用的端口。

    Returns:
        该端口上确实是 kc console 时为 ``True``。
    """
    probe_url = f"http://{host}:{port}{_CONSOLE_PROBE_PATH}"
    try:
        with urllib.request.urlopen(probe_url, timeout=_CONSOLE_PROBE_TIMEOUT_SECONDS) as response:
            version_payload = json.loads(response.read())
    except (OSError, ValueError, http.client.HTTPException):
        return False
    return isinstance(version_payload, dict) and "version" in version_payload


def find_running_console(*, host: str, candidate_ports: Iterable[int]) -> int | None:
    """在候选端口里找出已在监听的 ``kc console`` 实例。

    只对绑不上的端口发起 HTTP 探测：干净机器上默认端口可直接绑定，一个请求都
    不会发出。

    Args:
        host: 监听地址（固定回环）。
        candidate_ports: 待探测端口，按升序遍历，命中即返回最早的实例。

    Returns:
        现有 console 实际监听的端口；候选里没有 console 在跑时返回 ``None``。
    """
    for candidate_port in candidate_ports:
        if _port_is_available(host, candidate_port):
            continue
        if _is_running_console(host, candidate_port):
            return candidate_port
    return None


def reopen_running_console(*, host: str, port: int, open_browser: bool) -> None:
    """复用已在运行的 console：告知其 URL，并按需重新打开浏览器面板。

    Args:
        host: 现有实例的监听地址。
        port: 现有实例实际监听的端口。
        open_browser: 是否调用 ``webbrowser.open`` 重新打开面板；``--no-browser``
            时只打印 URL，供脚本或远程转发场景自行取用。
    """
    console_url = console_url_for(host, port)
    typer.echo(f"kc console is already running on {console_url} (reopening, no new instance)")
    if open_browser:
        webbrowser.open(console_url)


def console_candidate_ports(*, explicit_port: int | None, default_port: int) -> Iterable[int]:
    """返回本次调用需要探测“是否已有 console”的端口集合。

    显式 ``--port`` 时只关心那个端口；缺省时覆盖与端口顺延相同的扫描窗口，
    这样现有实例当初因端口占用而顺延过也仍能被找回来。

    Args:
        explicit_port: 用户显式传入的 ``--port``。
        default_port: 配置里的默认端口，扫描窗口起点。

    Returns:
        升序的待探测端口集合。
    """
    if explicit_port is not None:
        return [explicit_port]
    return range(default_port, default_port + _PORT_SCAN_WINDOW)


def resolve_console_port(*, host: str, explicit_port: int | None, default_port: int) -> int:
    """解析新 console 实例实际监听的端口。

    调用前 :func:`find_running_console` 已确认候选端口上没有 console 在跑，
    因此这里的“端口被占用”只剩本机其它服务的场景。

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
    console_url = console_url_for(host, port)
    typer.echo(f"kc console listening on {console_url} (Ctrl+C to stop)")
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
                "Port to listen on. Omit to reuse a running console or "
                "auto-pick starting from [agent_runner.console].port; "
                "explicit ports never shift."
            ),
        ),
    ] = None,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Do not open the console in a browser tab."),
    ] = False,
) -> None:
    """启动 KedaCode 管理终端（API + 内置前端面板），前台运行。

    已有 console 在跑时不开第二个实例：直接打开它监听的 URL 后退出，因此关掉
    浏览器再敲一次 ``kc console`` 就能重新看到面板，无需重启服务。想并行跑
    第二个 console（例如另一套状态目录），显式指定一个空闲 ``--port``。
    """
    if ctx.invoked_subcommand is not None:
        return
    console_settings = load_fresh_agent_runner_settings().console
    running_console_port = find_running_console(
        host=CONSOLE_HOST,
        candidate_ports=console_candidate_ports(
            explicit_port=port,
            default_port=console_settings.port,
        ),
    )
    if running_console_port is not None:
        reopen_running_console(
            host=CONSOLE_HOST,
            port=running_console_port,
            open_browser=not no_browser,
        )
        return
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
    "console_candidate_ports",
    "console_url_for",
    "console_callback",
    "find_running_console",
    "launch_console",
    "reopen_running_console",
    "resolve_console_port",
]

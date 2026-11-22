"""用真实回环 HTTP 和 CLI 确认 Console 复用已有实例。"""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from backend.api import cli_typer_console
from backend.api.cli_typer_app import console_app

HOST = "127.0.0.1"
VERSION_ENDPOINT = "/api/v1/agent-runner/console/version"


class _ConsoleHandler(BaseHTTPRequestHandler):
    """提供现有 Console 使用的本地版本入口。"""

    def do_GET(self) -> None:
        if self.path != VERSION_ENDPOINT:
            self.send_error(404)
            return
        response = b'{"version":"0.5.0"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *arguments) -> None:
        """测试用服务无需写访问日志。"""


class _OtherHandler(_ConsoleHandler):
    """在端口上提供另一个服务，没有 Console 的版本入口。"""

    def do_GET(self) -> None:
        self.send_error(404)


@contextmanager
def _listening(handler_type):
    with ThreadingHTTPServer((HOST, 0), handler_type) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            worker.join(timeout=5)


@pytest.fixture
def launch_tracking(monkeypatch: pytest.MonkeyPatch):
    """只观察浏览器和服务启动端口，不打开用户浏览器或另起生产服务。"""
    browsers = []
    launches = []
    monkeypatch.setattr(cli_typer_console.webbrowser, "open", browsers.append)
    monkeypatch.setattr(
        cli_typer_console.uvicorn,
        "run",
        lambda _app, *, host, port: launches.append((host, port)),
    )
    return browsers, launches


def test_console_reopens_the_url_on_an_explicit_running_port(launch_tracking) -> None:
    """已运行端口直接重开网页并退出，不重复启动服务。"""
    browsers, launches = launch_tracking
    with _listening(_ConsoleHandler) as running_port:
        result = CliRunner().invoke(console_app, ["--port", str(running_port)])
    assert result.exit_code == 0, result.output
    assert browsers == [f"http://{HOST}:{running_port}/"]
    assert launches == []


def test_console_reuses_configured_listener_without_opening_browser(
    monkeypatch: pytest.MonkeyPatch,
    launch_tracking,
) -> None:
    """默认端口已有实例，--no-browser 输出它的 URL 并结束。"""
    browsers, launches = launch_tracking
    with _listening(_ConsoleHandler) as running_port:
        monkeypatch.setattr(
            cli_typer_console,
            "load_fresh_agent_runner_settings",
            lambda: SimpleNamespace(console=SimpleNamespace(port=running_port)),
        )
        result = CliRunner().invoke(console_app, ["--no-browser"])
    assert result.exit_code == 0, result.output
    assert f"http://{HOST}:{running_port}/" in result.output
    assert browsers == []
    assert launches == []


def test_console_leaves_unrelated_service_alone(launch_tracking) -> None:
    """明确指定的端口被别的服务占用时，不打开该服务或另拉实例。"""
    browsers, launches = launch_tracking
    with _listening(_OtherHandler) as running_port:
        result = CliRunner().invoke(console_app, ["--port", str(running_port), "--no-browser"])
    assert result.exit_code != 0
    assert not browsers
    assert not launches

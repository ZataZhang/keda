"""Tests for the ``kc console`` command: port resolution, reuse detection, browser flag."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

import backend.api.app as api_app
import backend.api.cli_typer_console as cli_console
import backend.api.static as static_assets
from backend.api.app import app
from backend.api.cli_typer_console import (
    CONSOLE_HOST,
    ConsolePortUnavailableError,
    console_candidate_ports,
    console_url_for,
    find_running_console,
    launch_console,
    resolve_console_port,
)
from backend.api.cli_typer_app import console_app
from backend.infrastructure.config.settings import (
    AgentRunnerConsoleSettings,
)
from backend.infrastructure.config import agent_runner_settings


def _stub_port_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    occupied: set[int],
    console_ports: set[int] = frozenset(),
) -> None:
    """Make port probing deterministic instead of depending on real sockets.

    Asserting against a fixed port band is inherently flaky: a second
    ``kc console``, a worktree evidence run, or any unrelated local service can
    hold one of those ports and turn this test file red. Tests that exercise the
    *selection* logic stub the probe; ``test_real_probe_sees_a_bound_port`` and
    ``test_real_probe_identifies_a_console`` keep real-socket checks of the
    probes themselves. ``console_ports`` 同时钉死“该端口上的服务是不是 console”
    这一层判定，避免单元测试真的向回环发 HTTP 请求。
    """
    monkeypatch.setattr(cli_console, "_port_is_available", lambda host, port: port not in occupied)
    monkeypatch.setattr(
        cli_console, "_is_running_console", lambda host, port: port in console_ports
    )


def _stub_console_settings(monkeypatch: pytest.MonkeyPatch, *, port: int) -> None:
    """钉死命令读到的缺省端口，避免测试跟随开发者本机 config.toml 漂移。"""
    monkeypatch.setattr(
        cli_console,
        "load_fresh_agent_runner_settings",
        lambda: type("_Settings", (), {"console": AgentRunnerConsoleSettings(port=port)})(),
    )


def _stub_console_static_dir(monkeypatch: pytest.MonkeyPatch, *, directory: Path) -> None:
    """让静态产物检查使用指定目录（挂载与 CLI 预检共用同一个解析入口）。"""
    monkeypatch.setattr(static_assets, "console_static_dir", lambda: directory)


def _make_console_panel(directory: Path) -> Path:
    """造出「面板产物已就位」的目录，返回该目录便于喂给上面的 stub。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text("<html><body>panel</body></html>\n", encoding="utf-8")
    return directory


# 面板产物不可用时的修复指引：问题 → console-sync → 发布 wheel → --api-only 逃生口。
_EXPECTED_REPAIR_GUIDANCE = [
    "Console frontend static assets are missing or incomplete: "
    "no index.html in backend.api.static/console.",
    "In a source checkout, run `just console-sync` from the repository root.",
    "Alternatively, install a release wheel; it includes the frontend assets.",
    "If you only need the API, pass `kc console --api-only`.",
]


def _assert_repair_guidance_is_one_contiguous_block(output: str) -> None:
    """断言四行指引连续出现，用户顺着读就能照做，不会被别的启动日志打断。"""
    output_lines = [line for line in output.splitlines() if line.strip()]
    block_start = output_lines.index(_EXPECTED_REPAIR_GUIDANCE[0])
    assert output_lines[block_start : block_start + len(_EXPECTED_REPAIR_GUIDANCE)] == (
        _EXPECTED_REPAIR_GUIDANCE
    )


def _mounted_panel_directories(mounted_app: FastAPI) -> list[str]:
    """列出挂在 ``/`` 兜底路由上的静态面板目录；没有挂载时为空。"""
    return [
        route.app.directory for route in mounted_app.routes if getattr(route, "path", None) == ""
    ]


def _fail_uvicorn_run(*args, **kwargs):
    raise AssertionError("uvicorn.run must not be reached")


@contextmanager
def _serving(handler_class: type[BaseHTTPRequestHandler]) -> Iterator[int]:
    """在回环临时端口上起一个 HTTP 服务，yield 实际端口号。"""
    fake_server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    serve_thread = threading.Thread(target=fake_server.serve_forever, daemon=True)
    serve_thread.start()
    try:
        yield int(fake_server.server_address[1])
    finally:
        fake_server.shutdown()
        serve_thread.join(timeout=5)
        fake_server.server_close()


class _JsonApiHandler(BaseHTTPRequestHandler):
    """对任意 GET 固定回一段 JSON 的假服务，用来模拟端口上已有其它 HTTP 服务。"""

    payload: ClassVar[dict] = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 规定的命名
        response_bytes = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def log_message(self, *args) -> None:
        """静音测试服务器的访问日志。"""


class _ConsoleLikeHandler(_JsonApiHandler):
    """形态与 console 版本端点一致的假服务。"""

    payload = {"version": "0.0.0-probe-fixture"}


class _ForeignApiHandler(_JsonApiHandler):
    """回环上另一个会答 JSON、但没有版本键的服务。"""

    payload = {"service": "something-else"}


def _bind_ephemeral_port() -> tuple[socket.socket, int]:
    """Bind a listening socket on an OS-assigned port; return it with its port."""
    listening_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listening_socket.bind(("127.0.0.1", 0))
    listening_socket.listen(1)
    return listening_socket, int(listening_socket.getsockname()[1])


class TestResolveConsolePort:
    """resolve_console_port 的顺延与显式端口语义。"""

    def test_explicit_free_port_returned_as_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A free explicit port is honored verbatim."""
        _stub_port_probe(monkeypatch, occupied=set())
        assert (
            resolve_console_port(host="127.0.0.1", explicit_port=58321, default_port=8313) == 58321
        )

    def test_explicit_occupied_port_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An occupied explicit port must fail loudly instead of shifting."""
        _stub_port_probe(monkeypatch, occupied={58322})
        with pytest.raises(ConsolePortUnavailableError, match="already in use"):
            resolve_console_port(host="127.0.0.1", explicit_port=58322, default_port=8313)

    def test_default_port_free_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without --port the configured default port is used when free."""
        _stub_port_probe(monkeypatch, occupied=set())
        assert (
            resolve_console_port(host="127.0.0.1", explicit_port=None, default_port=58323) == 58323
        )

    def test_scans_forward_from_default_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without --port, an occupied default port shifts to the next free one."""
        _stub_port_probe(monkeypatch, occupied={58324})
        resolved = resolve_console_port(host="127.0.0.1", explicit_port=None, default_port=58324)
        assert resolved == 58325

    def test_scan_window_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No free port inside the scan window must raise, not bind blindly."""
        monkeypatch.setattr(cli_console, "_port_is_available", lambda host, port: False)
        with pytest.raises(ConsolePortUnavailableError, match="No free port"):
            resolve_console_port(host="127.0.0.1", explicit_port=None, default_port=8313)


def test_real_probe_sees_a_bound_port() -> None:
    """The production probe reports a port that is really being listened on.

    One real-socket check is kept so the stubbed selection tests above cannot
    drift away from actual socket behavior; the port is OS-assigned, so this
    test never collides with a fixed port band.
    """
    listening_socket, occupied_port = _bind_ephemeral_port()
    try:
        assert cli_console._port_is_available("127.0.0.1", occupied_port) is False
    finally:
        listening_socket.close()


def test_real_probe_identifies_a_console_over_http() -> None:
    """真发一次 HTTP 请求：探测要认得回环上形态与控制台一致的服务。"""
    with _serving(_ConsoleLikeHandler) as console_like_port:
        assert cli_console._is_running_console("127.0.0.1", console_like_port) is True


def test_real_probe_rejects_a_foreign_json_service() -> None:
    """端口上有 HTTP 服务不等于有 console：没有版本键就不能认领。"""
    with _serving(_ForeignApiHandler) as foreign_port:
        assert cli_console._is_running_console("127.0.0.1", foreign_port) is False


def test_real_probe_gives_up_on_a_silent_listener() -> None:
    """连得上但不回数据的本机服务必须超时放行，否则 CLI 会挂在探测上。"""
    listening_socket, silent_port = _bind_ephemeral_port()
    try:
        assert cli_console._is_running_console("127.0.0.1", silent_port) is False
    finally:
        listening_socket.close()


def test_real_probe_gives_up_on_a_garbage_speaker() -> None:
    """连得上、回数据但不是 HTTP 的本机服务（裸 TCP 协议）也不能让探测抛错。"""

    def _reply_garbage_once() -> None:
        connection, _ = listening_socket.accept()
        try:
            connection.recv(1024)
            connection.sendall(b"NOT-HTTP-GARBAGE\r\n")
        finally:
            connection.close()

    listening_socket, garbage_port = _bind_ephemeral_port()
    try:
        reply_thread = threading.Thread(target=_reply_garbage_once, daemon=True)
        reply_thread.start()
        assert cli_console._is_running_console("127.0.0.1", garbage_port) is False
        reply_thread.join(timeout=5)
    finally:
        listening_socket.close()


def test_probe_path_exists_on_the_console_app() -> None:
    """探测路径必须真实存在于后端路由里。

    这条是整套复用逻辑的地基：路径漂移时判定永远不命中，``kc console``
    会静默退回“再起一个实例”，而所有 stub 掉的单测照样全绿。
    """
    probe_response = TestClient(app).get(cli_console._CONSOLE_PROBE_PATH)
    assert probe_response.status_code == 200
    assert "version" in probe_response.json()


def test_static_mount_and_cli_check_share_the_console_static_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """挂载与 CLI 预检必须共同使用 static 包提供的资源路径。"""
    static_dir = _make_console_panel(tmp_path / "console")
    resolved_directories: list[Path] = []

    def _shared_console_static_dir() -> Path:
        resolved_directories.append(static_dir)
        return static_dir

    mounted_app = FastAPI()
    monkeypatch.setattr(api_app, "app", mounted_app)
    monkeypatch.setattr(static_assets, "console_static_dir", _shared_console_static_dir)
    api_app._mount_console_static()

    mounted_static_route = mounted_app.router.routes[-1]
    assert mounted_static_route.path == ""
    assert mounted_static_route.app.directory == str(static_dir)

    _stub_console_settings(monkeypatch, port=58429)
    _stub_port_probe(monkeypatch, occupied=set())
    monkeypatch.setattr(cli_console, "launch_console", lambda **kwargs: None)
    result = CliRunner().invoke(console_app, ["--no-browser"])

    assert result.exit_code == 0
    assert resolved_directories == [static_dir, static_dir]


class TestConsoleCandidatePorts:
    """console_candidate_ports 的探测范围。"""

    def test_explicit_port_is_the_only_candidate(self) -> None:
        """显式 --port 只问那个端口，不去别处找一个跑着的实例。"""
        assert console_candidate_ports(explicit_port=58400, default_port=8313) == [58400]

    def test_default_port_covers_the_shift_window(self) -> None:
        """缺省探测范围与端口顺延窗口一致，顺延过的现有实例才能被找回。"""
        assert list(console_candidate_ports(explicit_port=None, default_port=58401)) == list(
            range(58401, 58401 + cli_console._PORT_SCAN_WINDOW)
        )


class TestFindRunningConsole:
    """find_running_console 把“绑不上 + 认得出 console”的端口挑出来。"""

    def test_free_candidates_are_never_probed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """干净机器上一个 HTTP 请求都不该发。"""
        _stub_port_probe(monkeypatch, occupied=set())
        monkeypatch.setattr(
            cli_console,
            "_is_running_console",
            lambda host, port: (_ for _ in ()).throw(
                AssertionError("must not HTTP-probe a bindable port")
            ),
        )
        assert find_running_console(host="127.0.0.1", candidate_ports=range(58410, 58415)) is None

    def test_returns_the_console_holding_the_default_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """默认端口被自己的 console 占住时，返回它而不是顺延。"""
        _stub_port_probe(monkeypatch, occupied={58416}, console_ports={58416})
        assert find_running_console(host="127.0.0.1", candidate_ports=range(58416, 58420)) == 58416

    def test_shifted_console_beats_a_bindable_default_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """现有实例当初顺延过：即便默认端口现在空着，也复用现有实例。"""
        _stub_port_probe(monkeypatch, occupied={58419}, console_ports={58419})
        assert find_running_console(host="127.0.0.1", candidate_ports=range(58418, 58422)) == 58419

    def test_foreign_occupants_are_not_claimed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """占用者不是 console 时不认领，交回端口顺延逻辑。"""
        _stub_port_probe(monkeypatch, occupied={58420, 58421})
        assert find_running_console(host="127.0.0.1", candidate_ports=[58420, 58421]) is None


class TestConsoleReusesRunningInstance:
    """``kc console`` 重入时复用现有实例，不起第二个。"""

    def test_reopens_browser_and_exits_without_launching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """默认端口上已有 console → 打开它的 URL、退出码 0、绝不碰 uvicorn。"""
        opened_urls: list[str] = []
        _stub_console_settings(monkeypatch, port=58430)
        _stub_port_probe(monkeypatch, occupied={58430}, console_ports={58430})
        monkeypatch.setattr(cli_console.uvicorn, "run", _fail_uvicorn_run)
        monkeypatch.setattr(
            cli_console.webbrowser, "open", lambda url, *a, **k: opened_urls.append(url)
        )

        result = CliRunner().invoke(console_app, [])

        assert result.exit_code == 0
        assert opened_urls == [console_url_for(CONSOLE_HOST, 58430)]
        assert "already running" in result.stdout

    def test_no_browser_flag_only_reports_the_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--no-browser 复用现有实例时不拉起浏览器，但仍要告知 URL。"""
        _stub_console_settings(monkeypatch, port=58431)
        _stub_port_probe(monkeypatch, occupied={58431}, console_ports={58431})
        monkeypatch.setattr(
            static_assets,
            "console_static_dir",
            lambda: (_ for _ in ()).throw(AssertionError("reuse must skip the static check")),
        )
        monkeypatch.setattr(cli_console.uvicorn, "run", _fail_uvicorn_run)
        monkeypatch.setattr(
            cli_console.webbrowser,
            "open",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("webbrowser.open must not be called with --no-browser")
            ),
        )

        result = CliRunner().invoke(console_app, ["--no-browser"])

        assert result.exit_code == 0
        assert console_url_for(CONSOLE_HOST, 58431) in result.stdout

    def test_explicit_port_already_serving_console_is_reopened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """显式 --port 指向已在服务的 console 时重开面板，而不是报错退出。"""
        opened_urls: list[str] = []
        _stub_console_settings(monkeypatch, port=58432)
        _stub_port_probe(monkeypatch, occupied={58433}, console_ports={58433})
        monkeypatch.setattr(cli_console.uvicorn, "run", _fail_uvicorn_run)
        monkeypatch.setattr(
            cli_console.webbrowser, "open", lambda url, *a, **k: opened_urls.append(url)
        )

        result = CliRunner().invoke(console_app, ["--port", "58433"])

        assert result.exit_code == 0
        assert opened_urls == [console_url_for(CONSOLE_HOST, 58433)]

    def test_no_console_anywhere_still_starts_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """没找到现有实例时，首次启动路径与端口不变。"""
        launched: list[tuple[str, int]] = []
        _stub_console_settings(monkeypatch, port=58434)
        _stub_port_probe(monkeypatch, occupied=set())
        _stub_console_static_dir(monkeypatch, directory=_make_console_panel(tmp_path / "console"))
        monkeypatch.setattr(
            cli_console,
            "launch_console",
            lambda *, host, port, open_browser: launched.append((host, port)),
        )

        result = CliRunner().invoke(console_app, ["--no-browser"])

        assert result.exit_code == 0
        assert launched == [(CONSOLE_HOST, 58434)]


def test_console_missing_static_assets_fails_with_repair_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """面板产物整体缺失时默认退出并给出可执行修复路径。"""
    _stub_console_settings(monkeypatch, port=58435)
    _stub_port_probe(monkeypatch, occupied=set())
    _stub_console_static_dir(monkeypatch, directory=tmp_path / "missing-console")
    monkeypatch.setattr(cli_console, "error_console", Console(stderr=True, width=200))
    launched_ports: list[int] = []
    monkeypatch.setattr(
        cli_console,
        "launch_console",
        lambda *, host, port, open_browser: launched_ports.append(port),
    )

    result = CliRunner().invoke(console_app, ["--no-browser"])

    assert result.exit_code == 1
    _assert_repair_guidance_is_one_contiguous_block(result.output)
    assert "The web panel will be unavailable." not in result.output
    assert launched_ports == []


def test_console_incomplete_static_assets_fail_before_listening(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """面板目录存在但缺入口文件时，默认启动同样要在监听前失败并给出修复路径。

    判定只看目录是否存在时，一次被打断的 `just console-sync` 或残缺的解包产物
    会让命令照常启动，浏览器打开 `/` 依然是 404——正是本 Issue 报的现象。
    """
    _stub_console_settings(monkeypatch, port=58441)
    _stub_port_probe(monkeypatch, occupied=set())
    partial_panel_dir = tmp_path / "console"
    (partial_panel_dir / "_next").mkdir(parents=True)
    _stub_console_static_dir(monkeypatch, directory=partial_panel_dir)
    monkeypatch.setattr(cli_console, "error_console", Console(stderr=True, width=200))
    launched_ports: list[int] = []
    monkeypatch.setattr(
        cli_console,
        "launch_console",
        lambda *, host, port, open_browser: launched_ports.append(port),
    )

    result = CliRunner().invoke(console_app, ["--no-browser"])

    assert result.exit_code == 1
    _assert_repair_guidance_is_one_contiguous_block(result.output)
    assert launched_ports == []


def test_api_only_console_starts_when_panel_entry_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """残缺产物属于「面板不可用」，显式 --api-only 仍要能起 API。"""
    _stub_console_settings(monkeypatch, port=58442)
    _stub_port_probe(monkeypatch, occupied=set())
    partial_panel_dir = tmp_path / "console"
    (partial_panel_dir / "_next").mkdir(parents=True)
    _stub_console_static_dir(monkeypatch, directory=partial_panel_dir)
    launched_ports: list[int] = []
    monkeypatch.setattr(
        cli_console,
        "launch_console",
        lambda *, host, port, open_browser: launched_ports.append(port),
    )

    result = CliRunner().invoke(console_app, ["--api-only", "--no-browser"])

    assert result.exit_code == 0
    assert launched_ports == [58442]


def test_static_mount_skips_panel_when_entry_file_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """挂载侧与 CLI 预检同判定：入口文件缺失时不挂 `/`，避免面板 404。"""
    mounted_app = FastAPI()
    partial_panel_dir = tmp_path / "console"
    (partial_panel_dir / "_next").mkdir(parents=True)
    monkeypatch.setattr(api_app, "app", mounted_app)
    _stub_console_static_dir(monkeypatch, directory=partial_panel_dir)

    api_app._mount_console_static()

    assert _mounted_panel_directories(mounted_app) == []


def test_static_mount_serves_panel_when_entry_file_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """入口文件就位时才挂载，且挂载目录就是同一个解析入口。"""
    mounted_app = FastAPI()
    panel_dir = _make_console_panel(tmp_path / "console")
    monkeypatch.setattr(api_app, "app", mounted_app)
    _stub_console_static_dir(monkeypatch, directory=panel_dir)

    api_app._mount_console_static()

    assert _mounted_panel_directories(mounted_app) == [str(panel_dir)]


def test_api_only_console_starts_when_static_assets_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """显式 --api-only 允许缺产物时启动 API。"""
    _stub_console_settings(monkeypatch, port=58436)
    _stub_port_probe(monkeypatch, occupied=set())
    _stub_console_static_dir(monkeypatch, directory=tmp_path / "missing-console")
    launched_ports: list[int] = []
    monkeypatch.setattr(
        cli_console,
        "launch_console",
        lambda *, host, port, open_browser: launched_ports.append(port),
    )

    result = CliRunner().invoke(console_app, ["--api-only", "--no-browser"])

    assert result.exit_code == 0
    assert launched_ports == [58436]


def test_api_only_help_explains_that_the_panel_is_unavailable() -> None:
    """--api-only 的帮助明确说明只提供 API 的代价。"""
    result = CliRunner().invoke(console_app, ["--help"])
    # Rich 会在旗标字符间插入 ANSI 转义，先还原纯文本再检查。
    output = Text.from_ansi(result.output).plain

    assert result.exit_code == 0
    # 帮助文本按终端宽度折行，只断言不会跨行的短片段。
    assert "--api-only" in output
    assert "Start even when" in output
    assert "dashboard assets are" in output
    assert "missing or incomplete" in output
    assert "/ then returns" in output
    assert "Complete assets keep" in output
    assert "enabled." in output


class FakeTimer:
    """threading.Timer 替身：start() 时同步执行，便于断言调用顺序。"""

    instances: list["FakeTimer"] = []

    def __init__(self, interval, function, args=None, kwargs=None) -> None:
        self.interval = interval
        self.function = function
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self) -> None:
        self.function(*self.args, **self.kwargs)


def test_launch_console_opens_browser_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """launch_console should schedule the browser open and run uvicorn."""
    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(cli_console, "_BROWSER_OPEN_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(cli_console.threading, "Timer", FakeTimer)
    FakeTimer.instances.clear()
    monkeypatch.setattr(
        cli_console.webbrowser,
        "open",
        lambda url, *args, **kwargs: calls.append(("browser", (url,))),
    )
    monkeypatch.setattr(
        cli_console.uvicorn,
        "run",
        lambda app_target, host, port: calls.append(("uvicorn", (app_target, host, port))),
    )

    launch_console(host="127.0.0.1", port=58326, open_browser=True)

    assert ("browser", ("http://127.0.0.1:58326/",)) in calls
    assert ("uvicorn", ("backend.api.app:app", "127.0.0.1", 58326)) in calls


def test_launch_console_no_browser_skips_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-browser must not touch webbrowser at all."""

    def _fail_open(*args, **kwargs):
        raise AssertionError("webbrowser.open must not be called with --no-browser")

    monkeypatch.setattr(
        cli_console.uvicorn,
        "run",
        lambda app_target, host, port: None,
    )
    monkeypatch.setattr(cli_console.webbrowser, "open", _fail_open)

    launch_console(host="127.0.0.1", port=58327, open_browser=False)


def test_console_command_reports_occupied_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit --port occupied by a *non-console* service → error and non-zero exit."""
    _stub_console_settings(monkeypatch, port=8313)
    _stub_port_probe(monkeypatch, occupied={58328})
    monkeypatch.setattr(cli_console.uvicorn, "run", _fail_uvicorn_run)
    result = CliRunner().invoke(console_app, ["--port", "58328", "--no-browser"])
    assert result.exit_code != 0


class TestDefaultRunnerCommand:
    """_default_runner_command 的运行时解析顺序与显式配置优先。"""

    def test_explicit_config_wins_over_runtime_resolution(self) -> None:
        """config.toml 显式配置的 runner_command 始终优先于运行时默认解析。"""
        console_settings = AgentRunnerConsoleSettings(runner_command=["/custom/kc"])
        assert console_settings.runner_command == ["/custom/kc"]

    def test_argv0_named_kc_is_used_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """以当前 CLI 入口启动时直接沿用 sys.argv[0]。"""
        monkeypatch.setattr(
            agent_runner_settings.sys, "argv", ["/tmp/kedacode-clean/bin/kc", "console"]
        )
        assert agent_runner_settings._default_runner_command() == ["/tmp/kedacode-clean/bin/kc"]

    def test_exe_suffixed_argv0_is_used_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 上 argv[0] 带 .exe 后缀，按 stem 比较才不会漏判。

        用正斜杠路径表达，避免在 POSIX 上 ``PurePosixPath`` 不把反斜杠当
        分隔符导致用例失真；被测的差异点是 ``.exe`` 后缀本身。
        """
        monkeypatch.setattr(
            agent_runner_settings.sys,
            "argv",
            ["/c/Users/zata/.local/bin/kedacode.exe", "console"],
        )
        assert agent_runner_settings._default_runner_command() == [
            "/c/Users/zata/.local/bin/kedacode.exe"
        ]

    def test_falls_back_to_which_when_argv0_is_not_a_current_command(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """uvicorn 等其它入口启动后端时，只按当前命令名从 PATH 解析。

        which_command 是 resolve_own_command_argv 的默认参数，绑定的是真实
        ``shutil.which``，无法用 monkeypatch 替换其函数对象；改为钉死 PATH 让其在
        调用时读到受控目录，从而稳定命中新主名 kc。
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        kc_path = bin_dir / "kc"
        kc_path.write_text("#!/bin/sh\n", encoding="utf-8")
        kc_path.chmod(0o755)
        monkeypatch.setattr(
            agent_runner_settings.sys, "argv", ["/usr/bin/uvicorn", "backend.api.app:app"]
        )
        monkeypatch.setenv("PATH", str(bin_dir))
        assert agent_runner_settings._default_runner_command() == [str(kc_path)]

    def test_falls_back_to_uv_run_when_no_current_command_is_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """既非自有入口、PATH 也找不到任一自有名时兜底 ``uv run kc``。"""
        empty_bin_dir = tmp_path / "empty-bin"
        empty_bin_dir.mkdir()
        monkeypatch.setattr(agent_runner_settings.sys, "argv", ["python", "-m", "backend.main"])
        monkeypatch.setenv("PATH", str(empty_bin_dir))
        assert agent_runner_settings._default_runner_command() == ["uv", "run", "kc"]


class TestListenHostIsNotConfigurable:
    """回归：监听地址必须钉死在回环，不存在任何 CLI 参数或配置逃生口。

    面板带写操作而认证是空实现，监听地址是唯一访问控制。曾经把 host 做成
    ``AgentRunnerConsoleSettings`` 字段，导致两行 config.toml 就能让面板
    监听 ``*`` 并对整个网段返回 200。
    """

    def test_console_host_is_loopback(self) -> None:
        """硬编码常量必须是回环地址。"""
        assert CONSOLE_HOST == "127.0.0.1"

    def test_console_settings_has_no_host_field(self) -> None:
        """配置模型不得再暴露 host 字段。"""
        assert "host" not in AgentRunnerConsoleSettings.model_fields

    def test_stale_host_key_in_config_is_ignored(self) -> None:
        """历史 config.toml 里残留的 host 键被忽略，且不会变成可用属性。"""
        console_settings = AgentRunnerConsoleSettings(host="0.0.0.0")  # type: ignore[call-arg]
        assert not hasattr(console_settings, "host")

    def test_callback_always_binds_loopback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """即便配置里塞了 host，命令实际传给 uvicorn 的仍是回环地址。"""
        monkeypatch.setattr(
            cli_console,
            "load_fresh_agent_runner_settings",
            lambda: type(
                "_Settings",
                (),
                {"console": AgentRunnerConsoleSettings(host="0.0.0.0", port=58327)},  # type: ignore[call-arg]
            )(),
        )
        launched: list[tuple[str, int]] = []
        _stub_port_probe(monkeypatch, occupied=set())
        _stub_console_static_dir(monkeypatch, directory=_make_console_panel(tmp_path / "console"))
        monkeypatch.setattr(
            cli_console,
            "launch_console",
            lambda *, host, port, open_browser: launched.append((host, port)),
        )
        result = CliRunner().invoke(console_app, ["--no-browser"])
        assert result.exit_code == 0
        assert launched == [("127.0.0.1", 58327)]

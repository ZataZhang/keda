"""Tests for the ``iar console`` command: port resolution and browser flag."""

from __future__ import annotations

import socket

import pytest
from typer.testing import CliRunner

import backend.api.cli_typer_console as cli_console
from backend.api.cli_typer_console import (
    CONSOLE_HOST,
    ConsolePortUnavailableError,
    launch_console,
    resolve_console_port,
)
from backend.api.cli_typer_app import console_app
from backend.infrastructure.config.settings import (
    AgentRunnerConsoleSettings,
)
from backend.infrastructure.config import agent_runner_settings


def _occupy_port(port: int, host: str = "127.0.0.1") -> socket.socket:
    """Bind a real listening socket to simulate an occupied port."""
    blocking_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocking_socket.bind((host, port))
    blocking_socket.listen(1)
    return blocking_socket


class TestResolveConsolePort:
    """resolve_console_port 的顺延与显式端口语义。"""

    def test_explicit_free_port_returned_as_is(self) -> None:
        """A free explicit port is honored verbatim."""
        assert (
            resolve_console_port(host="127.0.0.1", explicit_port=58321, default_port=8313) == 58321
        )

    def test_explicit_occupied_port_raises(self) -> None:
        """An occupied explicit port must fail loudly instead of shifting."""
        blocker = _occupy_port(58322)
        try:
            with pytest.raises(ConsolePortUnavailableError, match="already in use"):
                resolve_console_port(host="127.0.0.1", explicit_port=58322, default_port=8313)
        finally:
            blocker.close()

    def test_default_port_free_returned(self) -> None:
        """Without --port the configured default port is used when free."""
        assert (
            resolve_console_port(host="127.0.0.1", explicit_port=None, default_port=58323) == 58323
        )

    def test_scans_forward_from_default_port(self) -> None:
        """Without --port, an occupied default port shifts to the next free one."""
        blocker = _occupy_port(58324)
        try:
            resolved = resolve_console_port(
                host="127.0.0.1", explicit_port=None, default_port=58324
            )
            assert resolved == 58325
        finally:
            blocker.close()

    def test_scan_window_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No free port inside the scan window must raise, not bind blindly."""
        monkeypatch.setattr(cli_console, "_port_is_available", lambda host, port: False)
        with pytest.raises(ConsolePortUnavailableError, match="No free port"):
            resolve_console_port(host="127.0.0.1", explicit_port=None, default_port=8313)


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
    """Explicit --port occupied → friendly error and non-zero exit."""

    def _fail_launch(*args, **kwargs):
        raise AssertionError("uvicorn.run must not be reached on a port error")

    monkeypatch.setattr(cli_console.uvicorn, "run", _fail_launch)
    blocker = _occupy_port(58328)
    try:
        result = CliRunner().invoke(console_app, ["--port", "58328", "--no-browser"])
    finally:
        blocker.close()
    assert result.exit_code != 0


class TestDefaultRunnerCommand:
    """_default_runner_command 的运行时解析顺序与显式配置优先。"""

    def test_explicit_config_wins_over_runtime_resolution(self) -> None:
        """config.toml 显式配置的 runner_command 始终优先于运行时默认解析。"""
        console_settings = AgentRunnerConsoleSettings(runner_command=["/custom/iar"])
        assert console_settings.runner_command == ["/custom/iar"]

    def test_argv0_named_iar_is_used_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """iar 入口直接运行时取 sys.argv[0]，保证与当前安装态同源。"""
        monkeypatch.setattr(
            agent_runner_settings.sys, "argv", ["/tmp/iar-clean/bin/iar", "console"]
        )
        monkeypatch.setattr(agent_runner_settings.shutil, "which", lambda name: "/from/which/iar")
        assert agent_runner_settings._default_runner_command() == ["/tmp/iar-clean/bin/iar"]

    def test_exe_suffixed_argv0_is_used_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 上 argv[0] 带 .exe 后缀，按 stem 比较才不会漏判。

        用正斜杠路径表达，避免在 POSIX 上 ``PurePosixPath`` 不把反斜杠当
        分隔符导致用例失真；被测的差异点是 ``.exe`` 后缀本身。
        """
        monkeypatch.setattr(
            agent_runner_settings.sys,
            "argv",
            ["/c/Users/zata/.local/bin/iar.exe", "console"],
        )
        monkeypatch.setattr(agent_runner_settings.shutil, "which", lambda name: "/from/which/iar")
        assert agent_runner_settings._default_runner_command() == [
            "/c/Users/zata/.local/bin/iar.exe"
        ]

    def test_falls_back_to_which_when_argv0_is_not_iar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """uvicorn 等其它入口启动后端时，从 PATH 解析 iar。"""
        monkeypatch.setattr(
            agent_runner_settings.sys, "argv", ["/usr/bin/uvicorn", "backend.api.app:app"]
        )
        monkeypatch.setattr(
            agent_runner_settings.shutil, "which", lambda name: "/opt/homebrew/bin/iar"
        )
        assert agent_runner_settings._default_runner_command() == ["/opt/homebrew/bin/iar"]

    def test_falls_back_to_uv_run_when_iar_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """既非 iar 入口、PATH 也找不到 iar 时兜底 uv run。"""
        monkeypatch.setattr(agent_runner_settings.sys, "argv", ["python", "-m", "backend.main"])
        monkeypatch.setattr(agent_runner_settings.shutil, "which", lambda name: None)
        assert agent_runner_settings._default_runner_command() == ["uv", "run", "iar"]


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

    def test_callback_always_binds_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        monkeypatch.setattr(
            cli_console,
            "launch_console",
            lambda *, host, port, open_browser: launched.append((host, port)),
        )
        result = CliRunner().invoke(console_app, ["--no-browser"])
        assert result.exit_code == 0
        assert launched == [("127.0.0.1", 58327)]

"""桌面 companion 的 launcher、origin 边界与 route 校验测试（PRD FR-4 / FR-6 / rv-2）。

系统子进程只在「真的起了什么进程」这一点上做替身记录（argv 是本测试要断言的
对象本身）；状态判定、工单递增、pidfile 复用与归属防护、锁粒度与 origin 校验
都走真实实现。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import backend.api.routes.agent_runner_console_runtime as runtime_routes
import backend.engines.desktop_companion.launcher as launcher_module
from backend.api.routes.agent_runner_console_runtime import (
    _trusted_bound_origin,
    _trusted_console_origin,
)
from backend.core.shared.interfaces.desktop_companion import DesktopCompanionStatus
import backend.engines.desktop_companion.helper as helper_module
from backend.engines.desktop_companion.helper import validate_console_origin
from backend.engines.desktop_companion.launcher import (
    HELPER_MODULE,
    PywebviewCompanionLauncher,
)

CONSOLE_ORIGIN = "http://127.0.0.1:8313"


class _FakeProcess:
    """记录 argv 的最小进程替身：``poll`` 结果由测试控制。"""

    def __init__(self, *, exit_code: int | None = None) -> None:
        self.args: list[str] = []
        self.pid = 424242
        self.returncode: int | None = exit_code
        self._exit_code = exit_code

    def poll(self) -> int | None:
        """返回退出码；``None`` 表示仍在运行。与真实 ``Popen`` 一样回填 ``returncode``。"""
        if self._exit_code is not None:
            self.returncode = self._exit_code
        return self._exit_code


def _launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: _FakeProcess,
    *,
    console_session: str = "session-abc",
):
    """构造指向临时 pidfile、运行时可用的 launcher。"""

    def _fake_popen(args, **kwargs):  # noqa: ANN001, ANN003 - 替身只关心 argv
        process.args = list(args)
        return process

    monkeypatch.setattr(launcher_module, "desktop_runtime_available", lambda: True)
    monkeypatch.setattr(launcher_module.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(launcher_module, "_pid_is_alive", lambda pid: process.poll() is None)
    return PywebviewCompanionLauncher(
        pidfile_path=tmp_path / "companion.pid",
        console_session=console_session,
    )


def test_open_window_uses_fixed_argv_and_validated_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """窗口通过固定 argv 启动 helper，origin 只能是已校验的 loopback Console。"""
    process = _FakeProcess()
    launcher = _launcher(tmp_path, monkeypatch, process)

    snapshot = launcher.open_window(console_origin=CONSOLE_ORIGIN)

    assert process.args == [sys.executable, "-m", HELPER_MODULE, "--origin", CONSOLE_ORIGIN]
    assert snapshot.status is DesktopCompanionStatus.LAUNCHED
    assert snapshot.focus_ticket == 1
    pidfile_lines = (tmp_path / "companion.pid").read_text(encoding="utf-8").split("\n")
    assert pidfile_lines[0] == "424242"
    assert pidfile_lines[1] == f"-m {HELPER_MODULE}"
    # 第三行是归属会话：另一个 Console 进程据此认不出这是自己的窗口。
    assert pidfile_lines[2] == "session-abc"


def test_repeat_activation_focuses_instead_of_spawning_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重复点击聚焦既有窗口，不再起第二个进程（FR-6）。"""
    process = _FakeProcess()
    launcher = _launcher(tmp_path, monkeypatch, process)
    launcher.open_window(console_origin=CONSOLE_ORIGIN)
    spawn_argv = list(process.args)

    snapshot = launcher.open_window(console_origin=CONSOLE_ORIGIN)

    assert process.args == spawn_argv
    assert snapshot.status is DesktopCompanionStatus.FOCUSED
    assert snapshot.focus_ticket == 2
    assert snapshot.window_exists is True


def test_missing_runtime_reports_not_installed_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """桌面运行时缺失时如实返回 not_installed，且不产生进程。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(launcher_module, "desktop_runtime_available", lambda: False)
    monkeypatch.setattr(
        launcher_module.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append(list(args)) or _FakeProcess(),
    )
    launcher = PywebviewCompanionLauncher(
        pidfile_path=tmp_path / "companion.pid", console_session="s"
    )

    snapshot = launcher.open_window(console_origin=CONSOLE_ORIGIN)

    assert snapshot.status is DesktopCompanionStatus.NOT_INSTALLED
    assert calls == []
    assert "kedacode[desktop]" in snapshot.hint


def test_close_window_requests_exit_and_returns_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关闭走工单而非 kill：helper 退出后状态回到 idle，正常退出不算失败。"""
    process = _FakeProcess()
    launcher = _launcher(tmp_path, monkeypatch, process)
    launcher.open_window(console_origin=CONSOLE_ORIGIN)

    process._exit_code = 0
    snapshot = launcher.close_window(wait_seconds=0.0)

    assert snapshot.close_ticket == 1
    assert snapshot.status is DesktopCompanionStatus.IDLE
    assert snapshot.window_exists is False


def test_helper_crash_is_observable_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """helper 异常退出（非零码）必须可见并带原因，不能静默变回「未开启」。"""
    process = _FakeProcess()
    launcher = _launcher(tmp_path, monkeypatch, process)
    launcher.open_window(console_origin=CONSOLE_ORIGIN)

    process._exit_code = 3
    snapshot = launcher.snapshot(console_origin=CONSOLE_ORIGIN)

    assert snapshot.status is DesktopCompanionStatus.FAILED
    assert "退出码 3" in (snapshot.detail or "")


def test_recycled_pid_is_never_mistaken_for_our_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pidfile 里的命令行标记不匹配时按「没有窗口」处理，不去接管别人的进程。"""
    process = _FakeProcess()
    launcher = _launcher(tmp_path, monkeypatch, process)
    pidfile = tmp_path / "companion.pid"
    pidfile.write_text("999999\n-sm something-else\n", encoding="utf-8")

    snapshot = launcher.open_window(console_origin=CONSOLE_ORIGIN)

    assert snapshot.status is DesktopCompanionStatus.LAUNCHED
    assert process.args == [sys.executable, "-m", HELPER_MODULE, "--origin", CONSOLE_ORIGIN]


def test_another_consoles_window_is_neither_claimed_nor_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pidfile 归别的 Console 会话：不能报「已聚焦」，也不能把别人的窗口关掉。

    工单写在各自 launcher 的内存计数里，本进程递增永远送不到对方 helper 的轮询；
    把它当自己的窗口会让按钮说谎，还会让第二个入口无法开出自己的小窗。
    """
    owner_process = _FakeProcess()
    owner = _launcher(tmp_path, monkeypatch, owner_process, console_session="session-owner")
    owner.open_window(console_origin=CONSOLE_ORIGIN)

    ours = _launcher(tmp_path, monkeypatch, owner_process, console_session="session-ours")
    snapshot = ours.open_window(console_origin=CONSOLE_ORIGIN)

    assert snapshot.status is DesktopCompanionStatus.LAUNCHED
    assert snapshot.window_exists is True
    # 对方那份 launcher 仍持有自己的句柄，不受影响。
    assert owner.snapshot(console_origin=CONSOLE_ORIGIN).status is (DesktopCompanionStatus.RUNNING)


def test_same_session_pidfile_counts_as_our_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会话身份相符时，即使本进程没持有句柄也如实报告「窗口在」。"""
    process = _FakeProcess()
    launcher = _launcher(tmp_path, monkeypatch, process)
    (tmp_path / "companion.pid").write_text(
        f"999999\n-m {HELPER_MODULE}\nsession-abc\n", encoding="utf-8"
    )

    snapshot = launcher.snapshot(console_origin=CONSOLE_ORIGIN)

    assert snapshot.status is DesktopCompanionStatus.RUNNING
    assert snapshot.window_exists is True


def test_close_wait_does_not_block_the_status_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关窗的有界等待不能占着锁：看板与 helper 的状态探针必须随时读得到。"""
    process = _FakeProcess()
    launcher = _launcher(tmp_path, monkeypatch, process)
    launcher.open_window(console_origin=CONSOLE_ORIGIN)

    probe_read_close_ticket: list[bool] = []

    def _probe() -> None:
        snapshot = launcher.snapshot(console_origin=CONSOLE_ORIGIN)
        probe_read_close_ticket.append(snapshot.close_ticket == 1)

    closer = threading.Thread(target=lambda: launcher.close_window(wait_seconds=1.0))
    closer.start()
    time.sleep(0.2)  # 让 closer 进入等待段（helper 仍在收尾）
    probe = threading.Thread(target=_probe)
    probe.start()
    probe_started = time.monotonic()
    probe.join(timeout=2.0)
    probe_elapsed = time.monotonic() - probe_started
    closer.join(timeout=3.0)

    assert probe_read_close_ticket == [True]
    assert probe_elapsed < 0.5, f"状态探针被 close_window 的等待阻塞 {probe_elapsed:.2f}s"


def _http_request(
    server: tuple[str, int],
    headers: list[tuple[str, str]],
) -> Request:
    """构造带 ``scope["server"]`` 的 http 请求，模拟真实 uvicorn 注入的绑定地址。"""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/agent-runner/console/desktop-companion/window",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers],
        "server": server,
    }
    return Request(scope)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("server", "headers"),
    [
        pytest.param(("127.0.0.1", 8313), [], id="missing-origin"),
        pytest.param(
            ("127.0.0.1", 8313),
            [("Origin", "http://evil.test:8313")],
            id="external-host",
        ),
        pytest.param(
            ("127.0.0.1", 8313),
            [("Origin", "https://127.0.0.1:8313")],
            id="wrong-scheme",
        ),
        pytest.param(
            ("127.0.0.1", 8313),
            [("Origin", "http://127.0.0.1:9999")],
            id="other-local-port",
        ),
        pytest.param(
            ("127.0.0.1", 8313),
            [("Origin", "http://127.0.0.1:8313/evil")],
            id="origin-with-path",
        ),
        pytest.param(
            ("0.0.0.0", 8313),
            [("Origin", "http://0.0.0.0:8313")],
            id="non-loopback-bind",
        ),
    ],
)
def test_cross_origin_launch_requests_are_rejected(
    server: tuple[str, int], headers: list[tuple[str, str]]
) -> None:
    """跨源、缺 Origin、非回环绑定都必须拿不到启动系统窗口的能力。"""
    with pytest.raises(HTTPException) as excinfo:
        _trusted_console_origin(_http_request(server, headers))
    assert excinfo.value.status_code in {403, 503}


@pytest.mark.parametrize(
    ("server", "origin", "expected"),
    [
        pytest.param(
            ("127.0.0.1", 8313),
            "http://127.0.0.1:8313",
            "http://127.0.0.1:8313",
            id="loopback-ip",
        ),
        # 回环别名指向同一个 Console，判定同样放行；但窗口地址取绑定侧，
        # 不回显请求头里的 host。
        pytest.param(
            ("127.0.0.1", 8313),
            "http://localhost:8313",
            "http://127.0.0.1:8313",
            id="localhost-alias",
        ),
        pytest.param(
            ("127.0.0.1", 8313),
            "http://127.0.0.1:8313/",
            "http://127.0.0.1:8313",
            id="trailing-slash",
        ),
        # IPv6 字面量必须加方括号，否则拼出来的不是合法 URL，helper 只会连到一个
        # 解析不了的 origin。
        pytest.param(("::1", 8313), "http://[::1]:8313", "http://[::1]:8313", id="ipv6-loopback"),
    ],
)
def test_same_origin_launch_requests_are_accepted(
    server: tuple[str, int], origin: str, expected: str
) -> None:
    """同源 POST 通过校验，返回由实际绑定推出的 origin 供固定窗口加载。"""
    request = _http_request(server, [("Origin", origin)])
    assert _trusted_console_origin(request) == expected


def test_status_probe_reports_the_bound_origin_with_ipv6_brackets() -> None:
    """只读探针同样按绑定地址给 origin，IPv6 不会漏掉方括号。"""
    assert _trusted_bound_origin(_http_request(("::1", 8313), [])) == "http://[::1]:8313"
    assert _trusted_bound_origin(_http_request(("0.0.0.0", 8313), [])) == ""


def test_helper_only_loads_loopback_http_origins() -> None:
    """helper 自己再校验一次 argv：拒绝非回环、非 http 与带路径的 origin。"""
    assert validate_console_origin("http://127.0.0.1:8313") == "http://127.0.0.1:8313"
    for bad in (
        "https://127.0.0.1:8313",
        "http://evil.test:8313",
        "http://127.0.0.1:8313/app/companion/",
        "http://127.0.0.1:8313?x=1",
    ):
        with pytest.raises(ValueError):
            validate_console_origin(bad)


def test_close_ticket_destroys_the_window_before_the_loop_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关窗工单必须真的销毁窗口：`func` 返回不等于 GUI 循环结束。

    pywebview 6 的 ``Window`` 只有 ``destroy()``，写 ``close()`` 会静默无效，
    桌面窗口永远关不掉——这条只能靠真实窗口发现，故在此钉住回归。
    """
    monkeypatch.setattr(helper_module, "POLL_INTERVAL_SECONDS", 0.0)
    statuses = iter(
        [
            {"console_session": "s", "focus_ticket": 0, "close_ticket": 0},
            {"console_session": "s", "focus_ticket": 0, "close_ticket": 1},
        ]
    )
    monkeypatch.setattr(
        helper_module, "fetch_companion_status", lambda origin, **kwargs: next(statuses)
    )

    class _Window:
        """只记录销毁动作的窗口替身。"""

        def __init__(self) -> None:
            self.destroyed = False

        def destroy(self) -> None:
            """pywebview 的关窗入口。"""
            self.destroyed = True

    window = _Window()
    controller = helper_module.CompanionController(
        origin=CONSOLE_ORIGIN,
        expected_session="s",
        window=window,
        companion_url=f"{CONSOLE_ORIGIN}/app/companion/",
    )
    controller.run()
    assert controller.stop_requested is True
    assert window.destroyed is True


def test_orphaned_helper_exits_without_waiting_for_a_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Console 重启后旧窗口自行退出，同样要把退出落成真实关窗。"""
    monkeypatch.setattr(helper_module, "POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(
        helper_module,
        "fetch_companion_status",
        lambda origin, **kwargs: {
            "console_session": "other-session",
            "focus_ticket": 0,
            "close_ticket": 0,
        },
    )

    class _Window:
        """只记录销毁动作的窗口替身。"""

        def __init__(self) -> None:
            self.destroyed = False

        def destroy(self) -> None:
            """pywebview 的关窗入口。"""
            self.destroyed = True

    window = _Window()
    controller = helper_module.CompanionController(
        origin=CONSOLE_ORIGIN,
        expected_session="s",
        window=window,
        companion_url=f"{CONSOLE_ORIGIN}/app/companion/",
    )
    controller.run()
    assert window.destroyed is True


def test_runtime_routes_are_registered_under_the_console_prefix() -> None:
    """端点路径与 helper 里写死的探针路径一致，否则窗口只会看到 404。"""
    paths = {route.path for route in runtime_routes.router.routes}
    assert paths == {
        "/agent-runner/console/runtime/tasks",
        "/agent-runner/console/desktop-companion/status",
        "/agent-runner/console/desktop-companion/window",
        "/agent-runner/console/desktop-companion/window/close",
    }

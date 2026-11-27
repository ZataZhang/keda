"""浏览器宿主的按需启动、调用隔离、权限边界与异常回收回归。"""

from __future__ import annotations

import json
import subprocess
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from backend.core.shared.models.browser_session import validate_browser_arguments
from backend.core.use_cases.run_agent_once import run_agent_with_prompt
from backend.core.use_cases.agent_runner_output_routing import _OutputRoutedProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, CommandResult
from backend.infrastructure.browser_host import (
    BrowserHost,
    browser_host_session,
)
from backend.infrastructure.child_env import AGENT_BROWSER_ENV, build_sanitized_child_env


def _request(environment: dict[str, str], arguments: list[str]) -> dict:
    """通过真实回环通道执行一次动作。"""
    request = Request(
        environment["KC_BROWSER_URL"],
        data=json.dumps({"arguments": arguments}).encode(),
        headers={"Authorization": "Bearer " + environment["KC_BROWSER_TOKEN"]},
    )
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


def test_browser_host_is_lazy_isolated_and_closes_on_exception(tmp_path, monkeypatch):
    """只有 open 才调用浏览器，嵌套调用隔离身份且抛错仍关闭精确会话。"""
    invocations = []

    def run_browser(command, **kwargs):
        invocations.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "browser output", "")

    monkeypatch.setattr(
        "backend.infrastructure.browser_host.shutil.which", lambda _: "/runtime/playwright-cli"
    )
    monkeypatch.setattr("backend.infrastructure.browser_host.subprocess.run", run_browser)
    monkeypatch.setenv("MODEL_API_KEY", "must-not-reach-browser")
    with pytest.raises(RuntimeError, match="writer failed"), browser_host_session(tmp_path):
        first_environment = build_sanitized_child_env()
        assert json.loads(_request(first_environment, ["status"])["stdout"])["requested"] is False
        assert invocations == []
        assert _request(first_environment, ["open", "http://localhost:3000"])["returncode"] == 0
        assert "MODEL_API_KEY" not in invocations[0][1]["env"]
        with browser_host_session(tmp_path):
            second_environment = build_sanitized_child_env()
            assert first_environment != second_environment
            assert (
                _request(second_environment, ["open", "http://localhost:3001"])["returncode"] == 0
            )
        assert invocations[-1][0][2] == "close"
        assert invocations[-1][0][1] == invocations[1][0][1]
        assert build_sanitized_child_env() == first_environment
        raise RuntimeError("writer failed")
    assert invocations[-1][0][2] == "close"
    assert invocations[-1][0][1] == invocations[0][0][1]
    assert AGENT_BROWSER_ENV.get() is None


@pytest.mark.parametrize(
    "arguments",
    [
        ["eval", "process.env"],
        ["run-code", "anything"],
        ["open", "http://localhost", "--config=untrusted.json"],
        ["open", "file:///etc/passwd"],
        ["close-all"],
        ["snapshot", "--session=other-task"],
        ["screenshot", "--filename", "../other-task.png"],
    ],
)
def test_browser_bridge_refuses_host_execution_and_cross_task_paths(tmp_path, arguments):
    """浏览器通道不能升级为宿主任意脚本、配置或跨任务写入入口。"""
    with pytest.raises(ValueError):
        validate_browser_arguments(arguments, tmp_path)


def test_browser_bridge_checks_token_before_dispatch(tmp_path):
    """真实 HTTP 入口拒绝另一个调用的令牌。"""
    with browser_host_session(tmp_path):
        environment = build_sanitized_child_env()
        environment["KC_BROWSER_TOKEN"] = "wrong-invocation"
        with pytest.raises(HTTPError) as failure:
            _request(environment, ["status"])
        assert failure.value.code == 403


def test_readonly_call_revokes_inherited_browser_credentials(tmp_path, monkeypatch):
    """只读调用剔除从父进程继承的浏览器通道。"""
    monkeypatch.setenv("KC_BROWSER_URL", "http://127.0.0.1:1/")
    monkeypatch.setenv("KC_BROWSER_TOKEN", "parent-token")
    with browser_host_session(tmp_path, enabled=False):
        assert "KC_BROWSER_URL" not in build_sanitized_child_env()
        assert "KC_BROWSER_TOKEN" not in build_sanitized_child_env()


@pytest.mark.parametrize("agent_name", ["codex", "claude", "pi"])
def test_common_agent_entry_exposes_browser_without_starting_it(tmp_path, agent_name, monkeypatch):
    """三个执行器通过同一调用边界获得浏览器说明与独立通道。"""
    monkeypatch.setattr(
        BrowserHost, "_run", lambda *_args, **_kwargs: pytest.fail("eager browser launch")
    )

    class InspectingRunner:
        supports_browser_host = True

        def run(self, command, *, input_text=None, **kwargs):
            environment = build_sanitized_child_env()
            assert environment["KC_BROWSER_URL"].startswith("http://127.0.0.1:")
            assert "kc browser" in (" ".join(command) + (input_text or ""))
            return CommandResult(command=tuple(command), return_code=0, stdout="done", stderr="")

    run_agent_with_prompt(
        agent_name,
        "Inspect this task",
        tmp_path,
        _OutputRoutedProcessRunner(InspectingRunner(), lambda _: None),
        config=AppConfig(),
        capture_output=True,
    )
    assert AGENT_BROWSER_ENV.get() is None


def test_form_text_is_preserved_even_when_it_looks_like_options(tmp_path):
    """填表内容中的选项语法必须按用户原文传递。"""
    arguments = ["fill", "e5", "--config=literal text"]
    assert validate_browser_arguments(arguments, tmp_path) == arguments

"""每次 Agent 调用独享的回环浏览器通道，按需在宿主侧启动 Playwright CLI。"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Iterator
from urllib.request import ProxyHandler, Request, build_opener

from backend.core.shared.models.browser_session import validate_browser_arguments
from backend.infrastructure.child_env import AGENT_BROWSER_ENV, build_e2e_child_env

_URL_ENV = "KC_BROWSER_URL"
_TOKEN_ENV = "KC_BROWSER_TOKEN"
_COMMAND_TIMEOUT = 75


def request_browser_command(arguments: list[str]) -> dict:
    """向当前调用的宿主提交浏览器动作。

    Args:
        arguments: 浏览器动作及参数。

    Returns:
        含 stdout、stderr、returncode 的命令结果。

    Raises:
        ValueError: 当前会话没有浏览器通道。
        OSError: 宿主已退出或连接失败。
    """
    endpoint = os.environ.get(_URL_ENV, "")
    token = os.environ.get(_TOKEN_ENV, "")
    if not endpoint or not token:
        raise ValueError("No browser host in this session. Use an Agent launched by kc run.")
    request = Request(
        endpoint,
        data=json.dumps({"arguments": arguments}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    # 本机通道不经过用户的 HTTP 代理，避免泄漏连接令牌或连错宿主。
    with build_opener(ProxyHandler({})).open(request, timeout=_COMMAND_TIMEOUT + 15) as response:
        return json.load(response)


class BrowserHost:
    """持有一个调用的浏览器会话、串行命令与宿主执行权限。"""

    def __init__(self, worktree: Path) -> None:
        """记录工作树与独立身份。

        Args:
            worktree: 本次调用的工作树，构造阶段不启动浏览器或写入文件。
        """
        self.worktree = worktree.resolve()
        self.session = f"kc-{uuid.uuid4().hex}"
        self.token = secrets.token_urlsafe(32)
        self.runtime = Path.home() / ".kedacode" / "browser-host" / self.session
        self.lock = threading.Lock()
        self.used = False
        self.closing = False

    def execute(self, arguments: list[str]) -> dict:
        """串行执行经过校验的动作，status 查询不会创建浏览器。

        Args:
            arguments: 请求的浏览器动作和参数。

        Returns:
            含 returncode、stdout 和 stderr 的结果，失败保留非零退出码。
        """
        with self.lock:
            if self.closing:
                return {"returncode": 1, "stdout": "", "stderr": "Browser host is closing."}
            try:
                normalized = validate_browser_arguments(arguments, self.worktree)
                if normalized[0] == "status":
                    return {
                        "returncode": 0,
                        "stdout": json.dumps({"session": self.session, "requested": self.used}),
                        "stderr": "",
                    }
                if normalized[0] == "screenshot":
                    if not any(value.startswith("--filename=") for value in normalized):
                        output = self.worktree / ".kedacode" / "browser" / f"{uuid.uuid4().hex}.png"
                        normalized.append(f"--filename={output}")
                    output = Path(
                        next(
                            value.split("=", 1)[1]
                            for value in normalized
                            if value.startswith("--filename=")
                        )
                    )
                    output.parent.mkdir(parents=True, exist_ok=True)
                outcome = self._run(normalized)
                if normalized[0] == "screenshot" and outcome["returncode"] == 0:
                    outcome["screenshot_path"] = str(output)
                    outcome["stdout"] += f"\nSaved screenshot to {output}\n"
                return outcome
            except subprocess.TimeoutExpired:
                return {
                    "returncode": 124,
                    "stdout": "",
                    "stderr": "Browser command timed out after 75 seconds.",
                }
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                return {"returncode": 1, "stdout": "", "stderr": str(exc)}

    def _run(self, arguments: list[str], *, timeout: int = _COMMAND_TIMEOUT) -> dict:
        """从受控工作目录执行官方 CLI，配置和会话身份由宿主持有。"""
        executable = shutil.which("playwright-cli")
        if executable is None:
            raise ValueError(
                "playwright-cli is missing. Install @playwright/cli and its Chromium runtime on the runner host."
            )
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        browser_config = self.runtime / "browser.json"
        browser_config.write_text(
            json.dumps(
                {
                    "browser": {
                        "browserName": "chromium",
                        "isolated": True,
                        "launchOptions": {"headless": True},
                    },
                    "timeouts": {"navigation": 30000, "action": 10000},
                }
            ),
            encoding="utf-8",
        )
        action, *values = arguments
        command = [executable, f"-s={self.session}", action]
        if arguments[0] == "open":
            self.used = True
            command.extend([f"--config={browser_config}", "--idle-timeout=300000"])
        # minimist 的 -- 终止选项解析，填表内容即使以 -- 开头也始终作为文本。
        command.extend(values if action == "screenshot" else ["--", *values])
        child_env = build_e2e_child_env()
        # 不继承调用方自定义的浏览器配置、Node 加载钩子与其他会话身份。
        child_env = {
            name: value
            for name, value in child_env.items()
            if not name.startswith(("PLAYWRIGHT_", "PWTEST_", "NODE_OPTIONS"))
        }
        child_env["PWTEST_CLI_GLOBAL_CONFIG"] = str(self.runtime)
        completed = subprocess.run(
            command,
            cwd=self.runtime,
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def close(self) -> None:
        """关闭本次会话；异常退出时由浏览器的空闲超时继续兜底回收。"""
        with self.lock:
            self.closing = True
            if self.used:
                with contextlib.suppress(OSError, ValueError, subprocess.SubprocessError):
                    self._run(["close"], timeout=10)
            shutil.rmtree(self.runtime, ignore_errors=True)


class _BrowserHandler(BaseHTTPRequestHandler):
    """仅接收本次调用令牌授权的有限浏览器动作。"""

    def do_POST(self) -> None:
        """校验通道身份与请求体，返回结构化命令结果。"""
        host: BrowserHost = self.server.browser_host
        if self.path != "/" or not secrets.compare_digest(
            self.headers.get("Authorization", ""), f"Bearer {host.token}"
        ):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Browser request must be between 1 and 65536 bytes.")
            request = json.loads(self.rfile.read(length))
            arguments = request["arguments"]
            if not isinstance(arguments, list):
                raise TypeError("arguments must be a list.")
            outcome = host.execute(arguments)
        except (ValueError, KeyError, TypeError) as exc:
            outcome = {"returncode": 2, "stdout": "", "stderr": str(exc)}
        payload = json.dumps(outcome).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        """浏览器动作可能含凭据，本机通道不记录请求内容。"""


@contextlib.contextmanager
def browser_host_session(worktree: Path, *, enabled: bool = True) -> Iterator[None]:
    """在宿主挂接一次调用的浏览器通道，首次 open 时才启动浏览器。

    Args:
        worktree: 本次调用的工作树。
        enabled: 是否授予浏览器动作能力，只读用途应关闭。

    Yields:
        通道生命周期；退出时撤销连接参数并关闭浏览器。
    """
    if not enabled:
        reset_token = AGENT_BROWSER_ENV.set({})
        try:
            yield
        finally:
            AGENT_BROWSER_ENV.reset(reset_token)
        return
    host = BrowserHost(worktree)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BrowserHandler)
    server.browser_host = host
    server.daemon_threads = True
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True, name="kc-browser-host"
    )
    thread.start()
    reset_token = AGENT_BROWSER_ENV.set(
        {_URL_ENV: f"http://127.0.0.1:{server.server_port}/", _TOKEN_ENV: host.token}
    )
    try:
        yield
    finally:
        AGENT_BROWSER_ENV.reset(reset_token)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        host.close()

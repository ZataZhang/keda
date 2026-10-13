"""桌面 companion 的进程入口：在本子进程主线程上承载 pywebview 窗口。

Console 的主线程属于 uvicorn，而 macOS 的 Cocoa 要求 GUI 循环跑在主线程，
因此窗口必须待在这个独立进程里。本模块只做三件事：

1. 用**已校验的 loopback origin** 打开一个紧凑、可置顶、可关闭的窗口，内容是
   Console 自己的 ``/app/companion/`` route——不重复实现看板，也不读任何新数据源；
2. 轮询同一个 Console 的只读状态接口，把 launcher 递增的 focus / close 工单翻译
   成原生的「带到前台」「关窗退出」动作；
3. 观测 Console 的消失与回归：会话身份变了说明这是上一个 Console 留下的孤儿窗口，
   自行退出；暂时连不上则显示明确的断连提示并在恢复后重新加载，绝不显示半个
   缓存出来的「进度」。

命令行参数固定为本模块自己的 ``--origin``，不接受命令、文件路径或外部 host。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

#: companion 只允许加载本机回环地址；与 ``cli_typer_console.CONSOLE_HOST`` 同源约束。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: companion 精简看板的固定路径，窗口内容与网页看板共用同一 route。
COMPANION_PATH = "/app/companion/"

#: companion 状态接口路径（同源只读 GET）。
STATUS_PATH = "/api/v1/agent-runner/console/desktop-companion/status"

#: 工单轮询间隔：窗口内容自身有轮询，这里只负责「聚焦 / 关闭 / 断连」判定。
POLL_INTERVAL_SECONDS = 1.0

#: 连续失败多少次才判定 Console 不可达：避免一次抖动就把看板刷成错误页。
_UNREACHABLE_THRESHOLD = 3

#: 断连时展示的页面：明确说明读不到，而不是留旧内容冒充最新进度。
_DISCONNECTED_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>桌面进度</title>
<style>
body{margin:0;padding:24px;font:13px/1.6 -apple-system,"Segoe UI",sans-serif;
color:#94a3b8;background:#0f172a}
h1{font-size:15px;margin:0 0 8px;color:#e2e8f0}
p{margin:0}
</style></head>
<body><h1>读不到 Console</h1>
<p>本机 Console 已停止或暂时无响应，桌面小窗没有可用进度。
重新启动 Console 后本窗口会自动恢复；窗口不会用缓存内容冒充最新状态。</p>
</body></html>"""


def validate_console_origin(origin: str) -> str:
    """校验 companion 只允许加载的 loopback http origin，返回规范化 origin。

    Args:
        origin: launcher 传入的 Console origin，形如 ``http://127.0.0.1:8313``。

    Returns:
        去掉尾斜杠后的 origin。

    Raises:
        ValueError: scheme 非 http、host 非回环，或带了路径 / 查询串 / 片段。
    """
    normalized = origin.rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme != "http":
        raise ValueError(f"companion 只加载本机 http Console，收到 scheme={parsed.scheme!r}")
    if parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError(f"companion 只加载本机回环地址，收到 host={parsed.hostname!r}")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("companion 的 origin 不允许携带路径、查询串或片段")
    return normalized


def fetch_companion_status(origin: str, *, timeout: float = 3.0) -> dict[str, object]:
    """读取一次当前 Console 的 companion 状态。

    Args:
        origin: 已校验的 Console origin。
        timeout: 单次请求超时秒数。

    Returns:
        状态接口的 JSON 响应体。

    Raises:
        urllib.error.URLError: Console 不可达。
        ValueError: 响应不是 JSON 对象。
    """
    request = urllib.request.Request(  # noqa: S310 - origin 已由 validate_console_origin 限定回环 http
        f"{origin}{STATUS_PATH}",
        method="GET",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("companion 状态接口返回了非对象 JSON")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    """构造 helper 的固定 argv 解析器（只认 ``--origin`` / ``--session``）。"""
    parser = argparse.ArgumentParser(
        prog="python -m backend.engines.desktop_companion.helper",
        description="KedaCode 桌面进度小窗（可选 companion 运行时）。",
    )
    parser.add_argument(
        "--origin",
        required=True,
        help="已校验的本机 Console origin，形如 http://127.0.0.1:8313",
    )
    parser.add_argument(
        "--session",
        default="",
        help="Console 会话身份；缺省时由首次状态读取对齐。",
    )
    return parser


class CompanionController:
    """把 Console 侧的工单计数翻译成原生窗口动作的轮询循环。

    Attributes:
        origin: 已校验的 Console origin。
        window: pywebview 窗口句柄。
        companion_url: 窗口固定加载的看板 URL。
    """

    def __init__(
        self,
        *,
        origin: str,
        expected_session: str,
        window: object,
        companion_url: str,
    ) -> None:
        self.origin = origin
        self.expected_session = expected_session
        self.window = window
        self.companion_url = companion_url
        self._last_focus_ticket: int | None = None
        self._last_close_ticket: int | None = None
        self._consecutive_failures = 0
        self._showing_disconnected = False
        self.stop_requested = False

    def run(self) -> None:
        """轮询状态直到需要退出；任何单次读取失败都不让窗口崩掉。

        轮询跑在 pywebview 的工作线程上，`func` 返回并不会让主线程的 GUI 循环
        结束，所以退出必须落到 ``window.close()``——窗口全关后 ``webview.start``
        才返回，进程才算真正收尾。
        """
        while not self.stop_requested:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                status = fetch_companion_status(self.origin)
            except (urllib.error.URLError, OSError, ValueError):
                self._handle_unreachable()
                continue
            self._consecutive_failures = 0
            self._recover_if_needed()
            self._apply(status)
        self._close_window()

    def _apply(self, status: dict[str, object]) -> None:
        """按状态快照执行聚焦 / 关闭 / 孤儿退出。"""
        session = str(status.get("console_session") or "")
        # Console 重启过：旧窗口属于上一个会话，留着只会一直显示旧 origin 的
        # 状态，不如直接退出，让用户在新 Console 里重新开启。
        if self.expected_session and session and session != self.expected_session:
            self.stop_requested = True
            return
        if not self.expected_session and session:
            self.expected_session = session

        close_ticket = status.get("close_ticket")
        focus_ticket = status.get("focus_ticket")
        if isinstance(close_ticket, int):
            if self._last_close_ticket is not None and close_ticket > self._last_close_ticket:
                self.stop_requested = True
                return
            self._last_close_ticket = close_ticket
        if isinstance(focus_ticket, int):
            if self._last_focus_ticket is not None and focus_ticket > self._last_focus_ticket:
                self._bring_to_front()
            self._last_focus_ticket = focus_ticket

    def _close_window(self) -> None:
        """关掉原生窗口：这是让 GUI 循环返回、进程退出的唯一路径。

        pywebview 6 的 ``Window`` 只有 ``destroy()``，没有 ``close()``；写错名字
        会静默无效，窗口永远留在桌面上，所以这里按可用方法名依次探测。
        """
        for name in ("destroy", "close"):
            closer = getattr(self.window, name, None)
            if callable(closer):
                closer()
                return

    def _bring_to_front(self) -> None:
        """把已存在的窗口带回前台，而不是再开一个。"""
        for name in ("restore", "show"):
            action = getattr(self.window, name, None)
            if callable(action):
                action()
        try:
            self.window.on_top = True  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - 各后端对置顶支持度不同，失败只降级为不置顶
            pass

    def _handle_unreachable(self) -> None:
        """连续失败到阈值才显示断连页，避免一次抖动刷掉看板。"""
        self._consecutive_failures += 1
        if self._consecutive_failures < _UNREACHABLE_THRESHOLD or self._showing_disconnected:
            return
        self._showing_disconnected = True
        loader = getattr(self.window, "load_html", None)
        if callable(loader):
            loader(_DISCONNECTED_HTML)

    def _recover_if_needed(self) -> None:
        """Console 恢复后重新加载真实看板，断连提示不残留在窗口里。"""
        if not self._showing_disconnected:
            return
        self._showing_disconnected = False
        loader = getattr(self.window, "load_url", None)
        if callable(loader):
            loader(self.companion_url)


def main(argv: list[str] | None = None) -> int:
    """helper 进程入口：解析 argv、开窗并在主线程上跑 GUI 循环。

    Args:
        argv: 命令行参数；缺省取 :data:`sys.argv`。

    Returns:
        进程退出码：0 正常关闭，2 origin 非法，3 桌面运行时缺失。
    """
    args = _build_parser().parse_args(argv)
    try:
        origin = validate_console_origin(args.origin)
    except ValueError as exc:
        print(f"desktop companion 拒绝启动：{exc}", file=sys.stderr)
        return 2
    try:
        import webview
    except ImportError:
        print(
            "desktop companion 拒绝启动：未安装桌面运行时（pip install 'kedacode[desktop]'）。",
            file=sys.stderr,
        )
        return 3

    companion_url = f"{origin}{COMPANION_PATH}"
    window = webview.create_window(
        title="KedaCode 运行时",
        url=companion_url,
        width=420,
        height=620,
        min_size=(360, 420),
        on_top=True,
    )
    controller = CompanionController(
        origin=origin,
        expected_session=args.session,
        window=window,
        companion_url=companion_url,
    )
    # GUI 循环必须留在主线程；pywebview 的 start(func) 把轮询放进自己的工作线程，
    # 主线程继续阻塞在 Cocoa 事件循环上（macOS 不接受子线程跑 GUI）。
    webview.start(controller.run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COMPANION_PATH",
    "STATUS_PATH",
    "CompanionController",
    "fetch_companion_status",
    "main",
    "validate_console_origin",
]

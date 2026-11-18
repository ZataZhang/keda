"""本机 Console 的请求来源守卫：挡住经浏览器跨站打到回环 API 的请求。

API 按「本机单用户」信任边界运行、不做认证，监听回环并不等于只有本人能访问：
用户浏览器里的任意网页都能向 ``http://127.0.0.1:<port>`` 发请求。两条路径需要挡：

- **DNS rebinding**：恶意域名解析到 ``127.0.0.1`` 后，浏览器把它当同源，读写接口全部
  可达。特征是 ``Host`` 头不是回环主机名。
- **跨站写请求**：不带请求体的 ``POST`` 属于「简单请求」，没有 CORS 预检，直接发出。
  特征是 ``Origin`` / ``Referer`` 不是回环来源。

守卫只管**到达回环地址的连接**（ASGI ``scope["server"]`` 是本次连接的本地地址）。
容器端口映射、显式 ``HOST=0.0.0.0`` 后经网卡地址进来的连接不归这里管——那类部署
的访问控制由外层网络边界负责。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

#: 回环主机名闭集：``localhost`` 与两个回环字面量指向同一个本机 Console。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: 不改状态的方法：只做 ``Host`` 校验，不要求来源头。
_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _url_hostname(url_text: str) -> str:
    """取 URL 的小写主机名；解析不了时返回空串（按非回环处理）。"""
    try:
        return (urlsplit(url_text).hostname or "").lower()
    except ValueError:
        return ""


def validate_loopback_request_source(scope: Scope) -> str | None:
    """校验到达回环地址的 HTTP 请求确实来自本机页面或非浏览器客户端。

    Args:
        scope: ASGI HTTP 连接的 scope。

    Returns:
        拒绝原因；请求可放行（含不归本守卫管的非回环连接）时返回 ``None``。
    """
    server_address = scope.get("server")
    if not server_address or str(server_address[0]) not in LOOPBACK_HOSTS:
        return None

    request_headers = Headers(scope=scope)
    raw_host_header = request_headers.get("host", "").strip()
    # 缺 Host 的只可能是非浏览器客户端（HTTP/1.0 探针），浏览器一定带。
    if raw_host_header and _url_hostname(f"http://{raw_host_header}") not in LOOPBACK_HOSTS:
        return f"Host {raw_host_header!r} 不是本机回环地址，拒绝该请求。"

    if scope["method"] in _READ_ONLY_METHODS:
        return None

    raw_origin_header = request_headers.get("origin", "").strip()
    origin_is_opaque = raw_origin_header.lower() == "null"
    # WebKit（桌面小窗）对同源 POST 可能不带 Origin 或写成 "null"，此时退回 Referer。
    claimed_source = (
        raw_origin_header
        if raw_origin_header and not origin_is_opaque
        else request_headers.get("referer", "").strip()
    )
    if not claimed_source:
        # 两个头都没有：curl / kc CLI 等非浏览器客户端。"null" 则是浏览器的不透明来源。
        if origin_is_opaque:
            return "Origin 为 null 且缺少 Referer，无法确认同源，拒绝该请求。"
        return None
    if _url_hostname(claimed_source) not in LOOPBACK_HOSTS:
        return f"来源 {claimed_source!r} 不是本机 Console 页面，拒绝该请求。"
    return None


class LoopbackRequestGuardMiddleware:
    """在路由之前执行 :func:`validate_loopback_request_source`，不通过即返回 403。"""

    def __init__(self, app: ASGIApp) -> None:
        """记录被包裹的下游 ASGI 应用。

        Args:
            app: 下游 ASGI 应用。
        """
        self._downstream_app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """对 HTTP 请求做来源校验，其余 ASGI 事件（lifespan 等）原样透传。

        Args:
            scope: ASGI scope。
            receive: ASGI receive 通道。
            send: ASGI send 通道。
        """
        if scope["type"] == "http":
            rejection_reason = validate_loopback_request_source(scope)
            if rejection_reason is not None:
                rejection_response = JSONResponse({"detail": rejection_reason}, status_code=403)
                await rejection_response(scope, receive, send)
                return
        await self._downstream_app(scope, receive, send)

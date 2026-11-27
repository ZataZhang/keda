"""浏览器宿主与 CLI 客户端的静态装配入口。"""

from backend.infrastructure.browser_host import (
    browser_host_session,
    request_browser_command,
)

__all__ = ["browser_host_session", "request_browser_command"]

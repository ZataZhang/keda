"""桌面 companion 端口的 core 装配入口（薄 facade）。

按四层依赖方向（``api → core → engines → infrastructure``），``api/`` 只依赖
``core/``，本模块把下层实现转发给上层。``core/`` 静态依赖 ``engines/`` 是
允许的（见 ``docs/ai-standards/architecture.md``），不再用 ``importlib``
动态加载：依赖因此对检查器、IDE 与类型检查都可见。本模块只做层次归位，
不新增编排逻辑；各名字语义以
:mod:`backend.engines.desktop_companion.launcher` 与
:mod:`backend.engines.desktop_companion.installer` 为准。
"""

from __future__ import annotations

from backend.engines.desktop_companion.installer import (
    DesktopCompanionInstallError,
    install_desktop_companion,
    is_desktop_companion_installed,
)
from backend.engines.desktop_companion.launcher import (
    create_desktop_companion_launcher,
    desktop_runtime_available,
    platform_supported,
    wait_for_helper_exit,
)


__all__ = [
    "create_desktop_companion_launcher",
    "desktop_runtime_available",
    "DesktopCompanionInstallError",
    "install_desktop_companion",
    "is_desktop_companion_installed",
    "platform_supported",
    "wait_for_helper_exit",
]

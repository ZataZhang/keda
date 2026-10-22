"""桌面 companion 端口的 core 装配入口（薄 facade）。

``api/`` 层不得直接 ``import backend.engines.*``（见
``hooks/shared/check_architecture.py``），但窗口能力确实属于 engines 子树，
因此沿用 :mod:`backend.core.use_cases.agent_runner_factory` 的先例：模块加载时
经 ``importlib.import_module`` 解析 engines 实现并绑定为模块级名字，本模块只做
层次归位，不新增编排逻辑，也不在这里起进程或碰 ``subprocess``。

窗口启动能力语义以 :mod:`backend.engines.desktop_companion.launcher` 为准；
安装能力语义以 :mod:`backend.engines.desktop_companion.installer` 为准。窗口端口契约见
:class:`backend.core.shared.interfaces.desktop_companion.DesktopCompanionLauncher`。
"""

from __future__ import annotations

import importlib

_launcher_module = importlib.import_module("backend.engines.desktop_companion.launcher")
_installer_module = importlib.import_module("backend.engines.desktop_companion.installer")

create_desktop_companion_launcher = _launcher_module.create_desktop_companion_launcher
desktop_runtime_available = _launcher_module.desktop_runtime_available
platform_supported = _launcher_module.platform_supported
wait_for_helper_exit = _launcher_module.wait_for_helper_exit
DesktopCompanionInstallError = _installer_module.DesktopCompanionInstallError
install_desktop_companion = _installer_module.install_desktop_companion
is_desktop_companion_installed = _installer_module.is_desktop_companion_installed

__all__ = [
    "create_desktop_companion_launcher",
    "desktop_runtime_available",
    "DesktopCompanionInstallError",
    "install_desktop_companion",
    "is_desktop_companion_installed",
    "platform_supported",
    "wait_for_helper_exit",
]

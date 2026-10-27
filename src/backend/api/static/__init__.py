"""随 wheel 分发的静态资源包。

``console/`` 子目录存放 ``frontend-public`` 静态导出产物，由发布流程
（或本地前端构建脚本）拷入，不直接入库（见 ``.gitignore``）。

产物路径在这里解析而不是写在 ``backend.api.app`` 里：CLI 启动预检只想确认
产物是否就位，不该为此导入整个 FastAPI 应用；挂载与预检共用同一个解析入口，
才能保证两边判定一致。
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable

#: 面板入口文件名：``frontend-public`` 静态导出后的首页，也是
#: ``just console-sync`` 判定构建成功的那一个产物。
CONSOLE_PANEL_ENTRY_FILE_NAME = "index.html"


def console_static_dir() -> Traversable:
    """返回管理终端前端静态产物目录。

    Returns:
        包资源中的管理终端静态产物目录，产物未构建或未随包分发时该目录不存在。
    """
    return files(__name__).joinpath("console")


def console_panel_dir_if_available() -> Traversable | None:
    """面板产物完整时返回其目录，否则返回 ``None``。

    只看目录是否存在会把「构建被打断、产物只同步了一半、解包残缺」判成就绪：
    目录在、入口文件不在，服务照常监听而浏览器只会拿到 404。因此以入口文件本身
    作为就绪信号，让挂载与 ``kc console`` 的启动预检共用同一个判定。

    Returns:
        面板入口文件存在时返回产物目录；目录缺失、目录为空或入口文件缺失均返回
        ``None``。
    """
    console_dir = console_static_dir()
    if console_dir.joinpath(CONSOLE_PANEL_ENTRY_FILE_NAME).is_file():
        return console_dir
    return None


__all__ = [
    "CONSOLE_PANEL_ENTRY_FILE_NAME",
    "console_panel_dir_if_available",
    "console_static_dir",
]

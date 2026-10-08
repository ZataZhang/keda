"""KedaCode 版本解析（CLI ``--version`` 与 console API 共用）。

把「查已安装的 ``kedacode`` 发行版版本、取不到时给一个形态合法的回退值」这条
规则只写一遍。分发包名是 ``kedacode``（PRD FR-1），查询必须用发行名而非仓库名
``keda``。
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata

__all__ = ["resolve_keda_version"]

#: PyPI / 本机发行版名，``kc --version`` 与 console 版本端点共用。
_DISTRIBUTION_NAME = "kedacode"

#: 查不到发行版元数据时的回退版本（例如未打包的 sdist 源码树里运行）。
_UNKNOWN_VERSION = "0.0.0+unknown"


def resolve_keda_version() -> str:
    """返回已安装的 ``kedacode`` 发行版版本。

    ``uv tool install --editable .`` 的 editable 安装会解析到 ``pyproject.toml``
    记录的版本；若发行版元数据不可得（例如在解包后的 sdist 里直接运行），返回
    :data:`_UNKNOWN_VERSION`，让 ``kc --version`` 的调用方拿到一个形态合法的值
    而不是异常。

    Returns:
        str: 版本字符串，取不到时回退为 ``"0.0.0+unknown"``。
    """
    try:
        return importlib_metadata.version(_DISTRIBUTION_NAME)
    except importlib_metadata.PackageNotFoundError:
        return _UNKNOWN_VERSION

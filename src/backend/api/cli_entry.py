"""kc 命令入口：加载完整 CLI 前拦截运行环境缺依赖的情况。

``kc`` / ``kedacode`` 两个 console script 指向本模块的 :func:`main`。完整 CLI
（:mod:`backend.api.cli`）导入时会加载整条依赖链；源码更新新增了第三方依赖、
安装环境却没有重装时（典型是可编辑安装 ``git pull`` 之后），任何命令都会直接
抛 ``ModuleNotFoundError`` 堆栈，daemon 调起的子命令也会反复失败。

本模块只依赖标准库，把这类错误转成一句可执行的修复提示，以退出码 1 结束。
``backend`` 自身的子模块缺失属于代码问题，原样抛出，不掩盖堆栈；命令执行
期间的延迟导入（如可选 extras）也不在拦截范围内。
"""

from __future__ import annotations

import json
import sys
from importlib import metadata as importlib_metadata
from urllib.parse import urlparse
from urllib.request import url2pathname

_DISTRIBUTION_NAME = "kedacode"
_OWN_TOP_LEVEL_PACKAGE = "backend"
_BROKEN_ENVIRONMENT_EXIT_CODE = 1


def _editable_source_directory() -> str | None:
    """可编辑安装时返回源码目录；非可编辑或读不到 PEP 610 安装记录时返回 ``None``。"""
    try:
        direct_url_text = importlib_metadata.distribution(_DISTRIBUTION_NAME).read_text(
            "direct_url.json"
        )
    except (importlib_metadata.PackageNotFoundError, OSError):
        return None
    if not direct_url_text:
        return None
    try:
        direct_url_record = json.loads(direct_url_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(direct_url_record, dict):
        return None
    directory_info = direct_url_record.get("dir_info")
    source_url = direct_url_record.get("url")
    if not (isinstance(directory_info, dict) and directory_info.get("editable")):
        return None
    if not isinstance(source_url, str) or not source_url.startswith("file:"):
        return None
    return url2pathname(urlparse(source_url).path)


def _reinstall_hint() -> str:
    """按当前安装方式给出重装依赖的命令。"""
    normalized_prefix = sys.prefix.replace("\\", "/").casefold().rstrip("/") + "/"
    is_uv_tool_install = "/uv/tools/" in normalized_prefix
    editable_source_directory = _editable_source_directory()
    if editable_source_directory is not None:
        if is_uv_tool_install:
            return f'uv tool install --reinstall --editable "{editable_source_directory}"'
        project_venv_prefix = f"{editable_source_directory}/.venv".replace("\\", "/").casefold()
        if normalized_prefix.rstrip("/") == project_venv_prefix:
            return f'uv sync  (run inside "{editable_source_directory}")'
        return f'uv pip install --python "{sys.executable}" -e "{editable_source_directory}"'
    if is_uv_tool_install:
        return f"uv tool install --reinstall {_DISTRIBUTION_NAME}"
    return f"reinstall {_DISTRIBUTION_NAME} with the installer you used (uv / pipx / pip)"


def _report_missing_dependency(missing_module_name: str) -> None:
    """把缺依赖的结论和修复命令写到 stderr。"""
    sys.stderr.write(
        f"kc cannot start: the Python module '{missing_module_name}' is not installed in "
        f"this kc environment ({sys.prefix}).\n"
        "This usually means kc's dependencies changed (for example after updating the "
        "source) but the environment was not reinstalled.\n"
        f"Fix it with:\n  {_reinstall_hint()}\n"
    )


def main(argv: list[str] | None = None) -> int:
    """kc console script 入口：先导入完整 CLI，缺第三方依赖时给出修复提示。

    Args:
        argv: 命令行参数；省略时由 CLI 自行读取 :data:`sys.argv`。

    Returns:
        CLI 退出码；运行环境缺依赖时为 ``1``。

    Raises:
        ModuleNotFoundError: 缺失的是 ``backend`` 自身的子模块（代码问题，保留堆栈）。
    """
    try:
        from backend.api.cli import main as cli_main
        from backend.api.cli_typer import main as _typer_main  # noqa: F401 - 预先暴露缺依赖
    except ModuleNotFoundError as import_error:
        missing_module_name = import_error.name or ""
        if not missing_module_name or (
            missing_module_name.split(".", 1)[0] == _OWN_TOP_LEVEL_PACKAGE
        ):
            raise
        _report_missing_dependency(missing_module_name)
        return _BROKEN_ENVIRONMENT_EXIT_CODE
    return cli_main(argv)


__all__ = ["main"]

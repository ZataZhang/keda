"""KedaCode 产品身份及运行时路径解析。

本模块只定义当前支持的产品名称：CLI 命令、环境变量前缀、本机状态目录、仓库配置
文件、随包 skill 和输出协议入口点。IAR 到 KedaCode 的一次性迁移逻辑不放在这里，
运行时不自动读取旧命令、环境变量或路径。
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

__all__ = [
    "COMPLETION_ENV_VAR_NAME",
    "ENV_PREFIX",
    "LONG_COMMAND_NAME",
    "OPERATOR_SKILL_NAME",
    "OUTPUT_PROTOCOL_ENTRY_POINT_GROUP",
    "OWN_COMMAND_NAMES",
    "PRIMARY_COMMAND_NAME",
    "PRODUCT_DISPLAY_NAME",
    "REPOSITORY_CONFIG_FILENAME",
    "STATE_DIR_NAME",
    "build_child_env",
    "effective_repository_config_path",
    "emit_notice_once",
    "has_repository_config",
    "is_own_command_name",
    "normalize_state_path",
    "read_product_env_value",
    "resolve_own_command_argv",
    "state_home",
]

PRIMARY_COMMAND_NAME = "kc"
"""用户敲的主命令名。"""

LONG_COMMAND_NAME = "kedacode"
"""与发行包同名的等价长写入口。"""

OWN_COMMAND_NAMES: tuple[str, ...] = (PRIMARY_COMMAND_NAME, LONG_COMMAND_NAME)
"""当前发行版提供的命令入口。"""

PRODUCT_DISPLAY_NAME = "KedaCode"
"""面向用户的显示名。"""

STATE_DIR_NAME = ".kedacode"
"""本机状态目录名。"""

REPOSITORY_CONFIG_FILENAME = ".kedacode.toml"
"""仓库本地配置文件名。"""

ENV_PREFIX = "KEDACODE_"
"""产品环境变量前缀。"""

OPERATOR_SKILL_NAME = "kedacode-operator"
"""随包 operator skill 名。"""

OUTPUT_PROTOCOL_ENTRY_POINT_GROUP = "kedacode.agent_output_protocols"
"""agent 输出协议的插件入口点分组。"""

COMPLETION_ENV_VAR_NAME = "_KC_COMPLETE"
"""Click shell 补全协议使用的环境变量名。"""

_EMITTED_NOTICE_SET: set[str] = set()
"""已通过 stderr 输出过的提示，用于同一进程内去重。"""


def emit_notice_once(notice_text: str | None) -> None:
    """把一条提示写到 stderr，同一进程内同一文本只输出一次。

    Args:
        notice_text: 提示文本；``None`` 或空串时不输出。
    """
    if not notice_text or notice_text in _EMITTED_NOTICE_SET:
        return
    _EMITTED_NOTICE_SET.add(notice_text)
    sys.stderr.write(f"{notice_text}\n")
    sys.stderr.flush()


def _reset_emitted_notices() -> None:
    """清空进程内提示去重集合（仅测试使用）。"""
    _EMITTED_NOTICE_SET.clear()


def state_home(home_path: Path | None = None) -> Path:
    """返回本机状态目录路径，不创建目录。

    Args:
        home_path: 家目录；省略时取 :meth:`pathlib.Path.home`。

    Returns:
        当前产品状态目录路径。
    """
    resolved_home_path = Path.home() if home_path is None else Path(home_path)
    return resolved_home_path / STATE_DIR_NAME


def read_product_env_value(
    env_suffix: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """读取 ``KEDACODE_<后缀>`` 环境变量，空白值视为未设置。

    Args:
        env_suffix: 环境变量后缀。
        environ: 环境变量映射；省略时读取 :data:`os.environ`。

    Returns:
        去除首尾空白后的环境变量值；未设置或为空白时为 ``None``。
    """
    source_environ = os.environ if environ is None else environ
    raw_value = source_environ.get(f"{ENV_PREFIX}{env_suffix}")
    if raw_value is None:
        return None
    stripped_value = raw_value.strip()
    return stripped_value or None


def build_child_env(values_by_suffix: Mapping[str, str]) -> dict[str, str]:
    """为派生子进程构造产品环境变量。

    Args:
        values_by_suffix: 后缀到取值的映射，例如 ``{"REPO_ID": "demo"}``。

    Returns:
        仅包含 ``KEDACODE_<后缀>`` 名称的环境变量字典。
    """
    return {
        f"{ENV_PREFIX}{env_suffix}": env_value for env_suffix, env_value in values_by_suffix.items()
    }


def effective_repository_config_path(repo_root: Path) -> Path:
    """返回仓库本地配置文件的当前路径。"""
    return Path(repo_root) / REPOSITORY_CONFIG_FILENAME


def has_repository_config(repo_root: Path) -> bool:
    """仓库是否存在当前命名的本地配置文件。"""
    return effective_repository_config_path(repo_root).is_file()


def _command_name_stem(command_name: str | None) -> str:
    """取可执行文件 basename、去掉 Windows 扩展名并转小写。"""
    if not command_name:
        return ""
    stem_name = Path(command_name).name.lower()
    for extension in (".exe", ".cmd", ".bat"):
        if stem_name.endswith(extension):
            stem_name = stem_name[: -len(extension)]
    return stem_name


def is_own_command_name(command_name: str | None) -> bool:
    """判断可执行文件名是否为当前产品入口 ``kc`` 或 ``kedacode``。

    Args:
        command_name: 可执行文件名或路径，允许 ``None``。

    Returns:
        命中当前发行版入口时为 ``True``。
    """
    return _command_name_stem(command_name) in OWN_COMMAND_NAMES


def resolve_own_command_argv(
    argv0_value: str | None,
    which_command: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """解析自我调用时应使用的命令 argv 前缀。

    Args:
        argv0_value: ``sys.argv[0]`` 或等价的可执行文件路径。
        which_command: PATH 查找函数，供测试注入。

    Returns:
        当前进程以产品入口启动时使用该入口；否则查找 ``kc``、``kedacode``，
        均不存在时回落到源码树命令 ``uv run kc``。
    """
    if is_own_command_name(argv0_value):
        return [str(argv0_value)]
    for own_command_name in OWN_COMMAND_NAMES:
        resolved_command_path = which_command(own_command_name)
        if resolved_command_path:
            return [resolved_command_path]
    return ["uv", "run", PRIMARY_COMMAND_NAME]


_STATE_PATH_PLACEHOLDERS: tuple[str, ...] = ("~", "$HOME", "${HOME}")
"""配置中支持的家目录占位写法。"""


def normalize_state_path(
    raw_path_value: str | Path,
    home_path: Path | None = None,
    state_home_path: Path | None = None,
) -> Path:
    """把当前状态目录前缀归一化到本机状态目录。

    Args:
        raw_path_value: 配置中的原始路径取值。
        home_path: 家目录；省略时取 :meth:`pathlib.Path.home`。
        state_home_path: 当前状态目录；省略时使用 :func:`state_home`。

    Returns:
        命中 ``.kedacode`` 前缀时替换成当前状态目录，否则保留原路径。
    """
    resolved_home_path = Path.home() if home_path is None else Path(home_path)
    effective_state_path = (
        state_home(resolved_home_path) if state_home_path is None else Path(state_home_path)
    )
    raw_path_text = str(raw_path_value)
    for placeholder in _STATE_PATH_PLACEHOLDERS:
        remainder_text = _strip_path_prefix(raw_path_text, f"{placeholder}/{STATE_DIR_NAME}")
        if remainder_text is not None:
            return effective_state_path.joinpath(*_split_remainder(remainder_text))
    absolute_prefix_text = str(resolved_home_path / STATE_DIR_NAME)
    remainder_text = _strip_path_prefix(raw_path_text, absolute_prefix_text)
    if remainder_text is not None:
        return effective_state_path.joinpath(*_split_remainder(remainder_text))
    return Path(raw_path_text)


def _strip_path_prefix(raw_path_text: str, prefix_text: str) -> str | None:
    """命中路径前缀时返回余下部分；否则返回 ``None``。"""
    if raw_path_text == prefix_text:
        return ""
    if raw_path_text.startswith(f"{prefix_text}/"):
        return raw_path_text[len(prefix_text) + 1 :]
    return None


def _split_remainder(remainder_text: str) -> tuple[str, ...]:
    """把剩余路径片段拆成可 join 的多段。"""
    return tuple(part for part in remainder_text.split("/") if part)

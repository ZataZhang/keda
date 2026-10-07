"""产品身份唯一事实源（KedaCode / ``kc``）与新旧名双读解析。

本模块只依赖标准库，处于四层架构的 ``core.shared.models``，因此 infrastructure、
core、engines、api 都可以引用它。它存在的理由是把「新名优先、旧名兜底」这一条
优先级规则只写一遍：命令名、本机状态目录、环境变量前缀、仓库配置文件名、随包
skill 名、插件入口点分组名，每个名字都有新旧两种写法，散落定义必然漂移。

约束：

- 解析函数是**纯函数**：允许只读文件系统探测（``exists`` / ``is_symlink`` /
  ``samefile``），不写盘、不打日志，只返回结果与提示文本。
- 提示的唯一出口是 :func:`emit_notice_once`，它直接写 ``sys.stderr``。不要用
  ``logger`` 输出提示：应用日志的 stdout 处理器挂在 root logger 上，人类模式会
  落进 stdout，而配置加载早于机器模式的日志改绑。
- 跨进程契约（GitHub 标记、Webhook 签名头、agent 执行标记、补全变量、
  ``.gitignore`` 托管块）不是用户可见名字，**永久保留旧拼写**，不在本模块改名。
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "ENV_PREFIX",
    "LEGACY_COMMAND_HINT",
    "LEGACY_COMMAND_NAME",
    "LEGACY_ENV_PREFIX",
    "LEGACY_OPERATOR_SKILL_NAME",
    "LEGACY_OUTPUT_PROTOCOL_ENTRY_POINT_GROUP",
    "LEGACY_REPOSITORY_CONFIG_FILENAME",
    "LEGACY_STATE_DIR_NAME",
    "LONG_COMMAND_NAME",
    "OPERATOR_SKILL_NAME",
    "OUTPUT_PROTOCOL_ENTRY_POINT_GROUP",
    "OWN_COMMAND_NAMES",
    "PRIMARY_COMMAND_NAME",
    "PRODUCT_DISPLAY_NAME",
    "REPOSITORY_CONFIG_FILENAME",
    "STATE_DIR_NAME",
    "build_child_env_aliases",
    "effective_repository_config_path",
    "emit_notice_once",
    "has_repository_config",
    "is_legacy_command_name",
    "is_own_command_name",
    "normalize_state_path",
    "read_product_env",
    "read_product_env_value",
    "resolve_own_command_argv",
    "resolve_repository_config_path",
    "resolve_state_home",
    "state_home",
]

PRIMARY_COMMAND_NAME = "kc"
"""用户敲的主命令名（可由产品名 KedaCode 推导）。"""

LONG_COMMAND_NAME = "kedacode"
"""与 PyPI 包名同名的等价长写入口，不提示改名。"""

LEGACY_COMMAND_NAME = "iar"
"""弃用别名的旧命令名，长期保留、不设移除日期。"""

OWN_COMMAND_NAMES: tuple[str, ...] = (PRIMARY_COMMAND_NAME, LONG_COMMAND_NAME, LEGACY_COMMAND_NAME)
"""自有命令名集合：进程识别、自我调用、REPL 前缀判定都用它。"""

PRODUCT_DISPLAY_NAME = "KedaCode"
"""面向用户的显示名（管理终端标题、文档、帮助）。"""

STATE_DIR_NAME = ".kedacode"
"""新的本机状态目录名。"""

LEGACY_STATE_DIR_NAME = ".iar"
"""旧的本机状态目录名，双读期间继续可用。"""

REPOSITORY_CONFIG_FILENAME = ".kedacode.toml"
"""新的仓库本地配置文件名。"""

LEGACY_REPOSITORY_CONFIG_FILENAME = ".iar.toml"
"""旧的仓库本地配置文件名。"""

ENV_PREFIX = "KEDACODE_"
"""新的环境变量前缀。"""

LEGACY_ENV_PREFIX = "IAR_"
"""旧的环境变量前缀，双读期间继续可用。"""

OPERATOR_SKILL_NAME = "kedacode-operator"
"""随包 operator skill 名。"""

LEGACY_OPERATOR_SKILL_NAME = "iar-operator"
"""旧随包 operator skill 名，仅用于清理历史副本。"""

OUTPUT_PROTOCOL_ENTRY_POINT_GROUP = "kedacode.agent_output_protocols"
"""新的 agent 输出协议入口点分组。"""

LEGACY_OUTPUT_PROTOCOL_ENTRY_POINT_GROUP = "iar.agent_output_protocols"
"""旧的 agent 输出协议入口点分组，第三方插件可能仍在注册。"""

#: 补全协议环境变量名是已安装补全脚本写死的契约，永久保留旧拼写。
COMPLETION_ENV_VAR_NAME = "_IAR_COMPLETE"

_SELF_PACKAGE_NAME = "kedacode"
"""发行包名（本次改名保持不变，出现在改名提醒文案里）。"""

LEGACY_COMMAND_HINT = (
    f"note: the '{LEGACY_COMMAND_NAME}' command has been renamed to "
    f"'{PRIMARY_COMMAND_NAME}' (package: {_SELF_PACKAGE_NAME}); "
    f"'{LEGACY_COMMAND_NAME}' keeps working as a deprecated alias."
)
"""仅在人直接敲 ``iar`` 时给出的改名提醒。"""

StateHomeSource = Literal["new", "legacy", "both", "absent"]
"""状态目录解析结果来源：新目录 / 旧目录 / 两者并存 / 都还没有。"""

EnvNameSource = Literal["new", "legacy", "conflict", "unset"]
"""环境变量取值来源：新名 / 旧名 / 新旧冲突 / 都未设置。"""

#: 已在 stderr 输出过的提示文本：同一进程内同一句只说一次，避免刷屏。
_EMITTED_NOTICE_SET: set[str] = set()


@dataclass(frozen=True)
class StateHomeResolution:
    """本机状态目录的解析结果。

    Attributes:
        path: 当前生效的状态目录路径（本函数不创建它）。
        source: 取值来源，见 :data:`StateHomeSource`。
        notice: 需要转达给用户的提示文本；无需提示时为 ``None``。
    """

    path: Path
    source: StateHomeSource
    notice: str | None


@dataclass(frozen=True)
class ProductEnvValue:
    """一个产品环境变量的双读结果。

    Attributes:
        suffix: 变量后缀（例如 ``CONFIG`` 对应 ``KEDACODE_CONFIG`` / ``IAR_CONFIG``）。
        value: 生效取值；两个名字都未设置时为 ``None``。
        source: 取值来源，见 :data:`EnvNameSource`。
        notice: 需要转达给用户的提示文本；无需提示时为 ``None``。
    """

    suffix: str
    value: str | None
    source: EnvNameSource
    notice: str | None


@dataclass(frozen=True)
class RepositoryConfigResolution:
    """仓库本地配置文件的解析结果。

    Attributes:
        path: 应读写（或新建）的配置文件路径。
        exists: 解析出的路径当前是否为已存在的文件。
        notice: 需要转达给用户的提示文本；无需提示时为 ``None``。
    """

    path: Path
    exists: bool
    notice: str | None


def emit_notice_once(notice_text: str | None) -> None:
    """把一条提示写到 stderr，同一进程内同一文本只说一次。

    全仓唯一的改名类提示出口。任何层都不许用 ``logger`` 输出这类提示，也不许
    另写一个 stderr 助手：应用日志的 stdout 处理器挂在 root logger 上，人类模式
    的提示会落进 stdout，配置加载期产生的提示在机器模式同样如此。

    Args:
        notice_text: 提示文本；``None`` 或空串时不输出任何东西。
    """
    if not notice_text or notice_text in _EMITTED_NOTICE_SET:
        return
    _EMITTED_NOTICE_SET.add(notice_text)
    sys.stderr.write(f"{notice_text}\n")
    sys.stderr.flush()


def _reset_emitted_notices() -> None:
    """清空进程内提示去重集合（仅测试使用）。"""
    _EMITTED_NOTICE_SET.clear()


def _same_directory(left_path: Path, right_path: Path) -> bool:
    """两只路径是否为同一个目录（跟随符号链接，因此链接与真实目录判为同一个）。"""
    try:
        return os.path.samefile(left_path, right_path)
    except OSError:
        return False


def resolve_state_home(home_path: Path | None = None) -> StateHomeResolution:
    """按「新目录优先」解析本机状态目录，不创建任何目录。

    解析表（PRD FR-3）：

    | 新目录 | 旧目录 | 结果 | 提示 |
    |---|---|---|---|
    | 存在 | 指向它的链接 / 同一目录 | 新目录 | 无 |
    | 存在 | 不存在 | 新目录 | 无 |
    | 不存在 | 真实目录或指向别处的链接 | 旧目录 | 可执行 ``kc config migrate`` |
    | 存在 | 另一个独立目录 | 新目录 | 警告旧目录被忽略 |
    | 不存在 | 不存在 | 新目录（由调用方首次写盘时创建） | 无 |

    Args:
        home_path: 家目录；省略时取 :meth:`pathlib.Path.home`。

    Returns:
        StateHomeResolution: 生效路径、来源与提示文本。
    """
    resolved_home_path = Path.home() if home_path is None else Path(home_path)
    new_state_path = resolved_home_path / STATE_DIR_NAME
    legacy_state_path = resolved_home_path / LEGACY_STATE_DIR_NAME
    new_dir_exists = new_state_path.is_dir()
    legacy_dir_exists = legacy_state_path.is_dir()
    if new_dir_exists:
        if legacy_dir_exists and not _same_directory(new_state_path, legacy_state_path):
            return StateHomeResolution(
                path=new_state_path,
                source="both",
                notice=(
                    f"warning: both '{legacy_state_path}' and '{new_state_path}' are separate "
                    f"directories; using '{new_state_path}' and ignoring the old one. "
                    "Run `kc config migrate` to merge them manually first, or remove whichever "
                    "directory you do not want."
                ),
            )
        return StateHomeResolution(path=new_state_path, source="new", notice=None)
    if legacy_dir_exists:
        return StateHomeResolution(
            path=legacy_state_path,
            source="legacy",
            notice=(
                f"note: the local state directory '{legacy_state_path}' has been renamed to "
                f"'{new_state_path}'; run `kc config migrate` to move it (the old name keeps "
                "working until then)."
            ),
        )
    return StateHomeResolution(path=new_state_path, source="absent", notice=None)


def state_home(home_path: Path | None = None) -> Path:
    """调用方便形式：解析状态目录并把提示交给 :func:`emit_notice_once`。

    Args:
        home_path: 家目录；省略时取 :meth:`pathlib.Path.home`。

    Returns:
        Path: 当前生效的本机状态目录路径。
    """
    resolution = resolve_state_home(home_path)
    emit_notice_once(resolution.notice)
    return resolution.path


def _meaningful_env_value(environ: Mapping[str, str], key: str) -> str | None:
    """读取环境变量，把未设置与全空白取值都视为「未设置」。"""
    raw_value = environ.get(key)
    if raw_value is None:
        return None
    stripped_value = raw_value.strip()
    return stripped_value or None


def read_product_env(
    environ: Mapping[str, str],
    env_suffix: str,
) -> ProductEnvValue:
    """按「新名优先、旧名兜底」解析一个产品环境变量。

    规则（PRD FR-4）：只有旧名时沿用旧值并提示一次；两者都有且取值不同时用新值
    并警告一次；两者相同时静默采用。任一情况都不许静默退回默认值——调用方拿到
    ``value is None`` 才能走默认分支。

    Args:
        environ: 环境变量映射（通常是 :data:`os.environ`）。
        env_suffix: 变量后缀，例如 ``CONFIG``、``SKILLS_DIR``。

    Returns:
        ProductEnvValue: 生效取值、来源与提示文本。
    """
    new_key = f"{ENV_PREFIX}{env_suffix}"
    legacy_key = f"{LEGACY_ENV_PREFIX}{env_suffix}"
    new_value = _meaningful_env_value(environ, new_key)
    legacy_value = _meaningful_env_value(environ, legacy_key)
    if new_value is not None:
        if legacy_value is not None and legacy_value != new_value:
            return ProductEnvValue(
                suffix=env_suffix,
                value=new_value,
                source="conflict",
                notice=(
                    f"warning: both '{legacy_key}' and '{new_key}' are set and differ; "
                    f"using '{new_key}'. Update your script to the '{ENV_PREFIX}' "
                    "name (see docs/guides/migrating-from-iar.md)."
                ),
            )
        return ProductEnvValue(suffix=env_suffix, value=new_value, source="new", notice=None)
    if legacy_value is not None:
        return ProductEnvValue(
            suffix=env_suffix,
            value=legacy_value,
            source="legacy",
            notice=(
                f"note: '{legacy_key}' has been renamed to '{new_key}'; the old name still "
                "works. Update your script or CI (see docs/guides/migrating-from-iar.md)."
            ),
        )
    return ProductEnvValue(suffix=env_suffix, value=None, source="unset", notice=None)


def read_product_env_value(
    env_suffix: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """调用方便形式：双读环境变量并把提示交给 :func:`emit_notice_once`。

    Args:
        env_suffix: 变量后缀。
        environ: 环境变量映射；省略时取 :data:`os.environ`。

    Returns:
        str | None: 生效取值，两个名字都未设置时为 ``None``。
    """
    source_environ = os.environ if environ is None else environ
    env_value = read_product_env(source_environ, env_suffix)
    emit_notice_once(env_value.notice)
    return env_value.value


def build_child_env_aliases(values_by_suffix: Mapping[str, str]) -> dict[str, str]:
    """为本产品派生的子进程同时生成新旧两套环境变量名。

    子进程可能是旧版本程序或用户的旧脚本，只给新名会让它读不到配置（PRD D-26）。

    Args:
        values_by_suffix: 后缀到取值的映射，例如 ``{"REPO_ID": "demo"}``。

    Returns:
        dict[str, str]: 同时含 ``KEDACODE_<后缀>`` 与 ``IAR_<后缀>`` 的字典。
    """
    alias_env: dict[str, str] = {}
    for env_suffix, env_value in values_by_suffix.items():
        alias_env[f"{ENV_PREFIX}{env_suffix}"] = env_value
        alias_env[f"{LEGACY_ENV_PREFIX}{env_suffix}"] = env_value
    return alias_env


def resolve_repository_config_path(repo_root: Path) -> RepositoryConfigResolution:
    """按「新文件优先」解析仓库本地配置文件路径。

    只有旧文件时读写都落在旧文件上且**日常不提醒**（每个受管仓库都会命中，逐条
    提醒等于刷屏）；两者并存时以新文件为准并警告一次；都没有时返回新文件名，供
    调用方新建。

    Args:
        repo_root: 仓库根目录。

    Returns:
        RepositoryConfigResolution: 读写路径、是否存在、提示文本。
    """
    resolved_root_path = Path(repo_root)
    new_config_path = resolved_root_path / REPOSITORY_CONFIG_FILENAME
    legacy_config_path = resolved_root_path / LEGACY_REPOSITORY_CONFIG_FILENAME
    new_config_exists = new_config_path.is_file()
    if new_config_exists:
        if legacy_config_path.is_file():
            return RepositoryConfigResolution(
                path=new_config_path,
                exists=True,
                notice=(
                    f"warning: both '{legacy_config_path.name}' and "
                    f"'{new_config_path.name}' exist in {resolved_root_path}; using "
                    f"'{new_config_path.name}'. Run `kc config migrate` to remove the old file."
                ),
            )
        return RepositoryConfigResolution(path=new_config_path, exists=True, notice=None)
    if legacy_config_path.is_file():
        return RepositoryConfigResolution(path=legacy_config_path, exists=True, notice=None)
    return RepositoryConfigResolution(path=new_config_path, exists=False, notice=None)


def effective_repository_config_path(repo_root: Path) -> Path:
    """调用方便形式：解析仓库配置文件路径并把提示交给 :func:`emit_notice_once`。"""
    resolution = resolve_repository_config_path(repo_root)
    emit_notice_once(resolution.notice)
    return resolution.path


def has_repository_config(repo_root: Path) -> bool:
    """仓库是否存在可用的本地配置文件（新旧任一）。"""
    resolution = resolve_repository_config_path(repo_root)
    emit_notice_once(resolution.notice)
    return resolution.exists


def _command_name_stem(command_name: str | None) -> str:
    """取可执行文件名的 basename、去掉 Windows 扩展名并转小写；入参为空时返回空串。"""
    if not command_name:
        return ""
    stem_name = Path(command_name).name.lower()
    for extension in (".exe", ".cmd", ".bat"):
        if stem_name.endswith(extension):
            stem_name = stem_name[: -len(extension)]
    return stem_name


def is_own_command_name(command_name: str | None) -> bool:
    """判断一个可执行文件名是否是本产品的自有命令名。

    取 basename 并去掉扩展名（Windows 的 ``iar.exe`` 同样命中），大小写不敏感。

    Args:
        command_name: 可执行文件名或路径，允许 ``None``。

    Returns:
        bool: 命中 :data:`OWN_COMMAND_NAMES` 之一时为 ``True``。
    """
    return _command_name_stem(command_name) in OWN_COMMAND_NAMES


def is_legacy_command_name(command_name: str | None) -> bool:
    """判断一个可执行文件名是否为弃用别名 :data:`LEGACY_COMMAND_NAME`。

    与 :func:`is_own_command_name` 同样的 basename / 扩展名归一化，但只认旧名——
    改名提醒只在人直接敲旧命令时出现，敲 ``kc`` / ``kedacode`` 都不该被提醒。

    Args:
        command_name: 可执行文件名或路径，允许 ``None``。

    Returns:
        bool: 命中的是旧别名时为 ``True``。
    """
    return _command_name_stem(command_name) == LEGACY_COMMAND_NAME


def resolve_own_command_argv(
    argv0_value: str | None,
    which_command: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """解析自我调用时应使用的命令 argv 前缀。

    顺序（PRD §6.1.6）：当前进程就是以三个自有名字之一启动的 → 用它；否则按
    ``kc`` → ``kedacode`` → ``iar`` 查 PATH；都找不到时回落到 ``uv run kc``
    （源码树场景）。

    Args:
        argv0_value: ``sys.argv[0]`` 或等价的可执行文件路径。
        which_command: PATH 查找函数，单元测试可注入。

    Returns:
        list[str]: argv 前缀列表。
    """
    if is_own_command_name(argv0_value):
        return [str(argv0_value)]
    for own_command_name in OWN_COMMAND_NAMES:
        resolved_command_path = which_command(own_command_name)
        if resolved_command_path:
            return [resolved_command_path]
    return ["uv", "run", PRIMARY_COMMAND_NAME]


_STATE_PATH_PLACEHOLDERS: tuple[str, ...] = ("~", "$HOME", "${HOME}")
"""配置里可能出现的家目录占位写法。"""


def normalize_state_path(
    raw_path_value: str | Path,
    home_path: Path | None = None,
    state_home_path: Path | None = None,
) -> Path:
    """把配置中写死的状态目录前缀归一化到当前生效的状态目录。

    迁移前后、旧路径链接存在与否，同一份配置都应指向同一处；否则比较路径时会
    因为链接把同一个目录误判为两个。命中的前缀形态：``~/.iar``、``~/.kedacode``、
    ``$HOME/...``、``${HOME}/...`` 以及家目录绝对路径形式。

    Args:
        raw_path_value: 配置中的原始路径取值。
        home_path: 家目录；省略时取 :meth:`pathlib.Path.home`。
        state_home_path: 当前生效状态目录；省略时自行解析（不输出提示）。

    Returns:
        Path: 归一化后的路径；不含状态目录前缀时原样返回。
    """
    resolved_home_path = Path.home() if home_path is None else Path(home_path)
    effective_state_path = (
        resolve_state_home(resolved_home_path).path
        if state_home_path is None
        else Path(state_home_path)
    )
    raw_path_text = str(raw_path_value)
    for state_dir_name in (STATE_DIR_NAME, LEGACY_STATE_DIR_NAME):
        for placeholder in _STATE_PATH_PLACEHOLDERS:
            prefix_text = f"{placeholder}/{state_dir_name}"
            remainder_text = _strip_path_prefix(raw_path_text, prefix_text)
            if remainder_text is not None:
                return effective_state_path.joinpath(*_split_remainder(remainder_text))
        absolute_prefix_text = str(resolved_home_path / state_dir_name)
        remainder_text = _strip_path_prefix(raw_path_text, absolute_prefix_text)
        if remainder_text is not None:
            return effective_state_path.joinpath(*_split_remainder(remainder_text))
    return Path(raw_path_text)


def _strip_path_prefix(raw_path_text: str, prefix_text: str) -> str | None:
    """命中前缀时返回去掉前缀后的剩余部分（无剩余时为空串），未命中返回 ``None``。"""
    if raw_path_text == prefix_text:
        return ""
    if raw_path_text.startswith(f"{prefix_text}/"):
        return raw_path_text[len(prefix_text) + 1 :]
    return None


def _split_remainder(remainder_text: str) -> Sequence[str]:
    """把剩余路径片段拆成可 join 的多段。"""
    return tuple(part for part in remainder_text.split("/") if part)

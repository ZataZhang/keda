"""配置文件发现与 TOML 设置源。

本模块是 ``settings.py`` 的最低层：负责 ``.kedacode.toml`` / ``config.toml`` 的
定位，以及把 TOML 段落接入 pydantic-settings 的自定义 source。它不依赖任何设置
模型，``settings.py`` 与 ``agent_runner_settings.py`` 都从这里取用。新旧名字的
解析规则一律交给 :mod:`backend.core.shared.models.product_identity`。
"""

import tomllib
from pathlib import Path
from typing import Any

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
)

from backend.core.shared.models import product_identity
from backend.core.shared.models.config_edit import ConfigLockBusyError
from backend.infrastructure.config.toml_section_editor import config_write_lock

_SETTINGS_FILE_PATH: Path = Path(__file__).resolve()
#: 全局安装的 ``kc`` 拿不到源码模板时，用来承载 ``[agent_runner.repositories]`` 的最小配置。
_MINIMAL_REGISTRY_CONFIG = "[agent_runner]\n"
_CONFIG_DIR_PATH: Path = _SETTINGS_FILE_PATH.parent
_INFRASTRUCTURE_DIR_PATH: Path = _CONFIG_DIR_PATH.parent
_BACKEND_DIR_PATH: Path = _INFRASTRUCTURE_DIR_PATH.parent
_SOURCE_DIR_PATH: Path = _BACKEND_DIR_PATH.parent


def _resolve_project_root_from_settings_path(settings_path: Path) -> Path:
    """Locate the project root by searching upward for ``pyproject.toml``.

    First searches from the settings file location, then from the current
    working directory. Falls back to the legacy directory-based heuristic when
    no marker file is found, preserving behavior for non-package invocations.
    """
    for start_path in (settings_path, Path.cwd()):
        for parent in start_path.parents:
            if (parent / "pyproject.toml").is_file():
                return parent
    return _SOURCE_DIR_PATH.parent


_PROJECT_ROOT_PATH: Path = _resolve_project_root_from_settings_path(_SETTINGS_FILE_PATH)


def _global_state_dir() -> Path:
    """Return the effective KedaCode state directory under the user's home.

    新旧目录双读（新目录优先，只有旧目录时沿用旧目录并提示迁移），提示由身份
    模块一次性写到 stderr。
    """
    return product_identity.state_home()


def _ensure_global_config_toml() -> Path | None:
    """确保状态目录的全局配置存在；仅在首次创建时从 Keda 源码配置播种。

    正常运行时，状态目录文件是唯一全局配置源；源码根配置不会按 cwd 被直接读取。
    只有解析结果指向新目录时才可能新建目录；机器上只有旧目录时沿用旧目录，
    不会悄悄新建 ``~/.kedacode``。
    """
    global_dir = _global_state_dir()
    global_config = global_dir / "config.toml"
    if global_config.is_file():
        return global_config
    source_config = _PROJECT_ROOT_PATH / "config.toml"
    if not source_config.is_file() or not _is_product_config_toml(source_config):
        return None
    try:
        global_dir.mkdir(parents=True, exist_ok=True)
        with config_write_lock(global_config):
            # 存在性检查要在锁内重做：两个首次启动的进程都在锁外看到"文件还没有"，
            # 就会各写一份 seed，把先一步的 registry 写入者刚落的段一起截掉。
            if global_config.is_file():
                return global_config
            global_config.write_text(
                source_config.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        return global_config
    except OSError:
        return None
    except ConfigLockBusyError:
        # 已有写入者持锁在这份配置上动手：本次 seed 退出，交回它写出的结果。
        return global_config if global_config.is_file() else None


def _is_product_config_toml(candidate: Path) -> bool:
    """判断 ``candidate`` 是否包含可用于首次播种的 KedaCode 配置。

    运行时全局配置固定来自状态目录；源码根文件只用于首次创建状态目录配置。
    模板派生项目仓库根的应用级 ``config.toml`` 通常不含 ``[agent_runner]``，
    因此不能拿来播种全局设置。

    Args:
        candidate: 待判定的 ``config.toml`` 路径。

    Returns:
        文件可解析且含顶层 ``agent_runner`` 表时为 ``True``；读取或解析失败时
        为 ``False``（发现阶段不该因为一个坏文件而崩溃）。
    """
    try:
        with open(candidate, "rb") as config_file:
            parsed_config: dict[str, Any] = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return isinstance(parsed_config.get("agent_runner"), dict)


def _resolve_env_config_toml() -> Path | None:
    """``KEDACODE_CONFIG``（旧前缀写法继续兜底）指向的配置文件；不可达时 ``None``。

    环境变量在两种解析里都是最高优先级，故提取共用；静默退回默认配置是被禁止的，
    所以取值解析与提示都由身份模块负责。
    """
    env_config = product_identity.read_product_env_value("CONFIG")
    if not env_config:
        return None
    env_path = Path(env_config).expanduser()
    if env_path.is_file():
        return env_path
    if env_path.is_dir():
        candidate = env_path / "config.toml"
        if candidate.is_file():
            return candidate
    return None


def _seed_registry_config_in_state_home() -> Path | None:
    """在没有源码模板可 seed 时，在状态目录里落一份最小的 registry 载体。

    全局安装的 ``kc``（``uv tool install kedacode``）跑在任何目录里都找不到 keda 源码根的
    ``config.toml``，此前 ``resolve_registry_config_toml_path()`` 会返回那个不存在的路径，
    读取时抛 ``FileNotFoundError``，`kc registry list` / `sync` 直接以退出码 3 失败。
    这里改成在状态目录里创建只含 ``[agent_runner]`` 空表的最小配置：状态目录本来就优先
    解析到 ``~/.kedacode``，只有旧目录的机器仍写到 ``~/.iar``（legacy-alias），不新建目录。

    Returns:
        创建成功或已存在时返回该路径；目录或文件写失败时返回 ``None``。
    """
    global_dir = _global_state_dir()
    target = global_dir / "config.toml"
    if target.is_file():
        return target
    try:
        global_dir.mkdir(parents=True, exist_ok=True)
        with config_write_lock(target):
            # 同上：seed 前先确认没有别的写入者在持锁期间已经建好这份配置。
            if target.is_file():
                return target
            target.write_text(_MINIMAL_REGISTRY_CONFIG, encoding="utf-8")
    except OSError:
        return None
    except ConfigLockBusyError:
        return target if target.is_file() else None
    return target


def _global_then_source_fallback() -> Path | None:
    """cwd 向上查找落空时的两级回落：``<state home>/config.toml`` → 源码根。"""
    global_config = _ensure_global_config_toml()
    if global_config is not None:
        return global_config
    fallback = _PROJECT_ROOT_PATH / "config.toml"
    if fallback.is_file():
        return fallback
    return None


def _find_config_toml() -> Path | None:
    """Resolve the effective *project* config.toml using the standard search order.

    宿主应用（含从 keda 模板派生的项目）把应用配置写在仓库根的 ``config.toml``
    （``[app]`` / ``[database]`` / ``[preview]`` …），因此这里的 cwd 向上查找接受
    任何同名文件；KedaCode 自己的机器级配置见 :func:`_find_product_config_toml`。

    Search order:
    1. ``KEDACODE_CONFIG`` (or its legacy-prefixed equivalent) environment variable, if set.
    2. Walk upward from the current working directory.
    3. ``<state home>/config.toml`` (seeded from the source root if missing).
    4. keda source root config.toml.
    """
    env_config = _resolve_env_config_toml()
    if env_config is not None:
        return env_config

    cwd = Path.cwd()
    for path in [cwd, *cwd.parents]:
        candidate = path / "config.toml"
        if candidate.is_file():
            return candidate

    return _global_then_source_fallback()


def _find_product_config_toml() -> Path:
    """解析 KedaCode 的全局 ``config.toml``。

    显式 ``KEDACODE_CONFIG`` 是可覆盖入口；未设置时始终使用状态目录配置，
    不按当前工作目录寻找 ``config.toml``。源码根文件只可能在状态目录首次创建时
    被复制一次，不会在已有全局配置时覆盖它。
    """
    env_config = _resolve_env_config_toml()
    if env_config is not None:
        return env_config

    global_config = _ensure_global_config_toml()
    return global_config or (_global_state_dir() / "config.toml")


def resolve_config_toml_path() -> Path:
    """解析当前生效的**全局** ``config.toml`` 路径。

    生命周期矩阵写回、托管进程注入的 ``KEDACODE_CONFIG`` 都使用该路径。
    未设置显式覆盖时，它始终指向状态目录，不会回退读取当前仓库的 ``config.toml``。
    """
    return _find_product_config_toml()


def resolve_registry_config_toml_path() -> Path:
    """解析仓库 registry 使用的全局 config.toml 路径。

    Registry 记录的是 KedaCode 托管的所有仓库，必须是全局共享的，不能因为
    用户在某个项目目录内执行命令就写入该项目的 config.toml。

    解析顺序：
    1. ``KEDACODE_CONFIG`` 环境变量（旧前缀写法继续兜底，如果显式设置），
       用于测试或高级用户覆盖。
    2. ``<state home>/config.toml``（首次调用时从源码根目录 seed 默认配置）。
    3. keda 源码根目录 ``config.toml`` 作为 fallback。
    4. 源码根也没有模板时（全局安装的 ``kc``），在状态目录里创建最小配置承接 registry。
    """
    env_config = product_identity.read_product_env_value("CONFIG")
    if env_config:
        env_path = Path(env_config).expanduser()
        if env_path.is_file() or env_path.parent.exists():
            return env_path
    global_config = _ensure_global_config_toml()
    if global_config is not None:
        return global_config
    source_config = _PROJECT_ROOT_PATH / "config.toml"
    if source_config.is_file():
        return source_config
    return _seed_registry_config_in_state_home() or source_config


def resolve_project_root_path() -> Path:
    """返回 keda 项目源码根目录（托管进程的默认 cwd）。"""
    return _PROJECT_ROOT_PATH


#: ``config.toml`` 里归 KedaCode 自己所有的段落。只有这些段必须从**机器级**配置读取
#: （见 :func:`_find_product_config_toml`）；其余段落属于宿主应用，继续按 cwd 向上查找
#: 的项目 ``config.toml`` 读取——派生项目里的 ``preview_env.py`` 正是靠这一点读到
#: 本仓的 ``[preview]``。
_PRODUCT_OWNED_TOML_SECTIONS = frozenset({"agent_runner"})


def _load_toml_section_data(section_name: str) -> dict[str, Any]:
    """从 config.toml 加载指定 section 的配置。

    ``agent_runner`` 段归 KedaCode 自己所有，走机器级配置解析（跳过应用级同名
    文件）；其它段归宿主应用，沿用按 cwd 向上查找的项目 ``config.toml``。

    Args:
        section_name: TOML section 名称。

    Returns:
        section 内容字典，文件不存在或 section 不存在时返回空 dict。
    """
    toml_path = (
        _find_product_config_toml()
        if section_name in _PRODUCT_OWNED_TOML_SECTIONS
        else _find_config_toml()
    )
    if toml_path is None:
        return {}
    try:
        with open(toml_path, "rb") as toml_file:
            toml_data: dict[str, Any] = tomllib.load(toml_file)
        return toml_data.get(section_name, {})
    except Exception:
        return {}


def _load_registry_toml_section_data(section_name: str) -> dict[str, Any]:
    """从 registry 专用的 config.toml 加载指定 section。

    Registry 与通用配置解耦：仓库列表必须全局共享，因此优先读取
    ``KEDACODE_CONFIG``（旧名兜底）或 ``<state home>/config.toml``；仅当全局
    registry 不存在时 fallback 到当前生效的 config.toml（兼容 legacy 项目级
    registry）。

    Args:
        section_name: TOML section 名称。

    Returns:
        section 内容字典，文件不存在或 section 不存在时返回空 dict。
    """
    registry_path = resolve_registry_config_toml_path()
    try:
        with open(registry_path, "rb") as toml_file:
            toml_data: dict[str, Any] = tomllib.load(toml_file)
        return toml_data.get(section_name, {})
    except Exception:
        return {}


class _TomlSectionSource(PydanticBaseSettingsSource):
    """读取 config.toml 指定 section，并可跳过由专用来源管理的字段。"""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        section_name: str,
        *,
        excluded_field_names: frozenset[str] = frozenset(),
    ) -> None:
        """初始化 TOML 来源及其排除字段。"""
        super().__init__(settings_cls)
        self._section_data: dict[str, Any] = _load_toml_section_data(section_name)
        self._excluded_field_names = excluded_field_names

    def get_field_value(
        self,
        field: Any,  # noqa: ARG002
        field_name: str,
    ) -> tuple[Any, str, bool]:
        if field_name in self._excluded_field_names:
            return None, field_name, False
        field_value: Any = self._section_data.get(field_name)
        return field_value, field_name, False

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field_name in self.settings_cls.model_fields:
            if field_name in self._excluded_field_names:
                continue
            field_value: Any = self._section_data.get(field_name)
            if field_value is not None:
                result[field_name] = field_value
        return result


class _NestedTomlSectionSource(PydanticBaseSettingsSource):
    """把 config.toml 的**顶层** section 挂到目标模型的单个字段上。

    ``[agent_session]`` 与 ``[agent_runner]`` 是两个顶层命名空间，但都由
    :class:`AgentRunnerSettings` 这一个配置根承载（``AppConfig`` 只经这一个根装配，
    不为第二段配置再开一条加载链）。于是顶层 section 需要一次"降一级"的映射：
    读到 ``agent_session`` 整段，写成 ``{"session": 整段}``。

    与 :class:`_TomlSectionSource` 一致：文件缺失或 section 缺失时返回空，配置加载
    照常完成并落到模型默认值（未配置不等于错误）。
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        section_name: str,
        target_field_name: str,
    ) -> None:
        super().__init__(settings_cls)
        self._target_field_name = target_field_name
        self._section_data: dict[str, Any] = _load_toml_section_data(section_name)

    def get_field_value(
        self,
        field: Any,  # noqa: ARG002
        field_name: str,
    ) -> tuple[Any, str, bool]:
        field_value: Any = self._section_data if field_name == self._target_field_name else None
        return field_value, field_name, False

    def __call__(self) -> dict[str, Any]:
        if not self._section_data or self._target_field_name not in self.settings_cls.model_fields:
            return {}
        return {self._target_field_name: self._section_data}


def _env_toml_init_sources(
    settings_cls: type[BaseSettings],
    section_name: str,
    env_settings: PydanticBaseSettingsSource,
    init_settings: PydanticBaseSettingsSource,
) -> tuple[PydanticBaseSettingsSource, ...]:
    """Build the standard env > TOML > init settings source order."""
    toml_source: _TomlSectionSource = _TomlSectionSource(settings_cls, section_name)
    return (
        env_settings,
        toml_source,
        init_settings,
    )


class _RegistryRepositoriesSource(PydanticBaseSettingsSource):
    """从 registry 专用 config.toml 读取 ``[agent_runner.repositories]`` 的源。"""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        agent_runner_data = _load_registry_toml_section_data("agent_runner")
        self._repositories: dict[str, Any] = agent_runner_data.get("repositories", {})

    def get_field_value(
        self,
        field: Any,  # noqa: ARG002
        field_name: str,
    ) -> tuple[Any, str, bool]:
        if field_name == "repositories":
            return self._repositories, field_name, False
        return None, field_name, False  # type: ignore[return-value]

    def __call__(self) -> dict[str, Any]:
        return {"repositories": self._repositories} if self._repositories else {}

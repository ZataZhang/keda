"""配置文件发现与 TOML 设置源。

本模块是 ``settings.py`` 的最低层：负责 .iar.toml / config.toml 的定位，以及把
TOML 段落接入 pydantic-settings 的自定义 source。它不依赖任何设置模型，
``settings.py`` 与 ``agent_runner_settings.py`` 都从这里取用。
"""

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
)

_SETTINGS_FILE_PATH: Path = Path(__file__).resolve()
_CONFIG_DIR_PATH: Path = _SETTINGS_FILE_PATH.parent
_INFRASTRUCTURE_DIR_PATH: Path = _CONFIG_DIR_PATH.parent
_BACKEND_DIR_PATH: Path = _INFRASTRUCTURE_DIR_PATH.parent
_SOURCE_DIR_PATH: Path = _BACKEND_DIR_PATH.parent
IAR_REPOSITORY_CONFIG_FILENAME = ".iar.toml"


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


def _global_iar_dir() -> Path:
    """Return the global IAR state directory under the user's home."""
    return Path.home() / ".iar"


def _ensure_global_config_toml() -> Path | None:
    """Ensure ``~/.iar/config.toml`` exists, seeding from the source root.

    This provides a stable configuration home for globally-installed ``iar``
    invocations outside any project directory.
    """
    global_dir = _global_iar_dir()
    global_config = global_dir / "config.toml"
    if global_config.is_file():
        return global_config
    source_config = _PROJECT_ROOT_PATH / "config.toml"
    if not source_config.is_file():
        return None
    try:
        global_dir.mkdir(parents=True, exist_ok=True)
        global_config.write_text(
            source_config.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return global_config
    except OSError:
        return None


def _find_config_toml() -> Path | None:
    """Resolve the effective config.toml using the standard search order.

    Search order:
    1. ``IAR_CONFIG`` environment variable, if set.
    2. Walk upward from the current working directory.
    3. ``~/.iar/config.toml`` (seeded from the source root if missing).
    4. keda source root config.toml.
    """
    env_config = os.environ.get("IAR_CONFIG")
    if env_config:
        env_path = Path(env_config).expanduser()
        if env_path.is_file():
            return env_path
        if env_path.is_dir():
            candidate = env_path / "config.toml"
            if candidate.is_file():
                return candidate

    cwd = Path.cwd()
    for path in [cwd, *cwd.parents]:
        candidate = path / "config.toml"
        if candidate.is_file():
            return candidate

    global_config = _ensure_global_config_toml()
    if global_config is not None:
        return global_config

    fallback = _PROJECT_ROOT_PATH / "config.toml"
    if fallback.is_file():
        return fallback
    return None


def resolve_config_toml_path() -> Path:
    """解析当前生效的 config.toml 路径（找不到时回退到源码根目录）。"""
    return _find_config_toml() or (_PROJECT_ROOT_PATH / "config.toml")


def resolve_registry_config_toml_path() -> Path:
    """解析仓库 registry 使用的全局 config.toml 路径。

    Registry 记录的是 IAR 托管的所有仓库，必须是全局共享的，不能因为
    用户在某个项目目录内执行命令就写入该项目的 config.toml。

    解析顺序：
    1. ``IAR_CONFIG`` 环境变量（如果显式设置），用于测试或高级用户覆盖。
    2. ``~/.iar/config.toml``（首次调用时从源码根目录 seed 默认配置）。
    3. keda 源码根目录 ``config.toml`` 作为最后 fallback。
    """
    env_config = os.environ.get("IAR_CONFIG")
    if env_config:
        env_path = Path(env_config).expanduser()
        if env_path.is_file() or env_path.parent.exists():
            return env_path
    global_config = _ensure_global_config_toml()
    if global_config is not None:
        return global_config
    return _PROJECT_ROOT_PATH / "config.toml"


def resolve_project_root_path() -> Path:
    """返回 keda 项目源码根目录（托管进程的默认 cwd）。"""
    return _PROJECT_ROOT_PATH


def _load_toml_section_data(section_name: str) -> dict[str, Any]:
    """从 config.toml 加载指定 section 的配置。

    Args:
        section_name: TOML section 名称。

    Returns:
        section 内容字典，文件不存在或 section 不存在时返回空 dict。
    """
    toml_path = _find_config_toml()
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
    ``IAR_CONFIG`` 或 ``~/.iar/config.toml``；仅当全局 registry 不存在时
    fallback 到当前生效的 config.toml（兼容 legacy 项目级 registry）。

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
    """从 config.toml 指定 section 读取配置的自定义源。"""

    def __init__(self, settings_cls: type[BaseSettings], section_name: str) -> None:
        super().__init__(settings_cls)
        self._section_data: dict[str, Any] = _load_toml_section_data(section_name)

    def get_field_value(
        self,
        field: Any,  # noqa: ARG002
        field_name: str,
    ) -> tuple[Any, str, bool]:
        field_value: Any = self._section_data.get(field_name)
        return field_value, field_name, False

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field_name in self.settings_cls.model_fields:
            field_value: Any = self._section_data.get(field_name)
            if field_value is not None:
                result[field_name] = field_value
        return result


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

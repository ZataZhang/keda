"""仓库 registry（config.toml）的受限写回实现。

使用 ``tomlkit`` 做 round-trip 编辑：保留文件中的全部注释与格式，
只触碰 ``agent_runner.repositories.<repo_id>`` 子树。round-trip、跨进程锁与
原子替换**不在本模块实现**：一律委托
:func:`backend.infrastructure.config.toml_section_editor.mutate_toml_document`
——它与生命周期设置写入器共用同一份落盘原语（唯一临时名、沿用目标权限位、
失败不动原文件），因此两个写入者同时改 ``config.toml`` 时不会互相截断、
不会把配置权限抖成 0600，也不会互相丢键。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.items import Table

from backend.infrastructure.config.toml_section_editor import mutate_toml_document


@dataclass(frozen=True)
class RegistryRepositoryEntry:
    """registry 中一个仓库条目的摘要视图（与 core 同构，鸭子类型）。"""

    repo_id: str
    path: str
    enabled: bool
    display_name: str | None
    path_exists: bool


class TomlRegistryEditor:
    """``IRepositoryRegistryEditor`` 端口的 tomlkit 实现（鸭子类型）。"""

    def __init__(self, config_path: str | Path) -> None:
        """初始化编辑器。

        Args:
            config_path: ``config.toml`` 的路径。
        """
        self._config_path = Path(config_path).expanduser()

    def _read_document(self) -> tomlkit.TOMLDocument:
        """读取 round-trip 文档（文件缺失时按 ``OSError`` 失败，不返回空 registry）。"""
        return tomlkit.parse(self._config_path.read_text(encoding="utf-8"))

    def _mutate_document(self, mutate: Callable[[tomlkit.TOMLDocument], None]) -> None:
        """锁内完成一次读-改-写：``mutate`` 抛异常则原文件一字不动。"""
        mutate_toml_document(self._config_path, mutate)

    @staticmethod
    def _repositories_table(document: tomlkit.TOMLDocument) -> Table:
        agent_runner_table = document.get("agent_runner")
        if agent_runner_table is None:
            agent_runner_table = tomlkit.table(is_super_table=True)
            document["agent_runner"] = agent_runner_table
        repositories_table = agent_runner_table.get("repositories")
        if repositories_table is None:
            repositories_table = tomlkit.table(is_super_table=True)
            agent_runner_table["repositories"] = repositories_table
        return repositories_table

    @staticmethod
    def _require_repositories(document: tomlkit.TOMLDocument, repo_id: str) -> Table:
        """取已包含 ``repo_id`` 条目的 ``repositories`` 表。

        Raises:
            KeyError: registry 里没有该仓库条目。
        """
        agent_runner_table = document.get("agent_runner") or {}
        repositories_table = agent_runner_table.get("repositories")
        if repositories_table is None or repo_id not in repositories_table:
            raise KeyError(f"Repository '{repo_id}' not found in the registry.")
        return repositories_table

    def list_repositories(self) -> list[RegistryRepositoryEntry]:
        """列出 registry 中的全部仓库条目。"""
        document = self._read_document()
        agent_runner_table = document.get("agent_runner") or {}
        repositories_table = agent_runner_table.get("repositories") or {}
        registry_entries: list[RegistryRepositoryEntry] = []
        for repo_id, repo_table in repositories_table.items():
            configured_path = str(repo_table.get("path", ""))
            resolved_path = Path(configured_path).expanduser()
            registry_entries.append(
                RegistryRepositoryEntry(
                    repo_id=str(repo_id),
                    path=configured_path,
                    enabled=bool(repo_table.get("enabled", True)),
                    display_name=(
                        str(repo_table["display_name"]) if "display_name" in repo_table else None
                    ),
                    path_exists=resolved_path.exists(),
                )
            )
        return registry_entries

    def add_repository(self, *, repo_id: str, path: str, display_name: str | None) -> None:
        """新增一个仓库条目（enabled 默认 true）。"""

        def _insert(document: tomlkit.TOMLDocument) -> None:
            repositories_table = self._repositories_table(document)
            if repo_id in repositories_table:
                raise ValueError(f"Repository '{repo_id}' already exists in the registry.")
            repository_table = tomlkit.table()
            repository_table["path"] = path
            repository_table["enabled"] = True
            if display_name:
                repository_table["display_name"] = display_name
            repositories_table[repo_id] = repository_table

        self._mutate_document(_insert)

    def set_enabled(self, repo_id: str, *, enabled: bool) -> None:
        """启用或停用一个已有条目。"""

        def _toggle(document: tomlkit.TOMLDocument) -> None:
            self._require_repositories(document, repo_id)[repo_id]["enabled"] = enabled

        self._mutate_document(_toggle)

    def remove_repository(self, repo_id: str) -> None:
        """从 registry 中删除一个仓库条目。"""

        def _remove(document: tomlkit.TOMLDocument) -> None:
            del self._require_repositories(document, repo_id)[repo_id]

        self._mutate_document(_remove)

"""保留式 TOML 表键写回。

用 ``tomlkit`` 做 round-trip 编辑：保留文件中的全部注释、空行与格式，只触碰
调用方点名的键。写入采用「同目录临时文件 + ``os.replace``」原子替换，避免写坏
配置文件。

``config.toml``（机器级）与 ``.iar.toml``（仓库级）共用本模块——两处写回语义
必须一致：**只写请求里显式给出的键**（值为 ``None`` 表示删除该键），文件其余
内容一字不动。
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import Table


def _ensure_table(document: tomlkit.TOMLDocument, table_path: Sequence[str]) -> Table:
    """沿 ``table_path`` 逐级取（或建）表，返回叶子表。

    Args:
        document: 已解析的 TOML 文档。
        table_path: 表路径，如 ``("agent_runner", "lifecycle_agents")``。

    Returns:
        叶子 :class:`tomlkit.items.Table`。
    """
    current: Any = document
    for index, key in enumerate(table_path):
        existing = current.get(key)
        if existing is None:
            is_leaf = index == len(table_path) - 1
            # 非叶子用 super table，保证后续子段能正确渲染成 `[a.b]` 而非内联表。
            new_table = tomlkit.table(is_super_table=not is_leaf)
            current[key] = new_table
            existing = new_table
        current = existing
    return current


def _prune_empty_table(document: tomlkit.TOMLDocument, table_path: Sequence[str]) -> None:
    """若 ``table_path`` 叶子表已空则删除它，避免留下空段头。"""
    if not table_path:
        return
    parent: Any = document
    for key in table_path[:-1]:
        parent = parent.get(key)
        if parent is None:
            return
    leaf_key = table_path[-1]
    leaf_table = parent.get(leaf_key)
    if isinstance(leaf_table, Table) and len(leaf_table) == 0:
        del parent[leaf_key]


def update_toml_table_keys(
    config_path: str | Path,
    table_path: Sequence[str],
    values: Mapping[str, Any],
) -> None:
    """保留式更新 ``table_path`` 下点名的键。

    值为 ``None`` 时删除该键（用于"跟随上一层 / 删除本键"语义）；其余值直接写入。
    只修改签名为 ``values`` 的键，文件其余内容与格式保持不变。文件不存在时新建，
    并按需创建中间表。

    Args:
        config_path: 目标 TOML 文件路径（``config.toml`` 或 ``.iar.toml``）。
        table_path: 表路径，如 ``("agent_runner", "lifecycle_agents")``。
        values: 键 -> 新值；``None`` 表示删除该键。

    Raises:
        ValueError: ``table_path`` 为空。
    """
    if not table_path:
        raise ValueError("table_path must not be empty.")
    resolved_path = Path(config_path).expanduser()
    if resolved_path.is_file():
        document = tomlkit.parse(resolved_path.read_text(encoding="utf-8"))
    else:
        document = tomlkit.document()

    table = _ensure_table(document, table_path)
    for key, value in values.items():
        if value is None:
            if key in table:
                del table[key]
        else:
            table[key] = value
    _prune_empty_table(document, table_path)

    temp_path = resolved_path.with_name(resolved_path.name + ".tmp")
    temp_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    os.replace(temp_path, resolved_path)


__all__ = ["update_toml_table_keys"]

"""保留式 TOML 表键写回。

用 ``tomlkit`` 做 round-trip 编辑：保留文件中的全部注释、空行与格式，只触碰
调用方点名的键。写入采用「同目录临时文件 + ``os.replace``」原子替换，避免写坏
配置文件。

``config.toml``（机器级）与 ``.kedacode.toml``（仓库级）共用本模块——两处写回语义
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


def _load_roundtrip_document(
    config_path: str | Path,
    table_path: Sequence[str],
) -> tuple[Path, tomlkit.TOMLDocument, Table]:
    """读回（或新建）round-trip 文档并取到 ``table_path`` 叶子表。

    Args:
        config_path: 目标 TOML 文件路径，不存在时以空文档起步。
        table_path: 表路径，中间表按需创建。

    Returns:
        ``(解析后的文件路径, 文档, 叶子表)``。

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
    return resolved_path, document, _ensure_table(document, table_path)


def _atomic_dump(resolved_path: Path, document: tomlkit.TOMLDocument) -> None:
    """同目录临时文件 + ``os.replace`` 原子写回文档。"""
    temp_path = resolved_path.with_name(resolved_path.name + ".tmp")
    temp_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    os.replace(temp_path, resolved_path)


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
    并按需创建中间表。**全部取值都是删除且目标键本就不存在时不落盘**（无改动、
    不产生 diff、也不凭空创建文件）。

    Args:
        config_path: 目标 TOML 文件路径（``config.toml`` 或 ``.kedacode.toml``）。
        table_path: 表路径，如 ``("agent_runner", "lifecycle_agents")``。
        values: 键 -> 新值；``None`` 表示删除该键。

    Raises:
        ValueError: ``table_path`` 为空。
    """
    resolved_path, document, table = _load_roundtrip_document(config_path, table_path)
    changed = False
    for key, value in values.items():
        if value is None:
            if key in table:
                del table[key]
                changed = True
        else:
            table[key] = value
            changed = True
    if not changed:
        # 纯删除且目标键本就不存在：没有任何改动，不落盘。否则 tomlkit 的
        # round-trip 会重排/补尾随空行，让"只动点名的键"在无操作时也产生 diff，
        # 甚至在配置文件不存在时凭空建出一个空文件。
        return
    _prune_empty_table(document, table_path)

    _atomic_dump(resolved_path, document)


def update_toml_array_of_tables(
    config_path: str | Path,
    table_path: Sequence[str],
    key: str,
    entries: Sequence[Mapping[str, Any]],
) -> None:
    """整体替换 ``table_path`` 下的数组表 ``key``（如 ``agent_fallback_candidates``）。

    ``update_toml_table_keys`` 只能写标量 / 内联值，无法产出规范的
    ``[[a.b.key]]`` 数组表形态；本函数专为此形态：把 ``entries`` 渲染为逐条
    ``[[...]]`` 子表并**整体替换**旧值（顺序即 ``entries`` 顺序）。每条 ``entry``
    里值为 ``None`` 的字段直接省略（回读时由 Pydantic 默认值补回，与"该候选不设
    此字段"语义一致）。``entries`` 为空时删除该键；键本就不存在且无内容可写时
    **不落盘**（不产生 diff、不凭空建文件）。文件其余内容与格式保持不变。

    Args:
        config_path: 目标 TOML 文件路径（``config.toml`` 或 ``.kedacode.toml``）。
        table_path: 数组表所属父表路径，如 ``("agent_runner", "runner")``。
        key: 数组表键名，如 ``"agent_fallback_candidates"``。
        entries: 有序条目列表，每项是 ``字段名 -> 值``（值为 ``None`` 表示省略）。

    Raises:
        ValueError: ``table_path`` 为空。
    """
    resolved_path, document, table = _load_roundtrip_document(config_path, table_path)
    if not entries:
        # 空列表 = 清空 / 保持不存在；本就无此键则无任何改动，不落盘。
        if key not in table:
            return
        del table[key]
    else:
        array_table = tomlkit.aot()
        for entry in entries:
            element = tomlkit.table()
            for field_name, field_value in entry.items():
                if field_value is not None:
                    element[field_name] = field_value
            array_table.append(element)
        # 整体替换旧数组表（保留式写回只对点名的 key 生效）。
        if key in table:
            del table[key]
        table[key] = array_table

    _atomic_dump(resolved_path, document)


__all__ = ["update_toml_array_of_tables", "update_toml_table_keys"]

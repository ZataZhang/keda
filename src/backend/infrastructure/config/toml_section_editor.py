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
from tomlkit.items import AoT, Table


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
    """原子写回文档；渲染文本与磁盘现状一致时**不落盘**。

    ``os.replace`` 只保证单次写入的原子性，不保证"共写但没变"的键不产生 diff：
    同值写回在这里被识别为 no-op，文件（含被替换段的位置与行内注释）保持原样。
    """
    rendered_document = tomlkit.dumps(document)
    if resolved_path.is_file() and resolved_path.read_text(encoding="utf-8") == rendered_document:
        return
    temp_path = resolved_path.with_name(resolved_path.name + ".tmp")
    temp_path.write_text(rendered_document, encoding="utf-8")
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
    不产生 diff、也不凭空创建文件）；取值与磁盘现状相同同样不落盘（no-op 写回
    不产生文件抖动）。

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


def _render_entries_as_array_table(entries: Sequence[Mapping[str, Any]]) -> AoT:
    """把 ``entries`` 渲染成全新的数组表（每条一个 ``[[...]]`` 子表）。"""
    array_table = tomlkit.aot()
    for entry in entries:
        array_table.append(_entry_to_table(entry))
    return array_table


def _entry_to_table(entry: Mapping[str, Any]) -> Table:
    """把一条 entry 转成子表；值为 ``None`` 的字段省略。"""
    element = tomlkit.table()
    for field_name, field_value in entry.items():
        if field_value is not None:
            element[field_name] = field_value
    return element


def _apply_entries_in_place(existing: AoT, entries: Sequence[Mapping[str, Any]]) -> None:
    """把 ``entries`` 逐条原地写回已存在的数组表，长度差用追加 / 弹出收尾。

    原地改写保住数组表在文件里的**位置**与段前 / 段内**注释**；整体删除再追加会
    把 ``[[...]]`` 段挪到父表末尾并抹掉手写注释。

    Args:
        existing: 文件里已存在的数组表（会被就地修改）。
        entries: 期望内容，顺序即写后的候选顺序。
    """
    for index, entry in enumerate(entries):
        if index < len(existing):
            element = existing[index]
        else:
            element = _entry_to_table(entry)
            existing.append(element)
        for field_name, field_value in entry.items():
            if field_value is None:
                if field_name in element:
                    del element[field_name]
            else:
                element[field_name] = field_value
        for stale_field_name in [name for name in element if name not in entry]:
            del element[stale_field_name]
    while len(existing) > len(entries):
        existing.pop()


def update_toml_array_of_tables(
    config_path: str | Path,
    table_path: Sequence[str],
    key: str,
    entries: Sequence[Mapping[str, Any]],
) -> None:
    """整体替换 ``table_path`` 下的数组表 ``key``（如 ``agent_fallback_candidates``）。

    ``update_toml_table_keys`` 只能写标量 / 内联值，无法产出规范的
    ``[[a.b.key]]`` 数组表形态；本函数专为此形态：写后内容**整体等于** ``entries``
    （顺序即 ``entries`` 顺序）。每条 ``entry`` 里值为 ``None`` 的字段直接省略
    （回读时由 Pydantic 默认值补回，与"该候选不设此字段"语义一致）。``entries``
    为空时删除该键；键本就不存在且无内容可写时**不落盘**（不产生 diff、不凭空建
    文件）。已存在的数组表按条**原地改写**（保住位置与手写注释）；渲染结果与磁盘
    现状相同（如重复提交同一条候选链）时同样不落盘。文件其余内容与格式保持不变。

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
        existing = table.get(key)
        if isinstance(existing, AoT):
            _apply_entries_in_place(existing, entries)
        else:
            table[key] = _render_entries_as_array_table(entries)

    _atomic_dump(resolved_path, document)


__all__ = ["update_toml_array_of_tables", "update_toml_table_keys"]

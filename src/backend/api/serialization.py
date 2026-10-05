"""API 层共用的 DTO 序列化。

FastAPI 路由与 CLI 的 ``--json`` 输出必须给出**同一份**结构：字段名或 Enum 展开方式
一旦分叉，agent 侧解析与 Console 显示就会各自解释同一个 DTO。因此这里提供唯一实现，
两层都导入它，不再各自复制一份 ``_serialize``。

core 只暴露 frozen dataclass / Enum（不依赖 FastAPI），本模块负责把它们转成 JSON
友好结构：dataclass 递归成 dict、Enum 取 ``value``、容器递归、其余原样返回。
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

__all__ = ["serialize_value"]


def serialize_value(value: Any) -> Any:
    """递归地把 dataclass / Enum 转成 JSON 友好结构。

    Args:
        value: 任意 core DTO（dataclass、Enum、容器或标量）。

    Returns:
        ``json.dumps`` 可直接消费的结构：dataclass 转 dict，Enum 转其 ``value``，
        tuple / list / dict 逐项递归，其余原样返回。
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {key: serialize_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_value(item) for key, item in value.items()}
    return value

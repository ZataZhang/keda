"""核心层通用统计计算。"""

from __future__ import annotations

import math
from collections.abc import Sequence


def linear_percentile(sorted_values: Sequence[float], fraction: float) -> float | None:
    """按线性插值计算分位数；输入序列须已排序，空序列返回 ``None``。

    Args:
        sorted_values: 按升序排列的数值。
        fraction: 介于 0 与 1 之间的分位点。

    Returns:
        对应分位数；序列为空时返回 ``None``。
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[int(position)]
    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    return lower_value + (upper_value - lower_value) * (position - lower_index)

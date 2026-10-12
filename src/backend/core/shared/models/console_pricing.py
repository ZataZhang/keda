"""管理终端模型价格与调用用量的核心模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelTokenPricing:
    """单个模型按百万 token 计价的价格配置（USD）。

    Attributes:
        model_id: 调用账本中的模型标识符。
        input_usd_per_million: 未缓存输入 token 的每百万单价。
        output_usd_per_million: 输出 token 的每百万单价。
        cache_read_usd_per_million: 缓存读取 token 的每百万单价。
        cache_creation_usd_per_million: 缓存写入 token 的每百万单价。
    """

    model_id: str
    input_usd_per_million: float
    output_usd_per_million: float
    cache_read_usd_per_million: float
    cache_creation_usd_per_million: float


@dataclass(frozen=True)
class ModelPricingSettings:
    """管理终端当前保存的模型价格列表。

    Attributes:
        models: 已保存的模型价格配置。
        updated_at: 最近保存时间；未保存时为空字符串。
    """

    models: tuple[ModelTokenPricing, ...]
    updated_at: str

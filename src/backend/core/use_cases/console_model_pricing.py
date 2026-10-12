"""管理终端模型 token 价格的读取、校验与保存用例。"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Sequence

from backend.core.shared.interfaces.console_model_pricing import IConsoleModelPricingStore
from backend.core.shared.models.console_pricing import ModelPricingSettings, ModelTokenPricing

_MAX_MODEL_PRICES = 100
_PRICE_FIELDS = (
    "input_usd_per_million",
    "output_usd_per_million",
    "cache_read_usd_per_million",
    "cache_creation_usd_per_million",
)


def get_model_pricing_settings(store: IConsoleModelPricingStore) -> ModelPricingSettings:
    """读取模型价格配置；本机尚无保存值时返回空配置。

    Args:
        store: 模型价格设置存储端口实现。

    Returns:
        当前保存的模型价格列表与更新时间。

    Raises:
        ValueError: 持久化内容损坏或违反价格配置约束。
    """
    stored_payload = store.get_model_pricing_payload()
    if stored_payload is None:
        return ModelPricingSettings(models=(), updated_at="")
    raw_rows, updated_at = stored_payload
    model_prices = _model_prices_from_payload(raw_rows)
    return ModelPricingSettings(models=model_prices, updated_at=updated_at)


def update_model_pricing_settings(
    store: IConsoleModelPricingStore,
    *,
    models: Sequence[ModelTokenPricing],
) -> ModelPricingSettings:
    """校验并完整替换模型价格配置。

    Args:
        store: 模型价格设置存储端口实现。
        models: 按 USD / 百万 token 计价的模型价格。

    Returns:
        写入后的标准化配置与更新时间。

    Raises:
        ValueError: 模型名重复、为空，价格无效或配置数量超限。
    """
    normalized_models = _normalize_model_prices(models)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload_rows = [
        {
            "model_id": model_price.model_id,
            **{price_field: getattr(model_price, price_field) for price_field in _PRICE_FIELDS},
        }
        for model_price in normalized_models
    ]
    store.save_model_pricing_payload(payload_rows, updated_at=updated_at)
    return ModelPricingSettings(models=normalized_models, updated_at=updated_at)


def _model_prices_from_payload(
    raw_rows: list[dict[str, object]],
) -> tuple[ModelTokenPricing, ...]:
    """把存储层的结构化行解码为校验后的领域模型。"""
    model_prices: list[ModelTokenPricing] = []
    for raw_row in raw_rows:
        model_id = raw_row.get("model_id")
        if not isinstance(model_id, str):
            raise ValueError("已保存的模型价格缺少有效 model_id。")
        raw_rates = [raw_row.get(price_field) for price_field in _PRICE_FIELDS]
        if any(
            isinstance(price_rate, bool) or not isinstance(price_rate, int | float)
            for price_rate in raw_rates
        ):
            raise ValueError(f"模型「{model_id}」的价格数据格式无效。")
        model_prices.append(
            ModelTokenPricing(
                model_id=model_id,
                input_usd_per_million=float(raw_rates[0]),
                output_usd_per_million=float(raw_rates[1]),
                cache_read_usd_per_million=float(raw_rates[2]),
                cache_creation_usd_per_million=float(raw_rates[3]),
            )
        )
    return _normalize_model_prices(model_prices)


def _normalize_model_prices(
    models: Sequence[ModelTokenPricing],
) -> tuple[ModelTokenPricing, ...]:
    """标准化并验证整份模型价格列表。"""
    if len(models) > _MAX_MODEL_PRICES:
        raise ValueError(f"模型价格配置最多支持 {_MAX_MODEL_PRICES} 个模型。")
    normalized_models: list[ModelTokenPricing] = []
    seen_model_ids: set[str] = set()
    for model_price in models:
        model_id = model_price.model_id.strip()
        if not model_id:
            raise ValueError("模型名称不能为空。")
        if len(model_id) > 200:
            raise ValueError("模型名称不能超过 200 个字符。")
        if model_id in seen_model_ids:
            raise ValueError(f"模型「{model_id}」重复配置。")
        seen_model_ids.add(model_id)
        normalized_rates: dict[str, float] = {}
        for price_field in _PRICE_FIELDS:
            raw_rate = getattr(model_price, price_field)
            if isinstance(raw_rate, bool) or not isinstance(raw_rate, int | float):
                raise ValueError(f"模型「{model_id}」的价格必须是非负数字。")
            price_rate = float(raw_rate)
            if not math.isfinite(price_rate) or price_rate < 0:
                raise ValueError(f"模型「{model_id}」的价格必须是有限的非负数字。")
            normalized_rates[price_field] = price_rate
        normalized_models.append(ModelTokenPricing(model_id=model_id, **normalized_rates))
    return tuple(normalized_models)


__all__ = ["get_model_pricing_settings", "update_model_pricing_settings"]

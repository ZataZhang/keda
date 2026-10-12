"""管理终端模型价格设置的持久化端口。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IConsoleModelPricingStore(ABC):
    """模型价格配置的持久化接口。"""

    @abstractmethod
    def get_model_pricing_payload(self) -> tuple[list[dict[str, object]], str] | None:
        """读取价格配置原始行和更新时间。

        Returns:
            价格配置行与更新时间；无已保存配置时返回 ``None``。
        """
        ...

    @abstractmethod
    def save_model_pricing_payload(
        self,
        pricing_rows: list[dict[str, object]],
        *,
        updated_at: str,
    ) -> None:
        """保存完整价格配置列表；存储故障向调用方抛出。

        Args:
            pricing_rows: 已校验的模型价格字段清单。
            updated_at: 设置更新时间。
        """
        ...

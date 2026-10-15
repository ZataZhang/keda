"""GitHub API 额度预算端口。

所有 ``gh`` 调用共用同一个账号额度（GraphQL 与 REST 各 5000/小时），而 daemon、
Monitor、CLI 是各自独立的进程。本端口让它们共享两件事：

- **用量账本**：谁在什么类型的调用上花了多少，用来回答“额度被谁用掉了”；
- **暂停闸门**：任何一方撞到限流或探测到额度见底，所有进程在重置时间之前都
  不再发请求，而不是各自重试、各自把错误写满日志。

实现见 ``backend.engines.agent_runner.persistence.github_api_budget_json``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

API_KIND_GRAPHQL = "graphql"
API_KIND_REST = "rest"


class IGitHubApiBudget(ABC):
    """跨进程共享的 GitHub API 额度预算。"""

    @abstractmethod
    def ensure_available(self, api_kind: str) -> None:
        """额度已耗尽（硬暂停）时抛出 ``GitHubApiRateLimitError``，不发出真实请求。

        额度只是偏低（软暂停）时不抛：那最后一小段预留要留给进行中的收尾。

        Args:
            api_kind: ``API_KIND_GRAPHQL`` 或 ``API_KIND_REST``。

        Raises:
            GitHubApiRateLimitError: 该类型的额度仍在暂停期内。
        """

    @abstractmethod
    def record_call(self, api_kind: str, operation: str) -> None:
        """把一次真实请求记入用量账本。

        Args:
            api_kind: ``API_KIND_GRAPHQL`` 或 ``API_KIND_REST``。
            operation: 去掉编号与仓库名的操作标签，如 ``issue create``、
                ``api GET issues/N/comments``。
        """

    @abstractmethod
    def record_rate_limited(self, api_kind: str, resume_at: datetime) -> None:
        """记录该类型额度已耗尽，在 ``resume_at`` 之前所有进程停止请求。"""

    @abstractmethod
    def probe_due(self) -> bool:
        """距离上一次额度探测是否已超过探测间隔。"""

    @abstractmethod
    def record_probe(
        self,
        api_kind: str,
        *,
        remaining: int,
        limit: int,
        reset_at: datetime,
    ) -> None:
        """记录一次额度探测结果：耗尽 → 硬暂停；偏低 → 只暂停轮询；充足 → 撤销旧标记。"""

    @abstractmethod
    def pause_remaining_seconds(self) -> float:
        """“轮询”还应暂停多久（含额度偏低的软暂停）；没有暂停时返回 ``0.0``。"""

    @abstractmethod
    def usage_summary(self) -> dict[str, int]:
        """最近一小时按 ``<kind>:<operation>`` 聚合的调用次数。"""

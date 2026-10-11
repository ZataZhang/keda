"""发布档位（``kc run`` 的 per-run 门禁级别）。

执行 agent 结束后还剩多少门禁与第二个 agent，由本档位决定。旁路级别由一个枚举
承载，避免多个布尔值组成矛盾状态。
"""

from __future__ import annotations

from enum import Enum


class PublishStage(str, Enum):
    """一次运行的发布档位。"""

    NORMAL = "normal"
    FAST = "fast"
    DIRECT = "direct"
    PRD_AUTO_MERGE = "prd_auto_merge"

    @property
    def skips_independent_verification(self) -> bool:
        """是否跳过独立 verifier。"""
        return self is not PublishStage.NORMAL

    @property
    def skips_rv_reexecution(self) -> bool:
        """是否跳过 Realistic Validation 重跑；PRD 自动合并仍会运行它。"""
        return self in {PublishStage.FAST, PublishStage.DIRECT}

    @property
    def skips_review_and_repo_verification(self) -> bool:
        """是否再跳过 pre-PR review（第二个 agent）与 runner 侧的验证命令（``--direct-pr``）。"""
        return self is PublishStage.DIRECT

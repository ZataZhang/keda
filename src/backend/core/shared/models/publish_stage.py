"""发布档位（``iar run`` 的 per-run 门禁旁路级别）。

执行 agent 结束后还剩多少门禁与第二个 agent，由本档位决定。三个档位是**嵌套**
关系（``direct`` 的跳过范围严格包含 ``fast``），因此用一个字段而不是两个布尔承载：
「既是 fast 又是 direct」这类矛盾状态在类型上不可表示。
"""

from __future__ import annotations

from enum import Enum


class PublishStage(str, Enum):
    """一次运行的发布档位。"""

    NORMAL = "normal"
    FAST = "fast"
    DIRECT = "direct"

    @property
    def skips_independent_verification(self) -> bool:
        """是否跳过 Phase 4.5 的 rv 复跑与独立 verifier（``--fast-merge`` 的旁路范围）。"""
        return self is not PublishStage.NORMAL

    @property
    def skips_review_and_repo_verification(self) -> bool:
        """是否再跳过 pre-PR review（第二个 agent）与 runner 侧的验证命令（``--direct-pr``）。"""
        return self is PublishStage.DIRECT

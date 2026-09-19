"""生命周期 Agent 矩阵的共享领域模型与常量。

本模块是"阶段 -> agent"键名闭集的**唯一代码内定义**：infrastructure 的
pydantic 设置模型、core 的冻结配置模型、解析函数与 console API 都从这里取，
避免九键清单在多处各写一遍而漂移。

矩阵把流水线的九个生命周期阶段映射到"该阶段用哪个 agent"，取值域是：

- ``auto``：按阶段各自的既有语义路由（实现=标签路由 / 校验=回退链上第一个
  ≠ 实现者 / 审核=不同人优先 / 监督=沿用本次实现者 / 辩论=按标签路由）；
- ``executor``：跟随实现阶段选中的 agent（**仅** ``fix`` / ``closeout`` 合法）；
- 已注册的 agent 名。

``repl``（交互式会话）不算流水线生命周期，不在闭集内。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

LIFECYCLE_AGENT_AUTO = "auto"
"""矩阵取值：按该阶段既有语义自动路由。"""

LIFECYCLE_AGENT_EXECUTOR = "executor"
"""矩阵取值：跟随实现阶段选中的 agent（仅 fix / closeout 合法）。"""

#: 九个生命周期键；顺序即 UI 展示顺序，也是解析与文档的遍历顺序。
LIFECYCLE_AGENT_KEYS: tuple[str, ...] = (
    "implementation",
    "fix",
    "closeout",
    "verifier",
    "review",
    "supervisor",
    "planner",
    "content_generation",
    "deliberate",
)

#: 允许 ``executor`` 取值的生命周期（其余键写 ``executor`` 报配置错误）。
LIFECYCLE_AGENT_EXECUTOR_KEYS: frozenset[str] = frozenset({"fix", "closeout"})

#: 允许 ``auto`` 取值的生命周期（其余键写 ``auto`` 报配置错误）。
LIFECYCLE_AGENT_AUTO_KEYS: frozenset[str] = frozenset(
    {"implementation", "verifier", "review", "supervisor", "deliberate"}
)

#: PRD 文件头部覆盖**真正会被消费**的生命周期键。
#: ``planner`` 的唯一消费点是 ``iar ask``（交互式决策），既没有 Issue 也没有 PRD
#: 上下文，PRD 级覆盖对它没有消费点；因此 PRD 覆盖抽屉不提供该行，写回也拒绝该键，
#: 避免写入一个静默无效的声明。（``planner`` 的矩阵值本身仍然有效。）
LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS: tuple[str, ...] = tuple(
    key for key in LIFECYCLE_AGENT_KEYS if key != "planner"
)

#: 各阶段 ``auto`` 的**真实**语义文案（界面上如实描述，不统一成标签路由）。
LIFECYCLE_AGENT_AUTO_DESCRIPTIONS: Mapping[str, str] = {
    "implementation": "按 Issue 上的 agent/* 标签路由",
    "verifier": "从回退顺序里挑第一个 ≠ 实现者的 agent",
    "review": "优先挑 ≠ 实现者；allow_same_agent 为真时沿用实现者",
    "supervisor": "发布路径沿用本次实现者",
    "deliberate": "按 Issue 上的 agent/deliberate 标签路由",
}

#: 未声明且既有配置键也为 ``auto`` 时的内置兜底 agent。
#: 与 ``choose_agent`` 的历史回落（``"claude"``）一致，保证零新配置行为不变。
LIFECYCLE_AGENT_BUILTIN_DEFAULT = "claude"

#: 来源层标注取值（供 console API 呈递）。
LIFECYCLE_SOURCE_PRD_OVERRIDE = "prd_override"
LIFECYCLE_SOURCE_REPOSITORY = "repository"
LIFECYCLE_SOURCE_GLOBAL = "global"
LIFECYCLE_SOURCE_LEGACY = "legacy"
LIFECYCLE_SOURCE_BUILTIN = "builtin"


def normalize_lifecycle_agent_value(value: str) -> str:
    """规范化矩阵/覆盖里的原始取值（去空白、小写）。

    Args:
        value: 原始字符串。

    Returns:
        规范化后的取值；空串归一为 ``auto`` 由调用方进一步校验。
    """
    return (value or "").strip().lower()


def concrete_declared_agent(
    lifecycle_config: "LifecycleAgentsConfig",
    lifecycle: str,
) -> str | None:
    """返回该生命周期显式声明的**具体 agent 名**。

    ``auto`` / ``executor`` 这类非具体取值返回 ``None``——它们要留给解析函数按
    阶段语义展开，不能被当成 agent 名装配进其它配置。

    Args:
        lifecycle_config: 合并后的矩阵声明视图。
        lifecycle: 生命周期键。

    Returns:
        具体 agent 名；未声明或声明为 ``auto`` / ``executor`` 时返回 ``None``。
    """
    declared_value = lifecycle_config.declared_value(lifecycle)
    if declared_value is None:
        return None
    if normalize_lifecycle_agent_value(declared_value) in (
        LIFECYCLE_AGENT_AUTO,
        LIFECYCLE_AGENT_EXECUTOR,
    ):
        return None
    return declared_value


@dataclass(frozen=True)
class LifecycleAgentsConfig:
    """``[agent_runner.lifecycle_agents]`` 两层显式声明的冻结视图。

    只记录**显式声明**过的键及其来源层，未声明的键交由解析函数按
    "既有配置键 -> 内置默认"继续回落。``repository_layer`` 来自仓库级
    ``.iar.toml``，``global_layer`` 来自机器级 ``config.toml``；同键时
    仓库层赢过全局层。

    Attributes:
        global_layer: 全局层显式声明的键值。
        repository_layer: 仓库层显式声明的键值。
    """

    global_layer: Mapping[str, str] = field(default_factory=dict)
    repository_layer: Mapping[str, str] = field(default_factory=dict)

    def declared_value(self, lifecycle: str) -> str | None:
        """返回该生命周期在当前合并视图里显式声明的取值。

        Args:
            lifecycle: 生命周期键。

        Returns:
            仓库层优先的声明值；两层都未声明时返回 ``None``。
        """
        if lifecycle in self.repository_layer:
            return self.repository_layer[lifecycle]
        if lifecycle in self.global_layer:
            return self.global_layer[lifecycle]
        return None

    def declared_layer(self, lifecycle: str) -> str | None:
        """返回该生命周期声明所在的层（``repository`` / ``global`` / ``None``）。"""
        if lifecycle in self.repository_layer:
            return LIFECYCLE_SOURCE_REPOSITORY
        if lifecycle in self.global_layer:
            return LIFECYCLE_SOURCE_GLOBAL
        return None


__all__ = [
    "LIFECYCLE_AGENT_AUTO",
    "LIFECYCLE_AGENT_AUTO_DESCRIPTIONS",
    "LIFECYCLE_AGENT_AUTO_KEYS",
    "LIFECYCLE_AGENT_BUILTIN_DEFAULT",
    "LIFECYCLE_AGENT_EXECUTOR",
    "LIFECYCLE_AGENT_EXECUTOR_KEYS",
    "LIFECYCLE_AGENT_KEYS",
    "LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS",
    "LIFECYCLE_SOURCE_BUILTIN",
    "LIFECYCLE_SOURCE_GLOBAL",
    "LIFECYCLE_SOURCE_LEGACY",
    "LIFECYCLE_SOURCE_PRD_OVERRIDE",
    "LIFECYCLE_SOURCE_REPOSITORY",
    "LifecycleAgentsConfig",
    "concrete_declared_agent",
    "normalize_lifecycle_agent_value",
]

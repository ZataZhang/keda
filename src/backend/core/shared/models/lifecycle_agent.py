"""生命周期 Agent 矩阵的共享领域模型与常量。

本模块是"阶段 -> agent"键名闭集的**唯一代码内定义**：infrastructure 的
pydantic 设置模型、core 的冻结配置模型、解析函数与 console API 都从这里取，
避免九键清单在多处各写一遍而漂移。

本模块同时是九个键「触发入口」分组的唯一事实源
（``LIFECYCLE_AGENT_ENTRY_GROUPS``）：九个阶段不在同一条流水线上，分组
常量声明"哪些阶段共享同一次 claim / 消费点"，console 只读视图逐行下发给
界面，前端不持有第二份键 -> 组映射。导入期校验九键恰好各属一组、不重不漏。

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


@dataclass(frozen=True)
class LifecycleAgentEntryGroup:
    """一个「触发入口」分组：组 id、中文组名、一行组说明与组内键（按展示顺序）。

    Attributes:
        entry: 组 id（``pipeline`` / ``discussion_content`` / ``standalone``）。
        label: 组中文名（界面组标题）。
        summary: 一行组说明（组标题旁的触发入口描述）。
        keys: 组内生命周期键，按 ``LIFECYCLE_AGENT_KEYS`` 中的相对顺序排列。
    """

    entry: str
    label: str
    summary: str
    keys: tuple[str, ...]


#: 九个生命周期键的「触发入口」分组（唯一事实源）。组顺序即界面展示顺序，
#: 组内顺序沿用 ``LIFECYCLE_AGENT_KEYS`` 的相对顺序——分组只声明"哪些阶段
#: 共享同一次触发入口"，不改动 ``lifecycles`` 数组的键序契约。
#:
#: - ``pipeline``：``iar run`` / ``daemon`` 认领后同一次 claim 内依次发生的六个阶段；
#: - ``discussion_content``：Phase 0 讨论（``deliberate``）与横切的内容生成
#:   （``content_generation``，``iar issue create`` / Phase 1 / 开 Draft PR 三个 target）；
#: - ``standalone``：``planner``，唯一消费点是 ``iar ask``，不在任何 Issue 流水线上。
#:
#: 语义出处与消费点对照见 ``docs/guides/lifecycle-agent-matrix.md`` 的
#: 「各阶段在哪触发」一节；新增阶段或拆分 claim 时必须同轮更新本常量。
LIFECYCLE_AGENT_ENTRY_GROUPS: tuple[LifecycleAgentEntryGroup, ...] = (
    LifecycleAgentEntryGroup(
        entry="pipeline",
        label="实现流水线",
        summary="iar run / daemon 认领后，在同一 worktree 的同一次 claim 内依次触发。",
        keys=("implementation", "fix", "closeout", "verifier", "review", "supervisor"),
    ),
    LifecycleAgentEntryGroup(
        entry="discussion_content",
        label="讨论与内容生成",
        summary=(
            "辩论在 Phase 0 就该 Issue 展开（此时 PRD 尚不存在）；"
            "内容生成横切 iar issue create、Phase 1 与开 Draft PR 三处。"
        ),
        keys=("content_generation", "deliberate"),
    ),
    LifecycleAgentEntryGroup(
        entry="standalone",
        label="独立入口",
        summary="iar ask，不在任何 Issue 流水线上（无 Issue / PRD 上下文）。",
        keys=("planner",),
    ),
)

#: 每个生命周期阶段「何时被读」的一句话触发时机（``LIFECYCLE_AGENT_AUTO_DESCRIPTIONS``
#: 描述 ``auto`` 取值语义，本表描述阶段本身的消费点）。
LIFECYCLE_AGENT_TRIGGERS: Mapping[str, str] = {
    "implementation": "iar run / daemon 认领 agent/ready Issue 时启动实现。",
    "fix": "同一次 claim 内，验证未通过时修复。",
    "closeout": "同一次 claim 内，交付收尾阶段执行。",
    "verifier": "同一次 claim 内，实现产出后做验证。",
    "review": "同一次 claim 内，开 Draft PR 前审查。",
    "supervisor": "同一次 claim 的发布路径，或 iar review / review-daemon 单独一轮。",
    "planner": "iar ask 交互式决策，不在任何 Issue 流水线上。",
    "content_generation": "横切 iar issue create、Phase 1 Issue→PRD 与开 Draft PR 三处。",
    "deliberate": "Phase 0 就该 Issue 在评论区讨论（此时 PRD 尚不存在）。",
}

#: 生命周期键 -> 所属组（由 ``LIFECYCLE_AGENT_ENTRY_GROUPS`` 派生，禁止手写第二份）。
LIFECYCLE_AGENT_ENTRY_BY_KEY: Mapping[str, LifecycleAgentEntryGroup] = {
    lifecycle_key: entry_group
    for entry_group in LIFECYCLE_AGENT_ENTRY_GROUPS
    for lifecycle_key in entry_group.keys
}


def _validate_entry_groups() -> None:
    """导入期校验分组完整性：九键恰好各属一组，不重不漏、无未知键。

    与闭集"写错键在配置加载期直接报错"的风格一致：分组写错在导入本模块时
    立即抛错，不静默放行。

    Raises:
        ValueError: 存在未知键、重复键或未分组的键。
    """
    grouped_keys = [
        lifecycle_key
        for entry_group in LIFECYCLE_AGENT_ENTRY_GROUPS
        for lifecycle_key in entry_group.keys
    ]
    unknown_keys = sorted(set(grouped_keys) - set(LIFECYCLE_AGENT_KEYS))
    if unknown_keys:
        raise ValueError(
            "LIFECYCLE_AGENT_ENTRY_GROUPS 出现未知生命周期键: "
            f"{', '.join(unknown_keys)}。合法键: {', '.join(LIFECYCLE_AGENT_KEYS)}。"
        )
    duplicate_keys = sorted(
        {lifecycle_key for lifecycle_key in grouped_keys if grouped_keys.count(lifecycle_key) > 1}
    )
    if duplicate_keys:
        raise ValueError(
            f"LIFECYCLE_AGENT_ENTRY_GROUPS 重复分组了生命周期键: {', '.join(duplicate_keys)}。"
        )
    missing_keys = [key for key in LIFECYCLE_AGENT_KEYS if key not in LIFECYCLE_AGENT_ENTRY_BY_KEY]
    if missing_keys:
        raise ValueError(
            f"LIFECYCLE_AGENT_ENTRY_GROUPS 未给以下生命周期键分组: {', '.join(missing_keys)}。"
        )


_validate_entry_groups()

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
    "LIFECYCLE_AGENT_ENTRY_BY_KEY",
    "LIFECYCLE_AGENT_ENTRY_GROUPS",
    "LIFECYCLE_AGENT_EXECUTOR",
    "LIFECYCLE_AGENT_EXECUTOR_KEYS",
    "LIFECYCLE_AGENT_KEYS",
    "LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS",
    "LIFECYCLE_AGENT_TRIGGERS",
    "LIFECYCLE_SOURCE_BUILTIN",
    "LIFECYCLE_SOURCE_GLOBAL",
    "LIFECYCLE_SOURCE_LEGACY",
    "LIFECYCLE_SOURCE_PRD_OVERRIDE",
    "LIFECYCLE_SOURCE_REPOSITORY",
    "LifecycleAgentEntryGroup",
    "LifecycleAgentsConfig",
    "concrete_declared_agent",
    "normalize_lifecycle_agent_value",
]

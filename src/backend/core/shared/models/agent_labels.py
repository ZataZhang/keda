"""标准 GitHub 标签集合的唯一定义源。

`kc labels sync`（:mod:`backend.infrastructure.github_labels`）与 console 的
Issue 标签写入校验都从本模块取集合，避免出现第二份硬编码清单——一旦两处各写
一份，「网页允许的标签」就会与实际同步到仓库的标签漂移，越界写入会静默破坏
监控口径（一个拼错的标签名就能让 Issue 脱离队列视图）。

集合分两部分：

- :data:`STANDARD_LABEL_SPECS` —— 与 agent 注册表无关的静态标准标签。
- ``agent/<name>`` 路由标签 —— 由 ``labels.agent_labels`` 的键派生，颜色与描述
  取自 agent 注册表（缺失时回落内置 spec，再兜底通用值）。

配置可以把标准名**覆盖**成别的名字（``labels.ready = "agent/q"``），因此每个标签
都同时有「标准名」与「生效名」两个身份：同步写的是生效名，网页校验比对的也是
生效名。
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.shared.models.agent_spec import BUILTIN_AGENT_SPECS, AgentSpec
from backend.core.shared.models.agent_runner import LabelConfig

#: 静态标准标签：``(标准名, 颜色, 描述)``。顺序即 ``labels sync`` 的创建顺序。
STANDARD_LABEL_SPECS: tuple[tuple[str, str, str], ...] = (
    ("agent/ready", "0E8A16", "Issue is ready for a local AI runner to claim."),
    (
        "agent/running",
        "FBCA04",
        "Issue is currently being executed by a local AI runner.",
    ),
    (
        "agent/supervising",
        "C5DEF5",
        "PR exists and automatic post-PR supervisor is reviewing or reprocessing.",
    ),
    ("agent/review", "1D76DB", "AI runner opened work for human review."),
    ("agent/failed", "D73A4A", "AI runner failed and posted details."),
    ("agent/blocked", "000000", "AI runner needs human input."),
    (
        "agent/waiting",
        "FEF2C0",
        "Issue has unmet dependencies and is waiting for upstream closure.",
    ),
    (
        "agent/rework-prd",
        "D93F0B",
        "Request the AI runner to generate or rewrite this Issue's PRD.",
    ),
    (
        "agent/deliberate",
        "D4C5F9",
        "Issue needs multi-agent deliberation (Phase 0) before implementation.",
    ),
    (
        "direct-pr",
        "D93F0B",
        "One-shot DIRECT across claimants; consumed after confirmed Draft PR. Not a workflow state.",
    ),
    (
        "validation/pending",
        "FBCA04",
        "Realistic Validation evidence awaits human sign-off on the PR.",
    ),
    (
        "validation/passed",
        "0E8A16",
        "A human verified the validation evidence and signed off.",
    ),
    (
        "validation/verifier-passed",
        "0E8A16",
        "Independent verifier agent approved this PR.",
    ),
    (
        "source/prd",
        "0052CC",
        "Issue has a canonical PRD tracked in the repository.",
    ),
    ("type/feature", "1D76DB", "User-facing feature or capability work."),
    ("type/refactor", "5319E7", "Internal refactor or structural improvement."),
    ("type/bug", "D73A4A", "Broken behavior or regression fix."),
    ("status/backlog", "BFDADC", "Tracked work that is not in progress yet."),
)

#: 可被配置覆盖生效名的标准名 → :class:`LabelConfig` 字段名。
#: 未列出的名（``source/prd``、``type/*``、``status/backlog``）始终用标准名。
CONFIGURABLE_LABEL_FIELDS: tuple[tuple[str, str], ...] = (
    ("agent/ready", "ready"),
    ("agent/running", "running"),
    ("agent/supervising", "supervising"),
    ("agent/review", "review"),
    ("agent/failed", "failed"),
    ("agent/blocked", "blocked"),
    ("agent/waiting", "waiting"),
    ("agent/rework-prd", "rework_prd"),
    ("agent/deliberate", "deliberate"),
    ("validation/pending", "validation_pending"),
    ("validation/passed", "validation_passed"),
    ("validation/verifier-passed", "verifier_passed"),
    ("direct-pr", "direct_pr"),
)

#: 直发档选择标签的特殊约定：配置可以把它置空表示「本仓库不用直发档」，
#: 此时该标签既不参与同步，也不属于允许写入的集合。
_OPTIONAL_BLANKABLE_LABEL_NAME = "direct-pr"


@dataclass(frozen=True)
class LabelSpec:
    """一个标准标签的同步定义与其在目标仓库的生效名。

    Attributes:
        standard_name: 约定标准名（``labels sync`` 的语义身份，不随配置改变）。
        color: ``gh label create`` 使用的颜色（六位 HEX，无 ``#``）。
        description: 标签描述。
        effective_name: 配置覆盖后真正写入 GitHub、也是 Issue 上可见的标签名。
    """

    standard_name: str
    color: str
    description: str
    effective_name: str


def configured_label_names(labels: LabelConfig) -> dict[str, str]:
    """返回「标准名 → 配置覆盖后的生效名」映射（含 agent 路由标签）。

    Args:
        labels: 仓库生效的标签配置。

    Returns:
        仅包含被显式覆盖项的映射；未覆盖的标准名不在映射里，由调用方回落标准名。
    """
    configured = {
        standard_name: getattr(labels, config_field)
        for standard_name, config_field in CONFIGURABLE_LABEL_FIELDS
    }
    configured.update(
        {f"agent/{agent_name}": label for agent_name, label in labels.agent_labels.items()}
    )
    return configured


def agent_label_meta(
    agent_name: str,
    agent_registry: dict[str, AgentSpec] | None = None,
) -> tuple[str, str]:
    """返回 agent 路由标签的颜色与描述（从 agent 注册表派生）。

    注册表优先用调用方传入的合并视图（含配置注册的新 agent），缺省回落内置
    默认；两者都未命中时使用通用兜底，保证未知 agent 仍可同步标签。

    Args:
        agent_name: agent 名（标签为 ``agent/<agent_name>``）。
        agent_registry: agent 注册表；``None`` 时只看内置 spec。

    Returns:
        ``(color, description)`` 元组。
    """
    spec = None
    if agent_registry is not None:
        spec = agent_registry.get(agent_name)
    if spec is None:
        spec = BUILTIN_AGENT_SPECS.get(agent_name)
    if spec is None:
        return "5319E7", f"Use {agent_name} for local runner execution."
    return spec.label_color, spec.label_description


def standard_label_specs(
    labels: LabelConfig,
    agent_registry: dict[str, AgentSpec] | None = None,
) -> tuple[LabelSpec, ...]:
    """返回一次 ``labels sync`` 会写入仓库的全部标签（含生效名解析）。

    Args:
        labels: 仓库生效的标签配置。
        agent_registry: agent 注册表，为 agent 路由标签提供颜色与描述。

    Returns:
        同步顺序排列的 :class:`LabelSpec` 元组；被配置置空的可选标签会被剔除。
    """
    spec_triples = list(STANDARD_LABEL_SPECS)
    for agent_name in labels.agent_labels:
        agent_color, agent_description = agent_label_meta(agent_name, agent_registry)
        spec_triples.append((f"agent/{agent_name}", agent_color, agent_description))

    name_overrides = configured_label_names(labels)
    resolved_specs: list[LabelSpec] = []
    for standard_name, label_color, label_description in spec_triples:
        effective_name = name_overrides.get(standard_name, standard_name)
        if standard_name == _OPTIONAL_BLANKABLE_LABEL_NAME:
            effective_name = effective_name.strip()
            if not effective_name:
                continue
        resolved_specs.append(
            LabelSpec(
                standard_name=standard_name,
                color=label_color,
                description=label_description,
                effective_name=effective_name,
            )
        )
    return tuple(resolved_specs)


def standard_label_names(
    labels: LabelConfig,
    agent_registry: dict[str, AgentSpec] | None = None,
) -> frozenset[str]:
    """返回同步集合的生效标签名，供网页侧写入做成员校验。

    Args:
        labels: 仓库生效的标签配置。
        agent_registry: agent 注册表（与 ``labels sync`` 同口径）。

    Returns:
        允许在网页上增删的标签名集合。
    """
    return frozenset(
        label_spec.effective_name for label_spec in standard_label_specs(labels, agent_registry)
    )


__all__ = [
    "CONFIGURABLE_LABEL_FIELDS",
    "STANDARD_LABEL_SPECS",
    "LabelSpec",
    "agent_label_meta",
    "configured_label_names",
    "standard_label_names",
    "standard_label_specs",
]

"""Agent 可用性事实与引导编排：安装引导、启动前门禁与设置视图的共同来源。

可用性只有一个判定：**可执行文件是否解析得到**（``shutil.which(spec.bin)``），
与 :func:`backend.core.use_cases.agent_invocation.resolve_agent_spec` 一样按
``PATH`` 解析、不做目录嗅探、不起任何子进程。列表呈现（``kc agent list``）、
引导（``kc agent setup``）、启动前门禁与 Console 生命周期设置页都消费本模块，
禁止任何一层各自再算一套可用性判断。

注册表以调用方传入的 ``AppConfig`` / ``agents`` 映射为准（内置
``BUILTIN_AGENT_SPECS`` 与 ``[agent_runner.agents.*]`` 自定义条目的合并结果），
本模块不读配置、不落盘：setup / fix / add 只做**计划与校验**，写入由调用方
经既有 lifecycle editor（持同一把配置写锁）执行，失败即不触达文件。
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_RUN,
    AGENT_PROFILES,
    NON_INTERACTIVE_PROMPT_DELIVERIES,
    AgentProfileSpec,
    AgentSpec,
)
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_AUTO,
    LIFECYCLE_AGENT_EXECUTOR,
    LIFECYCLE_AGENT_KEYS,
    concrete_declared_agent,
    normalize_lifecycle_agent_value,
)
from backend.core.use_cases.agent_invocation import build_agent_invocation
from backend.core.use_cases.agent_runner_failure import AgentUnavailableError
from backend.core.use_cases.agent_runner_output_protocols import (
    get_output_protocol_registry,
)

# ---------------------------------------------------------------------------
# 可用性事实
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentAvailability:
    """单个 agent 的可用性事实。

    Attributes:
        agent: 注册表里的 agent 名。
        bin: 声明的可执行文件名。
        resolved_path: ``shutil.which`` 解析到的绝对路径；未安装时为 ``None``。
        available: 可执行文件是否解析得到。
        declared_profiles: 该 agent 声明的非交互用途名，按
            ``backend.core.shared.models.agent_spec.AGENT_PROFILES``
            的稳定顺序排列（不含 ``interactive`` 声明）。
    """

    agent: str
    bin: str
    resolved_path: Path | None
    available: bool
    declared_profiles: tuple[str, ...]


def resolve_agent_availability(agent_name: str, agent_spec: AgentSpec) -> AgentAvailability:
    """返回单个 agent 的可用性事实（只查 PATH，不起子进程）。

    Args:
        agent_name: 注册表里的 agent 名。
        agent_spec: 对应的注册声明。

    Returns:
        可用性视图；``available`` 仅由 ``shutil.which(agent_spec.bin)`` 决定。
    """
    resolved_bin = shutil.which(agent_spec.bin)
    return AgentAvailability(
        agent=agent_name,
        bin=agent_spec.bin,
        resolved_path=Path(resolved_bin) if resolved_bin is not None else None,
        available=resolved_bin is not None,
        declared_profiles=tuple(
            profile_name for profile_name in AGENT_PROFILES if profile_name in agent_spec.profiles
        ),
    )


def list_agent_availability(
    agents: Mapping[str, AgentSpec],
) -> tuple[AgentAvailability, ...]:
    """按注册顺序返回全部已注册 agent 的可用性事实。

    Args:
        agents: 生效注册表（通常是 ``AppConfig.agents``；顺序即呈现优先级）。

    Returns:
        与输入同序的可用性视图元组。
    """
    return tuple(
        resolve_agent_availability(agent_name, agent_spec)
        for agent_name, agent_spec in agents.items()
    )


def availability_available_agents(
    agents: Mapping[str, AgentSpec],
) -> tuple[AgentAvailability, ...]:
    """只返回本机解析得到可执行文件的 agent（引导菜单的唯一候选来源）。"""
    return tuple(item for item in list_agent_availability(agents) if item.available)


def agent_availability_entries(
    agents: Mapping[str, AgentSpec],
) -> list[dict[str, Any]]:
    """把生效注册表的可用性事实序列化成按注册顺序排列的机读条目。

    ``kc agent`` 的机器输出与 Console 生命周期视图共用这一份形状，避免同一个事实在
    两处发展出不同键名。
    """
    return [
        {
            "agent": item.agent,
            "bin": item.bin,
            "available": item.available,
            "bin_path": str(item.resolved_path) if item.resolved_path is not None else None,
            "declared_profiles": list(item.declared_profiles),
        }
        for item in list_agent_availability(agents)
    ]


# ---------------------------------------------------------------------------
# 首启提示判定（FR-4）
# ---------------------------------------------------------------------------

#: 首启提示检查的阶段：实现阶段是绝大多数交互式命令的第一道 agent 选择。
HINT_CHECK_LIFECYCLE = "implementation"


@dataclass(frozen=True)
class AgentSetupHint:
    """首启提示判定结果。

    Attributes:
        needed: 是否需要提示（``True`` 当且仅当没有有效选择，或首选不可用）。
        reason: ``"ok"`` / ``"no-selection"`` / ``"unavailable"`` 三态之一。
        agent: 判定涉及的 agent 名；没有显式选择时为 ``None``。
        bin: 该 agent 声明的可执行文件名；``agent`` 为 ``None`` 时同为 ``None``。
    """

    needed: bool
    reason: str
    agent: str | None
    bin: str | None


def _declared_preferred_agent(config: AppConfig) -> str | None:
    """按"阶段预设绑定 → 矩阵显式声明 → runner 默认"取首选具体 agent 名。

    ``auto`` / ``executor`` 不是具体 agent 名（要留给解析函数按阶段语义展开），
    因此这里不把它们当作有效选择——首启提示的语义是"用户有没有在这台机器上
    留下一个可执行的显式选择"。
    """
    preset_name = config.lifecycle_presets.declared_value(HINT_CHECK_LIFECYCLE)
    if preset_name is not None:
        preset = config.agent_presets.get(preset_name)
        if preset is not None:
            return preset.agent
    declared = concrete_declared_agent(config.lifecycle_agents, HINT_CHECK_LIFECYCLE)
    if declared is not None:
        return declared
    runner_default = normalize_lifecycle_agent_value(config.runner.default_agent)
    if runner_default in (LIFECYCLE_AGENT_AUTO, LIFECYCLE_AGENT_EXECUTOR):
        return None
    return runner_default or None


def resolve_setup_hint(config: AppConfig) -> AgentSetupHint:
    """判定生效配置是否需要在首次交互式使用时提示一次。

    Args:
        config: 生效 ``AppConfig``（含仓库层覆盖）。

    Returns:
        三态判定：无显式选择、选择不可用、或选择可用。
    """
    agent_name = _declared_preferred_agent(config)
    if agent_name is None:
        return AgentSetupHint(needed=True, reason="no-selection", agent=None, bin=None)
    agent_spec = config.agents.get(agent_name)
    if agent_spec is None:
        return AgentSetupHint(needed=True, reason="unavailable", agent=agent_name, bin=None)
    availability = resolve_agent_availability(agent_name, agent_spec)
    if not availability.available:
        return AgentSetupHint(
            needed=True, reason="unavailable", agent=agent_name, bin=availability.bin
        )
    return AgentSetupHint(needed=False, reason="ok", agent=agent_name, bin=availability.bin)


# ---------------------------------------------------------------------------
# 启动前门禁（FR-5）
# ---------------------------------------------------------------------------

#: 门禁覆盖的阶段：实现（含其预设）+ 校验 + 审核，即一次完整轮次会真正
#: 调用 agent 的三个入口。修复 / 收尾沿用实现者（executor），不单独检查。
GATE_LIFECYCLE_STAGES: tuple[str, ...] = ("implementation", "verifier", "review")


class PrimaryAgentNotInstalledError(AgentUnavailableError):
    """阶段解析到的 agent 在本机没有可执行文件，**不允许**被回退链绕过。

    继承 :class:`AgentUnavailableError` 让既有的"不可用"分类继续认识它；回退
    阶梯对这一类型必须原样上抛——回退链的语义是"这个 agent 跑挂了换一个"，
    不是"这台机器没装就偷偷换人"（临时换人请显式传 ``--agent``）。
    """

    def __init__(self, agent: str, message: str) -> None:
        super().__init__(agent)
        # 父类文案只点名 agent；门禁要点名阶段、预设、可执行文件与下一步命令。
        self.args = (message,)
        self.agent = agent


def _stage_agent_and_reason(
    stage: str,
    config: AppConfig,
    *,
    agent_override: str | None = None,
) -> tuple[str, str | None, str | None] | None:
    """返回 ``(agent, preset, bin)``；该阶段可用或未点名具体 agent 时返回 ``None``。

    与运行期一致地走"预设绑定优先于矩阵"：预设整体决定 agent，因此不可用时
    点名的是预设（用户要改的是预设或装回 CLI）。``agent_override`` 只作用于
    实现阶段——显式换人时该阶段就按换进来的人判定，其余阶段仍按配置解析。
    """
    preset_name: str | None = None
    override = normalize_lifecycle_agent_value(agent_override or "")
    if stage == HINT_CHECK_LIFECYCLE and override not in ("", LIFECYCLE_AGENT_AUTO):
        agent_name = override
    else:
        declared_preset = config.lifecycle_presets.declared_value(stage)
        if declared_preset is not None:
            model_preset = config.agent_presets.get(declared_preset)
            if model_preset is None:
                # 绑定指向已删除的预设：解析链会按回落层走，这里不制造第二条报错口径。
                return None
            preset_name = declared_preset
            agent_name = model_preset.agent
        else:
            agent_name = concrete_declared_agent(config.lifecycle_agents, stage)
            if agent_name is None:
                # 未显式声明：实现阶段回落 runner.default_agent（内置必有），
                # 校验 / 审核回落既有配置键或 auto 路由，由运行期自己判定。
                if stage != HINT_CHECK_LIFECYCLE:
                    return None
                runner_default = normalize_lifecycle_agent_value(config.runner.default_agent)
                agent_name = runner_default or None
            if agent_name is None or agent_name in (
                LIFECYCLE_AGENT_AUTO,
                LIFECYCLE_AGENT_EXECUTOR,
            ):
                return None
    agent_spec = config.agents.get(agent_name)
    if agent_spec is None:
        return (agent_name, preset_name, None)
    availability = resolve_agent_availability(agent_name, agent_spec)
    if availability.available:
        return None
    return (agent_name, preset_name, availability.bin)


def resolve_run_gate_block(
    config: AppConfig,
    *,
    agent_override: str | None = None,
) -> tuple[str, str | None, str, str | None] | None:
    """启动前可用性门禁：返回第一个不可用的阶段选择，或 ``None`` 放行。

    Args:
        config: 生效 ``AppConfig``（含仓库层覆盖与回退候选）。
        agent_override: ``--agent`` 覆盖值（实现阶段用它替代配置解析结果）。

    Returns:
        ``(stage, preset, agent, bin)`` 元组；``bin`` 为 ``None`` 表示该 agent
        根本没注册。放行时返回 ``None``。

    回退候选**不参与**判定：主选没装就是配置错误，临时换人必须显式传
    ``--agent``（见 PRD 决策二），运行时失败才轮到回退链。
    """
    for stage in GATE_LIFECYCLE_STAGES:
        blocked = _stage_agent_and_reason(stage, config, agent_override=agent_override)
        if blocked is not None:
            agent_name, preset_name, bin_name = blocked
            return (stage, preset_name, agent_name, bin_name)
    return None


def assert_run_gate(config: AppConfig, *, agent_override: str | None = None) -> None:
    """在领取 Issue / 启动进程之前执行门禁，不可用即 fail-fast。

    Args:
        config: 生效 ``AppConfig``。
        agent_override: ``--agent`` 覆盖值；显式换人时按换进来的 agent 判定
            实现阶段（其余阶段仍按配置解析）。

    Raises:
        PrimaryAgentNotInstalledError: 某个阶段解析到的 agent 未注册或可执行
            文件解析不到；消息点名阶段、预设、agent、可执行文件与下一步命令。
    """
    blocked = resolve_run_gate_block(config, agent_override=agent_override)
    if blocked is None:
        return
    stage, preset_name, agent_name, bin_name = blocked
    preset_note = f" (preset '{preset_name}')" if preset_name is not None else ""
    if bin_name is None:
        detail = (
            f"stage '{stage}'{preset_note} resolves to agent '{agent_name}', "
            f"which is not registered. Registered agents: {', '.join(config.agents)}. "
            "Install its CLI and register it with `kc agent add`, or run "
            "`kc agent setup` to choose an installed agent."
        )
        bin_for_error = agent_name
    else:
        detail = (
            f"stage '{stage}'{preset_note} resolves to agent '{agent_name}' "
            f"(bin '{bin_name}'), which is not available on PATH. Install '{bin_name}' "
            "or run `kc agent setup`; `kc agent setup --fix` removes global-layer "
            "bindings that point at unavailable agents."
        )
        bin_for_error = bin_name
    raise PrimaryAgentNotInstalledError(bin_for_error, detail)


# ---------------------------------------------------------------------------
# 引导计划（kc agent setup / --fix）
# ---------------------------------------------------------------------------

#: 引导允许直接点名的阶段菜单（实现 / 校验 / 审核；其余阶段走高级配置）。
SETUP_MENU_STAGES: tuple[str, ...] = ("implementation", "verifier", "review")


class AgentOnboardingError(ValueError):
    """setup / fix / add 计划在触达配置文件之前失败。

    ``kind`` 让 API 层把同一类失败映射到稳定退出码：``"usage"`` →
    ``USAGE(2)``（参数组合不成立），``"not_found"`` → ``NOT_FOUND(3)``
    （选了本机没装的 agent）。
    """

    def __init__(self, message: str, *, kind: str = "usage") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class AgentSetupPlan:
    """一次 ``kc agent setup`` 写入的完整计划（未触达文件即可预览与判等）。

    Attributes:
        stage: 生命周期阶段键。
        agent: 矩阵写入值（``[agent_runner.lifecycle_agents]`` 的该键）；
            预设路径为 ``None``。
        preset: 预设绑定写入值（``[agent_runner.lifecycle_presets]`` 的该键）；
            矩阵路径为 ``None``。
        unbind_stage_preset: 全局层该阶段是否已有预设绑定需要解绑——预设会
            遮蔽矩阵，不解绑则刚写入的 agent 选择永远不会生效。
        already_satisfied: 全局层现状已等于目标状态（重复执行不写文件）。
    """

    stage: str
    agent: str | None
    preset: str | None
    unbind_stage_preset: bool
    already_satisfied: bool


def _require_available_agent(
    agent_name: str, agents: Mapping[str, AgentSpec], *, context: str
) -> AgentAvailability:
    """确认 agent 已注册且可执行文件解析得到，否则抛分类好的计划错误。"""
    agent_spec = agents.get(agent_name)
    if agent_spec is None:
        raise AgentOnboardingError(
            f"{context}: agent '{agent_name}' is not registered. "
            f"Registered agents: {', '.join(agents)}. "
            "Register a local CLI with `kc agent add` first.",
        )
    availability = resolve_agent_availability(agent_name, agent_spec)
    if not availability.available:
        raise AgentOnboardingError(
            f"{context}: agent '{agent_name}' (bin '{availability.bin}') is not "
            "available on PATH. Install the CLI, choose another agent "
            "(`kc agent list`), or register it with `kc agent add`.",
            kind="not_found",
        )
    return availability


def build_setup_plan(
    config: AppConfig,
    *,
    stage: str,
    agent: str | None = None,
    preset: str | None = None,
) -> AgentSetupPlan:
    """校验一次 setup 选择并给出全局层写入计划（不触达文件）。

    Args:
        config: **全局层** ``AppConfig``（``agent_presets`` / 两层声明视图
            据此只反映机器级文件）。
        stage: 生命周期阶段键（九键闭集）。
        agent: 显式选择的 agent 名（写矩阵并解绑遮蔽预设）；与 ``preset`` 二选一。
        preset: 已定义预设名（写阶段 → 预设绑定）；与 ``agent`` 二选一。

    Returns:
        :class:`AgentSetupPlan`；``already_satisfied`` 为真时应用步骤应跳过写入。

    Raises:
        AgentOnboardingError: 阶段名非法、``agent``/``preset`` 没给或同时给了、
            预设未定义或其 agent 不可用、agent 未注册或不可用
            （``kind="not_found"``）。
    """
    if stage not in LIFECYCLE_AGENT_KEYS:
        raise AgentOnboardingError(
            f"unknown lifecycle stage '{stage}'. Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}."
        )
    if (agent is None) == (preset is None):
        raise AgentOnboardingError("setup needs exactly one of --agent <name> or --preset <name>.")
    if preset is not None:
        model_preset = config.agent_presets.get(preset)
        if model_preset is None:
            raise AgentOnboardingError(
                f"preset '{preset}' is not defined. See `kc agent presets` "
                "for the defined names, or create one with `kc agent preset set`."
            )
        _require_available_agent(model_preset.agent, config.agents, context=f"preset '{preset}'")
        return AgentSetupPlan(
            stage=stage,
            agent=None,
            preset=preset,
            unbind_stage_preset=False,
            already_satisfied=config.lifecycle_presets.global_layer.get(stage) == preset,
        )
    _require_available_agent(str(agent), config.agents, context=f"--agent {agent}")
    return AgentSetupPlan(
        stage=stage,
        agent=agent,
        preset=None,
        unbind_stage_preset=stage in config.lifecycle_presets.global_layer,
        already_satisfied=(
            stage not in config.lifecycle_presets.global_layer
            and config.lifecycle_agents.global_layer.get(stage) == agent
        ),
    )


@dataclass(frozen=True)
class AgentFixEntry:
    """``--fix`` 要清理的一条全局层阶段绑定。

    Attributes:
        stage: 生命周期阶段键。
        layer: 绑定所在段（``"lifecycle_presets"`` / ``"lifecycle_agents"``）。
        value: 被清理的绑定值（预设名或 agent 名）。
        reason: 为什么它指向了一个不可用的 agent（点给用户看，不静默）。
    """

    stage: str
    layer: str
    value: str
    reason: str


def _unavailable_reason(value: str, agents: Mapping[str, AgentSpec]) -> str | None:
    """矩阵取值指向不可用 agent 时给出点名理由；可用/特殊令牌返回 ``None``。"""
    if value in (LIFECYCLE_AGENT_AUTO, LIFECYCLE_AGENT_EXECUTOR):
        return None
    agent_spec = agents.get(value)
    if agent_spec is None:
        return f"agent '{value}' is not registered"
    availability = resolve_agent_availability(value, agent_spec)
    if not availability.available:
        return f"agent '{value}' (bin '{availability.bin}') is not available on PATH"
    return None


def build_fix_plan(config: AppConfig) -> tuple[AgentFixEntry, ...]:
    """列出全局层中指向不可用 agent 的阶段绑定（``kc agent setup --fix``）。

    只读判定两段的**全局层显式声明**：``lifecycle_presets`` 绑定的预设未定义、
    或其 agent 未注册 / 不可用；``lifecycle_agents`` 声明的 agent 未注册 / 不可用
    （``auto`` / ``executor`` 特殊令牌不动）。预设定义本身**永远不删**——那是
    用户的模型选择记录，可能只是这台机器暂时没装对应 CLI。
    """
    entries: list[AgentFixEntry] = []
    for stage, preset_name in config.lifecycle_presets.global_layer.items():
        model_preset = config.agent_presets.get(preset_name)
        if model_preset is None:
            reason = f"preset '{preset_name}' is not defined"
        else:
            reason_text = _unavailable_reason(model_preset.agent, config.agents)
            if reason_text is None:
                continue
            reason = f"preset '{preset_name}' binds {reason_text}"
        entries.append(AgentFixEntry(stage, "lifecycle_presets", preset_name, reason))
    for stage, value in config.lifecycle_agents.global_layer.items():
        reason_text = _unavailable_reason(value, config.agents)
        if reason_text is None:
            continue
        entries.append(AgentFixEntry(stage, "lifecycle_agents", value, reason_text))
    return tuple(entries)


# ---------------------------------------------------------------------------
# 自定义注册计划（kc agent add）
# ---------------------------------------------------------------------------

#: 注册名即 TOML 表键与 ``agent/<name>`` 标签的来源，限制为目录/标签安全的字符集。
_AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

#: 新注册 agent 的 GitHub 标签默认色（与 codex 默认一致，仅用于注册块预览）。
_DEFAULT_CUSTOM_LABEL_COLOR = "5319E7"


@dataclass(frozen=True)
class AgentAddPlan:
    """一次自定义 agent 注册的校验结果与写回素材。

    Attributes:
        agent_name: 注册名。
        table_fields: ``[agent_runner.agents.<name>]`` 顶层字段（只含用户
            显式给出的项）。
        profile_fields: ``[agent_runner.agents.<name>.profiles.run]`` 字段。
        agent_spec: 合并后的完整 spec（供生效注册表演算）。
        toml_preview: 注册块的 TOML 文本（默认输出，与 --write 落盘内容一致）。
    """

    agent_name: str
    table_fields: dict[str, object]
    profile_fields: dict[str, object]
    agent_spec: AgentSpec
    toml_preview: str


def build_agent_add_plan(
    config: AppConfig,
    agent_name: str,
    *,
    bin_name: str,
    args: tuple[str, ...] = (),
    prompt_delivery: str = "argv_tail",
    prompt_flag: str | None = None,
    output_protocol: str = "plain",
    auth_home: str | None = None,
    label: str | None = None,
) -> AgentAddPlan:
    """校验一个最小 run 用途注册块并产出写回素材（不触达文件）。

    校验与运行期读配置走**同一条组装路径**：计划阶段就用
    :func:`backend.core.use_cases.agent_invocation.build_agent_invocation`
    把候选 spec 演算成 argv，注册成功却拼不出命令的情况在这里就被拒绝。
    只声明 run 用途；deliberate / generate / repl / judge 不猜，用户需要时
    再按文档补注册块。

    Args:
        config: 生效 ``AppConfig``（重名判定与演算基线）。
        agent_name: 注册名（合法字符集见 ``_AGENT_NAME_PATTERN``）。
        bin_name: 可执行文件名。
        args: run 用途的基础 argv 片段（``--arg`` 逐个给出）。
        prompt_delivery: 提示词投递方式（限非交互闭集）。
        prompt_flag: ``prompt_delivery="flag"`` 时承载提示词的参数名。
        output_protocol: 输出协议 id（必须已在 entry point 注册表）。
        auth_home: 可选的本机认证/配置根目录声明。
        label: 可选的路由标签覆盖（默认 ``agent/<name>``）。

    Returns:
        :class:`AgentAddPlan`。

    Raises:
        AgentOnboardingError: 名称非法或与生效注册表重名、投递方式非法或
            缺 ``prompt_flag``、协议未注册、可执行文件名为空、或
            ``build_agent_invocation()`` 组装失败。
    """
    if not _AGENT_NAME_PATTERN.match(agent_name):
        raise AgentOnboardingError(
            f"invalid agent name '{agent_name}': use letters/digits/'_'/'-' "
            "starting with a letter."
        )
    if agent_name in config.agents:
        raise AgentOnboardingError(
            f"agent '{agent_name}' is already registered. Edit its "
            "[agent_runner.agents.<name>] block instead of adding a second one."
        )
    if not bin_name.strip():
        raise AgentOnboardingError("--bin must be a non-empty executable name.")
    if prompt_delivery not in NON_INTERACTIVE_PROMPT_DELIVERIES:
        raise AgentOnboardingError(
            f"invalid --prompt-delivery '{prompt_delivery}'. Valid values for the "
            f"run profile: {', '.join(NON_INTERACTIVE_PROMPT_DELIVERIES)}."
        )
    if prompt_delivery == "flag" and not (prompt_flag or "").strip():
        raise AgentOnboardingError("--prompt-delivery flag requires --prompt-flag.")
    registered_protocol_ids = get_output_protocol_registry().list_ids()
    if output_protocol not in registered_protocol_ids:
        raise AgentOnboardingError(
            f"output protocol '{output_protocol}' is not registered. "
            f"Available protocols: {', '.join(registered_protocol_ids)} "
            "(`kc agent doctor --protocols`). New structured protocols need code, "
            "not config."
        )
    label_value = label or f"agent/{agent_name}"
    profile_spec = AgentProfileSpec(
        args=tuple(args),
        prompt_flag=prompt_flag,
        prompt_delivery=prompt_delivery,
        output_protocol=output_protocol,
    )
    agent_spec = AgentSpec(
        bin=bin_name.strip(),
        label=label_value,
        label_color=_DEFAULT_CUSTOM_LABEL_COLOR,
        label_description="",
        auth_home=auth_home,
        profiles={AGENT_PROFILE_RUN: profile_spec},
    )
    # 同路径演算：把候选 spec 并进生效注册表，用真正的组装函数拼一次 argv。
    candidate_config = dataclasses.replace(config, agents={**config.agents, agent_name: agent_spec})
    try:
        build_agent_invocation(
            agent_name,
            AGENT_PROFILE_RUN,
            "golden-prompt",
            Path.cwd(),
            candidate_config,
        )
    except ValueError as exc:
        raise AgentOnboardingError(f"candidate run profile cannot assemble an argv: {exc}") from exc

    table_fields: dict[str, object] = {"bin": agent_spec.bin, "label": label_value}
    if auth_home is not None:
        table_fields["auth_home"] = auth_home
    profile_fields: dict[str, object] = {
        "args": list(args),
        "prompt_delivery": prompt_delivery,
        "output_protocol": output_protocol,
    }
    if prompt_flag is not None:
        profile_fields["prompt_flag"] = prompt_flag
    return AgentAddPlan(
        agent_name=agent_name,
        table_fields=table_fields,
        profile_fields=profile_fields,
        agent_spec=agent_spec,
        toml_preview=_render_registration_toml(agent_name, table_fields, profile_fields),
    )


def _render_registration_toml(
    agent_name: str,
    table_fields: Mapping[str, object],
    profile_fields: Mapping[str, object],
) -> str:
    """把注册块渲染成与 --write 落盘内容一致的 TOML 文本（预览即所得）。"""
    lines = [f"[agent_runner.agents.{agent_name}]"]
    lines.extend(f"{key} = {json.dumps(value)}" for key, value in table_fields.items())
    lines.append("")
    lines.append(f"[agent_runner.agents.{agent_name}.profiles.run]")
    lines.extend(f"{key} = {json.dumps(value)}" for key, value in profile_fields.items())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 写回编排（编辑器由 API 层构造；core 不 import infrastructure 路径解析）
# ---------------------------------------------------------------------------


class SetupSettingsEditor(Protocol):
    """setup / fix / add 写回所需的最小编辑器面（由 lifecycle editor 满足）。"""

    @property
    def config_path(self) -> Path:
        """写入目标文件路径。"""
        ...

    def write_transaction(self):
        """把整段读-改-写圈进同一把配置写锁的上下文。"""
        ...

    def update_lifecycle_agents(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.lifecycle_agents]``。"""
        ...

    def update_lifecycle_presets(self, values: Mapping[str, object]) -> None:
        """写回 ``[agent_runner.lifecycle_presets]``。"""
        ...

    def update_agent_registration(
        self,
        agent_name: str,
        fields: Mapping[str, object],
        profile_run_fields: Mapping[str, object],
    ) -> None:
        """写回自定义 agent 注册块与其 run 用途子表。"""
        ...


def apply_setup_plan(editor: SetupSettingsEditor, plan: AgentSetupPlan) -> None:
    """经既有编辑器与写锁落一次 setup 计划（幂等：已满足则不触盘）。

    先解绑遮蔽矩阵的预设、再写矩阵：解绑要求该键在本层存在，是最可能失败的
    一步，放在前面保证失败时矩阵写入还没发生。

    Raises:
        OSError / ConfigDocumentInvalidError / ConfigKeyMissingError /
            ConfigLockBusyError: 原样交回，由 API 层归类退出码；任何一条抛出
            时配置文件保持调用前状态（解绑先失败则矩阵未写）。
    """
    if plan.already_satisfied:
        return
    with editor.write_transaction():
        if plan.preset is not None:
            editor.update_lifecycle_presets({plan.stage: plan.preset})
            return
        if plan.unbind_stage_preset:
            editor.update_lifecycle_presets({plan.stage: None})
        editor.update_lifecycle_agents({plan.stage: str(plan.agent)})


def apply_fix_plan(editor: SetupSettingsEditor, entries: tuple[AgentFixEntry, ...]) -> None:
    """一次锁内批删 --fix 计划点名的全局层绑定（不删预设定义）。"""
    if not entries:
        return
    preset_unbinds = {entry.stage: None for entry in entries if entry.layer == "lifecycle_presets"}
    agent_unbinds = {entry.stage: None for entry in entries if entry.layer == "lifecycle_agents"}
    with editor.write_transaction():
        if preset_unbinds:
            editor.update_lifecycle_presets(preset_unbinds)
        if agent_unbinds:
            editor.update_lifecycle_agents(agent_unbinds)


def apply_add_plan(editor: SetupSettingsEditor, plan: AgentAddPlan) -> None:
    """把已校验的注册块一次落盘到全局配置。"""
    with editor.write_transaction():
        editor.update_agent_registration(plan.agent_name, plan.table_fields, plan.profile_fields)


__all__ = [
    "GATE_LIFECYCLE_STAGES",
    "HINT_CHECK_LIFECYCLE",
    "SETUP_MENU_STAGES",
    "AgentAvailability",
    "AgentAddPlan",
    "AgentFixEntry",
    "AgentOnboardingError",
    "AgentSetupHint",
    "AgentSetupPlan",
    "PrimaryAgentNotInstalledError",
    "SetupSettingsEditor",
    "agent_availability_entries",
    "apply_add_plan",
    "apply_fix_plan",
    "apply_setup_plan",
    "assert_run_gate",
    "availability_available_agents",
    "build_agent_add_plan",
    "build_fix_plan",
    "build_setup_plan",
    "list_agent_availability",
    "resolve_agent_availability",
    "resolve_run_gate_block",
    "resolve_setup_hint",
]

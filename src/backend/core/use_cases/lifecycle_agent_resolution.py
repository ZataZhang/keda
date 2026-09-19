"""生命周期 Agent 解析：把"阶段 -> agent"从散落配置收敛为一个命名函数。

优先级（高到低）：

1. PRD 文件头部 ``lifecycle_agents`` 覆盖块（PRD 级，只影响该 PRD）；
2. ``[agent_runner.lifecycle_agents]`` 仓库层（``.iar.toml``）；
3. ``[agent_runner.lifecycle_agents]`` 全局层（``config.toml``）；
4. 既有散落配置键（``runner.default_agent`` / ``validation.verifier_agent`` /
   ``pre_pr_review.review_agent`` / ``post_pr_supervisor.supervisor_agent`` /
   ``interactive_decision.default_agent`` / ``generated_content.default_agent`` /
   ``deliberation.default_synthesizer``）；
5. 内置默认（:data:`LIFECYCLE_AGENT_BUILTIN_DEFAULT`）。

取值语义：``auto`` 按该阶段**既有**语义路由（实现=标签路由 / 校验=回退链上
第一个 ≠ 实现者 / 审核=不同人优先 / 监督=沿用本次实现者 / 辩论=标签路由），
``executor`` 跟随实现阶段选中的 agent（仅 fix / closeout 合法），其余必须是
已注册 agent 名——未注册时抛 :class:`UnknownAgentError`（fail-fast，不静默
回落）。

本模块刻意不重写各阶段的 ``auto`` 语义：能委托给既有函数的一律委托
（``choose_agent`` / ``resolve_reviewer_agent``），只在"矩阵声明了 auto 但
既有配置键不是 auto"这一条路径上就地实现同一套语义。
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Mapping
from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_AUTO,
    LIFECYCLE_AGENT_AUTO_KEYS,
    LIFECYCLE_AGENT_BUILTIN_DEFAULT,
    LIFECYCLE_AGENT_EXECUTOR,
    LIFECYCLE_AGENT_EXECUTOR_KEYS,
    LIFECYCLE_AGENT_KEYS,
    LIFECYCLE_SOURCE_LEGACY,
    LIFECYCLE_SOURCE_PRD_OVERRIDE,
    normalize_lifecycle_agent_value,
)

_logger = logging.getLogger(__name__)

_PRD_OVERRIDE_BLOCK_PATTERN = re.compile(r"^\s*[-*]\s*lifecycle_agents\s*:\s*$", re.IGNORECASE)
_PRD_OVERRIDE_ENTRY_PATTERN = re.compile(
    r"^\s+[-*]\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>\S.*?)\s*$"
)


def parse_prd_lifecycle_overrides(
    prd_text: str,
    *,
    prd_path: str | Path | None = None,
) -> dict[str, str]:
    """解析 PRD markdown 头部的 ``lifecycle_agents`` 覆盖块。

    覆盖块形如（落在标题下的 bullet 区，与 §8 依赖声明的嵌套 bullet 同型）::

        - lifecycle_agents:
          - implementation: claude
          - review: codex

    未知键名、非法取值（``executor`` 用在非 fix/closeout、``auto`` 用在无
    auto 语义的阶段、空值）都立即报错并带上 PRD 路径，不静默忽略。

    Args:
        prd_text: PRD markdown 全文。
        prd_path: 报错信息里引用的 PRD 路径（可选）。

    Returns:
        生命周期键 -> 覆盖值；没有覆盖块时返回空 dict。

    Raises:
        ValueError: 覆盖块里的键名或取值非法。
    """
    overrides: dict[str, str] = {}
    in_block = False
    location_suffix = f" (PRD: {prd_path})" if prd_path is not None else ""
    for raw_line in prd_text.splitlines():
        if not in_block:
            if _PRD_OVERRIDE_BLOCK_PATTERN.match(raw_line):
                in_block = True
            continue
        entry_match = _PRD_OVERRIDE_ENTRY_PATTERN.match(raw_line)
        if entry_match is None:
            if not raw_line.strip():
                continue
            # 覆盖块在遇到第一个非条目行时结束。
            break
        lifecycle_key = entry_match.group("key")
        raw_value = entry_match.group("value").strip()
        _validate_prd_override_entry(lifecycle_key, raw_value, location_suffix=location_suffix)
        overrides[lifecycle_key] = normalize_lifecycle_agent_value(raw_value)
    return overrides


def render_prd_lifecycle_overrides_block(overrides: Mapping[str, str]) -> list[str]:
    """把覆盖映射渲染成 PRD 头部 bullet 块的行列表。

    条目按 :data:`LIFECYCLE_AGENT_KEYS` 的固定顺序输出，保证同样的覆盖集合
    总是产生逐字节相同的文本（避免 UI 反复保存导致无意义 diff）。
    """
    block_lines = ["- lifecycle_agents:"]
    for lifecycle_key in LIFECYCLE_AGENT_KEYS:
        if lifecycle_key in overrides:
            block_lines.append(f"  - {lifecycle_key}: {overrides[lifecycle_key]}")
    return block_lines


def upsert_prd_lifecycle_overrides(
    prd_text: str,
    overrides: Mapping[str, str],
) -> str:
    """写入 / 替换 PRD 头部的 ``lifecycle_agents`` 覆盖块，保留其余内容。

    ``overrides`` 是**完整的期望集合**（不是增量）：块被整体重写，块之外的
    头部内容与正文一律不动。空集合表示删除该块。

    Args:
        prd_text: PRD markdown 全文。
        overrides: 生命周期键 -> 覆盖值（完整期望集合）。

    Returns:
        写回后的 PRD 全文。

    Raises:
        ValueError: 键名或取值非法。
    """
    for lifecycle_key, raw_value in overrides.items():
        _validate_prd_override_entry(lifecycle_key, raw_value, location_suffix="")
    new_block = render_prd_lifecycle_overrides_block(overrides) if overrides else []
    lines = prd_text.splitlines()

    block_start: int | None = None
    block_end: int | None = None
    for index, raw_line in enumerate(lines):
        if _PRD_OVERRIDE_BLOCK_PATTERN.match(raw_line):
            block_start = index
            block_end = index + 1
            while block_end < len(lines) and _PRD_OVERRIDE_ENTRY_PATTERN.match(lines[block_end]):
                block_end += 1
            break

    if block_start is not None and block_end is not None:
        if (
            not new_block
            and block_start > 0
            and not lines[block_start - 1].strip()
            and block_end < len(lines)
            and not lines[block_end].strip()
        ):
            # 删除整块时顺带吃掉一个相邻空行，避免留下连续空行。
            block_end += 1
        updated_lines = [*lines[:block_start], *new_block, *lines[block_end:]]
    elif new_block:
        insert_at = _find_prd_override_insert_index(lines)
        updated_lines = [*lines[:insert_at], *new_block, "", *lines[insert_at:]]
    else:
        updated_lines = lines

    trailing_newline = "\n" if prd_text.endswith("\n") else ""
    return "\n".join(updated_lines) + trailing_newline


def _find_prd_override_insert_index(lines: list[str]) -> int:
    """返回新覆盖块应插入的行号（标题下的 bullet 区末尾）。

    从标题行之后推进，跳过空行与顶层 bullet（如 ``- GitHub Issue:``），落在第一
    个非 bullet 行（通常是引用块或章节标题）之前。
    """
    title_index = 0
    for index, raw_line in enumerate(lines):
        if raw_line.startswith("# "):
            title_index = index
            break
    insert_at = title_index + 1
    while insert_at < len(lines):
        stripped = lines[insert_at].lstrip()
        if not stripped or stripped.startswith(("-", "*")):
            insert_at += 1
            continue
        break
    return insert_at


def _validate_prd_override_entry(
    lifecycle_key: str,
    raw_value: str,
    *,
    location_suffix: str,
) -> None:
    """校验单条 PRD 覆盖条目，非法时抛 ``ValueError``。"""
    if lifecycle_key not in LIFECYCLE_AGENT_KEYS:
        raise ValueError(
            f"PRD lifecycle_agents: unknown lifecycle key '{lifecycle_key}'"
            f"{location_suffix}. Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}."
        )
    normalized = normalize_lifecycle_agent_value(raw_value)
    if not normalized:
        raise ValueError(
            f"PRD lifecycle_agents.{lifecycle_key}: value must not be empty{location_suffix}."
        )
    if (
        normalized == LIFECYCLE_AGENT_EXECUTOR
        and lifecycle_key not in LIFECYCLE_AGENT_EXECUTOR_KEYS
    ):
        raise ValueError(
            f"PRD lifecycle_agents.{lifecycle_key}: 'executor' is only valid for "
            f"{', '.join(sorted(LIFECYCLE_AGENT_EXECUTOR_KEYS))}{location_suffix}."
        )
    if normalized == LIFECYCLE_AGENT_AUTO and lifecycle_key not in LIFECYCLE_AGENT_AUTO_KEYS:
        raise ValueError(
            f"PRD lifecycle_agents.{lifecycle_key}: 'auto' is not a valid value for this "
            f"stage{location_suffix}."
        )


def legacy_configured_agent(lifecycle: str, config: AppConfig) -> str:
    """返回该生命周期对应的**既有散落配置键**取值。

    Args:
        lifecycle: 生命周期键。
        config: 应用配置。

    Returns:
        既有配置键的原始取值（可能是 ``auto`` / 具体 agent 名 / ``executor``）。
    """
    if lifecycle == "implementation":
        return config.runner.default_agent
    if lifecycle in LIFECYCLE_AGENT_EXECUTOR_KEYS:
        return LIFECYCLE_AGENT_EXECUTOR
    if lifecycle == "verifier":
        return config.validation.verifier_agent
    if lifecycle == "review":
        return config.pre_pr_review.review_agent
    if lifecycle == "supervisor":
        return config.post_pr_supervisor.supervisor_agent
    if lifecycle == "planner":
        return config.interactive_decision.default_agent
    if lifecycle == "content_generation":
        return config.generated_content.default_agent
    if lifecycle == "deliberate":
        return config.deliberation.default_synthesizer
    raise ValueError(
        f"Unknown lifecycle key '{lifecycle}'. Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}."
    )


def effective_prd_overrides(
    issue: IssueSummary | None,
    prd_overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """合并「Issue 携带的 PRD 覆盖」与调用方显式传入的覆盖。

    Issue 上的 ``lifecycle_overrides`` 由编排入口在拿到仓库路径后从 PRD 文件解析
    回填，使实现 / 审核 / 监督等阶段在**不新增参数**的情况下也能读到 PRD 级覆盖；
    调用方显式传入的 ``prd_overrides``（如执行循环里直接从 worktree 读出的那份）
    优先于 Issue 携带的版本。

    Args:
        issue: 当前 Issue；``None`` 表示无上下文。
        prd_overrides: 调用方显式传入的覆盖。

    Returns:
        合并后的 PRD 级覆盖；两者都为空时返回空 dict。
    """
    merged_overrides: dict[str, str] = {}
    if issue is not None:
        merged_overrides.update(dict(getattr(issue, "lifecycle_overrides", ()) or ()))
    if prd_overrides:
        merged_overrides.update(prd_overrides)
    return merged_overrides


def attach_prd_lifecycle_overrides(
    issue: IssueSummary,
    repo_path: Path | None,
) -> IssueSummary:
    """从该 Issue 引用的 PRD 文件解析头部覆盖，回填到 ``IssueSummary``。

    最佳努力：PRD 路径缺失、文件不存在或解析失败时原样返回该 Issue（记一条 warning），
    绝不让"读 PRD 头部"这一步打断流水线。

    Args:
        issue: 待处理的 Issue。
        repo_path: 目标仓库主检出根（PRD 随仓库存在，worktree 尚未创建时也可读）。

    Returns:
        回填了 ``lifecycle_overrides`` 的 Issue；无覆盖或读取失败时返回原对象。
    """
    if repo_path is None:
        return issue
    # 局部导入：agent_runner_feedback 与解析函数分属不同子系统，避免模块级环。
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path

    prd_relative_path = extract_prd_path(issue.body)
    if prd_relative_path is None:
        return issue
    prd_file_path = repo_path / prd_relative_path
    try:
        prd_text = prd_file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _logger.warning(
            "Issue #%d: could not read PRD '%s' for lifecycle overrides: %s",
            issue.number,
            prd_file_path,
            exc,
        )
        return issue
    try:
        overrides = parse_prd_lifecycle_overrides(prd_text, prd_path=prd_relative_path)
    except ValueError as exc:
        _logger.warning(
            "Issue #%d: invalid lifecycle_agents block in '%s': %s",
            issue.number,
            prd_file_path,
            exc,
        )
        return issue
    if not overrides:
        return issue
    return dataclasses.replace(issue, lifecycle_overrides=tuple(sorted(overrides.items())))


def resolve_lifecycle_agent(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    selected_agent: str | None = None,
    override_agent: str = "auto",
    prd_overrides: Mapping[str, str] | None = None,
) -> str:
    """解析某个生命周期阶段实际使用的 agent。

    Args:
        lifecycle: 九个生命周期键之一。
        config: 已按"仓库层 > 全局层"合并好的应用配置。
        issue: 当前 Issue；``auto`` 标签路由（实现 / 辩论）需要。
        selected_agent: 实现阶段选中的 agent；``executor`` 与多数 ``auto``
            语义需要它。
        override_agent: 命令行 ``--agent`` 覆盖；``auto`` 表示未指定。
        prd_overrides: PRD 文件头部覆盖（最高优先级）。

    Returns:
        该阶段使用的 agent 名。

    Raises:
        ValueError: 生命周期键非法，或矩阵 / 覆盖里的取值违反阶段约束。
        UnknownAgentError: 解析结果是未注册的 agent 名（fail-fast）。
    """
    if lifecycle not in LIFECYCLE_AGENT_KEYS:
        raise ValueError(
            f"Unknown lifecycle key '{lifecycle}'. Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}."
        )

    merged_prd_overrides = effective_prd_overrides(issue, prd_overrides)
    if lifecycle in merged_prd_overrides:
        return _materialize(
            lifecycle,
            merged_prd_overrides[lifecycle],
            source=LIFECYCLE_SOURCE_PRD_OVERRIDE,
            config=config,
            issue=issue,
            selected_agent=selected_agent,
            override_agent=override_agent,
        )

    declared_value = config.lifecycle_agents.declared_value(lifecycle)
    if declared_value is not None:
        return _materialize(
            lifecycle,
            declared_value,
            source=config.lifecycle_agents.declared_layer(lifecycle) or LIFECYCLE_SOURCE_LEGACY,
            config=config,
            issue=issue,
            selected_agent=selected_agent,
            override_agent=override_agent,
        )

    return _materialize(
        lifecycle,
        legacy_configured_agent(lifecycle, config),
        source=LIFECYCLE_SOURCE_LEGACY,
        config=config,
        issue=issue,
        selected_agent=selected_agent,
        override_agent=override_agent,
    )


def _materialize(
    lifecycle: str,
    raw_value: str,
    *,
    source: str,
    config: AppConfig,
    issue: IssueSummary | None,
    selected_agent: str | None,
    override_agent: str,
) -> str:
    """把一个原始取值（``auto`` / ``executor`` / agent 名）解析成具体 agent。"""
    normalized = normalize_lifecycle_agent_value(raw_value)
    if normalized == LIFECYCLE_AGENT_EXECUTOR:
        if lifecycle not in LIFECYCLE_AGENT_EXECUTOR_KEYS:
            raise ValueError(
                f"lifecycle '{lifecycle}' (source={source}) resolves to 'executor', which is "
                f"only valid for {', '.join(sorted(LIFECYCLE_AGENT_EXECUTOR_KEYS))}."
            )
        if not selected_agent:
            raise ValueError(
                f"lifecycle '{lifecycle}' (source={source}) resolves to 'executor' but no "
                "implementation agent is available in this context."
            )
        return selected_agent
    if normalized == LIFECYCLE_AGENT_AUTO:
        if lifecycle not in LIFECYCLE_AGENT_AUTO_KEYS:
            # 这些阶段没有 auto 语义；历史配置键写成 auto 时收敛到内置默认
            # （与 generated_content 的 _DEFAULT_AUTO_AGENT 一致）。
            return _validate_registered(
                LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
            )
        return _resolve_auto(
            lifecycle,
            config,
            issue=issue,
            selected_agent=selected_agent,
            override_agent=override_agent,
        )
    return _validate_registered(normalized, lifecycle=lifecycle, config=config)


def _validate_registered(agent_name: str, *, lifecycle: str, config: AppConfig) -> str:
    """确认 agent 已注册；未注册时抛 :class:`UnknownAgentError`（带阶段名）。"""
    from backend.core.use_cases.agent_invocation import (
        UnknownAgentError,
        resolve_registered_agents,
    )

    if agent_name not in config.agents:
        raise UnknownAgentError(
            f"lifecycle '{lifecycle}' resolved to agent '{agent_name}', which is not registered. "
            f"Registered agents: {', '.join(resolve_registered_agents(config))}. "
            "Fix the value in [agent_runner.lifecycle_agents] / the PRD lifecycle_agents block, "
            f"or register an [agent_runner.agents.{agent_name}] block."
        )
    return agent_name


def _resolve_auto(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None,
    selected_agent: str | None,
    override_agent: str,
) -> str:
    """按该阶段的**既有**语义解析 ``auto``。

    能委托的一律委托既有函数（实现走 :func:`choose_agent`、审核 /
    监督走 :func:`resolve_reviewer_agent` / :func:`resolve_supervisor_agent`），
    只在"矩阵声明 auto 但既有配置键不是 auto"这一条路径上就地实现同一套语义，
    绝不把各阶段统一成标签路由。
    """
    # Local import breaks the run_agent_once <-> resolver cycle.
    from backend.core.use_cases.run_agent_once import (
        choose_agent,
        resolve_reviewer_agent,
        resolve_supervisor_agent,
    )

    if lifecycle == "implementation":
        if issue is None:
            if override_agent != LIFECYCLE_AGENT_AUTO:
                return _validate_registered(override_agent, lifecycle=lifecycle, config=config)
            default_agent = config.runner.default_agent
            return _validate_registered(
                default_agent
                if default_agent != LIFECYCLE_AGENT_AUTO
                else LIFECYCLE_AGENT_BUILTIN_DEFAULT,
                lifecycle=lifecycle,
                config=config,
            )
        return choose_agent(issue, config, override_agent)

    if lifecycle == "verifier":
        # 与 run_verifier_agent._choose_verifier_agent 的 auto 分支同语义：
        # 从回退链里挑第一个 ≠ 实现者，都没有再退回实现者。
        for candidate_agent in config.runner.agent_fallback_order:
            if candidate_agent != selected_agent:
                return _validate_registered(candidate_agent, lifecycle=lifecycle, config=config)
        if selected_agent:
            return _validate_registered(selected_agent, lifecycle=lifecycle, config=config)
        return _validate_registered(
            LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
        )

    if lifecycle == "review":
        if config.pre_pr_review.review_agent == LIFECYCLE_AGENT_AUTO and issue is not None:
            return resolve_reviewer_agent(
                issue, config, selected_agent or LIFECYCLE_AGENT_BUILTIN_DEFAULT
            )
        # 矩阵显式声明 auto 但既有键是具体 agent：按 allow_same_agent 语义就地解析。
        if selected_agent and config.pre_pr_review.allow_same_agent:
            return _validate_registered(selected_agent, lifecycle=lifecycle, config=config)
        for registered_agent in config.agents:
            if registered_agent != selected_agent:
                return _validate_registered(registered_agent, lifecycle=lifecycle, config=config)
        return _validate_registered(
            selected_agent or LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
        )

    if lifecycle == "supervisor":
        if config.post_pr_supervisor.supervisor_agent == LIFECYCLE_AGENT_AUTO and issue is not None:
            return resolve_supervisor_agent(
                issue, config, override_agent, fallback_agent=selected_agent
            )
        if selected_agent:
            return _validate_registered(selected_agent, lifecycle=lifecycle, config=config)
        if issue is not None:
            return choose_agent(issue, config, LIFECYCLE_AGENT_AUTO)
        return _validate_registered(
            LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
        )

    if lifecycle == "deliberate":
        if issue is not None:
            return choose_agent(issue, config, LIFECYCLE_AGENT_AUTO)
        return _validate_registered(
            LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
        )

    return _validate_registered(LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config)


__all__ = [
    "legacy_configured_agent",
    "parse_prd_lifecycle_overrides",
    "render_prd_lifecycle_overrides_block",
    "resolve_lifecycle_agent",
    "upsert_prd_lifecycle_overrides",
]

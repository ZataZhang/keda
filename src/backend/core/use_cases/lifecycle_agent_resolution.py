"""生命周期 Agent 解析：把"阶段 -> agent"从散落配置收敛为一个命名函数。

优先级（高到低）：

1. PRD 文件头部 ``lifecycle_agents`` 覆盖块（PRD 级，只影响该 PRD）；
2. ``[agent_runner.lifecycle_agents]`` 仓库层（``.kedacode.toml``）；
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

矩阵声明之外还有两层更高的显式选择：命令行 ``--agent``（最高）与 Console
「换执行器续跑」的一次性显式执行器覆盖（:func:`explicit_executor_override_agent`，
高于阶段预设绑定）——后者的存在理由见该函数 docstring。

本模块刻意不重写各阶段的 ``auto`` 语义：``auto`` 一律按该阶段**既有**语义路由
（实现走 ``choose_agent`` 的标签路由；校验 / 审核 / 监督先在既有配置键里取具体
agent、键本身也是 ``auto`` 时才展开成回退链 / 不同人优先 / 沿用实现者；辩论走标签
路由）。能委托给既有函数的一律委托（``choose_agent`` / ``resolve_reviewer_agent`` /
``resolve_supervisor_agent``），只有"既有配置键也是 auto、缺少委托所需的 Issue
上下文"这条路径才就地展开同一套语义。这样 console 呈递的生效值与 runner 实际
使用的是同一套判定。
"""

from __future__ import annotations

from backend.core.use_cases.issue_workspace import resolve_issue_branch

import dataclasses
import logging
import re
from collections.abc import Mapping
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_model_preset import ModelSelection, resolve_model_selection
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_AUTO,
    LIFECYCLE_AGENT_AUTO_KEYS,
    LIFECYCLE_AGENT_BUILTIN_DEFAULT,
    LIFECYCLE_AGENT_EXECUTOR,
    LIFECYCLE_AGENT_EXECUTOR_KEYS,
    LIFECYCLE_AGENT_KEYS,
    LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS,
    LIFECYCLE_SOURCE_LEGACY,
    LIFECYCLE_SOURCE_PRD_OVERRIDE,
    normalize_lifecycle_agent_value,
)
from backend.core.use_cases.agent_candidate_fallback import effective_fallback_candidates

_logger = logging.getLogger(__name__)

#: PRD 头部覆盖块名闭集：``lifecycle_agents``（阶段 -> agent）与
#: ``lifecycle_presets``（阶段 -> 预设，本 PRD 新增）。两个块共用同一套
#: 头部 bullet 区解析骨架，仅取值校验不同。
PRD_OVERRIDE_BLOCK_AGENTS = "lifecycle_agents"
PRD_OVERRIDE_BLOCK_PRESETS = "lifecycle_presets"

_PRD_OVERRIDE_BLOCK_NAMES: tuple[str, ...] = (PRD_OVERRIDE_BLOCK_AGENTS, PRD_OVERRIDE_BLOCK_PRESETS)


def _prd_block_pattern(block_name: str) -> re.Pattern[str]:
    """构建指定块名的头部块匹配模式（整行 ``- <block_name>:``）。"""
    return re.compile(rf"^\s*[-*]\s*{re.escape(block_name)}\s*:\s*$", re.IGNORECASE)


_PRD_OVERRIDE_BLOCK_PATTERNS: dict[str, re.Pattern[str]] = {
    block_name: _prd_block_pattern(block_name) for block_name in _PRD_OVERRIDE_BLOCK_NAMES
}
# 取值允许为空：空值交给 :func:`_validate_prd_override_entry` 显式报错，而不是因为
# 模式匹配失败把整块解析提前截断（那会静默丢掉块里后续的条目）。
_PRD_OVERRIDE_ENTRY_PATTERN = re.compile(
    r"^\s+[-*]\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.*?)\s*$"
)


def parse_prd_lifecycle_overrides(
    prd_text: str,
    *,
    prd_path: str | Path | None = None,
    block_name: str = PRD_OVERRIDE_BLOCK_AGENTS,
) -> dict[str, str]:
    """解析 PRD markdown 头部的生命周期覆盖块。

    覆盖块形如（落在标题下的 bullet 区，与 §8 依赖声明的嵌套 bullet 同型）::

        - lifecycle_agents:
          - implementation: claude
          - review: codex

    ``block_name`` 参数化块名：``lifecycle_agents``（阶段 -> agent）或
    ``lifecycle_presets``（阶段 -> 预设名）。只扫描**头部 bullet 区**
    （H1 之后到第一个非 bullet 行之前，见 :func:`_prd_header_bounds`）——
    正文里引用该语法的段落不会被误当成覆盖块。

    未知键名、非法取值、空值都立即报错并带上 PRD 路径，不静默忽略。

    Args:
        prd_text: PRD markdown 全文。
        prd_path: 报错信息里引用的 PRD 路径（可选）。
        block_name: 块名（``lifecycle_agents`` | ``lifecycle_presets``）。

    Returns:
        生命周期键 -> 覆盖值；没有覆盖块时返回空 dict。

    Raises:
        ValueError: 覆盖块里的键名或取值非法，或块名不在闭集内。
    """
    lines = prd_text.splitlines()
    header_start, header_end = _prd_header_bounds(lines)
    overrides: dict[str, str] = {}
    in_block = False
    location_suffix = f" (PRD: {prd_path})" if prd_path is not None else ""
    block_pattern = _require_block_pattern(block_name)
    for raw_line in lines[header_start:header_end]:
        if not in_block:
            if block_pattern.match(raw_line):
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
        _validate_prd_override_entry(
            lifecycle_key,
            raw_value,
            location_suffix=location_suffix,
            block_name=block_name,
        )
        overrides[lifecycle_key] = _normalize_prd_override_value(block_name, raw_value)
    return overrides


def render_prd_lifecycle_overrides_block(
    overrides: Mapping[str, str],
    *,
    block_name: str = PRD_OVERRIDE_BLOCK_AGENTS,
) -> list[str]:
    """把覆盖映射渲染成 PRD 头部 bullet 块的行列表。

    条目按 :data:`LIFECYCLE_AGENT_KEYS` 的固定顺序输出，保证同样的覆盖集合
    总是产生逐字节相同的文本（避免 UI 反复保存导致无意义 diff）。
    """
    _require_block_pattern(block_name)
    block_lines = [f"- {block_name}:"]
    for lifecycle_key in LIFECYCLE_AGENT_KEYS:
        if lifecycle_key in overrides:
            block_lines.append(f"  - {lifecycle_key}: {overrides[lifecycle_key]}")
    return block_lines


def upsert_prd_lifecycle_overrides(
    prd_text: str,
    overrides: Mapping[str, str],
    *,
    block_name: str = PRD_OVERRIDE_BLOCK_AGENTS,
) -> str:
    """写入 / 替换 PRD 头部的生命周期覆盖块，保留其余内容。

    ``overrides`` 是**完整的期望集合**（不是增量）：块被整体重写，块之外的
    头部内容与正文一律不动。空集合表示删除该块。只识别头部 bullet 区里的块
    （与 :func:`parse_prd_lifecycle_overrides` 同一区域），正文内容不会被改写。
    原文件的换行风格（LF / CRLF）保持不变。

    Args:
        prd_text: PRD markdown 全文。
        overrides: 生命周期键 -> 覆盖值（完整期望集合）。
        block_name: 块名（``lifecycle_agents`` | ``lifecycle_presets``）。

    Returns:
        写回后的 PRD 全文。

    Raises:
        ValueError: 键名或取值非法，或块名不在闭集内。
    """
    block_pattern = _require_block_pattern(block_name)
    for lifecycle_key, raw_value in overrides.items():
        _validate_prd_override_entry(
            lifecycle_key,
            raw_value,
            location_suffix="",
            block_name=block_name,
        )
    normalized_overrides = {
        lifecycle_key: _normalize_prd_override_value(block_name, raw_value)
        for lifecycle_key, raw_value in overrides.items()
    }
    new_block = (
        render_prd_lifecycle_overrides_block(normalized_overrides, block_name=block_name)
        if normalized_overrides
        else []
    )
    lines = prd_text.splitlines()
    header_start, header_end = _prd_header_bounds(lines)

    block_start: int | None = None
    block_end: int | None = None
    for index in range(header_start, header_end):
        if block_pattern.match(lines[index]):
            block_start = index
            block_end = index + 1
            while block_end < header_end and _PRD_OVERRIDE_ENTRY_PATTERN.match(lines[block_end]):
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
        insert_at = header_end
        updated_lines = [*lines[:insert_at], *new_block, "", *lines[insert_at:]]
    else:
        updated_lines = lines

    line_ending = "\r\n" if "\r\n" in prd_text else "\n"
    trailing_newline = line_ending if prd_text.endswith("\n") else ""
    return line_ending.join(updated_lines) + trailing_newline


def _prd_header_bounds(lines: list[str]) -> tuple[int, int]:
    """返回 PRD 头部 bullet 区的行号区间 ``[start, end)``。

    从 H1 标题（缺失时按第 0 行）之后推进，跳过空行与顶层 bullet（如
    ``- GitHub Issue:``），落在第一个非 bullet 行（通常是引用块或章节标题）之前。
    覆盖块的读、写都限制在该区间内，正文里的同名 bullet 不会被误认。
    """
    title_index = 0
    for index, raw_line in enumerate(lines):
        if raw_line.startswith("# "):
            title_index = index
            break
    start = title_index + 1
    end = start
    while end < len(lines):
        stripped = lines[end].lstrip()
        if not stripped or stripped.startswith(("-", "*")):
            end += 1
            continue
        break
    return start, end


def _require_block_pattern(block_name: str) -> re.Pattern[str]:
    """返回块名的匹配模式；块名不在闭集时抛 ``ValueError``。"""
    block_pattern = _PRD_OVERRIDE_BLOCK_PATTERNS.get(block_name)
    if block_pattern is None:
        raise ValueError(
            f"Unknown PRD override block name '{block_name}'. "
            f"Valid block names: {', '.join(_PRD_OVERRIDE_BLOCK_NAMES)}."
        )
    return block_pattern


def _normalize_prd_override_value(block_name: str, raw_value: str) -> str:
    """按块名规范化条目取值。

    ``lifecycle_agents`` 的取值域是 ``auto`` / ``executor`` / agent 名，
    沿用矩阵的规范化（去空白、小写）；``lifecycle_presets`` 的取值是预设名
    （自由命名空间，区分大小写），只去首尾空白。
    """
    if block_name == PRD_OVERRIDE_BLOCK_PRESETS:
        return raw_value.strip()
    return normalize_lifecycle_agent_value(raw_value)


def _validate_prd_override_entry(
    lifecycle_key: str,
    raw_value: str,
    *,
    location_suffix: str,
    block_name: str,
) -> None:
    """校验单条 PRD 覆盖条目，非法时抛 ``ValueError``。"""
    valid_keys = (
        LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS
        if block_name == PRD_OVERRIDE_BLOCK_PRESETS
        else LIFECYCLE_AGENT_KEYS
    )
    if lifecycle_key not in valid_keys:
        raise ValueError(
            f"PRD {block_name}: unknown lifecycle key '{lifecycle_key}'"
            f"{location_suffix}. Valid keys: {', '.join(valid_keys)}."
        )
    normalized = normalize_lifecycle_agent_value(raw_value)
    if not normalized:
        raise ValueError(
            f"PRD {block_name}.{lifecycle_key}: value must not be empty{location_suffix}."
        )
    if block_name == PRD_OVERRIDE_BLOCK_PRESETS:
        # 预设名是否存在留给解析期校验（与 agent 注册同口径），这里只校验形状。
        return
    if (
        normalized == LIFECYCLE_AGENT_EXECUTOR
        and lifecycle_key not in LIFECYCLE_AGENT_EXECUTOR_KEYS
    ):
        raise ValueError(
            f"PRD {block_name}.{lifecycle_key}: 'executor' is only valid for "
            f"{', '.join(sorted(LIFECYCLE_AGENT_EXECUTOR_KEYS))}{location_suffix}."
        )
    if normalized == LIFECYCLE_AGENT_AUTO and lifecycle_key not in LIFECYCLE_AGENT_AUTO_KEYS:
        raise ValueError(
            f"PRD {block_name}.{lifecycle_key}: 'auto' is not a valid value for this "
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


def explicit_executor_override_agent(
    issue: IssueSummary | None,
    lifecycle: str,
    *,
    config: AppConfig,
) -> str | None:
    """返回该 Issue 携带的**一次性显式执行器覆盖**（Console「换执行器续跑」）。

    这层覆盖是针对单个 Issue、单个阶段的一次性显式选择（用户在 Console 为
    失败阶段挑的执行器），优先级**高于**阶段预设绑定与生命周期矩阵声明、
    **低于**命令行 ``--agent``。必须独立成层：阶段预设绑定整体决定该阶段
    （agent + 模型 + 推理档），若把它投递成矩阵声明，预设绑定会静默遮蔽这层
    选择——用户选了 mcode，Runner 仍按预设的 codex 跑，"换执行器续跑"形同虚设。

    Args:
        issue: 当前 Issue；``None`` 表示无上下文。
        lifecycle: 生命周期键。
        config: 已按"仓库层 > 全局层"合并好的应用配置（校验 agent 是否注册）。

    Returns:
        该阶段被显式覆盖的 agent 名；未覆盖时返回 ``None``。

    Raises:
        UnknownAgentError: 覆盖值不是已注册 agent（fail-fast，不静默回落）。
    """
    if issue is None:
        return None
    explicit_overrides = dict(getattr(issue, "explicit_executor_overrides", ()) or ())
    overridden_agent = explicit_overrides.get(lifecycle)
    if overridden_agent is None:
        return None
    return _validate_registered(overridden_agent, lifecycle=lifecycle, config=config)


def effective_prd_preset_overrides(
    issue: IssueSummary | None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """合并「Issue 携带的 PRD ``lifecycle_presets`` 块」与调用方显式传入的绑定。

    与 :func:`effective_prd_overrides` 同机制：Issue 上的
    ``lifecycle_preset_overrides`` 由编排入口随 PRD 解析回填；调用方显式传入
    （CLI ``--preset`` 锚定阶段的虚拟绑定）优先于 Issue 携带的版本。

    Args:
        issue: 当前 Issue；``None`` 表示无上下文。
        prd_preset_overrides: 调用方显式传入的阶段 -> 预设名绑定。

    Returns:
        合并后的 PRD 级预设绑定；两者都为空时返回空 dict。
    """
    merged_bindings: dict[str, str] = {}
    if issue is not None:
        merged_bindings.update(dict(getattr(issue, "lifecycle_preset_overrides", ()) or ()))
    if prd_preset_overrides:
        merged_bindings.update(prd_preset_overrides)
    return merged_bindings


def declared_lifecycle_preset(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> str | None:
    """返回该阶段显式绑定的预设名（PRD 块 > 仓库层 > 全局层）；无绑定返回 ``None``。

    Raises:
        ValueError: 生命周期键非法。
    """
    if lifecycle not in LIFECYCLE_AGENT_KEYS:
        raise ValueError(
            f"Unknown lifecycle key '{lifecycle}'. Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}."
        )
    merged_bindings = effective_prd_preset_overrides(issue, prd_preset_overrides)
    bound_preset = merged_bindings.get(lifecycle)
    if isinstance(bound_preset, str) and bound_preset:
        return bound_preset
    configured_binding = config.lifecycle_presets.declared_value(lifecycle)
    # isinstance 防御：测试替身（MagicMock config）的 attribute 链会返回非字符串，
    # 与"未声明"同口径处理，保证未配置路径零行为变化。
    if isinstance(configured_binding, str) and configured_binding:
        return configured_binding
    return None


def attach_prd_lifecycle_overrides(
    issue: IssueSummary,
    repo_path: Path | None,
    *,
    process_runner: IProcessRunner | None = None,
    git_remote: str = "origin",
) -> IssueSummary:
    """从该 Issue 引用的 PRD 文件解析头部覆盖，回填到 ``IssueSummary``。

    主检出 PRD 缺失或读取失败、PRD 覆盖块解析失败时，记录 warning 并原样返回该 Issue。
    如果 Issue 已发布到任务分支，则该分支是执行输入的权威来源；缺失、越出仓库或不是
    Markdown 的 PRD 锚点，以及获取或读取分支 PRD 失败，都会抛错阻止流水线继续，不能
    静默回退到主检出文件或默认执行器。成功读取后，同时解析 ``lifecycle_agents`` 与
    ``lifecycle_presets`` 两个块，分别回填到 ``lifecycle_overrides`` 与
    ``lifecycle_preset_overrides``。

    Args:
        issue: 待处理的 Issue。
        repo_path: 目标仓库主检出根（PRD 随仓库存在，worktree 尚未创建时也可读）。
        process_runner: 可选 Git 进程端口；主检出缺少 PRD 时从 ``issue-<N>`` 获取。
        git_remote: runner 配置的 Git remote 名称。

    Returns:
        回填了覆盖字段的 Issue；主检出 PRD 无覆盖、读取失败或覆盖块解析失败时返回原对象。
        已发布任务分支 PRD 的获取或读取失败会抛出异常；其覆盖块解析失败仍返回原对象。

    Raises:
        ValueError: 已发布 Issue 缺少有效的仓库内 Markdown PRD 锚点，或发布 marker 无效。
        RuntimeError: 已发布 Issue 缺少仓库根目录，无法读取其权威 PRD 来源。
    """
    # 局部导入：agent_runner_feedback 与解析函数分属不同子系统，避免模块级环。
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path
    from backend.core.use_cases.issue_prd_publication import (
        published_issue_prd_branch,
        read_published_issue_prd_text,
    )

    published_issue_branch = published_issue_prd_branch(
        issue_number=issue.number,
        issue_body=issue.body,
        issue_url=issue.url,
    )
    has_published_prd = published_issue_branch is not None
    prd_relative_path = extract_prd_path(issue.body)
    if prd_relative_path is None:
        if has_published_prd:
            raise ValueError(
                f"Issue #{issue.number} has a published PRD marker but no valid PRD path anchor."
            )
        return issue
    if repo_path is None:
        if has_published_prd:
            raise RuntimeError(
                f"Issue #{issue.number} has a published PRD but no repository root is available."
            )
        return issue
    resolved_repo_path = repo_path.resolve()
    prd_file_path = (resolved_repo_path / prd_relative_path).resolve()
    try:
        prd_relative_to_repo = prd_file_path.relative_to(resolved_repo_path)
    except ValueError:
        if has_published_prd:
            raise ValueError(
                f"Issue #{issue.number} has a published PRD path outside repository "
                f"'{resolved_repo_path}': '{prd_relative_path}'."
            )
        _logger.warning(
            "Issue #%d: PRD path '%s' is outside repository '%s'; skipping lifecycle overrides.",
            issue.number,
            prd_relative_path,
            resolved_repo_path,
        )
        return issue
    if prd_relative_to_repo.suffix.lower() != ".md":
        if has_published_prd:
            raise ValueError(
                f"Issue #{issue.number} has a published PRD path that is not a Markdown file: "
                f"'{prd_relative_path}'."
            )
        _logger.warning(
            "Issue #%d: PRD path '%s' is not a Markdown file; skipping lifecycle overrides.",
            issue.number,
            prd_relative_path,
        )
        return issue
    published_branch_prd_text: str | None = None
    if process_runner is not None:
        # 新发布协议把已发布 Issue 分支作为执行输入的权威来源；即使主检出也有同名
        # 文件，仍先读远端，避免执行器覆盖来自另一份较旧或较新的本地副本。
        try:
            published_branch_prd_text = read_published_issue_prd_text(
                issue=issue,
                repo_path=resolved_repo_path,
                git_remote=git_remote,
                process_runner=process_runner,
            )
        except (RuntimeError, ValueError) as exc:
            _logger.warning(
                "Issue #%d: could not read its published PRD branch for lifecycle overrides: %s",
                issue.number,
                exc,
            )
            raise
    if published_branch_prd_text is not None:
        prd_text = published_branch_prd_text
    elif prd_file_path.is_file():
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
    elif process_runner is not None:
        issue_branch = resolve_issue_branch(resolved_repo_path, issue)
        fetch_result = process_runner.run(
            [
                "git",
                "fetch",
                git_remote,
                f"+{issue_branch}:refs/remotes/{git_remote}/{issue_branch}",
            ],
            cwd=resolved_repo_path,
            check=False,
        )
        if fetch_result.return_code != 0:
            _logger.warning(
                "Issue #%d: could not fetch '%s/%s' to read PRD lifecycle overrides: %s",
                issue.number,
                git_remote,
                issue_branch,
                fetch_result.stderr.strip(),
            )
            return issue
        show_result = process_runner.run(
            [
                "git",
                "show",
                f"{git_remote}/{issue_branch}:{prd_relative_to_repo.as_posix()}",
            ],
            cwd=resolved_repo_path,
            check=False,
        )
        if show_result.return_code != 0:
            _logger.warning(
                "Issue #%d: could not read PRD '%s' from '%s/%s': %s",
                issue.number,
                prd_relative_path,
                git_remote,
                issue_branch,
                show_result.stderr.strip(),
            )
            return issue
        prd_text = show_result.stdout
    else:
        _logger.warning(
            "Issue #%d: PRD '%s' is missing from the main checkout and no Git process "
            "runner is available to read its task branch.",
            issue.number,
            prd_file_path,
        )
        return issue
    updated_fields: dict[str, object] = {}
    for block_name, field_name in (
        (PRD_OVERRIDE_BLOCK_AGENTS, "lifecycle_overrides"),
        (PRD_OVERRIDE_BLOCK_PRESETS, "lifecycle_preset_overrides"),
    ):
        try:
            block_overrides = parse_prd_lifecycle_overrides(
                prd_text, prd_path=prd_relative_path, block_name=block_name
            )
        except ValueError as exc:
            _logger.warning(
                "Issue #%d: invalid %s block in '%s': %s",
                issue.number,
                block_name,
                prd_file_path,
                exc,
            )
            return issue
        if block_overrides:
            updated_fields[field_name] = tuple(sorted(block_overrides.items()))
    if not updated_fields:
        return issue
    return dataclasses.replace(issue, **updated_fields)


def resolve_lifecycle_agent(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    selected_agent: str | None = None,
    override_agent: str = "auto",
    prd_overrides: Mapping[str, str] | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> str:
    """解析某个生命周期阶段实际使用的 agent。

    阶段绑定预设（PRD ``lifecycle_presets`` 块 > 仓库层 > 全局层）时，预设
    整体决定该阶段的 (agent, 模型, 推理档)——本函数返回预设声明的 agent
    （遮蔽矩阵同键声明）；显式 ``override_agent``（CLI ``--agent``）仍然最高，
    此时绑定让位、模型绑定由调用方按 agent 匹配规则丢弃。

    Args:
        lifecycle: 九个生命周期键之一。
        config: 已按"仓库层 > 全局层"合并好的应用配置。
        issue: 当前 Issue；``auto`` 标签路由（实现 / 辩论）与 PRD 级覆盖需要。
        selected_agent: 实现阶段选中的 agent；``executor`` 与多数 ``auto``
            语义需要它。
        override_agent: 命令行 ``--agent`` 覆盖；``auto`` 表示未指定。
        prd_overrides: PRD 文件头部 ``lifecycle_agents`` 块（agent 覆盖）。
        prd_preset_overrides: 显式传入的阶段 -> 预设名绑定（CLI ``--preset``
            锚定阶段的虚拟绑定也走这里），优先于 Issue 携带的 PRD 块。

    Returns:
        该阶段使用的 agent 名。

    Raises:
        ValueError: 生命周期键非法，或矩阵 / 覆盖 / 绑定里的取值违反阶段约束，
            或绑定的预设名未定义。
        UnknownAgentError: 解析结果是未注册的 agent 名（fail-fast）。
    """
    if lifecycle not in LIFECYCLE_AGENT_KEYS:
        raise ValueError(
            f"Unknown lifecycle key '{lifecycle}'. Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}."
        )

    # 显式 CLI --agent 最高：绑定让位（模型绑定由调用方按 agent 匹配规则丢弃）。
    if override_agent == LIFECYCLE_AGENT_AUTO:
        # 一次性显式执行器覆盖次之：高于预设绑定与矩阵声明（否则用户在 Console
        # 选定的执行器会被预设静默遮蔽）。
        explicit_agent = explicit_executor_override_agent(issue, lifecycle, config=config)
        if explicit_agent is not None:
            return explicit_agent
        bound_preset_name = declared_lifecycle_preset(
            lifecycle, config, issue=issue, prd_preset_overrides=prd_preset_overrides
        )
        if bound_preset_name is not None:
            bound_selection = resolve_model_selection(bound_preset_name, config)
            return _validate_registered(bound_selection.agent, lifecycle=lifecycle, config=config)

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


def resolve_lifecycle_model_selection(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
    model_override: str | None = None,
    effort_override: str | None = None,
) -> ModelSelection | None:
    """解析某个生命周期阶段的模型绑定；无绑定时返回 ``None``（零注入）。

    解析优先级（高到低）：显式 ``prd_preset_overrides``（CLI ``--preset``
    锚定阶段的虚拟绑定）> Issue 携带的 PRD ``lifecycle_presets`` 块 >
    仓库层 ``[agent_runner.lifecycle_presets]`` > 全局层。命中绑定时解析预设
    （命令行 ``--model`` / ``--reasoning-effort`` 覆盖同名字段），并校验预设
    声明的 agent 已注册。

    与 :func:`resolve_lifecycle_agent` 的协作约定：命中绑定的阶段，agent 解析
    也走绑定（预设声明的 agent）；调用方在**执行 agent 确定后**必须校验
    ``model_selection.agent == agent_name``，不等（显式 ``--agent`` 换人 /
    回退）则置 ``None`` 并记日志，绝不把 A CLI 的模型参数塞给 B CLI。

    Args:
        lifecycle: 九个生命周期键之一。
        config: 已按"仓库层 > 全局层"合并好的应用配置。
        issue: 当前 Issue（PRD 级绑定随它流动）。
        prd_preset_overrides: 显式传入的阶段 -> 预设名绑定。
        model_override: 命令行 ``--model`` 覆盖；``None`` 表示未覆盖。
        effort_override: 命令行 ``--reasoning-effort`` 覆盖；同上。

    Returns:
        解析后的 :class:`ModelSelection`；该阶段无绑定时返回 ``None``。

    Raises:
        ValueError: 生命周期键非法，或绑定的预设名未定义（fail-fast）。
        UnknownAgentError: 预设声明的 agent 未注册。
    """
    bound_preset_name = declared_lifecycle_preset(
        lifecycle, config, issue=issue, prd_preset_overrides=prd_preset_overrides
    )
    if bound_preset_name is None:
        return None
    model_selection = resolve_model_selection(
        bound_preset_name,
        config,
        model_override=model_override,
        effort_override=effort_override,
    )
    _validate_registered(model_selection.agent, lifecycle=lifecycle, config=config)
    return model_selection


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

    能委托的一律委托既有函数（实现走 :func:`choose_agent`，审核 / 监督走
    :func:`resolve_reviewer_agent` / :func:`resolve_supervisor_agent`）；校验没有
    可委托的函数，就地按 ``_choose_verifier_agent`` 的顺序展开。共同点：既有配置键
    配成**具体 agent** 时一律先用它——``auto`` 表示"沿用既有语义"，不是"跳过既有键"，
    否则 console 呈递的生效值会与 runner 实际取值分叉。绝不把各阶段统一成标签路由。
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
        # 与 run_verifier_agent._choose_verifier_agent 同语义：既有键配成具体 agent
        # 时先用它（矩阵声明 auto 表示"沿用既有语义"，不是"跳过既有键"）。
        legacy_verifier_agent = config.validation.verifier_agent
        if (
            legacy_verifier_agent
            and normalize_lifecycle_agent_value(legacy_verifier_agent) != LIFECYCLE_AGENT_AUTO
        ):
            return _validate_registered(legacy_verifier_agent, lifecycle=lifecycle, config=config)
        # 否则从有效回退链里挑第一个 ≠ 实现者，都没有再退回实现者。
        # 迭代来源与 run_verifier_agent._choose_verifier_agent 同口径：
        # effective_fallback_candidates（候选数组非空时整体接管，空数组把旧名单
        # 折叠为无预设候选）；直接读 agent_fallback_order 会在候选数组写后
        # 读到 stale 顺序。每个候选仍逐个过 _validate_registered 注册校验。
        for fallback_candidate in effective_fallback_candidates(config):
            candidate_agent = fallback_candidate.agent
            if candidate_agent != selected_agent:
                return _validate_registered(candidate_agent, lifecycle=lifecycle, config=config)
        if selected_agent:
            return _validate_registered(selected_agent, lifecycle=lifecycle, config=config)
        return _validate_registered(
            LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
        )

    if lifecycle == "review":
        # 同 resolve_reviewer_agent：既有键配成具体 agent 时先用它。
        legacy_review_agent = config.pre_pr_review.review_agent
        if (
            legacy_review_agent
            and normalize_lifecycle_agent_value(legacy_review_agent) != LIFECYCLE_AGENT_AUTO
        ):
            return _validate_registered(legacy_review_agent, lifecycle=lifecycle, config=config)
        if issue is not None:
            return resolve_reviewer_agent(
                issue, config, selected_agent or LIFECYCLE_AGENT_BUILTIN_DEFAULT
            )
        # 既有键也是 auto 且没有 Issue 上下文：按 allow_same_agent 语义就地解析。
        if selected_agent and config.pre_pr_review.allow_same_agent:
            return _validate_registered(selected_agent, lifecycle=lifecycle, config=config)
        for registered_agent in config.agents:
            if registered_agent != selected_agent:
                return _validate_registered(registered_agent, lifecycle=lifecycle, config=config)
        return _validate_registered(
            selected_agent or LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
        )

    if lifecycle == "supervisor":
        # 同 resolve_supervisor_agent：既有键配成具体 agent 时先用它。
        legacy_supervisor_agent = config.post_pr_supervisor.supervisor_agent
        if (
            legacy_supervisor_agent
            and normalize_lifecycle_agent_value(legacy_supervisor_agent) != LIFECYCLE_AGENT_AUTO
        ):
            return _validate_registered(legacy_supervisor_agent, lifecycle=lifecycle, config=config)
        if issue is not None:
            return resolve_supervisor_agent(
                issue, config, override_agent, fallback_agent=selected_agent
            )
        if selected_agent:
            return _validate_registered(selected_agent, lifecycle=lifecycle, config=config)
        return _validate_registered(
            LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
        )

    if lifecycle == "deliberate":
        if issue is not None:
            # 辩论阶段按标签路由；显式声明 lifecycle="deliberate" 以免
            # implementation 阶段的一次性执行器覆盖串味到这里。
            return choose_agent(
                issue,
                config,
                LIFECYCLE_AGENT_AUTO,
                lifecycle="deliberate",
            )
        return _validate_registered(
            LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config
        )

    return _validate_registered(LIFECYCLE_AGENT_BUILTIN_DEFAULT, lifecycle=lifecycle, config=config)


__all__ = [
    "PRD_OVERRIDE_BLOCK_AGENTS",
    "PRD_OVERRIDE_BLOCK_PRESETS",
    "attach_prd_lifecycle_overrides",
    "declared_lifecycle_preset",
    "effective_prd_overrides",
    "effective_prd_preset_overrides",
    "legacy_configured_agent",
    "parse_prd_lifecycle_overrides",
    "render_prd_lifecycle_overrides_block",
    "resolve_lifecycle_agent",
    "resolve_lifecycle_model_selection",
    "upsert_prd_lifecycle_overrides",
]

"""为 GitHub Issues 和 PRs 生成内容。

本模块提供 AI 辅助的内容生成能力，支持两种目标：

1. **Issue 内容生成**（``generate_issue_content``）：根据 PRD 上下文生成
   Issue 标题和正文，用于 ``kc issue create`` 工作流。
2. **PR 内容生成**（``generate_pr_content``）：根据 Issue 信息、提交日志和
   diff 统计生成 PR 标题和正文，用于 ``publish_changes`` 工作流。

PRD 生成在 :mod:`backend.core.use_cases.generated_prd_content`，复用本模块的
渲染、截断与 agent 运行 helper。

两条路径都遵循相同的级联策略：

- **Agent 模式**（默认）：调用本地 AI agent（如 Claude/Codex/Kimi）生成内容。
  agent 缺 prompt、超时、不可执行或输出不合格都不会抛出，而是落到下一级。
- **Template 渲染**：使用 ``.format()`` 模板渲染，变量来自上下文对象。它是 agent
  失败后的中间兜底（``config.fallback == "template"``）；``mode = "template"`` 已废弃，
  只表示跳过 agent 这一步。
- **Hard fallback**：当上述方式均失败或禁用时，返回调用方提供的 fallback 内容。

所有模板渲染通过 ``_render_template`` 统一处理，支持 ``IssueContext``
和 ``PrContext`` 两种上下文对象。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IProcessRunner,
)
from backend.core.shared.markdown_code_fence import strip_code_fences
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import (
    GeneratedContentConfig,
    GeneratedContentTargetConfig,
    GeneratedIssueContent,
    GeneratedPrContent,
)
from backend.core.shared.models.agent_spec import AGENT_PROFILE_GENERATE
from backend.core.use_cases.agent_invocation_tracing import (
    PHASE_CONTENT_GENERATION,
    InvocationStartRequest,
    finish_invocation,
    start_invocation,
)
from backend.core.use_cases.repository_content_templates import (
    append_repository_template_context,
)

_logger = logging.getLogger(__name__)

# GitHub Issue 标题的最大长度限制。超过此长度会被截断并追加 "..."
_MAX_TITLE_LENGTH = 256

# 整行只有 GitHub closing keyword + Issue 引用（如 ``Closes #42``）是正文锚点，不是标题。
_CLOSING_REFERENCE_TITLE = re.compile(
    r"(?:close|fix|resolve)(?:e?[sd])?:?\s*#\d+\.?", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# 上下文对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssueContext:
    """Issue 内容生成的上下文变量。

    这些字段会被模板字符串通过 ``.format(**context.__dict__)`` 引用，
    因此字段名称直接对应模板中的占位符名（如 ``{prd_introduction}``）。

    Attributes:
        issue_type: Issue 类型，如 ``"feature"``、``"bug"``。
        title: fallback 标题（通常由调用方从 PRD 文件名或 H1 提取）。
        prd_title: 从 PRD H1 标题中提取的纯净标题（去掉 ``PRD:`` 前缀）。
        relative_prd_path: PRD 文件相对于仓库根目录的路径（字符串形式）。
        acceptance_items: 验收清单条目，以换行符连接的 Markdown 复选框文本。
        prd_text: PRD 的完整原始文本，供 agent 模式作为完整上下文输入。
        prd_introduction: PRD 第一节（引言/概述）的内容。
        prd_goals: PRD 目标章节的内容。
        prd_requirement_shape: PRD 需求形态章节的内容。
        prd_change_impact_tree: PRD 变更影响树章节的内容。
    """

    issue_type: str
    title: str
    prd_title: str
    relative_prd_path: str
    acceptance_items: str
    prd_text: str
    prd_introduction: str
    prd_goals: str
    prd_requirement_shape: str
    prd_change_impact_tree: str


@dataclass(frozen=True)
class PrContext:
    """PR 内容生成的上下文变量。

    与 ``IssueContext`` 类似，字段名称直接作为模板占位符使用。

    Attributes:
        issue_number: 关联的 GitHub Issue 编号。
        issue_title: Issue 标题。
        issue_body: Issue 正文。
        branch: 当前工作分支名称。
        base_branch: 基础分支名称（如 ``main``）。
        commit_log: 从 ``base_branch`` 到 HEAD 的提交信息列表。
        commit_messages: ``commit_log`` 的别名，保持与旧模板兼容。
        diff_stat: ``git diff --stat`` 的输出，显示变更文件统计。
        git_diff_stat: ``diff_stat`` 的别名，保持与旧模板兼容。
    """

    issue_number: int
    issue_title: str
    issue_body: str
    branch: str
    base_branch: str
    commit_log: str
    commit_messages: str
    diff_stat: str
    git_diff_stat: str
    repo_url: str = ""


@dataclass(frozen=True)
class GeneratedPrdContent:
    """PRD 内容生成结果。

    Attributes:
        text: 生成的 PRD 完整 markdown 文本。
        source: 生成来源标识，如 ``"template"``、``"agent"`` 或 ``"fallback"``。
    """

    text: str
    source: str = "fallback"


@dataclass(frozen=True)
class PrdContext:
    """PRD 内容生成的上下文变量。

    字段名称直接作为模板占位符使用，与 ``IssueContext`` / ``PrContext`` 一致。

    Attributes:
        issue_number: Issue 编号。
        issue_title: Issue 标题。
        issue_body: Issue 正文。
        issue_comments: 所有评论拼接后的文本，每条评论前缀 ``Comment:\n``。
        existing_prd_text: 现有 PRD 文本（如果 Issue 已关联 PRD）。
        repo_structure_summary: 仓库顶层结构摘要，用于 agent 了解项目布局。
    """

    issue_number: int
    issue_title: str
    issue_body: str
    issue_comments: str
    existing_prd_text: str
    repo_structure_summary: str


# ---------------------------------------------------------------------------
# PRD 章节提取
# ---------------------------------------------------------------------------


def extract_prd_section(prd_text: str, section_keywords: tuple[str, ...]) -> str:
    """根据关键词从 PRD 中提取指定章节的内容。

    扫描逻辑：

    1. 逐行读取 PRD，寻找以 ``## `` 开头的 H2 标题。
    2. 将标题（去掉 ``## `` 后）转小写，检查是否包含任意一个关键词。
    3. 第一个匹配的 H2 标志着目标章节的开始；下一个 H2（或文件结束）标志着结束。
    4. 返回章节内所有行（不包括标题行本身），去除首尾空白。

    注意：本函数对中文 PRD 的兼容性依赖于 ``section_keywords`` 中是否包含
    中文关键词。例如引言章节需要 ``"引言"`` 或 ``"概述"`` 才能匹配
    ``## 1. 引言与目标``。

    Args:
        prd_text: PRD 的完整文本。
        section_keywords: 用于匹配章节标题的关键词元组。

    Returns:
        提取的章节内容字符串；未找到匹配章节时返回空字符串。
    """
    lines = prd_text.splitlines()
    in_section = False
    section_lines: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if in_section:
                # 遇到下一个 H2 标题，当前章节结束。
                break
            heading = line[3:].strip().lower()
            if any(keyword in heading for keyword in section_keywords):
                in_section = True
                continue
        if in_section:
            section_lines.append(line)
    return "\n".join(section_lines).strip()


def extract_first_h2_section(prd_text: str) -> str:
    """提取 PRD 中第一个 ``## `` 二级标题下的全部内容。

    当按关键词提取引言失败时，使用此函数作为兜底：无论第一节标题如何命名，
    都把它下面的内容取出来，避免 Issue fallback 正文出现空的 Summary。

    Args:
        prd_text: PRD 的完整文本。

    Returns:
        第一个 ``## `` 章节的内容；未找到 ``## `` 标题时返回空字符串。
    """
    lines = prd_text.splitlines()
    in_section = False
    section_lines: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            in_section = True
            continue
        if in_section:
            section_lines.append(line)
    return "\n".join(section_lines).strip()


# ---------------------------------------------------------------------------
# 上下文构建
# ---------------------------------------------------------------------------


def build_issue_context(
    *,
    issue_type: str,
    title: str,
    relative_prd_path: Path,
    prd_text: str,
    acceptance_items: list[str],
) -> IssueContext:
    """为 Issue 内容生成构建上下文变量。

    从 PRD 文本中提取多个章节（引言、目标、需求形态、变更影响），
    同时从 PRD 的 H1 标题中提取纯净标题作为 ``prd_title``。

    Args:
        issue_type: Issue 类型标识。
        title: fallback 标题。
        relative_prd_path: PRD 文件相对路径。
        prd_text: 完整 PRD 文本。
        acceptance_items: 验收清单条目列表。

    Returns:
        供模板渲染或 agent prompt 使用的 ``IssueContext`` 实例。
    """
    # 提取 PRD 的各个章节。关键词列表需覆盖中英文场景。
    introduction = extract_prd_section(prd_text, ("introduction", "intro", "引言", "概述"))
    if not introduction:
        introduction = extract_first_h2_section(prd_text)
    goals = extract_prd_section(prd_text, ("goal", "目标"))
    requirement_shape = extract_prd_section(prd_text, ("requirement", "需求", "shape"))
    change_impact_tree = extract_prd_section(prd_text, ("change impact", "impact tree", "变更影响"))

    # 从 PRD 的第一行 H1 标题中提取纯净标题，去掉 "PRD:" / "PRD：" 前缀。
    # 跳过 Part A / Part B 这类结构标题，避免把章节标题误当成 feature 标题。
    prd_title_text = title
    for line in prd_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            candidate = re.sub(r"^PRD[:：]\s*", "", stripped[2:]).strip()
            if candidate and not re.match(r"^Part\s+[AB]\b", candidate, re.IGNORECASE):
                prd_title_text = candidate
                break

    return IssueContext(
        issue_type=issue_type,
        title=title,
        prd_title=prd_title_text,
        relative_prd_path=relative_prd_path.as_posix(),
        acceptance_items="\n".join(acceptance_items),
        prd_text=prd_text,
        prd_introduction=introduction,
        prd_goals=goals,
        prd_requirement_shape=requirement_shape,
        prd_change_impact_tree=change_impact_tree,
    )


# ---------------------------------------------------------------------------
# 模板渲染辅助函数
# ---------------------------------------------------------------------------


def _render_template(template: str, context: IssueContext | PrContext | PrdContext) -> str:
    """使用上下文变量渲染模板字符串。

    直接调用 ``str.format(**context.__dict__)``，因此模板中的占位符必须与
    上下文对象的字段名完全一致（如 ``{prd_introduction}``、``{issue_number}``）。

    Args:
        template: 包含占位符的模板字符串。
        context: 提供变量值的上下文对象。

    Returns:
        渲染后的字符串。

    Raises:
        KeyError: 当模板中包含上下文对象不存在的占位符时。
        ValueError: 当模板格式不合法时。
    """
    return template.format(**context.__dict__)


def _try_render_templates(
    target: GeneratedContentTargetConfig,
    context: IssueContext | PrContext | PrdContext,
) -> tuple[str, str]:
    """尝试渲染标题和正文模板。

    分别渲染 ``title_template`` 和 ``body_template``。如果某个模板未配置
    或渲染失败（KeyError/ValueError），则对应结果为空字符串。

    Args:
        target: 包含 ``title_template`` 和 ``body_template`` 的目标配置。
        context: 提供变量值的上下文对象。

    Returns:
        ``(generated_title, generated_body)`` 元组。空字符串表示
        该模板缺失或渲染失败。
    """
    generated_title = ""
    generated_body = ""
    if target.title_template:
        try:
            generated_title = _render_template(target.title_template, context)
        except (KeyError, ValueError):
            pass
    if target.body_template:
        try:
            generated_body = _render_template(target.body_template, context)
        except (KeyError, ValueError):
            pass
    return generated_title, generated_body


def _truncate_text(text: str, max_chars: int) -> str:
    """将文本截断至指定长度，超出部分用省略号替代。

    用于控制 agent prompt 的输入长度，防止超过模型的上下文窗口限制。

    Args:
        text: 原始文本。
        max_chars: 最大允许字符数。

    Returns:
        截断后的文本。如果原始文本未超过限制则原样返回。
    """
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


# ---------------------------------------------------------------------------
# 内容校验
# ---------------------------------------------------------------------------


def _validate_issue_body(body: str, relative_prd_path: str) -> bool:
    """验证 Issue 正文是否包含必需的 PRD 路径锚点。

    Issue 正文必须包含 ``- PRD path: `{relative_prd_path}` `` 这一行，
    否则 runner 无法从 Issue 中反向定位到对应的 PRD 文件。

    Args:
        body: 待验证的 Issue 正文。
        relative_prd_path: 期望的 PRD 相对路径。

    Returns:
        包含锚点时返回 ``True``，否则返回 ``False``。
    """
    if not body or not body.strip():
        return False
    anchor = f"- PRD path: `{relative_prd_path}`"
    return anchor in body


def _validate_pr_body(body: str, issue_number: int) -> bool:
    """验证 PR 正文是否在可被 GitHub 识别的位置包含必需的 Closes 锚点。

    PR 正文必须包含 ``Closes #{issue_number}`` 形式的文本，
    以便 GitHub 在合并 PR 时自动关闭关联的 Issue。围栏代码块里的同一段文字
    只是示例，GitHub 不会据此建立 Issue 关联，因此不能算通过——否则正文会带着
    一个平台看不见的锚点发布出去，关联静默缺失。

    Args:
        body: 待验证的 PR 正文。
        issue_number: 期望关闭的 Issue 编号。

    Returns:
        锚点位于正文（非围栏内容）时返回 ``True``，否则返回 ``False``。
    """
    if not body or not body.strip():
        return False
    closes_pattern = rf"Closes\s*#\s*{issue_number}"
    return bool(re.search(closes_pattern, strip_code_fences(body)))


# ---------------------------------------------------------------------------
# Agent 调用与输出解析
# ---------------------------------------------------------------------------


# 当 target 与全局默认 agent 都为 "auto" 时收敛到的首选 agent。与
# run_agent_once.choose_agent 的最终兜底保持一致（auto -> claude），避免把 "auto"
# 透传到命令构造处被静默当作 codex。
_DEFAULT_AUTO_AGENT = "claude"


def _resolve_generation_agent(
    target_agent: str,
    default_agent: str,
    *,
    override_agent: str | None = None,
) -> str:
    """解析内容生成使用的具体 agent 名称。

    顺序：target 级显式 agent > 生命周期矩阵值（``override_agent``，来自
    ``lifecycle_agents.content_generation``）> 全局 ``default_agent``；都为
    ``"auto"`` 时收敛到 :data:`_DEFAULT_AUTO_AGENT`，不把 ``"auto"`` 透传到
    命令构造处被静默当作 codex。
    """
    if target_agent != "auto":
        return target_agent
    if override_agent is not None and override_agent != "auto":
        return override_agent
    if default_agent != "auto":
        return default_agent
    return _DEFAULT_AUTO_AGENT


def _run_content_generator(
    generator: IContentGenerator,
    agent_name: str,
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    model_selection: ModelSelection | None = None,
) -> str:
    """运行内容生成器并返回原始输出文本。

    prompt 为空（未配置也没有播种）、agent 进程返回非零退出码、超时或无法执行
    （如 CLI 未安装）时，记录警告日志并返回空字符串，让调用方回退到 template /
    fallback 内容——内容生成失败不能中断 Issue 创建或 PR 发布。

    Args:
        generator: 内容生成器接口实例。
        agent_name: 要使用的 agent 名称（如 ``"claude"``、``"kimi"``）。
        prompt: 发送给 agent 的完整 prompt 文本。
        cwd: agent 工作目录。
        timeout_seconds: agent 执行超时时间（秒）。
        model_selection: 阶段绑定的模型选择；``None`` 表示不注入模型参数。

    Returns:
        agent 的标准输出（已去除首尾空白）。未执行或执行失败时返回空字符串。
    """
    if not prompt.strip():
        _logger.warning("Content generator '%s' skipped: no prompt configured", agent_name)
        return ""
    # 内容生成是**独立的一次真实进程调用**，与 implementation/review 分开记录；
    # 非 Issue 运行（建 Issue、idea 草稿、REPL）没有绑定观测上下文，此处整体退化为
    # no-op，业务行为与接入前逐字节一致。
    observation = start_invocation(
        InvocationStartRequest(
            agent_name=agent_name,
            phase=PHASE_CONTENT_GENERATION,
            profile=AGENT_PROFILE_GENERATE,
            requested_model=(model_selection.model if model_selection is not None else None),
            requested_reasoning_effort=(
                model_selection.reasoning_effort if model_selection is not None else None
            ),
        )
    )
    try:
        result = generator.generate(
            agent_name=agent_name,
            prompt=prompt,
            cwd=cwd,
            timeout=timeout_seconds,
            model_selection=model_selection,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        finish_invocation(observation, exc=exc)
        _logger.warning("Content generator '%s' did not finish: %s", agent_name, exc)
        return ""
    finish_invocation(observation, result=result)
    if result.return_code != 0:
        _logger.warning(
            "Content generator exited with code %d: %s",
            result.return_code,
            result.stderr,
        )
        return ""
    return result.stdout.strip()


def _parse_json_output(output_text: str) -> tuple[str, str]:
    """从 JSON 输出中解析标题和正文。

    支持被 Markdown 代码块包裹的 JSON（如 `` ```json\n{...}\n``` ``），
    会自动去掉代码块标记后再解析。

    Args:
        output_text: agent 输出的原始文本。

    Returns:
        ``(title, body)`` 元组。解析失败时返回 ``("", "")``，并对非空输出记录警告
        （``output`` 配成 json 而提示词回 Markdown 时，否则会静默退回 template）。
    """
    text = output_text.strip()
    # 处理被 Markdown 代码块包裹的情况（例如 ```json\n{...}\n```）
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            title = str(data.get("title", "")).strip()
            body = str(data.get("body", "")).strip()
            return title, body
    except json.JSONDecodeError:
        pass
    if text:
        _logger.warning("Agent output is not a JSON object with title/body: %.80r", text)
    return "", ""


def _parse_markdown_output(output_text: str) -> tuple[str, str]:
    """从 Markdown 输出中提取标题。

    将第一个非空行作为标题（去掉前导的 ``# `` 标记）。
    正文返回完整的原始输出，由调用方决定如何使用。

    Args:
        output_text: agent 输出的原始文本。

    Returns:
        ``(title, body)`` 元组。如果输出为空，title 为空字符串。
    """
    lines = output_text.splitlines()
    title = ""
    body = output_text
    for line in lines:
        stripped = line.strip()
        if stripped:
            title = stripped.lstrip("# ").strip()
            body = output_text
            break
    return title, body


# draft_pr 的 markdown 阶段只约定正文（首行必须是 ``Closes #N``），但 agent 常在正文前
# 先说一句对话式开场白（如 ``Here is the draft PR body:``），并可能把整份正文包进一层
# ```markdown 围栏。这类行既不是标题也不是正文，若不剥离会被 ``_parse_markdown_output``
# 当成标题，导致开场白泄漏进 PR 元数据。
_PREAMBLE_LEAD = re.compile(
    r"^(?:here(?:'s| is)\b|sure\b|certainly\b|okay\b|of course\b|below is\b|"
    r"the following\b|i(?:'ll| will)\b|as requested\b)",
    re.IGNORECASE,
)
# 水平分隔线（``---`` / ``***`` / ``___`` / ``===``），常见于开场白与正文之间。
_HORIZONTAL_RULE = re.compile(r"^(?:[-*_=]\s*){3,}$")
# Markdown 代码围栏起始行（``` 或 ~~~，可带语言标记）。
_CODE_FENCE = re.compile(r"^(`{3,}|~{3,})")
# draft_pr 的 prompt 契约规定正文首行是关闭引用（``Closes #{issue_number}``）。锚点之前
# 的内容按定义不是正文，因此整段丢弃；``_PREAMBLE_LEAD`` 只认对话式引导语，认不出用完整
# 句子写的开场白（见 :func:`_trim_before_closing_anchor`）。与
# :data:`_CLOSING_REFERENCE_TITLE` 的区别：那是标题作废判定（整行匹配），这里只要求行首
# 匹配，允许 ``Closes #42 for this patrol`` 这类带后缀的锚点行。
_CLOSING_REFERENCE_LINE = re.compile(r"^(?:close|fix|resolve)(?:e?[sd])?:?\s*#\d+", re.IGNORECASE)
# agent 有时把标题单独写成一行标签（``**Title:** `fix: ...` ``）：它是 PR 元数据，不是正文。
_TITLE_ANNOTATION_LINE = re.compile(r"^\**\s*Title\s*:\**\s*(?P<title>.+?)\s*$", re.IGNORECASE)
# 标题候选行超过这个长度就按多句散文处理（阈值取仓库提交规范的 subject ≤72 字符，
# 见 ``docs/ai-standards/tooling.md``），宁可回退到模板标题也不把散文发成 PR 标题。
_MAX_PR_TITLE_LINE_LENGTH = 72


def _is_markdown_preamble_line(line: str) -> bool:
    """判断单行是否属于开场白/装饰，而非 PR 正文或标题。

    Args:
        line: Markdown 输出中的一行原始文本。

    Returns:
        属于空白、HTML 注释、水平分隔线、对话式开场白或以冒号结尾的引导语时返回
        ``True``。
    """
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("<!--"):
        return True
    if _HORIZONTAL_RULE.match(stripped):
        return True
    if _PREAMBLE_LEAD.match(stripped):
        return True
    # 以冒号结尾的短句通常是引导语（``Here is the draft PR body:``），不是标题。
    return stripped.endswith(":") and len(stripped) <= 120


def _closing_anchor_index(lines: list[str]) -> int | None:
    """返回第一个关闭引用行（draft_pr 正文首行）的下标，没有则返回 ``None``。"""
    for index, line in enumerate(lines):
        if _CLOSING_REFERENCE_LINE.match(line.strip()):
            return index
    return None


def _is_plausible_pr_title(region_lines: list[str]) -> bool:
    """锚点之前是否只有一行够短、且没被标成标题的文本——那种行按既有约定就是 PR 标题。"""
    non_blank_lines = [line.strip() for line in region_lines if line.strip()]
    if len(non_blank_lines) != 1:
        return False
    candidate_line = non_blank_lines[0]
    if _TITLE_ANNOTATION_LINE.match(candidate_line):
        return False
    return len(candidate_line) <= _MAX_PR_TITLE_LINE_LENGTH


def _drop_unpaired_code_fence_closer(body_lines: list[str]) -> list[str]:
    """正文围栏数为奇数时丢掉末尾的关闭行：它的配对起始行在锚点之前已被当作开场白。"""
    fence_line_indexes = [
        index for index, line in enumerate(body_lines) if _CODE_FENCE.match(line.strip())
    ]
    if len(fence_line_indexes) % 2 == 0:
        return body_lines
    return body_lines[: fence_line_indexes[-1]]


def _trim_before_closing_anchor(markdown_text: str) -> str:
    """剥离 :func:`_is_markdown_preamble_line` 认不出的散文开场白与包裹正文的代码围栏。

    PR #328 的标题是 ``I compared the branch against the upstream ...`` ——agent 用完整
    句子写开场白、再写 ``**Title:**`` 标签并把正文包进 ```markdown 围栏，既有的引导语
    白名单一条都不命中，于是整段开场白既进了 PR 标题也留在了正文里。prompt 契约规定正文
    首行是 ``Closes #N``，所以锚点之前的多行内容按定义不是正文，整段丢弃。

    Args:
        markdown_text: 已剥离可识别引导语之后的 draft_pr Markdown 文本。

    Returns:
        从关闭锚点开始的正文；锚点前只有一行可能的标题时原样返回。
    """
    lines = markdown_text.splitlines()
    anchor_index = _closing_anchor_index(lines)
    if anchor_index is None or anchor_index == 0:
        return markdown_text
    if _is_plausible_pr_title(lines[:anchor_index]):
        return markdown_text
    body_lines = lines[anchor_index:]
    preceding_lines = [line for line in lines[:anchor_index] if line.strip()]
    if preceding_lines and _CODE_FENCE.match(preceding_lines[-1].strip()):
        body_lines = _drop_unpaired_code_fence_closer(body_lines)
    return "\n".join(body_lines).strip()


def _extract_pr_title_annotation(output_text: str) -> str:
    """回收关闭锚点之前 agent 显式写出的 ``Title:`` 标签值。

    Args:
        output_text: agent 输出的原始 Markdown 文本。

    Returns:
        标签里的标题（去掉包裹的反引号）；没有标签、或标签值长到更像散文时返回空串。
    """
    lines = output_text.splitlines()
    anchor_index = _closing_anchor_index(lines)
    region_lines = lines if anchor_index is None else lines[:anchor_index]
    title = ""
    for line in region_lines:
        annotation_match = _TITLE_ANNOTATION_LINE.match(line.strip())
        if annotation_match:
            title = annotation_match.group("title").strip().strip("`").strip()
    if len(title) > _MAX_PR_TITLE_LINE_LENGTH:
        return ""
    return title


def _strip_markdown_preamble(output_text: str) -> str:
    """剥离 draft_pr markdown 输出的开头开场白与最外层代码围栏。

    agent 常以 ``Here is the draft PR body:`` 之类的开场白起头，并可能把整份正文包进
    一层 ```markdown 围栏。这些内容不是标题也不是正文，若不剥离，首行会被当作 PR
    标题、正文也会被围栏包住。最多迭代两轮：先剥开场白，再拆最外层围栏，并处理围栏
    内仍残留开场白的情况。最后一轮之后仍认不出的散文开场白交给
    :func:`_trim_before_closing_anchor` 按关闭锚点剥离。

    Args:
        output_text: agent 输出的原始 Markdown 文本。

    Returns:
        去掉开头开场白/分隔线/注释行与最外层代码围栏后的 Markdown 文本。
    """
    lines = output_text.splitlines()

    for _ in range(2):
        start = 0
        while start < len(lines) and _is_markdown_preamble_line(lines[start]):
            start += 1
        lines = lines[start:]

        if not lines:
            return ""

        fence_match = _CODE_FENCE.match(lines[0].strip())
        if not fence_match:
            break
        fence = fence_match.group(1)
        end = len(lines)
        while end > 1 and not lines[end - 1].strip():
            end -= 1
        if end > 1 and lines[end - 1].strip().startswith(fence):
            lines = lines[1 : end - 1]
        else:
            lines = lines[1:]

    return _trim_before_closing_anchor("\n".join(lines).strip())


# ---------------------------------------------------------------------------
# Issue 内容生成
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepositoryContentRequest:
    """仓库正文生成的一次调用上下文。"""

    config: GeneratedContentConfig
    target: GeneratedContentTargetConfig
    context: object
    cwd: Path | None
    model_selection: ModelSelection | None
    kind: Literal["issue", "pr"]


def _generate_repository_content(
    generator: IContentGenerator | None, request: RepositoryContentRequest
) -> tuple[str, str]:
    """统一正文生成的模型选择、输入裁剪与仓库模板参考注入。"""
    if request.target.mode == "template":
        return _bound_repository_content(
            _try_render_templates(request.target, request.context), request
        )
    if request.target.mode != "agent" or generator is None or request.cwd is None:
        return "", ""
    selected_agent = (
        request.model_selection.agent
        if request.model_selection is not None
        else _resolve_generation_agent(
            request.target.agent,
            request.config.default_agent,
            override_agent=request.config.lifecycle_default_agent,
        )
    )
    rendered_prompt = _render_template(request.target.prompt, request.context)
    bounded_prompt = _truncate_text(rendered_prompt, request.config.max_input_chars)
    repository_prompt = append_repository_template_context(
        bounded_prompt, request.cwd, request.kind
    )
    output_text = _run_content_generator(
        generator,
        selected_agent,
        repository_prompt,
        request.cwd,
        request.target.timeout_seconds,
        model_selection=request.model_selection,
    )
    if request.target.output == "json":
        parsed_content = _parse_json_output(output_text)
    elif request.kind == "pr":
        stripped_output = _strip_markdown_preamble(output_text)
        annotated_title = _extract_pr_title_annotation(output_text)
        parsed_content = (
            (annotated_title, stripped_output)
            if annotated_title
            else _parse_markdown_output(stripped_output)
        )
    else:
        parsed_content = _parse_markdown_output(output_text)
    return _bound_repository_content(parsed_content, request)


def _bound_repository_content(
    parsed_content: tuple[str, str], request: RepositoryContentRequest
) -> tuple[str, str]:
    """统一标题和正文的长度边界，排除被误作 PR 标题的关闭引用。"""
    parsed_title, parsed_body = parsed_content
    if request.kind == "pr" and _CLOSING_REFERENCE_TITLE.fullmatch(parsed_title):
        parsed_title = ""
    return parsed_title[:_MAX_TITLE_LENGTH], parsed_body[: request.config.max_input_chars]


def _generate_issue_with_fallback(
    request: RepositoryContentRequest,
    generator: IContentGenerator | None,
    fallback: GeneratedIssueContent,
    validate_body: Callable[[str], bool],
) -> GeneratedIssueContent:
    """复用两类 Issue 的生成级联，由调用方提供相反的 PRD 锚点判据。"""
    if not request.config.enabled or not request.target.enabled:
        return fallback
    generated_title, generated_body = _generate_repository_content(generator, request)
    if generated_title and generated_body and validate_body(generated_body):
        return GeneratedIssueContent(
            title=generated_title, body=generated_body, source=request.target.mode
        )
    if request.target.mode == "agent" and request.config.fallback == "template":
        _logger.warning("Issue agent produced no usable content, rendering templates")
        template_title, template_body = _bound_repository_content(
            _try_render_templates(request.target, request.context), request
        )
        if template_title and template_body and validate_body(template_body):
            return GeneratedIssueContent(
                title=template_title, body=template_body, source="template"
            )
    return fallback


def generate_issue_content(
    *,
    config: GeneratedContentConfig,
    context: IssueContext,
    fallback_title: str,
    fallback_body: str,
    generator: IContentGenerator | None = None,
    cwd: Path | None = None,
    model_selection: ModelSelection | None = None,
) -> GeneratedIssueContent:
    """生成 Issue 标题和正文，支持多级回退。

    执行流程：

    1. 如果 ``generated_content`` 被禁用，直接返回 fallback。
    2. 根据 ``target.mode`` 选择生成策略：
       - ``"agent"``（默认）：调用 AI agent，然后根据 ``target.output`` 解析 JSON 或 Markdown。
       - ``"template"``（已废弃）：跳过 agent，直接用 ``_try_render_templates`` 渲染模板。
    3. 截断标题和正文至安全长度，验证正文是否包含 ``- PRD path:`` 锚点。
    4. 验证通过则返回生成结果，source 标记为 ``target.mode``。
    5. 如果 agent 模式失败且 ``config.fallback == "template"``，尝试 template 兜底。
    6. 最终仍失败则返回 ``fallback_title`` / ``fallback_body``，source 标记为 ``"fallback"``。

    Args:
        config: 生成内容配置，包含 mode、fallback 策略、max_input_chars 等。
        context: Issue 上下文，提供模板/agent 所需的变量。
        fallback_title: 当所有生成方式失败时使用的标题。
        fallback_body: 当所有生成方式失败时使用的正文。
        generator: agent 模式所需的内容生成器。template 模式可为 ``None``。
        cwd: agent 工作目录。agent 模式必需。

    Returns:
        包含 title、body 和 source 的 ``GeneratedIssueContent`` 实例。
    """
    return _generate_issue_with_fallback(
        RepositoryContentRequest(
            config, config.issue_from_prd, context, cwd, model_selection, "issue"
        ),
        generator,
        GeneratedIssueContent(title=fallback_title, body=fallback_body, source="fallback"),
        lambda body: _validate_issue_body(body, context.relative_prd_path),
    )


# ---------------------------------------------------------------------------
# Git 信息收集（用于 PR 内容生成）
# ---------------------------------------------------------------------------


def _get_commit_log(
    worktree_path: Path,
    base_branch: str,
    process_runner: IProcessRunner,
) -> str:
    """获取从 ``base_branch`` 到 HEAD 的提交信息列表。

    使用 ``git log {base_branch}..HEAD --pretty=format:%s`` 命令，
    仅提取提交的 subject 行。

    Args:
        worktree_path: 工作树目录。
        base_branch: 基础分支名称。
        process_runner: 执行 Git 命令的 runner。

    Returns:
        以换行符分隔的提交 subject 列表。无提交时返回空字符串。
    """
    result = process_runner.run(
        ["git", "log", f"{base_branch}..HEAD", "--pretty=format:%s"],
        cwd=worktree_path,
        check=False,
    )
    return result.stdout.strip()


def _get_diff_stat(
    worktree_path: Path,
    base_branch: str,
    process_runner: IProcessRunner,
) -> str:
    """获取从 ``base_branch`` 到 HEAD 的 diff 统计信息。

    使用 ``git diff --stat {base_branch}...HEAD`` 命令，
    显示每个变更文件的增删行数统计。

    Args:
        worktree_path: 工作树目录。
        base_branch: 基础分支名称。
        process_runner: 执行 Git 命令的 runner。

    Returns:
        ``git diff --stat`` 的输出文本。无变更时返回空字符串。
    """
    result = process_runner.run(
        ["git", "diff", "--stat", f"{base_branch}...HEAD"],
        cwd=worktree_path,
        check=False,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# PR 上下文构建
# ---------------------------------------------------------------------------


def build_pr_context(
    *,
    issue: object,
    branch: str,
    base_branch: str,
    worktree_path: Path,
    process_runner: IProcessRunner,
    target_config: GeneratedContentTargetConfig,
) -> PrContext:
    """为 PR 内容生成构建上下文变量。

    根据 ``target_config`` 中的开关（``include_commit_log``、
    ``include_diff_stat``）决定是否执行相应的 Git 命令收集信息。
    这些 Git 操作可能耗时，因此仅在配置要求时才执行。

    Args:
        issue: 关联的 Issue 对象，需具有 ``number``、``title``、``body`` 属性。
        branch: 当前工作分支。
        base_branch: 基础分支。
        worktree_path: 工作树目录。
        process_runner: 执行 Git 命令的 runner。
        target_config: 生成目标配置，控制是否收集 commit log 和 diff stat。

    Returns:
        供模板渲染或 agent prompt 使用的 ``PrContext`` 实例。
    """
    commit_log = ""
    diff_stat = ""
    if target_config.include_commit_log:
        commit_log = _get_commit_log(worktree_path, base_branch, process_runner)
    if target_config.include_diff_stat:
        diff_stat = _get_diff_stat(worktree_path, base_branch, process_runner)
    return PrContext(
        issue_number=issue.number,
        issue_title=issue.title,
        issue_body=issue.body,
        branch=branch,
        base_branch=base_branch,
        commit_log=commit_log,
        commit_messages=commit_log,
        diff_stat=diff_stat,
        git_diff_stat=diff_stat,
        repo_url=getattr(issue, "url", "").partition("/issues/")[0],
    )


# ---------------------------------------------------------------------------
# PR 内容生成
# ---------------------------------------------------------------------------


def generate_pr_content(
    *,
    config: GeneratedContentConfig,
    context: PrContext,
    fallback_title: str,
    fallback_body: str,
    generator: IContentGenerator | None = None,
    cwd: Path | None = None,
    model_selection: ModelSelection | None = None,
) -> GeneratedPrContent:
    """生成 PR 标题和正文，支持多级回退。

    逻辑与 ``generate_issue_content`` 基本一致，区别：

    - 使用 ``config.draft_pr`` 而非 ``config.issue_from_prd`` 作为目标配置。
    - 正文校验使用 ``_validate_pr_body``（检查 ``Closes #{issue_number}``）。
    - title 允许为空，验证失败时会回退到 ``fallback_title``。

    Args:
        config: 生成内容配置。
        context: PR 上下文。
        fallback_title: 当所有生成方式失败时使用的标题。
        fallback_body: 当所有生成方式失败时使用的正文。
        generator: agent 模式所需的内容生成器。
        cwd: agent 工作目录。

    Returns:
        包含 title、body 和 source 的 ``GeneratedPrContent`` 实例。
    """
    target = config.draft_pr
    generation_request = RepositoryContentRequest(
        config, target, context, cwd, model_selection, "pr"
    )
    if not config.enabled or not target.enabled:
        return GeneratedPrContent(title=fallback_title, body=fallback_body, source="fallback")

    generated_title, generated_body = _generate_repository_content(generator, generation_request)

    if generated_body and _validate_pr_body(generated_body, context.issue_number):
        return GeneratedPrContent(
            title=generated_title or fallback_title,
            body=generated_body,
            source=target.mode,
        )

    # Agent 失败：按配置尝试 template 中间兜底。
    if target.mode == "agent" and config.fallback == "template":
        _logger.warning("draft_pr: agent produced no usable content, rendering templates")
        generated_title, generated_body = _try_render_templates(target, context)
        if generated_title:
            generated_title = generated_title[:_MAX_TITLE_LENGTH]
        if generated_body:
            generated_body = generated_body[: config.max_input_chars]
        if generated_body and _validate_pr_body(generated_body, context.issue_number):
            return GeneratedPrContent(
                title=generated_title or fallback_title,
                body=generated_body,
                source="template",
            )

    return GeneratedPrContent(title=fallback_title, body=fallback_body, source="fallback")

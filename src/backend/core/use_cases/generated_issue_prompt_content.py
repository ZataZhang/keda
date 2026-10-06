"""一句话需求 → Issue 正文的内容生成（``iar issue create --from-prompt``）。

与 ``issue_from_prd`` 的方向相反：本目标的产物**不得**携带 PRD 锚点。无锚点
Issue 的正文就是唯一需求来源，写入 ``- PRD path:`` 会让 runner 去定位一个不存在
的 PRD 并卡在交付门上。因此这里不复用
:func:`backend.core.use_cases.generated_content.generate_issue_content`（它硬校验
锚点*存在*），而是自成一条 ``agent → template → fallback`` 级联，并把校验反向做：
产物出现 PRD 指涉即判为不合格，退回确定性模板。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IContentGenerator
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import (
    GeneratedContentConfig,
    GeneratedIssueContent,
)
from backend.core.use_cases.agent_runner_feedback import extract_prd_path
from backend.core.use_cases.generated_content import (
    _MAX_TITLE_LENGTH,
    _parse_json_output,
    _parse_markdown_output,
    _render_template,
    _resolve_generation_agent,
    _run_content_generator,
    _try_render_templates,
    _truncate_text,
)

_logger = logging.getLogger(__name__)

#: 机器锚点之外同样禁止出现的 PRD 指涉：``build_issue_body`` 的小节标题。
#: 留着它会让 reviewer 以为有一份规范 PRD 可查。
_PRD_SECTION_HEADING = re.compile(r"^#{2}\s*Canonical PRD\s*$", re.IGNORECASE | re.MULTILINE)


def contains_prd_reference(issue_body: str) -> bool:
    """正文是否指涉了一份 PRD（机器锚点或 ``## Canonical PRD`` 小节）。

    Args:
        issue_body: 待检查的 Issue 正文。

    Returns:
        含任一种 PRD 指涉时返回 ``True``。
    """
    if extract_prd_path(issue_body) is not None:
        return True
    return bool(_PRD_SECTION_HEADING.search(issue_body))


@dataclass(frozen=True)
class IssuePromptContext:
    """一句话需求生成 Issue 正文时的模板变量。

    字段名直接对应模板占位符（``str.format(**context.__dict__)``）。

    Attributes:
        issue_type: Issue 类型，如 ``"feature"``。
        title: 回退标题（通常由调用方从需求首行截断得到）。
        prompt_text: 用户给出的自然语言需求原文，正文的唯一需求来源。
        validation_directive: 要求 agent 另写验收段的指令文本；默认空串。
    """

    issue_type: str
    title: str
    prompt_text: str
    validation_directive: str


def build_issue_prompt_context(
    *,
    issue_type: str,
    prompt_text: str,
    fallback_title: str,
    require_validation: bool,
) -> IssuePromptContext:
    """组装 :class:`IssuePromptContext`。

    Args:
        issue_type: Issue 类型标签后缀。
        prompt_text: 用户的一句话需求原文。
        fallback_title: 生成失败时使用的标题。
        require_validation: 是否要求正文自带验收清单。

    Returns:
        可直接喂给模板渲染与 agent 提示词的上下文。
    """
    validation_directive = (
        "Additionally, write a '## Acceptance Criteria' section listing 2-4 "
        "concretely checkable outcomes of this requirement."
        if require_validation
        else ""
    )
    return IssuePromptContext(
        issue_type=issue_type,
        title=fallback_title,
        prompt_text=prompt_text,
        validation_directive=validation_directive,
    )


def _validate_prompt_body(body: str) -> bool:
    """正文可用性校验：非空且**不**指涉 PRD。

    判据与 ``generated_content._validate_issue_body`` 相反——那里锚点是必要条件，
    这里它是禁止条件。
    """
    if not body or not body.strip():
        return False
    return not contains_prd_reference(body)


def generate_issue_prompt_content(
    *,
    config: GeneratedContentConfig,
    context: IssuePromptContext,
    fallback_title: str,
    fallback_body: str,
    generator: IContentGenerator | None = None,
    cwd: Path | None = None,
    model_selection: ModelSelection | None = None,
) -> GeneratedIssueContent:
    """为 ``--from-prompt`` 生成 Issue 标题与正文，支持多级回退。

    执行流程与 :func:`generated_content.generate_issue_content` 同形
    （``agent → template → fallback``），差别只在读取 ``config.issue_from_prompt``
    目标，以及用 :func:`_validate_prompt_body` 反向拒绝 PRD 指涉：agent 若自作主张
    写出锚点，产物整份作废并退回模板。

    Args:
        config: 生成内容配置（含 ``enabled``、回退策略、输入长度上限）。
        context: 一句话需求的模板上下文。
        fallback_title: 全部生成方式失败时的标题。
        fallback_body: 全部生成方式失败时的正文（调用方确定性构造，必无锚点）。
        generator: agent 模式所需的内容生成器；``None`` 时跳过 agent。
        cwd: agent 工作目录；``None`` 时跳过 agent。
        model_selection: 阶段绑定的模型选择。

    Returns:
        包含 title、body 和 source 的 ``GeneratedIssueContent`` 实例。
    """
    target = config.issue_from_prompt
    if not config.enabled or not target.enabled:
        return GeneratedIssueContent(title=fallback_title, body=fallback_body, source="fallback")

    generated_title = ""
    generated_body = ""

    if target.mode == "template":
        generated_title, generated_body = _try_render_templates(target, context)
    elif target.mode == "agent" and generator is not None and cwd is not None:
        agent_name = (
            model_selection.agent
            if model_selection is not None
            else _resolve_generation_agent(
                target.agent, config.default_agent, override_agent=config.lifecycle_default_agent
            )
        )
        prompt = _render_template(target.prompt, context)
        prompt = _truncate_text(prompt, config.max_input_chars)
        output_text = _run_content_generator(
            generator,
            agent_name,
            prompt,
            cwd,
            target.timeout_seconds,
            model_selection=model_selection,
        )
        # 与 issue_from_prd 一致：解析方式跟随 target 的 output，配错不会静默退化。
        if target.output == "json":
            generated_title, generated_body = _parse_json_output(output_text)
        else:
            generated_title, generated_body = _parse_markdown_output(output_text)

    if generated_title:
        generated_title = generated_title[:_MAX_TITLE_LENGTH]
    if generated_body:
        generated_body = generated_body[: config.max_input_chars]

    if generated_title and generated_body and _validate_prompt_body(generated_body):
        return GeneratedIssueContent(title=generated_title, body=generated_body, source=target.mode)

    if target.mode == "agent" and config.fallback == "template":
        _logger.warning("issue_from_prompt: agent produced no usable content, rendering templates")
        generated_title, generated_body = _try_render_templates(target, context)
        if generated_title:
            generated_title = generated_title[:_MAX_TITLE_LENGTH]
        if generated_body:
            generated_body = generated_body[: config.max_input_chars]
        if generated_title and generated_body and _validate_prompt_body(generated_body):
            return GeneratedIssueContent(
                title=generated_title, body=generated_body, source="template"
            )

    return GeneratedIssueContent(title=fallback_title, body=fallback_body, source="fallback")

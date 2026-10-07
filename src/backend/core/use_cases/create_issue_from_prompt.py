"""``kc issue create --from-prompt``：一句话需求直接建出无 PRD 的 Issue。

与 :mod:`create_issue_from_prd` 的区别只有一处语义：**产物不指向任何 PRD**。
正文就是需求的唯一来源，runner 侧的所有门禁（交付门、证据门禁、PRD 契约）都按
「无锚点」分支降级运行。因此本模块：

* 不复用 ``generate_issue_content``（它硬校验 PRD 锚点存在），走
  :func:`generated_issue_prompt_content.generate_issue_prompt_content`；
* 不写 ``- GitHub Issue:`` 回链、不发布任何文件 —— 工作区必须零 diff；
* 复用同一套建 Issue / 标签 / 依赖宣告链路（``github_client.create_issue`` +
  :func:`issue_labels.apply_routing_labels` + ``format_dependency_marker``）。

验收段（``## Realistic Validation``）的**存在与否由旗标决定，不由 agent 决定**：
默认态会剥掉 agent 自写的验收段，让证据门禁确定关闭；``--require-validation``
时无论 agent 是否配合，都由代码物化出该小节，让门禁确定开启。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
)
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import (
    DEFAULT_VALIDATION_EVIDENCE_DIR,
    GeneratedContentConfig,
    LabelConfig,
)
from backend.core.use_cases.agent_runner_dependencies import format_dependency_marker
from backend.core.use_cases.agent_runner_validation import (
    build_issue_validation_section,
    resolve_pending_issue_evidence_relpath,
)
from backend.core.use_cases.agent_runner_validation_parsing import (
    _VALIDATION_SECTION_HEADER_RE,
    extract_realistic_validation_items,
)
from backend.core.use_cases.create_issue_from_prd import _resolve_dependencies
from backend.core.use_cases.generated_issue_prompt_content import (
    build_issue_prompt_context,
    generate_issue_prompt_content,
)
from backend.core.use_cases.issue_labels import apply_routing_labels

_logger = logging.getLogger(__name__)

#: 标题里需求文本的最大长度，超出部分交给 GitHub 的标题截断容忍度之前的自我约束。
_MAX_FALLBACK_TITLE_CHARS = 80

#: 无 PRD 时 ``--depends-on`` 就是依赖的唯一声明来源，因此非空即物化为标记。


@dataclass(frozen=True)
class IssueFromPromptRequest:
    """从一句话需求创建 GitHub Issue 的输入参数。

    Attributes:
        repo_path: 仓库根目录的绝对路径。
        prompt_text: 自然语言需求原文，将成为 Issue 正文的唯一需求来源。
        issue_type: 用于构建初始标签的类别，例如 ``"feature"`` → ``"type/feature"``。
        title_override: 非 ``None`` 时同时覆盖 AI 生成的标题与回退标题。
        queue_ready: 是否在创建后立即添加 ``agent/ready`` 标签。
        issue_agent: agent 路由标签键，``"auto"`` / ``"none"`` 表示不打。
        labels_config: 显式指定的标签名称；``None`` 时用默认值。
        depends_on: 上游 Issue 编号列表。
        require_validation: 是否要求该 Issue 自带验收清单（决定证据门禁开关）。
        generated_content_config: AI 内容生成配置；``None`` 或未启用时直接用
            确定性回退正文。
        validation_language: Realistic Validation 固定标签语言。
        structured_evidence: 是否物化 ``iar:structured-evidence`` marker。
        evidence_dir: 证据目录配置根（仓库相对）。
        model_selection: ``content_generation`` 阶段绑定的模型选择。
    """

    repo_path: Path
    prompt_text: str
    issue_type: str
    title_override: str | None = None
    queue_ready: bool = False
    issue_agent: str = "auto"
    labels_config: LabelConfig | None = None
    depends_on: tuple[int, ...] = ()
    require_validation: bool = False
    generated_content_config: GeneratedContentConfig | None = None
    validation_language: str = "zh-CN"
    structured_evidence: bool = True
    evidence_dir: str = DEFAULT_VALIDATION_EVIDENCE_DIR
    model_selection: ModelSelection | None = None


# ---------------------------------------------------------------------------
# 正文构造
# ---------------------------------------------------------------------------


def derive_prompt_title(prompt_text: str) -> str:
    """从需求原文派生回退标题（首个非空行，压平换行并截断）。

    Args:
        prompt_text: 需求原文。

    Returns:
        不含类型前缀的标题文本。
    """
    for line in prompt_text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            if len(stripped) <= _MAX_FALLBACK_TITLE_CHARS:
                return stripped
            return stripped[: _MAX_FALLBACK_TITLE_CHARS - 1].rstrip() + "…"
    return "Untitled requirement"


def build_prompt_fallback_body(prompt_text: str) -> str:
    """构造确定性回退正文：需求原文逐字保留 + 交付说明。

    这是 agent 与模板都不可用时的 hard fallback，必须**自足**（读者无需任何 PRD
    即可理解需求）且**不含任何 PRD 指涉**。

    Args:
        prompt_text: 需求原文。

    Returns:
        Issue 正文 Markdown 字符串（不含验收小节，由调用方按旗标物化）。
    """
    return "\n".join(
        [
            "## Requirement",
            "",
            prompt_text.strip(),
            "",
            "## Delivery Notes",
            "",
            "- 本 Issue 由 ``kc issue create --from-prompt`` 创建：正文就是需求的",
            "  唯一来源，仓库里没有对应的 PRD 文件。",
            "- Recommended branch: `task/<issue-number>-<slug>`",
            "- Worktree command: `just worktree --issue <issue-number>`",
            "- PR should include: `Closes #<issue-number>`",
            "",
        ]
    )


def strip_validation_section(markdown_text: str) -> str:
    """移除 ``Realistic Validation`` 小节（含其下所有内容，直到同级或更浅的标题）。

    默认态用它保证「agent 自作主张写了验收段」不会把证据门禁打开。围栏代码块
    （```` ``` ````）内的行按内容处理、不当成标题——否则块内 YAML/shell 注释行
    （``# ...``）会被误判为标题，既提前截断剥离、又可能把块外的 fenced 内容
    切出未闭合的围栏（与 :func:`agent_runner_validation_parsing._iterate_validation_section_lines`
    同一规则）。

    Args:
        markdown_text: 原始正文。

    Returns:
        去掉该小节后的正文（无该小节时归一化尾部换行后返回）。
    """
    kept_lines: list[str] = []
    skip_until_level: int | None = None
    in_code_fence = False
    for line in markdown_text.splitlines():
        stripped_line = line.strip()
        if stripped_line.startswith("```"):
            in_code_fence = not in_code_fence
            if skip_until_level is None:
                kept_lines.append(line)
            continue
        if not in_code_fence and line.startswith("#"):
            heading_level = len(line) - len(line.lstrip("#"))
            if skip_until_level is not None:
                if heading_level <= skip_until_level:
                    skip_until_level = None
                else:
                    continue
            if _VALIDATION_SECTION_HEADER_RE.match(line[heading_level:].strip()):
                skip_until_level = heading_level
                continue
        elif skip_until_level is not None:
            continue
        kept_lines.append(line)
    stripped_text = "\n".join(kept_lines).rstrip()
    return f"{stripped_text}\n" if stripped_text else ""


def validation_items_from(body: str, prompt_text: str) -> list[str]:
    """取出已生成正文里的验收条目；agent 没写时用需求原文兜一条。

    兜底条目保证 ``--require-validation`` 的门禁开启不依赖 agent 是否配合。

    Args:
        body: 内容生成产出的正文（**剥离前**的原文）。
        prompt_text: 需求原文，兜底条目的内容来源。

    Returns:
        规范化后的未勾选清单行列表，至少一条。
    """
    items = extract_realistic_validation_items(body)
    if items:
        return items
    return [f"- [ ] 通过真实入口验证以下需求已达成：{derive_prompt_title(prompt_text)}"]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def create_issue_from_prompt(
    *,
    request: IssueFromPromptRequest,
    github_client: IGitHubClient,
    content_generator: IContentGenerator | None = None,
) -> str:
    """用一句话需求创建 GitHub Issue，不产生也不引用任何 PRD 文件。

    编排流程：

    1. 标签装配（复用 :func:`issue_labels.apply_routing_labels`，与 PRD 路径一致）。
    2. 确定性回退标题 / 正文。
    3. 可选的 AI 内容生成（``agent → template → fallback``）。
    4. 按旗标物化或剥离 ``Realistic Validation`` 小节。
    5. 追加 ``iar:depends-on`` marker（``--depends-on`` 是本路径唯一的依赖来源）。
    6. 调用 ``github_client.create_issue()``。

    Args:
        request: Issue 创建请求。
        github_client: 与 GitHub 交互的客户端。
        content_generator: agent 模式所需的内容生成器；``None`` 时跳过生成。

    Returns:
        创建的 GitHub Issue URL。

    Raises:
        ValueError: 当 ``prompt_text`` 为空，或 ``issue_agent`` 不可识别时。
    """
    prompt_text = request.prompt_text.strip()
    if not prompt_text:
        raise ValueError("--from-prompt requires a non-empty requirement text.")

    effective_labels_config = request.labels_config or LabelConfig()
    labels = [f"type/{request.issue_type}", "status/backlog"]
    # 无 PRD 的 Issue 不打 source/prd：标签缺失本身就是「没有规范 PRD」的信号。
    apply_routing_labels(
        labels,
        queue_ready=request.queue_ready,
        ready_deferred_until_publish=False,
        issue_agent=request.issue_agent,
        labels_config=effective_labels_config,
    )

    fallback_title = request.title_override or (
        f"[{request.issue_type.title()}] {derive_prompt_title(prompt_text)}"
    )
    fallback_body = build_prompt_fallback_body(prompt_text)

    title = fallback_title
    body = fallback_body
    gc_config = request.generated_content_config
    if gc_config is not None and gc_config.enabled:
        generated = generate_issue_prompt_content(
            config=gc_config,
            context=build_issue_prompt_context(
                issue_type=request.issue_type,
                prompt_text=prompt_text,
                fallback_title=fallback_title,
                require_validation=request.require_validation,
            ),
            fallback_title=fallback_title,
            fallback_body=fallback_body,
            generator=content_generator,
            cwd=request.repo_path if content_generator is not None else None,
            model_selection=request.model_selection,
        )
        title = generated.title
        body = generated.body
    # 与 PRD 路径同一优先级：显式 --title 压过生成标题与回退标题。
    if request.title_override:
        title = request.title_override

    # 验收小节的存在与否由旗标决定，不由 agent 决定：条目文本先从生成正文里取，
    # 然后整段剥离，要求态再由代码物化一次。默认态因此确定没有小节 —— 即使 agent
    # 自作主张写了也不会把证据门禁打开。
    validation_items = validation_items_from(body=body, prompt_text=prompt_text)
    body = strip_validation_section(body)
    if request.require_validation:
        validation_section = build_issue_validation_section(
            checklist_items=validation_items,
            waiver_reason=None,
            language=request.validation_language,
            structured_evidence=request.structured_evidence,
            evidence_dir=resolve_pending_issue_evidence_relpath(request.evidence_dir),
        )
        body = f"{body.rstrip()}\n\n{validation_section}\n"

    # 无 PRD 时 --depends-on 是依赖的唯一声明来源，所以解析结果非空就物化标记
    # （PRD 路径还要看 PRD 自己声明的 gate_type，这里没有那一层）。
    _, resolved_issues, sequence = _resolve_dependencies("", depends_on=request.depends_on)
    if resolved_issues:
        dependency_marker = format_dependency_marker(
            issue_numbers=resolved_issues,
            sequence=sequence,
        )
        body = f"{body.rstrip()}\n\n{dependency_marker}\n"

    issue_url = github_client.create_issue(title=title, body=body, labels=labels)
    _logger.info("Created GitHub Issue from prompt: %s", issue_url)
    return issue_url

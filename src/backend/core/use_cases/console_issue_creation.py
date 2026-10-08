"""Console 侧「一句话建 Issue」装配层（FR-8）。

:mod:`create_issue_from_prompt` 是 CLI ``kc issue create --from-prompt`` 的既有实现，
本模块只负责把仓库配置与用户输入映射成它的请求对象，不复制任何编排逻辑：
标签装配、AI 内容生成、验收段物化、依赖标记全部沿用既有路径。

与 CLI 的差异仅有三处，前两处由入口形态决定：

* Console 没有 argv，因此不做 CLI 模型锚定（``apply_cli_model_preset``），
  模型选择直接取配置里的 ``content_generation`` 阶段绑定；
* ``queue_ready`` 固定为 ``False``（人工决定 D-06）——建完 Issue 不自动入队，
  入队是「加入就绪」按钮（FR-3）的独立语义；
* ``issue_type`` 在调用用例之前先过标签纪律（见 :func:`_validated_issue_type`）：
  它会变成 ``type/<issue_type>`` 写进 GitHub，而 CLI 那一侧由 argv 枚举把关，网页没有。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import RepositoryRunContext
from backend.core.use_cases.create_issue_from_prompt import (
    IssueFromPromptRequest,
    create_issue_from_prompt,
)
from backend.core.use_cases.issue_label_actions import allowed_label_names
from backend.core.use_cases.lifecycle_agent_resolution import (
    resolve_lifecycle_model_selection,
)

_ISSUE_URL_NUMBER_RE = re.compile(r"/issues/(\d+)(?:[/?#]|$)")

#: 网页建 Issue 的类型标签前缀：``issue_type`` 只允许作为该前缀后的已同步标签名落地。
_ISSUE_TYPE_LABEL_PREFIX = "type/"


@dataclass(frozen=True)
class ConsoleCreatedIssue:
    """Console 建 Issue 的结果视图。

    Attributes:
        number: 新建 Issue 的编号；后端无法从 URL 解析时 ``issue_url`` 仍可用。
        issue_url: GitHub Issue 网页地址。
    """

    number: int
    issue_url: str


def selectable_issue_types(context: RepositoryRunContext) -> tuple[str, ...]:
    """返回该仓库网页可用的 Issue 类型（升序、稳定可断言）。

    口径与标签写入纪律同源：``kc labels sync`` 会同步的 ``type/*`` 标签去掉前缀。

    Args:
        context: 仓库运行上下文。

    Returns:
        可选的 ``issue_type`` 取值元组（如 ``("bug", "feature", "refactor")``）。
    """
    return tuple(
        sorted(
            label_name.removeprefix(_ISSUE_TYPE_LABEL_PREFIX)
            for label_name in allowed_label_names(context)
            if label_name.startswith(_ISSUE_TYPE_LABEL_PREFIX)
        )
    )


def _validated_issue_type(context: RepositoryRunContext, issue_type: str) -> str:
    """把 ``issue_type`` 约束在已同步的 ``type/*`` 标签集内并返回去空白取值。

    用例内部按 ``type/<issue_type>`` 直接拼标签名，不做集合校验的话，网页这条写路径
    就能凭任意字符串让 gh 建出一个集合外的新标签——绕过 FR-5 的「网页不创建新标签」，
    且失败发生在 gh 侧、报错难以定位。CLI 靠 argv 枚举挡住同类输入，console 没有 argv。

    Args:
        context: 仓库运行上下文（决定已同步的标签集合）。
        issue_type: 请求带来的 Issue 类型。

    Returns:
        去空白后的合法类型名。

    Raises:
        ValueError: 类型为空，或拼出的 ``type/<issue_type>`` 不在同步集合内。
    """
    normalized = issue_type.strip()
    allowed = selectable_issue_types(context)
    if normalized not in allowed:
        raise ValueError(
            f"issue_type {issue_type!r} 不在本仓库已同步的类型标签集内，"
            f"可选取值：{', '.join(allowed)}。网页不创建新标签；确需新类型请先在终端"
            "执行 `kc labels sync`。"
        )
    return normalized


def _parse_issue_number(issue_url: str) -> int:
    """从 Issue URL 中解析 Issue 编号。

    Args:
        issue_url: ``create_issue_from_prompt`` 返回的网页地址。

    Returns:
        Issue 编号。

    Raises:
        ValueError: URL 不含 ``/issues/<n>`` 段时抛出。
    """
    match = _ISSUE_URL_NUMBER_RE.search(issue_url)
    if not match:
        raise ValueError(f"Cannot parse an issue number from URL: {issue_url}")
    return int(match.group(1))


def _resolve_content_generation_setup(
    context: RepositoryRunContext,
    process_runner: IProcessRunner,
) -> tuple[object | None, object | None]:
    """按配置决定是否启用 agent 内容生成，并取回阶段模型选择。

    Args:
        context: 仓库运行上下文。
        process_runner: 子进程端口，供 agent 模式的内容生成器调用 CLI。

    Returns:
        ``(model_selection, content_generator)``；未启用 agent 模式时
        ``content_generator`` 为 ``None``，正文走确定性回退。
    """
    from backend.core.use_cases.agent_runner_factory import create_content_generator

    generated_content_config = context.config.generated_content
    target_config = generated_content_config.issue_from_prompt
    model_selection = resolve_lifecycle_model_selection("content_generation", context.config)
    content_generator = None
    if generated_content_config.enabled and target_config.enabled and target_config.mode == "agent":
        content_generator = create_content_generator(process_runner, config=context.config)
    return model_selection, content_generator


def create_issue_from_prompt_for_console(
    *,
    context: RepositoryRunContext,
    process_runner: IProcessRunner,
    prompt_text: str,
    issue_type: str = "feature",
    title_override: str | None = None,
) -> ConsoleCreatedIssue:
    """用一句话需求在指定仓库建 Issue，不入队也不启动 runner。

    Args:
        context: 仓库运行上下文。
        process_runner: 子进程端口。
        prompt_text: 需求原文。
        issue_type: Issue 类型标签后缀（``feature`` / ``bug`` 等），必须落在该仓库已同步
            的 ``type/*`` 集合内。
        title_override: 非 ``None`` 时覆盖生成标题。

    Returns:
        新建 Issue 的编号与地址。

    Raises:
        ValueError: 需求文本为空、``issue_type`` 不在已同步的 ``type/*`` 集合内、
            agent 路由不可识别，或返回 URL 无法解析编号。
    """
    issue_type = _validated_issue_type(context, issue_type)
    model_selection, content_generator = _resolve_content_generation_setup(context, process_runner)
    validation_config = context.config.validation
    from backend.core.use_cases.agent_runner_factory import create_github_client

    github_client = create_github_client(context.repo_path, process_runner)
    issue_url = create_issue_from_prompt(
        request=IssueFromPromptRequest(
            repo_path=context.repo_path,
            prompt_text=prompt_text,
            issue_type=issue_type,
            title_override=title_override,
            queue_ready=False,
            issue_agent="auto",
            labels_config=context.config.labels,
            require_validation=False,
            generated_content_config=context.config.generated_content,
            validation_language=validation_config.language,
            structured_evidence=validation_config.structured_evidence,
            evidence_dir=validation_config.evidence_dir,
            model_selection=model_selection,
        ),
        github_client=github_client,
        content_generator=content_generator,
    )
    return ConsoleCreatedIssue(number=_parse_issue_number(issue_url), issue_url=issue_url)

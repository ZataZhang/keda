"""根据本地 PRD Markdown 文件创建 GitHub Issue。

本模块实现 ``kc issue create`` 工作流：

1. 读取本地 PRD Markdown 文件。
2. 提取元数据（标题、验收清单、引言）。
3. 可选：通过 AI 生成更丰富的 Issue 内容（默认 agent，失败时回退到模板）。
4. 通过 ``IGitHubClient`` 创建 GitHub Issue。
5. 将创建的 Issue URL 回写到 PRD 中。
6. 可选：发布 PRD 文件（stage、commit、push），使 Issue 链接持久化到仓库。

内容生成遵循三级级联策略：

- **Agent 模式**（``generated_content`` 启用时的默认，即 ``mode="agent"``）：
  AI agent 根据完整 PRD 上下文生成 Issue 标题和正文。
- **Template 兜底**（当 agent 失败且 ``fallback="template"`` 时；直接设置
  ``mode="template"`` 已废弃，只是跳过 agent 这一步）：
  使用类似 Jinja2 的 ``.format()`` 模板，通过 PRD 上下文变量
  （如 ``{prd_introduction}``、``{relative_prd_path}`` 等）渲染标题/正文。
- **Hard fallback**（始终可用）：``build_issue_body()`` 构建一个确定的
  Markdown 正文，包含 PRD 路径锚点、验收清单以及（自本次修复后）PRD 引言章节。

所有文件系统 I/O 均显式指定 ``encoding="utf-8"``。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    LabelConfig,
    evidence_dir_uses_task_subdirs,
)
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_GENERATE,
    BUILTIN_AGENT_SPECS,
    AgentSpec,
)
from backend.core.use_cases.agent_runner_dependencies import (
    format_dependency_marker,
    parse_dependency_marker,
    parse_delivery_dependencies,
)
from backend.core.use_cases.agent_runner_validation import (
    assert_realistic_validation_oracle_valid,
    build_issue_validation_section,
    extract_evidence_format_waiver_reason,
    extract_realistic_validation_items,
    extract_validation_waiver_reason,
)
from backend.core.use_cases.generated_content import (
    build_issue_context,
    generate_issue_content,
)
from backend.core.use_cases.issue_prd_github_link import append_prd_github_link
from backend.core.use_cases.lifecycle_agent_resolution import parse_prd_lifecycle_overrides

from backend.core.use_cases.create_issue_from_prd_helpers import (
    ISSUE_LINE_RE as ISSUE_LINE_RE,
    ISSUE_LINK_LINE_RE,
    ISSUE_NUMBER_RE,
    IssueFromPrdRequest,
    PrdPublishContext,
    build_issue_body,
    build_issue_labels,
    current_git_branch,
    extract_acceptance_items,
    extract_title,
    parse_issue_number,
    publish_prd_file,
    resolve_prd_paths,
    validate_ready_publish_branch,
    validate_staged_changes_are_prd_only,
    write_issue_link,
)  # noqa: F401  # 保留既有 use case 导入路径

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency resolution helpers
# ---------------------------------------------------------------------------


def _resolve_dependencies(
    prd_text: str,
    *,
    repo_path: Path | None = None,
    current_prd_path: Path | None = None,
    depends_on: tuple[int, ...] = (),
) -> tuple[str, tuple[int, ...], str]:
    """Merge PRD structured dependencies, explicit markers and CLI overrides.

    Three sources are merged with CLI taking highest precedence:

    1. ``Delivery Dependencies`` section in the PRD.
    2. Explicit ``kc:depends-on`` markers in the PRD body.
    3. CLI ``--depends-on`` overrides.

    Args:
        prd_text: Full PRD Markdown text.
        repo_path: Repository root, required when the PRD declares PRD file
            dependencies in ``Depends on tasks/issues``.
        current_prd_path: Current PRD path, used to reject self-dependencies.
        depends_on: CLI override issue numbers.

    Returns:
        ``(gate_type, resolved_issues, sequence)``.
        ``gate_type`` is the gate from the PRD section (``none`` if absent);
        ``sequence`` is the PRD's ``Sequence`` field (``via-main`` if absent).
    """
    from_prd = parse_delivery_dependencies(prd_text)

    # Start with PRD section values
    gate_type = from_prd.gate_type
    resolved_issues = list(from_prd.depends_on_issues)
    if gate_type == "hard" and from_prd.depends_on_prds:
        resolved_issues.extend(
            _materialize_prd_dependencies(
                repo_path=repo_path,
                current_prd_path=current_prd_path,
                prd_refs=from_prd.depends_on_prds,
            )
        )

    # Merge explicit markers in PRD body (compat path)
    explicit = parse_dependency_marker(prd_text)
    if explicit is not None:
        resolved_issues.extend(explicit.issue_numbers)

    # CLI overrides are additive
    resolved_issues.extend(depends_on)

    # Deduplicate while preserving order
    seen_issues: set[int] = set()
    deduped_issues = [n for n in resolved_issues if not (n in seen_issues or seen_issues.add(n))]

    # ``stack`` declared either in the Delivery Dependencies section or carried by
    # an explicit ``kc:depends-on ... mode="stack"`` marker in the body. The
    # latter is the compat path; honour it instead of regenerating a via-main
    # marker that would silently drop the strategy.
    sequence = from_prd.sequence
    if sequence != "stack" and explicit is not None and explicit.sequence == "stack":
        sequence = "stack"

    # ``stack`` sequencing chains each downstream onto a *single* upstream
    # branch. Multiple upstreams would need a DAG, which the runner does not
    # model (it forks from ``issue-<first>`` only), so reject it loudly instead
    # of silently ignoring every upstream but the first.
    if sequence == "stack" and len(deduped_issues) > 1:
        raise ValueError(
            "Sequence: stack supports exactly one upstream Issue, but "
            f"{len(deduped_issues)} were resolved: {deduped_issues}. "
            "Use Sequence: via-main for multiple upstreams."
        )

    return (
        gate_type,
        tuple(deduped_issues),
        sequence,
    )


def _materialize_prd_dependencies(
    *,
    repo_path: Path | None,
    current_prd_path: Path | None,
    prd_refs: tuple[str, ...],
) -> list[int]:
    """Resolve PRD path/name dependencies into Issue numbers.

    A referenced PRD must carry a real ``- GitHub Issue: .../issues/N`` link;
    otherwise the command fails fast with an actionable error.
    """
    if repo_path is None:
        raise ValueError(
            "Cannot resolve PRD dependencies from 'Depends on tasks/issues' "
            "without a repository path. Use GitHub Issue numbers such as '#42', "
            "or call issue creation with repo_path available."
        )

    repo_root = repo_path.resolve()
    current_path = current_prd_path.resolve() if current_prd_path else None
    issue_numbers: list[int] = []

    for prd_ref in prd_refs:
        referenced_prd_path = _resolve_dependency_prd_path(
            repo_root=repo_root,
            prd_ref=prd_ref,
        )
        if current_path is not None and referenced_prd_path == current_path:
            raise ValueError(
                "Cannot materialize PRD dependency "
                f"{prd_ref!r}: it resolves to the current PRD. Remove the "
                "self-dependency or replace it with the intended upstream "
                "Issue number, for example '#42'."
            )

        referenced_text = referenced_prd_path.read_text(encoding="utf-8")
        issue_number = _extract_prd_issue_number(referenced_text)
        if issue_number is not None:
            issue_numbers.append(issue_number)
            continue

        relative_path = _format_repo_relative_path(repo_root, referenced_prd_path)
        raise ValueError(
            "Cannot materialize PRD dependency "
            f"{prd_ref!r} resolved to {relative_path!r}: the referenced PRD "
            "has no '- GitHub Issue: .../issues/N' link. Create the upstream "
            "Issue first (kc issue create writes its link back into the PRD), "
            "or replace the dependency with a concrete Issue number such as '#42'."
        )

    return issue_numbers


def _extract_prd_issue_number(prd_text: str) -> int | None:
    """Extract the first GitHub Issue number linked from a PRD."""
    for line in prd_text.splitlines():
        if not ISSUE_LINK_LINE_RE.match(line):
            # 占位符（如 "(待创建)"）视为尚未关联 Issue。
            continue
        issue_number_match = ISSUE_NUMBER_RE.search(line)
        if issue_number_match is None:
            raise ValueError(f"Invalid GitHub Issue link in PRD: {line}")
        return int(issue_number_match.group("issue_number"))
    return None


def _resolve_dependency_prd_path(*, repo_root: Path, prd_ref: str) -> Path:
    """Resolve a PRD dependency reference to one Markdown file in the repo."""
    cleaned_ref = prd_ref.strip().strip("`").strip()
    if not cleaned_ref:
        raise ValueError(
            "Empty PRD dependency reference in 'Depends on tasks/issues'. "
            "Leave the field empty or write 'none' when there is no dependency."
        )

    raw_path = Path(cleaned_ref).expanduser()
    if raw_path.is_absolute():
        return _validate_dependency_prd_path(
            repo_root=repo_root,
            candidate_path=raw_path,
            prd_ref=cleaned_ref,
        )

    if "/" in cleaned_ref or "\\" in cleaned_ref:
        return _resolve_repo_relative_dependency_prd_path(
            repo_root=repo_root,
            relative_ref=cleaned_ref,
        )

    repo_relative_matches = _existing_markdown_path_candidates(repo_root / cleaned_ref)
    task_matches = _find_task_prd_matches(repo_root=repo_root, prd_ref=cleaned_ref)
    matches = _dedupe_paths([*repo_relative_matches, *task_matches])

    if not matches:
        raise ValueError(_format_missing_prd_dependency_error(cleaned_ref))
    if len(matches) > 1:
        matched_paths = ", ".join(
            _format_repo_relative_path(repo_root, matched_path) for matched_path in matches
        )
        raise ValueError(
            "Ambiguous PRD dependency reference "
            f"{cleaned_ref!r} in 'Depends on tasks/issues'. It matches "
            f"multiple PRD files: {matched_paths}. Use a repo-relative path, "
            "for example 'tasks/pending/<filename>.md'."
        )
    return _validate_dependency_prd_path(
        repo_root=repo_root,
        candidate_path=matches[0],
        prd_ref=cleaned_ref,
    )


def _resolve_repo_relative_dependency_prd_path(*, repo_root: Path, relative_ref: str) -> Path:
    """Resolve an explicit repo-relative PRD dependency path."""
    candidate_paths = _existing_markdown_path_candidates(repo_root / relative_ref)
    if not candidate_paths:
        raise ValueError(_format_missing_prd_dependency_error(relative_ref))
    return _validate_dependency_prd_path(
        repo_root=repo_root,
        candidate_path=candidate_paths[0],
        prd_ref=relative_ref,
    )


def _existing_markdown_path_candidates(candidate_path: Path) -> list[Path]:
    """Return existing path candidates, trying ``.md`` when omitted."""
    candidates = [candidate_path]
    if candidate_path.suffix == "":
        candidates.append(candidate_path.with_suffix(".md"))
    return [
        path.resolve() for path in candidates if path.is_file() and path.suffix.lower() == ".md"
    ]


def _find_task_prd_matches(*, repo_root: Path, prd_ref: str) -> list[Path]:
    """Find PRD dependency matches by filename or stem under ``tasks/``."""
    tasks_path = repo_root / "tasks"
    if not tasks_path.is_dir():
        return []
    matches: list[Path] = []
    for candidate_path in tasks_path.rglob("*.md"):
        if candidate_path.name == prd_ref or candidate_path.stem == prd_ref:
            matches.append(candidate_path.resolve())
    return sorted(matches, key=lambda path: path.as_posix())


def _validate_dependency_prd_path(*, repo_root: Path, candidate_path: Path, prd_ref: str) -> Path:
    """Validate that a resolved PRD path is a Markdown file inside the repo."""
    resolved_path = candidate_path.resolve()
    try:
        resolved_path.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(
            "PRD dependency reference "
            f"{prd_ref!r} resolves outside the repository. Use a GitHub Issue "
            "number such as '#42' or a PRD path under this repository."
        ) from exc
    if not resolved_path.is_file() or resolved_path.suffix.lower() != ".md":
        raise ValueError(_format_missing_prd_dependency_error(prd_ref))
    return resolved_path


def _dedupe_paths(candidate_paths: list[Path]) -> list[Path]:
    """Deduplicate paths while preserving deterministic order."""
    seen_paths: set[Path] = set()
    deduped_paths: list[Path] = []
    for candidate_path in candidate_paths:
        resolved_path = candidate_path.resolve()
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)
        deduped_paths.append(resolved_path)
    return sorted(deduped_paths, key=lambda path: path.as_posix())


def _format_missing_prd_dependency_error(prd_ref: str) -> str:
    """Return actionable guidance for an unresolved PRD dependency ref."""
    return (
        "Could not resolve PRD dependency reference "
        f"{prd_ref!r} in 'Depends on tasks/issues'. Use a GitHub Issue number "
        "such as '#42', a repo-relative PRD path such as "
        "'tasks/pending/P2-FEAT-20260527-190923-prd-from-issue.md', or a "
        "unique PRD filename/stem under 'tasks/'. For no dependency, leave "
        "the field empty or write 'none'."
    )


def _format_repo_relative_path(repo_root: Path, candidate_path: Path) -> str:
    """Format a path relative to the repository when possible."""
    try:
        return candidate_path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return candidate_path.as_posix()


# ---------------------------------------------------------------------------
# 主流程编排
# ---------------------------------------------------------------------------


_EVIDENCE_FORMAT_PROMPT_TEMPLATE = """\
判断以下 checklist item 是否明确要求执行者产出特定格式的证据文件。
只考虑 item 本身对"证据格式"的要求，忽略对参考文件、目录、外部资源的描述。

Item: "{item_text}"

选择规则：item 描述的是多步交互或异步状态变化的流程（点击 → 等待 → 出结果、
流式输出、跨页面跳转）时选 video；只是静态页面/单一状态才选 screenshot。

输出 JSON: {{"kind": "video"}}、{{"kind": "screenshot"}} 或 {{"kind": "none"}}
可选 kind: screenshot, pdf, txt, word, excel, csv, video, none
"""

_KNOWN_EVIDENCE_KINDS: frozenset[str] = frozenset(
    {"screenshot", "pdf", "txt", "word", "excel", "csv", "video", "none"}
)


def resolve_agent_name(
    agent_name: str,
    default: str = "claude",
    *,
    agents: Mapping[str, AgentSpec] | None = None,
) -> str:
    """Return a concrete agent name, falling back to *default* for unknown values.

    CLI and config accept ``"auto"`` / ``"none"`` as aliases; this helper
    normalises them to a real agent that ``IContentGenerator`` can execute.
    合法性由 agent 注册表成员校验：只有声明了 ``generate`` 用途（内容
    生成能力）的 agent 会被接受，其余回落默认值。

    Args:
        agent_name: 请求里的 agent 值（可能是路由别名或未注册名）。
        default: 别名 / 未注册名回落的 agent 名。
        agents: 生效注册表；``None`` 时只认内置 ``BUILTIN_AGENT_SPECS``。

    Raises:
        ValueError: 名字**已注册**但没声明 ``generate`` 用途。显式点名一个不能
            生成内容的 agent 是配置错误，不能悄悄换成 ``default`` 跑别的 agent；
            未注册的名字仍按既有行为回落默认值。
    """
    registry = agents if agents is not None else BUILTIN_AGENT_SPECS
    agent_spec = registry.get(agent_name)
    if agent_spec is None:
        return default
    if AGENT_PROFILE_GENERATE in agent_spec.profiles:
        return agent_name
    generate_capable = sorted(
        name for name, spec in registry.items() if AGENT_PROFILE_GENERATE in spec.profiles
    )
    raise ValueError(
        f"agent '{agent_name}' is registered but does not declare the "
        f"'{AGENT_PROFILE_GENERATE}' profile, so it cannot generate Issue content. "
        f"Pick one of {', '.join(generate_capable)} or declare "
        f"[agent_runner.agents.{agent_name}.profiles.generate] in the config."
    )


def _parse_evidence_format_with_agent(
    *,
    checklist_items: list[str],
    generator: IContentGenerator | None,
    cwd: Path,
    agent_name: str,
    agents: Mapping[str, AgentSpec] | None = None,
) -> dict[int, str]:
    """Use an agent to parse the required evidence format for each item.

    Returns a mapping ``{item_number: kind}``.  If the agent is unavailable
    or returns invalid JSON, returns an empty dict so the caller can fall
    back to regex matching.
    """
    if generator is None or not checklist_items:
        return {}

    resolved_agent = resolve_agent_name(agent_name, agents=agents)
    parsed_formats: dict[int, str] = {}

    for item_number, item_text in enumerate(checklist_items, start=1):
        prompt = _EVIDENCE_FORMAT_PROMPT_TEMPLATE.format(item_text=item_text)
        try:
            result = generator.generate(
                agent_name=resolved_agent,
                prompt=prompt,
                cwd=cwd,
                timeout=30,
            )
        except Exception:
            _logger.warning("Agent call failed for item %d, skipping", item_number)
            continue

        if result.return_code != 0:
            _logger.warning(
                "Agent exited with code %d for item %d: %s",
                result.return_code,
                item_number,
                result.stderr,
            )
            continue

        output_text = result.stdout.strip()
        if output_text.startswith("```"):
            lines = output_text.splitlines()
            if len(lines) >= 2:
                output_text = "\n".join(lines[1:-1]).strip()

        try:
            data = json.loads(output_text)
            if isinstance(data, dict):
                kind = str(data.get("kind", "")).strip().lower()
                if kind in _KNOWN_EVIDENCE_KINDS and kind != "none":
                    parsed_formats[item_number] = kind
        except json.JSONDecodeError:
            _logger.warning(
                "Agent output is not valid JSON for item %d: %s",
                item_number,
                output_text[:200],
            )

    return parsed_formats


def _build_evidence_format_markers(parsed_formats: dict[int, str]) -> str:
    """Build hidden ``kc:evidence-format`` markers from parsed formats."""
    if not parsed_formats:
        return ""
    lines = [
        f"<!-- kc:evidence-format item={num} kind={kind} -->"
        for num, kind in sorted(parsed_formats.items())
    ]
    return "\n".join(lines)


def create_issue_from_prd(
    *,
    request: IssueFromPrdRequest,
    github_client: IGitHubClient,
    process_runner: IProcessRunner | None = None,
    content_generator: IContentGenerator | None = None,
) -> str:
    """从 PRD 创建 GitHub Issue 并将 URL 回写到 PRD。

    编排流程：

    1. **路径解析** —— 将 ``prd_path`` 转换为绝对路径和仓库相对路径。
    2. **PRD 校验** —— 读取 PRD 并检查是否已存在 Issue 链接
       （防止重复创建，除非 ``force=True``）。
    3. **标题提取** —— 从 PRD H1 标题或文件名 slug 派生 fallback 标题；
       ``title_override`` 优先级最高。
    4. **标签组装** —— 构建初始标签集合（type、backlog、source，
       可选的 ready/agent 标签）。
    5. **发布预检**（可选）—— 当 ``publish_prd=True`` 时，
       验证分支状态和暂存区变更，然后捕获 ``PrdPublishContext`` 供第 9 步使用。
    6. **Fallback 正文** —— 基于 PRD 元数据构建确定的 Issue 正文
       （验收清单 + 引言 + 交付说明）。
    7. **内容生成**（可选）—— 当 ``generated_content_config`` 启用时，
       调用 ``generate_issue_content()``，遵循模块文档中描述的
       ``agent → template → fallback`` 级联策略。
       返回的 title/body 替换 fallback 值。
    8. **Issue 创建** —— 调用 ``github_client.create_issue()``。
    9. **回写** —— 将 Issue URL 持久化到 PRD 文件。
    10. **发布**（可选）—— 执行预检时捕获的发布上下文
        （stage、commit、push）。如果 ``queue_ready=True``，
        在 push 成功*之后*再添加 ready 标签。

    Args:
        request: Issue 创建请求。
        github_client: 与 GitHub 交互的客户端。
        process_runner: 用于 PRD 发布 Git 命令的可选 runner。
            当 ``publish_prd=True`` 时必需。
        content_generator: 用于 AI 生成 Issue 内容的可选生成器。
            当 ``generated_content_config`` 使用 agent 模式时必需。

    Returns:
        创建的 GitHub Issue URL。

    Raises:
        ValueError: 当 PRD 已有 GitHub Issue 链接且 ``force=False``，
            或当 ``publish_prd=True`` 时缺少 ``process_runner``。
        RuntimeError: 当 PRD 发布无法完成时（错误分支、暂存区冲突、
            push 失败等）。
    """

    # ------------------------------------------------------------------
    # 1. 解析路径，得到绝对路径（用于 I/O）和相对路径（用于 Issue 正文和提交信息）。
    # ------------------------------------------------------------------
    absolute_prd_path, relative_prd_path = resolve_prd_paths(request.repo_path, request.prd_path)

    # ------------------------------------------------------------------
    # 2. 读取 PRD 并防止意外重复创建 Issue。
    # ------------------------------------------------------------------
    prd_text = absolute_prd_path.read_text(encoding="utf-8")
    if not request.force and any(ISSUE_LINK_LINE_RE.match(line) for line in prd_text.splitlines()):
        raise ValueError("PRD already has a GitHub Issue link. Use --force to replace it.")

    # ------------------------------------------------------------------
    # 3. 派生 fallback 标题。当 PRD 没有 H1 标题时，使用文件名 slug（"-prd-" 之后部分）作为最后手段。
    # ------------------------------------------------------------------
    fallback_title = absolute_prd_path.stem.split("-prd-", maxsplit=1)[-1].replace("-", " ")
    fallback_title = (
        request.title_override
        or f"[{request.issue_type.title()}] {extract_title(prd_text, fallback_title)}"
    )

    # ------------------------------------------------------------------
    # 4. 构建标签。
    # ------------------------------------------------------------------
    effective_labels_config = request.labels_config or LabelConfig()
    labels = build_issue_labels(request, effective_labels_config)

    # ------------------------------------------------------------------
    # 4.5 解析并物化依赖声明。
    # ------------------------------------------------------------------
    gate_type, resolved_issues, sequence = _resolve_dependencies(
        prd_text,
        repo_path=request.repo_path,
        current_prd_path=absolute_prd_path,
        depends_on=request.depends_on,
    )
    dependency_marker = ""
    if gate_type == "hard" and resolved_issues:
        dependency_marker = format_dependency_marker(
            issue_numbers=resolved_issues,
            sequence=sequence,
        )

    # ------------------------------------------------------------------
    # 5. 发布预检（仅在 publish_prd=True 时执行）。
    # ------------------------------------------------------------------
    publish_context: PrdPublishContext | None = None
    if request.publish_prd:
        if process_runner is None:
            raise ValueError("process_runner is required when publish_prd=True.")
        current_branch = current_git_branch(request.repo_path, process_runner)
        validate_ready_publish_branch(
            current_branch=current_branch,
            git_base_branch=request.git_base_branch,
            queue_ready=request.queue_ready,
        )
        validate_staged_changes_are_prd_only(request.repo_path, relative_prd_path, process_runner)
        publish_context = PrdPublishContext(
            repo_path=request.repo_path,
            relative_prd_path=relative_prd_path,
            git_remote=request.git_remote,
            current_branch=current_branch,
        )

    # ------------------------------------------------------------------
    # 6. 构建确定的 fallback 正文。
    # ------------------------------------------------------------------
    fallback_body = build_issue_body(
        relative_prd_path=relative_prd_path,
        title=fallback_title,
        acceptance_items=extract_acceptance_items(prd_text),
        prd_text=prd_text,
        dependency_marker=dependency_marker,
    )

    # ------------------------------------------------------------------
    # 7. 可选的 AI 内容生成（agent → template → fallback）。
    # ------------------------------------------------------------------
    title = fallback_title
    body = fallback_body
    gc_config = request.generated_content_config
    if gc_config is not None:
        # PRD 级 ``content_generation`` 覆盖：来源 PRD 文件头部声明的 agent 高于
        # 矩阵 / 既有配置派生的 ``lifecycle_default_agent``。头部块非法时只告警，
        # 与流水线里的 ``attach_prd_lifecycle_overrides`` 保持同一"最佳努力"语义。
        try:
            prd_lifecycle_overrides = parse_prd_lifecycle_overrides(
                prd_text, prd_path=relative_prd_path
            )
        except ValueError as exc:
            _logger.warning(
                "Ignoring invalid lifecycle_agents block in '%s': %s", relative_prd_path, exc
            )
            prd_lifecycle_overrides = {}
        prd_content_generation_agent = prd_lifecycle_overrides.get("content_generation")
        if prd_content_generation_agent:
            gc_config = replace(gc_config, lifecycle_default_agent=prd_content_generation_agent)
    if gc_config is not None and gc_config.enabled:
        gc_context = build_issue_context(
            issue_type=request.issue_type,
            title=fallback_title,
            relative_prd_path=relative_prd_path,
            prd_text=prd_text,
            acceptance_items=extract_acceptance_items(prd_text),
        )
        gc_cwd = request.repo_path if content_generator is not None else None
        generated = generate_issue_content(
            config=gc_config,
            context=gc_context,
            fallback_title=fallback_title,
            fallback_body=fallback_body,
            generator=content_generator,
            cwd=gc_cwd,
            model_selection=request.model_selection,
        )
        title = generated.title
        body = generated.body

    # Ensure dependency marker survives AI-generated body as well.
    if dependency_marker and dependency_marker not in body:
        body = f"{body.rstrip()}\n\n{dependency_marker}\n"

    # 在锚点行尾追加 GitHub blob 链接；无 GitHub remote 时原样返回（幂等）。
    body = append_prd_github_link(body, repo_path=request.repo_path)

    # ------------------------------------------------------------------
    # 7.5 物化 Realistic Validation 区块。
    # 确定性步骤，独立于 AI 生成正文：waiver 声明物化为 hidden marker，
    # 否则把 PRD 的验证清单复制为 Issue body 的未勾选清单，
    # 供 runner 的证据门禁与 PR 人工签收清单消费。
    # ------------------------------------------------------------------
    # 物化前大声失败：oracle 块存在却不可解析会让清单为空、连带关掉整个证据
    # 门禁，必须在此拦下，绝不静默产出一个「无验证」的 Issue（Issue #191）。
    assert_realistic_validation_oracle_valid(prd_text)
    validation_checklist_items = extract_realistic_validation_items(prd_text)
    validation_waiver_reason = extract_validation_waiver_reason(prd_text)
    if validation_checklist_items or validation_waiver_reason is not None:
        format_markers = ""
        if request.parse_evidence_format_with_agent and content_generator is not None:
            parsed_formats = _parse_evidence_format_with_agent(
                checklist_items=validation_checklist_items,
                generator=content_generator,
                cwd=request.repo_path,
                agent_name=request.issue_agent,
                agents=request.agents,
            )
            format_markers = _build_evidence_format_markers(parsed_formats)
        evidence_dir_root = request.evidence_dir.strip("/")
        issue_evidence_dir = (
            f"{evidence_dir_root}/{relative_prd_path.stem}"
            if evidence_dir_uses_task_subdirs(evidence_dir_root)
            else evidence_dir_root
        )
        validation_section = build_issue_validation_section(
            checklist_items=validation_checklist_items,
            waiver_reason=validation_waiver_reason,
            format_waiver_reason=extract_evidence_format_waiver_reason(prd_text),
            language=request.validation_language,
            structured_evidence=request.structured_evidence,
            evidence_dir=issue_evidence_dir,
        )
        if format_markers:
            validation_section = f"{format_markers}\n\n{validation_section}"
        body = f"{body.rstrip()}\n\n{validation_section}\n"

    # ------------------------------------------------------------------
    # 8. 创建 GitHub Issue。
    # ------------------------------------------------------------------
    issue_url = github_client.create_issue(title=title, body=body, labels=labels)

    # ------------------------------------------------------------------
    # 9. 将 Issue URL 回写到 PRD。
    # ------------------------------------------------------------------
    write_issue_link(
        prd_text=prd_text,
        absolute_prd_path=absolute_prd_path,
        issue_url=issue_url,
        force=request.force,
    )

    # ------------------------------------------------------------------
    # 10. 发布 PRD 文件并可选地添加 ready 标签。
    # ------------------------------------------------------------------
    if publish_context is not None:
        publish_prd_file(publish_context, process_runner)
        if request.queue_ready:
            github_client.edit_issue_labels(
                parse_issue_number(issue_url), add=[effective_labels_config.ready]
            )

    _logger.info("Created GitHub Issue: %s", issue_url)
    return issue_url

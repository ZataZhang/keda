"""夜间批次聚合的 Issue / PR 来源资格、依赖排序与重试解析。"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig, PullRequestContext
from backend.core.use_cases.agent_runner_batch_aggregate_models import (
    REQUIRED_CHECKS_STATE as _REQUIRED_CHECKS_STATE,
    BatchAggregateError,
    BatchSource,
    BatchSourceResolutionRequest,
)
from backend.core.use_cases.agent_runner_dependencies import parse_dependency_marker
from backend.core.use_cases.agent_runner_feedback import (
    extract_prd_path,
    is_prd_archive_path,
    resolve_prd_archive_path,
)
from backend.core.use_cases.agent_runner_pr_body_contract import (
    find_aggregate_pr_body_contract_violations,
    find_pr_body_contract_violations,
    has_contract_annotation,
    parse_aggregate_source_marker,
    unique_prd_paths,
)
from backend.core.use_cases.issue_prd_github_link import resolve_github_slug

_logger = logging.getLogger(__name__)

__all__ = [
    "aggregate_branch_name",
    "build_aggregate_retry_command",
    "build_superseded_marker",
    "plan_merge_order",
    "resolve_aggregate_prd_paths",
    "resolve_batch_repo_identifier",
    "resolve_batch_sources",
    "validate_aggregate_candidate_count",
    "validate_aggregate_issue_numbers",
]


def build_aggregate_retry_command(repo_id: str | None, issue_numbers: Iterable[int]) -> str:
    """构造可复制的聚合重试命令（``kc pr aggregate --issue <N> ...``）。

    Args:
        repo_id: 目标仓库 id；``None`` 时省略 ``--repo-id``（由当前仓库解析）。
        issue_numbers: 批次来源 Issue 编号（升序去重）。

    Returns:
        单行重试命令字符串。
    """
    ordered = sorted({int(number) for number in issue_numbers})
    parts = ["kc pr aggregate"]
    if repo_id:
        parts.append(f"--repo-id {repo_id}")
    parts.extend(f"--issue {number}" for number in ordered)
    return " ".join(parts)


def validate_aggregate_issue_numbers(issue_numbers: Iterable[int]) -> tuple[int, ...]:
    """校验显式批次至少包含两个不同的正 Issue 编号。

    Args:
        issue_numbers: 操作员选择的 Issue 编号。

    Returns:
        原输入顺序保留的 Issue 编号元组。

    Raises:
        ValueError: 批次少于两个 Issue、编号非正或存在重复。
    """
    issue_number_tuple = tuple(int(number) for number in issue_numbers)
    if len(issue_number_tuple) < 2:
        raise ValueError("aggregate batch needs at least 2 Issue numbers")
    if any(number <= 0 for number in issue_number_tuple):
        raise ValueError("Issue numbers must be positive integers")
    if len(set(issue_number_tuple)) != len(issue_number_tuple):
        raise ValueError("aggregate batch cannot contain duplicate Issue numbers")
    return issue_number_tuple


def validate_aggregate_candidate_count(candidate_count: int) -> int:
    """校验自动队列本轮实际选中的 Issue 数量不少于两个。"""
    if candidate_count < 2:
        raise ValueError("aggregate batch needs at least 2 selected Issues")
    return candidate_count


def _validate_source_eligibility(sources: Sequence[BatchSource]) -> tuple[int, ...]:
    """校验来源集合的资格（数量、去重、base 一致、检查与可读性），返回 Issue 编号集。

    Raises:
        BatchAggregateError: 任一资格不满足。
    """
    try:
        issue_numbers = validate_aggregate_issue_numbers(source.issue_number for source in sources)
    except ValueError as exc:
        raise BatchAggregateError(
            str(exc),
            failure_category="eligibility",
        ) from exc
    pr_numbers = [source.pr_number for source in sources]
    source_branches = [source.branch for source in sources]
    if len(set(pr_numbers)) != len(pr_numbers) or len(set(source_branches)) != len(source_branches):
        raise BatchAggregateError(
            "aggregate batch has duplicate source PR numbers or source branches",
            failure_category="eligibility",
        )
    base_shas = {source.base_sha for source in sources}
    if len(base_shas) != 1 or not next(iter(base_shas)):
        raise BatchAggregateError(
            "aggregate batch sources do not share a single non-empty base SHA "
            f"(got {sorted(base_shas)})",
            failure_category="ordering",
        )
    for source in sources:
        if source.checks_state != _REQUIRED_CHECKS_STATE:
            raise BatchAggregateError(
                f"source PR #{source.pr_number} (Issue #{source.issue_number}) "
                f"required checks are {source.checks_state!r}, not SUCCESS",
                failure_category="eligibility",
            )
        if source.pr_number <= 0 or not source.head_sha or not source.branch:
            raise BatchAggregateError(
                f"Issue #{source.issue_number} has no unique readable source PR/branch",
                failure_category="eligibility",
            )
    return tuple(issue_numbers)


def _validate_dependencies(sources: Sequence[BatchSource], issue_numbers: set[int]) -> None:
    """校验显式依赖都指向批内已知 Issue（缺依赖 fail closed）。

    Raises:
        BatchAggregateError: 存在指向批外未知 Issue 的依赖。
    """
    for source in sources:
        for dependency in source.dependency_issue_numbers:
            if dependency not in issue_numbers:
                raise BatchAggregateError(
                    f"Issue #{source.issue_number} depends on #{dependency}, "
                    "which is not a member of this batch",
                    failure_category="ordering",
                )


def plan_merge_order(sources: Sequence[BatchSource]) -> tuple[BatchSource, ...]:
    """按显式依赖拓扑排序来源，同级以 Issue 编号升序打破平局。

    上游（被依赖）来源排在下游之前；依赖环、缺依赖、base 不一致、非 SUCCESS 检查、
    无可读来源 PR / 分支、批次不足两个来源，全部 fail closed。

    Args:
        sources: 已解析并校验过的来源集合。

    Returns:
        合并顺序的来源元组。

    Raises:
        BatchAggregateError: 资格或拓扑不满足。
    """
    issue_numbers = _validate_source_eligibility(sources)
    _validate_dependencies(sources, set(issue_numbers))

    by_number = {source.issue_number: source for source in sources}
    in_degree = {number: 0 for number in by_number}
    dependents: dict[int, list[int]] = defaultdict(list)
    for source in sources:
        for dependency in dict.fromkeys(source.dependency_issue_numbers):
            dependents[dependency].append(source.issue_number)
            in_degree[source.issue_number] += 1

    ready = sorted(number for number, degree in in_degree.items() if degree == 0)
    ordered: list[BatchSource] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_number[current])
        for child in dependents[current]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)
        ready.sort()

    if len(ordered) != len(by_number):
        remaining = sorted(set(by_number) - {source.issue_number for source in ordered})
        raise BatchAggregateError(
            f"aggregate dependency cycle detected among Issues {remaining}",
            failure_category="ordering",
        )
    return tuple(ordered)


def resolve_aggregate_prd_paths(sources: Sequence[BatchSource]) -> tuple[str, ...]:
    """收集并去重排序本批次所有来源的 PRD 路径（无 PRD 的来源不贡献路径）。"""
    return unique_prd_paths(source.prd_path for source in sources if source.prd_path)


def aggregate_branch_name(issue_numbers: Iterable[int]) -> str:
    """从批次成员构造稳定的集成分支名。"""
    return f"batch-{'-'.join(str(number) for number in sorted(set(issue_numbers)))}"


def resolve_batch_repo_identifier(*, repo_path: Path, repo_id: str, config: AppConfig) -> str:
    """从仓库配置或 Git remote 解析用于 GitHub PR 查询的 owner/name。"""
    identity = config.repositories.get(repo_id)
    configured_identifier = identity.github_repo if identity is not None else None
    repo_identifier = configured_identifier or resolve_github_slug(repo_path)
    if not repo_identifier:
        raise BatchAggregateError(
            f"Repository '{repo_id}' has no GitHub owner/name identity; configure github_repo.",
            failure_category="eligibility",
        )
    return repo_identifier


def _source_branch(issue_number: int) -> str:
    """返回 Runner 为 Issue 发布的固定来源分支名。"""
    return f"issue-{issue_number}"


def build_superseded_marker(total_pr_number: int) -> str:
    """构造可供聚合重试验证的来源 PR 取代 marker。"""
    return f"<!-- iar:aggregate-superseded version=1 total_pr={total_pr_number} -->"


def _read_source_pr_context(
    *,
    request: BatchSourceResolutionRequest,
    source_pr_number: int,
    source_pr_state: str,
    source_branch: str,
    total_pr_context: PullRequestContext | None,
) -> tuple[PullRequestContext, bool]:
    """读取开放来源 PR，或验证并读取属于当前总 PR 的已关闭来源。"""
    if source_pr_state in {"open", "draft"}:
        source_context = request.github_client.get_pull_request_context(
            source_branch, require_success=True
        )
        if source_context is None or source_context.number != source_pr_number:
            raise BatchAggregateError(
                f"source PR #{source_pr_number} is not the unique PR on {source_branch}",
                failure_category="eligibility",
            )
        return source_context, False

    if source_pr_state != "closed" or total_pr_context is None:
        raise BatchAggregateError(
            f"closed source PR #{source_pr_number} has no open aggregate PR to resume",
            failure_category="eligibility",
        )
    expected_marker = build_superseded_marker(total_pr_context.number or 0)
    source_comments = request.github_client.list_pr_comments(source_pr_number)
    if not any(expected_marker in comment for comment in source_comments):
        raise BatchAggregateError(
            f"closed source PR #{source_pr_number} was not closed by this aggregate batch",
            failure_category="eligibility",
        )
    source_context = request.github_client.get_pull_request_context_by_number(
        source_pr_number, require_success=True
    )
    if source_context is None or source_context.branch != source_branch:
        raise BatchAggregateError(
            f"closed source PR #{source_pr_number} no longer matches {source_branch}",
            failure_category="eligibility",
        )
    return source_context, True


def resolve_batch_sources(request: BatchSourceResolutionRequest) -> tuple[BatchSource, ...]:
    """读取并校验一个显式来源批次，支持同一总 PR 的部分关闭重试。

    资格门（逐 Issue 判定，任一不满足即整批拒绝）：Issue 必须仍为 open 且带
    ``config.labels.review``（默认 ``agent/review``）标签；每个 Issue 仅允许一个未合并
    来源 PR，且该 PR 的必需 checks 必须已经是 ``SUCCESS``。closed 来源只有在其评论含有
    当前开放总 PR 的 superseded marker 时才可重试。

    这两道门决定了单次队列运行的现实边界：同一轮 ``kc run --all-ready --aggregate-pr``
    刚发布的 Issue 此时通常还停在 ``agent/supervising``（监督阶段还没把 Issue 推进到
    review 标签），新建 Draft PR 的 checks 也仍是 PENDING，因此队列侧的聚合调用一般会在
    这里被拒，需要等 checks 与标签落定后再用 ``kc pr aggregate --issue ...`` 补一次。

    Args:
        request: 仓库、GitHub client、批次 Issue 和配置身份。

    Returns:
        已解析来源元组，顺序与输入 Issue 一致。

    Raises:
        BatchAggregateError: 来源 PR 不唯一、门禁未通过、身份不一致或不能安全重试。
    """
    try:
        issue_numbers = validate_aggregate_issue_numbers(request.issue_numbers)
    except ValueError as exc:
        raise BatchAggregateError(str(exc), failure_category="eligibility") from exc
    retry_command = build_aggregate_retry_command(request.repo_id, issue_numbers)
    batch_branch = aggregate_branch_name(issue_numbers)
    try:
        existing_total_context = request.github_client.get_pull_request_context(
            batch_branch, require_success=True
        )
    except Exception as exc:  # noqa: BLE001 - 权威 PR 查询失败时不可猜测是否可重试。
        raise BatchAggregateError(
            f"cannot resolve existing aggregate PR for {batch_branch}: {exc}",
            failure_category="eligibility",
            retry_command=retry_command,
        ) from exc
    if existing_total_context is not None:
        total_declaration = parse_aggregate_source_marker(existing_total_context.body)
        if (
            existing_total_context.number is None
            or existing_total_context.branch != batch_branch
            or existing_total_context.is_draft is not True
            or total_declaration is None
            or total_declaration.issue_numbers != tuple(sorted(issue_numbers))
        ):
            raise BatchAggregateError(
                f"existing PR on {batch_branch} is not a retryable total PR for this Issue set",
                failure_category="eligibility",
                retry_command=retry_command,
            )

    sources: list[BatchSource] = []
    for issue_number in issue_numbers:
        try:
            issue = request.github_client.get_issue(issue_number)
            if issue.state.upper() != "OPEN" or request.config.labels.review not in issue.labels:
                raise BatchAggregateError(
                    f"Issue #{issue_number} is not an open Issue carrying the "
                    f"`{request.config.labels.review}` label (aggregate members must have "
                    "finished their post-PR supervision first)",
                    failure_category="eligibility",
                )
            linked_prs = request.github_client.list_pull_requests_for_issue(
                request.repo_identifier, issue_number
            )
            if existing_total_context is not None:
                linked_prs = [
                    linked_pr
                    for linked_pr in linked_prs
                    if linked_pr.number != existing_total_context.number
                ]
            if len(linked_prs) != 1:
                raise BatchAggregateError(
                    f"Issue #{issue_number} does not have exactly one linked source PR "
                    f"(found {len(linked_prs)})",
                    failure_category="eligibility",
                )
            source_pr = linked_prs[0]
            if source_pr.merged or source_pr.state not in {"open", "draft", "closed"}:
                raise BatchAggregateError(
                    f"Issue #{issue_number} source PR #{source_pr.number} is merged or unsupported",
                    failure_category="eligibility",
                )
            source_branch = _source_branch(issue_number)
            source_context, source_pr_was_closed = _read_source_pr_context(
                request=request,
                source_pr_number=source_pr.number,
                source_pr_state=source_pr.state,
                source_branch=source_branch,
                total_pr_context=existing_total_context,
            )
            if (
                source_context.checks_state != _REQUIRED_CHECKS_STATE
                or not source_context.head_sha
                or not source_context.base_sha
            ):
                raise BatchAggregateError(
                    f"source PR #{source_pr.number} required checks are "
                    f"{source_context.checks_state!r}, not {_REQUIRED_CHECKS_STATE}, or its "
                    "head/base SHA metadata is not readable yet",
                    failure_category="eligibility",
                )
            if (
                "<!-- iar:direct-pr" in source_context.body
                or "<!-- iar:fast-merge" in source_context.body
            ):
                raise BatchAggregateError(
                    f"source PR #{source_pr.number} bypassed required review or verification gates",
                    failure_category="eligibility",
                )
            prd_path = extract_prd_path(issue.body)
            if prd_path is not None and (
                find_pr_body_contract_violations(source_context.body, issue.body)
                or has_contract_annotation(source_context.body)
            ):
                raise BatchAggregateError(
                    f"source PR #{source_pr.number} does not satisfy its single-PRD v1 contract",
                    failure_category="eligibility",
                )
            archived_prd_path = resolve_prd_archive_path(prd_path) if prd_path else None
            archived_prd_path = archived_prd_path or prd_path
            if archived_prd_path and not is_prd_archive_path(archived_prd_path):
                raise BatchAggregateError(
                    f"Issue #{issue_number} PRD path is not an archived repository path: "
                    f"{archived_prd_path}",
                    failure_category="eligibility",
                )
            dependency_declaration = parse_dependency_marker(issue.body)
            sources.append(
                BatchSource(
                    issue_number=issue_number,
                    issue_url=issue.url,
                    pr_number=source_pr.number,
                    pr_url=source_pr.url,
                    branch=source_branch,
                    head_sha=source_context.head_sha,
                    base_sha=source_context.base_sha,
                    checks_state=source_context.checks_state or "",
                    prd_path=archived_prd_path,
                    dependency_issue_numbers=(
                        dependency_declaration.issue_numbers if dependency_declaration else ()
                    ),
                    issue=issue,
                    pr_context=source_context,
                    source_pr_was_closed=source_pr_was_closed,
                )
            )
        except BatchAggregateError as exc:
            raise BatchAggregateError(
                f"Issue #{issue_number}: {exc}",
                failure_category=exc.failure_category,
                retry_command=retry_command,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - GitHub reads fail closed with a retry command.
            raise BatchAggregateError(
                f"Issue #{issue_number} source resolution failed: {exc}",
                failure_category="eligibility",
                retry_command=retry_command,
            ) from exc

    try:
        ordered_sources = plan_merge_order(sources)
    except BatchAggregateError as exc:
        raise BatchAggregateError(
            str(exc), failure_category=exc.failure_category, retry_command=retry_command
        ) from exc

    if existing_total_context is not None:
        contract_violations = find_aggregate_pr_body_contract_violations(
            existing_total_context.body,
            expected_prd_paths=resolve_aggregate_prd_paths(ordered_sources),
            expected_issue_numbers=issue_numbers,
            expected_source_pr_numbers=(source.pr_number for source in ordered_sources),
        )
        if contract_violations:
            raise BatchAggregateError(
                f"existing aggregate PR #{existing_total_context.number} does not match "
                f"the requested source set: {', '.join(contract_violations)}",
                failure_category="eligibility",
                retry_command=retry_command,
            )
    return tuple(sources)

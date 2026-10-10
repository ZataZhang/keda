"""夜间任务批次聚合为单一总 PR 的共享 core 用例。

一次显式启用的队列运行（``kc run --all-ready --aggregate-pr``）或一条手动 / 重试
命令（``kc pr aggregate --issue <N> ...``）在**全部选中任务各自通过既有门禁之后**，
把它们的来源分支按依赖顺序集成到一个隔离的 ``batch-*`` 分支，重新验证组合后的完整
代码树，发布**唯一一份**总 Draft PR，然后关闭并标注其来源 PR（来源分支与 PR 记录
全部保留）。任何一步失败都不提前关闭来源 PR，并输出可复制的重试命令。

设计边界（务必与 PRD §7.6 一致）：

- **本地可验证**（rv-1 / rv-2 / rv-3）：参数校验在 CLI 层；这里的依赖拓扑排序
  :func:`plan_merge_order`、隔离分支集成 :func:`build_aggregate_branch`、PRD 路径
  去重（复用 contract 的 :func:`unique_prd_paths`）与总 PR 正文构造，都在临时本地
  Git 与纯字符串上验证，不触碰 GitHub。
- **外部行为不验证**（rv-4）：:func:`resolve_batch_sources`、总 PR 发布、必需
  GitHub checks 读取与来源 PR 关闭需要真实 GitHub 状态；本轮不访问真实服务，也不以
  fake GitHub 结果作为通过证据。这些函数照常实现，但交付时明确披露为未验证。

依赖方向仍守 ``api -> core -> engines -> infrastructure``：Git 原语复用
:mod:`agent_runner_git`，GitHub 读写只经 ``IGitHubClient`` 端口，正文契约只经
:mod:`agent_runner_pr_body_contract`。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
)
from backend.core.use_cases.agent_runner_batch_aggregate_git import (
    AggregateBranchBuildRequest,
    AggregateBranchResult,
    AggregateGitError,
    AggregateGitRequest,
    aggregate_tree_is_clean,
    build_aggregate_branch as _build_aggregate_branch,
    create_aggregate_worktree,
    delete_unpublished_aggregate_branch,
    fetch_latest_base_sha,
    push_aggregate_branch,
    remove_aggregate_worktree,
    verify_source_heads_unchanged,
)
from backend.core.use_cases.agent_runner_batch_aggregate_models import (
    REQUIRED_CHECKS_STATE as _REQUIRED_CHECKS_STATE,
    AggregateConflictError,
    AggregatePRBodyContext,
    AggregateResult,
    BatchAggregateError,
    BatchAggregateRequest,
    BatchSource,
    BatchSourceResolutionRequest,
)
from backend.core.use_cases.agent_runner_batch_aggregate_sources import (
    aggregate_branch_name,
    build_aggregate_retry_command,
    build_superseded_marker,
    plan_merge_order,
    resolve_aggregate_prd_paths,
    resolve_batch_repo_identifier,
    resolve_batch_sources,
    validate_aggregate_candidate_count,
    validate_aggregate_issue_numbers,
)
from backend.core.use_cases.agent_runner_feedback import (
    is_prd_archive_path,
)
from backend.core.use_cases.agent_runner_git import (
    get_head_sha,
    list_git_remotes,
    run_verification,
)
from backend.core.use_cases.agent_runner_pr_body_contract import (
    build_aggregate_contract_annotation_block,
    build_aggregate_merge_acceptance_block,
    build_aggregate_source_marker,
    find_aggregate_pr_body_contract_violations,
    unique_prd_paths,
)
from backend.core.use_cases.run_agent_once import choose_agent
from backend.core.use_cases.run_verifier_agent import ValidationVerdict, run_verifier_gate

_logger = logging.getLogger(__name__)

_VERIFIER_PASS_LINE_PATTERN = re.compile(
    r"^(?:verdict|status|结论|判定|裁决|状态)\s*[:：]\s*pass(?:\b|[-—])",
    re.IGNORECASE,
)

__all__ = [
    "AggregateBranchResult",
    "AggregateConflictError",
    "AggregateResult",
    "BatchAggregateError",
    "BatchSource",
    "BatchSourceResolutionRequest",
    "BatchAggregateRequest",
    "AggregatePRBodyContext",
    "aggregate_batch",
    "build_aggregate_branch",
    "build_aggregate_retry_command",
    "build_total_pr_body",
    "plan_merge_order",
    "resolve_aggregate_prd_paths",
    "resolve_batch_repo_identifier",
    "resolve_batch_sources",
    "validate_aggregate_issue_numbers",
    "validate_aggregate_candidate_count",
    "verify_aggregate_tree",
]


def verify_aggregate_tree(
    *,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> tuple[list[CommandResult], bool]:
    """在组合后的完整树上运行仓库配置的验证命令（短路语义沿用 :func:`run_verification`）。

    返回 ``verification_commands`` 的执行结果与是否全部通过。独立 verifier 由
    聚合生命周期随后在同一组合 worktree 单独运行；本函数只覆盖本地整树验证命令。

    Args:
        worktree_path: 集成分支所在 worktree。
        config: 应用配置（读取 ``runner.verification_commands``）。
        process_runner: 进程运行器。

    Returns:
        ``(verification_results, passed)``。
    """
    verification_results = run_verification(worktree_path, config, process_runner)
    passed = bool(verification_results) and all(
        result.return_code == 0 for result in verification_results
    )
    return verification_results, passed


def build_aggregate_branch(
    request: AggregateBranchBuildRequest,
) -> AggregateBranchResult:
    """组合来源提交；将 Git conflict 映射为聚合生命周期错误。"""
    try:
        return _build_aggregate_branch(request)
    except AggregateGitError as exc:
        if exc.failure_category == "conflict" and exc.head_sha and exc.command_result:
            raise AggregateConflictError(
                exc.head_sha,
                exc.command_result,
            ) from exc
        raise


def _format_verification_summary(
    verification_results: Sequence[CommandResult],
    *,
    passed: bool,
    verifier_summaries: Sequence[str],
) -> str:
    """把组合树验证命令结果渲染成总 PR 正文里的批次验证摘要小节。"""
    status_text = "PASS" if passed else "FAIL"
    lines = ["## Batch Verification", "", f"- combined-tree verification: {status_text}", ""]
    for result in verification_results:
        command_text = " ".join(result.command)
        lines.append(f"- `{command_text}` → exit {result.return_code}")
    if verifier_summaries:
        lines.extend(
            [
                "",
                "### Independent verifier",
                "",
                *[f"- {summary}" for summary in verifier_summaries],
            ]
        )
    return "\n".join(lines)


def build_total_pr_body(context: AggregatePRBodyContext) -> str:
    """构造唯一总 Draft PR 的正文（来源清单 + 去重 PRD 集 + provenance + v2 接受声明）。

    正文用 v2 契约构造；若本地契约判定仍有缺项 / 重复 / 来源集合不匹配，追加
    不合规标注块（与单 PRD 软门同型），供人工审阅与合并队列硬门消费。
    """
    ordered_sources = sorted(context.sources, key=lambda source: source.issue_number)
    issue_numbers = [source.issue_number for source in ordered_sources]
    source_pr_numbers = [source.pr_number for source in ordered_sources]
    source_marker = build_aggregate_source_marker(
        issue_numbers=issue_numbers,
        source_pr_numbers=source_pr_numbers,
    )
    body_lines = [
        "Nightly batch aggregate PR: one Draft PR supersedes the listed source PRs.",
        "",
        source_marker,
        "",
        "## Batch Sources",
        "",
    ]
    body_lines.extend(
        [
            f"- Issue #{source.issue_number} <{source.issue_url}> "
            f"→ PR #{source.pr_number} <{source.pr_url}> · head `{source.head_sha}`"
            for source in ordered_sources
        ]
    )
    body_lines += ["", "## Unique PRD Set", ""]
    body_lines.extend(f"- `{prd_path}`" for prd_path in unique_prd_paths(context.prd_paths))
    body_lines.extend(["", "## Per-Issue Evidence", ""])
    for source in ordered_sources:
        if source.prd_path is None:
            body_lines.append(f"- Issue #{source.issue_number}: no PRD-backed evidence package")
            continue
        prd_stem = Path(source.prd_path).stem
        evidence_directory = f"tasks/evidence/{prd_stem}"
        evidence_links = ", ".join(
            f"[{label}]({evidence_directory}/{prd_stem}.{suffix}.md)"
            for label, suffix in (
                ("plan", "verification-plan"),
                ("evidence", "evidence-report"),
                ("verifier", "verifier-report"),
            )
        )
        body_lines.append(f"- Issue #{source.issue_number} · `{source.prd_path}`: {evidence_links}")
    body_lines += [
        "",
        _format_verification_summary(
            context.verification_results,
            passed=context.verification_passed,
            verifier_summaries=context.verifier_summaries,
        ),
        "",
        "## Base / Head / Tree Provenance",
        "",
        f"- base: `{context.base_sha}`",
        f"- aggregate head: `{context.head_sha}`",
        f"- aggregate tree: `{context.tree_sha}`",
        "",
        build_aggregate_merge_acceptance_block(context.prd_paths),
        "",
    ]
    pr_body = "\n".join(body_lines)
    violations = find_aggregate_pr_body_contract_violations(
        pr_body,
        expected_prd_paths=context.prd_paths,
        expected_issue_numbers=issue_numbers,
        expected_source_pr_numbers=source_pr_numbers,
    )
    if resolve_aggregate_prd_paths(ordered_sources) != unique_prd_paths(context.prd_paths):
        violations.append("prd-link")
    if violations:
        violations = sorted(set(violations))
        pr_body = f"{pr_body.rstrip()}\n\n{build_aggregate_contract_annotation_block(violations)}\n"
    return pr_body


# ---------------------------------------------------------------------------
# GitHub 生命周期属于 rv-4：产品入口实现完整；本轮不访问真实服务、不以 fake 作证据。
# ---------------------------------------------------------------------------


def _verifier_report_has_pass_verdict(report_text: str) -> bool:
    """识别独立 verifier 报告开头的明确 PASS verdict，忽略普通正文中的 PASS。"""
    for report_line in report_text.splitlines()[:40]:
        normalized_line = report_line.strip().strip("#>` ").replace("*", "")
        if not normalized_line:
            continue
        if normalized_line.upper() == "PASS":
            return True
        if _VERIFIER_PASS_LINE_PATTERN.match(normalized_line):
            return True
        if re.match(r"^(?:verdict|status|结论|判定|裁决|状态)\s*[:：]", normalized_line, re.I):
            return False
    return False


def _validate_aggregate_prd_evidence(worktree_path: Path, prd_paths: Sequence[str]) -> None:
    """要求组合树包含已归档 PRD、三份文本证据报告和明确的 PASS verifier verdict。"""
    repository_root = worktree_path.resolve()
    for prd_path_text in unique_prd_paths(prd_paths):
        prd_relative_path = Path(prd_path_text)
        if (
            prd_relative_path.is_absolute()
            or ".." in prd_relative_path.parts
            or not is_prd_archive_path(prd_path_text)
            or prd_relative_path.suffix.lower() != ".md"
        ):
            raise BatchAggregateError(
                f"aggregate PRD path is unsafe or not archived: {prd_path_text}",
                failure_category="verification",
            )
        prd_file_path = worktree_path / prd_relative_path
        evidence_dir = worktree_path / "tasks" / "evidence" / prd_relative_path.stem
        evidence_files = (
            evidence_dir / f"{prd_relative_path.stem}.verification-plan.md",
            evidence_dir / f"{prd_relative_path.stem}.evidence-report.md",
            evidence_dir / f"{prd_relative_path.stem}.verifier-report.md",
        )
        try:
            resolved_required_paths = tuple(
                required_path.resolve(strict=True)
                for required_path in (prd_file_path, *evidence_files)
            )
        except OSError as exc:
            raise BatchAggregateError(
                f"archived PRD or required evidence reports are missing for {prd_path_text}",
                failure_category="verification",
            ) from exc
        if any(
            not required_path.is_relative_to(repository_root) or not required_path.is_file()
            for required_path in resolved_required_paths
        ):
            raise BatchAggregateError(
                f"PRD evidence path escapes the aggregate tree for {prd_path_text}",
                failure_category="verification",
            )
        verifier_report_text = resolved_required_paths[3].read_text(encoding="utf-8")
        if not _verifier_report_has_pass_verdict(verifier_report_text):
            raise BatchAggregateError(
                f"independent verifier report is not PASS for {prd_path_text}",
                failure_category="verification",
            )


@dataclass(frozen=True)
class AggregateVerifierRequest:
    """组合树独立 verifier 的执行上下文。"""

    sources: tuple[BatchSource, ...]
    worktree_path: Path
    config: AppConfig
    process_runner: IProcessRunner


def _verify_aggregate_prds(request: AggregateVerifierRequest) -> tuple[str, ...]:
    """在完整组合树上对每个 PRD-backed 来源重跑现有独立 verifier。"""
    verifier_summaries: list[str] = []
    for source in request.sources:
        if source.prd_path is None:
            continue
        if source.issue is None:
            raise BatchAggregateError(
                f"Issue #{source.issue_number} has no issue context for independent verification",
                failure_category="verification",
            )
        builder_agent = choose_agent(source.issue, request.config, "auto")
        try:
            verdict: ValidationVerdict | None = run_verifier_gate(
                source.issue,
                request.worktree_path,
                request.config,
                request.process_runner,
                builder_agent,
            )
        except Exception as exc:  # noqa: BLE001 - verifier errors fail closed.
            raise BatchAggregateError(
                f"Issue #{source.issue_number} independent verifier failed: {exc}",
                failure_category="verification",
            ) from exc
        if verdict is None:
            raise BatchAggregateError(
                f"Issue #{source.issue_number} did not produce an independent verifier verdict",
                failure_category="verification",
            )
        if not verdict.passed:
            raise BatchAggregateError(
                f"Issue #{source.issue_number} independent verifier returned {verdict.risk.upper()}: "
                f"{verdict.findings}",
                failure_category="verification",
            )
        verifier_summaries.append(
            f"Issue #{source.issue_number}: {verdict.risk.upper()} by {verdict.agent or 'configured verifier'}"
        )
    return tuple(verifier_summaries)


@dataclass(frozen=True)
class SourcePRCloseoutRequest:
    """关闭来源 PR 时使用的总 PR 身份与重试上下文。"""

    github_client: IGitHubClient
    sources: tuple[BatchSource, ...]
    total_pr_url: str
    total_pr_number: int
    retry_command: str


def _close_source_prs(
    request: SourcePRCloseoutRequest,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """逐个关闭来源 PR 并附取代 marker，返回已完成和待重试编号。"""
    closed_numbers: list[int] = []
    pending_numbers: list[int] = []
    superseded_comment = (
        build_superseded_marker(request.total_pr_number)
        + f"\nSuperseded by the batch total PR: {request.total_pr_url}\n\n"
        "This source PR is closed as part of a nightly batch aggregation; its branch, "
        "comments, review history and Issue record are preserved. Review and merge the "
        "single total PR instead.\n\n"
        f"Retry aggregation safely with: `{request.retry_command}`"
    )
    for source in request.sources:
        if source.source_pr_was_closed:
            # 来源解析仅在来源 PR 含当前批次 superseded marker 时接纳已关闭 PR；
            # 重试时跳过关闭可避免重复写 GitHub 评论。
            closed_numbers.append(source.pr_number)
            continue
        try:
            request.github_client.close_pull_request(source.pr_number, comment=superseded_comment)
            closed_numbers.append(source.pr_number)
        except Exception as exc:  # noqa: BLE001 - 部分关闭必须逐项报告且可重试。
            _logger.error("failed to close source PR #%d: %s", source.pr_number, exc)
            pending_numbers.append(source.pr_number)
    return tuple(closed_numbers), tuple(pending_numbers)


def _same_aggregate_pr_body_text(github_read_body: str, generated_body: str) -> bool:
    """按 GitHub 回读后会保住的形态比较两份总 PR 正文。

    GitHub 会对提交过的 Markdown 做空白规范化（行尾空格、缩进、空行数量），逐字
    比较会把"同一份正文"判成不一致并以收尾失败阻断来源关闭；这里只丢弃这类排版
    差异，正文内容（marker、PRD 清单、声明句）仍逐行参与比较。

    Args:
        github_read_body: GitHub 回读到的 PR 正文。
        generated_body: 本地确定性生成的 PR 正文。

    Returns:
        两者在规范化后是否一致。
    """
    read_body_lines = [line.strip() for line in github_read_body.splitlines() if line.strip()]
    generated_body_lines = [line.strip() for line in generated_body.splitlines() if line.strip()]
    return read_body_lines == generated_body_lines


def aggregate_batch(request: BatchAggregateRequest) -> AggregateResult:
    """构建、验证和发布唯一总 Draft PR，然后按 checks 结果收尾来源 PR。

    Args:
        request: 已解析来源集合与本地 / GitHub 依赖。

    Returns:
        唯一总 PR、组合 tree 和来源收尾结果。

    Raises:
        BatchAggregateError: 资格、冲突、验证、发布或部分关闭失败；安全时带重试命令。
    """
    retry_command = build_aggregate_retry_command(
        request.repo_id, [source.issue_number for source in request.sources]
    )
    try:
        ordered_sources = plan_merge_order(request.sources)
    except BatchAggregateError as exc:
        raise BatchAggregateError(
            str(exc), failure_category=exc.failure_category, retry_command=retry_command
        ) from exc
    prd_paths = resolve_aggregate_prd_paths(ordered_sources)
    if prd_paths and not request.config.validation.verifier_enabled:
        raise BatchAggregateError(
            "aggregate PRD delivery requires the configured independent verifier",
            failure_category="verification",
            retry_command=retry_command,
        )
    try:
        remote_names = list_git_remotes(request.repo_path, request.process_runner)
    except Exception as exc:  # noqa: BLE001 - Git remote identity is required to fail closed.
        raise BatchAggregateError(
            f"cannot resolve the repository remote for aggregate integration: {exc}",
            failure_category="eligibility",
            retry_command=retry_command,
        ) from exc
    remote_name = (
        request.config.git.remote if request.config.git.remote in remote_names else "origin"
    )
    batch_branch = aggregate_branch_name(source.issue_number for source in ordered_sources)
    source_branches = tuple((source.branch, source.head_sha) for source in ordered_sources)
    git_request = AggregateGitRequest(
        repo_path=request.repo_path,
        remote_name=remote_name,
        base_branch=request.config.git.base_branch,
        batch_branch=batch_branch,
        source_branches=source_branches,
        process_runner=request.process_runner,
    )

    for attempt_number in range(2):
        try:
            aggregate_worktree = create_aggregate_worktree(git_request)
        except AggregateGitError as exc:
            raise BatchAggregateError(
                str(exc),
                failure_category=exc.failure_category,
                retry_command=retry_command,
            ) from exc
        try:
            branch_result = build_aggregate_branch(
                AggregateBranchBuildRequest(
                    worktree_path=aggregate_worktree.path,
                    batch_branch=batch_branch,
                    base_sha=aggregate_worktree.base_sha,
                    source_head_shas=tuple(source.head_sha for source in ordered_sources),
                    process_runner=request.process_runner,
                )
            )
            _validate_aggregate_prd_evidence(aggregate_worktree.path, prd_paths)
            try:
                verification_results, verification_passed = verify_aggregate_tree(
                    worktree_path=aggregate_worktree.path,
                    config=request.config,
                    process_runner=request.process_runner,
                )
            except Exception as exc:  # noqa: BLE001 - configured tree checks fail closed.
                raise BatchAggregateError(
                    f"combined-tree verification could not complete: {exc}",
                    failure_category="verification",
                    retry_command=retry_command,
                ) from exc
            if not verification_passed:
                raise BatchAggregateError(
                    "combined-tree verification failed; refusing to publish a total PR",
                    failure_category="verification",
                    retry_command=retry_command,
                )
            verifier_summaries = _verify_aggregate_prds(
                AggregateVerifierRequest(
                    sources=ordered_sources,
                    worktree_path=aggregate_worktree.path,
                    config=request.config,
                    process_runner=request.process_runner,
                )
            )
            if not aggregate_tree_is_clean(
                worktree_path=aggregate_worktree.path,
                process_runner=request.process_runner,
            ):
                raise BatchAggregateError(
                    "verification changed the aggregate worktree; refusing to publish an "
                    "uncommitted tree",
                    failure_category="verification",
                    retry_command=retry_command,
                )
            verified_head_sha = get_head_sha(aggregate_worktree.path, request.process_runner)
            verified_tree_result = request.process_runner.run(
                ["git", "rev-parse", "HEAD^{tree}"],
                cwd=aggregate_worktree.path,
                check=False,
            )
            if (
                verified_tree_result.return_code != 0
                or verified_head_sha != branch_result.head_sha
                or verified_tree_result.stdout.strip() != branch_result.tree_sha
            ):
                raise BatchAggregateError(
                    "independent verification changed the aggregate HEAD or tree; refusing to publish",
                    failure_category="verification",
                    retry_command=retry_command,
                )
            try:
                verify_source_heads_unchanged(git_request)
                latest_base_sha = fetch_latest_base_sha(git_request)
            except AggregateGitError as exc:
                raise BatchAggregateError(
                    str(exc),
                    failure_category=exc.failure_category,
                    retry_command=retry_command,
                ) from exc
            if latest_base_sha != aggregate_worktree.base_sha:
                if attempt_number == 0:
                    continue
                raise BatchAggregateError(
                    "target base advanced during both aggregate verification attempts; "
                    "no total PR was published",
                    failure_category="base-drift",
                    retry_command=retry_command,
                )

            body_context = AggregatePRBodyContext(
                sources=tuple(ordered_sources),
                prd_paths=prd_paths,
                verification_results=tuple(verification_results),
                verification_passed=True,
                base_sha=aggregate_worktree.base_sha,
                head_sha=branch_result.head_sha,
                tree_sha=branch_result.tree_sha,
                verifier_summaries=verifier_summaries,
            )
            total_pr_body = build_total_pr_body(body_context)
            body_violations = find_aggregate_pr_body_contract_violations(
                total_pr_body,
                expected_prd_paths=prd_paths,
                expected_issue_numbers=(source.issue_number for source in ordered_sources),
                expected_source_pr_numbers=(source.pr_number for source in ordered_sources),
            )
            if body_violations:
                raise BatchAggregateError(
                    f"generated aggregate PR body violates its contract: {body_violations}",
                    failure_category="verification",
                    retry_command=retry_command,
                )
            try:
                existing_total_context = request.github_client.get_pull_request_context(
                    batch_branch, require_success=True
                )
            except Exception as exc:  # noqa: BLE001 - 不在未知 PR 身份下写远端分支。
                raise BatchAggregateError(
                    f"cannot verify the aggregate PR branch identity: {exc}",
                    failure_category="publication",
                    retry_command=retry_command,
                ) from exc
            if existing_total_context is not None:
                if (
                    existing_total_context.number is None
                    or existing_total_context.is_draft is not True
                ):
                    raise BatchAggregateError(
                        f"existing PR on {batch_branch} is not a verifiable Draft aggregate PR",
                        failure_category="publication",
                        retry_command=retry_command,
                    )
                existing_violations = find_aggregate_pr_body_contract_violations(
                    existing_total_context.body,
                    expected_prd_paths=prd_paths,
                    expected_issue_numbers=(source.issue_number for source in ordered_sources),
                    expected_source_pr_numbers=(source.pr_number for source in ordered_sources),
                )
                if existing_violations:
                    raise BatchAggregateError(
                        f"existing PR #{existing_total_context.number} on {batch_branch} is not "
                        "the same aggregate batch",
                        failure_category="publication",
                        retry_command=retry_command,
                    )
            try:
                push_aggregate_branch(
                    git_request,
                    worktree_path=aggregate_worktree.path,
                    allow_update=existing_total_context is not None,
                    expected_remote_sha=(
                        existing_total_context.head_sha
                        if existing_total_context is not None
                        else None
                    ),
                )
            except AggregateGitError as exc:
                raise BatchAggregateError(
                    str(exc),
                    failure_category=exc.failure_category,
                    retry_command=retry_command,
                ) from exc
            if existing_total_context is None:
                try:
                    total_pr_url = request.github_client.create_draft_pr(
                        title=f"[Batch] Nightly aggregate ({len(ordered_sources)} Issues)",
                        body=total_pr_body,
                        base_branch=request.config.git.base_branch,
                        cwd=aggregate_worktree.path,
                    )
                except Exception as exc:  # noqa: BLE001 - 发布失败后清理本次未认领分支。
                    try:
                        created_total_context = request.github_client.get_pull_request_context(
                            batch_branch, require_success=True
                        )
                    except Exception as lookup_exc:  # noqa: BLE001 - 创建结果不确定时保留分支。
                        raise BatchAggregateError(
                            "total PR creation failed and its result could not be checked; "
                            f"batch branch was preserved: {lookup_exc}",
                            failure_category="publication",
                            retry_command=retry_command,
                        ) from exc
                    if created_total_context is not None:
                        total_pr_url = created_total_context.pr_url
                    else:
                        try:
                            delete_unpublished_aggregate_branch(
                                git_request,
                                expected_remote_sha=branch_result.head_sha,
                            )
                        except AggregateGitError as cleanup_exc:
                            raise BatchAggregateError(
                                "total PR creation failed and its unpublished batch branch "
                                f"could not be safely removed: {cleanup_exc}",
                                failure_category="publication",
                                retry_command=retry_command,
                            ) from exc
                        raise BatchAggregateError(
                            f"failed to create total Draft PR: {exc}",
                            failure_category="publication",
                            retry_command=retry_command,
                        ) from exc
            else:
                total_pr_url = existing_total_context.pr_url
                if not _same_aggregate_pr_body_text(existing_total_context.body, total_pr_body):
                    request.github_client.update_pull_request_body(
                        existing_total_context.number, total_pr_body
                    )
            try:
                total_pr_context = request.github_client.get_pull_request_context(
                    batch_branch, require_success=True
                )
            except Exception as exc:  # noqa: BLE001 - 无法确认 checks 时保留来源 PR。
                raise BatchAggregateError(
                    f"total PR checks could not be read; source PRs remain open: {exc}",
                    failure_category="closeout",
                    retry_command=retry_command,
                ) from exc
            if total_pr_context is None or total_pr_context.number is None:
                raise BatchAggregateError(
                    "total Draft PR context is unreadable after publication; source PRs remain "
                    f"open and total PR is {total_pr_url}",
                    failure_category="closeout",
                    retry_command=retry_command,
                )
            if (
                total_pr_context.head_sha != branch_result.head_sha
                or total_pr_context.base_sha != aggregate_worktree.base_sha
                or total_pr_context.is_draft is not True
            ):
                raise BatchAggregateError(
                    "total Draft PR refs or draft state do not match the published batch; source "
                    f"PRs remain open and total PR is {total_pr_url} "
                    f"(head={total_pr_context.head_sha} expected {branch_result.head_sha}; "
                    f"base={total_pr_context.base_sha} expected {aggregate_worktree.base_sha}; "
                    f"draft={total_pr_context.is_draft})",
                    failure_category="closeout",
                    retry_command=retry_command,
                )
            if not _same_aggregate_pr_body_text(total_pr_context.body, total_pr_body):
                raise BatchAggregateError(
                    "total Draft PR body does not match the generated aggregate contract; source "
                    f"PRs remain open and total PR is {total_pr_url}. This is a body write/read "
                    "difference, not a checks failure; re-run the retry command to re-apply "
                    "the body.",
                    failure_category="closeout",
                    retry_command=retry_command,
                )
            if total_pr_context.checks_state != _REQUIRED_CHECKS_STATE:
                check_state = total_pr_context.checks_state
                check_details = (
                    "; ".join(total_pr_context.checks_summary)
                    if total_pr_context.checks_summary
                    else "GitHub returned no individual check URLs"
                )
                raise BatchAggregateError(
                    "total Draft PR required checks are not all SUCCESS; source PRs remain open "
                    f"and total PR is {total_pr_url} "
                    f"(checks={check_state}; inspect: {check_details})",
                    failure_category="closeout",
                    retry_command=retry_command,
                )
            closed_numbers, pending_numbers = _close_source_prs(
                SourcePRCloseoutRequest(
                    github_client=request.github_client,
                    sources=tuple(ordered_sources),
                    total_pr_url=total_pr_url,
                    total_pr_number=total_pr_context.number,
                    retry_command=retry_command,
                )
            )
            if pending_numbers:
                raise BatchAggregateError(
                    f"partial source PR closeout: closed={list(closed_numbers)}, "
                    f"pending={list(pending_numbers)}",
                    failure_category="closeout",
                    retry_command=retry_command,
                )
            return AggregateResult(
                total_pr_url=total_pr_url,
                batch_branch=batch_branch,
                base_sha=aggregate_worktree.base_sha,
                head_sha=branch_result.head_sha,
                tree_sha=branch_result.tree_sha,
                source_pr_numbers_closed=closed_numbers,
                source_pr_numbers_pending=pending_numbers,
                prd_paths=prd_paths,
                verification_passed=True,
            )
        except BatchAggregateError as exc:
            if exc.retry_command:
                raise
            raise BatchAggregateError(
                str(exc),
                failure_category=exc.failure_category,
                retry_command=retry_command,
            ) from exc
        except AggregateGitError as exc:
            if exc.failure_category == "conflict" and exc.head_sha and exc.command_result:
                raise AggregateConflictError(
                    exc.head_sha,
                    exc.command_result,
                    retry_command=retry_command,
                ) from exc
            raise BatchAggregateError(
                str(exc),
                failure_category=exc.failure_category,
                retry_command=retry_command,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 未知阶段失败时来源 PR 仍保持开放。
            raise BatchAggregateError(
                f"aggregate lifecycle failed safely: {exc}",
                failure_category="publication",
                retry_command=retry_command,
            ) from exc
        finally:
            remove_aggregate_worktree(
                repo_path=request.repo_path,
                worktree_path=aggregate_worktree.path,
                process_runner=request.process_runner,
            )
    raise BatchAggregateError(
        "aggregate base changed repeatedly; no total PR was published",
        failure_category="base-drift",
        retry_command=retry_command,
    )

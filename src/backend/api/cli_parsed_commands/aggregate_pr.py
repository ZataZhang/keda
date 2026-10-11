"""``kc pr aggregate`` 已完成 Issue 批次的命令处理器。"""

from __future__ import annotations

from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_helpers import (
    _ensure_gh_auth_or_prompt,
    _resolve_cli_repository_targets,
    require_single_repository_target,
)
from backend.api.cli_output import CliError
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_console import console
from backend.core.use_cases.agent_runner_batch_aggregate import (
    BatchAggregateError,
    BatchAggregateRequest,
    BatchSourceResolutionRequest,
    aggregate_batch,
    resolve_batch_repo_identifier,
    resolve_batch_sources,
    validate_aggregate_issue_numbers,
)


def run_aggregate_pr_command(ctx: ParsedCommandContext) -> int:
    """解析同仓库已完成来源，并聚合或重试整个显式批次。"""
    try:
        issue_numbers = validate_aggregate_issue_numbers(ctx.parsed.issues)
    except (AttributeError, TypeError, ValueError) as exc:
        raise CliError(
            str(exc) or "kc pr aggregate requires at least two distinct --issue values.",
            code=ExitCode.USAGE,
            suggestion="kc pr aggregate --issue <N> --issue <N>",
        ) from exc

    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    repository = require_single_repository_target("pr aggregate", contexts)
    from backend.api import cli as _cli

    _cli.require_iar_repository_initialized(repository.repo_path, ctx.process_runner)
    _ensure_gh_auth_or_prompt(repository.repo_path, ctx.process_runner)
    github_client = ctx.github_client_factory(repository.repo_path)
    try:
        repo_identifier = resolve_batch_repo_identifier(
            repo_path=repository.repo_path,
            repo_id=repository.repo_id,
            config=repository.config,
        )
    except BatchAggregateError as exc:
        raise CliError(
            str(exc),
            code=ExitCode.USAGE,
        ) from exc
    source_request = BatchSourceResolutionRequest(
        repo_path=repository.repo_path,
        github_client=github_client,
        config=repository.config,
        repo_id=repository.repo_id,
        issue_numbers=issue_numbers,
        repo_identifier=repo_identifier,
    )
    try:
        sources = resolve_batch_sources(source_request)
        if ctx.parsed.dry_run:
            unique_prd_paths = sorted({source.prd_path for source in sources if source.prd_path})
            console.print(
                f"Batch preview: {len(sources)} Issues, " f"{len(unique_prd_paths)} unique PRDs."
            )
            for source in sources:
                console.print(
                    f"- Issue #{source.issue_number} → PR #{source.pr_number} "
                    f"({source.prd_path or 'no PRD'})"
                )
            for prd_path in unique_prd_paths:
                console.print(f"- PRD: {prd_path}")
            return 0
        result = aggregate_batch(
            BatchAggregateRequest(
                repo_path=repository.repo_path,
                repo_id=repository.repo_id,
                config=repository.config,
                github_client=github_client,
                process_runner=ctx.process_runner,
                sources=sources,
            )
        )
    except BatchAggregateError as exc:
        raise CliError(
            f"Batch aggregation failed ({exc.failure_category}): {exc}",
            code=ExitCode.GENERAL,
            suggestion=exc.retry_command,
        ) from exc

    console.print(f"Total Draft PR: {result.total_pr_url}")
    console.print(
        f"Batch: {len(sources)} Issues, {len(result.prd_paths)} unique PRDs; "
        f"base {result.base_sha}, head {result.head_sha}, tree {result.tree_sha}."
    )
    console.print(
        "Source PRs closed: "
        + ", ".join(f"#{number}" for number in result.source_pr_numbers_closed)
    )
    return 0

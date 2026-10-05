"""``iar run`` / ``iar daemon`` / ``iar review`` / ``iar review-daemon``
/ ``iar recover`` / ``iar blocked-continue`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher.
"""

from __future__ import annotations

from pathlib import Path

from backend.api.cli_console import console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_helpers import (
    _create_run_history_store_or_none,
    _ensure_gh_auth_or_prompt,
    _resolve_cli_repository_targets,
    _resolve_run_trigger,
)
from backend.api.cli_output import OUTPUT_FORMAT_JSON, CliError, emit
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_registry import _run_daemon_status_command
from backend.api.agent_runner_views.runner_live_view import create_runner_live_view
from backend.api import cli as _cli
from backend.core.use_cases.agent_runner_factory import logger


def _dry_run_preview(contexts: list, *, agent: str, max_issues: int) -> dict:
    """组装 ``iar run --dry-run`` 的机读预览（本轮执行计划，逐 Issue 明细在 stderr 日志）。"""
    return {
        "dry_run": True,
        "agent": agent,
        "max_issues": max_issues,
        "repositories": [
            {"repo_id": context.repo_id, "repo_path": str(context.repo_path)}
            for context in contexts
        ],
    }


def run_run_command(ctx: ParsedCommandContext) -> int:
    """``iar run``: run one agent-runner polling cycle."""
    # 机器输出只定义在 --dry-run 预览组合下：真实执行的过程输出是流式文本，
    # 混进 JSON 只会产出不可解析的垃圾。在解析任何仓库目标之前 fail fast。
    if ctx.output_format == OUTPUT_FORMAT_JSON and not ctx.parsed.dry_run:
        raise CliError(
            "--json/--output json 只在 --dry-run 预览下有定义；真实执行的过程输出是流式文本。",
            code=ExitCode.USAGE,
            suggestion="iar run --dry-run --json",
        )
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    # CLI --preset 一次性锚定（implementation 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    contexts = apply_cli_model_preset(contexts, ctx.parsed, anchored_stage="implementation")
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if contexts:
        _ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    content_generator = _cli.create_content_generator(
        ctx.process_runner, config=contexts[0].config if contexts else None
    )
    config_by_repo_path = {context.repo_path: context.config for context in contexts}

    def transcript_runner_factory(repo_path: Path) -> object:
        return _cli.create_transcript_runner(config=config_by_repo_path.get(repo_path))

    exit_code = _cli.run_agent_repositories_once(
        contexts=contexts,
        dry_run=ctx.parsed.dry_run,
        agent=ctx.parsed.agent,
        max_issues=ctx.parsed.max_issues or ctx.runner_settings.runner.max_issues,
        process_runner=ctx.process_runner,
        github_client_factory=ctx.github_client_factory,
        content_generator=content_generator,
        run_history_store=_create_run_history_store_or_none(),
        run_trigger=_resolve_run_trigger("run"),
        max_prd_issues=1,
        transcript_runner_factory=transcript_runner_factory,
        max_deliberation_issues=ctx.runner_settings.daemon.max_deliberation_issues,
    )
    if not ctx.parsed.dry_run or ctx.output_format != OUTPUT_FORMAT_JSON:
        return exit_code
    # 机器模式的 dry-run：stdout 只放执行计划预览（逐 Issue 明细已由日志走
    # stderr），dry-run 通过用退出码 10 与"真跑了并成功"的 0 区分开。
    emit(
        _dry_run_preview(
            contexts,
            agent=ctx.parsed.agent,
            max_issues=ctx.parsed.max_issues or ctx.runner_settings.runner.max_issues,
        ),
        fmt=OUTPUT_FORMAT_JSON,
    )
    if exit_code:
        return exit_code
    return int(ExitCode.DRY_RUN_OK)


def run_daemon_command(ctx: ParsedCommandContext) -> int:
    """``iar daemon``: run daemon continuously or report status."""
    daemon_command = getattr(ctx.parsed, "daemon_command", "run")
    if daemon_command == "status":
        return _run_daemon_status_command(
            parsed=ctx.parsed,
            process_runner=ctx.process_runner,
            runner_settings=ctx.runner_settings,
            repo_id=ctx.repo_id,
            repo_override=ctx.repo_override,
        )
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    # CLI --preset 一次性锚定（implementation 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    contexts = apply_cli_model_preset(contexts, ctx.parsed, anchored_stage="implementation")
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if contexts:
        _ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    interval = (
        ctx.parsed.interval
        if ctx.parsed.interval is not None
        else ctx.runner_settings.daemon.run_interval_seconds
    )
    # Parallel execution: --concurrency overrides the toml default
    # ([agent_runner.runner].max_concurrent_issues; 1 = sequential).
    # A per-Issue live view (Rich on a TTY, plain otherwise) is created
    # only when running >1 in parallel; the sequential path is unchanged.
    daemon_concurrency = (
        getattr(ctx.parsed, "concurrency", None) or ctx.runner_settings.runner.max_concurrent_issues
    )
    daemon_output_view = create_runner_live_view() if daemon_concurrency > 1 else None

    def content_generator_factory(repo_path: Path):
        return _cli.create_content_generator(
            ctx.process_runner, config=config_by_repo_path.get(repo_path)
        )

    config_by_repo_path = {context.repo_path: context.config for context in contexts}

    def transcript_runner_factory(repo_path: Path) -> object:
        return _cli.create_transcript_runner(config=config_by_repo_path.get(repo_path))

    # Single-instance guard: a second daemon for an already-served
    # repository would double the queue polling and agent spawns, so
    # refuse to start rather than pile up duplicate daemons.
    daemon_repo_ids = [context.repo_id for context in contexts]
    daemon_locks_dir = _cli.daemon_lock_dir(ctx.runner_settings.console.process_registry_path)
    try:
        acquired_daemon_locks = _cli.acquire_daemon_locks(daemon_locks_dir, daemon_repo_ids)
    except _cli.DaemonAlreadyRunningError as already_running:
        raise CliError(
            str(already_running),
            code=ExitCode.CONFLICT,
            suggestion="iar daemon status",
        ) from already_running
    try:
        _cli.run_agent_daemon(
            contexts=contexts,
            interval=interval,
            agent=ctx.parsed.agent,
            max_issues=ctx.parsed.max_issues or ctx.runner_settings.runner.max_issues,
            process_runner=ctx.process_runner,
            github_client_factory=ctx.github_client_factory,
            content_generator_factory=content_generator_factory,
            run_history_store=_create_run_history_store_or_none(),
            run_trigger=_resolve_run_trigger("daemon"),
            max_prd_issues=1,
            transcript_runner_factory=transcript_runner_factory,
            max_deliberation_issues=ctx.runner_settings.daemon.max_deliberation_issues,
            concurrency=daemon_concurrency,
            output_view=daemon_output_view,
            reclaim_stale_running=ctx.runner_settings.daemon.reclaim_stale_running,
            reclaim_ttl_seconds=ctx.runner_settings.daemon.reclaim_ttl_seconds,
            # Continuous backlog scheduling: injected as a factory so core never
            # constructs infrastructure objects itself. Repositories that did not
            # opt into the fast lane (autopilot.enabled) skip the stage entirely.
            backlog_store_factory=_cli.create_backlog_store,
        )
    finally:
        _cli.release_daemon_locks(acquired_daemon_locks)
    return 0


def run_review_command(ctx: ParsedCommandContext) -> int:
    """``iar review``: one supervisor review polling cycle."""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    # CLI --preset 一次性锚定（supervisor 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    review_contexts = apply_cli_model_preset(contexts, ctx.parsed, anchored_stage="supervisor")
    for context in review_contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if review_contexts:
        _ensure_gh_auth_or_prompt(review_contexts[0].repo_path, ctx.process_runner)
    aggregated_exit_code = 0
    for context in review_contexts:
        github_client = ctx.github_client_factory(context.repo_path)
        try:
            repo_exit_code = _cli.review_once(
                repo_path=context.repo_path,
                config=context.config,
                dry_run=ctx.parsed.dry_run,
                agent=ctx.parsed.agent,
                max_issues=ctx.parsed.max_issues or ctx.runner_settings.runner.max_issues,
                github_client=github_client,
                process_runner=ctx.process_runner,
                run_history_store=_create_run_history_store_or_none(),
                repo_id=context.repo_id,
            )
            if repo_exit_code != 0:
                aggregated_exit_code = 1
        except Exception as exc:  # noqa: BLE001
            aggregated_exit_code = 1
            logger.error(
                "Repository '%s' review_once failed: %s",
                context.repo_id,
                exc,
            )
    return aggregated_exit_code


def run_review_daemon_command(ctx: ParsedCommandContext) -> int:
    """``iar review-daemon``: run supervisor review continuously."""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    # CLI --preset 一次性锚定（supervisor 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    contexts = apply_cli_model_preset(contexts, ctx.parsed, anchored_stage="supervisor")
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if contexts:
        _ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    interval = (
        ctx.parsed.interval
        if ctx.parsed.interval is not None
        else ctx.runner_settings.daemon.review_interval_seconds
    )
    _cli.run_review_daemon(
        contexts=contexts,
        interval=interval,
        agent=ctx.parsed.agent,
        max_issues=ctx.parsed.max_issues or ctx.runner_settings.runner.max_issues,
        process_runner=ctx.process_runner,
        github_client_factory=ctx.github_client_factory,
        run_history_store=_create_run_history_store_or_none(),
    )
    return 0


def run_recover_command(ctx: ParsedCommandContext) -> int:
    """``iar recover``: resume a failed publish operation for an Issue."""
    from backend.core.use_cases.recover_publish import (
        PublishRecoveryError,
        PublishRecoveryRequest,
        recover_publish_issue,
    )

    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if len(contexts) != 1:
        raise CliError(
            "iar recover requires exactly one target repository. "
            "Use --repo or --repo-id to specify.",
            code=ExitCode.USAGE,
            suggestion="iar registry list",
        )
    context = contexts[0]
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    # 恢复发布的 PR 复用正常发布的正文生成，因此需要内容生成器；缺失时正常发布
    # 用例会退回配置的 template / fallback 正文。
    content_generator = _cli.create_content_generator(ctx.process_runner, config=context.config)
    request = PublishRecoveryRequest(
        issue_number=ctx.parsed.issue,
        expected_branch=ctx.parsed.branch,
    )
    try:
        result = recover_publish_issue(
            request=request,
            repo_path=context.repo_path,
            config=context.config,
            github_client=github_client,
            process_runner=ctx.process_runner,
            content_generator=content_generator,
        )
        logger.info(
            "Publish recovered for Issue #%d: %s",
            result.issue_number,
            result.pr_url,
        )
        console.print(
            f"[green]Publish recovered for Issue #{result.issue_number}:[/] {result.pr_url}"
        )
        return 0
    except PublishRecoveryError as exc:
        logger.error(
            "Publish recovery failed (category=%s): %s",
            exc.failure_category,
            exc,
        )
        return 1


def run_blocked_continue_command(ctx: ParsedCommandContext) -> int:
    """``iar blocked-continue``: resume a blocked Issue after fixing paths."""
    from backend.core.use_cases.blocked_continue import (
        BlockedContinueError,
        blocked_continue_issue,
    )
    from backend.api.cli_console import error_console

    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if len(contexts) != 1:
        raise CliError(
            "iar blocked-continue requires exactly one target repository. "
            "Use --repo or --repo-id to specify.",
            code=ExitCode.USAGE,
            suggestion="iar registry list",
        )
    context = contexts[0]
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    try:
        claimed = blocked_continue_issue(
            issue_number=ctx.parsed.issue,
            repo_path=context.repo_path,
            config=context.config,
            agent=ctx.parsed.agent,
            github_client=github_client,
            process_runner=ctx.process_runner,
        )
        if claimed:
            console.print(f"[green]Issue #{ctx.parsed.issue} resumed successfully.[/]")
            return 0
        console.print(f"[yellow]Issue #{ctx.parsed.issue} was claimed by another runner.[/]")
        return 0
    except BlockedContinueError as exc:
        logger.error("blocked-continue failed: %s", exc)
        error_console.print(f"[red]blocked-continue failed:[/] {exc}")
        return 1


__all__ = [
    "run_blocked_continue_command",
    "run_daemon_command",
    "run_recover_command",
    "run_review_command",
    "run_review_daemon_command",
    "run_run_command",
]

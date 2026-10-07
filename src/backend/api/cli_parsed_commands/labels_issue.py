"""``kc labels`` / ``kc issue create`` / ``kc issue list`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher.
"""

from __future__ import annotations

from pathlib import Path

from backend.api.cli_console import console, error_console
from backend.api.cli_helpers import _resolve_cli_repository_targets
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    CliError,
    emit,
    emit_json,
)
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api import cli as _cli
from backend.core.use_cases.agent_runner_factory import logger
from backend.api.cli_utils import _format_cli_exception


def run_labels_command(ctx: ParsedCommandContext) -> int:
    """``kc labels sync``: sync standard labels to the target repository."""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if contexts:
        _cli._ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    for context in contexts:
        github_client = ctx.github_client_factory(context.repo_path)
        _cli.sync_labels(
            labels_config=context.config.labels,
            github_client=github_client,
            agent_registry=context.config.agents,
        )
    logger.info("Labels are ready.")
    return 0


def _require_issue_create_source(
    parsed: object, *, raw_prd_paths: list[str], from_prompt: str | None
) -> None:
    """校验「PRD 路径」与 ``--from-prompt`` 二者必居其一，且旗标不互相矛盾。

    两种输入方式决定完全不同的产物（有无 PRD 锚点），所以不允许混用，也不允许
    在 ``--from-prompt`` 下继续给只服务于 PRD 的旗标 —— 静默忽略会让人以为
    PRD 被发布了。

    Args:
        parsed: 解析后的命令行参数（读取 ``publish_prd`` / ``force`` / ``require_validation``）。
        raw_prd_paths: 位置参数给出的 PRD 文件/目录。
        from_prompt: ``--from-prompt`` 的文本；未给出时为 ``None``。

    Raises:
        CliError: 二者都不给、二者都给、需求文本为空，或旗标与输入方式矛盾（退出码 2）。
    """
    has_prd_paths = bool(raw_prd_paths)
    if has_prd_paths and from_prompt is not None:
        raise CliError(
            "kc issue create takes either PRD path arguments or --from-prompt, not both.",
            code=ExitCode.USAGE,
            suggestion='kc issue create --from-prompt "<需求>"（或 kc issue create <prd-path>）',
        )
    if not has_prd_paths and from_prompt is None:
        raise CliError(
            "kc issue create needs a requirement source: PRD path argument(s) or --from-prompt.",
            code=ExitCode.USAGE,
            suggestion='kc issue create --from-prompt "<一句话需求>"',
        )
    if from_prompt is None:
        if getattr(parsed, "require_validation", False):
            raise CliError(
                "--require-validation only applies to --from-prompt; a PRD-backed Issue "
                "takes its acceptance checklist from the PRD itself.",
                code=ExitCode.USAGE,
                suggestion='kc issue create --from-prompt "<需求>" --require-validation',
            )
        return
    if not from_prompt.strip():
        raise CliError(
            "--from-prompt requires a non-empty requirement text.",
            code=ExitCode.USAGE,
            suggestion='kc issue create --from-prompt "<一句话需求>"',
        )
    conflicting_flags = ", ".join(
        flag
        for flag, attr in (("--publish-prd", "publish_prd"), ("--force", "force"))
        if getattr(parsed, attr, None) is not None
    )
    if conflicting_flags:
        raise CliError(
            f"{conflicting_flags} only apply to PRD-path creation; "
            "--from-prompt creates an Issue with no PRD to publish or overwrite.",
            code=ExitCode.USAGE,
            suggestion='kc issue create --from-prompt "<需求>"',
        )


def _resolve_content_generation_setup(ctx: ParsedCommandContext, context: object, target_name: str):
    """解析 ``content_generation`` 阶段的模型选择，并按目标是否启用 agent 模式造生成器。

    Args:
        ctx: 已解析命令上下文。
        context: 单仓运行上下文（提供 ``config``）。
        target_name: 生成目标字段名（``issue_from_prd`` / ``issue_from_prompt``）。

    Returns:
        ``(model_selection, content_generator)`` 元组；未启用 agent 模式时
        ``content_generator`` 为 ``None``。
    """
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset_to_config
    from backend.core.use_cases.lifecycle_agent_resolution import (
        resolve_lifecycle_model_selection,
    )

    gc_config = context.config.generated_content
    anchored_config = apply_cli_model_preset_to_config(
        context.config, ctx.parsed, anchored_stage="content_generation"
    )
    selection = resolve_lifecycle_model_selection("content_generation", anchored_config)
    target_config = getattr(gc_config, target_name)
    content_generator = None
    if gc_config.enabled and target_config.enabled and target_config.mode == "agent":
        content_generator = _cli.create_content_generator(ctx.process_runner)
    return selection, content_generator


def _run_issue_create_from_prompt(
    ctx: ParsedCommandContext, *, context: object, from_prompt: str, machine_mode: bool
) -> int:
    """``kc issue create --from-prompt``：建一个不引用任何 PRD 的 Issue。

    本路径不读工作区文件、不写回链、不发布任何内容，因此工作区必须零 diff。
    """
    _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    _cli._ensure_gh_auth_or_prompt(context.repo_path, ctx.process_runner)
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    selection, content_generator = _resolve_content_generation_setup(
        ctx, context, "issue_from_prompt"
    )
    require_validation = bool(getattr(ctx.parsed, "require_validation", False))
    try:
        issue_url = _cli.create_issue_from_prompt(
            request=_cli.IssueFromPromptRequest(
                repo_path=context.repo_path,
                prompt_text=from_prompt,
                issue_type=ctx.parsed.type,
                title_override=ctx.parsed.title,
                queue_ready=ctx.parsed.ready,
                issue_agent=ctx.parsed.agent,
                labels_config=context.config.labels,
                depends_on=tuple(getattr(ctx.parsed, "depends_on", []) or []),
                require_validation=require_validation,
                generated_content_config=context.config.generated_content,
                validation_language=context.config.validation.language,
                structured_evidence=context.config.validation.structured_evidence,
                evidence_dir=context.config.validation.evidence_dir,
                model_selection=selection,
            ),
            github_client=github_client,
            content_generator=content_generator,
        )
    except Exception as exc:  # noqa: BLE001 - 与 PRD 路径一致：单条失败报失败清单。
        detail = _format_cli_exception(exc)
        logger.error("Failed to create Issue from --from-prompt:\n%s", detail)
        if machine_mode:
            emit_json({"created": [], "skipped": [], "failed": [from_prompt], "note": None})
        else:
            error_console.print("[red]Failed to create GitHub Issue from --from-prompt:[/]")
            error_console.print(detail, markup=False)
        return ExitCode.GENERAL

    if machine_mode:
        emit_json(
            {
                "created": [
                    {
                        "prompt": from_prompt,
                        "issue_url": issue_url,
                        "ready": bool(ctx.parsed.ready),
                        "require_validation": require_validation,
                    }
                ],
                "skipped": [],
                "failed": [],
                "note": None,
            }
        )
    else:
        console.print(f"[green]Created GitHub Issue:[/] {issue_url}")
    if not ctx.parsed.ready:
        logger.info(
            "Issue created without '%s' label. " "Use --ready if you want a runner to pick it up.",
            context.config.labels.ready,
        )
    logger.info("Created GitHub Issue: %s", issue_url)
    return ExitCode.SUCCESS


def run_issue_create_command(ctx: ParsedCommandContext) -> int:
    """``kc issue create``: create GitHub Issues from PRD files or a prompt."""
    raw_prd_paths = list(getattr(ctx.parsed, "prd_paths", []) or [])
    from_prompt = getattr(ctx.parsed, "from_prompt", None)
    machine_mode = ctx.output_format == OUTPUT_FORMAT_JSON
    created_issues: list[dict[str, object]] = []

    _require_issue_create_source(ctx.parsed, raw_prd_paths=raw_prd_paths, from_prompt=from_prompt)

    context = _cli.resolve_issue_from_prd_target(
        ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_path_override=ctx.repo_override,
        cwd=Path.cwd(),
    )
    if from_prompt is not None:
        return _run_issue_create_from_prompt(
            ctx, context=context, from_prompt=from_prompt, machine_mode=machine_mode
        )
    try:
        prd_paths, skipped_prd_paths = _cli._expand_prd_paths(context.repo_path, raw_prd_paths)
    except ValueError as exc:
        raise CliError(
            str(exc),
            code=ExitCode.USAGE,
            suggestion="kc issue create <prd-path>（文件或包含 PRD 的目录）",
        ) from exc

    for skipped_prd_path in skipped_prd_paths:
        if not machine_mode:
            console.print(f"[yellow]Skipped PRD with existing Issue:[/] {skipped_prd_path}")
        logger.info("Skipped PRD with existing Issue: %s", skipped_prd_path)

    if not prd_paths:
        if machine_mode:
            emit_json(
                {
                    "created": [],
                    "skipped": list(skipped_prd_paths),
                    "failed": [],
                    "note": "所有请求的 PRD 都已存在对应 Issue。",
                }
            )
        else:
            console.print(
                "[green]All PRDs in the requested directories already have GitHub Issues.[/]"
            )
        return 0

    if len(prd_paths) > 1 and ctx.parsed.title is not None:
        raise CliError(
            "--title cannot be used when creating Issues from multiple PRDs.",
            code=ExitCode.USAGE,
            suggestion="kc issue create <prd-path> --title <title>",
        )

    _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    _cli._ensure_gh_auth_or_prompt(context.repo_path, ctx.process_runner)
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    # content_generation 阶段：CLI --preset 一次性锚定 + 配置绑定解析。
    content_generation_selection, content_generator = _resolve_content_generation_setup(
        ctx, context, "issue_from_prd"
    )
    gc_config = context.config.generated_content
    # --publish-prd / --force 是三态（未给出 / 显式开 / 显式关）：``--from-prompt``
    # 需要把「没提」和「显式 --no-publish-prd」区分开（后者是矛盾指令，要报错），
    # PRD 路径在这里把「没提」归一回原本的默认值，行为逐字不变。
    publish_prd = True if ctx.parsed.publish_prd is None else ctx.parsed.publish_prd
    force = bool(ctx.parsed.force)

    failed_prd_paths: list[str] = []
    for prd_path_text in prd_paths:
        # publish_prd 默认开启；仅当用户显式 --no-publish-prd 时，
        # 先把 queue_ready 压成 False，避免 Issue 还没发布就已经 ready，
        # runner 在 worktree 里读到过时 PRD。交互式 prompt 在 push 成功后再补 ready。
        queue_ready_for_request = ctx.parsed.ready if publish_prd else False
        try:
            _, relative_prd_path = _cli.resolve_prd_paths(context.repo_path, Path(prd_path_text))
            issue_url = _cli.create_issue_from_prd(
                request=_cli.IssueFromPrdRequest(
                    repo_path=context.repo_path,
                    prd_path=Path(prd_path_text),
                    issue_type=ctx.parsed.type,
                    title_override=ctx.parsed.title,
                    queue_ready=queue_ready_for_request,
                    issue_agent=ctx.parsed.agent,
                    labels_config=context.config.labels,
                    force=force,
                    publish_prd=publish_prd,
                    git_remote=context.config.git.remote,
                    git_base_branch=context.config.git.base_branch,
                    generated_content_config=gc_config,
                    depends_on=tuple(getattr(ctx.parsed, "depends_on", []) or []),
                    parse_evidence_format_with_agent=context.config.validation.parse_evidence_format_with_agent,
                    validation_language=context.config.validation.language,
                    structured_evidence=context.config.validation.structured_evidence,
                    evidence_dir=context.config.validation.evidence_dir,
                    model_selection=content_generation_selection,
                ),
                github_client=github_client,
                process_runner=ctx.process_runner,
                content_generator=content_generator,
            )

            published = False
            if not publish_prd and machine_mode:
                # FR-8：机器模式不触发交互提示，PRD 保持未发布并如实回报。
                logger.info(
                    "Machine mode skips the interactive PRD publish prompt; "
                    "PRD %s was not published.",
                    relative_prd_path,
                )
            elif not publish_prd:
                published = _cli._prompt_and_publish_prd_if_needed(
                    repo_path=context.repo_path,
                    relative_prd_path=relative_prd_path,
                    issue_url=issue_url,
                    queue_ready=ctx.parsed.ready,
                    git_remote=context.config.git.remote,
                    labels_config=context.config.labels,
                    github_client=github_client,
                    process_runner=ctx.process_runner,
                )
            if not ctx.parsed.ready or (ctx.parsed.ready and not publish_prd and not published):
                logger.info(
                    "Issue created without '%s' label. "
                    "Use --ready if you want a runner to pick it up.",
                    context.config.labels.ready,
                )
            logger.info("Created GitHub Issue: %s", issue_url)
            created_issues.append(
                {
                    "prd": prd_path_text,
                    "issue_url": issue_url,
                    # ready 报告生效状态而非请求旗标：--no-publish-prd 且未发布时
                    # queue_ready 被压成 False，机器消费方不能读到与标签不符的 true。
                    "ready": bool(ctx.parsed.ready and (publish_prd or published)),
                    "prd_published": published if not publish_prd else True,
                }
            )
            if not machine_mode:
                console.print(f"[green]Created GitHub Issue:[/] {issue_url}")
        except Exception as exc:  # noqa: BLE001 - batch should continue.
            failed_prd_paths.append(prd_path_text)
            error_detail = _format_cli_exception(exc)
            logger.error(
                "Failed to create Issue from %s:\n%s",
                prd_path_text,
                error_detail,
            )
            error_console.print(f"[red]Failed to create Issue from {prd_path_text}:[/]")
            error_console.print(error_detail, markup=False)

    if machine_mode:
        emit_json(
            {
                "created": created_issues,
                "skipped": list(skipped_prd_paths),
                "failed": failed_prd_paths,
                # 与上方「全部已存在 Issue」早退路径保持同构：note 恒在，无说明时为 null。
                "note": None,
            }
        )
    if failed_prd_paths:
        logger.error(
            "Issue creation failed for %d PRD(s): %s",
            len(failed_prd_paths),
            ", ".join(failed_prd_paths),
        )
        return 1
    return 0


def run_issue_list_command(ctx: ParsedCommandContext) -> int:
    """``kc issue list``: list Issues with linked PR status."""
    if ctx.parsed.with_pr and ctx.parsed.without_pr:
        raise CliError(
            "--with-pr and --without-pr are mutually exclusive.",
            code=ExitCode.USAGE,
            suggestion="kc issue list --with-pr",
        )
    from backend.core.use_cases.issue_pr_status import (
        IssueListRequest,
        list_issues_with_prs,
        render_issue_with_pulls_json,
        render_pr_column,
    )
    from rich.console import Console
    from rich.table import Table

    def _resolve_targets(
        *,
        repo_id: str | None,
        repo_path_override: str | None,
        all_repositories: bool,
    ) -> list:
        return _cli.resolve_repository_targets(
            ctx.runner_settings,
            repo_id=repo_id,
            repo_path_override=repo_path_override,
            all_repositories=all_repositories,
        )

    request = IssueListRequest(
        repo_id=ctx.repo_id,
        repo_path_override=ctx.repo_override,
        all_repositories=getattr(ctx.parsed, "all_registered", False),
        state_filter=ctx.parsed.state,
        label_filter=ctx.parsed.label,
        with_pr=(True if ctx.parsed.with_pr else False if ctx.parsed.without_pr else None),
        limit=ctx.parsed.limit,
    )
    try:
        result = list_issues_with_prs(
            request,
            cwd=Path.cwd(),
            github_client_factory=ctx.github_client_factory,
            resolve_targets=_resolve_targets,
        )
    except ValueError as exc:
        from backend.api.cli_helpers import repository_selector_error

        raise repository_selector_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - CLI should print concise failures.
        logger.error("kc issue list failed: %s", exc)
        error_console.print(f"[red]kc issue list failed:[/] {exc}")
        return 1

    def _render_rows() -> list[dict[str, object]]:
        return [render_issue_with_pulls_json(issue_row) for issue_row in result.rows]

    def _render_table() -> None:
        render_console = Console()
        # Only list multiple repositories in one table when the rows actually span
        # more than one repo; a single-repo listing stays narrow.
        multi_repo = len({row.repo for row in result.rows if row.repo}) > 1
        table = Table(show_header=True, header_style="bold")
        if multi_repo:
            table.add_column("Repo")
        table.add_column("Issue")
        table.add_column("Title")
        table.add_column("State")
        table.add_column("Labels")
        table.add_column("PR")
        for row in result.rows:
            cells = [
                f"#{row.number}",
                row.title,
                row.state,
                ", ".join(row.labels),
                render_pr_column(row.pulls),
            ]
            if multi_repo:
                cells.insert(0, row.repo or "-")
            table.add_row(*cells)
        render_console.print(table)

    # 机器模式：stdout 只有 JSON 数组（与旧 ``--output json`` 同构），逐仓错误走 stderr。
    payload = _render_rows() if ctx.output_format == OUTPUT_FORMAT_JSON else None
    emit(payload, fmt=ctx.output_format, human_renderer=_render_table)
    for repo_label, error_message in result.errors:
        error_console.print(f"[red]Error fetching {repo_label}:[/] {error_message}")
    return 1 if result.errors else 0


__all__ = ["run_issue_create_command", "run_issue_list_command", "run_labels_command"]

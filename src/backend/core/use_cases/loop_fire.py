"""Loop fire use case.

A single :func:`fire_loop` invocation:

1. Loads the recipe from disk and renders the PRD body template with the
   built-in and ``pre_command`` variables.
2. Writes a new PRD to ``tasks/pending/`` with a date-stamped filename.
3. Reuses :func:`create_issue_from_prd` (or the plain-Issue path) to create a
   GitHub Issue.
4. The default ``loop/<id>`` label (and any extras from the recipe) is part
   of the create call itself, so an Issue never exists without it.
5. Updates the loop's persisted state with the new ``last_fire_at``,
   ``next_fire_at`` and ``fire_count`` so the daemon can schedule the
   subsequent run.

The function honours a ``dry_run`` flag that short-circuits steps 2-5 so
the user can preview what a fire would produce without touching the
filesystem or GitHub.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.loop_scheduler import ILoopClock, ILoopStateStore
from backend.core.shared.models.agent_runner import LabelConfig
from backend.core.shared.models.loop import (
    LoopFireResult,
    LoopFireStatus,
    LoopManager,
    LoopOutput,
    LoopTask,
)
from backend.core.use_cases.create_issue_from_prd import (
    IssueFromPrdRequest,
    create_issue_from_prd,
)
from backend.core.use_cases.create_issue_from_prompt import (
    IssueFromPromptRequest,
    create_issue_from_prompt,
)
from backend.core.use_cases.loop_recipe import (
    parse_loop_recipe,
    parse_pre_command_output,
    render_loop_recipe,
    render_loop_recipe_title,
)
from backend.core.use_cases.loop_scheduler import (
    compute_next_fire,
    localize_loop_datetime,
)

_logger = logging.getLogger(__name__)


_DEFAULT_PRD_DIR = Path("tasks") / "pending"


# ---------------------------------------------------------------------------
# Path / filename helpers
# ---------------------------------------------------------------------------


def build_prd_path(
    repo_path: Path,
    *,
    loop_id: str,
    priority: str,
    fire_at: datetime,
) -> Path:
    """Compute the absolute path of the PRD to generate for a fire.

    Args:
        repo_path: Target repository root.
        loop_id: Loop identifier (used as the slug when no explicit slug
            is configured).
        priority: PRD priority tag (``P0``/``P1``/``P2``/``P3``).
        fire_at: Wall-clock time of the fire.

    Returns:
        Absolute path of the generated PRD.
    """
    timestamp = fire_at.strftime("%Y%m%d-%H%M%S")
    file_name = f"{priority}-FEAT-{timestamp}-{loop_id}.md"
    return (repo_path / _DEFAULT_PRD_DIR / file_name).resolve()


def _slugify(text: str) -> str:
    """Best-effort slug from a recipe body title."""
    lowered = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    return slug.strip("-") or "loop"


# ---------------------------------------------------------------------------
# Issue deduplication
# ---------------------------------------------------------------------------


def _issue_already_exists(
    github_client: IGitHubClient,
    *,
    loop_id: str,
    day_token: str,
    limit: int = 50,
) -> bool:
    """Return True when an open Issue with ``loop/<id>`` label exists for today.

    Args:
        github_client: GitHub client used to query the target repository.
        loop_id: Loop identifier; the label is ``loop/<id>``.
        day_token: ``YYYY-MM-DD`` token expected to appear in the Issue
            title.
        limit: Maximum number of issues to fetch for the label.

    Returns:
        True when an open Issue matches both the label and the day token.
    """
    label = f"loop/{loop_id}"
    try:
        issues = github_client.list_issues_by_label(label, limit, state="open")
    except Exception as exc:  # noqa: BLE001 - GitHub client may raise.
        _logger.warning(
            "Could not check for existing loop issues on label %r: %s",
            label,
            exc,
        )
        return False
    return any(day_token in issue.title for issue in issues)


def _active_loop_issue_reason(
    github_client: IGitHubClient,
    *,
    loop_id: str,
    limit: int = 50,
) -> str | None:
    """判断 agent-daemon loop 的上一轮 Issue 是否还没结束。

    规则只有一条：只要带 ``loop/<id>`` 标签的 Issue 仍是开放状态，就不触发下一轮。
    交付 PR 合并后 ``Closes #N`` 会关闭 Issue，那时才放行；因此“PR 已建、等人工审核”
    这类中间状态也算未结束，不会让下一轮重复做同一件事、与未合并的 PR 冲突。

    不靠 workflow 标签推断“算不算完成”：标签集合会随 runner 状态演进，漏判一个就会
    产生重叠任务；而 Issue 是否关闭是 GitHub 上唯一确定的终态。

    Args:
        github_client: 用于查询 loop Issue 的 GitHub 客户端。
        loop_id: loop 标识，对应 ``loop/<id>`` 标签。
        limit: 最多检查的开放 Issue 数量。

    Returns:
        需要暂停时返回人读原因（含 Issue 编号与当前 workflow 标签）；上一轮已关闭
        时返回 ``None``。

    Raises:
        RuntimeError: 无法安全读取 GitHub 状态时抛出（宁可暂停也不重叠触发）。
    """
    label = f"loop/{loop_id}"
    try:
        issues = github_client.list_issues_by_label(label, limit, state="open")
    except Exception as exc:  # noqa: BLE001 - pausing is safer than overlap.
        raise RuntimeError(
            f"Could not inspect active Issues for loop {loop_id!r}; fire paused."
        ) from exc
    if not issues:
        return None
    previous_issue = issues[0]
    workflow_labels = [
        label_name
        for label_name in previous_issue.labels
        if label_name.startswith(("agent/", "human/"))
    ]
    state_text = ", ".join(workflow_labels) if workflow_labels else "no runner label"
    return (
        f"previous Issue #{previous_issue.number} is still open ({state_text}); "
        "the next fire waits until it is closed (its PR merged)"
    )


def _skip_when_reason(skip_when: str | None, variables: dict[str, str]) -> str | None:
    """按 ``pre_command`` 变量求值 ``skip_when``，命中时返回人读原因。

    条件形如 ``KEY=value1,value2``：变量存在且取值在列表内才算命中；变量缺失
    视为不命中（fail-open），避免前置命令异常时悄悄吞掉本次触发。

    Args:
        skip_when: recipe 声明的跳过条件；为空表示不跳过。
        variables: ``pre_command`` 注入的模板变量。

    Returns:
        命中时的原因文本；否则 ``None``。
    """
    if not skip_when:
        return None
    variable_name, _, raw_values = skip_when.partition("=")
    variable_name = variable_name.strip()
    allowed_values = {value.strip() for value in raw_values.split(",")}
    actual_value = variables.get(variable_name)
    if actual_value is not None and actual_value.strip() in allowed_values:
        return f"skip_when {skip_when!r} matched ({variable_name}={actual_value.strip()})"
    return None


def _persist_skip_reason(
    state_store: ILoopStateStore,
    task: LoopTask,
    *,
    reason: str,
    next_fire_at: str | None = None,
) -> None:
    """记录本次未造票的原因；内容没变化时不写状态文件，避免每轮重写。"""
    updated = replace(
        task,
        last_skip_reason=reason,
        next_fire_at=next_fire_at if next_fire_at is not None else task.next_fire_at,
    )
    if updated != task:
        state_store.upsert_task(updated)


# ---------------------------------------------------------------------------
# Pre-command execution
# ---------------------------------------------------------------------------


def _run_pre_command(
    command: str,
    *,
    cwd: Path,
    process_runner: IProcessRunner,
) -> dict[str, str]:
    """Execute the pre_command and parse its ``KEY=value`` stdout.

    Args:
        command: Shell command to run. The command is passed to a real
            shell so users can use ``&&`` / pipes / env vars.
        cwd: Working directory for the command (typically the recipe's
            parent directory or repository root).
        process_runner: Process runner abstraction.

    Returns:
        Mapping of variable name to value. Returns an empty dict when the
        command produces no parseable output.
    """
    try:
        result = process_runner.run(
            ["/bin/sh", "-c", command],
            cwd=cwd,
            check=False,
            capture_output=True,
        )
    except Exception as exc:  # noqa: BLE001 - keep loop running.
        _logger.warning("pre_command failed: %s", exc)
        return {}
    if result.return_code != 0:
        _logger.warning(
            "pre_command exited with %d; stderr=%s",
            result.return_code,
            result.stderr.strip(),
        )
    return parse_pre_command_output(result.stdout)


# ---------------------------------------------------------------------------
# Public fire entry point
# ---------------------------------------------------------------------------


def fire_loop(
    task: LoopTask,
    *,
    repo_path: Path,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    state_store,
    clock: ILoopClock,
    content_generator: IContentGenerator | None = None,
    labels_config: LabelConfig | None = None,
    dry_run: bool = False,
) -> LoopFireResult:
    """Execute a single loop fire.

    Args:
        task: Registered loop task to fire.
        repo_path: Target repository root.
        github_client: GitHub client used to create the Issue.
        process_runner: Process runner used for ``pre_command`` and PRD
            publishing.
        state_store: Loop state store; the task's ``last_fire_at`` /
            ``next_fire_at`` are updated when ``dry_run`` is False.
        clock: Wall-clock abstraction.
        content_generator: Optional content generator passed through to
            :func:`create_issue_from_prd`.
        labels_config: Optional label config; defaults to :class:`LabelConfig`.
        dry_run: When True, render the PRD and report what *would* happen
            without writing the file, calling GitHub, or mutating the
            state store.

    Returns:
        A :class:`LoopFireResult` describing the outcome.

    Raises:
        FileNotFoundError: When the recipe file is missing.
        ValueError: When the recipe is malformed.
    """
    recipe = parse_loop_recipe(task.recipe_path)
    effective_timezone_name = task.timezone_name or recipe.timezone_name
    fire_at_utc = clock.now().astimezone(timezone.utc)
    fire_at = localize_loop_datetime(
        fire_at_utc,
        timezone_name=effective_timezone_name,
    )
    day_token = fire_at.strftime("%Y-%m-%d")
    effective_labels = labels_config or LabelConfig()

    # ``output: issue`` 的 loop 不产生 PRD 文件：配方正文直接成为 Issue 正文。
    prd_path = (
        build_prd_path(
            repo_path,
            loop_id=task.slug or task.id,
            priority=task.priority,
            fire_at=fire_at,
        )
        if task.output == LoopOutput.PRD.value
        else None
    )

    if not dry_run and task.managed_by == LoopManager.AGENT_DAEMON.value:
        active_reason = _active_loop_issue_reason(github_client, loop_id=task.id)
        if active_reason is not None:
            _persist_skip_reason(state_store, task, reason=active_reason)
            return LoopFireResult(
                loop_id=task.id,
                status=LoopFireStatus.SKIPPED_ACTIVE,
                skipped_reason=active_reason,
                next_fire_at=task.next_fire_at,
            )

    extra_variables: dict[str, str] = {}
    if task.pre_command:
        extra_variables = _run_pre_command(
            task.pre_command,
            cwd=task.recipe_path.parent,
            process_runner=process_runner,
        )

    skip_reason = _skip_when_reason(task.skip_when, extra_variables)
    if skip_reason is not None:
        next_skip_dt = compute_next_fire(
            task.schedule,
            after=fire_at_utc,
            timezone_name=effective_timezone_name,
        )
        if not dry_run:
            _persist_skip_reason(
                state_store,
                task,
                reason=skip_reason,
                next_fire_at=next_skip_dt.isoformat(),
            )
        return LoopFireResult(
            loop_id=task.id,
            status=LoopFireStatus.DRY_RUN if dry_run else LoopFireStatus.SKIPPED_CONDITION,
            skipped_reason=(f"Would skip: {skip_reason}" if dry_run else skip_reason),
            next_fire_at=next_skip_dt.isoformat(),
        )

    rendered_body = render_loop_recipe(
        recipe,
        fire_at=fire_at,
        extra_variables=extra_variables,
    )
    rendered_title = render_loop_recipe_title(
        recipe,
        fire_at=fire_at,
        extra_variables=extra_variables,
    )
    issue_title = rendered_title or recipe.id
    if dry_run:
        next_fire_dt = compute_next_fire(
            task.schedule,
            after=fire_at_utc,
            timezone_name=effective_timezone_name,
        )
        output_text = (
            f"render PRD at {prd_path}"
            if prd_path is not None
            else "create a plain Issue (no PRD file)"
        )
        return LoopFireResult(
            loop_id=task.id,
            status=LoopFireStatus.DRY_RUN,
            prd_path=prd_path,
            relative_prd_path=prd_path.relative_to(repo_path) if prd_path is not None else None,
            skipped_reason=(
                f"Would {output_text} with title "
                f"{issue_title!r} and labels {list(recipe.all_labels())}; "
                f"next fire at {next_fire_dt.isoformat()}."
            ),
            next_fire_at=next_fire_dt.isoformat(),
        )

    relative_prd_path = prd_path.relative_to(repo_path) if prd_path is not None else None
    if prd_path is not None:
        prd_path.parent.mkdir(parents=True, exist_ok=True)
        prd_path.write_text(rendered_body, encoding="utf-8")

    if _issue_already_exists(
        github_client,
        loop_id=task.id,
        day_token=day_token,
    ):
        next_fire_dt = compute_next_fire(
            task.schedule,
            after=fire_at_utc,
            timezone_name=effective_timezone_name,
        )
        updated = replace(
            task,
            last_fire_at=fire_at_utc.isoformat(),
            next_fire_at=next_fire_dt.isoformat(),
        )
        state_store.upsert_task(updated)
        return LoopFireResult(
            loop_id=task.id,
            status=LoopFireStatus.SKIPPED_DUPLICATE,
            prd_path=prd_path,
            relative_prd_path=relative_prd_path,
            skipped_reason=(
                f"Open Issue with label loop/{task.id} already exists for "
                f"{day_token}; skipped creation."
            ),
            next_fire_at=next_fire_dt.isoformat(),
        )

    issue_url = _create_loop_issue(
        task,
        prd_path=prd_path,
        rendered_body=rendered_body,
        issue_title=issue_title,
        repo_path=repo_path,
        labels_config=effective_labels,
        extra_labels=tuple(_effective_extra_labels(task, recipe)),
        github_client=github_client,
        process_runner=process_runner,
        content_generator=content_generator,
    )
    issue_number = _extract_issue_number(issue_url)

    next_fire_dt = compute_next_fire(
        task.schedule,
        after=fire_at_utc,
        timezone_name=effective_timezone_name,
    )
    updated = replace(
        task,
        last_fire_at=fire_at_utc.isoformat(),
        next_fire_at=next_fire_dt.isoformat(),
        fire_count=task.fire_count + 1,
        last_skip_reason=None,
    )
    state_store.upsert_task(updated)

    return LoopFireResult(
        loop_id=task.id,
        status=LoopFireStatus.FIRED,
        prd_path=prd_path,
        relative_prd_path=relative_prd_path,
        issue_url=issue_url,
        issue_number=issue_number,
        next_fire_at=next_fire_dt.isoformat(),
    )


def _create_loop_issue(
    task: LoopTask,
    *,
    prd_path: Path | None,
    rendered_body: str,
    issue_title: str,
    repo_path: Path,
    labels_config: LabelConfig,
    extra_labels: tuple[str, ...],
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None,
) -> str:
    """按 loop 的产出形态创建 Issue，返回 Issue URL。

    ``output: prd``：PRD 已写入磁盘，走完整的“从 PRD 建 Issue”流程（含 PRD 链接回写）。
    ``output: issue``：不碰任何 PRD 文件，配方正文原文作为 Issue 正文，Issue 与 PRD
    生命周期（归档、一对一链接、按 PRD 分目录的证据）完全无关。

    ``loop/<id>`` 与配方标签在创建调用里一并带上，而不是事后补：事后补一旦失败，Issue 已
    存在却没有 ``loop/<id>``，去重与“上一个未关闭则暂停”都认不出它，下一次触发会重复建。
    """
    issue_title_text = f"[{task.issue_type.title()}] {issue_title}"
    if prd_path is None:
        return create_issue_from_prompt(
            request=IssueFromPromptRequest(
                repo_path=repo_path,
                prompt_text=rendered_body,
                issue_type=task.issue_type,
                title_override=issue_title_text,
                queue_ready=task.queue_ready,
                issue_agent=task.agent,
                labels_config=labels_config,
                generated_content_config=None,
                extra_labels=extra_labels,
            ),
            github_client=github_client,
            content_generator=content_generator,
        )
    return create_issue_from_prd(
        request=IssueFromPrdRequest(
            repo_path=repo_path,
            prd_path=prd_path,
            issue_type=task.issue_type,
            title_override=issue_title_text,
            queue_ready=task.queue_ready,
            issue_agent=task.agent,
            labels_config=labels_config,
            force=False,
            publish_prd=task.publish_prd,
            git_remote="origin",
            git_base_branch="main",
            generated_content_config=None,
            depends_on=(),
            parse_evidence_format_with_agent=False,
            validation_language="zh-CN",
            structured_evidence=True,
            extra_labels=extra_labels,
        ),
        github_client=github_client,
        process_runner=process_runner,
        content_generator=content_generator,
    )


def _extract_issue_number(issue_url: str) -> int | None:
    """Parse the trailing ``/issues/<N>`` segment from a GitHub Issue URL."""
    match = re.search(r"/issues/(\d+)", issue_url)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _effective_extra_labels(
    task: LoopTask,
    recipe,
) -> Sequence[str]:
    """Return the deduped label set to apply on top of the create_issue defaults.

    ``recipe.all_labels()`` is already deduplicated and ordered, so this
    helper only has to merge the loop-task-level ``labels`` (which the
    CLI stores verbatim) into the recipe's set without re-deduping the
    recipe half.
    """
    seen: set[str] = set(recipe.all_labels())
    ordered: list[str] = list(seen)
    for label in task.labels:
        if label and label not in seen:
            seen.add(label)
            ordered.append(label)
    return ordered

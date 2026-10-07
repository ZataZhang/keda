"""Publishing and safety validation for the agent runner."""

from __future__ import annotations

from dataclasses import replace
from fnmatch import fnmatch
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_runner_dependencies import (
    format_direct_pr_marker,
    format_fast_merge_marker,
)
from backend.core.use_cases.agent_runner_feedback import (
    assert_prd_archived_for_publish,
)
from backend.core.use_cases.agent_runner_git import (
    get_current_branch,
    list_changed_paths,
    list_git_remotes,
)
from backend.core.use_cases.agent_runner_validation import (
    build_validation_checklist_block,
    ensure_no_evidence_paths_in_changes,
    extract_realistic_validation_items,
    validation_required,
)
from backend.core.use_cases.generated_content import (
    build_pr_context,
    generate_pr_content,
)
from backend.core.use_cases.generated_prd_content import ensure_prd_machine_contract_available
from backend.core.use_cases.lifecycle_agent_resolution import effective_prd_overrides
from backend.core.use_cases.agent_runner_pr_body_contract import (
    append_missing_contract_anchors,
    build_contract_annotation_block,
    build_contract_prompt_prefix,
    find_pr_body_contract_violations,
    load_prd_publish_contract,
)


class DraftPRCreationError(RuntimeError):
    """Raised when draft PR creation fails after a successful push."""

    pass


class PushChangesError(RuntimeError):
    """Raised when pushing the branch to remote fails."""

    pass


__all__ = [
    "DraftPRCreationError",
    "PushChangesError",
    "create_draft_pr",
    "is_forbidden_path",
    "publish_changes",
    "push_changes",
    "run_preflight_checks",
    "validate_publish_remote",
    "validate_safe_changes",
]


def is_forbidden_path(changed_path_text: str, config: AppConfig) -> bool:
    """Whether a changed path matches any configured forbidden pattern.

    Matches both the full repo-relative path and its basename against
    ``config.safety.forbidden_path_patterns`` (fnmatch), so a pattern like
    ``.env`` blocks ``.env`` anywhere in the tree.
    """
    changed_path_name = Path(changed_path_text).name
    for forbidden_pattern in config.safety.forbidden_path_patterns:
        if fnmatch(changed_path_text, forbidden_pattern) or fnmatch(
            changed_path_name,
            forbidden_pattern,
        ):
            return True
    return False


def validate_safe_changes(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> None:
    """Refuse to publish changes to configured forbidden paths."""
    blocked_paths = [
        changed_path_text
        for changed_path_text in list_changed_paths(worktree_path, process_runner)
        if is_forbidden_path(changed_path_text, config)
    ]
    if blocked_paths:
        blocked_paths_text = ", ".join(sorted(set(blocked_paths)))
        raise RuntimeError(f"Refusing to publish forbidden paths: {blocked_paths_text}")


def validate_publish_remote(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> str:
    """Return the configured publish remote after confirming it exists."""
    remote_names = list_git_remotes(worktree_path, process_runner)
    configured_remote_name = config.git.remote
    if configured_remote_name in remote_names:
        return configured_remote_name

    available_remotes_text = ", ".join(remote_names) if remote_names else "(none)"
    raise RuntimeError(
        "Configured git remote "
        f"'{configured_remote_name}' does not exist. "
        f"Available remotes: {available_remotes_text}. "
        "Update [agent_runner.git].remote in .kedacode.toml or config.toml before publishing."
    )


def run_preflight_checks(
    repo_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> None:
    """Validate runner configuration before claiming any Issue.

    除发布远端校验外，还预检 prd skill 的 Machine Contract：KedaCode 的 prompt 只持有
    契约指针，skill 缺失或主版本不符时执行 agent 拿不到格式约定，必须在开跑前
    fail fast（报错含 ``kc init`` 修复指引）。
    """
    validate_publish_remote(repo_path, config, process_runner)
    ensure_prd_machine_contract_available()


def _validate_branch_for_publish(
    worktree_path: Path,
    process_runner: IProcessRunner,
    *,
    expected_branch: str | None,
    issue: IssueSummary,
) -> str:
    """Resolve the current branch and verify it matches ``expected_branch``.

    Shared by :func:`push_changes` and :func:`create_draft_pr` to keep the
    branch drift guard consistent with the legacy :func:`publish_changes`
    behavior.
    """
    branch = get_current_branch(worktree_path, process_runner)
    if not branch:
        raise RuntimeError("Refusing to publish: worktree is in detached HEAD state.")
    if expected_branch is not None and branch != expected_branch:
        raise RuntimeError(
            f"Refusing to publish from unexpected branch: {branch} (expected {expected_branch})"
        )
    return branch


def push_changes(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    *,
    expected_branch: str | None = None,
    require_prd_archived: bool = True,
) -> str:
    """Push the current branch to the configured remote.

    Pre-push safety checks (PRD archive assertion, forbidden path scan, evidence
    path exclusion, remote validation) are run in the same order as the legacy
    :func:`publish_changes` so the on-disk guarantees are unchanged.

    Args:
        issue: Issue being published (used for PRD archive assertions).
        worktree_path: Agent worktree path.
        config: Agent Runner configuration.
        process_runner: Command runner.
        expected_branch: Optional explicit branch to publish from. When set, the
            worktree's current branch must match or the call is rejected.
        require_prd_archived: When ``True`` (default), enforce the canonical
            PRD delivery gate before pushing: the PRD must already be archived
            (open Human-Confirmed items are fine — they are answered on the PR).
            Only the exhausted-retries Draft PR and PRD rework proposals pass
            ``False``; neither archives the PRD.

    Returns:
        The branch that was pushed.

    Raises:
        RuntimeError: If a safety check fails or ``git push`` exits non-zero.
    """
    branch = _validate_branch_for_publish(
        worktree_path,
        process_runner,
        expected_branch=expected_branch,
        issue=issue,
    )
    if require_prd_archived:
        assert_prd_archived_for_publish(issue, worktree_path)
    validate_safe_changes(worktree_path, config, process_runner)
    ensure_no_evidence_paths_in_changes(worktree_path, config, process_runner)
    publish_remote_name = validate_publish_remote(worktree_path, config, process_runner)
    try:
        process_runner.run(["git", "push", "-u", publish_remote_name, branch], cwd=worktree_path)
    except (RuntimeError, OSError) as exc:
        raise PushChangesError(str(exc)) from exc
    return branch


def _format_publish_stage_annotation(
    issue_number: int,
    publish_stage: PublishStage,
) -> str | None:
    """渲染发布档位的自我声明段（marker + 人读说明）。

    两个旁路档位在 PR 正文里留下同族但值不同的 marker，让 reviewer 与下游机器
    判定都能区分「跳了哪一层」；默认档位不产生任何标注。

    Args:
        issue_number: 本次发布针对的 Issue 编号。
        publish_stage: 本次运行的发布档位。

    Returns:
        自我声明段落文本；``PublishStage.NORMAL`` 时返回 ``None``。
    """
    if publish_stage is PublishStage.FAST:
        marker = format_fast_merge_marker(issue_number)
        human_note = (
            "> **快速通道发布**：本 PR 经快速通道发布，未经过自动化验证门禁，" "合并前请人工验证。"
        )
    elif publish_stage is PublishStage.DIRECT:
        marker = format_direct_pr_marker(issue_number)
        human_note = (
            "> **直发档发布**：本 PR 经 ``--direct-pr`` 发布，runner 侧未运行审核 "
            "Agent 与仓库验证命令，质量门禁转移到本 PR 上的 CI；"
            "合并前请确认 CI 变绿并人工验证。"
        )
    else:
        return None
    return "\n".join([marker, "", human_note])


def create_draft_pr(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    *,
    expected_branch: str | None = None,
    content_generator: IContentGenerator | None = None,
    publish_stage: PublishStage = PublishStage.NORMAL,
) -> tuple[str, str]:
    """Create a draft PR for the current branch, or reuse an existing open PR.

    Assumes the branch has already been pushed by :func:`push_changes`. Performs
    the same branch guard, PR lookup, generated content, and validation
    checklist logic as the legacy :func:`publish_changes` so the resulting PR
    text and behaviour are unchanged.

    Args:
        issue: Issue being published.
        worktree_path: Agent worktree path.
        config: Agent Runner configuration.
        github_client: GitHub client used for PR lookup and creation.
        process_runner: Command runner.
        expected_branch: Optional explicit branch to verify before creating the
            PR. When set, the worktree's current branch must match.
        content_generator: Optional AI content generator for PR title/body.
        publish_stage: 发布档位。``FAST``（``kc run --fast-merge``）在正文末尾注入
            ``iar:fast-merge`` 自我声明 marker 与人读未验证标注；``DIRECT``
            （``kc run --direct-pr``）注入同族的 ``iar:direct-pr`` marker 与直发说明；
            ``NORMAL`` 不注入任何档位标注。

    Returns:
        ``(branch, pr_url)`` tuple.

    Raises:
        DraftPRCreationError: If the PR lookup or creation step fails.
    """
    branch = _validate_branch_for_publish(
        worktree_path,
        process_runner,
        expected_branch=expected_branch,
        issue=issue,
    )

    try:
        existing_pr_url = github_client.find_open_pr_by_head(branch)
    except Exception as exc:
        raise DraftPRCreationError(str(exc)) from exc
    if existing_pr_url is not None:
        return branch, existing_pr_url

    fallback_title = f"[Agent] {issue.title}"
    fallback_body = f"Closes #{issue.number}\n\nGenerated by issue-agent-runner.\n"

    gc_config = config.generated_content
    # PRD 级 ``content_generation`` 覆盖：该 Issue 引用的 PRD 文件头部声明的 agent
    # 高于矩阵 / 既有配置派生的 ``lifecycle_default_agent``（target 级显式 agent 仍更优先）。
    prd_content_generation_agent = effective_prd_overrides(issue).get("content_generation")
    if prd_content_generation_agent:
        gc_config = replace(gc_config, lifecycle_default_agent=prd_content_generation_agent)
    # prd skill 发布契约教学：agent 模式生成前把契约参考注入 prompt（调用侧包装
    # config，generated_content 保持与 skill 解耦）。skill 不可达时静默跳过教学，
    # 发布端软门仍会照常校验并标注。
    if gc_config.enabled and gc_config.draft_pr.mode == "agent":
        publish_contract_text = load_prd_publish_contract()
        if publish_contract_text:
            gc_config = replace(
                gc_config,
                draft_pr=replace(
                    gc_config.draft_pr,
                    prompt=(
                        f"{build_contract_prompt_prefix(publish_contract_text)}\n\n"
                        f"{gc_config.draft_pr.prompt}"
                    ),
                ),
            )
    pr_title = fallback_title
    pr_body = fallback_body
    is_agent_authored_body = False
    if gc_config.enabled:
        gc_context = build_pr_context(
            issue=issue,
            branch=branch,
            base_branch=config.git.base_branch,
            worktree_path=worktree_path,
            process_runner=process_runner,
            target_config=gc_config.draft_pr,
        )
        # content_generation 阶段绑定的模型选择（发布路径按配置绑定解析；
        # 预设整体决定该阶段 agent 与模型，未绑定为 None 时零变化）。
        from backend.core.use_cases.lifecycle_agent_resolution import (
            resolve_lifecycle_model_selection,
        )

        generated = generate_pr_content(
            config=gc_config,
            context=gc_context,
            fallback_title=fallback_title,
            fallback_body=fallback_body,
            generator=content_generator,
            cwd=worktree_path,
            model_selection=resolve_lifecycle_model_selection(
                "content_generation", config, issue=issue
            ),
        )
        pr_title = generated.title
        pr_body = generated.body
        is_agent_authored_body = generated.source == "agent"

    # 确定性正文（fallback / body_template）没有 agent 可教，契约锚点由代码补齐；
    # agent 撰写的正文缺锚点则交给下面的软门标注，暴露"agent 无视教学"。
    if not is_agent_authored_body:
        pr_body = append_missing_contract_anchors(pr_body, issue.body)

    if validation_required(issue.body, config):
        validation_checklist_items = extract_realistic_validation_items(issue.body)
        if validation_checklist_items:
            checklist_block = build_validation_checklist_block(validation_checklist_items)
            pr_body = f"{pr_body.rstrip()}\n\n{checklist_block}\n"

    # prd skill 发布契约软门：缺锚点不阻断发布，改为在正文末尾追加显式标注，
    # 供人工审阅与合并队列硬门（parse 该 marker）消费。
    contract_violations = find_pr_body_contract_violations(pr_body, issue.body)
    if contract_violations:
        contract_block = build_contract_annotation_block(contract_violations)
        pr_body = f"{pr_body.rstrip()}\n\n{contract_block}\n"

    # 档位自我声明：机器可读 marker + 人读说明，避免未验证 PR 混入正常证据链。
    stage_annotation = _format_publish_stage_annotation(issue.number, publish_stage)
    if stage_annotation is not None:
        pr_body = f"{pr_body.rstrip()}\n\n{stage_annotation}\n"

    try:
        pr_url = github_client.create_draft_pr(
            title=pr_title,
            body=pr_body,
            base_branch=config.git.base_branch,
            cwd=worktree_path,
        )
    except Exception as exc:
        raise DraftPRCreationError(str(exc)) from exc
    return branch, pr_url


def publish_changes(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    *,
    expected_branch: str | None = None,
    content_generator: IContentGenerator | None = None,
    require_prd_archived: bool = True,
) -> tuple[str, str]:
    """Compatibility wrapper that pushes then creates a draft PR in one call.

    New flows should call :func:`push_changes` and :func:`create_draft_pr`
    independently so the publish gate (PR creation) can be ordered around the
    pre-PR review. This wrapper is retained for call sites that legitimately
    need the combined behaviour (e.g. PRD rework publication that is not gated
    by pre-PR review).
    """
    push_changes(
        issue,
        worktree_path,
        config,
        process_runner,
        expected_branch=expected_branch,
        require_prd_archived=require_prd_archived,
    )
    return create_draft_pr(
        issue,
        worktree_path,
        config,
        github_client,
        process_runner,
        expected_branch=expected_branch,
        content_generator=content_generator,
    )

"""Issue 状态处理器：按 Issue 所处状态分派到对应的处理路径。

本模块承接原先落在 ``agent_runner_orchestrate.py`` 的四个分派处理器：

- :func:`_process_ready_issue` —— 新 Issue → 完整 Agent 执行 → 评审 → 发布
- :func:`_process_running_rework` —— 已运行 Issue → 检出 rework 标记 → 修复 → 评审
- :func:`_process_running_publish_recovery` —— 已运行 Issue → 有本地 commit → 直接评审 → 发布
- :func:`_process_blocked_resolution` —— ``agent/blocked`` Issue → 在既有 worktree 上继续解阻

调用方是 :mod:`backend.core.use_cases.agent_runner_orchestration_runtime` 的
派发层；它按名字从 ``agent_runner_orchestrate`` 的命名空间取这些处理器与协作者
（见 :data:`RUNTIME_DEPENDENCY_NAMES` 与
``agent_runner_orchestrate._orchestration_runtime_module``），因此本模块的
运行时依赖保持可替换：orchestrate 在每次派发前把它们同步过来。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    AttemptResult,
    CommandResult,
    IssueSummary,
    ReviewEventMarker,
    TokenUsage,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_runner_blocked_claim import (
    _acquire_blocked_claim_lock,
    _release_blocked_claim_lock,
    worktree_claim_lock_path,
)
from backend.core.use_cases.agent_runner_claim_arbitration import arbitrate_first_claim
from backend.core.use_cases.agent_onboarding import assert_run_gate
from backend.core.use_cases.agent_runner_direct_pr_label import (
    PublishStageDecision,
    PublishStageSelection,
    PublishStageSelectionRequest,
    resolve_claim_publish_stage,
)
from backend.core.use_cases.agent_runner_direct_pr_round import (
    DirectPrPublicationCandidate,
    reset_unstarted_direct_pr_selection,
)
from backend.core.use_cases.agent_runner_issue_blocked_resolution import (
    _guard_blocked_issue_has_resolution,
)
from backend.core.use_cases.agent_runner_failure_handoff import (
    _read_previous_failure_context,
    _record_failure_handoff,
)
from backend.core.use_cases.agent_runner_git import (
    get_current_branch,
    has_changes,
)
from backend.core.use_cases.agent_runner_publication import (
    LocalCommitReuseRequest,
    PreVerifierDraftPublicationRequest,
    _finish_existing_commit_publication,
    _finish_implementation_publication,
    _reuse_existing_local_commit,
    _reuse_local_commit,
    publish_draft_pr_before_verifier,
)
from backend.core.use_cases.agent_runner_recovery_selection import (
    DirectPrHandoffRequest,
    RecoverySelectionRequest,
    admit_claimed_selection,
    finish_direct_pr_handoff,
    resolve_recovery_selection,
)
from backend.core.use_cases.agent_runner_rework import build_missing_worktree_comment
from backend.core.use_cases.agent_runner_supervisor import _run_supervisor_with_repair_loop
from backend.core.use_cases.agent_runner_validation import (
    ValidationEvidenceError,
    publish_validation_evidence_best_effort,
)
from backend.core.use_cases.agent_runner_workflow import (
    transition_issue_workflow_state,
)
from backend.core.use_cases.agent_runner_worktree_branch import _ensure_worktree_branch
from backend.core.use_cases.agent_runner_worktree_probe import _find_worktree_path_for_issue
from backend.core.use_cases.pr_supervisor import (
    execute_rebase,
    execute_repair,
)
from backend.core.use_cases.pr_supervisor_findings import _load_previous_findings
from backend.core.use_cases.pr_supervisor_messages import build_rebase_repair_complete_comment
from backend.core.use_cases.run_agent_once import (
    choose_agent,
    create_or_reuse_worktree,
    get_head_sha,
    resolve_implementation_model_selection,
    resolve_repair_agent,
)

_logger = logging.getLogger(__name__)

__all__ = [
    "_guard_blocked_issue_has_resolution",
    "_process_blocked_resolution",
    "_process_ready_issue",
    "_process_running_publish_recovery",
    "_process_running_rework",
]

# 在被派发前由 ``agent_runner_orchestrate._orchestration_runtime_module``
# 从 orchestrate 的命名空间同步过来的执行侧协作者：git 读取、worktree 供给与分支
# 对齐、agent 选择、发布与 blocked claim。测试与集成方仍在
# ``agent_runner_orchestrate`` 上替换它们（与
# ``agent_runner_orchestration_runtime.RUNTIME_DEPENDENCY_NAMES`` 同一套机制，
# 保持单一补丁中枢，避免同一处替换要打两个模块）。
RUNTIME_DEPENDENCY_NAMES = (
    "_acquire_blocked_claim_lock",
    "_ensure_worktree_branch",
    "_find_worktree_path_for_issue",
    "_finish_existing_commit_publication",
    "_finish_implementation_publication",
    "_release_blocked_claim_lock",
    "_reuse_existing_local_commit",
    "arbitrate_first_claim",
    "publish_draft_pr_before_verifier",
    "_run_supervisor_with_repair_loop",
    "choose_agent",
    "create_or_reuse_worktree",
    "execute_rebase",
    "get_current_branch",
    "get_head_sha",
)


def _resume_direct_pr_handoff(
    github_client: IGitHubClient,
    decision: PublishStageDecision,
    candidate: DirectPrPublicationCandidate,
    config: AppConfig,
) -> bool:
    """已发布轮次只补标签与 workflow 交接，不启动 builder 或新的门禁。"""
    return finish_direct_pr_handoff(
        DirectPrHandoffRequest(
            github_client,
            decision,
            candidate,
            lambda: transition_issue_workflow_state(
                github_client, decision.issue.number, config, config.labels.review
            ),
        )
    )


def _process_blocked_resolution(
    *,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    fallback_preset: str | None = None,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None = None,
    marker: ReviewEventMarker,
    on_attempt_recorded: Callable[[AttemptResult, list[AttemptResult]], None] | None = None,
    on_agent_usage: Callable[[str, str, TokenUsage], None] | None = None,
    publish_stage: PublishStage = PublishStage.NORMAL,
    stage_selection: PublishStageSelection | None = None,
    repo_id: str | None = None,
) -> None:
    """处理带 blocked_resolution marker 的 blocked Issue。

    在现有 worktree 上发送 continuation prompt，让 Agent 继续完成剩余任务。

    Args:
        issue: Issue 对象
        repo_path: 仓库根目录
        config: 应用配置
        agent: Agent 覆盖
        fallback_preset: 回退链条轮到本候选时携带的预设名（由
            :func:`run_issue_with_agent_fallback` 注入）；``None`` 表示沿用
            implementation 阶段绑定。
        github_client: GitHub 客户端
        process_runner: 进程运行器
        content_generator: 可选的 AI 内容生成器
        marker: blocked_resolution_requested 事件标记
        repo_id: 稳定的仓库 registry id，用于短期记忆分区。
        publish_stage: 发布档位：FAST / DIRECT 跳过 Phase 4.5 的 RV 重跑与 verifier；
            PRD_AUTO_MERGE 保留 RV 重跑、只跳 verifier；DIRECT 再跳过审核 agent 与仓内验证命令。
    """
    from backend.core.use_cases.agent_runner_issue_control import (
        VerifierCancelledByHumanError,
        finalize_verifier_takeover,
    )
    from backend.core.use_cases.agent_runner_publish import validate_safe_changes
    from backend.core.use_cases.run_agent_once import (
        build_blocked_continuation_prompt,
        run_agent_until_committed,
    )

    selected_agent = choose_agent(issue, config, agent)
    # implementation 阶段绑定的模型选择：换人（CLI --agent 显式指定 / 回退链
    # 换候选）在 drop 里丢弃并记日志，模型 flag 绝不跨 CLI 注入；回退链条轮到
    # 携带 preset 的候选时改按该候选预设解析（单一入口见 core helper）。
    implementation_model_selection = resolve_implementation_model_selection(
        selected_agent,
        config,
        issue=issue,
        fallback_preset=fallback_preset,
    )

    # 定位 worktree 并确认分支
    worktree_path = _find_worktree_path_for_issue(repo_path, issue, config, process_runner)
    expected_branch = f"issue-{issue.number}"
    _ensure_worktree_branch(worktree_path, expected_branch, issue, config, process_runner)
    current_branch = get_current_branch(worktree_path, process_runner)
    if current_branch != expected_branch:
        raise RuntimeError(
            f"Blocked resolution aborted: on branch {current_branch}, expected {expected_branch}"
        )

    # 原子锁：防止多个 runner 同时处理同一个 blocked Issue 的 worktree
    lock_path = worktree_claim_lock_path(worktree_path)
    _acquire_blocked_claim_lock(lock_path, issue.number)
    try:
        stage_decision = resolve_recovery_selection(
            RecoverySelectionRequest(
                selection=PublishStageSelectionRequest(
                    publish_stage, issue, config, github_client, stage_selection
                ),
                claim=lambda: arbitrate_first_claim(
                    issue_number=issue.number,
                    github_client=github_client,
                    config=config,
                    selected_agent=selected_agent,
                ),
                candidate=lambda: DirectPrPublicationCandidate(
                    current_branch, get_head_sha(worktree_path, process_runner)
                ),
                handoff=lambda decision, candidate: _resume_direct_pr_handoff(
                    github_client, decision, candidate, config
                ),
            )
        )
        if stage_decision is None:
            return
        issue = stage_decision.issue
        publish_stage = stage_decision.publish_stage
        pre_verifier_publication = PreVerifierDraftPublicationRequest(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            github_client=github_client,
            process_runner=process_runner,
            expected_branch=current_branch,
            publish_stage=publish_stage,
            content_generator=content_generator,
        )
        # worktree 必须是 clean 的
        if has_changes(worktree_path, process_runner):
            raise RuntimeError(
                "Blocked resolution aborted: worktree has uncommitted changes. "
                "Please commit or stash them before continuing."
            )

        # 再次检查无 forbidden paths
        validate_safe_changes(worktree_path, config, process_runner)

        # 构建并发送 continuation prompt
        continuation_prompt = build_blocked_continuation_prompt(
            issue, worktree_path, marker.blocked_paths
        )
        before_sha = get_head_sha(worktree_path, process_runner)

        try:
            commit_result = run_agent_until_committed(
                selected_agent=selected_agent,
                issue=issue,
                worktree_path=worktree_path,
                config=config,
                process_runner=process_runner,
                before_sha=before_sha,
                expected_branch=current_branch,
                prompt_override=continuation_prompt,
                on_attempt_recorded=on_attempt_recorded,
                on_agent_usage=on_agent_usage,
                before_verifier_callback=partial(
                    publish_draft_pr_before_verifier, pre_verifier_publication
                ),
                model_selection=implementation_model_selection,
                publish_stage=publish_stage,
                repo_id=repo_id,
            )
        except VerifierCancelledByHumanError:
            # 人工接管（Issue #278）：解阻续作路径与首跑同样交班——verifier
            # 进程树已回收、控制行已落闸，收敛 GitHub 事实后正常返回。
            finalize_verifier_takeover(
                repo_id=repo_id,
                issue=issue,
                config=config,
                github_client=github_client,
                expected_branch=current_branch,
            )
            _logger.info(
                "Issue #%d: verifier during blocked resolution taken over by a human.",
                issue.number,
            )
            return

        # 完成发布流程
        _finish_implementation_publication(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            selected_agent=selected_agent,
            github_client=github_client,
            process_runner=process_runner,
            expected_branch=current_branch,
            commit_result=commit_result,
            content_generator=content_generator,
            publish_stage=publish_stage,
            direct_pr_label=stage_decision.direct_pr_label,
            repo_id=repo_id,
        )
    finally:
        _release_blocked_claim_lock(lock_path)


def _process_ready_issue(
    *,
    publish_stage: PublishStage = PublishStage.NORMAL,
    stage_selection: PublishStageSelection | None = None,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    fallback_preset: str | None = None,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    on_claimed: Callable[[], None] | None = None,
    content_generator: IContentGenerator | None = None,
    on_attempt_recorded: Callable[[AttemptResult, list[AttemptResult]], None] | None = None,
    on_agent_usage: Callable[[str, str, TokenUsage], None] | None = None,
    repo_id: str | None = None,
) -> None:
    """处理 ready 状态的 Issue（完整实现路径）。

    ready Issue 是新 claim 的 Issue，需要完整处理：
    1. 标记为 running 并评论声明
    2. 创建或复用 worktree
    3. 检查是否有已存在的本地 commit（恢复路径）
    4. 如无本地 commit 则运行 Agent 实现
    5. 完成发布流程

    Args:
        publish_stage: 调用侧请求的发布档位。FAST / DIRECT 在 builder 提交后不重跑
            rv_reexec 且不启动独立 verifier；PRD_AUTO_MERGE 保留 rv_reexec、只跳 verifier。
            Issue 上的发布选择标签会在认领后决定有效档位。
        issue: Issue 对象
        repo_path: 仓库根目录
        config: 应用配置
        agent: Agent 覆盖（auto/codex/claude）
        github_client: GitHub 客户端
        process_runner: 进程运行器
        content_generator: 可选的 AI 内容生成器
        repo_id: 稳定的仓库 registry id，用于短期记忆分区。
    """
    from backend.core.use_cases.agent_runner_issue_control import (
        VerifierCancelledByHumanError,
        finalize_verifier_takeover,
    )
    from backend.core.use_cases.run_agent_once import (
        MaxRetriesExceededError,
        PrdDeliveryError,
        ProviderCapacityError,
        VerificationFailedError,
        build_progress_continuation_prompt,
        checkpoint_uncommitted_progress,
        run_agent_until_committed,
    )

    selected_agent = choose_agent(issue, config, agent)
    # implementation 阶段绑定的模型选择：换人（CLI --agent 显式指定 / 回退链
    # 换候选）在 drop 里丢弃并记日志，模型 flag 绝不跨 CLI 注入；回退链条轮到
    # 携带 preset 的候选时改按该候选预设解析（单一入口见 core helper）。
    implementation_model_selection = resolve_implementation_model_selection(
        selected_agent,
        config,
        issue=issue,
        fallback_preset=fallback_preset,
    )
    # 启动前门禁（FR-5）：在投递认领标记、建 worktree 之前确认配置层解析出的阶段
    # agent 在本机可执行。配置指向没装的 CLI 是配置错误，必须在 GitHub 上留下认领
    # 痕迹之前停下。判定只看配置解析结果、不看 ``agent``：本函数会被回退阶梯按候选
    # 逐个调用，候选没装属于运行时失败（起进程时 FileNotFoundError → AgentUnavailableError），
    # 由阶梯跳过，不是配置错误。显式 ``--agent`` 由仓库轮询入口的那次预检负责。
    assert_run_gate(config)

    # 步骤 1: 声明 Issue —— 真 CAS：先投递认领标记，回读后确认最早认领者才切 running。
    # 落败方不碰标签、worktree 或 agent，由派发层按 skip 处理。
    claim_bid = arbitrate_first_claim(
        issue_number=issue.number,
        github_client=github_client,
        config=config,
        selected_agent=selected_agent,
        on_won=on_claimed,
    )

    # 档位决定在认领之后做：只有赢家需要确立档位，落败方（ClaimArbitrationLost 已在
    # 上面抛出）不会因一次只读解析去碰标签。Issue 上的直发标签把「这次要直发」这个
    # 选择带给任何认领方，准入规则与 CLI 入口同源于 core。
    if stage_selection is None or stage_selection.decision is None:
        reset_unstarted_direct_pr_selection(github_client, issue)
    stage_decision = resolve_claim_publish_stage(
        PublishStageSelectionRequest(publish_stage, issue, config, github_client, stage_selection)
    )

    # 步骤 2: 准备 worktree
    worktree_path = create_or_reuse_worktree(repo_path, issue, config, process_runner)
    before_sha = get_head_sha(worktree_path, process_runner)
    expected_branch = get_current_branch(worktree_path, process_runner)

    if _resume_direct_pr_handoff(
        github_client,
        stage_decision,
        DirectPrPublicationCandidate(expected_branch, before_sha),
        config,
    ):
        return
    admit_claimed_selection(
        PublishStageSelectionRequest(publish_stage, issue, config, github_client, stage_selection),
        stage_decision,
        claim_bid.comment_id,
    )
    issue = stage_decision.issue
    publish_stage = stage_decision.publish_stage
    pre_verifier_publication = PreVerifierDraftPublicationRequest(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        github_client=github_client,
        process_runner=process_runner,
        expected_branch=expected_branch,
        publish_stage=publish_stage,
        content_generator=content_generator,
    )

    # 步骤 3: 检查恢复路径
    #
    # 已有本地提交分三种情况：
    # - 完全达到交付标准 → 直接发布，不调用 agent。
    # - 存在提交但门禁未过（上一次 claim 的 WIP checkpoint / 部分进度）→ 不硬失败，
    #   在已提交进度上重跑 agent 续作（continuation prompt）。
    # - 无本地提交 → 全新实现。
    continuation_prompt: str | None = None
    try:
        commit_result = (
            _reuse_local_commit(
                LocalCommitReuseRequest(issue, worktree_path, config, process_runner, publish_stage)
            )
            if publish_stage is PublishStage.DIRECT
            else _reuse_existing_local_commit(issue, worktree_path, config, process_runner)
        )
    except (VerificationFailedError, PrdDeliveryError, ValidationEvidenceError) as exc:
        _logger.info(
            "Issue #%d has partial local commits not yet delivery-ready (%s); "
            "re-running agent to continue from committed progress.",
            issue.number,
            exc.__class__.__name__,
        )
        commit_result = None
        verification_results: list[CommandResult] | None = None
        failure_summary = str(exc)
        if isinstance(exc, VerificationFailedError):
            verification_results = exc.verification_results
        continuation_prompt = build_progress_continuation_prompt(
            issue,
            worktree_path,
            failure_summary=failure_summary,
            verification_results=verification_results,
            previous_failure_context=_read_previous_failure_context(issue, github_client),
        )

    if commit_result is not None:
        # 有已存在的本地 commit 且已达交付标准 → 恢复路径
        publish_draft_pr_before_verifier(pre_verifier_publication)
        _finish_existing_commit_publication(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            selected_agent=selected_agent,
            github_client=github_client,
            process_runner=process_runner,
            expected_branch=expected_branch,
            commit_result=commit_result,
            content_generator=content_generator,
            publish_stage=publish_stage,
            direct_pr_label=stage_decision.direct_pr_label,
            repo_id=repo_id,
        )
        return

    # 步骤 4: 无可发布的本地 commit → Agent 执行（首跑，或在 checkpoint 上续作）。
    #
    # 失败前把 agent 的在途进度提交成 WIP checkpoint，使其能被下一次 claim 复用、
    # 继续推进；否则体量较大的 PRD 会在每次 claim 从零开始、永远收敛不了。
    try:
        new_commit_result = run_agent_until_committed(
            selected_agent=selected_agent,
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            before_sha=before_sha,
            expected_branch=expected_branch,
            prompt_override=continuation_prompt,
            on_attempt_recorded=on_attempt_recorded,
            on_agent_usage=on_agent_usage,
            before_verifier_callback=partial(
                publish_draft_pr_before_verifier, pre_verifier_publication
            ),
            model_selection=implementation_model_selection,
            publish_stage=publish_stage,
            repo_id=repo_id,
        )
    except VerifierCancelledByHumanError:
        # 人工接管（Issue #278）：verifier 进程树已回收、控制行已落闸，交班
        # 收敛 GitHub 侧事实后**正常返回**——不 checkpoint、不写失败交接、
        # 也不进换 agent 回退：人被接管不是一次可由下一个 agent 修复的失败。
        finalize_verifier_takeover(
            repo_id=repo_id,
            issue=issue,
            config=config,
            github_client=github_client,
            expected_branch=expected_branch,
        )
        _logger.info(
            "Issue #%d: independent verifier taken over by a human; run handed off "
            "to manual review.",
            issue.number,
        )
        return
    except (
        MaxRetriesExceededError,
        ProviderCapacityError,
        KeyboardInterrupt,
    ) as in_flight_failure_exc:
        # 切换 agent 前、或被 Ctrl-C / SIGINT 优雅打断时,先把在途进度 checkpoint：
        # 让 fallback 链上的下一个 agent、或重新 claim 时能在已提交进度上续作,而不是
        # 从零重来。KeyboardInterrupt 同样 checkpoint 后再抛出,让中断照常退出。
        # best-effort：checkpoint 自身异常不得掩盖原始失败/中断（禁改路径已被
        # checkpoint 内部隔离,不再整块放弃）。
        checkpoint_sha: str | None = None
        try:
            checkpoint_sha = checkpoint_uncommitted_progress(
                issue,
                worktree_path,
                config,
                process_runner,
                expected_branch=expected_branch,
            )
        except Exception as checkpoint_exc:  # noqa: BLE001 - 不能掩盖原始失败
            _logger.warning(
                "Failed to checkpoint in-progress work for Issue #%d: %s",
                issue.number,
                checkpoint_exc,
            )
        else:
            if checkpoint_sha is not None:
                _logger.info(
                    "Checkpointed in-progress work for Issue #%d at %s "
                    "for the next claim to continue.",
                    issue.number,
                    checkpoint_sha,
                )

        # 交接只属于"这一轮没通过验收"，所以显式收窄到 MaxRetriesExceededError：
        # ProviderCapacityError 是限流/容量，不代表本轮工作未通过；KeyboardInterrupt
        # 是用户主动中断，且它根本没有 attempt_results。挂在整个 except 元组上会让
        # 一次 Ctrl-C 发出一条假的失败结论、甚至一个 Draft PR，并因访问
        # attempt_results 抛 AttributeError 把用户的中断变成一条假故障。
        if isinstance(in_flight_failure_exc, MaxRetriesExceededError):
            _record_failure_handoff(
                issue=issue,
                worktree_path=worktree_path,
                config=config,
                github_client=github_client,
                process_runner=process_runner,
                failure_exc=in_flight_failure_exc,
                checkpoint_sha=checkpoint_sha,
                expected_branch=expected_branch,
                content_generator=content_generator,
            )
        raise

    # 步骤 5: 完成发布流程
    _finish_implementation_publication(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        selected_agent=selected_agent,
        github_client=github_client,
        process_runner=process_runner,
        expected_branch=expected_branch,
        commit_result=new_commit_result,
        content_generator=content_generator,
        publish_stage=publish_stage,
        direct_pr_label=stage_decision.direct_pr_label,
        repo_id=repo_id,
    )


def _process_running_rework(
    *,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    marker: ReviewEventMarker,
    repo_id: str | None = None,
    supervisor_owned: bool = False,
    **kwargs: object,
) -> None:
    """处理带 rework 标记的 Issue。

    runner 恢复旧的 ``agent/running`` 返工时，生命周期仍走 runner 状态；Supervisor
    自己恢复或执行修复时，始终保持 ``agent/supervising``，并把后续复审留给下一轮。

    Args:
        issue: Issue 对象
        repo_path: 仓库根目录
        config: 应用配置
        agent: Agent 覆盖
        github_client: GitHub 客户端
        process_runner: 进程运行器
        marker: 事件标记（包含动作类型和分支信息）
        repo_id: 稳定仓库标识（Issue #278），透传给 rework 后的监督循环做
            人工接管闸门（``autopilot_hold``）检查。
        supervisor_owned: 是否由 post-PR supervisor 执行；为真时不进入 running，也不
            在当前 review pass 内再次启动 supervisor cycle。
    """
    pr_branch = marker.pr_branch
    if pr_branch is None:
        raise RuntimeError("Rework marker missing pr_branch")

    if supervisor_owned:
        supervisor_agent = choose_agent(issue, config, agent)
        arbitrate_first_claim(
            issue_number=issue.number,
            github_client=github_client,
            config=config,
            selected_agent=supervisor_agent,
            target_label=config.labels.supervising,
        )

    # 定位 worktree；缺失时进入 blocked 并给出可操作的恢复说明。
    try:
        worktree_path = _find_worktree_path_for_issue(repo_path, issue, config, process_runner)
    except FileNotFoundError as exc:
        message = str(exc)
        prefix = "(path_command output): "
        suffix = ". path_command return_code="
        expected_path = message
        if prefix in message and suffix in message:
            expected_path = message.split(prefix, 1)[1].split(suffix, 1)[0]
        github_client.comment_issue(
            issue.number,
            build_missing_worktree_comment(
                issue=issue,
                pr_branch=pr_branch,
                expected_path=expected_path,
            ),
        )
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.blocked)
        return

    # worktree 可能因上一次 runner 在 rebase 中途中断而停在 detached HEAD；
    # 先治愈回目标分支再校验，避免对中断状态直接硬失败、把 Issue 打成 failed。
    _ensure_worktree_branch(worktree_path, pr_branch, issue, config, process_runner)

    current_branch = get_current_branch(worktree_path, process_runner)
    if current_branch != pr_branch:
        raise RuntimeError(f"Rework aborted: on branch {current_branch}, expected {pr_branch}")

    expected_head = marker.head_sha or get_head_sha(worktree_path, process_runner)
    action = marker.action or "repair_pr_branch"
    supervisor_agent = choose_agent(issue, config, agent)
    # rework 路径拿不到本次实现者，``repair_agent='executor'`` 会按 Issue 标签
    # 回落（解析器内部记录来源）；findings 取跨 cycle 落盘的未解决清单。
    repair_agent = resolve_repair_agent(
        config.post_pr_supervisor.repair_agent,
        issue=issue,
        config=config,
        reviewing_agent=supervisor_agent,
    )

    # 执行修复或 rebase
    if action == "rebase_pr_branch":
        verification_results = execute_rebase(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            pr_branch=pr_branch,
            expected_head=expected_head,
            supervisor_agent=supervisor_agent,
        )
        rebase_sha = get_head_sha(worktree_path, process_runner)
        github_client.comment_issue(
            issue.number,
            build_rebase_repair_complete_comment(
                action=action,
                head_sha=rebase_sha,
                verification_passed=all(result.return_code == 0 for result in verification_results),
            ),
        )
    else:
        verification_results = execute_repair(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            pr_branch=pr_branch,
            expected_head=expected_head,
            repair_agent=repair_agent,
            findings=_load_previous_findings(worktree_path, config, issue.number),
        )
        repair_sha = get_head_sha(worktree_path, process_runner)
        github_client.comment_issue(
            issue.number,
            build_rebase_repair_complete_comment(
                action=action,
                head_sha=repair_sha,
                verification_passed=all(result.return_code == 0 for result in verification_results),
            ),
        )

    # 修复后刷新验证证据：新 head 需要新证据与新一轮人工签收
    # best-effort：见 publish_validation_evidence_best_effort docstring。
    rework_pr_url = github_client.find_open_pr_by_head(pr_branch)
    if rework_pr_url is not None:
        publish_validation_evidence_best_effort(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            github_client=github_client,
            process_runner=process_runner,
            pr_url=rework_pr_url,
            head_sha=get_head_sha(worktree_path, process_runner),
        )

    # 标记为 supervising 并获取 PR 上下文
    transition_issue_workflow_state(github_client, issue.number, config, config.labels.supervising)

    # Review daemon 的这次调用已经是 supervisor 触发的 repair。让下一轮按 PR/head/check
    # 变化重新评审，避免在同一调用栈里重复启动 cycle 或把返工转交给 runner。
    if supervisor_owned:
        return

    # 修复后再次运行监督循环
    if config.post_pr_supervisor.enabled:
        pr_context = github_client.get_pull_request_context(pr_branch)
        if pr_context is None:
            _logger.warning(
                "Deferring post-rework supervisor for Issue #%d branch %s: "
                "complete PR context is unavailable.",
                issue.number,
                pr_branch,
            )
            return
        _run_supervisor_with_repair_loop(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            github_client=github_client,
            process_runner=process_runner,
            pr_context=pr_context,
            supervisor_agent=supervisor_agent,
            repo_id=repo_id,
        )
    else:
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.review)


def _process_running_publish_recovery(
    *,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None = None,
    publish_stage: PublishStage = PublishStage.NORMAL,
    stage_selection: PublishStageSelection | None = None,
    cleanup_only: bool = False,
    repo_id: str | None = None,
    **kwargs: object,
) -> None:
    """恢复 running Issue 的发布流程。

    用于 runner 重启后发现 running Issue 已有本地 commit 的情况。
    通过复用已有 commit 来完成发布，无需重新运行 Agent。

    Args:
        issue: Issue 对象
        repo_path: 仓库根目录
        config: 应用配置
        agent: Agent 覆盖
        github_client: GitHub 客户端
        process_runner: 进程运行器
        content_generator: 可选的 AI 内容生成器
        publish_stage: 发布档位：FAST / DIRECT 跳过发布前的最终 RV / verifier 复核；
            PRD_AUTO_MERGE 仍重跑 RV，只跳过 verifier。
        cleanup_only: 仅补既有 PR 交接；关联失效时拒绝转成普通发布。
        repo_id: 稳定仓库标识（Issue #278），透传给恢复发布路径做 verdict
            发布守卫与 PR 后监督的人工接管闸门检查。
    """
    selected_agent = choose_agent(issue, config, agent)

    # 定位 worktree 并确认分支
    worktree_path = _find_worktree_path_for_issue(repo_path, issue, config, process_runner)
    expected_branch = f"issue-{issue.number}"

    # 原子锁：恢复路径会对 worktree 做 rebase 治愈与发布等写操作，必须与其他
    # runner（含 blocked 恢复）在同一 worktree 上互斥，否则并发 git 操作会互相
    # 破坏一个本就脆弱的 mid-rebase 工作区。锁被活进程持有时抛
    # BlockedWorktreeClaimedError，由 run_once 调度循环记日志后跳过。
    lock_path = worktree_claim_lock_path(worktree_path)
    _acquire_blocked_claim_lock(lock_path, issue.number)
    try:
        _ensure_worktree_branch(worktree_path, expected_branch, issue, config, process_runner)

        # 共享恢复选择先交接已发布轮次，再对新工作应用 fresh 准入。
        stage_decision = resolve_recovery_selection(
            RecoverySelectionRequest(
                selection=PublishStageSelectionRequest(
                    publish_stage, issue, config, github_client, stage_selection
                ),
                claim=lambda: arbitrate_first_claim(
                    issue_number=issue.number,
                    github_client=github_client,
                    config=config,
                    selected_agent=selected_agent,
                ),
                candidate=lambda: DirectPrPublicationCandidate(
                    expected_branch, get_head_sha(worktree_path, process_runner)
                ),
                handoff=lambda decision, candidate: _resume_direct_pr_handoff(
                    github_client, decision, candidate, config
                ),
                cleanup_only=cleanup_only,
            )
        )
        if stage_decision is None:
            return
        issue = stage_decision.issue
        publish_stage = stage_decision.publish_stage

        # 检查是否有可复用的本地 commit
        commit_result = (
            _reuse_local_commit(
                LocalCommitReuseRequest(issue, worktree_path, config, process_runner, publish_stage)
            )
            if publish_stage is PublishStage.DIRECT
            else _reuse_existing_local_commit(issue, worktree_path, config, process_runner)
        )
        if commit_result is None:
            raise RuntimeError(
                f"Issue #{issue.number} has no clean local commit ready for publication."
            )

        # 完成发布流程（恢复路径）
        _finish_existing_commit_publication(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            selected_agent=selected_agent,
            github_client=github_client,
            process_runner=process_runner,
            expected_branch=expected_branch,
            commit_result=commit_result,
            content_generator=content_generator,
            publish_stage=publish_stage,
            direct_pr_label=stage_decision.direct_pr_label,
            repo_id=repo_id,
        )
    finally:
        _release_blocked_claim_lock(lock_path)

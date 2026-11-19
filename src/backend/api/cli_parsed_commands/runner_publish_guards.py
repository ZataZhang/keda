"""CLI run 发布旁路的 Issue 读取与准入门禁。"""

from __future__ import annotations

from pathlib import Path

from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_runner_factory import create_machine_identity_store


def _resolve_publish_stage(*, fast_merge: bool, direct_pr: bool) -> PublishStage:
    """把 ``kc run`` 的两个旁路旗标折算成一个发布档位。

    两个旗标刻意互斥而不「取更强者」：它们的存在感不同（一个跳独立验证，一个连
    reviewer 与仓库验证一起跳），静默升级会掩盖调用者的真实意图。

    Args:
        fast_merge: 是否给出 ``--fast-merge``。
        direct_pr: 是否给出 ``--direct-pr``。

    Returns:
        本次运行的发布档位。

    Raises:
        CliError: 两个旗标同时给出。
    """
    if fast_merge and direct_pr:
        raise CliError(
            "--fast-merge and --direct-pr are mutually exclusive; --direct-pr already "
            "includes everything --fast-merge skips, so combining them is ambiguous "
            "rather than stronger.",
            code=ExitCode.USAGE,
            suggestion="kc run --issue <N> --fast-merge · kc run --issue <N> --direct-pr",
        )
    if direct_pr:
        return PublishStage.DIRECT
    if fast_merge:
        return PublishStage.FAST
    return PublishStage.NORMAL


def _target_issue_details(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
    flag_name: str,
    check_purpose: str,
) -> list:
    """读取每个目标仓库里该 Issue 的详情；读不到即以用法错误拒绝旁路档位。

    两个旁路档位的前置检查都是 fail-closed 的：无法从 Issue 详情证明条件成立，就不能
    放行跳过门禁的运行。

    Args:
        ctx: 已解析命令上下文（提供 Issue 读取客户端工厂）。
        contexts: 目标仓库列表（同仓只读一次）。
        target_issue: 目标 Issue 编号。
        flag_name: 触发本次检查的旗标名，用于报错与修复建议。
        check_purpose: 读取详情要回答的问题（用于报错信息）。

    Returns:
        每个去重后仓库的 Issue 详情（含正文与标签）。

    Raises:
        CliError: Issue 无法读取。
    """
    details: list = []
    for context in _unique_repository_contexts(contexts):
        github_client = ctx.github_client_factory(context.repo_path)
        try:
            details.append(github_client.get_issue(target_issue))
        except Exception as exc:  # noqa: BLE001 - 无法证明前置条件即拒绝旁路
            raise CliError(
                f"{flag_name} requires reading Issue #{target_issue} to check "
                f"{check_purpose}, but the lookup failed: {exc}",
                code=ExitCode.USAGE,
                suggestion=f"Verify the Issue number and repository access, "
                f"or rerun without {flag_name}.",
            ) from exc
    return details


def _target_issue_bodies(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
    flag_name: str,
    check_purpose: str,
) -> list[str]:
    """读取每个目标仓库里该 Issue 的正文（``_target_issue_details`` 的正文投影）。"""
    return [
        issue_detail.body
        for issue_detail in _target_issue_details(
            ctx,
            contexts=contexts,
            target_issue=target_issue,
            flag_name=flag_name,
            check_purpose=check_purpose,
        )
    ]


def _unique_repository_contexts(contexts: list) -> list:
    """按仓库路径去重的目标上下文列表（多仓 ``--repo`` 时同一仓只查一次）。"""
    unique: list = []
    seen_paths: set[Path] = set()
    for context in contexts:
        if context.repo_path in seen_paths:
            continue
        seen_paths.add(context.repo_path)
        unique.append(context)
    return unique


def _require_explicit_target_claimable(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
    ignore_active_local_claim: bool = False,
) -> None:
    """显式定向准入：目标不可领取时给出响亮错误与非零退出码（FR-21）。

    错误原因由 core 层给出（core 不引用 ``ExitCode``），这里映射成退出码：
    读不到 / 非 open → ``NOT_FOUND (3)``，被他人活跃认领或 blocked 未解除 →
    ``CONFLICT (5)``。确认认领归属时 GitHub 评论结构不完整（缺更新时间或作者）会
    直接报错，同样按 ``CONFLICT (5)`` 回报「现在领不动」，而不是把 traceback
    抛给操作者。

    Raises:
        CliError: 目标 Issue 当前不可被本进程领取，或无法确认其认领归属。
    """
    from backend.core.use_cases.run_target_admission import (
        TARGET_UNCLAIMABLE_NOT_FOUND,
        TargetNotClaimableError,
        require_explicit_target_claimable,
    )

    for context in _unique_repository_contexts(contexts):
        try:
            github_client = ctx.github_client_factory(context.repo_path)
            machine_id = create_machine_identity_store().load_or_create().machine_id
            from backend.core.use_cases.agent_runner_reconcile import reconcile_stale_attempts

            require_explicit_target_claimable(
                issue_number=target_issue,
                github_client=github_client,
                config=context.config,
                machine_id=machine_id,
                ignore_active_local_claim=ignore_active_local_claim,
            )
            reconcile_stale_attempts(
                repo_path=context.repo_path,
                config=context.config,
                github_client=github_client,
                process_runner=ctx.process_runner,
                issue_number=target_issue,
                machine_id=machine_id,
            )
            require_explicit_target_claimable(
                issue_number=target_issue,
                github_client=github_client,
                config=context.config,
                machine_id=machine_id,
                ignore_active_local_claim=ignore_active_local_claim,
            )
        except TargetNotClaimableError as exc:
            raise CliError(
                str(exc),
                code=(
                    ExitCode.NOT_FOUND
                    if exc.reason == TARGET_UNCLAIMABLE_NOT_FOUND
                    else ExitCode.CONFLICT
                ),
                suggestion=exc.suggestion,
            ) from exc
        except RuntimeError as exc:
            raise CliError(
                f"Cannot confirm the claim on Issue #{target_issue}: {exc}",
                code=ExitCode.CONFLICT,
                suggestion=(
                    f"kc peers --repo-id {context.repo_id}  " "# 确认机器状态与在途认领后重试"
                ),
            ) from exc


def _has_existing_direct_pr_cleanup(
    github_client: IGitHubClient, issue_detail: IssueSummary
) -> bool:
    """只读核验成功同轮 PR，让 core 认领后补交接而非重新构建。"""
    from backend.core.use_cases.agent_runner_direct_pr_round import has_readonly_direct_pr_cleanup

    try:
        return has_readonly_direct_pr_cleanup(github_client, issue_detail)
    except Exception as exc:
        raise CliError(
            f"Cannot establish existing Direct PR handoff for Issue #{issue_detail.number}: {exc}",
            code=ExitCode.USAGE,
        ) from exc


def _reject_fast_merge_on_stack_issue(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
) -> None:
    """快速通道 stack 门禁：目标 Issue 声明 ``mode="stack"`` 依赖时拒绝放行。

    检查发生在任何 agent 启动与 daemon 接管之前；Issue 取不到时同样拒绝
    （无法证明不是 stack 就不走旁路，fail-closed）。

    Raises:
        CliError: 目标 Issue 声明 stack 顺序依赖，或 Issue 无法读取。
    """
    from backend.core.use_cases.agent_runner_dependencies import parse_dependency_marker

    for context in _unique_repository_contexts(contexts):
        for issue_detail in _target_issue_details(
            ctx,
            contexts=[context],
            target_issue=target_issue,
            flag_name="--fast-merge",
            check_purpose="its dependency declaration",
        ):
            declaration = parse_dependency_marker(issue_detail.body)
            if declaration is None or declaration.sequence != "stack":
                continue
            if _has_existing_direct_pr_cleanup(
                ctx.github_client_factory(context.repo_path), issue_detail
            ):
                continue
            upstream = ", ".join(f"#{number}" for number in declaration.issue_numbers)
            raise CliError(
                f"Issue #{target_issue} declares a stack dependency (upstream: {upstream}); "
                "--fast-merge is rejected because an unverified upstream would poison "
                "every fork on the chain.",
                code=ExitCode.USAGE,
                suggestion=f"Run Issue #{target_issue} without --fast-merge, or "
                "fast-merge the upstream Issue first.",
            )


def _reject_fast_merge_on_direct_pr_label(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
) -> None:
    """快速通道与直发标签的冲突门禁：目标 Issue 带 ``direct-pr`` 标签时拒绝放行。

    两个旁路的跳入门禁范围不同（直发连审核 agent 与仓内验证一起跳），同时给出时不猜
    优先级，明确报冲突。core 在认领后还会再判一次，这里让单定向的调用方在启动任何
    agent（乃至 takeover 停 daemon）之前就听到错误。Issue 读不到时同样拒绝（fail-closed）。

    Raises:
        CliError: 目标 Issue 带直发标签，或 Issue 无法读取。
    """
    from backend.core.use_cases.agent_runner_direct_pr_label import configured_direct_pr_label

    for context in _unique_repository_contexts(contexts):
        label = configured_direct_pr_label(context.config)
        if label is None:
            continue
        for issue_detail in _target_issue_details(
            ctx,
            contexts=[context],
            target_issue=target_issue,
            flag_name="--fast-merge",
            check_purpose=f"whether it carries the '{label}' label",
        ):
            if label in issue_detail.labels:
                if _has_existing_direct_pr_cleanup(
                    ctx.github_client_factory(context.repo_path), issue_detail
                ):
                    continue
                raise CliError(
                    f"Issue #{target_issue} carries the '{label}' label while --fast-merge "
                    "was requested; the two bypass tiers skip different gates, so neither "
                    "is silently chosen.",
                    code=ExitCode.USAGE,
                    suggestion=f"kc run --issue {target_issue} (drops --fast-merge and honors the "
                    f"Issue direct track) · or remove '{label}' from Issue #{target_issue} to run "
                    "the fast track.",
                )


def _reject_direct_pr_on_prd_backed_issue(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
) -> None:
    """直发档范围门禁：目标 Issue 带 PRD 锚点时拒绝放行（FR-16）。

    PRD-backed Issue 必须走 PRD 交付门（校验验收清单、归档 PRD），那是 PRD 卫生的
    一部分，不能被「快速出 PR」旁路。Issue 正文读不到时同样拒绝（fail-closed）。

    Raises:
        CliError: 目标 Issue 带 PRD 锚点，或 Issue 无法读取。
    """
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path

    for context in _unique_repository_contexts(contexts):
        for issue_detail in _target_issue_details(
            ctx,
            contexts=[context],
            target_issue=target_issue,
            flag_name="--direct-pr",
            check_purpose="whether it carries a PRD anchor",
        ):
            prd_path = extract_prd_path(issue_detail.body)
            if prd_path is None or _has_existing_direct_pr_cleanup(
                ctx.github_client_factory(context.repo_path), issue_detail
            ):
                continue
            raise CliError(
                f"Issue #{target_issue} is PRD-backed (anchor: `{prd_path}`); --direct-pr "
                "is only defined for Issues without a PRD anchor, because a PRD-backed "
                "Issue must pass the PRD delivery gate and archive its PRD.",
                code=ExitCode.USAGE,
                suggestion=f"kc run --issue {target_issue} --fast-merge (keeps the "
                "reviewer and repository verification, and the PRD gate still applies) "
                f"· or rerun --direct-pr without a PRD-anchored Issue.",
            )

"""管理终端的白名单操作动作分发与审计。

支持的动作（白名单，未知动作一律拒绝）：

- ``run_once`` / ``review_once``：为目标仓库启动一次性托管子进程。
- ``retry_failed``：将 failed Issue 的 label 翻转回 ready（与手工
  ``gh`` 操作等价，不绕过 workflow 状态机）。
- ``blocked_continue``：启动一次性 ``kc blocked-continue`` 托管子进程
  （agent 执行耗时长，必须进程隔离，不能在 API 进程内跑）。
- ``recover_failed_publish``：复用 CLI ``kc recover`` 的既有恢复用例，把已提交
  但推送/建 PR 失败的发布收尾（不重跑 agent、不产生新提交）。

所有动作（含被拒绝与出错的）都写入审计日志。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.interfaces.runner_console import (
    AuditEntry,
    IRunHistoryStore,
    IRunnerProcessSupervisor,
    RunnerProcessKind,
    RunnerProcessRecord,
)
from backend.core.shared.models.agent_runner import (
    PublishRecoveryRequest,
    RepositoryRunContext,
)
from backend.core.use_cases.agent_runner_issue_control import (
    IssueControlUnavailableError,
    request_verifier_takeover,
)
from backend.core.use_cases.console_processes import (
    ConsoleProcessError,
    start_runner_process,
)
from backend.core.use_cases.recover_publish import (
    PublishRecoveryError,
    recover_publish_issue,
)

_logger = logging.getLogger(__name__)

_CONSOLE_ACTOR = "console"

#: 仓库级动作 → 托管进程类型。
REPOSITORY_ACTIONS: dict[str, RunnerProcessKind] = {
    "run_once": RunnerProcessKind.RUN_ONCE,
    "review_once": RunnerProcessKind.REVIEW_ONCE,
}

#: Issue 级动作白名单。
ISSUE_ACTIONS = (
    "retry_failed",
    "blocked_continue",
    "recover_failed_publish",
    "take_over_verifier",
)


class ConsoleActionError(ValueError):
    """动作被拒绝（未知动作、状态不满足或参数非法）。"""


@dataclass(frozen=True)
class ConsoleActionResult:
    """一次动作执行的结果。"""

    action: str
    result: str  # accepted / rejected / error
    detail: str
    process: RunnerProcessRecord | None = None
    takeover_http_status: int | None = None
    """人工接管动作的 HTTP 语义（202/200/409）；非接管动作为 ``None``。"""
    takeover_outcome: str | None = None
    """接管请求分类（accepted/pending/handed_over/*_conflict/not_found）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(
    store: IRunHistoryStore,
    *,
    action: str,
    repo_id: str | None,
    issue_number: int | None,
    params_json: str,
    result: str,
    detail: str | None,
) -> None:
    store.append_audit(
        AuditEntry(
            occurred_at=_now_iso(),
            actor=_CONSOLE_ACTOR,
            action=action,
            repo_id=repo_id,
            issue_number=issue_number,
            params_json=params_json,
            result=result,
            detail=detail,
        )
    )


def _find_context(repo_id: str, contexts: Sequence[RepositoryRunContext]) -> RepositoryRunContext:
    for context in contexts:
        if context.repo_id == repo_id:
            return context
    raise ConsoleActionError(f"Repository '{repo_id}' is not an enabled registry target.")


def execute_repository_action(
    *,
    action: str,
    repo_id: str,
    contexts: Sequence[RepositoryRunContext],
    supervisor: IRunnerProcessSupervisor,
    store: IRunHistoryStore,
    runner_command: Sequence[str],
    spawn_cwd: Path,
) -> ConsoleActionResult:
    """执行一个仓库级动作（run_once / review_once）。

    Returns:
        ConsoleActionResult: 含被启动的一次性进程记录。
    """
    params_json = f'{{"action": "{action}", "repo_id": "{repo_id}"}}'
    if action not in REPOSITORY_ACTIONS:
        detail = f"Unknown repository action '{action}'."
        _audit(
            store,
            action=action,
            repo_id=repo_id,
            issue_number=None,
            params_json=params_json,
            result="rejected",
            detail=detail,
        )
        raise ConsoleActionError(detail)
    try:
        process_record = start_runner_process(
            repo_id=repo_id,
            kind=REPOSITORY_ACTIONS[action],
            contexts=contexts,
            supervisor=supervisor,
            runner_command=runner_command,
            spawn_cwd=spawn_cwd,
        )
    except ConsoleProcessError as exc:
        _audit(
            store,
            action=action,
            repo_id=repo_id,
            issue_number=None,
            params_json=params_json,
            result="rejected",
            detail=str(exc),
        )
        raise ConsoleActionError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - audit unexpected failures too.
        _audit(
            store,
            action=action,
            repo_id=repo_id,
            issue_number=None,
            params_json=params_json,
            result="error",
            detail=str(exc),
        )
        raise
    detail = f"Spawned {action} process {process_record.process_id}."
    _audit(
        store,
        action=action,
        repo_id=repo_id,
        issue_number=None,
        params_json=params_json,
        result="accepted",
        detail=detail,
    )
    return ConsoleActionResult(
        action=action, result="accepted", detail=detail, process=process_record
    )


def _execute_retry_failed(
    *,
    issue_number: int,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
) -> str:
    issue = github_client.get_issue(issue_number)
    failed_label = context.config.labels.failed
    ready_label = context.config.labels.ready
    if failed_label not in issue.labels:
        raise ConsoleActionError(
            f"Issue #{issue_number} does not carry label '{failed_label}'; "
            "retry_failed only applies to failed Issues."
        )
    github_client.edit_issue_labels(issue_number, add=[ready_label], remove=[failed_label])
    return f"Issue #{issue_number}: '{failed_label}' -> '{ready_label}'."


def _execute_recover_failed_publish(
    *,
    issue_number: int,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
) -> str:
    """恢复失败的发布：直接调用 CLI ``kc recover`` 背后的同一个用例。

    PR 正文生成与 CLI 一致地传入内容生成器；生成器工厂只在真的执行恢复时才取，
    避免其他动作无谓地把 engines 装配层拉进调用路径。
    """
    from backend.core.use_cases.agent_runner_factory import create_content_generator

    try:
        recovery_result = recover_publish_issue(
            request=PublishRecoveryRequest(issue_number=issue_number),
            repo_path=context.repo_path,
            config=context.config,
            github_client=github_client,
            process_runner=process_runner,
            content_generator=create_content_generator(process_runner, config=context.config),
            repo_id=context.repo_id,
        )
    except PublishRecoveryError as exc:
        raise ConsoleActionError(
            f"Issue #{issue_number} 发布恢复失败（{exc.failure_category}）：{exc}"
        ) from exc
    reused_suffix = "（复用已有 PR）" if recovery_result.pr_reused else ""
    return (
        f"Issue #{issue_number} 发布已恢复：{recovery_result.pr_url}{reused_suffix}，"
        f"分支 {recovery_result.branch}。"
    )


def _reject_takeover(
    store: IRunHistoryStore,
    *,
    repo_id: str,
    issue_number: int,
    params_json: str,
    detail: str,
) -> ConsoleActionError:
    """记录被拒绝的接管审计并返回待抛出的 ConsoleActionError（路由映射 400）。"""
    _audit(
        store,
        action="take_over_verifier",
        repo_id=repo_id,
        issue_number=issue_number,
        params_json=params_json,
        result="rejected",
        detail=detail,
    )
    return ConsoleActionError(detail)


def _execute_take_over_verifier(
    *,
    store: IRunHistoryStore,
    repo_id: str,
    issue_number: int,
    run_id: str | None,
    head_sha: str | None,
    params_json: str,
) -> ConsoleActionResult:
    """按 run id 与 HEAD SHA 精确请求接管当前 Issue 的独立 verifier。

    接管只登记取消意图（CAS ``running`` → ``takeover_requested``），进程是否真的
    停下由 runner 侧回收后回读控制行确认——本动作绝不自称"进程已停止"。分类结果
    映射到 HTTP 语义（202 处理中 / 200 已交班 / 409 状态已变化）。
    """
    if not run_id or not head_sha:
        raise _reject_takeover(
            store,
            repo_id=repo_id,
            issue_number=issue_number,
            params_json=params_json,
            detail="take_over_verifier requires run_id and head_sha.",
        )
    try:
        takeover = request_verifier_takeover(
            repo_id=repo_id,
            issue_number=issue_number,
            run_id=run_id,
            head_sha=head_sha,
            control_store=store,
        )
    except IssueControlUnavailableError as exc:
        raise _reject_takeover(
            store,
            repo_id=repo_id,
            issue_number=issue_number,
            params_json=params_json,
            detail=str(exc),
        ) from exc

    accepted = takeover.http_status in (200, 202)
    detail = f"takeover outcome={takeover.outcome}; observed_state={takeover.observed_state}"
    _audit(
        store,
        action="take_over_verifier",
        repo_id=repo_id,
        issue_number=issue_number,
        params_json=params_json,
        result="accepted" if accepted else "rejected",
        detail=detail,
    )
    return ConsoleActionResult(
        action="take_over_verifier",
        result="accepted" if accepted else "rejected",
        detail=detail,
        takeover_http_status=takeover.http_status,
        takeover_outcome=takeover.outcome,
    )


def execute_issue_action(
    *,
    action: str,
    repo_id: str,
    issue_number: int,
    contexts: Sequence[RepositoryRunContext],
    github_client_factory: Callable[[Path], IGitHubClient],
    supervisor: IRunnerProcessSupervisor,
    store: IRunHistoryStore,
    runner_command: Sequence[str],
    spawn_cwd: Path,
    process_runner: IProcessRunner,
    run_id: str | None = None,
    head_sha: str | None = None,
) -> ConsoleActionResult:
    """执行一个 Issue 级动作（retry_failed / blocked_continue / recover_failed_publish / take_over_verifier）。"""
    params_payload: dict[str, object] = {
        "action": action,
        "repo_id": repo_id,
        "issue_number": issue_number,
    }
    if run_id is not None:
        params_payload["run_id"] = run_id
    if head_sha is not None:
        params_payload["head_sha"] = head_sha
    params_json = json.dumps(params_payload)

    def _reject(detail: str) -> ConsoleActionError:
        _audit(
            store,
            action=action,
            repo_id=repo_id,
            issue_number=issue_number,
            params_json=params_json,
            result="rejected",
            detail=detail,
        )
        return ConsoleActionError(detail)

    if action not in ISSUE_ACTIONS:
        raise _reject(f"Unknown issue action '{action}'.")
    if issue_number <= 0:
        raise _reject("issue_number must be a positive integer.")

    try:
        context = _find_context(repo_id, contexts)
    except ConsoleActionError as exc:
        raise _reject(str(exc)) from exc

    if action == "take_over_verifier":
        return _execute_take_over_verifier(
            store=store,
            repo_id=repo_id,
            issue_number=issue_number,
            run_id=run_id,
            head_sha=head_sha,
            params_json=params_json,
        )

    try:
        if action == "retry_failed":
            github_client = github_client_factory(context.repo_path)
            detail = _execute_retry_failed(
                issue_number=issue_number,
                context=context,
                github_client=github_client,
            )
            process_record = None
        elif action == "recover_failed_publish":
            github_client = github_client_factory(context.repo_path)
            detail = _execute_recover_failed_publish(
                issue_number=issue_number,
                context=context,
                github_client=github_client,
                process_runner=process_runner,
            )
            process_record = None
        else:  # blocked_continue
            process_record = start_runner_process(
                repo_id=repo_id,
                kind=RunnerProcessKind.BLOCKED_CONTINUE,
                contexts=contexts,
                supervisor=supervisor,
                runner_command=runner_command,
                spawn_cwd=spawn_cwd,
                issue_number=issue_number,
            )
            detail = (
                f"Spawned blocked-continue process {process_record.process_id} "
                f"for Issue #{issue_number}."
            )
    except ConsoleActionError as exc:
        raise _reject(str(exc)) from exc
    except ConsoleProcessError as exc:
        raise _reject(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - audit unexpected failures too.
        _audit(
            store,
            action=action,
            repo_id=repo_id,
            issue_number=issue_number,
            params_json=params_json,
            result="error",
            detail=str(exc),
        )
        raise

    _audit(
        store,
        action=action,
        repo_id=repo_id,
        issue_number=issue_number,
        params_json=params_json,
        result="accepted",
        detail=detail,
    )
    return ConsoleActionResult(
        action=action, result="accepted", detail=detail, process=process_record
    )

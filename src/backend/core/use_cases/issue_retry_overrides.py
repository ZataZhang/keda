"""Issue 单次续跑执行器覆盖的可选本地存储能力。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from backend.core.shared.interfaces.runner_console import IIssueRetryOverrideStore
from backend.core.shared.models.agent_runner_config import AppConfig, IssueSummary
from backend.core.use_cases.runtime_failure_diagnosis import RETRYABLE_LIFECYCLE_STAGES


@dataclass(frozen=True)
class IssueRetryOverrideRequest:
    """Runner 解析单次 Issue 执行器覆盖所需的上下文。"""

    issue: IssueSummary
    issue_kind: str
    repo_id: str
    config: AppConfig
    run_history_store: object | None


@dataclass
class _IssueRetryOverrideClaim:
    """等待 Runner 成功认领后消费的一次性覆盖。"""

    store: IIssueRetryOverrideStore
    repo_id: str
    issue_number: int
    selection: tuple[str, str]
    consumed: bool = False

    def validate_selected_executor(self, selected_executor: str) -> None:
        """保证 verifier 与实现执行器保持独立。"""
        stage, executor = self.selection
        if stage == "verifier" and executor == selected_executor:
            raise ValueError(
                f"Verifier executor '{executor}' matches the implementation executor; "
                "choose a different verifier to preserve independent review."
            )

    def consume(self) -> None:
        """在 GitHub Issue 认领成功后原子消费覆盖。"""
        if self.consumed:
            return
        consumed_override = self.store.pop_issue_retry_override(
            repo_id=self.repo_id,
            issue_number=self.issue_number,
        )
        if consumed_override != self.selection:
            raise RuntimeError(
                "The one-time executor override changed before the Issue claim completed; "
                "refresh the dashboard and retry."
            )
        self.consumed = True


def resolve_issue_retry_override_store(store: object | None) -> IIssueRetryOverrideStore | None:
    """从 Console SQLite 读写对象中识别单次覆盖能力。"""
    if store is None:
        return None
    required_methods = (
        "set_issue_retry_override",
        "get_issue_retry_override",
        "pop_issue_retry_override",
        "clear_issue_retry_override",
    )
    if all(callable(getattr(store, method_name, None)) for method_name in required_methods):
        return cast(IIssueRetryOverrideStore, store)
    return None


def prepare_issue_retry_override(
    request: IssueRetryOverrideRequest,
) -> tuple[IssueSummary, _IssueRetryOverrideClaim | None]:
    """验证并应用待处理的单次执行器覆盖。"""
    override_store = resolve_issue_retry_override_store(request.run_history_store)
    if override_store is None or request.issue_kind != "ready":
        return request.issue, None

    selection = override_store.get_issue_retry_override(
        repo_id=request.repo_id,
        issue_number=request.issue.number,
    )
    if selection is None:
        return request.issue, None

    stage, executor = selection
    if stage not in RETRYABLE_LIFECYCLE_STAGES:
        raise ValueError(f"Unsupported one-time retry lifecycle stage '{stage}'.")
    agent_spec = request.config.agents.get(executor)
    if agent_spec is None or "run" not in agent_spec.profiles:
        raise ValueError(
            f"One-time retry executor '{executor}' is no longer registered "
            "with a run profile. Choose a registered executor and retry again."
        )

    lifecycle_overrides = dict(request.issue.lifecycle_overrides)
    lifecycle_overrides[stage] = executor
    prepared_issue = replace(
        request.issue,
        lifecycle_overrides=tuple(lifecycle_overrides.items()),
    )
    claim = _IssueRetryOverrideClaim(
        store=override_store,
        repo_id=request.repo_id,
        issue_number=request.issue.number,
        selection=selection,
    )
    return prepared_issue, claim

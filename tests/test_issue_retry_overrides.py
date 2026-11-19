"""Issue 单次续跑执行器覆盖用例测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.core.shared.models.agent_runner_config import AppConfig, IssueSummary
from backend.core.use_cases.issue_retry_overrides import (
    IssueRetryOverrideRequest,
    prepare_issue_retry_override,
)


class _IssueRetryOverrideStore:
    def __init__(self, selection: tuple[str, str] | None) -> None:
        self.selection = selection
        self.pop_count = 0

    def set_issue_retry_override(
        self, *, repo_id: str, issue_number: int, stage: str, executor: str
    ) -> None:
        self.selection = (stage, executor)

    def get_issue_retry_override(
        self, *, repo_id: str, issue_number: int
    ) -> tuple[str, str] | None:
        return self.selection

    def pop_issue_retry_override(
        self, *, repo_id: str, issue_number: int
    ) -> tuple[str, str] | None:
        self.pop_count += 1
        selection = self.selection
        self.selection = None
        return selection

    def clear_issue_retry_override(self, *, repo_id: str, issue_number: int) -> None:
        self.selection = None


def test_prepare_retry_override_applies_and_consumes_once() -> None:
    store = _IssueRetryOverrideStore(("implementation", "claude"))
    issue = _issue()

    prepared_issue, claim = prepare_issue_retry_override(
        IssueRetryOverrideRequest(
            issue=issue,
            issue_kind="ready",
            repo_id="keda-main",
            config=AppConfig(),
            run_history_store=store,
        )
    )

    # 覆盖必须落在「一次性显式执行器覆盖」层：矩阵 / PRD 覆盖层会被阶段预设
    # 绑定遮蔽，投递到那一层会让用户选的执行器永远不生效。
    assert prepared_issue.explicit_executor_overrides == (("implementation", "claude"),)
    assert prepared_issue.lifecycle_overrides == ()
    assert claim is not None
    assert store.selection == ("implementation", "claude")
    claim.consume()
    claim.consume()
    assert store.selection is None
    assert store.pop_count == 1


def test_prepare_retry_override_keeps_existing_explicit_overrides() -> None:
    """已有的一次性覆盖不丢：换一个阶段覆盖时另一个阶段的选择仍在。"""
    store = _IssueRetryOverrideStore(("verifier", "claude"))
    issue = replace(_issue(), explicit_executor_overrides=(("implementation", "codex"),))

    prepared_issue, _ = prepare_issue_retry_override(
        IssueRetryOverrideRequest(
            issue=issue,
            issue_kind="ready",
            repo_id="keda-main",
            config=AppConfig(),
            run_history_store=store,
        )
    )

    assert prepared_issue.explicit_executor_overrides == (
        ("implementation", "codex"),
        ("verifier", "claude"),
    )


def test_prepare_retry_override_rejects_unregistered_executor() -> None:
    store = _IssueRetryOverrideStore(("implementation", "missing"))

    with pytest.raises(ValueError, match="no longer registered"):
        prepare_issue_retry_override(
            IssueRetryOverrideRequest(
                issue=_issue(),
                issue_kind="ready",
                repo_id="keda-main",
                config=AppConfig(agents={}),
                run_history_store=store,
            )
        )


def test_verifier_override_keeps_implementation_independent() -> None:
    store = _IssueRetryOverrideStore(("verifier", "claude"))
    _, claim = prepare_issue_retry_override(
        IssueRetryOverrideRequest(
            issue=_issue(),
            issue_kind="ready",
            repo_id="keda-main",
            config=AppConfig(),
            run_history_store=store,
        )
    )

    assert claim is not None
    with pytest.raises(ValueError, match="matches the implementation executor"):
        claim.validate_selected_executor("claude")
    claim.validate_selected_executor("codex")


def _issue() -> IssueSummary:
    return IssueSummary(
        number=19,
        title="Retry with a different executor",
        url="https://github.com/example/repo/issues/19",
        body="",
        labels=("agent/ready",),
    )


def test_fresh_issue_snapshot_adoption_keeps_one_time_executor_override() -> None:
    """认领后采用 fresh 快照时，一次性执行器覆盖必须仍在。

    断点就在这里：认领后用 GitHub fresh 快照整体替换 Issue，本地附加的覆盖字段
    （PRD 级 lifecycle 覆盖 + 一次性显式执行器覆盖）被抹掉——review / supervisor
    这类按覆盖选执行器的阶段因此静默回落，阶段预设绑定照旧生效。
    """
    from backend.core.use_cases.agent_runner_issue_handlers import (
        _adopt_fresh_issue_snapshot,
    )

    previous_issue = replace(
        _issue(),
        lifecycle_overrides=(("review", "codex"),),
        lifecycle_preset_overrides=(("review", "codex-luna-max"),),
        explicit_executor_overrides=(("review", "mcode"),),
    )
    fresh_issue = replace(_issue(), labels=("agent/running",))

    adopted_issue = _adopt_fresh_issue_snapshot(fresh_issue, previous_issue)

    # fresh 快照的标签生效。
    assert adopted_issue.labels == ("agent/running",)
    # 本地覆盖字段仍在：否则覆盖选中的执行器根本没机会生效。
    assert adopted_issue.explicit_executor_overrides == (("review", "mcode"),)
    assert adopted_issue.lifecycle_overrides == (("review", "codex"),)
    assert adopted_issue.lifecycle_preset_overrides == (("review", "codex-luna-max"),)


def test_review_stage_uses_override_after_fresh_snapshot_adoption() -> None:
    """认领后采用 fresh 快照 → review 阶段仍按覆盖选执行器，不被阶段预设遮蔽。"""
    from backend.core.shared.models.agent_model_preset import AgentModelPreset
    from backend.core.shared.models.lifecycle_agent import LifecyclePresetsConfig
    from backend.core.use_cases.agent_runner_agent_selection import resolve_reviewer_agent
    from backend.core.use_cases.agent_runner_issue_handlers import (
        _adopt_fresh_issue_snapshot,
    )

    config = AppConfig(
        agent_presets={"codex-luna-max": AgentModelPreset(agent="codex", model="gpt-6-luna")},
        lifecycle_presets=LifecyclePresetsConfig(global_layer={"review": "codex-luna-max"}),
    )
    previous_issue = replace(_issue(), explicit_executor_overrides=(("review", "mcode"),))
    fresh_issue = replace(_issue(), labels=("agent/running",))

    adopted_issue = _adopt_fresh_issue_snapshot(fresh_issue, previous_issue)

    assert resolve_reviewer_agent(adopted_issue, config, "codex") == "mcode"

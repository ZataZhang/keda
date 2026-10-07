"""验证当前认领选择冻结、fresh 依赖准入及落败方写入隔离。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases import agent_runner_issue_handlers as handlers
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimArbitrationLost
from backend.core.use_cases.agent_runner_direct_pr_label import (
    DirectPrNotEligibleError,
    PublishStageSelection,
    PublishStageSelectionRequest,
    resolve_claim_publish_stage,
)
from tests.conftest import FakeProcessRunner
from tests.support.agent_runner import config_with_review_disabled
from tests.test_agent_runner_direct_pr_label import _client, _issue, _patch_ready_handler


@pytest.mark.parametrize(
    ("requested", "initial_labels", "fallback_labels", "expected"),
    [
        (
            PublishStage.NORMAL,
            ("agent/ready", "direct-pr"),
            ("agent/running",),
            PublishStage.DIRECT,
        ),
        (
            PublishStage.NORMAL,
            ("agent/ready",),
            ("agent/running", "direct-pr"),
            PublishStage.NORMAL,
        ),
        (PublishStage.FAST, ("agent/ready",), ("agent/running", "direct-pr"), PublishStage.FAST),
        (
            PublishStage.DIRECT,
            ("agent/ready",),
            ("agent/running", "direct-pr"),
            PublishStage.DIRECT,
        ),
    ],
)
def test_fallback_freezes_selection_but_refreshes_body(
    requested, initial_labels, fallback_labels, expected
) -> None:
    """标签增删影响下一次认领；当前 fallback 仍使用已选择档位及最新正文。"""
    config = config_with_review_disabled()
    client = _client(labels=initial_labels, body="original")
    selection = PublishStageSelection()
    snapshot = _issue(labels=initial_labels)
    request = PublishStageSelectionRequest(requested, snapshot, config, client, selection)
    selection.decision = resolve_claim_publish_stage(request)
    client.set_issue_labels(snapshot.number, fallback_labels)
    client.set_issue_body(snapshot.number, "fresh fallback body")

    fallback = resolve_claim_publish_stage(request)

    assert fallback.publish_stage is expected
    assert fallback.direct_pr_label == selection.decision.direct_pr_label
    assert fallback.issue.body == "fresh fallback body"
    assert fallback.issue.labels == fallback_labels
    assert not [
        call for call in client.calls if call["method"] in ("comment_issue", "edit_issue_labels")
    ]


def test_sibling_issues_use_independent_selection_caches() -> None:
    """同一批次的直发决定不能污染另一个普通 Issue。"""
    config = config_with_review_disabled()
    client = _client(number=7)
    client.set_issue_labels(8, ("agent/ready",))
    client.set_issue_body(8, "sibling")
    first = PublishStageSelection()
    second = PublishStageSelection()
    for number, selection in ((7, first), (8, second)):
        selection.decision = resolve_claim_publish_stage(
            PublishStageSelectionRequest(
                PublishStage.NORMAL, _issue(number), config, client, selection
            )
        )
    assert first.decision.publish_stage is PublishStage.DIRECT
    assert second.decision.publish_stage is PublishStage.NORMAL
    client.set_issue_labels(7, ("agent/running",))
    client.set_issue_labels(8, ("agent/running", "direct-pr"))
    assert (
        resolve_claim_publish_stage(
            PublishStageSelectionRequest(PublishStage.NORMAL, _issue(8), config, client, second)
        ).publish_stage
        is PublishStage.NORMAL
    )


def _forbidden_builder(**_kwargs):
    """准入失败或落败时调用 builder 就使测试失败。"""
    pytest.fail("builder must not run")


def test_fresh_dependency_stops_ready_handler_before_publication(monkeypatch) -> None:
    """队列无依赖但认领后正文新增未完成依赖，不得建立直发检查点或消费标签。"""
    client = _client(body="<!-- iar:depends-on #42 -->")
    client.set_issue_labels(42, ("agent/ready",))
    _patch_ready_handler(monkeypatch, run_agent=_forbidden_builder)
    monkeypatch.setattr(
        handlers, "arbitrate_first_claim", lambda **_kwargs: SimpleNamespace(comment_id=91)
    )
    with pytest.raises(DirectPrNotEligibleError, match="unresolved dependencies"):
        handlers._process_ready_issue(
            issue=_issue(body="snapshot without dependency"),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
        )
    assert "direct-pr" in client.get_issue(7).labels
    assert not [
        call
        for call in client.calls
        if call["method"]
        in ("comment_issue", "edit_issue_comment", "create_draft_pr", "edit_issue_labels")
    ]


def test_claim_loser_cannot_write_a_round_checkpoint(monkeypatch) -> None:
    """认领落败不能写持久选择，也不能继续读取准入或创建 PR。"""
    client = _client()
    _patch_ready_handler(monkeypatch, run_agent=_forbidden_builder)

    def lose(**_kwargs):
        raise ClaimArbitrationLost("earlier bidder won", winner=None)

    monkeypatch.setattr(handlers, "arbitrate_first_claim", lose)
    with pytest.raises(ClaimArbitrationLost):
        handlers._process_ready_issue(
            issue=_issue(),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
        )
    assert client.calls == []

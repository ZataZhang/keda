"""Issue 直发标签（``direct-pr``）的跨机器发布协议测试（PRD P1-FEAT-20261007-184115）。

覆盖四件事：认领后 fresh 读取解析出的档位表、直发准入在任何 builder / PR 副作用之前
拒绝、标签由认领赢家在**确认同次 PR** 之后消费（失败时保留、下一轮只补清理），以及
``direct-pr`` 作为**非 workflow 状态标签**在改名、同步、配置加载上的完整性。
"""

from __future__ import annotations

import argparse
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError
from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    IssueSummary,
    LabelConfig,
    PullRequestContext,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases import agent_runner_issue_handlers as handlers
from backend.core.use_cases import agent_runner_publication as publication
from backend.core.use_cases import run_agent_once
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimArbitrationLost
from backend.core.use_cases.agent_runner_dependencies import format_direct_pr_marker
from backend.core.use_cases.agent_runner_direct_pr_label import (
    ORIGIN_ISSUE_LABEL,
    ORIGIN_RUN_FLAG,
    ORIGIN_RUN_FLAG_AND_LABEL,
    DirectPrFreshReadError,
    DirectPrLabelCleanupPendingError,
    DirectPrLabelPublicationRequest,
    DirectPrNotEligibleError,
    DirectPrStageConflictError,
    apply_direct_pr_label_policy,
    consume_label_after_publication,
    finish_label_cleanup_if_already_published,
    resolve_publish_stage,
)
from backend.core.use_cases.agent_runner_direct_pr_round import (
    DirectPrPublicationCandidate,
    DirectPrRoundError,
    prepare_direct_pr_publication,
    save_direct_pr_selection,
)
from backend.core.use_cases.agent_runner_workflow import (
    build_transition_labels,
    workflow_state_labels,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import config_with_review_disabled

_ISSUE_NUMBER = 7
_BRANCH = f"issue-{_ISSUE_NUMBER}"
_HEAD = "head-sha"
_PR_URL = "https://github.com/example/repo/pull/900"
_PRD_ANCHORED_BODY = "# Example\n\n- PRD path: `tasks/pending/P1-FEAT-20260101-000000-x.md`\n"


def _issue(
    number: int = _ISSUE_NUMBER,
    *,
    labels: tuple[str, ...] = ("agent/ready",),
    body: str = "Example body",
) -> IssueSummary:
    """队列快照里的 Issue——它的标签代表**可能过期**的视图，不参与放行判定。"""
    return IssueSummary(
        number=number,
        title=f"Issue #{number}",
        url=f"https://github.com/example/repo/issues/{number}",
        body=body,
        labels=labels,
    )


def _client(
    *,
    labels: tuple[str, ...] = ("agent/ready", "direct-pr"),
    body: str = "Example body",
    number: int = _ISSUE_NUMBER,
) -> FakeGitHubClient:
    """fresh 读取返回指定标签与正文的 GitHub 客户端。"""
    client = FakeGitHubClient()
    client.set_issue_labels(number, labels)
    client.set_issue_body(number, body)
    return client


def _label_writes(client: FakeGitHubClient) -> list[dict]:
    return [call for call in client.calls if call["method"] == "edit_issue_labels"]


def _pr_context(
    *, issue_number: int = _ISSUE_NUMBER, head_sha: str = _HEAD, body: str | None = None
) -> PullRequestContext:
    """构造分支上的 open Draft PR 上下文（默认与本轮发布同次）。"""
    return PullRequestContext(
        pr_url=_PR_URL,
        branch=_BRANCH,
        head_sha=head_sha,
        base_sha="base-sha",
        number=900,
        body=body if body is not None else f"PR body\n\n{format_direct_pr_marker(issue_number)}\n",
    )


def _prepare_round(client: FakeGitHubClient) -> None:
    """在 PR 出现前保存本轮候选及无历史 PR 的事实。"""
    fresh_issue = client.get_issue(_ISSUE_NUMBER)
    save_direct_pr_selection(client, fresh_issue, "direct-pr", claim_comment_id=501)
    prepare_direct_pr_publication(
        client,
        fresh_issue,
        DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
    )


def _publish_round(client: FakeGitHubClient) -> None:
    """保存候选后提交 PR，不能仅靠一个任意历史 marker 布置正例。"""
    _prepare_round(client)
    client.set_pr_context(_BRANCH, _pr_context())
    client._open_prs[_BRANCH] = _PR_URL


# ---------------------------------------------------------------------------
# 解析表：档位由认领后的 fresh 读取确立
# ---------------------------------------------------------------------------


def test_label_upgrades_a_normal_claim_to_direct() -> None:
    """守护进程 / 批量（NORMAL）命中直发标签 → DIRECT，并记下待消费的标签名。"""
    decision = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=_issue(labels=("agent/ready",)),
        config=AppConfig(),
        github_client=_client(),
    )

    assert decision.publish_stage is PublishStage.DIRECT
    assert decision.origin == ORIGIN_ISSUE_LABEL
    assert decision.direct_pr_label == "direct-pr"


def test_label_under_the_direct_flag_records_both_origins() -> None:
    """旗标本已直发、标签同时命中 → 仍是 DIRECT，来源写明两者都成立。"""
    decision = resolve_publish_stage(
        requested_stage=PublishStage.DIRECT,
        issue=_issue(labels=("agent/ready", "direct-pr")),
        config=AppConfig(),
        github_client=_client(),
    )

    assert decision.publish_stage is PublishStage.DIRECT
    assert decision.origin == ORIGIN_RUN_FLAG_AND_LABEL
    assert decision.direct_pr_label == "direct-pr"


def test_flag_only_direct_request_leaves_no_label_to_consume() -> None:
    """负例：纯旗标直发（Issue 上没有标签）不产生任何标签写副作用。"""
    client = _client(labels=("agent/ready",))

    decision = resolve_publish_stage(
        requested_stage=PublishStage.DIRECT,
        issue=_issue(labels=("agent/ready",)),
        config=AppConfig(),
        github_client=client,
    )

    assert decision.publish_stage is PublishStage.DIRECT
    assert decision.origin == ORIGIN_RUN_FLAG
    assert decision.direct_pr_label is None
    assert _label_writes(client) == []


def test_fast_request_with_the_label_is_a_rejected_conflict() -> None:
    """FAST 与直发标签同时成立 → 明确拒绝冲突，不静默取更强的旁路。"""
    client = _client()

    with pytest.raises(DirectPrStageConflictError) as error:
        resolve_publish_stage(
            requested_stage=PublishStage.FAST,
            issue=_issue(labels=("agent/ready",)),
            config=AppConfig(),
            github_client=client,
        )

    assert "direct-pr" in str(error.value)
    assert _label_writes(client) == []


def test_stage_comes_from_the_fresh_read_not_the_snapshot() -> None:
    """档位只认认领后的 fresh 读取：旧快照既不能放行，也不能扣住直发。"""
    labelled_after_claim = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=_issue(labels=("agent/ready",)),
        config=AppConfig(),
        github_client=_client(labels=("agent/ready", "direct-pr")),
    )
    assert labelled_after_claim.publish_stage is PublishStage.DIRECT

    revoked_after_snapshot = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=_issue(labels=("agent/ready", "direct-pr")),
        config=AppConfig(),
        github_client=_client(labels=("agent/ready",)),
    )
    assert revoked_after_snapshot.publish_stage is PublishStage.NORMAL
    assert revoked_after_snapshot.direct_pr_label is None


def test_label_removal_is_the_only_variable_in_direct_negative_control() -> None:
    """同一队列输入仅去掉 fresh API 标签，DIRECT 判据必须由绿转红。"""
    snapshot = _issue(labels=("agent/ready",))
    observed = []
    for labels in (("agent/ready", "direct-pr"), ("agent/ready",)):
        observed.append(
            resolve_publish_stage(
                requested_stage=PublishStage.NORMAL,
                issue=snapshot,
                config=AppConfig(),
                github_client=_client(labels=labels),
            ).publish_stage
        )
    assert observed == [PublishStage.DIRECT, PublishStage.NORMAL]


def test_batch_claims_resolve_their_own_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一轮批量逐 Issue 独立：带标签的直发，兄弟 Issue 仍走完整门禁。"""
    client = FakeGitHubClient()
    client.set_issue_labels(11, ("agent/ready", "direct-pr"))
    client.set_issue_labels(12, ("agent/ready",))
    dispatched: list[dict[str, Any]] = []

    def _fake_finish(**kwargs: Any) -> None:
        dispatched.append(
            {
                "number": kwargs["issue"].number,
                "stage": kwargs["publish_stage"],
                "label": kwargs["direct_pr_label"],
            }
        )

    _patch_ready_handler(
        monkeypatch,
        run_agent=lambda **kwargs: AgentCommitResult(verification_results=[], attempt_results=[]),
    )
    monkeypatch.setattr(handlers, "_finish_implementation_publication", _fake_finish)

    for number in (11, 12):
        handlers._process_ready_issue(
            issue=_issue(number, body="Example body"),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
            publish_stage=PublishStage.NORMAL,
        )

    assert dispatched == [
        {"number": 11, "stage": PublishStage.DIRECT, "label": "direct-pr"},
        {"number": 12, "stage": PublishStage.NORMAL, "label": None},
    ]


# ---------------------------------------------------------------------------
# fresh 读取失败：队列快照不能证明当前未标记，必须 fail-closed
# ---------------------------------------------------------------------------


def test_unlabelled_normal_snapshot_fails_closed_on_read_failure() -> None:
    """旧快照未标记不能证明当前未标记；fresh 读取失败不得执行。"""
    client = FakeGitHubClient()
    client.set_get_issue_error(RuntimeError("gh: network down"))

    with pytest.raises(DirectPrFreshReadError):
        resolve_publish_stage(
            requested_stage=PublishStage.NORMAL,
            issue=_issue(labels=("agent/ready",)),
            config=AppConfig(),
            github_client=client,
        )
    assert _label_writes(client) == []


def test_bypass_decision_fails_closed_when_the_issue_cannot_be_read() -> None:
    """直发判定依赖的那次读取失败 → fail-closed 不执行，且不写任何标签。"""
    client = FakeGitHubClient()
    client.set_get_issue_error(RuntimeError("gh: network down"))

    with pytest.raises(DirectPrFreshReadError) as error:
        resolve_publish_stage(
            requested_stage=PublishStage.NORMAL,
            issue=_issue(labels=("agent/ready", "direct-pr")),
            config=AppConfig(),
            github_client=client,
        )

    assert "fail-closed" in str(error.value)
    assert _label_writes(client) == []


# ---------------------------------------------------------------------------
# 准入：标签不扩大旁路范围
# ---------------------------------------------------------------------------


def test_prd_anchored_issue_is_refused_before_the_agent_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """带 PRD 锚点的 Issue 命中直发标签也拒绝：不跑 agent、不建 PR、不消费标签。"""
    client = _client(body=_PRD_ANCHORED_BODY)
    agent_calls: list[str] = []
    _patch_ready_handler(
        monkeypatch,
        run_agent=lambda **kwargs: (
            agent_calls.append("agent")
            or AgentCommitResult(verification_results=[], attempt_results=[])
        ),
    )
    finished: list[str] = []
    monkeypatch.setattr(
        handlers, "_finish_implementation_publication", lambda **kwargs: finished.append("pr")
    )

    with pytest.raises(DirectPrNotEligibleError) as error:
        handlers._process_ready_issue(
            issue=_issue(labels=("agent/ready", "direct-pr"), body=_PRD_ANCHORED_BODY),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
            publish_stage=PublishStage.NORMAL,
        )

    assert "PRD-backed" in str(error.value)
    assert agent_calls == []
    assert finished == []
    assert not [write for write in _label_writes(client) if "direct-pr" in write["remove"]]


def test_unreadable_body_is_not_treated_as_admissible() -> None:
    """正文读不到就无法证明它无 PRD 锚点：直发一律拒绝（fail-closed）。"""
    client = FakeGitHubClient()
    client.set_get_issue_error(RuntimeError("gh: 502"))

    with pytest.raises(DirectPrFreshReadError):
        apply_direct_pr_label_policy(
            requested_stage=PublishStage.DIRECT,
            issue=_issue(labels=("agent/ready",)),
            config=AppConfig(),
            github_client=client,
        )


# ---------------------------------------------------------------------------
# 消费：确认同次 PR 之后，只由认领赢家移除
# ---------------------------------------------------------------------------


def test_consume_removes_only_the_direct_pr_label() -> None:
    """消费只移除直发标签本身，同一 Issue 的其他标签一概不动。"""
    client = _client(labels=("agent/ready", "direct-pr", "type/bug", "agent/codex"))
    _publish_round(client)

    consume_label_after_publication(
        DirectPrLabelPublicationRequest(
            github_client=client,
            issue_number=_ISSUE_NUMBER,
            direct_pr_label="direct-pr",
            candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
            pr_url=_PR_URL,
        ),
    )

    writes = _label_writes(client)
    assert [write["remove"] for write in writes] == [["direct-pr"]]
    assert [write["add"] for write in writes] == [[]]
    assert set(client.get_issue(_ISSUE_NUMBER).labels) == {
        "agent/ready",
        "type/bug",
        "agent/codex",
    }


def test_flag_only_publication_writes_no_labels() -> None:
    """旗标直发（无标签）时发布链不写任何标签，保持旗标路径原有的副作用范围。"""
    client = _client()

    consume_label_after_publication(
        DirectPrLabelPublicationRequest(
            github_client=client,
            issue_number=_ISSUE_NUMBER,
            direct_pr_label=None,
            candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
            pr_url=_PR_URL,
        ),
    )

    assert _label_writes(client) == []
    assert "direct-pr" in client.get_issue(_ISSUE_NUMBER).labels


def test_unconfirmed_publication_refuses_full_success() -> None:
    """分支上没有可确认的 PR → 保留标签并显式声明这不算完整成功。"""
    client = _client()

    with pytest.raises(DirectPrLabelCleanupPendingError) as error:
        consume_label_after_publication(
            DirectPrLabelPublicationRequest(
                github_client=client,
                issue_number=_ISSUE_NUMBER,
                direct_pr_label="direct-pr",
                candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
                pr_url=_PR_URL,
            ),
        )

    assert _PR_URL in str(error.value)
    assert "not a full success" in str(error.value)
    assert _label_writes(client) == []


def test_removal_failure_reports_pending_cleanup_with_the_pr_url() -> None:
    """PR 已发布、移除失败 → 报告「发布成功、清理待恢复」并带上 PR URL 与下一轮去向。"""
    client = _client()
    _publish_round(client)
    client.edit_issue_labels = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("gh: rate limited")
    )

    with pytest.raises(DirectPrLabelCleanupPendingError) as error:
        consume_label_after_publication(
            DirectPrLabelPublicationRequest(
                github_client=client,
                issue_number=_ISSUE_NUMBER,
                direct_pr_label="direct-pr",
                candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
                pr_url=_PR_URL,
            ),
        )

    assert _PR_URL in str(error.value)
    assert "next claim" in str(error.value)
    assert "rate limited" in str(error.value)
    assert "direct-pr" in client.get_issue(_ISSUE_NUMBER).labels


def test_successful_delete_response_without_persisted_change_stays_pending() -> None:
    """删除返回成功但标签仍存在时，fresh 回读不得宣称完整成功。"""
    client = _client()
    _publish_round(client)
    client.edit_issue_labels = lambda *args, **kwargs: None
    with pytest.raises(DirectPrLabelCleanupPendingError, match="not visible"):
        consume_label_after_publication(
            DirectPrLabelPublicationRequest(
                github_client=client,
                issue_number=_ISSUE_NUMBER,
                direct_pr_label="direct-pr",
                candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
                pr_url=_PR_URL,
            )
        )
    assert "direct-pr" in client.get_issue(_ISSUE_NUMBER).labels


def test_next_claim_finishes_only_the_cleanup() -> None:
    """崩溃或删除失败后的下一轮：确认同次 PR 后只删标签，不重建、不再建 PR。"""
    client = _client()
    _publish_round(client)

    pr_url = finish_label_cleanup_if_already_published(
        DirectPrLabelPublicationRequest(
            github_client=client,
            issue_number=_ISSUE_NUMBER,
            direct_pr_label="direct-pr",
            candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
        ),
    )

    assert pr_url == _PR_URL
    assert "direct-pr" not in client.get_issue(_ISSUE_NUMBER).labels


def test_cleanup_probe_is_a_noop_when_nothing_was_published() -> None:
    """负控：没有已发布 PR 时补清理返回 None，调用方继续正常执行链。"""
    client = _client()

    assert (
        finish_label_cleanup_if_already_published(
            DirectPrLabelPublicationRequest(
                github_client=client,
                issue_number=_ISSUE_NUMBER,
                direct_pr_label="direct-pr",
                candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
            ),
        )
        is None
    )
    assert "direct-pr" in client.get_issue(_ISSUE_NUMBER).labels


@pytest.mark.parametrize(
    "context",
    [
        _pr_context(head_sha="stale-head"),
        _pr_context(issue_number=999),
        _pr_context(body="一个没有档位 marker 的普通 PR 正文"),
        None,
    ],
    ids=["other-round-head", "other-issue-marker", "marker-less-pr", "no-pr"],
)
def test_a_pr_that_is_not_this_publication_never_consumes_the_label(
    context: PullRequestContext | None,
) -> None:
    """历史 / 错号 / 无 marker 的 PR 都不构成「同次发布」证据，标签一律保留。"""
    client = _client()
    _prepare_round(client)
    client.set_pr_context(_BRANCH, context)
    if context is not None:
        client._open_prs[_BRANCH] = context.pr_url
        with pytest.raises(DirectPrRoundError):
            finish_label_cleanup_if_already_published(
                DirectPrLabelPublicationRequest(
                    github_client=client,
                    issue_number=_ISSUE_NUMBER,
                    direct_pr_label="direct-pr",
                    candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
                ),
            )
    else:
        assert (
            finish_label_cleanup_if_already_published(
                DirectPrLabelPublicationRequest(
                    github_client=client,
                    issue_number=_ISSUE_NUMBER,
                    direct_pr_label="direct-pr",
                    candidate=DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD),
                ),
            )
            is None
        )
    assert "direct-pr" in client.get_issue(_ISSUE_NUMBER).labels


# ---------------------------------------------------------------------------
# 归属：只有走到发布链的赢家消费
# ---------------------------------------------------------------------------


def test_label_survives_an_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行失败、PR 从未建立 → 标签保留，下一轮仍能按直发重试。"""
    client = _client()

    def _crash(**_kwargs: Any) -> AgentCommitResult:
        raise RuntimeError("agent crashed")

    _patch_ready_handler(monkeypatch, run_agent=_crash)

    with pytest.raises(RuntimeError):
        handlers._process_ready_issue(
            issue=_issue(labels=("agent/ready", "direct-pr")),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
            publish_stage=PublishStage.NORMAL,
        )

    assert "direct-pr" in client.get_issue(_ISSUE_NUMBER).labels
    assert not [write for write in _label_writes(client) if "direct-pr" in write["remove"]]


def test_claim_loser_neither_establishes_a_stage_nor_consumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """认领落败方不做档位判定、也不写任何标签——消费只属于赢家。"""
    client = _client()

    def _lose(**_kwargs: Any) -> None:
        raise ClaimArbitrationLost("claim lost to an earlier bidder", winner=None)

    monkeypatch.setattr(handlers, "arbitrate_first_claim", _lose)

    with pytest.raises(ClaimArbitrationLost):
        handlers._process_ready_issue(
            issue=_issue(labels=("agent/ready", "direct-pr")),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
            publish_stage=PublishStage.NORMAL,
        )

    assert _label_writes(client) == []
    assert [call for call in client.calls if call["method"] == "get_issue"] == []


def test_publication_consumes_the_label_on_both_recovery_paths() -> None:
    """首跑发布与「已有本地 commit」恢复发布都必须消费标签，漏一条就会重复直发。"""
    for factory in (
        publication._finish_implementation_publication,
        publication._finish_existing_commit_publication,
    ):
        source = inspect.getsource(factory)
        assert "consume_label_after_publication(" in source, f"{factory.__name__} 没有消费直发标签"
        assert "direct_pr_label" in source, f"{factory.__name__} 没有接收待消费标签名"


# ---------------------------------------------------------------------------
# 标签面：非 workflow 状态标签、改名、同步、配置
# ---------------------------------------------------------------------------


def test_direct_pr_label_is_not_a_workflow_state() -> None:
    """直发标签不是队列状态：状态切换必须保留它，否则跨机器的选择在认领当场就被吃掉。"""
    config = AppConfig()
    assert config.labels.direct_pr not in workflow_state_labels(config)

    result = build_transition_labels(("agent/ready", "direct-pr"), config, config.labels.running)
    assert "direct-pr" in result
    assert config.labels.running in result
    assert config.labels.ready not in result


def test_renamed_label_still_drives_the_direct_track() -> None:
    """改名不影响语义：判定依据是配置里的标签名，而不是字面量 ``direct-pr``。"""
    config = AppConfig(labels=LabelConfig(direct_pr="ship/now"))

    decision = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=_issue(labels=("agent/ready",)),
        config=config,
        github_client=_client(labels=("agent/ready", "ship/now")),
    )

    assert decision.publish_stage is PublishStage.DIRECT
    assert decision.direct_pr_label == "ship/now"


def test_blank_label_name_disables_the_label_path() -> None:
    """标签名配成空串即关闭这条路径，即便 Issue 上挂着默认名。"""
    decision = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=_issue(labels=("agent/ready", "direct-pr")),
        config=AppConfig(labels=LabelConfig(direct_pr="")),
        github_client=_client(labels=("agent/ready", "direct-pr")),
    )

    assert decision.publish_stage is PublishStage.NORMAL
    assert decision.direct_pr_label is None


def test_labels_sync_provisions_the_direct_pr_label(tmp_path: Path) -> None:
    """``kc labels sync`` 必须创建 direct-pr，否则用户在 GitHub 上无从打这个标签。"""
    from backend.infrastructure.github_client import GitHubCliClient

    runner = FakeProcessRunner()
    GitHubCliClient(tmp_path, runner).sync_labels(LabelConfig())

    created = [
        list(call)[3] for call in runner.calls if list(call)[:3] == ["gh", "label", "create"]
    ]
    assert "direct-pr" in created


def test_labels_sync_honours_a_renamed_direct_pr_label(tmp_path: Path) -> None:
    """改名后同步的是配置名本身，不会留下一个无人使用的默认标签。"""
    from backend.infrastructure.github_client import GitHubCliClient

    runner = FakeProcessRunner()
    GitHubCliClient(tmp_path, runner).sync_labels(LabelConfig(direct_pr="ship/now"))

    created = [
        list(call)[3] for call in runner.calls if list(call)[:3] == ["gh", "label", "create"]
    ]
    assert "ship/now" in created
    assert "direct-pr" not in created


@pytest.mark.parametrize("configured_label", ["", "   ", "  ship/now  "])
def test_labels_sync_matches_the_effective_direct_label(
    tmp_path: Path, configured_label: str
) -> None:
    """同步与档位判定使用相同有效名，禁用时不向 GitHub 创建空标签。"""
    from backend.infrastructure.github_client import GitHubCliClient

    process_runner = FakeProcessRunner()
    GitHubCliClient(tmp_path, process_runner).sync_labels(LabelConfig(direct_pr=configured_label))
    created_labels = [
        list(command)[3]
        for command in process_runner.calls
        if list(command)[:3] == ["gh", "label", "create"]
    ]
    assert all(label.strip() for label in created_labels)
    assert "direct-pr" not in created_labels
    assert ("ship/now" in created_labels) == bool(configured_label.strip())


def test_label_name_survives_the_config_surface() -> None:
    """配置面闭环：settings 默认值 → LabelConfig → 仓库级覆盖，缺一段标签名就会漂移。"""
    from backend.engines.agent_runner.factory_config_builder import (
        build_label_config_from_settings,
    )
    from backend.engines.agent_runner.factory_config_merge import _merge_label_config
    from backend.infrastructure.config.agent_runner_settings import AgentRunnerLabelSettings

    assert AgentRunnerLabelSettings().direct_pr == "direct-pr"
    built = build_label_config_from_settings(AgentRunnerLabelSettings(), {})
    assert built.direct_pr == "direct-pr"
    merged = _merge_label_config(
        LabelConfig(),
        AgentRunnerLabelSettings(direct_pr="ship/now"),
        agent_registry={},
    )
    assert merged.direct_pr == "ship/now"


# ---------------------------------------------------------------------------
# api 表面：--fast-merge 与直发标签的前置冲突拒绝
# ---------------------------------------------------------------------------


def _gate_client(labels: tuple[str, ...]) -> FakeGitHubClient:
    client = FakeGitHubClient()
    client.set_issue_labels(_ISSUE_NUMBER, labels)
    return client


def _reject_kwargs(client: FakeGitHubClient, tmp_path: Path) -> dict[str, Any]:
    from backend.api.cli_output import OUTPUT_FORMAT_TABLE
    from backend.api.cli_parsed_commands.runner import _reject_fast_merge_on_direct_pr_label
    from backend.api.cli_parsed_context import ParsedCommandContext

    ctx = ParsedCommandContext(
        parsed=argparse.Namespace(command="run"),
        process_runner=None,
        runner_settings=None,
        repo_id=None,
        repo_override=None,
        github_client_factory=lambda repo_path: client,
        output_format=OUTPUT_FORMAT_TABLE,
    )
    return {
        "gate": _reject_fast_merge_on_direct_pr_label,
        "ctx": ctx,
        "contexts": [SimpleNamespace(repo_path=tmp_path, config=AppConfig())],
        "target_issue": _ISSUE_NUMBER,
    }


def test_fast_merge_is_rejected_when_the_issue_is_labelled(tmp_path: Path) -> None:
    """``--fast-merge`` 命中直发标签 → 用法错误，且不写任何标签。"""
    kwargs = _reject_kwargs(_gate_client(("agent/ready", "direct-pr")), tmp_path)

    with pytest.raises(CliError) as error:
        kwargs["gate"](kwargs["ctx"], contexts=kwargs["contexts"], target_issue=7)

    assert error.value.code == ExitCode.USAGE
    assert "direct-pr" in str(error.value)


def test_fast_merge_passes_an_unlabelled_issue(tmp_path: Path) -> None:
    """负控：同一道门禁放过没有直发标签的 Issue，拒绝不是无条件拦截。"""
    kwargs = _reject_kwargs(_gate_client(("agent/ready",)), tmp_path)

    kwargs["gate"](kwargs["ctx"], contexts=kwargs["contexts"], target_issue=7)


def test_fast_merge_gate_is_fail_closed_on_unreadable_issue(tmp_path: Path) -> None:
    """读不到 Issue 就无法证明它没有直发标签 → 一律拒绝放行。"""
    client = FakeGitHubClient()
    client.set_get_issue_error(RuntimeError("gh: 502"))
    kwargs = _reject_kwargs(client, tmp_path)

    with pytest.raises(CliError) as error:
        kwargs["gate"](kwargs["ctx"], contexts=kwargs["contexts"], target_issue=7)

    assert error.value.code == ExitCode.USAGE


def test_run_command_wires_the_fast_merge_label_conflict_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """负控锚点：删掉 ``run_run_command`` 里对门禁的调用，本条必须失败。

    私有函数自己通过测试证明不了命令入口真的挂了这道门禁，因此这里打桩的是「门禁被
    调用」这件事本身，并断言被拒后没有任何执行被派发。
    """
    from backend.api.cli_parsed_commands import runner as runner_module

    dispatched: list = []
    repo_context = SimpleNamespace(repo_path=tmp_path, repo_id="demo", config=AppConfig())
    monkeypatch.setattr(
        runner_module, "_resolve_cli_repository_targets", lambda **kwargs: [repo_context]
    )
    monkeypatch.setattr(
        "backend.api.cli_parsed_commands.runner._cli.require_iar_repository_initialized",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(runner_module, "_ensure_gh_auth_or_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "backend.api.cli_model_preset_anchor.apply_cli_model_preset",
        lambda contexts, parsed, *, anchored_stage: contexts,
    )
    monkeypatch.setattr(
        "backend.api.cli_parsed_commands.runner._cli.run_agent_repositories_once",
        lambda **kwargs: dispatched.append(kwargs) or 0,
    )
    monkeypatch.setattr(
        runner_module, "_reject_fast_merge_on_stack_issue", lambda *args, **kwargs: None
    )

    def _sentinel(*args: Any, **kwargs: Any) -> None:
        raise CliError(f"gate invoked for {kwargs['target_issue']}", code=ExitCode.USAGE)

    monkeypatch.setattr(runner_module, "_reject_fast_merge_on_direct_pr_label", _sentinel)

    with pytest.raises(CliError) as error:
        runner_module.run_run_command(_run_context(issue=7, fast_merge=True))

    assert "gate invoked for 7" in str(error.value)
    assert dispatched == []


def _run_context(**parsed_kwargs: Any) -> Any:
    from backend.api.cli_output import OUTPUT_FORMAT_TABLE
    from backend.api.cli_parsed_context import ParsedCommandContext

    defaults: dict[str, Any] = {
        "command": "run",
        "prd_path": None,
        "dry_run": False,
        "issue": None,
        "all_ready": False,
        "takeover": False,
        "yes": False,
        "fast_merge": False,
        "direct_pr": False,
        "agent": "auto",
        "max_issues": None,
        "config": None,
        "repo": None,
        "repo_id": None,
        "all_repositories": False,
    }
    defaults.update(parsed_kwargs)
    return ParsedCommandContext(
        parsed=argparse.Namespace(**defaults),
        process_runner=None,
        runner_settings=SimpleNamespace(
            runner=SimpleNamespace(max_issues=1),
            console=SimpleNamespace(process_registry_path=Path("/tmp/registry")),
            daemon=SimpleNamespace(max_deliberation_issues=1),
        ),
        repo_id=None,
        repo_override=None,
        github_client_factory=lambda repo_path: FakeGitHubClient(),
        output_format=OUTPUT_FORMAT_TABLE,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _patch_ready_handler(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_agent: Any,
) -> None:
    """把 ``_process_ready_issue`` 的 worktree / git / agent 调用打桩。

    本组测试要证的是「认领后确立档位 → 发布后消费标签」这条跨机器协议，而不是 agent
    执行本身，因此只保留标签读写、认领仲裁与档位解析这些真实环节。
    """
    worktree_path = Path("/tmp/issue-7")
    monkeypatch.setattr(handlers, "create_or_reuse_worktree", lambda *a, **k: worktree_path)
    monkeypatch.setattr(handlers, "get_head_sha", lambda *a, **k: _HEAD)
    monkeypatch.setattr(handlers, "get_current_branch", lambda *a, **k: _BRANCH)
    monkeypatch.setattr(handlers, "_reuse_existing_local_commit", lambda *a, **k: None)
    monkeypatch.setattr(run_agent_once, "run_agent_until_committed", run_agent)

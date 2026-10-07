"""验证直发终态及最终标签写失败后同轮 PR 的交接重试。"""

from types import SimpleNamespace

import pytest

from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    PostPrSupervisorConfig,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases import agent_runner_issue_handlers as handlers
from backend.core.use_cases import agent_runner_publication as publication
from backend.core.use_cases.agent_runner_direct_pr_round import read_direct_pr_round
from tests.test_agent_runner_direct_pr_round import (
    _BRANCH,
    _NUMBER,
    _URL,
    _forbid_builder,
    _published_storage,
    _runner,
)


@pytest.mark.parametrize(
    "case",
    [
        (entry, enabled, fail)
        for entry in ("implementation", "existing")
        for enabled in (False, True)
        for fail in (False, True)
    ],
)
def test_direct_publication_completes_only_after_review_transition(
    tmp_path, monkeypatch, case
) -> None:
    """两条实际收尾链始终进入 review；最终写失败保留检查点并重试同一个 PR。"""
    entry, enabled, fail = case
    client = _published_storage(tmp_path / "github.json")
    config = AppConfig(post_pr_supervisor=PostPrSupervisorConfig(enabled=enabled))
    process_runner = _runner()
    _forbid_builder(monkeypatch)
    monkeypatch.setattr(publication, "_try_distill_skill_after_success", lambda **_kwargs: None)
    monkeypatch.setattr(publication, "_push_changes_with_recovery_context", lambda **_kwargs: None)
    monkeypatch.setattr(
        publication,
        "_review_verify_create_pr",
        lambda _request: SimpleNamespace(branch=_BRANCH, pr_url=_URL),
    )
    monkeypatch.setattr(publication, "_publish_verified_pr", lambda _request: None)
    monkeypatch.setattr(handlers, "_find_worktree_path_for_issue", lambda *_args: tmp_path)
    monkeypatch.setattr(handlers, "_ensure_worktree_branch", lambda *_args: None)
    original_edit = client.edit_issue_labels
    failures = []

    def edit_labels(number, *, add=(), remove=()):
        if fail and "agent/review" in add and not failures:
            failures.append(number)
            raise RuntimeError("final review transition unavailable")
        return original_edit(number, add=add, remove=remove)

    client.edit_issue_labels = edit_labels
    finish = (
        publication._finish_implementation_publication
        if entry == "implementation"
        else publication._finish_existing_commit_publication
    )
    arguments = dict(
        issue=client.get_issue(_NUMBER),
        worktree_path=tmp_path,
        config=config,
        selected_agent="auto",
        github_client=client,
        process_runner=process_runner,
        expected_branch=_BRANCH,
        commit_result=AgentCommitResult([], []),
        publish_stage=PublishStage.DIRECT,
        direct_pr_label="direct-pr",
    )
    if fail:
        with pytest.raises(Exception, match="final review transition unavailable"):
            finish(**arguments)
        pending = read_direct_pr_round(client, client.get_issue(_NUMBER))
        assert pending is not None and not pending.handoff_complete and pending.pr_url == _URL
        assert "agent/running" in client.get_issue(_NUMBER).labels
        assert "direct-pr" not in client.get_issue(_NUMBER).labels
        handlers._process_running_publish_recovery(
            issue=client.get_issue(_NUMBER),
            repo_path=tmp_path,
            config=config,
            agent="auto",
            github_client=client,
            process_runner=process_runner,
            cleanup_only=True,
        )
    else:
        finish(**arguments)
    completed = read_direct_pr_round(client, client.get_issue(_NUMBER))
    assert completed is not None and completed.handoff_complete and completed.pr_url == _URL
    labels = client.get_issue(_NUMBER).labels
    assert "agent/review" in labels and "agent/supervising" not in labels
    assert client.create_count == 0
    assert not any(list(command)[:2] == ["git", "push"] for command in process_runner.calls)
    assert not any(command and command[0] == "just" for command in process_runner.calls)

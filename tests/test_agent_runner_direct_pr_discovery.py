"""验证 run/daemon 共用发现入口可交接已发布直发轮次而不启动新工作。"""

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases import agent_runner_issue_handlers as handlers
from backend.core.use_cases import agent_runner_orchestration_runtime as runtime
from backend.core.use_cases import run_agent_once
from backend.core.use_cases.agent_runner_direct_pr_round import read_direct_pr_round
from tests.test_agent_runner_direct_pr_round import (
    _NUMBER,
    _URL,
    _forbid_builder,
    _published_storage,
    _runner,
)


@pytest.mark.parametrize("workflow", ["agent/ready", "agent/blocked", "agent/running"])
@pytest.mark.parametrize("targeted", [False, True])
def test_actual_dispatch_handoffs_published_round_despite_new_dependency(
    tmp_path, monkeypatch, workflow, targeted
) -> None:
    """真实发现和串行分派绕过新依赖/缺 marker，只交接既有同轮 PR。"""
    client = _published_storage(tmp_path / "github.json", label_present=False)
    client.set_issue_labels(_NUMBER, (workflow, "test/preserve"))
    client.set_issue_body(
        _NUMBER,
        "- PRD path: `tasks/pending/new-requirement.md`\n<!-- iar:depends-on #42 -->",
    )
    client.set_issue_labels(42, ("agent/ready",))
    client.list_ready_issues = lambda *_args: (
        [client.get_issue(_NUMBER)] if workflow == "agent/ready" else []
    )
    client.list_review_candidate_issues = lambda labels, _limit: (
        [client.get_issue(_NUMBER)] if workflow in labels else []
    )
    _forbid_builder(monkeypatch)
    monkeypatch.setattr(run_agent_once, "run_preflight_checks", lambda *_args: None)
    monkeypatch.setattr(runtime, "process_validation_gate", lambda **_kwargs: None)
    monkeypatch.setattr(handlers, "_find_worktree_path_for_issue", lambda *_args: tmp_path)
    monkeypatch.setattr(handlers, "_ensure_worktree_branch", lambda *_args: None)
    process_runner = _runner()

    result = runtime.run_once(
        runtime.RunOnceRequest(
            repo_path=tmp_path,
            config=AppConfig(),
            dry_run=False,
            agent="auto",
            max_issues=1,
            github_client=client,
            process_runner=process_runner,
            target_issue=_NUMBER if targeted else None,
        )
    )

    assert result == 0
    record = read_direct_pr_round(client, client.get_issue(_NUMBER))
    assert record is not None and record.handoff_complete and record.pr_url == _URL
    assert "agent/supervising" in client.get_issue(_NUMBER).labels
    assert client.create_count == 0
    assert not any(list(command)[:2] == ["git", "push"] for command in process_runner.calls)
    assert not any(command and command[0] == "just" for command in process_runner.calls)


@pytest.mark.parametrize("workflow", ["agent/ready", "agent/blocked"])
def test_unassociated_issue_does_not_gain_cleanup_discovery(
    tmp_path, monkeypatch, workflow
) -> None:
    """历史 PR 或无检查点无法旁路依赖/缺 blocked marker 的发现限制。"""
    client = _published_storage(tmp_path / "github.json")
    client._issue_comment_entries.clear()
    client.set_issue_labels(_NUMBER, (workflow, "direct-pr"))
    client.set_issue_body(_NUMBER, "<!-- iar:depends-on #42 -->")
    client.set_issue_labels(42, ("agent/ready",))
    client.list_ready_issues = lambda *_args: (
        [client.get_issue(_NUMBER)] if workflow == "agent/ready" else []
    )
    client.list_review_candidate_issues = lambda labels, _limit: (
        [client.get_issue(_NUMBER)] if workflow in labels else []
    )
    dispatched = []
    monkeypatch.setattr(
        runtime, "_process_single_issue", lambda *args, **kwargs: dispatched.append(args)
    )
    monkeypatch.setattr(run_agent_once, "run_preflight_checks", lambda *_args: None)
    monkeypatch.setattr(runtime, "process_validation_gate", lambda **_kwargs: None)

    assert (
        runtime.run_once(
            runtime.RunOnceRequest(tmp_path, AppConfig(), False, "auto", 1, client, _runner())
        )
        == 0
    )
    assert dispatched == []
    assert "direct-pr" in client.get_issue(_NUMBER).labels
    assert client.create_count == 0


@pytest.mark.parametrize("fault", ["completed", "removed", "mismatched"])
def test_cleanup_discovery_cannot_turn_into_new_publication(tmp_path, monkeypatch, fault) -> None:
    """发现后关联变化，持锁恢复必须停止，不能重启发布或审查门禁。"""
    from backend.core.use_cases.agent_runner_direct_pr_round import complete_direct_pr_round

    client = _published_storage(tmp_path / "github.json", label_present=False)
    client.set_issue_labels(_NUMBER, ("agent/ready",))
    client.set_issue_body(_NUMBER, "<!-- iar:depends-on #42 -->")
    client.list_ready_issues = lambda *_args: [client.get_issue(_NUMBER)]
    client.list_review_candidate_issues = lambda *_args: []
    original = runtime._has_published_direct_pr_handoff

    def race(github_client, issue):
        proven = original(github_client, issue)
        if fault == "completed":
            complete_direct_pr_round(client, issue)
        elif fault == "removed":
            client._issue_comment_entries.clear()
        else:
            client._pr_contexts.clear()
            client._open_prs.clear()
        return proven

    monkeypatch.setattr(runtime, "_has_published_direct_pr_handoff", race)
    _forbid_builder(monkeypatch)
    monkeypatch.setattr(run_agent_once, "run_preflight_checks", lambda *_args: None)
    monkeypatch.setattr(runtime, "process_validation_gate", lambda **_kwargs: None)
    monkeypatch.setattr(handlers, "_find_worktree_path_for_issue", lambda *_args: tmp_path)
    monkeypatch.setattr(handlers, "_ensure_worktree_branch", lambda *_args: None)
    process_runner = _runner()
    assert (
        runtime.run_once(
            runtime.RunOnceRequest(tmp_path, AppConfig(), False, "auto", 1, client, process_runner)
        )
        == 1
    )
    assert client.create_count == 0
    assert not any(list(command)[:2] == ["git", "push"] for command in process_runner.calls)
    assert not any(command and command[0] == "just" for command in process_runner.calls)
    assert "agent/supervising" not in client.get_issue(_NUMBER).labels

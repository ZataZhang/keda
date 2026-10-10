"""验证真实 PR adapter 的权威读取不能把故障当成历史 PR 不存在。"""

import json

import pytest

from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.agent_runner_dependencies import format_direct_pr_marker
from backend.core.use_cases.agent_runner_direct_pr_round import (
    DirectPrPublicationCandidate,
    DirectPrRoundError,
    associated_direct_pr,
    prepare_direct_pr_publication,
    read_direct_pr_round,
    save_direct_pr_selection,
)
from backend.infrastructure.github_client import GitHubCliClient
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.test_agent_runner_direct_pr_label import _issue

_CANDIDATE = DirectPrPublicationCandidate("issue-7", "a" * 40)
_URL = "https://github.com/example/repo/pull/91"
_ROW = dict(
    url=_URL,
    number=91,
    body=format_direct_pr_marker(7),
    headRefName="issue-7",
    headRefOid="a" * 40,
    baseRefOid="b" * 40,
    isDraft=False,
)


def _client(monkeypatch, tmp_path, reply):
    """PR 查询走真实 adapter，评论 checkpoint 使用可检查的既有测试端口。"""
    client = GitHubCliClient(tmp_path, FakeProcessRunner())
    comments = FakeGitHubClient()
    responses = [reply]
    queries = []

    def query(command, *, cwd, check=True):
        queries.append(tuple(command))
        return_code, stdout = responses[0]
        return CommandResult(tuple(command), return_code, stdout, "query unavailable")

    monkeypatch.setattr(client, "_run_with_retry", query)
    for method in ("list_issue_comment_entries", "comment_issue", "edit_issue_comment"):
        monkeypatch.setattr(client, method, getattr(comments, method))
    save_direct_pr_selection(client, _issue(), "direct-pr", claim_comment_id=91)
    return client, comments, responses, queries


@pytest.mark.parametrize(
    "reply",
    [
        (1, ""),
        (0, ""),
        (0, "   "),
        (0, "broken"),
        (0, "null"),
        (0, "{}"),
        (0, '["wrong row"]'),
        (0, "[{}]"),
        (0, json.dumps([{**_ROW, "headRefOid": ""}])),
        (0, json.dumps([{**_ROW, "headRefName": "other"}])),
        (0, json.dumps([{**_ROW, "number": True}])),
        (0, json.dumps([_ROW, _ROW])),
    ],
)
def test_failed_authoritative_query_never_persists_prior_absence(monkeypatch, tmp_path, reply):
    """失败响应不授权创建；查询恢复后同 head 历史 PR 仍被拒绝。"""
    client, comments, responses, _queries = _client(monkeypatch, tmp_path, reply)
    with pytest.raises(DirectPrRoundError, match="Cannot establish absence"):
        prepare_direct_pr_publication(client, _issue(), _CANDIDATE)
    pending = read_direct_pr_round(client, _issue())
    assert pending is not None and not pending.prior_pr_absent and pending.branch == ""
    assert not any(call["method"] == "edit_issue_comment" for call in comments.calls)
    responses[0] = (0, json.dumps([_ROW]))
    with pytest.raises(DirectPrRoundError, match="older PR"):
        prepare_direct_pr_publication(client, _issue(), _CANDIDATE)
    assert not any(call["method"] == "create_draft_pr" for call in comments.calls)


def test_successful_empty_list_proves_absence_and_same_round_pr_is_bound(monkeypatch, tmp_path):
    """只有成功 [] 可以保存候选；后续同轮 PR 的完整身份仍须匹配。"""
    client, _comments, responses, _queries = _client(monkeypatch, tmp_path, (0, "[]"))
    assert prepare_direct_pr_publication(client, _issue(), _CANDIDATE) is None
    record = read_direct_pr_round(client, _issue())
    assert record is not None and record.prior_pr_absent and record.branch == "issue-7"
    responses[0] = (0, json.dumps([_ROW]))
    assert associated_direct_pr(client, record, _CANDIDATE) == _URL


def test_association_query_failure_refuses_cleanup_authorization(monkeypatch, tmp_path):
    """候选已有检查点，后续权威查询失败仍不得宣称 PR 已确认或消费标签。"""
    client, comments, responses, _queries = _client(monkeypatch, tmp_path, (0, "[]"))
    prepare_direct_pr_publication(client, _issue(), _CANDIDATE)
    record = read_direct_pr_round(client, _issue())
    responses[0] = (1, "")
    with pytest.raises(DirectPrRoundError, match="Cannot confirm Direct PR publication"):
        associated_direct_pr(client, record, _CANDIDATE)
    pending = read_direct_pr_round(client, _issue())
    assert pending is not None and pending.pr_url is None and not pending.handoff_complete
    assert not any(call["method"] == "edit_issue_labels" for call in comments.calls)


def test_default_pr_query_failure_preserves_legacy_none(monkeypatch, tmp_path):
    """权威读取为 opt-in，普通 reviewer 的缺上下文语义保持不变。"""
    client, _comments, _responses, _queries = _client(monkeypatch, tmp_path, (1, ""))
    assert client.get_pull_request_context("issue-7") is None

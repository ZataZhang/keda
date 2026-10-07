"""真实 GitHub adapter 的检查点作者授权及读取失败边界。"""

import json
from types import SimpleNamespace
from dataclasses import asdict

import pytest

from backend.core.shared.models.agent_runner import AppConfig, CommandResult
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_runner_direct_pr_label import resolve_publish_stage
from backend.core.use_cases.agent_runner_direct_pr_round import (
    DirectPrRound,
    DirectPrRoundError,
    read_direct_pr_round,
)
from backend.infrastructure.github_client import GitHubCliClient
from tests.conftest import FakeProcessRunner
from tests.test_agent_runner_direct_pr_label import _issue, _patch_ready_handler
from tests.support.agent_runner import config_with_review_disabled
from backend.core.use_cases import agent_runner_issue_handlers as handlers


def _comment(author="other", *, own=False, body="comment"):
    """构造 gh 原生评论元数据，不把组织 MEMBER 当授权。"""
    return dict(
        url="https://github.com/example/repo/issues/7#issuecomment-11",
        body=body,
        viewerDidAuthor=own,
        author={"login": author},
        authorAssociation="MEMBER",
    )


def _client(monkeypatch, tmp_path, comments, permissions):
    """驱动真实 adapter 委托，捕获本轮评论及权限查询。"""
    client = GitHubCliClient(tmp_path, FakeProcessRunner())
    calls = []

    def query(command, *, cwd, check=True):
        calls.append(tuple(command))
        payload = comments if command[1] == "issue" else permissions
        if isinstance(payload, CommandResult):
            return payload
        return CommandResult(
            command=tuple(command), return_code=0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr(client, "_run_with_retry", query)
    monkeypatch.setattr(client, "_get_owner_repo", lambda: "example/repo")
    return client, calls


@pytest.mark.parametrize("permission", ["triage", "write", "maintain", "admin"])
def test_other_author_current_repository_role_is_trusted(monkeypatch, tmp_path, permission):
    """不同机器/账号的授权认领者可恢复；triage 的 scalar read 仍由 role_name 识别。"""
    client, calls = _client(
        monkeypatch,
        tmp_path,
        {"comments": [_comment(), _comment()]},
        {"permission": "read" if permission == "triage" else permission, "role_name": permission},
    )
    assert client.list_issue_comment_entries(7, trusted_only=True) == [
        (11, "comment"),
        (11, "comment"),
    ]
    assert len([call for call in calls if call[1] == "api"]) == 1
    client.list_issue_comment_entries(7, trusted_only=True)
    assert len([call for call in calls if call[1] == "api"]) == 2
    permissions = {"permission": "read", "role_name": "read"}
    monkeypatch.setattr(
        client,
        "_run_with_retry",
        lambda command, **_kwargs: CommandResult(
            command=tuple(command),
            return_code=0,
            stdout=json.dumps({"comments": [_comment()]} if command[1] == "issue" else permissions),
            stderr="",
        ),
    )
    assert client.list_issue_comment_entries(7, trusted_only=True) == []


def test_self_author_does_not_need_permission_lookup(monkeypatch, tmp_path):
    """当前登录账号自己的检查点可直接回读确认。"""
    client, calls = _client(monkeypatch, tmp_path, {"comments": [_comment(own=True)]}, {})
    assert client.list_issue_comment_entries(7, trusted_only=True) == [(11, "comment")]
    assert not any(call[1] == "api" for call in calls)


@pytest.mark.parametrize("permission", ["read", "none", "write"])
def test_started_checkpoint_trust_controls_actual_builder_stage(monkeypatch, tmp_path, permission):
    """相同已开始检查点仅在作者有 write 权限时恢复 DIRECT，公开只读作者走 NORMAL。"""
    record = DirectPrRound(
        11,
        "https://github.com/example/repo",
        7,
        "91",
        "direct-pr",
        branch="issue-7",
        head="a" * 40,
        prior_pr_absent=True,
    )
    payload = asdict(record)
    payload.pop("comment_id")
    payload.update(version=1, stage="DIRECT")
    forged = "<!-- iar:direct-pr-round " + json.dumps(payload) + " -->"
    client, _calls = _client(
        monkeypatch,
        tmp_path,
        {"comments": [_comment(body=forged)]},
        {"permission": permission, "role_name": permission},
    )
    monkeypatch.setattr(client, "get_issue", lambda _number: _issue())
    decision = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=_issue(),
        config=AppConfig(),
        github_client=client,
    )
    expected_stage = PublishStage.DIRECT if permission == "write" else PublishStage.NORMAL
    assert decision.publish_stage is expected_stage
    assert decision.direct_pr_label == ("direct-pr" if permission == "write" else None)

    builder_stages = []

    def capture_builder(**arguments):
        builder_stages.append(arguments["publish_stage"])
        raise RuntimeError("builder boundary captured")

    _patch_ready_handler(monkeypatch, run_agent=capture_builder)
    monkeypatch.setattr(handlers, "get_head_sha", lambda *_args: "a" * 40)
    monkeypatch.setattr(handlers, "_reuse_local_commit", lambda _request: None)
    monkeypatch.setattr(client, "get_pull_request_context", lambda _branch: None)
    monkeypatch.setattr(client, "find_open_pr_by_head", lambda _branch: None)
    monkeypatch.setattr(
        handlers, "arbitrate_first_claim", lambda **_kwargs: SimpleNamespace(comment_id=91)
    )
    with pytest.raises(RuntimeError, match="builder boundary captured"):
        handlers._process_ready_issue(
            issue=_issue(),
            repo_path=tmp_path,
            config=config_with_review_disabled(),
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
        )
    assert builder_stages == [expected_stage]


@pytest.mark.parametrize("fault", ["query", "metadata", "permission", "unknown"])
def test_trusted_read_failure_never_becomes_empty_checkpoint(monkeypatch, tmp_path, fault):
    """评论读取/权限错误和未知元数据均保留不可判定状态，而非回退 NORMAL。"""
    error = CommandResult(command=(), return_code=1, stdout="", stderr="unavailable")
    comments = (
        error
        if fault == "query"
        else {"comments": [_comment(body="<!-- iar:direct-pr-round marker")]}
    )
    permissions = (
        error
        if fault == "permission"
        else {"permission": "unknown" if fault == "unknown" else "write"}
    )
    if fault == "metadata":
        comments["comments"][0].pop("viewerDidAuthor")
    client, _calls = _client(monkeypatch, tmp_path, comments, permissions)
    with pytest.raises(DirectPrRoundError, match="Cannot read Direct PR checkpoint"):
        read_direct_pr_round(client, _issue())
    with pytest.raises(DirectPrRoundError):
        resolve_publish_stage(
            requested_stage=PublishStage.NORMAL,
            issue=_issue(),
            config=AppConfig(),
            github_client=client,
        )
    builder_calls = []
    _patch_ready_handler(monkeypatch, run_agent=lambda **_kwargs: builder_calls.append(True))
    monkeypatch.setattr(client, "get_issue", lambda _number: _issue())
    monkeypatch.setattr(
        handlers, "arbitrate_first_claim", lambda **_kwargs: SimpleNamespace(comment_id=91)
    )
    with pytest.raises(DirectPrRoundError):
        handlers._process_ready_issue(
            issue=_issue(),
            repo_path=tmp_path,
            config=config_with_review_disabled(),
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
        )
    assert builder_calls == []


def test_default_legacy_read_failure_still_returns_empty(monkeypatch, tmp_path):
    """可信读取为 opt-in，不改变其他既有评论消费接口的尽力语义。"""
    error = CommandResult(command=(), return_code=1, stdout="", stderr="unavailable")
    client, _calls = _client(monkeypatch, tmp_path, error, {})
    assert client.list_issue_comment_entries(7) == []


def test_checkpoint_filter_does_not_query_unrelated_public_authors(monkeypatch, tmp_path):
    """普通 Issue 评论不触发权限查询；检查点候选才进入可信作者核验。"""
    client, calls = _client(monkeypatch, tmp_path, {"comments": [_comment()]}, {})
    assert read_direct_pr_round(client, _issue()) is None
    assert len(calls) == 1 and calls[0][1] == "issue"


@pytest.mark.parametrize("flag", ["triage", "push", "maintain", "admin"])
def test_explicit_repository_permission_flags_authorize_other_machine(monkeypatch, tmp_path, flag):
    """角色标量为 read 时仍读取真实权限布尔位，不根据关联身份猜测。"""
    client, _calls = _client(
        monkeypatch,
        tmp_path,
        {"comments": [_comment()]},
        {"permission": "read", "user": {"permissions": {flag: True, "pull": True}}},
    )
    assert client.list_issue_comment_entries(7, trusted_only=True) == [(11, "comment")]


@pytest.mark.parametrize("comments", [{}, {"comments": None}, {"comments": [{"body": None}]}])
def test_malformed_comment_query_is_not_an_absent_checkpoint(monkeypatch, tmp_path, comments):
    """未知响应结构和缺失正文不得冒充成功读取的空评论列表。"""
    client, _calls = _client(monkeypatch, tmp_path, comments, {})
    with pytest.raises(DirectPrRoundError):
        read_direct_pr_round(client, _issue())


def test_raw_comments_reuse_entry_query_and_preserve_empty_body_semantics(monkeypatch, tmp_path):
    """原正文接口复用条目查询，空值仍被过滤且默认不查询作者权限。"""
    client, calls = _client(
        monkeypatch,
        tmp_path,
        {"comments": [{"body": "kept"}, {"body": ""}, {"body": None}, {}]},
        {},
    )
    assert client.list_issue_comments(7) == ["kept"]
    assert len(calls) == 1 and calls[0][1] == "issue"

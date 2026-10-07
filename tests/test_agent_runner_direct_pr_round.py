"""以磁盘上的 GitHub 替身验证直发发布跨客户端的崩溃恢复。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
    PostPrSupervisorConfig,
    PublishRecoveryRequest,
    PullRequestContext,
    ReviewEventMarker,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases import agent_runner_issue_handlers as handlers
from backend.core.use_cases import run_agent_once
from backend.core.use_cases.agent_runner_direct_pr_label import resolve_publish_stage
from backend.core.use_cases.agent_runner_direct_pr_round import (
    DirectPrPublicationCandidate,
    DirectPrRoundError,
    associated_direct_pr,
    complete_direct_pr_round,
    prepare_direct_pr_publication,
    read_direct_pr_round,
    save_direct_pr_selection,
)
from backend.core.use_cases.agent_runner_publish import create_draft_pr
from backend.core.use_cases.recover_publish import recover_publish_issue
from tests.conftest import FakeGitHubClient, FakeProcessRunner

_NUMBER = 17
_BRANCH = "issue-17"
_HEAD = "a" * 40
_URL = "https://github.com/example/repo/pull/91"
_ISSUE = IssueSummary(
    number=_NUMBER,
    title="直发恢复测试",
    url="https://github.com/example/repo/issues/17",
    body="纯文档修改",
    labels=("agent/running", "direct-pr", "test/preserve"),
)
_CANDIDATE = DirectPrPublicationCandidate(branch=_BRANCH, head=_HEAD)


@dataclass
class _Faults:
    """只在明确的外部写入边界注入故障。"""

    lose_create_response: bool = False
    ignore_comment_update: bool = False
    fail_comment_update: bool = False
    fail_label_removal: bool = False
    ignore_label_removal: bool = False


class _DurableGitHub(FakeGitHubClient):
    """将评论、PR 和创建次数写到磁盘，恢复使用全新的客户端。"""

    def __init__(self, storage: Path, faults: _Faults | None = None) -> None:
        super().__init__()
        self.storage = storage
        self.faults = faults or _Faults()
        self.create_count = 0
        if storage.exists():
            snapshot = json.loads(storage.read_text(encoding="utf-8"))
            self._issue_comment_entries = {
                int(number): [(int(comment_id), body) for comment_id, body in entries]
                for number, entries in snapshot["comments"].items()
            }
            self._next_comment_id = snapshot["next_comment_id"]
            self._open_prs = snapshot["open_prs"]
            self._pr_contexts = {
                branch: PullRequestContext(**context)
                for branch, context in snapshot["contexts"].items()
            }
            self.create_count = snapshot["create_count"]
            self._issue_labels = {
                int(number): tuple(labels) for number, labels in snapshot["labels"].items()
            }
            self._issue_get_bodies = {
                int(number): body for number, body in snapshot["bodies"].items()
            }

    def _persist(self) -> None:
        """提交替身服务状态；不把内存中的调用次数当成持久证据。"""
        self.storage.write_text(
            json.dumps(
                {
                    "comments": self._issue_comment_entries,
                    "next_comment_id": self._next_comment_id,
                    "open_prs": self._open_prs,
                    "contexts": {
                        branch: asdict(context)
                        for branch, context in self._pr_contexts.items()
                        if context is not None
                    },
                    "create_count": self.create_count,
                    "labels": self._issue_labels,
                    "bodies": self._issue_get_bodies,
                }
            ),
            encoding="utf-8",
        )

    def comment_issue(self, issue_number: int, body: str) -> None:
        """创建评论后保存服务端状态。"""
        super().comment_issue(issue_number, body)
        self._persist()

    def set_issue_labels(self, issue_number: int, labels: Sequence[str]) -> None:
        """提交当前 Issue 标签供恢复客户端读取。"""
        super().set_issue_labels(issue_number, labels)
        self._persist()

    def set_issue_body(self, issue_number: int, body: str) -> None:
        """提交当前 Issue 正文供恢复客户端读取。"""
        super().set_issue_body(issue_number, body)
        self._persist()

    def edit_issue_labels(
        self, issue_number: int, *, add: Sequence[str] = (), remove: Sequence[str] = ()
    ) -> None:
        """保存标签删除与 workflow 交接的真实结果。"""
        if self.faults.fail_label_removal and "direct-pr" in remove:
            raise RuntimeError("label deletion failed")
        if self.faults.ignore_label_removal and "direct-pr" in remove:
            return
        super().edit_issue_labels(issue_number, add=add, remove=remove)
        self._persist()

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        """可模拟写入报错或返回成功但服务端没有更新。"""
        if self.faults.fail_comment_update:
            raise RuntimeError("checkpoint write failed")
        if self.faults.ignore_comment_update:
            return
        super().edit_issue_comment(comment_id, body)
        self._persist()

    def create_draft_pr(self, *, title: str, body: str, base_branch: str, cwd: Path) -> str:
        """先提交 PR，再可选丢失创建响应，模拟最危险的崩溃窗口。"""
        self.create_count += 1
        self.install_pr(
            PullRequestContext(
                pr_url=_URL,
                branch=_BRANCH,
                head_sha=_HEAD,
                base_sha="b" * 40,
                number=91,
                body=body,
            )
        )
        if self.faults.lose_create_response:
            raise RuntimeError("PR committed but create response lost")
        return _URL

    def install_pr(self, context: PullRequestContext) -> None:
        """布置真实替身服务里的开放 PR，而非只返回预设 URL。"""
        self._pr_contexts[_BRANCH] = context
        self._open_prs[_BRANCH] = context.pr_url
        self._persist()


def _matching_pr() -> PullRequestContext:
    """使用保留的原 DIRECT marker。"""
    return PullRequestContext(
        pr_url=_URL,
        branch=_BRANCH,
        head_sha=_HEAD,
        base_sha="b" * 40,
        number=91,
        body=f"<!-- iar:direct-pr issued={_NUMBER} -->",
    )


def _runner() -> FakeProcessRunner:
    """仅替换 Git 进程，实际发布编排和评论持久化照常运行。"""
    return FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout=_BRANCH,
                stderr="",
            ),
            ("git", "rev-parse", "HEAD"): CommandResult(
                command=("git", "rev-parse", "HEAD"),
                return_code=0,
                stdout=_HEAD,
                stderr="",
            ),
        }
    )


def test_create_response_crash_recovers_from_disk_without_second_pr(tmp_path: Path) -> None:
    """真实 create 编排提交 PR 后丢失响应，新客户端只恢复同轮关联。"""
    storage = tmp_path / "github.json"
    original = _DurableGitHub(storage, _Faults(lose_create_response=True))
    saved = save_direct_pr_selection(original, _ISSUE, "direct-pr", claim_comment_id=501)

    with pytest.raises(Exception, match="create response lost"):
        create_draft_pr(
            _ISSUE,
            tmp_path,
            AppConfig(),
            original,
            _runner(),
            expected_branch=_BRANCH,
            publish_stage=PublishStage.DIRECT,
        )

    recovered = _DurableGitHub(storage)
    candidate = read_direct_pr_round(recovered, _ISSUE)
    assert candidate is not None and candidate.prior_pr_absent
    assert candidate.round == saved.round and candidate.pr_url is None
    assert recovered.create_count == 1
    assert prepare_direct_pr_publication(recovered, _ISSUE, _CANDIDATE) == _URL
    fresh = _DurableGitHub(storage)
    association = read_direct_pr_round(fresh, _ISSUE)
    assert association is not None and association.pr_url == _URL
    assert fresh.create_count == 1


def test_old_same_head_pr_cannot_authorize_a_new_round(tmp_path: Path) -> None:
    """同 Issue、分支、head 和 marker 都相同的旧 PR 仍不能消费新轮标签。"""
    client = _DurableGitHub(tmp_path / "github.json")
    previous = save_direct_pr_selection(client, _ISSUE, "direct-pr", claim_comment_id=501)
    prepare_direct_pr_publication(client, _ISSUE, _CANDIDATE)
    client.install_pr(_matching_pr())
    assert associated_direct_pr(client, read_direct_pr_round(client, _ISSUE), _CANDIDATE) == _URL
    complete_direct_pr_round(client, _ISSUE)
    next_round = save_direct_pr_selection(client, _ISSUE, "direct-pr", claim_comment_id=502)
    assert next_round.round != previous.round

    with pytest.raises(DirectPrRoundError, match="older PR"):
        prepare_direct_pr_publication(client, _ISSUE, _CANDIDATE)
    assert client.create_count == 0
    assert not any(call["method"] == "edit_issue_labels" for call in client.calls)


@pytest.mark.parametrize("ignored", [False, True])
def test_candidate_must_be_persisted_before_pr_creation(tmp_path: Path, ignored: bool) -> None:
    """候选写入失败或未真正提交时不能创建 PR。"""
    client = _DurableGitHub(tmp_path / "github.json")
    save_direct_pr_selection(client, _ISSUE, "direct-pr", claim_comment_id=501)
    client.faults = _Faults(
        fail_comment_update=not ignored,
        ignore_comment_update=ignored,
    )
    with pytest.raises(DirectPrRoundError):
        create_draft_pr(
            _ISSUE,
            tmp_path,
            AppConfig(),
            client,
            _runner(),
            expected_branch=_BRANCH,
            publish_stage=PublishStage.DIRECT,
        )
    assert _DurableGitHub(client.storage).create_count == 0


@pytest.mark.parametrize("mismatch", ["head", "branch", "issue", "repo", "url"])
def test_candidate_rejects_a_different_publication(tmp_path: Path, mismatch: str) -> None:
    """关联必须同时匹配仓库、Issue、分支、head 和已确认 URL。"""
    client = _DurableGitHub(tmp_path / "github.json")
    save_direct_pr_selection(client, _ISSUE, "direct-pr", claim_comment_id=501)
    prepare_direct_pr_publication(client, _ISSUE, _CANDIDATE)
    context = _matching_pr()
    client.install_pr(context)
    record = read_direct_pr_round(client, _ISSUE)
    assert record is not None
    assert associated_direct_pr(client, record, _CANDIDATE) == _URL
    record = read_direct_pr_round(client, _ISSUE)
    assert record is not None
    altered = {
        "head": replace(context, head_sha="c" * 40),
        "branch": replace(context, branch="issue-99"),
        "issue": replace(context, body="<!-- iar:direct-pr issued=99 -->"),
        "repo": replace(context, pr_url="https://github.com/other/repo/pull/91"),
        "url": replace(context, pr_url="https://github.com/example/repo/pull/92"),
    }[mismatch]
    client.install_pr(altered)
    with pytest.raises(DirectPrRoundError):
        associated_direct_pr(client, record, _CANDIDATE)


def test_foreign_repository_checkpoint_fails_closed(tmp_path: Path) -> None:
    """同编号 Issue 的评论被搬到另一仓库不能提供发布关联。"""
    client = _DurableGitHub(tmp_path / "github.json")
    save_direct_pr_selection(client, _ISSUE, "direct-pr", claim_comment_id=501)
    other_issue = replace(_ISSUE, url="https://github.com/other/repo/issues/17")
    with pytest.raises(DirectPrRoundError):
        read_direct_pr_round(_DurableGitHub(client.storage), other_issue)


def test_recovery_claim_keeps_original_round(tmp_path: Path) -> None:
    """换客户端和认领进程也沿用未完成发布的轮次。"""
    storage = tmp_path / "github.json"
    initial = save_direct_pr_selection(
        _DurableGitHub(storage), _ISSUE, "direct-pr", claim_comment_id=501
    )
    resumed = save_direct_pr_selection(
        _DurableGitHub(storage), _ISSUE, "direct-pr", claim_comment_id=999
    )
    assert resumed.round == initial.round
    assert resumed.comment_id == initial.comment_id


def test_completed_round_never_authorizes_cleanup(tmp_path: Path) -> None:
    """已交接的旧轮次不能成为下一轮标签的消费许可。"""
    client = _DurableGitHub(tmp_path / "github.json")
    save_direct_pr_selection(client, _ISSUE, "direct-pr", claim_comment_id=501)
    prepare_direct_pr_publication(client, _ISSUE, _CANDIDATE)
    client.install_pr(_matching_pr())
    record = read_direct_pr_round(client, _ISSUE)
    assert record is not None
    associated_direct_pr(client, record, _CANDIDATE)
    complete_direct_pr_round(client, _ISSUE)
    finished = read_direct_pr_round(_DurableGitHub(client.storage), _ISSUE)
    assert finished is not None and finished.handoff_complete
    assert associated_direct_pr(client, finished, _CANDIDATE) is None


def _published_storage(storage: Path, *, label_present: bool = True) -> _DurableGitHub:
    """提交一个发布成功、交接尚未完成的轮次，再改变当前正文。"""
    client = _DurableGitHub(storage)
    client.set_issue_labels(_NUMBER, _ISSUE.labels)
    client.set_issue_body(_NUMBER, _ISSUE.body)
    save_direct_pr_selection(client, _ISSUE, "direct-pr", claim_comment_id=501)
    prepare_direct_pr_publication(client, _ISSUE, _CANDIDATE)
    client.install_pr(_matching_pr())
    record = read_direct_pr_round(client, _ISSUE)
    assert record is not None
    assert associated_direct_pr(client, record, _CANDIDATE) == _URL
    client.set_issue_body(_NUMBER, "- PRD path: `tasks/pending/new-requirement.md`")
    if not label_present:
        client.edit_issue_labels(_NUMBER, remove=("direct-pr",))
    return _DurableGitHub(storage)


def _forbid_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复已成功 PR 时不允许任何新构建调用。"""

    def reject_builder(**kwargs):
        pytest.fail("Published Direct PR recovery invoked the builder")

    monkeypatch.setattr(run_agent_once, "run_agent_until_committed", reject_builder)
    monkeypatch.setattr(
        handlers, "arbitrate_first_claim", lambda **kwargs: SimpleNamespace(comment_id=999)
    )
    monkeypatch.setattr(
        "backend.core.use_cases.agent_runner_claim_arbitration.arbitrate_first_claim",
        lambda **kwargs: SimpleNamespace(comment_id=999),
    )


@pytest.mark.parametrize(
    "case",
    [
        (entry, label, enabled)
        for entry in ("ready", "running", "blocked")
        for label in (True, False)
        for enabled in (True, False)
    ],
)
def test_claim_entries_only_handoff_published_round_after_body_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, bool, bool],
) -> None:
    """三个实际 handler 都恢复同轮 PR，当前 PRD 或已删除标签不触发新门禁。"""
    entry, label_present, supervisor_enabled = case
    client = _published_storage(tmp_path / "github.json", label_present=label_present)
    _forbid_builder(monkeypatch)
    monkeypatch.setattr(handlers, "create_or_reuse_worktree", lambda *args: tmp_path)
    monkeypatch.setattr(handlers, "_find_worktree_path_for_issue", lambda *args: tmp_path)
    monkeypatch.setattr(handlers, "_ensure_worktree_branch", lambda *args: None)
    process_runner = _runner()
    current_issue = client.get_issue(_NUMBER)
    arguments = {
        "issue": current_issue,
        "repo_path": tmp_path,
        "config": AppConfig(post_pr_supervisor=PostPrSupervisorConfig(enabled=supervisor_enabled)),
        "agent": "auto",
        "github_client": client,
        "process_runner": process_runner,
    }
    if entry == "blocked":
        handlers._process_blocked_resolution(
            **arguments,
            marker=ReviewEventMarker(version=1, phase="blocked_resolution_requested", cycle=1),
        )
    elif entry == "running":
        handlers._process_running_publish_recovery(**arguments)
    else:
        handlers._process_ready_issue(**arguments)

    fresh_client = _DurableGitHub(client.storage)
    completed = read_direct_pr_round(fresh_client, fresh_client.get_issue(_NUMBER))
    assert completed is not None and completed.handoff_complete
    assert completed.round == "501" and completed.pr_url == _URL
    final_labels = set(fresh_client.get_issue(_NUMBER).labels)
    assert "direct-pr" not in final_labels and "test/preserve" in final_labels
    assert "agent/review" in final_labels
    assert fresh_client.create_count == 0
    assert not any(list(command)[:2] == ["git", "push"] for command in process_runner.calls)
    assert not any(command and command[0] == "just" for command in process_runner.calls)


@pytest.mark.parametrize(
    "case", [(label, enabled) for label in (True, False) for enabled in (True, False)]
)
def test_standalone_recover_only_handoffs_published_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: tuple[bool, bool]
) -> None:
    """真实 recover 用例遇正文新增 PRD 或已删标签，只交接同轮 PR。"""
    label_present, supervisor_enabled = case
    client = _published_storage(tmp_path / "github.json", label_present=label_present)
    _forbid_builder(monkeypatch)
    process_runner = _runner()
    path_command = ("echo", str(tmp_path))
    process_runner.responses[path_command] = CommandResult(
        command=path_command, return_code=0, stdout=str(tmp_path), stderr=""
    )
    config = AppConfig(post_pr_supervisor=PostPrSupervisorConfig(enabled=supervisor_enabled))
    config = replace(config, worktree=replace(config.worktree, path_command=f"echo {tmp_path}"))
    result = recover_publish_issue(
        request=PublishRecoveryRequest(issue_number=_NUMBER, expected_branch=_BRANCH),
        repo_path=tmp_path,
        config=config,
        github_client=client,
        process_runner=process_runner,
    )
    assert result.pr_url == _URL and result.pr_reused
    assert result.supervisor_action == "direct_pr_cleanup"
    fresh_client = _DurableGitHub(client.storage)
    completed = read_direct_pr_round(fresh_client, fresh_client.get_issue(_NUMBER))
    assert completed is not None and completed.handoff_complete
    assert "direct-pr" not in fresh_client.get_issue(_NUMBER).labels
    assert "agent/review" in fresh_client.get_issue(_NUMBER).labels
    assert "agent/supervising" not in fresh_client.get_issue(_NUMBER).labels
    assert fresh_client.create_count == 0
    assert not any(list(command)[:2] == ["git", "push"] for command in process_runner.calls)


@pytest.mark.parametrize("ignore_removal", [False, True], ids=["api-error", "success-no-write"])
def test_delete_failure_preserves_pr_and_can_recover_without_republication(
    tmp_path: Path,
    ignore_removal: bool,
) -> None:
    """删除报错时磁盘上仍有标签和原 PR，新客户端恢复只完成原交接。"""
    from backend.core.use_cases.agent_runner_direct_pr_label import (
        DirectPrLabelCleanupPendingError,
    )

    client = _published_storage(tmp_path / "github.json")
    client.faults = _Faults(
        fail_label_removal=not ignore_removal, ignore_label_removal=ignore_removal
    )
    decision = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=client.get_issue(_NUMBER),
        config=AppConfig(),
        github_client=client,
    )
    expected_error = "not visible" if ignore_removal else "label deletion failed"
    with pytest.raises(DirectPrLabelCleanupPendingError, match=expected_error) as error:
        handlers._resume_direct_pr_handoff(client, decision, _CANDIDATE, AppConfig())
    assert _URL in str(error.value)
    fresh = _DurableGitHub(client.storage)
    assert "direct-pr" in fresh.get_issue(_NUMBER).labels
    pending = read_direct_pr_round(fresh, fresh.get_issue(_NUMBER))
    assert pending is not None and pending.pr_url == _URL and not pending.handoff_complete
    assert handlers._resume_direct_pr_handoff(fresh, decision, _CANDIDATE, AppConfig())
    recovered = _DurableGitHub(client.storage)
    assert "direct-pr" not in recovered.get_issue(_NUMBER).labels
    assert recovered.create_count == 0


def test_delete_then_handoff_crash_recovers_even_without_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """标签删除提交后 workflow 报错，重启仍保持原 DIRECT 且只补交接。"""
    client = _published_storage(tmp_path / "github.json")
    original_transition = handlers.transition_issue_workflow_state

    def fail_transition(*args, **kwargs):
        raise RuntimeError("workflow handoff crashed")

    monkeypatch.setattr(handlers, "transition_issue_workflow_state", fail_transition)
    decision = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=client.get_issue(_NUMBER),
        config=AppConfig(),
        github_client=client,
    )
    with pytest.raises(RuntimeError, match="handoff crashed"):
        handlers._resume_direct_pr_handoff(client, decision, _CANDIDATE, AppConfig())
    restarted = _DurableGitHub(client.storage)
    assert "direct-pr" not in restarted.get_issue(_NUMBER).labels
    restored = resolve_publish_stage(
        requested_stage=PublishStage.NORMAL,
        issue=restarted.get_issue(_NUMBER),
        config=AppConfig(),
        github_client=restarted,
    )
    assert restored.publish_stage is PublishStage.DIRECT
    monkeypatch.setattr(handlers, "transition_issue_workflow_state", original_transition)
    assert handlers._resume_direct_pr_handoff(restarted, restored, _CANDIDATE, AppConfig())
    final_client = _DurableGitHub(client.storage)
    finished = read_direct_pr_round(final_client, final_client.get_issue(_NUMBER))
    assert finished is not None and finished.handoff_complete
    assert final_client.create_count == 0
    assert not any(
        call["method"] == "edit_issue_labels" and "direct-pr" in call["remove"]
        for call in restarted.calls
    )

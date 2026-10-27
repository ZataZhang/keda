"""认领时任务输入快照（``agent_runner_issue_snapshot``）的行为测试。

git 与文件系统全部真实；只有 Issue 对象由测试直接构造。
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases import agent_runner_issue_handlers as handlers
from backend.core.use_cases import agent_runner_issue_snapshot as snapshot_module
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimArbitrationLost
from backend.core.use_cases.agent_runner_issue_snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    IssueSnapshotRequest,
    append_snapshot_if_changed,
    build_issue_snapshot,
    read_issue_snapshots,
    record_issue_input_snapshot,
    snapshot_file_path,
)
from backend.infrastructure.process_runner import SubprocessRunner
from tests.conftest import FakeProcessRunner
from tests.support.agent_runner import config_with_review_disabled, create_commit, init_git_repo
from tests.test_agent_runner_direct_pr_label import (
    _client,
    _patch_ready_handler,
)
from tests.test_agent_runner_direct_pr_label import (
    _issue as _label_issue,
)


def _git(repo_path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _issue(*, body: str = "修复登录页\n\n详细描述 B1", number: int = 7) -> IssueSummary:
    return IssueSummary(
        number=number,
        title="修复登录页",
        url=f"https://github.com/example/repo/issues/{number}",
        body=body,
        labels=("agent/ready", "bug"),
        state="OPEN",
    )


def _request(repo_path: Path, issue: IssueSummary) -> IssueSnapshotRequest:
    return IssueSnapshotRequest(
        issue=issue,
        repo_id="demo",
        worktree_path=repo_path,
        config=AppConfig(),
        process_runner=SubprocessRunner(),
    )


@pytest.fixture()
def issue_branch_repo(tmp_path: Path) -> tuple[Path, str]:
    """main 上有基线提交；当前位于 issue-7 分支，已多出两个提交。返回(仓库, 基线 sha)。"""
    repo_path = init_git_repo(tmp_path / "repo")
    base_sha = create_commit(repo_path, "base")
    _git(repo_path, "checkout", "-b", "issue-7")
    create_commit(repo_path, "leftover work 1")
    create_commit(repo_path, "leftover work 2")
    return repo_path, base_sha


def test_snapshot_path_lives_under_state_home_and_sanitizes_repo_id(tmp_path: Path) -> None:
    path = snapshot_file_path(repo_id="../evil/repo", issue_number=7, home_path=tmp_path)

    assert path == tmp_path / ".kedacode" / "dataset" / "issue-snapshots" / ".._evil_repo" / (
        "issue-7.snapshots.jsonl"
    )
    assert tmp_path in path.parents


def test_snapshot_records_original_body_and_fork_point_not_head(
    issue_branch_repo: tuple[Path, str],
) -> None:
    repo_path, base_sha = issue_branch_repo
    issue = _issue()

    snapshot = build_issue_snapshot(_request(repo_path, issue))

    assert snapshot.schema_version == SNAPSHOT_SCHEMA_VERSION
    assert snapshot.body == issue.body
    assert snapshot.body_sha256 == hashlib.sha256(issue.body.encode("utf-8")).hexdigest()
    assert snapshot.base_sha == base_sha
    assert snapshot.head_sha == _git(repo_path, "rev-parse", "HEAD")
    assert snapshot.base_sha != snapshot.head_sha
    assert snapshot.base_ref == "main"
    assert snapshot.labels == ("agent/ready", "bug")


def test_unchanged_body_is_not_appended_and_edited_body_is_appended_without_touching_old_lines(
    issue_branch_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo_path, _ = issue_branch_repo
    snapshot_path = snapshot_file_path(repo_id="demo", issue_number=7, home_path=tmp_path)

    assert record_issue_input_snapshot(_request(repo_path, _issue()), home_path=tmp_path) is True
    assert record_issue_input_snapshot(_request(repo_path, _issue()), home_path=tmp_path) is False
    first_line_bytes = snapshot_path.read_bytes().splitlines()[0]
    assert len(snapshot_path.read_text(encoding="utf-8").splitlines()) == 1

    edited = _issue(body="修复登录页（已被编辑）B2")
    assert record_issue_input_snapshot(_request(repo_path, edited), home_path=tmp_path) is True

    lines = snapshot_path.read_bytes().splitlines()
    assert len(lines) == 2
    assert lines[0] == first_line_bytes
    assert json.loads(lines[1])["body"] == "修复登录页（已被编辑）B2"


def test_body_that_changes_back_is_appended_again(
    issue_branch_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo_path, _ = issue_branch_repo
    for body in ("B1", "B2", "B1"):
        record_issue_input_snapshot(_request(repo_path, _issue(body=body)), home_path=tmp_path)

    snapshot_path = snapshot_file_path(repo_id="demo", issue_number=7, home_path=tmp_path)
    bodies = [record["body"] for record in read_issue_snapshots(snapshot_path)]
    assert bodies == ["B1", "B2", "B1"]


def test_unresolvable_base_keeps_head_and_leaves_base_null(tmp_path: Path) -> None:
    repo_path = init_git_repo(tmp_path / "repo")
    create_commit(repo_path, "only commit")
    _git(repo_path, "branch", "-m", "trunk")

    snapshot = build_issue_snapshot(_request(repo_path, _issue()))

    assert snapshot.base_sha is None
    assert snapshot.base_ref is None
    assert snapshot.head_sha == _git(repo_path, "rev-parse", "HEAD")


def test_stack_sequencing_uses_upstream_issue_branch_as_base(tmp_path: Path) -> None:
    repo_path = init_git_repo(tmp_path / "repo")
    create_commit(repo_path, "base")
    _git(repo_path, "checkout", "-b", "issue-42")
    upstream_sha = create_commit(repo_path, "upstream work")
    _git(repo_path, "checkout", "-b", "issue-43")
    create_commit(repo_path, "downstream work")
    stacked_issue = _issue(body='<!-- kc:depends-on #42 mode="stack" -->', number=43)

    snapshot = build_issue_snapshot(_request(repo_path, stacked_issue))

    assert snapshot.base_ref == "issue-42"
    assert snapshot.base_sha == upstream_sha


def test_reader_skips_corrupt_lines_and_unknown_schema_versions_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    snapshot_path = tmp_path / "issue-7.snapshots.jsonl"
    good = {"schema_version": 1, "body": "ok", "body_sha256": "a"}
    future = {"schema_version": SNAPSHOT_SCHEMA_VERSION + 1, "body": "from the future"}
    snapshot_path.write_text(
        "\n".join([json.dumps(good), "{not json", json.dumps(future), ""]), encoding="utf-8"
    )

    with caplog.at_level(logging.WARNING):
        records = read_issue_snapshots(snapshot_path)

    assert records == [good]
    assert "unknown schema_version" in caplog.text
    assert "corrupt snapshot line" in caplog.text


def test_corrupt_last_line_is_treated_as_changed_and_old_lines_survive(
    issue_branch_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo_path, _ = issue_branch_repo
    snapshot_path = snapshot_file_path(repo_id="demo", issue_number=7, home_path=tmp_path)
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text('{"schema_version": 1, "body_sha256": "x"}\n{broken', encoding="utf-8")
    original_bytes = snapshot_path.read_bytes()

    snapshot = build_issue_snapshot(_request(repo_path, _issue()))
    assert append_snapshot_if_changed(snapshot_path=snapshot_path, snapshot=snapshot) is True

    assert snapshot_path.read_bytes().startswith(original_bytes)


def test_snapshot_failure_only_warns_and_never_raises(
    issue_branch_repo: tuple[Path, str], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo_path, _ = issue_branch_repo
    # 家目录路径被一个普通文件占住，目录创建必然失败。
    blocked_home_path = tmp_path / "blocked-home"
    blocked_home_path.write_text("i am a file", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        written = record_issue_input_snapshot(
            _request(repo_path, _issue()), home_path=blocked_home_path
        )

    assert written is False
    assert "failed to record issue input snapshot" in caplog.text


def test_snapshot_does_not_touch_the_processed_repository(
    issue_branch_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo_path, _ = issue_branch_repo
    status_before = _git(repo_path, "status", "--porcelain")

    record_issue_input_snapshot(_request(repo_path, _issue()), home_path=tmp_path / "home")

    assert _git(repo_path, "status", "--porcelain") == status_before == ""
    assert not list(repo_path.glob("**/*.snapshots.jsonl"))


class _StopAfterAgentStarted(Exception):
    """探针 Agent 启动后立即中止，只用来观察事件顺序。"""


def test_ready_handler_records_snapshot_after_claim_and_before_agent_starts(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        handlers,
        "record_issue_input_snapshot",
        lambda request: events.append(f"snapshot:{request.issue.number}"),
    )

    def run_agent(*_args, **_kwargs):
        events.append("agent")
        raise _StopAfterAgentStarted

    _patch_ready_handler(monkeypatch, run_agent=run_agent)
    monkeypatch.setattr(
        handlers, "arbitrate_first_claim", lambda **_kwargs: SimpleNamespace(comment_id=91)
    )

    with pytest.raises(Exception):  # noqa: B017 - 只关心事件顺序，不关心中止后的错误包装
        handlers._process_ready_issue(
            issue=_label_issue(),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=_client(),
            process_runner=FakeProcessRunner(),
        )

    assert events[:2] == ["snapshot:7", "agent"]


def test_claim_loser_writes_no_snapshot(monkeypatch) -> None:
    recorded: list[object] = []
    monkeypatch.setattr(handlers, "record_issue_input_snapshot", recorded.append)
    _patch_ready_handler(monkeypatch, run_agent=lambda *_a, **_k: None)

    def lose(**_kwargs):
        raise ClaimArbitrationLost("earlier bidder won", winner=None)

    monkeypatch.setattr(handlers, "arbitrate_first_claim", lose)

    with pytest.raises(ClaimArbitrationLost):
        handlers._process_ready_issue(
            issue=_label_issue(),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=_client(),
            process_runner=FakeProcessRunner(),
        )

    assert recorded == []


@pytest.mark.parametrize("repo_id", ["..", ".", "../evil/repo"])
def test_snapshot_path_never_leaves_the_snapshot_area(tmp_path: Path, repo_id: str) -> None:
    path = snapshot_file_path(repo_id=repo_id, issue_number=7, home_path=tmp_path)

    snapshot_area = tmp_path / ".kedacode" / "dataset" / "issue-snapshots"
    assert path.resolve().is_relative_to(snapshot_area.resolve())
    assert path.parent != snapshot_area


def test_ready_handler_continues_after_snapshot_failure(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """快照写入失败时，认领后的执行链照常走到 Agent；只留一条警告，不产生快照文件。"""
    blocked_home_path = tmp_path / "blocked-home"
    blocked_home_path.write_text("i am a file", encoding="utf-8")
    original_snapshot_file_path = snapshot_module.snapshot_file_path
    monkeypatch.setattr(
        snapshot_module,
        "snapshot_file_path",
        lambda *, repo_id, issue_number, home_path=None: original_snapshot_file_path(
            repo_id=repo_id, issue_number=issue_number, home_path=blocked_home_path
        ),
    )
    events: list[str] = []

    def run_agent(*_args, **_kwargs):
        events.append("agent")
        raise _StopAfterAgentStarted

    _patch_ready_handler(monkeypatch, run_agent=run_agent)
    monkeypatch.setattr(
        handlers, "arbitrate_first_claim", lambda **_kwargs: SimpleNamespace(comment_id=91)
    )

    with caplog.at_level(logging.WARNING), pytest.raises(_StopAfterAgentStarted):
        handlers._process_ready_issue(
            issue=_label_issue(),
            repo_path=Path("."),
            config=config_with_review_disabled(),
            agent="auto",
            github_client=_client(),
            process_runner=FakeProcessRunner(),
        )

    assert events == ["agent"]
    assert "failed to record issue input snapshot" in caplog.text
    assert blocked_home_path.read_text(encoding="utf-8") == "i am a file"

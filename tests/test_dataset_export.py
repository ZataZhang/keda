"""``scripts.dataset_export`` 的只读导出行为测试。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from scripts.dataset_export import export_samples as export_module
from scripts.dataset_export.export_samples import (
    ExportOptions,
    IssueGroup,
    classify_size_band,
    classify_task_kind,
    export_samples,
    has_failed_then_succeeded,
    merge_groups_by_repo_path,
    open_readonly,
    pick_pull_request,
    summarize_samples,
)

_SCHEMA = """
CREATE TABLE run_records (id INTEGER PRIMARY KEY, repo_id TEXT, repo_path TEXT,
  issue_number INTEGER, trigger TEXT, agent TEXT, outcome TEXT, error_summary TEXT,
  started_at TEXT, finished_at TEXT, duration_seconds REAL, issue_title TEXT, issue_url TEXT);
CREATE TABLE attempt_records (id INTEGER PRIMARY KEY, repo_id TEXT, issue_number INTEGER,
  agent TEXT, attempt_number INTEGER, failure_type TEXT, recovered INTEGER, detail TEXT,
  started_at TEXT, finished_at TEXT, duration_seconds REAL, failure_phase TEXT,
  preset TEXT, model TEXT);
CREATE TABLE agent_invocation_events (id INTEGER PRIMARY KEY, repo_id TEXT,
  issue_number INTEGER, event_type TEXT, phase TEXT, role TEXT, agent TEXT,
  occurred_at TEXT, detail_json TEXT);
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    """带 main 与 issue-7 分支的临时仓库，issue-7 上有一个提交。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "issue-7")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix: solve issue 7")
    _git(repo, "checkout", "main")
    return repo


@pytest.fixture()
def console_db(tmp_path: Path, sample_repo: Path) -> Path:
    """含一个真实 Issue、一个测试仓库 Issue 的 console.db。"""
    db_path = tmp_path / "console.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(_SCHEMA)
    runs = [
        ("keda", str(sample_repo), 7, "completed", "修复 7"),
        ("rv-demo", str(sample_repo), 1, "failed", None),
    ]
    for index, (repo_id, path, issue, outcome, title) in enumerate(runs, start=1):
        connection.execute(
            "INSERT INTO run_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                index,
                repo_id,
                path,
                issue,
                "cli_run",
                "claude",
                outcome,
                None,
                "2026-10-01T00:00:00+00:00",
                "2026-10-01T00:10:00+00:00",
                600.0,
                title,
                None,
            ),
        )
    for number, failure_type in enumerate(["agent_error", "success"], start=1):
        connection.execute(
            "INSERT INTO attempt_records VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("keda", 7, "claude", number, failure_type, 0, "", "t0", "t1", 1.0, None, None, "m"),
        )
    connection.execute(
        "INSERT INTO agent_invocation_events VALUES (NULL,?,?,?,?,?,?,?,?)",
        (
            "keda",
            7,
            "invocation_started",
            "implementation",
            "implementer",
            "claude",
            "t0",
            json.dumps({"requested_model": "opus"}),
        ),
    )
    connection.commit()
    connection.close()
    return db_path


def test_export_builds_issue_keyed_sample_and_skips_demo_repos(console_db: Path) -> None:
    samples = export_samples(ExportOptions(db_path=console_db, output_path=None))

    assert [s["sample_id"] for s in samples] == ["keda#issue-7"]
    sample = samples[0]
    assert sample["input"]["provenance"] == "title_only"
    assert sample["input"]["body"] is None
    assert [c["subject"] for c in sample["code"]["commits"]] == ["fix: solve issue 7"]
    assert sample["code"]["base_sha"] != sample["code"]["final_sha"]
    labels = sample["labels"]
    assert labels["resolved"] is True
    assert labels["resolved_basis"] == "run_completed"
    assert labels["has_commits"] is True
    assert labels["has_evidence"] is False
    assert labels["has_preference_pair"] is True
    assert labels["task_kind"] == "development"
    assert labels["merge_commit_count"] == 0
    assert labels["size_band"] == "small"
    # 没有已合并 PR 作依据时，不能进入评测集
    assert labels["eval_ready"] is False
    assert sample["invocations"][0]["requested_model"] == "opus"
    assert "repo_path" not in json.dumps(sample)


def test_missing_input_is_never_labelled_verbatim(console_db: Path) -> None:
    connection = sqlite3.connect(console_db)
    connection.execute("UPDATE run_records SET issue_title = NULL")
    connection.commit()
    connection.close()

    samples = export_samples(ExportOptions(db_path=console_db, output_path=None))

    assert samples[0]["input"]["provenance"] == "missing"
    assert summarize_samples(samples)["eval_ready"] == 0


def test_database_is_opened_read_only(console_db: Path) -> None:
    connection = open_readonly(console_db)
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM run_records")
    connection.close()


@pytest.mark.parametrize(
    ("failure_types", "expected"),
    [
        (["success"], False),
        (["agent_error", "success"], True),
        (["success", "agent_error"], False),
    ],
)
def test_has_failed_then_succeeded(failure_types: list[str], expected: bool) -> None:
    attempts = [{"failure_type": value} for value in failure_types]
    assert has_failed_then_succeeded(attempts) is expected


def _pull_request_fixture(sample_repo: Path, *, state: str) -> dict[str, object]:
    """构造与 ``sample_repo`` 的 issue-7 分支一致的 PR 数据，并让 PR head 领先本地分支。"""
    _git(sample_repo, "checkout", "issue-7")
    (sample_repo / "c.txt").write_text("c", encoding="utf-8")
    _git(sample_repo, "add", ".")
    _git(sample_repo, "commit", "-m", "fix: follow-up pushed only to the PR")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sample_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    first = subprocess.run(
        ["git", "rev-parse", "HEAD~1"], cwd=sample_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _git(sample_repo, "checkout", "main")
    # 本地分支回退到第一个提交，模拟"本地落后于 PR"
    _git(sample_repo, "branch", "-f", "issue-7", first)
    return {
        "number": 9,
        "state": state,
        "headRefName": "issue-7",
        "headRefOid": head,
        "additions": 2,
        "deletions": 0,
        "changedFiles": 2,
        "commits": [
            {"oid": first, "messageHeadline": "fix: solve issue 7"},
            {"oid": head, "messageHeadline": "fix: follow-up pushed only to the PR"},
        ],
    }


@pytest.mark.parametrize(("state", "expected_ready"), [("MERGED", True), ("OPEN", False)])
def test_pull_request_is_authoritative_for_commits_and_resolution(
    console_db: Path,
    sample_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    expected_ready: bool,
) -> None:
    pull_request = _pull_request_fixture(sample_repo, state=state)
    monkeypatch.setattr(
        export_module,
        "collect_gh_facts",
        lambda **_: {
            "issue": {"title": "修复 7", "body": "原文", "url": "u"},
            "pull_requests": [pull_request],
        },
    )

    samples = export_samples(ExportOptions(db_path=console_db, output_path=None, use_gh=True))

    sample = samples[0]
    assert sample["code"]["source"] == "pull_request"
    assert sample["code"]["final_sha"] == pull_request["headRefOid"]
    assert len(sample["code"]["commits"]) == 2
    assert sample["labels"]["resolved"] is (state == "MERGED")
    assert sample["labels"]["resolved_basis"] == "pr_merged"
    assert sample["labels"]["eval_ready"] is expected_ready


def test_patrol_issue_is_not_eval_ready(
    console_db: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pull_request = _pull_request_fixture(sample_repo, state="MERGED")
    monkeypatch.setattr(
        export_module,
        "collect_gh_facts",
        lambda **_: {
            "issue": {"title": "[Bug] Main CI 夜间巡检 — 2026-10-15", "body": "x", "url": "u"},
            "pull_requests": [pull_request],
        },
    )

    sample = export_samples(ExportOptions(db_path=console_db, output_path=None, use_gh=True))[0]

    assert sample["labels"]["task_kind"] == "patrol"
    assert sample["labels"]["eval_ready"] is False


def test_pick_pull_request_prefers_latest_merged() -> None:
    pull_requests = [
        {"number": 1, "state": "MERGED"},
        {"number": 2, "state": "CLOSED"},
        {"number": 3, "state": "MERGED"},
    ]
    assert pick_pull_request(pull_requests)["number"] == 3
    assert pick_pull_request([{"number": 5, "state": "OPEN"}])["number"] == 5
    assert pick_pull_request(None) is None


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (None, None),
        ({"files": 1, "additions": 1, "deletions": 0}, "small"),
        ({"files": 21, "additions": 1140, "deletions": 143}, "medium"),
        ({"files": 84, "additions": 10045, "deletions": 1895}, "large"),
    ],
)
def test_classify_size_band(size: dict[str, int] | None, expected: str | None) -> None:
    assert classify_size_band(size) == expected


def test_classify_task_kind() -> None:
    assert classify_task_kind("Keda 阻塞与失败巡查 — 2026-10-20") == "patrol"
    assert classify_task_kind("Stats 页面增加顶部 Tab") == "development"
    assert classify_task_kind(None) == "development"


def _group(repo_id: str, repo_path: Path, *, run_count: int) -> IssueGroup:
    runs = [
        {"repo_path": str(repo_path), "started_at": f"2026-10-0{index}", "outcome": "failed"}
        for index in range(1, run_count + 1)
    ]
    attempts = [{"started_at": run["started_at"], "failure_type": "agent_error"} for run in runs]
    return IssueGroup(repo_id=repo_id, issue_number=7, runs=runs, attempts=attempts)


def test_same_repo_path_under_two_repo_ids_is_exported_once(sample_repo: Path) -> None:
    groups = [
        _group("keda-main", sample_repo, run_count=1),
        _group("keda", sample_repo, run_count=2),
    ]

    merged = merge_groups_by_repo_path(groups)

    assert len(merged) == 1
    assert merged[0].repo_id == "keda"
    assert merged[0].alias_repo_ids == ["keda-main"]
    assert len(merged[0].runs) == 3
    assert [run["started_at"] for run in merged[0].runs] == sorted(
        run["started_at"] for run in merged[0].runs
    )


def test_unverified_merge_history_is_not_eval_ready(
    console_db: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pull_request = _pull_request_fixture(sample_repo, state="MERGED")
    monkeypatch.setattr(
        export_module,
        "collect_gh_facts",
        lambda **_: {
            "issue": {"title": "修复 7", "body": "原文", "url": "u"},
            "pull_requests": [pull_request],
        },
    )
    monkeypatch.setattr(export_module, "count_merge_commits", lambda **_: None)

    sample = export_samples(ExportOptions(db_path=console_db, output_path=None, use_gh=True))[0]

    assert sample["labels"]["merge_commit_count"] is None
    assert sample["labels"]["eval_ready"] is False


def _write_snapshot(
    home_path: Path,
    *,
    body: str,
    base_sha: str | None,
    captured_at: str = "2026-10-01T00:00:00+00:00",
) -> None:
    """在注入的临时家目录里为 keda#7 写一条认领时快照。"""
    import hashlib

    from backend.core.use_cases import agent_runner_issue_snapshot as issue_snapshot

    snapshot = issue_snapshot.IssueInputSnapshot(
        schema_version=issue_snapshot.SNAPSHOT_SCHEMA_VERSION,
        captured_at=captured_at,
        repo_id="keda",
        issue_number=7,
        title="认领时标题",
        body=body,
        body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        labels=("agent/ready",),
        state="OPEN",
        url="https://github.com/example/repo/issues/7",
        base_sha=base_sha,
        head_sha=None,
        base_ref="main",
    )
    snapshot_path = issue_snapshot.snapshot_file_path(
        repo_id="keda", issue_number=7, home_path=home_path
    )
    issue_snapshot.append_snapshot_if_changed(snapshot_path=snapshot_path, snapshot=snapshot)


def test_snapshot_at_claim_is_preferred_and_edit_after_claim_is_flagged(
    console_db: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home_path = tmp_path / "home"
    pull_request = _pull_request_fixture(sample_repo, state="MERGED")
    _write_snapshot(home_path, body="认领时的原文 B1", base_sha="a" * 40)
    monkeypatch.setattr(
        export_module,
        "collect_gh_facts",
        lambda **_: {
            "issue": {"title": "现在的标题", "body": "被改过的正文 B2", "url": "u"},
            "pull_requests": [pull_request],
        },
    )

    sample = export_samples(
        ExportOptions(db_path=console_db, output_path=None, use_gh=True, home_path=home_path)
    )[0]

    assert sample["input"]["provenance"] == "snapshot_at_claim"
    assert sample["input"]["body"] == "认领时的原文 B1"
    assert sample["input"]["title"] == "认领时标题"
    assert sample["input"]["edited_since_claim"] is True
    assert sample["code"]["base_sha"] == "a" * 40
    assert sample["code"]["base_sha_source"] == "snapshot"


def test_snapshot_without_later_edit_is_not_flagged(console_db: Path, tmp_path: Path) -> None:
    home_path = tmp_path / "home"
    _write_snapshot(home_path, body="B1", base_sha=None)

    sample = export_samples(
        ExportOptions(db_path=console_db, output_path=None, home_path=home_path)
    )[0]

    assert sample["input"]["provenance"] == "snapshot_at_claim"
    assert sample["input"]["edited_since_claim"] is False
    assert sample["code"]["base_sha_source"] == "inferred"


def test_issue_without_snapshot_exports_exactly_as_before(console_db: Path, tmp_path: Path) -> None:
    sample = export_samples(
        ExportOptions(db_path=console_db, output_path=None, home_path=tmp_path / "empty-home")
    )[0]

    assert sample["input"]["provenance"] == "title_only"
    assert sample["input"]["edited_since_claim"] is None
    assert sample["input"]["body"] is None


def _fake_gh(
    monkeypatch: pytest.MonkeyPatch, *, listed: list[dict], details: dict, issues: dict
) -> None:
    """按 gh 子命令路由的假 ``fetch_gh_json``：只回放给定数据，未预期的调用直接失败。"""

    def fake_fetch_gh_json(*, repo_path: Path, args: list[str]):
        if args[:2] == ["repo", "view"]:
            return {"nameWithOwner": "owner/repo"}
        if args[:2] == ["pr", "list"]:
            return listed
        if args[:2] == ["pr", "view"]:
            return details.get(int(args[2]))
        if args[:2] == ["issue", "view"]:
            return issues.get(int(args[2]))
        pytest.fail(f"unexpected gh call: {args}")

    monkeypatch.setattr(export_module, "fetch_gh_json", fake_fetch_gh_json)


def _github_pr(
    sample_repo: Path, *, number: int, head: str, title: str, closing: list[int]
) -> dict:
    """基于 ``sample_repo`` 的真实提交构造一个已合并 PR 的详情。"""
    detail = _pull_request_fixture(sample_repo, state="MERGED")
    detail.update(
        number=number,
        headRefName=head,
        title=title,
        body="PR 描述：做了什么",
        mergedAt="2026-10-01T00:00:00Z",
        closingIssuesReferences=[{"number": n} for n in closing],
        author={"login": "someone", "is_bot": False},
        url=f"https://github.com/owner/repo/pull/{number}",
    )
    return detail


def _pr_only_options(tmp_path: Path, repo_path: Path) -> ExportOptions:
    return ExportOptions(
        db_path=tmp_path / "missing.db",
        output_path=None,
        github_pr_repo_paths=(repo_path,),
        home_path=tmp_path / "home",
    )


def test_pr_without_linked_issue_is_reconstructed_and_never_eval_ready(
    sample_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    detail = _github_pr(sample_repo, number=9, head="feat/x", title="feat: add x", closing=[])
    _fake_gh(
        monkeypatch,
        listed=[{"number": 9, "headRefName": "feat/x", "author": {}}],
        details={9: detail},
        issues={},
    )

    samples = export_samples(_pr_only_options(tmp_path, sample_repo))

    sample = samples[0]
    assert sample["origin"] == "github_pr"
    assert sample["sample_id"] == "owner/repo#pr-9"
    assert sample["input"]["provenance"] == "pr_description"
    assert sample["input"]["title"] == "feat: add x"
    assert sample["code"]["source"] == "pull_request"
    assert sample["runs"] == sample["attempts"] == []
    assert sample["labels"]["has_preference_pair"] is False
    assert sample["labels"]["eval_ready"] is False
    assert summarize_samples(samples)["input_pr_description"] == 1


def test_pr_with_linked_issue_uses_issue_body_and_can_be_eval_ready(
    sample_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    detail = _github_pr(sample_repo, number=9, head="feat/x", title="feat: add x", closing=[5])
    issue = {
        "title": "需求标题",
        "body": "需求原文",
        "url": "https://github.com/owner/repo/issues/5",
    }
    _fake_gh(
        monkeypatch,
        listed=[{"number": 9, "headRefName": "feat/x", "author": {}}],
        details={9: detail},
        issues={5: issue},
    )

    sample = export_samples(_pr_only_options(tmp_path, sample_repo))[0]

    assert sample["input"]["provenance"] == "verbatim_issue_body"
    assert sample["input"]["body"] == "需求原文"
    assert sample["issue_number"] == 5
    assert sample["labels"]["merge_commit_count"] == 0
    assert sample["labels"]["eval_ready"] is True


def test_bot_prs_and_prs_already_exported_from_keda_runs_are_skipped(
    console_db: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    keda_pr = _github_pr(sample_repo, number=9, head="issue-7", title="[Agent] fix", closing=[7])
    listed = [
        {"number": 9, "headRefName": "issue-7", "author": {"is_bot": False}},
        {"number": 10, "headRefName": "dependabot/x", "author": {"is_bot": True}},
        {"number": 11, "headRefName": "feat/y", "author": {"is_bot": False}},
    ]
    human_pr = {**keda_pr, "number": 11, "headRefName": "feat/y", "closingIssuesReferences": []}
    _fake_gh(monkeypatch, listed=listed, details={9: keda_pr, 11: human_pr}, issues={})

    samples = export_samples(
        ExportOptions(
            db_path=console_db,
            output_path=None,
            github_pr_repo_paths=(sample_repo,),
            home_path=tmp_path / "home",
        )
    )

    assert [s["sample_id"] for s in samples] == ["keda#issue-7", "owner/repo#pr-11"]
    assert [s["origin"] for s in samples] == ["keda_run", "github_pr"]


def test_pr_source_is_skipped_quietly_when_gh_is_unavailable(
    sample_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(export_module, "fetch_gh_json", lambda **_: None)

    samples = export_samples(_pr_only_options(tmp_path, sample_repo))

    assert samples == []
    assert "gh 不可用" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("docs(prd): archive issue-209 PRD", "maintenance"),
        ("chore(template): sync prd skill", "maintenance"),
        ("chore: bump", "maintenance"),
        ("feat(cli): add tokens command", "development"),
        ("fix(agent-runner): stop preemption", "development"),
        ("Stats 页 Token 用量补按 PRD 维度汇总", "development"),
    ],
)
def test_conventional_commit_docs_and_chore_titles_are_maintenance(
    title: str, expected: str
) -> None:
    assert classify_task_kind(title) == expected

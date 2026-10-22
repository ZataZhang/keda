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

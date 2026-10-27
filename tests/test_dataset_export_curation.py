"""普通提交来源（``commits``）与后期补全叠加层（``curation``）的行为测试。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.dataset_export.cli import main
from scripts.dataset_export.commits import list_plain_commits
from scripts.dataset_export.curation import (
    build_curation_worklist,
    load_curation_entries,
    parse_curation_record,
)
from scripts.dataset_export.export_samples import ExportOptions, export_samples, find_base_branch
from tests.support.agent_runner import create_commit, init_git_repo


def _git(repo_path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_as(repo_path: Path, author: str, message: str) -> str:
    """以指定作者名提交一个带真实改动的提交。"""
    (repo_path / f"f{len(list(repo_path.iterdir()))}.txt").write_text(message, encoding="utf-8")
    _git(repo_path, "add", ".")
    subprocess.run(
        ["git", "-C", str(repo_path), "-c", f"user.name={author}", "commit", "-q", "-m", message],
        check=True,
        capture_output=True,
    )
    return _git(repo_path, "rev-parse", "HEAD")


@pytest.fixture()
def repo_with_mixed_commits(tmp_path: Path) -> dict[str, Any]:
    """main 上有：基线、两个普通提交、一个 squash-PR 提交、一个机器人提交、一个合并提交。"""
    repo_path = init_git_repo(tmp_path / "proj")
    _git(repo_path, "remote", "add", "origin", "https://github.com/owner/repo.git")
    create_commit(repo_path, "root")
    plain_one = _commit_as(
        repo_path, "Test User", "feat: plain one\n\n详细说明 api_key = sk-abcdefghijklmnopqrstuvwx"
    )
    _commit_as(repo_path, "Test User", "fix: from a squash merged pr (#12)")
    _commit_as(repo_path, "dependabot[bot]", "chore(deps): bump x")
    _git(repo_path, "checkout", "-b", "side")
    _commit_as(repo_path, "Test User", "side work")
    _git(repo_path, "checkout", "main")
    _git(repo_path, "merge", "--no-ff", "-m", "Merge branch 'side'", "side")
    plain_two = _commit_as(repo_path, "Test User", "fix: plain two")
    return {"repo": repo_path, "plain_one": plain_one, "plain_two": plain_two}


def _commit_options(tmp_path: Path, repo_path: Path, **overrides: Any) -> ExportOptions:
    return ExportOptions(
        db_path=tmp_path / "missing.db",
        output_path=None,
        commit_repo_paths=(repo_path,),
        home_path=tmp_path / "home",
        **overrides,
    )


# --- 普通提交来源 ------------------------------------------------------------


def test_plain_commits_skip_pr_squash_bot_merge_and_covered_commits(
    repo_with_mixed_commits: dict[str, Any],
) -> None:
    repo_path = repo_with_mixed_commits["repo"]

    subjects = [
        commit["subject"]
        for commit in list_plain_commits(
            repo_path=repo_path,
            default_ref="main",
            limit=10,
            excluded_shas={repo_with_mixed_commits["plain_two"]},
        )
    ]

    assert subjects == ["feat: plain one"]


def test_plain_commit_sample_records_facts_only_and_is_never_eval_ready(
    tmp_path: Path, repo_with_mixed_commits: dict[str, Any]
) -> None:
    repo_path = repo_with_mixed_commits["repo"]

    samples = export_samples(_commit_options(tmp_path, repo_path))

    by_subject = {s["input"]["title"]: s for s in samples}
    assert set(by_subject) == {"feat: plain one", "fix: plain two"}
    sample = by_subject["feat: plain one"]
    assert sample["origin"] == "git_commit"
    assert sample["sample_id"] == f"owner/repo#commit-{repo_with_mixed_commits['plain_one'][:10]}"
    assert sample["input"]["provenance"] == "commit_message"
    assert "sk-abcdef" not in sample["input"]["body"]
    assert sample["input"]["redaction_count"] == 1
    assert sample["code"]["final_sha"] == repo_with_mixed_commits["plain_one"]
    assert sample["code"]["base_sha"] == _git(
        repo_path, "rev-parse", f"{sample['code']['final_sha']}^"
    )
    assert sample["labels"]["resolved_basis"] == "on_default_branch"
    assert sample["labels"]["has_preference_pair"] is False
    assert sample["labels"]["eval_ready"] is False
    assert "author" not in json.dumps(sample)


def test_commit_limit_keeps_the_most_recent(
    tmp_path: Path, repo_with_mixed_commits: dict[str, Any]
) -> None:
    samples = export_samples(
        _commit_options(tmp_path, repo_with_mixed_commits["repo"], commit_limit=1)
    )

    assert [s["input"]["title"] for s in samples] == ["fix: plain two"]


# --- 补全叠加层 --------------------------------------------------------------


def _write_curation(directory: Path, *records: dict[str, Any], name: str = "c1.jsonl") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def _record(sample_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sample_id": sample_id,
        "at": "2026-10-22T08:00:00Z",
        "by": {"kind": "human", "name": "someone"},
        **fields,
    }


def _curated_samples(tmp_path: Path, repo_path: Path, *records: dict[str, Any]) -> dict[str, dict]:
    curation_dir = tmp_path / "curation"
    _write_curation(curation_dir, *records)
    options = _commit_options(tmp_path, repo_path, curation_dir=curation_dir)
    return {s["sample_id"]: s for s in export_samples(options)}


def test_human_curation_replaces_weak_input_keeps_original_and_makes_the_sample_eval_ready(
    tmp_path: Path, repo_with_mixed_commits: dict[str, Any]
) -> None:
    sample_id = f"owner/repo#commit-{repo_with_mixed_commits['plain_two'][:10]}"

    sample = _curated_samples(
        tmp_path,
        repo_with_mixed_commits["repo"],
        _record(
            sample_id,
            input={"title": "补全后的标题", "body": "补全后的任务描述"},
            check_recipe="pytest",
            notes="复核过",
        ),
    )[sample_id]

    assert sample["input"]["provenance"] == "curated_human"
    assert sample["input"]["title"] == "补全后的标题"
    assert sample["input"]["body"] == "补全后的任务描述"
    assert sample["input"]["original"] == {
        "provenance": "commit_message",
        "title": "fix: plain two",
        "body": "",
    }
    assert sample["curation"]["check_recipe"] == "pytest"
    assert sample["curation"]["notes"] == ["复核过"]
    assert sample["labels"]["eval_ready"] is True


@pytest.mark.parametrize(
    ("by", "reviewed", "expected_provenance", "expected_ready"),
    [
        ({"kind": "agent", "model": "m"}, False, "curated_agent", False),
        ({"kind": "agent", "model": "m"}, True, "curated_agent_reviewed", True),
    ],
)
def test_agent_curation_is_trusted_only_after_a_human_reviewed_it(
    tmp_path: Path,
    repo_with_mixed_commits: dict[str, Any],
    by: dict[str, str],
    reviewed: bool,
    expected_provenance: str,
    expected_ready: bool,
) -> None:
    sample_id = f"owner/repo#commit-{repo_with_mixed_commits['plain_two'][:10]}"

    sample = _curated_samples(
        tmp_path,
        repo_with_mixed_commits["repo"],
        _record(sample_id, by=by, reviewed_by_human=reviewed, input={"body": "Agent 补的任务描述"}),
    )[sample_id]

    assert sample["input"]["provenance"] == expected_provenance
    assert sample["labels"]["eval_ready"] is expected_ready
    assert sample["curation"]["last_by"]["model"] == "m"


def test_latest_entry_wins_per_field_and_exclude_blocks_eval_ready(
    tmp_path: Path, repo_with_mixed_commits: dict[str, Any]
) -> None:
    sample_id = f"owner/repo#commit-{repo_with_mixed_commits['plain_two'][:10]}"

    sample = _curated_samples(
        tmp_path,
        repo_with_mixed_commits["repo"],
        _record(sample_id, at="2026-10-22T08:00:00Z", input={"body": "旧描述"}),
        _record(sample_id, at="2026-10-22T09:00:00Z", input={"body": "新描述"}),
        _record(sample_id, at="2026-10-22T10:00:00Z", exclude=True, notes="题目有歧义"),
    )[sample_id]

    assert sample["input"]["body"] == "新描述"
    assert sample["curation"]["entry_count"] == 3
    assert sample["labels"]["excluded"] is True
    assert sample["labels"]["eval_ready"] is False


def test_curated_text_is_redacted_and_uncurated_samples_are_untouched(
    tmp_path: Path, repo_with_mixed_commits: dict[str, Any]
) -> None:
    plain_two_id = f"owner/repo#commit-{repo_with_mixed_commits['plain_two'][:10]}"
    plain_one_id = f"owner/repo#commit-{repo_with_mixed_commits['plain_one'][:10]}"

    samples = _curated_samples(
        tmp_path,
        repo_with_mixed_commits["repo"],
        _record(plain_two_id, input={"body": "联系 a.b@corp.com 修一下"}),
    )

    assert "a.b@corp.com" not in samples[plain_two_id]["input"]["body"]
    assert samples[plain_two_id]["input"]["redaction_count"] == 1
    assert "curation" not in samples[plain_one_id]
    assert samples[plain_one_id]["input"]["provenance"] == "commit_message"


def test_unknown_samples_and_invalid_lines_are_reported_not_fatal(
    tmp_path: Path, repo_with_mixed_commits: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    curation_dir = tmp_path / "curation"
    _write_curation(
        curation_dir,
        _record("owner/repo#commit-doesnotexist", input={"body": "x"}),
        {"schema_version": 99, "sample_id": "x", "by": {"kind": "human"}},
        _record("x", by={"kind": "robot"}),
        _record("x", input={"unknown_field": "x"}),
    )

    load = load_curation_entries(curation_dir)
    export_samples(
        _commit_options(tmp_path, repo_with_mixed_commits["repo"], curation_dir=curation_dir)
    )

    assert load.skipped_lines == 3
    assert set(load.entries_by_sample) == {"owner/repo#commit-doesnotexist"}
    assert "找不到样本的补全 1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "record",
    [
        "not a dict",
        {"schema_version": 1, "sample_id": 5, "by": {"kind": "human"}},
        {"schema_version": 1, "sample_id": "x", "by": {"kind": "human"}, "exclude": "yes"},
        {"schema_version": 1, "sample_id": "x", "by": {"kind": "human"}, "input": {"body": 3}},
    ],
)
def test_malformed_curation_records_are_rejected(record: Any) -> None:
    assert parse_curation_record(record) is None


# --- 待补全清单 --------------------------------------------------------------


def test_worklist_lists_only_weak_development_samples_with_commits(
    tmp_path: Path, repo_with_mixed_commits: dict[str, Any]
) -> None:
    plain_two_id = f"owner/repo#commit-{repo_with_mixed_commits['plain_two'][:10]}"
    samples = list(
        _curated_samples(
            tmp_path,
            repo_with_mixed_commits["repo"],
            _record(plain_two_id, input={"body": "补全了"}),
        ).values()
    )
    samples.append(
        {
            **samples[0],
            "sample_id": "owner/repo#commit-maint",
            "labels": {**samples[0]["labels"], "task_kind": "maintenance"},
        }
    )

    worklist = build_curation_worklist(samples)

    assert [item["current_input"]["title"] for item in worklist] == ["feat: plain one"]
    assert worklist[0]["commit_subjects"] == ["feat: plain one"]
    assert worklist[0]["base_sha"] and worklist[0]["final_sha"]


def test_cli_writes_samples_and_worklist_files(
    tmp_path: Path, repo_with_mixed_commits: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    output_path = tmp_path / "out.jsonl"
    worklist_path = tmp_path / "worklist.jsonl"

    exit_code = main(
        [
            "--db", str(tmp_path / "missing.db"),
            "--commits-repo", str(repo_with_mixed_commits["repo"]),
            "--output", str(output_path),
            "--curation-worklist", str(worklist_path),
        ]
    )  # fmt: skip

    assert exit_code == 0
    assert len(output_path.read_text(encoding="utf-8").splitlines()) == 2
    assert len(worklist_path.read_text(encoding="utf-8").splitlines()) == 2
    assert "待补全清单: 2 条" in capsys.readouterr().err


def test_default_branch_is_read_from_the_remote_declaration(tmp_path: Path) -> None:
    """默认分支不叫 main/master 时（如 hugo、trunk），必须读远程声明才找得到。"""
    remote_path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote_path)], check=True, capture_output=True
    )
    repo_path = init_git_repo(tmp_path / "proj")
    create_commit(repo_path, "root")
    _git(repo_path, "branch", "-m", "trunk")
    _git(repo_path, "remote", "add", "up", str(remote_path))
    _git(repo_path, "push", "-q", "up", "trunk")
    _git(repo_path, "remote", "set-head", "up", "trunk")

    assert find_base_branch(repo_path=repo_path) == "up/trunk"


def test_default_branch_falls_back_to_common_names_without_a_remote_head(tmp_path: Path) -> None:
    repo_path = init_git_repo(tmp_path / "proj")
    create_commit(repo_path, "root")

    assert find_base_branch(repo_path=repo_path) == "main"

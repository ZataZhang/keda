"""样本归档（``archive``）与 ``--archive-dir`` 的行为测试。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.dataset_export.archive import (
    MANIFEST_FILENAME,
    archive_samples,
    sample_file_path,
    serialize_sample,
)
from scripts.dataset_export.cli import main
from tests.support.agent_runner import create_commit, init_git_repo


def _sample(
    sample_id: str, *, origin: str = "git_commit", repo_id: str = "owner/repo", **extra: Any
) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "origin": origin,
        "repo_id": repo_id,
        "input": {"title": "t"},
        **extra,
    }


def test_sample_file_path_is_safe_and_grouped_by_origin_and_repo(tmp_path: Path) -> None:
    path = sample_file_path(tmp_path, _sample("owner/repo#pr-9", origin="github_pr"))

    assert path == tmp_path / "raw" / "github_pr" / "owner_repo" / "owner_repo_pr-9.json"


@pytest.mark.parametrize("repo_id", ["..", "../../etc", "a/../../b", ""])
def test_sample_file_path_never_leaves_the_archive_directory(tmp_path: Path, repo_id: str) -> None:
    path = sample_file_path(tmp_path, _sample("x#1", repo_id=repo_id))

    assert path.resolve().is_relative_to((tmp_path / "raw").resolve())


def test_serialization_is_stable_regardless_of_key_order() -> None:
    first = {"b": 1, "a": {"y": 2, "x": 3}}
    second = {"a": {"x": 3, "y": 2}, "b": 1}

    assert serialize_sample(first) == serialize_sample(second)
    assert serialize_sample(first).endswith("\n")


def test_archive_reports_added_then_unchanged_then_updated(tmp_path: Path) -> None:
    samples = [_sample("a#1"), _sample("a#2")]

    first = archive_samples(samples, tmp_path)
    second = archive_samples(samples, tmp_path)
    changed = archive_samples(
        [_sample("a#1", input={"title": "changed"}), _sample("a#2")], tmp_path
    )

    assert (first.added, first.updated, first.unchanged) == (2, 0, 0)
    assert (second.added, second.updated, second.unchanged) == (0, 0, 2)
    assert (changed.added, changed.updated, changed.unchanged) == (0, 1, 1)
    updated_file = sample_file_path(tmp_path, _sample("a#1"))
    assert json.loads(updated_file.read_text(encoding="utf-8"))["input"] == {"title": "changed"}


def test_unchanged_samples_are_not_rewritten(tmp_path: Path) -> None:
    sample = _sample("a#1")
    archive_samples([sample], tmp_path)
    path = sample_file_path(tmp_path, sample)
    first_mtime = path.stat().st_mtime_ns

    archive_samples([sample], tmp_path)

    assert path.stat().st_mtime_ns == first_mtime


def test_archive_never_deletes_samples_whose_source_has_disappeared(tmp_path: Path) -> None:
    archive_samples([_sample("a#1"), _sample("a#2")], tmp_path)

    report = archive_samples([_sample("a#2")], tmp_path)

    assert sample_file_path(tmp_path, _sample("a#1")).is_file()
    assert report.total_by_origin == {"git_commit": 2}


def test_manifest_counts_by_origin_and_has_no_timestamps(tmp_path: Path) -> None:
    archive_samples(
        [_sample("a#1"), _sample("a#2", origin="github_pr"), _sample("a#3", origin="github_pr")],
        tmp_path,
    )

    manifest_text = (tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    assert manifest["total"] == 3
    assert manifest["total_by_origin"] == {"git_commit": 1, "github_pr": 2}
    assert "at" not in manifest and "time" not in " ".join(manifest)


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo_path), *args], check=True, capture_output=True)


def test_cli_archives_without_dumping_samples_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_path = init_git_repo(tmp_path / "proj")
    _git(repo_path, "remote", "add", "origin", "https://github.com/owner/repo.git")
    create_commit(repo_path, "root")
    create_commit(repo_path, "fix: a plain change")
    archive_dir = tmp_path / "datasets"
    arguments = [
        "--db",
        str(tmp_path / "missing.db"),
        "--commits-repo",
        str(repo_path),
        "--archive-dir",
        str(archive_dir),
    ]

    assert main(arguments) == 0
    first = capsys.readouterr()
    assert main(arguments) == 0
    second = capsys.readouterr()

    assert first.out == ""
    assert "归档: 新增 1，更新 0，未变 0" in first.err
    assert "归档: 新增 0，更新 0，未变 1" in second.err
    archived = list((archive_dir / "raw" / "git_commit" / "owner_repo").glob("*.json"))
    assert len(archived) == 1
    assert (
        json.loads(archived[0].read_text(encoding="utf-8"))["input"]["title"]
        == "fix: a plain change"
    )


def test_cli_still_writes_the_jsonl_when_both_output_and_archive_are_given(tmp_path: Path) -> None:
    repo_path = init_git_repo(tmp_path / "proj")
    create_commit(repo_path, "root")
    create_commit(repo_path, "fix: a plain change")
    output_path = tmp_path / "out.jsonl"

    main(
        [
            "--db", str(tmp_path / "missing.db"),
            "--commits-repo", str(repo_path),
            "--archive-dir", str(tmp_path / "datasets"),
            "--output", str(output_path),
        ]
    )  # fmt: skip

    assert len(output_path.read_text(encoding="utf-8").splitlines()) == 1

"""待补全清单的"改动上下文"（``worklist_context``）与其在清单里的接入测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.dataset_export import worklist_context
from scripts.dataset_export.curation import build_curation_worklist
from scripts.dataset_export.export_samples import ExportOptions, export_samples
from scripts.dataset_export.worklist_context import (
    change_area,
    collect_change_context,
    fetch_closing_issues,
    is_test_path,
    parse_closing_issue_numbers,
)
from tests.support.agent_runner import create_commit, init_git_repo


def _git(repo_path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_files(repo_path: Path, files: dict[str, str], message: str) -> tuple[str, str]:
    """写入文件并提交，返回 (提交前的 HEAD, 新提交)。"""
    before = _git(repo_path, "rev-parse", "HEAD")
    for relative_path, content in files.items():
        target = repo_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo_path, "add", ".")
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-q", "-m", message],
        check=True,
        capture_output=True,
    )
    return before, _git(repo_path, "rev-parse", "HEAD")


@pytest.fixture()
def repo_path(tmp_path: Path) -> Path:
    repo = init_git_repo(tmp_path / "proj")
    _git(repo, "remote", "add", "origin", "https://github.com/owner/repo.git")
    create_commit(repo, "root")
    _commit_files(
        repo,
        {
            "src/backend/app.py": "def existing_entry():\n    return 1\n",
            "tests/test_app.py": "from src.backend.app import existing_entry\n\n\ndef test_it():\n    assert existing_entry() == 1\n",
        },
        "base",
    )
    return repo


# --- 纯函数 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_app.py", True),
        ("src/pkg/test_helpers.py", True),
        ("frontend/components/button.test.tsx", True),
        ("frontend/__tests__/x.ts", True),
        ("src/backend/app.py", False),
        ("docs/guides/testing.md", False),
        ("src/latest_results.py", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README.md", "."),
        ("tests/test_app.py", "tests"),
        ("src/backend/app.py", "src/backend"),
        ("src/backend/core/use_cases/x.py", "src/backend"),
        ("docs/guides/a.md", "docs/guides"),
    ],
)
def test_change_area(path: str, expected: str) -> None:
    assert change_area(path) == expected


def test_closing_issue_numbers_are_parsed_deduplicated_and_ordered() -> None:
    texts = [
        "fix(x): thing",
        "Closes #12\n\nand also fixes: #7, resolved #12",
        None,
        "see #9 for context",
    ]

    assert parse_closing_issue_numbers(texts) == [12, 7]


def test_closing_issues_are_fetched_truncated_and_failures_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_fetch(*, repo_path: Path, args: list[str]) -> Any:
        number = int(args[2])
        return {"title": f"issue {number}", "body": "x" * 9000} if number == 5 else None

    monkeypatch.setattr(worklist_context, "fetch_gh_json", fake_fetch)

    issues = fetch_closing_issues(repo_path=tmp_path, numbers=[5, 6])

    assert [issue["number"] for issue in issues] == [5]
    assert len(issues[0]["body"]) == worklist_context.CLOSING_ISSUE_BODY_MAX_CHARS


# --- 改动上下文 -------------------------------------------------------------


def test_tests_that_use_only_existing_entry_points_are_testable(repo_path: Path) -> None:
    base, final = _commit_files(
        repo_path,
        {
            "src/backend/app.py": "def existing_entry():\n    return 2\n",
            "tests/test_app.py": "from src.backend.app import existing_entry\n\n\ndef test_it():\n    assert existing_entry() == 2\n",
        },
        "fix: change the result",
    )

    context = collect_change_context(repo_path=repo_path, base_sha=base, final_sha=final)

    assert context["check_recipe_hint"] == "tests_use_existing_entry_points"
    assert context["new_symbols_referenced_by_tests"] == []
    assert context["test_files"] == ["tests/test_app.py"]
    assert context["files_by_area"] == {"src/backend": 1, "tests": 1}


def test_tests_that_import_new_symbols_or_modules_are_flagged(repo_path: Path) -> None:
    base, final = _commit_files(
        repo_path,
        {
            "src/backend/brand_new_module.py": "def brand_new_helper():\n    return 3\n\n\nTHRESHOLD_SECONDS = 5\n",
            "tests/test_new.py": "from src.backend.brand_new_module import brand_new_helper, THRESHOLD_SECONDS\n\n\ndef test_new():\n    assert brand_new_helper() == 3\n",
        },
        "feat: add a helper",
    )

    context = collect_change_context(repo_path=repo_path, base_sha=base, final_sha=final)

    assert context["check_recipe_hint"] == "tests_use_new_symbols"
    assert {"brand_new_helper", "THRESHOLD_SECONDS", "brand_new_module"} <= set(
        context["new_symbols_referenced_by_tests"]
    )
    assert context["new_files"] == ["src/backend/brand_new_module.py"]


def test_change_without_tests_and_change_only_in_tests_have_their_own_hints(
    repo_path: Path,
) -> None:
    docs_base, docs_final = _commit_files(repo_path, {"docs/guides/a.md": "hello\n"}, "docs: a")
    tests_base, tests_final = _commit_files(
        repo_path, {"tests/test_app.py": "def test_other():\n    assert True\n"}, "test: tweak"
    )

    docs = collect_change_context(repo_path=repo_path, base_sha=docs_base, final_sha=docs_final)
    only_tests = collect_change_context(
        repo_path=repo_path, base_sha=tests_base, final_sha=tests_final
    )

    assert docs["check_recipe_hint"] == "no_tests"
    assert only_tests["check_recipe_hint"] == "only_test_changes"


def test_unreachable_commits_give_no_context(repo_path: Path) -> None:
    assert (
        collect_change_context(repo_path=repo_path, base_sha="0" * 40, final_sha="1" * 40) is None
    )


# --- 在清单里的接入 -----------------------------------------------------------


def _worklist(tmp_path: Path, repo_path: Path, **kwargs: Any) -> list[dict[str, Any]]:
    options = ExportOptions(
        db_path=tmp_path / "missing.db",
        output_path=None,
        commit_repo_paths=(repo_path,),
        home_path=tmp_path / "home",
    )
    samples = export_samples(options)
    return build_curation_worklist(samples, **kwargs)


def test_worklist_items_carry_change_context_and_closing_issues(
    tmp_path: Path, repo_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit_files(
        repo_path,
        {"src/backend/app.py": "def existing_entry():\n    return 9\n"},
        "fix: change it\n\nCloses #42",
    )
    monkeypatch.setattr(
        worklist_context,
        "fetch_gh_json",
        lambda *, repo_path, args: {"title": "原始需求", "body": "需求正文"},
    )

    item = next(
        entry
        for entry in _worklist(
            tmp_path, repo_path, repo_paths={"owner/repo": repo_path}, use_gh=True
        )
        if entry["current_input"]["title"] == "fix: change it"
    )

    assert item["closing_issue_numbers"] == [42]
    assert item["closing_issues"] == [{"number": 42, "title": "原始需求", "body": "需求正文"}]
    assert item["change_context"]["files_by_area"] == {"src/backend": 1}
    assert item["change_context"]["check_recipe_hint"] == "no_tests"


def test_worklist_without_repo_paths_keeps_the_basic_shape(tmp_path: Path, repo_path: Path) -> None:
    _commit_files(
        repo_path,
        {"src/backend/app.py": "def existing_entry():\n    return 8\n"},
        "fix: x\n\nFixes #7",
    )

    items = _worklist(tmp_path, repo_path)

    assert items
    assert all("change_context" not in item and "closing_issues" not in item for item in items)
    assert any(item["closing_issue_numbers"] == [7] for item in items)

"""判分器校准（``calibrate``）的行为测试：真实的临时 git 仓库 + 真实的 pytest 子进程。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.dataset_export import calibrate
from scripts.dataset_export.calibrate import (
    STATUS_CALIBRATED,
    STATUS_COUPLED,
    STATUS_ERROR,
    STATUS_GOLD_FAILED,
    STATUS_NO_FAIL_TO_PASS,
    STATUS_NO_TESTS,
    calibrate_all,
    calibrate_sample,
    calibration_path,
    is_up_to_date,
    load_candidates,
    main,
    plan_calibration,
)
from tests.support.agent_runner import create_commit, init_git_repo

PYTEST_COMMAND = [sys.executable, "-m", "pytest"]
PYPROJECT = '[tool.pytest.ini_options]\npythonpath = ["."]\n'
APP_BUGGY = "def add(a, b):\n    return a - b\n\n\ndef untouched():\n    return 1\n"
APP_FIXED = "def add(a, b):\n    return a + b\n\n\ndef untouched():\n    return 1\n"


def _git(repo_path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo_path: Path, files: dict[str, str], message: str) -> tuple[str, str]:
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
    create_commit(repo, "root")
    _commit(
        repo,
        {
            "pyproject.toml": PYPROJECT,
            "app.py": APP_BUGGY,
            "tests/test_existing.py": "from app import untouched\n\n\ndef test_existing():\n    assert untouched() == 1\n",
        },
        "base",
    )
    return repo


def _sample(sample_id: str, base: str, final: str, repo_id: str = "o/r") -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "repo_id": repo_id,
        "code": {"base_sha": base, "final_sha": final},
        "labels": {"eval_ready": True},
    }


def _calibrate(repo_path: Path, base: str, final: str) -> dict[str, Any]:
    return calibrate_sample(
        _sample("o/r#x", base, final),
        repo_path=repo_path,
        command=PYTEST_COMMAND,
        timeout_seconds=120,
    )


def test_a_behavioral_fix_with_a_new_test_is_calibrated(repo_path: Path) -> None:
    base, final = _commit(
        repo_path,
        {
            "app.py": APP_FIXED,
            "tests/test_add.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        },
        "fix: add subtracted",
    )

    record = _calibrate(repo_path, base, final)

    assert record["status"] == STATUS_CALIBRATED
    assert record["fail_to_pass"] == ["tests.test_add::test_add"]
    assert record["black_box_fail_to_pass"] == ["tests.test_add::test_add"]
    assert record["test_files"] == ["tests/test_add.py"]
    assert record["counts"]["gold"] == {"passed": 1}


def test_a_test_that_fails_only_because_a_new_name_is_missing_is_coupled(repo_path: Path) -> None:
    base, final = _commit(
        repo_path,
        {
            "helper.py": "def brand_new_helper():\n    return 7\n",
            "tests/test_helper.py": "from helper import brand_new_helper\n\n\ndef test_helper():\n    assert brand_new_helper() == 7\n",
        },
        "feat: add helper",
    )

    record = _calibrate(repo_path, base, final)

    assert record["status"] == STATUS_COUPLED
    assert record["fail_to_pass"] == ["tests.test_helper::test_helper"]
    assert record["black_box_fail_to_pass"] == []


def test_only_the_behavioral_tests_count_as_black_box_when_a_change_has_both(
    repo_path: Path,
) -> None:
    base, final = _commit(
        repo_path,
        {
            "app.py": APP_FIXED,
            "helper.py": "def brand_new_helper():\n    return 7\n",
            "tests/test_add.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
            "tests/test_helper.py": "from helper import brand_new_helper\n\n\ndef test_helper():\n    assert brand_new_helper() == 7\n",
        },
        "fix and feat together",
    )

    record = _calibrate(repo_path, base, final)

    assert record["status"] == STATUS_CALIBRATED
    assert set(record["fail_to_pass"]) == {
        "tests.test_add::test_add",
        "tests.test_helper::test_helper",
    }
    assert record["black_box_fail_to_pass"] == ["tests.test_add::test_add"]


def test_a_test_that_already_passes_before_the_change_cannot_discriminate(repo_path: Path) -> None:
    base, final = _commit(
        repo_path,
        {
            "tests/test_untouched.py": "from app import untouched\n\n\ndef test_untouched_again():\n    assert untouched() == 1\n"
        },
        "test: add a test that already passes",
    )

    record = _calibrate(repo_path, base, final)

    assert record["status"] == STATUS_NO_FAIL_TO_PASS
    assert record["pass_to_pass"] == ["tests.test_untouched::test_untouched_again"]


def test_a_change_without_python_tests_is_not_judgeable(repo_path: Path) -> None:
    base, final = _commit(repo_path, {"app.py": APP_BUGGY + "# a comment\n"}, "chore: comment")

    record = _calibrate(repo_path, base, final)

    assert record["status"] == STATUS_NO_TESTS and record["test_files"] == []


def test_a_judge_that_fails_even_with_the_original_fix_is_rejected(repo_path: Path) -> None:
    base, final = _commit(
        repo_path,
        {"tests/test_broken.py": "def test_broken():\n    assert False\n"},
        "test: broken judge",
    )

    record = _calibrate(repo_path, base, final)

    assert record["status"] == STATUS_GOLD_FAILED
    assert record["gold_failures"] == ["tests.test_broken::test_broken"]


def test_missing_commits_give_an_error_record_instead_of_raising(repo_path: Path) -> None:
    record = _calibrate(repo_path, "0" * 40, "1" * 40)

    assert record["status"] == STATUS_ERROR and record["reasons"]


def test_plan_follows_renames_deletions_and_support_files(repo_path: Path) -> None:
    base, final = _commit(
        repo_path,
        {
            "tests/support.py": "VALUE = 1\n",
            "tests/test_new.py": "def test_new():\n    assert True\n",
        },
        "add support and test",
    )
    _git(repo_path, "mv", "tests/test_new.py", "tests/test_renamed.py")
    (repo_path / "tests" / "test_existing.py").unlink()
    _git(repo_path, "add", "-A")
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-q", "-m", "rename and delete"],
        check=True,
        capture_output=True,
    )
    final = _git(repo_path, "rev-parse", "HEAD")

    plan = plan_calibration(repo_path=repo_path, base_sha=base, final_sha=final)

    assert plan.test_files == ["tests/test_renamed.py"]
    assert ("D", "tests/test_existing.py") in plan.support_changes
    assert ("A", "tests/support.py") in plan.support_changes


def test_calibration_never_touches_the_source_repository_or_the_real_home(
    repo_path: Path, tmp_path: Path
) -> None:
    base, final = _commit(
        repo_path,
        {
            "app.py": APP_FIXED,
            "tests/test_add.py": "import os\nfrom pathlib import Path\nfrom app import add\n\n\ndef test_add():\n    Path(os.path.expanduser('~/written-by-test')).write_text('x')\n    assert add(1, 2) == 3\n",
        },
        "fix: add",
    )
    status_before = _git(repo_path, "status", "--porcelain")
    worktrees_before = _git(repo_path, "worktree", "list")

    record = _calibrate(repo_path, base, final)

    assert record["status"] == STATUS_CALIBRATED
    assert _git(repo_path, "status", "--porcelain") == status_before
    assert _git(repo_path, "worktree", "list") == worktrees_before
    assert not (Path.home() / "written-by-test").exists()


def test_calibrate_all_writes_records_skips_up_to_date_ones_and_counts_statuses(
    repo_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, final = _commit(
        repo_path,
        {
            "app.py": APP_FIXED,
            "tests/test_add.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        },
        "fix: add",
    )
    sample = _sample("o/r#fix-add", base, final)
    out_dir = tmp_path / "calibration"

    first = calibrate_all(
        [sample], repo_paths={"o/r": repo_path}, out_dir=out_dir, command=PYTEST_COMMAND, jobs=1
    )
    second = calibrate_all(
        [sample], repo_paths={"o/r": repo_path}, out_dir=out_dir, command=PYTEST_COMMAND, jobs=1
    )

    record = json.loads(calibration_path(out_dir, sample).read_text(encoding="utf-8"))
    assert first == {STATUS_CALIBRATED: 1} == second
    assert record["status"] == STATUS_CALIBRATED and record["schema_version"] == 1
    assert "已有记录，跳过" in capsys.readouterr().out
    assert is_up_to_date(calibration_path(out_dir, sample), sample)


def test_a_sample_whose_repository_has_no_path_is_an_error_not_a_crash(tmp_path: Path) -> None:
    counts = calibrate_all(
        [_sample("o/r#x", "a", "b", repo_id="unknown")],
        repo_paths={},
        out_dir=tmp_path / "c",
        jobs=1,
    )

    assert counts == {STATUS_ERROR: 1}


def test_load_candidates_filters_by_eval_ready_and_by_id(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw" / "x" / "y"
    raw_dir.mkdir(parents=True)
    for sample_id, ready in (("a", True), ("b", False)):
        (raw_dir / f"{sample_id}.json").write_text(
            json.dumps({"sample_id": sample_id, "labels": {"eval_ready": ready}}), encoding="utf-8"
        )

    assert [
        s["sample_id"]
        for s in load_candidates(tmp_path / "raw", only_eval_ready=True, sample_ids=[])
    ] == ["a"]
    assert len(load_candidates(tmp_path / "raw", only_eval_ready=False, sample_ids=[])) == 2
    assert [
        s["sample_id"]
        for s in load_candidates(tmp_path / "raw", only_eval_ready=True, sample_ids=["b"])
    ] == ["b"]


def test_cli_main_calibrates_the_candidates_end_to_end(
    repo_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, final = _commit(
        repo_path,
        {
            "app.py": APP_FIXED,
            "tests/test_add.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        },
        "fix: add",
    )
    raw_dir = tmp_path / "raw" / "git_commit" / "o_r"
    raw_dir.mkdir(parents=True)
    (raw_dir / "x.json").write_text(json.dumps(_sample("o/r#fix", base, final)), encoding="utf-8")

    exit_code = main(
        [
            "--raw-dir", str(tmp_path / "raw"),
            "--out-dir", str(tmp_path / "calibration"),
            "--repo", f"o/r={repo_path}",
            "--command", f"{sys.executable} -m pytest",
            "--jobs", "1",
        ]
    )  # fmt: skip

    assert exit_code == 0
    assert '"calibrated": 1' in capsys.readouterr().out
    assert calibrate.calibration_path(tmp_path / "calibration", {"sample_id": "o/r#fix"}).is_file()


def test_a_conftest_that_cannot_load_before_the_change_makes_the_task_coupled_not_an_error(
    repo_path: Path,
) -> None:
    """改动带来的 conftest 依赖这次新增的名字：改动前 pytest 在加载阶段就退出、不出报告。这是最彻底的耦合，不是环境错误。"""
    base, final = _commit(
        repo_path,
        {
            "helper.py": "def brand_new_helper():\n    return 7\n",
            "tests/conftest.py": "from helper import brand_new_helper  # noqa: F401\n",
            "tests/test_uses_infra.py": "def test_infra():\n    assert True\n",
        },
        "feat: tests infrastructure depends on a new helper",
    )

    record = _calibrate(repo_path, base, final)

    assert record["status"] == STATUS_COUPLED
    assert record["black_box_fail_to_pass"] == []
    assert record["fail_to_pass"] == ["tests.test_uses_infra::test_infra"]
    assert any("conftest" in reason for reason in record["reasons"])


def test_the_record_keeps_the_reason_each_test_failed_before_the_change(repo_path: Path) -> None:
    base, final = _commit(
        repo_path,
        {
            "app.py": APP_FIXED,
            "helper.py": "def brand_new_helper():\n    return 7\n",
            "tests/test_add.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
            "tests/test_helper.py": "from helper import brand_new_helper\n\n\ndef test_helper():\n    assert brand_new_helper() == 7\n",
        },
        "fix and feat together",
    )

    detail = _calibrate(repo_path, base, final)["fail_to_pass_detail"]

    assert detail["tests.test_add::test_add"]["kind"] == "behavioral"
    assert "assert" in detail["tests.test_add::test_add"]["noop_message"].lower()
    assert detail["tests.test_helper::test_helper"]["kind"] == "missing_symbol"
    assert "No module named" in detail["tests.test_helper::test_helper"]["noop_message"]


@pytest.mark.parametrize(
    ("message", "expected_kind"),
    [
        ("fixture 'brand_new_fixture' not found", "missing_symbol"),
        ("ImportError: cannot import name 'x' from 'y'", "missing_symbol"),
        ("AttributeError: module 'm' has no attribute 'new_name'", "missing_symbol"),
        ("TypeError: f() got an unexpected keyword argument 'new_option'", "missing_symbol"),
        ("AssertionError: assert 1 == 2", "behavioral"),
        ("assert 'FINAL' in ''", "behavioral"),
    ],
)
def test_failure_messages_are_classified_by_whether_a_new_name_is_missing(
    tmp_path: Path, message: str, expected_kind: str
) -> None:
    xml_path = tmp_path / "junit.xml"
    xml_path.write_text(
        '<testsuites><testsuite><testcase classname="tests.t" name="test_a">'
        f'<failure message="{message}">{message}</failure></testcase></testsuite></testsuites>',
        encoding="utf-8",
    )

    outcome = calibrate.parse_junit(xml_path)["tests.t::test_a"]

    assert outcome.failure_kind == expected_kind
    assert outcome.message == message

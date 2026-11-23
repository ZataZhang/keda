"""从实际 kc 入口验证进程级校准、报告和真实模型授权。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def tiny_evaluation_repo(tmp_path: Path) -> Path:
    """建立一题可真的失败和通过的独立 Git 快照。"""
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = ("git", "-C", str(repo))
    subprocess.run((*git, "init", "-q", "-b", "main"), check=True)
    value_module = repo / "value.py"
    value_module.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run((*git, "add", "value.py"), check=True)
    subprocess.run(
        (
            *git,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "baseline",
        ),
        check=True,
    )
    value_module.write_text("VALUE = 2\n", encoding="utf-8")
    gold_patch = subprocess.check_output((*git, "diff", "--unified=0"), encoding="utf-8")
    value_module.write_text("VALUE = 1\n", encoding="utf-8")
    suite_dir = repo / "tasks/evaluation-suites"
    case_dir = suite_dir / "cli-fixture.d/update-value"
    checks = case_dir / "held_out/tests"
    checks.mkdir(parents=True)
    (case_dir / "gold.patch").write_text(gold_patch, encoding="utf-8")
    (checks / "test_value.py").write_text(
        "from pathlib import Path\n\n"
        "def test_value():\n"
        "    source = Path(__file__).resolve().parents[1] / 'value.py'\n"
        "    assert source.read_text(encoding='utf-8').strip() == 'VALUE = 2'\n",
        encoding="utf-8",
    )
    (suite_dir / "cli-fixture.yaml").write_text(
        """id: cli-fixture
version: 1.0.0
repo_path: .
base_ref: main
cases:
- id: update-value
  prompt: Set VALUE to 2.
  check_recipe: pytest
  check_targets: [tests/test_value.py]
  held_out: [tests/test_value.py]
  gold_patch: gold.patch
""",
        encoding="utf-8",
    )
    return repo


def _kc(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    # 直接运行当前环境的安装入口，cwd 刻意不在 Keda 源仓库。
    executable = Path(sys.executable).with_name("kc")
    return subprocess.run(
        [str(executable), "eval", *arguments],
        cwd=cwd,
        env={
            **os.environ,
            "KEDACODE_NO_UPDATE_CHECK": "1",
            "PATH": f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}",
        },
        capture_output=True,
        encoding="utf-8",
        timeout=70,
        check=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="评测校准依赖 POSIX 进程组")
def test_kc_eval_calibration_and_report(tmp_path, tiny_evaluation_repo):
    repo = str(tiny_evaluation_repo)
    calibration_dir = tmp_path / "calibration"
    listing = _kc(tmp_path, "suites", "--repo", repo, "--json")
    assert listing.returncode == 0, listing.stderr
    assert json.loads(listing.stdout)["suites"][0]["suite_id"] == "cli-fixture"

    calibration = _kc(
        tmp_path,
        "calibrate",
        "--repo",
        repo,
        "--suite",
        "cli-fixture",
        "--case",
        "update-value",
        "--output-dir",
        str(calibration_dir),
        "--concurrency",
        "2",
        "--json",
    )
    assert calibration.returncode == 0, calibration.stderr
    summary = json.loads(calibration.stdout)
    assert summary["complete"] and summary["completed_cells"] == 6
    assert summary["arms"]["gold"]["accepted_patches"] == 3
    assert summary["arms"]["noop"]["valid_scores"] == 3
    assert summary["arms"]["noop"]["accepted_patches"] == 0
    assert all(not row["execution"]["agent_calls"] for row in summary["records"])

    report = _kc(tmp_path, "report", str(calibration_dir), "--json")
    assert report.returncode == 0, report.stderr
    assert json.loads(report.stdout) == summary
    markdown_report = (calibration_dir / "report.md").read_text(encoding="utf-8")
    assert markdown_report.startswith("# 题库校准\n")
    assert "不调用模型" in markdown_report
    assert (tiny_evaluation_repo / "value.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    refusal = _kc(
        tmp_path,
        "run",
        "--repo",
        repo,
        "--suite",
        "cli-fixture",
        "--calibration",
        str(calibration_dir),
        "--output-dir",
        str(tmp_path / "live"),
        "--json",
    )
    assert refusal.returncode == 2
    assert not refusal.stdout
    assert "--live" in json.loads(refusal.stderr)["message"]
    assert not (tmp_path / "live").exists()

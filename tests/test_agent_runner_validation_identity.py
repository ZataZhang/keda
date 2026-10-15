"""Realistic Validation evidence identity regression tests."""

from __future__ import annotations

from pathlib import Path

from backend.core.use_cases.agent_runner_validation_identity import (
    PrChangeFingerprintRequest,
    compute_pr_change_fingerprint,
)
from backend.infrastructure.process_runner import SubprocessRunner


def _git(repo_path: Path, *arguments: str) -> str:
    """Run a Git command in a temporary test repository."""
    result = SubprocessRunner().run(["git", *arguments], cwd=repo_path)
    return result.stdout.strip()


def _commit_all(repo_path: Path, message: str) -> str:
    """Commit all current fixture files and return the commit SHA."""
    _git(repo_path, "add", "--all")
    _git(repo_path, "commit", "-m", message)
    return _git(repo_path, "rev-parse", "HEAD")


def _fingerprint(repo_path: Path, base_sha: str, head_sha: str, runner: SubprocessRunner) -> str:
    """Compute the product-change identity used by the gate."""
    fingerprint = compute_pr_change_fingerprint(
        PrChangeFingerprintRequest(
            repo_path=repo_path,
            remote="origin",
            base_sha=base_sha,
            head_sha=head_sha,
            process_runner=runner,
        )
    )
    assert fingerprint is not None
    return fingerprint


def test_pr_change_fingerprint_survives_main_rebase_and_evidence_updates(
    tmp_path: Path,
) -> None:
    """Main and evidence-package changes leave evidence bound to the same PR contribution."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    runner = SubprocessRunner()
    _git(repo_path, "init", "--initial-branch=main")
    _git(repo_path, "config", "user.email", "test@example.com")
    _git(repo_path, "config", "user.name", "Validation identity test")

    (repo_path / "base.txt").write_text("base\n", encoding="utf-8")
    _commit_all(repo_path, "base")
    _git(repo_path, "switch", "-c", "issue-42")
    (repo_path / "product.py").write_text("value = 1\n", encoding="utf-8")
    (repo_path / "tasks/archive").mkdir(parents=True)
    (repo_path / "tasks/archive/demo.md").write_text("PRD record v1\n", encoding="utf-8")
    (repo_path / "tasks/evidence/demo").mkdir(parents=True)
    (repo_path / "tasks/evidence/demo/evidence.json").write_text("{}\n", encoding="utf-8")
    old_head_sha = _commit_all(repo_path, "feature and records")

    _git(repo_path, "switch", "main")
    (repo_path / "main-progress.txt").write_text("main advanced\n", encoding="utf-8")
    new_base_sha = _commit_all(repo_path, "advance main")
    old_branch_fingerprint = _fingerprint(repo_path, new_base_sha, old_head_sha, runner)

    _git(repo_path, "switch", "issue-42")
    _git(repo_path, "rebase", "main")
    rebased_head_sha = _git(repo_path, "rev-parse", "HEAD")
    rebased_fingerprint = _fingerprint(repo_path, new_base_sha, rebased_head_sha, runner)
    assert rebased_head_sha != old_head_sha
    assert rebased_fingerprint == old_branch_fingerprint

    (repo_path / "tasks/evidence/demo/evidence.json").write_text(
        '{"updated": true}\n', encoding="utf-8"
    )
    record_only_head_sha = _commit_all(repo_path, "update validation records")
    record_only_fingerprint = _fingerprint(repo_path, new_base_sha, record_only_head_sha, runner)
    assert record_only_fingerprint == old_branch_fingerprint

    (repo_path / "tasks/archive/demo.md").write_text("PRD record v2\n", encoding="utf-8")
    changed_prd_head_sha = _commit_all(repo_path, "change acceptance record")
    changed_prd_fingerprint = _fingerprint(repo_path, new_base_sha, changed_prd_head_sha, runner)
    assert changed_prd_fingerprint != old_branch_fingerprint

    (repo_path / "product.py").write_text("value = 2\n", encoding="utf-8")
    changed_head_sha = _commit_all(repo_path, "change feature behavior")
    changed_fingerprint = _fingerprint(repo_path, new_base_sha, changed_head_sha, runner)
    assert changed_fingerprint != changed_prd_fingerprint

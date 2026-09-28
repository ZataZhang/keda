"""验证 IAR 与 PRD 看板的 Issue 关联和活动锁生命周期。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core.use_cases.agent_runner_prd_activity import (
    PrdActivityConflictError,
    PrdActivityLease,
)


def _load_status_module():
    script_dir = Path(__file__).resolve().parents[1] / "scripts/shared/just"
    sys.path.insert(0, str(script_dir))
    module_spec = importlib.util.spec_from_file_location("prd_status", script_dir / "prd_status.py")
    assert module_spec is not None and module_spec.loader is not None
    status_module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = status_module
    module_spec.loader.exec_module(status_module)
    return status_module


def test_issue_branch_matches_explicit_prd_link(tmp_path: Path) -> None:
    """PRD 的唯一 Issue 锚点可定位 IAR worktree，slug 分支仍优先。"""
    status_module = _load_status_module()
    prd_path = tmp_path / "P1-FEAT-20260928-example.md"
    prd_path.write_text(
        "# Example\n- GitHub Issue: https://github.com/acme/repo/issues/39\n",
        encoding="utf-8",
    )
    issue_worktree = tmp_path / "issue-39"
    issue_branches = [("issue-39", issue_worktree)]
    assert status_module.match_worktree_for_prd(issue_branches, "example", prd_path) == (
        "issue-39",
        issue_worktree,
    )
    slug_worktree = tmp_path / "example"
    both_branches = [("issue-39", issue_worktree), ("feat/example", slug_worktree)]
    assert status_module.match_worktree_for_prd(both_branches, "example", prd_path) == (
        "feat/example",
        slug_worktree,
    )


def test_iar_activity_lock_visible_to_status(tmp_path: Path) -> None:
    """真实锁脚本创建、续期和释放的锁可被看板读取。"""
    source_root = Path(__file__).resolve().parents[1]
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-q", str(repo_path)], check=True)
    script_dir = repo_path / "scripts/shared/just"
    script_dir.mkdir(parents=True)
    shutil.copy2(source_root / "scripts/shared/just/prd_lock.py", script_dir / "prd_lock.py")
    pending_dir = repo_path / "tasks/pending"
    pending_dir.mkdir(parents=True)
    prd_name = "P1-FEAT-20260928-example.md"
    (pending_dir / prd_name).write_text("# Example\n", encoding="utf-8")

    lease = PrdActivityLease(repo_path, f"tasks/pending/{prd_name}", 39, "qoder")
    lease.start()
    lock_path = repo_path / "tasks/evidence/P1-FEAT-20260928-example/active.lock"
    try:
        lock_metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert lock_metadata["ai_tool"] == "iar/qoder"
        assert lock_metadata["branch"] == "issue-39"
        assert lock_metadata["heartbeat_at"]
    finally:
        lease.close()
    assert not lock_path.exists()


def test_iar_does_not_run_through_existing_prd_lock(tmp_path: Path) -> None:
    """已有 PRD 锁时拒绝并发执行，且不改写原锁。"""
    repo_path = tmp_path / "repo"
    prd_path = repo_path / "tasks/pending/P1-FEAT-20260928-example.md"
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text("# Example\n", encoding="utf-8")
    lock_script = repo_path / "scripts/shared/just/prd_lock.py"
    lock_script.parent.mkdir(parents=True)
    lock_script.write_text("# existing script\n", encoding="utf-8")
    lock_path = repo_path / "tasks/evidence/P1-FEAT-20260928-example/active.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"ai_tool": "just"}', encoding="utf-8")

    lease = PrdActivityLease(repo_path, "tasks/pending/P1-FEAT-20260928-example.md", 39, "qoder")
    with pytest.raises(PrdActivityConflictError):
        lease.start()
    assert lock_path.read_text(encoding="utf-8") == '{"ai_tool": "just"}'


def test_status_reads_issue_worktree_prd_progress(tmp_path: Path) -> None:
    """真实看板入口从 issue-N 分支读取清单，并展示该分支位置。"""
    source_root = Path(__file__).resolve().parents[1]
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-q", str(repo_path)], check=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    pending_dir = repo_path / "tasks/pending"
    pending_dir.mkdir(parents=True)
    script_dir = repo_path / "scripts/shared/just"
    script_dir.mkdir(parents=True)
    shutil.copy2(source_root / "scripts/shared/just/prd_lock.py", script_dir / "prd_lock.py")
    prd_name = "P1-FEAT-20260928-example.md"
    prd_path = pending_dir / prd_name
    prd_path.write_text(
        "# Example\n- GitHub Issue: https://github.com/acme/repo/issues/39\n"
        "\n## Acceptance Checklist\n- [ ] A\n- [ ] B\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_path), "add", "tasks", "scripts"], check=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-qm", "Fixture"], check=True)
    worktree_path = tmp_path / "issue-39"
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "-qb", "issue-39", str(worktree_path)],
        check=True,
    )
    (worktree_path / "tasks/pending" / prd_name).write_text(
        "# Example\n- GitHub Issue: https://github.com/acme/repo/issues/39\n"
        "\n## Acceptance Checklist\n- [x] A\n- [ ] B\n",
        encoding="utf-8",
    )
    lease = PrdActivityLease(repo_path, f"tasks/pending/{prd_name}", 39, "qoder")
    lease.start()
    try:
        status_result = subprocess.run(
            [sys.executable, str(source_root / "scripts/shared/just/prd_status.py"), "pending"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "1/2" in status_result.stdout
        assert "RUNNING iar/qoder" in status_result.stdout
        assert "@issue-39" in status_result.stdout
    finally:
        lease.close()

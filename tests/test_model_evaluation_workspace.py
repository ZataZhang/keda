"""模型评测候选快照仓库的 Git 隔离与判分资料测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.engines.model_evaluation import workspace


def test_snapshot_repo_excludes_hidden_paths_from_git_history(tmp_path: Path) -> None:
    """隐藏路径不能从候选工作树、提交、对象库或 Git checkout 还原。"""
    source_repo = _create_source_repo(tmp_path)
    source_commit = _commit(source_repo, "baseline")
    (source_repo / "tests" / "test_future.py").write_text(
        "def test_future(): pass\n", encoding="utf-8"
    )
    repair_commit = _commit(source_repo, "future repair")

    snapshot = tmp_path / "isolated"
    hidden_paths = ("tests/test_hidden.py", "tasks/answer.md")
    observed_source_tree = workspace.create_snapshot_repo(
        str(source_repo), source_commit, snapshot, excluded_paths=hidden_paths
    )
    expected_source_tree = subprocess.run(
        ["git", "rev-parse", f"{source_commit}^{{tree}}"],
        cwd=source_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert observed_source_tree == expected_source_tree
    assert workspace.worktree_tree_hash(snapshot) != expected_source_tree
    assert (
        len(
            subprocess.run(
                ["git", "log", "--all", "--oneline"],
                cwd=snapshot,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        )
        == 1
    )
    assert not (snapshot / "tests" / "test_future.py").exists()
    assert all(not (snapshot / hidden_path).exists() for hidden_path in hidden_paths)
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=snapshot,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == ""
    )
    for hidden_path in hidden_paths:
        assert (
            subprocess.run(
                ["git", "cat-file", "-e", f"HEAD:{hidden_path}"],
                cwd=snapshot,
                check=False,
                capture_output=True,
            ).returncode
            != 0
        )
    hidden_answer_blob = subprocess.run(
        ["git", "rev-parse", f"{source_commit}:tasks/answer.md"],
        cwd=source_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", hidden_answer_blob],
            cwd=snapshot,
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{repair_commit}^{{commit}}"],
            cwd=snapshot,
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )
    assert not subprocess.run(
        ["git", "remote"], cwd=snapshot, check=True, capture_output=True, text=True
    ).stdout.strip()

    subprocess.run(["git", "checkout", "--", "."], cwd=snapshot, check=True)
    assert all(not (snapshot / hidden_path).exists() for hidden_path in hidden_paths)

    full_snapshot = tmp_path / "unfiltered"
    assert (
        workspace.create_snapshot_repo(str(source_repo), source_commit, full_snapshot)
        == expected_source_tree
    )
    assert workspace.worktree_tree_hash(full_snapshot) == expected_source_tree
    assert (full_snapshot / "tasks" / "answer.md").exists()


def test_snapshot_diff_omits_candidate_hidden_content_and_restores_for_checks(
    tmp_path: Path,
) -> None:
    """生成评审 diff 前清除隐藏路径，并在判分前安全还原其冻结版本。"""
    source_repo = _create_source_repo(tmp_path)
    source_commit = _commit(source_repo, "baseline")
    snapshot = tmp_path / "isolated"
    hidden_paths = ("tests/test_hidden.py", "tasks/answer.md")
    workspace.create_snapshot_repo(
        str(source_repo), source_commit, snapshot, excluded_paths=hidden_paths
    )

    (snapshot / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    (snapshot / "tests").mkdir(parents=True)
    (snapshot / "tests" / "test_hidden.py").write_text("candidate test tamper\n", encoding="utf-8")
    (snapshot / "tasks").mkdir(parents=True)
    (snapshot / "tasks" / "answer.md").write_text("candidate answer tamper\n", encoding="utf-8")
    workspace.remove_held_out_files(snapshot, hidden_paths)
    candidate_diff = workspace.collect_diff(snapshot, "HEAD")
    assert "src/app.py" in candidate_diff
    assert "test_hidden.py" not in candidate_diff
    assert "answer.md" not in candidate_diff
    assert "candidate answer tamper" not in candidate_diff

    outside_marker = tmp_path / "outside-marker"
    outside_marker.write_text("unchanged\n", encoding="utf-8")
    (snapshot / "pyproject.toml").unlink()
    (snapshot / "pyproject.toml").symlink_to(outside_marker)
    (snapshot / "tests" / "test_hidden.py").symlink_to(outside_marker)
    workspace.restore_held_out_base_files(str(source_repo), source_commit, snapshot, hidden_paths)
    workspace.restore_dependency_manifests(str(source_repo), source_commit, snapshot)
    held_out = tmp_path / "materials" / "held_out"
    (held_out / "tests").mkdir(parents=True)
    (held_out / "tests" / "test_hidden.py").write_text(
        "def test_hidden(): pass\n", encoding="utf-8"
    )
    workspace.overlay_held_out(snapshot, held_out, ("tests/test_hidden.py",))
    assert (snapshot / "pyproject.toml").read_text(encoding="utf-8").startswith("[project]")
    assert (snapshot / "uv.lock").read_text(encoding="utf-8") == "version = 1\n"
    assert (snapshot / "tests" / "test_hidden.py").read_text(encoding="utf-8") == (
        "def test_hidden(): pass\n"
    )
    assert (snapshot / "tasks" / "answer.md").read_text(encoding="utf-8") == ("hidden answer\n")
    assert outside_marker.read_text(encoding="utf-8") == "unchanged\n"


def _create_source_repo(tmp_path: Path) -> Path:
    """为快照判分测试建立包含隐藏文件与依赖清单的来源仓库。"""
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=source_repo, check=True)
    subprocess.run(["git", "config", "user.name", "fixture"], cwd=source_repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.test"],
        cwd=source_repo,
        check=True,
    )
    (source_repo / "src").mkdir()
    (source_repo / "tests").mkdir()
    (source_repo / "tasks").mkdir()
    (source_repo / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (source_repo / "tests" / "test_hidden.py").write_text(
        "def test_hidden(): assert True\n", encoding="utf-8"
    )
    (source_repo / "tasks" / "answer.md").write_text("hidden answer\n", encoding="utf-8")
    (source_repo / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (source_repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return source_repo


def _commit(repo_path: Path, subject: str) -> str:
    """为隔离仓库 fixture 创建一次提交并返回 SHA。"""
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", subject], cwd=repo_path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

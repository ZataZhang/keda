"""证据提交语义集成测试（PRD: iar-prd-skill-alignment, FR-1/2/3, rv-3）。

用 ``tmp_path`` 里的**真实 git 仓库**验证：新约定下证据写入
``tasks/evidence/<prd-stem>/`` 后，``git add -A`` + commit 的树里只含 ``.md``
文本报告，原始产物（截图/oracle 脚本）不进任何 commit；被 ``git add -f`` 强制
加入时发布前拦截（``ensure_no_evidence_paths_in_changes``）拒绝；显式配置
``.iar/evidence`` 的 legacy 仓库行为逐字节不变。

不 mock git 与文件系统；git 操作直接走 subprocess，门禁函数走真实
``SubprocessRunner``。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    ValidationConfig,
)
from backend.core.use_cases.agent_runner_validation import (
    ensure_evidence_dir_excluded,
    ensure_no_evidence_paths_in_changes,
    resolve_evidence_dir,
    resolve_issue_evidence_dir,
)
from backend.core.use_cases.agent_runner_repository_local import (
    GitignoreSyncOptions,
    ensure_gitignore_entries,
)
from backend.infrastructure.process_runner import SubprocessRunner

_PRD_STEM = "P1-FEAT-20990101-000000-demo"


def _git(repo_path: Path, *args: str) -> str:
    """在临时仓库里跑一条真实 git 命令并返回 stdout。"""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> Path:
    """建一个带一次初始提交的真实 git 仓库（提交需本地 user 配置）。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _git(repo_path, "init", "-q")
    _git(repo_path, "config", "user.email", "test@example.com")
    _git(repo_path, "config", "user.name", "Test")
    (repo_path / "README.md").write_text("# demo\n", encoding="utf-8")
    _git(repo_path, "add", "README.md")
    _git(repo_path, "commit", "-q", "-m", "init")
    return repo_path


def _issue_with_prd(number: int = 42) -> IssueSummary:
    return IssueSummary(
        number=number,
        title="Demo",
        url=f"https://github.com/example/repo/issues/{number}",
        body=f"## Summary\n\n- PRD path: `tasks/pending/{_PRD_STEM}.md`\n",
        labels=("agent/ready",),
    )


def _write_evidence(evidence_dir: Path) -> None:
    """写一份含 .md 报告、原始产物与 oracle 脚本的证据目录。"""
    scripts_dir = evidence_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (evidence_dir / f"{_PRD_STEM}.evidence-report.md").write_text(
        "# evidence report\n", encoding="utf-8"
    )
    (evidence_dir / "rv-1-shot.png").write_bytes(b"\x89PNG\r\n")
    (scripts_dir / "rv-1-oracle.py").write_text("assert True\n", encoding="utf-8")


def _commit_tree_files(repo_path: Path) -> list[str]:
    """``git ls-tree`` 出 HEAD 树的全部文件（证据断言的事实源）。"""
    return _git(repo_path, "ls-tree", "-r", "--name-only", "HEAD").splitlines()


def test_evidence_commit_semantics_resolution_rules(tmp_path: Path) -> None:
    """单一解析入口：PRD stem 子目录、issue-<N> 兜底、legacy 扁平不变。"""
    config = AppConfig()
    assert (
        resolve_evidence_dir(tmp_path, config, prd_stem=_PRD_STEM)
        == tmp_path / "tasks" / "evidence" / _PRD_STEM
    )
    assert (
        resolve_issue_evidence_dir(tmp_path, config, _issue_with_prd())
        == tmp_path / "tasks" / "evidence" / _PRD_STEM
    )
    # 无 PRD anchor 的 Issue 兜底到 issue-<N>。
    no_prd_issue = IssueSummary(
        number=7,
        title="No PRD",
        url="https://github.com/example/repo/issues/7",
        body="plain body",
        labels=(),
    )
    assert (
        resolve_issue_evidence_dir(tmp_path, config, no_prd_issue)
        == tmp_path / "tasks" / "evidence" / "issue-7"
    )
    # 显式 legacy 配置：扁平目录，逐字节不变。
    legacy_config = AppConfig(validation=ValidationConfig(evidence_dir=".iar/evidence"))
    assert (
        resolve_issue_evidence_dir(tmp_path, legacy_config, _issue_with_prd())
        == tmp_path / ".iar" / "evidence"
    )


def test_evidence_commit_semantics_only_markdown_reports_tracked(tmp_path: Path) -> None:
    """git add -A + commit 后，commit 树只含 .md 报告，原始产物不进 git 历史。"""
    repo_path = _init_repo(tmp_path)
    # 由 init 的托管块机制真实 provision 白名单规则。
    ensure_gitignore_entries(GitignoreSyncOptions(repo_root_path=repo_path))
    gitignore_text = (repo_path / ".gitignore").read_text(encoding="utf-8")
    assert "tasks/evidence/**" in gitignore_text
    assert "!tasks/evidence/**/*.md" in gitignore_text

    evidence_dir = repo_path / "tasks" / "evidence" / _PRD_STEM
    _write_evidence(evidence_dir)

    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-q", "-m", "implement + evidence reports")

    tree_files = _commit_tree_files(repo_path)
    assert f"tasks/evidence/{_PRD_STEM}/{_PRD_STEM}.evidence-report.md" in tree_files
    assert f"tasks/evidence/{_PRD_STEM}/rv-1-shot.png" not in tree_files
    assert f"tasks/evidence/{_PRD_STEM}/scripts/rv-1-oracle.py" not in tree_files


def test_evidence_commit_semantics_publish_guard_rejects_forced_artifacts(
    tmp_path: Path,
) -> None:
    """被 git add -f 强制 stage 的原始产物，发布前拦截必须拒绝；.md 放行。"""
    repo_path = _init_repo(tmp_path)
    ensure_gitignore_entries(GitignoreSyncOptions(repo_root_path=repo_path))
    evidence_dir = repo_path / "tasks" / "evidence" / _PRD_STEM
    _write_evidence(evidence_dir)
    process_runner = SubprocessRunner()
    config = AppConfig()

    # 强制加入 .md 报告：放行（它本就该进版本库）。
    _git(
        repo_path,
        "add",
        "-f",
        f"tasks/evidence/{_PRD_STEM}/{_PRD_STEM}.evidence-report.md",
    )
    ensure_no_evidence_paths_in_changes(repo_path, config, process_runner)

    # 再强制加入原始产物与 oracle 脚本：拦截。
    _git(repo_path, "add", "-f", f"tasks/evidence/{_PRD_STEM}/rv-1-shot.png")
    _git(repo_path, "add", "-f", f"tasks/evidence/{_PRD_STEM}/scripts/rv-1-oracle.py")
    with pytest.raises(RuntimeError, match="rv-1-shot.png"):
        ensure_no_evidence_paths_in_changes(repo_path, config, process_runner)


def test_evidence_commit_semantics_legacy_config_unchanged(tmp_path: Path) -> None:
    """显式 evidence_dir=\".iar/evidence\" 的仓库：整目录排除 + 拦截所有证据路径。"""
    repo_path = _init_repo(tmp_path)
    legacy_config = AppConfig(validation=ValidationConfig(evidence_dir=".iar/evidence"))
    process_runner = SubprocessRunner()

    # legacy：runner 仍把证据目录写进 info/exclude 做整目录排除。
    ensure_evidence_dir_excluded(repo_path, legacy_config, process_runner)
    exclude_text = (repo_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.iar/evidence/" in exclude_text
    assert "/.iar/rv_reexec_cache.json" in exclude_text

    evidence_dir = repo_path / ".iar" / "evidence"
    _write_evidence(evidence_dir)

    # git add -A 遵守 info/exclude：证据（含 .md）一律不进 commit 树。
    (repo_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-q", "-m", "implement")
    assert not any(path.startswith(".iar/evidence/") for path in _commit_tree_files(repo_path))
    assert "src.py" in _commit_tree_files(repo_path)

    # 强制加入时连 .md 报告也拦截（legacy 语义：证据永不进代码 diff）。
    _git(repo_path, "add", "-f", f".iar/evidence/{_PRD_STEM}.evidence-report.md")
    with pytest.raises(RuntimeError, match="evidence"):
        ensure_no_evidence_paths_in_changes(repo_path, legacy_config, process_runner)

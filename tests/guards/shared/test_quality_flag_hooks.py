"""守护 quality flag hooks 的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
QUALITY_FLAG_SCRIPT_PATH = REPO_ROOT / "scripts" / "shared" / "hooks" / "quality_flag.sh"
CHECK_TEST_FLAG_SCRIPT_PATH = REPO_ROOT / "scripts" / "shared" / "hooks" / "check_test_flag.sh"


def run_command(command_parts: list[str], cwd_path: Path) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command with captured UTF-8 output."""

    return subprocess.run(
        command_parts,
        cwd=cwd_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def run_quality_flag_snippet(
    repo_path: Path, shell_snippet: str
) -> subprocess.CompletedProcess[str]:
    """Run a shell snippet after sourcing the quality flag helpers."""

    bash_script = "\n".join(
        [
            "set -euo pipefail",
            f"source {shlex.quote(str(QUALITY_FLAG_SCRIPT_PATH))}",
            shell_snippet,
        ]
    )
    return run_command(["bash", "-lc", bash_script], cwd_path=repo_path)


def init_git_repo(repo_path: Path) -> None:
    """Initialize a Git repository without creating the first commit."""

    init_process = run_command(["git", "init", "-b", "main"], cwd_path=repo_path)
    assert init_process.returncode == 0, init_process.stderr


def write_text_file(repo_path: Path, relative_path: str, content: str) -> None:
    """Create or overwrite a UTF-8 text file inside a repository."""

    file_path = repo_path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def test_quality_metadata_handles_unborn_head(tmp_path: Path) -> None:
    """Branch and HEAD helpers should stay stable before the first commit."""

    init_git_repo(tmp_path)

    metadata_process = run_quality_flag_snippet(
        tmp_path,
        'printf "%s\\n%s\\n" "$(quality_branch_name)" "$(quality_head_hash)"',
    )

    assert metadata_process.returncode == 0, metadata_process.stderr
    assert metadata_process.stdout.splitlines() == ["main", "no-commit"]


def test_quality_effective_tree_ignores_docs_before_first_commit(
    tmp_path: Path,
) -> None:
    """The test tree should ignore doc-only edits even without a HEAD commit."""

    init_git_repo(tmp_path)
    write_text_file(tmp_path, "src/backend/api/app.py", "print('v1')\n")
    write_text_file(tmp_path, "README.md", "# first\n")

    initial_tree_process = run_quality_flag_snippet(tmp_path, "quality_effective_tree working test")
    assert initial_tree_process.returncode == 0, initial_tree_process.stderr
    initial_tree_hash = initial_tree_process.stdout.strip()

    write_text_file(tmp_path, "README.md", "# second\n")
    doc_only_tree_process = run_quality_flag_snippet(
        tmp_path, "quality_effective_tree working test"
    )
    assert doc_only_tree_process.returncode == 0, doc_only_tree_process.stderr
    assert doc_only_tree_process.stdout.strip() == initial_tree_hash

    write_text_file(tmp_path, "src/backend/api/app.py", "print('v2')\n")
    code_change_tree_process = run_quality_flag_snippet(
        tmp_path, "quality_effective_tree working test"
    )
    assert code_change_tree_process.returncode == 0, code_change_tree_process.stderr
    assert code_change_tree_process.stdout.strip() != initial_tree_hash


def test_check_test_flag_accepts_matching_flag_before_first_commit(
    tmp_path: Path,
) -> None:
    """The commit hook should accept a matching flag in an unborn repository."""

    init_git_repo(tmp_path)
    write_text_file(tmp_path, "src/backend/api/app.py", "print('ready')\n")
    write_text_file(tmp_path, "README.md", "# docs\n")

    add_process = run_command(["git", "add", "."], cwd_path=tmp_path)
    assert add_process.returncode == 0, add_process.stderr

    flag_process = run_quality_flag_snippet(
        tmp_path,
        """
git_dir="$(quality_git_dir)"
branch_name="$(quality_branch_name)"
head_hash="$(quality_head_hash)"
test_tree="$(quality_effective_tree staged test)"
quality_write_flag "$git_dir/.last_tested_commit" "$branch_name" "$head_hash" "$test_tree"
""".strip(),
    )
    assert flag_process.returncode == 0, flag_process.stderr

    hook_process = run_command(
        ["bash", str(CHECK_TEST_FLAG_SCRIPT_PATH)],
        cwd_path=tmp_path,
    )

    assert hook_process.returncode == 0, hook_process.stderr
    assert "just test 标记有效" in hook_process.stdout

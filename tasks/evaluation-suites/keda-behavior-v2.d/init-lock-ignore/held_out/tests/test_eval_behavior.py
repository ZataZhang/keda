"""用真实 Git 判断初始化管理块是否正确忽略锁文件。"""

import subprocess

from backend.engines.agent_runner.repository_local import (
    GitignoreSyncOptions,
    ensure_gitignore_entries,
)


def test_lock_files_are_ignored_without_hiding_configuration(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ignore_path = tmp_path / ".gitignore"
    ignore_path.write_text("# user-owned\ncustom.cache\n", encoding="utf-8")
    options = GitignoreSyncOptions(repo_root_path=tmp_path)
    ensure_gitignore_entries(options)
    once = ignore_path.read_bytes()
    ensure_gitignore_entries(options)
    assert ignore_path.read_bytes() == once
    assert once.startswith(b"# user-owned\ncustom.cache\n")
    for filename in (".kedacode.toml.lock", "config.toml.lock", "nested/project.toml.lock"):
        assert (
            subprocess.run(
                ["git", "check-ignore", "--no-index", filename], cwd=tmp_path, capture_output=True
            ).returncode
            == 0
        )
    for filename in (".kedacode.toml", "config.toml", "README.md"):
        assert (
            subprocess.run(
                ["git", "check-ignore", "--no-index", filename], cwd=tmp_path, capture_output=True
            ).returncode
            == 1
        )


def test_dry_run_does_not_rewrite_user_file(tmp_path):
    ignore_path = tmp_path / ".gitignore"
    ignore_path.write_text("user-rule\n", encoding="utf-8")
    ensure_gitignore_entries(GitignoreSyncOptions(repo_root_path=tmp_path, dry_run=True))
    assert ignore_path.read_text(encoding="utf-8") == "user-rule\n"

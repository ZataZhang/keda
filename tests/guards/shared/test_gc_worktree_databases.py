"""守护 worktree 孤儿数据库盘点/回收脚本契约的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

# 仓库根推导而非 CWD 相对路径：守卫必须与 pytest 的启动目录解耦。
_PROJECT_ROOT_PATH = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _PROJECT_ROOT_PATH / "scripts" / "shared" / "worktree" / "gc_worktree_databases.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "gc_worktree_databases",
    _SCRIPT_PATH,
)
assert _SCRIPT_SPEC is not None
assert _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)


def test_build_worktree_database_identifier_matches_create_sh_format() -> None:
    """标识符必须与 create.sh 拼装格式逐字一致，否则盘点会漏判或误判。"""
    identifier = _SCRIPT_MODULE.build_worktree_database_identifier(
        "freshai",
        "feat/blog-html",
        "20732898",
    )
    assert identifier == "freshai_wt_feat/blog-html_20732898"


def test_filter_orphan_candidates_requires_prefix_and_digest_tail() -> None:
    """所有权判定同时要求 wt 前缀与 8 位十六进制摘要尾巴，二者缺一不可。"""
    orphan_names = _SCRIPT_MODULE.filter_orphan_database_candidates(
        [
            "freshai",
            "information_schema",
            "freshai_wt_feat_login_20732898",
            "freshai_wt_feat_login",
            "freshai_feat_other_20732898",
            "other_repo_wt_feat_login_20732898",
        ],
        set(),
        "freshai",
    )
    assert orphan_names == ["freshai_wt_feat_login_20732898"]


def test_filter_orphan_candidates_excludes_live_worktree_names() -> None:
    """命中存活集合的库名必须被排除，避免误删在用 worktree 的库。"""
    live_names = {"freshai_wt_feat_login_20732898"}
    orphan_names = _SCRIPT_MODULE.filter_orphan_database_candidates(
        [
            "freshai_wt_feat_login_20732898",
            "freshai_wt_feat_old_45957ef0",
        ],
        live_names,
        "freshai",
    )
    assert orphan_names == ["freshai_wt_feat_old_45957ef0"]


def test_parse_main_database_url_accepts_sql_dialects_only(tmp_path: Path) -> None:
    """DATABASE_URL 只接受 PostgreSQL/MySQL；SQLite、空值与缺失文件均视为无库。"""
    mysql_env_path = tmp_path / ".env.local"
    mysql_env_path.write_text(
        "DATABASE_URL=mysql+pymysql://root:secret@localhost:3306/freshai\n",
        encoding="utf-8",
    )
    parsed_mysql_url = _SCRIPT_MODULE.parse_main_database_url(mysql_env_path)
    assert parsed_mysql_url is not None
    assert parsed_mysql_url.hostname == "localhost"

    sqlite_env_path = tmp_path / "sqlite.env"
    sqlite_env_path.write_text("DATABASE_URL=sqlite:///./dev.db\n", encoding="utf-8")
    assert _SCRIPT_MODULE.parse_main_database_url(sqlite_env_path) is None

    empty_env_path = tmp_path / "empty.env"
    empty_env_path.write_text("DATABASE_URL=\n", encoding="utf-8")
    assert _SCRIPT_MODULE.parse_main_database_url(empty_env_path) is None

    assert _SCRIPT_MODULE.parse_main_database_url(tmp_path / "missing.env") is None


@pytest.mark.slow
def test_compute_branch_digest_matches_git_hash_object(tmp_path: Path) -> None:
    """分支摘要必须与 create.sh 的 git hash-object 结果一致。"""
    repo_root = tmp_path / "digest-repo"
    repo_root.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )

    expected_digest = (
        subprocess.run(
            ["git", "hash-object", "--stdin"],
            input=b"feat/demo",
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .strip()[:8]
    )

    assert _SCRIPT_MODULE.compute_branch_digest(repo_root, "feat/demo") == expected_digest


@pytest.mark.slow
def test_build_live_worktree_database_names_covers_registered_worktrees(
    tmp_path: Path,
) -> None:
    """存活集合必须覆盖所有注册 worktree（含目录存在者）的派生库名。"""
    repo_root = tmp_path / "live-repo"
    repo_root.mkdir()

    def run_git(*git_arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo_root), *git_arguments],
            check=True,
            capture_output=True,
        )

    run_git("init")
    run_git(
        "-c",
        "user.email=guard@example.com",
        "-c",
        "user.name=guard",
        "commit",
        "--allow-empty",
        "-m",
        "init",
    )
    run_git("worktree", "add", "-b", "feat/demo", str(tmp_path / "wt-demo"))

    live_names = _SCRIPT_MODULE.build_live_worktree_database_names(repo_root, "live-repo")
    branch_digest = _SCRIPT_MODULE.compute_branch_digest(repo_root, "feat/demo")
    expected_name = _SCRIPT_MODULE.derive_database_name(f"live-repo_wt_feat/demo_{branch_digest}")

    assert expected_name in live_names

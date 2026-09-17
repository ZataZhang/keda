"""守护 ``just worktree -o`` 名称解析的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/worktree/open.sh``。核心不变量：

1. **看板 / PRD 流程给出的名称必须能被 ``-o`` 打开。** ``just prd status`` 的
   ACTIVITY 列展示 PRD slug，PRD 文件名与 ``tasks/pending/....md`` 路径也是日常
   复制来源；``-o`` 只认精确分支名时，"看板告诉你名字、``-o`` 打不开"的死循环
   会直接复现。解析规则必须复用 ``prd_branch_match.sh``（slug 与分支全名或
   分支最后一段相等即命中），不得在调用方另立一套。
2. **精确分支名优先。** ``feat/x`` 与 ``tasks/x`` 并存时，用户显式输入的分支名
   不能被误判为歧义。
3. **歧义必须报错列候选，不得猜一个打开。** 多个 worktree 共享同一 slug 时，
   静默挑第一个会把人带进错误的 worktree。
4. **唯一事实源是 ``git worktree list``。** 旧约定 ``$repo_parent/<名称>`` 既不是
   ``create.sh`` 的落盘位置，命中残留目录时还会打开 ``.git`` 已失效的工作目录；
   ``-o`` 不得回退到该路径。
5. **未命中时错误信息必须列出当前可打开的 worktree**，只报"找不到"会让用户
   无从判断是名字写错还是 worktree 根本没建。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

_WORKTREE_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "worktree"
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
_OPEN_SCRIPT_PATH = _WORKTREE_SCRIPTS_PATH / "open.sh"

_FIXTURE_PRD_NAME = "P2-FEAT-20260101-000000-avatar-upload"
_FIXTURE_PRD_SLUG = "avatar-upload"
_FIXTURE_PRD_RELATIVE_PATH = f"tasks/pending/{_FIXTURE_PRD_NAME}.md"


def _run_git(repo_path: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    """在指定目录执行 git 命令并返回结果。"""
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_main_repo(repo_path: Path) -> Path:
    """初始化一个含 fixture PRD 与一次提交的真实 git 仓库。"""
    repo_path.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "init", "-b", "main")
    _run_git(repo_path, "config", "user.email", "guard@example.com")
    _run_git(repo_path, "config", "user.name", "guard-test")
    prd_file_path = repo_path / _FIXTURE_PRD_RELATIVE_PATH
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    prd_file_path.write_text("# fixture PRD\n", encoding="utf-8")
    _run_git(repo_path, "add", ".")
    _run_git(repo_path, "commit", "-m", "init")
    return repo_path


def _add_convention_worktree(main_repo_path: Path, branch_name: str) -> Path:
    """按 ``create.sh`` 的真实约定 ``<repo_parent>/<repo>-worktrees/<branch>`` 建 worktree。"""
    worktree_path = main_repo_path.parent / f"{main_repo_path.name}-worktrees" / branch_name
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(main_repo_path, "worktree", "add", "-b", branch_name, str(worktree_path))
    return worktree_path


def _run_ai_open(main_repo_path: Path, *cli_args: str) -> subprocess.CompletedProcess[str]:
    """在子 shell 里 source ``open.sh`` 后调用 ``ai_open <名称> --cmd true``。

    ``--cmd true`` 让末尾的编辑器启动退化成 ``true`` 内建命令，既不打开 IDE 也不
    掩盖退出码；``/bin/bash`` 固定 macOS 自带 3.2，保证兼容性声明真的被跑到。
    """
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1" && shift && ai_open "$@"',
            "bash",
            str(_OPEN_SCRIPT_PATH),
            *cli_args,
            "--cmd",
            "true",
        ],
        cwd=main_repo_path,
        capture_output=True,
        text=True,
    )


def test_slug_opens_worktree_on_prefixed_branch(tmp_path: Path) -> None:
    """看板 slug 必须能打开 ``feat/<slug>`` 上的 worktree。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_convention_worktree(main_repo_path, f"feat/{_FIXTURE_PRD_SLUG}")

    open_result = _run_ai_open(main_repo_path, _FIXTURE_PRD_SLUG)

    assert open_result.returncode == 0, open_result.stdout + open_result.stderr
    assert str(worktree_path) in open_result.stdout


def test_prd_file_stem_and_relative_path_open_same_worktree(tmp_path: Path) -> None:
    """PRD 文件 stem 与 ``tasks/pending/....md`` 路径都要命中同一 worktree。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_convention_worktree(main_repo_path, f"feat/{_FIXTURE_PRD_SLUG}")

    for lookup_name in (
        _FIXTURE_PRD_NAME,
        _FIXTURE_PRD_RELATIVE_PATH,
        f"./{_FIXTURE_PRD_RELATIVE_PATH}",
    ):
        open_result = _run_ai_open(main_repo_path, lookup_name)
        assert open_result.returncode == 0, lookup_name + open_result.stdout + open_result.stderr
        assert str(worktree_path) in open_result.stdout, lookup_name


def test_exact_branch_name_wins_over_slug_match(tmp_path: Path) -> None:
    """``feat/x`` 与 ``tasks/x`` 并存时，输入 ``x`` 命中的是精确同名分支。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    exact_branch_worktree_path = _add_convention_worktree(main_repo_path, _FIXTURE_PRD_SLUG)
    prefixed_worktree_path = _add_convention_worktree(main_repo_path, f"tasks/{_FIXTURE_PRD_SLUG}")

    open_result = _run_ai_open(main_repo_path, _FIXTURE_PRD_SLUG)

    assert open_result.returncode == 0, open_result.stdout + open_result.stderr
    assert str(exact_branch_worktree_path) in open_result.stdout
    assert str(prefixed_worktree_path) not in open_result.stdout


def test_ambiguous_slug_lists_candidates_and_fails(tmp_path: Path) -> None:
    """多个 worktree 共享同一 slug 时报错并列出候选，不得猜一个打开。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _add_convention_worktree(main_repo_path, f"feat/{_FIXTURE_PRD_SLUG}")
    _add_convention_worktree(main_repo_path, f"tasks/{_FIXTURE_PRD_SLUG}")

    open_result = _run_ai_open(main_repo_path, _FIXTURE_PRD_SLUG)

    assert open_result.returncode != 0
    assert f"feat/{_FIXTURE_PRD_SLUG}" in open_result.stdout
    assert f"tasks/{_FIXTURE_PRD_SLUG}" in open_result.stdout


def test_legacy_parent_directory_convention_is_not_used(tmp_path: Path) -> None:
    """旧约定 ``$repo_parent/<名称>`` 目录即使存在也不得被当作 worktree 打开。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    legacy_directory_path = tmp_path / _FIXTURE_PRD_SLUG
    legacy_directory_path.mkdir()

    open_result = _run_ai_open(main_repo_path, _FIXTURE_PRD_SLUG)

    assert open_result.returncode != 0
    assert str(legacy_directory_path) not in open_result.stdout


def test_not_found_lists_available_worktrees(tmp_path: Path) -> None:
    """未命中时错误信息必须列出可用 worktree，帮助区分写错名字与 worktree 未创建。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _add_convention_worktree(main_repo_path, "feat/other-branch")

    open_result = _run_ai_open(main_repo_path, "no-such-worktree")

    assert open_result.returncode != 0
    assert "feat/other-branch" in open_result.stdout
    assert "PRD slug" in open_result.stdout


def test_open_by_slug_claims_pending_prd_lock_with_real_branch(tmp_path: Path) -> None:
    """按 slug 打开时顺手领锁，锁记录的 branch 必须是 worktree 真实检出的分支。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_convention_worktree(main_repo_path, f"feat/{_FIXTURE_PRD_SLUG}")
    prd_lock_target_path = main_repo_path / "scripts" / "shared" / "just" / "prd_lock.py"
    prd_lock_target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_JUST_SCRIPTS_PATH / "prd_lock.py", prd_lock_target_path)

    open_result = _run_ai_open(main_repo_path, _FIXTURE_PRD_SLUG)

    assert open_result.returncode == 0, open_result.stdout + open_result.stderr
    lock_path = main_repo_path / "tasks" / "evidence" / _FIXTURE_PRD_NAME / "active.lock"
    lock_metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    expected_worktree_label = os.path.relpath(worktree_path.resolve(), main_repo_path.resolve())
    assert lock_metadata["branch"] == f"feat/{_FIXTURE_PRD_SLUG}"
    assert lock_metadata["worktree"] == expected_worktree_label

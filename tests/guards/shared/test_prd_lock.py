"""守护 PRD 执行锁脚本的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/just/prd_lock.py``。核心不变量：

1. **锁的唯一事实源在主仓库。** 在 linked worktree 内领锁时，锁文件必须落在
   主仓库 ``tasks/evidence/<stem>/active.lock``，而不是 worktree 自己那份——
   否则各 worktree 各存一份锁，重复开工防护形同虚设。
2. **并发领锁唯一成功。** ``O_EXCL`` 原子创建保证两个不同归属的会话同时领锁时
   恰一个成功；去掉这层原子性就会重演"两个会话改同一批文件"的事故。
3. **死锁可被接管且留档，但接管理由只有心跳过期。** 心跳超时的锁必须能自动
   接管，旧锁改名留档而不是静默覆盖；持锁 pid 已死**不**构成接管理由——锁脚本
   与 agent 工具调用的会话首领都是短命进程，pid 死亡不代表持锁会话已死。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# prd_lock.py 不是包的一部分，import 前需把它所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
if str(_JUST_SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(_JUST_SCRIPTS_PATH))

import prd_lock  # noqa: E402

_PRD_LOCK_SCRIPT_PATH = _JUST_SCRIPTS_PATH / "prd_lock.py"
_FIXTURE_PRD_NAME = "P2-FEAT-20260101-000000-lock-target"
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
    """初始化一个带 tasks 结构与一次提交的真实 git 仓库。"""
    repo_path.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "init", "-b", "main")
    _run_git(repo_path, "config", "user.email", "guard@example.com")
    _run_git(repo_path, "config", "user.name", "guard-test")
    prd_file_path = repo_path / _FIXTURE_PRD_RELATIVE_PATH
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    prd_file_path.write_text(
        "# fixture PRD\n\n## Acceptance Checklist\n\n- [ ] item one\n- [x] item two\n",
        encoding="utf-8",
    )
    _run_git(repo_path, "add", ".")
    _run_git(repo_path, "commit", "-m", "init")
    return repo_path


def _add_linked_worktree(main_repo_path: Path, worktree_name: str) -> Path:
    """为主仓库创建一个真实 linked worktree 并返回其路径。"""
    worktree_path = main_repo_path.parent / worktree_name
    _run_git(main_repo_path, "worktree", "add", "-b", worktree_name, str(worktree_path))
    return worktree_path


def _run_lock_cli(cwd_path: Path, *cli_args: str) -> subprocess.CompletedProcess[str]:
    """以子进程方式在指定工作目录运行锁脚本 CLI。"""
    return subprocess.run(
        [sys.executable, str(_PRD_LOCK_SCRIPT_PATH), *cli_args],
        cwd=cwd_path,
        capture_output=True,
        text=True,
    )


def _lock_file_path(repo_path: Path, prd_stem: str = _FIXTURE_PRD_NAME) -> Path:
    """返回某仓库视图下锁文件应在的位置。"""
    return repo_path / "tasks" / "evidence" / prd_stem / "active.lock"


def _write_foreign_lock(
    repo_path: Path,
    *,
    heartbeat_at: datetime,
    holder_pid: int,
    holder_worktree: str = "../other-worktree",
    holder_tool: str = "claude",
    holder_branch: str = "feat-foreign",
    prd_stem: str = _FIXTURE_PRD_NAME,
) -> Path:
    """直接写入一把指定状态的他人锁，用于构造过期 / 新鲜冲突场景。"""
    lock_path = _lock_file_path(repo_path, prd_stem)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_metadata = {
        "pid": holder_pid,
        "hostname": socket.gethostname(),
        "worktree": holder_worktree,
        "started_at": (heartbeat_at - timedelta(minutes=5)).isoformat(),
        "heartbeat_at": heartbeat_at.isoformat(),
        "ai_tool": holder_tool,
        "branch": holder_branch,
    }
    lock_path.write_text(json.dumps(lock_metadata), encoding="utf-8")
    return lock_path


def _read_lock_metadata(lock_path: Path) -> dict:
    """读取锁文件 JSON。"""
    return json.loads(lock_path.read_text(encoding="utf-8"))


def _spawn_dead_pid() -> int:
    """启动一个立即退出的子进程，返回其（已死亡且已回收的）pid。"""
    child_process = subprocess.Popen([sys.executable, "-c", "pass"])
    child_process.wait()
    return child_process.pid


def test_claim_creates_lock_with_full_metadata(tmp_path: Path) -> None:
    """首次领锁：原子创建成功，锁 JSON 字段齐全，退出码为 0。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")

    claim_result = _run_lock_cli(
        main_repo_path,
        "claim",
        _FIXTURE_PRD_RELATIVE_PATH,
        "--tool",
        "kimi",
        "--branch",
        "feat-lock",
    )

    assert claim_result.returncode == 0, claim_result.stderr
    lock_path = _lock_file_path(main_repo_path)
    assert lock_path.is_file()
    lock_metadata = _read_lock_metadata(lock_path)
    assert lock_metadata["pid"] > 0
    assert lock_metadata["hostname"] == socket.gethostname()
    assert lock_metadata["worktree"] == ""
    assert lock_metadata["ai_tool"] == "kimi"
    assert lock_metadata["branch"] == "feat-lock"
    datetime.fromisoformat(lock_metadata["started_at"])
    datetime.fromisoformat(lock_metadata["heartbeat_at"])


def test_claim_same_owner_is_idempotent_and_keeps_metadata(tmp_path: Path) -> None:
    """同归属重复领锁幂等刷新：退出 0、不留档、不覆盖首次自报的展示元数据。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    first_claim = _run_lock_cli(
        main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH, "--tool", "kimi"
    )
    assert first_claim.returncode == 0, first_claim.stderr

    second_claim = _run_lock_cli(main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)

    assert second_claim.returncode == 0, second_claim.stderr
    lock_metadata = _read_lock_metadata(_lock_file_path(main_repo_path))
    assert lock_metadata["ai_tool"] == "kimi"
    stale_leftovers = list(_lock_file_path(main_repo_path).parent.glob("active.lock.*.stale"))
    assert not stale_leftovers


def test_concurrent_claim_from_two_worktrees_exactly_one_wins(tmp_path: Path) -> None:
    """两个不同 linked worktree 同时领同一把锁：多轮并发下每轮恰一个成功。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_a_path = _add_linked_worktree(main_repo_path, "wt-race-a")
    worktree_b_path = _add_linked_worktree(main_repo_path, "wt-race-b")

    for round_index in range(5):
        round_prd_stem = f"{_FIXTURE_PRD_NAME}-race{round_index}"
        round_prd_relative = f"tasks/pending/{round_prd_stem}.md"
        # claim 只要求 PRD 文件存在于当前工作树；worktree 检出独立于主仓库，
        # 需在各自文件系统写入同名 fixture 文件。
        for round_repo_path in (main_repo_path, worktree_a_path, worktree_b_path):
            round_prd_path = round_repo_path / round_prd_relative
            round_prd_path.parent.mkdir(parents=True, exist_ok=True)
            round_prd_path.write_text("# race\n", encoding="utf-8")

        claim_process_a = subprocess.Popen(
            [sys.executable, str(_PRD_LOCK_SCRIPT_PATH), "claim", round_prd_relative],
            cwd=worktree_a_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        claim_process_b = subprocess.Popen(
            [sys.executable, str(_PRD_LOCK_SCRIPT_PATH), "claim", round_prd_relative],
            cwd=worktree_b_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout_a, _ = claim_process_a.communicate()
        stdout_b, stderr_b = claim_process_b.communicate()

        round_exit_codes = sorted([claim_process_a.returncode, claim_process_b.returncode])
        assert round_exit_codes == [0, 1], (
            f"第 {round_index} 轮并发领锁退出码异常: {round_exit_codes}\n"
            f"worktree-a: {stdout_a}\nworktree-b: {stdout_b}{stderr_b}"
        )


def test_fresh_foreign_lock_is_rejected_with_holder_info(tmp_path: Path) -> None:
    """他人新鲜锁：拒绝并输出持锁工具、分支与显式释放提示，退出码 1。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _write_foreign_lock(
        main_repo_path, heartbeat_at=datetime.now(timezone.utc), holder_pid=os.getpid()
    )

    claim_result = _run_lock_cli(main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)

    assert claim_result.returncode == 1
    combined_output = claim_result.stdout + claim_result.stderr
    assert "claude" in combined_output
    assert "feat-foreign" in combined_output
    assert "release" in combined_output


def test_stale_heartbeat_lock_is_taken_over_and_archived(tmp_path: Path) -> None:
    """心跳过期（pid 仍活）：自动接管成功，旧锁改名留档为 .stale。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _write_foreign_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_pid=os.getpid(),
    )

    claim_result = _run_lock_cli(
        main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH, "--tool", "kimi"
    )

    assert claim_result.returncode == 0, claim_result.stderr
    stale_archive_files = list(_lock_file_path(main_repo_path).parent.glob("active.lock.*.stale"))
    assert len(stale_archive_files) == 1
    new_lock_metadata = _read_lock_metadata(_lock_file_path(main_repo_path))
    assert new_lock_metadata["worktree"] == ""
    assert new_lock_metadata["ai_tool"] == "kimi"


def test_dead_holder_pid_alone_does_not_make_lock_stale(tmp_path: Path) -> None:
    """同主机 pid 已死但心跳新鲜的锁仍视为新鲜锁，他人开工被拒绝。

    锁脚本与 agent 工具调用的会话首领都是短命进程，pid 存活与否不代表持锁
    会话存亡；过期判定只看心跳。该用例锁住这条语义，防止误把 pid 死亡当接管理由。
    """
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _write_foreign_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc),
        holder_pid=_spawn_dead_pid(),
    )

    claim_result = _run_lock_cli(main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)

    assert claim_result.returncode == 1
    assert not list(_lock_file_path(main_repo_path).parent.glob("active.lock.*.stale"))


def test_claim_inside_worktree_lands_in_main_repo(tmp_path: Path) -> None:
    """在 linked worktree 内领锁：锁落主仓库证据目录，worktree 内不出现同名锁。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "wt-claim")

    claim_result = _run_lock_cli(linked_worktree_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)

    assert claim_result.returncode == 0, claim_result.stderr
    assert _lock_file_path(main_repo_path).is_file()
    assert not _lock_file_path(linked_worktree_path).exists()
    lock_metadata = _read_lock_metadata(_lock_file_path(main_repo_path))
    assert lock_metadata["worktree"] != ""


def test_main_repo_lock_is_adopted_by_linked_worktree_claim(tmp_path: Path) -> None:
    """开工移交：主仓库领取的锁被同一仓库 linked worktree 的开工自检认领。

    ``just implement`` 在主仓库领锁后 executor 进入 worktree 工作；worktree 内的
    第 0 步 ``just prd start`` 必须把锁归属移交到当前 worktree 而非报冲突，
    否则开工入口会自我阻塞。
    """
    main_repo_path = _init_main_repo(tmp_path / "repo")
    linked_worktree_path = _add_linked_worktree(main_repo_path, "wt-adopt")
    entry_claim = _run_lock_cli(main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)
    assert entry_claim.returncode == 0, entry_claim.stderr

    worktree_claim = _run_lock_cli(linked_worktree_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)

    assert worktree_claim.returncode == 0, worktree_claim.stderr
    lock_metadata = _read_lock_metadata(_lock_file_path(main_repo_path))
    assert lock_metadata["worktree"] != ""
    # 移交后 branch 展示字段必须更新为 worktree 的实际分支，否则看板仍显示 @main。
    assert lock_metadata["branch"] == "wt-adopt"


def test_heartbeat_refreshes_own_lock_and_rejects_foreign_or_missing(tmp_path: Path) -> None:
    """心跳：自己的锁续期成功；锁不存在或归属不符时警告并非零退出。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")

    missing_heartbeat = _run_lock_cli(main_repo_path, "heartbeat", _FIXTURE_PRD_RELATIVE_PATH)
    assert missing_heartbeat.returncode == 1

    claim_result = _run_lock_cli(main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)
    assert claim_result.returncode == 0, claim_result.stderr
    lock_path = _lock_file_path(main_repo_path)
    backdated_metadata = _read_lock_metadata(lock_path)
    backdated_metadata["heartbeat_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    lock_path.write_text(json.dumps(backdated_metadata), encoding="utf-8")

    own_heartbeat = _run_lock_cli(main_repo_path, "heartbeat", _FIXTURE_PRD_RELATIVE_PATH)
    assert own_heartbeat.returncode == 0, own_heartbeat.stderr
    refreshed_metadata = _read_lock_metadata(lock_path)
    refreshed_at = datetime.fromisoformat(refreshed_metadata["heartbeat_at"])
    assert datetime.now(timezone.utc) - refreshed_at < timedelta(minutes=5)

    _write_foreign_lock(
        main_repo_path, heartbeat_at=datetime.now(timezone.utc), holder_pid=os.getpid()
    )
    foreign_heartbeat = _run_lock_cli(main_repo_path, "heartbeat", _FIXTURE_PRD_RELATIVE_PATH)
    assert foreign_heartbeat.returncode == 1


def test_release_removes_own_lock_and_foreign_release_requires_force(tmp_path: Path) -> None:
    """释放：自己的锁直接删除；他人锁无 --force 拒绝、带 --force 删除并告示。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    claim_result = _run_lock_cli(main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)
    assert claim_result.returncode == 0, claim_result.stderr

    own_release = _run_lock_cli(main_repo_path, "release", _FIXTURE_PRD_RELATIVE_PATH)
    assert own_release.returncode == 0, own_release.stderr
    assert not _lock_file_path(main_repo_path).exists()

    _write_foreign_lock(
        main_repo_path, heartbeat_at=datetime.now(timezone.utc), holder_pid=os.getpid()
    )
    plain_release = _run_lock_cli(main_repo_path, "release", _FIXTURE_PRD_RELATIVE_PATH)
    assert plain_release.returncode == 1
    assert _lock_file_path(main_repo_path).is_file()

    forced_release = _run_lock_cli(main_repo_path, "release", _FIXTURE_PRD_RELATIVE_PATH, "--force")
    assert forced_release.returncode == 0, forced_release.stderr
    assert not _lock_file_path(main_repo_path).exists()


def test_concurrent_stale_takeover_exactly_one_wins(tmp_path: Path) -> None:
    """过期锁并发接管：两个 worktree 同时接管同一把过期锁，每轮恰一个成功。

    接管路径的"读锁 → 判过期 → 留档 → 写新锁"必须是原子的；否则两个进程同时
    通过 stale 判定后都会写入成功，后者甚至把前者刚接管的新锁归档——重复开工
    防护在最需要它的接管场景下失效。
    """
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_a_path = _add_linked_worktree(main_repo_path, "wt-takeover-a")
    worktree_b_path = _add_linked_worktree(main_repo_path, "wt-takeover-b")

    for round_index in range(10):
        round_prd_stem = f"{_FIXTURE_PRD_NAME}-takeover{round_index}"
        round_prd_relative = f"tasks/pending/{round_prd_stem}.md"
        for round_repo_path in (main_repo_path, worktree_a_path, worktree_b_path):
            round_prd_path = round_repo_path / round_prd_relative
            round_prd_path.parent.mkdir(parents=True, exist_ok=True)
            round_prd_path.write_text("# takeover race\n", encoding="utf-8")
        _write_foreign_lock(
            main_repo_path,
            heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
            holder_pid=os.getpid(),
            prd_stem=round_prd_stem,
        )

        claim_process_a = subprocess.Popen(
            [sys.executable, str(_PRD_LOCK_SCRIPT_PATH), "claim", round_prd_relative],
            cwd=worktree_a_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        claim_process_b = subprocess.Popen(
            [sys.executable, str(_PRD_LOCK_SCRIPT_PATH), "claim", round_prd_relative],
            cwd=worktree_b_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout_a, stderr_a = claim_process_a.communicate()
        stdout_b, stderr_b = claim_process_b.communicate()

        round_exit_codes = sorted([claim_process_a.returncode, claim_process_b.returncode])
        assert round_exit_codes == [0, 1], (
            f"第 {round_index} 轮并发接管退出码异常: {round_exit_codes}\n"
            f"worktree-a: {stdout_a}{stderr_a}\nworktree-b: {stdout_b}{stderr_b}"
        )
        # 最终锁必须是一方可解析的新锁，且归属恰为胜出的那个 worktree。
        final_lock_metadata = _read_lock_metadata(_lock_file_path(main_repo_path, round_prd_stem))
        assert final_lock_metadata["worktree"] != "../other-worktree"


def test_unparseable_or_naive_heartbeat_treated_as_stale(tmp_path: Path) -> None:
    """心跳时间戳无法解析或不带时区（naive）时按过期处理，不得抛异常。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")

    for corrupt_heartbeat in ("not-a-timestamp", "2026-09-15 10:00:00"):
        lock_path = _write_foreign_lock(
            main_repo_path,
            heartbeat_at=datetime.now(timezone.utc),
            holder_pid=os.getpid(),
        )
        corrupt_metadata = _read_lock_metadata(lock_path)
        corrupt_metadata["heartbeat_at"] = corrupt_heartbeat
        lock_path.write_text(json.dumps(corrupt_metadata), encoding="utf-8")

        claim_result = _run_lock_cli(main_repo_path, "claim", _FIXTURE_PRD_RELATIVE_PATH)

        assert (
            claim_result.returncode == 0
        ), f"心跳 {corrupt_heartbeat!r} 应按过期接管: {claim_result.stderr}"
        release_result = _run_lock_cli(main_repo_path, "release", _FIXTURE_PRD_RELATIVE_PATH)
        assert release_result.returncode == 0, release_result.stderr


def test_inspect_prd_lock_reports_none_fresh_and_stale(tmp_path: Path) -> None:
    """看板查询接口：无锁 / 新鲜锁 / 过期锁分别返回 none / fresh / stale。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")

    empty_snapshot = prd_lock.inspect_prd_lock(main_repo_path, _FIXTURE_PRD_NAME)
    assert empty_snapshot.state == "none"

    _write_foreign_lock(
        main_repo_path, heartbeat_at=datetime.now(timezone.utc), holder_pid=os.getpid()
    )
    fresh_snapshot = prd_lock.inspect_prd_lock(main_repo_path, _FIXTURE_PRD_NAME)
    assert fresh_snapshot.state == "fresh"
    assert fresh_snapshot.metadata["ai_tool"] == "claude"

    _write_foreign_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=1),
        holder_pid=os.getpid(),
    )
    stale_snapshot = prd_lock.inspect_prd_lock(main_repo_path, _FIXTURE_PRD_NAME)
    assert stale_snapshot.state == "stale"

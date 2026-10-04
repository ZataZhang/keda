"""PRD 状态看板守卫测试共用的 fixture 构造与渲染辅助。

**测试辅助模块，非测试文件**（pytest 不收集）。``test_prd_status.py``（worktree / 依赖 /
证据信号）与 ``test_prd_status_acceptance.py``（验收轴：横幅与 AWAITING HUMAN 分区）
都要在真实 git 仓库里搭 fixture、再渲染看板单元格；这套构造只在此维护一份，免得两个
测试文件各带一份后悄悄漂移。

被测对象随看板脚本拆分而各归其位：ACTIVITY 列在 ``prd_activity.py``、DEPS 列在
``prd_deps.py``、记录模型与其余列在 ``prd_status.py``——这里按"调用方真实用到的那个
模块"逐个调用，不经任何 re-export。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# prd_*.py 脚本不是包的一部分，import 前需把它们所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
if str(_JUST_SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(_JUST_SCRIPTS_PATH))

import prd_activity  # noqa: E402
import prd_deps  # noqa: E402
import prd_lock  # noqa: E402
import prd_status  # noqa: E402

FIXTURE_PRD_NAME = "P2-FEAT-20260101-000000-avatar-upload"
FIXTURE_PRD_RELATIVE_PATH = f"tasks/pending/{FIXTURE_PRD_NAME}.md"
FIXTURE_PRD_ARCHIVE_RELATIVE_PATH = f"tasks/archive/{FIXTURE_PRD_NAME}.md"
PLAIN_PALETTE = prd_status.Palette(enabled=False)

DEFAULT_FIXTURE_PRD_TEXT = "# fixture PRD\n\n## Acceptance Checklist\n\n- [ ] item one\n"
DEPENDENCY_PRD_TEMPLATE = (
    "# fixture PRD\n"
    "\n"
    "## 8. Delivery Dependencies\n"
    "\n"
    "- Group: none\n"
    "- Depends on tasks/issues:\n"
    "  - {dependency_ref}\n"
    "- Gate type: {gate_type}\n"
    "\n"
    "## 9. Acceptance Checklist\n"
    "\n"
    "- [ ] item one\n"
)
UPSTREAM_PRD_NAME = "P1-FEAT-20260101-000001-upstream-task"
UPSTREAM_PRD_SLUG = "upstream-task"


def run_git(repo_path: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    """在指定目录执行 git 命令并返回结果。"""
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )


def init_main_repo(repo_path: Path, prd_text: str = DEFAULT_FIXTURE_PRD_TEXT) -> Path:
    """初始化一个带 tasks 结构与一次提交的真实 git 仓库。

    Args:
        repo_path (Path): 待初始化的仓库目录。
        prd_text (str): fixture PRD 的正文；依赖列用例传入含 §8 的模板。
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    run_git(repo_path, "init", "-b", "main")
    run_git(repo_path, "config", "user.email", "guard@example.com")
    run_git(repo_path, "config", "user.name", "guard-test")
    prd_file_path = repo_path / FIXTURE_PRD_RELATIVE_PATH
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    prd_file_path.write_text(prd_text, encoding="utf-8")
    run_git(repo_path, "add", ".")
    run_git(repo_path, "commit", "-m", "init")
    return repo_path


def add_linked_worktree(main_repo_path: Path, branch_name: str, worktree_name: str) -> Path:
    """为主仓库创建一个真实 linked worktree 并返回其路径。"""
    worktree_path = main_repo_path.parent / worktree_name
    run_git(main_repo_path, "worktree", "add", "-b", branch_name, str(worktree_path))
    return worktree_path


def write_lock(
    repo_path: Path,
    *,
    heartbeat_at: datetime,
    holder_worktree: str,
    holder_tool: str = "claude",
    holder_branch: str = "feat/avatar-upload",
) -> Path:
    """直接写入一把指定状态的锁，构造看板的 fresh / stale 场景。"""
    lock_path = repo_path / "tasks" / "evidence" / FIXTURE_PRD_NAME / "active.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_metadata = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "worktree": holder_worktree,
        "started_at": (heartbeat_at - timedelta(minutes=10)).isoformat(),
        "heartbeat_at": heartbeat_at.isoformat(),
        "ai_tool": holder_tool,
        "branch": holder_branch,
    }
    lock_path.write_text(json.dumps(lock_metadata), encoding="utf-8")
    return lock_path


def render_activity_cell(
    repo_path: Path, prd_relative_path: str = FIXTURE_PRD_RELATIVE_PATH
) -> str:
    """读取 fixture PRD 并渲染 ACTIVITY 单元格（无颜色）。

    Args:
        repo_path (Path): fixture 仓库根目录。
        prd_relative_path (str): PRD 相对仓库根的路径；归档场景传 archive 下的那份。
    """
    return prd_activity.format_activity_cell(
        collect_fixture_record(repo_path, prd_relative_path),
        repo_path,
        repo_path / "tasks" / "evidence",
        PLAIN_PALETTE,
    )


def collect_fixture_record(
    repo_path: Path, prd_relative_path: str = FIXTURE_PRD_RELATIVE_PATH
) -> prd_status.PrdRecord:
    """收集 fixture PRD 的看板记录，工作树列表按仓库现状实时获取。

    Args:
        repo_path (Path): fixture 仓库根目录。
        prd_relative_path (str): PRD 相对仓库根的路径；归档场景传 archive 下的那份。
    """
    return prd_status.collect_prd_record(
        repo_path / prd_relative_path,
        repo_path / "tasks" / "evidence",
        prd_lock.list_linked_worktree_branches(repo_path),
    )


def render_checklist_cell(
    repo_path: Path, prd_relative_path: str = FIXTURE_PRD_RELATIVE_PATH
) -> str:
    """渲染 CHECKLIST 单元格（无颜色）。

    Args:
        repo_path (Path): fixture 仓库根目录。
        prd_relative_path (str): PRD 相对仓库根的路径；归档场景传 archive 下的那份。
    """
    return prd_status.format_checklist_cell(
        collect_fixture_record(repo_path, prd_relative_path), PLAIN_PALETTE
    )


def render_evidence_cell(repo_path: Path) -> str:
    """渲染 EVIDENCE 单元格（无颜色）。"""
    return prd_status.format_evidence_cell(collect_fixture_record(repo_path), PLAIN_PALETTE)


def checklist_prd_text(checked_item_count: int, unchecked_item_count: int) -> str:
    """生成带指定勾选数量的 fixture PRD 正文，用于构造分支副本与主仓库副本的差异。"""
    checked_items_text = "".join(
        f"- [x] item {item_index}\n" for item_index in range(1, checked_item_count + 1)
    )
    unchecked_items_text = "".join(
        f"- [ ] item {item_index}\n"
        for item_index in range(
            checked_item_count + 1, checked_item_count + unchecked_item_count + 1
        )
    )
    return f"# fixture PRD\n\n## Acceptance Checklist\n\n{checked_items_text}{unchecked_items_text}"


def render_deps_cell(repo_path: Path) -> str:
    """读取 fixture PRD 并渲染 DEPS 单元格（无颜色）。"""
    return prd_deps.format_deps_cell(
        collect_fixture_record(repo_path),
        repo_path / "tasks" / "pending",
        repo_path / "tasks" / "archive",
        PLAIN_PALETTE,
    )


def init_dependency_repo(repo_path: Path, dependency_ref: str, gate_type: str) -> Path:
    """初始化 §8 声明了指定依赖引用与 gate 类型的 fixture 仓库。"""
    return init_main_repo(
        repo_path,
        prd_text=DEPENDENCY_PRD_TEMPLATE.format(dependency_ref=dependency_ref, gate_type=gate_type),
    )


def write_upstream_prd(repo_path: Path, bucket: str) -> Path:
    """在 ``tasks/pending`` 或 ``tasks/archive`` 下写入上游 fixture PRD。"""
    upstream_path = repo_path / "tasks" / bucket / f"{UPSTREAM_PRD_NAME}.md"
    upstream_path.parent.mkdir(parents=True, exist_ok=True)
    upstream_path.write_text("# upstream PRD\n", encoding="utf-8")
    return upstream_path


AWAITING_PRD_NAME = "P2-FEAT-20260101-000002-awaiting-review"
AWAITING_PRD_SLUG = "awaiting-review"


def prd_text_with_acceptance_banner(banner_line: str, *, human_item_mark: str = " ") -> str:
    """生成带指定验收状态横幅行的 fixture PRD 正文。

    横幅行只给状态本身，第二行沿用规范要求的"§9 投影"说明；清单里机器项已勾、
    Human-Confirmed 项默认未勾，即 ``🧍 待人工验收`` 的真实形态。

    Args:
        banner_line (str): 横幅行正文（不含行首 ``> ``）。
        human_item_mark (str): Human-Confirmed 项的勾选标记；传 ``"x"`` 构造人已确认的
            ``✅ 已验收`` 形态。
    """
    return (
        "# fixture PRD\n"
        "\n"
        f"> {banner_line}\n"
        "> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。\n"
        "\n"
        "## 9. Acceptance Checklist\n"
        "\n"
        "#### Human-Confirmed\n"
        "\n"
        "- [x] machine item\n"
        f"- [{human_item_mark}] human item\n"
    )


NOT_STARTED_BANNER_LINE = "⬜ **验收状态**：未开工。"
AWAITING_BANNER_LINE = "🧍 **验收状态**：待人工验收 — 仅剩 1 项未确认。"
ACCEPTED_BANNER_LINE = "✅ **验收状态**：已验收 — 验收清单已全部完成。"
# 归档落地"3 天又 10 分钟前"：多出的 10 分钟让 ``3d0h`` 断言不被测试自身耗时推过整点。
LANDED_THREE_DAYS_AGO_SECONDS = 3 * 86400 + 600


def write_main_archived_prd(
    repo_path: Path, prd_text: str, *, prd_name: str = FIXTURE_PRD_NAME
) -> Path:
    """在主仓库 ``tasks/archive`` 写入一条已归档 fixture PRD（只写文件，不提交）。

    Args:
        repo_path (Path): fixture 仓库根目录。
        prd_text (str): PRD 正文。
        prd_name (str): PRD 文件名（不含 ``.md``）；默认与 ``tasks/pending`` 里的 fixture
            同名，分区用例传 ``AWAITING_PRD_NAME`` 以得到独立的 slug。
    """
    archived_prd_path = repo_path / "tasks" / "archive" / f"{prd_name}.md"
    archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    archived_prd_path.write_text(prd_text, encoding="utf-8")
    return archived_prd_path


def commit_with_backdated_time(repo_path: Path, commit_message: str, seconds_ago: float) -> None:
    """提交全部改动，并把提交时间回拨 ``seconds_ago`` 秒。

    归档落地时间取自"把文件加到该路径的那次提交"；要模拟"已归档几天"只能回拨提交
    时间，改 mtime 影响不到它。
    """
    backdated_date_text = f"{int(datetime.now(timezone.utc).timestamp() - seconds_ago)} +0000"
    run_git(repo_path, "add", ".")
    subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": backdated_date_text,
            "GIT_COMMITTER_DATE": backdated_date_text,
        },
    )


def run_status_main(repo_path: Path, scope_text: str, capsys, monkeypatch) -> str:
    """在 fixture 仓库里以指定 scope 运行 ``prd_status.main()`` 并返回标准输出。"""
    monkeypatch.chdir(repo_path)
    monkeypatch.setattr(sys, "argv", ["prd_status.py", scope_text])
    prd_status.main()
    return capsys.readouterr().out

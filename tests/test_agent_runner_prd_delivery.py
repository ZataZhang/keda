"""Tests for PRD delivery readiness gating.

Covers ``resolve_prd_archive_path`` and ``ensure_prd_delivery_ready``:
acceptance checklist completeness, the acceptance status banner, Change Log
format enforcement and the pending -> archive ``git mv`` transition (archive
once the executor side is done, open Human-Confirmed items included)."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.core.shared import prd_checklist
from backend.core.shared.models.agent_runner import (
    DeliveryGateFailureKind,
    IssueSummary,
)
from backend.core.shared.prd_machine_contract import PRD_MACHINE_CONTRACT_POINTER
from backend.core.use_cases.run_agent_once import (
    PrdDeliveryError,
    ensure_prd_delivery_ready,
    resolve_prd_archive_path,
)
from backend.core.use_cases.agent_runner_feedback import assert_prd_archived_for_publish
from tests.conftest import FakeProcessRunner
from tests.support.agent_runner import (
    ACCEPTED_BANNER,
    AWAITING_HUMAN_BANNER,
    NOT_STARTED_BANNER,
    build_acceptance_prd,
    create_commit,
    init_git_repo,
    make_prd_issue,
    require_banner_aware_prd_skill,
    run_git,
)

_PENDING_RELATIVE_PATH = "tasks/pending/example.md"
_ARCHIVE_RELATIVE_PATH = "tasks/archive/example.md"
_ARCHIVE_RENAME_STATUS = f"R  {_PENDING_RELATIVE_PATH} -> {_ARCHIVE_RELATIVE_PATH}"
_ARCHIVE_CALLS = [
    ["git", "add", "--", _PENDING_RELATIVE_PATH],
    ["git", "mv", _PENDING_RELATIVE_PATH, _ARCHIVE_RELATIVE_PATH],
]


def test_resolve_prd_archive_path_converts_pending() -> None:
    """Pending PRD paths should map to the archive directory."""
    assert resolve_prd_archive_path("tasks/pending/example.md") == "tasks/archive/example.md"


def test_resolve_prd_archive_path_returns_none_for_non_pending() -> None:
    """Non-pending paths should not resolve to an archive path."""
    assert resolve_prd_archive_path("tasks/archive/example.md") is None
    assert resolve_prd_archive_path("docs/example.md") is None


def test_ensure_prd_delivery_ready_skips_when_no_prd_path(tmp_path: Path) -> None:
    """Gate should be a no-op when the Issue has no canonical PRD path."""
    issue = IssueSummary(number=1, title="T", url="U", body="No PRD.", labels=())
    fake_runner = FakeProcessRunner()
    ensure_prd_delivery_ready(issue, tmp_path, fake_runner)
    assert fake_runner.calls == []


def test_ensure_prd_delivery_ready_raises_when_pending_incomplete(
    tmp_path: Path,
) -> None:
    """Pending PRD with unchecked items should raise PrdDeliveryError."""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(
        "\n".join(
            [
                "# PRD",
                "",
                "## Acceptance Checklist",
                "",
                "- [x] done",
                "- [ ] undone",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake_runner = FakeProcessRunner()
    with pytest.raises(PrdDeliveryError, match="unchecked items"):
        ensure_prd_delivery_ready(issue, tmp_path, fake_runner)


def test_open_human_item_no_longer_keeps_prd_pending(tmp_path: Path) -> None:
    """人审空框不再让 PRD 滞留 pending：执行侧完成即 ``git add`` + ``git mv`` 归档。

    归档不替人回答——runner 不改 PRD 内容，空框与 🧍 横幅原样留给人。
    """
    pending_path = tmp_path / _PENDING_RELATIVE_PATH
    pending_path.parent.mkdir(parents=True)
    (tmp_path / "tasks" / "archive").mkdir()
    prd_text = build_acceptance_prd(AWAITING_HUMAN_BANNER, human_marks=(" ",))
    pending_path.write_text(prd_text, encoding="utf-8")
    fake_runner = FakeProcessRunner()

    ensure_prd_delivery_ready(make_prd_issue(), tmp_path, fake_runner)

    assert fake_runner.calls == _ARCHIVE_CALLS
    assert pending_path.read_text(encoding="utf-8") == prd_text


def test_deferred_human_review_gate_no_longer_blocks_archive(tmp_path: Path) -> None:
    """老 PRD 把人审项写成 ``[~]`` 也照常归档；合并队列会按保守口径继续等人。"""
    pending_path = tmp_path / _PENDING_RELATIVE_PATH
    pending_path.parent.mkdir(parents=True)
    (tmp_path / "tasks" / "archive").mkdir()
    pending_path.write_text(
        build_acceptance_prd(ACCEPTED_BANNER, human_marks=("~",)), encoding="utf-8"
    )
    fake_runner = FakeProcessRunner()

    ensure_prd_delivery_ready(make_prd_issue(), tmp_path, fake_runner)

    assert fake_runner.calls == _ARCHIVE_CALLS


def test_archived_prd_with_open_human_item_passes_both_gates(tmp_path: Path) -> None:
    """已归档、横幅为 🧍、只剩人审空框的 PRD 两道门禁都放行，且不再移动。

    旧语义在这里以 "awaits human review" 拒绝；v5 下人审空框不拦交付与发布。
    """
    archive_path = tmp_path / _ARCHIVE_RELATIVE_PATH
    archive_path.parent.mkdir(parents=True)
    archive_path.write_text(
        build_acceptance_prd(AWAITING_HUMAN_BANNER, human_marks=(" ",)), encoding="utf-8"
    )
    issue = make_prd_issue()
    fake_runner = FakeProcessRunner()

    ensure_prd_delivery_ready(issue, tmp_path, fake_runner)
    assert_prd_archived_for_publish(issue, tmp_path)

    assert fake_runner.calls == []


def test_ensure_prd_delivery_ready_requires_change_log_for_prd_change(
    tmp_path: Path,
) -> None:
    """本轮修改 PRD 时必须有独立于 Checklist 的结构化 Change Log。"""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_content = "# PRD\n\n## Acceptance Checklist\n\n- [x] done\n"
    prd_path.write_text(
        baseline_content + "\n新增了实现细节。\n",
        encoding="utf-8",
    )
    fake_runner = FakeProcessRunner()

    with pytest.raises(PrdDeliveryError, match="without a Change Log section"):
        ensure_prd_delivery_ready(
            issue,
            tmp_path,
            fake_runner,
            prd_baseline_content=baseline_content,
        )


def test_ensure_prd_delivery_ready_requires_new_change_log_entry(
    tmp_path: Path,
) -> None:
    """已有 Change Log 时，每次新的 PRD 修改仍须追加一条记录。"""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    baseline_content = """# PRD

## Change Log

### 2026-07-13 · Earlier change
- 类型：实现细化
- 原文：原实现
- 变更后：新实现
- 原因：补齐边界
- 影响：无用户可见变化
- 审核：已记录

## Acceptance Checklist

- [x] done
"""
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(
        baseline_content.replace("# PRD", "# PRD\n\n新的实现说明"),
        encoding="utf-8",
    )
    fake_runner = FakeProcessRunner()

    with pytest.raises(PrdDeliveryError, match="without appending a Change Log entry"):
        ensure_prd_delivery_ready(
            issue,
            tmp_path,
            fake_runner,
            prd_baseline_content=baseline_content,
        )


def _prd_body_with_table_change_log(baseline_content: str) -> str:
    """构造把 Change Log 写成 Markdown 表格的 PRD（解析器会数成 0 条）。"""
    return (
        baseline_content
        + "\n新的实现说明\n\n## Change Log\n\n"
        + "| # | Type | Before | After | Reason | Impact | Review |\n"
        + "|---|------|--------|-------|--------|--------|--------|\n"
        + "| CL-1 | evidence | 旧 | 新 | 补齐证据 | 无用户可见变化 | 待审 |\n"
    )


def test_ensure_prd_delivery_ready_rejects_table_change_log(tmp_path: Path) -> None:
    """Change Log 写成表格时门禁必须报错并解释表格行不被计入。

    这是历史 recovery 死循环的根因：agent 用表格记录变更、解析器数成 0 条，
    错误信息又不说明原因，agent 每轮只会往表格里再补一行。
    """
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_content = "# PRD\n\n## Acceptance Checklist\n\n- [x] done\n"
    prd_path.write_text(
        _prd_body_with_table_change_log(baseline_content),
        encoding="utf-8",
    )
    fake_runner = FakeProcessRunner()

    with pytest.raises(
        PrdDeliveryError,
        match="without a Change Log entry.*table rows are not counted",
    ):
        ensure_prd_delivery_ready(
            issue,
            tmp_path,
            fake_runner,
            prd_baseline_content=baseline_content,
        )


def test_ensure_prd_delivery_ready_accepts_bullet_change_log(tmp_path: Path) -> None:
    """规范的 ``###`` 标题 + bullet 字段格式必须通过 Change Log 门禁。"""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tasks" / "archive").mkdir(parents=True, exist_ok=True)
    baseline_content = f"# PRD\n\n{ACCEPTED_BANNER}\n\n## Acceptance Checklist\n\n- [x] done\n"
    prd_path.write_text(
        baseline_content
        + "\n## Change Log\n\n"
        + "### 2026-07-24 · 验收更新\n"
        + "- Type: evidence\n"
        + "- Before: 验收项未完成\n"
        + "- After: 验收项已完成\n"
        + "- Reason: 已执行缺失验证\n"
        + "- Impact: 无用户可见变化\n"
        + "- Review: runner 门禁待验证\n",
        encoding="utf-8",
    )
    fake_runner = FakeProcessRunner()

    # 不抛异常即视为通过；runner 会随后 git add + git mv 归档。
    ensure_prd_delivery_ready(
        issue,
        tmp_path,
        fake_runner,
        prd_baseline_content=baseline_content,
    )


def test_ensure_prd_delivery_ready_validates_change_log_on_archive_path(
    tmp_path: Path,
) -> None:
    """agent 私自把 PRD 移进 archive/ 也不能绕过 Change Log 门禁。"""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    baseline_content = "# PRD\n\n## Acceptance Checklist\n\n- [x] done\n"
    # 模拟 agent 违规 git mv：pending 路径不存在，archive 路径存在且 Change Log 是表格。
    archive_path = tmp_path / "tasks" / "archive" / "example.md"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(
        _prd_body_with_table_change_log(baseline_content),
        encoding="utf-8",
    )
    fake_runner = FakeProcessRunner()

    with pytest.raises(PrdDeliveryError, match="without a Change Log entry"):
        ensure_prd_delivery_ready(
            issue,
            tmp_path,
            fake_runner,
            prd_baseline_content=baseline_content,
        )


def test_ensure_prd_delivery_ready_git_mv_when_pending_complete(
    tmp_path: Path,
) -> None:
    """Complete pending PRD should be moved to archive by git mv."""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    archive_dir = tmp_path / "tasks" / "archive"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(build_acceptance_prd(ACCEPTED_BANNER), encoding="utf-8")
    fake_runner = FakeProcessRunner()
    ensure_prd_delivery_ready(issue, tmp_path, fake_runner)
    # The on-disk PRD is staged before the move so ``git mv`` cannot abort with
    # "not under version control" when the file is untracked (e.g. left behind
    # by a PRD rewrite that overwrote it without re-staging).
    add_call = ["git", "add", "--", "tasks/pending/example.md"]
    mv_call = [
        "git",
        "mv",
        "tasks/pending/example.md",
        "tasks/archive/example.md",
    ]
    assert add_call in fake_runner.calls
    assert mv_call in fake_runner.calls
    assert fake_runner.calls.index(add_call) < fake_runner.calls.index(mv_call)


def test_ensure_prd_delivery_ready_passes_when_archive_complete(
    tmp_path: Path,
) -> None:
    """Archived PRD with all items checked should pass the gate."""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    archive_path = tmp_path / "tasks" / "archive" / "example.md"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(build_acceptance_prd(ACCEPTED_BANNER), encoding="utf-8")
    fake_runner = FakeProcessRunner()
    ensure_prd_delivery_ready(issue, tmp_path, fake_runner)
    assert fake_runner.calls == []


def test_ensure_prd_delivery_ready_raises_when_missing_section(
    tmp_path: Path,
) -> None:
    """PRD without Acceptance Checklist section should raise PrdDeliveryError."""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text("# PRD\n", encoding="utf-8")
    fake_runner = FakeProcessRunner()
    with pytest.raises(PrdDeliveryError, match="Acceptance Checklist section missing"):
        ensure_prd_delivery_ready(issue, tmp_path, fake_runner)


def test_ensure_prd_delivery_ready_raises_when_prd_missing(
    tmp_path: Path,
) -> None:
    """Missing canonical PRD should raise PrdDeliveryError."""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    fake_runner = FakeProcessRunner()
    with pytest.raises(PrdDeliveryError, match="Canonical PRD not found"):
        ensure_prd_delivery_ready(issue, tmp_path, fake_runner)


def test_ensure_prd_delivery_ready_raises_when_archive_dir_missing(
    tmp_path: Path,
) -> None:
    """Pending PRD ready for archive but missing archive dir should raise PrdDeliveryError."""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(build_acceptance_prd(ACCEPTED_BANNER), encoding="utf-8")
    fake_runner = FakeProcessRunner()
    with pytest.raises(PrdDeliveryError, match="Archive directory does not exist"):
        ensure_prd_delivery_ready(issue, tmp_path, fake_runner)


def test_ensure_prd_delivery_ready_archives_untracked_prd_real_git(
    tmp_path: Path,
) -> None:
    """A complete PRD present on disk but missing from the index is archived.

    Reproduces the runner failure where a PRD rewrite left the pending file
    untracked with its deletion staged: ``git mv`` alone aborts with "not under
    version control" (exit 128). The archive step must stage the on-disk file
    first so the move succeeds and the rewritten content is preserved.
    """
    from backend.infrastructure.process_runner import SubprocessRunner

    repo = init_git_repo(tmp_path)
    pending_path = tmp_path / "tasks" / "pending" / "example.md"
    archive_dir = tmp_path / "tasks" / "archive"
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / ".gitkeep").write_text("", encoding="utf-8")
    prd_body = build_acceptance_prd(ACCEPTED_BANNER)
    pending_path.write_text(prd_body, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"],
        check=True,
        capture_output=True,
    )
    create_commit(repo, "publish prd")

    # Reproduce the broken worktree state: drop the PRD from the index and
    # overwrite it on disk, leaving it untracked with its deletion staged.
    subprocess.run(
        ["git", "-C", str(repo), "rm", "--cached", "tasks/pending/example.md"],
        check=True,
        capture_output=True,
    )
    rewritten_body = prd_body + "<!-- rewritten -->\n"
    pending_path.write_text(rewritten_body, encoding="utf-8")

    issue = IssueSummary(
        number=7,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )

    ensure_prd_delivery_ready(issue, tmp_path, SubprocessRunner())

    archived_path = archive_dir / "example.md"
    assert not pending_path.exists()
    assert archived_path.exists()
    assert archived_path.read_text(encoding="utf-8") == rewritten_body
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "tasks/archive/example.md"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tracked == "tasks/archive/example.md"


def _print_delivery_record(stage_label: str, repo_path: Path) -> None:
    """打印交付前后的 git 状态与目录列表；``pytest -rP`` 收进证据报告。"""
    print(f"== {stage_label} ==")
    print("$ git status --short")
    print(run_git(repo_path, "status", "--short").rstrip() or "(clean)")
    for directory_name in ("pending", "archive"):
        listed_names = sorted(
            entry_path.name for entry_path in (repo_path / "tasks" / directory_name).iterdir()
        )
        print(f"$ ls -A tasks/{directory_name}")
        print("\n".join(listed_names) or "(empty)")


def _commit_pending_prd(repo_path: Path, prd_text: str) -> Path:
    """在真实 git 仓库里提交一份 pending PRD（archive 目录已存在），返回 pending 路径。"""
    init_git_repo(repo_path)
    pending_path = repo_path / _PENDING_RELATIVE_PATH
    archive_dir = repo_path / "tasks" / "archive"
    pending_path.parent.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    (archive_dir / ".gitkeep").write_text("", encoding="utf-8")
    pending_path.write_text(prd_text, encoding="utf-8")
    run_git(repo_path, "add", "-A")
    create_commit(repo_path, "publish prd")
    return pending_path


def _assert_prd_not_moved(repo_path: Path) -> None:
    """PRD 仍在 pending、archive 下没有它，git 索引也没有 rename。"""
    assert (repo_path / _PENDING_RELATIVE_PATH).exists()
    assert not (repo_path / _ARCHIVE_RELATIVE_PATH).exists()
    assert run_git(repo_path, "status", "--porcelain") == ""


def test_delivery_archives_prd_awaiting_human_review_real_git(tmp_path: Path) -> None:
    """rv-1：执行侧完成、只剩人审空框的 PRD 在交付时就归档，空框与横幅原样保留。

    与 runner 成功路径同一调用顺序（交付检查 → 发布前检查）；git 走真实
    ``SubprocessRunner``，PRD 解析走本机安装的 prd skill，不打桩。
    """
    from backend.infrastructure.process_runner import SubprocessRunner

    pending_path = _commit_pending_prd(
        tmp_path,
        build_acceptance_prd(AWAITING_HUMAN_BANNER, execution_marks=("x", "~"), human_marks=(" ",)),
    )
    archive_path = tmp_path / _ARCHIVE_RELATIVE_PATH
    pending_bytes = pending_path.read_bytes()
    issue = make_prd_issue()
    _print_delivery_record("before delivery", tmp_path)

    ensure_prd_delivery_ready(issue, tmp_path, SubprocessRunner())
    assert_prd_archived_for_publish(issue, tmp_path)

    _print_delivery_record("after delivery", tmp_path)
    archived_text = archive_path.read_text(encoding="utf-8")
    print("== archived PRD: banner line and Human-Confirmed group ==")
    print(next(line for line in archived_text.splitlines() if line.startswith("> ")))
    print(archived_text[archived_text.index("### Human-Confirmed") :].rstrip())
    # fresh-state probe：另起 git 进程读索引，并从磁盘重新读归档文件比对字节。
    status_lines = run_git(tmp_path, "status", "--porcelain").splitlines()
    assert _ARCHIVE_RENAME_STATUS in status_lines
    assert run_git(tmp_path, "ls-files", _ARCHIVE_RELATIVE_PATH).strip() == _ARCHIVE_RELATIVE_PATH
    assert run_git(tmp_path, "ls-files", _PENDING_RELATIVE_PATH).strip() == ""
    assert not pending_path.exists()
    assert archive_path.read_bytes() == pending_bytes


def test_delivery_keeps_prd_pending_while_an_executor_item_is_open_real_git(
    tmp_path: Path,
) -> None:
    """rv-2：执行侧还有 1 项未勾时不归档，失败信息点名该条目；横幅一致也不例外。"""
    from backend.infrastructure.process_runner import SubprocessRunner

    _commit_pending_prd(
        tmp_path,
        build_acceptance_prd(AWAITING_HUMAN_BANNER, execution_marks=("x", " "), human_marks=(" ",)),
    )

    with pytest.raises(PrdDeliveryError) as exc_info:
        ensure_prd_delivery_ready(make_prd_issue(), tmp_path, SubprocessRunner())

    assert exc_info.value.kind is DeliveryGateFailureKind.CHECKLIST_UNCHECKED
    assert "- [ ] rv-2: executor-owned item 2" in str(exc_info.value)
    # 人审空框不算执行侧未勾，不能被点名成"漏勾"。
    assert "decision 1" not in str(exc_info.value)
    _assert_prd_not_moved(tmp_path)


@pytest.mark.parametrize(
    ("banner_line", "human_marks", "current_state", "expected_state"),
    (
        pytest.param(NOT_STARTED_BANNER, (" ",), "not_started", "awaiting_human", id="not-started"),
        pytest.param(None, (" ",), "missing or unrecognized", "awaiting_human", id="missing"),
        pytest.param(ACCEPTED_BANNER, (" ",), "accepted", "awaiting_human", id="accepted-but-open"),
        pytest.param(
            AWAITING_HUMAN_BANNER, ("x",), "awaiting_human", "accepted", id="awaiting-but-answered"
        ),
    ),
)
def test_banner_mismatch_blocks_archive_and_publish_real_git(
    tmp_path: Path,
    banner_line: str | None,
    human_marks: tuple[str, ...],
    current_state: str,
    expected_state: str,
) -> None:
    """rv-3：横幅与清单不一致时不归档、也不发布；失败种类可交给收尾回合。

    交付入口用真实 git；随后模拟 agent 违规自行 ``git mv``，发布前检查同样拦下。
    """
    from backend.infrastructure.process_runner import SubprocessRunner

    require_banner_aware_prd_skill()
    _commit_pending_prd(tmp_path, build_acceptance_prd(banner_line, human_marks=human_marks))
    issue = make_prd_issue()

    with pytest.raises(PrdDeliveryError) as delivery_error:
        ensure_prd_delivery_ready(issue, tmp_path, SubprocessRunner())

    assert delivery_error.value.kind is DeliveryGateFailureKind.ACCEPTANCE_BANNER_MISMATCH
    assert delivery_error.value.kind.is_closeout_eligible
    assert f"the banner state is `{current_state}`" in str(delivery_error.value)
    assert f"must be `{expected_state}`" in str(delivery_error.value)
    _assert_prd_not_moved(tmp_path)

    run_git(tmp_path, "mv", _PENDING_RELATIVE_PATH, _ARCHIVE_RELATIVE_PATH)
    with pytest.raises(PrdDeliveryError) as publish_error:
        assert_prd_archived_for_publish(issue, tmp_path)

    assert publish_error.value.kind is DeliveryGateFailureKind.ACCEPTANCE_BANNER_MISMATCH
    assert f"must be `{expected_state}`" in str(publish_error.value)


def test_prd_without_human_items_archives_exactly_as_before_real_git(tmp_path: Path) -> None:
    """rv-7 第一组：没有人审项、横幅为 ✅ 的 PRD，归档时机与提交内容与改动前一致。

    同一用例在未修改的 src 上的输出留作基线（见证据报告），两边的暂存区与字节必须相同。
    """
    from backend.infrastructure.process_runner import SubprocessRunner

    pending_path = _commit_pending_prd(tmp_path, build_acceptance_prd(ACCEPTED_BANNER))
    pending_bytes = pending_path.read_bytes()
    issue = make_prd_issue()

    ensure_prd_delivery_ready(issue, tmp_path, SubprocessRunner())
    assert_prd_archived_for_publish(issue, tmp_path)

    status_lines = run_git(tmp_path, "status", "--porcelain").splitlines()
    print("$ git status --porcelain")
    print("\n".join(status_lines))
    assert status_lines == [_ARCHIVE_RENAME_STATUS]
    assert (tmp_path / _ARCHIVE_RELATIVE_PATH).read_bytes() == pending_bytes


def _pre_v5_contract_parser(original_parser: Any) -> Any:
    """把真实 skill 的契约 JSON 剥成 v3/v4 形状（无横幅状态、无人审空框列表）。"""

    def parse_like_pre_v5_skill(file_content: str) -> dict[str, Any]:
        contract_payload = copy.deepcopy(original_parser(file_content))
        contract_payload.pop("acceptance_status", None)
        contract_payload.get("checklist", {}).pop("human_unchecked", None)
        return contract_payload

    return parse_like_pre_v5_skill


def test_pre_v5_skill_skips_the_banner_check_real_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rv-7 第二组：skill 读不出横幅（v3/v4）时不触发横幅检查，只按执行侧判据归档。

    打桩只发生在测试边界的 ``parse_prd_contract``：保留真实 skill 的其余输出，
    只删掉 v5 新增的两个键，模拟本机仍装着旧版 skill。
    """
    from backend.infrastructure.process_runner import SubprocessRunner

    monkeypatch.setattr(
        prd_checklist,
        "parse_prd_contract",
        _pre_v5_contract_parser(prd_checklist.parse_prd_contract),
    )
    _commit_pending_prd(tmp_path, build_acceptance_prd(NOT_STARTED_BANNER, human_marks=(" ",)))
    issue = make_prd_issue()

    ensure_prd_delivery_ready(issue, tmp_path, SubprocessRunner())
    assert_prd_archived_for_publish(issue, tmp_path)

    assert run_git(tmp_path, "status", "--porcelain").splitlines() == [_ARCHIVE_RENAME_STATUS]


# ---------------------------------------------------------------------------
# 门禁失败分类：收尾类 vs 真失败类
# ---------------------------------------------------------------------------

_CHECKLIST_BASELINE = f"# PRD\n\n{ACCEPTED_BANNER}\n\n## Acceptance Checklist\n\n- [x] done\n"
_COMPLETE_CHANGE_LOG_ENTRY = "\n".join(
    [
        "",
        "### 已有条目",
        "- 类型：范围",
        "- 原文：旧",
        "- 变更后：新",
        "- 原因：需要",
        "- 影响：无",
        "- 审核：已记录",
        "",
    ]
)


def _write_prd(tmp_path: Path, content: str) -> None:
    """Write the canonical pending PRD used by the classification cases."""
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    ("prd_content", "baseline_content", "expected_kind"),
    (
        # 清单未勾 → 收尾类。
        (
            "# PRD\n\n## Acceptance Checklist\n\n- [x] done\n- [ ] undone\n",
            None,
            DeliveryGateFailureKind.CHECKLIST_UNCHECKED,
        ),
        # 清单章节整体缺失 → PRD 结构有问题，保持真失败类。
        ("# PRD\n", None, DeliveryGateFailureKind.SUBSTANTIVE),
        # 改了 PRD 却没有 Change Log 章节 → 收尾类。
        (
            _CHECKLIST_BASELINE + "\n新增实现说明。\n",
            _CHECKLIST_BASELINE,
            DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE,
        ),
        # 有章节但一条都解析不出（表格写法）→ 收尾类。
        (
            _CHECKLIST_BASELINE + "\n## Change Log\n\n| 类型 | 原文 |\n|---|---|\n| 范围 | 旧 |\n",
            _CHECKLIST_BASELINE,
            DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE,
        ),
        # 改了 PRD 但没有相对基线追加新条目 → 收尾类。
        (
            _CHECKLIST_BASELINE
            + "\n## Change Log\n"
            + _COMPLETE_CHANGE_LOG_ENTRY
            + "\n实现说明。\n",
            _CHECKLIST_BASELINE + "\n## Change Log\n" + _COMPLETE_CHANGE_LOG_ENTRY,
            DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE,
        ),
        # 新条目缺字段 → 收尾类。
        (
            _CHECKLIST_BASELINE + "\n## Change Log\n\n### 新条目\n- 类型：范围\n",
            _CHECKLIST_BASELINE,
            DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE,
        ),
    ),
)
def test_prd_delivery_errors_carry_their_closeout_classification(
    tmp_path: Path,
    prd_content: str,
    baseline_content: str | None,
    expected_kind: DeliveryGateFailureKind,
) -> None:
    """每个 PRD 交付抛出点都在抛出处声明分类，不靠事后匹配错误文案。"""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    _write_prd(tmp_path, prd_content)

    with pytest.raises(PrdDeliveryError) as exc_info:
        ensure_prd_delivery_ready(
            issue,
            tmp_path,
            FakeProcessRunner(),
            prd_baseline_content=baseline_content,
        )

    assert exc_info.value.kind is expected_kind


def test_missing_canonical_prd_stays_substantive(tmp_path: Path) -> None:
    """PRD 文件根本不存在 → 真失败类，整轮重跑。"""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )

    with pytest.raises(PrdDeliveryError) as exc_info:
        ensure_prd_delivery_ready(issue, tmp_path, FakeProcessRunner())

    assert exc_info.value.kind is DeliveryGateFailureKind.SUBSTANTIVE


def test_missing_archive_dir_stays_substantive(tmp_path: Path) -> None:
    """归档目录缺失是仓库结构问题 → 真失败类。"""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    _write_prd(tmp_path, _CHECKLIST_BASELINE)

    with pytest.raises(PrdDeliveryError) as exc_info:
        ensure_prd_delivery_ready(issue, tmp_path, FakeProcessRunner())

    assert "Archive directory does not exist" in str(exc_info.value)
    assert exc_info.value.kind is DeliveryGateFailureKind.SUBSTANTIVE


def _write_pending_prd(tmp_path: Path, body: str) -> IssueSummary:
    """把给定正文写进一个 pending PRD，返回引用它的 Issue。"""
    issue = IssueSummary(
        number=1,
        title="T",
        url="U",
        body="PRD path: `tasks/pending/example.md`",
        labels=(),
    )
    prd_path = tmp_path / "tasks" / "pending" / "example.md"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(body, encoding="utf-8")
    return issue


def test_gate_error_names_an_unrecognized_human_group(tmp_path: Path) -> None:
    """提到 Human-Confirmed 却没识别出分组时，报错本身要指出是结构问题。

    否则 agent 只会看到"这几项未勾选"，而它的规则又要求人属项保持未勾，
    于是一边是不可满足的指令、一边是看不见的病因（实证：ai-assistant #53）。
    """
    issue = _write_pending_prd(
        tmp_path,
        "\n".join(
            [
                "## Acceptance Checklist",
                "",
                "Human-Confirmed:",
                "",
                "- [ ] 决定一：人属项",
                "",
                "### Behavior Acceptance",
                "",
                "- [ ] rv-1 PASS",
                "",
            ]
        ),
    )

    with pytest.raises(PrdDeliveryError, match="Suspected checklist-structure problem") as excinfo:
        ensure_prd_delivery_ready(issue, tmp_path, FakeProcessRunner())

    message = str(excinfo.value)
    assert "no Human-Confirmed group was recognized" in message
    # 只报事实 + 指向契约，不复述格式：分组写法由 prd skill 的 Machine Contract
    # 定义，keda 里再写一份就成了第二出处（乃至发明契约没有的变体）。
    assert PRD_MACHINE_CONTRACT_POINTER in message
    assert "###" not in message
    assert "**" not in message


def test_gate_error_omits_the_hint_when_the_group_is_recognized(tmp_path: Path) -> None:
    """分组已被正常识别时不要追加结构诊断，避免误导。"""
    issue = _write_pending_prd(
        tmp_path,
        "\n".join(
            [
                "## Acceptance Checklist",
                "",
                "### Human-Confirmed",
                "",
                "- [ ] 决定一：人属项",
                "",
                "### Behavior Acceptance",
                "",
                "- [ ] rv-1 PASS",
                "",
            ]
        ),
    )

    with pytest.raises(PrdDeliveryError, match="unchecked items") as excinfo:
        ensure_prd_delivery_ready(issue, tmp_path, FakeProcessRunner())

    assert "Suspected checklist-structure problem" not in str(excinfo.value)


def test_gate_error_omits_the_hint_when_human_group_is_absent(tmp_path: Path) -> None:
    """完全不涉及人属项的 PRD 不该被结构诊断干扰。"""
    issue = _write_pending_prd(
        tmp_path,
        "\n".join(
            [
                "## Acceptance Checklist",
                "",
                "### Validation Acceptance",
                "",
                "- [ ] rv-1 PASS",
                "",
            ]
        ),
    )

    with pytest.raises(PrdDeliveryError, match="unchecked items") as excinfo:
        ensure_prd_delivery_ready(issue, tmp_path, FakeProcessRunner())

    assert "Suspected checklist-structure problem" not in str(excinfo.value)

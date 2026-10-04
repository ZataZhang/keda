"""守护 PRD 状态看板验收轴的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：验收状态横幅的读法（``prd_acceptance.py``）、``prd_status.py`` 的 AWAITING HUMAN
分区与 ARCHIVE 月视图，以及 ``prd_activity.py`` 对待人工记录的 ACTIVITY 渲染。目录轴
（pending / archive）与验收轴（横幅）相互正交，worktree / 依赖 / 证据信号见
``test_prd_status.py``。核心不变量：

1. **AWAITING HUMAN 分区只认"已归档的副本 + 🧍 横幅"，且一个 PRD 只出现一次。**
   归档只代表执行侧交付完成，人工验收是另一条轴线：看板读到的那份副本已在
   ``tasks/archive``（主仓库里，或分支 worktree 里已归档、只差合并），且横幅为
   ``🧍 待人工验收``，才算"机器证据齐备、只等人确认"，并从目录分区里摘出，任何 scope
   下非空时都要露出。**落在 ``tasks/pending`` 的副本即使横幅写着 🧍 也留在 PENDING**——
   归档与翻横幅在同一次交付里完成，pending 里的 🧍 只可能是重开后忘了复位、或翻了
   横幅还没归档，执行侧的活可能还没做完，不能让陈旧的横幅把它从待办里摘走。同名 PRD
   在 pending 与 archive 并存时以 pending 副本为准，archive 那份不入该段、留在 ARCHIVE
   里被月视图报 ⚠。横幅是 §9 验收清单的唯一投影，看板读它同样走分支副本（执行方在
   worktree 里翻的状态才算数）。只接受引用块行（``>`` 开头）里带 ``验收状态`` /
   ``Acceptance Status`` 的声明，并取标记之后**最先出现**的状态词——模板括号里的提示文字
   不得抢占行首的 ``⬜ 未开工``；旧称 ``✅ 可归档`` 按 ``已验收`` 读。没有横幅或认不出
   状态词时按未开工处理、留在 PENDING：按 §9 未勾项结构反推会漏判真实的人工项（常挂在
   ``Human-Confirmed`` 之外的小节下），也会把尚未完工的 PRD 误报成"等你验收"。分区只做
   呈现，不搬文件。
2. **ARCHIVE 月视图的 ``⚠ 有未勾完的清单`` 只表示异常。** 待人工记录已被摘到
   AWAITING HUMAN，剩在 ARCHIVE 里的记录清单没勾完就是真异常（横幅写着已验收却仍有
   空框、历史记录缺横幅、与 pending 副本并存的残留等），不能被"等人验收"的空框稀释。
3. **已归档且待人工的记录，ACTIVITY 显示 ``🧍 waiting <时长>``。** 从归档落地的
   提交时间起算（工作树里未被 git 追踪时退回 mtime），让验收积压多久一眼可见；有活跃
   锁时仍显示锁状态——有人正在重开或回填，比"在等"更要紧；过期锁（无活性佐证）不盖住
   "在等"，渲染成 ``🧍 waiting <时长> · stale lock``，已验收的记录则照旧显示 STALE。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# prd_*.py 脚本与共用 fixtures 不是包的一部分，import 前需把它们所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
_SHARED_GUARDS_PATH = Path(__file__).resolve().parent
for _import_path in (_JUST_SCRIPTS_PATH, _SHARED_GUARDS_PATH):
    if str(_import_path) not in sys.path:
        sys.path.insert(0, str(_import_path))

import prd_acceptance  # noqa: E402
import prd_activity  # noqa: E402
import prd_status  # noqa: E402
from prd_status_fixtures import (  # noqa: E402
    ACCEPTED_BANNER_LINE,
    AWAITING_BANNER_LINE,
    AWAITING_PRD_NAME,
    AWAITING_PRD_SLUG,
    DEFAULT_FIXTURE_PRD_TEXT,
    FIXTURE_PRD_ARCHIVE_RELATIVE_PATH,
    FIXTURE_PRD_RELATIVE_PATH,
    LANDED_THREE_DAYS_AGO_SECONDS,
    NOT_STARTED_BANNER_LINE,
    add_linked_worktree,
    collect_fixture_record,
    commit_with_backdated_time,
    init_main_repo,
    prd_text_with_acceptance_banner,
    render_activity_cell,
    run_status_main,
    write_lock,
    write_main_archived_prd,
)


def test_acceptance_status_banner_reads_first_state_after_marker() -> None:
    """三态各取标记之后最先出现的状态词；模板括号里的备注不得抢占行首状态。"""
    assert (
        prd_acceptance.parse_acceptance_status(
            prd_text_with_acceptance_banner(
                "🧍 **验收状态**：待人工验收 — 仅剩 4 项 Human-Confirmed 未确认。"
            )
        )
        == prd_acceptance.ACCEPTANCE_STATUS_AWAITING_HUMAN
    )
    assert (
        prd_acceptance.parse_acceptance_status(
            prd_text_with_acceptance_banner(
                "⬜ **验收状态**：未开工。（归档时与 §9 对齐：Human-Confirmed 仍有空框 → "
                "🧍 待人工验收，全部由人确认后 → ✅ 已验收；⬜ 不可归档）"
            )
        )
        == prd_acceptance.ACCEPTANCE_STATUS_NOT_STARTED
    )
    assert (
        prd_acceptance.parse_acceptance_status(
            prd_text_with_acceptance_banner("✅ **验收状态**：已验收 — 验收清单已全部完成。")
        )
        == prd_acceptance.ACCEPTANCE_STATUS_ACCEPTED
    )


def test_legacy_ready_to_archive_banner_reads_as_accepted() -> None:
    """旧称 ``✅ 可归档`` 是 ``已验收`` 的同义词：存量 PRD 不必回头改文件。"""
    assert (
        prd_acceptance.parse_acceptance_status(
            prd_text_with_acceptance_banner(
                "✅ **Acceptance Status**：可归档 — 验收清单已全部完成。"
            )
        )
        == prd_acceptance.ACCEPTANCE_STATUS_ACCEPTED
    )


def test_acceptance_status_ignores_mentions_outside_banner_line() -> None:
    """正文与决策日志里对该横幅的讨论不是状态声明：非引用块行一律不认。"""
    raw_decision_log_text = (
        "# fixture PRD\n"
        "\n"
        "## 13. Decision Log\n"
        "\n"
        "- 收尾时把验收状态横幅翻成 🧍 待人工验收。\n"
    )

    assert prd_acceptance.parse_acceptance_status(raw_decision_log_text) == ""
    assert prd_acceptance.parse_acceptance_status(DEFAULT_FIXTURE_PRD_TEXT) == ""


def test_awaiting_human_section_sits_between_pending_and_archive(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """AWAITING HUMAN 单独成段，夹在 PENDING 与 ARCHIVE 之间；未开工的那条仍留在 PENDING。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(
        main_repo_path,
        prd_text_with_acceptance_banner(AWAITING_BANNER_LINE),
        prd_name=AWAITING_PRD_NAME,
    )

    raw_output_text = run_status_main(main_repo_path, "status", capsys, monkeypatch)

    pending_section_index = raw_output_text.index("PENDING (1)")
    awaiting_section_index = raw_output_text.index("AWAITING HUMAN (1)")
    archive_section_index = raw_output_text.index("ARCHIVE (0)")
    assert raw_output_text.index("avatar-upload") < awaiting_section_index
    assert pending_section_index < awaiting_section_index < raw_output_text.index(AWAITING_PRD_SLUG)
    assert raw_output_text.index(AWAITING_PRD_SLUG) < archive_section_index


@pytest.mark.parametrize(
    ("copy_location", "expected_pending_count"),
    [("main-pending", 2), ("branch-pending", 1)],
)
def test_pending_copy_with_awaiting_banner_stays_in_pending(
    copy_location: str, expected_pending_count: int, tmp_path: Path, capsys, monkeypatch
) -> None:
    """落在 ``tasks/pending`` 的副本即使横幅写着 🧍，也留在 PENDING，不进 AWAITING HUMAN。

    归档与翻横幅在同一次交付里完成，所以 pending 里的 🧍 只可能是重开后忘了复位、
    或翻了横幅还没归档——执行侧的活可能还没做完，不能让陈旧的横幅把它从待办里摘走。
    """
    main_repo_path = init_main_repo(tmp_path / "repo")
    awaiting_prd_text = prd_text_with_acceptance_banner(AWAITING_BANNER_LINE)
    if copy_location == "main-pending":
        prd_relative_path = f"tasks/pending/{AWAITING_PRD_NAME}.md"
        (main_repo_path / prd_relative_path).write_text(awaiting_prd_text, encoding="utf-8")
    else:
        # 分支副本也只在 pending：执行方在 worktree 里翻了横幅，但还没把 PRD 移进 archive。
        prd_relative_path = FIXTURE_PRD_RELATIVE_PATH
        linked_worktree_path = add_linked_worktree(
            main_repo_path, "feat/avatar-upload", "wt-flipped-not-archived"
        )
        (linked_worktree_path / prd_relative_path).write_text(awaiting_prd_text, encoding="utf-8")

    prd_record = collect_fixture_record(main_repo_path, prd_relative_path)
    raw_output_text = run_status_main(main_repo_path, "status", capsys, monkeypatch)

    assert prd_record.acceptance_status == prd_acceptance.ACCEPTANCE_STATUS_AWAITING_HUMAN
    assert not prd_record.awaits_human_review
    assert f"PENDING ({expected_pending_count})" in raw_output_text
    assert "AWAITING HUMAN" not in raw_output_text


def test_pending_prd_without_banner_is_not_inferred_as_awaiting(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """没有横幅时不按 §9 结构反推：只剩 Human-Confirmed 未勾也留在 PENDING。"""
    main_repo_path = init_main_repo(
        tmp_path / "repo",
        prd_text=(
            "# fixture PRD\n"
            "\n"
            "## Acceptance Checklist\n"
            "\n"
            "#### Human-Confirmed\n"
            "\n"
            "- [x] machine item\n"
            "- [ ] human item\n"
        ),
    )
    monkeypatch.chdir(main_repo_path)
    monkeypatch.setattr(sys, "argv", ["prd_status.py", "status"])

    prd_status.main()
    raw_output_text = capsys.readouterr().out

    assert "PENDING (1)" in raw_output_text
    assert "AWAITING HUMAN" not in raw_output_text


def test_acceptance_status_banner_prefers_branch_copy(tmp_path: Path) -> None:
    """横幅同样取分支副本：主仓库副本是 ⬜ 未开工，分支归档副本已翻 🧍 的记录按待人工判定。

    两份副本各写一个不同的横幅，断言才有区分力：记录读出的是 🧍，而主仓库那份自己读出
    来仍是 ⬜——证明判定确实来自分支副本，而不是碰巧与主仓库副本一致。
    """
    main_repo_path = init_main_repo(
        tmp_path / "repo", prd_text=prd_text_with_acceptance_banner(NOT_STARTED_BANNER_LINE)
    )
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-review")
    branch_archived_prd_path = linked_worktree_path / FIXTURE_PRD_ARCHIVE_RELATIVE_PATH
    branch_archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    branch_archived_prd_path.write_text(
        prd_text_with_acceptance_banner(AWAITING_BANNER_LINE), encoding="utf-8"
    )

    prd_record = collect_fixture_record(main_repo_path)

    assert prd_record.acceptance_status == prd_acceptance.ACCEPTANCE_STATUS_AWAITING_HUMAN
    assert prd_record.awaits_human_review
    assert (
        prd_acceptance.parse_acceptance_status(
            (main_repo_path / FIXTURE_PRD_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        == prd_acceptance.ACCEPTANCE_STATUS_NOT_STARTED
    )


def test_pending_and_archive_copies_of_one_prd_are_claimed_once_with_pending_preferred(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """同名 PRD 在 pending 与 archive 并存：以 pending 副本为准，archive 那份不进 AWAITING HUMAN。

    并存本身就是异常（重开应是 ``git mv`` 回 pending）。pending 副本代表还在进行的那一份，
    archive 里残留的 🧍 副本若被分区认领，会让人去验收一条已重开的 PRD；它留在 ARCHIVE
    里，由月视图把它没勾完的空框报成 ⚠ 异常。
    """
    main_repo_path = init_main_repo(tmp_path / "repo")
    (main_repo_path / "tasks" / "pending" / f"{AWAITING_PRD_NAME}.md").write_text(
        prd_text_with_acceptance_banner(NOT_STARTED_BANNER_LINE), encoding="utf-8"
    )
    write_main_archived_prd(
        main_repo_path,
        prd_text_with_acceptance_banner(AWAITING_BANNER_LINE),
        prd_name=AWAITING_PRD_NAME,
    )

    raw_output_text = run_status_main(main_repo_path, "status", capsys, monkeypatch)

    assert "AWAITING HUMAN" not in raw_output_text
    assert "PENDING (2)" in raw_output_text
    assert "ARCHIVE (1)" in raw_output_text
    assert "⚠ 有未勾完的清单" in raw_output_text


def test_archived_awaiting_human_prd_moves_into_awaiting_section(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """已归档但横幅为 🧍 的 PRD 汇总进 AWAITING HUMAN，不留在 ARCHIVE 里报 ⚠。

    归档只代表执行侧交付完成：该 PRD 的 Human-Confirmed 空框是"等你确认"，不是归档
    月视图该报警的"清单没勾完"异常。
    """
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(
        main_repo_path,
        prd_text_with_acceptance_banner(AWAITING_BANNER_LINE),
        prd_name=AWAITING_PRD_NAME,
    )

    raw_output_text = run_status_main(main_repo_path, "status", capsys, monkeypatch)

    awaiting_section_text = raw_output_text[
        raw_output_text.index("AWAITING HUMAN (1)") : raw_output_text.index("ARCHIVE (")
    ]
    assert AWAITING_PRD_SLUG in awaiting_section_text
    assert "ARCHIVE (0)" in raw_output_text
    assert "⚠ 有未勾完的清单" not in raw_output_text
    assert raw_output_text.count(AWAITING_PRD_SLUG) == 1


@pytest.mark.parametrize("scope_text", ["status", "pending", "archive", "all"])
def test_awaiting_human_section_shows_in_every_scope(
    scope_text: str, tmp_path: Path, capsys, monkeypatch
) -> None:
    """待人工验收是验收欠账：不论看 pending 还是 archive，只要有就必须露出，且只出现一次。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(
        main_repo_path,
        prd_text_with_acceptance_banner(AWAITING_BANNER_LINE),
        prd_name=AWAITING_PRD_NAME,
    )

    raw_output_text = run_status_main(main_repo_path, scope_text, capsys, monkeypatch)

    assert "AWAITING HUMAN (1)" in raw_output_text
    assert raw_output_text.count(AWAITING_PRD_SLUG) == 1


def test_archive_scope_omits_awaiting_section_when_nothing_waits(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """没有待人工记录时不打印空的 AWAITING HUMAN 分区。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(
        main_repo_path,
        prd_text_with_acceptance_banner(ACCEPTED_BANNER_LINE, human_item_mark="x"),
        prd_name=AWAITING_PRD_NAME,
    )

    raw_output_text = run_status_main(main_repo_path, "archive", capsys, monkeypatch)

    assert "AWAITING HUMAN" not in raw_output_text


def test_archived_accepted_prd_stays_in_archive_without_warning(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """人已确认（横幅 ✅ 已验收、清单全勾）的归档记录留在 ARCHIVE，不亮 ⚠。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(
        main_repo_path,
        prd_text_with_acceptance_banner(ACCEPTED_BANNER_LINE, human_item_mark="x"),
        prd_name=AWAITING_PRD_NAME,
    )

    raw_output_text = run_status_main(main_repo_path, "status", capsys, monkeypatch)

    assert "AWAITING HUMAN" not in raw_output_text
    assert "ARCHIVE (1)" in raw_output_text
    assert "⚠ 有未勾完的清单" not in raw_output_text


@pytest.mark.parametrize(
    "archived_prd_text",
    [
        pytest.param(
            prd_text_with_acceptance_banner(ACCEPTED_BANNER_LINE), id="accepted-banner-open-box"
        ),
        pytest.param(DEFAULT_FIXTURE_PRD_TEXT, id="no-banner-open-box"),
    ],
)
def test_archived_prd_with_open_box_outside_awaiting_is_flagged_as_anomaly(
    archived_prd_text: str, tmp_path: Path, capsys, monkeypatch
) -> None:
    """``⚠ 有未勾完的清单`` 只剩异常含义：没被横幅声明为待人工的归档记录有空框就要亮。

    横幅写着已验收却仍有空框、历史记录缺横幅——这两类若被 AWAITING HUMAN 的分流顺手
    吞掉，归档就会悄悄放过没做完的活。
    """
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(main_repo_path, archived_prd_text, prd_name=AWAITING_PRD_NAME)

    raw_output_text = run_status_main(main_repo_path, "status", capsys, monkeypatch)

    assert "AWAITING HUMAN" not in raw_output_text
    assert "⚠ 有未勾完的清单" in raw_output_text
    assert AWAITING_PRD_SLUG in raw_output_text


def test_archived_awaiting_human_activity_shows_waiting_time_from_archive_commit(
    tmp_path: Path,
) -> None:
    """已归档待人工的记录 ACTIVITY 显示 🧍 waiting，时长取自归档落地的提交时间。

    文件 mtime 刚被写成"现在"：若实现误取 mtime，这里会得到 ``0m`` 而不是 ``3d0h``。
    """
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(main_repo_path, prd_text_with_acceptance_banner(AWAITING_BANNER_LINE))
    commit_with_backdated_time(main_repo_path, "archive prd", LANDED_THREE_DAYS_AGO_SECONDS)

    raw_cell_text = render_activity_cell(main_repo_path, FIXTURE_PRD_ARCHIVE_RELATIVE_PATH)

    assert raw_cell_text == "🧍 waiting 3d0h"


def test_archived_awaiting_human_activity_falls_back_to_mtime_when_untracked(
    tmp_path: Path,
) -> None:
    """归档文件还没被 git 追踪时，等待时长退回文件 mtime。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    archived_prd_path = write_main_archived_prd(
        main_repo_path, prd_text_with_acceptance_banner(AWAITING_BANNER_LINE)
    )
    two_days_ago_timestamp = (
        datetime.now(timezone.utc) - timedelta(days=2, minutes=10)
    ).timestamp()
    os.utime(archived_prd_path, (two_days_ago_timestamp, two_days_ago_timestamp))

    raw_cell_text = render_activity_cell(main_repo_path, FIXTURE_PRD_ARCHIVE_RELATIVE_PATH)

    assert raw_cell_text == "🧍 waiting 2d0h"


def test_archived_awaiting_human_with_fresh_lock_shows_lock_state(tmp_path: Path) -> None:
    """有活跃锁时仍显示锁状态：有人正在重开或回填，比"在等"更要紧。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(main_repo_path, prd_text_with_acceptance_banner(AWAITING_BANNER_LINE))
    commit_with_backdated_time(main_repo_path, "archive prd", LANDED_THREE_DAYS_AGO_SECONDS)
    write_lock(main_repo_path, heartbeat_at=datetime.now(timezone.utc), holder_worktree="")

    raw_cell_text = render_activity_cell(main_repo_path, FIXTURE_PRD_ARCHIVE_RELATIVE_PATH)

    assert raw_cell_text.startswith("RUNNING")
    assert "waiting" not in raw_cell_text


def test_archived_awaiting_human_with_stale_lock_still_shows_waiting(tmp_path: Path) -> None:
    """过期锁（心跳超时、无活性佐证）不能盖住"在等人验收"：显示 waiting，并附 stale lock 提示。

    若 STALE 抢先，一条验收积压了几天的 PRD 在看板上只剩一个红色的"锁过期"，欠人的那
    笔账就从 ACTIVITY 里消失了；锁残留本身仍要点出来，好让人去清理。
    """
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(main_repo_path, prd_text_with_acceptance_banner(AWAITING_BANNER_LINE))
    commit_with_backdated_time(main_repo_path, "archive prd", LANDED_THREE_DAYS_AGO_SECONDS)
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_worktree="",
    )

    raw_cell_text = render_activity_cell(main_repo_path, FIXTURE_PRD_ARCHIVE_RELATIVE_PATH)

    assert raw_cell_text == "🧍 waiting 3d0h · stale lock"


def test_archived_accepted_prd_with_stale_lock_still_shows_stale(tmp_path: Path) -> None:
    """已验收的归档记录没有"在等人"可报：过期锁照旧显示 STALE。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(
        main_repo_path,
        prd_text_with_acceptance_banner(ACCEPTED_BANNER_LINE, human_item_mark="x"),
    )
    write_lock(
        main_repo_path,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
        holder_worktree="",
    )

    raw_cell_text = render_activity_cell(main_repo_path, FIXTURE_PRD_ARCHIVE_RELATIVE_PATH)

    assert raw_cell_text.startswith("STALE")
    assert "waiting" not in raw_cell_text


def test_archived_accepted_prd_activity_has_no_waiting_marker(tmp_path: Path) -> None:
    """人已确认的归档记录没有"在等人"的标记。"""
    main_repo_path = init_main_repo(tmp_path / "repo")
    write_main_archived_prd(
        main_repo_path, prd_text_with_acceptance_banner(ACCEPTED_BANNER_LINE, human_item_mark="x")
    )
    commit_with_backdated_time(main_repo_path, "archive prd", LANDED_THREE_DAYS_AGO_SECONDS)

    raw_cell_text = render_activity_cell(main_repo_path, FIXTURE_PRD_ARCHIVE_RELATIVE_PATH)

    assert "🧍" not in raw_cell_text
    assert "waiting" not in raw_cell_text


def test_branch_archived_awaiting_human_record_is_listed_before_merge(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """合并前窗口：分支里已归档且横幅 🧍、主线仍在 pending 的记录同样进 AWAITING HUMAN。

    归档随交付改动一起落地，所以"分支已归档"就是执行侧完成；它只差合并，ACTIVITY 仍
    报 ``branch-archived · awaiting merge``，不会被当成还在 PENDING 里等开工。
    """
    main_repo_path = init_main_repo(tmp_path / "repo")
    linked_worktree_path = add_linked_worktree(main_repo_path, "feat/avatar-upload", "wt-pre-merge")
    branch_archived_prd_path = linked_worktree_path / FIXTURE_PRD_ARCHIVE_RELATIVE_PATH
    branch_archived_prd_path.parent.mkdir(parents=True, exist_ok=True)
    branch_archived_prd_path.write_text(
        prd_text_with_acceptance_banner(AWAITING_BANNER_LINE), encoding="utf-8"
    )

    assert collect_fixture_record(main_repo_path).awaits_human_review
    assert render_activity_cell(main_repo_path).startswith(
        "✔ branch-archived @feat/avatar-upload · awaiting merge"
    )

    raw_output_text = run_status_main(main_repo_path, "status", capsys, monkeypatch)

    assert "AWAITING HUMAN (1)" in raw_output_text
    assert "PENDING (0)" in raw_output_text


def test_resolve_archive_landed_timestamp_returns_none_for_missing_file(tmp_path: Path) -> None:
    """git 与文件系统都取不到时返回 ``None``，调用方据此不渲染等待时长。"""
    assert prd_activity.resolve_archive_landed_timestamp(tmp_path / "gone.md") is None

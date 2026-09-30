"""Tests for backend.core.shared.prd_checklist."""

from __future__ import annotations


from backend.core.shared.prd_checklist import PrdChecklistResult, parse_prd_checklist


def test_human_confirmed_items_remain_unchecked_without_blocking_executor() -> None:
    """人审项保留原样，后续执行项仍由提交前门禁检查。"""
    checklist = parse_prd_checklist(
        "\n".join(
            [
                "## 9. Acceptance Checklist",
                "### Human-Confirmed",
                "- [ ] Review the PR screenshots",
                "### Validation Acceptance",
                "- [ ] Run the integration test",
            ]
        )
    )

    assert len(checklist.human_pending_items) == 1
    assert checklist.execution_unchecked_items == [(5, "- [ ] Run the integration test")]


def test_deferred_human_review_gate_is_still_pending() -> None:
    """旧收尾流程写成 `[~]` 的 PR 人审项也不能被误判为可归档。"""
    checklist = parse_prd_checklist(
        "## Acceptance Checklist\n### Human-Confirmed\n"
        "- [~] 截图已审阅 — runner-owned gate: PR review\n"
    )

    assert len(checklist.human_pending_items) == 1
    assert checklist.is_complete is False


class TestParsePrdChecklist:
    """Tests for parse_prd_checklist."""

    def test_empty_file_returns_no_section(self) -> None:
        """Empty content should report section not found."""
        result = parse_prd_checklist("")
        assert result == PrdChecklistResult(section_found=False, unchecked_items=[])

    def test_no_acceptance_section_returns_not_found(self) -> None:
        """Content without acceptance heading should report section not found."""
        content = "# PRD\n\n## Some Other Section\n\n- [ ] item\n"
        result = parse_prd_checklist(content)
        assert result == PrdChecklistResult(section_found=False, unchecked_items=[])

    def test_all_checked_items_are_complete(self) -> None:
        """When all items are checked, unchecked_items should be empty."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "## Acceptance Checklist",
                "",
                "- [x] item 1",
                "- [X] item 2",
                "",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.section_found is True
        assert result.unchecked_items == []
        assert result.is_complete is True

    def test_tilde_marked_items_count_as_resolved(self) -> None:
        """`- [~]` 是豁免标记（runner 自持门禁 / 未执行即终止），不算未勾选。

        归档门禁依赖这个语义给 agent 留出口：`- [~] … — runner-owned gate: …`
        必须放行，否则「等独立 verifier PASS 后才归档」这类条目会让 attempt 死循环
        （实证：freshai Issue #111）。
        """
        content = "\n".join(
            [
                "# PRD",
                "",
                "## Acceptance Checklist",
                "",
                "- [x] 真实入口证据已产出",
                "- [~] 独立 verifier 全链 PASS 后才归档 — runner-owned gate: Phase 3.6",
                "",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.unchecked_items == []
        assert result.is_complete is True

    def test_unchecked_items_are_reported_with_line_numbers(self) -> None:
        """Unchecked items should include 1-based line numbers."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "## Acceptance Checklist",
                "",
                "- [x] done",
                "- [ ] undone 1",
                "- [ ] undone 2",
                "",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.section_found is True
        assert result.unchecked_items == [
            (6, "- [ ] undone 1"),
            (7, "- [ ] undone 2"),
        ]
        assert result.is_complete is False

    def test_checkboxes_outside_section_are_ignored(self) -> None:
        """Checkboxes before or after the acceptance section should be ignored."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "- [ ] before section",
                "",
                "## Acceptance Checklist",
                "",
                "- [x] done",
                "",
                "## Notes",
                "",
                "- [ ] after section",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.section_found is True
        assert result.unchecked_items == []

    def test_checkboxes_inside_code_blocks_are_ignored(self) -> None:
        """Checkboxes inside fenced code blocks within the section should be ignored."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "## Acceptance Checklist",
                "",
                "- [x] real item",
                "",
                "```markdown",
                "- [ ] inside code block",
                "```",
                "",
                "- [ ] real unchecked",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.unchecked_items == [(11, "- [ ] real unchecked")]

    def test_numbered_heading_is_recognized(self) -> None:
        """Numbered headings like '## 7. Acceptance Checklist' should match."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "## 7. Acceptance Checklist",
                "",
                "- [x] done",
                "- [ ] undone",
                "",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.section_found is True
        assert result.unchecked_items == [(6, "- [ ] undone")]

    def test_bilingual_heading_is_recognized(self) -> None:
        """Bilingual headings like 'Acceptance Checklist（验收清单）' should match."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "## 7. Acceptance Checklist（验收清单）",
                "",
                "- [x] done",
                "- [ ] undone",
                "",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.section_found is True
        assert result.unchecked_items == [(6, "- [ ] undone")]

    def test_section_ends_at_next_top_level_heading(self) -> None:
        """The acceptance section should stop at the next '##' heading."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "## Acceptance Checklist",
                "",
                "- [ ] first",
                "",
                "## Next Section",
                "",
                "- [ ] second",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.unchecked_items == [(5, "- [ ] first")]

    def test_empty_checklist_section_is_complete(self) -> None:
        """A checklist section with no checkboxes at all should be considered complete."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "## Acceptance Checklist",
                "",
                "Some text without checkboxes.",
                "",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.section_found is True
        assert result.unchecked_items == []
        assert result.is_complete is True

    def test_tilde_code_fences_are_ignored(self) -> None:
        """Tilde-style fenced code blocks should also be ignored."""
        content = "\n".join(
            [
                "# PRD",
                "",
                "## Acceptance Checklist",
                "",
                "~~~python",
                "- [ ] inside tilde block",
                "~~~",
                "",
                "- [x] done",
            ]
        )
        result = parse_prd_checklist(content)
        assert result.unchecked_items == []


class TestHumanConfirmedGroupFormatting:
    """Human-Confirmed 分组的书写形式容错。

    背景：``**Human-Confirmed**`` 这种整行加粗的写法曾让分组彻底不被识别，
    人属项落入执行项集合，交付门禁报 checklist_unchecked 并触发
    closeout/repair 死循环（实证：ai-assistant Issue #53）。
    """

    def test_bold_group_label_is_recognized_and_closed_by_sibling_bold_label(self) -> None:
        """整行加粗的分组标签要能开组，并被下一个同级加粗标签关闭。"""
        content = "\n".join(
            [
                "## 9. Acceptance Checklist",
                "",
                "### 9.2 Acceptance Evidence Package",
                "",
                "**Human-Confirmed**",
                "",
                "- [ ] 决定一：人属项",
                "",
                "**Behavior Acceptance**",
                "",
                "- [ ] rv-1 PASS：待独立 verifier",
                "",
            ]
        )

        result = parse_prd_checklist(content)

        assert result.human_pending_items == [(7, "- [ ] 决定一：人属项")]
        assert result.execution_unchecked_items == [(11, "- [ ] rv-1 PASS：待独立 verifier")]

    def test_heading_label_with_suffix_is_recognized(self) -> None:
        """带说明后缀的标题（prd skill 模板写法）也要被识别。"""
        content = "\n".join(
            [
                "## 9. Acceptance Checklist",
                "",
                "### Human-Confirmed (来自 Part A 风险地图)",
                "",
                "- [ ] 人属项",
                "",
                "### Behavior Acceptance",
                "",
                "- [ ] 执行项",
                "",
            ]
        )

        result = parse_prd_checklist(content)

        assert result.human_pending_items == [(5, "- [ ] 人属项")]
        assert result.execution_unchecked_items == [(9, "- [ ] 执行项")]

    def test_inline_bold_human_confirmed_is_not_a_group_marker(self) -> None:
        """行内强调（加粗后还有正文）不得被当作分组标签。"""
        content = "\n".join(
            [
                "## 9. Acceptance Checklist",
                "",
                "**Human-Confirmed（2026-09-23）：** 用户确认了该决定。",
                "",
                "- [ ] 执行项",
                "",
            ]
        )

        result = parse_prd_checklist(content)

        assert result.human_pending_items == []
        assert result.execution_unchecked_items == [(5, "- [ ] 执行项")]

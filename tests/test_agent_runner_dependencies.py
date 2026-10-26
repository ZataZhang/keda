"""Tests for agent_runner_dependencies module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    DependencyBlocker,
    DependencyDeclaration,
    DependencyVerdict,
    DeliveryDependencyDeclaration,
    IssueSummary,
    LabelConfig,
)
from backend.core.use_cases.agent_runner_dependencies import (
    _canonical_blockers_hash,
    build_waiting_comment,
    clear_dependency_waiting,
    evaluate_dependencies,
    format_dependency_marker,
    format_dependency_wait_marker,
    mark_dependency_waiting,
    parse_delivery_dependencies,
    parse_dependency_marker,
    parse_latest_dependency_wait_marker,
)
from backend.core.use_cases.create_issue_from_prd import _resolve_dependencies


# ---------------------------------------------------------------------------
# parse_delivery_dependencies
# ---------------------------------------------------------------------------


class TestParseDeliveryDependencies:
    def test_empty_prd_returns_none(self) -> None:
        result = parse_delivery_dependencies("# PRD: Foo\n\nSome text.")
        assert result == DeliveryDependencyDeclaration()

    def test_parses_all_fields(self) -> None:
        prd_text = """
## Delivery Dependencies

- Depends on tasks/issues: #42, 43
- Gate type: hard
- Notes: wait for upstream
"""
        result = parse_delivery_dependencies(prd_text)
        assert result.depends_on_issues == (42, 43)
        assert result.depends_on_prds == ()
        assert result.gate_type == "hard"
        assert result.notes == "wait for upstream"

    def test_parses_markdown_list_fields(self) -> None:
        prd_text = """
## Delivery Dependencies

- Depends on tasks/issues:
  - #42
  - 43
  - tasks/pending/P2-FEAT-20260527-190923-prd-from-issue.md
- Gate type: hard
- Notes:
  - wait for upstream
  - publish order matters
"""
        result = parse_delivery_dependencies(prd_text)
        assert result.depends_on_issues == (42, 43)
        assert result.depends_on_prds == (
            "tasks/pending/P2-FEAT-20260527-190923-prd-from-issue.md",
        )
        assert result.gate_type == "hard"
        assert result.notes == "wait for upstream publish order matters"

    def test_none_dependency_values_are_treated_as_empty(self) -> None:
        prd_text = """
## Delivery Dependencies

- Depends on tasks/issues: none
- Gate type: none
"""
        result = parse_delivery_dependencies(prd_text)
        assert result.depends_on_issues == ()
        assert result.depends_on_prds == ()
        assert result.gate_type == "none"

    def test_legacy_group_field_fails_loudly(self) -> None:
        prd_text = """
## Delivery Dependencies

- Group: my-group
- Depends on tasks/issues: #42
- Gate type: hard
"""
        with pytest.raises(ValueError, match="Task-group dependencies were removed"):
            parse_delivery_dependencies(prd_text)

    def test_legacy_depends_on_groups_field_fails_loudly(self) -> None:
        prd_text = """
## Delivery Dependencies

- Depends on groups: group-a, group-b
- Gate type: hard
"""
        with pytest.raises(ValueError, match="Task-group dependencies were removed"):
            parse_delivery_dependencies(prd_text)

    def test_prd_filename_references_are_preserved(self) -> None:
        prd_text = """
## Delivery Dependencies

- Depends on tasks/issues:
  - P2-FEAT-20260527-190923-prd-from-issue
- Gate type: hard
"""
        result = parse_delivery_dependencies(prd_text)

        assert result.depends_on_issues == ()
        assert result.depends_on_prds == ("P2-FEAT-20260527-190923-prd-from-issue",)

    def test_prd_reference_with_code_span_and_note_is_cleaned(self) -> None:
        prd_text = """
## Delivery Dependencies

- Depends on tasks/issues:
  - `tasks/archive/P1-FEAT-20260611-205725-agent-runner-unified-ops-console.md`（已完成；提供管理终端 shell、仓库 registry、进程/审计模式）
  - `tasks/archive/P1-FEAT-20260614-200054-frontend-prd-backlog.md` (已完成; 提供 `/backlog` 页面)
- Gate type: none
"""
        result = parse_delivery_dependencies(prd_text)

        assert result.depends_on_issues == ()
        assert result.depends_on_prds == (
            "tasks/archive/P1-FEAT-20260611-205725-agent-runner-unified-ops-console.md",
            "tasks/archive/P1-FEAT-20260614-200054-frontend-prd-backlog.md",
        )

    def test_soft_gate(self) -> None:
        prd_text = """
## Delivery Dependencies

- Gate type: soft
"""
        result = parse_delivery_dependencies(prd_text)
        assert result.gate_type == "soft"

    def test_none_gate(self) -> None:
        prd_text = """
## Delivery Dependencies

- Gate type: none
"""
        result = parse_delivery_dependencies(prd_text)
        assert result.gate_type == "none"

    def test_section_ends_at_next_header(self) -> None:
        prd_text = """
## Delivery Dependencies

- Notes: first

## Some Other Section

- Notes: second
"""
        result = parse_delivery_dependencies(prd_text)
        assert result.notes == "first"

    def test_parses_real_m13_two_level_delivery_dependencies(self) -> None:
        """M13 PRD 的真实双层写法应保留 hard 依赖和排序声明。"""
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "tasks/pending/P1-FEAT-20261019-180938-m13-keda-vs-claude-workflow-evaluation.md"
        )
        prd_text = fixture_path.read_text(encoding="utf-8")

        result = parse_delivery_dependencies(prd_text)

        assert result.depends_on_issues == ()
        assert result.depends_on_prds == (
            "tasks/pending/P1-FEAT-20261019-140531-m13-golden-eval-baseline.md",
        )
        assert result.gate_type == "hard"
        assert result.sequence == "via-main"

    @pytest.mark.parametrize(
        ("prd_text", "expected"),
        [
            (
                "## Delivery Dependencies\n"
                "- Depends on tasks/issues: #42\n"
                "- Gate type: hard\n"
                "- Sequence: via-main\n",
                DeliveryDependencyDeclaration(
                    depends_on_issues=(42,), gate_type="hard", sequence="via-main"
                ),
            ),
            (
                "## 8. Delivery Dependencies\n"
                "### Delivery Dependencies\n"
                "- Depends on tasks/issues: #43\n"
                "- Gate type: hard\n"
                "- Sequence: stack\n",
                DeliveryDependencyDeclaration(
                    depends_on_issues=(43,), gate_type="hard", sequence="stack"
                ),
            ),
            (
                "## 8. Delivery Dependencies\n"
                "### Delivery Dependencies\n"
                "- Depends on tasks/issues: #44\n"
                "- Gate type: soft\n",
                DeliveryDependencyDeclaration(
                    depends_on_issues=(44,), gate_type="soft", sequence="via-main"
                ),
            ),
            (
                "## 8. Delivery Dependencies\n"
                "### Delivery Dependencies\n"
                "- Depends on tasks/issues: none\n"
                "- Gate type: none\n",
                DeliveryDependencyDeclaration(),
            ),
        ],
    )
    def test_parses_delivery_dependency_heading_shapes(
        self, prd_text: str, expected: DeliveryDependencyDeclaration
    ) -> None:
        """单层和双层标题下的门禁类型与 Sequence 均按声明解析。"""
        assert parse_delivery_dependencies(prd_text) == expected

    def test_matches_upstream_shared_delivery_dependency_samples_or_issue_fallback(
        self,
    ) -> None:
        """逐例验证上游共享样例；样例同步前使用 Issue #315 的回归形状。"""
        repository_root = Path(__file__).resolve().parents[1]
        shared_samples_directory = repository_root / "tests/guards/shared"
        shared_sample_paths = sorted(shared_samples_directory.glob("*delivery*dependenc*.json"))
        if shared_sample_paths:
            sample_cases: list[dict[str, object]] = []
            for shared_sample_path in shared_sample_paths:
                sample_payload = json.loads(shared_sample_path.read_text(encoding="utf-8"))
                if isinstance(sample_payload, dict):
                    sample_cases.extend(sample_payload["cases"])
                else:
                    sample_cases.extend(sample_payload)
        else:
            # TODO: 上游同步 Delivery Dependencies 共享样例后，删除本 Issue 的本地回退样例。
            m13_fixture_path = (
                repository_root
                / "tasks/pending/P1-FEAT-20261019-180938-m13-keda-vs-claude-workflow-evaluation.md"
            )
            sample_cases = [
                {
                    "name": "m13-real-hard-via-main",
                    "prd_fragment": m13_fixture_path.read_text(encoding="utf-8"),
                    "expected": {
                        "depends_on_issues": [],
                        "depends_on_prds": [
                            "tasks/pending/P1-FEAT-20261019-140531-m13-golden-eval-baseline.md"
                        ],
                        "gate_type": "hard",
                        "sequence": "via-main",
                        "notes": (
                            "需要前置 PRD 已将 suite 校准、快照隔离、延后判分、重复结果账本及 "
                            "v16 schema 落到主线；本 PRD 之后将账本推进到 v17。"
                        ),
                    },
                },
                {
                    "name": "two-level-hard-stack",
                    "prd_fragment": (
                        "## 8. Delivery Dependencies\n### Delivery Dependencies\n"
                        "- Depends on tasks/issues: #42\n- Gate type: hard\n"
                        "- Sequence: stack\n"
                    ),
                    "expected": {
                        "depends_on_issues": [42],
                        "depends_on_prds": [],
                        "gate_type": "hard",
                        "sequence": "stack",
                    },
                },
                {
                    "name": "two-level-soft",
                    "prd_fragment": (
                        "## 8. Delivery Dependencies\n### Delivery Dependencies\n"
                        "- Depends on tasks/issues: #43\n- Gate type: soft\n"
                    ),
                    "expected": {
                        "depends_on_issues": [43],
                        "depends_on_prds": [],
                        "gate_type": "soft",
                        "sequence": "via-main",
                    },
                },
                {
                    "name": "two-level-none",
                    "prd_fragment": (
                        "## 8. Delivery Dependencies\n### Delivery Dependencies\n"
                        "- Depends on tasks/issues: none\n- Gate type: none\n"
                    ),
                    "expected": {
                        "depends_on_issues": [],
                        "depends_on_prds": [],
                        "gate_type": "none",
                        "sequence": "via-main",
                    },
                },
            ]

        for sample_case in sample_cases:
            expected_declaration = sample_case["expected"]
            assert isinstance(expected_declaration, dict), sample_case["name"]
            assert parse_delivery_dependencies(sample_case["prd_fragment"]) == (
                DeliveryDependencyDeclaration(
                    depends_on_issues=tuple(expected_declaration["depends_on_issues"]),
                    depends_on_prds=tuple(expected_declaration["depends_on_prds"]),
                    gate_type=expected_declaration["gate_type"],
                    sequence=expected_declaration["sequence"],
                    notes=expected_declaration.get("notes", ""),
                )
            ), sample_case["name"]


# ---------------------------------------------------------------------------
# parse_dependency_marker
# ---------------------------------------------------------------------------


class TestParseDependencyMarker:
    def test_no_marker_returns_none(self) -> None:
        assert parse_dependency_marker("No markers here") is None

    def test_retired_marker_name_returns_none(self) -> None:
        assert parse_dependency_marker("<!-- iar:depends-on #42 -->") is None

    def test_parses_issue_numbers(self) -> None:
        body = "<!-- kc:depends-on #42 #99 -->"
        result = parse_dependency_marker(body)
        assert result == DependencyDeclaration(issue_numbers=(42, 99))

    def test_legacy_group_token_only_returns_none(self) -> None:
        body = "<!-- kc:depends-on group:alpha group:beta -->"
        assert parse_dependency_marker(body) is None

    def test_mixed_marker_ignores_group_tokens(self) -> None:
        body = "<!-- kc:depends-on #7 group:gamma #12 -->"
        result = parse_dependency_marker(body)
        assert result == DependencyDeclaration(issue_numbers=(7, 12))

    def test_multiple_markers(self) -> None:
        body = "<!-- kc:depends-on #1 -->\n<!-- kc:depends-on #2 -->"
        result = parse_dependency_marker(body)
        assert result == DependencyDeclaration(issue_numbers=(1, 2))


# ---------------------------------------------------------------------------
# format_dependency_marker
# ---------------------------------------------------------------------------


class TestFormatDependencyMarker:
    def test_empty_returns_empty(self) -> None:
        assert format_dependency_marker() == ""

    def test_issues_only(self) -> None:
        assert format_dependency_marker(issue_numbers=(42,)) == "<!-- kc:depends-on #42 -->"


# ---------------------------------------------------------------------------
# comment deduplication helpers
# ---------------------------------------------------------------------------


class TestCommentDeduplication:
    def test_canonical_blockers_hash_stable(self) -> None:
        b1 = DependencyBlocker("issue", "42", "OPEN")
        b2 = DependencyBlocker("issue", "43", "OPEN")
        h1 = _canonical_blockers_hash((b1, b2))
        h2 = _canonical_blockers_hash((b1, b2))
        assert h1 == h2
        assert len(h1) == 16

    def test_parse_latest_dependency_wait_marker(self) -> None:
        comments = [
            "old\n<!-- kc:dependency-wait blockers=abc123 -->",
            "new\n<!-- kc:dependency-wait blockers=def456 -->",
        ]
        assert parse_latest_dependency_wait_marker(comments) == "def456"

    def test_parse_latest_none(self) -> None:
        assert parse_latest_dependency_wait_marker(["no marker"]) is None


# ---------------------------------------------------------------------------
# evaluate_dependencies
# ---------------------------------------------------------------------------


class FakeGitHubClientForDeps:
    """Minimal fake for dependency evaluation tests."""

    def __init__(
        self,
        issues: dict[int, IssueSummary] | None = None,
        pushed_branches: set[str] | None = None,
    ) -> None:
        self.issues = issues or {}
        self.pushed_branches = pushed_branches or set()
        self.calls: list[dict] = []

    def get_issue(self, issue_number: int) -> IssueSummary:
        self.calls.append({"method": "get_issue", "issue_number": issue_number})
        if issue_number not in self.issues:
            raise RuntimeError(f"Issue #{issue_number} not found")
        return self.issues[issue_number]

    def find_open_pr_by_head(self, branch: str) -> str | None:
        return "https://example.test/pr/1" if branch in self.pushed_branches else None

    def find_merged_pr_by_head(self, branch: str) -> str | None:
        return None


class TestEvaluateDependencies:
    def test_no_dependencies_satisfied(self) -> None:
        client = FakeGitHubClientForDeps()
        decl = DependencyDeclaration()
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.satisfied is True
        assert verdict.blockers == ()

    def test_issue_dependency_closed(self) -> None:
        client = FakeGitHubClientForDeps(
            issues={
                42: IssueSummary(
                    number=42,
                    title="Done",
                    url="",
                    body="",
                    labels=(),
                    state="CLOSED",
                )
            }
        )
        decl = DependencyDeclaration(issue_numbers=(42,))
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.satisfied is True

    def test_issue_dependency_open_blocked(self) -> None:
        client = FakeGitHubClientForDeps(
            issues={
                42: IssueSummary(
                    number=42,
                    title="Open",
                    url="",
                    body="",
                    labels=(),
                    state="OPEN",
                )
            }
        )
        decl = DependencyDeclaration(issue_numbers=(42,))
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.satisfied is False
        assert verdict.blockers == (
            DependencyBlocker(blocker_type="issue", target="42", current_state="OPEN"),
        )

    def test_issue_dependency_unknown_state_blocked(self) -> None:
        client = FakeGitHubClientForDeps()
        decl = DependencyDeclaration(issue_numbers=(404,))
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.satisfied is False
        assert verdict.blockers[0].current_state == "unknown"

    def test_failed_upstream_detected(self) -> None:
        client = FakeGitHubClientForDeps(
            issues={
                42: IssueSummary(
                    number=42,
                    title="Failed",
                    url="",
                    body="",
                    labels=("agent/failed",),
                    state="OPEN",
                )
            }
        )
        decl = DependencyDeclaration(issue_numbers=(42,))
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.has_failed_or_blocked_upstream is True

    def test_stack_satisfied_when_upstream_branch_pushed(self) -> None:
        client = FakeGitHubClientForDeps(pushed_branches={"issue-42"})
        decl = DependencyDeclaration(issue_numbers=(42,), sequence="stack")
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.satisfied is True
        assert verdict.blockers == ()

    def test_stack_blocked_when_upstream_branch_not_pushed(self) -> None:
        client = FakeGitHubClientForDeps()
        decl = DependencyDeclaration(issue_numbers=(42,), sequence="stack")
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.satisfied is False
        assert verdict.blockers == (
            DependencyBlocker(
                blocker_type="branch",
                target="issue-42",
                current_state="NOT_PUSHED",
            ),
        )

    def test_via_main_ignores_branch_readiness(self) -> None:
        # via-main waits for the Issue to close, not the branch to be pushed.
        client = FakeGitHubClientForDeps(
            pushed_branches={"issue-42"},
            issues={
                42: IssueSummary(number=42, title="Open", url="", body="", labels=(), state="OPEN")
            },
        )
        decl = DependencyDeclaration(issue_numbers=(42,), sequence="via-main")
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.satisfied is False

    def test_stack_upstream_failure_surfaces_warning(self) -> None:
        # A failed/blocked upstream must not leave the downstream waiting silently.
        client = FakeGitHubClientForDeps(
            issues={
                42: IssueSummary(
                    number=42,
                    title="Upstream",
                    url="",
                    body="",
                    labels=(LabelConfig().failed,),
                    state="OPEN",
                )
            }
        )
        decl = DependencyDeclaration(issue_numbers=(42,), sequence="stack")
        verdict = evaluate_dependencies(decl, client, LabelConfig())
        assert verdict.satisfied is False
        assert verdict.has_failed_or_blocked_upstream is True


# ---------------------------------------------------------------------------
# mark_dependency_waiting / clear_dependency_waiting
# ---------------------------------------------------------------------------


class TestWaitingSideEffects:
    def test_dry_run_only_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        from tests.conftest import FakeGitHubClient

        client = FakeGitHubClient()
        issue = IssueSummary(number=1, title="T", url="", body="", labels=())
        verdict = DependencyVerdict(
            satisfied=False,
            blockers=(DependencyBlocker("issue", "42", "OPEN"),),
        )
        with caplog.at_level("INFO"):
            mark_dependency_waiting(
                issue=issue,
                verdict=verdict,
                github_client=client,
                labels_config=LabelConfig(),
                dry_run=True,
            )
        assert "DRY RUN" in caplog.text
        assert not any(c["method"] == "edit_issue_labels" for c in client.calls)

    def test_adds_waiting_label(self) -> None:
        from tests.conftest import FakeGitHubClient

        client = FakeGitHubClient()
        issue = IssueSummary(number=1, title="T", url="", body="", labels=())
        verdict = DependencyVerdict(
            satisfied=False,
            blockers=(DependencyBlocker("issue", "42", "OPEN"),),
        )
        mark_dependency_waiting(
            issue=issue,
            verdict=verdict,
            github_client=client,
            labels_config=LabelConfig(),
            dry_run=False,
        )
        label_call = [c for c in client.calls if c["method"] == "edit_issue_labels"]
        assert len(label_call) == 1
        assert "agent/waiting" in label_call[0]["add"]

    def test_comment_deduplication(self) -> None:
        from tests.conftest import FakeGitHubClient

        client = FakeGitHubClient()
        client.comment_issue(1, "first\n<!-- kc:dependency-wait blockers=abc -->")
        issue = IssueSummary(number=1, title="T", url="", body="", labels=())
        # Same blockers as stored in comment above
        verdict = DependencyVerdict(
            satisfied=False,
            blockers=(DependencyBlocker("issue", "42", "OPEN"),),
        )
        # Force hash to match by setting up same comment
        client._issue_comments[1] = [f"first\n{format_dependency_wait_marker(verdict.blockers)}"]
        mark_dependency_waiting(
            issue=issue,
            verdict=verdict,
            github_client=client,
            labels_config=LabelConfig(),
            dry_run=False,
        )
        # Only label edit, no new comment because blockers unchanged
        comment_calls = [c for c in client.calls if c["method"] == "comment_issue"]
        assert len(comment_calls) == 1  # the setup comment only

    def test_clear_waiting_removes_label(self) -> None:
        from tests.conftest import FakeGitHubClient

        client = FakeGitHubClient()
        issue = IssueSummary(
            number=1,
            title="T",
            url="",
            body="",
            labels=("agent/waiting",),
        )
        clear_dependency_waiting(
            issue=issue,
            github_client=client,
            labels_config=LabelConfig(),
            dry_run=False,
        )
        label_call = [c for c in client.calls if c["method"] == "edit_issue_labels"]
        assert len(label_call) == 1
        assert "agent/waiting" in label_call[0]["remove"]

    def test_clear_waiting_noop_when_no_label(self) -> None:
        from tests.conftest import FakeGitHubClient

        client = FakeGitHubClient()
        issue = IssueSummary(number=1, title="T", url="", body="", labels=())
        clear_dependency_waiting(
            issue=issue,
            github_client=client,
            labels_config=LabelConfig(),
            dry_run=False,
        )
        assert not any(c["method"] == "edit_issue_labels" for c in client.calls)


# ---------------------------------------------------------------------------
# build_waiting_comment
# ---------------------------------------------------------------------------


class TestBuildWaitingComment:
    def test_includes_blockers(self) -> None:
        verdict = DependencyVerdict(
            satisfied=False,
            blockers=(
                DependencyBlocker("issue", "42", "OPEN"),
                DependencyBlocker("issue", "43", "OPEN"),
            ),
        )
        comment = build_waiting_comment(verdict, 1, LabelConfig())
        assert "Issue #42" in comment
        assert "Issue #43" in comment
        assert "kc:dependency-wait" in comment

    def test_upstream_failure_warning(self) -> None:
        verdict = DependencyVerdict(
            satisfied=False,
            blockers=(DependencyBlocker("issue", "42", "OPEN"),),
            has_failed_or_blocked_upstream=True,
        )
        comment = build_waiting_comment(verdict, 1, LabelConfig())
        assert "Upstream failure detected" in comment

    def test_upstream_failure_includes_resolution_guidance(self) -> None:
        verdict = DependencyVerdict(
            satisfied=False,
            blockers=(DependencyBlocker("issue", "42", "OPEN"),),
            has_failed_or_blocked_upstream=True,
        )
        comment = build_waiting_comment(verdict, 1, LabelConfig())
        assert "How to resolve" in comment
        assert "Upstream failure" in comment
        assert "agent/failed" in comment
        # Open-issue blocker also produces guidance.
        assert "Open upstream" in comment

    def test_unknown_state_includes_resolution_guidance(self) -> None:
        verdict = DependencyVerdict(
            satisfied=False,
            blockers=(DependencyBlocker("issue", "42", "unknown"),),
        )
        comment = build_waiting_comment(verdict, 1, LabelConfig())
        assert "How to resolve" in comment
        assert "Unknown state" in comment

    def test_no_resolution_section_when_no_blockers(self) -> None:
        verdict = DependencyVerdict(satisfied=True, blockers=())
        comment = build_waiting_comment(verdict, 1, LabelConfig())
        assert "How to resolve" not in comment

    def test_branch_blocker_includes_stack_guidance(self) -> None:
        verdict = DependencyVerdict(
            satisfied=False,
            blockers=(DependencyBlocker("branch", "issue-42", "NOT_PUSHED"),),
        )
        comment = build_waiting_comment(verdict, 1, LabelConfig())
        assert "Upstream branch `issue-42`" in comment
        assert "How to resolve" in comment
        assert "Upstream branch not ready" in comment


# ---------------------------------------------------------------------------
# _resolve_dependencies (create_issue_from_prd helper)
# ---------------------------------------------------------------------------


class TestResolveDependencies:
    def test_no_dependencies(self) -> None:
        gate, issues, sequence = _resolve_dependencies("# PRD\n")
        assert gate == "none"
        assert issues == ()
        assert sequence == "via-main"

    def test_prd_only(self) -> None:
        prd = """
## Delivery Dependencies

- Depends on tasks/issues: #42
- Gate type: hard
"""
        gate, issues, sequence = _resolve_dependencies(prd)
        assert gate == "hard"
        assert issues == (42,)
        assert sequence == "via-main"

    def test_cli_overrides(self) -> None:
        prd = "# PRD\n"
        gate, issues, sequence = _resolve_dependencies(prd, depends_on=(99,))
        assert gate == "none"
        assert issues == (99,)

    def test_merge_and_dedup(self) -> None:
        prd = """
## Delivery Dependencies

- Depends on tasks/issues: #1
- Gate type: hard
"""
        gate, issues, sequence = _resolve_dependencies(prd, depends_on=(1, 2))
        assert issues == (1, 2)

    def test_explicit_marker_compat_ignores_group_token(self) -> None:
        prd = "<!-- kc:depends-on #5 group:g3 -->\n# PRD\n"
        gate, issues, sequence = _resolve_dependencies(prd)
        assert issues == (5,)

    def test_stack_sequence_resolved(self) -> None:
        prd = """
## Delivery Dependencies

- Depends on tasks/issues: #42
- Gate type: hard
- Sequence: stack
"""
        gate, issues, sequence = _resolve_dependencies(prd)
        assert gate == "hard"
        assert issues == (42,)
        assert sequence == "stack"

    def test_invalid_sequence_rejected(self) -> None:
        prd = """
## Delivery Dependencies

- Depends on tasks/issues: #42
- Gate type: hard
- Sequence: sideways
"""
        with pytest.raises(ValueError):
            _resolve_dependencies(prd)

    def test_stack_multiple_upstreams_rejected(self) -> None:
        prd = """
## Delivery Dependencies

- Depends on tasks/issues: #42, #43
- Gate type: hard
- Sequence: stack
"""
        with pytest.raises(ValueError, match="exactly one upstream"):
            _resolve_dependencies(prd)

    def test_stack_single_upstream_allowed(self) -> None:
        prd = """
## Delivery Dependencies

- Depends on tasks/issues: #42
- Gate type: hard
- Sequence: stack
"""
        gate, issues, sequence = _resolve_dependencies(prd)
        assert sequence == "stack"
        assert issues == (42,)

    def test_explicit_stack_marker_preserved_without_sequence_field(self) -> None:
        prd = '<!-- kc:depends-on #5 mode="stack" -->\n# PRD\n'
        gate, issues, sequence = _resolve_dependencies(prd)
        assert issues == (5,)
        assert sequence == "stack"


# ---------------------------------------------------------------------------
# Fail-fast validation
# ---------------------------------------------------------------------------


class TestFailFastValidation:
    def test_invalid_gate_type_raises(self) -> None:
        prd_text = """
## Delivery Dependencies

- Gate type: maybe
"""
        with pytest.raises(ValueError, match="Invalid 'Gate type'"):
            parse_delivery_dependencies(prd_text)

    def test_plain_text_task_reference_is_preserved_for_materialization(self) -> None:
        prd_text = """
## Delivery Dependencies

- Depends on tasks/issues: foo
- Gate type: hard
"""
        result = parse_delivery_dependencies(prd_text)

        assert result.depends_on_issues == ()
        assert result.depends_on_prds == ("foo",)

    def test_case_insensitive_gate_type_normalised(self) -> None:
        prd_text = """
## Delivery Dependencies

- Gate type: HARD
"""
        result = parse_delivery_dependencies(prd_text)
        assert result.gate_type == "hard"

    def test_unknown_field_raises(self) -> None:
        prd_text = """
## Delivery Dependencies

- Grou: my-group
"""
        with pytest.raises(ValueError, match="Unknown field"):
            parse_delivery_dependencies(prd_text)

"""Tests for agent runner feedback prompts."""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.models.agent_runner import CommandResult, IssueSummary
from backend.core.use_cases.agent_runner_feedback import (
    build_fix_prompt,
    build_progress_continuation_prompt,
    build_prompt,
    build_recovery_prompt,
    format_prd_delivery_detail,
    format_prd_delivery_failure,
)


def _make_issue(number: int = 42) -> IssueSummary:
    return IssueSummary(
        number=number,
        title="Example Issue",
        url=f"https://github.com/example/repo/issues/{number}",
        body="Example body",
        labels=(),
    )


def _assert_forbids_git_index_mutation_and_flag_reruns(
    prompt: str, *, recovery: bool = False
) -> None:
    assert "git reset" in prompt
    assert "git checkout" in prompt
    assert "mutates the git index" in prompt
    if recovery:
        assert "stale verification/test flag" in prompt
    else:
        assert (
            "re-running verification commands" in prompt
            or "re-run that verification command" in prompt
        )


def test_build_fix_prompt_includes_verification_failure() -> None:
    """Fix prompt should contain the failed command and output."""
    issue = _make_issue()
    worktree_path = Path("/worktree")
    verification_results = [
        CommandResult(
            command=("just", "lint"),
            return_code=1,
            stdout="lint failed\n",
            stderr="lint stderr\n",
        )
    ]

    prompt = build_fix_prompt(issue, worktree_path, verification_results=verification_results)

    assert "Fix the verification failure" in prompt
    assert "just lint" in prompt
    assert "lint failed" in prompt
    assert "lint stderr" in prompt


def test_build_fix_prompt_forbids_global_deliverables() -> None:
    """Fix prompt must tell the agent not to touch evidence/PRD/commit request."""
    issue = _make_issue()
    worktree_path = Path("/worktree")
    verification_results = [
        CommandResult(
            command=("ruff", "check"),
            return_code=1,
            stdout="E501\n",
            stderr="",
        )
    ]

    prompt = build_fix_prompt(issue, worktree_path, verification_results=verification_results)

    assert "Do not update evidence files" in prompt
    assert "PRD Acceptance Checklists" in prompt
    assert "commit requests" in prompt


def test_build_fix_prompt_forbids_git_index_mutation() -> None:
    """Fix prompt must forbid git index mutations and remind about verification flags."""
    issue = _make_issue()
    worktree_path = Path("/worktree")
    verification_results = [
        CommandResult(
            command=("ruff", "check"),
            return_code=1,
            stdout="E501\n",
            stderr="",
        )
    ]

    prompt = build_fix_prompt(issue, worktree_path, verification_results=verification_results)

    _assert_forbids_git_index_mutation_and_flag_reruns(prompt)


def test_build_fix_prompt_does_not_include_prd_closeout() -> None:
    """Fix prompt should not ask the agent to archive PRDs or update checklists."""
    issue = _make_issue()
    worktree_path = Path("/worktree")
    verification_results = [
        CommandResult(
            command=("ruff", "check"),
            return_code=1,
            stdout="E501\n",
            stderr="",
        )
    ]

    prompt = build_fix_prompt(issue, worktree_path, verification_results=verification_results)

    assert "move the PRD" not in prompt.lower()
    assert "tasks/pending" not in prompt
    assert "tasks/archive" not in prompt
    assert "update the PRD" not in prompt


def test_build_fix_prompt_includes_verification_commands_summary() -> None:
    """Fix prompt should list all verification commands when summary is provided."""
    issue = _make_issue()
    worktree_path = Path("/worktree")
    verification_results = [
        CommandResult(
            command=("ruff", "check"),
            return_code=1,
            stdout="E501\n",
            stderr="",
        )
    ]

    prompt = build_fix_prompt(
        issue,
        worktree_path,
        verification_results=verification_results,
        verification_commands_summary="- `ruff check`\n- `pytest -q`",
    )

    assert "ruff check" in prompt
    assert "pytest -q" in prompt
    assert "first failure stops the chain" in prompt
    assert "project conventions" in prompt


def test_build_recovery_prompt_still_includes_prd_closeout() -> None:
    """Recovery prompt should retain global deliverable responsibilities."""
    issue = _make_issue()
    worktree_path = Path("/worktree")

    prompt = build_recovery_prompt(
        issue,
        worktree_path,
        recovery_attempt=1,
        max_recovery_attempts=2,
        failure_summary="something failed",
    )

    assert "Repair GitHub Issue" in prompt
    assert "something failed" in prompt


def test_build_recovery_prompt_includes_verification_results() -> None:
    """Recovery prompt should include raw verification failures when provided."""
    issue = _make_issue()
    worktree_path = Path("/worktree")
    verification_results = [
        CommandResult(
            command=("pytest", "-q"),
            return_code=1,
            stdout="1 failed\n",
            stderr="",
        )
    ]

    prompt = build_recovery_prompt(
        issue,
        worktree_path,
        recovery_attempt=1,
        max_recovery_attempts=2,
        failure_summary="tests failed",
        verification_results=verification_results,
    )

    assert "pytest -q" in prompt
    assert "1 failed" in prompt
    assert "first failure stops the chain" in prompt
    assert "project conventions" in prompt


def test_build_recovery_prompt_forbids_git_index_mutation_and_flag_reruns() -> None:
    """Recovery prompt must forbid git index mutations and remind about verification flags."""
    issue = _make_issue()
    worktree_path = Path("/worktree")

    prompt = build_recovery_prompt(
        issue,
        worktree_path,
        recovery_attempt=1,
        max_recovery_attempts=2,
        failure_summary="tests failed",
    )

    _assert_forbids_git_index_mutation_and_flag_reruns(prompt, recovery=True)


def test_build_prompt_includes_verification_commands_summary() -> None:
    """Initial execution prompt should list verification commands and project conventions reminder."""
    issue = _make_issue()
    worktree_path = Path("/worktree")

    class _FakePromptConfig:
        phases = {}

    prompt = build_prompt(
        issue,
        worktree_path,
        _FakePromptConfig(),  # type: ignore[arg-type]
        verification_commands_summary="- `just test`\n- `git diff --check`",
    )

    assert "Verification commands the runner will run before committing" in prompt
    assert "just test" in prompt
    assert "git diff --check" in prompt
    assert "project conventions" in prompt


def test_build_prompt_forbids_git_index_mutation_and_flag_reruns() -> None:
    """Execution prompt must forbid git index mutations and remind about verification flags."""
    issue = _make_issue()
    worktree_path = Path("/worktree")

    class _FakePromptConfig:
        phases = {}

    prompt = build_prompt(
        issue,
        worktree_path,
        _FakePromptConfig(),  # type: ignore[arg-type]
        verification_commands_summary="- `just test`\n- `git diff --check`",
    )

    _assert_forbids_git_index_mutation_and_flag_reruns(prompt)


def test_build_progress_continuation_prompt_includes_failure_context() -> None:
    """Continuation prompt should include previous failure and verification output."""
    issue = _make_issue()
    worktree_path = Path("/worktree")
    verification_results = [
        CommandResult(
            command=("ruff", "check"),
            return_code=1,
            stdout="E501 line too long\n",
            stderr="",
        )
    ]

    prompt = build_progress_continuation_prompt(
        issue,
        worktree_path,
        failure_summary="verification failed",
        verification_results=verification_results,
    )

    assert "Continue GitHub Issue" in prompt
    assert "verification failed" in prompt
    assert "ruff check" in prompt
    assert "E501 line too long" in prompt
    assert "project conventions" in prompt


def test_prd_delivery_failure_offers_runner_owned_gate_escape() -> None:
    """归档门禁的 recovery prompt 必须给出 runner 自持门禁的处理方式。

    否则 agent 会卡在「等独立 verifier PASS 后才归档」这类条目上反复重试
    （实证：freshai Issue #111 连续 5 次 attempt 都失败在同一行）。
    """
    prompt = format_prd_delivery_failure(
        "Acceptance Checklist has unchecked items in tasks/pending/example.md:\n"
        "  - L502: - [ ] 独立 verifier 对 rv-1～rv-6 全链证据 PASS 后才归档。"
    )

    assert "independent verifier" in prompt
    assert "[~]" in prompt
    assert "runner-owned gate" in prompt
    assert "Never tick an item whose evidence you did not actually produce." in prompt


def test_prd_delivery_detail_stays_diagnostic_only() -> None:
    """attempt 历史的 Detail 只记失败原因，不夹带通用指导。"""
    detail = format_prd_delivery_detail("Acceptance Checklist has unchecked items in x.md")

    assert detail.splitlines()[-1] == "Acceptance Checklist has unchecked items in x.md"
    assert "[~]" not in detail


# ── rv-2：上一轮交接记录回灌续作 prompt（限量 + 截断 + 无记录时不注入）──────────

_HANDOFF_RECORD = "\n".join(
    [
        "<!-- iar:failure-context checkpoint=a1b2c3d4 attempts=3 verifier=red "
        "evidence=tasks/evidence/example -->",
        "",
        "## Agent Runner Handoff — Recovery Budget Exhausted",
        "",
        "- Snapshot nature: `WIP checkpoint` — a mid-progress commit, "
        "**not** a finished implementation.",
        "- Checkpoint commit: `a1b2c3d4`",
        "- Verifier: formed an explicit **red** verdict; its findings are quoted in "
        "the gate report below.",
        "",
        "### Last gate report",
        "",
        "rv-3 not satisfied: the PRD detail panel stays blank.",
    ]
)

_STALE_HANDOFF_RECORD = (
    "<!-- iar:failure-context checkpoint=deadbee attempts=9 verifier=red "
    "evidence=tasks/evidence/old -->\n"
    "rv-1 not satisfied: the legacy export path was never wired."
)


def test_continuation_prompt_injects_previous_handoff_record() -> None:
    """上一轮的交接记录要出现在续作 prompt 里，且被明确标注为"陈述而非裁定"。

    这是本功能的主要收益：接手方不必从零反推上一轮干了什么。同时必须提醒它快照是
    WIP 中途进度,否则续作 agent 会把半成品当成完成品。
    """
    prompt = build_progress_continuation_prompt(
        _make_issue(),
        Path("/worktree"),
        previous_failure_context=_HANDOFF_RECORD,
    )

    assert "rv-3 not satisfied: the PRD detail panel stays blank." in prompt
    assert "Checkpoint commit: `a1b2c3d4`" in prompt
    assert "**statement**, not a ruling" in prompt
    assert "WIP mid-progress" in prompt


#: 本功能**之前**（base commit `87ab96ee`）由 `build_progress_continuation_prompt` 产出的
#: 逐字原文，从改动前的实现实跑采集。rv-2 场景③要求"无交接记录时 prompt 与本功能不存在
#: 时逐字一致"——拿改动后的同一个函数跟自己比是同义反复（实现就算注入了任意文本也照样
#: 通过），所以这里钉一份改前金标准。
_PRE_FEATURE_CONTINUATION_PROMPT = (
    "Continue GitHub Issue #42: Example Issue\n"
    "\n"
    "Issue URL: https://github.com/example/repo/issues/42\n"
    "Worktree: /worktree\n"
    "\n"
    "This worktree already contains committed progress from earlier runner attempts. "
    "Do not restart from scratch and do not revert existing commits. Inspect the "
    "current state first (`git log`, existing files, and the PRD Acceptance "
    "Checklist), then implement only what remains.\n"
    "If the Issue references a PRD, read it to see the remaining work.\n"
    "\n"
    "The previous attempt failed with:\n"
    "verification failed\n"
    "\n"
    "\n"
    "Execution rules:\n"
    "- Only modify files inside the current worktree.\n"
    "- Before requesting a commit, re-check project conventions (naming, dependency "
    "direction, file encoding, max line length, etc.).\n"
    "- Do not merge main, switch branches, push, or create PRs; the runner handles "
    "publishing.\n"
    "- Do not run `git add` or `git commit`; after finishing your changes, write "
    "`.agent-runner/commit-request.json` as JSON with `commit_message`.\n"
    "- Finish with a concise summary, tests run, and remaining risk."
)


def test_continuation_prompt_without_handoff_record_matches_pre_feature_golden() -> None:
    """没有交接记录时不注入、不报错，prompt 与本功能存在前的产物逐字相等。

    断言对象是**改动前实现实跑出来的金标准**，因此这条能抓到"空交接段仍插进一个空串
    把 prompt 撑出一个多余空行"这类真实回归。
    """
    prompt_without_record = build_progress_continuation_prompt(
        _make_issue(),
        Path("/worktree"),
        failure_summary="verification failed",
        previous_failure_context="",
    )

    assert prompt_without_record == _PRE_FEATURE_CONTINUATION_PROMPT
    assert "handoff" not in prompt_without_record.lower()
    assert "not a ruling" not in prompt_without_record
    # 反向：同一条输入只要给了记录就必须真的变长、变内容，否则说明注入根本没生效。
    prompt_with_record = build_progress_continuation_prompt(
        _make_issue(),
        Path("/worktree"),
        failure_summary="verification failed",
        previous_failure_context=_HANDOFF_RECORD,
    )
    assert prompt_with_record != prompt_without_record
    assert "rv-3 not satisfied" in prompt_with_record


def test_continuation_prompt_truncates_overlong_handoff_record() -> None:
    """超长交接记录被显式截断，且保留头部的关键字段而不是静默丢弃。

    记录含 attempt 历史，容易撑爆 prompt 预算；关键字段（快照 SHA、verifier 结论）
    排在头部，所以保头截尾既限量又不丢判断依据。截断必须留痕，否则接手方会以为自己
    看到了全文。
    """
    oversized_record = "\n".join(
        [
            "<!-- iar:failure-context checkpoint=a1b2c3d4 attempts=3 verifier=red -->",
            "- Checkpoint commit: `a1b2c3d4`",
            "- Verifier: formed an explicit **red** verdict.",
            "x" * 20000,
            "TAIL-ONLY-CONTENT-MUST-NOT-LEAK",
        ]
    )

    prompt = build_progress_continuation_prompt(
        _make_issue(),
        Path("/worktree"),
        previous_failure_context=oversized_record,
    )

    assert "Checkpoint commit: `a1b2c3d4`" in prompt
    assert "TAIL-ONLY-CONTENT-MUST-NOT-LEAK" not in prompt
    assert "[handoff record truncated" in prompt


def test_find_latest_failure_context_comment_is_bounded_to_most_recent() -> None:
    """latest-wins 只取最近一条：过期失败不得进 prompt，也不得被全量注入。"""
    from backend.core.use_cases.agent_runner_events import (
        find_latest_failure_context_comment,
    )

    comments = [
        _STALE_HANDOFF_RECORD,
        "unrelated human comment",
        _HANDOFF_RECORD,
        "a later comment without any marker",
    ]

    selected = find_latest_failure_context_comment(comments)

    assert selected == _HANDOFF_RECORD
    assert "rv-1 not satisfied" not in selected
    assert find_latest_failure_context_comment(["plain", "unrelated"]) is None
    assert find_latest_failure_context_comment([]) is None


def test_failure_context_marker_round_trips_without_a_shared_contract_object() -> None:
    """marker 只是键值元数据：格式化→解析成对，且不引入新的契约对象或枚举。

    无快照时 checkpoint 渲染成 ``none`` 而不是省略该字段，使读取侧能分辨"确实没有
    快照"与"marker 不带这个键"。
    """
    from backend.core.use_cases.agent_runner_events import (
        format_failure_context_marker,
        has_failure_context_marker,
    )

    marker_without_snapshot = format_failure_context_marker(
        checkpoint_sha=None,
        attempt_count=0,
        verifier_state="no-verdict",
        evidence_dir="tasks/evidence/example",
    )

    assert marker_without_snapshot == (
        "<!-- iar:failure-context checkpoint=none attempts=0 "
        "verifier=no-verdict evidence=tasks/evidence/example -->"
    )
    assert has_failure_context_marker(marker_without_snapshot)
    assert not has_failure_context_marker("<!-- iar:event version=1 phase=x cycle=0 -->")
    # PR 正文尾部的回链段带 ``iar:failure-context-ref`` 前缀，不能被误认成交接记录。
    assert not has_failure_context_marker("<!-- iar:failure-context-ref -->")

"""交付收尾层（Closeout Agent）的行为测试。

覆盖三条硬约束与分流边界：收尾成功后本轮不再调实现 Agent；举不出证据的验收条目
不会被勾上；收尾越界改代码即判失败升级；三类真失败永远不进收尾层。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    DeliveryGateFailureKind,
    FailureType,
    IssueSummary,
    PostPrSupervisorConfig,
    PrePrReviewConfig,
    RunnerConfig,
    ValidationConfig,
)
from backend.core.use_cases.agent_runner_closeout import (
    CloseoutAllowedScope,
    CloseoutSnapshot,
    build_closeout_allowed_scope,
    find_closeout_scope_violations,
    format_closeout_attempt_detail,
    summarize_closeout_changes,
)
from backend.core.use_cases.run_agent_once import (
    MaxRetriesExceededError,
    run_agent_until_committed,
)
from tests.conftest import FakeProcessRunner
from tests.support.agent_runner import (
    is_bash_wrapped_verification_call,
    write_commit_request,
)

_PRD_RELATIVE_PATH = "tasks/pending/example.md"
_CHANGE_LOG_BLOCK = "\n".join(
    [
        "",
        "## Change Log",
        "",
        "### 收尾补记",
        "- 类型：验收状态更新",
        "- 原文：验收项未勾",
        "- 变更后：验收项已勾",
        "- 原因：证据已产出",
        "- 影响：交付状态更新",
        "- 审核：runner 门禁重跑",
        "",
    ]
)


def _make_issue() -> IssueSummary:
    """Return the closeout fixture Issue pointing at a pending PRD."""
    return IssueSummary(
        number=7,
        title="Closeout fixture",
        url="https://github.com/example/repo/issues/7",
        body=f"PRD path: `{_PRD_RELATIVE_PATH}`",
        labels=("agent/ready",),
    )


def _prd_text(*, second_item_checked: bool) -> str:
    """Render the fixture PRD with its second checklist item ticked or not."""
    second_mark = "x" if second_item_checked else " "
    return "\n".join(
        [
            "# PRD: Closeout fixture",
            "",
            "## 9. Acceptance Checklist",
            "",
            "- [x] item 1",
            f"- [{second_mark}] item 2",
            "",
        ]
    )


def _write_fixture_worktree(tmp_path: Path) -> Path:
    """Create a worktree holding an incomplete PRD plus a pending commit request."""
    worktree_path = tmp_path / "issue-7"
    worktree_path.mkdir()
    prd_path = worktree_path / _PRD_RELATIVE_PATH
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(_prd_text(second_item_checked=False), encoding="utf-8")
    (worktree_path / "tasks" / "archive").mkdir(parents=True, exist_ok=True)
    (worktree_path / "src").mkdir(parents=True, exist_ok=True)
    (worktree_path / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    write_commit_request(worktree_path, "agent: implement")
    return worktree_path


def _closeout_config(**runner_overrides: object) -> AppConfig:
    """Return a config whose only enabled gate chain is the PRD delivery gate."""
    runner_kwargs: dict[str, object] = {
        "max_recovery_attempts": 1,
        "recovery_retry_delay_seconds": 0,
        "timeout_seconds": 100,
        "fix_timeout_seconds": 40,
        "closeout_timeout_seconds": 20,
        "closeout_visual_timeout_seconds": 90,
        "verification_commands": ("just lint",),
        "agent_fallback_order": (),
    }
    runner_kwargs.update(runner_overrides)
    return AppConfig(
        runner=RunnerConfig(**runner_kwargs),  # type: ignore[arg-type]
        validation=ValidationConfig(verifier_enabled=False),
        pre_pr_review=PrePrReviewConfig(enabled=False),
        post_pr_supervisor=PostPrSupervisorConfig(enabled=False),
    )


class _CloseoutScenarioRunner(FakeProcessRunner):
    """Fake runner whose closeout pass performs a configurable edit.

    ``closeout_action`` 收到 worktree 路径，负责模拟收尾 agent 的真实写入行为
    （勾清单、乱勾、改源码……）。除此之外它只负责让 git / 验证命令表现正常。
    """

    def __init__(self, worktree_path: Path, closeout_action) -> None:
        super().__init__()
        self.worktree_path = worktree_path
        self.closeout_action = closeout_action
        self.agent_prompts: list[str] = []
        self.agent_timeouts: list[int | None] = []
        self._committed = False

    def run(
        self,
        command,
        *,
        cwd,
        check=True,
        timeout=None,
        inactivity_timeout=None,
        capture_output=True,
        input_text=None,
        label=None,
        output_sink=None,
        output_protocol=None,
    ):
        """Serve the git / verification / agent calls the execution loop makes."""
        command_tuple = tuple(command)
        self.calls.append(list(command))
        if command_tuple[:1] == ("claude",):
            prompt = command_tuple[-1]
            self.agent_prompts.append(prompt)
            self.agent_timeouts.append(timeout)
            if "Finish the delivery closeout" in prompt:
                self.closeout_action(self.worktree_path)
            return CommandResult(command_tuple, 0, "", "")
        if command_tuple == ("git", "rev-parse", "HEAD"):
            return CommandResult(
                command_tuple,
                0,
                "after-sha\n" if self._committed else "before-sha\n",
                "",
            )
        if command_tuple == ("git", "branch", "--show-current"):
            return CommandResult(command_tuple, 0, "issue-7\n", "")
        if command_tuple[:3] == ("git", "status", "--porcelain"):
            stdout = "" if self._committed else " M src/feature.py\0"
            return CommandResult(command_tuple, 0, stdout, "")
        if command_tuple == ("git", "commit", "-m", "agent: implement"):
            self._committed = True
            return CommandResult(command_tuple, 0, "", "")
        if command_tuple[:2] == ("git", "mv"):
            source_path = Path(cwd) / command_tuple[2]
            target_path = Path(cwd) / command_tuple[3]
            if source_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.rename(target_path)
            return CommandResult(command_tuple, 0, "", "")
        if command_tuple == ("just", "lint") or is_bash_wrapped_verification_call(
            command, ("just", "lint")
        ):
            return CommandResult(command_tuple, 0, "", "")
        return CommandResult(command_tuple, 0, "", "")


def _tick_checklist_with_change_log(worktree_path: Path) -> None:
    """收尾 agent 的合规行为：勾上条目并追加一条完整 Change Log。"""
    prd_path = worktree_path / _PRD_RELATIVE_PATH
    prd_path.write_text(
        _prd_text(second_item_checked=True) + _CHANGE_LOG_BLOCK,
        encoding="utf-8",
    )


def _tick_checklist_without_change_log(worktree_path: Path) -> None:
    """收尾 agent 乱勾：勾满清单但不留 Change Log，门禁重跑必须挡回去。"""
    prd_path = worktree_path / _PRD_RELATIVE_PATH
    prd_path.write_text(_prd_text(second_item_checked=True), encoding="utf-8")


def _tick_checklist_and_touch_source(worktree_path: Path) -> None:
    """收尾 agent 越界：顺手改了一个 ``src/`` 源文件。"""
    _tick_checklist_with_change_log(worktree_path)
    (worktree_path / "src" / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")


def _run_loop(worktree_path: Path, config: AppConfig, fake_runner: FakeProcessRunner):
    """Drive ``run_agent_until_committed`` against the closeout fixture."""
    return run_agent_until_committed(
        selected_agent="claude",
        issue=_make_issue(),
        worktree_path=worktree_path,
        config=config,
        process_runner=fake_runner,
        before_sha="before-sha",
        expected_branch="issue-7",
    )


def test_closeout_repairs_unchecked_checklist_within_the_same_attempt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """rv-1: 收尾成功后本轮直接继续发布，实现 Agent 只被调用一次。"""
    worktree_path = _write_fixture_worktree(tmp_path)
    fake_runner = _CloseoutScenarioRunner(worktree_path, _tick_checklist_with_change_log)

    with caplog.at_level(logging.INFO, logger="backend.core.use_cases.agent_runner_closeout"):
        result = _run_loop(worktree_path, _closeout_config(), fake_runner)

    assert len(fake_runner.agent_prompts) == 2
    assert "Finish the delivery closeout" in fake_runner.agent_prompts[1]
    assert all("Recovery attempt" not in prompt for prompt in fake_runner.agent_prompts)
    closeout_attempts = [
        attempt
        for attempt in result.attempt_results
        if attempt.failure_type == FailureType.DELIVERY_CLOSEOUT
    ]
    assert len(closeout_attempts) == 1
    assert closeout_attempts[0].recovered is True
    assert "item 2" in closeout_attempts[0].detail
    assert "收尾补记" in closeout_attempts[0].detail
    assert result.attempt_results[-1].failure_type == FailureType.SUCCESS
    assert "Starting Closeout Agent for Issue #7 (kind=checklist_unchecked, timeout=20s)." in (
        caplog.text
    )
    assert any(phase.name == "closeout" for phase in closeout_attempts[0].phase_durations)


def test_closeout_cannot_tick_items_without_passing_the_full_gate_chain(
    tmp_path: Path,
) -> None:
    """rv-2: 举不出证据的勾选被门禁链重跑挡回，PRD 还原为未勾并整轮升级。"""
    worktree_path = _write_fixture_worktree(tmp_path)
    fake_runner = _CloseoutScenarioRunner(worktree_path, _tick_checklist_without_change_log)

    with pytest.raises(MaxRetriesExceededError) as error_info:
        _run_loop(worktree_path, _closeout_config(max_recovery_attempts=0), fake_runner)

    prd_text = (worktree_path / _PRD_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "- [ ] item 2" in prd_text
    assert not any(
        attempt.failure_type == FailureType.DELIVERY_CLOSEOUT
        for attempt in error_info.value.attempt_results
    )


def test_closeout_out_of_scope_source_edit_fails_the_closeout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """rv-3: 收尾 pass 改了 ``src/`` 下的文件时判越界并升级，本轮不发布。"""
    worktree_path = _write_fixture_worktree(tmp_path)
    fake_runner = _CloseoutScenarioRunner(worktree_path, _tick_checklist_and_touch_source)

    with caplog.at_level(logging.WARNING, logger="backend.core.use_cases.run_agent_once"):
        with pytest.raises(MaxRetriesExceededError) as error_info:
            _run_loop(worktree_path, _closeout_config(max_recovery_attempts=0), fake_runner)

    assert "modified out-of-scope files" in caplog.text
    assert "src/feature.py" in caplog.text
    assert not any(
        attempt.failure_type == FailureType.DELIVERY_CLOSEOUT
        for attempt in error_info.value.attempt_results
    )
    # 越界写入本身不回滚（按设计交给完整重跑处理），但 PRD 编辑被撤销。
    prd_text = (worktree_path / _PRD_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "- [ ] item 2" in prd_text


def test_closeout_layer_is_skipped_when_disabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """rv-5: ``closeout_agent_enabled=false`` 时行为与本层落地前一致。"""
    worktree_path = _write_fixture_worktree(tmp_path)
    fake_runner = _CloseoutScenarioRunner(worktree_path, _tick_checklist_with_change_log)

    with caplog.at_level(logging.INFO, logger="backend.core.use_cases.run_agent_once"):
        with pytest.raises(MaxRetriesExceededError) as error_info:
            _run_loop(
                worktree_path,
                _closeout_config(max_recovery_attempts=0, closeout_agent_enabled=False),
                fake_runner,
            )

    assert len(fake_runner.agent_prompts) == 1
    assert "Closeout Agent disabled for Issue #7" in caplog.text
    assert len(error_info.value.attempt_results) == 1
    assert not any(
        attempt.failure_type == FailureType.DELIVERY_CLOSEOUT
        for attempt in error_info.value.attempt_results
    )
    assert "PRD delivery check failed" in error_info.value.attempt_results[0].detail
    prd_text = (worktree_path / _PRD_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "- [ ] item 2" in prd_text


def test_visual_evidence_closeout_uses_its_own_timeout() -> None:
    """rv-6: 视觉证据补采取视觉超时，其余取文本超时；两者按 None 回退。"""
    runner_config = RunnerConfig(
        timeout_seconds=100,
        fix_timeout_seconds=40,
        closeout_timeout_seconds=20,
        closeout_visual_timeout_seconds=90,
    )
    assert (
        runner_config.resolve_closeout_timeout_seconds(
            DeliveryGateFailureKind.FRONTEND_VISUAL_EVIDENCE_MISSING
        )
        == 90
    )
    assert (
        runner_config.resolve_closeout_timeout_seconds(DeliveryGateFailureKind.CHECKLIST_UNCHECKED)
        == 20
    )

    fallback_config = RunnerConfig(
        timeout_seconds=100,
        fix_timeout_seconds=40,
        closeout_timeout_seconds=None,
        closeout_visual_timeout_seconds=None,
    )
    assert (
        fallback_config.resolve_closeout_timeout_seconds(
            DeliveryGateFailureKind.FRONTEND_VISUAL_EVIDENCE_MISSING
        )
        == 40
    )

    bare_config = RunnerConfig(
        timeout_seconds=100,
        fix_timeout_seconds=None,
        closeout_timeout_seconds=None,
        closeout_visual_timeout_seconds=None,
    )
    assert (
        bare_config.resolve_closeout_timeout_seconds(DeliveryGateFailureKind.CHECKLIST_UNCHECKED)
        == 100
    )


def test_substantive_gate_failures_never_reach_the_closeout_layer(tmp_path: Path) -> None:
    """rv-4: 真失败（此处为证据目录为空）不进收尾层，走既有整轮重跑。"""
    worktree_path = _write_fixture_worktree(tmp_path)
    prd_path = worktree_path / _PRD_RELATIVE_PATH
    prd_path.write_text(_prd_text(second_item_checked=True), encoding="utf-8")
    issue = IssueSummary(
        number=7,
        title="Closeout fixture",
        url="https://github.com/example/repo/issues/7",
        body="\n".join(
            [
                f"PRD path: `{_PRD_RELATIVE_PATH}`",
                "",
                "## Realistic Validation",
                "- [ ] 通过真实 CLI 入口执行并保存输出。",
            ]
        ),
        labels=("agent/ready",),
    )
    fake_runner = _CloseoutScenarioRunner(worktree_path, _tick_checklist_with_change_log)

    with pytest.raises(MaxRetriesExceededError) as error_info:
        run_agent_until_committed(
            selected_agent="claude",
            issue=issue,
            worktree_path=worktree_path,
            config=_closeout_config(max_recovery_attempts=0),
            process_runner=fake_runner,
            before_sha="before-sha",
            expected_branch="issue-7",
        )

    assert len(fake_runner.agent_prompts) == 1
    assert all("Finish the delivery closeout" not in prompt for prompt in fake_runner.agent_prompts)
    assert not any(
        attempt.failure_type == FailureType.DELIVERY_CLOSEOUT
        for attempt in error_info.value.attempt_results
    )


def test_summarize_closeout_changes_reports_the_actual_diff() -> None:
    """FR-12: 留痕以前后文本差异为准，删除条目不冒充勾选。"""
    before = CloseoutSnapshot(
        prd_text="\n".join(
            [
                "## Acceptance Checklist",
                "",
                "- [ ] ticked later",
                "- [ ] deleted later",
                "- [ ] still unchecked",
            ]
        ),
        evidence_file_names=("rv-1-old.txt",),
        changed_path_digests={},
    )
    after = CloseoutSnapshot(
        prd_text="\n".join(
            [
                "## Acceptance Checklist",
                "",
                "- [x] ticked later",
                "- [ ] still unchecked",
                "",
                "## Change Log",
                "",
                "### 收尾补记",
                "- 类型：验收状态更新",
            ]
        ),
        evidence_file_names=("rv-1-old.txt", "rv-2-new.png"),
        changed_path_digests={},
    )

    summary = summarize_closeout_changes(before, after)

    assert summary.checked_items == ("ticked later",)
    assert summary.resolved_items == ("deleted later",)
    assert summary.new_change_log_entries == ("收尾补记",)
    assert summary.added_evidence_files == ("rv-2-new.png",)
    detail = format_closeout_attempt_detail(summary)
    assert "ticked later" in detail
    assert "deleted later" in detail
    assert "rv-2-new.png" in detail
    # 汇总行是 attempt 历史表唯一显示的一行，必须点名而不只报数字。
    rollup_line = detail.splitlines()[-1]
    assert rollup_line.startswith("Closeout diff: ")
    assert "ticked later" in rollup_line
    assert "收尾补记" in rollup_line or "1 Change Log entry" in rollup_line


def test_find_closeout_scope_violations_only_flags_content_changes() -> None:
    """越界判定看内容摘要差异，而不是"出现在改动路径集合里"。"""
    allowed_scope = CloseoutAllowedScope(
        prd_paths=("tasks/pending/example.md", "tasks/archive/example.md"),
        evidence_dir=".iar/evidence",
    )
    before = CloseoutSnapshot(
        prd_text="",
        evidence_file_names=(),
        changed_path_digests={
            "src/feature.py": "aaa",
            "tasks/pending/example.md": "bbb",
        },
    )
    unchanged_source = CloseoutSnapshot(
        prd_text="",
        evidence_file_names=(),
        changed_path_digests={
            "src/feature.py": "aaa",
            "tasks/pending/example.md": "ccc",
            ".iar/evidence/rv-1.png": "ddd",
        },
    )
    assert find_closeout_scope_violations(before, unchanged_source, allowed_scope) == ()

    changed_source = CloseoutSnapshot(
        prd_text="",
        evidence_file_names=(),
        changed_path_digests={
            "src/feature.py": "zzz",
            "tasks/pending/example.md": "bbb",
        },
    )
    assert find_closeout_scope_violations(before, changed_source, allowed_scope) == (
        "src/feature.py",
    )


def test_build_closeout_allowed_scope_covers_the_archive_target() -> None:
    """允许集必须同时含 pending 与 archive 两个 PRD 路径。"""
    scope = build_closeout_allowed_scope(_make_issue(), _closeout_config())

    assert scope.prd_paths == (_PRD_RELATIVE_PATH, "tasks/archive/example.md")
    assert scope.allows("tasks/archive/example.md") is True
    # 收尾 pass 可在证据目录（含按任务子目录）内补证据。
    assert scope.allows("tasks/evidence/example/scripts/rv-1-oracle.py") is True
    assert scope.allows("src/feature.py") is False

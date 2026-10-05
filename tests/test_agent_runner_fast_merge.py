"""``iar run --fast-merge`` 快速通道行为测试。

覆盖 PRD P1-FEAT-20261005-215933 的四个自动门禁点：旁路生效、默认不旁路、
stack 拒绝、builder 失败不掩盖；外加 marker 可解析性、PR 正文标注与
普通 PR 负例、旗标机读表面。
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    GeneratedContentConfig,
    GitConfig,
    IssueSummary,
    RunnerConfig,
)
from backend.core.use_cases.agent_runner_dependencies import (
    format_fast_merge_marker,
    parse_fast_merge_marker,
)
from backend.core.use_cases.agent_runner_final_verification import (
    FinalVerificationRequest,
    ensure_final_verifier_verdict,
)
from backend.core.use_cases.agent_runner_structured_evidence import ValidationEvidenceError
from backend.core.use_cases.run_verifier_agent import ValidationVerdict
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import make_ready_issue

# ---------------------------------------------------------------------------
# marker 族：格式与解析
# ---------------------------------------------------------------------------


def test_fast_merge_marker_round_trip() -> None:
    """marker 由代码生成、可被同族解析函数读回 Issue 编号；非 marker 返回 None。"""
    marker = format_fast_merge_marker(12)
    assert marker == "<!-- iar:fast-merge issued=12 -->"
    assert parse_fast_merge_marker(f"body\n\n{marker}\n") == 12
    assert parse_fast_merge_marker('<!-- iar:depends-on #3 mode="stack" -->') is None
    assert parse_fast_merge_marker("<!-- iar:fast-merge -->") is None


# ---------------------------------------------------------------------------
# core 旁路：Phase 4.5
# ---------------------------------------------------------------------------


def _patch_execution_loop_gates(
    monkeypatch: pytest.MonkeyPatch,
    *,
    committed: list[bool],
    gate_calls: list[str],
) -> None:
    """把执行循环的 agent 调用与验证门禁替换为可观测的探针。

    ``ensure_validation_evidence_ready`` 在 Phase 3.5（提交前）与 Phase 4.5
    （提交后 rv 复跑）都会被调用，两次都记为 ``"evidence"``；快速通道只旁路
    Phase 4.5，故 Phase 3.5 的一次仍会出现。``"rv_reexec"`` / ``"verifier"``
    只在 Phase 4.5 触发，是旁路的判据。
    """
    from backend.core.use_cases import run_agent_execution_loop as execution_loop
    from backend.core.use_cases import run_verifier_agent as verifier_module

    def _commit(*args: object, **kwargs: object) -> list[object]:
        committed[0] = True
        return []

    def _verify(*args: object, **kwargs: object) -> ValidationVerdict:
        gate_calls.append("verifier")
        return ValidationVerdict(risk="green")

    monkeypatch.setattr(execution_loop, "run_agent", lambda *args, **kwargs: None)
    monkeypatch.setattr(execution_loop, "run_verification", lambda *args: [])
    monkeypatch.setattr(
        execution_loop, "get_head_sha", lambda *args: "new" if committed[0] else "old"
    )
    monkeypatch.setattr(execution_loop, "has_changes", lambda *args: not committed[0])
    monkeypatch.setattr(execution_loop, "commit_requested_changes", _commit)
    monkeypatch.setattr(execution_loop, "ensure_prd_delivery_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        execution_loop, "warn_legacy_evidence_helpers", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        execution_loop, "ensure_no_misplaced_evidence_helpers", lambda *args, **kwargs: None
    )

    def _evidence(*args: object, **kwargs: object) -> None:
        gate_calls.append("evidence")

    def _rv(*args: object, **kwargs: object) -> None:
        gate_calls.append("rv_reexec")

    monkeypatch.setattr(execution_loop, "ensure_validation_evidence_ready", _evidence)
    monkeypatch.setattr(execution_loop, "ensure_validation_commands_pass", _rv)
    monkeypatch.setattr(verifier_module, "run_verifier_gate", _verify)


def _execution_request(
    execution_loop_module: Any,
    tmp_path: Path,
    *,
    fast_merge: bool,
    config: AppConfig | None = None,
) -> Any:
    """构造一次最小执行请求（builder 恒成功提交一版代码）。"""
    return execution_loop_module.AgentExecutionRequest(
        selected_agent="codex",
        issue=IssueSummary(7, "Example", "https://example.test/7", "body", ()),
        worktree_path=tmp_path,
        config=config or AppConfig(),
        process_runner=FakeProcessRunner(),
        before_sha="old",
        expected_branch="issue-7",
        fast_merge=fast_merge,
    )


def test_fast_merge_skips_phase_45_gates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """加旗标：Phase 4.5 两道验证门禁零调用、结论为空、跳过日志含旗标来源。"""
    from backend.core.use_cases import run_agent_execution_loop as execution_loop

    committed = [False]
    gate_calls: list[str] = []
    _patch_execution_loop_gates(monkeypatch, committed=committed, gate_calls=gate_calls)

    with caplog.at_level(logging.INFO):
        result = execution_loop.run_agent_until_committed(
            _execution_request(execution_loop, tmp_path, fast_merge=True)
        )

    # Phase 3.5 的证据齐备检查照常跑一次；被旁路的只是 Phase 4.5 的 rv_reexec + verifier。
    assert gate_calls == ["evidence"]
    assert "rv_reexec" not in gate_calls
    assert "verifier" not in gate_calls
    assert result.verifier_verdict is None
    skip_lines = [rec.message for rec in caplog.records if "Fast-merge" in rec.message]
    assert any("--fast-merge" in message for message in skip_lines)


def test_default_run_still_executes_phase_45_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """不加旗标（默认路径）：rv_reexec 与 verifier 照常按序执行——零回归锚点。"""
    from backend.core.use_cases import run_agent_execution_loop as execution_loop

    committed = [False]
    gate_calls: list[str] = []
    _patch_execution_loop_gates(monkeypatch, committed=committed, gate_calls=gate_calls)

    result = execution_loop.run_agent_until_committed(
        _execution_request(execution_loop, tmp_path, fast_merge=False)
    )

    # 默认路径：Phase 3.5 与 Phase 4.5 各查一次证据齐备，再复跑 rv 命令、独立 verifier。
    assert gate_calls == ["evidence", "evidence", "rv_reexec", "verifier"]
    assert result.verifier_verdict is not None
    assert result.verifier_verdict.risk == "green"


def test_fast_merge_does_not_mask_builder_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """builder 没产出可用提交时，快速通道照常报错、不开 PR、不掩盖失败。"""
    from backend.core.use_cases import run_agent_execution_loop as execution_loop
    from backend.core.use_cases.run_agent_once import MaxRetriesExceededError

    committed = [False]
    gate_calls: list[str] = []
    _patch_execution_loop_gates(monkeypatch, committed=committed, gate_calls=gate_calls)

    def _never_commit(*args: object, **kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(execution_loop, "commit_requested_changes", _never_commit)
    # 无恢复重试：首次未提交即报错，既贴合"不掩盖失败"的断言，又免去恢复等待。
    no_retry_config = AppConfig(runner=RunnerConfig(max_recovery_attempts=0))

    with pytest.raises(MaxRetriesExceededError):
        execution_loop.run_agent_until_committed(
            _execution_request(execution_loop, tmp_path, fast_merge=True, config=no_retry_config)
        )

    assert gate_calls == ["evidence"]
    assert "rv_reexec" not in gate_calls
    assert "verifier" not in gate_calls


# ---------------------------------------------------------------------------
# 发布前最终复核旁路
# ---------------------------------------------------------------------------


def _final_request(tmp_path: Path, *, fast_merge: bool) -> FinalVerificationRequest:
    return FinalVerificationRequest(
        issue=IssueSummary(7, "Example", "https://example.test/7", "body", ()),
        worktree_path=tmp_path,
        config=AppConfig(),
        process_runner=FakeProcessRunner(),
        selected_agent="codex",
        verified_sha="builder-sha",
        verifier_verdict=None,
        fast_merge=fast_merge,
    )


def test_final_verifier_bypassed_under_fast_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """review 改动 HEAD 后，快速通道不再重跑最终 RV / verifier。"""
    from backend.core.use_cases import agent_runner_final_verification as final_gate

    monkeypatch.setattr(final_gate, "has_changes", lambda *args: False)
    monkeypatch.setattr(final_gate, "get_head_sha", lambda *args: "review-sha")
    monkeypatch.setattr(
        final_gate,
        "ensure_validation_evidence_ready",
        lambda *args: pytest.fail("fast-merge must not re-run RV evidence gate"),
    )
    monkeypatch.setattr(
        final_gate,
        "run_verifier_gate",
        lambda *args, **kwargs: pytest.fail("fast-merge must not run the verifier"),
    )

    assert ensure_final_verifier_verdict(_final_request(tmp_path, fast_merge=True)) is None


def test_final_verification_clean_tree_precondition_still_enforced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """快速通道只旁路验证门禁，不放松"已提交干净工作树"的发布前提。"""
    from backend.core.use_cases import agent_runner_final_verification as final_gate

    monkeypatch.setattr(final_gate, "has_changes", lambda *args: True)
    with pytest.raises(ValidationEvidenceError, match="clean committed worktree"):
        ensure_final_verifier_verdict(_final_request(tmp_path, fast_merge=True))


# ---------------------------------------------------------------------------
# PR 正文标注（走发布编排，不直接拼 body）
# ---------------------------------------------------------------------------


def _publish_draft_pr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fast_merge: bool) -> str:
    """经 _review_verify_create_pr 真实发布链路创建 Draft PR，返回捕获的 PR 正文。"""
    from backend.core.use_cases import agent_runner_publication as publication

    monkeypatch.setattr(publication, "run_pre_pr_review", lambda **kwargs: ("new-sha", []))
    monkeypatch.setattr(publication, "get_head_sha", lambda *args: "new-sha")

    observed: list[bool] = []

    def _record_final(request: FinalVerificationRequest) -> None:
        observed.append(request.fast_merge)
        return None

    monkeypatch.setattr(publication, "ensure_final_verifier_verdict", _record_final)

    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-7\n",
                stderr="",
            )
        }
    )
    config = AppConfig(
        git=GitConfig(remote="origin", base_branch="main"),
        generated_content=GeneratedContentConfig(enabled=False),
    )
    request = publication._PublicationReviewRequest(
        verification_request=FinalVerificationRequest(
            issue=IssueSummary(7, "Example", "https://example.test/7", "body", ()),
            worktree_path=tmp_path,
            config=config,
            process_runner=fake_runner,
            selected_agent="codex",
            verified_sha="new-sha",
            verifier_verdict=None,
            fast_merge=fast_merge,
        ),
        github_client=fake_client,
        expected_branch="issue-7",
        verification_results=[],
        push_callback=lambda: None,
        content_generator=None,
    )
    publication._review_verify_create_pr(request)

    assert observed == [fast_merge]
    pr_calls = [c for c in fake_client.calls if c["method"] == "create_draft_pr"]
    assert len(pr_calls) == 1
    return str(pr_calls[0]["body"])


def test_fast_merge_pr_body_carries_self_declaration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """快速通道 PR 正文带机器可读 marker 与人读未验证说明。"""
    pr_body = _publish_draft_pr(monkeypatch, tmp_path, fast_merge=True)

    assert parse_fast_merge_marker(pr_body) == 7
    assert "本 PR 经快速通道发布，未经过自动化验证门禁，合并前请人工验证" in pr_body


def test_normal_pr_body_has_no_fast_merge_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """负例：默认发布的 PR 正文不含任何 fast-merge 标注。"""
    pr_body = _publish_draft_pr(monkeypatch, tmp_path, fast_merge=False)

    assert "iar:fast-merge" not in pr_body


# ---------------------------------------------------------------------------
# api 表面：旗标解析、组合拒绝、stack 门禁、dry-run 预览
# ---------------------------------------------------------------------------


def test_parser_accepts_fast_merge_flag() -> None:
    """argparse facade 解析 --fast-merge；缺省 False（默认行为零变化）。"""
    from backend.api.cli_parser import build_parser

    parsed = build_parser().parse_args(["run", "--issue", "5", "--fast-merge"])
    assert parsed.fast_merge is True
    default_parsed = build_parser().parse_args(["run", "--issue", "5"])
    assert default_parsed.fast_merge is False


def _run_context(**parsed_kwargs: Any) -> Any:
    from backend.api.cli_parsed_context import ParsedCommandContext
    from backend.api.cli_output import OUTPUT_FORMAT_TABLE

    defaults: dict[str, Any] = {
        "command": "run",
        "prd_path": None,
        "dry_run": False,
        "issue": None,
        "all_ready": False,
        "takeover": False,
        "yes": False,
        "fast_merge": True,
        "agent": "auto",
        "max_issues": None,
        "config": None,
        "repo": None,
        "repo_id": None,
        "all_repositories": False,
    }
    defaults.update(parsed_kwargs)
    return ParsedCommandContext(
        parsed=argparse.Namespace(**defaults),
        process_runner=None,
        runner_settings=None,
        repo_id=None,
        repo_override=None,
        github_client_factory=None,
        output_format=OUTPUT_FORMAT_TABLE,
    )


def test_fast_merge_rejected_with_all_ready() -> None:
    """快速通道只对单一目标定义；--all-ready 组合在触达仓库前即用法错误。"""
    from backend.api.cli_exit_codes import ExitCode
    from backend.api.cli_output import CliError
    from backend.api.cli_parsed_commands.runner import run_run_command

    with pytest.raises(CliError) as error:
        run_run_command(_run_context(issue=None, all_ready=True))
    assert error.value.code == ExitCode.USAGE
    assert "--all-ready" in str(error.value)


def _stack_issue_body(upstream: int = 3) -> str:
    return (
        "# PRD\n\n- GitHub Issue: https://github.com/org/repo/issues/7\n\n"
        f'<!-- iar:depends-on #{upstream} mode="stack" -->\n'
    )


def _stack_gate_context(fake_client: FakeGitHubClient) -> Any:
    from backend.api.cli_parsed_context import ParsedCommandContext
    from backend.api.cli_output import OUTPUT_FORMAT_TABLE

    return ParsedCommandContext(
        parsed=argparse.Namespace(command="run"),
        process_runner=None,
        runner_settings=None,
        repo_id=None,
        repo_override=None,
        github_client_factory=lambda repo_path: fake_client,
        output_format=OUTPUT_FORMAT_TABLE,
    )


def test_stack_dependency_issue_rejected_before_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stack 声明 Issue + --fast-merge → 用法错误；无 stack 声明 → 放行。"""
    from backend.api.cli_exit_codes import ExitCode
    from backend.api.cli_output import CliError
    from backend.api.cli_parsed_commands.runner import _reject_fast_merge_on_stack_issue

    stack_client = FakeGitHubClient()
    stack_client.get_issue = lambda number: IssueSummary(
        number, "Example", f"https://example.test/{number}", _stack_issue_body(), ()
    )
    contexts = [SimpleNamespace(repo_path=tmp_path)]

    with pytest.raises(CliError) as error:
        _reject_fast_merge_on_stack_issue(
            _stack_gate_context(stack_client), contexts=contexts, target_issue=7
        )
    assert error.value.code == ExitCode.USAGE
    assert "stack" in str(error.value)

    plain_client = FakeGitHubClient()
    plain_client.get_issue = lambda number: IssueSummary(
        number, "Example", f"https://example.test/{number}", "# PRD 无依赖声明", ()
    )
    _reject_fast_merge_on_stack_issue(
        _stack_gate_context(plain_client), contexts=contexts, target_issue=7
    )


def test_stack_gate_is_fail_closed(tmp_path: Path) -> None:
    """无法证明目标不是 stack 声明时一律拒绝（Issue 读不到也不放行旁路）。"""
    from backend.api.cli_output import CliError
    from backend.api.cli_parsed_commands.runner import _reject_fast_merge_on_stack_issue

    broken_client = FakeGitHubClient()

    def _raise(_number: int) -> IssueSummary:
        raise RuntimeError("gh unavailable")

    broken_client.get_issue = _raise
    with pytest.raises(CliError):
        _reject_fast_merge_on_stack_issue(
            _stack_gate_context(broken_client),
            contexts=[SimpleNamespace(repo_path=tmp_path)],
            target_issue=7,
        )


def test_dry_run_preview_reports_fast_merge() -> None:
    """机读预览显式携带 fast_merge，供上层调用方按同一契约消费。"""
    from backend.api.cli_parsed_commands.runner import _dry_run_preview

    context = SimpleNamespace(repo_id="keda-test", repo_path=Path("/tmp/keda-test"))
    preview = _dry_run_preview(
        [context], agent="auto", max_issues=1, target_issue=7, fast_merge=True
    )
    assert preview["fast_merge"] is True
    assert preview["target_issue"] == 7
    assert preview["all_ready"] is False


def test_make_ready_issue_shape() -> None:
    """守卫：本文件依赖的共享夹具仍带 body 字段（marker 解析的前提）。"""
    assert isinstance(make_ready_issue().body, str)

"""``iar run --direct-pr`` 直发档行为测试（PRD P1-FEAT-20261006-122336 FR-14~FR-19）。

覆盖三件事：直发档的跳过范围（严格包含快速通道）、档位作为唯一事实源的派生关系
（``--fast-merge`` 外部行为逐字不变的负例）、以及两个旗标的互斥与"仅限无 PRD 锚点"
前置拒绝（fail-closed）。
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    GeneratedContentConfig,
    GitConfig,
    IssueSummary,
    PostPrSupervisorConfig,
    RunnerConfig,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_runner_commit import commit_requested_changes
from backend.core.use_cases.agent_runner_dependencies import (
    format_direct_pr_marker,
    format_fast_merge_marker,
    parse_direct_pr_marker,
    parse_fast_merge_marker,
)
from backend.core.use_cases.agent_runner_feedback import VerificationFailedError
from backend.core.use_cases.agent_runner_final_verification import (
    FinalVerificationRequest,
    ensure_final_verifier_verdict,
)
from backend.core.use_cases.agent_runner_structured_evidence import ValidationEvidenceError
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import write_commit_request

_PRD_ANCHORED_BODY = "# Example\n\n- PRD path: `tasks/pending/P1-FEAT-20260101-000000-example.md`\n"


# ---------------------------------------------------------------------------
# 档位本身：嵌套关系与派生布尔
# ---------------------------------------------------------------------------


def test_publish_stage_skip_scope_is_nested() -> None:
    """直发档的跳过范围严格包含快速通道：跳 reviewer 必跳独立验证。"""
    assert PublishStage.NORMAL.skips_independent_verification is False
    assert PublishStage.NORMAL.skips_review_and_repo_verification is False
    assert PublishStage.FAST.skips_independent_verification is True
    assert PublishStage.FAST.skips_review_and_repo_verification is False
    assert PublishStage.DIRECT.skips_independent_verification is True
    assert PublishStage.DIRECT.skips_review_and_repo_verification is True


def test_agent_request_fast_merge_boolean_is_a_derived_view() -> None:
    """``fast_merge`` 布尔只是档位的派生视图，矛盾组合不可表示。"""
    from backend.core.use_cases.run_agent_execution_loop import AgentExecutionRequest

    def _request(**kwargs: Any) -> Any:
        return AgentExecutionRequest(
            selected_agent="codex",
            issue=IssueSummary(7, "Example", "https://example.test/7", "body", ()),
            worktree_path=Path("/tmp/issue-7"),
            config=AppConfig(),
            process_runner=FakeProcessRunner(),
            before_sha="old",
            expected_branch="issue-7",
            **kwargs,
        )

    legacy = _request(fast_merge=True)
    assert legacy.publish_stage is PublishStage.FAST
    assert legacy.fast_merge is True

    direct = _request(publish_stage=PublishStage.DIRECT)
    assert direct.publish_stage is PublishStage.DIRECT
    assert direct.fast_merge is False

    normal = _request()
    assert normal.publish_stage is PublishStage.NORMAL
    assert normal.fast_merge is False


# ---------------------------------------------------------------------------
# marker 族：格式、解析与跨族负例
# ---------------------------------------------------------------------------


def test_direct_pr_marker_round_trip() -> None:
    """直发档 marker 可被同族解析函数读回 Issue 编号，且不被快速通道解析器认出。"""
    marker = format_direct_pr_marker(12)
    assert marker == "<!-- iar:direct-pr issued=12 -->"
    assert parse_direct_pr_marker(f"body\n\n{marker}\n") == 12
    assert parse_direct_pr_marker(format_fast_merge_marker(12)) is None
    assert parse_fast_merge_marker(marker) is None
    assert parse_direct_pr_marker("<!-- iar:direct-pr -->") is None


# ---------------------------------------------------------------------------
# core 旁路：执行循环（Phase 2 验证命令 + Phase 3.5 证据门 + Phase 4.5）
# ---------------------------------------------------------------------------


def _patch_execution_loop(
    monkeypatch: pytest.MonkeyPatch,
    *,
    committed: list[bool],
    gate_calls: list[str],
) -> None:
    """把执行循环的 agent 调用与三道门禁换成可观测探针。

    探针同时记录 ``verification``（Phase 2 的 runner 验证命令）、``evidence``
    （Phase 3.5 证据门）与 ``rv_reexec`` / ``verifier``（Phase 4.5），直发档要求
    四者全部为空——这正是它与快速通道的差别所在。
    """
    from backend.core.use_cases import run_agent_execution_loop as execution_loop
    from backend.core.use_cases import run_verifier_agent as verifier_module
    from backend.core.use_cases.run_verifier_agent import ValidationVerdict

    def _commit(*args: object, **kwargs: object) -> list[object]:
        committed[0] = True
        return []

    def _verify(*args: object, **kwargs: object) -> ValidationVerdict:
        gate_calls.append("verifier")
        return ValidationVerdict(risk="green")

    def _record(name: str, return_value: Any = None) -> Any:
        def _probe(*args: object, **kwargs: object) -> Any:
            gate_calls.append(name)
            return return_value

        return _probe

    monkeypatch.setattr(execution_loop, "run_agent", lambda *args, **kwargs: None)
    monkeypatch.setattr(execution_loop, "run_verification", _record("verification", []))
    monkeypatch.setattr(
        execution_loop,
        "get_head_sha",
        lambda *args: "new" if committed[0] else "old",
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
    monkeypatch.setattr(execution_loop, "ensure_validation_evidence_ready", _record("evidence"))
    monkeypatch.setattr(execution_loop, "ensure_validation_commands_pass", _record("rv_reexec"))
    monkeypatch.setattr(verifier_module, "run_verifier_gate", _verify)


def _execution_request(
    execution_loop_module: Any,
    tmp_path: Path,
    *,
    publish_stage: PublishStage,
) -> Any:
    """构造一次最小执行请求（builder 恒成功提交一版代码）。"""
    return execution_loop_module.AgentExecutionRequest(
        selected_agent="codex",
        issue=IssueSummary(7, "Example", "https://example.test/7", "body", ()),
        worktree_path=tmp_path,
        config=AppConfig(),
        process_runner=FakeProcessRunner(),
        before_sha="old",
        expected_branch="issue-7",
        publish_stage=publish_stage,
    )


def test_direct_pr_skips_every_runner_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """直发档：验证命令、证据门、rv 复跑、独立 verifier 全部零调用，提交仍发生。"""
    from backend.core.use_cases import run_agent_execution_loop as execution_loop

    committed = [False]
    gate_calls: list[str] = []
    _patch_execution_loop(monkeypatch, committed=committed, gate_calls=gate_calls)

    with caplog.at_level(logging.INFO):
        result = execution_loop.run_agent_until_committed(
            _execution_request(execution_loop, tmp_path, publish_stage=PublishStage.DIRECT)
        )

    assert gate_calls == []
    assert result.verifier_verdict is None
    assert committed == [True]
    skip_lines = [rec.message for rec in caplog.records if "Direct-pr" in rec.message]
    assert any("--direct-pr" in message for message in skip_lines)


def test_fast_merge_still_runs_phase_2_and_evidence_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """负例：快速通道只旁路 Phase 4.5，验证命令与证据门照常——两档跳过范围可区分。"""
    from backend.core.use_cases import run_agent_execution_loop as execution_loop

    committed = [False]
    gate_calls: list[str] = []
    _patch_execution_loop(monkeypatch, committed=committed, gate_calls=gate_calls)

    execution_loop.run_agent_until_committed(
        _execution_request(execution_loop, tmp_path, publish_stage=PublishStage.FAST)
    )

    assert "verification" in gate_calls
    assert "evidence" in gate_calls
    assert "rv_reexec" not in gate_calls
    assert "verifier" not in gate_calls


# ---------------------------------------------------------------------------
# core 旁路：发布前最终复核
# ---------------------------------------------------------------------------


def _final_request(tmp_path: Path, *, publish_stage: PublishStage) -> FinalVerificationRequest:
    return FinalVerificationRequest(
        issue=IssueSummary(7, "Example", "https://example.test/7", "body", ()),
        worktree_path=tmp_path,
        config=AppConfig(),
        process_runner=FakeProcessRunner(),
        selected_agent="codex",
        verified_sha="builder-sha",
        verifier_verdict=None,
        publish_stage=publish_stage,
    )


def test_final_verification_bypassed_under_direct_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.core.use_cases import agent_runner_final_verification as final_gate

    monkeypatch.setattr(final_gate, "has_changes", lambda *args: False)
    monkeypatch.setattr(final_gate, "get_head_sha", lambda *args: "review-sha")
    monkeypatch.setattr(
        final_gate,
        "ensure_validation_evidence_ready",
        lambda *args: pytest.fail("direct-pr must not re-run the evidence gate"),
    )
    monkeypatch.setattr(
        final_gate,
        "ensure_validation_commands_pass",
        lambda *args: pytest.fail("direct-pr must not re-run the rv commands"),
    )
    monkeypatch.setattr(
        final_gate,
        "run_verifier_gate",
        lambda *args, **kwargs: pytest.fail("direct-pr must not run the verifier"),
    )

    assert (
        ensure_final_verifier_verdict(_final_request(tmp_path, publish_stage=PublishStage.DIRECT))
        is None
    )


def test_direct_pr_clean_tree_precondition_still_enforced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """负例：旁路的是验证门禁，"已提交干净工作树"的发布前提不放松。"""
    from backend.core.use_cases import agent_runner_final_verification as final_gate

    monkeypatch.setattr(final_gate, "has_changes", lambda *args: True)
    with pytest.raises(ValidationEvidenceError, match="clean committed worktree"):
        ensure_final_verifier_verdict(_final_request(tmp_path, publish_stage=PublishStage.DIRECT))


# ---------------------------------------------------------------------------
# commit proxy：直发档仍提交，但不跑仓库验证命令
# ---------------------------------------------------------------------------


def _commit_worktree(tmp_path: Path) -> Path:
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir(parents=True)
    write_commit_request(worktree_path, "agent: implement example")
    return worktree_path


def _commit_runner() -> FakeProcessRunner:
    return FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                ("git", "branch", "--show-current"), 0, "issue-123\n", ""
            ),
            ("git", "status", "--porcelain"): CommandResult(
                ("git", "status", "--porcelain"), 0, " M src/example.py\n", ""
            ),
            ("ruff", "check"): CommandResult(
                ("ruff", "check"), 1, "src/example.py:1:1: E501 Line too long\n", ""
            ),
        }
    )


_FAILING_COMMIT_CONFIG = AppConfig(
    runner=RunnerConfig(
        verification_commands=("ruff check",),
        pre_commit_verification_command="pre-commit run --all-files",
    )
)


def test_direct_pr_commit_proxy_skips_verification_but_still_commits(tmp_path: Path) -> None:
    """验证命令恒红时直发档照常提交（门禁转移到 CI），负例对照默认档打回。"""
    with pytest.raises(VerificationFailedError):
        commit_requested_changes(
            IssueSummary(123, "Example", "https://example.test/123", "body", ()),
            _commit_worktree(tmp_path / "normal"),
            _FAILING_COMMIT_CONFIG,
            _commit_runner(),
            expected_branch="issue-123",
            publish_stage=PublishStage.NORMAL,
        )

    direct_runner = _commit_runner()
    assert (
        commit_requested_changes(
            IssueSummary(123, "Example", "https://example.test/123", "body", ()),
            _commit_worktree(tmp_path / "direct"),
            _FAILING_COMMIT_CONFIG,
            direct_runner,
            expected_branch="issue-123",
            publish_stage=PublishStage.DIRECT,
        )
        == []
    )
    assert ["ruff", "check"] not in direct_runner.calls
    assert not any(call[:1] == ["pre-commit"] for call in direct_runner.calls)
    assert any(call[:2] == ["git", "commit"] for call in direct_runner.calls)


def test_direct_pr_commit_proxy_keeps_branch_guard(tmp_path: Path) -> None:
    """负例：跳的是验证，不是安全门——分支不符仍拒绝提交。"""
    with pytest.raises(RuntimeError, match="unexpected branch"):
        commit_requested_changes(
            IssueSummary(123, "Example", "https://example.test/123", "body", ()),
            _commit_worktree(tmp_path / "direct"),
            _FAILING_COMMIT_CONFIG,
            _commit_runner(),
            expected_branch="issue-999",
            publish_stage=PublishStage.DIRECT,
        )


# ---------------------------------------------------------------------------
# 发布编排：直发档不启动第二个 agent
# ---------------------------------------------------------------------------


def _publish_with_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: PublishStage
) -> tuple[str, int]:
    """经真实发布编排链路创建 Draft PR，返回 ``(PR 正文, reviewer agent 调用次数)``。"""
    from backend.core.use_cases import agent_runner_publication as publication

    review_call_count = 0

    def _review(**kwargs: object) -> tuple[str, list[object]]:
        nonlocal review_call_count
        review_call_count += 1
        return ("new-sha", [])

    monkeypatch.setattr(publication, "run_pre_pr_review", _review)
    monkeypatch.setattr(publication, "get_head_sha", lambda *args: "new-sha")
    monkeypatch.setattr(publication, "ensure_final_verifier_verdict", lambda request: None)

    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                ("git", "branch", "--show-current"), 0, "issue-7\n", ""
            )
        }
    )
    request = publication._PublicationReviewRequest(
        verification_request=FinalVerificationRequest(
            issue=IssueSummary(7, "Example", "https://example.test/7", "body", ()),
            worktree_path=tmp_path,
            config=AppConfig(
                git=GitConfig(remote="origin", base_branch="main"),
                generated_content=GeneratedContentConfig(enabled=False),
            ),
            process_runner=fake_runner,
            selected_agent="codex",
            verified_sha="new-sha",
            verifier_verdict=None,
            publish_stage=stage,
        ),
        github_client=fake_client,
        expected_branch="issue-7",
        verification_results=[],
        push_callback=lambda: None,
        content_generator=None,
    )
    publication._review_verify_create_pr(request)

    pr_calls = [c for c in fake_client.calls if c["method"] == "create_draft_pr"]
    assert len(pr_calls) == 1
    return str(pr_calls[0]["body"]), review_call_count


def test_direct_pr_skips_review_agent_and_marks_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """直发档：reviewer agent 零调用，PR 正文带 direct marker 且无 fast-merge marker。"""
    pr_body, review_call_count = _publish_with_stage(monkeypatch, tmp_path, PublishStage.DIRECT)

    assert review_call_count == 0
    assert parse_direct_pr_marker(pr_body) == 7
    assert parse_fast_merge_marker(pr_body) is None


def test_fast_merge_still_runs_review_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """负例：快速通道仍跑 pre-PR review（reviewer 调用一次），标注仍是 fast-merge 族。"""
    pr_body, review_call_count = _publish_with_stage(monkeypatch, tmp_path, PublishStage.FAST)

    assert review_call_count == 1
    assert parse_fast_merge_marker(pr_body) == 7
    assert parse_direct_pr_marker(pr_body) is None


def test_normal_pr_body_carries_no_stage_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """负例：默认档正文既无 fast-merge 也无 direct-pr 标注。"""
    pr_body, review_call_count = _publish_with_stage(monkeypatch, tmp_path, PublishStage.NORMAL)

    assert review_call_count == 1
    assert "iar:fast-merge" not in pr_body
    assert "iar:direct-pr" not in pr_body


# ---------------------------------------------------------------------------
# PR 后监督：直发档不进监督循环
# ---------------------------------------------------------------------------


def _supervisor_config(*, enabled: bool) -> AppConfig:
    return AppConfig(post_pr_supervisor=PostPrSupervisorConfig(enabled=enabled))


def test_direct_pr_skips_post_pr_supervisor() -> None:
    """直发档不进 PR 后监督（那是 Draft PR 之后的又一次 agent 调用）。"""
    from backend.core.use_cases.agent_runner_publication import _should_run_post_pr_supervisor

    enabled_config = _supervisor_config(enabled=True)
    assert _should_run_post_pr_supervisor(enabled_config, PublishStage.DIRECT, 7) is False
    assert _should_run_post_pr_supervisor(enabled_config, PublishStage.FAST, 7) is True
    assert _should_run_post_pr_supervisor(enabled_config, PublishStage.NORMAL, 7) is True

    disabled_config = _supervisor_config(enabled=False)
    assert _should_run_post_pr_supervisor(disabled_config, PublishStage.NORMAL, 7) is False


# ---------------------------------------------------------------------------
# api 表面：旗标解析、互斥、仅限无 PRD 锚点（fail-closed）
# ---------------------------------------------------------------------------


def test_parser_accepts_direct_pr_flag() -> None:
    """argparse facade 解析 --direct-pr；缺省 False（默认行为零变化）。"""
    from backend.api.cli_parser import build_parser

    parsed = build_parser().parse_args(["run", "--issue", "5", "--direct-pr"])
    assert parsed.direct_pr is True
    assert parsed.fast_merge is False
    assert build_parser().parse_args(["run", "--issue", "5"]).direct_pr is False


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
        "fast_merge": False,
        "direct_pr": False,
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


def test_direct_pr_and_fast_merge_are_mutually_exclusive() -> None:
    """两个旗标同时给出 → 用法错误，不静默取更强者。"""
    from backend.api.cli_parsed_commands.runner import run_run_command

    with pytest.raises(CliError) as error:
        run_run_command(_run_context(issue=5, fast_merge=True, direct_pr=True))
    assert error.value.code == ExitCode.USAGE
    assert "--fast-merge" in str(error.value) and "--direct-pr" in str(error.value)


def test_direct_pr_rejected_with_all_ready() -> None:
    """直发档只对单一目标定义；--all-ready 组合在触达仓库前即用法错误。"""
    from backend.api.cli_parsed_commands.runner import run_run_command

    with pytest.raises(CliError) as error:
        run_run_command(_run_context(direct_pr=True, all_ready=True))
    assert error.value.code == ExitCode.USAGE
    assert "--all-ready" in str(error.value)


def test_publish_stage_resolution_matrix() -> None:
    """旗标 → 档位的折算：无旗标 normal，单旗标对应档位。"""
    from backend.api.cli_parsed_commands.runner import _resolve_publish_stage

    assert _resolve_publish_stage(fast_merge=False, direct_pr=False) is PublishStage.NORMAL
    assert _resolve_publish_stage(fast_merge=True, direct_pr=False) is PublishStage.FAST
    assert _resolve_publish_stage(fast_merge=False, direct_pr=True) is PublishStage.DIRECT


def _gate_context(fake_client: FakeGitHubClient) -> Any:
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


def _client_returning(body: str) -> FakeGitHubClient:
    client = FakeGitHubClient()
    client.get_issue = lambda number: IssueSummary(
        number, "Example", f"https://example.test/{number}", body, ()
    )
    return client


def test_direct_pr_rejected_on_prd_anchored_issue(tmp_path: Path) -> None:
    """带 PRD 锚点的 Issue 用 --direct-pr → 拒绝，且错误信息指向 --fast-merge。"""
    from backend.api.cli_parsed_commands.runner import _reject_direct_pr_on_prd_backed_issue

    with pytest.raises(CliError) as error:
        _reject_direct_pr_on_prd_backed_issue(
            _gate_context(_client_returning(_PRD_ANCHORED_BODY)),
            contexts=[SimpleNamespace(repo_path=tmp_path)],
            target_issue=7,
        )
    assert error.value.code == ExitCode.USAGE
    assert "--fast-merge" in str(error.value.suggestion or "")

    _reject_direct_pr_on_prd_backed_issue(
        _gate_context(_client_returning("# Example 无 PRD 锚点")),
        contexts=[SimpleNamespace(repo_path=tmp_path)],
        target_issue=7,
    )


def test_direct_pr_gate_is_fail_closed(tmp_path: Path) -> None:
    """负例：读不到 Issue 就无法证明它无 PRD 锚点，一律拒绝放行。"""
    from backend.api.cli_parsed_commands.runner import _reject_direct_pr_on_prd_backed_issue

    broken_client = FakeGitHubClient()

    def _raise(_number: int) -> IssueSummary:
        raise RuntimeError("gh unavailable")

    broken_client.get_issue = _raise
    with pytest.raises(CliError) as error:
        _reject_direct_pr_on_prd_backed_issue(
            _gate_context(broken_client),
            contexts=[SimpleNamespace(repo_path=tmp_path)],
            target_issue=7,
        )
    assert error.value.code == ExitCode.USAGE

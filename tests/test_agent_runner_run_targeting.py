"""``iar run`` 目标必填 / 互斥接管 与 ``iar daemon`` autopilot 覆盖的测试。

对应 PRD：run-daemon-autopilot-control-surface（FR-1/2/4/5/7/8）。
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.api.cli_exit_codes import ExitCode
from backend.core.shared.models.agent_runner import (
    AppConfig,
    AutopilotConfig,
    IssueSummary,
    RepositoryRunContext,
)
from backend.core.use_cases.console_processes import (
    ConsoleProcessError,
    RunnerProcessKind,
    build_runner_argv,
)
from backend.core.use_cases.run_target_resolve import (
    RunTargetResolveError,
    resolve_prd_target_issue_number,
)
from tests.conftest import FakeGitHubClient

REPO_ID = "keda-test"


# ─────────────────────────────────────────────────────────────────────────────
# 目标解析：PRD 路径 → 回链 Issue
# ─────────────────────────────────────────────────────────────────────────────


def _write_prd(repo_path: Path, relative_path: str, *, issue_number: int | None) -> Path:
    prd_path = repo_path / relative_path
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    issue_line = (
        f"- GitHub Issue: https://github.com/example/repo/issues/{issue_number}"
        if issue_number is not None
        else "- GitHub Issue: (to be created)"
    )
    prd_path.write_text(f"# PRD\n\n{issue_line}\n", encoding="utf-8")
    return prd_path


def test_resolve_prd_target_issue_number_returns_linked_issue(tmp_path: Path) -> None:
    """PRD 带 ``- GitHub Issue: .../issues/N`` 回链时解析出 N。"""
    prd_path = _write_prd(tmp_path, "tasks/pending/a.md", issue_number=7)
    assert resolve_prd_target_issue_number(repo_path=tmp_path, prd_path=prd_path) == 7


def test_resolve_prd_target_issue_number_rejects_missing_link(tmp_path: Path) -> None:
    """PRD 无回链时报错并提示先 ``iar issue create``。"""
    prd_path = _write_prd(tmp_path, "tasks/pending/b.md", issue_number=None)
    with pytest.raises(RunTargetResolveError) as excinfo:
        resolve_prd_target_issue_number(repo_path=tmp_path, prd_path=prd_path)
    assert "iar issue create" in str(excinfo.value)


def test_resolve_prd_target_issue_number_rejects_missing_file(tmp_path: Path) -> None:
    """PRD 文件不存在时报错。"""
    with pytest.raises(RunTargetResolveError):
        resolve_prd_target_issue_number(repo_path=tmp_path, prd_path="tasks/pending/none.md")


# ─────────────────────────────────────────────────────────────────────────────
# CLI 解析：新旗标
# ─────────────────────────────────────────────────────────────────────────────


def test_argparse_run_accepts_target_flags() -> None:
    """argparse ``run`` 接受 --issue / PRD 路径 / --all-ready / --takeover / --yes。"""
    from backend.api.cli_parser import build_parser

    parsed = build_parser().parse_args(
        ["run", "tasks/pending/a.md", "--issue", "3", "--all-ready", "--takeover", "--yes"]
    )
    assert parsed.prd_path == "tasks/pending/a.md"
    assert parsed.issue == 3
    assert parsed.all_ready is True
    assert parsed.takeover is True
    assert parsed.yes is True


def test_argparse_run_target_flags_default_off() -> None:
    """argparse ``run`` 新旗标缺省值。"""
    from backend.api.cli_parser import build_parser

    parsed = build_parser().parse_args(["run"])
    assert parsed.prd_path is None
    assert parsed.issue is None
    assert parsed.all_ready is False
    assert parsed.takeover is False
    assert parsed.yes is False


def test_argparse_daemon_autopilot_override_tristate() -> None:
    """argparse ``daemon`` 的 --autopilot/--no-autopilot 三态（缺省 None）。"""
    from backend.api.cli_parser import build_parser

    assert build_parser().parse_args(["daemon"]).autopilot_override is None
    assert build_parser().parse_args(["daemon", "--autopilot"]).autopilot_override is True
    assert build_parser().parse_args(["daemon", "--no-autopilot"]).autopilot_override is False


def test_typer_run_help_has_no_autopilot() -> None:
    """``iar run --help`` 有目标旗标但绝不出现 --autopilot（rv-4）。

    Rich 在窄终端（CI 80 列）会对旗标做 ANSI 着色与折行，子串断言前先
    去除 ANSI 转义与全部空白，保证断言与终端宽度无关。
    """
    import re

    from typer.testing import CliRunner

    from backend.api.cli_typer_app import app

    result = CliRunner().invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")
    compact_help = re.sub(r"\s+", "", ansi_pattern.sub("", result.output))
    assert "--issue" in compact_help
    assert "--all-ready" in compact_help
    assert "--takeover" in compact_help
    assert "--autopilot" not in compact_help


# ─────────────────────────────────────────────────────────────────────────────
# dispatch：目标必填 / 互斥 / 接管
# ─────────────────────────────────────────────────────────────────────────────


def _mock_run_context(repo_path: Path) -> MagicMock:
    context = MagicMock()
    context.repo_path = repo_path
    context.repo_id = REPO_ID
    context.display_name = "Keda Test"
    context.config = AppConfig()
    return context


@contextlib.contextmanager
def _run_patches():
    # Dispatch 层 run 测试的公共打桩（gh 客户端 / 初始化检查）。
    with (
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
    ):
        yield


def test_main_run_without_target_is_usage_error() -> None:
    """``iar run`` 无目标无 --all-ready → 用法错误退出码 2（rv-5）。"""
    from backend.api.cli import main

    exit_code = main(["run"])
    assert exit_code == int(ExitCode.USAGE)


def test_main_run_rejects_issue_and_prd_path_together(tmp_path: Path) -> None:
    """``--issue`` 与 PRD 路径互斥。"""
    from backend.api.cli import main

    exit_code = main(["run", "tasks/pending/a.md", "--issue", "3"])
    assert exit_code == int(ExitCode.USAGE)


def test_main_run_with_daemon_running_rejects_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同仓 daemon 在跑时默认拒绝（rv-2），提示 --takeover。"""
    from backend.api.cli import main
    from backend.api.cli_exit_codes import ExitCode as Code

    monkeypatch.setenv("IAR_SKIP_GH_AUTH_CHECK", "1")
    context = _mock_run_context(tmp_path)
    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.core.use_cases.daemon_single_instance.find_live_daemon_pid",
            return_value=4242,
        ),
        _run_patches(),
    ):
        exit_code = main(["run", "--all-ready"])

    assert exit_code == int(Code.CONFLICT)


def test_main_run_takeover_yes_stops_daemon_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--takeover --yes`` 停 daemon 并 reclaim 后执行定向 run（rv-6）。"""
    from backend.api.cli import main
    from backend.api.cli_run_takeover import DaemonTakeoverResult

    monkeypatch.setenv("IAR_SKIP_GH_AUTH_CHECK", "1")
    context = _mock_run_context(tmp_path)
    captured: dict = {}

    def fake_take_over(**kwargs):
        captured.update(kwargs)
        return DaemonTakeoverResult(
            repo_id=kwargs["repo_id"],
            daemon_pid=kwargs["daemon_pid"],
            managed=False,
            final_signal="sigterm",
            reclaimed_issues=(5,),
        )

    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.core.use_cases.daemon_single_instance.find_live_daemon_pid",
            return_value=4242,
        ),
        patch(
            "backend.api.cli_run_takeover.take_over_daemon",
            side_effect=fake_take_over,
        ),
        patch("backend.api.cli.run_agent_repositories_once", return_value=0) as mock_run,
        _run_patches(),
    ):
        exit_code = main(["run", "--issue", "7", "--takeover", "--yes"])

    assert exit_code == 0
    assert captured["daemon_pid"] == 4242
    assert captured["repo_id"] == REPO_ID
    assert mock_run.call_args.kwargs["target_issue"] == 7


def test_main_run_takeover_declined_leaves_daemon_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接管确认被拒绝时不动作、不停 daemon（rv-6 negative control）。"""
    from backend.api.cli import main

    monkeypatch.setenv("IAR_SKIP_GH_AUTH_CHECK", "1")
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    context = _mock_run_context(tmp_path)
    with (
        patch(
            "backend.api.cli_helpers.resolve_repository_targets",
            return_value=[context],
        ),
        patch(
            "backend.core.use_cases.daemon_single_instance.find_live_daemon_pid",
            return_value=4242,
        ),
        patch(
            "backend.api.cli_run_takeover.take_over_daemon",
            side_effect=AssertionError("must not be called"),
        ),
        patch("rich.prompt.Confirm.ask", return_value=False),
        _run_patches(),
    ):
        exit_code = main(["run", "--issue", "7", "--takeover"])

    assert exit_code == int(ExitCode.USAGE)


# ─────────────────────────────────────────────────────────────────────────────
# 定向收窄：run_once 的 target_issue
# ─────────────────────────────────────────────────────────────────────────────


class _TargetedGitHubClient(FakeGitHubClient):
    """带固定 ready / running / blocked 候选的 Fake 客户端。"""

    def __init__(self, issue: IssueSummary) -> None:
        super().__init__()
        self._issue = issue

    def get_issue(self, issue_number: int) -> IssueSummary:
        self.calls.append({"method": "get_issue", "issue_number": issue_number})
        return self._issue

    def list_ready_issues(self, ready_label: str, limit: int) -> list[IssueSummary]:
        self.calls.append({"method": "list_ready_issues", "ready_label": ready_label})
        return []

    def list_review_candidate_issues(self, labels: list[str], limit: int) -> list[IssueSummary]:
        self.calls.append({"method": "list_review_candidate_issues", "labels": list(labels)})
        return []


def _issue_summary(number: int, labels: tuple[str, ...]) -> IssueSummary:
    return IssueSummary(
        number=number,
        title=f"Issue {number}",
        url=f"https://github.com/example/repo/issues/{number}",
        body="",
        labels=labels,
    )


class _QueueGitHubClient(_TargetedGitHubClient):
    """队列轮询模式（无 target）用的 Fake：ready 列表返回固定候选。"""

    def __init__(self, issues: tuple[IssueSummary, ...]) -> None:
        super().__init__(issues[0])
        self._issues = issues

    def list_ready_issues(self, ready_label: str, limit: int) -> list[IssueSummary]:
        self.calls.append(
            {"method": "list_ready_issues", "ready_label": ready_label, "limit": limit}
        )
        return list(self._issues)


def test_run_once_targeted_with_durable_state_label_skips_ready_discovery(tmp_path: Path) -> None:
    """定向到带 ``agent/running`` 的目标不进 ready 候选通道，也不触发 ready 全量扫描。

    显式定向的放宽只覆盖「无状态 / 仅就绪」两类；已带 durable 状态标签的目标按
    原通道处理（这里是 running 恢复通道），不被当作新 ready 任务重跑。
    """
    from backend.core.use_cases.agent_runner_orchestrate import run_once

    config = AppConfig()
    client = _TargetedGitHubClient(_issue_summary(7, (config.labels.running,)))
    exit_code = run_once(
        repo_path=tmp_path,
        config=config,
        dry_run=True,
        agent="auto",
        max_issues=1,
        github_client=client,
        process_runner=MagicMock(),
        target_issue=7,
    )
    assert exit_code == 0
    # 走了定向路径：get_issue 被调用，且未触发全量 ready 扫描。
    assert {"method": "get_issue", "issue_number": 7} in client.calls
    assert not [call for call in client.calls if call["method"] == "list_ready_issues"]


def test_run_once_claim_arbitration_lost_skips_without_marking_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """领取仲裁落败的 Issue 被安静跳过（返回 0），绝不标 failed、不改任何标签。

    回归护点：``_process_single_issue`` 的 ``except ClaimArbitrationLost`` 处理器
    排在兜底 ``except Exception``（会标 failed）之前——输掉竞争不是执行失败，
    把赢家的 ``agent/running`` 覆盖成 ``agent/failed`` 会让该 Issue 被永久卡死。
    """
    import backend.core.use_cases.agent_runner_issue_handlers as issue_handlers
    import backend.core.use_cases.agent_runner_orchestration_runtime as runtime
    import backend.core.use_cases.run_agent_once as run_agent_once
    from backend.core.use_cases.agent_runner_claim_arbitration import ClaimArbitrationLost
    from backend.core.use_cases.agent_runner_orchestrate import run_once

    def _lose_arbitration(**_kwargs: object) -> object:
        raise ClaimArbitrationLost("Issue #7 was claimed earlier by another runner")

    monkeypatch.setattr(run_agent_once, "run_preflight_checks", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "process_validation_gate", lambda **k: None)
    monkeypatch.setattr(issue_handlers, "arbitrate_first_claim", _lose_arbitration)

    config = AppConfig()
    client = _TargetedGitHubClient(_issue_summary(7, ()))
    exit_code = run_once(
        repo_path=tmp_path,
        config=config,
        dry_run=False,
        agent="auto",
        max_issues=1,
        github_client=client,
        process_runner=MagicMock(),
        target_issue=7,
    )

    assert exit_code == 0
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]
    assert not [call for call in client.calls if call["method"] == "comment_issue"]


def test_run_once_targeted_dry_run_does_not_crash_on_missing_discovery_limit(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """定向 + --dry-run 必须能预览：发现宽度变量在定向分支里也要有绑定。

    回归：DRY RUN 汇总日志引用 ``ready_discovery_limit``，而它原先只在队列轮询
    分支赋值，导致 ``iar run --issue N --dry-run``（文档推荐的预览入口）在目标
    被准入时直接崩溃。
    """
    from backend.core.use_cases.agent_runner_orchestrate import run_once

    config = AppConfig()
    client = _TargetedGitHubClient(_issue_summary(7, ()))
    with caplog.at_level(logging.INFO):
        exit_code = run_once(
            repo_path=tmp_path,
            config=config,
            dry_run=True,
            agent="auto",
            max_issues=1,
            github_client=client,
            process_runner=MagicMock(),
            target_issue=7,
        )
    assert exit_code == 0
    preview_lines = [r.message for r in caplog.records if "DRY RUN" in r.getMessage()]
    assert any("Issue #7" in line for line in preview_lines)
    assert not any("candidates returned by GitHub" in line for line in preview_lines)


def test_run_once_queue_mode_dry_run_reports_discovery_limit(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """守护进程侧（无 target）的 DRY RUN 措辞保持不变。"""
    from backend.core.use_cases.agent_runner_orchestrate import run_once

    config = AppConfig()
    client = _QueueGitHubClient((_issue_summary(8, (config.labels.ready,)),))
    with caplog.at_level(logging.INFO):
        run_once(
            repo_path=tmp_path,
            config=config,
            dry_run=True,
            agent="auto",
            max_issues=1,
            github_client=client,
            process_runner=MagicMock(),
        )
    assert any("candidates returned by GitHub" in r.getMessage() for r in caplog.records)


def test_run_once_targeted_passes_target_to_request(tmp_path: Path) -> None:
    """orchestrate.run_once 把 target_issue 传进 RunOnceRequest。"""
    import backend.core.use_cases.agent_runner_orchestrate as orchestrate_module
    from backend.core.use_cases.agent_runner_orchestrate import run_once

    captured: dict = {}

    class _FakeRuntimeModule:
        def run_once(self, request):
            captured["target_issue"] = request.target_issue
            return 0

        class RunOnceRequest:  # 与真实同名 dataclass 结构兼容即可
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

    with patch.object(
        orchestrate_module, "_orchestration_runtime_module", return_value=_FakeRuntimeModule()
    ):
        run_once(
            repo_path=tmp_path,
            config=AppConfig(),
            dry_run=True,
            agent="auto",
            max_issues=1,
            github_client=MagicMock(),
            process_runner=MagicMock(),
            target_issue=9,
        )
    assert captured["target_issue"] == 9


# ─────────────────────────────────────────────────────────────────────────────
# daemon autopilot 覆盖（FR-8 / rv-8）
# ─────────────────────────────────────────────────────────────────────────────


class _RecordingBacklogStore:
    """记录 advance 是否被调用的最小 IBacklogStore 替身。"""

    def __init__(self) -> None:
        self.advance_calls = 0


def _run_one_daemon_pass(
    monkeypatch: pytest.MonkeyPatch,
    *,
    context: RepositoryRunContext,
    store: _RecordingBacklogStore,
    autopilot_override: bool | None = None,
) -> None:
    """跑恰好一轮 daemon（所有 phase 打桩），验证调度门控。"""
    import backend.core.use_cases.run_agent_daemon as daemon_module
    from backend.core.use_cases.run_agent_daemon import run_agent_daemon

    scheduled: dict = {}

    def fake_advance(**kwargs):
        scheduled["ran"] = True
        raise AssertionError("real advance must not run")

    monkeypatch.setattr(daemon_module, "process_prd_rework_issues", lambda **kwargs: None)
    monkeypatch.setattr(daemon_module, "run_once", lambda **kwargs: None)

    real_advance = daemon_module.advance_backlog_queue

    def advance_recorder(**kwargs):
        store.advance_calls += 1
        return real_advance(**kwargs)

    monkeypatch.setattr(daemon_module, "advance_backlog_queue", advance_recorder)

    def stop_after_one_pass(*_args, **_kwargs):
        if store.advance_calls and not scheduled:
            scheduled["ran"] = True
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon_module.time, "sleep", stop_after_one_pass)

    with pytest.raises(KeyboardInterrupt):
        run_agent_daemon(
            contexts=[context],
            interval=0,
            agent="auto",
            max_issues=1,
            process_runner=MagicMock(),
            github_client_factory=lambda repo_path: MagicMock(),
            backlog_store_factory=lambda: store,
            autopilot_override=autopilot_override,
        )


def test_daemon_autopilot_flag_enables_scheduling_when_config_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置 autopilot.enabled=false + --autopilot → 调度阶段运行（rv-8）。"""
    config = replace(AppConfig(), autopilot=AutopilotConfig(enabled=False))
    context = RepositoryRunContext(
        repo_id=REPO_ID, display_name="Keda Test", repo_path=tmp_path, config=config
    )
    store = _RecordingBacklogStore()
    _run_one_daemon_pass(monkeypatch, context=context, store=store, autopilot_override=True)
    assert store.advance_calls == 1


def test_daemon_no_autopilot_flag_disables_scheduling_when_config_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置 autopilot.enabled=true + --no-autopilot → 调度阶段关闭（rv-8）。"""
    config = replace(AppConfig(), autopilot=AutopilotConfig(enabled=True))
    context = RepositoryRunContext(
        repo_id=REPO_ID, display_name="Keda Test", repo_path=tmp_path, config=config
    )
    store = _RecordingBacklogStore()
    _run_one_daemon_pass(monkeypatch, context=context, store=store, autopilot_override=False)
    assert store.advance_calls == 0


def test_daemon_without_flag_follows_config_each_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不传旗标 → 跟随配置（enabled=false → 不调度）（rv-8）。"""
    config = replace(AppConfig(), autopilot=AutopilotConfig(enabled=False))
    context = RepositoryRunContext(
        repo_id=REPO_ID, display_name="Keda Test", repo_path=tmp_path, config=config
    )

    store = _RecordingBacklogStore()
    _run_one_daemon_pass(monkeypatch, context=context, store=store)
    assert store.advance_calls == 0


# ─────────────────────────────────────────────────────────────────────────────
# Console spawn 路径迁移（FR-7）
# ─────────────────────────────────────────────────────────────────────────────


def test_build_runner_argv_run_once_with_issue_targets_issue() -> None:
    """RUN_ONCE 带 issue_number → ``iar run --issue N``（Console 开始此 PRD）。"""
    argv = build_runner_argv(
        runner_command=["uv", "run", "iar"],
        kind=RunnerProcessKind.RUN_ONCE,
        repo_id=REPO_ID,
        issue_number=11,
    )
    assert argv == ("uv", "run", "iar", "run", "--issue", "11", "--repo-id", REPO_ID)


def test_build_runner_argv_run_once_without_issue_is_all_ready() -> None:
    """RUN_ONCE 不带 issue_number → 显式 ``--all-ready``（等价旧 iar run）。"""
    argv = build_runner_argv(
        runner_command=["uv", "run", "iar"],
        kind=RunnerProcessKind.RUN_ONCE,
        repo_id=REPO_ID,
    )
    assert argv == ("uv", "run", "iar", "run", "--all-ready", "--repo-id", REPO_ID)


def test_build_runner_argv_rejects_non_positive_issue_for_run_once() -> None:
    """RUN_ONCE 传非法 issue_number 与 BLOCKED_CONTINUE 一致拒绝。"""
    with pytest.raises(ConsoleProcessError):
        build_runner_argv(
            runner_command=["iar"],
            kind=RunnerProcessKind.RUN_ONCE,
            repo_id=REPO_ID,
            issue_number=0,
        )

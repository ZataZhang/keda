"""Agent 调用观测的端到端链路测试（Issue #242）。

被测边界全部是真的：真实子进程（假 agent CLI 脚本，经 ``uv run python``
拉起）→ 真实 ``SubprocessRunner`` / claude stream-json 协议 →
真实 ``run_agent_with_prompt`` 进程边界 → 真实 ``SqliteConsoleStore`` 账本 →
真实 per-Issue 日志文件。被替换的只有 agent CLI 本身与 GitHub 侧。

对应 PRD Realistic Validation 的 rv-1（起止标记 + 身份 + 请求/自报模型）、
rv-2（重试/回退是独立 invocation 并以 ``retry_of`` 关联）、rv-3（失败与超时
也记终态）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.core.shared.interfaces.runner_console import InvocationEventRecord
from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import AppConfig, RunnerConfig
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_RUN,
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
    PLAIN_PROTOCOL_ID,
    AgentProfileSpec,
    AgentSpec,
    BUILTIN_AGENT_SPECS,
)
from backend.core.use_cases.agent_invocation_tracing import (
    EVENT_INVOCATION_FINISHED,
    EVENT_INVOCATION_STARTED,
    FAILURE_TIMEOUT,
    INVOCATION_END_MARKER,
    INVOCATION_START_MARKER,
    MODEL_NOT_REQUESTED,
    MODEL_SOURCE_EXECUTOR_REPORT,
    MODEL_SOURCE_UNKNOWN,
    MODEL_UNREPORTED,
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    PHASE_IMPLEMENTATION,
    PHASE_REVIEW,
    RETRY_REASON_TRANSIENT,
    bound_invocation_trace_context,
    build_invocation_trace_context,
    resolve_invocation_log_path,
)
from backend.core.use_cases.agent_runner_output_routing import (
    _OutputRoutedProcessRunner,
    issue_output_routing,
)
from backend.core.use_cases.run_agent_once import run_agent_with_prompt_resilient
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from backend.infrastructure.process_runner import SubprocessRunner

_REPO_ID = "keda-main"
_ISSUE_NUMBER = 242
#: 请求下发的模型（预设绑定）——与执行器自报值刻意不同，钉住"两者不互相回填"。
_REQUESTED_MODEL = "claude-sonnet-4-5"
#: 执行器 stdout 自报的实际模型。
_REPORTED_MODEL = "claude-sonnet-4-5-20250929"

_STREAM_EVENTS: tuple[dict[str, object], ...] = (
    {"type": "system", "subtype": "init", "session_id": "sess-abc-123", "model": _REPORTED_MODEL},
    {
        "type": "result",
        "result": "done",
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 340,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 120,
        },
    },
)


def _write_fake_agent_script(worktree: Path, *, name: str, body: str) -> str:
    """在 worktree 里落一个真实可执行的假 agent 脚本，返回其路径。"""
    script = worktree / f"fake_agent_{name}.py"
    script.write_text(body, encoding="utf-8")
    return str(script)


def _success_script_body() -> str:
    """一次干净成功：按 claude stream-json 信封自报会话 id、模型与用量。"""
    lines = "".join(
        f"sys.stdout.write(json.dumps({event!r}) + '\\n')\n" for event in _STREAM_EVENTS
    )
    return "import json, sys\n" + lines


def _transient_then_success_script_body(counter_path: str) -> str:
    """第一次以瞬态网络错误收场，第二次干净成功（用计数器文件区分）。"""
    lines = "".join(
        f"sys.stdout.write(json.dumps({event!r}) + '\\n')\n" for event in _STREAM_EVENTS
    )
    return (
        "import json, os, sys\n"
        f"counter = {counter_path!r}\n"
        "seen = int(open(counter, encoding='utf-8').read()) if os.path.exists(counter) else 0\n"
        "open(counter, 'w', encoding='utf-8').write(str(seen + 1))\n"
        "if seen == 0:\n"
        "    sys.stderr.write('upstream error: connection reset by peer\\n')\n"
        "    raise SystemExit(1)\n" + lines
    )


def _timeout_script_body() -> str:
    """一直不产出、也不退出：用于触发墙钟超时。"""
    return "import time\ntime.sleep(30)\n"


def _silent_script_body() -> str:
    """成功但**不**自报模型：用于验证"未提供"不被配置值回填。"""
    return (
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'type': 'result', 'result': 'done'}) + '\\n')\n"
    )


def _config_with_fake_agent(script_path: str, *, output_protocol: str) -> AppConfig:
    """把内置 claude 的 run profile 指向假脚本。

    ``model_args`` / ``reasoning_effort_args`` 必须声明：模型绑定命中未声明模板的
    agent 时 ``build_agent_invocation`` 会 fail-fast，那样就测不到"请求模型"这条
    事实了。
    """
    claude_spec = BUILTIN_AGENT_SPECS["claude"]
    fake_spec = AgentSpec(
        bin="uv",
        label=claude_spec.label,
        label_color=claude_spec.label_color,
        label_description=claude_spec.label_description,
        model_args=("--model", "{model}"),
        reasoning_effort_args=("--reasoning-effort", "{effort}"),
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=("run", "python", script_path),
                prompt_delivery="argv_tail",
                output_protocol=output_protocol,
            )
        },
    )
    return AppConfig(
        runner=RunnerConfig(max_recovery_attempts=0, transient_retry_delay_seconds=0),
        agents={**BUILTIN_AGENT_SPECS, "claude": fake_spec},
    )


def _git_worktree(tmp_path: Path) -> Path:
    """建一个真 git 仓库当 worktree（会话记录落盘与 SHA 探测都走真路径）。"""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True, capture_output=True)
    (worktree / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=worktree, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    return worktree


class _InvocationRunHarness:
    """一次"真 Issue run"的最小装配：真路由 + 真账本 + 真进程边界。"""

    def __init__(
        self,
        tmp_path: Path,
        *,
        script_body: str,
        name: str = "flow",
        output_protocol: str = CLAUDE_STREAM_JSON_PROTOCOL_ID,
    ) -> None:
        self.worktree = _git_worktree(tmp_path)
        self.log_base = tmp_path / "repo" / "logs"
        self.log_base.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = tmp_path / "console.db"
        self.store = SqliteConsoleStore(self.db_path)
        self.config = _config_with_fake_agent(
            _write_fake_agent_script(self.worktree, name=name, body=script_body),
            output_protocol=output_protocol,
        )

    def invoke(
        self,
        *,
        phase: str = PHASE_IMPLEMENTATION,
        prompt: str = "implement the thing",
        model_selection: ModelSelection | None = None,
        timeout_seconds: int | None = None,
        transient_retry_attempts: int = 0,
    ) -> None:
        """在真路由作用域内跑一次真实进程调用（异常原样上抛给调用方断言）。"""
        with issue_output_routing(
            repo_id=_REPO_ID,
            issue_number=_ISSUE_NUMBER,
            log_base=self.log_base,
            output_view=NoOpRunnerLiveView(),
        ) as sink:
            routed_runner = _OutputRoutedProcessRunner(SubprocessRunner(), sink)
            with bound_invocation_trace_context(
                build_invocation_trace_context(
                    repo_id=_REPO_ID,
                    issue_number=_ISSUE_NUMBER,
                    run_history_store=self.store,
                )
            ):
                run_agent_with_prompt_resilient(
                    "claude",
                    prompt,
                    self.worktree,
                    routed_runner,
                    config=self.config,
                    capture_output=True,
                    timeout_seconds=timeout_seconds,
                    transient_retry_attempts=transient_retry_attempts,
                    model_selection=model_selection,
                    invocation_phase=phase,
                    invocation_attempt=1,
                )

    def log_text(self) -> str:
        """读取本次 run 真实落盘的 per-Issue 日志。"""
        log_dir = self.log_base / "agent-runner" / "issues" / _REPO_ID
        matching = sorted(log_dir.glob(f"issue-{_ISSUE_NUMBER}-*.log"))
        assert len(matching) == 1, f"expected exactly one Issue log, got {matching}"
        return matching[0].read_text(encoding="utf-8")

    def events(self) -> list[InvocationEventRecord]:
        """fresh 读：新开 store 连接，模拟 ``kc logs`` 的独立会话。"""
        fresh_store = SqliteConsoleStore(self.db_path)
        return fresh_store.list_issue_invocation_events(
            repo_id=_REPO_ID, issue_number=_ISSUE_NUMBER
        )

    def details(self) -> list[dict[str, object]]:
        return [json.loads(event.detail_json) for event in self.events()]


def _marker_lines(log_text: str, marker: str) -> list[str]:
    return [line for line in log_text.splitlines() if marker in line]


class TestInvocationMarkersReachExistingIssueLog:
    """rv-1：起止标记与完整身份落在**既有**的 per-Issue 日志里。"""

    def test_start_and_end_markers_carry_identity_and_models(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IAR_CONFIG", str(tmp_path / "unused-config.toml"))
        harness = _InvocationRunHarness(tmp_path, script_body=_success_script_body())
        harness.invoke(model_selection=ModelSelection(agent="claude", model=_REQUESTED_MODEL))

        log_text = harness.log_text()
        start_lines = _marker_lines(log_text, INVOCATION_START_MARKER)
        end_lines = _marker_lines(log_text, INVOCATION_END_MARKER)
        assert len(start_lines) == 1
        assert len(end_lines) == 1

        events = harness.events()
        assert [event.event_type for event in events] == [
            EVENT_INVOCATION_STARTED,
            EVENT_INVOCATION_FINISHED,
        ]
        invocation_id = events[0].invocation_id
        assert events[0].phase == PHASE_IMPLEMENTATION
        assert events[0].role == "implementer"
        assert events[0].agent == "claude"

        for marker_line in (*start_lines, *end_lines):
            # 并发归属靠标记行自带的身份，不靠相邻文本推断。
            assert f"invocation={invocation_id}" in marker_line
            assert f"run={events[0].run_id}" in marker_line
            assert f"issue={_ISSUE_NUMBER}" in marker_line
            assert "attempt=1" in marker_line
            assert f"phase={PHASE_IMPLEMENTATION}" in marker_line
            assert "role=implementer" in marker_line
            assert "executor=claude" in marker_line

        assert f"model_requested={_REQUESTED_MODEL}" in start_lines[0]
        assert f"model_requested={_REQUESTED_MODEL}" in end_lines[0]
        assert f"model_reported={_REPORTED_MODEL}" in end_lines[0]
        assert f"model_source={MODEL_SOURCE_EXECUTOR_REPORT}" in end_lines[0]
        assert "outcome=ok" in end_lines[0]
        assert "exit_code=0" in end_lines[0]
        assert "duration_s=" in end_lines[0]

    def test_events_persist_requested_and_reported_model_separately(self, tmp_path: Path) -> None:
        harness = _InvocationRunHarness(tmp_path, script_body=_success_script_body())
        harness.invoke(
            model_selection=ModelSelection(
                agent="claude", model=_REQUESTED_MODEL, reasoning_effort="high"
            )
        )

        started_detail, finished_detail = harness.details()
        assert started_detail["requested_model"] == _REQUESTED_MODEL
        assert started_detail["requested_reasoning_effort"] == "high"
        assert started_detail["reported_model"] is None
        assert started_detail["model_source"] == MODEL_SOURCE_UNKNOWN
        assert started_detail["outcome"] is None

        assert finished_detail["requested_model"] == _REQUESTED_MODEL
        assert finished_detail["reported_model"] == _REPORTED_MODEL
        assert finished_detail["model_source"] == MODEL_SOURCE_EXECUTOR_REPORT
        assert finished_detail["outcome"] == OUTCOME_OK
        assert finished_detail["exit_code"] == 0
        assert finished_detail["resumed_session_id"] is None
        assert finished_detail["token_usage"] == {
            "input_tokens": 1200,
            "output_tokens": 340,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 120,
        }
        assert finished_detail["duration_seconds"] >= 0.0

    def test_log_locator_points_back_at_the_real_issue_log(self, tmp_path: Path) -> None:
        """事件里的定位串必须能解析回真实日志文件（读取侧唯一入口）。"""
        harness = _InvocationRunHarness(tmp_path, script_body=_success_script_body())
        harness.invoke()

        started_detail, _ = harness.details()
        locator = started_detail["log_locator"]
        assert isinstance(locator, str) and locator
        resolved = resolve_invocation_log_path(harness.log_base, locator)
        assert resolved.exists()
        assert INVOCATION_START_MARKER in resolved.read_text(encoding="utf-8")

    def test_run_id_correlates_without_prd(self, tmp_path: Path) -> None:
        """无 PRD 的 Issue 同样完整关联：run 身份不含 PRD 路径。"""
        harness = _InvocationRunHarness(tmp_path, script_body=_success_script_body())
        harness.invoke()

        events = harness.events()
        run_id = events[0].run_id
        assert run_id.startswith(f"{_REPO_ID}#issue-{_ISSUE_NUMBER}#")
        assert "prd" not in run_id.lower()
        assert all(event.run_id == run_id for event in events)

    def test_unreported_model_stays_unknown_end_to_end(self, tmp_path: Path) -> None:
        """执行器没自报模型时记"未提供"，绝不回填配置里的绑定值。"""
        harness = _InvocationRunHarness(tmp_path, script_body=_silent_script_body(), name="quiet")
        harness.invoke(model_selection=ModelSelection(agent="claude", model=_REQUESTED_MODEL))

        log_text = harness.log_text()
        end_line = _marker_lines(log_text, INVOCATION_END_MARKER)[0]
        assert f"model_reported={MODEL_UNREPORTED}" in end_line
        assert f"model_source={MODEL_SOURCE_UNKNOWN}" in end_line

        _, finished_detail = harness.details()
        assert finished_detail["reported_model"] is None
        assert finished_detail["model_source"] == MODEL_SOURCE_UNKNOWN
        assert finished_detail["requested_model"] == _REQUESTED_MODEL

    def test_no_model_binding_shows_not_requested(self, tmp_path: Path) -> None:
        harness = _InvocationRunHarness(
            tmp_path, script_body=_silent_script_body(), name="nobinding"
        )
        harness.invoke()

        log_text = harness.log_text()
        start_line = _marker_lines(log_text, INVOCATION_START_MARKER)[0]
        assert f"model_requested={MODEL_NOT_REQUESTED}" in start_line
        started_detail, _ = harness.details()
        assert started_detail["requested_model"] is None


class TestRetryAndFallbackAreSeparateInvocations:
    """rv-2：重试是**独立**的调用事实，用 retry_of 关联而不是合并。"""

    def test_transient_retry_records_two_linked_invocations(self, tmp_path: Path) -> None:
        counter_path = str(tmp_path / "attempt-counter")
        # 走 plain 协议：stderr 会被完整捕获进 CommandFailedError，瞬态签名
        # （connection reset）才判得出来；claude 流式路径不采集 stderr。
        harness = _InvocationRunHarness(
            tmp_path,
            script_body=_transient_then_success_script_body(counter_path),
            name="flaky",
            output_protocol=PLAIN_PROTOCOL_ID,
        )
        harness.invoke(transient_retry_attempts=1)

        events = harness.events()
        assert [event.event_type for event in events] == [
            EVENT_INVOCATION_STARTED,
            EVENT_INVOCATION_FINISHED,
            EVENT_INVOCATION_STARTED,
            EVENT_INVOCATION_FINISHED,
        ]
        first_id = events[0].invocation_id
        second_id = events[2].invocation_id
        assert first_id != second_id

        details = harness.details()
        assert details[0]["retry_of"] is None
        assert details[1]["outcome"] == OUTCOME_ERROR
        assert details[2]["retry_of"] == first_id
        assert details[2]["retry_reason"] == RETRY_REASON_TRANSIENT
        assert details[3]["outcome"] == OUTCOME_OK

        log_text = harness.log_text()
        end_lines = _marker_lines(log_text, INVOCATION_END_MARKER)
        assert len(end_lines) == 2
        assert f"retry_of={first_id}" in end_lines[1]
        assert (
            f"retry_reason={RETRY_REASON_TRANSIENT}"
            in _marker_lines(log_text, INVOCATION_START_MARKER)[1]
        )

    def test_review_phase_records_its_own_role(self, tmp_path: Path) -> None:
        harness = _InvocationRunHarness(tmp_path, script_body=_success_script_body(), name="review")
        harness.invoke(phase=PHASE_REVIEW)

        events = harness.events()
        assert events[0].phase == PHASE_REVIEW
        assert events[0].role == "reviewer"
        assert "role=reviewer" in harness.log_text()


class TestFailuresAndTimeoutsRecordOutcome:
    """rv-3：失败与超时也记终态；观测绝不改变业务结果。"""

    def test_wall_clock_timeout_records_timeout_outcome(self, tmp_path: Path) -> None:
        harness = _InvocationRunHarness(tmp_path, script_body=_timeout_script_body(), name="slow")
        with pytest.raises(subprocess.TimeoutExpired):
            harness.invoke(timeout_seconds=1)

        events = harness.events()
        assert [event.event_type for event in events] == [
            EVENT_INVOCATION_STARTED,
            EVENT_INVOCATION_FINISHED,
        ]
        _, finished_detail = harness.details()
        assert finished_detail["outcome"] == OUTCOME_TIMEOUT
        assert finished_detail["failure_category"] == FAILURE_TIMEOUT
        assert finished_detail["finished_at"] is not None

        end_line = _marker_lines(harness.log_text(), INVOCATION_END_MARKER)[0]
        assert f"outcome={OUTCOME_TIMEOUT}" in end_line
        assert f"failure_category={FAILURE_TIMEOUT}" in end_line

    def test_business_exception_still_propagates_unchanged(self, tmp_path: Path) -> None:
        """观测落盘不得吞掉或改写业务异常（FR-3 的旁路语义）。"""
        counter_path = str(tmp_path / "attempt-counter")
        harness = _InvocationRunHarness(
            tmp_path,
            script_body=_transient_then_success_script_body(counter_path),
            name="propagate",
            output_protocol=PLAIN_PROTOCOL_ID,
        )
        # 不给重试额度：第一次的瞬态失败必须原样抛给上层 recovery。
        with pytest.raises(subprocess.CalledProcessError):
            harness.invoke(transient_retry_attempts=0)
        assert len(harness.events()) == 2

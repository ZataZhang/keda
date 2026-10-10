"""Agent 调用观测（Issue #242）的单元测试：身份、脱敏、降级与时间线解读。

被测边界是 :mod:`backend.core.use_cases.agent_invocation_tracing` 的纯逻辑与
旁路语义；真实子进程与真实 SQLite 的端到端链路见
``tests/test_agent_invocation_flow.py``，真实 ``kc`` CLI 的验收证据见 PRD 的
Realistic Validation。

三条不可让步的约束各有一组用例钉住：

1. 旁路语义——存储故障只降级 coverage，绝不改变业务结果；
2. 不采自由文本——提示词/argv/异常 message 一律不出现在事件与日志标记里；
3. 不虚构终态——只有开始事件时按 ``process_confirmed_exited`` 区分
   ``unclosed`` / ``incomplete``，永不反推结束时间。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path

import pytest

from backend.core.shared.interfaces.agent_runner import AGENT_REPORTED_MODEL_ATTR_NAME
from backend.core.shared.interfaces.runner_console import InvocationEventRecord
from backend.core.shared.models.agent_runner import CommandResult, TokenUsage
from backend.core.use_cases.agent_invocation_tracing import (
    COVERAGE_REASON_STORE_WRITE_FAILED,
    EVENT_INVOCATION_FINISHED,
    EVENT_INVOCATION_STARTED,
    FAILURE_NONZERO_EXIT,
    FAILURE_TIMEOUT,
    INVOCATION_COVERAGE_INCOMPLETE_MARKER,
    INVOCATION_END_MARKER,
    INVOCATION_START_MARKER,
    MODEL_NOT_REQUESTED,
    MODEL_SOURCE_EXECUTOR_REPORT,
    MODEL_SOURCE_UNKNOWN,
    MODEL_UNREPORTED,
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_INCOMPLETE,
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    OUTCOME_UNCLOSED,
    PHASE_FIX,
    PHASE_IMPLEMENTATION,
    PHASE_REVIEW,
    PHASE_UNSPECIFIED,
    RETRY_REASON_EXECUTOR_FALLBACK,
    RETRY_REASON_TRANSIENT,
    InvocationLogLocatorError,
    InvocationStartRequest,
    InvocationTraceContext,
    bound_invocation_trace_context,
    build_invocation_run_id,
    build_invocation_timeline,
    build_log_locator,
    classify_invocation_failure,
    describe_unclosed_invocations,
    finish_invocation,
    link_next_invocation,
    normalize_reported_model,
    resolve_invocation_log_path,
    resolve_phase_role,
    sanitize_identifier,
    start_invocation,
)

_REPO_ID = "keda-main"
#: 一段"绝不能落库"的自由文本：argv_tail 投递下它会整段出现在 argv 里。
_SECRET_PROMPT = "please refactor SECRET-TOKEN=abc123 and delete everything"


class _FakeInvocationStore:
    """内存版调用事件账本；记录写入次数以便断言幂等与降级。"""

    def __init__(self) -> None:
        self.events: list[InvocationEventRecord] = []

    def append_invocation_event(self, event_record: InvocationEventRecord) -> bool:
        self.events.append(event_record)
        return True

    def list_invocation_events(self, *, run_id: str) -> list[InvocationEventRecord]:
        return [event for event in self.events if event.run_id == run_id]

    def list_issue_invocation_events(
        self, *, repo_id: str, issue_number: int, limit: int = 500
    ) -> list[InvocationEventRecord]:
        matched = [
            event
            for event in self.events
            if event.repo_id == repo_id and event.issue_number == issue_number
        ]
        return matched[-limit:]


class _BrokenInvocationStore(_FakeInvocationStore):
    """每次写入都抛错的账本：用于验证旁路语义（故障不得外溢到业务）。"""

    def append_invocation_event(self, event_record: InvocationEventRecord) -> bool:
        raise RuntimeError("simulated console store outage")


def _context(store: object | None = None, **overrides: object) -> InvocationTraceContext:
    """构造一个绑定到临时日志根的观测上下文。"""
    kwargs: dict[str, object] = {
        "repo_id": _REPO_ID,
        "run_id": "keda-main#issue-7#20261008T000000Z-abcdef012345",
        "issue_number": 7,
        "store": store if store is not None else _FakeInvocationStore(),
        "log_root": Path("/tmp/logs"),
        "log_locator": "agent-runner/issues/keda-main/issue-7-20261008-000000.log",
    }
    kwargs.update(overrides)
    return InvocationTraceContext(**kwargs)  # type: ignore[arg-type]


def _request(**overrides: object) -> InvocationStartRequest:
    """构造一次实现阶段调用的起点声明。"""
    kwargs: dict[str, object] = {
        "agent_name": "claude",
        "phase": PHASE_IMPLEMENTATION,
        "profile": "run",
        "attempt_number": 1,
    }
    kwargs.update(overrides)
    return InvocationStartRequest(**kwargs)  # type: ignore[arg-type]


def _details(store: _FakeInvocationStore) -> list[dict[str, object]]:
    """把已落库事件的 detail_json 解出来，便于逐字段断言。"""
    return [json.loads(event.detail_json) for event in store.events]


class TestSanitization:
    """ "不采自由文本"约束的执行点。"""

    def test_rejects_free_text_and_keeps_whitelist_shapes(self) -> None:
        assert sanitize_identifier(_SECRET_PROMPT, fallback="未提供") == "未提供"
        assert sanitize_identifier("claude-sonnet-4-5", fallback="x") == "claude-sonnet-4-5"
        assert sanitize_identifier("org/team:model@v1.2+build_3", fallback="x") == (
            "org/team:model@v1.2+build_3"
        )

    def test_rejects_non_string_and_oversized_values(self) -> None:
        assert sanitize_identifier(None, fallback="未提供") == "未提供"
        assert sanitize_identifier(1234, fallback="未提供") == "未提供"
        assert sanitize_identifier("a" * 200, fallback="未提供") == "未提供"

    def test_reported_model_missing_becomes_none_not_config_default(self) -> None:
        assert normalize_reported_model(None) is None
        assert normalize_reported_model("") is None
        assert normalize_reported_model("模型 名字 带空格") is None
        assert normalize_reported_model("gpt-5-mini") == "gpt-5-mini"

    def test_failure_classification_reads_type_only(self) -> None:
        """异常 message 内嵌整段 argv（含提示词）时，归类结果不含任何原文。"""
        exc = subprocess.CalledProcessError(
            1, ["claude", "-p", _SECRET_PROMPT], output="leaked", stderr="leaked"
        )
        category = classify_invocation_failure(exc)
        assert category == FAILURE_NONZERO_EXIT
        assert _SECRET_PROMPT not in category
        assert "SECRET-TOKEN" not in category

    def test_failure_categories_are_a_closed_set(self) -> None:
        assert classify_invocation_failure(subprocess.TimeoutExpired(["claude"], 30)) == (
            FAILURE_TIMEOUT
        )
        assert classify_invocation_failure(FileNotFoundError("claude")) == "agent_unavailable"
        assert classify_invocation_failure(OSError("boom")) == "os_error"
        assert classify_invocation_failure(RuntimeError("boom")) == "runtime_error"
        assert classify_invocation_failure(ValueError("boom")) == "unknown"

    def test_unknown_phase_falls_back_to_unspecified(self) -> None:
        assert resolve_phase_role("totally-made-up") == "unspecified"
        assert resolve_phase_role(PHASE_IMPLEMENTATION) == "implementer"
        assert resolve_phase_role(PHASE_REVIEW) == "reviewer"
        assert resolve_phase_role(PHASE_FIX) == "fixer"


class TestLogLocator:
    """日志定位串必须相对日志根，读取侧拒绝任何越界形态。"""

    def test_build_locator_is_relative_posix(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_path = log_root / "agent-runner" / "issues" / _REPO_ID / "issue-7-x.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("", encoding="utf-8")
        assert build_log_locator(log_root, log_path) == (
            f"agent-runner/issues/{_REPO_ID}/issue-7-x.log"
        )

    def test_build_locator_outside_root_returns_none(self, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere" / "issue-7.log"
        outside.parent.mkdir(parents=True)
        outside.write_text("", encoding="utf-8")
        assert build_log_locator(tmp_path / "logs", outside) is None

    def test_resolve_rejects_absolute_and_parent_traversal(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()
        with pytest.raises(InvocationLogLocatorError):
            resolve_invocation_log_path(log_root, "/etc/passwd")
        with pytest.raises(InvocationLogLocatorError):
            resolve_invocation_log_path(log_root, "../../etc/passwd")
        with pytest.raises(InvocationLogLocatorError):
            resolve_invocation_log_path(log_root, "")

    def test_resolve_rejects_symlink_escape(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "secret.log").write_text("x", encoding="utf-8")
        (log_root / "escape").symlink_to(outside_dir, target_is_directory=True)
        with pytest.raises(InvocationLogLocatorError):
            resolve_invocation_log_path(log_root, "escape/secret.log")

    def test_resolve_accepts_relative_path_inside_root(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        (log_root / "issues").mkdir(parents=True)
        resolved = resolve_invocation_log_path(log_root, "issues/issue-7.log")
        assert resolved == (log_root / "issues" / "issue-7.log").resolve()


class TestRunIdentity:
    """run 身份不依赖 PRD：无 PRD 的 Issue 同样完整关联。"""

    def test_run_id_is_prd_independent_and_unique(self) -> None:
        first = build_invocation_run_id(_REPO_ID, 228)
        second = build_invocation_run_id(_REPO_ID, 228)
        assert first != second
        assert f"{_REPO_ID}#issue-228#" in first
        assert "prd" not in first.lower()

    def test_run_id_without_issue_number_still_usable(self) -> None:
        run_id = build_invocation_run_id(_REPO_ID, None)
        assert run_id.startswith(f"{_REPO_ID}#issue-none#")

    def test_unsafe_repo_id_is_sanitized(self) -> None:
        run_id = build_invocation_run_id("repo with spaces\nand newline", 3)
        assert run_id.startswith("unknown-repo#issue-3#")


class TestNoContextIsNoOp:
    """未绑定上下文（非 Issue 场景）时必须完全空转。"""

    def test_start_returns_none_and_writes_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        store = _FakeInvocationStore()
        with caplog.at_level(logging.DEBUG, logger="backend"):
            observation = start_invocation(_request())
            finish_invocation(observation, result=CommandResult((), 0, "", ""))
        assert observation is None
        assert store.events == []
        assert caplog.text == ""

    def test_link_next_invocation_is_silent_without_context(self) -> None:
        link_next_invocation(RETRY_REASON_TRANSIENT)


class TestStartFinish:
    """开始/结束两条事件的字段口径与旁路语义。"""

    def test_two_events_share_identity_and_split_lifecycle_fields(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request(requested_model="claude-sonnet-4-5"))
            assert observation is not None
            finish_invocation(
                observation,
                result=CommandResult(
                    command=("claude",),
                    return_code=0,
                    stdout="",
                    stderr="",
                    reported_model="claude-sonnet-4-5-20250929",
                    token_usage=TokenUsage(10, 20, 30, 40),
                ),
            )

        assert [event.event_type for event in store.events] == [
            EVENT_INVOCATION_STARTED,
            EVENT_INVOCATION_FINISHED,
        ]
        started_detail, finished_detail = _details(store)
        assert started_detail["invocation_id"] == finished_detail["invocation_id"]
        assert started_detail["run_id"] == finished_detail["run_id"] == store.events[0].run_id
        assert started_detail["phase"] == PHASE_IMPLEMENTATION
        assert started_detail["role"] == "implementer"
        assert started_detail["executor"] == "claude"
        # 请求模型与执行器自报模型是两个独立事实，绝不互相回填。
        assert finished_detail["requested_model"] == "claude-sonnet-4-5"
        assert finished_detail["reported_model"] == "claude-sonnet-4-5-20250929"
        assert finished_detail["model_source"] == MODEL_SOURCE_EXECUTOR_REPORT
        assert finished_detail["outcome"] == OUTCOME_OK
        assert finished_detail["exit_code"] == 0
        assert finished_detail["failure_category"] is None
        assert finished_detail["token_usage"] == {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 40,
        }
        assert finished_detail["token_usage_source"] == MODEL_SOURCE_EXECUTOR_REPORT
        assert isinstance(finished_detail["duration_seconds"], float)
        assert finished_detail["duration_seconds"] >= 0.0
        # 开始事件不预判终态。
        assert started_detail["outcome"] is None
        assert started_detail["finished_at"] is None
        assert started_detail["reported_model"] is None
        assert started_detail["model_source"] == MODEL_SOURCE_UNKNOWN
        assert started_detail["internal_agent_coverage"] == "unobserved"
        assert started_detail["log_locator"] == (
            "agent-runner/issues/keda-main/issue-7-20261008-000000.log"
        )

    def test_unreported_model_stays_unknown_and_is_never_backfilled(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request(requested_model="claude-opus-4-1"))
            finish_invocation(observation, result=CommandResult((), 0, "", ""))
        _, finished_detail = _details(store)
        assert finished_detail["reported_model"] is None
        assert finished_detail["model_source"] == MODEL_SOURCE_UNKNOWN
        # 请求值只说明"下发了什么"，不得冒充"执行器报告了什么"。
        assert finished_detail["requested_model"] == "claude-opus-4-1"

    def test_unrequested_model_is_recorded_as_absent(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request())
            finish_invocation(observation, result=CommandResult((), 0, "", ""))
        started_detail, finished_detail = _details(store)
        assert started_detail["requested_model"] is None
        assert finished_detail["requested_model"] is None
        assert finished_detail["token_usage"] is None
        assert finished_detail["token_usage_source"] is None

    def test_nonzero_exit_records_failure_outcome(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request())
            finish_invocation(observation, result=CommandResult((), 3, "", ""))
        _, finished_detail = _details(store)
        assert finished_detail["outcome"] == OUTCOME_FAILED
        assert finished_detail["exit_code"] == 3
        assert finished_detail["failure_category"] == FAILURE_NONZERO_EXIT

    def test_timeout_records_timeout_outcome_without_exit_code(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request())
            with pytest.raises(subprocess.TimeoutExpired):
                try:
                    raise subprocess.TimeoutExpired(["claude", "-p", _SECRET_PROMPT], 30)
                finally:
                    finish_invocation(observation, exc=subprocess.TimeoutExpired(["claude"], 30))
        _, finished_detail = _details(store)
        assert finished_detail["outcome"] == OUTCOME_TIMEOUT
        assert finished_detail["failure_category"] == FAILURE_TIMEOUT
        assert finished_detail["exit_code"] is None
        assert _SECRET_PROMPT not in json.dumps(finished_detail, ensure_ascii=False)

    def test_crash_path_keeps_executor_reported_model_from_exception(self) -> None:
        store = _FakeInvocationStore()
        exc = subprocess.CalledProcessError(1, ["claude", "-p", _SECRET_PROMPT])
        setattr(exc, AGENT_REPORTED_MODEL_ATTR_NAME, "claude-sonnet-4-5-20250929")
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request())
            finish_invocation(observation, exc=exc)
        _, finished_detail = _details(store)
        assert finished_detail["reported_model"] == "claude-sonnet-4-5-20250929"
        assert finished_detail["model_source"] == MODEL_SOURCE_EXECUTOR_REPORT
        assert finished_detail["outcome"] == OUTCOME_ERROR
        assert finished_detail["exit_code"] == 1
        assert _SECRET_PROMPT not in json.dumps(finished_detail, ensure_ascii=False)

    def test_missing_result_and_exception_records_error(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request())
            finish_invocation(observation)
        _, finished_detail = _details(store)
        assert finished_detail["outcome"] == OUTCOME_ERROR
        assert finished_detail["failure_category"] == "unknown"

    def test_unknown_phase_is_normalized_before_recording(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request(phase="invented-phase"))
            finish_invocation(observation, result=CommandResult((), 0, "", ""))
        assert store.events[0].phase == PHASE_UNSPECIFIED
        assert store.events[0].role == "unspecified"
        assert _details(store)[0]["phase"] == PHASE_UNSPECIFIED

    def test_free_text_never_reaches_events(self) -> None:
        """提示词/argv/环境变量值一律不落库：detail 里只有闭集与自生成 id。"""
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(
                _request(agent_name=f"claude {_SECRET_PROMPT}", requested_model=_SECRET_PROMPT)
            )
            finish_invocation(observation, result=CommandResult((), 0, _SECRET_PROMPT, "err"))
        blob = "".join(event.detail_json for event in store.events)
        assert _SECRET_PROMPT not in blob
        assert "SECRET-TOKEN" not in blob
        # 不可信的 agent 名与模型名被收敛成闭集兜底值，而不是原文透传。
        started_detail, _ = _details(store)
        assert started_detail["executor"] == "unknown"
        assert started_detail["requested_model"] is None


class TestLogMarkers:
    """标记行是运营者唯一的实时抓手：身份必须齐全，自由文本必须缺席。"""

    def test_start_and_end_markers_carry_full_identity(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = _FakeInvocationStore()
        with caplog.at_level(logging.INFO, logger="backend"):
            with bound_invocation_trace_context(_context(store)):
                observation = start_invocation(_request(requested_model="claude-sonnet-4-5"))
                finish_invocation(
                    observation,
                    result=CommandResult((), 0, "", "", reported_model="claude-sonnet-4-5"),
                )

        start_lines = [line for line in caplog.text.splitlines() if INVOCATION_START_MARKER in line]
        end_lines = [line for line in caplog.text.splitlines() if INVOCATION_END_MARKER in line]
        assert len(start_lines) == 1
        assert len(end_lines) == 1
        invocation_id = store.events[0].invocation_id
        for marker_line in (*start_lines, *end_lines):
            assert f"invocation={invocation_id}" in marker_line
            assert f"run={store.events[0].run_id}" in marker_line
            assert "issue=7" in marker_line
            assert "attempt=1" in marker_line
            assert f"phase={PHASE_IMPLEMENTATION}" in marker_line
            assert "role=implementer" in marker_line
            assert "executor=claude" in marker_line
        assert "model_requested=claude-sonnet-4-5" in start_lines[0]
        assert "model_reported=claude-sonnet-4-5" in end_lines[0]
        assert f"model_source={MODEL_SOURCE_EXECUTOR_REPORT}" in end_lines[0]
        assert "outcome=ok" in end_lines[0]
        assert "duration_s=" in end_lines[0]

    def test_marker_shows_absent_model_states_explicitly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="backend"):
            with bound_invocation_trace_context(_context()):
                observation = start_invocation(_request())
                finish_invocation(observation, result=CommandResult((), 0, "", ""))
        end_line = next(line for line in caplog.text.splitlines() if INVOCATION_END_MARKER in line)
        assert f"model_requested={MODEL_NOT_REQUESTED}" in end_line
        assert f"model_reported={MODEL_UNREPORTED}" in end_line
        assert f"model_source={MODEL_SOURCE_UNKNOWN}" in end_line

    def test_markers_never_contain_prompt_text(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="backend"):
            with bound_invocation_trace_context(_context()):
                observation = start_invocation(_request(requested_model=_SECRET_PROMPT))
                finish_invocation(
                    observation,
                    exc=subprocess.CalledProcessError(1, ["claude", "-p", _SECRET_PROMPT]),
                )
        assert _SECRET_PROMPT not in caplog.text
        assert "SECRET-TOKEN" not in caplog.text


class TestRetryLinkage:
    """回退/重试是**独立**的调用事实，用 retry_of 关联而不是合并。"""

    def test_declared_link_is_consumed_exactly_once(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            first = start_invocation(_request(agent_name="claude"))
            assert first is not None
            finish_invocation(first, result=CommandResult((), 1, "", ""))

            link_next_invocation(RETRY_REASON_EXECUTOR_FALLBACK)
            second = start_invocation(_request(agent_name="kimi"))
            assert second is not None
            finish_invocation(second, result=CommandResult((), 0, "", ""))

            third = start_invocation(_request(agent_name="kimi"))
            finish_invocation(third, result=CommandResult((), 0, "", ""))

        first_detail, _, second_detail, _, third_detail, _ = _details(store)
        assert first_detail["retry_of"] is None
        assert second_detail["retry_of"] == first_detail["invocation_id"]
        assert second_detail["retry_reason"] == RETRY_REASON_EXECUTOR_FALLBACK
        assert second_detail["executor"] == "kimi"
        # 关联不得顺延给更后面的调用。
        assert third_detail["retry_of"] is None
        assert third_detail["retry_reason"] is None
        # 两次调用各自独立成记录，没有被折叠成一条"成功"。
        invocation_ids = {event.invocation_id for event in store.events}
        assert len(invocation_ids) == 3

    def test_transient_retry_link_uses_closed_set_reason(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            first = start_invocation(_request())
            finish_invocation(first, exc=OSError("transient"))
            link_next_invocation(RETRY_REASON_TRANSIENT)
            second = start_invocation(_request())
            finish_invocation(second, result=CommandResult((), 0, "", ""))
        _, _, second_detail, _ = _details(store)
        assert second_detail["retry_reason"] == RETRY_REASON_TRANSIENT

    def test_link_without_prior_invocation_is_silent(self) -> None:
        store = _FakeInvocationStore()
        context = _context(store)
        with bound_invocation_trace_context(context):
            link_next_invocation(RETRY_REASON_TRANSIENT)
            observation = start_invocation(_request())
            finish_invocation(observation, result=CommandResult((), 0, "", ""))
        assert context.pending_retry_of is None
        assert _details(store)[0]["retry_of"] is None


class TestBypassSemantics:
    """观测故障绝不改变业务结果（FR-3）。"""

    def test_store_outage_degrades_coverage_and_raises_nothing(self) -> None:
        store = _BrokenInvocationStore()
        context = _context(store)
        with bound_invocation_trace_context(context):
            observation = start_invocation(_request())
            assert observation is not None
            assert observation.recorded is False
            finish_invocation(observation, result=CommandResult((), 0, "", ""))
        assert context.coverage_complete is False
        # 业务结果原样返回，没有被观测故障改写。
        assert store.events == []

    def test_coverage_marker_is_logged_once_on_first_degradation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        context = _context(_BrokenInvocationStore())
        with caplog.at_level(logging.WARNING, logger="backend"):
            with bound_invocation_trace_context(context):
                for _ in range(3):
                    observation = start_invocation(_request())
                    finish_invocation(observation, result=CommandResult((), 0, "", ""))
        marker_lines = [
            line
            for line in caplog.text.splitlines()
            if INVOCATION_COVERAGE_INCOMPLETE_MARKER in line
        ]
        assert len(marker_lines) == 1
        assert f"reason={COVERAGE_REASON_STORE_WRITE_FAILED}" in marker_lines[0]

    def test_missing_store_marks_coverage_incomplete_at_build_time(self) -> None:
        context = _context(store=None)
        context.store = None
        assert context.coverage_complete is True
        context.mark_coverage_incomplete("store_unavailable")
        assert context.coverage_complete is False


class TestTimeline:
    """时间线解读：未闭合 ≠ 已中断，终态时间绝不虚构。"""

    @staticmethod
    def _events(store: _FakeInvocationStore) -> list[InvocationEventRecord]:
        return list(store.events)

    def test_closed_invocation_reports_recorded_outcome(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request(requested_model="claude-sonnet-4-5"))
            finish_invocation(
                observation,
                result=CommandResult(
                    (),
                    0,
                    "",
                    "",
                    reported_model="claude-sonnet-4-5-20250929",
                ),
            )
        rows = build_invocation_timeline(self._events(store))
        assert len(rows) == 1
        assert rows[0].outcome == OUTCOME_OK
        assert rows[0].finished_at is not None
        assert rows[0].duration_seconds is not None
        assert rows[0].requested_model == "claude-sonnet-4-5"
        assert rows[0].reported_model == "claude-sonnet-4-5-20250929"

    def test_start_only_defaults_to_unclosed_without_inventing_times(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            start_invocation(_request())
        rows = build_invocation_timeline(self._events(store))
        assert len(rows) == 1
        assert rows[0].outcome == OUTCOME_UNCLOSED
        assert rows[0].finished_at is None
        assert rows[0].duration_seconds is None
        assert describe_unclosed_invocations(rows) == [rows[0].invocation_id]

    def test_start_only_upgrades_to_incomplete_when_exit_confirmed(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            start_invocation(_request())
        rows = build_invocation_timeline(self._events(store), process_confirmed_exited=True)
        assert rows[0].outcome == OUTCOME_INCOMPLETE
        # 即便确认已中断，也不反推结束时间与耗时。
        assert rows[0].finished_at is None
        assert rows[0].duration_seconds is None

    def test_retry_chain_is_readable_from_timeline(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            first = start_invocation(_request(agent_name="claude"))
            finish_invocation(first, result=CommandResult((), 1, "", ""))
            link_next_invocation(RETRY_REASON_EXECUTOR_FALLBACK)
            second = start_invocation(_request(agent_name="kimi", phase=PHASE_IMPLEMENTATION))
            finish_invocation(second, result=CommandResult((), 0, "", ""))
        rows = build_invocation_timeline(self._events(store))
        assert [row.executor for row in rows] == ["claude", "kimi"]
        assert rows[0].retry_of is None
        assert rows[1].retry_of == rows[0].invocation_id
        assert describe_unclosed_invocations(rows) == []

    def test_corrupt_detail_degrades_to_minimal_row(self) -> None:
        store = _FakeInvocationStore()
        with bound_invocation_trace_context(_context(store)):
            observation = start_invocation(_request())
            finish_invocation(observation, result=CommandResult((), 0, "", ""))
        corrupted = [
            InvocationEventRecord(
                run_id=event.run_id,
                event_key=event.event_key,
                event_type=event.event_type,
                invocation_id=event.invocation_id,
                repo_id=event.repo_id,
                issue_number=event.issue_number,
                phase=event.phase,
                role=event.role,
                agent=event.agent,
                occurred_at=event.occurred_at,
                detail_json="{not json",
            )
            for event in store.events
        ]
        rows = build_invocation_timeline(corrupted)
        assert len(rows) == 1
        assert rows[0].invocation_id == store.events[0].invocation_id

    def test_finish_without_start_is_ignored(self) -> None:
        store = _FakeInvocationStore()
        orphan = InvocationEventRecord(
            run_id="r",
            event_key="inv-x:finished",
            event_type=EVENT_INVOCATION_FINISHED,
            invocation_id="inv-x",
            repo_id=_REPO_ID,
            issue_number=7,
            phase=PHASE_IMPLEMENTATION,
            role="implementer",
            agent="claude",
            occurred_at="2026-10-08T00:00:00+00:00",
            detail_json=json.dumps({"outcome": OUTCOME_OK}),
        )
        store.events.append(orphan)
        assert build_invocation_timeline(self._events(store)) == []


class TestConcurrencyIsolation:
    """并发 Issue 的输出必须可归属，不靠相邻文本推断（FR-4 / FR-6）。"""

    def test_two_threads_do_not_cross_contaminate(self) -> None:
        store = _FakeInvocationStore()
        contexts = {
            7: _context(store, issue_number=7, run_id="run-issue-7"),
            9: _context(store, issue_number=9, run_id="run-issue-9"),
        }
        barrier = threading.Barrier(2)
        failures: list[BaseException] = []

        def worker(issue_number: int) -> None:
            try:
                with bound_invocation_trace_context(contexts[issue_number]):
                    for _ in range(20):
                        barrier.wait(timeout=5)
                        observation = start_invocation(_request())
                        finish_invocation(observation, result=CommandResult((), 0, "", ""))
            except BaseException as exc:  # noqa: BLE001 - 收集断言用。
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in (7, 9)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert failures == []
        assert len(store.events) == 80
        per_issue = {7: 0, 9: 0}
        for event in store.events:
            detail = json.loads(event.detail_json)
            assert event.issue_number == detail["issue_number"]
            assert event.run_id == f"run-issue-{event.issue_number}"
            per_issue[event.issue_number] += 1
        assert per_issue == {7: 40, 9: 40}

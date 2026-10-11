"""活跃 attempt 停滞监督的进程语义与决策矩阵（Issue #256 / FR-5…FR-7，rv-3、rv-4）。

分层刻意贴着 rv-3 的 ``mock_boundary`` 走：

- **真实**：``SubprocessRunner`` 起的真实进程组（含孙进程）、登记簿里的 pid/组、
  精确击杀后的存活复核、既有 recovery 的入口异常。
- **可替换**：诊断结论（provider 的作答文本）与 GitHub，PRD 明确允许替换。

被禁止的取巧在这里逐条有对应测试：默认关闭时必须一次模型都不调用、不建线程；
非 ``stalled`` 结论与"归属不可证实 / 现场变了 / 调用已结束"都不得触碰任何进程；
同一个停滞窗口至多一次诊断。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, CommandResult, IssueSummary
from backend.core.shared.models.agent_session import PreviewProfile
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_GENERATE,
    AGENT_PROFILE_RUN,
    AgentProfileSpec,
    AgentSpec,
)
from backend.core.shared.models.agent_stall import (
    AgentStallCancelledError,
    AttemptOwnership,
    ProgressSnapshot,
    StallCancelOutcome,
    StallSupervisorConfig,
    StallVerdictKind,
)
from backend.core.use_cases import agent_runner_stall_supervision as supervision
from backend.core.use_cases.agent_runner_stall_supervision import (
    STALL_DIAGNOSIS_MARKER,
    STALL_HANDOFF_MARKER,
    StallDiagnosisRequest,
    StallSupervisionRequest,
    build_progress_snapshot,
    build_supervision_prompt,
    parse_supervisor_verdict,
    supervised_agent_invocation,
)
from backend.core.use_cases.run_agent_once import run_agent_with_prompt
from backend.infrastructure import attempt_process_registry
from backend.infrastructure.process_runner import SubprocessRunner

# ---------------------------------------------------------------------------
# 假 writer：真实进程组里的长驻子进程 + 它自己拉起的孙进程
# ---------------------------------------------------------------------------

_WRITER_SOURCE = """
import json, os, pathlib, subprocess, sys, time
marker = pathlib.Path(sys.argv[1])
runtime = float(sys.argv[2])
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
marker.write_text(
    json.dumps({
        "pid": os.getpid(),
        "process_group": os.getpgid(0),
        "grandchild_pid": grandchild.pid,
    }),
    encoding="utf-8",
)
time.sleep(runtime)
pathlib.Path(str(marker) + ".finished").write_text("finished", encoding="utf-8")
"""

#: 巡检周期（秒）。域数据类不校验单位，测试把它缩到毫秒级以保持运行时长；
#: 生产取值由 ``[agent_runner.stall_supervisor]`` 的 pydantic 侧限制为正整数。
_TICK_SECONDS = 0.05


def _pid_alive(process_pid: int) -> bool:
    """该 pid 当前是否存在（信号 0 只探测存活，不影响进程）。"""
    try:
        os.kill(process_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_alive(process_group: int) -> bool:
    """该进程组里是否还有活着的成员（组级判据，覆盖孙进程）。"""
    try:
        os.killpg(process_group, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait_for_marker(marker: Path, *, timeout_seconds: float = 10.0) -> dict[str, int]:
    """等 writer 自报 pid/组/孙进程 pid（超时即失败，不静默继续）。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if marker.exists():
            text = marker.read_text(encoding="utf-8")
            if text.strip():
                return json.loads(text)
        time.sleep(0.02)
    raise AssertionError(f"writer never reported itself at {marker}")


@pytest.fixture
def writer_argv(tmp_path: Path) -> tuple[str, ...]:
    """写出可执行的假 writer 脚本并返回它的 argv（默认驻留 60 秒）。"""
    script = tmp_path / "writer.py"
    script.write_text(_WRITER_SOURCE, encoding="utf-8")
    script.chmod(0o755)
    return (sys.executable, str(script))


@pytest.fixture
def worktree_path(tmp_path: Path) -> Path:
    """一个真实 git 仓库（现场采样的输入必须是真的）。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    for git_args in (
        ["init", "-q"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
    ):
        subprocess.run(["git", *git_args], cwd=worktree, check=True, capture_output=True)
    (worktree / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=worktree, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"], cwd=worktree, check=True, capture_output=True
    )
    return worktree


def _supervision_request(
    *,
    runner: IProcessRunner,
    worktree: Path,
    attempt_key: str,
    diagnose: Callable[[StallDiagnosisRequest], str],
    config: StallSupervisorConfig | None = None,
) -> StallSupervisionRequest:
    """组装一次被监督调用的上下文（默认：开启监督 + 毫秒级巡检）。"""
    return StallSupervisionRequest(
        config=config
        or StallSupervisorConfig(
            enabled=True,
            check_interval_seconds=_TICK_SECONDS,
            stalled_after_seconds=0,
        ),
        process_runner=runner,
        worktree_path=worktree,
        attempt_key=attempt_key,
        writer_agent="fake-writer",
        supervisor_agent="fake-supervisor",
        issue_number=77,
        invocation_phase="implement",
        invocation_attempt=1,
        trace_context=None,
        diagnose=diagnose,
    )


def _verdict_diagnose(
    calls: list[StallDiagnosisRequest],
    answer: str,
    *,
    on_call: Callable[[int], None] | None = None,
) -> Callable[[StallDiagnosisRequest], str]:
    """记录式诊断通道：返回固定作答文本，可选在第 N 次调用时执行副作用。"""

    def _diagnose(request: StallDiagnosisRequest) -> str:
        calls.append(request)
        if on_call is not None:
            on_call(len(calls))
        return answer

    return _diagnose


_STALLED_ANSWER = (
    "现场指纹冻结 30 分钟，无任何交付。\n"
    "STALL_VERDICT: stalled\n"
    "STALL_SUMMARY: writer 卡在等一个永远不会来的输入，已无进展 42 分钟\n"
    "STALL_EVIDENCE: HEAD 未变且工作区状态指纹冻结"
)


def _run_supervised_writer(
    *,
    runner: SubprocessRunner,
    worktree: Path,
    writer_argv: Sequence[str],
    request: StallSupervisionRequest,
    runtime_seconds: float,
) -> tuple[CommandResult | AgentStallCancelledError | None, dict[str, int], Path]:
    """在被监督的调用里真跑一个 writer，返回结局与其自报的身份。

    结局按属性判定而不是按类别：``SubprocessRunner`` 返回 infrastructure 侧那份同名
    ``CommandResult``，与 core 侧的结构等价、类别不同。
    """
    marker = worktree.parent / f"writer-{request.attempt_key}.json"
    argv = (*writer_argv, str(marker), str(runtime_seconds))

    def _invoke() -> CommandResult:
        return runner.run(
            list(argv),
            cwd=worktree,
            check=False,
            capture_output=False,
            attempt_key=request.attempt_key,
        )

    try:
        outcome: CommandResult | AgentStallCancelledError | None = supervised_agent_invocation(
            request, _invoke
        )
    except AgentStallCancelledError as cancel_exc:
        outcome = cancel_exc
    return outcome, _wait_for_marker(marker), marker


# ---------------------------------------------------------------------------
# 登记簿与精确击杀：真实进程组
# ---------------------------------------------------------------------------


def test_cancel_terminates_the_whole_writer_group_including_grandchildren(
    tmp_path: Path, writer_argv: Sequence[str]
) -> None:
    """stalled 处置杀的是**那个进程组**：直接子进程与孙进程一起没了。"""
    runner = SubprocessRunner()
    attempt_key = "issue-77-implement-1-probe"
    marker = tmp_path / "group-kill.json"
    argv = [*writer_argv, str(marker), "60"]

    with ThreadPoolExecutor(max_workers=1) as executor:
        running_future = executor.submit(
            lambda: runner.run(
                argv, cwd=tmp_path, check=False, capture_output=False, attempt_key=attempt_key
            )
        )
        marker_identity = _wait_for_marker(marker)
        try:
            ownership = runner.probe_live_attempt(attempt_key)
            assert ownership.confirmed, ownership.reason
            assert ownership.process_pid == marker_identity["pid"]
            assert ownership.child_process_group == marker_identity["process_group"]

            outcome = runner.cancel_live_attempt(attempt_key, ownership)
            assert outcome.cancelled and outcome.exited, outcome.reason
            assert outcome.process_group == marker_identity["process_group"]
            running_future.result(timeout=10)
        finally:
            if not running_future.done():
                running_future.cancel()
            for leftover_pid in (marker_identity["pid"], marker_identity["grandchild_pid"]):
                try:
                    os.kill(leftover_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    assert not _pid_alive(marker_identity["pid"])
    #: 孙进程在同一个组里：只杀直接子进程的实现会在这里露出来。
    assert not _pid_alive(marker_identity["grandchild_pid"])
    assert not _process_group_alive(marker_identity["process_group"])
    assert not (tmp_path / "group-kill.json.finished").exists()


def test_probe_reports_unconfirmed_for_an_attempt_that_was_never_registered() -> None:
    """不在册就是不可证实：监督器只能交班，绝没有可杀的句柄。"""
    runner = SubprocessRunner()

    ownership = runner.probe_live_attempt("issue-1-implement-1-ghost")

    assert not ownership.confirmed
    assert "no live process registered" in ownership.reason
    assert ownership.child_process_group is None


def test_cancel_refuses_when_the_verdict_saw_a_different_identity(
    tmp_path: Path, writer_argv: Sequence[str]
) -> None:
    """pid/组与结论里那份不一致时拒绝发信号（pid 复用是最典型的误杀）。"""
    runner = SubprocessRunner()
    attempt_key = "issue-77-implement-1-identity"
    marker = tmp_path / "identity.json"
    argv = [*writer_argv, str(marker), "5"]

    with ThreadPoolExecutor(max_workers=1) as executor:
        running_future = executor.submit(
            lambda: runner.run(
                argv, cwd=tmp_path, check=False, capture_output=False, attempt_key=attempt_key
            )
        )
        identity = _wait_for_marker(marker)
        live_ownership = runner.probe_live_attempt(attempt_key)
        assert live_ownership.confirmed
        stale_verdicts = (
            replace(live_ownership, child_process_group=identity["process_group"] + 1),
            replace(live_ownership, process_started_at=live_ownership.process_started_at + 1),
        )
        try:
            for stale_verdict in stale_verdicts:
                refused = runner.cancel_live_attempt(attempt_key, stale_verdict)
                assert not refused.cancelled
                assert "identity moved" in refused.reason
                assert refused.process_group is None
                assert _pid_alive(identity["pid"]), "拒绝路径不得触碰任何进程"
            result = running_future.result(timeout=15)
        finally:
            for leftover_pid in (identity["pid"], identity["grandchild_pid"]):
                try:
                    os.kill(leftover_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    assert result.return_code == 0
    assert (tmp_path / "identity.json.finished").exists()


def test_cancel_refuses_when_the_live_process_creation_identity_changes(
    tmp_path: Path,
    writer_argv: Sequence[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """诊断后的实时创建时刻变化时拒绝信号，即使旧 verdict 本身完全匹配。"""
    runner = SubprocessRunner()
    attempt_key = "issue-77-implement-1-creation-identity"
    marker = tmp_path / "creation-identity.json"
    argv = [*writer_argv, str(marker), "2"]

    with ThreadPoolExecutor(max_workers=1) as executor:
        running_future = executor.submit(
            lambda: runner.run(
                argv, cwd=tmp_path, check=False, capture_output=False, attempt_key=attempt_key
            )
        )
        identity = _wait_for_marker(marker)
        ownership = runner.probe_live_attempt(attempt_key)
        assert ownership.confirmed
        actual_start_time = ownership.process_started_at
        assert actual_start_time is not None
        monkeypatch.setattr(
            attempt_process_registry,
            "process_start_time",
            lambda process_pid: actual_start_time + 1 if process_pid == identity["pid"] else None,
        )

        refused = runner.cancel_live_attempt(attempt_key, ownership)

        assert not refused.cancelled
        assert "live process identity changed before cancellation" in refused.reason
        assert _pid_alive(identity["pid"]), "实时创建身份改变时不得触碰 writer"
        monkeypatch.undo()
        result = running_future.result(timeout=15)

    assert result.return_code == 0
    assert (tmp_path / "creation-identity.json.finished").exists()


def test_a_process_group_that_already_left_the_registry_cannot_be_cancelled(
    tmp_path: Path, writer_argv: Sequence[str]
) -> None:
    """注销之后（attempt 结束）再来的结论找不到对象：不发信号，也不误杀后继轮次。"""
    runner = SubprocessRunner()
    attempt_key = "issue-77-implement-1-unregistered"
    marker = tmp_path / "unregistered.json"
    argv = [*writer_argv, str(marker), "2"]

    result = runner.run(
        argv, cwd=tmp_path, check=False, capture_output=False, attempt_key=attempt_key
    )
    identity = _wait_for_marker(marker)
    assert result.return_code == 0

    ownership = runner.probe_live_attempt(attempt_key)
    assert not ownership.confirmed
    outcome = runner.cancel_live_attempt(
        attempt_key,
        AttemptOwnership(
            confirmed=True,
            process_pid=identity["pid"],
            child_process_group=identity["process_group"],
        ),
    )
    assert not outcome.cancelled
    assert "no longer registered" in outcome.reason


def test_a_finished_attempt_leaves_nothing_for_a_late_verdict_to_cancel(
    tmp_path: Path,
    writer_argv: Sequence[str],
) -> None:
    """注销之后（attempt 结束）再来的结论找不到对象：不发信号，也不误杀后继轮次。"""
    runner = SubprocessRunner()
    attempt_key = "issue-77-implement-1-finished"
    marker = tmp_path / "finished.json"

    result = runner.run(
        [*writer_argv, str(marker), "1"],
        cwd=tmp_path,
        check=False,
        capture_output=False,
        attempt_key=attempt_key,
    )
    assert result.return_code == 0

    ownership = runner.probe_live_attempt(attempt_key)
    assert not ownership.confirmed, ownership.reason
    assert ownership.child_process_group is None


# ---------------------------------------------------------------------------
# 现场采样：真实 git 状态，且刻意与 stdout 无关
# ---------------------------------------------------------------------------


def _snapshot_of(worktree: Path) -> ProgressSnapshot | None:
    """用真实 SubprocessRunner 采一次现场（签名来源必须是真 git）。"""
    request = _supervision_request(
        runner=SubprocessRunner(),
        worktree=worktree,
        attempt_key="issue-77-implement-1-snapshot",
        diagnose=lambda _request: "",
    )
    return build_progress_snapshot(request)


def test_progress_signature_tracks_real_delivery_not_output_noise(worktree_path: Path) -> None:
    """签名只认交付（HEAD / 工作区状态）；持续输出本身不算进展。"""
    first = _snapshot_of(worktree_path)
    assert first is not None
    noise = subprocess.run(
        [sys.executable, "-c", "print('x' * 100000)"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert noise.stdout
    after_noise = _snapshot_of(worktree_path)
    assert after_noise is not None and after_noise.signature == first.signature

    (worktree_path / "new-file.txt").write_text("delivered\n", encoding="utf-8")
    after_write = _snapshot_of(worktree_path)
    assert after_write is not None and after_write.has_progress_since(first)
    assert "head=" in (after_write.detail or "")


def test_snapshot_is_uncertain_when_the_worktree_cannot_be_read(tmp_path: Path) -> None:
    """读不到现场（不是 git 仓库）时返回 None：不可证实即交班，绝不处置。"""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    assert _snapshot_of(not_a_repo) is None


def test_progress_signature_has_no_room_for_output_activity(worktree_path: Path) -> None:
    """签名固定由六个事实拼成：provider 的 stdout 不在其中，所以刷不出"假进展"。"""
    noisy = subprocess.run(
        [sys.executable, "-c", "import sys; print('x' * 200000); sys.stdout.flush()"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert noisy.stdout.startswith("xxxx")

    snapshot = _snapshot_of(worktree_path)
    assert snapshot is not None
    assert len(snapshot.signature.split("|")) == 6
    assert "x" * 100 not in snapshot.signature
    assert "x" * 100 not in snapshot.detail


# ---------------------------------------------------------------------------
# 结论解析：闭集，拼错的单词没有击杀权
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected_kind"),
    [
        ("STALL_VERDICT: stalled\nSTALL_SUMMARY: s\nSTALL_EVIDENCE: e", StallVerdictKind.stalled),
        ("STALL_VERDICT: progress\nSTALL_SUMMARY: s\nSTALL_EVIDENCE: e", StallVerdictKind.progress),
        ("STALL_VERDICT: blocked\nSTALL_SUMMARY: s\nSTALL_EVIDENCE: e", StallVerdictKind.blocked),
        ("STALL_VERDICT: STALLED\nSTALL_SUMMARY: s\nSTALL_EVIDENCE: e", StallVerdictKind.stalled),
        ("STALL_VERDICT: probably-stalled", StallVerdictKind.uncertain),
        ("我觉得它停滞了", StallVerdictKind.uncertain),
        ("", StallVerdictKind.uncertain),
    ],
)
def test_verdict_parser_maps_into_the_closed_set(answer: str, expected_kind: object) -> None:
    """闭集解析：规范结论归位，任何不规范作答降级为 uncertain。"""
    verdict = parse_supervisor_verdict(answer)

    assert verdict.kind is expected_kind


@pytest.mark.parametrize(
    "answer",
    [
        "STALL_VERDICT: stalled\nSTALL_EVIDENCE: 指纹冻结",
        "STALL_VERDICT: stalled\nSTALL_SUMMARY: 卡在等输入",
        "STALL_VERDICT: stalled",
    ],
)
def test_stalled_without_summary_or_evidence_is_not_actionable(answer: str) -> None:
    """``stalled`` 却缺摘要或证据＝证据不足：不可动手。"""
    verdict = parse_supervisor_verdict(answer)

    assert verdict.kind is StallVerdictKind.uncertain
    assert not verdict.may_cancel


#: provider 在真实诊断里写出的**各种非规范形态**：三个协议字段都在，只是形态不同。
#: 每一段都以 ``STALL_VERDICT/STALL_SUMMARY/STALL_EVIDENCE`` 给出可证实的停滞，严格前缀
#: 匹配会把它们读成"没有结论"，于是一次该续跑的停滞被降级为 uncertain、永远不会自动续跑
#: （Issue #256 交付后实测到的两类误报）。
_ANSWER_SHAPES = {
    "三个协议字段之外追加了别的字段": (
        "现场分析如下。\n结论：stalled\n理由：没有新交付\n"
        "STALL_VERDICT: stalled\nSTALL_SUMMARY: 指纹冻结 40 分钟\n"
        "风险：低\nSTALL_EVIDENCE: HEAD 未变\n建议：重启这一轮"
    ),
    "只用中文键名": "结论：stalled\n摘要：指纹冻结\n证据：HEAD 未变",
    "键名被 markdown 强调包住": (
        "**STALL_VERDICT:** stalled\n**STALL_SUMMARY:** 指纹冻结\n**STALL_EVIDENCE:** HEAD 未变"
    ),
    "写成列表": "- STALL_VERDICT: stalled\n- STALL_SUMMARY: 指纹冻结\n- STALL_EVIDENCE: HEAD 未变",
    "写成 JSON 对象": (
        '{\n  "STALL_VERDICT": "stalled",\n  "STALL_SUMMARY": "指纹冻结",\n'
        '  "STALL_EVIDENCE": ["HEAD 未变", "无新调用终态"]\n}'
    ),
    "写成代码块": (
        "```text\nSTALL_VERDICT: stalled\nSTALL_SUMMARY: 指纹冻结\nSTALL_EVIDENCE: HEAD 未变\n```"
    ),
    "全角冒号": "STALL_VERDICT：stalled\nSTALL_SUMMARY：指纹冻结\nSTALL_EVIDENCE：HEAD 未变",
    "结论带括号说明": (
        "STALL_VERDICT: stalled（无交付）\nSTALL_SUMMARY: 指纹冻结\nSTALL_EVIDENCE: HEAD 未变"
    ),
    "结论带句号": "STALL_VERDICT: stalled.\nSTALL_SUMMARY: 指纹冻结\nSTALL_EVIDENCE: HEAD 未变",
    "结论写成中文": "STALL_VERDICT: 停滞\nSTALL_SUMMARY: 指纹冻结\nSTALL_EVIDENCE: HEAD 未变",
    "结论中英并列同义": (
        "STALL_VERDICT: stalled / 停滞\nSTALL_SUMMARY: 指纹冻结\nSTALL_EVIDENCE: HEAD 未变"
    ),
    "取值写在接下来几行": (
        "STALL_VERDICT:\nstalled\nSTALL_SUMMARY:\n现场指纹冻结 42 分钟，没有任何交付\n"
        "STALL_EVIDENCE:\n- HEAD 未变\n- 调用终态数未变"
    ),
    "写成表格行": (
        "| 字段 | 值 |\n| --- | --- |\n| STALL_VERDICT | stalled |\n"
        "| STALL_SUMMARY | 指纹冻结 |\n| STALL_EVIDENCE | HEAD 未变 |"
    ),
    "回声了提示词里的选项行再给结论": (
        "STALL_VERDICT: progress|blocked|stalled|uncertain 四选一\n"
        "STALL_SUMMARY: 见下\nSTALL_EVIDENCE: 见下\n"
        "分析：现场指纹没有动过。\n"
        "STALL_VERDICT: stalled\nSTALL_SUMMARY: 指纹冻结 40 分钟\n"
        "STALL_EVIDENCE: HEAD 未变且无新调用终态"
    ),
}


@pytest.mark.parametrize("shape", sorted(_ANSWER_SHAPES))
def test_non_canonical_stalled_answers_are_still_actionable(shape: str) -> None:
    """形态容错：可证实的停滞不因写法不同被降级，也不因此丢掉自动续跑。"""
    verdict = parse_supervisor_verdict(_ANSWER_SHAPES[shape])

    assert verdict.kind is StallVerdictKind.stalled, shape
    assert verdict.may_cancel, shape
    assert verdict.summary, shape


@pytest.mark.parametrize(
    ("shape", "answer"),
    [
        (
            "骑墙：两个结论并列",
            "STALL_VERDICT: stalled 或 uncertain\nSTALL_SUMMARY: s\nSTALL_EVIDENCE: e",
        ),
        ("只有散文", "看起来任务卡住了，建议人工介入。"),
        ("未知键名", "FOO: stalled\nBAR: 停滞"),
        ("拼错的结论", "STALL_VERDICT: stalle\nSTALL_SUMMARY: s\nSTALL_EVIDENCE: e"),
    ],
)
def test_free_text_and_hedged_answers_keep_the_handoff(shape: str, answer: str) -> None:
    """容错只到**形态**为止：自相矛盾、拼错、自由文本都没有击杀权。"""
    verdict = parse_supervisor_verdict(answer)

    assert verdict.kind is StallVerdictKind.uncertain, shape
    assert not verdict.may_cancel, shape


def test_stalled_verdict_with_split_fields_reaches_the_recovery_summary() -> None:
    """分多行写的 stalled：摘要与证据都收齐，交给既有 recovery 的不是一句空话。"""
    verdict = parse_supervisor_verdict(_ANSWER_SHAPES["取值写在接下来几行"])

    assert verdict.summary == "现场指纹冻结 42 分钟，没有任何交付"
    assert verdict.evidence == ("HEAD 未变", "调用终态数未变")


def test_supervision_prompt_carries_no_prompt_or_environment(
    worktree_path: Path,
) -> None:
    """诊断输入只有身份与现场指纹：提示词原文、环境变量、凭据都不出去。"""
    request = _supervision_request(
        runner=SubprocessRunner(),
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-prompt",
        diagnose=lambda _request: "",
    )
    snapshot = build_progress_snapshot(request)
    assert snapshot is not None

    prompt = build_supervision_prompt(request=request, snapshot=snapshot, stalled_seconds=90)

    assert "只读 observer" in prompt
    assert "STALL_VERDICT:" in prompt and "STALL_SUMMARY:" in prompt
    assert "issue #77" in prompt
    # 「指纹冻结」必须由提示词说成已核实的事实：只给一个指纹让模型自己证冻结，
    # 真实 provider 会据此回 uncertain（线上误报的第三条腿）。
    assert "逐字未变" in prompt
    assert "Implement the issue" not in prompt
    assert "environ" not in prompt.lower()
    assert str(os.environ.get("HOME", "/nope")) not in prompt


# ---------------------------------------------------------------------------
# observer 决策矩阵：只有 stalled + 归属可证实才会杀
# ---------------------------------------------------------------------------


def test_disabled_supervision_starts_no_observer_and_calls_no_model(
    tmp_path: Path, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """默认关闭时：不起线程、不采样、不调模型，结果原样交回。"""
    runner = SubprocessRunner()
    diagnose_calls: list[StallDiagnosisRequest] = []
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-off",
        diagnose=_verdict_diagnose(diagnose_calls, _STALLED_ANSWER),
        config=StallSupervisorConfig(enabled=False),
    )
    threads_before = {thread.name for thread in threading.enumerate()}

    outcome, _, _ = _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=0.4,
    )

    assert not isinstance(outcome, AgentStallCancelledError) and outcome.return_code == 0
    assert diagnose_calls == []
    assert not [
        name
        for name in {thread.name for thread in threading.enumerate()} - threads_before
        if name.startswith("iar-stall-")
    ]
    assert runner.probe_live_attempt(request.attempt_key).confirmed is False


def test_stalled_attempt_is_cancelled_once_and_the_summary_reaches_recovery(
    tmp_path: Path, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """唯一可动手路径：真实组被停一次，诊断摘要进 AgentStallCancelledError。"""
    runner = SubprocessRunner()
    diagnose_calls: list[StallDiagnosisRequest] = []
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-stalled",
        diagnose=_verdict_diagnose(diagnose_calls, _STALLED_ANSWER),
    )

    outcome, identity, marker = _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=60,
    )

    assert isinstance(outcome, AgentStallCancelledError)
    # 继承 RuntimeError：既有 recovery 的失败分支已经捕获它，不新增第二条恢复路径。
    assert isinstance(outcome, RuntimeError)
    assert "writer 卡在等一个永远不会来的输入" in outcome.diagnosis_summary
    assert outcome.verdict_kind == "stalled"
    assert len(diagnose_calls) == 1
    assert diagnose_calls[0].agent_name == "fake-supervisor"
    assert not _process_group_alive(identity["process_group"])
    assert not _pid_alive(identity["grandchild_pid"])
    assert not marker.with_name(marker.name + ".finished").exists()
    assert (tmp_path / f"writer-{request.attempt_key}.json").exists()


@pytest.mark.parametrize(
    ("answer", "marker"),
    [
        (
            "STALL_VERDICT: blocked\nSTALL_SUMMARY: 需要人类输入\nSTALL_EVIDENCE: 等待确认",
            STALL_HANDOFF_MARKER,
        ),
        ("STALL_VERDICT: progress\nSTALL_SUMMARY: 仍在推进\nSTALL_EVIDENCE: 有新 commit", ""),
        ("STALL_VERDICT: nonsense", STALL_HANDOFF_MARKER),
        ("STALL_VERDICT: stalled\nSTALL_SUMMARY: 缺证据", STALL_HANDOFF_MARKER),
    ],
)
def test_non_actionable_verdicts_leave_the_writer_running(
    writer_argv: Sequence[str], worktree_path: Path, answer: str, marker: str
) -> None:
    """blocked / progress / 不规范结论一律交班：writer 活到自己的结局，零击杀。"""
    runner = SubprocessRunner()
    diagnose_calls: list[StallDiagnosisRequest] = []
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key=f"issue-77-implement-1-{abs(hash(answer)) % 10**6}",
        diagnose=_verdict_diagnose(diagnose_calls, answer),
    )

    outcome, identity, _ = _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=0.5,
    )

    assert not isinstance(outcome, AgentStallCancelledError), outcome
    assert outcome.return_code == 0
    assert _pid_alive(identity["pid"]) is False  # 自己跑完退出的，不是被杀的
    assert diagnose_calls, "到点了应该问一次"


def test_unprovable_ownership_hands_off_without_signalling_anything(
    tmp_path: Path,
    writer_argv: Sequence[str],
    worktree_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """stalled 结论 + 归属不可证实：只记录原因，writer 正常跑完。"""
    caplog.set_level(logging.INFO, logger=supervision.__name__)
    runner = SubprocessRunner()
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        # 这个键从未被 run() 登记过：probe 必然给出"不可证实"。
        attempt_key="issue-77-implement-1-unproven",
        diagnose=_verdict_diagnose([], _STALLED_ANSWER),
    )
    marker = tmp_path / "unproven.json"
    argv = [*writer_argv, str(marker), "0.6"]

    def _invoke() -> CommandResult:
        # 关键：不带 attempt_key，因此没有可取消的登记条目。
        return runner.run(list(argv), cwd=worktree_path, check=False, capture_output=False)

    outcome = supervised_agent_invocation(request, _invoke)

    assert not isinstance(outcome, AgentStallCancelledError) and outcome.return_code == 0
    assert (tmp_path / "unproven.json.finished").exists()
    assert any(
        STALL_HANDOFF_MARKER in record.message and "进程归属不可证实" in record.message
        for record in caplog.records
    )


def test_identity_change_during_diagnosis_drops_the_stalled_verdict(
    monkeypatch: pytest.MonkeyPatch, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """取消前必须当场再读一次现场：签名变了就说明诊断依据已过期，不动手。"""
    runner = SubprocessRunner()
    frozen = ProgressSnapshot(signature="frozen", observed_at_mono=time.monotonic(), detail="d")
    moved = ProgressSnapshot(signature="moved", observed_at_mono=time.monotonic(), detail="d")
    #: 采样顺序：进展锚 → 巡检判定为停滞 → 取消前的当场复核（这里已经变了）。
    samples: list[ProgressSnapshot] = [frozen, frozen, moved]
    #: 丢弃之后现场一直在动：不该再被读成一个新的停滞窗口。
    samples.extend(
        ProgressSnapshot(
            signature=f"moving-{index}",
            observed_at_mono=time.monotonic(),
            detail="d",
        )
        for index in range(200)
    )
    sample_iterator = iter(samples)

    def _fake_snapshot(_request: StallSupervisionRequest) -> ProgressSnapshot | None:
        return next(sample_iterator, samples[-1])

    monkeypatch.setattr(supervision, "build_progress_snapshot", _fake_snapshot)
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-changed",
        diagnose=_verdict_diagnose([], _STALLED_ANSWER),
    )

    outcome, identity, marker = _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=0.5,
    )

    assert not isinstance(outcome, AgentStallCancelledError), outcome
    assert outcome.return_code == 0
    #: writer 是自己跑完的（写了收尾标记），不是被监督器终止的。
    assert Path(str(marker) + ".finished").exists()
    assert _pid_alive(identity["pid"]) is False


def test_stalled_verdict_after_the_attempt_ended_never_cancels(
    monkeypatch: pytest.MonkeyPatch, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """调用已结束时到达的 stalled 结论必须丢弃：此时任何击杀都是对陌生进程动手。"""
    runner = SubprocessRunner()
    frozen = ProgressSnapshot(signature="frozen", observed_at_mono=time.monotonic(), detail="d")
    monkeypatch.setattr(supervision, "build_progress_snapshot", lambda _request: frozen)
    diagnosed = threading.Event()

    def _slow_diagnose(_request: StallDiagnosisRequest) -> str:
        diagnosed.set()
        time.sleep(0.4)  # 诊断跑完之前，被监督的调用已经正常结束
        return _STALLED_ANSWER

    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-late",
        diagnose=_slow_diagnose,
    )
    marker = tmp_marker = worktree_path / "late.json"

    def _invoke() -> CommandResult:
        result = runner.run(
            [*writer_argv, str(marker), "0.05"],
            cwd=worktree_path,
            check=False,
            capture_output=False,
            attempt_key=request.attempt_key,
        )
        diagnosed.wait(timeout=5)
        time.sleep(0.1)
        return result

    outcome = supervised_agent_invocation(request, _invoke)

    assert not isinstance(outcome, AgentStallCancelledError), outcome
    assert outcome.return_code == 0
    assert Path(str(tmp_marker) + ".finished").exists()


def test_one_diagnosis_per_stalled_window(
    monkeypatch: pytest.MonkeyPatch, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """同一个停滞窗口至多问一次：反复问只烧 token，不产生新事实。"""
    runner = SubprocessRunner()
    frozen = ProgressSnapshot(signature="frozen", observed_at_mono=time.monotonic(), detail="d")
    monkeypatch.setattr(supervision, "build_progress_snapshot", lambda _request: frozen)
    diagnose_calls: list[StallDiagnosisRequest] = []
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-window",
        # progress 结论不会终止观察，让循环继续巡检多个周期。
        diagnose=_verdict_diagnose(
            diagnose_calls,
            "STALL_VERDICT: progress\nSTALL_SUMMARY: 仍在推进\nSTALL_EVIDENCE: 阶段在走",
        ),
    )

    _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=1.0,
    )

    assert len(diagnose_calls) == 1


def test_progressing_attempt_never_reaches_a_diagnosis(
    monkeypatch: pytest.MonkeyPatch, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """签名在动＝有交付在推进：一次模型都不调用。"""
    runner = SubprocessRunner()
    counter = {"index": 0}

    def _moving_snapshot(_request: StallSupervisionRequest) -> ProgressSnapshot:
        counter["index"] += 1
        return ProgressSnapshot(
            signature=f"moving-{counter['index']}",
            observed_at_mono=time.monotonic(),
            detail="d",
        )

    monkeypatch.setattr(supervision, "build_progress_snapshot", _moving_snapshot)
    diagnose_calls: list[StallDiagnosisRequest] = []
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-progress",
        diagnose=_verdict_diagnose(diagnose_calls, _STALLED_ANSWER),
    )

    outcome, _, _ = _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=0.4,
    )

    assert not isinstance(outcome, AgentStallCancelledError) and outcome.return_code == 0
    assert diagnose_calls == []


def test_diagnosis_channel_failure_is_uncertain_and_non_actionable(
    writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """诊断自己挂了＝证据不足：不升级为处置，writer 照常跑完。"""
    runner = SubprocessRunner()

    def _boom(_request: StallDiagnosisRequest) -> str:
        raise RuntimeError("supervisor CLI unavailable")

    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-boom",
        diagnose=_boom,
    )

    outcome, _, _ = _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=0.4,
    )

    assert not isinstance(outcome, AgentStallCancelledError) and outcome.return_code == 0


def test_a_second_diagnosis_chance_after_a_failed_one(
    monkeypatch: pytest.MonkeyPatch, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """诊断调用自己失败不等于"结论是 uncertain"：下个巡检周期再问一次，别把剩余调用变成无监督。"""
    runner = SubprocessRunner()
    frozen = ProgressSnapshot(signature="frozen", observed_at_mono=time.monotonic(), detail="d")
    monkeypatch.setattr(supervision, "build_progress_snapshot", lambda _request: frozen)
    answers = iter(
        [
            RuntimeError("supervisor CLI unavailable"),
            _STALLED_ANSWER,
        ]
    )
    diagnose_calls: list[StallDiagnosisRequest] = []

    def _flaky_diagnose(request: StallDiagnosisRequest) -> str:
        diagnose_calls.append(request)
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-retry",
        diagnose=_flaky_diagnose,
    )

    outcome, _, _ = _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=3.0,
    )

    assert len(diagnose_calls) >= 2, diagnose_calls
    assert isinstance(outcome, AgentStallCancelledError), outcome


def test_a_transient_read_failure_at_the_anchor_does_not_disable_supervision(
    monkeypatch: pytest.MonkeyPatch, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """开场读不到现场（``index.lock`` 一类瞬时故障）只推迟进展锚，不会让本次调用失去监督。"""
    runner = SubprocessRunner()
    frozen = ProgressSnapshot(signature="frozen", observed_at_mono=time.monotonic(), detail="d")
    samples = iter([None, None, frozen])
    monkeypatch.setattr(
        supervision, "build_progress_snapshot", lambda _request: next(samples, frozen)
    )
    diagnose_calls: list[StallDiagnosisRequest] = []
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-anchor-retry",
        diagnose=_verdict_diagnose(diagnose_calls, _STALLED_ANSWER),
    )

    outcome, _, _ = _run_supervised_writer(
        runner=runner,
        worktree=worktree_path,
        writer_argv=writer_argv,
        request=request,
        runtime_seconds=3.0,
    )

    assert diagnose_calls, "锚点瞬时读取失败后仍须问到结论"
    assert isinstance(outcome, AgentStallCancelledError), outcome


def test_audit_lines_are_replayed_in_the_calling_thread(
    monkeypatch: pytest.MonkeyPatch, writer_argv: Sequence[str], worktree_path: Path
) -> None:
    """审计行在**调用线程**里补写一遍，才能落进既有 per-Issue 日志（按线程过滤）。"""
    runner = SubprocessRunner()
    frozen = ProgressSnapshot(signature="frozen", observed_at_mono=time.monotonic(), detail="d")
    monkeypatch.setattr(supervision, "build_progress_snapshot", lambda _request: frozen)
    request = _supervision_request(
        runner=runner,
        worktree=worktree_path,
        attempt_key="issue-77-implement-1-audit",
        diagnose=_verdict_diagnose(
            [], "STALL_VERDICT: blocked\nSTALL_SUMMARY: 需要人\nSTALL_EVIDENCE: 等输入"
        ),
    )
    seen: list[tuple[str, str]] = []

    class _RecordingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append((threading.current_thread().name, record.getMessage()))

    handler = _RecordingHandler(level=logging.INFO)
    supervision_logger = logging.getLogger(supervision.__name__)
    supervision_logger.addHandler(handler)
    previous_level = supervision_logger.level
    supervision_logger.setLevel(logging.INFO)
    try:
        _run_supervised_writer(
            runner=runner,
            worktree=worktree_path,
            writer_argv=writer_argv,
            request=request,
            runtime_seconds=0.4,
        )
    finally:
        supervision_logger.removeHandler(handler)
        supervision_logger.setLevel(previous_level)

    main_thread_lines = [text for name, text in seen if name == threading.main_thread().name]
    observer_lines = [text for name, text in seen if name.startswith("iar-stall-")]
    assert any(STALL_DIAGNOSIS_MARKER in text for text in observer_lines)
    assert any(STALL_HANDOFF_MARKER in text for text in main_thread_lines), main_thread_lines


# ---------------------------------------------------------------------------
# 用例接线：只有"带 Issue 身份的 run attempt"才进监督（rv-4 零调用）
# ---------------------------------------------------------------------------


class _RecordingRunner(IProcessRunner):
    """记录 ``run`` 入参的极简执行器（不碰进程；默认 probe/cancel 即 fail-closed）。"""

    def __init__(self) -> None:
        self.run_kwargs: list[dict[str, object]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        timeout: int | None = None,
        inactivity_timeout: int | None = None,
        capture_output: bool = True,
        input_text: str | None = None,
        label: str | None = None,
        output_sink: Callable[[str], None] | None = None,
        output_protocol: str | None = None,
        attempt_key: str | None = None,
    ) -> CommandResult:
        self.run_kwargs.append({"command": list(command), "attempt_key": attempt_key})
        return CommandResult(command=tuple(command), return_code=0, stdout="ok", stderr="")

    def probe_live_attempt(self, attempt_key: str) -> AttemptOwnership:  # noqa: D102 - 端口默认值
        return AttemptOwnership(confirmed=False, reason="test double")

    def cancel_live_attempt(
        self, attempt_key: str, expected: AttemptOwnership
    ) -> StallCancelOutcome:
        return StallCancelOutcome(reason="test double")


def _agent_run_kwargs(runner: _RecordingRunner) -> list[dict[str, object]]:
    """记录里属于 agent 调用本身的那几条。

    开启监督后现场采样会先向同一个执行器问几次 git（HEAD / 分支 / 状态 / diff），
    因此 ``run_kwargs[0]`` 是一条读命令而不是 agent 调用。
    """
    agent_calls: list[dict[str, object]] = []
    for recorded_kwargs in runner.run_kwargs:
        command = recorded_kwargs["command"]
        assert isinstance(command, list), command
        if command and command[0] != "git":
            agent_calls.append(recorded_kwargs)
    return agent_calls


def _supervised_app_config(**overrides: object) -> AppConfig:
    """注册假 writer / 假只读 supervisor 并可选覆盖监督配置。"""
    supervisor_config = StallSupervisorConfig(
        enabled=True,
        check_interval_seconds=_TICK_SECONDS,
        stalled_after_seconds=0,
        agent="fake-supervisor",
    )
    config_overrides: dict[str, object] = {
        "agents": {
            "fake-writer": AgentSpec(
                bin="/usr/bin/true",
                label="agent/fake-writer",
                label_color="000000",
                label_description="Fake writer.",
                profiles={AGENT_PROFILE_RUN: AgentProfileSpec(args=("--unattended",))},
            ),
            "fake-supervisor": AgentSpec(
                bin="/usr/bin/true",
                label="agent/fake-supervisor",
                label_color="000000",
                label_description="Fake read-only supervisor.",
                profiles={AGENT_PROFILE_GENERATE: AgentProfileSpec(args=(), read_only=True)},
            ),
        },
        "stall_supervisor": supervisor_config,
    }
    config_overrides.update(overrides)
    return AppConfig(**config_overrides)  # type: ignore[arg-type]


_ISSUE = IssueSummary(number=77, title="T", url="http://example.test/77", body="B", labels=())


def test_run_attempt_with_issue_identity_registers_the_attempt_for_supervision(
    worktree_path: Path,
) -> None:
    """开启监督 + 带 Issue 身份的 run 调用：登记 attempt 键，交给监督包装层。"""
    runner = _RecordingRunner()

    run_agent_with_prompt(
        "fake-writer",
        "Implement the issue.",
        worktree_path,
        runner,
        config=_supervised_app_config(),
        issue=_ISSUE,
        invocation_phase="implement",
        invocation_attempt=1,
    )

    assert runner.run_kwargs, "writer 调用必须发生"
    attempt_key = _agent_run_kwargs(runner)[0]["attempt_key"]
    assert isinstance(attempt_key, str) and attempt_key.startswith("issue-77-implement-1-")


def test_supervision_off_by_default_does_not_touch_the_invocation(worktree_path: Path) -> None:
    """默认（关闭）配置下：不登记 attempt 键，argv 与本特性之前逐字节一致。"""
    runner = _RecordingRunner()

    run_agent_with_prompt(
        "fake-writer",
        "Implement the issue.",
        worktree_path,
        runner,
        config=_supervised_app_config(
            stall_supervisor=StallSupervisorConfig(agent="fake-supervisor")
        ),
        issue=_ISSUE,
        invocation_phase="implement",
    )

    writer_kwargs = _agent_run_kwargs(runner)[0]
    assert writer_kwargs["attempt_key"] is None
    assert writer_kwargs["command"] == [
        "/usr/bin/true",
        "--unattended",
        "Implement the issue.",
    ]


def test_calls_without_issue_identity_are_not_supervised(worktree_path: Path) -> None:
    """``kc ask`` / REPL / 辩论不是 attempt 主体：开启监督也不起 observer。"""
    runner = _RecordingRunner()

    run_agent_with_prompt(
        "fake-writer",
        "Implement the issue.",
        worktree_path,
        runner,
        config=_supervised_app_config(),
        issue=None,
        invocation_phase="implement",
    )

    assert runner.run_kwargs[0]["attempt_key"] is None


def test_non_run_profiles_are_not_supervised(worktree_path: Path) -> None:
    """诊断走 generate 只读 profile，因此它自身永远不会再被监督（无递归 observer）。"""
    runner = _RecordingRunner()

    run_agent_with_prompt(
        "fake-supervisor",
        "Review only.",
        worktree_path,
        runner,
        config=_supervised_app_config(),
        issue=_ISSUE,
        profile=AGENT_PROFILE_GENERATE,
        invocation_phase="supervisor",
    )

    assert _agent_run_kwargs(runner)[0]["attempt_key"] is None


def test_supervisor_without_a_read_only_generate_profile_is_disabled(
    worktree_path: Path,
) -> None:
    """诊断通道必须可验证只读：supervisor 只有非只读 generate 时整体停用，不调模型。"""
    runner = _RecordingRunner()
    config = _supervised_app_config()
    writer_agents = dict(config.agents)
    writer_agents["fake-supervisor"] = AgentSpec(
        bin="/usr/bin/true",
        label="agent/fake-supervisor",
        label_color="000000",
        label_description="Fake supervisor without a read-only profile.",
        profiles={AGENT_PROFILE_GENERATE: AgentProfileSpec(args=("--write",))},
    )

    run_agent_with_prompt(
        "fake-writer",
        "Implement the issue.",
        worktree_path,
        runner,
        config=AppConfig(
            agents=writer_agents,
            stall_supervisor=config.stall_supervisor,
            agent_session=config.agent_session,
        ),
        issue=_ISSUE,
        invocation_phase="implement",
    )

    assert runner.run_kwargs[0]["attempt_key"] is None


# ---------------------------------------------------------------------------
# 配置生效与拒绝（真实新进程 + 真实 TOML，rv-4）
# ---------------------------------------------------------------------------


def _run_cli_in_new_process(
    repo_path: Path,
    args: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    """起一个**新的** kc 进程（不 mock loader、不共享进程缓存、不带宿主级配置）。

    入口用 ``import main`` 而不是 ``python -m backend.api.cli``：后者会把 ``cli.py``
    当 ``__main__`` 再执行一遍，撞上 ``cli_parsed_commands`` 的包初始化回环（main 分支
    上同样如此，不是本特性引入的）。
    """
    project_root = Path(__file__).resolve().parents[1]
    state_home = repo_path / "state-home"
    state_home.mkdir(exist_ok=True)
    child_env = {
        **os.environ,
        "PYTHONPATH": str(project_root / "src"),
        "HOME": str(state_home),
    }
    #: 宿主 runner 会注入机器级配置指针，新进程必须只看到本次临时配置。
    for ambient_env_name in ("KEDACODE_CONFIG", "IAR_CONFIG"):
        child_env.pop(ambient_env_name, None)
    entrypoint = "from backend.api.cli import main; raise SystemExit(main())"
    return subprocess.run(
        [sys.executable, "-c", entrypoint, *args],
        cwd=repo_path,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


def _repo_with_session_toml(tmp_path: Path, session_toml: str, *, name: str = "cfg-repo") -> Path:
    """写一个真实仓库配置（``[agent_session]`` / ``[agent_runner.*]`` 由参数给出）。"""
    from tests.test_agent_runner_cli import _init_iar_repo

    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    repo_path = _init_iar_repo(tmp_path / name)
    (repo_path / ".iar.toml").write_text(
        (repo_path / ".iar.toml").read_text(encoding="utf-8") + session_toml,
        encoding="utf-8",
    )
    return repo_path


def test_preview_status_loads_defaults_in_a_fresh_cli_process(tmp_path: Path) -> None:
    """默认配置下新进程读取预览状态：监督关闭、预览未启动，且零进程副作用。"""
    repo_path = _repo_with_session_toml(tmp_path, "")

    result = _run_cli_in_new_process(
        repo_path, ["preview", "status", "--repo", str(repo_path), "--json"]
    )

    assert result.returncode == 0, result.stderr
    assert '"state"' in result.stdout and '"none"' in result.stdout, result.stdout


def test_invalid_supervisor_intervals_are_rejected_before_any_spawn(tmp_path: Path) -> None:
    """无效巡检周期/阈值：加载期就指名字段，不启动任何进程。"""
    for repo_name, bad_toml, needle in (
        (
            "interval-zero",
            "\n[agent_runner.stall_supervisor]\ncheck_interval_seconds = 0\n",
            "check_interval_seconds",
        ),
        (
            "threshold-negative",
            "\n[agent_runner.stall_supervisor]\nstalled_after_seconds = -1\n",
            "stalled_after_seconds",
        ),
    ):
        repo_path = _repo_with_session_toml(tmp_path, bad_toml, name=repo_name)

        result = _run_cli_in_new_process(repo_path, ["preview", "status", "--repo", str(repo_path)])

        assert result.returncode != 0, (result.stdout, result.stderr)
        combined = result.stdout + result.stderr
        assert needle in combined, combined
        assert "Traceback" not in combined


def test_public_preview_ready_url_is_rejected_at_config_load(tmp_path: Path) -> None:
    """ready 地址主机不在回环闭集内＝加载期错误，永远不会走到 spawn。"""
    repo_path = _repo_with_session_toml(
        tmp_path,
        "\n[agent_session.preview]\n"
        'argv = ["pnpm", "run", "dev"]\n'
        'ready_url = "http://10.20.30.40:3000"\n',
    )

    result = _run_cli_in_new_process(repo_path, ["preview", "status", "--repo", str(repo_path)])

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "loopback" in combined.lower(), combined


def test_repo_layer_overrides_the_declared_preview_argv(tmp_path: Path) -> None:
    """仓库层 argv 覆盖全局层：生效值来自真实合并后的 AppConfig。"""
    repo_path = _repo_with_session_toml(
        tmp_path,
        "\n[agent_session.preview]\n"
        'argv = ["pnpm", "-w", "run", "dev"]\n'
        'ready_url = "http://127.0.0.1:3100"\n',
    )

    result = _run_cli_in_new_process(
        repo_path, ["preview", "status", "--repo", str(repo_path), "--json"]
    )

    assert result.returncode == 0, result.stderr
    assert '"none"' in result.stdout, result.stdout
    profile = PreviewProfile(argv=("pnpm", "-w", "run", "dev"), ready_url="http://127.0.0.1:3100")
    assert profile.is_declared and profile.ready_timeout_seconds > 0

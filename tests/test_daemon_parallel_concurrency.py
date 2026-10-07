"""Tests for parallel daemon execution plumbing: output routing and live views.

Covers the per-Issue output-routing layer (``agent_runner_output_routing``), the
process runner's ``output_sink`` forwarding, and the runner live views. The
end-to-end ``run_once`` parallel behavior is tested in
``test_agent_runner_orchestrate``.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

from backend.core.shared.interfaces.issue_log_reader import IssueLogStatus
from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.shared.models.agent_spec import CLAUDE_STREAM_JSON_PROTOCOL_ID
from backend.core.use_cases.agent_runner_output_routing import (
    _OutputRoutedProcessRunner,
    issue_output_routing,
    per_issue_log_path,
)
from backend.core.use_cases.issue_logs import ATTEMPT_END_MARKER, read_issue_log
from backend.api.agent_runner_views.runner_live_view import (
    PlainRunnerLiveView,
    create_runner_live_view,
)
from backend.infrastructure import process_runner as process_runner_module
from backend.infrastructure.console.issue_log_reader import FilesystemIssueLogReader
from backend.infrastructure.process_runner import SubprocessRunner
from tests.conftest import FakeProcessRunner

#: per-Issue 实时输出的行首时间戳（Issue #223）：``[HH:MM:SS] `` + 原文。
_LINE_TIMESTAMP_PATTERN = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")


def _timestamped_agent_lines(log_text: str) -> list[str]:
    """取出日志中带行首时间戳的行并去掉前缀，便于断言原始 agent 文本。

    worker 叙述行经 logging handler 写入，自带完整日期时间而不带该前缀，
    所以这个筛选只保留 agent 流式输出行——正是 Issue #223 要求可对时的部分。
    """
    return [
        _LINE_TIMESTAMP_PATTERN.sub("", line)
        for line in log_text.splitlines()
        if _LINE_TIMESTAMP_PATTERN.match(line)
    ]


class _RecordingView(NoOpRunnerLiveView):
    """Live view that records the chunks appended per Issue."""

    def __init__(self) -> None:
        self.appended: list[tuple[int, str]] = []
        self.statuses: list[tuple[int, str]] = []

    def append(self, issue_number: int, chunk: str) -> None:
        self.appended.append((issue_number, chunk))

    def update_status(self, issue_number: int, status: str) -> None:
        self.statuses.append((issue_number, status))


# --- SubprocessRunner.output_sink forwarding -------------------------------


def test_subprocess_runner_routes_streamed_output_to_sink(tmp_path: Path) -> None:
    """A non-captured command's stdout is routed to output_sink, not printed."""
    chunks: list[str] = []
    SubprocessRunner().run(
        [sys.executable, "-c", "print('hello-sink')"],
        cwd=tmp_path,
        check=False,
        capture_output=False,
        output_sink=chunks.append,
    )
    assert any("hello-sink" in chunk for chunk in chunks)


def test_subprocess_runner_forwards_output_sink_to_claude_stream(
    monkeypatch, tmp_path: Path
) -> None:
    """claude-stream-json 协议把 sink 转发给 run_filtered_claude_stream。"""
    captured: dict[str, object] = {}

    def _fake_stream(command, **kwargs):
        captured["output_sink"] = kwargs.get("output_sink")
        return subprocess.CompletedProcess(args=list(command), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(process_runner_module, "run_filtered_claude_stream", _fake_stream)

    def sink(_chunk: str) -> None:
        return None

    SubprocessRunner().run(
        ["claude", "--output-format", "stream-json", "-p", "hi"],
        cwd=tmp_path,
        check=False,
        capture_output=False,
        output_sink=sink,
        # 路由由调用方声明的协议驱动，不再嗅探命令行内容。
        output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
    )
    assert captured["output_sink"] is sink


# --- _OutputRoutedProcessRunner --------------------------------------------


def test_output_routed_runner_injects_default_sink(tmp_path: Path) -> None:
    """The wrapper defaults output_sink to the Issue's sink."""
    base = FakeProcessRunner()

    def issue_sink(_chunk: str) -> None:
        return None

    wrapped = _OutputRoutedProcessRunner(base, issue_sink)
    wrapped.run(["git", "status"], cwd=tmp_path)
    assert base.output_sinks[-1] is issue_sink


def test_output_routed_runner_respects_explicit_sink(tmp_path: Path) -> None:
    """An explicit output_sink overrides the wrapper's default."""
    base = FakeProcessRunner()
    wrapped = _OutputRoutedProcessRunner(base, lambda _c: None)

    def explicit_sink(_chunk: str) -> None:
        return None

    wrapped.run(["git", "status"], cwd=tmp_path, output_sink=explicit_sink)
    assert base.output_sinks[-1] is explicit_sink


def test_output_routed_runner_forwards_full_run_contract(tmp_path: Path) -> None:
    """The wrapper forwards every ``IProcessRunner.run`` keyword, not just sink.

    串行与并行路径都把包装器注入全部下游调用；少转发 ``input_text`` 或
    ``output_protocol`` 会在精简签名的运行器上炸 TypeError，或静默丢掉
    协议路由。
    """
    base = FakeProcessRunner()
    wrapped = _OutputRoutedProcessRunner(base, lambda _c: None)

    wrapped.run(
        ["git", "mktree"],
        cwd=tmp_path,
        check=False,
        timeout=5,
        inactivity_timeout=3,
        capture_output=True,
        input_text="tree entries\n",
        label="mktree",
        output_protocol="plain",
    )
    assert base.input_texts[-1] == "tree entries\n"
    assert base.timeouts[-1] == 5
    assert base.inactivity_timeouts[-1] == 3
    assert base.labels[-1] == "mktree"


def test_output_routed_runner_tolerates_partial_fake_signature(tmp_path: Path) -> None:
    """精简签名的运行器不认识的参数必须被过滤，而不是炸 TypeError。

    测试 fake / 自定义 runner 常只声明 ``cwd`` 等少数字段；包装器按
    ``inspect.signature`` 过滤后再委托。
    """

    class _MinimalRunner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def run(self, command, *, cwd, check=True, capture_output=True, label=None):
            self.calls.append(list(command))
            return CommandResult(tuple(command), 0, "", "")

    base = _MinimalRunner()
    wrapped = _OutputRoutedProcessRunner(base, lambda _c: None)
    result = wrapped.run(
        ["git", "status"],
        cwd=tmp_path,
        timeout=5,
        input_text="ignored\n",
        output_protocol="plain",
    )
    assert result.return_code == 0
    assert base.calls == [["git", "status"]]


# --- issue_output_routing ---------------------------------------------------


def test_issue_output_routing_writes_file_and_view(tmp_path: Path) -> None:
    """The sink writes each chunk, line-timestamped, to the Issue file and view."""
    view = _RecordingView()
    with issue_output_routing(
        repo_id="repo", issue_number=7, log_base=tmp_path, output_view=view
    ) as sink:
        sink("agent says hi\n")

    log_files = list((tmp_path / "agent-runner" / "issues" / "repo").glob("issue-7-*.log"))
    assert len(log_files) == 1
    assert "agent says hi" in log_files[0].read_text(encoding="utf-8")
    assert len(view.appended) == 1
    issue_number, appended_chunk = view.appended[0]
    assert issue_number == 7
    assert _LINE_TIMESTAMP_PATTERN.match(appended_chunk)
    assert appended_chunk.endswith("agent says hi\n")


def test_issue_output_routing_serial_mirror_writes_file_and_console(tmp_path: Path, capsys) -> None:
    """串行路径的 sink 同时落盘，并把同一份带行首时间戳的文本镜像回原终端。

    这覆盖「单次 ``iar run`` 之后，第二终端仍能按 Issue 找到输出」的核心
    机制：文件与原 stdout 必须同时有内容、逐行一致且互不替代（Issue #223
    后前台与 per-Issue 日志共用 sink，因此也共用时间戳）。
    """
    mirrored: list[str] = []
    with issue_output_routing(
        repo_id="repo",
        issue_number=11,
        log_base=tmp_path,
        output_view=NoOpRunnerLiveView(),
        console_sink=mirrored.append,
    ) as sink:
        sink("agent progress line\n")

    log_files = list((tmp_path / "agent-runner" / "issues" / "repo").glob("issue-11-*.log"))
    assert len(log_files) == 1
    log_text = log_files[0].read_text(encoding="utf-8")
    assert "agent progress line" in log_text
    assert len(mirrored) == 1
    assert _LINE_TIMESTAMP_PATTERN.match(mirrored[0])
    # 前台镜像与落盘内容是同一份文本：Console 读到的行与操作者看到的是行的。
    assert mirrored[0] in log_text


def test_issue_output_routing_captures_worker_thread_logs(tmp_path: Path) -> None:
    """backend.* log records from the worker thread land in the Issue's file."""
    logger = logging.getLogger("backend.test_parallel_routing")
    logger.setLevel(logging.INFO)
    with issue_output_routing(
        repo_id="r", issue_number=9, log_base=tmp_path, output_view=NoOpRunnerLiveView()
    ):
        logger.info("worker-thread-line-xyz")

    log_file = next((tmp_path / "agent-runner" / "issues" / "r").glob("issue-9-*.log"))
    assert "worker-thread-line-xyz" in log_file.read_text(encoding="utf-8")


def test_issue_output_routing_end_to_end_log_file_timestamps_agent_lines(
    tmp_path: Path,
) -> None:
    """端到端（Issue #223）：真实 runner → 真实路由 → 磁盘日志 → Console 读取端口。

    其余路由用例直接喂 sink 裸 chunk，绕过了生产者；这条钉住运营者与 Console
    实际读到的那份产物：agent 流式输出的每个物理行在
    ``issue-<N>-<ts>.log`` 里行首带 ``[HH:MM:SS]``，而 ``[iar-attempt-end]``
    终态标记保持裸行——``iar logs --issue --follow`` 靠精确匹配它判断运行
    结束，被前缀污染就会一路跟到空闲兜底才退出。
    """
    repo_root = tmp_path / "repo"
    log_base = repo_root / "logs"
    repo_root.mkdir(parents=True, exist_ok=True)
    child_script = "print('[agent tool] Bash: ls')\nprint('answer line')\n"

    with issue_output_routing(
        repo_id="fixture-repo",
        issue_number=42,
        log_base=log_base,
        output_view=NoOpRunnerLiveView(),
    ) as sink:
        routed_runner = _OutputRoutedProcessRunner(SubprocessRunner(), sink)
        routed_runner.run(
            [sys.executable, "-c", child_script],
            cwd=repo_root,
            capture_output=False,
            check=False,
        )

    log_file = next(log_base.glob("agent-runner/issues/fixture-repo/issue-42-*.log"))
    log_text = log_file.read_text(encoding="utf-8")
    assert _timestamped_agent_lines(log_text) == [
        "[agent tool] Bash: ls",
        "answer line",
    ]
    marker_lines = [line for line in log_text.splitlines() if ATTEMPT_END_MARKER in line]
    assert marker_lines == [ATTEMPT_END_MARKER]

    # Console / CLI 走的读取端口：读取端不解析前缀，逐字把带时间线的文本交给前端。
    reader = FilesystemIssueLogReader(
        lambda repo_id: repo_root if repo_id == "fixture-repo" else None
    )
    selection = read_issue_log(reader=reader, repo_id="fixture-repo", issue_number=42)
    assert selection.status is IssueLogStatus.OK
    assert _timestamped_agent_lines(selection.content) == [
        "[agent tool] Bash: ls",
        "answer line",
    ]
    assert ATTEMPT_END_MARKER in selection.content


def test_per_issue_log_path_layout(tmp_path: Path) -> None:
    """The per-Issue log path follows the agreed layout."""
    path = per_issue_log_path(tmp_path, "my-repo", 42)
    assert path.parent == tmp_path / "agent-runner" / "issues" / "my-repo"
    assert path.name.startswith("issue-42-")
    assert path.suffix == ".log"


# --- runner live views ------------------------------------------------------


def test_create_runner_live_view_non_tty_returns_plain() -> None:
    """Outside an interactive TTY (pytest), the factory returns the plain view."""
    assert isinstance(create_runner_live_view(), PlainRunnerLiveView)
    assert isinstance(create_runner_live_view(plain=True), PlainRunnerLiveView)


def test_noop_runner_live_view_is_inert() -> None:
    """The no-op view accepts every call without error."""
    view = NoOpRunnerLiveView()
    view.register_issue(1, "claude")
    view.append(1, "x")
    view.update_status(1, "completed")
    view.log("pass-level")
    view.close()


def test_plain_runner_live_view_prefixes_output(capsys) -> None:
    """The plain view prefixes each line with its Issue number."""
    view = PlainRunnerLiveView()
    view.register_issue(5, "claude")
    view.append(5, "line one\n")
    view.update_status(5, "completed")
    view.close()
    out = capsys.readouterr().out
    assert "[issue #5" in out
    assert "line one" in out
    assert "status=completed" in out

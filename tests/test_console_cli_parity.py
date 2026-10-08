"""Console 网页补齐 CLI 操作能力（Issue #247）核心用例与契约测试。

覆盖 PRD P1-FEAT-20261008-165241 中可被机器断言的验收点：

- FR-3 加入就绪：建 Issue / 打标签但**绝不启动 runner**；已就绪幂等；执行中冲突拒绝。
- FR-4 全部 Issue 列表：按队列标签口径标注 ``monitored``，state 参数校验。
- FR-5 标签编辑：越界集合在写入前被拒（GitHub 零变化），合法写入以 fresh read 返回。
- FR-7 启动选项：缺省选项与「无选项」的 argv 逐字节一致，非法组合被拒。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.core.shared.interfaces.runner_console import (
    IRunnerProcessSupervisor,
    RunnerProcessKind,
    RunnerProcessRecord,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    RepositoryRunContext,
)
from backend.core.shared.models.backlog import BacklogPrdState
from backend.core.shared.models.runner_launch import (
    RunnerLaunchOptions,
    RunnerLaunchOptionsError,
)
from backend.core.use_cases import backlog_actions
from backend.core.use_cases.backlog_actions import (
    BacklogActionError,
    BacklogEnqueueConflictError,
    enqueue_prd_ready,
    start_prd,
)
from backend.core.use_cases.console_issues import list_repository_issues
from backend.core.use_cases.console_processes import (
    ConsoleProcessError,
    build_runner_argv,
)
from backend.core.use_cases.issue_label_actions import (
    IssueLabelActionError,
    IssueLabelNotAllowedError,
    read_issue_labels,
    update_issue_labels,
)
from tests.conftest import FakeBacklogStore, FakeGitHubClient, FakeProcessRunner

REPO_ID = "keda-test"


@dataclass(frozen=True)
class _RecordingSupervisor(IRunnerProcessSupervisor):
    """记录 spawn 的 argv 而不是真的启动进程，用于断言启动命令内容。"""

    spawns: list[tuple[str, tuple[str, ...]]]

    def spawn(
        self,
        *,
        repo_id: str,
        kind: RunnerProcessKind,
        argv,
        cwd: Path,
    ) -> RunnerProcessRecord:
        self.spawns.append((repo_id, tuple(argv)))
        return RunnerProcessRecord(
            process_id="fake",
            repo_id=repo_id,
            kind=kind,
            pid=1,
            status="running",
            exit_code=None,
            log_path="",
            command=tuple(argv),
            started_at="",
            stopped_at=None,
        )

    def list_processes(self) -> list[RunnerProcessRecord]:
        return []

    def list_unmanaged_processes(self, registry_entries) -> list[RunnerProcessRecord]:
        return []

    def get_process(self, process_id: str) -> RunnerProcessRecord | None:
        return None

    def stop(self, process_id: str, *, timeout_seconds: int) -> RunnerProcessRecord:
        raise KeyError(process_id)

    def read_log(self, process_id: str, *, offset: int, max_bytes: int):
        raise KeyError(process_id)


def _context(repo_path: Path) -> RepositoryRunContext:
    return RepositoryRunContext(
        repo_id=REPO_ID,
        display_name="Keda Test",
        repo_path=repo_path,
        config=AppConfig(),
    )


def _write_prd(repo_path: Path, relative_path: str, *, issue_number: int | None = None) -> str:
    """写一个最小 pending PRD，返回仓库相对路径（含/不含 Issue 回链）。"""
    prd_file_path = repo_path / relative_path
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    issue_line = (
        f"- GitHub Issue: https://github.com/example/repo/issues/{issue_number}"
        if issue_number is not None
        else "- GitHub Issue: (to be created)"
    )
    prd_file_path.write_text(
        f"# PRD: {prd_file_path.stem}\n\n{issue_line}\n\n"
        "## Acceptance Checklist\n\n- [ ] item one\n",
        encoding="utf-8",
    )
    return relative_path


# ─────────────────────────────────────────────────────────────────────────────
# FR-3：加入就绪 —— 打标签 / 建 Issue，但绝不启动 runner
# ─────────────────────────────────────────────────────────────────────────────


def _forbid_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """把启动路径打桩成「一旦被调用就失败」，从结构上证明入队不启动 runner。"""

    def _boom(*args, **kwargs):
        raise AssertionError("enqueue_prd_ready 绝不能启动 runner")

    monkeypatch.setattr(backlog_actions, "start_runner_process", _boom)
    monkeypatch.setattr(backlog_actions, "_spawn_runner", _boom)


def test_enqueue_ready_labels_existing_issue_without_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已有 Issue 的 PRD 入队：只补 ready 标签、移除 failed，且不启动 runner。"""
    _forbid_spawn(monkeypatch)
    prd_path = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20261008-enq.md", issue_number=11)
    context = _context(tmp_path)
    client = FakeGitHubClient()
    client.set_issue_labels(11, ("agent/failed",))
    store = FakeBacklogStore(repo_id=REPO_ID)

    result = enqueue_prd_ready(
        prd_path=prd_path,
        repo_id=REPO_ID,
        contexts=[context],
        github_client=client,
        store=store,
        process_runner=FakeProcessRunner(),
    )

    assert result.state is BacklogPrdState.READY
    assert result.issue_number == 11
    label_edits = [c for c in client.calls if c["method"] == "edit_issue_labels"]
    assert label_edits == [
        {
            "method": "edit_issue_labels",
            "issue_number": 11,
            "add": ["agent/ready"],
            "remove": ["agent/failed"],
        }
    ]
    assert store.audits[-1].action == "enqueue_ready"
    assert store.audits[-1].result == "accepted"


def test_enqueue_ready_creates_issue_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 Issue 的 PRD 入队走建 Issue 路径，同样绝不启动 runner。"""
    _forbid_spawn(monkeypatch)
    prd_path = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20261008-new.md")
    context = _context(tmp_path)
    client = FakeGitHubClient()
    store = FakeBacklogStore(repo_id=REPO_ID)
    created: list[int] = []

    def _fake_create(prd, ctx, github_client, process_runner):
        created.append(99)
        return 99

    monkeypatch.setattr(backlog_actions, "_create_issue_for_prd", _fake_create)

    result = enqueue_prd_ready(
        prd_path=prd_path,
        repo_id=REPO_ID,
        contexts=[context],
        github_client=client,
        store=store,
        process_runner=FakeProcessRunner(),
    )

    assert created == [99]
    assert result.issue_number == 99
    assert result.state is BacklogPrdState.READY


def test_enqueue_ready_conflicts_when_issue_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关联 Issue 正在执行时入队被拒，GitHub 标签零变化。"""
    _forbid_spawn(monkeypatch)
    prd_path = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20261008-run.md", issue_number=21)
    context = _context(tmp_path)
    client = FakeGitHubClient()
    client.set_issue_labels(21, ("agent/running",))
    store = FakeBacklogStore(repo_id=REPO_ID)

    with pytest.raises(BacklogEnqueueConflictError):
        enqueue_prd_ready(
            prd_path=prd_path,
            repo_id=REPO_ID,
            contexts=[context],
            github_client=client,
            store=store,
            process_runner=FakeProcessRunner(),
        )

    assert not [c for c in client.calls if c["method"] == "edit_issue_labels"]
    assert store.audits[-1].result == "rejected"


def test_enqueue_ready_is_idempotent_on_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已就绪的 Issue 再入队只重申标签，成功且不启动 runner。"""
    _forbid_spawn(monkeypatch)
    prd_path = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20261008-ready.md", issue_number=31)
    context = _context(tmp_path)
    client = FakeGitHubClient()
    client.set_issue_labels(31, ("agent/ready",))
    store = FakeBacklogStore(repo_id=REPO_ID)

    result = enqueue_prd_ready(
        prd_path=prd_path,
        repo_id=REPO_ID,
        contexts=[context],
        github_client=client,
        store=store,
        process_runner=FakeProcessRunner(),
    )

    assert result.issue_number == 31
    assert result.state is BacklogPrdState.READY


def test_enqueue_ready_missing_prd_raises(tmp_path: Path) -> None:
    """不存在的 PRD 入队抛出 BacklogActionError。"""
    context = _context(tmp_path)
    with pytest.raises(BacklogActionError):
        enqueue_prd_ready(
            prd_path="tasks/pending/P1-FEAT-20261008-nope.md",
            repo_id=REPO_ID,
            contexts=[context],
            github_client=FakeGitHubClient(),
            store=FakeBacklogStore(repo_id=REPO_ID),
            process_runner=FakeProcessRunner(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# FR-7：开始此 PRD 的高级选项 —— 缺省契约逐字节一致
# ─────────────────────────────────────────────────────────────────────────────


def test_start_prd_default_options_argv_matches_no_options(tmp_path: Path) -> None:
    """不传选项与传缺省选项，启动 argv 必须逐字节一致（默认契约不变）。"""
    prd_path = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20261008-def.md", issue_number=41)
    context = _context(tmp_path)
    base_command = ("uv", "run", "kc")

    supervisor_no_opts = _RecordingSupervisor(spawns=[])
    start_prd(
        prd_path=prd_path,
        repo_id=REPO_ID,
        contexts=[context],
        github_client=FakeGitHubClient(),
        supervisor=supervisor_no_opts,
        store=FakeBacklogStore(repo_id=REPO_ID),
        runner_command=base_command,
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
    )

    supervisor_default = _RecordingSupervisor(spawns=[])
    start_prd(
        prd_path=prd_path,
        repo_id=REPO_ID,
        contexts=[context],
        github_client=FakeGitHubClient(),
        supervisor=supervisor_default,
        store=FakeBacklogStore(repo_id=REPO_ID),
        runner_command=base_command,
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
        launch_options=RunnerLaunchOptions(),
    )

    assert supervisor_no_opts.spawns == supervisor_default.spawns
    argv = supervisor_no_opts.spawns[0][1]
    assert "--fast-merge" not in argv
    assert "--agent" not in argv
    assert "--preset" not in argv


def test_start_prd_launch_options_reach_argv(tmp_path: Path) -> None:
    """非缺省选项必须真的出现在启动 argv 里。"""
    prd_path = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20261008-opt.md", issue_number=42)
    context = _context(tmp_path)
    supervisor = _RecordingSupervisor(spawns=[])

    start_prd(
        prd_path=prd_path,
        repo_id=REPO_ID,
        contexts=[context],
        github_client=FakeGitHubClient(),
        supervisor=supervisor,
        store=FakeBacklogStore(repo_id=REPO_ID),
        runner_command=("uv", "run", "kc"),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
        launch_options=RunnerLaunchOptions(fast_merge=True, agent="codex"),
    )

    argv = supervisor.spawns[0][1]
    assert "--fast-merge" in argv
    assert "--agent" in argv
    assert "codex" in argv


# ─────────────────────────────────────────────────────────────────────────────
# FR-7 底层：RunnerLaunchOptions 折算与 build_runner_argv 的字节一致性/非法组合
# ─────────────────────────────────────────────────────────────────────────────


def test_runner_launch_options_default_is_empty_flags() -> None:
    """全缺省选项判定为 default 且 cli_flags 为空元组。"""
    options = RunnerLaunchOptions()
    assert options.is_default()
    assert options.cli_flags() == ()


def test_build_runner_argv_byte_identity_default_vs_none() -> None:
    """kind=RUN_ONCE 定向 Issue 时，None / 缺省 / 不传该参数三者 argv 完全相同。"""
    argv_none = build_runner_argv(
        runner_command=("uv", "run", "kc"),
        kind=RunnerProcessKind.RUN_ONCE,
        repo_id=REPO_ID,
        issue_number=7,
        options=None,
    )
    argv_default = build_runner_argv(
        runner_command=("uv", "run", "kc"),
        kind=RunnerProcessKind.RUN_ONCE,
        repo_id=REPO_ID,
        issue_number=7,
        options=RunnerLaunchOptions(),
    )
    argv_omitted = build_runner_argv(
        runner_command=("uv", "run", "kc"),
        kind=RunnerProcessKind.RUN_ONCE,
        repo_id=REPO_ID,
        issue_number=7,
    )
    assert argv_none == argv_default == argv_omitted


def test_build_runner_argv_appends_non_default_flags() -> None:
    """非缺省选项把对应旗标追加进 argv。"""
    argv = build_runner_argv(
        runner_command=("kc",),
        kind=RunnerProcessKind.DAEMON,
        repo_id=REPO_ID,
        options=RunnerLaunchOptions(preset="strong", model="gpt-5"),
    )
    assert "--preset" in argv and "strong" in argv
    assert "--model" in argv and "gpt-5" in argv


@pytest.mark.parametrize(
    "options",
    [
        RunnerLaunchOptions(fast_merge=True, direct_pr=True),
        RunnerLaunchOptions(model="gpt-5"),
        RunnerLaunchOptions(reasoning_effort="high"),
    ],
)
def test_build_runner_argv_rejects_invalid_options(options: RunnerLaunchOptions) -> None:
    """互斥档位 / 缺 preset 的覆盖旗标在构建 argv 时被拒。"""
    with pytest.raises(ConsoleProcessError):
        build_runner_argv(
            runner_command=("kc",),
            kind=RunnerProcessKind.RUN_ONCE,
            repo_id=REPO_ID,
            options=options,
        )


@pytest.mark.parametrize(
    "options",
    [
        RunnerLaunchOptions(fast_merge=True, direct_pr=True),
        RunnerLaunchOptions(model="gpt-5"),
        RunnerLaunchOptions(agent="has space"),
        RunnerLaunchOptions(agent="-leading-dash"),
    ],
)
def test_runner_launch_options_cli_flags_raises(options: RunnerLaunchOptions) -> None:
    """cli_flags 自身对非法组合抛 RunnerLaunchOptionsError。"""
    with pytest.raises(RunnerLaunchOptionsError):
        options.cli_flags()


# ─────────────────────────────────────────────────────────────────────────────
# FR-4：全部 Issue 列表 —— monitored 标注与参数校验
# ─────────────────────────────────────────────────────────────────────────────


def _summary(number: int, labels: tuple[str, ...], state: str = "OPEN") -> IssueSummary:
    return IssueSummary(
        number=number,
        title=f"Issue #{number}",
        url=f"https://github.com/example/repo/issues/{number}",
        body="",
        labels=labels,
        state=state,
    )


def test_list_repository_issues_marks_monitored(tmp_path: Path) -> None:
    """带队列标签的 Issue monitored=True，普通 Issue monitored=False。"""
    context = _context(tmp_path)
    client = FakeGitHubClient()
    client.set_list_issues_by_label_result([_summary(1, ("agent/ready",)), _summary(2, ("bug",))])

    entries = list_repository_issues(context=context, github_client=client, state="open", limit=50)

    by_number = {entry.number: entry for entry in entries}
    assert by_number[1].monitored is True
    assert by_number[2].monitored is False
    list_call = next(c for c in client.calls if c["method"] == "list_issues_by_label")
    assert list_call["label"] is None
    assert list_call["state"] == "open"


def test_list_repository_issues_rejects_bad_state(tmp_path: Path) -> None:
    """非法 state 取值在调用 GitHub 之前被拒。"""
    context = _context(tmp_path)
    client = FakeGitHubClient()
    with pytest.raises(ValueError):
        list_repository_issues(context=context, github_client=client, state="weird")
    assert not [c for c in client.calls if c["method"] == "list_issues_by_label"]


def test_list_repository_issues_bounds_limit(tmp_path: Path) -> None:
    """limit 超出天花板时被收敛到 500 再传给客户端。"""
    context = _context(tmp_path)
    client = FakeGitHubClient()
    client.set_list_issues_by_label_result([_summary(1, ())])
    list_repository_issues(context=context, github_client=client, state="all", limit=9999)
    list_call = next(c for c in client.calls if c["method"] == "list_issues_by_label")
    assert list_call["limit"] == 500


# ─────────────────────────────────────────────────────────────────────────────
# FR-5：标签编辑 —— 越界拒绝（GitHub 零变化）与合法 fresh read
# ─────────────────────────────────────────────────────────────────────────────


def test_read_issue_labels_returns_allowed_set(tmp_path: Path) -> None:
    """读取返回当前标签与允许集合（允许集合含 agent/ready 标准标签）。"""
    context = _context(tmp_path)
    client = FakeGitHubClient()
    client.set_issue_labels(5, ("agent/running",))

    snapshot = read_issue_labels(issue_number=5, context=context, github_client=client)

    assert snapshot.labels == ("agent/running",)
    assert "agent/ready" in snapshot.allowed_labels


def test_update_issue_labels_out_of_set_rejected_without_write(
    tmp_path: Path,
) -> None:
    """越界标签在写入前被拒，edit_issue_labels 完全不被调用（GitHub 零变化）。"""
    context = _context(tmp_path)
    client = FakeGitHubClient()
    client.set_issue_labels(6, ())

    with pytest.raises(IssueLabelNotAllowedError):
        update_issue_labels(
            issue_number=6,
            add=["agent/redy"],
            remove=[],
            context=context,
            github_client=client,
        )

    assert not [c for c in client.calls if c["method"] == "edit_issue_labels"]


def test_update_issue_labels_in_set_fresh_read(tmp_path: Path) -> None:
    """合法新增后调用 edit，并以 fresh read 的标签作为响应。"""
    context = _context(tmp_path)
    client = FakeGitHubClient()
    client.set_issue_labels(7, ("agent/failed",))

    snapshot = update_issue_labels(
        issue_number=7,
        add=["agent/ready"],
        remove=["agent/failed"],
        context=context,
        github_client=client,
    )

    assert [c for c in client.calls if c["method"] == "edit_issue_labels"]
    assert "agent/ready" in snapshot.labels
    assert "agent/failed" not in snapshot.labels


def test_update_issue_labels_conflicting_add_remove(tmp_path: Path) -> None:
    """同一标签既加又删属于自相矛盾，拒绝且不写入。"""
    context = _context(tmp_path)
    client = FakeGitHubClient()
    with pytest.raises(IssueLabelActionError):
        update_issue_labels(
            issue_number=8,
            add=["agent/ready"],
            remove=["agent/ready"],
            context=context,
            github_client=client,
        )
    assert not [c for c in client.calls if c["method"] == "edit_issue_labels"]

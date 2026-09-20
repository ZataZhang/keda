"""Roadmap 仓库级 Autopilot 控制的用例与 API 契约测试。

覆盖链条：API 路由 → core 用例 → 受限端口 → 真实临时仓库的 ``.iar.toml``
→ fresh load 读回。GitHub / store / supervisor 按测试目标使用 fake（不参与
自动合并或 daemon 决策），配置文件本身与读写全程真实——写回目标必须是临时
注册仓，不得改脏开发仓配置。注意本仓根 ``.iar.toml`` 受 git 跟踪。
"""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_roadmap as roadmap_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import (
    AppConfig,
    AutopilotConfig,
    RepositoryRunContext,
    SafetyConfig,
)
from backend.core.shared.interfaces.runner_console import (
    RunnerProcessKind,
    RunnerProcessRecord,
)
from backend.core.use_cases.agent_runner_merge_queue import (
    _autopilot_enabled,
    process_merge_queue,
)
from backend.core.use_cases.roadmap_actions import advance_roadmap_queue
from backend.core.use_cases.roadmap_autopilot_settings import (
    RoadmapAutopilotError,
    daemon_is_running,
    load_autopilot_state,
    set_autopilot_enabled,
)
from backend.infrastructure.config.agent_runner_settings import (
    load_agent_runner_local_settings,
)
from backend.infrastructure.config.repository_settings_editor import (
    TomlRepositoryAutopilotSettingsEditor,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeGitHubClient, FakeProcessRunner, FakeRoadmapStore

client = TestClient(app)

REPO_ID = "keda-main"

_CONFIG_TEMPLATE = """# tmp repo config
[agent_runner.autopilot]
enabled = false
merge_method = "squash"
require_verifier_pass = true
auto_sign_off = false
merge_check_timeout_seconds = 1800
"""


def _make_editor() -> TomlRepositoryAutopilotSettingsEditor:
    return TomlRepositoryAutopilotSettingsEditor()


def _read_local_enabled(repo_root: Path) -> bool:
    """用标准 tomllib 读取临时仓配置（模拟 fresh loader 的磁盘读取）。"""
    with open(repo_root / ".iar.toml", "rb") as handle:
        data = tomllib.load(handle)
    return bool(data["agent_runner"]["autopilot"]["enabled"])


def _contexts_for(repo_root: Path, *, auto_merge: bool = False):
    """构造当前 tmp 仓库的上下文：fresh load 的真实值来自磁盘文件本身。"""

    def loader():
        enabled = _read_local_enabled(repo_root)
        config = replace(
            AppConfig(),
            autopilot=AutopilotConfig(enabled=enabled),
            safety=SafetyConfig(auto_merge=auto_merge),
        )
        return [
            RepositoryRunContext(
                repo_id=REPO_ID,
                display_name="tmp repo",
                repo_path=repo_root,
                config=config,
            )
        ]

    return loader


def _daemon_record(repo_id: str, status: str = "running") -> RunnerProcessRecord:
    return RunnerProcessRecord(
        process_id=f"pid-{repo_id}",
        repo_id=repo_id,
        kind=RunnerProcessKind.DAEMON,
        pid=4242,
        status=status,
        exit_code=None,
        log_path="",
        command=("iar", "daemon"),
        started_at="2026-09-19T00:00:00+00:00",
        stopped_at=None,
    )


class _FakeSupervisor:
    def __init__(self, records: list[RunnerProcessRecord]) -> None:
        self._records = records

    def list_processes(self) -> list[RunnerProcessRecord]:
        return self._records


def test_daemon_is_running_detects_repo_daemon() -> None:
    """daemon 判定只看同仓库的 daemon 进程，其它 repo/kind 不算。"""
    records = [
        _daemon_record("other-repo"),
        _daemon_record(REPO_ID, status="exited"),
        RunnerProcessRecord(
            process_id="x",
            repo_id=REPO_ID,
            kind=RunnerProcessKind.RUN_ONCE,
            pid=1,
            status="running",
            exit_code=None,
            log_path="",
            command=(),
            started_at="",
            stopped_at=None,
        ),
    ]
    assert daemon_is_running(REPO_ID, records) is False
    assert daemon_is_running(REPO_ID, records + [_daemon_record(REPO_ID)]) is True


def test_load_autopilot_state_reports_real_conditions(tmp_path: Path) -> None:
    """状态快照必须分别给出 Autopilot / 自动合并 / daemon 三件事。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".iar.toml").write_text(_CONFIG_TEMPLATE, encoding="utf-8")

    state = load_autopilot_state(
        repo_id=REPO_ID,
        contexts=_contexts_for(repo_root, auto_merge=True)(),
        supervisor=_FakeSupervisor([_daemon_record(REPO_ID)]),
        max_parallel=3,
        editor=_make_editor(),
    )
    assert state.enabled is False
    assert state.persisted_enabled is False
    assert state.auto_merge_enabled is True
    assert state.daemon_running is True
    assert state.max_parallel == 3
    assert state.config_source == ".iar.toml"


def test_set_autopilot_enabled_writes_then_reads_back(tmp_path: Path) -> None:
    """写回后必须能从 fresh loader 读回同值，且只改一个键。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config_path = repo_root / ".iar.toml"
    config_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
    before_lines = config_path.read_text(encoding="utf-8").splitlines()

    state = set_autopilot_enabled(
        repo_id=REPO_ID,
        enabled=True,
        editor=_make_editor(),
        contexts_loader=_contexts_for(repo_root),
        supervisor=_FakeSupervisor([]),
        max_parallel=2,
    )

    assert state.enabled is True
    assert state.persisted_enabled is True
    after_lines = config_path.read_text(encoding="utf-8").splitlines()
    changed = [pair for pair in zip(before_lines, after_lines) if pair[0] != pair[1]]
    assert changed == [("enabled = false", "enabled = true")]


def test_set_autopilot_enabled_rejects_stale_fresh_loader(tmp_path: Path) -> None:
    """fresh load 与请求值不一致（未真正落盘）必须报错，而不是 200 冒充成功。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".iar.toml").write_text(_CONFIG_TEMPLATE, encoding="utf-8")

    def stale_loader():
        return [
            RepositoryRunContext(
                repo_id=REPO_ID,
                display_name="tmp",
                repo_path=repo_root,
                config=replace(AppConfig(), autopilot=AutopilotConfig(enabled=False)),
            )
        ]

    with pytest.raises(RoadmapAutopilotError):
        set_autopilot_enabled(
            repo_id=REPO_ID,
            enabled=True,
            editor=_make_editor(),
            contexts_loader=stale_loader,
            supervisor=_FakeSupervisor([]),
            max_parallel=2,
        )


# ── rv-4：现有持续调度链与双重合并门禁 ─────────────────────────────────────


_WRITE_PRD_HELPER = """# PRD: {title}

- GitHub Issue: https://github.com/example/repo/issues/{issue}

## Acceptance Checklist

- [ ] item one
"""


def _write_prd(repo_root: Path, relative_path: str, *, title: str, issue: int) -> str:
    prd_path = repo_root / relative_path
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(_WRITE_PRD_HELPER.format(title=title, issue=issue), encoding="utf-8")
    return relative_path


def _real_config_for(repo_root: Path) -> AppConfig:
    """用产品自身的 loader 从临时仓真实 .iar.toml 读出生效配置片段。"""
    settings = load_agent_runner_local_settings(repo_root)
    assert settings is not None
    # local 配置里未写的段为 None：此时沿用 AppConfig 默认值（auto_merge=False）。
    local_autopilot = settings.autopilot
    local_safety = settings.safety
    return replace(
        AppConfig(),
        autopilot=replace(
            AutopilotConfig(), enabled=bool(local_autopilot.enabled) if local_autopilot else False
        ),
        safety=SafetyConfig(auto_merge=bool(local_safety.auto_merge) if local_safety else False),
    )


def test_upstream_merged_promotes_downstream_when_autopilot_enabled(tmp_path: Path) -> None:
    """Autopilot 开启且上游已合并时，现有 advance 会自动启动下游（无需再点全局开始）。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".iar.toml").write_text(
        _CONFIG_TEMPLATE.replace("false", "true", 1), encoding="utf-8"
    )
    upstream = _write_prd(
        repo_root, "tasks/pending/P1-FEAT-20260101-upstream.md", title="Upstream", issue=1
    )
    downstream_relative = "tasks/pending/P1-FEAT-20260101-downstream.md"
    downstream = _write_prd(repo_root, downstream_relative, title="Downstream", issue=2)
    # 下游显式声明依赖上游 Issue，确保「上游合并 → 下游解锁」是被真实验证的，
    # 而不是碰巧被 discovery 捞起来。
    (repo_root / downstream).write_text(
        (repo_root / downstream).read_text(encoding="utf-8")
        + "\n## Delivery Dependencies\n\n- Depends on tasks/issues: #1\n",
        encoding="utf-8",
    )

    store = FakeRoadmapStore(repo_id=REPO_ID, max_parallel=1)
    store.seed(upstream, "running")
    store.seed(downstream, "queued")
    github_client = FakeGitHubClient()
    github_client._issue_states[1] = "CLOSED"
    github_client._issue_comments[1] = [
        "<!-- iar:event version=1 phase=draft_pr_created cycle=1 pr_branch=issue-1 -->"
    ]
    github_client._merged_prs["issue-1"] = "https://github.com/example/repo/pull/1"

    report = advance_roadmap_queue(
        context=RepositoryRunContext(
            repo_id=REPO_ID,
            display_name="tmp",
            repo_path=repo_root,
            config=_real_config_for(repo_root),
        ),
        github_client=github_client,
        store=store,
        process_runner=FakeProcessRunner(),
    )

    assert report.reconciled_completed == [upstream]
    assert [item.prd_path for item in report.started] == [downstream]
    assert github_client._issue_labels[2] == ("agent/ready",)


def test_autopilot_disabled_promotes_nothing(tmp_path: Path) -> None:
    """Autopilot 关闭时同一场景零晋升：不得靠 UI 开关弱化这道门控。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".iar.toml").write_text(_CONFIG_TEMPLATE, encoding="utf-8")
    context = RepositoryRunContext(
        repo_id=REPO_ID,
        display_name="tmp",
        repo_path=repo_root,
        config=_real_config_for(repo_root),
    )
    assert context.config.autopilot.enabled is False
    # 既有的 daemon 门控据此跳过整个 roadmap 调度阶段（detail 实现见
    # tests/test_roadmap_advance.py::test_gate_disabled_skips_scheduling）。
    assert _autopilot_enabled(context.config) is False


def test_merge_queue_requires_both_switches(tmp_path: Path) -> None:
    """只有 autopilot.enabled 为真而 safety.auto_merge 为假时必须零自动合并。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".iar.toml").write_text(
        _CONFIG_TEMPLATE.replace("false", "true", 1), encoding="utf-8"
    )
    config = _real_config_for(repo_root)
    assert config.autopilot.enabled is True
    assert config.safety.auto_merge is False

    github_client = FakeGitHubClient()
    exit_code, outcomes = process_merge_queue(
        repo_path=repo_root,
        config=config,
        github_client=github_client,
        process_runner=FakeProcessRunner(),
        supervisor_agent="kimi",
    )
    assert exit_code == 0
    assert outcomes == []
    # 双门禁未全开时不应产生任何远端合并动作
    assert not [call for call in github_client.calls if call["method"] == "merge_pull_request"]


# ── API 契约 ────────────────────────────────────────────────────────────────


@pytest.fixture
def autopilot_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把 Roadmap 路由接到临时仓库 / 临时 store / fake supervisor。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".iar.toml").write_text(_CONFIG_TEMPLATE, encoding="utf-8")
    store = SqliteConsoleStore(tmp_path / "console.db")

    contexts_loader = _contexts_for(repo_root, auto_merge=False)
    monkeypatch.setattr(roadmap_routes, "create_roadmap_store", lambda: store)
    monkeypatch.setattr(roadmap_routes, "_resolve_contexts", contexts_loader)
    monkeypatch.setattr(roadmap_routes, "create_process_supervisor", lambda: _FakeSupervisor([]))
    return {"repo_root": repo_root, "store": store}


def test_get_autopilot_endpoint(autopilot_environment) -> None:
    """GET 返回完整闭环字段（含第二个门禁 auto_merge）。"""
    response = client.get(f"/api/v1/agent-runner/roadmap/autopilot?repo_id={REPO_ID}")
    assert response.status_code == 200
    data = response.json()
    assert data["repo_id"] == REPO_ID
    assert data["enabled"] is False
    assert data["auto_merge_enabled"] is False
    assert data["daemon_running"] is False
    assert data["max_parallel"] == 2
    assert data["config_source"] == ".iar.toml"


def test_get_autopilot_rejects_non_bool_enabled(autopilot_environment) -> None:
    """`.iar.toml` 里的 enabled 不是布尔时必须返回 4xx，而不是漏成 500。"""
    (autopilot_environment["repo_root"] / ".iar.toml").write_text(
        _CONFIG_TEMPLATE.replace("enabled = false", 'enabled = "yes"'),
        encoding="utf-8",
    )
    response = client.get(f"/api/v1/agent-runner/roadmap/autopilot?repo_id={REPO_ID}")
    assert response.status_code == 400
    assert "布尔" in response.json()["detail"]


def test_patch_autopilot_persists_and_reads_back(autopilot_environment) -> None:
    """PATCH → 写回 → fresh GET 读回同值；配置 diff 只能有一行变化。"""
    repo_root = autopilot_environment["repo_root"]
    config_path = repo_root / ".iar.toml"
    before_lines = config_path.read_text(encoding="utf-8").splitlines()

    response = client.patch(
        f"/api/v1/agent-runner/roadmap/autopilot?repo_id={REPO_ID}",
        json={"repo_id": REPO_ID, "enabled": True},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    # 成功响应必须来自 fresh load，而不是请求体回显
    after_lines = config_path.read_text(encoding="utf-8").splitlines()
    assert len(after_lines) == len(before_lines)
    assert sum(1 for pair in zip(before_lines, after_lines) if pair[0] != pair[1]) == 1

    follow_up = client.get(f"/api/v1/agent-runner/roadmap/autopilot?repo_id={REPO_ID}")
    assert follow_up.json()["enabled"] is True
    assert follow_up.json()["persisted_enabled"] is True
    # safety.auto_merge 不得被本开关连带修改
    assert follow_up.json()["auto_merge_enabled"] is False


def test_patch_autopilot_rejects_unknown_repo(autopilot_environment) -> None:
    """未知仓库返回 400，且不触碰任何文件。"""
    response = client.patch(
        "/api/v1/agent-runner/roadmap/autopilot?repo_id=nope",
        json={"repo_id": "nope", "enabled": True},
    )
    assert response.status_code == 400


def test_patch_autopilot_conflicts_when_config_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目标仓没有 .iar.toml → 409，且不得凭空创建配置文件。"""
    repo_root = tmp_path / "repo-without-config"
    repo_root.mkdir()
    store = SqliteConsoleStore(tmp_path / "console.db")
    contexts_loader = lambda: [  # noqa: E731 - 这里故意伪装缺文件仓库的上下文
        RepositoryRunContext(
            repo_id=REPO_ID,
            display_name="tmp",
            repo_path=repo_root,
            config=AppConfig(),
        )
    ]
    monkeypatch.setattr(roadmap_routes, "create_roadmap_store", lambda: store)
    monkeypatch.setattr(roadmap_routes, "_resolve_contexts", contexts_loader)
    monkeypatch.setattr(roadmap_routes, "create_process_supervisor", lambda: _FakeSupervisor([]))

    response = client.patch(
        f"/api/v1/agent-runner/roadmap/autopilot?repo_id={REPO_ID}",
        json={"repo_id": REPO_ID, "enabled": True},
    )
    assert response.status_code == 409
    assert not (repo_root / ".iar.toml").exists()

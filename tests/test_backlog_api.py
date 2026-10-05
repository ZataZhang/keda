"""Tests for the backlog API routes."""

from __future__ import annotations

import base64
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_backlog as backlog_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    PostPrSupervisorConfig,
    PullRequestContext,
    RepositoryRunContext,
)
from backend.core.use_cases.agent_runner_events import format_event_marker
from backend.core.use_cases.backlog_ci_delivery import build_ci_failure_key
from backend.infrastructure.config.repository_settings_editor import (
    TomlRepositoryAutopilotSettingsEditor,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeGitHubClient, FakeProcessRunner

client = TestClient(app)


@pytest.fixture
def backlog_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wire backlog routes to tmp-backed store, repo, and fake GitHub client."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    repo_dir = tmp_path / "repo"
    pending_dir = repo_dir / "tasks" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "P1-FEAT-20260101-test.md").write_text(
        "# PRD: Test Feature\n\n## Acceptance Checklist\n- [ ] item\n",
        encoding="utf-8",
    )

    contexts = [
        RepositoryRunContext(
            repo_id="keda-main",
            display_name="Keda Main",
            repo_path=repo_dir,
            config=AppConfig(),
        )
    ]
    github_client = FakeGitHubClient()

    monkeypatch.setattr(backlog_routes, "create_backlog_store", lambda: store)
    monkeypatch.setattr(backlog_routes, "_resolve_contexts", lambda: contexts)
    monkeypatch.setattr(backlog_routes, "create_github_client", lambda repo_path: github_client)
    monkeypatch.setattr(
        backlog_routes,
        "create_process_runner",
        lambda: type("FakeRunner", (), {"run": lambda *a, **k: None})(),
    )
    monkeypatch.setattr(
        backlog_routes,
        "create_process_supervisor",
        lambda: type(
            "FakeSupervisor",
            (),
            {
                "list_processes": lambda: [],
                "spawn": lambda **kwargs: type(
                    "Record",
                    (),
                    {
                        "process_id": "fake-id",
                        "repo_id": kwargs.get("repo_id"),
                        "kind": kwargs.get("kind"),
                        "pid": 1234,
                        "status": "running",
                        "exit_code": None,
                        "log_path": "",
                        "command": kwargs.get("argv"),
                        "started_at": "",
                        "stopped_at": None,
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr(
        backlog_routes, "resolve_console_spawn_cwd", lambda repo_id, contexts: tmp_path
    )

    from backend.infrastructure.config.settings import (
        AgentRunnerConsoleSettings,
        AgentRunnerSettings,
    )

    fake_settings = AgentRunnerSettings(
        console=AgentRunnerConsoleSettings(
            runner_command=["echo", "fake"],
        )
    )
    monkeypatch.setattr(backlog_routes, "load_fresh_agent_runner_settings", lambda: fake_settings)

    return {
        "store": store,
        "repo_dir": repo_dir,
        "github_client": github_client,
        "tmp_path": tmp_path,
    }


def test_list_backlog_prds(backlog_environment) -> None:
    """GET /backlog/prds should return scanned PRDs."""
    response = client.get(
        "/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["repo_id"] == "keda-main"
    assert len(data["prds"]) == 1
    assert data["prds"][0]["title"] == "Test Feature"


def test_backlog_cache_reused_after_slow_build(
    backlog_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构建耗时超过 TTL 时缓存仍须命中，否则前端每轮轮询都会全量重扫。"""
    build_count = 0

    def slow_build(repo_id: str, include_archived: bool) -> dict:
        nonlocal build_count
        build_count += 1
        time.sleep(0.3)
        return {
            "prds": [],
            "skipped": [],
            "repo_id": repo_id,
            "include_archived": include_archived,
            "scanned_at": "",
        }

    backlog_routes._BACKLOG_CACHE.clear()
    monkeypatch.setattr(backlog_routes, "_BACKLOG_CACHE_TTL_SECONDS", 0.2)
    monkeypatch.setattr(backlog_routes, "_build_backlog_response", slow_build)

    for _ in range(2):
        response = client.get(
            "/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false"
        )
        assert response.status_code == 200

    assert build_count == 1
    backlog_routes._BACKLOG_CACHE.clear()


def test_update_settings(backlog_environment) -> None:
    """PATCH /backlog/settings should persist settings."""
    response = client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"max_parallel": 3, "default_view": "list"},
    )
    assert response.status_code == 200
    assert response.json()["max_parallel"] == 3

    response = client.get("/api/v1/agent-runner/backlog/settings?repo_id=keda-main")
    assert response.status_code == 200
    assert response.json()["max_parallel"] == 3


def test_start_prd_rejects_missing_repo(backlog_environment) -> None:
    """Starting a PRD for an unknown repo must return 400."""
    encoded = base64.urlsafe_b64encode(b"tasks/pending/P1-FEAT-20260101-test.md").decode("ascii")
    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/start",
        json={"repo_id": "unknown"},
    )
    assert response.status_code == 400


def test_start_global_requires_valid_parallel(backlog_environment) -> None:
    """Global start must validate max_parallel bounds."""
    response = client.post(
        "/api/v1/agent-runner/backlog/start-global",
        json={"repo_id": "keda-main", "max_parallel": 0},
    )
    assert response.status_code == 422


# ── CI/CD 监控与自动修复端点 ────────────────────────────────────────────────

_CI_PRD_PATH = "tasks/pending/P1-FEAT-20260101-ci.md"
_CI_ISSUE = 7
_HEAD_A = "a" * 40
_HEAD_B = "b" * 40
_CI_SUMMARY = (
    "unit-tests (status=COMPLETED, conclusion=FAILURE) https://github.com/example/repo/runs/1",
)
_CI_CONFIG_TEMPLATE = """# tmp repo config
[agent_runner.autopilot]
enabled = false
merge_method = "squash"
require_verifier_pass = true
auto_sign_off = false
merge_check_timeout_seconds = 1800

[agent_runner.safety]
auto_merge = false

[agent_runner.post_pr_supervisor]
enabled = true
max_repair_attempts = 2
"""


def _encode(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii")


def _ci_pr_context(**overrides: object) -> PullRequestContext:
    defaults: dict[str, object] = {
        "pr_url": f"https://github.com/example/repo/pull/{_CI_ISSUE}",
        "branch": f"issue-{_CI_ISSUE}",
        "head_sha": _HEAD_A,
        "base_sha": "d" * 40,
        "checks_state": "FAILURE",
        "checks_summary": _CI_SUMMARY,
        "mergeable": True,
        "number": _CI_ISSUE,
    }
    defaults.update(overrides)
    return PullRequestContext(**defaults)  # type: ignore[arg-type]


def _repair_intent(head_sha: str, failure_digest: str | None = None) -> str:
    return format_event_marker(
        phase="post_pr_rework_requested",
        cycle=1,
        head_sha=head_sha,
        pr_branch=f"issue-{_CI_ISSUE}",
        action="repair_pr_branch",
        failure_digest=failure_digest,
    )


@pytest.fixture
def ci_environment(backlog_environment, monkeypatch: pytest.MonkeyPatch):
    """把 CI 端点接到临时仓库：真实 TOML writer + 真实 PRD 扫描 + fake GitHub。"""
    repo_dir = backlog_environment["repo_dir"]
    (repo_dir / ".iar.toml").write_text(_CI_CONFIG_TEMPLATE, encoding="utf-8")
    (repo_dir / _CI_PRD_PATH).write_text(
        "# PRD: CI Feature\n\n"
        "- GitHub Issue: https://github.com/example/repo/issues/7\n\n"
        "## Acceptance Checklist\n\n- [ ] item\n",
        encoding="utf-8",
    )

    def contexts():
        # 生效值必须来自磁盘上的真实配置，PATCH 的写后校验才有意义。
        text = (repo_dir / ".iar.toml").read_text(encoding="utf-8")
        config = replace(
            AppConfig(),
            post_pr_supervisor=PostPrSupervisorConfig(
                auto_repair_ci="auto_repair_ci = true" in text
            ),
        )
        return [
            RepositoryRunContext(
                repo_id="keda-main",
                display_name="Keda Main",
                repo_path=repo_dir,
                config=config,
            )
        ]

    github_client = backlog_environment["github_client"]
    github_client._issue_comments[_CI_ISSUE] = [
        format_event_marker(phase="draft_pr_created", cycle=1, pr_branch=f"issue-{_CI_ISSUE}")
    ]
    github_client.set_pr_context(f"issue-{_CI_ISSUE}", _ci_pr_context())

    monkeypatch.setattr(backlog_routes, "_resolve_contexts", contexts)
    monkeypatch.setattr(
        backlog_routes,
        "create_process_runner",
        lambda: FakeProcessRunner(
            responses={
                ("iar", "worktree", "path", "--branch", f"issue-{_CI_ISSUE}"): CommandResult(
                    command=("iar", "worktree", "path", "--branch", f"issue-{_CI_ISSUE}"),
                    return_code=0,
                    stdout=".\n",
                    stderr="",
                )
            }
        ),
    )
    monkeypatch.setattr(
        backlog_routes,
        "create_repository_autopilot_settings_editor",
        lambda: TomlRepositoryAutopilotSettingsEditor(),
    )
    backlog_routes._BACKLOG_CACHE.clear()
    return {"repo_dir": repo_dir, "github_client": github_client}


def test_get_ci_settings_reports_persisted_and_effective(ci_environment) -> None:
    """GET 分开给出持久值（未写入时为 null）与生效值，页面才能区分「没设过」和「显式关」。"""
    response = client.get("/api/v1/agent-runner/backlog/ci?repo_id=keda-main")
    assert response.status_code == 200
    data = response.json()
    assert data["auto_repair_ci"] is False
    assert data["persisted_auto_repair"] is None
    assert data["max_repair_attempts"] == 2
    assert data["config_source"] == ".iar.toml"


def test_patch_ci_settings_writes_only_the_auto_repair_key(ci_environment) -> None:
    """PATCH 只新增 auto_repair_ci 一行，不联动 autopilot / auto_merge。"""
    config_path = ci_environment["repo_dir"] / ".iar.toml"
    before = config_path.read_text(encoding="utf-8").splitlines()

    response = client.patch(
        "/api/v1/agent-runner/backlog/ci?repo_id=keda-main",
        json={"repo_id": "keda-main", "auto_repair_ci": True},
    )
    assert response.status_code == 200
    assert response.json()["auto_repair_ci"] is True
    assert response.json()["persisted_auto_repair"] is True

    after = config_path.read_text(encoding="utf-8").splitlines()
    assert [line for line in after if line not in before] == ["auto_repair_ci = true"]
    assert [line for line in before if line not in after] == []
    assert "enabled = false" in "\n".join(after)
    assert "auto_merge = false" in "\n".join(after)

    follow_up = client.get("/api/v1/agent-runner/backlog/ci?repo_id=keda-main")
    assert follow_up.json()["persisted_auto_repair"] is True


def test_patch_ci_settings_rejects_unknown_repo(ci_environment) -> None:
    """未知仓库返回 400，不落盘。"""
    response = client.patch(
        "/api/v1/agent-runner/backlog/ci?repo_id=nope",
        json={"repo_id": "nope", "auto_repair_ci": True},
    )
    assert response.status_code == 400
    assert (
        not (ci_environment["repo_dir"] / ".iar.toml")
        .read_text(encoding="utf-8")
        .startswith("nope")
    )


def test_patch_ci_settings_conflicts_when_config_missing(
    ci_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目标仓没有 .iar.toml 时返回 409 且不得凭空创建文件。"""
    empty_repo = ci_environment["repo_dir"].parent / "repo-without-config"
    empty_repo.mkdir()
    monkeypatch.setattr(
        backlog_routes,
        "_resolve_contexts",
        lambda: [
            RepositoryRunContext(
                repo_id="keda-main",
                display_name="Keda Main",
                repo_path=empty_repo,
                config=AppConfig(),
            )
        ],
    )
    response = client.patch(
        "/api/v1/agent-runner/backlog/ci?repo_id=keda-main",
        json={"repo_id": "keda-main", "auto_repair_ci": True},
    )
    assert response.status_code == 409
    assert not (empty_repo / ".iar.toml").exists()


def test_get_prd_ci_projection_separates_checks_from_policy(ci_environment) -> None:
    """投影同时给出原始 checks、三态策略与耗尽信息，前端不再自行推断生效值。"""
    response = client.get(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci?repo_id=keda-main"
    )
    assert response.status_code == 200
    delivery = response.json()["ci_delivery"]
    assert delivery["status"] == "failing"
    assert delivery["checks_state"] == "FAILURE"
    assert delivery["problems"][0]["name"] == "unit-tests"
    assert delivery["stored_policy"] == "inherit"
    assert delivery["global_auto_repair"] is False
    assert delivery["effective_auto_repair"] is False
    assert delivery["failure_key"]


def test_get_prd_ci_returns_reason_when_issue_not_linked(ci_environment) -> None:
    """没有关联 Issue 的 PRD 返回空投影 + 原因，而不是伪造一个状态。"""
    response = client.get(
        "/api/v1/agent-runner/backlog/prds/"
        f"{_encode('tasks/pending/P1-FEAT-20260101-test.md')}/ci?repo_id=keda-main"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ci_delivery"] is None
    assert "Issue" in data["reason"]


def test_patch_prd_ci_policy_writes_marker_and_reflects_in_projection(
    ci_environment,
) -> None:
    """策略写进 Issue marker（不是 console.db），读回后生效值立刻翻转。"""
    response = client.patch(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci-policy?repo_id=keda-main",
        json={"repo_id": "keda-main", "policy": "on"},
    )
    assert response.status_code == 200
    assert response.json()["stored_policy"] == "on"
    assert response.json()["ci_delivery"]["effective_auto_repair"] is True
    comments = ci_environment["github_client"].list_issue_comments(_CI_ISSUE)
    assert any("iar:ci-auto-repair-policy value=on" in comment for comment in comments)

    # latest-wins：再改回 off 时生效值跟随最新 marker
    off_response = client.patch(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci-policy?repo_id=keda-main",
        json={"repo_id": "keda-main", "policy": "off"},
    )
    assert off_response.json()["ci_delivery"]["effective_auto_repair"] is False


def test_patch_prd_ci_policy_rejects_unlinked_prd(ci_environment) -> None:
    """无 Issue 时单 PRD 策略无处可存，返回 409 而不是静默跟随全局。"""
    response = client.patch(
        "/api/v1/agent-runner/backlog/prds/"
        f"{_encode('tasks/pending/P1-FEAT-20260101-test.md')}/ci-policy?repo_id=keda-main",
        json={"repo_id": "keda-main", "policy": "on"},
    )
    assert response.status_code == 409


def test_backlog_prds_list_carries_ci_delivery_projection(ci_environment) -> None:
    """列表响应携带 CI 投影，前端首屏不必为每个 PRD 再发一轮请求。"""
    response = client.get(
        "/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false"
    )
    assert response.status_code == 200
    by_path = {prd["prd_path"]: prd for prd in response.json()["prds"]}
    assert by_path[_CI_PRD_PATH]["ci_delivery"]["status"] == "failing"
    assert by_path["tasks/pending/P1-FEAT-20260101-test.md"]["ci_delivery"] is None


def test_backlog_prds_list_reads_issue_comments_once_per_prd(ci_environment) -> None:
    """merged 判定、PR 分支解析与 CI 投影共用一次评论读取，不制造假上下文变化。"""
    client.get("/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false")
    reads = [
        call
        for call in ci_environment["github_client"].calls
        if call["method"] == "list_issue_comments"
    ]
    assert len(reads) == 1


def test_manual_repair_endpoint_accepts_first_request(ci_environment) -> None:
    """manual_repair：关闭态下人工请求仍进入既有修复路径，副作用只有一条意图评论。"""
    comments_before = len(ci_environment["github_client"]._issue_comments[_CI_ISSUE])
    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci-repair?repo_id=keda-main"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["decision"] == "ci_repair_allowed"
    assert data["head_sha"] == _HEAD_A
    new_comments = ci_environment["github_client"]._issue_comments[_CI_ISSUE][comments_before:]
    assert len(new_comments) == 1
    assert "failure_digest=" in new_comments[0]


def test_manual_repair_endpoint_is_idempotent_for_same_failure(ci_environment) -> None:
    """idempotent：第二次相同请求返回 409 且不再新增评论、标签或提交。"""
    first = client.post(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci-repair?repo_id=keda-main"
    )
    assert first.status_code == 200
    comment_count = len(ci_environment["github_client"]._issue_comments[_CI_ISSUE])

    second = client.post(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci-repair?repo_id=keda-main"
    )
    assert second.status_code == 409
    assert "重复" in second.json()["detail"] or "已经请求过" in second.json()["detail"]
    assert len(ci_environment["github_client"]._issue_comments[_CI_ISSUE]) == comment_count


def test_manual_repair_endpoint_rejects_when_exhausted(ci_environment) -> None:
    """exhausted：达到 max_repair_attempts 后连人工请求也停止副作用。"""
    ci_environment["github_client"]._issue_comments[_CI_ISSUE].extend(
        [
            _repair_intent(_HEAD_B, "digest-1"),
            _repair_intent("c" * 40, "digest-2"),
        ]
    )
    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci-repair?repo_id=keda-main"
    )
    assert response.status_code == 409
    assert "max_repair_attempts" in response.json()["detail"]
    # 两条历史意图之外没有新增评论
    assert len(ci_environment["github_client"]._issue_comments[_CI_ISSUE]) == 3


def test_manual_repair_endpoint_ignores_client_supplied_head(ci_environment) -> None:
    """调用方提交的 head/round 一律不被信任：判定只用服务端 fresh 解析的值。"""
    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci-repair?repo_id=keda-main",
        json={"head_sha": "f" * 40, "repair_round": 99},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["head_sha"] == _HEAD_A
    assert data["failure_key"] == build_ci_failure_key(
        pr_number=_CI_ISSUE, head_sha=_HEAD_A, checks_summary=_CI_SUMMARY
    )


def test_manual_repair_endpoint_rejects_when_pr_context_unreadable(ci_environment) -> None:
    """读不到当前 PR context 时返回 409，不按过期状态修复。"""
    ci_environment["github_client"].set_pr_context(f"issue-{_CI_ISSUE}", None)
    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{_encode(_CI_PRD_PATH)}/ci-repair?repo_id=keda-main"
    )
    assert response.status_code == 409

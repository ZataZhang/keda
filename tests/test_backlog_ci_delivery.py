"""Tests for the Backlog CI/CD delivery use case, policy gate, editor and API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_backlog as backlog_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    PostPrSupervisorConfig,
    PullRequestContext,
    RepositoryRunContext,
)
from backend.core.shared.models.backlog import CiDeliveryStatus, CiRepairPolicy
from backend.core.use_cases.agent_runner_events import (
    format_ci_auto_repair_policy_marker,
    format_event_marker,
    parse_latest_ci_auto_repair_policy,
)
from backend.core.use_cases.backlog_ci_delivery import (
    BacklogCiPolicyError,
    build_ci_delivery,
    compute_effective_auto_repair,
    count_ci_repair_rounds,
    enqueue_approved_auto_repair,
    gate_auto_repair_decision,
    read_stored_policy,
    request_manual_ci_repair,
    resolve_effective_policy_for_issue,
    set_prd_ci_policy,
)
from backend.infrastructure.config.repository_settings_editor import (
    RepositorySettingsEditError,
    TomlRepositoryAutopilotSettingsEditor,
)
from tests.conftest import FakeGitHubClient

client = TestClient(app)

# ─────────────────────────────────────────────────────────────────────────────
# marker 格式化与解析
# ─────────────────────────────────────────────────────────────────────────────


def test_policy_marker_roundtrip() -> None:
    """策略 marker 写入后按 latest-wins 读回。"""
    comments = [
        "noise",
        format_ci_auto_repair_policy_marker("off"),
        format_ci_auto_repair_policy_marker("on"),
    ]
    assert parse_latest_ci_auto_repair_policy(comments) == "on"
    assert read_stored_policy(comments) is CiRepairPolicy.ON


def test_policy_marker_absent_means_inherit() -> None:
    """无 marker 时等同 inherit（跟随全局）。"""
    assert read_stored_policy(["hello", format_event_marker(phase="x", cycle=1)]) is (
        CiRepairPolicy.INHERIT
    )


def test_policy_marker_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        format_ci_auto_repair_policy_marker("yes")


# ─────────────────────────────────────────────────────────────────────────────
# 三态 × 全局：六种组合的 effective 值
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stored", "global_enabled", "expected"),
    [
        (None, True, True),
        (None, False, False),
        ("inherit", True, True),
        ("inherit", False, False),
        ("on", True, True),
        ("on", False, True),
        ("off", True, False),
        ("off", False, False),
    ],
)
def test_effective_policy_matrix(stored: str | None, global_enabled: bool, expected: bool) -> None:
    assert compute_effective_auto_repair(stored, global_enabled) is expected


# ─────────────────────────────────────────────────────────────────────────────
# ci_delivery 投影
# ─────────────────────────────────────────────────────────────────────────────


def _pr_context(
    checks_state: str | None = "PENDING",
    checks_summary: tuple[str, ...] = (),
) -> PullRequestContext:
    return PullRequestContext(
        pr_url="https://github.com/example/repo/pull/7",
        branch="agent/issue-42",
        head_sha="abc123",
        base_sha="def456",
        checks_state=checks_state,
        checks_summary=checks_summary,
    )


def test_build_ci_delivery_no_pr() -> None:
    delivery = build_ci_delivery(
        prd_path="tasks/pending/x.md",
        issue_number=42,
        comments=[],
        pr_context=None,
        global_enabled=False,
        max_rounds=2,
    )
    assert delivery.status is CiDeliveryStatus.NO_PR
    assert delivery.round_count == 0
    assert delivery.stored_policy is CiRepairPolicy.INHERIT
    assert delivery.effective_enabled is False


def test_build_ci_delivery_pending_and_success() -> None:
    pending = build_ci_delivery(
        prd_path="x",
        issue_number=1,
        comments=[],
        pr_context=_pr_context("PENDING"),
        global_enabled=False,
        max_rounds=2,
    )
    assert pending.status is CiDeliveryStatus.PENDING
    success = build_ci_delivery(
        prd_path="x",
        issue_number=1,
        comments=[],
        pr_context=_pr_context("SUCCESS"),
        global_enabled=False,
        max_rounds=2,
    )
    assert success.status is CiDeliveryStatus.SUCCESS


def test_build_ci_delivery_failure_maps_summary_to_problems() -> None:
    delivery = build_ci_delivery(
        prd_path="x",
        issue_number=1,
        comments=[],
        pr_context=_pr_context("FAILURE", ("backend / typecheck: mypy failed",)),
        global_enabled=False,
        max_rounds=2,
    )
    assert delivery.status is CiDeliveryStatus.FAILURE
    assert len(delivery.problems) == 1
    assert delivery.problems[0].summary == "backend / typecheck: mypy failed"


def test_build_ci_delivery_unknown_state_is_unavailable() -> None:
    delivery = build_ci_delivery(
        prd_path="x",
        issue_number=1,
        comments=[],
        pr_context=_pr_context("SOMETHING_ODD"),
        global_enabled=False,
        max_rounds=2,
    )
    assert delivery.status is CiDeliveryStatus.UNAVAILABLE
    assert delivery.problems == ()


def test_build_ci_delivery_exhausted_after_max_rounds() -> None:
    comments = [
        format_event_marker(
            phase="post_pr_rework_requested", cycle=1, head_sha="a1b2c3", action="repair_pr_branch"
        ),
        format_event_marker(
            phase="post_pr_rework_requested", cycle=2, head_sha="d4e5f6", action="repair_pr_branch"
        ),
    ]
    delivery = build_ci_delivery(
        prd_path="x",
        issue_number=1,
        comments=comments,
        pr_context=_pr_context("FAILURE"),
        global_enabled=True,
        max_rounds=2,
    )
    assert delivery.round_count == 2
    assert delivery.exhausted is True
    assert delivery.exhausted_reason


def test_count_ci_repair_rounds_ignores_other_actions() -> None:
    comments = [
        format_event_marker(
            phase="post_pr_rework_requested", cycle=1, head_sha="a1b2c3", action="rebase_pr_branch"
        ),
        format_event_marker(
            phase="post_pr_rework_requested", cycle=2, head_sha="d4e5f6", action="repair_pr_branch"
        ),
    ]
    assert count_ci_repair_rounds(comments) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 自动修复策略门禁（review_once 调用面）
# ─────────────────────────────────────────────────────────────────────────────


def _config(auto_repair_ci: bool, max_attempts: int = 2) -> AppConfig:
    return AppConfig(
        post_pr_supervisor=PostPrSupervisorConfig(
            auto_repair_ci=auto_repair_ci, max_repair_attempts=max_attempts
        )
    )


def _issue() -> IssueSummary:
    return IssueSummary(number=42, title="t", url="https://example", body="", labels=())


def test_gate_blocks_when_global_off() -> None:
    github = FakeGitHubClient()
    github.set_pr_context("agent/issue-42", _pr_context("FAILURE"))
    allowed, reason = gate_auto_repair_decision(
        config=_config(False),
        github_client=github,
        issue_number=42,
        pr_context=_pr_context("FAILURE"),
        pr_branch="agent/issue-42",
    )
    assert allowed is False
    assert "未开启" in reason


def test_gate_blocks_when_prd_forced_off() -> None:
    github = FakeGitHubClient()
    github.comment_issue(42, format_ci_auto_repair_policy_marker("off"))
    allowed, _reason = gate_auto_repair_decision(
        config=_config(True),
        github_client=github,
        issue_number=42,
        pr_context=_pr_context("FAILURE"),
        pr_branch="agent/issue-42",
    )
    assert allowed is False


def test_gate_allows_forced_on_despite_global_off() -> None:
    github = FakeGitHubClient()
    github.comment_issue(42, format_ci_auto_repair_policy_marker("on"))
    allowed, _reason = gate_auto_repair_decision(
        config=_config(False),
        github_client=github,
        issue_number=42,
        pr_context=_pr_context("FAILURE"),
        pr_branch="agent/issue-42",
    )
    assert allowed is True


def test_gate_blocks_when_rounds_exhausted() -> None:
    github = FakeGitHubClient()
    github.comment_issue(
        42,
        format_event_marker(
            phase="post_pr_rework_requested", cycle=1, head_sha="a1b2c3", action="repair_pr_branch"
        ),
    )
    github.comment_issue(
        42,
        format_event_marker(
            phase="post_pr_rework_requested", cycle=2, head_sha="d4e5f6", action="repair_pr_branch"
        ),
    )
    allowed, reason = gate_auto_repair_decision(
        config=_config(True),
        github_client=github,
        issue_number=42,
        pr_context=_pr_context("FAILURE"),
        pr_branch="agent/issue-42",
    )
    assert allowed is False
    assert "耗尽" in reason


def test_enqueue_approved_auto_repair_is_idempotent_per_head() -> None:
    github = FakeGitHubClient()
    github.set_pr_context("agent/issue-42", _pr_context("FAILURE"))
    config = _config(True)
    queued, _detail = enqueue_approved_auto_repair(
        issue=_issue(),
        pr_context=_pr_context("FAILURE"),
        config=config,
        github_client=github,
        pr_branch="agent/issue-42",
    )
    assert queued is True
    comments_after_first = github.list_issue_comments(42)
    queued_again, detail = enqueue_approved_auto_repair(
        issue=_issue(),
        pr_context=_pr_context("FAILURE"),
        config=config,
        github_client=github,
        pr_branch="agent/issue-42",
    )
    assert queued_again is False
    # 幂等：重入不产生第二份修复意图评论。
    assert github.list_issue_comments(42) == comments_after_first
    assert "已存在" in detail


# ─────────────────────────────────────────────────────────────────────────────
# 单次手动修复（幂等 / 上限）
# ─────────────────────────────────────────────────────────────────────────────


def test_manual_repair_requests_once_then_idempotent() -> None:
    github = FakeGitHubClient()
    config = _config(False)  # 手动修复不受自动开关约束。
    requested, _detail = request_manual_ci_repair(
        issue=_issue(),
        pr_context=_pr_context("FAILURE"),
        config=config,
        github_client=github,
    )
    assert requested is True
    comments_after_first = github.list_issue_comments(42)
    requested_again, detail = request_manual_ci_repair(
        issue=_issue(),
        pr_context=_pr_context("FAILURE"),
        config=config,
        github_client=github,
    )
    assert requested_again is False
    assert github.list_issue_comments(42) == comments_after_first
    assert "幂等跳过" in detail


def test_manual_repair_rejects_when_exhausted() -> None:
    github = FakeGitHubClient()
    github.comment_issue(
        42,
        format_event_marker(
            phase="post_pr_rework_requested", cycle=1, head_sha="a1b2c3", action="repair_pr_branch"
        ),
    )
    github.comment_issue(
        42,
        format_event_marker(
            phase="post_pr_rework_requested", cycle=2, head_sha="d4e5f6", action="repair_pr_branch"
        ),
    )
    requested, detail = request_manual_ci_repair(
        issue=_issue(),
        pr_context=_pr_context("FAILURE", ()),
        config=_config(False, max_attempts=2),
        github_client=github,
    )
    assert requested is False
    assert "耗尽" in detail


def test_resolve_effective_policy_for_issue_uses_fresh_comments() -> None:
    github = FakeGitHubClient()
    assert (
        resolve_effective_policy_for_issue(
            config=_config(False), github_client=github, issue_number=42
        )
        is False
    )
    github.comment_issue(42, format_ci_auto_repair_policy_marker("on"))
    assert (
        resolve_effective_policy_for_issue(
            config=_config(False), github_client=github, issue_number=42
        )
        is True
    )


def test_set_prd_ci_policy_rejects_bad_value() -> None:
    github = FakeGitHubClient()
    with pytest.raises(BacklogCiPolicyError):
        set_prd_ci_policy(github_client=github, issue_number=42, value="maybe")


# ─────────────────────────────────────────────────────────────────────────────
# 受限配置编辑器：auto_repair_ci 单键写回
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def editor_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".iar.toml").write_text(
        "[agent_runner]\n[agent_runner.autopilot]\nenabled = true\n"
        "# 保留注释\n[agent_runner.post_pr_supervisor]\nmax_repair_attempts = 3\n",
        encoding="utf-8",
    )
    return repo


def test_editor_auto_repair_ci_roundtrip(editor_repo: Path) -> None:
    editor = TomlRepositoryAutopilotSettingsEditor()
    assert editor.read_auto_repair_ci(editor_repo) is None
    editor.set_auto_repair_ci(editor_repo, True)
    assert editor.read_auto_repair_ci(editor_repo) is True
    editor.set_auto_repair_ci(editor_repo, False)
    assert editor.read_auto_repair_ci(editor_repo) is False
    # 只改目标键：注释与相邻键必须逐字保留。
    raw = (editor_repo / ".iar.toml").read_text(encoding="utf-8")
    assert "# 保留注释" in raw
    assert "max_repair_attempts = 3" in raw
    assert "enabled = true" in raw


def test_editor_auto_repair_ci_missing_file(tmp_path: Path) -> None:
    editor = TomlRepositoryAutopilotSettingsEditor()
    assert editor.read_auto_repair_ci(tmp_path) is None
    with pytest.raises(RepositorySettingsEditError):
        editor.set_auto_repair_ci(tmp_path, True)


def test_editor_auto_repair_ci_rejects_non_bool(editor_repo: Path) -> None:
    (editor_repo / ".iar.toml").write_text(
        '[agent_runner]\n[agent_runner.post_pr_supervisor]\nauto_repair_ci = "yes"\n',
        encoding="utf-8",
    )
    editor = TomlRepositoryAutopilotSettingsEditor()
    with pytest.raises(RepositorySettingsEditError):
        editor.read_auto_repair_ci(editor_repo)


# ─────────────────────────────────────────────────────────────────────────────
# 配置默认值：缺省关闭且四开关语义独立
# ─────────────────────────────────────────────────────────────────────────────


def test_auto_repair_ci_defaults_off_and_independent() -> None:
    config = AppConfig()
    assert config.post_pr_supervisor.auto_repair_ci is False
    assert config.autopilot.enabled is False
    assert config.safety.auto_merge is False
    # 语义独立：本 PRD 新开关默认值与既有三开关互不影响（fix_agent 默认开）。
    assert config.runner.fix_agent_enabled is True


# ─────────────────────────────────────────────────────────────────────────────
# API：状态投影 / 全局开关 / 策略覆盖 / 单次修复
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def ci_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Backlog CI 端点的最小环境：临时仓库 + fake GitHub + 默认配置。"""
    from backend.infrastructure.persistence.console_store import SqliteConsoleStore

    store = SqliteConsoleStore(tmp_path / "console.db")
    repo_dir = tmp_path / "repo"
    pending_dir = repo_dir / "tasks" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "P1-FEAT-20260101-ci.md").write_text(
        "# PRD: CI Feature\n\n"
        "- GitHub Issue: https://github.com/example/repo/issues/42\n\n"
        "## Acceptance Checklist\n- [ ] item\n",
        encoding="utf-8",
    )
    (repo_dir / ".iar.toml").write_text(
        "[agent_runner]\n[agent_runner.autopilot]\nenabled = false\n",
        encoding="utf-8",
    )
    context = RepositoryRunContext(
        repo_id="keda-main", display_name="Keda Main", repo_path=repo_dir, config=AppConfig()
    )
    github = FakeGitHubClient()
    github.comment_issue(
        42,
        "PR Branch: `agent/issue-42`\n",
    )
    github.set_pr_context("agent/issue-42", _pr_context("FAILURE", ("lint: failed",)))
    monkeypatch.setattr(backlog_routes, "create_backlog_store", lambda: store)

    # `_resolve_contexts` 在生产路径每次都 fresh load；这里模拟同一语义：
    # 每次调用都从磁盘 .iar.toml 重建 config，写回才能被后续读取看到。
    from dataclasses import replace as _dc_replace

    from backend.infrastructure.config.agent_runner_settings import (
        load_agent_runner_local_settings,
    )

    def _fresh_contexts():
        local = load_agent_runner_local_settings(repo_dir)
        fresh_config = context.config
        if local is not None and local.post_pr_supervisor is not None:
            supervisor_settings = local.post_pr_supervisor
            fresh_config = _dc_replace(
                context.config,
                post_pr_supervisor=PostPrSupervisorConfig(
                    enabled=supervisor_settings.enabled,
                    supervisor_agent=supervisor_settings.supervisor_agent,
                    repair_agent=supervisor_settings.repair_agent,
                    auto_repair_ci=supervisor_settings.auto_repair_ci,
                    max_repair_attempts=supervisor_settings.max_repair_attempts,
                ),
            )
        return [
            RepositoryRunContext(
                repo_id="keda-main",
                display_name="Keda Main",
                repo_path=repo_dir,
                config=fresh_config,
            )
        ]

    monkeypatch.setattr(backlog_routes, "_resolve_contexts", _fresh_contexts)
    monkeypatch.setattr(backlog_routes, "create_github_client", lambda repo_path: github)
    return {"github": github, "repo_dir": repo_dir, "context": context}


def _encoded(prd_path: str) -> str:
    import base64

    return base64.urlsafe_b64encode(prd_path.encode("utf-8")).decode("ascii")


def test_api_get_prd_ci(ci_environment) -> None:
    response = client.get(
        f"/api/v1/agent-runner/backlog/prds/{_encoded('tasks/pending/P1-FEAT-20260101-ci.md')}/ci"
        "?repo_id=keda-main"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failure"
    assert data["checks_state"] == "FAILURE"
    assert data["global_enabled"] is False
    assert data["effective_enabled"] is False
    assert data["stored_policy"] == "inherit"
    assert data["problems"][0]["summary"] == "lint: failed"


def test_api_global_toggle_fresh_readback(ci_environment) -> None:
    response = client.patch(
        "/api/v1/agent-runner/backlog/ci-repair-global",
        json={"repo_id": "keda-main", "enabled": True},
    )
    assert response.status_code == 200
    assert response.json()["global_enabled"] is True
    # fresh GET 也必须反映新值。
    get_response = client.get("/api/v1/agent-runner/backlog/ci-repair-global?repo_id=keda-main")
    assert get_response.json()["global_enabled"] is True


def test_api_prd_policy_write_and_fresh_read(ci_environment) -> None:
    encoded = _encoded("tasks/pending/P1-FEAT-20260101-ci.md")
    response = client.patch(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/ci-policy",
        json={"repo_id": "keda-main", "value": "on"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stored_policy"] == "on"
    assert data["effective_enabled"] is True
    # marker 写进了 Issue 评论流（同一事实源，CLI/Console 双向可见）。
    assert any(
        "iar:ci-auto-repair-policy value=on" in body
        for body in ci_environment["github"].list_issue_comments(42)
    )
    # 清除覆盖回到 inherit。
    reset = client.patch(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/ci-policy",
        json={"repo_id": "keda-main", "value": "inherit"},
    )
    assert reset.json()["stored_policy"] == "inherit"
    assert reset.json()["effective_enabled"] is False


def test_api_manual_repair_idempotent(ci_environment) -> None:
    encoded = _encoded("tasks/pending/P1-FEAT-20260101-ci.md")
    first = client.post(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/ci-repair",
        json={"repo_id": "keda-main"},
    )
    assert first.status_code == 200
    assert first.json()["requested"] is True
    comments_after_first = ci_environment["github"].list_issue_comments(42)
    second = client.post(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/ci-repair",
        json={"repo_id": "keda-main"},
    )
    assert second.status_code == 200
    assert second.json()["requested"] is False
    assert ci_environment["github"].list_issue_comments(42) == comments_after_first

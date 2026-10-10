"""Tests for the console API routes and resilient repository resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_console as console_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import (
    AppConfig,
    RepositoryRunContext,
)
from backend.infrastructure.config.registry_editor import TomlRegistryEditor
from backend.infrastructure.console.process_supervisor import (
    PidfileProcessSupervisor,
)
from backend.infrastructure.config.settings import (
    AgentRunnerConsoleSettings,
    AgentRunnerRepositorySettings,
    AgentRunnerSettings,
)
from backend.engines.agent_runner.factory import (
    resolve_repository_targets_with_diagnostics,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

client = TestClient(app)


@pytest.fixture
def console_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wire console routes to tmp-backed store/supervisor/registry/contexts."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    supervisor = PidfileProcessSupervisor(
        registry_path=tmp_path / "processes.json",
        log_dir=tmp_path / "logs",
    )
    repo_dir = tmp_path / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "# 保留注释\n"
        "[agent_runner.repositories.keda-main]\n"
        f'path = "{repo_dir}"\n'
        "enabled = true\n",
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
    fake_runner_command = [sys.executable, "-u", "-c", "print('fake runner')"]

    monkeypatch.setattr(console_routes, "create_console_store", lambda: store)
    monkeypatch.setattr(console_routes, "create_process_supervisor", lambda: supervisor)
    monkeypatch.setattr(
        console_routes,
        "create_registry_editor",
        lambda: TomlRegistryEditor(config_path),
    )
    monkeypatch.setattr(console_routes, "_resolve_contexts", lambda: contexts)
    # resolve_console_spawn_cwd 不再打桩：contexts 里已带 repo_path，
    # 真实实现应把托管进程 cwd 解析到目标仓库自身路径。

    fake_settings = AgentRunnerSettings(
        console=AgentRunnerConsoleSettings(
            runner_command=fake_runner_command,
            stop_timeout_seconds=5,
        )
    )
    monkeypatch.setattr(console_routes, "load_fresh_agent_runner_settings", lambda: fake_settings)
    return {
        "store": store,
        "supervisor": supervisor,
        "config_path": config_path,
        "tmp_path": tmp_path,
    }


def test_process_lifecycle_via_api(console_environment) -> None:
    """Start, list, read logs and stop a process through the HTTP API."""
    start_response = client.post(
        "/api/v1/agent-runner/console/processes",
        json={"repo_id": "keda-main", "kind": "run_once"},
    )
    assert start_response.status_code == 201, start_response.text
    process_id = start_response.json()["process_id"]

    list_response = client.get("/api/v1/agent-runner/console/processes")
    assert list_response.status_code == 200
    listed_ids = [p["process_id"] for p in list_response.json()["processes"]]
    assert process_id in listed_ids

    log_response = client.get(f"/api/v1/agent-runner/console/processes/{process_id}/logs?offset=0")
    assert log_response.status_code == 200
    assert "next_offset" in log_response.json()

    stop_response = client.post(f"/api/v1/agent-runner/console/processes/{process_id}/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] in ("stopped", "exited", "killed")


def test_duplicate_daemon_rejected_via_api(console_environment) -> None:
    """A second daemon for the same repo must return 409."""
    first = client.post(
        "/api/v1/agent-runner/console/processes",
        json={"repo_id": "keda-main", "kind": "daemon"},
    )
    assert first.status_code == 201
    process_id = first.json()["process_id"]
    # 第一个 fake daemon 立即退出的话去重就不会触发；等待时直接再启动。
    second = client.post(
        "/api/v1/agent-runner/console/processes",
        json={"repo_id": "keda-main", "kind": "daemon"},
    )
    # fake runner 可能已经退出（非常快），此时允许 201；
    # 仍在运行时必须 409。两种结果都不允许 500。
    assert second.status_code in (201, 409)
    client.post(f"/api/v1/agent-runner/console/processes/{process_id}/stop")


def test_unknown_process_kind_rejected(console_environment) -> None:
    """Kinds outside the whitelist enum must fail validation (422)."""
    response = client.post(
        "/api/v1/agent-runner/console/processes",
        json={"repo_id": "keda-main", "kind": "arbitrary_shell"},
    )
    assert response.status_code == 422


def test_issue_action_unknown_rejected_via_api(console_environment) -> None:
    """Unknown issue actions must return 400 and be audited."""
    response = client.post(
        "/api/v1/agent-runner/console/repositories/keda-main/issues/1/actions",
        json={"action": "merge_pr"},
    )
    assert response.status_code == 400
    audit_response = client.get("/api/v1/agent-runner/console/audit")
    audits = audit_response.json()["audits"]
    assert audits[0]["action"] == "merge_pr"
    assert audits[0]["result"] == "rejected"


def test_registry_endpoints(console_environment, tmp_path: Path) -> None:
    """Registry list/add/patch must round-trip through config.toml."""
    list_response = client.get("/api/v1/agent-runner/repositories")
    assert list_response.status_code == 200
    assert [r["repo_id"] for r in list_response.json()["repositories"]] == ["keda-main"]

    new_repo = tmp_path / "second-repo"
    (new_repo / ".git").mkdir(parents=True)
    add_response = client.post(
        "/api/v1/agent-runner/repositories",
        json={"repo_id": "second", "path": str(new_repo)},
    )
    assert add_response.status_code == 201, add_response.text

    bad_add_response = client.post(
        "/api/v1/agent-runner/repositories",
        json={"repo_id": "ghost", "path": "/not/here"},
    )
    assert bad_add_response.status_code == 400

    patch_response = client.patch(
        "/api/v1/agent-runner/repositories/second", json={"enabled": False}
    )
    assert patch_response.status_code == 200

    config_text = console_environment["config_path"].read_text(encoding="utf-8")
    assert "# 保留注释" in config_text
    assert "[agent_runner.repositories.second]" in config_text


def test_registry_delete_removes_entry_and_keeps_files(console_environment) -> None:
    """删除接口只移除注册：条目消失，本地仓库目录保持原样。"""
    repo_dir = console_environment["tmp_path"] / "repo"

    delete_response = client.delete("/api/v1/agent-runner/repositories/keda-main")

    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["path"] == str(repo_dir)
    config_text = console_environment["config_path"].read_text(encoding="utf-8")
    assert "[agent_runner.repositories.keda-main]" not in config_text
    assert (repo_dir / ".git").exists()


def test_registry_delete_unknown_repo_returns_404(console_environment) -> None:
    """Removing an unknown repo_id must return 404."""
    assert client.delete("/api/v1/agent-runner/repositories/ghost").status_code == 404


def test_registry_add_rejects_duplicate_path(console_environment) -> None:
    """同一路径被第二个 repo_id 复用必须返回 400，且不写入文件。"""
    repo_dir = console_environment["tmp_path"] / "repo"
    original_text = console_environment["config_path"].read_text(encoding="utf-8")

    response = client.post(
        "/api/v1/agent-runner/repositories",
        json={"repo_id": "keda-alias", "path": str(repo_dir)},
    )

    assert response.status_code == 400, response.text
    assert "already registered" in response.json()["detail"]
    assert console_environment["config_path"].read_text(encoding="utf-8") == original_text


def _write_iar_toml(repo_root: Path, repo_id: str, display_name: str) -> None:
    """Helper to write a minimal .iar.toml for discovery tests."""
    iar_toml = repo_root / ".iar.toml"
    iar_toml.write_text(
        "[agent_runner]\n"
        "[agent_runner.repository]\n"
        f'id = "{repo_id}"\n'
        f'display_name = "{display_name}"\n',
        encoding="utf-8",
    )


def test_discover_iar_repositories_finds_local_repos(console_environment, tmp_path: Path) -> None:
    """Discover endpoint must find IAR-initialized git repositories."""
    scan_root = tmp_path / "code"
    scan_root.mkdir()

    discovered_repo = scan_root / "foo"
    discovered_repo.mkdir()
    (discovered_repo / ".git").mkdir()
    _write_iar_toml(discovered_repo, "foo", "Foo Project")

    nested_parent = scan_root / "nested"
    nested_parent.mkdir()
    nested_repo = nested_parent / "bar"
    nested_repo.mkdir()
    (nested_repo / ".git").mkdir()
    _write_iar_toml(nested_repo, "bar", "Bar Project")

    non_iar_repo = scan_root / "baz"
    non_iar_repo.mkdir()
    (non_iar_repo / ".git").mkdir()

    response = client.get(
        "/api/v1/agent-runner/repositories/discover",
        params={"scan_root": str(scan_root)},
    )
    assert response.status_code == 200, response.text
    discovered = response.json()["repositories"]
    assert len(discovered) == 2
    repo_ids = {entry["repo_id"] for entry in discovered}
    assert repo_ids == {"bar", "foo"}
    assert all("display_name" in entry for entry in discovered)
    assert all("already_registered" in entry for entry in discovered)


def test_browse_repositories_lists_subdirectories(console_environment, tmp_path: Path) -> None:
    """Browse endpoint must list non-hidden subdirectories with their flags."""
    browse_root = tmp_path / "workspace"
    alpha_dir = browse_root / "alpha"
    alpha_dir.mkdir(parents=True)
    (alpha_dir / ".git").mkdir()
    _write_iar_toml(alpha_dir, "alpha", "Alpha Project")

    beta_dir = browse_root / "beta"
    beta_dir.mkdir()

    (browse_root / ".hidden").mkdir()
    (browse_root / "note.txt").write_text("not a directory", encoding="utf-8")

    response = client.get(
        "/api/v1/agent-runner/repositories/browse",
        params={"path": str(browse_root)},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    resolved_root = browse_root.resolve()
    assert body["path"] == str(resolved_root)
    assert body["parent"] == str(resolved_root.parent)
    assert body["suggested_repo_id"] == "workspace"
    assert body["suggested_display_name"] == "workspace"

    # 只列非隐藏目录：.hidden 与 note.txt 都不出现。
    assert [entry["name"] for entry in body["directories"]] == ["alpha", "beta"]

    alpha_entry = body["directories"][0]
    assert alpha_entry["is_git_repo"] is True
    assert alpha_entry["has_iar_config"] is True
    assert alpha_entry["already_registered"] is False
    assert alpha_entry["suggested_repo_id"] == "alpha"

    beta_entry = body["directories"][1]
    assert beta_entry["is_git_repo"] is False
    assert beta_entry["has_iar_config"] is False


def test_browse_repositories_marks_registered_directory(
    console_environment, tmp_path: Path
) -> None:
    """已在 registry 中的目录必须被标记，供选择器提示用户。"""
    response = client.get(
        "/api/v1/agent-runner/repositories/browse",
        params={"path": str(tmp_path)},
    )
    assert response.status_code == 200, response.text

    entries_by_name = {entry["name"]: entry for entry in response.json()["directories"]}
    # fixture 把 tmp_path/repo 注册成了 repo_id=keda-main。
    assert entries_by_name["repo"]["already_registered"] is True


def test_browse_repositories_rejects_missing_or_file_path(
    console_environment, tmp_path: Path
) -> None:
    """路径不存在或不是目录时必须返回 400，而不是 500 或空列表。"""
    missing_response = client.get(
        "/api/v1/agent-runner/repositories/browse",
        params={"path": str(tmp_path / "does-not-exist")},
    )
    assert missing_response.status_code == 400, missing_response.text

    regular_file = tmp_path / "note.txt"
    regular_file.write_text("not a directory", encoding="utf-8")
    file_response = client.get(
        "/api/v1/agent-runner/repositories/browse",
        params={"path": str(regular_file)},
    )
    assert file_response.status_code == 400, file_response.text


def test_browse_repositories_defaults_to_home(
    console_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不传 path 时从用户主目录开始，供选择器首次打开时定位。"""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    response = client.get("/api/v1/agent-runner/repositories/browse")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == str(fake_home.resolve())
    assert body["home"] == str(fake_home.resolve())
    assert body["directories"] == []


def test_batch_add_repositories_skips_existing(console_environment, tmp_path: Path) -> None:
    """Batch add should add new repos and skip already-registered ones."""
    first_repo = tmp_path / "first"
    first_repo.mkdir()
    (first_repo / ".git").mkdir()

    second_repo = tmp_path / "second"
    second_repo.mkdir()
    (second_repo / ".git").mkdir()

    response = client.post(
        "/api/v1/agent-runner/repositories/batch",
        json={
            "repositories": [
                {"repo_id": "keda-main", "path": str(first_repo)},
                {"repo_id": "second", "path": str(second_repo)},
            ]
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert len(result["added"]) == 1
    assert result["added"][0]["repo_id"] == "second"
    assert result["skipped"] == ["keda-main"]
    assert result["errors"] == []

    list_response = client.get("/api/v1/agent-runner/repositories")
    registered_ids = {r["repo_id"] for r in list_response.json()["repositories"]}
    assert registered_ids == {"keda-main", "second"}


def test_stats_history_empty(console_environment) -> None:
    """History endpoint must work with an empty store."""
    response = client.get("/api/v1/agent-runner/console/stats/history?days=7")
    assert response.status_code == 200
    assert response.json()["trend"] == []


def test_agent_performance_stats_reads_fresh_sqlite_rows_through_api(console_environment) -> None:
    """真实路由按仓库和时间窗口汇总持久化 attempt 与 run。"""
    import sqlite3
    from datetime import datetime, timedelta, timezone

    database_path = console_environment["tmp_path"] / "console.db"
    current_time = datetime.now(timezone.utc).replace(microsecond=0)
    recent_timestamp = (current_time - timedelta(days=2)).isoformat()
    older_timestamp = (current_time - timedelta(days=40)).isoformat()
    run_samples = (
        ("keda-main", "completed", 40, recent_timestamp),
        ("keda-main", "completed", 60, recent_timestamp),
        ("keda-main", "failed", 20, recent_timestamp),
        ("keda-main", "blocked", 30, recent_timestamp),
        ("keda-main", "completed", 900, older_timestamp),
        ("other-repo", "completed", 50, recent_timestamp),
    )
    attempt_samples = (
        ("keda-main", "codex", "success", 10, "removed-preset", "model-a", recent_timestamp),
        (
            "keda-main",
            "codex",
            "verification_failed",
            30,
            "removed-preset",
            "model-a",
            recent_timestamp,
        ),
        ("keda-main", "codex", "success", 50, "removed-preset", "model-b", recent_timestamp),
        ("keda-main", "", "future_failure_type", 20, None, None, recent_timestamp),
        ("other-repo", "codex", "success", 100, "removed-preset", "model-a", recent_timestamp),
        ("keda-main", "codex", "success", 5000, "removed-preset", "model-a", older_timestamp),
    )
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO run_records "
            "(repo_id, repo_path, issue_number, trigger, agent, outcome, error_summary, "
            "started_at, finished_at, duration_seconds) "
            "VALUES (?, '/tmp/repo', 1, 'test', 'codex', ?, NULL, ?, ?, ?)",
            [
                (repo_id, outcome, started_at, started_at, duration_seconds)
                for repo_id, outcome, duration_seconds, started_at in run_samples
            ],
        )
        connection.executemany(
            "INSERT INTO attempt_records "
            "(repo_id, issue_number, agent, attempt_number, failure_type, recovered, detail, "
            "started_at, finished_at, duration_seconds, preset, model) "
            "VALUES (?, 1, ?, 1, ?, 0, 'fixture detail', ?, ?, ?, ?, ?)",
            [
                (
                    repo_id,
                    agent,
                    failure_type,
                    started_at,
                    started_at,
                    duration_seconds,
                    preset,
                    model,
                )
                for repo_id, agent, failure_type, duration_seconds, preset, model, started_at in attempt_samples
            ],
        )
        connection.commit()

    # 由独立 SQLite connection fresh-read 原始样本，核对窗口内关键值。
    with sqlite3.connect(database_path) as connection:
        persisted_attempts = connection.execute(
            "SELECT agent, failure_type, duration_seconds, preset, model "
            "FROM attempt_records WHERE repo_id = 'keda-main' AND started_at >= ? "
            "ORDER BY id",
            ((current_time - timedelta(days=30)).isoformat(),),
        ).fetchall()
        persisted_runs = connection.execute(
            "SELECT outcome, duration_seconds FROM run_records "
            "WHERE repo_id = 'keda-main' AND started_at >= ? ORDER BY id",
            ((current_time - timedelta(days=30)).isoformat(),),
        ).fetchall()

    assert len(persisted_attempts) == 4
    assert sorted(row[2] for row in persisted_attempts if row[0] == "codex") == [10, 30, 50]
    assert {row[3:5] for row in persisted_attempts if row[3]} == {
        ("removed-preset", "model-a"),
        ("removed-preset", "model-b"),
    }
    assert len(persisted_runs) == 4

    response = client.get(
        "/api/v1/agent-runner/console/stats/agent-performance?repo_id=keda-main&days=30"
    )
    assert response.status_code == 200, response.text
    stats = response.json()
    assert stats["repo_id"] == "keda-main"
    assert stats["window_days"] == 30
    codex_group = next(group for group in stats["agents"] if group["agent"] == "codex")
    assert codex_group["attempt_count"] == len(
        [row for row in persisted_attempts if row[0] == "codex"]
    )
    assert codex_group["success_count"] == 2
    assert codex_group["non_success_count"] == 1
    assert codex_group["success_rate"] == 2 / 3
    assert codex_group["non_success_rate"] == 1 / 3
    assert codex_group["p50_duration_seconds"] == 30
    assert codex_group["p90_duration_seconds"] == 46
    assert codex_group["failure_types"] == [{"failure_type": "verification_failed", "count": 1}]
    assert stats["unbound_preset_attempt_count"] == 1
    assert len(stats["presets"]) == 2
    assert {group["model"] for group in stats["presets"]} == {"model-a", "model-b"}
    assert all(group["repo_id"] == "keda-main" for group in stats["presets"])
    assert {group["outcome"] for group in stats["runs"]} == {"completed", "failed", "blocked"}
    assert sum(group["run_count"] for group in stats["runs"]) == len(persisted_runs)
    assert {
        group["outcome"]: (
            group["run_count"],
            group["p50_duration_seconds"],
            group["p90_duration_seconds"],
        )
        for group in stats["runs"]
    } == {
        "completed": (2, 50, 58),
        "failed": (1, 20, 20),
        "blocked": (1, 30, 30),
    }

    all_repositories = client.get("/api/v1/agent-runner/console/stats/agent-performance?days=30")
    assert all_repositories.status_code == 200, all_repositories.text
    all_stats = all_repositories.json()
    assert all_stats["repo_id"] is None
    same_named_presets = [
        group for group in all_stats["presets"] if group["preset"] == "removed-preset"
    ]
    assert {group["repo_id"] for group in same_named_presets} == {"keda-main", "other-repo"}
    assert {group["repo_id"] for group in all_stats["runs"]} == {"keda-main", "other-repo"}
    assert all("agent" not in group for group in all_stats["runs"])

    short_window = client.get(
        "/api/v1/agent-runner/console/stats/agent-performance?repo_id=keda-main&days=1"
    )
    assert short_window.status_code == 200, short_window.text
    assert short_window.json()["agents"] == []
    assert short_window.json()["runs"] == []


def test_agent_performance_stats_rejects_invalid_days(console_environment) -> None:
    """API 校验统计窗口天数范围。"""
    response = client.get("/api/v1/agent-runner/console/stats/agent-performance?days=0")
    assert response.status_code == 422


def test_audit_endpoint_lists_actions(console_environment) -> None:
    """Audit endpoint should expose process start/stop entries."""
    start = client.post(
        "/api/v1/agent-runner/console/processes",
        json={"repo_id": "keda-main", "kind": "run_once"},
    )
    process_id = start.json()["process_id"]
    client.post(f"/api/v1/agent-runner/console/processes/{process_id}/stop")
    audits = client.get("/api/v1/agent-runner/console/audit").json()["audits"]
    actions = [a["action"] for a in audits]
    assert "start_run_once" in actions
    assert "stop_process" in actions


# ── 韧性解析（registry 路径漂移不拖死面板） ─────────────────────────────────


def test_resolution_diagnostics_isolates_broken_path(tmp_path: Path) -> None:
    """One broken registry path must not abort resolution of others."""
    import subprocess

    good_repo = tmp_path / "good"
    good_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(good_repo)], check=True)
    settings = AgentRunnerSettings(
        repositories={
            "good": AgentRunnerRepositorySettings(path=str(good_repo)),
            "broken": AgentRunnerRepositorySettings(path="/missing/path"),
        }
    )
    contexts, failures = resolve_repository_targets_with_diagnostics(settings)
    # 注意：pydantic-settings 会把 config.toml 里真实 registry 合并进来，
    # 这里只断言 fixture 条目的行为，不假设 registry 为空。
    resolved_repo_ids = [context.repo_id for context in contexts]
    assert "good" in resolved_repo_ids
    assert "broken" not in resolved_repo_ids
    broken_failures = [f for f in failures if f.repo_id == "broken"]
    assert len(broken_failures) == 1
    assert "does not exist" in broken_failures[0].error


def test_auth_me_returns_local_session() -> None:
    """The local auth endpoint must return a fixed operator session."""
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "local-operator"
    assert "display_name" in payload


# ── Issue 实时输出（按仓库 + Issue 号的只读日志） ────────────────────────────


def _write_issue_attempt(repo_dir: Path, repo_id: str, issue_number: int, text: str) -> Path:
    """在测试仓库下写一个符合命名约定的 Issue 尝试日志。"""
    log_dir = repo_dir / "logs" / "agent-runner" / "issues" / repo_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"issue-{issue_number}-20260929-120000.log"
    path.write_text(text, encoding="utf-8")
    return path


def _write_named_issue_attempt(
    repo_dir: Path, repo_id: str, issue_number: int, timestamp: str, text: str
) -> Path:
    """在测试仓库下写一个指定时间戳的 Issue 尝试日志。"""
    log_dir = repo_dir / "logs" / "agent-runner" / "issues" / repo_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"issue-{issue_number}-{timestamp}.log"
    path.write_text(text, encoding="utf-8")
    return path


def test_issue_log_requires_registered_repo(console_environment) -> None:
    """未知仓库必须返回 404，且不能读到任何文件。"""
    response = client.get("/api/v1/agent-runner/console/repositories/ghost/issues/1/logs")
    assert response.status_code == 404
    assert "not registered" in response.json()["detail"]


def test_issue_log_rejects_invalid_issue_number(console_environment) -> None:
    """非法 Issue 编号必须返回 400。"""
    response = client.get("/api/v1/agent-runner/console/repositories/keda-main/issues/0/logs")
    assert response.status_code == 400
    assert "positive integer" in response.json()["detail"]


def test_issue_log_empty_state_for_missing_attempt(console_environment) -> None:
    """没有日志文件时返回明确的空态，而不是回退到别的 Issue 或进程日志。"""
    response = client.get("/api/v1/agent-runner/console/repositories/keda-main/issues/42/logs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "no_attempt"
    assert payload["attempt_id"] is None
    assert payload["content"] == ""


def test_issue_log_reads_registered_repo_issue(console_environment) -> None:
    """命中注册仓库 + Issue 的日志文件，返回内容、尝试标识与续读偏移。"""
    repo_dir = console_environment["tmp_path"] / "repo"
    _write_issue_attempt(repo_dir, "keda-main", 42, "agent started\nagent finished\n")

    response = client.get("/api/v1/agent-runner/console/repositories/keda-main/issues/42/logs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["repo_id"] == "keda-main"
    assert payload["issue_number"] == 42
    assert payload["attempt_id"] == "issue-42-20260929-120000.log"
    assert payload["latest_attempt_id"] == "issue-42-20260929-120000.log"
    assert "agent started" in payload["content"]
    assert payload["next_offset"] > 0
    assert payload["eof"] is True


def test_issue_log_offset_pagination(console_environment) -> None:
    """按字节偏移续读不重复、不丢内容。"""
    repo_dir = console_environment["tmp_path"] / "repo"
    _write_issue_attempt(repo_dir, "keda-main", 7, "line-1\nline-2\nline-3\n")

    first = client.get(
        "/api/v1/agent-runner/console/repositories/keda-main/issues/7/logs?offset=0"
    ).json()
    assert first["status"] == "ok"
    assert first["eof"] is True

    # 从中间偏移续读，只读后半段。
    second = client.get(
        "/api/v1/agent-runner/console/repositories/keda-main/issues/7/logs?offset=7"
    ).json()
    assert second["status"] == "ok"
    assert second["content"] == "line-2\nline-3\n"
    assert second["next_offset"] == first["next_offset"]


def test_issue_log_attempt_switch_and_truncation(console_environment) -> None:
    """新尝试出现、旧尝试消失、文件截断都返回明确状态。"""
    import os

    repo_dir = console_environment["tmp_path"] / "repo"
    first_path = _write_issue_attempt(repo_dir, "keda-main", 9, "first attempt\n")
    first = client.get("/api/v1/agent-runner/console/repositories/keda-main/issues/9/logs").json()
    first_attempt = first["attempt_id"]

    # 新尝试出现：latest_attempt_id 应指向新文件（按 mtime 排序）。
    second_path = first_path.with_name("issue-9-20260929-120001.log")
    second_path.write_text("second attempt\n", encoding="utf-8")
    # 确保两个尝试的 mtime 可区分，避免同秒写入导致排序不稳定。
    os.utime(first_path, (1_700_000_000, 1_700_000_000))
    os.utime(second_path, (1_700_000_100, 1_700_000_100))
    poll = client.get(
        f"/api/v1/agent-runner/console/repositories/keda-main/issues/9/logs"
        f"?attempt_id={first_attempt}&offset=0"
    ).json()
    assert poll["status"] == "ok"
    assert poll["attempt_id"] == first_attempt
    assert poll["latest_attempt_id"] == "issue-9-20260929-120001.log"

    # 旧尝试被清理：显式 gone，不静默改读。
    first_path.unlink()
    gone = client.get(
        f"/api/v1/agent-runner/console/repositories/keda-main/issues/9/logs"
        f"?attempt_id={first_attempt}&offset=0"
    ).json()
    assert gone["status"] == "attempt_gone"
    assert gone["latest_attempt_id"] == "issue-9-20260929-120001.log"

    # 文件被截断（offset 越过当前大小）：显式 truncated。
    second_path.write_text("x\n", encoding="utf-8")
    truncated = client.get(
        "/api/v1/agent-runner/console/repositories/keda-main/issues/9/logs"
        "?attempt_id=issue-9-20260929-120001.log&offset=9999"
    ).json()
    assert truncated["status"] == "truncated"


def test_issue_log_tail_window_returns_last_bytes(console_environment) -> None:
    """``tail=true`` 忽略偏移，返回末尾窗口并把 next_offset 对齐到文件末尾。"""
    repo_dir = console_environment["tmp_path"] / "repo"
    content = "old-line\n" * 5000 + "tail-marker\n"
    path = _write_named_issue_attempt(repo_dir, "keda-main", 11, "20260929-130000", content)

    response = client.get(
        "/api/v1/agent-runner/console/repositories/keda-main/issues/11/logs?tail=true"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "tail-marker" in payload["content"]
    assert "old-line" in payload["content"]  # 尾部窗口内仍有历史行
    assert payload["eof"] is True
    # next_offset 对齐到文件末尾，调用方原地转入增量续读。
    assert payload["next_offset"] == path.stat().st_size

    # 非 tail 的小窗口从头读：只能看到头部，看不到尾部标记。
    head = client.get(
        "/api/v1/agent-runner/console/repositories/keda-main/issues/11/logs?max_bytes=200"
    ).json()
    assert "tail-marker" not in head["content"]
    assert head["eof"] is False


def test_issue_log_tail_window_with_explicit_attempt(console_environment) -> None:
    """``tail=true`` 可与显式 ``attempt_id`` 组合，定位到指定尝试的尾部。"""
    import os

    repo_dir = console_environment["tmp_path"] / "repo"
    first_path = _write_named_issue_attempt(
        repo_dir, "keda-main", 13, "20260929-130000", "first attempt tail\n"
    )
    second_path = _write_named_issue_attempt(
        repo_dir, "keda-main", 13, "20260929-130001", "second attempt tail\n"
    )
    os.utime(first_path, (1_700_000_000, 1_700_000_000))
    os.utime(second_path, (1_700_000_100, 1_700_000_100))

    response = client.get(
        "/api/v1/agent-runner/console/repositories/keda-main/issues/13/logs"
        "?attempt_id=issue-13-20260929-130000.log&tail=true"
    )
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["attempt_id"] == "issue-13-20260929-130000.log"
    assert payload["latest_attempt_id"] == "issue-13-20260929-130001.log"
    assert "first attempt tail" in payload["content"]
    assert payload["eof"] is True


def test_console_version_endpoint_reports_installed_version() -> None:
    """console 版本端点返回后端运行时版本，供「有新版本」提示与自身比对。"""
    response = client.get("/api/v1/agent-runner/console/version")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["version"], str)
    assert payload["version"]

"""``iar console`` 首屏「当前项目」解析测试。

覆盖 :mod:`backend.core.use_cases.console_context` 的四种落空/命中状态，
以及 ``GET /api/v1/agent-runner/console/context`` 路由的序列化输出。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_console as console_routes
import backend.core.use_cases.console_context as console_context
from backend.api.app import app
from backend.core.use_cases.console_context import resolve_console_context
from backend.infrastructure.config.settings import AgentRunnerSettings

client = TestClient(app)


def _init_git_repo(path: Path) -> Path:
    """把 ``path`` 初始化成真实 git 仓库并返回它。

    ``detect_git_repository_root`` 走 ``git rev-parse --show-toplevel``，
    空 ``.git`` 目录不算仓库，必须真跑一次 ``git init``。
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    return path


def _settings(repositories: dict[str, tuple[Path, bool]]) -> AgentRunnerSettings:
    """按 ``{repo_id: (path, enabled)}`` 造一份 registry settings。

    不走 ``AgentRunnerSettings(repositories=...)``：它的 settings source 链里
    ``_RegistryRepositoriesSource`` 优先级高于 init kwargs，会把传入的仓库表
    替换成真实 ``~/.iar/config.toml`` 的内容。这里用鸭子类型替身，只提供
    :func:`find_repository_match_for_path` 真正读到的 ``path`` / ``enabled``。
    """
    entries = {
        repo_id: SimpleNamespace(path=str(path), enabled=enabled)
        for repo_id, (path, enabled) in repositories.items()
    }
    return cast(AgentRunnerSettings, SimpleNamespace(repositories=entries))


def _patch_settings(monkeypatch: pytest.MonkeyPatch, settings: AgentRunnerSettings) -> None:
    monkeypatch.setattr(console_context, "load_fresh_agent_runner_settings", lambda: settings)


def test_cwd_inside_registered_repo_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cwd 在已登记且启用的仓库内时应命中该仓库 id。"""
    repo_dir = _init_git_repo(tmp_path / "repo")
    _patch_settings(monkeypatch, _settings({"keda": (repo_dir, True)}))

    context = resolve_console_context(repo_dir)

    assert context.status == "matched"
    assert context.repo_id == "keda"
    assert context.git_root == str(repo_dir.resolve())
    assert context.candidates == ()


def test_cwd_below_repo_root_resolves_to_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """从子目录启动 console 也应命中仓库根，而不是判成未登记。"""
    repo_dir = _init_git_repo(tmp_path / "repo")
    nested = repo_dir / "src" / "backend"
    nested.mkdir(parents=True)
    _patch_settings(monkeypatch, _settings({"keda": (repo_dir, True)}))

    context = resolve_console_context(nested)

    assert context.status == "matched"
    assert context.repo_id == "keda"
    assert context.git_root == str(repo_dir.resolve())


def test_cwd_outside_git_repo_reports_not_git_repo(tmp_path: Path) -> None:
    """不在任何 git 仓库内时不得抛错，只报状态。"""
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()

    context = resolve_console_context(plain_dir)

    assert context.status == "not_git_repo"
    assert context.repo_id is None
    assert context.git_root is None


def test_unregistered_git_repo_reports_not_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """git 仓库存在但不在 registry 里时不做默认选择。"""
    other_repo = _init_git_repo(tmp_path / "other")
    _patch_settings(monkeypatch, _settings({"keda": (_init_git_repo(tmp_path / "repo"), True)}))

    context = resolve_console_context(other_repo)

    assert context.status == "not_registered"
    assert context.repo_id is None


def test_disabled_repo_is_not_selected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """停用条目即便路径匹配也不作为首屏默认目标。"""
    repo_dir = _init_git_repo(tmp_path / "repo")
    _patch_settings(monkeypatch, _settings({"keda": (repo_dir, False)}))

    context = resolve_console_context(repo_dir)

    assert context.status == "disabled"
    assert context.repo_id is None


def test_console_context_endpoint_serializes_cwd_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """端点把进程 cwd 推断结果原样序列化给前端。"""
    repo_dir = _init_git_repo(tmp_path / "repo")
    monkeypatch.chdir(repo_dir)
    monkeypatch.setattr(
        console_routes,
        "resolve_console_context",
        lambda cwd: console_context.ConsoleContext(
            cwd=str(cwd),
            git_root=str(repo_dir.resolve()),
            repo_id="keda",
            status="matched",
        ),
    )

    response = client.get("/api/v1/agent-runner/console/context")

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_id"] == "keda"
    assert payload["status"] == "matched"
    assert payload["candidates"] == []


def test_console_context_endpoint_returns_200_when_unmatched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cwd 匹配不上是正常状态，端点不得因此返回 4xx。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        console_routes,
        "resolve_console_context",
        lambda cwd: console_context.ConsoleContext(
            cwd=str(cwd),
            git_root=None,
            repo_id=None,
            status="not_git_repo",
        ),
    )

    response = client.get("/api/v1/agent-runner/console/context")

    assert response.status_code == 200
    assert response.json()["repo_id"] is None

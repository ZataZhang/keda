"""托管 runner 凭据交付与上游校验客户端的回归（Issue #265，FR-5）。

覆盖：按仓库目录隔离、路径逃逸拒绝、明文只落本仓库目录、删除后清空；GitHub 校验
只发只读 GET（绝不 POST/写操作）、Agent 探测只发一条请求且 key 不进 URL。
"""

from __future__ import annotations

import os
import stat
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from backend.engines.agent_runner.runner_credential_handoff import (
    TmpfsRunnerCredentialHandoff,
)
from backend.core.shared.models.hosted_console import RunnerGitHubCredential
from backend.infrastructure.http_clients.hosted_credential_checks import (
    GitHubReadOnlyVerifier,
    MinimalAgentKeyProber,
)
import backend.engines.agent_runner.hosted_console_composition as hosted_composition
import backend.engines.agent_runner.runner_credential_handoff as handoff_module
from backend.engines.agent_runner.hosted_console_composition import (
    ComposeRunnerControl,
    _github_remote_matches,
)


class TestHandoffIsolation:
    def test_production_handoff_rejects_non_tmpfs_before_writing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(handoff_module, "is_tmpfs_directory", lambda _path: False)

        with pytest.raises(OSError, match="must be mounted as Linux tmpfs"):
            TmpfsRunnerCredentialHandoff(tmp_path / "auth")

        assert not (tmp_path / "auth" / "repo-a").exists()

    def test_repo_directories_are_isolated(self, tmp_path: Path):
        handoff = TmpfsRunnerCredentialHandoff(tmp_path / "auth", require_tmpfs=False)
        handoff.materialize(
            "repo-a", RunnerGitHubCredential("github_pat_A", "octo-a"), "claude", "sk-ant-a"
        )
        handoff.materialize(
            "repo-b", RunnerGitHubCredential("github_pat_B", "octo-b"), "codex", "sk-b"
        )
        dir_a = Path(handoff.host_directory("repo-a"))
        dir_b = Path(handoff.host_directory("repo-b"))
        assert dir_a != dir_b
        gh_a = (dir_a / "gh" / "hosts.yml").read_text(encoding="utf-8")
        assert "github_pat_A" in gh_a and "github_pat_B" not in gh_a
        assert 'user: "octo-a"' in gh_a
        gh_b = (dir_b / "gh" / "hosts.yml").read_text(encoding="utf-8")
        assert "github_pat_B" in gh_b and "github_pat_A" not in gh_b

    def test_files_are_owner_only(self, tmp_path: Path):
        handoff = TmpfsRunnerCredentialHandoff(tmp_path / "auth", require_tmpfs=False)
        written = handoff.materialize(
            "repo-a", RunnerGitHubCredential("github_pat_A", "octo-a"), None, None
        )
        for path in written:
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_path_escape_rejected(self, tmp_path: Path):
        handoff = TmpfsRunnerCredentialHandoff(tmp_path / "auth", require_tmpfs=False)
        with pytest.raises(ValueError):
            handoff.materialize(
                "../evil", RunnerGitHubCredential("github_pat_X", "octo"), None, None
            )
        with pytest.raises(ValueError):
            handoff.host_directory("a/b")

    def test_erase_removes_only_own_repo(self, tmp_path: Path):
        handoff = TmpfsRunnerCredentialHandoff(tmp_path / "auth", require_tmpfs=False)
        handoff.materialize("repo-a", RunnerGitHubCredential("github_pat_A", "octo-a"), None, None)
        handoff.materialize("repo-b", RunnerGitHubCredential("github_pat_B", "octo-b"), None, None)
        assert handoff.erase("repo-a") is True
        assert not Path(handoff.host_directory("repo-a")).exists()
        assert Path(handoff.host_directory("repo-b")).exists()

    def test_symlink_cannot_alias_another_repository(self, tmp_path: Path):
        handoff = TmpfsRunnerCredentialHandoff(tmp_path / "auth", require_tmpfs=False)
        handoff.materialize("repo-b", RunnerGitHubCredential("pat-b", "octo-b"), None, None)
        repo_a = Path(handoff.host_directory("repo-a"))
        repo_a.symlink_to(Path(handoff.host_directory("repo-b")), target_is_directory=True)

        with pytest.raises(ValueError, match="is a symlink"):
            handoff.materialize("repo-a", RunnerGitHubCredential("pat-a", "octo-a"), None, None)

        repo_b = Path(handoff.host_directory("repo-b"))
        assert "pat-b" in (repo_b / "gh" / "hosts.yml").read_text(encoding="utf-8")

    def test_unsupported_provider_rejected(self, tmp_path: Path):
        handoff = TmpfsRunnerCredentialHandoff(tmp_path / "auth", require_tmpfs=False)
        with pytest.raises(ValueError):
            handoff.materialize("repo-a", None, "gemini", "key")


def test_runner_checkout_origin_must_match_configured_github_repository(tmp_path: Path):
    """固定 checkout 只有 origin 指向凭据所属仓库时才可用于启动。"""
    repository_path = tmp_path / "checkout"
    repository_path.mkdir()
    subprocess.run(["git", "init", str(repository_path)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_path),
            "remote",
            "add",
            "origin",
            "git@github.com:octo/repo.git",
        ],
        check=True,
        capture_output=True,
    )

    assert _github_remote_matches(repository_path, "octo/repo") is True
    assert _github_remote_matches(repository_path, "other/repo") is False


def test_hosted_runner_control_starts_only_configured_repo_with_auth_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """宿主控制器只把固定 repo 与其 tmpfs auth 路径交给 Compose。"""
    auth_root = tmp_path / "auth"
    hosted_settings = SimpleNamespace(
        repository_id="pilot",
        repository_path=str(tmp_path / "checkout"),
        runner_auth_root=str(auth_root),
        desktop_enabled=False,
    )
    captured_requests = []

    class _RecordingContainerController:
        def run_container_up(self, compose_file, request):
            captured_requests.append((compose_file, request))

    monkeypatch.setattr(hosted_composition, "_github_remote_matches", lambda *_args: True)
    monkeypatch.setattr(hosted_composition, "is_tmpfs_directory", lambda _path: True)
    monkeypatch.setattr(handoff_module, "is_tmpfs_directory", lambda _path: True)
    monkeypatch.setattr(hosted_composition, "_has_native_auth_files", lambda _path: True)
    monkeypatch.setattr(
        hosted_composition,
        "ContainerOpsController",
        _RecordingContainerController,
    )

    runner_control = ComposeRunnerControl(
        compose_file=tmp_path / "compose.yml",
        hosted_settings=hosted_settings,
    )

    assert runner_control.start_runner("pilot", "octo/repo") is True
    assert runner_control.start_runner("other", "octo/repo") is False
    assert len(captured_requests) == 1
    compose_file, request = captured_requests[0]
    assert compose_file == tmp_path / "compose.yml"
    assert request.repo_id == "pilot"
    assert request.hosted is True
    assert request.runner_auth_dir == str(auth_root / "pilot")
    assert not request.gh_token


def test_hosted_runner_stop_refuses_unconfigured_repository(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        repository_id="pilot",
        repository_path=str(tmp_path / "checkout"),
        runner_auth_root=str(tmp_path / "auth"),
    )
    stopped = []
    monkeypatch.setattr(
        hosted_composition,
        "run_container_down",
        lambda _compose_file: stopped.append(True),
    )

    control = ComposeRunnerControl(compose_file=tmp_path / "compose.yml", hosted_settings=settings)

    assert control.stop_runner("other") is False
    assert stopped == []


def test_hosted_runner_stop_passes_bound_repo_id_to_compose(tmp_path, monkeypatch):
    """托管 Compose down 获得固定仓库、tmpfs 与状态卷的插值变量。"""
    settings = SimpleNamespace(
        repository_id="pilot",
        repository_path=str(tmp_path / "checkout"),
        runner_auth_root=str(tmp_path / "auth"),
        desktop_enabled=False,
    )
    captured: list[tuple[Path, dict[str, str]]] = []

    class _NoopHandoff:
        def __init__(self, _runner_auth_root):
            pass

        def host_directory(self, repo_id):
            return str(tmp_path / "auth" / repo_id)

    monkeypatch.setattr(
        hosted_composition,
        "run_container_down",
        lambda compose_file, *, env_overrides: captured.append((compose_file, env_overrides)),
    )
    monkeypatch.setattr(hosted_composition, "TmpfsRunnerCredentialHandoff", _NoopHandoff)

    control = ComposeRunnerControl(compose_file=tmp_path / "compose.yml", hosted_settings=settings)

    assert control.stop_runner("pilot") is True
    assert len(captured) == 1
    compose_file, env_overrides = captured[0]
    assert compose_file == tmp_path / "compose.yml"
    assert env_overrides == {
        "KEDACODE_REPO_ID": "pilot",
        "IAR_REPO_ID": "pilot",
        "REPO_PATH": str(tmp_path / "checkout"),
        "KEDACODE_RUNNER_AUTH_DIR": str(tmp_path / "auth" / "pilot"),
        "KEDACODE_DESKTOP_ENABLED": "false",
        "KEDACODE_RUNNER_PROFILE": "latest",
    }


class _RecordingTransport:
    def __init__(self, responses):
        self._responses = responses
        self.calls: list[tuple] = []

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, body, headers))
        path = url.split("api.github.com")[-1] if "api.github.com" in url else url
        path = path.split("?")[0]
        return self._responses.get((method, path), self._responses.get("default", (200, "{}")))


class TestGitHubVerifierReadOnly:
    def test_only_get_requests_issued(self):
        transport = _RecordingTransport(
            {
                ("GET", "/user"): (200, '{"login":"octo"}'),
                ("GET", "/repos/o/r"): (200, "{}"),
                ("GET", "/repos/o/r/issues"): (200, "[]"),
                ("GET", "/repos/o/r/pulls"): (200, "[]"),
                ("GET", "/repos/o/r/contents"): (200, "[]"),
                "default": (200, "{}"),
            }
        )
        verifier = GitHubReadOnlyVerifier(transport=transport)
        result = verifier.verify("github_pat_X", "o/r")
        assert result.accepted is True
        assert all(method == "GET" for method, *_ in transport.calls)
        assert len(transport.calls) >= 4

    def test_permission_denied_maps_to_insufficient(self):
        transport = _RecordingTransport(
            {
                ("GET", "/user"): (200, '{"login":"octo"}'),
                ("GET", "/repos/o/r"): (200, "{}"),
                ("GET", "/repos/o/r/issues"): (403, "{}"),
                "default": (200, "{}"),
            }
        )
        result = GitHubReadOnlyVerifier(transport=transport).verify("github_pat_X", "o/r")
        assert result.accepted is False
        assert result.rejection == "github_permissions_insufficient"

    def test_repo_missing_maps_to_inaccessible(self):
        transport = _RecordingTransport(
            {
                ("GET", "/user"): (200, '{"login":"octo"}'),
                ("GET", "/repos/o/r"): (404, "{}"),
                "default": (200, "{}"),
            }
        )
        result = GitHubReadOnlyVerifier(transport=transport).verify("github_pat_X", "o/r")
        assert result.rejection == "github_repo_inaccessible"

    def test_token_never_in_url(self):
        transport = _RecordingTransport(
            {
                ("GET", "/user"): (200, '{"login":"octo"}'),
                ("GET", "/repos/o/r"): (200, "{}"),
                ("GET", "/repos/o/r/issues"): (200, "[]"),
                ("GET", "/repos/o/r/pulls"): (200, "[]"),
                ("GET", "/repos/o/r/contents"): (200, "[]"),
                "default": (200, "{}"),
            }
        )
        GitHubReadOnlyVerifier(transport=transport).verify("github_pat_TOPSECRET", "o/r")
        for _method, url, _body, _headers in transport.calls:
            assert "TOPSECRET" not in url


class TestAgentProber:
    def test_single_request_key_in_header_only(self):
        calls = []

        def transport(method, url, body, headers):
            calls.append((method, url, body, headers))
            return (200, "{}")

        MinimalAgentKeyProber(transport=transport).probe("claude", "sk-ant-api03-TOPSECRET")
        assert len(calls) == 1
        _method, url, _body, headers = calls[0]
        assert "TOPSECRET" not in url
        assert headers.get("x-api-key") == "sk-ant-api03-TOPSECRET"

    def test_failure_detail_redacts_key(self):
        def transport(method, url, body, headers):
            return (401, '{"error":{"key":"sk-ant-api03-TOPSECRET"}}')

        result = MinimalAgentKeyProber(transport=transport).probe(
            "claude", "sk-ant-api03-TOPSECRET"
        )
        assert result.accepted is False
        assert "TOPSECRET" not in result.detail

    def test_unsupported_provider_no_request(self):
        calls = []

        def transport(method, url, body, headers):
            calls.append(method)
            return (200, "{}")

        result = MinimalAgentKeyProber(transport=transport).probe("gemini", "key")
        assert result.accepted is False
        assert calls == []

"""托管 Console 认证与凭据 API 的集成回归（Issue #265，rv-4 / L562）。

以真实 SQLite + Fernet 文件 + tmpfs 目录装配服务，用 TestClient 走真实路由，
证明：未认证访问受保护 API 一律 401（本机 no-op 会话在托管模式不复存在）、
引导/登录/登出闭环、会话 cookie HttpOnly、凭据响应不回显 secret、GitHub/Agent
保存与成本门控。
"""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.app as app_module
import backend.api.routes.hosted_auth as hosted_auth
import backend.api.routes.hosted_credentials as hosted_credentials
from backend.core.shared.models.hosted_console import (
    AgentKeyProbeResult,
    GitHubTokenCheck,
)
from backend.core.use_cases.hosted_console import HostedConsoleService
from backend.engines.agent_runner.runner_credential_handoff import (
    TmpfsRunnerCredentialHandoff,
)
from backend.infrastructure.persistence.hosted_console_store import HostedConsoleStore
from backend.infrastructure.persistence.hosted_credential_vault import HostedCredentialVault


class _AcceptVerifier:
    def verify(self, token, owner_repo):
        return GitHubTokenCheck(accepted=True, login="octo")


class _AcceptProber:
    def probe(self, provider, api_key):
        return AgentKeyProbeResult(accepted=True)


class _RunnerControl:
    def stop_runner(self, repo_id):
        return True

    def start_runner(self, repo_id, owner_repo):
        return True


def _make_settings(tmp_path: Path, public_base_url: str = "http://127.0.0.1:8313"):
    return SimpleNamespace(
        hosted=SimpleNamespace(
            enabled=True,
            public_base_url=public_base_url,
            runner_auth_root=str(tmp_path / "auth"),
            console_db_path=str(tmp_path / "c.db"),
            credentials_file=str(tmp_path / "cred.json"),
            data_key_file=str(tmp_path / "key.bin"),
            session_ttl_seconds=3600,
        )
    )


@pytest.fixture
def hosted_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = _make_settings(tmp_path)
    service = HostedConsoleService(
        identity_store=HostedConsoleStore(Path(settings.hosted.console_db_path)),
        credential_repo=HostedCredentialVault(
            credentials_file=Path(settings.hosted.credentials_file),
            data_key_file=Path(settings.hosted.data_key_file),
        ),
        github_verifier=_AcceptVerifier(),
        agent_prober=_AcceptProber(),
        handoff=TmpfsRunnerCredentialHandoff(
            Path(settings.hosted.runner_auth_root), require_tmpfs=False
        ),
        runner_control=_RunnerControl(),
        deployment_id="dep-test",
    )
    for module in (app_module, hosted_auth, hosted_credentials):
        monkeypatch.setattr(module, "create_hosted_console_service", lambda _s, _svc=service: _svc)
        monkeypatch.setattr(module, "load_fresh_agent_runner_settings", lambda _s=settings: _s)
    monkeypatch.setattr(app_module, "hosted_console_enabled", lambda _s: True)
    application = app_module.create_app()
    return TestClient(application), service


class TestHostedAuthBoundary:
    def test_local_auth_noop_is_gone_protected_api_requires_session(self, hosted_client):
        client, _ = hosted_client
        # 未认证读取凭据 → 401，而不是本机 no-op 会话放行
        response = client.get("/api/hosted/credentials")
        assert response.status_code == 401
        assert client.post("/api/hosted/credentials/runner/repo/start").status_code == 401
        assert client.post("/api/hosted/credentials/runner/repo/stop").status_code == 401

    def test_me_requires_valid_session(self, hosted_client):
        client, _ = hosted_client
        assert client.get("/api/auth/me").status_code == 401
        client.cookies.set("kc_console_session", "bogus-token")
        assert client.get("/api/auth/me").status_code == 401

    def test_runner_start_requires_verified_credentials(self, hosted_client):
        client, service = hosted_client
        code = service.issue_bootstrap_code()
        client.post(
            "/api/hosted/bootstrap",
            json={"bootstrap_code": code, "admin_password": "correct-horse-battery"},
        )

        response = client.post("/api/hosted/credentials/runner/repo/start")

        assert response.status_code == 409

    def test_runner_stop_requires_hosted_session(self, hosted_client):
        client, service = hosted_client
        code = service.issue_bootstrap_code()
        client.post(
            "/api/hosted/bootstrap",
            json={"bootstrap_code": code, "admin_password": "correct-horse-battery"},
        )

        response = client.post("/api/hosted/credentials/runner/repo/stop")

        assert response.status_code == 200
        assert response.json() == {"repo_id": "repo", "stopped": True}

    def test_bootstrap_then_me_roundtrip(self, hosted_client):
        client, service = hosted_client
        code = service.issue_bootstrap_code()
        response = client.post(
            "/api/hosted/bootstrap",
            json={"bootstrap_code": code, "admin_password": "correct-horse-battery"},
        )
        assert response.status_code == 200
        assert response.cookies.get("kc_console_session")
        # TestClient 的 cookie jar 已携带会话，/auth/me 应返回 principal
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user_id"] == "console-admin"

    def test_logout_revokes_session_and_clears_cookie(self, hosted_client):
        client, service = hosted_client
        bootstrap_code = service.issue_bootstrap_code()
        bootstrap_response = client.post(
            "/api/hosted/bootstrap",
            json={
                "bootstrap_code": bootstrap_code,
                "admin_password": "correct-horse-battery",
            },
        )
        assert bootstrap_response.status_code == 200

        logout_response = client.post("/api/auth/logout")

        assert logout_response.status_code == 204
        assert "max-age=0" in logout_response.headers.get("set-cookie", "").lower()
        assert client.get("/api/auth/me").status_code == 401

    def test_session_cookie_is_httponly_samesite(self, hosted_client):
        client, service = hosted_client
        code = service.issue_bootstrap_code()
        response = client.post(
            "/api/hosted/bootstrap",
            json={"bootstrap_code": code, "admin_password": "correct-horse-battery"},
        )
        set_cookie = response.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "samesite=strict" in set_cookie

    def test_https_origin_sets_secure_cookie(self):
        # Secure 判定独立于测试 http 传输：https origin 必须带 Secure。
        assert hosted_auth._cookie_kwargs("https://console.example.com")["secure"] is True
        assert hosted_auth._cookie_kwargs("http://127.0.0.1:8313")["secure"] is False

    def test_bootstrap_replay_rejected(self, hosted_client):
        client, service = hosted_client
        code = service.issue_bootstrap_code()
        first = client.post(
            "/api/hosted/bootstrap",
            json={"bootstrap_code": code, "admin_password": "correct-horse-battery"},
        )
        assert first.status_code == 200
        replay = client.post(
            "/api/hosted/bootstrap",
            json={"bootstrap_code": code, "admin_password": "another-password-1"},
        )
        assert replay.status_code == 401

    def test_login_wrong_password_rejected(self, hosted_client):
        client, service = hosted_client
        service.bootstrap_and_set_admin(service.issue_bootstrap_code(), "correct-horse-battery")
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        ok = client.post("/api/auth/login", json={"password": "correct-horse-battery"})
        assert ok.status_code == 200


class TestHostedCredentialsApi:
    def _authed(self, client, service):
        # bootstrap 响应已通过 Set-Cookie 在 TestClient 的 cookie jar 中持久化会话
        code = service.issue_bootstrap_code()
        resp = client.post(
            "/api/hosted/bootstrap",
            json={"bootstrap_code": code, "admin_password": "correct-horse-battery"},
        )
        assert resp.status_code == 200

    def test_credentials_never_echo_secret(self, hosted_client):
        client, service = hosted_client
        self._authed(client, service)
        save = client.post(
            "/api/hosted/credentials/github",
            json={"repo_id": "r", "owner_repo": "o/r", "token": "github_pat_TOPSECRET"},
        )
        assert save.status_code == 200
        assert "TOPSECRET" not in save.text
        listing = client.get("/api/hosted/credentials")
        assert "TOPSECRET" not in listing.text

    def test_agent_save_does_not_call_model_and_never_echoes_key(self, hosted_client):
        client, service = hosted_client
        self._authed(client, service)
        save = client.post(
            "/api/hosted/credentials/agent",
            json={
                "repo_id": "r",
                "owner_repo": "o/r",
                "provider": "claude",
                "api_key": "sk-ant-api03-TOPKEY",
            },
        )
        assert save.status_code == 200
        assert save.json()["status"]["state"] == "unverified"
        assert "TOPKEY" not in save.text

    def test_credentials_require_session(self, hosted_client):
        client, _ = hosted_client
        assert client.get("/api/hosted/credentials/guidance").status_code == 401

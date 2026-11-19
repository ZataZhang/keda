"""托管 Console 编排用例的单元回归（Issue #265，rv-4 认证/凭据生命周期）。

用假端口注入 ``HostedConsoleService``，覆盖 PRD 的安全不变式：引导码单次消费、
重放与错误码拒绝、口令验证、会话过期、GitHub 只读校验、Agent 保存不调模型、
成本门控、删除不代撤销上游、状态视图永不含 secret。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.core.shared.models.hosted_console import (
    AgentKeyProbeResult,
    GitHubTokenCheck,
    HostedCredentialKind,
    HostedCredentialRejectionCode,
    HostedCredentialState,
)
from backend.core.use_cases.hosted_console import HostedConsoleService


class _FakeIdentityStore:
    """内存身份存储：实现单次消费与过期判定。"""

    def __init__(self) -> None:
        self._bootstrap = None
        self._admin = None
        self._sessions: dict[str, object] = {}
        self._login_attempts: dict[str, tuple[datetime, int]] = {}

    def get_bootstrap(self):
        return self._bootstrap

    def put_bootstrap(self, record):
        if self._bootstrap is not None and self._bootstrap.consumed_at_utc is None:
            from dataclasses import replace

            self._bootstrap = replace(
                self._bootstrap, consumed_at_utc=self._bootstrap.issued_at_utc
            )
        self._bootstrap = record

    def consume_bootstrap(self, token_hash, now_utc):
        record = self._bootstrap
        if (
            record is None
            or record.token_hash != token_hash
            or record.consumed_at_utc is not None
            or now_utc >= record.expires_at_utc
        ):
            return False
        from dataclasses import replace

        self._bootstrap = replace(record, consumed_at_utc=now_utc)
        return True

    def get_admin(self):
        return self._admin

    def put_admin(self, record):
        self._admin = record

    def consume_login_attempt(self, client_hash, attempted_at_utc, window_seconds, max_attempts):
        record = self._login_attempts.get(client_hash)
        if record is None or (attempted_at_utc - record[0]).total_seconds() >= window_seconds:
            self._login_attempts[client_hash] = (attempted_at_utc, 1)
            return None
        window_started_at, attempt_count = record
        remaining_seconds = window_seconds - int(
            (attempted_at_utc - window_started_at).total_seconds()
        )
        if attempt_count >= max_attempts:
            return max(1, remaining_seconds)
        self._login_attempts[client_hash] = (window_started_at, attempt_count + 1)
        return None

    def clear_login_attempts(self, client_hash):
        self._login_attempts.pop(client_hash, None)

    def put_session(self, record):
        self._sessions[record.session_hash] = record

    def get_session(self, session_hash):
        return self._sessions.get(session_hash)

    def drop_session(self, session_hash):
        self._sessions.pop(session_hash, None)

    def prune_sessions_expired_before(self, now_utc):
        expired = [h for h, r in self._sessions.items() if not r.is_valid_at(now_utc)]
        for h in expired:
            self._sessions.pop(h)
        return len(expired)


class _FakeVault:
    """内存凭据库：记录写入的 secret 以断言不回显、失败保留旧值。"""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], dict] = {}

    def _key(self, repo_id, kind):
        return (repo_id, str(kind))

    def get_status(self, repo_id, kind):
        entry = self._entries.get(self._key(repo_id, kind))
        return entry["status"] if entry else None

    def list_statuses(self):
        return [entry["status"] for entry in self._entries.values()]

    def put_credential(self, status, secret):
        self._entries[self._key(status.repo_id, status.kind)] = {
            "status": status,
            "secret": secret,
        }

    def update_status(self, status):
        self._entries[self._key(status.repo_id, status.kind)]["status"] = status

    def read_secret(self, repo_id, kind):
        entry = self._entries.get(self._key(repo_id, kind))
        return entry["secret"] if entry else None

    def delete_credential(self, repo_id, kind):
        return self._entries.pop(self._key(repo_id, kind), None) is not None


class _StubHandoff:
    def __init__(self) -> None:
        self.materialized: list[str] = []
        self.erased: list[str] = []

    def materialize(self, repo_id, github_credential, agent_provider, agent_api_key):
        self.materialized.append(repo_id)
        return []

    def erase(self, repo_id):
        self.erased.append(repo_id)
        return True

    def host_directory(self, repo_id):
        return f"/tmp/{repo_id}"


class _StubRunnerControl:
    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.started: list[tuple[str, str]] = []

    def stop_runner(self, repo_id):
        self.stopped.append(repo_id)
        return True

    def start_runner(self, repo_id, owner_repo):
        self.started.append((repo_id, owner_repo))
        return True


def _service(*, github_check=None, probe_result=None):
    vault = _FakeVault()
    verifier = _StubVerifier(github_check or GitHubTokenCheck(accepted=True, login="octo"))
    prober = _StubProber(probe_result or AgentKeyProbeResult(accepted=True))
    return (
        HostedConsoleService(
            identity_store=_FakeIdentityStore(),
            credential_repo=vault,
            github_verifier=verifier,
            agent_prober=prober,
            handoff=_StubHandoff(),
            runner_control=_StubRunnerControl(),
            deployment_id="dep-1",
        ),
        vault,
        verifier,
        prober,
    )


class _StubVerifier:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def verify(self, token, owner_repo):
        self.calls += 1
        return self._result


class _StubProber:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def probe(self, provider, api_key):
        self.calls += 1
        return self._result


class TestBootstrap:
    def test_wrong_code_rejected_and_current_record_survives(self):
        service, *_ = _service()
        good = service.issue_bootstrap_code()
        with pytest.raises(PermissionError):
            service.bootstrap_and_set_admin("deadbeef" * 8, "correct-horse-battery")
        # 错误码不得消费当前记录：正确码仍可引导
        token = service.bootstrap_and_set_admin(good, "correct-horse-battery")
        assert service.resolve_session(token) is not None

    def test_replay_rejected(self):
        service, *_ = _service()
        code = service.issue_bootstrap_code()
        service.bootstrap_and_set_admin(code, "correct-horse-battery")
        with pytest.raises(PermissionError):
            service.bootstrap_and_set_admin(code, "another-password-12")

    def test_second_bootstrap_after_admin_disabled(self):
        service, *_ = _service()
        service.bootstrap_and_set_admin(service.issue_bootstrap_code(), "correct-horse-battery")
        new_code = service.issue_bootstrap_code()
        with pytest.raises(PermissionError, match="already provisioned"):
            service.bootstrap_and_set_admin(new_code, "another-password-12")

    def test_reshoot_invalidates_previous_unconsumed(self):
        service, *_ = _service()
        first = service.issue_bootstrap_code()
        service.issue_bootstrap_code()  # 重发作废旧码
        with pytest.raises(PermissionError):
            service.bootstrap_and_set_admin(first, "correct-horse-battery")

    def test_expired_code_rejected(self):
        service, *_ = _service()
        code = service.issue_bootstrap_code()
        # 直接把记录过期时间挪到过去
        store = service._identity
        from dataclasses import replace

        store._bootstrap = replace(
            store._bootstrap, expires_at_utc=datetime.now(UTC) - timedelta(seconds=1)
        )
        with pytest.raises(PermissionError):
            service.bootstrap_and_set_admin(code, "correct-horse-battery")


class TestSession:
    def test_unknown_session_resolves_none(self):
        service, *_ = _service()
        assert service.resolve_session("nope") is None

    def test_status_views_never_expose_secret(self):
        service, vault, *_ = _service()
        service.save_github_credential("r", "o/r", "github_pat_" + "A" * 36)
        for status in service.list_credential_statuses():
            assert not hasattr(status, "secret")
            assert "github_pat_" not in repr(status)


class TestCredentials:
    def test_github_save_calls_readonly_verifier_and_activates(self):
        service, vault, verifier, _ = _service()
        result = service.save_github_credential("r", "o/r", "github_pat_" + "A" * 36)
        assert verifier.calls == 1
        assert result.status.state is HostedCredentialState.ACTIVE
        assert vault.read_secret("r", HostedCredentialKind.GITHUB) == "github_pat_" + "A" * 36
        assert service._handoff.materialized == []

    def test_github_rejection_keeps_previous(self):
        service, vault, verifier, _ = _service()
        good = service.save_github_credential("r", "o/r", "github_pat_" + "A" * 36)
        assert good.status.state is HostedCredentialState.ACTIVE
        # 切换校验器为权限不足，再次保存应保留旧值且不覆盖密文
        verifier._result = GitHubTokenCheck(
            accepted=False,
            rejection=HostedCredentialRejectionCode.GITHUB_PERMISSIONS_INSUFFICIENT,
        )
        bad = service.save_github_credential("r", "o/r", "github_pat_" + "B" * 36)
        assert bad.kept_previous is True
        assert bad.status.state is HostedCredentialState.REJECTED
        assert vault.read_secret("r", HostedCredentialKind.GITHUB).endswith("A" * 36)

    def test_legacy_classic_token_rejected(self):
        service, *_ = _service()
        result = service.save_github_credential("r", "o/r", "ghp_" + "x" * 30)
        assert result.status.state is HostedCredentialState.REJECTED

    def test_agent_save_does_not_call_model(self):
        service, _, _, prober = _service()
        result = service.save_agent_credential("r", "o/r", "claude", "sk-ant-api03-XYZ")
        assert prober.calls == 0
        assert result.status.state is HostedCredentialState.UNVERIFIED

    def test_agent_test_requires_cost_confirmation(self):
        service, _, _, prober = _service()
        result = service.test_agent_credential(
            "r", "o/r", "claude", "sk-ant-api03-XYZ", cost_confirmed=False
        )
        assert prober.calls == 0
        assert result.kept_previous is True
        assert (
            result.status.last_rejection is HostedCredentialRejectionCode.COST_CONFIRMATION_REQUIRED
        )

    def test_agent_test_after_confirmation_promotes(self):
        service, vault, _, prober = _service()
        result = service.test_agent_credential(
            "r", "o/r", "claude", "sk-ant-api03-XYZ", cost_confirmed=True
        )
        assert prober.calls == 1
        assert result.status.state is HostedCredentialState.ACTIVE
        assert service._handoff.materialized == []

    def test_start_runner_requires_both_verified_credentials(self):
        service, *_ = _service()
        service.save_github_credential("r", "o/r", "github_pat_" + "A" * 36)
        with pytest.raises(ValueError, match="both GitHub and Agent credentials"):
            service.start_runner("r")

    def test_start_runner_uses_only_matching_active_repo_credentials(self):
        service, *_ = _service()
        service.save_github_credential("r", "o/r", "github_pat_" + "A" * 36)
        service.test_agent_credential("r", "o/r", "claude", "sk-ant-api03-XYZ", cost_confirmed=True)

        assert service.start_runner("r") is True
        assert service._runner_control.started == [("r", "o/r")]
        assert service._handoff.materialized == ["r"]

    def test_agent_rotation_preserves_active_secret_until_new_key_tests(self):
        service, vault, _, prober = _service()
        old_key = "sk-ant-api03-OLD"
        new_key = "sk-ant-api03-NEW"
        service.test_agent_credential("r", "o/r", "claude", old_key, cost_confirmed=True)
        prober._result = AgentKeyProbeResult(accepted=False)

        saved = service.save_agent_credential("r", "o/r", "claude", new_key)
        rejected = service.test_agent_credential("r", "o/r", "claude", new_key, cost_confirmed=True)

        assert saved.kept_previous is True
        assert rejected.kept_previous is True
        assert vault.read_secret("r", HostedCredentialKind.AGENT) == old_key

    def test_delete_keeps_encrypted_secret_when_runner_cannot_stop(self):
        service, vault, *_ = _service()
        token = "github_pat_" + "A" * 36
        service.save_github_credential("r", "o/r", token)
        service._runner_control.stop_runner = lambda _repo_id: False

        result = service.delete_credential("r", HostedCredentialKind.GITHUB)

        assert result.removed is False
        assert result.runner_stopped is False
        assert vault.read_secret("r", HostedCredentialKind.GITHUB) == token

    def test_delete_keeps_encrypted_secret_when_tmpfs_cleanup_fails(self):
        service, vault, *_ = _service()
        token = "github_pat_" + "A" * 36
        service.save_github_credential("r", "o/r", token)
        service._handoff.erase = lambda _repo_id: False

        result = service.delete_credential("r", HostedCredentialKind.GITHUB)

        assert result.removed is False
        assert result.runner_stopped is True
        assert result.tmpfs_erased is False
        assert vault.read_secret("r", HostedCredentialKind.GITHUB) == token

    def test_delete_stops_runner_and_does_not_claim_upstream_revoke(self):
        service, vault, *_ = _service()
        service.save_github_credential("r", "o/r", "github_pat_" + "A" * 36)
        result = service.delete_credential("r", HostedCredentialKind.GITHUB)
        assert result.removed is True
        assert result.upstream_revoked is False
        assert vault.get_status("r", HostedCredentialKind.GITHUB) is None

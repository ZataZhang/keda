"""托管身份存储与加密凭据库的真实文件回归（Issue #265，rv-4 / FR-5）。

覆盖 SQLite 单次消费的原子性、重放拒绝、重发作废旧码、过期拒绝；Fernet 凭据库的
加解密、文件 ``0600`` 权限、跨密钥解密失败关闭与候选替换。
"""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.core.shared.models.hosted_console import BootstrapTokenRecord
from backend.infrastructure.persistence.hosted_console_store import HostedConsoleStore
from backend.infrastructure.persistence.hosted_credential_vault import (
    HostedCredentialStatusView,
    HostedCredentialVault,
)


def _now():
    return datetime.now(UTC)


class TestIdentityStore:
    def test_consume_is_single_use(self, tmp_path: Path):
        store = HostedConsoleStore(tmp_path / "c.db")
        record = BootstrapTokenRecord(
            token_hash="h",
            deployment_id="d",
            issued_at_utc=_now(),
            expires_at_utc=_now() + timedelta(hours=24),
        )
        store.put_bootstrap(record)
        assert store.consume_bootstrap("h", _now()) is True
        assert store.consume_bootstrap("h", _now()) is False

    def test_replay_of_wrong_code_does_not_consume_current(self, tmp_path: Path):
        store = HostedConsoleStore(tmp_path / "c.db")
        store.put_bootstrap(
            BootstrapTokenRecord(
                token_hash="real-hash",
                deployment_id="d",
                issued_at_utc=_now(),
                expires_at_utc=_now() + timedelta(hours=24),
            )
        )
        assert store.consume_bootstrap("attacker-hash", _now()) is False
        # 真实码仍可消费
        assert store.consume_bootstrap("real-hash", _now()) is True

    def test_reshoot_invalidates_previous(self, tmp_path: Path):
        store = HostedConsoleStore(tmp_path / "c.db")
        store.put_bootstrap(
            BootstrapTokenRecord(
                token_hash="first",
                deployment_id="d",
                issued_at_utc=_now(),
                expires_at_utc=_now() + timedelta(hours=24),
            )
        )
        store.put_bootstrap(
            BootstrapTokenRecord(
                token_hash="second",
                deployment_id="d",
                issued_at_utc=_now(),
                expires_at_utc=_now() + timedelta(hours=24),
            )
        )
        assert store.consume_bootstrap("first", _now()) is False
        assert store.consume_bootstrap("second", _now()) is True

    def test_expired_not_consumable(self, tmp_path: Path):
        store = HostedConsoleStore(tmp_path / "c.db")
        store.put_bootstrap(
            BootstrapTokenRecord(
                token_hash="h",
                deployment_id="d",
                issued_at_utc=_now() - timedelta(days=2),
                expires_at_utc=_now() - timedelta(hours=1),
            )
        )
        assert store.consume_bootstrap("h", _now()) is False

    def test_session_roundtrip_and_prune(self, tmp_path: Path):
        from backend.infrastructure.persistence.hosted_console_store import HostedSessionRecord

        store = HostedConsoleStore(tmp_path / "c.db")
        now = _now()
        store.put_session(
            HostedSessionRecord(
                session_hash="s",
                user_id="u",
                created_at_utc=now,
                expires_at_utc=now + timedelta(hours=1),
            )
        )
        store.put_session(
            HostedSessionRecord(
                session_hash="old",
                user_id="u",
                created_at_utc=now - timedelta(days=1),
                expires_at_utc=now - timedelta(hours=1),
            )
        )
        assert store.get_session("s") is not None
        assert store.prune_sessions_expired_before(now) == 1
        assert store.get_session("old") is None


class TestCredentialVault:
    def _status(self, repo_id="r", kind="github"):
        return HostedCredentialStatusView(
            repo_id=repo_id,
            owner_repo=f"o/{repo_id}",
            kind=kind,
            provider="github",
            state="active",
            created_at_utc=_now(),
            secret_length=0,
        )

    def test_encrypt_decrypt_roundtrip_and_file_mode(self, tmp_path: Path):
        vault = HostedCredentialVault(
            credentials_file=tmp_path / "c.json", data_key_file=tmp_path / "k.bin"
        )
        vault.put_credential(self._status(), "github_pat_SECRET")
        assert vault.read_secret("r", "github") == "github_pat_SECRET"
        mode = stat.S_IMODE(os.stat(tmp_path / "c.json").st_mode)
        assert mode == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "k.bin").st_mode) == 0o600

    def test_ciphertext_not_plaintext_on_disk(self, tmp_path: Path):
        vault = HostedCredentialVault(
            credentials_file=tmp_path / "c.json", data_key_file=tmp_path / "k.bin"
        )
        vault.put_credential(self._status(), "github_pat_SUPERSECRET")
        raw = (tmp_path / "c.json").read_text(encoding="utf-8")
        assert "github_pat_SUPERSECRET" not in raw

    def test_status_view_excludes_secret(self, tmp_path: Path):
        vault = HostedCredentialVault(
            credentials_file=tmp_path / "c.json", data_key_file=tmp_path / "k.bin"
        )
        vault.put_credential(self._status(), "github_pat_SUPERSECRET")
        view = vault.get_status("r", "github")
        assert not hasattr(view, "secret")
        assert "github_pat_SUPERSECRET" not in repr(view)

    def test_delete_removes_entry(self, tmp_path: Path):
        vault = HostedCredentialVault(
            credentials_file=tmp_path / "c.json", data_key_file=tmp_path / "k.bin"
        )
        vault.put_credential(self._status(), "github_pat_X")
        assert vault.delete_credential("r", "github") is True
        assert vault.read_secret("r", "github") is None

    def test_cross_key_decryption_fails_closed(self, tmp_path: Path):
        vault_a = HostedCredentialVault(
            credentials_file=tmp_path / "c.json", data_key_file=tmp_path / "k1.bin"
        )
        vault_a.put_credential(self._status(), "github_pat_X")
        # 换用另一密钥文件读取同一密文（模拟跨 VM 拷贝密文）
        from cryptography.fernet import Fernet

        (tmp_path / "k2.bin").write_bytes(Fernet.generate_key())
        vault_b = HostedCredentialVault(
            credentials_file=tmp_path / "c.json", data_key_file=tmp_path / "k2.bin"
        )
        with pytest.raises(RuntimeError):
            vault_b.read_secret("r", "github")

    def test_promote_candidate_replaces_live(self, tmp_path: Path):
        vault = HostedCredentialVault(
            credentials_file=tmp_path / "c.json", data_key_file=tmp_path / "k.bin"
        )
        vault.put_credential(self._status(), "github_pat_OLD", pending_secret="github_pat_NEW")
        assert vault.read_secret("r", "github") == "github_pat_OLD"
        assert vault.read_candidate("r", "github") == "github_pat_NEW"
        view = vault.promote_candidate("r", "github", verified_at=_now())
        assert view.state == "active"
        assert vault.read_secret("r", "github") == "github_pat_NEW"
        assert vault.read_candidate("r", "github") is None

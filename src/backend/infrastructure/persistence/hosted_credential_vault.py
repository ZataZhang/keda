"""托管运行凭据的加密文件存储（Issue #265，FR-5）。

设计要点：

- 密文只存放在**专用凭据文件**（``credentials_file``），不写 console SQLite、
  不写 ``config.toml``、不写 ``.env``；数据密钥单独成文件
  （``data_key_file``），两者目录权限 ``0700``、文件权限 ``0600``。
- 使用 ``cryptography`` 的 Fernet（AES-128-CBC + HMAC-SHA256 认证加密）对
  secret 逐条加密；密钥文件缺失时首建并拒绝在 world-writable 路径落密钥。
- 凭据按 ``(repo_id, kind)`` 隔离；轮换支持「候选值暂存」：新值验证失败时
  线上旧值保持不变（对应 PRD 的 *验证失败保留旧值*）。
- 任何读取接口只返回不含 secret 的状态视图；明文只在 ``read_secret`` /
  ``read_candidate`` 解密后返回给宿主运行时交付路径。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_logger = logging.getLogger(__name__)

_CREDENTIALS_VERSION = 1


def _to_iso(moment: datetime) -> str:
    """把 aware datetime 转 ISO 文本。"""
    if moment.tzinfo is None:
        raise ValueError("naive datetime cannot be persisted for hosted credentials")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _from_iso(text: str) -> datetime:
    """解析 ISO 文本为 aware datetime。"""
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class HostedCredentialVault:
    """实现 ``HostedCredentialRepository`` 契约的加密文件存储。

    Args:
        credentials_file: 密文凭据 JSON 文件路径。
        data_key_file: Fernet 数据密钥文件路径（与密文分开保存）。
    """

    def __init__(self, *, credentials_file: Path, data_key_file: Path) -> None:
        """准备目录结构并加载或首建数据密钥。"""
        self._credentials_path = Path(credentials_file)
        self._key_path = Path(data_key_file)
        self._credentials_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._key_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._fernet = Fernet(self._load_or_create_data_key())

    def _load_or_create_data_key(self) -> bytes:
        """读取数据密钥；首次使用时生成并以 ``0600`` 落盘。"""
        if self._key_path.exists():
            key_bytes = self._key_path.read_bytes().strip()
            key_mode = self._key_path.stat().st_mode & 0o777
            if key_mode & 0o077:
                # 密钥文件被放宽到组/其他可读时立即收紧，避免与密文同读的泄漏面。
                _logger.warning(
                    "Hosted credential data key had open permissions (%o); tightening to 0600",
                    key_mode,
                )
                os.chmod(self._key_path, 0o600)
            return key_bytes
        generated = Fernet.generate_key()
        descriptor, tmp_name = tempfile.mkstemp(dir=self._key_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(generated)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self._key_path)
        except OSError:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return generated

    def _entry_id(self, repo_id: str, kind: str) -> str:
        """组合仓库与类别得到条目 key（闭集校验由调用方保证）。"""
        return f"{repo_id}::{kind}"

    def _load_entries(self) -> dict[str, dict]:
        """读取凭据文件；不存在时返回空表。"""
        if not self._credentials_path.exists():
            return {}
        raw = json.loads(self._credentials_path.read_text(encoding="utf-8"))
        return dict(raw.get("credentials", {}))

    def _store_entries(self, entries: dict[str, dict]) -> None:
        """原子写回凭据文件（temp + rename，权限 0600）。"""
        payload = {
            "version": _CREDENTIALS_VERSION,
            "credentials": entries,
        }
        descriptor, tmp_name = tempfile.mkstemp(dir=self._credentials_path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as credentials_file:
                json.dump(payload, credentials_file, ensure_ascii=False, indent=2)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self._credentials_path)
        except OSError:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    @staticmethod
    def _status_from_entry(entry: dict) -> "HostedCredentialStatusView":
        """把存储条目转成不含密文的状态视图。"""
        return HostedCredentialStatusView(
            repo_id=entry["repo_id"],
            owner_repo=entry["owner_repo"],
            kind=entry["kind"],
            provider=entry["provider"],
            state=entry["state"],
            github_login=entry.get("github_login", ""),
            created_at_utc=_from_iso(entry["created_at"]) if entry.get("created_at") else None,
            verified_at_utc=(_from_iso(entry["verified_at"]) if entry.get("verified_at") else None),
            expires_at_hint=entry.get("expires_at_hint", ""),
            last_rejection=entry.get("last_rejection"),
            secret_length=entry.get("secret_length", 0),
        )

    def get_status(self, repo_id: str, kind: str):
        """返回单条凭据状态视图；不存在返回 ``None``。"""
        entries = self._load_entries()
        entry = entries.get(self._entry_id(repo_id, kind))
        return self._status_from_entry(entry) if entry else None

    def list_statuses(self):
        """返回全部凭据状态视图（按 repo_id、kind 稳定排序）。"""
        entries = self._load_entries()
        views = [self._status_from_entry(entry) for entry in entries.values()]
        return sorted(views, key=lambda view: (view.repo_id, view.kind))

    def put_credential(
        self,
        status,
        secret: str,
        *,
        pending_secret: str | None = None,
        keep_live_secret: bool = False,
    ) -> None:
        """原子写入凭据的状态与密文。

        Args:
            status: 不含 secret 的状态记录（core 的 HostedCredentialStatus 或同构视图）。
            secret: 线上凭据明文；调用方只允许在内存里传递，绝不落日志。
            pending_secret: 轮换候选明文（未通过验证前不作为线上值）。
            keep_live_secret: True 时保留旧线上密文（仅换入候选的场景）。
        """
        entry_id = self._entry_id(status.repo_id, str(status.kind))
        entries = self._load_entries()
        previous = entries.get(entry_id, {})
        entry = {
            key: (_to_iso(value) if isinstance(value, datetime) else value)
            for key, value in asdict(status).items()
        }
        entry["repo_id"] = status.repo_id
        entry["kind"] = str(status.kind)
        entry["provider"] = str(status.provider)
        entry["state"] = str(status.state)
        if entry.get("last_rejection") is not None:
            entry["last_rejection"] = str(status.last_rejection)
        entry["secret_enc"] = (
            previous.get("secret_enc")
            if keep_live_secret and previous.get("secret_enc")
            else self._encrypt(secret)
        )
        entry["secret_length"] = len(secret)
        if pending_secret is not None:
            entry["pending_enc"] = self._encrypt(pending_secret)
            entry["pending_length"] = len(pending_secret)
        else:
            entry.pop("pending_enc", None)
            entry.pop("pending_length", None)
        entries[entry_id] = entry
        self._store_entries(entries)

    def update_status(self, status) -> None:
        """只替换状态元数据，密文与候选保持不变。"""
        entry_id = self._entry_id(status.repo_id, str(status.kind))
        entries = self._load_entries()
        if entry_id not in entries:
            raise KeyError(f"hosted credential {entry_id} not found")
        merged = dict(entries[entry_id])
        merged.update(
            {
                "owner_repo": status.owner_repo,
                "provider": str(status.provider),
                "state": str(status.state),
                "github_login": status.github_login,
                "created_at": _to_iso(status.created_at_utc) if status.created_at_utc else None,
                "verified_at": _to_iso(status.verified_at_utc) if status.verified_at_utc else None,
                "expires_at_hint": status.expires_at_hint,
                "last_rejection": (
                    str(status.last_rejection) if status.last_rejection is not None else None
                ),
            }
        )
        entries[entry_id] = merged
        self._store_entries(entries)

    def promote_candidate(self, repo_id: str, kind: str, *, verified_at: datetime):
        """候选密文验证成功后原子替换线上值，返回新状态视图。"""
        entry_id = self._entry_id(repo_id, kind)
        entries = self._load_entries()
        entry = entries.get(entry_id)
        if entry is None or "pending_enc" not in entry:
            raise KeyError(f"hosted credential candidate for {entry_id} not found")
        entry["secret_enc"] = entry.pop("pending_enc")
        entry["secret_length"] = entry.pop("pending_length", entry.get("secret_length", 0))
        entry["state"] = "active"
        entry["verified_at"] = _to_iso(verified_at)
        entry["last_rejection"] = None
        entries[entry_id] = entry
        self._store_entries(entries)
        return self._status_from_entry(entry)

    def read_secret(self, repo_id: str, kind: str) -> str | None:
        """解密并返回线上凭据明文（仅供宿主运行时交付路径使用）。"""
        entry = self._load_entries().get(self._entry_id(repo_id, kind))
        if entry is None or not entry.get("secret_enc"):
            return None
        return self._decrypt(entry["secret_enc"])

    def read_candidate(self, repo_id: str, kind: str) -> str | None:
        """解密并返回未验证的候选凭据明文。"""
        entry = self._load_entries().get(self._entry_id(repo_id, kind))
        if entry is None or not entry.get("pending_enc"):
            return None
        return self._decrypt(entry["pending_enc"])

    def delete_credential(self, repo_id: str, kind: str) -> bool:
        """删除该仓库该类别的全部密文与候选；返回是否删除了条目。"""
        entry_id = self._entry_id(repo_id, kind)
        entries = self._load_entries()
        if entry_id not in entries:
            return False
        entries.pop(entry_id)
        self._store_entries(entries)
        return True

    def _encrypt(self, secret: str) -> str:
        """加密单条 secret，返回 Fernet token 文本。"""
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def _decrypt(self, token_text: str) -> str:
        """解密 Fernet token；密钥不符（如换 VM 拷贝密文）时失败关闭。"""
        try:
            return self._fernet.decrypt(token_text.encode("ascii")).decode("utf-8")
        except InvalidToken as decrypt_error:
            raise RuntimeError(
                "Hosted credential file cannot be decrypted with this deployment's data key"
            ) from decrypt_error


@dataclass(frozen=True)
class HostedCredentialStatusView:
    """infrastructure 侧凭据状态视图（与 core.HostedCredentialStatus 同构）。"""

    repo_id: str
    owner_repo: str
    kind: str
    provider: str
    state: str
    github_login: str = ""
    created_at_utc: datetime | None = None
    verified_at_utc: datetime | None = None
    expires_at_hint: str = ""
    last_rejection: str | None = None
    secret_length: int = 0


def status_view_to_dict(view: HostedCredentialStatusView) -> dict:
    """把状态视图转成 API 可序列化的 dict（datetime 转 ISO 文本）。"""
    payload = asdict(view)
    for datetime_field in ("created_at_utc", "verified_at_utc"):
        value = payload.get(datetime_field)
        if isinstance(value, datetime):
            payload[datetime_field] = _to_iso(value)
    return payload


__all__ = [
    "HostedCredentialStatusView",
    "HostedCredentialVault",
    "status_view_to_dict",
]

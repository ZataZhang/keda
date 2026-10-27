"""以原子文件保存当前 KedaCode 安装的机器身份。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator

from backend.core.shared.interfaces.machine_identity import IMachineIdentityStore
from backend.core.shared.models.machine_identity import MachineIdentity
from backend.core.shared.models.product_identity import state_home

_logger = logging.getLogger(__name__)


class MachineIdentityStore(IMachineIdentityStore):
    """在状态目录的 ``machine.json`` 中持久化 UUID 身份。"""

    def __init__(self, machine_file: Path | None = None) -> None:
        self._machine_file = machine_file or state_home() / "machine.json"

    def load_or_create(self) -> MachineIdentity:
        """读取有效身份；缺失或损坏时在文件锁内生成并原子替换。"""
        self._machine_file.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._machine_file.with_suffix(".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            self._lock(lock_handle.fileno())
            try:
                existing_identity = self._read_identity()
                if existing_identity is not None:
                    return existing_identity
                machine_identity = MachineIdentity(
                    machine_id=str(uuid.uuid4()),
                    created_at=datetime.now(timezone.utc),
                )
                self._write_identity(machine_identity)
                return machine_identity
            finally:
                self._unlock(lock_handle.fileno())

    def _read_identity(self) -> MachineIdentity | None:
        """解析已有身份；损坏时告警并交给调用方重建。"""
        try:
            with self._machine_file.open("r", encoding="utf-8") as machine_handle:
                raw_identity = json.load(machine_handle)
            machine_id_uuid = uuid.UUID(str(raw_identity["machine_id"]))
            created_at = datetime.fromisoformat(str(raw_identity["created_at"]))
            if machine_id_uuid.version != 4 or created_at.tzinfo is None:
                raise ValueError(
                    "machine identity must be UUID4 with a timezone-aware creation time"
                )
            return MachineIdentity(str(machine_id_uuid), created_at.astimezone(timezone.utc))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            _logger.warning("Machine identity file is invalid; generating a new ID: %s", exc)
            return None

    def _write_identity(self, machine_identity: MachineIdentity) -> None:
        """写入同目录临时文件并原子替换正式文件。"""
        payload = {
            "machine_id": machine_identity.machine_id,
            "created_at": machine_identity.created_at.astimezone(timezone.utc).isoformat(),
        }
        temp_fd, temp_name = tempfile.mkstemp(
            prefix="machine-identity-", suffix=".json", dir=self._machine_file.parent
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as machine_handle:
                json.dump(payload, machine_handle, ensure_ascii=False, sort_keys=True)
                machine_handle.write("\n")
                machine_handle.flush()
                os.fsync(machine_handle.fileno())
            os.replace(temp_name, self._machine_file)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @contextmanager
    def presence_slot_lock(self) -> Iterator[None]:
        """跨进程串行化本机状态评论槽位的读取与复用。"""
        lock_path = self._machine_file.with_name("presence-slots.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            self._lock(lock_handle.fileno())
            try:
                yield
            finally:
                self._unlock(lock_handle.fileno())

    @staticmethod
    def _lock(file_descriptor: int) -> None:
        """锁定机器身份文件；当前平台使用建议性排它锁。"""
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Unix runner is the supported daemon host.
            return
        fcntl.flock(file_descriptor, fcntl.LOCK_EX)

    @staticmethod
    def _unlock(file_descriptor: int) -> None:
        """释放机器身份文件锁。"""
        try:
            import fcntl
        except ImportError:  # pragma: no cover - 与 _lock 的平台回退配对。
            return
        fcntl.flock(file_descriptor, fcntl.LOCK_UN)


__all__ = ["MachineIdentityStore"]

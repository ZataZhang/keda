"""本机稳定机器编号文件的生成、复用与损坏恢复测试。"""

from __future__ import annotations

import json
import uuid
from datetime import timezone
from pathlib import Path

import pytest

from backend.infrastructure.machine_identity_store import MachineIdentityStore


def test_machine_identity_store_generates_and_reuses_uuid4(tmp_path: Path) -> None:
    """首次读取写入带 UTC 时间的 UUID4，后续读取复用同一身份。"""
    machine_file = tmp_path / "state" / "machine.json"
    identity_store = MachineIdentityStore(machine_file)

    first_identity = identity_store.load_or_create()
    second_identity = identity_store.load_or_create()
    saved_identity = json.loads(machine_file.read_text(encoding="utf-8"))

    assert uuid.UUID(first_identity.machine_id).version == 4
    assert second_identity == first_identity
    assert first_identity.created_at.tzinfo == timezone.utc
    assert saved_identity["machine_id"] == first_identity.machine_id


@pytest.mark.parametrize(
    "invalid_identity",
    [
        "{invalid json",
        '{"machine_id":"not-a-uuid","created_at":"2026-10-26T00:00:00+00:00"}',
        '{"machine_id":"00000000-0000-0000-0000-000000000000",'
        '"created_at":"2026-10-26T00:00:00"}',
    ],
)
def test_machine_identity_store_replaces_corrupt_or_non_uuid4_identity(
    tmp_path: Path,
    invalid_identity: str,
) -> None:
    """JSON 损坏、非 UUID4 或无时区时间都触发新身份生成。"""
    machine_file = tmp_path / "machine.json"
    machine_file.write_text(invalid_identity, encoding="utf-8")

    identity = MachineIdentityStore(machine_file).load_or_create()
    saved_identity = json.loads(machine_file.read_text(encoding="utf-8"))

    assert uuid.UUID(identity.machine_id).version == 4
    assert identity.created_at.tzinfo == timezone.utc
    assert saved_identity["machine_id"] == identity.machine_id

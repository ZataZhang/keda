"""worktree 局部会话记录的原子落盘、宽容读取与可续传判据单元测试。

覆盖 :mod:`backend.core.use_cases.agent_runner_session_store`：崩溃对账判成「续传
恢复」后，领取侧完全依赖这份记录决定第二条命令是 ``--resume`` 还是全新会话，因此
落盘必须原子（不留半截 JSON）、读取必须宽容（损坏等价于"没有会话可续"）。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_session_store import (
    AgentSessionScope,
    agent_session_record_path,
    consume_agent_timeout_resume_attempt,
    load_agent_session_record,
    mark_agent_session_complete,
    resolve_resumable_session_id,
    save_agent_session_record,
)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    """写入后按 agent 读回，字段逐一对得上。"""
    saved_path = save_agent_session_record(
        tmp_path,
        agent_name="claude",
        session_id="sess-abc",
        scope=AgentSessionScope(issue_number=7),
    )
    record = load_agent_session_record(tmp_path, "claude")

    assert saved_path == agent_session_record_path(tmp_path, "claude")
    assert record is not None
    assert (record.agent_name, record.session_id, record.issue_number) == (
        "claude",
        "sess-abc",
        7,
    )
    assert record.resume_eligible is True
    assert record.timeout_resume_count == 0
    assert record.updated_at


def test_unspecified_phase_keeps_legacy_record_path(tmp_path: Path) -> None:
    assert agent_session_record_path(tmp_path, "claude", phase="unspecified") == (
        agent_session_record_path(tmp_path, "claude")
    )


def test_record_lives_under_gitignored_kc_dir(tmp_path: Path) -> None:
    """新会话记录默认落在 ``.kedacode/``，保持 worktree 局部。"""
    record_path = save_agent_session_record(tmp_path, agent_name="claude", session_id="sess-abc")
    assert record_path.relative_to(tmp_path).parts[:2] == (".kedacode", "agent-runner")


def test_existing_legacy_session_directory_remains_active(tmp_path: Path) -> None:
    """已有 ``.iar/`` 会话目录时继续沿用，避免丢失续传状态。"""
    legacy_sessions_path = tmp_path / ".iar" / "agent-runner" / "sessions"
    legacy_sessions_path.mkdir(parents=True)

    record_path = save_agent_session_record(tmp_path, agent_name="claude", session_id="sess-abc")

    assert record_path.parent == legacy_sessions_path


def test_save_leaves_no_temporary_file(tmp_path: Path) -> None:
    """原子写走 tmp + ``os.replace``，落盘后目录里只剩目标文件。"""
    save_agent_session_record(tmp_path, agent_name="claude", session_id="sess-abc")
    save_agent_session_record(tmp_path, agent_name="claude", session_id="sess-def")

    sessions_dir = agent_session_record_path(tmp_path, "claude").parent
    assert sorted(entry.name for entry in sessions_dir.iterdir()) == ["claude.json"]
    assert load_agent_session_record(tmp_path, "claude").session_id == "sess-def"


def test_empty_session_id_is_rejected(tmp_path: Path) -> None:
    """空 id 落盘没有意义且会让续传命令拼出 ``--resume ""``，直接拒绝。"""
    with pytest.raises(ValueError):
        save_agent_session_record(tmp_path, agent_name="claude", session_id="")


def test_load_is_tolerant_of_missing_and_corrupt_records(tmp_path: Path) -> None:
    """缺失、坏 JSON、顶层不是对象、session_id 空/类型错 —— 一律读成"没有会话"。"""
    record_path = agent_session_record_path(tmp_path, "claude")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    assert load_agent_session_record(tmp_path, "claude") is None

    corrupt_payloads = [
        "{not json",
        json.dumps(["sess-abc"]),
        json.dumps({"session_id": ""}),
        json.dumps({"session_id": 123}),
        json.dumps({"session_id": None}),
    ]
    for corrupt_payload in corrupt_payloads:
        record_path.write_text(corrupt_payload, encoding="utf-8")
        assert load_agent_session_record(tmp_path, "claude") is None


def test_load_keeps_optional_fields_out_of_shape(tmp_path: Path) -> None:
    """issue_number / updated_at 形状不符时降级为 None / 空串，不影响会话 id 可用性。"""
    save_agent_session_record(tmp_path, agent_name="claude", session_id="sess-abc")
    agent_session_record_path(tmp_path, "claude").write_text(
        json.dumps({"session_id": "sess-abc", "issue_number": "7", "updated_at": 1}),
        encoding="utf-8",
    )
    record = load_agent_session_record(tmp_path, "claude")

    assert record is not None
    assert (record.session_id, record.issue_number, record.updated_at) == (
        "sess-abc",
        None,
        "",
    )
    assert record.resume_eligible is False
    assert record.timeout_resume_count == 0


class TestResolveResumableSessionId:
    """领取侧的三重护栏：能力声明、记录可读、记录归属。"""

    def test_declared_capability_with_matching_record(self, tmp_path: Path) -> None:
        """claude 出厂声明续传能力 → 记录命中即返回会话 id。"""
        save_agent_session_record(
            tmp_path,
            agent_name="claude",
            session_id="sess-abc",
            scope=AgentSessionScope(issue_number=7),
        )
        assert (
            resolve_resumable_session_id(
                tmp_path,
                config=AppConfig(),
                agent_name="claude",
                scope=AgentSessionScope(issue_number=7),
            )
            == "sess-abc"
        )

    def test_agent_without_capability_never_resumes(self, tmp_path: Path) -> None:
        """未声明 ``supports_resume`` 的 CLI 即便留有会话记录也不续传（无硬编码分支）。"""
        config = AppConfig()
        unresumable_agent = "claude"
        config.agents[unresumable_agent] = replace(
            config.agents[unresumable_agent], supports_resume=False
        )
        save_agent_session_record(
            tmp_path,
            agent_name=unresumable_agent,
            session_id="sess-abc",
            scope=AgentSessionScope(issue_number=7),
        )

        assert (
            resolve_resumable_session_id(
                tmp_path,
                config=config,
                agent_name=unresumable_agent,
                scope=AgentSessionScope(issue_number=7),
            )
            is None
        )

    def test_missing_record_falls_back_to_fresh_session(self, tmp_path: Path) -> None:
        """没有记录（首轮 / 记录被清理）→ 全新会话，绝不报错。"""
        assert (
            resolve_resumable_session_id(
                tmp_path,
                config=AppConfig(),
                agent_name="claude",
                scope=AgentSessionScope(issue_number=7),
            )
            is None
        )

    def test_record_of_other_issue_is_ignored(self, tmp_path: Path) -> None:
        """worktree 复用换 Issue 时，续错会话比不续更糟。"""
        save_agent_session_record(
            tmp_path,
            agent_name="claude",
            session_id="sess-abc",
            scope=AgentSessionScope(issue_number=7),
        )
        assert (
            resolve_resumable_session_id(
                tmp_path,
                config=AppConfig(),
                agent_name="claude",
                scope=AgentSessionScope(issue_number=8),
            )
            is None
        )

    def test_record_without_issue_number_is_usable(self, tmp_path: Path) -> None:
        """记录没写 Issue 编号时不做归属断言，仍允许续传（旁路信息不该卡住恢复）。"""
        save_agent_session_record(tmp_path, agent_name="claude", session_id="sess-abc")
        assert (
            resolve_resumable_session_id(
                tmp_path,
                config=AppConfig(),
                agent_name="claude",
                scope=AgentSessionScope(issue_number=8),
            )
            == "sess-abc"
        )

    def test_completed_session_is_not_resumed(self, tmp_path: Path) -> None:
        scope = AgentSessionScope(issue_number=7)
        save_agent_session_record(
            tmp_path,
            agent_name="claude",
            session_id="sess-complete",
            scope=scope,
        )
        mark_agent_session_complete(tmp_path, agent_name="claude", scope=scope)

        assert (
            resolve_resumable_session_id(
                tmp_path,
                config=AppConfig(),
                agent_name="claude",
                scope=scope,
            )
            is None
        )

    def test_timeout_resume_budget_is_persisted_per_session(self, tmp_path: Path) -> None:
        config = AppConfig()
        scope = AgentSessionScope(issue_number=7)
        save_agent_session_record(
            tmp_path,
            agent_name="claude",
            session_id="sess-timeout",
            scope=scope,
        )

        assert (
            consume_agent_timeout_resume_attempt(
                tmp_path,
                config=config,
                agent_name="claude",
                session_id="sess-timeout",
                scope=scope,
                max_attempts=1,
            )
            == 1
        )
        record = load_agent_session_record(tmp_path, "claude")
        assert record is not None
        assert record.timeout_resume_count == 1
        assert (
            consume_agent_timeout_resume_attempt(
                tmp_path,
                config=config,
                agent_name="claude",
                session_id="sess-timeout",
                scope=scope,
                max_attempts=1,
            )
            is None
        )

    def test_timeout_resume_budget_does_not_transfer_to_new_session(self, tmp_path: Path) -> None:
        scope = AgentSessionScope(issue_number=7)
        save_agent_session_record(
            tmp_path,
            agent_name="claude",
            session_id="sess-old",
            scope=scope,
            timeout_resume_count=1,
        )
        save_agent_session_record(
            tmp_path,
            agent_name="claude",
            session_id="sess-new",
            scope=scope,
        )

        record = load_agent_session_record(tmp_path, "claude")
        assert record is not None
        assert record.session_id == "sess-new"
        assert record.timeout_resume_count == 0

    def test_unknown_agent_name_is_safe(self, tmp_path: Path) -> None:
        """注册表里没有的 agent 名 → 按"不可续传"处理。"""
        assert (
            resolve_resumable_session_id(
                tmp_path,
                config=AppConfig(),
                agent_name="not-registered",
                scope=AgentSessionScope(issue_number=7),
            )
            is None
        )

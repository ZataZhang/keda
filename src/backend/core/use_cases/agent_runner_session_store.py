"""Worktree-local agent session records for crash-reconciliation resume.

Agent Runner 的会话续传不把 session id 存进任何"第二状态源"（库表 / 租约）——
持有方是 agent CLI 自己，runner 只做**记录与回传**：把 agent 输出流里自报的
session id 落在该 Issue 的 worktree 局部（``.kedacode/``，仓库级 gitignore 内；旧 ``.iar/``
目录存在时继续使用），
恢复轮次构建命令时读回来注入 resume 模板。记录还标记执行链是否已经完整成功，以及
同一 session 已消耗的超时续跑预算，避免仅因历史 id 存在就自动续传。

落盘复用 Agent Runner 记忆持久化的 tmp + ``os.replace`` 原子写模式：临时文件与
目标文件同目录（同文件系统内 ``rename(2)`` 原子），显式 ``encoding="utf-8"``，
失败即清理，绝不留下半截 JSON。多进程并发写同一目标按 last-write-wins 收敛。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.repository_paths import (
    LEGACY_REPOSITORY_STATE_DIR_NAME,
    resolve_repository_state_path,
)

_logger = logging.getLogger(__name__)

#: 新会话目录；已有旧目录由路径解析器继续复用。
_AGENT_SESSION_DIR_RELPATH = Path(".kedacode") / "agent-runner" / "sessions"
_LEGACY_AGENT_SESSION_DIR_RELPATH = (
    Path(LEGACY_REPOSITORY_STATE_DIR_NAME) / "agent-runner" / "sessions"
)
_SESSION_PHASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class AgentSessionRecord:
    """一次 agent 运行自报的会话标识（worktree 局部记录的读取视图）。

    Attributes:
        agent_name: agent 注册名（记录按 agent 分文件，换人不会串会话）。
        session_id: agent CLI 输出协议或 resume hint 自报的会话 id。
        issue_number: 记录所属 Issue 编号（审计与排错用；读取侧不依赖它做判定）。
        updated_at: 落盘时间（UTC ISO-8601）。
        resume_eligible: 上一条执行链未完成时为 True；完整成功后置 False。
        timeout_resume_count: 该 session 已消耗的超时续跑轮数。
    """

    agent_name: str
    session_id: str
    issue_number: int | None
    updated_at: str
    resume_eligible: bool
    timeout_resume_count: int


@dataclass(frozen=True)
class AgentSessionScope:
    """同一 worktree 内区分 agent 会话的 Issue 与调用阶段。

    Attributes:
        issue_number: 记录所属 Issue；缺失时不执行归属校验。
        phase: 记录所属调用阶段；implementation、unspecified 或 None 使用旧版兼容路径。
    """

    issue_number: int | None = None
    phase: str | None = None


def agent_session_record_path(
    worktree_path: Path,
    agent_name: str,
    *,
    phase: str | None = None,
) -> Path:
    """返回某 agent 在该 worktree 的会话记录路径（不创建目录）。

    implementation / unspecified 阶段继续使用旧版 ``<agent>.json`` 路径；其他阶段使用
    ``<agent>.<phase>.json``，避免跨阶段覆盖续传上下文。
    """
    if phase is None or phase in ("implementation", "unspecified"):
        record_name = f"{agent_name}.json"
    else:
        if not _SESSION_PHASE_RE.fullmatch(phase):
            raise ValueError(f"Invalid agent session phase: {phase!r}")
        record_name = f"{agent_name}.{phase}.json"
    session_dir_path = resolve_repository_state_path(
        worktree_path,
        _AGENT_SESSION_DIR_RELPATH,
        _LEGACY_AGENT_SESSION_DIR_RELPATH,
    )
    return session_dir_path / record_name


def _atomic_write_text(target_path: Path, content: str) -> None:
    """同目录临时文件 + ``os.replace`` 原子落盘（tmp 失败即清理）。"""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f".{target_path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_agent_session_record(
    worktree_path: Path,
    *,
    agent_name: str,
    session_id: str,
    scope: AgentSessionScope | None = None,
    resume_eligible: bool = True,
    timeout_resume_count: int | None = None,
) -> Path:
    """把 agent 自报的 session id 原子写入 worktree 局部记录。

    Args:
        worktree_path: Issue 的 worktree 根目录。
        agent_name: agent 注册名。
        session_id: 非空的会话 id。
        scope: 会话所属 Issue 与阶段；实现阶段使用兼容路径，其他阶段独立落盘。
        resume_eligible: 记录是否可以被后续调用自动续传。
        timeout_resume_count: 已消耗的超时续跑轮数；省略时，同一 session 保留原计数。

    Returns:
        落盘后的记录文件路径。

    Raises:
        ValueError: ``session_id`` 为空串或 phase 格式不合法。
        OSError: 文件系统写入失败（调用侧按旁路失败处理，不影响主流程）。
    """
    if not session_id:
        raise ValueError("session_id must be non-empty to persist an agent session record.")
    previous_record = (
        _load_scoped_record(worktree_path, agent_name=agent_name, scope=scope)
        if scope is not None
        else load_agent_session_record(worktree_path, agent_name)
    )
    preserved_timeout_resume_count = (
        previous_record.timeout_resume_count
        if previous_record is not None and previous_record.session_id == session_id
        else 0
    )
    record_payload = {
        "agent": agent_name,
        "session_id": session_id,
        "issue_number": scope.issue_number if scope is not None else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "resume_eligible": resume_eligible,
        "timeout_resume_count": (
            preserved_timeout_resume_count
            if timeout_resume_count is None
            else max(0, timeout_resume_count)
        ),
    }
    target_path = agent_session_record_path(
        worktree_path,
        agent_name,
        phase=scope.phase if scope is not None else None,
    )
    _atomic_write_text(target_path, json.dumps(record_payload, ensure_ascii=False, indent=2) + "\n")
    return target_path


def load_agent_session_record(
    worktree_path: Path,
    agent_name: str,
    *,
    phase: str | None = None,
) -> AgentSessionRecord | None:
    """读取某 agent 的会话记录；缺失、损坏或形状不符时返回 ``None``。

    读取侧一律宽容：续传是"有则优先"的增益路径，任何解析失败都等价于"没有
    会话可续"，由调用侧降级为全新会话，绝不因此中断恢复轮次。
    """
    record_path = agent_session_record_path(worktree_path, agent_name, phase=phase)
    if not record_path.is_file():
        return None
    try:
        loaded_payload = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Ignoring unreadable agent session record %s: %s", record_path, exc)
        return None
    if not isinstance(loaded_payload, dict):
        return None
    session_id = loaded_payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    issue_number = loaded_payload.get("issue_number")
    updated_at = loaded_payload.get("updated_at")
    timeout_resume_count = loaded_payload.get("timeout_resume_count")
    return AgentSessionRecord(
        agent_name=str(loaded_payload.get("agent", agent_name)),
        session_id=session_id,
        issue_number=issue_number if isinstance(issue_number, int) else None,
        updated_at=updated_at if isinstance(updated_at, str) else "",
        # 旧记录没有执行结果，保守视为不可自动续传，避免把已成功的历史会话接上。
        resume_eligible=loaded_payload.get("resume_eligible") is True,
        timeout_resume_count=(
            timeout_resume_count
            if type(timeout_resume_count) is int and timeout_resume_count >= 0
            else 0
        ),
    )


def _load_scoped_record(
    worktree_path: Path,
    *,
    agent_name: str,
    scope: AgentSessionScope,
) -> AgentSessionRecord | None:
    """按阶段读取记录；新阶段没有记录时兼容读取旧版 implementation 记录。"""
    record = load_agent_session_record(worktree_path, agent_name, phase=scope.phase)
    if record is None and scope.phase not in (None, "implementation"):
        record = load_agent_session_record(worktree_path, agent_name)
    if (
        record is not None
        and scope.issue_number is not None
        and record.issue_number is not None
        and record.issue_number != scope.issue_number
    ):
        return None
    return record


def consume_agent_timeout_resume_attempt(
    worktree_path: Path,
    *,
    config: AppConfig,
    agent_name: str,
    session_id: str,
    scope: AgentSessionScope,
    max_attempts: int,
) -> int | None:
    """为匹配的未完成会话原子记一轮超时续跑预算。

    Returns:
        消耗后的轮数；记录不匹配、不可续传或预算耗尽时返回 ``None``。
    """
    if max_attempts <= 0:
        return None
    agent_spec = config.agents.get(agent_name)
    if agent_spec is None or not agent_spec.supports_resume:
        return None
    record = _load_scoped_record(worktree_path, agent_name=agent_name, scope=scope)
    if (
        record is None
        or not record.resume_eligible
        or record.session_id != session_id
        or record.timeout_resume_count >= max_attempts
    ):
        return None
    consumed_attempts = record.timeout_resume_count + 1
    try:
        save_agent_session_record(
            worktree_path,
            agent_name=agent_name,
            session_id=record.session_id,
            scope=scope,
            resume_eligible=True,
            timeout_resume_count=consumed_attempts,
        )
    except (OSError, ValueError) as exc:
        _logger.warning(
            "Could not persist timeout resume budget at %s (agent '%s'): %s",
            worktree_path,
            agent_name,
            exc,
        )
        return None
    return consumed_attempts


def mark_agent_session_complete(
    worktree_path: Path,
    *,
    agent_name: str,
    scope: AgentSessionScope,
) -> None:
    """将已完成阶段的会话标为不可自动续传；旁路落盘失败不影响成功结果。"""
    record = _load_scoped_record(worktree_path, agent_name=agent_name, scope=scope)
    if record is None:
        return
    try:
        save_agent_session_record(
            worktree_path,
            agent_name=agent_name,
            session_id=record.session_id,
            scope=scope,
            resume_eligible=False,
            timeout_resume_count=record.timeout_resume_count,
        )
    except (OSError, ValueError) as exc:
        _logger.warning(
            "Could not mark agent session complete at %s (agent '%s'): %s",
            worktree_path,
            agent_name,
            exc,
        )


def resolve_resumable_session_id(
    worktree_path: Path,
    *,
    config: AppConfig,
    agent_name: str,
    scope: AgentSessionScope,
) -> str | None:
    """该 agent 在该 worktree 是否有可续传的未完成会话；有则返回会话 id。

    护栏不满足时返回 ``None``（= 全新会话），绝不因此报错：

    1. agent 声明了续传能力（``agents.<name>.supports_resume``）——未声明的 CLI
       拿到 id 也不会认。
    2. 记录存在、可读且明确标记为未完成；旧版缺少执行状态的记录按不可续传处理。
    3. 记录属于这个 Issue：worktree 可能被复用到别的 Issue，续错会话比不续更糟。
    4. 同一 session 的超时续跑预算尚未耗尽；耗尽后后续 recovery 使用新会话。

    调用侧包括：**首轮领取**（崩溃对账判定「可续传」后重新入队的那次）、
    **recovery 轮次**（本轮 attempt 死掉后接着聊）与 **pre-PR review** 的 run profile；
    不同阶段使用独立记录，避免 review 覆盖实现会话。旧版未分阶段的记录在找不到
    阶段记录时作为兼容回退，避免升级时丢掉已有的续传上下文。
    """
    agent_spec = config.agents.get(agent_name)
    if agent_spec is None or not agent_spec.supports_resume:
        return None
    record = _load_scoped_record(worktree_path, agent_name=agent_name, scope=scope)
    if record is None or not record.resume_eligible:
        return None
    timeout_resume_attempts = max(0, config.runner.timeout_resume_attempts)
    if timeout_resume_attempts and record.timeout_resume_count >= timeout_resume_attempts:
        return None
    return record.session_id


__all__ = [
    "AgentSessionRecord",
    "AgentSessionScope",
    "agent_session_record_path",
    "consume_agent_timeout_resume_attempt",
    "load_agent_session_record",
    "mark_agent_session_complete",
    "resolve_resumable_session_id",
    "save_agent_session_record",
]

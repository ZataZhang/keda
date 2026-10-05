"""Worktree-local agent session records for crash-reconciliation resume.

Agent Runner 的会话续传不把 session id 存进任何"第二状态源"（库表 / 租约）——
持有方是 agent CLI 自己，runner 只做**记录与回传**：把 agent 输出流里自报的
session id 落在该 Issue 的 worktree 局部（``.iar/``，仓库级 gitignore 内），
恢复轮次构建命令时读回来注入 resume 模板。

落盘复用 Agent Runner 记忆持久化的 tmp + ``os.replace`` 原子写模式：临时文件与
目标文件同目录（同文件系统内 ``rename(2)`` 原子），显式 ``encoding="utf-8"``，
失败即清理，绝不留下半截 JSON。多进程并发写同一目标按 last-write-wins 收敛。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig

_logger = logging.getLogger(__name__)

#: 会话记录相对 worktree 根的目录（与 ``.iar`` 一起被仓库级 gitignore 覆盖）。
_AGENT_SESSION_DIR_RELPATH = Path(".iar") / "agent-runner" / "sessions"


@dataclass(frozen=True)
class AgentSessionRecord:
    """一次 agent 运行自报的会话标识（worktree 局部记录的读取视图）。

    Attributes:
        agent_name: agent 注册名（记录按 agent 分文件，换人不会串会话）。
        session_id: agent CLI 的会话 id（claude stream-json ``system/init``
            事件里的 ``session_id``）。
        issue_number: 记录所属 Issue 编号（审计与排错用；读取侧不依赖它做判定）。
        updated_at: 落盘时间（UTC ISO-8601）。
    """

    agent_name: str
    session_id: str
    issue_number: int | None
    updated_at: str


def agent_session_record_path(worktree_path: Path, agent_name: str) -> Path:
    """返回某 agent 在该 worktree 的会话记录路径（不创建目录）。"""
    return worktree_path / _AGENT_SESSION_DIR_RELPATH / f"{agent_name}.json"


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
    issue_number: int | None = None,
) -> Path:
    """把 agent 自报的 session id 原子写入 worktree 局部记录。

    Args:
        worktree_path: Issue 的 worktree 根目录。
        agent_name: agent 注册名。
        session_id: 非空的会话 id。
        issue_number: 所属 Issue 编号（可选，仅用于审计）。

    Returns:
        落盘后的记录文件路径。

    Raises:
        ValueError: ``session_id`` 为空串。
        OSError: 文件系统写入失败（调用侧按旁路失败处理，不影响主流程）。
    """
    if not session_id:
        raise ValueError("session_id must be non-empty to persist an agent session record.")
    record_payload = {
        "agent": agent_name,
        "session_id": session_id,
        "issue_number": issue_number,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    target_path = agent_session_record_path(worktree_path, agent_name)
    _atomic_write_text(target_path, json.dumps(record_payload, ensure_ascii=False, indent=2) + "\n")
    return target_path


def load_agent_session_record(worktree_path: Path, agent_name: str) -> AgentSessionRecord | None:
    """读取某 agent 的会话记录；缺失、损坏或形状不符时返回 ``None``。

    读取侧一律宽容：续传是"有则优先"的增益路径，任何解析失败都等价于"没有
    会话可续"，由调用侧降级为全新会话，绝不因此中断恢复轮次。
    """
    record_path = agent_session_record_path(worktree_path, agent_name)
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
    return AgentSessionRecord(
        agent_name=str(loaded_payload.get("agent", agent_name)),
        session_id=session_id,
        issue_number=issue_number if isinstance(issue_number, int) else None,
        updated_at=updated_at if isinstance(updated_at, str) else "",
    )


def resolve_resumable_session_id(
    worktree_path: Path,
    *,
    config: AppConfig,
    agent_name: str,
    issue_number: int,
) -> str | None:
    """该 agent 在该 worktree 是否有可续传的会话；有则返回会话 id。

    三重护栏，任一不满足都返回 ``None``（= 全新会话），绝不因此报错：

    1. agent 声明了续传能力（``agents.<name>.supports_resume``）——未声明的 CLI
       拿到 id 也不会认。
    2. 记录存在且可读（损坏按"没有会话"处理）。
    3. 记录属于这个 Issue：worktree 可能被复用到别的 Issue，续错会话比不续更糟。

    调用侧是两处：**首轮领取**（崩溃对账判定「可续传」后重新入队的那次）与
    **recovery 轮次**（本轮 attempt 死掉后接着聊），两处共用同一份判据。
    """
    agent_spec = config.agents.get(agent_name)
    if agent_spec is None or not agent_spec.supports_resume:
        return None
    record = load_agent_session_record(worktree_path, agent_name)
    if record is None:
        return None
    if record.issue_number is not None and record.issue_number != issue_number:
        return None
    return record.session_id


__all__ = [
    "AgentSessionRecord",
    "agent_session_record_path",
    "load_agent_session_record",
    "resolve_resumable_session_id",
    "save_agent_session_record",
]

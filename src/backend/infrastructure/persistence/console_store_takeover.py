"""单 Issue 运行控制（人工接管状态机）的 SQLite 存储 mixin。

与 :mod:`backend.core.shared.interfaces.issue_run_control` 端口的鸭子类型实现。
本模块**不 import core**：模型与状态取值按同构定义，字符串常量与 core 侧
逐字一致，靠 :mod:`tests` 与调用点保证契约同步。

所有状态迁移都是**单语句 CAS**（``UPDATE ... WHERE`` 旧状态与绑定字段匹配后
按 rowcount 判定胜负），跨进程（Console API 写请求、runner 写结果）并发下最多
一方成功；``BEGIN IMMEDIATE`` 不使用——CAS 语句本身即 SQLite 的原子单位。
控制行读写属于**决策路径**（不是旁路记录），失败必须上抛给调用方。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# 同构状态常量（与 core issue_run_control 端口逐字一致）。
_STATE_RUNNING = "running"
_STATE_TAKEOVER_REQUESTED = "takeover_requested"
_STATE_TAKEN_OVER = "taken_over"
_STATE_VERIFIER_COMPLETED = "verifier_completed"

# 同构接管结果分类（与 core TakeoverRequestOutcome.status 逐字一致）。
_TAKEOVER_ACCEPTED = "accepted"
_TAKEOVER_PENDING = "pending"
_TAKEOVER_HANDED_OVER = "handed_over"
_TAKEOVER_BINDING_MISMATCH = "binding_mismatch"
_TAKEOVER_COMPLETED_CONFLICT = "completed_conflict"
_TAKEOVER_NOT_FOUND = "not_found"

CREATE_ISSUE_RUN_CONTROL = """
CREATE TABLE IF NOT EXISTS issue_run_control (
    repo_id TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    pr_number INTEGER,
    run_id TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    phase TEXT NOT NULL,
    state TEXT NOT NULL,
    autopilot_hold INTEGER NOT NULL DEFAULT 0,
    request_id TEXT,
    requested_at TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (repo_id, issue_number)
)
"""


@dataclass(frozen=True)
class IssueRunControlEntry:
    """控制行快照（与 core ``IssueRunControlRecord`` 同构）。"""

    repo_id: str
    issue_number: int
    run_id: str
    head_sha: str
    phase: str
    state: str
    autopilot_hold: bool
    started_at: str
    pr_number: int | None = None
    request_id: str | None = None
    requested_at: str | None = None
    completed_at: str | None = None


@dataclass(frozen=True)
class TakeoverRequestResult:
    """接管请求 CAS 结果（与 core ``TakeoverRequestOutcome`` 同构）。"""

    status: str
    observed_state: str | None = None


class IssueRunControlMixin:
    """为 SQLite console store 提供单 Issue 接管控制行读写。"""

    def register_issue_run(self, entry: IssueRunControlEntry) -> None:
        """按 (repo_id, issue_number) upsert 一条 ``running`` 控制行。

        ``autopilot_hold`` 保留旧行的值：闸门只允许由同 head 的全新 verifier
        GREEN 清除，新一轮登记不得顺手洗掉。
        """
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO issue_run_control "
                "(repo_id, issue_number, pr_number, run_id, head_sha, phase, state, "
                " autopilot_hold, request_id, requested_at, started_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL) "
                "ON CONFLICT(repo_id, issue_number) DO UPDATE SET "
                "pr_number=excluded.pr_number, run_id=excluded.run_id, "
                "head_sha=excluded.head_sha, phase=excluded.phase, state=excluded.state, "
                "autopilot_hold=issue_run_control.autopilot_hold, "
                "request_id=NULL, requested_at=NULL, "
                "started_at=excluded.started_at, completed_at=NULL",
                (
                    entry.repo_id,
                    entry.issue_number,
                    entry.pr_number,
                    entry.run_id,
                    entry.head_sha,
                    entry.phase,
                    entry.state,
                    int(entry.autopilot_hold),
                    entry.started_at,
                ),
            )
            connection.commit()

    def get_issue_run(self, *, repo_id: str, issue_number: int) -> IssueRunControlEntry | None:
        """读取该 Issue 的控制行；从未登记时返回 ``None``。"""
        with self._connect() as connection:
            control_row = connection.execute(
                "SELECT repo_id, issue_number, pr_number, run_id, head_sha, phase, state, "
                "autopilot_hold, request_id, requested_at, started_at, completed_at "
                "FROM issue_run_control WHERE repo_id = ? AND issue_number = ?",
                (repo_id, issue_number),
            ).fetchone()
        if control_row is None:
            return None
        return _row_to_issue_run_control(control_row)

    def request_issue_takeover(
        self,
        *,
        repo_id: str,
        issue_number: int,
        run_id: str,
        head_sha: str,
        request_id: str,
        requested_at: str,
    ) -> TakeoverRequestResult:
        """CAS：仅 ``running`` 且 run_id/head_sha 匹配时翻到 takeover 并立即落闸。

        CAS 未命中时**不写任何状态**，只回读当前行并按绑定/状态分类结果。
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE issue_run_control "
                "SET state = ?, request_id = ?, requested_at = ?, autopilot_hold = 1 "
                "WHERE repo_id = ? AND issue_number = ? AND run_id = ? AND head_sha = ? "
                "AND state = ?",
                (
                    _STATE_TAKEOVER_REQUESTED,
                    request_id,
                    requested_at,
                    repo_id,
                    issue_number,
                    run_id,
                    head_sha,
                    _STATE_RUNNING,
                ),
            )
            connection.commit()
            if cursor.rowcount == 1:
                return TakeoverRequestResult(
                    status=_TAKEOVER_ACCEPTED, observed_state=_STATE_TAKEOVER_REQUESTED
                )
            control_row = connection.execute(
                "SELECT repo_id, issue_number, pr_number, run_id, head_sha, phase, state, "
                "autopilot_hold, request_id, requested_at, started_at, completed_at "
                "FROM issue_run_control WHERE repo_id = ? AND issue_number = ?",
                (repo_id, issue_number),
            ).fetchone()
        if control_row is None:
            return TakeoverRequestResult(status=_TAKEOVER_NOT_FOUND)
        current = _row_to_issue_run_control(control_row)
        if current.run_id != run_id or current.head_sha != head_sha:
            return TakeoverRequestResult(
                status=_TAKEOVER_BINDING_MISMATCH, observed_state=current.state
            )
        if current.state == _STATE_TAKEOVER_REQUESTED:
            return TakeoverRequestResult(status=_TAKEOVER_PENDING, observed_state=current.state)
        if current.state == _STATE_TAKEN_OVER:
            return TakeoverRequestResult(status=_TAKEOVER_HANDED_OVER, observed_state=current.state)
        # 绑定一致却仍是 running：CAS 与回读之间恰好被并发覆盖的极端竞态，幂等按在途处理。
        if current.state == _STATE_RUNNING:
            return TakeoverRequestResult(status=_TAKEOVER_PENDING, observed_state=current.state)
        return TakeoverRequestResult(
            status=_TAKEOVER_COMPLETED_CONFLICT, observed_state=current.state
        )

    def transition_issue_run(
        self,
        *,
        repo_id: str,
        issue_number: int,
        run_id: str,
        from_states: tuple[str, ...],
        to_state: str,
        autopilot_hold: bool | None = None,
        completed_at: str | None = None,
    ) -> bool:
        """CAS 状态迁移：仅本 run 且处于 ``from_states`` 之一时翻到 ``to_state``。"""
        if not from_states:
            raise ValueError("from_states must not be empty for a CAS transition")
        set_clauses = ["state = ?"]
        params: list[object] = [to_state]
        if autopilot_hold is not None:
            set_clauses.append("autopilot_hold = ?")
            params.append(int(autopilot_hold))
        if completed_at is not None:
            set_clauses.append("completed_at = ?")
            params.append(completed_at)
        state_placeholders = ", ".join("?" for _ in from_states)
        params.extend([repo_id, issue_number, run_id])
        params.extend(from_states)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE issue_run_control SET {', '.join(set_clauses)} "
                f"WHERE repo_id = ? AND issue_number = ? AND run_id = ? "
                f"AND state IN ({state_placeholders})",
                params,
            )
            connection.commit()
            return cursor.rowcount == 1

    def has_autopilot_hold(self, *, repo_id: str, issue_number: int) -> bool:
        """该 Issue 是否落着自动驾驶闸门。"""
        with self._connect() as connection:
            hold_row = connection.execute(
                "SELECT 1 FROM issue_run_control "
                "WHERE repo_id = ? AND issue_number = ? AND autopilot_hold = 1",
                (repo_id, issue_number),
            ).fetchone()
        return hold_row is not None

    def clear_autopilot_hold(
        self, *, repo_id: str, issue_number: int, run_id: str, head_sha: str
    ) -> bool:
        """仅当控制行停在 ``verifier_completed`` 且运行身份、head 一致时清除闸门。

        ``state`` 条件防止半路清闸：闸门只允许被**这一轮**在同一 head 上跑完并
        取得 GREEN 的 verifier 打开；正在 running 或已 stale/接管完成的行都不算。
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE issue_run_control SET autopilot_hold = 0 "
                "WHERE repo_id = ? AND issue_number = ? AND autopilot_hold = 1 "
                "AND run_id = ? AND head_sha = ? AND state = ?",
                (repo_id, issue_number, run_id, head_sha, _STATE_VERIFIER_COMPLETED),
            )
            connection.commit()
            return cursor.rowcount == 1


def _row_to_issue_run_control(control_row: sqlite3.Row) -> IssueRunControlEntry:
    """把一条 ``issue_run_control`` 行还原为同构 dataclass。"""
    return IssueRunControlEntry(
        repo_id=control_row["repo_id"],
        issue_number=int(control_row["issue_number"]),
        pr_number=(int(control_row["pr_number"]) if control_row["pr_number"] is not None else None),
        run_id=control_row["run_id"],
        head_sha=control_row["head_sha"],
        phase=control_row["phase"],
        state=control_row["state"],
        autopilot_hold=bool(control_row["autopilot_hold"]),
        started_at=control_row["started_at"],
        request_id=control_row["request_id"],
        requested_at=control_row["requested_at"],
        completed_at=control_row["completed_at"],
    )

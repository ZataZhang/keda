"""运行历史查询 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder

from backend.core.use_cases.agent_runner_factory import create_console_store
from backend.core.use_cases.agent_runner_run_history import (
    RunAttemptWindow,
    load_run_attempt_details,
    load_run_invocation_details,
)

router = APIRouter(tags=["agent-runner-console-runs"])


@router.get("/agent-runner/console/runs")
def list_console_runs(repo_id: str | None = None, limit: int = 100) -> dict:
    """倒序列出最近的运行记录。"""
    bounded_limit = min(max(limit, 1), 500)
    runs = create_console_store().list_recent_runs(repo_id=repo_id, limit=bounded_limit)
    return {"runs": jsonable_encoder(runs)}


@router.get("/agent-runner/console/runs/attempts")
def list_console_run_attempts(
    repo_id: str,
    issue_number: int,
    started_at: str,
    finished_at: str,
) -> dict:
    """读取某条运行记录时间窗口内的 Agent 尝试与实际进程调用详情。"""
    if issue_number <= 0:
        raise HTTPException(status_code=400, detail="issue_number must be a positive integer.")
    parsed_started_at = _parse_timestamp(started_at)
    parsed_finished_at = _parse_timestamp(finished_at)
    if parsed_started_at is None or parsed_finished_at is None:
        raise HTTPException(
            status_code=422,
            detail="started_at and finished_at must be ISO-8601 timestamps with a timezone.",
        )
    if parsed_finished_at < parsed_started_at:
        raise HTTPException(status_code=422, detail="finished_at must not precede started_at.")

    run_history_store = create_console_store()
    run_window = RunAttemptWindow(
        repo_id=repo_id,
        issue_number=issue_number,
        started_at=parsed_started_at,
        finished_at=parsed_finished_at,
    )
    attempts = load_run_attempt_details(
        run_history_store=run_history_store,
        run_window=run_window,
    )
    invocations = load_run_invocation_details(
        run_history_store=run_history_store,
        run_window=run_window,
    )
    return {
        "attempts": jsonable_encoder(attempts),
        "invocations": jsonable_encoder(invocations),
    }


def _parse_timestamp(timestamp: str) -> datetime | None:
    """解析带时区的 ISO-8601 时间戳。"""
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_timestamp.tzinfo is None:
        return None
    return parsed_timestamp

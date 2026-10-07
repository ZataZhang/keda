"""将 KedaCode 的 Issue 执行投影到仓库已有的 PRD 活动锁。"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

_HEARTBEAT_SECONDS = 60
_WORKTREE_POLL_SECONDS = 10


class PrdActivityConflictError(RuntimeError):
    """PRD 已由其他执行入口持锁，KedaCode 不得并发处理。"""


class PrdActivityLease:
    """为带 PRD 锚点的 Issue 维持看板兼容的执行锁。"""

    def __init__(self, repo_path: Path, prd_relative_path: str, issue_number: int, agent: str):
        """记录仓库、PRD 和 Issue 身份。"""
        self._repo_path = repo_path
        self._prd_relative_path = prd_relative_path
        self._issue_number = issue_number
        self._agent = agent
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock_started_at = ""
        self._lock_path: Path | None = None
        self._command_cwd = repo_path

    def start(self) -> None:
        """存在 PRD 锚点和锁脚本时领锁，并启动心跳。"""
        relative_path = Path(self._prd_relative_path)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path.parts[:2] != ("tasks", "pending")
            or not (self._repo_path / relative_path).is_file()
            or not (self._repo_path / "scripts/shared/just/prd_lock.py").is_file()
        ):
            return
        lock_path = self._repo_path / "tasks/evidence" / relative_path.stem / "active.lock"
        if lock_path.exists():
            raise PrdActivityConflictError(
                f"PRD activity lock already exists for Issue #{self._issue_number}: {lock_path}"
            )
        self._lock_path = lock_path
        worktree_path = self._find_worktree()
        self._command_cwd = (
            worktree_path
            if worktree_path is not None and (worktree_path / relative_path).is_file()
            else self._repo_path
        )
        claim_result = self._run_lock_command(
            "claim", "--tool", f"iar/{self._agent}", "--branch", f"issue-{self._issue_number}"
        )
        if claim_result.returncode != 0:
            raise PrdActivityConflictError(
                f"Could not claim PRD activity lock for Issue #{self._issue_number}"
            )
        self._lock_started_at = self._read_lock_metadata().get("started_at", "")
        if not self._owns_lock():
            raise PrdActivityConflictError(
                f"PRD activity lock ownership changed for Issue #{self._issue_number}"
            )
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        """停止心跳，并仅释放本次执行领取的锁。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._lock_started_at and self._owns_lock():
            self._run_lock_command("release")

    def _heartbeat_loop(self) -> None:
        elapsed_seconds = 0
        while not self._stop_event.wait(_WORKTREE_POLL_SECONDS):
            if not self._owns_lock():
                return
            worktree_path = self._find_worktree()
            if (
                worktree_path is not None
                and self._command_cwd != worktree_path
                and (worktree_path / self._prd_relative_path).is_file()
            ):
                self._command_cwd = worktree_path
                self._run_lock_command(
                    "claim",
                    "--tool",
                    f"iar/{self._agent}",
                    "--branch",
                    f"issue-{self._issue_number}",
                )
            elapsed_seconds += _WORKTREE_POLL_SECONDS
            if elapsed_seconds >= _HEARTBEAT_SECONDS:
                self._run_lock_command("heartbeat")
                elapsed_seconds = 0

    def _find_worktree(self) -> Path | None:
        git_result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self._repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        current_path: Path | None = None
        for line in git_result.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = Path(line.removeprefix("worktree "))
            elif line == f"branch refs/heads/issue-{self._issue_number}":
                return current_path
        return None

    def _read_lock_metadata(self) -> dict:
        if self._lock_path is None:
            return {}
        try:
            return json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _owns_lock(self) -> bool:
        metadata = self._read_lock_metadata()
        return bool(
            self._lock_started_at
            and metadata.get("started_at") == self._lock_started_at
            and metadata.get("ai_tool") == f"iar/{self._agent}"
        )

    def _run_lock_command(self, action: str, *extra_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(self._repo_path / "scripts/shared/just/prd_lock.py"),
                action,
                self._prd_relative_path,
                *extra_args,
            ],
            cwd=self._command_cwd,
            capture_output=True,
            text=True,
            check=False,
        )

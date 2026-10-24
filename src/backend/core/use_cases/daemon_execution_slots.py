"""daemon 的常驻执行名单：领到的 Issue 提交后不等待，按轮询间隔补位。

一次性 ``kc run`` 领到一批 Issue 后要等全部跑完才退出；daemon 若沿用同样的
"一轮跑完再看下一轮"，一个长任务就会把之后进入就绪队列的所有 Issue 挡住，
并行上限再大也用不满。本模块把"领取"和"等待完成"拆开：daemon 每个仓库持有
一个 :class:`DaemonExecutionSlots`，每次轮询先清理已结束的任务，再按空位提交
新任务并立即返回。

名单同时是"本机正在执行哪些 Issue"的事实来源：领取的各通道与崩溃对账都据此
跳过名单里的 Issue，保证同一个 Issue 只由一个线程推进。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

_logger = logging.getLogger(__name__)


@dataclass
class _InFlightIssue:
    """一个已提交、尚未被清理的 Issue 执行任务。

    Attributes:
        worker_thread: 执行该 Issue 的线程。
        exit_code: 任务结束后的退出码；运行中为 ``None``。
    """

    worker_thread: threading.Thread
    exit_code: int | None = None


@dataclass
class DaemonExecutionSlots:
    """daemon 单个仓库的在执行名单与提交入口（线程安全）。

    Attributes:
        repo_id: 所属仓库标识，只用于日志与线程命名。
    """

    repo_id: str
    _in_flight: dict[int, _InFlightIssue] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reap(self) -> list[tuple[int, int]]:
        """清理已结束的任务，返回它们的 ``(Issue 编号, 退出码)``。"""
        finished_issues: list[tuple[int, int]] = []
        with self._lock:
            for issue_number, in_flight_issue in list(self._in_flight.items()):
                if in_flight_issue.worker_thread.is_alive():
                    continue
                exit_code = 1 if in_flight_issue.exit_code is None else in_flight_issue.exit_code
                finished_issues.append((issue_number, exit_code))
                del self._in_flight[issue_number]
        for issue_number, exit_code in finished_issues:
            _logger.info(
                "Issue #%d finished in repository '%s' (exit code %d); slot released.",
                issue_number,
                self.repo_id,
                exit_code,
            )
        return finished_issues

    def in_flight_issue_numbers(self) -> frozenset[int]:
        """返回本机正在执行（含已结束但尚未清理）的 Issue 编号。"""
        with self._lock:
            return frozenset(self._in_flight)

    def submit(self, issue_number: int, process_issue: Callable[[], int]) -> None:
        """在独立线程里执行一个 Issue，立即返回。

        Args:
            issue_number: Issue 编号；已在名单里时拒绝重复提交。
            process_issue: 处理该 Issue 并返回退出码的可调用对象。

        Raises:
            ValueError: 该 Issue 已在执行名单里。
        """
        with self._lock:
            if issue_number in self._in_flight:
                raise ValueError(
                    f"Issue #{issue_number} is already in flight for '{self.repo_id}'."
                )
            in_flight_issue = _InFlightIssue(
                worker_thread=threading.Thread(
                    target=self._run_and_record,
                    args=(issue_number, process_issue),
                    name=f"kc-{self.repo_id}-issue-{issue_number}",
                    # 守护线程：SIGTERM 时 daemon 先整组终止 agent 子进程树再退出，
                    # 不能让解释器卡在等待这些线程上。
                    daemon=True,
                )
            )
            self._in_flight[issue_number] = in_flight_issue
            in_flight_issue.worker_thread.start()

    def _run_and_record(self, issue_number: int, process_issue: Callable[[], int]) -> None:
        """线程入口：执行任务并记录退出码；任何异常都按失败记录，不让线程静默死掉。"""
        try:
            exit_code = process_issue()
        except Exception as exc:  # noqa: BLE001 - 单个 Issue 失败不得影响其他任务与 daemon。
            _logger.error(
                "Issue #%d in repository '%s' raised during processing: %s",
                issue_number,
                self.repo_id,
                exc,
            )
            exit_code = 1
        with self._lock:
            in_flight_issue = self._in_flight.get(issue_number)
            if in_flight_issue is not None:
                in_flight_issue.exit_code = exit_code


__all__ = ["DaemonExecutionSlots"]

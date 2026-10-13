"""模型评测本地账本的存储端口（``IEvaluationStore``）。

实现方是 Console 的 SQLite 账本（``SqliteConsoleStore`` 的评测 mixin）；
core 用例只通过本端口读写 run / 候选结果 / 逐评审评分，不感知 SQL。

约定与既有 ``runner_console`` 端口一致：端口方法使用 core 侧冻结 dataclass
（:mod:`backend.core.shared.models.model_evaluation`）作为出入参形态，
实现方鸭子类型对齐即可，不需要 import 本模块。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from backend.core.shared.models.model_evaluation import (
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationScoreRecord,
)


class EvaluationStoreError(RuntimeError):
    """存储层不变量被破坏（如状态 CAS 冲突）时抛出的端口级错误。"""


class EvaluationStateConflictError(EvaluationStoreError):
    """状态比较并交换失败：当前状态与调用方预期不符。"""


class IEvaluationStore(ABC):
    """评测 run / 候选结果 / 逐评审评分的查询、保存、CAS 与级联删除端口。"""

    @abstractmethod
    def save_evaluation_run(self, run: EvaluationRunRecord) -> None:
        """插入或整体覆盖一个 run 快照行（配置事实 + 状态）。"""

    @abstractmethod
    def get_evaluation_run(self, run_id: str) -> EvaluationRunRecord | None:
        """按 id 读取 run；不存在返回 ``None``。"""

    @abstractmethod
    def list_evaluation_runs(self) -> list[EvaluationRunRecord]:
        """按创建时间倒序列出全部 run。"""

    @abstractmethod
    def update_evaluation_run_status(
        self,
        run_id: str,
        *,
        expected_statuses: Sequence[str],
        new_status: str,
        manifest_hash: str | None = None,
        error: str | None = None,
        finished: bool = False,
    ) -> EvaluationRunRecord:
        """对 run 状态做比较并交换（CAS）。

        Args:
            run_id: 目标 run。
            expected_statuses: 允许的来源状态集合；当前状态不在集合内即视为
                并发冲突，抛 :class:`EvaluationStateConflictError`。
            new_status: 目标状态。
            manifest_hash: 非 ``None`` 时同步写入（进入 awaiting_judge_consent 时冻结）。
            error: 非 ``None`` 时同步写入错误摘要。
            finished: True 时同时冻结 ``finished_at``。

        Returns:
            EvaluationRunRecord: 迁移后的最新行。
        """

    @abstractmethod
    def save_evaluation_result(self, result: EvaluationResultRecord) -> None:
        """插入或整体覆盖一个候选结果行。"""

    @abstractmethod
    def get_evaluation_result(self, result_id: str) -> EvaluationResultRecord | None:
        """按 id 读取候选结果；不存在返回 ``None``。"""

    @abstractmethod
    def list_evaluation_results(self, run_id: str) -> list[EvaluationResultRecord]:
        """列出某个 run 的全部候选结果（按 case/preset 稳定排序）。"""

    @abstractmethod
    def save_evaluation_score(self, score: EvaluationScoreRecord) -> None:
        """插入或覆盖一条逐评审评分行（同 result × judge 幂等覆盖）。"""

    @abstractmethod
    def list_evaluation_scores(self, run_id: str) -> list[EvaluationScoreRecord]:
        """列出某个 run 的全部评分行（含全部非 valid 状态）。"""

    @abstractmethod
    def delete_evaluation_run(self, run_id: str) -> EvaluationRunRecord | None:
        """级联删除一个 run 及其候选结果与评分行；返回被删除的 run（便于清理产物）。"""

    @abstractmethod
    def mark_interrupted_evaluation_runs(self, interrupted_at: str) -> int:
        """把所有活跃状态 run 标记为 interrupted，返回受影响行数（启动自愈用）。"""

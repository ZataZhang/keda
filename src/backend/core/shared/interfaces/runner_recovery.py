"""Agent Runner 严格恢复账本的存储端口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from backend.core.shared.models.runner_recovery import (
    OperationIntent,
    OperationRecord,
    OperationState,
    RecoveryFailureRecord,
    RecoveryRunRecord,
    RecoveryStageRecord,
)


class IRunnerRecoveryStore(ABC):
    """恢复决策所依赖的严格持久化端口。

    与运行历史和审计不同，此端口上的读写失败必须向调用方传播；调用方必须在
    账本写入成功后才执行对应的外部副作用。
    """

    @abstractmethod
    def begin_operation(self, intent: OperationIntent) -> OperationRecord:
        """持久化操作意图，或返回同一稳定身份的现有记录。

        Args:
            intent: 发送前确定的操作身份、目标和载荷摘要。

        Returns:
            已提交到本地 SQLite 的操作快照。

        Raises:
            RuntimeError: 同一操作键绑定到不同业务内容时抛出。
            sqlite3.Error: 严格存储不可写或事务失败时原样传播。
        """

    @abstractmethod
    def get_operation(self, operation_key: str) -> OperationRecord | None:
        """读取操作；遗留的 ``in_flight`` 会在事务中转成 ``unknown``。

        Args:
            operation_key: 稳定操作键。

        Returns:
            当前操作快照；不存在时为 ``None``。
        """

    @abstractmethod
    def transition_operation(
        self,
        operation_key: str,
        expected_states: Sequence[OperationState],
        new_state: OperationState,
        *,
        remote_reference: str | None = None,
        last_error: str | None = None,
    ) -> bool:
        """用状态比较并交换推进账本，供并发恢复者争抢唯一发送权。

        Args:
            operation_key: 稳定操作键。
            expected_states: 允许推进的旧状态集合。
            new_state: 新状态。
            remote_reference: 已确认的远端对象标识。
            last_error: 脱敏后的最近诊断。

        Returns:
            仅一个并发调用成功时为 ``True``。
        """

    @abstractmethod
    def record_failure(self, failure: RecoveryFailureRecord) -> RecoveryRunRecord:
        """累计恢复预算并保存下一次可运行时间。

        Args:
            failure: 故障、时间和有界预算参数。

        Returns:
            更新后的跨进程恢复状态。
        """

    @abstractmethod
    def get_run(self, run_key: str) -> RecoveryRunRecord | None:
        """读取跨进程恢复预算。

        Args:
            run_key: 仓库与 Issue 绑定的稳定恢复键。

        Returns:
            恢复状态；不存在时为 ``None``。
        """

    @abstractmethod
    def finish_run(self, run_key: str) -> None:
        """将一次成功完成的恢复链标记为完成，供之后的独立执行重开预算。"""

    @abstractmethod
    def save_stage(self, stage: RecoveryStageRecord) -> None:
        """保存通过的阶段及其成果前提。

        Args:
            stage: 阶段状态与代码、base、验证契约摘要。
        """

    @abstractmethod
    def get_stage(self, stage_key: str) -> RecoveryStageRecord | None:
        """按稳定阶段键读取已保存的结果。

        Args:
            stage_key: 阶段身份。

        Returns:
            阶段快照；不存在时为 ``None``。
        """

    @abstractmethod
    def list_unresolved_operations(self, run_key: str) -> list[OperationRecord]:
        """列出仍需对账或处理的操作。

        Args:
            run_key: 恢复运行键。

        Returns:
            ``pending``、``in_flight`` 或 ``unknown`` 操作。
        """

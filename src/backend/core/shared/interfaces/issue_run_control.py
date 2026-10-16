"""单 Issue 运行控制端口：独立 verifier 的人工接管（Issue #278）。

设计要点：

- Console API 进程与 runner 进程**跨进程**协作，唯一共享介质是
  ``console.history_db_path`` 指向的本地 SQLite；本端口把「谁在跑这次的
  独立 verifier、谁请求了接管」收敛成一张按 (repo_id, issue_number) 唯一的
  控制行，所有状态迁移都走**原子 compare-and-set**（UPDATE ... WHERE 旧状态
  与绑定字段匹配），绝不"读-改-写"两段式，避免接管请求与 verifier 完成竞态
  时两边都以为自己赢。
- 绑定身份是 repo_id + issue_number + run_id + head_sha 四元组：接管请求必须
  携带 UI 快照里看到的 run_id 与 head_sha，与控制行不一致即判 stale binding
  （页面数据过期），拒绝发出信号——防止把「上一次 verifier」的接管意图落到
  「正在跑的这一次」头上。
- ``autopilot_hold`` 是**持久**闸门：接管成功或复核过期都会置位，此后
  post-PR supervisor、review_once、merge queue 三个入口一律 fail closed；
  唯一清除路径是**同一 head 上的全新 verifier GREEN**。
- GitHub labels / PR 仍是交付事实源；本表只记录控制面状态，不承载结论。

存储侧字段一律用字符串（与 ``console_store_*`` 其余同构模型一致：持久层
不 import core），取值见下方状态常量；实现必须保证
:meth:`IIssueRunControl.request_issue_takeover` 与
:meth:`IIssueRunControl.transition_issue_run` 是**单语句原子 CAS**：
跨进程并发下最多一方成功。SQLite 实现位于
``infrastructure/persistence/console_store_takeover.py``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

#: 当前纳入控制面的唯一阶段：独立 verifier 运行中。
PHASE_VERIFIER = "verifier"

# 控制行状态机取值。迁移边（全部经 CAS）：
#   running → takeover_requested          （接管请求胜出）
#   running → verifier_completed / verifier_stale / pr_merged / expired
#   takeover_requested → taken_over       （runner 确认进程树已终止并完成交班）
#   takeover_requested → takeover_failed  （无法证实终止，交班未完成；含重启后
#                                          无人认领的孤儿请求，PRD §7.7）
#   verifier_completed → expired          （CAS 赢了但结论最终未发布：树漂移 / RED）
STATE_RUNNING = "running"
STATE_TAKEOVER_REQUESTED = "takeover_requested"
STATE_TAKEN_OVER = "taken_over"
STATE_TAKEOVER_FAILED = "takeover_failed"
STATE_VERIFIER_COMPLETED = "verifier_completed"
STATE_VERIFIER_STALE = "verifier_stale"
STATE_PR_MERGED = "pr_merged"
STATE_EXPIRED = "expired"

#: 尚未尘埃落定的状态：verifier 仍在跑，或已被请求停止但尚未交班。
OPEN_STATES: tuple[str, ...] = (STATE_RUNNING, STATE_TAKEOVER_REQUESTED)

# 接管请求 CAS 的结果分类（API 据此映射 HTTP 语义）：
TAKEOVER_ACCEPTED = "accepted"
"""本次请求把 ``running`` 翻成了 ``takeover_requested``（202 pending）。"""
TAKEOVER_PENDING = "pending"
"""已有等价的接管请求在途，幂等视作受理（202 pending）。"""
TAKEOVER_HANDED_OVER = "handed_over"
"""交班已完成，Issue 已经在人手里（200，UI 直接展示终态）。"""
TAKEOVER_BINDING_MISMATCH = "binding_mismatch"
"""run_id / head_sha 与控制行不符：页面数据已过期（409 conflict）。"""
TAKEOVER_COMPLETED_CONFLICT = "completed_conflict"
"""verifier 在请求落地前已完成/复核已过期/PR 已合并——没有可停的进程（409）。"""
TAKEOVER_NOT_FOUND = "not_found"
"""该 (repo_id, issue_number) 没有控制行：当前没有受控 verifier 在跑（409）。"""
TAKEOVER_STATE_CHANGE_CONFLICT = "state_change_conflict"
"""CAS 反复未命中且控制行仍读到 ``running``：取消意图没有持久，绝不按受理返回（409）。"""


@dataclass(frozen=True)
class IssueRunControlRecord:
    """一个 Issue 当前（或最近一次）受控运行的控制行快照。"""

    repo_id: str
    issue_number: int
    run_id: str
    head_sha: str
    phase: str
    state: str
    autopilot_hold: bool
    started_at: str  # ISO8601 UTC
    pr_number: int | None = None
    request_id: str | None = None
    requested_at: str | None = None
    completed_at: str | None = None


@dataclass(frozen=True)
class TakeoverRequestOutcome:
    """接管请求的结果：分类 + 观察到的控制行状态（诊断与文案用）。"""

    status: str
    observed_state: str | None = None


class IIssueRunControl(ABC):
    """单 Issue 运行控制存储端口（跨进程共享的接管状态机）。"""

    @abstractmethod
    def register_issue_run(self, record: IssueRunControlRecord) -> None:
        """登记一次新的受控运行（verifier 起跑前调用）。

        按 (repo_id, issue_number) upsert：上一轮无论处于什么终态都被新一轮
        覆盖为 ``running``；``autopilot_hold`` **保留旧值**——闸门只由同 run/head
        且经发布端 fresh-read 确认的全新 GREEN 清除，不能被新一轮登记顺手洗掉。

        Args:
            record: 新控制行；``state`` 必须是 ``running``，``request_id`` /
                ``requested_at`` / ``completed_at`` 由实现置空。
        """
        ...

    @abstractmethod
    def get_issue_run(self, *, repo_id: str, issue_number: int) -> IssueRunControlRecord | None:
        """读取该 Issue 当前控制行；从未登记过时返回 ``None``。"""
        ...

    @abstractmethod
    def request_issue_takeover(
        self,
        *,
        repo_id: str,
        issue_number: int,
        run_id: str,
        head_sha: str,
        request_id: str,
        requested_at: str,
    ) -> TakeoverRequestOutcome:
        """CAS：仅当控制行为 ``running`` 且 run_id/head_sha 完全匹配时翻到
        ``takeover_requested``、写入请求信息并立即置 ``autopilot_hold``。

        不满足匹配时**绝不写任何状态**，只回读并分类。已在
        ``takeover_requested`` 的重复请求幂等返回 ``pending``。

        ``accepted`` 与 ``pending`` 都对调用方承诺"取消意图 + 保持位已经持久"，
        因此实现必须保证：绑定一致却仍读到 ``running``（CAS 未命中与回读之间的并发
        窗口）时重试 CAS 并要求回读离开 ``running``，否则返回
        ``state_change_conflict``，不得把没有落盘的请求报成受理。
        """
        ...

    @abstractmethod
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
        """CAS 状态迁移：仅当控制行仍属于 ``run_id`` 且处于 ``from_states``
        之一时翻到 ``to_state``。

        Args:
            autopilot_hold: 非 ``None`` 时在同一条语句里落闸/开闸；``None``
                保持原值。
            completed_at: 终态写入时间（ISO8601 UTC），仅随本次迁移写入。

        Returns:
            是否迁移成功（rowcount==1）。失败说明另一方已在竞态中胜出，调用方
            据实回读处理。
        """
        ...

    @abstractmethod
    def has_autopilot_hold(self, *, repo_id: str, issue_number: int) -> bool:
        """该 Issue 是否处于自动驾驶闸门（supervisor / review / merge 一律 fail closed）。"""
        ...

    @abstractmethod
    def has_autopilot_hold_for_issue(self, *, issue_number: int) -> bool:
        """**不限仓库**地回答"该 Issue 编号是否落着闸门"。

        仅用于调用方解析不出 ``repo_id`` 时的 fail-closed 兜底：按错 key 查询查不到
        行，等于把正在被人工接管的 Issue 放回自动流程（FR-5）。跨仓库同号 Issue 的
        代价是自动处理多等一轮，方向仍是保守侧，因此本方法**不**用于任何放行判断。
        """
        ...

    @abstractmethod
    def clear_autopilot_hold(
        self, *, repo_id: str, issue_number: int, run_id: str, head_sha: str
    ) -> bool:
        """仅当运行身份与提交都匹配时清除闸门。

        这是 ``autopilot_hold`` 唯一的清除路径：**同一 head 上的全新 verifier
        GREEN**。run 或 head 已变化时返回 ``False``，闸门保持。
        """
        ...

    @abstractmethod
    def set_issue_run_pr_number(
        self, *, repo_id: str, issue_number: int, run_id: str, pr_number: int
    ) -> bool:
        """把关联 PR 编号补写到**本轮**运行行。

        登记发生在 Draft PR 刚刚发布、调用方手里还没有 PR 身份的时刻；交班
        fresh-read 拿到权威编号后用它回填，界面与后续阶段就不必再另行读取 Issue。
        ``run_id`` 不匹配（新一轮已重新登记同一 Issue）时返回 ``False``，绝不
        改写别的运行的行。
        """
        ...

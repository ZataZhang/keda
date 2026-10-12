"""单 Issue 人工接管（Issue #278）在 runner 侧的编排入口。

Console API 进程与 runner 进程通过
:mod:`backend.core.shared.interfaces.issue_run_control` 的控制表跨进程协作；
本模块承载 runner 侧的全部动作：

- **登记**：独立 verifier 起跑前写入 ``running`` 控制行并交出取消探针
  （:func:`begin_verifier_run`）；``repo_id`` 缺失或旁路存储不可用时**不开放
  接管窗口**，verifier 照常运行——接管是观测面增强，不是验证前提。
- **终态迁移**：完成（``verifier_completed``，仅由同 run/head 的真实 GREEN 且
  PR fresh-read 仍匹配后清闸）、
  失败/超时/红牌（``expired`` / ``takeover_failed``）、接管确认
  （``takeover_requested → taken_over`` 落闸）。
- **交班**：接管 CAS 受理时立即落 hold；取消确认后
  :func:`finalize_verifier_takeover` 做 PR fresh-read
  （已合并→停止后续阶段；HEAD 漂移→置 ``verifier_stale`` 并清理过期
  ``validation/verifier-passed`` 标签）、Issue 转 ``agent/review``、写
  ``cancelled_by_human`` 交接评论。**绝不**产生或复用任何 verdict 语义。
- **闸门观测**：:func:`autopilot_hold_blocks` 供 post-PR supervisor、
  review_once、merge queue 三个入口 fail closed。

边界与既有约定一致：GitHub labels / PR 仍是交付事实源，控制行只承载控制面
状态；交班的 GitHub 副作用逐段 best-effort——闸门随接管请求 CAS 已经落下，
不能因一条评论发送失败而被回退。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from backend.core.shared.interfaces.issue_run_control import (
    PHASE_VERIFIER,
    STATE_EXPIRED,
    STATE_PR_MERGED,
    STATE_RUNNING,
    STATE_TAKEN_OVER,
    STATE_TAKEOVER_FAILED,
    STATE_TAKEOVER_REQUESTED,
    STATE_VERIFIER_COMPLETED,
    STATE_VERIFIER_STALE,
    TAKEOVER_ACCEPTED,
    TAKEOVER_BINDING_MISMATCH,
    TAKEOVER_COMPLETED_CONFLICT,
    TAKEOVER_HANDED_OVER,
    TAKEOVER_NOT_FOUND,
    TAKEOVER_PENDING,
    IssueRunControlRecord,
)

if TYPE_CHECKING:
    from backend.core.shared.interfaces.agent_runner import IGitHubClient
    from backend.core.shared.models.agent_runner import AppConfig, IssueSummary

#: 接管请求结果 → HTTP 语义映射：202 已登记（进程尚未确认停止），
#: 200 已完成交班，409 目标已变化 / 无可停进程 / 页面数据过期。
TAKEOVER_HTTP_STATUS: dict[str, int] = {
    TAKEOVER_ACCEPTED: 202,
    TAKEOVER_PENDING: 202,
    TAKEOVER_HANDED_OVER: 200,
    TAKEOVER_BINDING_MISMATCH: 409,
    TAKEOVER_COMPLETED_CONFLICT: 409,
    TAKEOVER_NOT_FOUND: 409,
}

_logger = logging.getLogger(__name__)

#: 交班评论的 hidden marker：语义是"人工接管、无 verdict"，与
#: ``iar:verifier-verdict`` 互斥——解析器绝不从这条记录推断任何结论。
VERIFIER_TAKEOVER_MARKER_PREFIX = "<!-- iar:verifier-takeover"


def _utc_now_iso() -> str:
    """控制行时间戳：ISO8601 UTC，与 console 存储其余时间列同格式。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VerifierCancelledByHumanError(RuntimeError):
    """独立 verifier 被人工接管终止：无 verdict、不重试、不换 agent。

    刻意不进 :func:`run_issue_with_agent_fallback` 的换人元组，也不进
    recovery 循环——"人被接管"不是可由下一个 agent 修复的失败。
    """


def resolve_issue_run_control() -> object | None:
    """解析跨进程共享的 Issue 运行控制存储；不可用时返回 ``None``。

    core 不得静态 import engines（架构门禁），经工厂 shim 的
    ``create_console_store`` 惰性解析——实现端为旁路 SQLite 存储，
    与控制面表同库同文件。
    """
    try:
        from backend.core.use_cases.agent_runner_factory import create_console_store

        store = create_console_store()
    except Exception as exc:  # noqa: BLE001 - 接管是观测面增强，存储失败不阻断验证。
        _logger.warning("Issue run control store unavailable (%s); takeover disabled.", exc)
        return None
    required_methods = (
        "register_issue_run",
        "get_issue_run",
        "request_issue_takeover",
        "transition_issue_run",
        "has_autopilot_hold",
        "clear_autopilot_hold",
    )
    if not all(hasattr(store, method_name) for method_name in required_methods):
        _logger.warning("Console store lacks issue run control; takeover disabled.")
        return None
    return store


@dataclass(frozen=True)
class VerifierRunHandle:
    """一次已登记的 verifier 运行的 runner 侧句柄。

    ``store`` 为 duck-typed 的 IIssueRunControl 实现：持久层按架构约束不
    import core，无法用 isinstance 断言，因此类型放宽为 ``object``，方法集
    由 :func:`resolve_issue_run_control` 校验。
    """

    store: object
    repo_id: str
    issue_number: int
    run_id: str
    head_sha: str

    def _transition(
        self,
        *,
        from_states: tuple[str, ...],
        to_state: str,
        autopilot_hold: bool | None = None,
    ) -> bool:
        return bool(
            self.store.transition_issue_run(  # type: ignore[attr-defined]
                repo_id=self.repo_id,
                issue_number=self.issue_number,
                run_id=self.run_id,
                from_states=from_states,
                to_state=to_state,
                autopilot_hold=autopilot_hold,
                completed_at=_utc_now_iso(),
            )
        )

    def cancel_check(self) -> bool:
        """watchdog 秒级探针：仅当控制行仍属于本次 run 且被请求接管时为真。

        读库异常**向上抛**：由 process_runner 的探针容错按"未取消"处理，
        绝不因一次瞬时 SQLite 失败杀掉正在工作的 verifier。
        """
        row = self.store.get_issue_run(  # type: ignore[attr-defined]
            repo_id=self.repo_id, issue_number=self.issue_number
        )
        return (
            row is not None
            and row.state == STATE_TAKEOVER_REQUESTED
            and row.run_id == self.run_id
            and row.head_sha == self.head_sha
        )

    def confirm_takeover(self) -> bool:
        """取消落地后的复核 + 交接 CAS（PRD §7.4：再次确认 run id 与 HEAD）。

        先回读控制行确认接管请求确属本次运行，再 CAS
        ``takeover_requested → taken_over`` 并同语句落 ``autopilot_hold``。
        回读不符（请求已被其他路径改写）返回 ``False``，调用方按
        takeover_failed 处理。
        """
        row = self.store.get_issue_run(  # type: ignore[attr-defined]
            repo_id=self.repo_id, issue_number=self.issue_number
        )
        if (
            row is None
            or row.state != STATE_TAKEOVER_REQUESTED
            or row.run_id != self.run_id
            or row.head_sha != self.head_sha
        ):
            return False
        return self._transition(
            from_states=(STATE_TAKEOVER_REQUESTED,),
            to_state=STATE_TAKEN_OVER,
            autopilot_hold=True,
        )

    def mark_taken_over_failed(self) -> bool:
        """无法确认接管归属时置 ``takeover_failed``：交班未完成，已落闸继续保持。"""
        return self._transition(
            from_states=(STATE_TAKEOVER_REQUESTED,),
            to_state=STATE_TAKEOVER_FAILED,
        )

    def mark_completed(self) -> bool:
        """CAS ``running → verifier_completed``（写 verdict 副作用之前，PRD §7.6）。

        返回 ``False`` 表示另一方（接管请求）在竞态中胜出：本次 verdict 不得发布。
        """
        return self._transition(from_states=(STATE_RUNNING,), to_state=STATE_VERIFIER_COMPLETED)

    def clear_hold_on_green(self) -> bool:
        """GREEN verdict 落回登记的 run/head 才允许清闸（唯一清除路径）。"""
        return bool(
            self.store.clear_autopilot_hold(  # type: ignore[attr-defined]
                repo_id=self.repo_id,
                issue_number=self.issue_number,
                run_id=self.run_id,
                head_sha=self.head_sha,
            )
        )

    def mark_expired(self) -> bool:
        """本轮运行以失败/超时/红牌收场：关闭接管窗口，避免幽灵 pending。

        ``running → expired``；若请求恰好在最后时刻落地，则
        ``takeover_requested → takeover_failed``（进程已退出，交班无从发生）。
        """
        if self._transition(from_states=(STATE_RUNNING,), to_state=STATE_EXPIRED):
            return True
        return self._transition(
            from_states=(STATE_TAKEOVER_REQUESTED,),
            to_state=STATE_TAKEOVER_FAILED,
        )

    def mark_merged(self) -> bool:
        """fresh-read 发现 PR 已被外部合并时记录 ``pr_merged``。"""
        return self._transition(
            from_states=(STATE_TAKEN_OVER, STATE_VERIFIER_COMPLETED),
            to_state=STATE_PR_MERGED,
            autopilot_hold=True,
        )

    def mark_stale(self) -> bool:
        """fresh-read 发现 HEAD 已漂移：``taken_over → verifier_stale``（闸门保持）。"""
        return self._transition(from_states=(STATE_TAKEN_OVER,), to_state=STATE_VERIFIER_STALE)


def begin_verifier_run(
    *,
    repo_id: str | None,
    issue_number: int,
    head_sha: str,
) -> VerifierRunHandle | None:
    """verifier 起跑前登记受控运行；返回 ``None`` 表示本次无接管窗口。

    以下情况**照常运行 verifier**，只关闭接管入口并记一条日志：
    ``repo_id`` 未知（未带 ``--repo-id`` 的手工 CLI 运行）、旁路存储不可用、
    登记写入失败。绝不允许接管基础设施把验证本身打断。
    """
    if not repo_id or not head_sha:
        _logger.debug(
            "Issue #%d: verifier run registered without a takeover window "
            "(repo_id=%r, head_sha=%r).",
            issue_number,
            repo_id,
            head_sha,
        )
        return None
    store = resolve_issue_run_control()
    if store is None:
        return None
    run_id = str(uuid.uuid4())
    try:
        store.register_issue_run(  # type: ignore[attr-defined]
            IssueRunControlRecord(
                repo_id=repo_id,
                issue_number=issue_number,
                run_id=run_id,
                head_sha=head_sha,
                phase=PHASE_VERIFIER,
                state=STATE_RUNNING,
                autopilot_hold=False,
                started_at=_utc_now_iso(),
            )
        )
    except Exception as exc:  # noqa: BLE001 - 旁路登记失败不阻断 verifier。
        _logger.warning(
            "Issue #%d: verifier takeover registration failed (%s); "
            "running verifier without a takeover window.",
            issue_number,
            exc,
        )
        return None
    return VerifierRunHandle(
        store=store,
        repo_id=repo_id,
        issue_number=issue_number,
        run_id=run_id,
        head_sha=head_sha,
    )


def autopilot_hold_blocks(*, repo_id: str | None, issue_number: int) -> bool:
    """该 Issue 是否处于人工接管闸门：post-PR 自动阶段一律 fail closed。

    读库异常按"有闸门"处理（fail closed）：merge queue / supervisor 每轮都会
    重试，一次瞬时 SQLite 失败绝不能变成"闸门没读到、自动合并放行"。
    ``repo_id`` 缺失时返回 ``False``，因为这种运行未启用接管控制面；已知仓库
    但存储解析失败时返回 ``True``，避免读不到持久闸门时自动放行。
    """
    if not repo_id:
        return False
    store = resolve_issue_run_control()
    if store is None:
        _logger.warning(
            "Issue #%d: cannot read autopilot hold for repository %s; treating as held.",
            issue_number,
            repo_id,
        )
        return True
    try:
        return bool(
            store.has_autopilot_hold(  # type: ignore[attr-defined]
                repo_id=repo_id, issue_number=issue_number
            )
        )
    except Exception as exc:  # noqa: BLE001 - 读不到闸门状态时不放行。
        _logger.warning(
            "Issue #%d: autopilot hold check failed (%s); treating as held.",
            issue_number,
            exc,
        )
        return True


def discard_stale_verdict(
    *,
    repo_id: str | None,
    issue_number: int,
    verifier_run_id: str | None,
    verdict_input_sha: str,
) -> bool:
    """verdict 因 PR 合并/HEAD 漂移不可发布时，记 ``verifier_stale`` 并落闸。

    PRD §7.6："不应用旧 verdict 或启动后续自动阶段"。控制行仍是本轮
    ``verifier_completed`` 时同语句置 ``autopilot_hold``——此后 supervisor /
    review_once / merge queue 一律 fail closed，直到当前 HEAD 出现全新 GREEN。

    ``verifier_run_id`` 与 ``verdict_input_sha`` 必须同时匹配产出该 verdict 的
    控制行，才允许改写：不匹配说明新一轮已重新登记，过期 verdict 不得把新运行
    （即使碰巧是同一 HEAD）拖成 stale。

    Returns:
        是否成功落闸（无运行身份、无控制行 / 控制行已属另一轮 / 状态已不同 →
        ``False``，调用方只记日志）。
    """
    if not repo_id or not verifier_run_id:
        return False
    store = resolve_issue_run_control()
    if store is None:
        return False
    try:
        row = store.get_issue_run(  # type: ignore[attr-defined]
            repo_id=repo_id, issue_number=issue_number
        )
        if row is None or row.run_id != verifier_run_id or row.head_sha != verdict_input_sha:
            return False
        return bool(
            store.transition_issue_run(  # type: ignore[attr-defined]
                repo_id=repo_id,
                issue_number=issue_number,
                run_id=verifier_run_id,
                from_states=(STATE_VERIFIER_COMPLETED,),
                to_state=STATE_VERIFIER_STALE,
                autopilot_hold=True,
                completed_at=_utc_now_iso(),
            )
        )
    except Exception as exc:  # noqa: BLE001 - 闸门记不上时 GitHub 门禁仍在。
        _logger.warning(
            "Issue #%d: could not record verifier_stale for discarded verdict (%s).",
            issue_number,
            exc,
        )
        return False


def clear_verifier_hold_after_green(
    *,
    repo_id: str | None,
    issue_number: int,
    verifier_run_id: str | None,
    verifier_input_sha: str,
) -> bool:
    """PR fresh-read 确认 GREEN 对应当前 HEAD 后，按运行身份清除闸门。"""
    if not repo_id or not verifier_run_id:
        return False
    store = resolve_issue_run_control()
    if store is None:
        return False
    try:
        handle = VerifierRunHandle(
            store=store,
            repo_id=repo_id,
            issue_number=issue_number,
            run_id=verifier_run_id,
            head_sha=verifier_input_sha,
        )
        return handle.clear_hold_on_green()
    except Exception as exc:  # noqa: BLE001 - 清闸失败保持人工闸门。
        _logger.warning(
            "Issue #%d: could not clear autopilot hold after fresh GREEN (%s).",
            issue_number,
            exc,
        )
        return False


def mark_verifier_pr_merged(
    *,
    repo_id: str | None,
    issue_number: int,
    verifier_run_id: str | None,
) -> bool:
    """verifier 完成后 fresh-read 确认 PR 已合并时绑定当前 run 记录终态。"""
    if not repo_id or not verifier_run_id:
        return False
    store = resolve_issue_run_control()
    if store is None:
        return False
    try:
        return bool(
            store.transition_issue_run(  # type: ignore[attr-defined]
                repo_id=repo_id,
                issue_number=issue_number,
                run_id=verifier_run_id,
                from_states=(STATE_VERIFIER_COMPLETED,),
                to_state=STATE_PR_MERGED,
                autopilot_hold=True,
                completed_at=_utc_now_iso(),
            )
        )
    except Exception as exc:  # noqa: BLE001 - 记录失败不改变 GitHub 判断。
        _logger.warning(
            "Issue #%d: could not record pr_merged after verifier completion (%s).",
            issue_number,
            exc,
        )
        return False


def build_verifier_takeover_comment(
    issue_number: int,
    *,
    pr_url: str | None,
) -> str:
    """构造 ``cancelled_by_human`` 交接评论：显式声明**无 verdict**。"""
    lines = [
        f"{VERIFIER_TAKEOVER_MARKER_PREFIX} issue={issue_number} outcome=cancelled_by_human -->",
        "",
        "## ⏸ Independent verifier stopped — handed over to human review",
        "",
        "A Console operator took over this Issue while the independent verifier "
        "was running. The verifier subprocess tree for **this Issue only** was "
        "terminated; the daemon and other in-flight Issues are unaffected.",
        "",
        "- No verdict was produced: this is **not** a PASS, RED or YELLOW result, "
        "and `validation/verifier-passed` is not (and may have been removed as "
        "stale) set.",
        "- The Draft PR is kept as Draft for manual review; nothing was marked ready.",
        "- Automatic merge for this Issue is on hold (`autopilot_hold`) until a "
        "fresh independent verifier GREEN lands on the current HEAD.",
    ]
    if pr_url:
        lines.append("")
        lines.append(f"Review the retained Draft PR: {pr_url}")
    return "\n".join(lines)


def finalize_verifier_takeover(
    *,
    repo_id: str | None,
    issue: "IssueSummary",
    config: "AppConfig",
    github_client: "IGitHubClient",
    expected_branch: str,
    handle: VerifierRunHandle | None = None,
) -> None:
    """取消确认后的交班：PR fresh-read、清理过期标签、转 review、写交接评论。

    调用点在 ``_process_ready_issue`` / ``_process_blocked_resolution`` 捕获
    :class:`VerifierCancelledByHumanError` 处——此时进程树已终止、控制行已
    ``taken_over``（gate 内 ``confirm_takeover`` 完成 CAS + 落闸）。本函数只做
    GitHub 侧事实的收敛，逐段 best-effort，任何一步失败都**不上抛**：闸门与
    状态已随控制行落定，一条评论失败不能把交班回退成"仍在自动跑"。

    Args:
        handle: gate 交接的 run 句柄；``None`` 时按控制行回读重建（异常栈不
            跨层携带句柄，交班只需 repo/issue 与登记时的 head 绑定）。
    """
    if handle is None and repo_id:
        store = resolve_issue_run_control()
        rebuilt_row = None
        if store is not None:
            try:
                rebuilt_row = store.get_issue_run(  # type: ignore[attr-defined]
                    repo_id=repo_id, issue_number=issue.number
                )
            except Exception as exc:  # noqa: BLE001 - 回读失败降级为纯 GitHub 交班。
                _logger.warning(
                    "Issue #%d: takeover handoff could not reload the control row (%s).",
                    issue.number,
                    exc,
                )
        if rebuilt_row is not None and rebuilt_row.state in (
            STATE_TAKEOVER_REQUESTED,
            STATE_TAKEN_OVER,
        ):
            handle = VerifierRunHandle(
                store=store,  # type: ignore[arg-type]
                repo_id=repo_id,
                issue_number=issue.number,
                run_id=rebuilt_row.run_id,
                head_sha=rebuilt_row.head_sha,
            )
    pr_context = None
    try:
        pr_context = github_client.get_pull_request_context(expected_branch, require_success=True)
    except Exception as exc:  # noqa: BLE001 - 无法证明 PR 状态时不伪造交班终态。
        _logger.warning(
            "Issue #%d: takeover handoff could not freshly read the PR for branch %s (%s); "
            "leaving the handoff state unchanged until GitHub can be read.",
            issue.number,
            expected_branch,
            exc,
        )
        return

    if pr_context is None:
        # 开放 PR 查询成功返回空时，必须另行证明它确实已合并，不能把 CLI 错误
        # 或外部关闭误记为 pr_merged。
        try:
            merged_pr_url = github_client.find_merged_pr_by_head(
                expected_branch, require_success=True
            )
        except Exception as exc:  # noqa: BLE001 - 无法证明 merge 时不伪造终态。
            _logger.warning(
                "Issue #%d: takeover handoff could not establish whether branch %s "
                "was merged (%s); leaving the handoff state unchanged.",
                issue.number,
                expected_branch,
                exc,
            )
            return
        if merged_pr_url:
            # PR 已合并：自动阶段到此为止，不改 Issue 标签、不写交班评论。
            # 控制行记 pr_merged（若还能 CAS 成功）。
            if handle is not None:
                try:
                    handle.mark_merged()
                except Exception as exc:  # noqa: BLE001 - 终态 CAS 失败只影响观测面。
                    _logger.warning(
                        "Issue #%d: takeover handoff could not record pr_merged (%s).",
                        issue.number,
                        exc,
                    )
            _logger.info(
                "Issue #%d: branch %s was externally merged before takeover handoff; "
                "automatic stages stopped.",
                issue.number,
                expected_branch,
            )
            return

        # PR 已关闭但没有合并：仍完成人工交接，不过不伪造 pr_merged，且评论
        # 不会提供已失效的 PR 链接。
        _logger.info(
            "Issue #%d: branch %s has no open or merged PR at takeover handoff; "
            "continuing the Issue handoff without a PR link.",
            issue.number,
            expected_branch,
        )

    head_drifted = (
        handle is not None and pr_context is not None and pr_context.head_sha != handle.head_sha
    )
    if head_drifted:
        try:
            handle.mark_stale()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - 状态记不上不阻断清标签。
            _logger.warning(
                "Issue #%d: takeover handoff could not record verifier_stale (%s).",
                issue.number,
                exc,
            )

    # 过期 verifier 通过标签必须清掉（PRD §7.5/§7.6）：接管路径没有 verdict，
    # 留着旧标签等于让 merge queue 看到一个不存在的结论。
    if pr_context is not None and pr_context.number is not None:
        try:
            github_client.edit_issue_labels(
                pr_context.number,
                remove=(config.labels.verifier_passed,),
            )
        except Exception as exc:  # noqa: BLE001 - 清标签失败降级为人审提示。
            _logger.warning(
                "Issue #%d: takeover handoff could not remove stale %r from PR #%s (%s).",
                issue.number,
                config.labels.verifier_passed,
                pr_context.number,
                exc,
            )

    try:
        github_client.edit_issue_labels(
            issue.number,
            add=(config.labels.review,),
            remove=(config.labels.running,),
        )
    except Exception as exc:  # noqa: BLE001 - 标签失败时评论仍给出人审入口。
        _logger.warning(
            "Issue #%d: takeover handoff could not move labels to %r (%s).",
            issue.number,
            config.labels.review,
            exc,
        )

    try:
        github_client.comment_issue(
            issue.number,
            build_verifier_takeover_comment(
                issue.number,
                pr_url=pr_context.pr_url if pr_context is not None else None,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 评论失败不回退已完成的交班。
        _logger.warning(
            "Issue #%d: takeover handoff comment failed (%s); "
            "labels already moved to human review.",
            issue.number,
            exc,
        )

    _logger.info(
        "Issue #%d: verifier take-over finalized; %s; Draft PR %s kept as draft.",
        issue.number,
        "HEAD drifted from verifier input (verifier_stale)"
        if head_drifted
        else "handed to human review",
        f"#{pr_context.number}"
        if pr_context is not None and pr_context.number is not None
        else expected_branch,
    )


class IssueControlUnavailableError(RuntimeError):
    """接管控制存储不可达：不能确定取消范围，一律 fail closed。

    Console action 把它收敛成 ``ConsoleActionError``，路由据此回 400 并带上不可控
    原因（而非把"请求已登记"伪装成"进程已停止"），操作者看到可操作的失败文案，
    绝不会因此去停整个 daemon。
    """


@dataclass(frozen=True)
class VerifierTakeoverRequestResult:
    """Console 接管请求的分类结果（供路由映射 HTTP 语义与界面文案）。"""

    outcome: str
    """取值见 ``issue_run_control`` 的 TAKEOVER_* 常量。"""
    observed_state: str | None
    """回读到的控制行状态（诊断用；无行时为 ``None``）。"""
    http_status: int
    """按 PRD §7.3 映射的 HTTP 状态码（202 / 200 / 409）。"""


def request_verifier_takeover(
    *,
    repo_id: str,
    issue_number: int,
    run_id: str,
    head_sha: str,
    control_store: object | None = None,
) -> VerifierTakeoverRequestResult:
    """Console 侧发起单 Issue 接管：CAS ``running`` → ``takeover_requested``。

    仅当控制行仍为 ``running`` 且 ``run_id`` / ``head_sha`` 与 UI 快照完全一致时
    受理；否则不写任何状态，只回读并分类。存储不可达时抛
    :class:`IssueControlUnavailableError`——接管范围无法确定时必须 fail closed，
    绝不回退到停止 daemon 或其他 Issue。

    Args:
        repo_id: 目标仓库 id。
        issue_number: 目标 Issue 正整数编号。
        run_id: UI 从详情读到的当前运行身份。
        head_sha: UI 从详情读到的目标 PR HEAD。
        control_store: 调用方（Console action）已持有的共享控制存储；具备
            ``request_issue_takeover`` 能力时直接复用，避免重复解析，否则回退到
            :func:`resolve_issue_run_control`。

    Returns:
        分类结果，含映射后的 HTTP 状态码。
    """
    store = (
        control_store
        if control_store is not None and hasattr(control_store, "request_issue_takeover")
        else resolve_issue_run_control()
    )
    if store is None:
        raise IssueControlUnavailableError(
            "Issue run control store is unavailable; takeover cannot be safely scoped."
        )
    outcome = store.request_issue_takeover(  # type: ignore[attr-defined]
        repo_id=repo_id,
        issue_number=issue_number,
        run_id=run_id,
        head_sha=head_sha,
        request_id=str(uuid.uuid4()),
        requested_at=_utc_now_iso(),
    )
    http_status = TAKEOVER_HTTP_STATUS.get(outcome.status, 409)
    return VerifierTakeoverRequestResult(
        outcome=outcome.status,
        observed_state=outcome.observed_state,
        http_status=http_status,
    )


@dataclass(frozen=True)
class ActiveVerifierProjection:
    """Issue 详情对当前受控 verifier 的可展示投影（fresh read，非缓存推断）。"""

    run_id: str
    head_sha: str
    state: str
    can_take_over: bool
    pr_number: int | None
    autopilot_hold: bool


def active_verifier_projection(
    *,
    repo_id: str | None,
    issue_number: int,
) -> ActiveVerifierProjection | None:
    """读取该 Issue 当前控制行并投影为界面字段；无受控运行或不可达返回 ``None``。

    ``can_take_over`` 只在控制行为 ``running`` 时为真——已请求 / 已交班 /
    已完成 / HEAD 漂移均不可再发起接管。绝不仅凭"最新生命周期事件"推测运行中。
    """
    if not repo_id:
        return None
    store = resolve_issue_run_control()
    if store is None:
        return None
    try:
        row = store.get_issue_run(  # type: ignore[attr-defined]
            repo_id=repo_id, issue_number=issue_number
        )
    except Exception as exc:  # noqa: BLE001 - 读不到活动态时不展示接管。
        _logger.warning(
            "Issue #%d: active verifier projection read failed (%s); hiding takeover.",
            issue_number,
            exc,
        )
        return None
    if row is None:
        return None
    return ActiveVerifierProjection(
        run_id=row.run_id,
        head_sha=row.head_sha,
        state=row.state,
        can_take_over=row.state == STATE_RUNNING,
        pr_number=row.pr_number,
        autopilot_hold=row.autopilot_hold,
    )

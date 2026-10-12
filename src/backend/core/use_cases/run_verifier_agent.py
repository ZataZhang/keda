"""Independent verifier agent — 运行与门禁编排 (PR#2 T3)。

T3 在 builder 提交之后、开 PR 之前,由一个**独立 verifier agent**(尽量换一个
model、在 builder 提交点的干净 worktree)从需求意图独立复现真实入口并对抗性
证伪,产出结构化 verdict 喂给发布门禁。

本模块只负责**编排**:候选 agent 选择与回退、干净 worktree 前置条件、证据快照
与恢复、人工接管取消探针、verdict 发布前的 CAS,以及 fail-safe 阻断消息。verdict
协议本身(结论模型 / 隐藏 marker / prompt 组装)在 :mod:`agent_runner_verdict`,
原始响应落盘在 :mod:`agent_runner_verifier_response_log`,verdict 的 PR 侧副作用
在 :mod:`agent_runner_verdict_pr_effects`。协议与落盘的名字在本模块重新导出,
既有调用点仍按 ``run_verifier_agent`` 这条导入路径使用。
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_candidate_fallback import (
    build_agent_candidates,
    effective_fallback_candidates,
)
from backend.core.use_cases.agent_runner_evidence_snapshot import (
    restore_evidence_snapshot,
    snapshot_evidence_dir,
)
from backend.core.use_cases.agent_runner_failure import is_provider_capacity_failure
from backend.core.use_cases.agent_runner_issue_control import (
    VerifierCancelledByHumanError,
    VerifierRunHandle,
    begin_verifier_run,
)
from backend.core.use_cases.agent_runner_structured_evidence import (
    EvidenceManifest,
    ValidationEvidenceError,
    has_structured_evidence_marker,
    load_evidence_manifest,
)
from backend.core.use_cases.agent_runner_validation import (
    resolve_issue_evidence_dir,
    validation_required,
)
from backend.core.use_cases.agent_runner_verdict import (
    ValidationVerdict as ValidationVerdict,
    VerifierUnavailableError as VerifierUnavailableError,
    build_verifier_prompt as build_verifier_prompt,
    format_verifier_verdict_marker as format_verifier_verdict_marker,
    parse_verifier_verdict as parse_verifier_verdict,
)
from backend.core.use_cases.agent_runner_verdict_pr_effects import (
    apply_verifier_verdict_to_pr as apply_verifier_verdict_to_pr,
    build_verifier_yellow_comment as build_verifier_yellow_comment,
)
from backend.core.use_cases.agent_runner_verifier_response_log import (
    VERIFIER_RESPONSE_FILENAME,
    save_cancelled_verifier_response,
    save_timed_out_verifier_response,
    write_verifier_response_log,
)
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.use_cases.agent_invocation_tracing import PHASE_VERIFICATION
from backend.core.use_cases.lifecycle_agent_resolution import effective_prd_overrides
from backend.core.use_cases.run_agent_once import (
    extract_agent_response_text,
    get_head_sha,
    has_changes,
    run_agent_with_prompt_resilient,
)

_logger = logging.getLogger(__name__)


def run_verifier_agent(
    issue: IssueSummary,
    worktree_path: Path,
    builder_sha: str,
    manifest: EvidenceManifest,
    verifier_agent: str,
    process_runner: IProcessRunner,
    *,
    config: AppConfig | None = None,
    timeout_seconds: int | None = None,
    inactivity_timeout_seconds: int | None = None,
    response_log_path: Path | None = None,
    model_selection: ModelSelection | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ValidationVerdict:
    """Run the independent verifier agent and return its parsed verdict.

    在 builder 提交点的(干净)worktree、用 ``verifier_agent``(应不同于 builder)
    跑 :func:`build_verifier_prompt`,捕获输出并 :func:`parse_verifier_verdict`。
    解析为 fail-safe:无可解析 verdict 即 ``red``。本函数只负责"跑 + 解析",
    是否启用、选哪个 agent、red 后如何 repair,由编排层决定。

    Args:
        config: 应用配置;注册表取自 ``config.agents``,``None`` 时使用内置
            默认注册表。
        timeout_seconds: 墙钟上限。
        inactivity_timeout_seconds: 连续无输出多久判卡死。与墙钟并存,让墙钟
            能按最慢的真实工作量放宽,同时真卡死仍被及时杀掉。
        response_log_path: 可选,把原始响应写到这里供事后排查(见
            :func:`write_verifier_response_log`)。
        cancel_check: 人工接管取消探针（Issue #278），透传给
            ``process_runner.run``；探针为真时进程树被终止、本函数把
            ``InterruptedError`` 转译为 ``VerifierCancelledByHumanError``。

    Returns:
        ``ValidationVerdict``;verifier 的输出文本作为 ``findings`` 带回。

    Raises:
        subprocess.TimeoutExpired: verifier 超时被杀。抛出前先把已收到的部分
            输出落盘,否则这一轮的判定过程完全无从查证。
        VerifierCancelledByHumanError: 人工接管取消了本次 verifier（部分输出
            已落盘、无 verdict）。
    """
    prompt = build_verifier_prompt(issue, builder_sha, manifest)
    try:
        result = run_agent_with_prompt_resilient(
            verifier_agent,
            prompt,
            worktree_path,
            process_runner,
            config=config,
            capture_output=True,
            timeout_seconds=timeout_seconds,
            inactivity_timeout_seconds=inactivity_timeout_seconds,
            issue=issue,
            model_selection=model_selection,
            invocation_phase=PHASE_VERIFICATION,
            cancel_check=cancel_check,
        )
    except subprocess.TimeoutExpired as timeout_error:
        save_timed_out_verifier_response(
            timeout_error,
            issue=issue,
            verifier_agent=verifier_agent,
            builder_sha=builder_sha,
            response_log_path=response_log_path,
        )
        raise
    except InterruptedError as cancelled_error:
        # 人工接管（Issue #278）：进程树已被 watchdog 回收，这里只负责把
        # "被取消"与"超时/运行失败"区分开上抛，并保存无 verdict 的部分输出。
        save_cancelled_verifier_response(
            cancelled_error,
            issue=issue,
            verifier_agent=verifier_agent,
            builder_sha=builder_sha,
            response_log_path=response_log_path,
        )
        raise VerifierCancelledByHumanError(
            f"Independent verifier for Issue #{issue.number} was terminated by a "
            "human takeover; no verdict was produced."
        ) from cancelled_error
    response_text = extract_agent_response_text(result)
    verdict = replace(
        parse_verifier_verdict(response_text, findings=response_text.strip()[:4000]),
        # 观测回填：真实跑完的 agent 名（候选回退后 ≠ 配置值）与其自报用量。
        agent=verifier_agent,
        token_usage=result.token_usage,
    )

    saved_log_path: Path | None = None
    if response_log_path is not None:
        saved_log_path = write_verifier_response_log(
            response_log_path,
            [
                "# Independent verifier raw response",
                f"# issue: #{issue.number}",
                f"# verifier agent: {verifier_agent}",
                f"# builder sha: {builder_sha}",
                f"# parsed risk: {verdict.risk}",
                f"# verdict marker found: {'no' if verdict.missing_marker else 'yes'}",
                f"# response chars: {len(response_text)}",
            ],
            response_text,
        )
    if verdict.missing_marker:
        _logger.error(
            "Independent verifier (agent %r) emitted NO verdict marker for Issue #%d; "
            "fail-safe blocking as red. Raw response was %d chars%s — check whether it "
            "actually judged the change or just failed to follow the output protocol.",
            verifier_agent,
            issue.number,
            len(response_text),
            f", saved to {saved_log_path}" if saved_log_path else " (not saved)",
        )
    else:
        _logger.info(
            "Independent verifier (agent %r) returned %s for Issue #%d (%d chars%s).",
            verifier_agent,
            verdict.risk.upper(),
            issue.number,
            len(response_text),
            f", saved to {saved_log_path}" if saved_log_path else "",
        )
    return verdict


def _choose_verifier_agent(
    config: AppConfig,
    builder_agent: str,
    *,
    issue: IssueSummary | None = None,
    prd_overrides: Mapping[str, str] | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> str:
    """Pick an agent for the verifier, preferring one different from the builder.

    生命周期矩阵（或 PRD 覆盖）显式声明了具体 agent 时用它；阶段绑定预设时
    用预设声明的 agent（绑定整体决定 agent）；声明 ``auto`` 或两层都未声明时
    沿用既有语义：``verifier_agent`` 配成具体 agent 则用它；``auto`` 时从
    fallback 链里挑第一个 ≠ builder 的（独立性来自换人）；都没有再退回 builder。
    """
    from backend.core.shared.models.lifecycle_agent import (
        LIFECYCLE_AGENT_AUTO,
        normalize_lifecycle_agent_value,
    )
    from backend.core.use_cases.lifecycle_agent_resolution import (
        declared_lifecycle_preset,
        resolve_lifecycle_agent,
    )

    merged_overrides = dict(prd_overrides or {})
    declared_value = merged_overrides.get("verifier")
    if declared_value is None:
        declared_value = config.lifecycle_agents.declared_value("verifier")
    bound_preset_name = declared_lifecycle_preset(
        "verifier",
        config,
        issue=issue,
        prd_preset_overrides=prd_preset_overrides,
    )
    if bound_preset_name is not None or (
        declared_value is not None
        and normalize_lifecycle_agent_value(declared_value) != LIFECYCLE_AGENT_AUTO
    ):
        return resolve_lifecycle_agent(
            "verifier",
            config,
            issue=issue,
            selected_agent=builder_agent,
            prd_overrides=merged_overrides,
            prd_preset_overrides=prd_preset_overrides,
        )
    configured = config.validation.verifier_agent
    if configured and configured != "auto":
        return configured
    # 有效回退链（候选数组非空时整体接管、空数组把旧名单折叠为无预设候选）上
    # 第一个 ≠ builder 的 agent；直接读旧名单会在候选数组写后读到 stale 顺序。
    for fallback_candidate in effective_fallback_candidates(config):
        if fallback_candidate.agent != builder_agent:
            return fallback_candidate.agent
    return builder_agent


def _verifier_candidate_agents(
    config: AppConfig,
    builder_agent: str,
    primary_agent: str,
) -> tuple[str, ...]:
    """返回本次 verifier 的候选 agent 序列（首选之后按 fallback 链补齐）。

    显式声明的 ``verifier`` agent 仍然是首选——只有当它**根本跑不起来**（CLI
    不存在 / 额度耗尽 / 进程级失败）时才顺延。候选池复用**有效回退候选链**
    （``agent_fallback_candidates`` 非空时整体接管，空数组折叠旧
    ``agent_fallback_order``）与 ``max_agent_switches``，与 builder 换 agent
    用同一套配置与预算，不新增开关；独立性由"≠ builder"保证（换 model 即换判定视角）。

    具体枚举委托 :func:`build_agent_candidates`（review / supervisor 阶段共用同一
    套候选口径）。

    Args:
        config: 应用配置。
        builder_agent: 本次 builder 用的 agent，候选里必须排除。
        primary_agent: 首选 verifier agent（显式声明或 auto 挑出的那个）。

    Returns:
        去重后的候选序列，长度最多 ``max_agent_switches + 1``；首选始终在首位。
    """
    return build_agent_candidates(config, primary_agent, exclude_agent=builder_agent)


def _format_verifier_unavailable_message(
    *,
    issue_number: int,
    tried_agents: tuple[str, ...],
    failure: BaseException | None,
) -> str:
    """构造"所有候选 verifier agent 都跑不起来"的阻断消息。

    与 :func:`_format_verifier_block_message` 同一条纪律：把"verifier 侧跑不起来"
    与"verifier 判了 red"讲成两件不同的事，builder 才不会被指使去修一个不存在
    的缺陷。措辞刻意不要求 agent 去"修"，因为这是 runner/agent 侧的问题。

    Args:
        issue_number: 当前 Issue 编号。
        tried_agents: 依次尝试过的 agent 名。
        failure: 最后一次失败。

    Returns:
        写进 Issue 失败摘要的阻断消息。
    """
    failure_summary = str(failure).lower() if failure is not None else ""
    if "no parseable verdict marker" in failure_summary:
        reason = (
            "the candidate pool was exhausted after at least one agent failed to return "
            "a parseable verdict marker"
        )
    elif failure is not None and is_provider_capacity_failure(failure):
        reason = (
            "every candidate agent's provider is out of quota or rate limited, so retrying "
            "the same agents before the usage window resets cannot help"
        )
    else:
        reason = "every candidate agent failed to run or return a usable verdict"
    return (
        f"The independent verifier could not run or return a parseable verdict for issue "
        f"#{issue_number}: {reason}. "
        f"Tried agent(s): {', '.join(tried_agents) or '(none)'}. "
        "This is a runner/agent-side failure, NOT a proven defect in the change: do not "
        "invent fixes for findings that do not exist. Restore a working verifier agent "
        f"(or quota) before re-running. Last failure: {failure}"
    )


def _format_verifier_block_message(
    *,
    verdict: ValidationVerdict,
    verifier_agent: str,
    issue_number: int,
    response_log_path: Path,
) -> str:
    """构建阻断消息:把"真判 red"与"没吐 verdict"讲成两件不同的事。

    这条消息既进 attempt 历史的 Detail 列,也进 builder 的 recovery prompt。以前
    两种成因共用一句"Fix what the verifier found",于是 verifier 只是漏了 marker
    时,builder 会被指使去修一个根本不存在的发现——白烧一轮 attempt(实证:
    freshai Issue #111,verifier 用的 agent 未产出 marker)。
    """
    if verdict.missing_marker:
        return (
            f"Independent verifier (agent '{verifier_agent}') produced NO verdict marker "
            f"for issue #{issue_number}, so the fail-safe protocol blocks it like RED. "
            "This is a verifier-side protocol failure, NOT a proven defect in the change: "
            "do not invent fixes for findings that do not exist. Its raw output was saved "
            f"to `{response_log_path}` — read it first. If it contains no actual finding, "
            "leave the implementation and evidence as they are; if the verifier agent keeps "
            "failing to emit the marker, that is a runner/agent problem to escalate rather "
            "than a code problem. Raw output head:\n"
            f"{verdict.findings}"
        )
    return (
        f"Independent verifier (agent '{verifier_agent}') returned RED for "
        f"issue #{issue_number}: it could not independently confirm the change "
        "does what the issue asks. Findings:\n"
        f"{verdict.findings}\n"
        "Fix what the verifier found, or correct the Realistic Validation "
        "oracle if the check itself is wrong."
    )


def _reject_unpublished_verdict(
    run_handle: VerifierRunHandle | None, issue_number: int, error: ValidationEvidenceError
) -> ValidationEvidenceError:
    """结论最终没有发布时收回 ``verifier_completed``，并把原异常交回调用点 ``raise``。

    ``mark_completed`` 的 CAS 早于树漂移与 RED 两道检查：结论被这两道检查拒绝时
    控制行若仍停在 ``verifier_completed``，FR-1 的状态分类就会在整段 RED recovery
    窗口里对着一个从未落到 PR 上的 verdict 报"验证已完成"。收回为 ``expired``；
    接管恰好在同一时刻落地时改记 ``takeover_failed``。收回动作失败只记日志，绝不
    掩盖原始结论错误，也绝不伪造交班。
    """
    if run_handle is None:
        return error
    try:
        reclaimed = run_handle.mark_verdict_unpublished()
    except Exception as store_exc:  # noqa: BLE001 - 状态收回失败不改变 verifier 结论。
        _logger.warning(
            "Issue #%d: could not reclaim verifier_completed after an unpublished " "verdict (%s).",
            issue_number,
            store_exc,
        )
        return error
    if not reclaimed:
        _logger.info(
            "Issue #%d: verdict was not published and the control row was already "
            "settled by another terminal transition.",
            issue_number,
        )
    return error


def run_verifier_gate(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    builder_agent: str,
    *,
    prd_overrides: Mapping[str, str] | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
    repo_id: str | None = None,
) -> ValidationVerdict | None:
    """Pre-PR independent-verifier gate (PR#2 T3 integration).

    在 builder 通过证据门禁后、开 PR 之前运行:换一个 agent 对带结构化证据的
    issue 做独立对抗复验。

    - **red** → 抛 ``ValidationEvidenceError``,落进 builder 既有的 recovery
      循环(verifier findings 当 repair 反馈,自动重做、bounded;耗尽才升级给
      人)。因为是 pre-PR 门禁,所以"PR 存在 ⟹ verifier 已通过",无需额外的
      daemon 双门禁。
    - **yellow** → 记警告并放行,返回 verdict 供调用方在开 PR 后贴警告评论。
    - **green** → 放行,返回 verdict 供调用方在开 PR 后置 ``validation/verifier-passed`` label。

    默认开(``verifier_enabled``)。仅对带 ``iar:structured-evidence`` marker、且要求验证的 issue 生效。

    超时按运行事故处理而不是 verdict:墙钟(``verifier_timeout_seconds``)与静默期
    (``verifier_inactivity_timeout_seconds``)两条线并存,超时不伪造 verdict,而是
    先把部分输出落盘再让异常上抛。另外 verifier 复跑证据脚本会覆盖 builder 的
    ``rv-*`` 文件,因此这里对证据目录做快照并在结束后恢复。

    选定的 verifier agent 跑不起来或没有输出可解析 marker 时，按**有效回退候选链**
    (``agent_fallback_candidates``，空数组时折叠旧 ``agent_fallback_order``；封顶
    ``max_agent_switches``、始终 ≠ builder)顺延；全部候选耗尽后停止该 run，并把
    失败交给 runner 处理，绝不让 builder 修复 verifier 协议故障。

    Returns:
        ``ValidationVerdict`` 当 verifier 实际运行(verdict 非 red 时返回,red
        时抛异常);``None`` 当 verifier 未启用 / issue 不要求验证 / 无结构化证据
        marker(调用方据此决定是否在 PR 上做 label/评论副作用)。

    Raises:
        ValidationEvidenceError: verifier 给出明确 red 结论（经 recovery 自动打回 builder）。
        VerifierUnavailableError: 所有候选均无法运行或没有可解析结论；这是 verifier
            侧故障，不会启动 builder repair 循环。
        subprocess.TimeoutExpired: verifier 超时被杀(部分输出已落盘、证据已恢复)。
        VerifierCancelledByHumanError: 人工接管（Issue #278）终止了本次 verifier，
            或 verdict 与接管请求的最后竞态中接管获胜；无 verdict 发布。

    Args:
        repo_id: 稳定仓库标识。提供时（Console 托管运行）在控制存储登记本次
            verifier 运行并接入取消探针，**开放人工接管窗口**；``None``（未带
            ``--repo-id`` 的手工 CLI 运行）跳过登记，行为与本特性之前一致。
    """
    if not config.validation.verifier_enabled:
        return None
    if not validation_required(issue.body, config):
        return None
    if not has_structured_evidence_marker(issue.body):
        return None
    if has_changes(worktree_path, process_runner):
        raise ValidationEvidenceError(
            "Independent verifier requires a clean committed worktree; "
            "commit the final implementation before verification."
        )

    manifest = load_evidence_manifest(
        worktree_path,
        config,
        evidence_dir=resolve_issue_evidence_dir(worktree_path, config, issue),
    )
    verifier_agent = _choose_verifier_agent(
        config,
        builder_agent,
        issue=issue,
        prd_overrides=effective_prd_overrides(issue, prd_overrides),
        prd_preset_overrides=prd_preset_overrides,
    )
    # 校验阶段的模型选择：只认 verifier 自己的绑定（不继承实现者，继承仅限
    # executor 语义的 fix/closeout）；verifier_agent 与预设声明不一致
    # （既有键 / fallback 选了别人）时丢弃并记日志。
    from backend.core.use_cases.lifecycle_agent_resolution import (
        resolve_lifecycle_model_selection,
    )
    from backend.core.use_cases.run_agent_once import drop_model_selection_for_agent

    verifier_model_selection = drop_model_selection_for_agent(
        verifier_agent,
        resolve_lifecycle_model_selection(
            "verifier",
            config,
            issue=issue,
            prd_preset_overrides=prd_preset_overrides,
        ),
    )
    builder_sha = get_head_sha(worktree_path, process_runner)
    # 人工接管（Issue #278）：verifier 起跑前登记受控运行。repo_id 缺失、旁路
    # 存储不可用或登记失败时返回 ``None``——照常验证，只是不开放接管窗口。
    run_handle = begin_verifier_run(
        repo_id=repo_id,
        issue_number=issue.number,
        head_sha=builder_sha,
    )
    response_log_path = (
        resolve_issue_evidence_dir(worktree_path, config, issue) / VERIFIER_RESPONSE_FILENAME
    )
    candidate_agents = _verifier_candidate_agents(config, builder_agent, verifier_agent)
    chosen_agent = verifier_agent
    verdict: ValidationVerdict | None = None
    tried_agents: list[str] = []
    last_failure: BaseException | None = None
    protocol_failure_observed = False

    for index, candidate_agent in enumerate(candidate_agents):
        is_last_candidate = index == len(candidate_agents) - 1
        # verifier 复跑的正是 builder 的 capture 脚本,会把 rv-*.txt 覆盖成自己的
        # (可能是 negative control 的)输出;快照 + 恢复保证发布出去的仍是通过门禁
        # 的那份证据。恢复放在 finally:超时被杀时污染最严重，逐个候选取快照则保证
        # 换 agent 重跑时起点干净。
        evidence_snapshot = snapshot_evidence_dir(worktree_path, config, issue)
        try:
            candidate_verdict = run_verifier_agent(
                issue,
                worktree_path,
                builder_sha,
                manifest,
                candidate_agent,
                process_runner,
                config=config,
                timeout_seconds=config.validation.verifier_timeout_seconds,
                inactivity_timeout_seconds=config.validation.verifier_inactivity_timeout_seconds,
                response_log_path=response_log_path,
                # 候选回退换了 agent 就丢弃模型绑定（每个 CLI 模型命名空间不同）。
                model_selection=drop_model_selection_for_agent(
                    candidate_agent, verifier_model_selection
                ),
                cancel_check=run_handle.cancel_check if run_handle is not None else None,
            )
        except subprocess.TimeoutExpired:
            # 超时按运行事故处理:不伪造 verdict、也不换 agent(与原文语义一致)。
            if run_handle is not None:
                run_handle.mark_expired()
            raise
        except VerifierCancelledByHumanError:
            # 人工接管（Issue #278）：进程树已回收。控制存储再次确认 run id 与
            # HEAD 归属后才映射为"人工取消"交班；确认不了则置 takeover_failed，
            # 走 verifier 侧停止路径——绝不伪造接管成功，也绝不顺延下一个候选。
            if run_handle is None:
                raise
            confirmed = False
            try:
                confirmed = run_handle.confirm_takeover()
            except Exception as store_exc:  # noqa: BLE001 - 确认失败按未确认处理。
                _logger.warning(
                    "Issue #%d: takeover confirmation failed (%s).",
                    issue.number,
                    store_exc,
                )
            if not confirmed:
                try:
                    run_handle.mark_taken_over_failed()
                except Exception:  # noqa: BLE001 - 状态记不上也不能伪造交班完成。
                    _logger.warning(
                        "Issue #%d: could not record takeover_failed after failed confirmation.",
                        issue.number,
                    )
                raise VerifierUnavailableError(
                    f"Independent verifier for Issue #{issue.number} was interrupted, but the "
                    "takeover request could not be confirmed against the run control store; "
                    "the run is stopped without handoff."
                ) from None
            raise
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            # agent 跑不起来（CLI 缺失 / 额度耗尽 / 进程级失败）不该判死整个 Issue:
            # 顺延下一个候选,全部失败才降级成 fail-safe red 交回可恢复的 repair 循环。
            tried_agents.append(candidate_agent)
            last_failure = (
                RuntimeError(
                    "a previous verifier candidate returned no parseable verdict marker; "
                    f"candidate {candidate_agent} then failed to run: {exc}"
                )
                if protocol_failure_observed
                else exc
            )
            if is_last_candidate:
                break
            _logger.warning(
                "Independent verifier agent %r could not run for Issue #%d (%s); "
                "trying the next candidate agent.",
                candidate_agent,
                issue.number,
                exc,
            )
            continue
        finally:
            restore_evidence_snapshot(
                evidence_snapshot,
                keep_filenames=(VERIFIER_RESPONSE_FILENAME,),
            )
        if candidate_verdict.missing_marker:
            protocol_failure_observed = True
            tried_agents.append(candidate_agent)
            candidate_agent_slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate_agent)
            candidate_response_log_path = response_log_path.with_name(
                f"{response_log_path.stem}-{candidate_agent_slug}{response_log_path.suffix}"
            )
            try:
                if response_log_path.is_file():
                    candidate_response_log_path.write_text(
                        response_log_path.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
            except OSError as exc:
                _logger.warning(
                    "Could not preserve verifier response for Issue #%d and agent %r: %s",
                    issue.number,
                    candidate_agent,
                    exc,
                )
            last_failure = RuntimeError(
                f"agent {candidate_agent} returned no parseable verdict marker; "
                f"raw response log: {candidate_response_log_path}"
            )
            _logger.warning(
                "Independent verifier agent %r emitted no verdict marker for Issue #%d; "
                "trying the next independent candidate.",
                candidate_agent,
                issue.number,
            )
            verdict = None
            if is_last_candidate:
                break
            continue
        verdict = candidate_verdict
        chosen_agent = candidate_agent
        break

    if verdict is None:
        if run_handle is not None:
            run_handle.mark_expired()
        raise VerifierUnavailableError(
            _format_verifier_unavailable_message(
                issue_number=issue.number,
                tried_agents=tuple(tried_agents),
                failure=last_failure,
            )
        )
    if chosen_agent != verifier_agent:
        _logger.warning(
            "Independent verifier fell back to agent %r for Issue #%d; the configured "
            "verifier agent %r could not run.",
            chosen_agent,
            issue.number,
            verifier_agent,
        )
    if run_handle is not None:
        # 写 verdict 副作用之前的 compare-and-set（PRD §7.6）：接管请求恰好在
        # 最后时刻落地时 verdict 不得发布，交班由调用方按取消路径处理。
        completed = False
        try:
            completed = run_handle.mark_completed()
        except Exception as store_exc:  # noqa: BLE001 - 无法 CAS 时不得发布 verdict。
            _logger.warning(
                "Issue #%d: could not record verifier_completed (%s).",
                issue.number,
                store_exc,
            )
            raise VerifierUnavailableError(
                f"Independent verifier finished for Issue #{issue.number}, but its completion "
                "could not be atomically recorded; the verdict is not published."
            ) from store_exc
        if not completed:
            takeover_confirmed = False
            try:
                takeover_confirmed = run_handle.confirm_takeover()
            except Exception as store_exc:  # noqa: BLE001 - 确认失败按未确认处理。
                _logger.warning(
                    "Issue #%d: takeover confirmation failed after verdict race (%s).",
                    issue.number,
                    store_exc,
                )
            if takeover_confirmed:
                raise VerifierCancelledByHumanError(
                    f"Independent verifier verdict for Issue #{issue.number} was discarded: "
                    "a human takeover request won the final race; no verdict was published."
                )
            try:
                run_handle.mark_taken_over_failed()
            except Exception:  # noqa: BLE001 - 终态记录失败也不能发布竞态中落败的 verdict。
                _logger.warning(
                    "Issue #%d: could not record takeover_failed after verdict race loss.",
                    issue.number,
                )
            raise VerifierUnavailableError(
                f"Independent verifier finished for Issue #{issue.number}, but a pending "
                "takeover request left the run control state unverifiable; the verdict is "
                "not published and the run is stopped."
            )
    if get_head_sha(worktree_path, process_runner) != builder_sha or has_changes(
        worktree_path, process_runner
    ):
        raise _reject_unpublished_verdict(
            run_handle,
            issue.number,
            ValidationEvidenceError(
                "Independent verifier changed the committed code tree; restore its "
                "negative-control edits before accepting a verdict."
            ),
        )
    if verdict.blocks:
        raise _reject_unpublished_verdict(
            run_handle,
            issue.number,
            ValidationEvidenceError(
                _format_verifier_block_message(
                    verdict=verdict,
                    verifier_agent=chosen_agent,
                    issue_number=issue.number,
                    response_log_path=response_log_path,
                )
            ),
        )
    if verdict.risk == "yellow":
        _logger.warning(
            "Independent verifier returned YELLOW for issue #%d: %s",
            issue.number,
            verdict.findings,
        )
    return replace(
        verdict,
        verifier_input_sha=builder_sha,
        verifier_run_id=run_handle.run_id if run_handle is not None else None,
    )

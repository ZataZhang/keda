"""活跃 attempt 的停滞监督 observer（Issue #256 / FR-5…FR-7）。

监督器是共享 run-attempt 的一个**低频只读 observer**：它不拥有任务、不排队、
不新建 writer，只在"到点且无实质进展"时读一次现场、给出一个结论，然后要么交回
正常执行，要么把诊断摘要交给**既有** recovery 与既有门禁。

三条不可让步的约束：

1. **默认关闭即逐字节不变**——``enabled=False`` 时本模块不起线程、不登记进程、
   一次模型都不调用，被监督的调用就是原来那次调用。
2. **只读**——诊断走 agent 自己声明的只读 profile，输入只有进展签名与人读摘要，
   不含提示词、环境变量或凭据。
3. **精确取消**——只有 ``stalled`` 结论、且取消前**当场**再读一次现场与进程归属
   全部一致时才终止那个进程组；任何一项对不上都只记录原因并交班。

线程与日志归属：observer 跑在独立守护线程里，而 per-Issue 日志的 handler 按线程
id 过滤（见 ``agent_runner_output_routing._ThreadLogFilter``），所以监督线程的
记录只进运行控制台。巡检/诊断/交班结论先在内存里按顺序留存，被监督的那次调用
回到主线程后原样补写进**既有** Issue 日志——不新开第二份日志文件，也不新增表。

attempt 键唯一到"这一次真实进程调用"（含 invocation id）：被监督的调用一旦结束，
这个键就从进程登记册上消失，遗留的监督线程再也找不到可取消的对象，不可能误杀
后续 recovery 轮次新起的 writer。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.shared.models.agent_stall import (
    AgentStallCancelledError,
    ProgressSnapshot,
    StallSupervisorConfig,
    StallVerdictKind,
    SupervisorVerdict,
)
from backend.core.use_cases.agent_invocation_tracing import (
    InvocationTraceContext,
    bound_invocation_trace_context,
    new_invocation_id,
)
from backend.core.use_cases.agent_runner_git import get_current_branch, get_head_sha

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 可 grep 的标记（与既有 [iar-*] 标记同族）
# ---------------------------------------------------------------------------

STALL_CHECK_MARKER = "[iar-stall-check]"
"""一次巡检的结论行（进展/无进展计时）。"""

STALL_DIAGNOSIS_MARKER = "[iar-stall-diagnosis]"
"""一次只读诊断的结论行。"""

STALL_HANDOFF_MARKER = "[iar-stall-handoff]"
"""交班行：需要人处理，或判据不足以动手（不写、不杀）。"""

STALL_CANCEL_MARKER = "[iar-stall-cancel]"
"""精确取消结果行。"""

#: 诊断输出里的三个协议字段（规范写法）。
VERDICT_FIELD_PREFIX = "STALL_VERDICT:"
SUMMARY_FIELD_PREFIX = "STALL_SUMMARY:"
EVIDENCE_FIELD_PREFIX = "STALL_EVIDENCE:"

#: 每个协议槽位可接受的**键名别名**（小写、去标点后的形态）。provider 在结论之外
#: 追加字段时，常常同时把三个协议键写成中文键名或 ``**STALL_VERDICT:**``，严格前缀
#: 匹配会把这种作答读成"没有结论"，于是一次可证实的停滞被降级成 uncertain、不会自动
#: 续跑。容错只发生在**键名形态**上：结论取值仍必须落进闭集，``stalled`` 仍必须同时
#: 带摘要与证据，任何未知键一律忽略（不据自由文本推断结论）。
_VERDICT_KEY_ALIASES = frozenset(
    ("stall_verdict", "verdict", "结论", "判定", "判定结论", "结论判定")
)
_SUMMARY_KEY_ALIASES = frozenset(
    ("stall_summary", "summary", "摘要", "停滞摘要", "总结", "理由", "原因", "说明")
)
_EVIDENCE_KEY_ALIASES = frozenset(
    ("stall_evidence", "evidence", "证据", "依据", "支撑事实", "现场事实")
)

#: 行首装饰：markdown 列表符 / 引用符 / 有序列表 / 代码围栏 / JSON 括号与引号。
_LINE_DECORATION_PATTERN = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+|>|`{3,}|\{|\}|\"|'|\s)+")
#: 键与取值之间的分隔符（含中文全角冒号），取**第一个**分隔符，避免吃掉取值里的冒号。
_KEY_VALUE_SEPARATOR_PATTERN = re.compile(r"[:=：]")
#: 取值两端残留的装饰字符（强调、引号、行尾逗号、JSON 收尾）。
_VALUE_DECORATION_CHARACTERS = "`*\"“”' \t,，;；"

#: 结论取值里可接受的停滞表述（闭集的形态容错，不是自由文本猜测）。
_STALLED_VERDICT_TOKENS = frozenset(("stalled", "停滞", "已停滞", "卡住", "确认停滞"))
_PROGRESS_VERDICT_TOKENS = frozenset(("progress", "进展", "推进中", "仍在推进"))
_BLOCKED_VERDICT_TOKENS = frozenset(("blocked", "阻塞", "受阻", "需人工", "等待人工"))
_UNCERTAIN_VERDICT_TOKENS = frozenset(("uncertain", "不确定", "证据不足"))
_ALL_VERDICT_TOKENS = (
    _STALLED_VERDICT_TOKENS
    | _PROGRESS_VERDICT_TOKENS
    | _BLOCKED_VERDICT_TOKENS
    | _UNCERTAIN_VERDICT_TOKENS
)
#: 结论取值的分词边界：空白、标点、括号与表格/选项分隔符（竖线）。
_VERDICT_VALUE_SPLIT_PATTERN = re.compile(r"[\s,，。;；:：=|/\\()（）\[\]【】{}]+")
#: 表格行的行首特征：整行由竖线分格，键与值不在同一 ``键: 值`` 形态里。
_TABLE_ROW_PREFIX = "|"

#: 现场采样的 git 调用上限（秒）：读不到现场就等于"不可证实"，绝不能卡住 observer。
_GIT_SAMPLE_TIMEOUT_SECONDS = 30
#: 诊断摘要交给 recovery 的长度上限（字符）：摘要进提示词，不能无限膨胀。
_SUMMARY_MAX_CHARS = 1200
#: 审计行里失败详情的长度上限（字符）：异常文本可能带 provider 输出，只留足以定位的部分。
_FAILURE_DETAIL_MAX_CHARS = 200
#: observer 结束后等待线程退出的宽限（秒）：诊断仍在跑时不阻塞主流程。
_OBSERVER_JOIN_GRACE_SECONDS = 5.0

__all__ = [
    "EVIDENCE_FIELD_PREFIX",
    "STALL_CANCEL_MARKER",
    "STALL_CHECK_MARKER",
    "STALL_DIAGNOSIS_MARKER",
    "STALL_HANDOFF_MARKER",
    "SUMMARY_FIELD_PREFIX",
    "VERDICT_FIELD_PREFIX",
    "StallDiagnosisRequest",
    "StallSupervisionRequest",
    "build_progress_snapshot",
    "build_supervision_attempt_key",
    "build_supervision_prompt",
    "parse_supervisor_verdict",
    "supervised_agent_invocation",
]


@dataclass(frozen=True)
class StallDiagnosisRequest:
    """交给执行侧的一次只读诊断调用声明（本模块不认识任何 provider argv）。

    Attributes:
        agent_name: 诊断使用的 agent 名（生命周期矩阵 ``supervisor`` 键的解析结果）。
        prompt: 诊断提示词（由 :func:`build_supervision_prompt` 组装，无凭据）。
        worktree_path: 诊断在哪个工作区跑（只读 profile 的 cwd）。
        timeout_seconds: 单次诊断的 wall-clock 上限。
        inactivity_timeout_seconds: 单次诊断的无输出上限。
    """

    agent_name: str
    prompt: str
    worktree_path: Path
    timeout_seconds: int
    inactivity_timeout_seconds: int


@dataclass(frozen=True)
class StallSupervisionRequest:
    """一次被监督的 run-attempt 调用所需的全部上下文（参数收敛，避免逐字段传参）。

    Attributes:
        config: ``[agent_runner.stall_supervisor]`` 的运行时视图。
        process_runner: 被监督调用使用的同一执行器——进程归属复核与精确取消都经它，
            监督器自己不碰任何 pid。
        worktree_path: 现场采样所在工作区。
        attempt_key: 本次真实进程调用的登记键（:func:`build_supervision_attempt_key`）。
        writer_agent: 正在跑的写 agent 名（进诊断提示词，也进审计行）。
        supervisor_agent: 诊断使用的 agent 名。
        issue_number: 当前 Issue 编号（审计与观测身份）。
        invocation_phase: 被监督调用所属阶段（进审计行）。
        invocation_attempt: recovery 轮次（1 起）；非 attempt 主体调用为 ``None``。
        trace_context: 主线程捕获的观测上下文；诊断调用在 observer 线程里绑定它，
            因此监督器自己的调用也落进**既有**调用账本。ContextVar 不会被新线程继承，
            所以必须由主线程传进来。
        diagnose: 诊断通道（由调用侧注入，返回 provider 的作答文本）。注入而不是
            在本模块导入 ``run_agent_with_prompt``，是为了不把 core 使用层绕成环。
    """

    config: StallSupervisorConfig
    process_runner: IProcessRunner
    worktree_path: Path
    attempt_key: str
    writer_agent: str
    supervisor_agent: str
    issue_number: int | None
    invocation_phase: str
    invocation_attempt: int | None
    trace_context: InvocationTraceContext | None
    diagnose: Callable[[StallDiagnosisRequest], str]


def build_supervision_attempt_key(
    *,
    issue_number: int,
    invocation_phase: str,
    invocation_attempt: int | None,
) -> str:
    """生成本次真实进程调用的登记键（唯一到一次调用，不含自由文本）。"""
    attempt_text = invocation_attempt if invocation_attempt is not None else "solo"
    return f"issue-{issue_number}-{invocation_phase}-{attempt_text}-{new_invocation_id()}"


# ---------------------------------------------------------------------------
# 现场采样：进展判定的唯一输入
# ---------------------------------------------------------------------------


def _digest(text: str) -> str:
    """把任意流文本压成定长指纹（进签名与审计摘要，不含原文）。"""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _run_sample_command(
    process_runner: IProcessRunner,
    worktree_path: Path,
    command: list[str],
) -> str:
    """读一次现场：非零退出即抛错（调用方据此判"不可证实"，绝不猜）。"""
    sample_result = process_runner.run(
        command,
        cwd=worktree_path,
        check=False,
        timeout=_GIT_SAMPLE_TIMEOUT_SECONDS,
    )
    if sample_result.return_code != 0:
        raise RuntimeError(f"`{' '.join(command)}` exited {sample_result.return_code}")
    return sample_result.stdout or ""


def _invocation_ledger_signal(context: InvocationTraceContext | None) -> tuple[int, str]:
    """既有调用账本里的终态数与最近事件时刻（读不到就返回零值，不编造）。

    这是"调用终态"这一路进展信号的唯一来源：只读现成的账本，不再存第二份事实。
    """
    if context is None or context.store is None:
        return (0, "")
    try:
        event_rows = context.store.list_invocation_events(run_id=context.run_id)
    except Exception as exc:  # noqa: BLE001 - 旁路观测读失败不得影响判定路径。
        _logger.debug("Stall supervisor could not read invocation events: %s", exc)
        return (0, "")
    finished = [row for row in event_rows if row.event_type.endswith("finished")]
    return (len(finished), finished[-1].occurred_at if finished else "")


def build_progress_snapshot(request: StallSupervisionRequest) -> ProgressSnapshot | None:
    """采样一次现场，返回进展签名；采不到时返回 ``None``（等于"不可证实"）。

    签名刻意**不含 stdout**：持续输出但没有交付，正是"长思考"与"卡住"最难分的
    地方，把输出活动算作进展会让监督器永远看不见停滞。可采到的进展信号是
    HEAD sha、当前分支、工作区状态指纹、未提交 diff 指纹，以及既有调用账本里的
    终态数与最近事件时刻。
    """
    try:
        head_sha = get_head_sha(request.worktree_path, request.process_runner)
        branch = get_current_branch(request.worktree_path, request.process_runner)
        status_fingerprint = _digest(
            _run_sample_command(
                request.process_runner,
                request.worktree_path,
                ["git", "status", "--porcelain", "-z"],
            )
        )
        diff_fingerprint = _digest(
            _run_sample_command(
                request.process_runner,
                request.worktree_path,
                ["git", "diff", "--stat"],
            )
        )
    except Exception as exc:  # noqa: BLE001 - 现场读不到一律交回，不做任何处置。
        _logger.warning(
            "%s attempt=%s issue=%s verdict=uncertain detail=现场读取失败：%s",
            STALL_CHECK_MARKER,
            request.attempt_key,
            request.issue_number,
            exc,
        )
        return None
    finished_count, last_event_at = _invocation_ledger_signal(request.trace_context)
    signature = "|".join(
        (
            head_sha,
            branch,
            status_fingerprint,
            diff_fingerprint,
            str(finished_count),
            last_event_at,
        )
    )
    detail_text = (
        f"head={head_sha[:8] or '-'} branch={branch or 'detached'} "
        f"status={status_fingerprint} diff={diff_fingerprint} "
        f"调用终态={finished_count} 最近事件={last_event_at or '-'}"
    )
    return ProgressSnapshot(
        signature=signature,
        observed_at_mono=time.monotonic(),
        detail=detail_text,
    )


# ---------------------------------------------------------------------------
# 诊断提示词与结论解析
# ---------------------------------------------------------------------------


def build_supervision_prompt(
    *,
    request: StallSupervisionRequest,
    snapshot: ProgressSnapshot,
    stalled_seconds: int,
) -> str:
    """组装只读诊断提示词：只给身份与现场指纹，不给提示词原文与环境信息。"""
    attempt_text = request.invocation_attempt if request.invocation_attempt is not None else "-"
    return "\n".join(
        [
            "你是 KedaCode 的停滞监督器（**只读 observer**），不参与实现、不做任何写入。",
            "一个活跃的 writer 调用已经到巡检周期，请判断它到底是「在长时间思考」还是「确实停滞」。",
            "",
            "## 现场",
            f"- 仓库/Issue：issue #{request.issue_number if request.issue_number is not None else '-'}",
            f"- 阶段与轮次：phase={request.invocation_phase} attempt={attempt_text}",
            f"- 执行器：writer={request.writer_agent} supervisor={request.supervisor_agent}",
            f"- 停滞计时：已无实质进展约 {stalled_seconds} 秒（阈值 {request.config.stalled_after_seconds} 秒）",
            f"- 现场指纹：{snapshot.detail}",
            f"- 指纹比对：上面每一项在最近约 {stalled_seconds} 秒内的多次采样里**逐字未变**"
            "（巡检到点的前提就是复采得到同一指纹），"
            "因此「指纹冻结」是已核实的事实，不需要再找证据去证实它；"
            "你要判断的是这种冻结算不算「没有交付的停滞」。",
            "",
            "## 判定口径",
            "- progress：阶段、调用终态、HEAD 或工作区指纹里能看到交付在推进（长时间思考但仍在推进也算）。",
            "- blocked：需要人类输入、凭据、或远端配合才能继续；这不是停滞。",
            "- stalled：可证实的停滞——现场指纹冻结且没有任何交付，能说明卡在哪一步。",
            "- uncertain：证据不足、现场自相矛盾，或你无法只靠这些信息下结论。",
            "",
            "## 输出要求",
            "最后三行**必须**逐字按下面的前缀写，取值写在同一行的冒号后面，"
            "不要用表格/JSON/代码块，也不要在这三行里追加别的字段：",
            "STALL_VERDICT: 只写 progress、blocked、stalled、uncertain 中的一个单词（不要照抄这一行的选项列表）",
            "STALL_SUMMARY: 一段不超过 120 字的中文摘要，会被原样交给下一轮 recovery",
            "STALL_EVIDENCE: 一条支撑上述事实；事实多时可以重复这一行",
            "",
            "不要修改任何文件，不要执行 git add/commit/push，不要终止任何进程——"
            "处置由监督器在复核所有权后决定，你只负责判断。",
        ]
    )


def _split_table_row(line: str) -> tuple[str, str] | None:
    """markdown 表格行形态（``| STALL_VERDICT | stalled |``）→ ``(键, 值)``。

    仅认**以竖线开头的整行**：结论取值本身常含竖线（``progress|blocked|stalled``
    这种选项罗列），若按竖线切任何带竖线的行会把普通 ``键: 值`` 行切成两截、把结论
    弄丢。表格分隔行（``| --- | --- |``）切出来的键不在别名里，自然被忽略。
    """
    if not line.startswith(_TABLE_ROW_PREFIX):
        return None
    cells = [
        cell.strip().strip(_VALUE_DECORATION_CHARACTERS) for cell in line.strip("|").split("|")
    ]
    non_empty_cells = [cell for cell in cells if cell]
    if len(non_empty_cells) < 2:
        return None
    return non_empty_cells[0].lower(), non_empty_cells[1]


def _split_protocol_field(raw_line: str) -> tuple[str, str] | None:
    """把一行拆成 ``(键名形态, 取值)``；不是 ``键: 值`` 形态时返回 ``None``。

    只剥装饰（列表符、引用符、强调号、引号、JSON 括号与行尾逗号），不做任何语义推断：
    ``- **STALL_VERDICT:** stalled``、``"STALL_VERDICT": "stalled",``、
    ``| STALL_VERDICT | stalled |`` 与 ``结论：stalled`` 都会归一到同一形态，
    而自由文本行（不含分隔符也不是表格行）直接落空。
    """
    line = _LINE_DECORATION_PATTERN.sub("", raw_line.strip())
    if not line:
        return None
    table_row = _split_table_row(line)
    if table_row is not None:
        return table_row
    separator_match = _KEY_VALUE_SEPARATOR_PATTERN.search(line)
    if separator_match is None:
        return None
    key_text = line[: separator_match.start()].strip(_VALUE_DECORATION_CHARACTERS).lower()
    value_text = line[separator_match.end() :].strip().strip(_VALUE_DECORATION_CHARACTERS)
    if not key_text:
        return None
    return key_text, value_text


def _protocol_slot_for(key_text: str) -> str | None:
    """键名属于哪个协议槽位；未知键（provider 追加的额外字段）返回 ``None`` 并忽略。"""
    if key_text in _VERDICT_KEY_ALIASES:
        return "verdict"
    if key_text in _SUMMARY_KEY_ALIASES:
        return "summary"
    if key_text in _EVIDENCE_KEY_ALIASES:
        return "evidence"
    return None


def _normalize_verdict_token(value_text: str) -> str:
    """把结论取值收敛成闭集词：去掉括号说明、引号与尾部标点后取首个词。"""
    cleaned = value_text.strip("([（{【\"'`*").lower()
    for token in _VERDICT_VALUE_SPLIT_PATTERN.split(cleaned):
        if token:
            return token.strip(_VALUE_DECORATION_CHARACTERS + ".!?！？")
    return ""


def _is_verdict_enumeration(value_text: str) -> bool:
    """取值是否**同时**罗列了多个不同结论（选项回声或骑墙表述）。

    提示词里写着 ``STALL_VERDICT: progress|blocked|stalled|uncertain 四选一``，
    provider 常把这段要求原样回声一遍再给出真正结论。按首个词归一会把回声读成
    ``progress``，于是一次可证实的停滞被抑制、不会自动续跑——正好是反向的失效。
    同义词并列（``stalled / 停滞``）只解析出一种结论，不算罗列；真的并列了两个
    不同取值（``stalled 或 uncertain``）则视为自相矛盾，不据此动手。
    """
    distinct_kinds: set[StallVerdictKind] = set()
    for token in _VERDICT_VALUE_SPLIT_PATTERN.split(value_text.lower()):
        if token in _ALL_VERDICT_TOKENS:
            distinct_kinds.add(_verdict_kind_from_token(token))
    return len(distinct_kinds) > 1


def _verdict_kind_from_token(value_text: str) -> StallVerdictKind:
    """结论取值 → 闭集。无法归类的取值（含拼错与含糊表述）一律 ``uncertain``。"""
    token = _normalize_verdict_token(value_text)
    if token in _STALLED_VERDICT_TOKENS:
        return StallVerdictKind.stalled
    if token in _PROGRESS_VERDICT_TOKENS:
        return StallVerdictKind.progress
    if token in _BLOCKED_VERDICT_TOKENS:
        return StallVerdictKind.blocked
    if token in _UNCERTAIN_VERDICT_TOKENS:
        return StallVerdictKind.uncertain
    return StallVerdictKind.from_text(token)


def parse_supervisor_verdict(verdict_text: str) -> SupervisorVerdict:
    """从诊断作答里解析闭集结论。

    容错的边界是**形态**而非**语义**：

    - 键名认识中英文别名、markdown 强调/列表/表格、代码围栏与 JSON 写法；
    - 取值认识换行续写（``STALL_SUMMARY:`` 空值后接正文）与同一行多字段罗列；
    - provider 在三个协议字段之外追加的额外字段（``理由``/``风险``/``建议``）直接忽略；
    - 提示词模板被原样回声的"选项罗列"行跳过，同名槽位出现多次时以**最后一条**为准
      （输出要求写明结论放在最后三行）。

    但结论取值仍必须落进闭集，``stalled`` 必须同时给出摘要与证据，否则一律降级为
    :attr:`StallVerdictKind.uncertain`——一个拼错的单词不该获得"可以杀进程"的权力，
    而从自由文本里"读出来"的结论更不该。
    """
    verdict_values: list[str] = []
    summary_lines: list[str] = []
    evidence: list[str] = []
    open_slot: str | None = None
    for raw_line in (verdict_text or "").splitlines():
        line_text = raw_line.strip()
        protocol_field = _split_protocol_field(raw_line)
        slot = _protocol_slot_for(protocol_field[0]) if protocol_field is not None else None
        if slot is None:
            # 不是协议字段（或键名未知）：只有上一个协议行的取值为空时，这一行才是它的
            # 续写正文；空行结束续写段。未知键行不参与续写，避免把自由文本读成结论。
            if not line_text or protocol_field is not None:
                open_slot = None
            else:
                continuation_text = _LINE_DECORATION_PATTERN.sub("", line_text) or line_text
                if open_slot == "verdict":
                    if not _is_verdict_enumeration(continuation_text):
                        verdict_values.append(continuation_text)
                    open_slot = None
                elif open_slot == "summary":
                    summary_lines.append(continuation_text)
                elif open_slot == "evidence":
                    evidence.append(continuation_text)
            continue
        value_text = protocol_field[1]
        open_slot = slot if not value_text else None
        if not value_text:
            continue
        if slot == "verdict":
            if not _is_verdict_enumeration(value_text):
                verdict_values.append(value_text)
        elif slot == "summary":
            summary_lines.append(value_text)
        else:
            evidence.append(value_text)
    verdict_kind = StallVerdictKind.uncertain
    if verdict_values:
        verdict_kind = _verdict_kind_from_token(verdict_values[-1])
    summary = _clip_summary(" ".join(summary_lines))
    if verdict_kind.may_cancel and (not summary or not evidence):
        return SupervisorVerdict(
            kind=StallVerdictKind.uncertain,
            summary=summary,
            evidence=tuple((*evidence, "stalled 结论缺少摘要或支撑证据，按不确定交班")),
        )
    return SupervisorVerdict(kind=verdict_kind, summary=summary, evidence=tuple(evidence))


def _clip_summary(summary: str) -> str:
    """把诊断摘要裁进 recovery 提示词的长度上限（超长说明诊断没遵守输出契约）。"""
    if len(summary) <= _SUMMARY_MAX_CHARS:
        return summary
    return summary[:_SUMMARY_MAX_CHARS].rstrip() + "…"


# ---------------------------------------------------------------------------
# observer 主体
# ---------------------------------------------------------------------------


@dataclass
class _StallObserverState:
    """observer 线程与主线程之间共享的结果（单写者：observer；主线程只读一次）。

    Attributes:
        audit_lines: 按发生顺序留存的审计行，由主线程补写进既有 Issue 日志。
        cancel_error: 精确取消成功后待抛出的异常；``None`` 表示没有处置。
    """

    audit_lines: list[str] = field(default_factory=list)
    cancel_error: AgentStallCancelledError | None = None


class _StallObserver:
    """一次被监督调用的巡检循环（守护线程，随调用结束而结束）。"""

    def __init__(self, request: StallSupervisionRequest) -> None:
        """保存请求并准备停止位与结果槽。"""
        self._request = request
        self._stop_event = threading.Event()
        self._state = _StallObserverState()
        self._thread = threading.Thread(
            target=self._run,
            name=f"iar-stall-{request.attempt_key}",
            daemon=True,
        )

    def start(self) -> None:
        """起巡检线程；起点先采一次样作为进展锚。"""
        self._thread.start()

    def stop(self) -> None:
        """请求线程退出并做有界等待：诊断还在跑时不拖住主流程。"""
        self._stop_event.set()
        self._thread.join(timeout=_OBSERVER_JOIN_GRACE_SECONDS)

    @property
    def cancel_error(self) -> AgentStallCancelledError | None:
        """本次调用是否因可证实的停滞被精确取消（决定主线程抛什么）。"""
        return self._state.cancel_error

    @property
    def audit_lines(self) -> list[str]:
        """按发生顺序留存的审计行。"""
        return list(self._state.audit_lines)

    def _audit(self, line: str) -> None:
        """记录一条审计行，同时在监督线程的控制台输出（Issue 日志由主线程补写）。"""
        self._state.audit_lines.append(line)
        _logger.info(line)

    def _wait_for_anchor(self) -> ProgressSnapshot | None:
        """采一次现场作为进展锚；瞬时读取失败时按巡检周期重试，而不是整轮放弃。

        工作区读取失败常常是一瞬间的事（``index.lock`` 被并行 git 操作占住、工作区
        正在切换）。若在这里直接结束线程，本次调用剩下的时间里监督器**一次都不跑**，
        表现就是"确实停滞了，但日志里连一条诊断都没有"——那是监督自身失效，比误判
        更糟。停止位由被监督调用的结束驱动，因此采样失败不会拖长调用。
        """
        request = self._request
        while not self._stop_event.is_set():
            anchor = build_progress_snapshot(request)
            if anchor is not None:
                return anchor
            self._stop_event.wait(request.config.check_interval_seconds)
        return None

    def _run(self) -> None:
        """巡检主循环：未到点、有进展、或已处置过就一次模型都不调用。"""
        request = self._request
        anchor = self._wait_for_anchor()
        if anchor is None:
            return
        stalled_since_mono = anchor.observed_at_mono
        diagnosed_signature: str | None = None
        while not self._stop_event.wait(request.config.check_interval_seconds):
            if self._stop_event.is_set():
                return
            snapshot = build_progress_snapshot(request)
            if snapshot is None:
                continue
            if snapshot.signature != anchor.signature:
                anchor = snapshot
                stalled_since_mono = snapshot.observed_at_mono
                diagnosed_signature = None
                continue
            stalled_seconds = int(time.monotonic() - stalled_since_mono)
            if stalled_seconds < request.config.stalled_after_seconds:
                self._audit(
                    f"{STALL_CHECK_MARKER} attempt={request.attempt_key} "
                    f"issue={request.issue_number} phase={request.invocation_phase} "
                    f"verdict={StallVerdictKind.progress.value} "
                    f"stalled_for={stalled_seconds}s detail={snapshot.detail}"
                )
                continue
            if diagnosed_signature == anchor.signature:
                # 同一个停滞窗口至多一次诊断：反复问只会烧 token，不会给出新事实。
                continue
            diagnosed_signature = anchor.signature
            verdict = self._diagnose(snapshot, stalled_seconds)
            if verdict is None:
                # 诊断调用本身失败（网络/CLI 抖动）不等于"结论是 uncertain"：留着
                # 窗口，下一个巡检周期再问一次，否则一次抖动就把剩余调用变成无监督。
                diagnosed_signature = None
                continue
            if not verdict.kind.may_cancel:
                continue
            self._try_cancel(verdict, snapshot)

    def _diagnose(
        self, snapshot: ProgressSnapshot, stalled_seconds: int
    ) -> SupervisorVerdict | None:
        """跑一次只读诊断并把结论落进审计与既有调用账本。

        返回 ``None`` 表示**这次诊断没跑成**（调用异常），与"跑成了但结论是
        uncertain"区分开：前者可以重试，后者是结论。
        """
        request = self._request
        prompt = build_supervision_prompt(
            request=request, snapshot=snapshot, stalled_seconds=stalled_seconds
        )
        try:
            # ContextVar 不被新线程继承：显式绑定主线程传来的观测上下文，
            # 监督器自己的这次调用才会作为一对终态事件落进既有账本（FR-7 审计）。
            with bound_invocation_trace_context(self._trace_context_for_diagnosis()):
                verdict_text = request.diagnose(
                    StallDiagnosisRequest(
                        agent_name=request.supervisor_agent,
                        prompt=prompt,
                        worktree_path=request.worktree_path,
                        timeout_seconds=request.config.diagnosis_timeout_seconds,
                        inactivity_timeout_seconds=(
                            request.config.diagnosis_inactivity_timeout_seconds
                        ),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - 诊断失败等于证据不足，绝不升级为处置。
            self._audit(
                f"{STALL_DIAGNOSIS_MARKER} attempt={request.attempt_key} "
                f"issue={request.issue_number} supervisor={request.supervisor_agent} "
                f"verdict={StallVerdictKind.uncertain.value} "
                "reason=诊断调用失败（重试留给下个巡检周期）：未终止任何进程 "
                f"detail={type(exc).__name__}: {str(exc)[:_FAILURE_DETAIL_MAX_CHARS]}"
            )
            return None
        verdict = parse_supervisor_verdict(verdict_text)
        self._audit(
            f"{STALL_DIAGNOSIS_MARKER} attempt={request.attempt_key} "
            f"issue={request.issue_number} supervisor={request.supervisor_agent} "
            f"verdict={verdict.kind.value} stalled_for={stalled_seconds}s "
            f"summary={verdict.summary or '-'} evidence={len(verdict.evidence)}"
        )
        if verdict.kind in (StallVerdictKind.blocked, StallVerdictKind.uncertain):
            self._audit(
                f"{STALL_HANDOFF_MARKER} attempt={request.attempt_key} "
                f"issue={request.issue_number} verdict={verdict.kind.value} "
                "detail=按结论交班：不写入、不终止进程，等待人处理或下一轮现场变化"
            )
        return verdict

    def _trace_context_for_diagnosis(self) -> InvocationTraceContext | None:
        """给诊断调用一份**独立副本**的观测上下文，避免与主线程互踩重试关联。"""
        context = self._request.trace_context
        if context is None:
            return None
        return replace(context, pending_retry_of=None, pending_retry_reason=None)

    def _try_cancel(self, verdict: SupervisorVerdict, snapshot: ProgressSnapshot) -> None:
        """stalled 结论后的现场/归属复核与精确取消（任一项不成立就只交班）。"""
        request = self._request
        if self._stop_event.is_set():
            # 被监督的调用已经结束了：此时任何取消都是对陌生进程动手。
            self._audit(
                f"{STALL_HANDOFF_MARKER} attempt={request.attempt_key} "
                "detail=被监督调用已结束：丢弃 stalled 结论，不终止任何进程"
            )
            return
        fresh_snapshot = build_progress_snapshot(request)
        if fresh_snapshot is None or fresh_snapshot.signature != snapshot.signature:
            self._audit(
                f"{STALL_HANDOFF_MARKER} attempt={request.attempt_key} "
                "detail=诊断期间现场已变化：丢弃该 verdict，不终止任何进程"
            )
            return
        ownership = request.process_runner.probe_live_attempt(request.attempt_key)
        if not ownership.confirmed:
            self._audit(
                f"{STALL_HANDOFF_MARKER} attempt={request.attempt_key} "
                f"issue={request.issue_number} verdict={verdict.kind.value} "
                f"reason=进程归属不可证实（{ownership.reason or 'unknown'}）：不终止任何进程"
            )
            return
        outcome = request.process_runner.cancel_live_attempt(request.attempt_key, ownership)
        if not (outcome.cancelled and outcome.exited):
            self._audit(
                f"{STALL_CANCEL_MARKER} attempt={request.attempt_key} "
                f"cancelled={outcome.cancelled} exited={outcome.exited} "
                f"process_group={outcome.process_group or '-'} "
                f"reason={outcome.reason or '-'}"
            )
            return
        self._audit(
            f"{STALL_CANCEL_MARKER} attempt={request.attempt_key} "
            f"issue={request.issue_number} host={ownership.host_label or '-'} "
            f"pid={ownership.process_pid} process_group={outcome.process_group} "
            f"verdict={verdict.kind.value} detail=目标进程组已退出，诊断摘要交给既有 recovery"
        )
        self._state.cancel_error = AgentStallCancelledError(
            "\n".join(
                (
                    f"{STALL_CANCEL_MARKER} attempt={request.attempt_key} "
                    f"issue={request.issue_number} phase={request.invocation_phase} "
                    f"verdict={verdict.kind.value}：writer 进程组已由监督器终止并确认退出，"
                    "本次轮次按既有 recovery 继续。",
                    f"停滞诊断：{verdict.summary}",
                )
            )
        )
        self._stop_event.set()


def supervised_agent_invocation(
    request: StallSupervisionRequest,
    invoke: Callable[[], CommandResult],
) -> CommandResult:
    """带着停滞监督执行一次真实 agent 调用（``invoke`` 就是原来的调用）。

    关闭开关时直接返回 ``invoke()``：不起线程、不采样、不调用模型，行为与本特性
    之前完全一致。开启时，只有"结论为 stalled + 现场与进程归属当场复核一致 +
    目标进程组确认退出"这一条路径会把结果换成
    :class:`~backend.core.shared.models.agent_stall.AgentStallCancelledError`，
    于是诊断摘要**原样**流经既有的 ``classify_failure`` → recovery 提示词 →
    验证 / review / 发布门禁，不新增第二条恢复路径。

    Args:
        request: 本次调用的监督上下文。
        invoke: 被监督的真实调用（无参数，闭包已带好 argv 与观测句柄）。

    Returns:
        ``invoke()`` 的结果（未被处置时原样返回）。

    Raises:
        AgentStallCancelledError: 目标 writer 进程组因可证实的停滞被精确取消。
        Exception: ``invoke()`` 自身抛出的异常原样上抛（监督器不改变失败语义）。
    """
    if not request.config.enabled:
        return invoke()
    observer = _StallObserver(request)
    observer.start()
    try:
        try:
            result = invoke()
        except Exception as exc:  # noqa: BLE001 - 只在确实取消过才改写异常种类。
            if observer.cancel_error is None:
                raise
            raise observer.cancel_error from exc
    finally:
        observer.stop()
        _replay_audit_in_calling_thread(observer.audit_lines)
    if observer.cancel_error is not None:
        raise observer.cancel_error
    return result


def _replay_audit_in_calling_thread(audit_lines: list[str]) -> None:
    """把监督线程的审计行在**调用线程**里补写一遍。

    per-Issue 日志的 handler 只接收建立路由那一根线程的记录，所以 observer 自己
    ``_logger`` 出来的行进不了该 Issue 的日志文件。这里在调用线程重放一次，
    审计就落在既有日志里，不新开第二份日志。
    """
    for line in audit_lines:
        _logger.info("%s", line)

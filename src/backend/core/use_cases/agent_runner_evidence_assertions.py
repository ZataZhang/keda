"""Realistic Validation 输出断言的唯一实现（门禁与本地自检共用）。

命令 exit 0 不等于检查点成立：``[ -d x ] || true`` 这类兜底会让命令稳定
exit 0 而从未真正验证。本模块按 manifest 的 ``stdout_assertions`` 对 keda
自己复跑得到的 stdout/stderr 做子串断言，并给出实际输出中最接近期望串的一行。

调用方：

- 交付门禁编排 :func:`backend.core.use_cases.agent_runner_evidence_check.run_evidence_check`
- 门禁入口 :func:`backend.core.use_cases.agent_runner_validation.ensure_validation_commands_pass`

两处调用同一份实现，仓库内不存在第二套断言语义。
"""

from __future__ import annotations

import difflib
import heapq
from dataclasses import dataclass

from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.agent_runner_feedback import format_result_for_recovery
from backend.core.use_cases.agent_runner_structured_evidence import (
    EvidenceBlock,
    StdoutAssertion,
    ValidationEvidenceError,
)


def _assertion_target_text(assertion: StdoutAssertion, command_result: CommandResult) -> str:
    """Return the stream the assertion must be evaluated against."""
    return command_result.stderr if assertion.source == "stderr" else command_result.stdout


def _is_stdout_assertion_satisfied(
    assertion: StdoutAssertion, command_result: CommandResult
) -> bool:
    """判断一条输出断言是否被满足（``must_match`` 时要求出现，否则要求不出现）。"""
    target_text = _assertion_target_text(assertion, command_result)
    pattern_matched = assertion.pattern in target_text
    return pattern_matched if assertion.must_match else not pattern_matched


@dataclass(frozen=True)
class StdoutAssertionFailure:
    """一条未满足的输出断言，附实际输出中最接近期望串的一行（FR-4）。

    Attributes:
        assertion_index: 断言在 item ``stdout_assertions`` 中的 1-based 序号。
        assertion: 未满足的断言原文。
        closest_output_line: 实际输出中最接近 ``pattern`` 的非空行；目标流为空时为
            ``None``。
    """

    assertion_index: int
    assertion: StdoutAssertion
    closest_output_line: str | None


class StdoutAssertionError(ValidationEvidenceError):
    """输出断言未满足，携带结构化失败明细供门禁与自检共用渲染。

    自检命令需要把"期望串 vs 最接近的实际输出行"渲染进人读表格与 ``--json``；
    把明细挂在异常上（而不是让调用方解析英文散文）保证自检与门禁读到的是同一份
    判定结果，不会各自推导出不同结论。
    """

    def __init__(
        self,
        message: str,
        assertion_failures: tuple[StdoutAssertionFailure, ...],
    ) -> None:
        """记录失败消息与结构化断言明细。

        Args:
            message: 面向 agent 的完整失败说明。
            assertion_failures: 未满足的断言明细，至少一条。
        """
        super().__init__(message)
        self.assertion_failures = assertion_failures


_MAX_CLOSEST_LINE_CHARS = 300
_CLOSEST_LINE_PREFILTER_COUNT = 20


def _closest_output_line(output_text: str, pattern: str) -> str | None:
    """返回 ``output_text`` 中最接近 ``pattern`` 的非空行（相似度用 difflib）。

    先用 ``quick_ratio`` 线性预筛出少量候选，再对候选计算完整 ratio：单条 RV 命令
    的 stdout 可能有成百上千行，逐行做 O(n*m) 的完整比较在长输出上代价过高。
    """
    candidate_lines = [line.strip() for line in output_text.splitlines() if line.strip()]
    if not candidate_lines:
        return None
    prefilter_matcher = difflib.SequenceMatcher(None, pattern, "")

    def _quick_similarity(candidate_line: str) -> float:
        prefilter_matcher.set_seq2(candidate_line)
        return prefilter_matcher.quick_ratio()

    shortlisted_lines = heapq.nlargest(
        _CLOSEST_LINE_PREFILTER_COUNT, candidate_lines, key=_quick_similarity
    )
    closest_line = max(
        shortlisted_lines,
        key=lambda candidate_line: difflib.SequenceMatcher(None, pattern, candidate_line).ratio(),
    )
    if len(closest_line) <= _MAX_CLOSEST_LINE_CHARS:
        return closest_line
    return closest_line[: _MAX_CLOSEST_LINE_CHARS - 1] + "…"


def _find_stdout_assertion_failures(
    block: EvidenceBlock, command_result: CommandResult
) -> tuple[StdoutAssertionFailure, ...]:
    """收集 item 中所有未满足的输出断言（不短路，便于一次修完）。

    判定本身仍只调用同模块的唯一子串判定函数；本函数只负责遍历与采集诊断
    信息，不引入第二套匹配语义。
    """
    assertion_failures: list[StdoutAssertionFailure] = []
    for assertion_index, assertion in enumerate(block.stdout_assertions, start=1):
        if _is_stdout_assertion_satisfied(assertion, command_result):
            continue
        target_text = _assertion_target_text(assertion, command_result)
        assertion_failures.append(
            StdoutAssertionFailure(
                assertion_index=assertion_index,
                assertion=assertion,
                closest_output_line=_closest_output_line(target_text, assertion.pattern),
            )
        )
    return tuple(assertion_failures)


def _validate_stdout_assertions(block: EvidenceBlock, command_result: CommandResult) -> None:
    """对已 exit 0 的 RV 命令执行 manifest 声明的输出断言（补丁 1）。

    退出码只是"命令没崩"，不代表检查点成立：``[ -d x ] || true`` 这类兜底会让
    命令稳定 exit 0 而从未真正验证。这里按 manifest 的 ``stdout_assertions`` 对
    keda 自己复跑得到的 stdout/stderr 做子串断言，失败即抛
    ``StdoutAssertionError``（``ValidationEvidenceError`` 子类）进入既有 recovery
    循环——与 exit code 失败走同一条路。

    同一条命令的多条断言一次全部报出，并给出实际输出中最接近期望串的一行，
    避免 agent 每轮只看到第一条、只改一个字（Issue #334 的 `example-derived`
    事故形态）。

    Raises:
        StdoutAssertionError: 任一断言未被满足。错误消息指明断言序号、
            pattern、source 与最接近的实际输出行，并附带限长后的命令退出码、
            stdout 与 stderr。
    """
    assertion_failures = _find_stdout_assertion_failures(block, command_result)
    if not assertion_failures:
        return
    failure_lines: list[str] = []
    for assertion_failure in assertion_failures:
        assertion = assertion_failure.assertion
        expectation_text = (
            f"must contain {assertion.pattern!r}"
            if assertion.must_match
            else f"must not contain {assertion.pattern!r}"
        )
        failure_lines.append(
            f"- assertion #{assertion_failure.assertion_index}: {expectation_text} in "
            f"{assertion.source} (severity={assertion.severity})"
        )
        if assertion_failure.closest_output_line is None:
            failure_lines.append(f"  no output lines were captured on {assertion.source}")
        else:
            failure_lines.append(
                f"  closest {assertion.source} line: {assertion_failure.closest_output_line!r}"
            )
    failure_details_text = "\n".join(failure_lines)
    raise StdoutAssertionError(
        f"Realistic Validation item {block.item_number} exited 0 but its stdout "
        f"assertion(s) failed (command `{block.command}`). An exit code of 0 is not "
        "proof the checkpoint holds — remove `|| true` / `|| echo ok` style fallbacks "
        "and make the command actually assert, or correct the declared assertion.\n\n"
        f"{failure_details_text}\n\n"
        "Captured command result:\n"
        f"{format_result_for_recovery(command_result)}",
        assertion_failures,
    )


__all__ = ["StdoutAssertionError", "StdoutAssertionFailure"]

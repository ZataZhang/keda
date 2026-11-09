"""外部检查失败的结构化阻塞协议与无进展判定。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from backend.core.shared.models.agent_runner import CommandResult

CHECK_RESULT_PREFIX = "KEDACODE_CHECK_RESULT="
"""外部检查脚本声明人工阻塞结果时使用的单行前缀。"""

_DIRECTIVE_SUMMARY_MAX_LENGTH = 1200
_DIRECTIVE_ACTION_MAX_LENGTH = 800


@dataclass(frozen=True)
class CheckActionDirective:
    """检查脚本或 Runner 要求人工介入时的可读说明。

    Attributes:
        block_type: 阻塞来源，例如 ``human_action_required`` 或 ``recovery_stalled``。
        summary: 阻塞原因摘要。
        next_action: 操作者可以执行的下一步。
    """

    block_type: str
    summary: str
    next_action: str


@dataclass(frozen=True)
class CheckFailureSnapshot:
    """一次检查失败的稳定摘要与工作树快照。

    Attributes:
        failure_fingerprint: 命令、退出码和诊断输出的摘要。
        worktree_fingerprint: 已跟踪与未跟踪工作树内容的摘要；读取失败时为空。
    """

    failure_fingerprint: str
    worktree_fingerprint: str | None


@dataclass(frozen=True)
class CheckFailureAssessment:
    """一次检查失败的判定结果。"""

    snapshot: CheckFailureSnapshot
    directive: CheckActionDirective | None


CheckFailureInput = subprocess.CalledProcessError | Sequence[CommandResult]


def assess_check_failure(
    failure: CheckFailureInput,
    *,
    worktree_fingerprint: str | None,
    previous_snapshots: Collection[CheckFailureSnapshot],
) -> CheckFailureAssessment:
    """识别脚本声明的人工阻塞，或相同失败且工作树无进展的恢复循环。

    Args:
        failure: 外部命令异常，或验证命令返回的结果列表。
        worktree_fingerprint: 当前工作树内容摘要；读取失败时传入 ``None``。
        previous_snapshots: 本次执行中先前遇到的检查失败快照。

    Returns:
        CheckFailureAssessment: 当前快照及可选的人工作用指令。
    """
    failure_parts = _failure_parts(failure)
    snapshot = CheckFailureSnapshot(
        failure_fingerprint=_fingerprint_failure_parts(failure_parts),
        worktree_fingerprint=worktree_fingerprint,
    )
    directive = _parse_human_action_directive(failure_parts)
    if directive is not None:
        return CheckFailureAssessment(snapshot=snapshot, directive=directive)

    if worktree_fingerprint is not None and snapshot in previous_snapshots:
        return CheckFailureAssessment(
            snapshot=snapshot,
            directive=_build_recovery_stalled_directive(failure_parts),
        )
    return CheckFailureAssessment(snapshot=snapshot, directive=None)


def _failure_parts(
    failure: CheckFailureInput,
) -> list[tuple[tuple[str, ...], int, str, str]]:
    """把两类子进程失败转成相同的指纹与输出输入形态。"""
    if isinstance(failure, subprocess.CalledProcessError):
        command_value = failure.cmd
        if isinstance(command_value, bytes):
            command_parts = (command_value.decode("utf-8", errors="replace"),)
        elif isinstance(command_value, str):
            command_parts = (command_value,)
        elif isinstance(command_value, Sequence):
            command_parts = tuple(str(command_part) for command_part in command_value)
        else:
            command_parts = (str(command_value),) if command_value is not None else ()
        return [
            (
                command_parts,
                int(failure.returncode),
                _as_text(failure.stdout or failure.output),
                _as_text(failure.stderr),
            )
        ]

    return [
        (
            tuple(str(command_part) for command_part in command_result.command),
            command_result.return_code,
            command_result.stdout,
            command_result.stderr,
        )
        for command_result in failure
        if command_result.return_code != 0
    ]


def _as_text(output_value: object) -> str:
    """把子进程输出转换成稳定的文本输入。"""
    if isinstance(output_value, bytes):
        return output_value.decode("utf-8", errors="replace")
    return output_value if isinstance(output_value, str) else ""


def _fingerprint_failure_parts(
    failure_parts: list[tuple[tuple[str, ...], int, str, str]],
) -> str:
    """对命令、退出码和输出计算稳定摘要，不把原始命令写入观测日志。"""
    canonical_parts = [
        {
            "command": command_parts,
            "return_code": return_code,
            "stdout": stdout_text.replace("\r\n", "\n").strip(),
            "stderr": stderr_text.replace("\r\n", "\n").strip(),
        }
        for command_parts, return_code, stdout_text, stderr_text in failure_parts
    ]
    canonical_json = json.dumps(
        canonical_parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _parse_human_action_directive(
    failure_parts: list[tuple[tuple[str, ...], int, str, str]],
) -> CheckActionDirective | None:
    """解析退出输出中最后一条有效的人工阻塞标记。"""
    for _, _, stdout_text, stderr_text in reversed(failure_parts):
        output_lines = f"{stdout_text}\n{stderr_text}".splitlines()
        for output_line in reversed(output_lines):
            if not output_line.startswith(CHECK_RESULT_PREFIX):
                continue
            try:
                payload = json.loads(output_line[len(CHECK_RESULT_PREFIX) :])
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            version_value = payload.get("version")
            if (
                isinstance(version_value, bool)
                or not isinstance(version_value, int)
                or version_value != 1
            ):
                continue
            if payload.get("disposition") != "human_action_required":
                continue
            summary_text = _bounded_text(payload.get("summary"), _DIRECTIVE_SUMMARY_MAX_LENGTH)
            if not summary_text:
                continue
            action_text = _bounded_text(payload.get("next_action"), _DIRECTIVE_ACTION_MAX_LENGTH)
            return CheckActionDirective(
                block_type="human_action_required",
                summary=summary_text,
                next_action=action_text or "处理该检查要求的人工作用后再继续任务。",
            )
    return None


def _build_recovery_stalled_directive(
    failure_parts: list[tuple[tuple[str, ...], int, str, str]],
) -> CheckActionDirective:
    """为重复且工作树无进展的检查失败准备人工交接说明。"""
    _, return_code, _, _ = failure_parts[-1] if failure_parts else ((), 1, "", "")
    return CheckActionDirective(
        block_type="recovery_stalled",
        summary=(
            f"An external check exited with status {return_code}; the same failure "
            "recurred without worktree changes. Inspect the earlier attempt details for output."
        ),
        next_action=(
            "Inspect the failed command and resolve or update the check before continuing; "
            "then run `kc blocked-continue --issue <number>`."
        ),
    )


def _bounded_text(value: object, limit: int) -> str:
    """把协议字段压成单行并限制长度。"""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]

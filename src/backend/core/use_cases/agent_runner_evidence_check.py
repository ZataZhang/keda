"""本地证据自检编排：逐项复跑、收集结果、聚合渲染（FR-1～FR-5）。

判定逻辑只有一份，全部复用交付门禁的既有实现：

- manifest 解析与字段校验：``load_evidence_manifest``
- 命令复跑形态：``bash -lc`` + worktree cwd + 门禁超时语义
- 输出断言：交付门禁使用的同一子串判定函数（见 ``agent_runner_evidence_assertions``）

本模块只负责"逐项跑、收集结果、不抛异常"，把结果交给两个消费方：

- ``kc evidence check``（本地自检）：渲染人读表格或 ``--json``，按结果设置退出码；
- :func:`backend.core.use_cases.agent_runner_validation.ensure_validation_commands_pass`
  （交付门禁）：把全部失败聚合成一个 ``ValidationEvidenceError``。

这样自检与门禁不存在第二套判据，也不可能出现"本地绿、门禁红"的断言结论分歧。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    DeliveryGateFailureKind,
)
from backend.core.use_cases.agent_runner_feedback import format_result_for_recovery
from backend.core.use_cases.agent_runner_structured_evidence import (
    EvidenceBlock,
    ValidationEvidenceError,
    load_evidence_manifest,
)
from backend.core.use_cases import agent_runner_evidence_assertions

STATUS_PASSED = "passed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"

FAILURE_KIND_NONZERO_EXIT = "nonzero_exit"
FAILURE_KIND_TIMEOUT = "timeout"
FAILURE_KIND_ASSERTION = "assertion"


class EvidenceDirectoryResolutionError(ValueError):
    """缺省证据目录解析出现歧义（多个候选）时抛出。

    自检不猜目标目录：找到多个含 ``evidence.json`` 的子目录时列出候选并要求
    调用方用 ``--evidence`` 明确指定。CLI 把本错误映射为用法错误（退出码 2）。
    """

    def __init__(self, search_root: Path, candidates: tuple[Path, ...]) -> None:
        """记录搜索根与全部候选目录。

        Args:
            search_root: 配置的证据目录根（如 ``tasks/evidence``）。
            candidates: 含 ``evidence.json`` 的全部候选子目录。
        """
        candidate_text = ", ".join(str(candidate) for candidate in candidates)
        super().__init__(
            f"More than one evidence directory under `{search_root}` contains "
            f"`evidence.json`: {candidate_text}. Pass --evidence <dir> to choose one."
        )
        self.search_root = search_root
        self.candidates = candidates


def resolve_check_evidence_dir(
    worktree_path: Path,
    config: AppConfig,
    *,
    explicit_evidence: str | None = None,
) -> Path:
    """解析自检要读取的证据目录（显式参数 → 唯一候选 → 报错列候选）。

    Args:
        worktree_path: worktree 根目录。
        config: 运行配置，提供证据目录根。
        explicit_evidence: ``--evidence`` 的原始取值；相对路径按 worktree 解析。

    Returns:
        证据目录的绝对路径。显式参数不校验存在性——缺失由 manifest 加载给出
        可读错误；缺省模式下也允许返回配置根（其下没有 manifest 时同样报缺失）。

    Raises:
        EvidenceDirectoryResolutionError: 缺省模式下找到多个含 ``evidence.json``
            的子目录。
    """
    if explicit_evidence:
        explicit_path = Path(explicit_evidence).expanduser()
        if not explicit_path.is_absolute():
            explicit_path = worktree_path / explicit_path
        return explicit_path

    configured_root = worktree_path / config.validation.evidence_dir
    if (configured_root / "evidence.json").is_file():
        return configured_root
    if not configured_root.is_dir():
        return configured_root
    candidate_directories = tuple(
        sorted(
            candidate_path
            for candidate_path in configured_root.iterdir()
            if candidate_path.is_dir() and (candidate_path / "evidence.json").is_file()
        )
    )
    if len(candidate_directories) == 1:
        return candidate_directories[0]
    if len(candidate_directories) > 1:
        raise EvidenceDirectoryResolutionError(configured_root, candidate_directories)
    return configured_root


@dataclass(frozen=True)
class EvidenceCheckRequest:
    """一次自检/门禁复跑的输入。

    Attributes:
        evidence_dir: 已解析的证据目录。
        timeout_seconds: 本次单条命令的实际超时（自检可用 ``--timeout`` 调小）。
        gate_timeout_seconds: 交付门禁的配置上限（``reexecute_timeout_seconds``）。
        skip_block: 门禁缓存命中时返回 ``True`` 以跳过该条；自检不传。
        record_pass: 某条通过后的回调（门禁用于收集可写缓存的条目）；自检不传。
    """

    evidence_dir: Path
    timeout_seconds: int
    gate_timeout_seconds: int
    skip_block: Callable[[EvidenceBlock], bool] | None = None
    record_pass: Callable[[EvidenceBlock], None] | None = None


@dataclass(frozen=True)
class EvidenceManifestFailure:
    """manifest 无法解析时的失败条目（不执行任何证据命令）。

    Attributes:
        message: 原始失败说明。
        failure_kind: 门禁分类；``None`` 表示按真失败处理。
    """

    message: str
    failure_kind: DeliveryGateFailureKind | None = None


@dataclass(frozen=True)
class EvidenceCheckItemResult:
    """单个 checklist item 自检结果。

    Attributes:
        block: manifest 中该 item 的原始证据块。
        status: ``passed`` / ``skipped``（门禁缓存命中）/ ``failed`` / ``timeout``。
        failure_kind: ``nonzero_exit`` / ``timeout`` / ``assertion``；通过时为 ``None``。
        exit_code: 命令退出码；超时或未执行为 ``None``。
        duration_seconds: 命令墙钟耗时；跳过或未执行时为 ``0.0``。
        assertion_failures: 未满足的输出断言明细。
        detail: 面向 agent 的完整失败诊断（通过时为空）。
    """

    block: EvidenceBlock
    status: str
    failure_kind: str | None = None
    exit_code: int | None = None
    duration_seconds: float = 0.0
    assertion_failures: tuple[agent_runner_evidence_assertions.StdoutAssertionFailure, ...] = ()
    detail: str = ""

    @property
    def passed(self) -> bool:
        """该项是否算通过（缓存命中亦算通过）。"""
        return self.status in (STATUS_PASSED, STATUS_SKIPPED)


@dataclass(frozen=True)
class EvidenceCheckReport:
    """全部 item 的聚合结果；自检与门禁共用的唯一结果结构。

    Attributes:
        worktree_path: 复跑命令的 worktree。
        request: 本次复跑输入（含实际超时与门禁上限）。
        evidence_dir: 已解析的证据目录。
        manifest_failures: manifest 解析失败；非空时 ``item_results`` 必为空。
        item_results: 逐项结果（保持 manifest 顺序）。
        language: manifest 声明的语言；解析失败时为空串。
    """

    worktree_path: Path
    request: EvidenceCheckRequest
    evidence_dir: Path
    manifest_failures: tuple[EvidenceManifestFailure, ...]
    item_results: tuple[EvidenceCheckItemResult, ...]
    language: str = ""

    @property
    def passed(self) -> bool:
        """整体是否通过：manifest 合法且所有 item 通过。"""
        return not self.manifest_failures and all(
            item_result.passed for item_result in self.item_results
        )

    @property
    def failed_item_results(self) -> tuple[EvidenceCheckItemResult, ...]:
        """全部未通过的 item（保持 manifest 顺序）。"""
        return tuple(item_result for item_result in self.item_results if not item_result.passed)

    @property
    def passed_count(self) -> int:
        """通过（含缓存命中）的 item 数。"""
        return sum(1 for item_result in self.item_results if item_result.passed)

    @property
    def total_count(self) -> int:
        """manifest 中的 item 总数。"""
        return len(self.item_results)

    @property
    def local_timeout(self) -> bool:
        """本次实际超时是否严格小于门禁上限（即用了 ``--timeout`` 调小）。"""
        return self.request.timeout_seconds < self.request.gate_timeout_seconds

    @property
    def gate_failure_kind(self) -> DeliveryGateFailureKind:
        """门禁失败分类：manifest 格式错误沿用其分类，其余按真失败处理。"""
        for manifest_failure in self.manifest_failures:
            if manifest_failure.failure_kind is not None:
                return manifest_failure.failure_kind
        return DeliveryGateFailureKind.SUBSTANTIVE

    @property
    def evidence_dir_display(self) -> str:
        """证据目录的展示文本：worktree 相对路径优先。"""
        try:
            return self.evidence_dir.relative_to(self.worktree_path).as_posix()
        except ValueError:
            return self.evidence_dir.as_posix()

    def summary_text(self) -> str:
        """一行汇总：通过数/总数 + 各失败状态计数。"""
        if self.manifest_failures:
            return "manifest error — no evidence command was executed"
        failed_count = sum(
            1 for item_result in self.item_results if item_result.status == STATUS_FAILED
        )
        timeout_count = sum(
            1 for item_result in self.item_results if item_result.status == STATUS_TIMEOUT
        )
        skipped_count = sum(
            1 for item_result in self.item_results if item_result.status == STATUS_SKIPPED
        )
        segments = [f"{self.passed_count}/{self.total_count} passed"]
        if failed_count:
            segments.append(f"{failed_count} failed")
        if timeout_count:
            segments.append(f"{timeout_count} timed out")
        if skipped_count:
            segments.append(f"{skipped_count} skipped (gate cache)")
        return ", ".join(segments)

    def render_human(self) -> str:
        """渲染人读输出：逐项结论 + 失败诊断 + 汇总行。"""
        lines = [
            f"Evidence directory: {self.evidence_dir_display}",
            f"Timeout: {self.request.timeout_seconds}s "
            f"(delivery gate limit: {self.request.gate_timeout_seconds}s)",
        ]
        if self.local_timeout:
            lines.append(
                "Local timeout is shorter than the gate limit; a local timeout is "
                "not a gate verdict. Re-run without --timeout before treating it as "
                "a failure."
            )
        if self.manifest_failures:
            lines.append("")
            lines.append("Manifest errors (no evidence command was executed):")
            for manifest_failure in self.manifest_failures:
                lines.append(f"- {manifest_failure.message}")
        else:
            for item_result in self.item_results:
                lines.append("")
                lines.append(
                    f"{item_result.status.upper():<8} item {item_result.block.item_number}"
                    f" ({item_result.duration_seconds:.1f}s): {item_result.block.item_name}"
                )
                if item_result.detail:
                    for detail_line in item_result.detail.splitlines():
                        lines.append(f"  {detail_line}")
        lines.append("")
        lines.append(f"Result: {self.summary_text()}")
        return "\n".join(lines)

    def render_gate_failure(self) -> str:
        """渲染交付门禁的聚合失败消息（一次列出全部未通过项）。"""
        if self.manifest_failures:
            failure_lines = [
                "Structured evidence manifest is invalid; no evidence command was executed.",
                *(f"- {manifest_failure.message}" for manifest_failure in self.manifest_failures),
            ]
            return "\n".join(failure_lines)
        failed_results = self.failed_item_results
        header = (
            f"Realistic Validation re-execution failed for {len(failed_results)} item(s) "
            "after keda re-ran their commands. Every failure is listed below so they "
            "can all be fixed in one pass."
        )
        sections = [
            f"=== Item {item_result.block.item_number} ({item_result.status}) ===\n"
            f"{item_result.detail}"
            for item_result in failed_results
        ]
        return "\n\n".join([header, *sections])

    def to_payload(self) -> dict[str, object]:
        """构造稳定的机器可读结果（``kc evidence check --json``）。"""
        failed_count = sum(
            1 for item_result in self.item_results if item_result.status == STATUS_FAILED
        )
        timeout_count = sum(
            1 for item_result in self.item_results if item_result.status == STATUS_TIMEOUT
        )
        skipped_count = sum(
            1 for item_result in self.item_results if item_result.status == STATUS_SKIPPED
        )
        return {
            "version": 1,
            "worktree": str(self.worktree_path),
            "evidence_dir": self.evidence_dir_display,
            "timeout_seconds": self.request.timeout_seconds,
            "gate_timeout_seconds": self.request.gate_timeout_seconds,
            "local_timeout": self.local_timeout,
            "passed": self.passed,
            "summary": {
                "total": self.total_count,
                "passed": self.passed_count,
                "failed": failed_count,
                "timed_out": timeout_count,
                "skipped": skipped_count,
            },
            "manifest_errors": [
                {
                    "message": manifest_failure.message,
                    "failure_kind": (
                        manifest_failure.failure_kind.value
                        if manifest_failure.failure_kind is not None
                        else DeliveryGateFailureKind.SUBSTANTIVE.value
                    ),
                }
                for manifest_failure in self.manifest_failures
            ],
            "items": [
                {
                    "item_number": item_result.block.item_number,
                    "item_name": item_result.block.item_name,
                    "command": item_result.block.command,
                    "status": item_result.status,
                    "failure_kind": item_result.failure_kind,
                    "exit_code": item_result.exit_code,
                    "duration_seconds": round(item_result.duration_seconds, 3),
                    "assertion_failures": [
                        {
                            "assertion_index": assertion_failure.assertion_index,
                            "pattern": assertion_failure.assertion.pattern,
                            "source": assertion_failure.assertion.source,
                            "must_match": assertion_failure.assertion.must_match,
                            "severity": assertion_failure.assertion.severity,
                            "closest_output_line": assertion_failure.closest_output_line,
                        }
                        for assertion_failure in item_result.assertion_failures
                    ],
                    "detail": item_result.detail,
                }
                for item_result in self.item_results
            ],
        }


def _format_nonzero_exit_detail(block: EvidenceBlock, command_result: CommandResult) -> str:
    """构造非零退出的失败诊断（保留门禁历史文案以便 recovery 提示不变）。"""
    return (
        f"Realistic Validation item {block.item_number} failed when keda "
        "re-ran its command.\n\n"
        f"{format_result_for_recovery(command_result)}\n\n"
        "keda re-executes RV commands to confirm they actually pass — the "
        "agent's evidence file alone is not trusted. Fix the behavior so the "
        "command passes (or correct the command)."
    )


def _format_timeout_detail(block: EvidenceBlock, request: EvidenceCheckRequest) -> str:
    """构造超时诊断；本地调小时明确标注门禁上限（决策一验收）。"""
    local_timeout_note = ""
    if request.timeout_seconds < request.gate_timeout_seconds:
        local_timeout_note = (
            f" This run used the local --timeout {request.timeout_seconds}s; the "
            f"delivery gate limit is {request.gate_timeout_seconds}s, so this local "
            "timeout is not a gate verdict by itself."
        )
    return (
        f"Realistic Validation item {block.item_number} timed out when keda "
        f"re-ran its command (>{request.timeout_seconds}s): `{block.command}`. "
        "The reproducible command must be a self-terminating check that probes "
        "the real entry point and exits, not a long-running server."
        f"{local_timeout_note}"
    )


def _run_single_block(
    worktree_path: Path,
    process_runner: IProcessRunner,
    request: EvidenceCheckRequest,
    block: EvidenceBlock,
) -> EvidenceCheckItemResult:
    """复跑单个 item 的命令并按门禁同一判据分类结果（不抛异常）。"""
    try:
        command_result = process_runner.run(
            ["bash", "-lc", block.command],
            cwd=worktree_path,
            check=False,
            capture_output=True,
            timeout=request.timeout_seconds,
            label=f"rv-reexec-{block.item_number}",
        )
    except subprocess.TimeoutExpired:
        return EvidenceCheckItemResult(
            block=block,
            status=STATUS_TIMEOUT,
            failure_kind=FAILURE_KIND_TIMEOUT,
            detail=_format_timeout_detail(block, request),
        )
    if command_result.return_code != 0:
        return EvidenceCheckItemResult(
            block=block,
            status=STATUS_FAILED,
            failure_kind=FAILURE_KIND_NONZERO_EXIT,
            exit_code=command_result.return_code,
            duration_seconds=command_result.duration_seconds,
            detail=_format_nonzero_exit_detail(block, command_result),
        )
    try:
        agent_runner_evidence_assertions._validate_stdout_assertions(block, command_result)
    except agent_runner_evidence_assertions.StdoutAssertionError as assertion_error:
        return EvidenceCheckItemResult(
            block=block,
            status=STATUS_FAILED,
            failure_kind=FAILURE_KIND_ASSERTION,
            exit_code=command_result.return_code,
            duration_seconds=command_result.duration_seconds,
            assertion_failures=assertion_error.assertion_failures,
            detail=str(assertion_error),
        )
    return EvidenceCheckItemResult(
        block=block,
        status=STATUS_PASSED,
        exit_code=command_result.return_code,
        duration_seconds=command_result.duration_seconds,
    )


def run_evidence_check(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    request: EvidenceCheckRequest,
) -> EvidenceCheckReport:
    """复跑 evidence manifest 的全部命令并聚合逐项结论。

    manifest 解析失败时立即返回格式错误报告，**不执行任何证据命令**——否则
    一个字段笔误会带来真实副作用（探针写文件、启动服务），却仍被判失败。

    Args:
        worktree_path: worktree 根目录（命令 cwd）。
        config: 运行配置；manifest 解析复用其证据目录约定。
        process_runner: 命令执行端口（生产为真实子进程）。
        request: 本次复跑输入（证据目录、超时、门禁缓存钩子）。

    Returns:
        聚合结果；是否通过、退出码与渲染全部由调用方基于该结构决定。
    """
    try:
        manifest = load_evidence_manifest(
            worktree_path,
            config,
            evidence_dir=request.evidence_dir,
        )
    except ValidationEvidenceError as manifest_error:
        return EvidenceCheckReport(
            worktree_path=worktree_path,
            request=request,
            evidence_dir=request.evidence_dir,
            manifest_failures=(
                EvidenceManifestFailure(
                    message=str(manifest_error),
                    failure_kind=manifest_error.kind,
                ),
            ),
            item_results=(),
        )

    item_results: list[EvidenceCheckItemResult] = []
    for block in manifest.items:
        if request.skip_block is not None and request.skip_block(block):
            item_results.append(EvidenceCheckItemResult(block=block, status=STATUS_SKIPPED))
            continue
        item_result = _run_single_block(worktree_path, process_runner, request, block)
        if item_result.passed and request.record_pass is not None:
            request.record_pass(block)
        item_results.append(item_result)
    return EvidenceCheckReport(
        worktree_path=worktree_path,
        request=request,
        evidence_dir=request.evidence_dir,
        manifest_failures=(),
        item_results=tuple(item_results),
        language=manifest.language,
    )


__all__ = [
    "EvidenceCheckItemResult",
    "EvidenceCheckReport",
    "EvidenceCheckRequest",
    "EvidenceDirectoryResolutionError",
    "EvidenceManifestFailure",
    "resolve_check_evidence_dir",
    "run_evidence_check",
]

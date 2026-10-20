"""Worktree 项目依赖准备的结构化上下文（schema 唯一事实源）。

Issue #290 把「项目依赖准备」从**致命前置步骤**改成**可恢复的准备 + 诊断**：
Keda 只在自己权限范围内做安全可行的准备（复用已装依赖、仓库级安装），把缺失的
工具、选择依据、尝试过的命令与失败原因写成结构化记录，交给 Agent 自主决策；
需要提权、改主机全局环境或凭据的动作不代做，而是以可恢复状态呈现给人。

本模块拥有三件事：

1. 记录的数据形状（:class:`FrontendProjectRecord` /
   :class:`DependencyPreparationReport`）与落盘 JSON schema。
2. 落盘/读取：报告写在 worktree 的 ``.agent-runner/``（该目录由
   :mod:`backend.engines.agent_runner.repository_gitignore` 保证被 git 忽略），
   因此诊断永远不会进入代码 diff。
3. 给 Agent 的 prompt 段落渲染，以及「这次失败到底是不是环境造成的」归属判断
   （:func:`escalate_dependency_environment_failure`）。

写入失败绝不阻断交付：所有文件 I/O 都是 best effort，异常只记日志。
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path

_logger = logging.getLogger(__name__)

#: 落盘 JSON 的 schema 版本；字段语义变化时递增，读取端只认当前版本。
RECORD_SCHEMA_VERSION = 1

#: 报告在 worktree 内的相对路径（``.agent-runner/`` 已被 git 忽略）。
DEPENDENCY_PREPARATION_RELATIVE_PATH = Path(".agent-runner") / "dependency-preparation.json"

#: 单条 ``detail`` 保留的最大字符数：安装器 stderr 可能很长，且只作为诊断片段呈现。
MAX_DETAIL_CHARS = 500

# 处置结论（outcome）：Agent 与人都要能一眼看出这个项目到底有没有可用依赖。
OUTCOME_INSTALLED = "installed"
OUTCOME_REUSED = "reused"
OUTCOME_PRESENT = "present"
OUTCOME_SKIPPED = "skipped"
OUTCOME_NOT_PREPARED = "not_prepared"

# 原因码（reason）：机器可读，避免把自由文本当分类依据。
REASON_TOOL_MISSING = "tool_missing"
REASON_LAUNCH_FAILED = "launch_failed"
REASON_INSTALL_FAILED = "install_failed"
REASON_INSTALL_NO_OUTPUT = "install_produced_no_dependencies"
REASON_LINK_FAILED = "reuse_link_failed"
REASON_MANAGER_CONFLICT = "manager_conflict"
REASON_NO_LOCKFILE = "no_lockfile"
REASON_WORKSPACE_COVERED = "workspace_covered"
REASON_REUSABLE_ABSENT = "reusable_deps_absent"
REASON_REUSABLE_STALE = "reusable_deps_stale"
REASON_PREPARATION_CRASHED = "preparation_crashed"
REASON_NO_ACTION_NEEDED = "no_action_needed"

# 需要人来决定的类别：Keda 与 Agent 都不代做这些动作。
USER_ACTION_HOST_TOOLCHAIN = "host_toolchain"
USER_ACTION_PRIVILEGE = "privilege"
USER_ACTION_CREDENTIALS = "credentials"

#: 处置结论 → 该项目是否已经拥有可用依赖。
_USABLE_OUTCOMES = frozenset({OUTCOME_INSTALLED, OUTCOME_REUSED, OUTCOME_PRESENT})

#: ``detail`` 里可能混入的凭据形状：落盘与发 Issue 前一律脱敏。
#: 每个模式的捕获组必须是**不含密钥的前缀**，替换成 ``前缀 + [redacted]`` 后才不会把
#: 原文里的口令重新抄回去。
_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(_auth(?:Token|TokenEnv|Password|Login)\s*=\s*)\S+", re.IGNORECASE),
    re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^\s/@:]+:[^\s@]+@", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE),
    re.compile(r"(Authorization:\s*)\S+", re.IGNORECASE),
    re.compile(r"(npm_config__?auth\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"\b((?:api[_-]?key|password|passwd|secret|token)\s*[=:]\s*)\S+", re.IGNORECASE),
)

#: shell 里「命令不存在」的退出码（127），用于把失败归属到缺失的可执行文件。
_COMMAND_NOT_FOUND_EXIT_CODE = 127


@dataclass(frozen=True)
class FrontendProjectRecord:
    """一个前端项目在依赖准备阶段的完整诊断记录。

    Attributes:
        project_path: worktree 相对路径（POSIX 字符串），如 ``frontend-admin``。
        outcome: 处置结论，取值见 ``OUTCOME_*`` 常量。
        reason: 原因码，取值见 ``REASON_*`` 常量。
        package_manager: 选定（或候选）的包管理器名；无法判定时为 ``None``。
        selection_evidence: 选择依据证据行，逐条列出 ``packageManager`` 字段、
            workspace 根配置与锁文件等来源，冲突时全部保留交给 Agent。
        attempted_commands: 计划执行或已尝试过的命令（argv 的 shell 可读形式）；
            若可执行文件缺失，仍记录计划命令，``detail`` 说明它没有真正运行。
        exit_code: 安装命令退出码；未运行为 ``None``。
        requires_user_action: 需要人类决定的类别（``USER_ACTION_*``）；
            Agent 可自行处理时为 ``None``。
        detail: 已脱敏、已截断的诊断片段（失败原因、stderr 摘要等）。
    """

    project_path: str
    outcome: str
    reason: str
    package_manager: str | None = None
    selection_evidence: tuple[str, ...] = ()
    attempted_commands: tuple[str, ...] = ()
    exit_code: int | None = None
    requires_user_action: str | None = None
    detail: str = ""

    def to_json_object(self) -> dict[str, object]:
        """返回可 JSON 序列化的字段字典。"""
        json_object = asdict(self)
        json_object["selection_evidence"] = list(self.selection_evidence)
        json_object["attempted_commands"] = list(self.attempted_commands)
        return json_object

    @property
    def has_usable_dependencies(self) -> bool:
        """该记录是否表示项目已拥有可用依赖（无需 Agent 兜底安装）。"""
        return self.outcome in _USABLE_OUTCOMES

    @property
    def missing_executables(self) -> tuple[str, ...]:
        """该记录指向的缺失可执行文件名（用于失败归属判断）。"""
        if self.reason != REASON_TOOL_MISSING or not self.package_manager:
            return ()
        return (self.package_manager,)


@dataclass(frozen=True)
class DependencyPreparationReport:
    """一次 worktree 依赖准备的全量结果。

    Attributes:
        worktree_path: 目标 worktree 绝对路径字符串。
        records: 每个前端项目一条诊断记录，按扫描顺序。
    """

    worktree_path: str
    records: tuple[FrontendProjectRecord, ...] = field(default_factory=tuple)

    @property
    def installed_relative_paths(self) -> list[Path]:
        """安装成功的项目 worktree 相对路径。"""
        return _paths_with_outcome(self.records, OUTCOME_INSTALLED)

    @property
    def linked_relative_paths(self) -> list[Path]:
        """以符号链接复用主检出依赖的项目 worktree 相对路径。"""
        return _paths_with_outcome(self.records, OUTCOME_REUSED)

    @property
    def escalation_requests(self) -> tuple[FrontendProjectRecord, ...]:
        """需要人类决定的记录（提权 / 主机全局安装 / 凭据）。"""
        return tuple(record for record in self.records if record.requires_user_action)

    def to_json_object(self) -> dict[str, object]:
        """返回落盘用的 JSON 字典（schema 见 :data:`RECORD_SCHEMA_VERSION`）。"""
        return {
            "version": RECORD_SCHEMA_VERSION,
            "worktree_path": self.worktree_path,
            "projects": [record.to_json_object() for record in self.records],
        }


def _paths_with_outcome(records: tuple[FrontendProjectRecord, ...], outcome: str) -> list[Path]:
    """按处置结论取项目的 worktree 相对路径。"""
    return [Path(record.project_path) for record in records if record.outcome == outcome]


def sanitize_detail_text(detail_text: str) -> str:
    """脱敏并截断诊断片段，避免把凭据写进落盘报告与 Issue 评论。

    Args:
        detail_text: 安装器 stderr / 异常文本等原始诊断内容。

    Returns:
        脱敏后截断到 :data:`MAX_DETAIL_CHARS` 的文本；空输入返回空串。
    """
    if not detail_text:
        return ""
    sanitized_text = detail_text
    for credential_pattern in _CREDENTIAL_PATTERNS:
        sanitized_text = credential_pattern.sub(
            lambda match: f"{match.group(1)}[redacted]",
            sanitized_text,
        )
    sanitized_text = " ".join(sanitized_text.split())
    if len(sanitized_text) <= MAX_DETAIL_CHARS:
        return sanitized_text
    return f"{sanitized_text[:MAX_DETAIL_CHARS]}…[truncated]"


def format_command_text(command: tuple[str, ...] | list[str]) -> str:
    """把 argv 渲染成 shell 可读文本，用于诊断展示与复现。"""
    return shlex.join(str(token) for token in command)


def classify_user_action_from_failure_text(failure_text: str) -> str | None:
    """将包管理器失败输出归类为需要人类决定的凭据或权限问题。

    Args:
        failure_text: 包管理器返回的 stdout / stderr 文本。

    Returns:
        ``privilege``、``credentials`` 或 ``None``。
    """
    lowered_text = failure_text.lower()
    if any(signature in lowered_text for signature in _PRIVILEGE_FAILURE_SIGNATURES):
        return USER_ACTION_PRIVILEGE
    if any(signature in lowered_text for signature in _CREDENTIAL_FAILURE_SIGNATURES):
        return USER_ACTION_CREDENTIALS
    return None


_CREDENTIAL_FAILURE_SIGNATURES = (
    "401 unauthorized",
    "403 forbidden",
    "e401",
    "e403",
    "authentication token",
    "unable to authenticate",
    "npm token",
    "registry login",
    "_auth",
)
_PRIVILEGE_FAILURE_SIGNATURES = (
    "eacces",
    "eperm",
    "operation not permitted",
    "permission denied",
    "cannot write files",
    "sudo",
)


def write_dependency_preparation_report(
    worktree_path: Path,
    preparation_report: DependencyPreparationReport,
) -> bool:
    """把依赖准备报告写入 worktree 的 ``.agent-runner/`` 目录。

    写入是 best effort：目标目录不可写、序列化失败等只记日志并返回 ``False``，
    绝不向上抛——准备失败不得变成 Issue 的致命错误。

    Args:
        worktree_path: 目标 worktree 绝对路径。
        preparation_report: 本次准备结果。

    Returns:
        是否成功落盘。
    """
    record_path = worktree_path / DEPENDENCY_PREPARATION_RELATIVE_PATH
    try:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(
            json.dumps(preparation_report.to_json_object(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as write_error:
        _logger.warning(
            "Could not persist dependency preparation report for %s: %s",
            worktree_path,
            write_error,
        )
        return False
    return True


def load_dependency_preparation_records(worktree_path: Path) -> tuple[FrontendProjectRecord, ...]:
    """读取 worktree 内已落盘的依赖准备记录。

    Args:
        worktree_path: 目标 worktree 绝对路径。

    Returns:
        记录元组；文件缺失、JSON 非法或 schema 版本不匹配时返回空元组。
    """
    record_path = worktree_path / DEPENDENCY_PREPARATION_RELATIVE_PATH
    if not record_path.is_file():
        return ()
    try:
        loaded_text = record_path.read_text(encoding="utf-8")
        loaded_object = json.loads(loaded_text)
    except (OSError, ValueError) as read_error:
        _logger.info(
            "Ignoring unreadable dependency preparation report %s: %s",
            record_path,
            read_error,
        )
        return ()
    if (
        not isinstance(loaded_object, dict)
        or loaded_object.get("version") != RECORD_SCHEMA_VERSION
        or not isinstance(loaded_object.get("projects"), list)
    ):
        return ()
    record_projects: list[FrontendProjectRecord] = []
    for project_entry in loaded_object["projects"]:
        if not isinstance(project_entry, dict) or "project_path" not in project_entry:
            continue
        record_projects.append(
            FrontendProjectRecord(
                project_path=str(project_entry.get("project_path", "")),
                outcome=str(project_entry.get("outcome", OUTCOME_NOT_PREPARED)),
                reason=str(project_entry.get("reason", "")),
                package_manager=_optional_str(project_entry.get("package_manager")),
                selection_evidence=_tuple_of_str(project_entry.get("selection_evidence")),
                attempted_commands=_tuple_of_str(project_entry.get("attempted_commands")),
                exit_code=_optional_int(project_entry.get("exit_code")),
                requires_user_action=_optional_str(project_entry.get("requires_user_action")),
                detail=str(project_entry.get("detail", "")),
            )
        )
    return tuple(record_projects)


def _optional_str(value: object) -> str | None:
    """把 JSON 值收敛成字符串或 ``None``。"""
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    """把 JSON 值收敛成整数或 ``None``（``bool`` 不算整数）。"""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _tuple_of_str(value: object) -> tuple[str, ...]:
    """把 JSON 列表收敛成字符串元组。"""
    if not isinstance(value, list):
        return ()
    return tuple(str(entry_value) for entry_value in value)


def render_dependency_context_section(worktree_path: Path) -> str:
    """渲染给 Agent 的「依赖准备上下文」prompt 段落。

    无记录（文件不存在或 worktree 未准备过）时返回空串，prompt 与改动前逐字一致。

    Args:
        worktree_path: agent 工作的 worktree 路径。

    Returns:
        Markdown 段落文本，或空串。
    """
    records = load_dependency_preparation_records(worktree_path)
    if not records:
        return ""
    lines = [
        "## Worktree dependency preparation context (runner diagnostics)",
        "",
        "The runner prepared project dependencies **recoverably** before handing you this "
        "task: it only does what is safe inside the repository and reports the rest. "
        "A dependency gap is not a task failure - decide the next safe step yourself.",
        "",
    ]
    for record in records:
        status_text = "ready" if record.has_usable_dependencies else "NOT ready"
        manager_fragment = (
            f", package manager: {record.package_manager}" if record.package_manager else ""
        )
        lines.append(
            f"- `{record.project_path}` - {status_text} "
            f"(outcome: {record.outcome}, reason: {record.reason}{manager_fragment})"
        )
        for evidence_line in record.selection_evidence:
            lines.append(f"  - selection evidence: {evidence_line}")
        if record.attempted_commands:
            lines.append(f"  - planned/attempted commands: {'; '.join(record.attempted_commands)}")
        else:
            lines.append(
                "  - planned/attempted commands: none (no safe install command was selected)"
            )
        if record.exit_code is not None:
            lines.append(f"  - exit code: {record.exit_code}")
        if record.detail:
            lines.append(f"  - diagnostics: {record.detail}")
        if record.requires_user_action:
            lines.append(
                f"  - needs a human decision ({record.requires_user_action}): see the "
                "handling rules below."
            )
    lines.extend(_DEPENDENCY_HANDLING_RULES)
    return "\n".join(lines)


_DEPENDENCY_HANDLING_RULES: tuple[str, ...] = (
    "",
    "Handling rules for these diagnostics:",
    "- Dependencies marked ready need no action; leave them untouched.",
    "- You decide the next step autonomously: reuse what is already on disk (a parent "
    "workspace install, the main checkout's ``node_modules``, or another worktree's), "
    "or install **repository-local** with the package manager the evidence points at.",
    "- Never run ``sudo``, never install into the host's global prefix or toolchain "
    "(no ``npm install -g``, no editing shell rc files), never write outside this "
    "worktree, and never use credentials you were not given.",
    "- If the only remaining path requires privilege escalation, a host-global toolchain, "
    "or credentials the runner does not have: stop, state the exact blocker, the concrete "
    "command(s) a human could run, and the alternatives. That is a recoverable human "
    "decision, not a failure - do not mark the task failed for it.",
    "- Missing frontend dependencies only block work that actually needs them; "
    "backend, docs, and test work should proceed normally.",
)


def escalate_dependency_environment_failure(
    raw_error: BaseException,
    worktree_path: Path,
) -> BaseException:
    """把「环境缺工具」导致的失败转成可恢复状态，而不是 ``agent/failed``.

    ``agent/failed`` 表示 Agent/执行本身失败。准备工作已经把缺工具、选择依据与失败
    原因写成结构化记录，因此当失败可归因到这些记录（缺可执行文件、或验证命令以 127
    / ``command not found`` 结束）时，应改判为需要人类决定的可恢复阻塞。

    归属判据刻意保守：只在能对上记录里的工具名时才转判，其余失败原样返回，
    保持既有 ``agent/failed`` 语义不被稀释。

    Args:
        raw_error: 执行链路抛出的原始异常。
        worktree_path: 本次运行的 worktree 路径。

    Returns:
        可归因到环境缺口时返回 :class:`DependencyEnvironmentBlockedError`；
        否则返回原始异常。
    """
    from backend.core.use_cases.agent_runner_failure import (
        DependencyEnvironmentBlockedError,
    )

    records = load_dependency_preparation_records(worktree_path)
    if not records:
        return raw_error
    blocked_tools = {
        tool
        for record in records
        for tool in record.missing_executables
        if not record.has_usable_dependencies
    }
    mentioned_tools = _tools_named_by_failure(raw_error, blocked_tools)
    failure_command_results = _failure_command_results(raw_error)
    action_records = tuple(
        record
        for record in records
        if not record.has_usable_dependencies
        and record.requires_user_action in {USER_ACTION_PRIVILEGE, USER_ACTION_CREDENTIALS}
        and any(
            _command_result_matches_user_action(command_result, record)
            for command_result in failure_command_results
        )
    )
    if not mentioned_tools and not action_records:
        return raw_error
    relevant_records = tuple(
        record
        for record in records
        if (record.reason == REASON_TOOL_MISSING and record.package_manager in mentioned_tools)
        or record in action_records
    )
    return DependencyEnvironmentBlockedError(
        build_environment_block_message(
            tuple(sorted(mentioned_tools)),
            relevant_records,
            root_cause=f"{type(raw_error).__name__}: {raw_error}",
        ),
        records,
    )


def _tools_named_by_failure(
    failure_error: BaseException,
    blocked_tools: set[str],
) -> set[str]:
    """从失败异常里找出被点名的缺失工具（结构化字段 + 127 退出码证据）."""
    named_tools: set[str] = set()
    missing_filename = getattr(failure_error, "filename", None)
    if isinstance(missing_filename, str):
        executable_name = Path(missing_filename).name
        if executable_name in blocked_tools:
            named_tools.add(executable_name)
    for command_result in _failure_command_results(failure_error):
        if not _looks_like_missing_command(command_result, blocked_tools):
            continue
        named_tools.update(tool for tool in blocked_tools if tool in command_result.stderr)
    return named_tools


def _failure_command_results(failure_error: BaseException) -> list[object]:
    """取出异常及其包装 cause 携带的验证命令结果。"""
    pending_errors = [failure_error]
    visited_error_ids: set[int] = set()
    collected_results: list[object] = []
    while pending_errors:
        current_error = pending_errors.pop()
        if id(current_error) in visited_error_ids:
            continue
        visited_error_ids.add(id(current_error))

        direct_results = getattr(current_error, "verification_results", None)
        if isinstance(direct_results, list):
            collected_results.extend(direct_results)
        attempt_results = getattr(current_error, "attempt_results", None)
        if isinstance(attempt_results, list):
            for attempt_result in attempt_results:
                attempt_outputs = getattr(attempt_result, "verification_results", None)
                if isinstance(attempt_outputs, list):
                    collected_results.extend(attempt_outputs)

        for chained_error in (
            getattr(current_error, "__cause__", None),
            getattr(current_error, "__context__", None),
        ):
            if isinstance(chained_error, BaseException):
                pending_errors.append(chained_error)
    return collected_results


def _looks_like_missing_command(command_result: object, blocked_tools: set[str]) -> bool:
    """判断一条命令结果是否形如「可执行文件不存在」."""
    return_code = getattr(command_result, "return_code", None)
    stderr_text = getattr(command_result, "stderr", "")
    if not isinstance(stderr_text, str):
        return False
    names_blocked_tool = any(tool in stderr_text for tool in blocked_tools)
    if not names_blocked_tool:
        return False
    return return_code == _COMMAND_NOT_FOUND_EXIT_CODE or "command not found" in stderr_text


def _command_result_matches_user_action(
    command_result: object,
    dependency_record: FrontendProjectRecord,
) -> bool:
    """仅当失败命令针对该包管理器且输出命中已知权限/凭据特征时才阻塞。"""
    if dependency_record.requires_user_action is None or not dependency_record.package_manager:
        return False
    command_value = getattr(command_result, "command", ())
    if isinstance(command_value, (tuple, list)):
        command_tokens = tuple(str(token) for token in command_value)
    else:
        command_tokens = ()
    manager_name = dependency_record.package_manager
    command_mentions_manager = any(
        Path(command_token).name == manager_name or manager_name in command_token
        for command_token in command_tokens
    )
    if not command_mentions_manager:
        return False
    stdout_text = getattr(command_result, "stdout", "")
    stderr_text = getattr(command_result, "stderr", "")
    failure_text = "\n".join(
        output_text for output_text in (stdout_text, stderr_text) if isinstance(output_text, str)
    )
    return (
        classify_user_action_from_failure_text(failure_text)
        == dependency_record.requires_user_action
    )


def build_environment_block_message(
    blocked_tools: tuple[str, ...],
    records: tuple[FrontendProjectRecord, ...],
    *,
    root_cause: str,
) -> str:
    """组装面向人的阻塞说明：缺什么、为什么、人能选什么。"""
    lines = ["Project dependency preparation needs a human decision."]
    if blocked_tools:
        lines.append(f"Missing required executable(s): {', '.join(blocked_tools)}.")
    action_labels = tuple(
        sorted(
            {
                record.requires_user_action
                for record in records
                if record.requires_user_action is not None
            }
        )
    )
    if action_labels:
        lines.append(f"Required human action(s): {', '.join(action_labels)}.")
    lines.extend(("", "Runner diagnostics:"))
    for record in records:
        if record.requires_user_action is None and record.reason != REASON_TOOL_MISSING:
            continue
        lines.append(
            f"- `{record.project_path}`: outcome={record.outcome} reason={record.reason} "
            f"manager={record.package_manager or 'undetermined'}"
        )
        for evidence_line in record.selection_evidence:
            lines.append(f"  - evidence: {evidence_line}")
        if record.detail:
            lines.append(f"  - diagnostics: {record.detail}")
    lines.extend(("", "Options for the operator:"))
    if blocked_tools or USER_ACTION_HOST_TOOLCHAIN in action_labels:
        lines.append(
            f"- Install the toolchain on this host so `{', '.join(blocked_tools)}` is on PATH, "
            "or use a runner environment that provides it."
        )
    if USER_ACTION_PRIVILEGE in action_labels:
        lines.append(
            "- Use a repository-local or rootless install path, or have an operator adjust "
            "the required filesystem permissions."
        )
    if USER_ACTION_CREDENTIALS in action_labels:
        lines.append(
            "- Provide the required registry credentials through the runner's secret or "
            "environment configuration; do not paste credentials into the Issue."
        )
    lines.extend(
        [
            "- Pre-install the project dependencies in the main checkout so the worktree can "
            "reuse them, then resume with `kc blocked-continue`.",
            "",
            f"Root cause: {sanitize_detail_text(root_cause)}",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "DEPENDENCY_PREPARATION_RELATIVE_PATH",
    "DependencyPreparationReport",
    "FrontendProjectRecord",
    "OUTCOME_INSTALLED",
    "OUTCOME_NOT_PREPARED",
    "OUTCOME_PRESENT",
    "OUTCOME_REUSED",
    "OUTCOME_SKIPPED",
    "REASON_INSTALL_FAILED",
    "REASON_INSTALL_NO_OUTPUT",
    "REASON_LAUNCH_FAILED",
    "REASON_LINK_FAILED",
    "REASON_MANAGER_CONFLICT",
    "REASON_NO_ACTION_NEEDED",
    "REASON_NO_LOCKFILE",
    "REASON_PREPARATION_CRASHED",
    "REASON_REUSABLE_ABSENT",
    "REASON_REUSABLE_STALE",
    "REASON_TOOL_MISSING",
    "REASON_WORKSPACE_COVERED",
    "USER_ACTION_CREDENTIALS",
    "USER_ACTION_HOST_TOOLCHAIN",
    "USER_ACTION_PRIVILEGE",
    "build_environment_block_message",
    "classify_user_action_from_failure_text",
    "escalate_dependency_environment_failure",
    "format_command_text",
    "load_dependency_preparation_records",
    "render_dependency_context_section",
    "sanitize_detail_text",
    "write_dependency_preparation_report",
]

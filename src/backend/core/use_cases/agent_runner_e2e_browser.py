"""浏览器 E2E 验证命令的执行层（FR-2 / FR-3 / FR-4 / FR-5）。

keda 不内置、不绑定任何 E2E 断言框架（D-01）：本模块只负责"提供执行环境 +
收产物 + 门禁"——可用性预检、受控的应用启停、以 child_env 白名单环境执行
目标仓库自己的浏览器脚本、产物过 FR-11a artifact health 硬层。验证脚本
拿不到 runner 凭据（GitHub token、模型 API key 等默认不在白名单内）；
超时击杀走进程组回收，浏览器与被测应用进程一并带走，不留僵尸。

失败语义进入现有 ``VERIFICATION_FAILED`` 可恢复通道（D-03），不新增状态机
分支：所有失败以非零 ``CommandResult`` 返回，stderr 携带
``BROWSER_E2E_FAILURE [category=<分类>]`` 标记与修复指引，供失败评论与
recovery prompt 渲染（见
:func:`backend.core.use_cases.agent_runner_failure.format_e2e_failure_classification`）。
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    E2E_CHILD_ENV_PROFILE,
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    BrowserE2EVerificationCommand,
    CommandResult,
)
from backend.core.use_cases.agent_runner_structured_evidence import (
    ArtifactSpec,
    ValidationEvidenceError,
    validate_evidence_artifact,
)

__all__ = [
    "E2EFailureCategory",
    "extract_e2e_failure_category",
    "format_e2e_failure_classification",
    "run_browser_e2e_verification_command",
]

#: 失败结果 stderr 携带的分类标记前缀，供执行层与渲染层共同解析。
E2E_FAILURE_MARKER_PREFIX = "BROWSER_E2E_FAILURE"

_MARKER_PATTERN = re.compile(re.escape(E2E_FAILURE_MARKER_PREFIX) + r"\s+\[category=([a-z_]+)\]")

_PROBE_TIMEOUT_SECONDS = 120
_PROBE_OUTPUT_TAIL_LINES = 20
_APP_LOG_TAIL_LINES = 40
_DEFAULT_STARTUP_WAIT_SECONDS = 10
_READINESS_POLL_INTERVAL_SECONDS = 1
_MAX_CAPTURED_TAIL_CHARS = 4000

#: 默认浏览器运行时探测（启发式，不绑定框架）：命中 Playwright 浏览器缓存
#: 目录或任一可执行浏览器即认为环境可用；探测失败时给出安装指引。
_DEFAULT_BROWSER_PROBE_COMMAND = """
for cache_dir in "${PLAYWRIGHT_BROWSERS_PATH:-}" \
    "$HOME/Library/Caches/ms-playwright" "$HOME/.cache/ms-playwright"; do
    if [ -n "$cache_dir" ] && [ -d "$cache_dir" ]; then exit 0; fi
done
for browser_executable in chromium chromium-browser google-chrome chrome firefox; do
    if command -v "$browser_executable" >/dev/null 2>&1; then exit 0; fi
done
exit 1
"""


class E2EFailureCategory(str, Enum):
    """浏览器 E2E 验证失败的分类（rv-3：可诊断、不坍缩为同一裸失败）。

    Attributes:
        token: 标记 ``[category=...]`` 与配置文档中使用的小写分类 token。
        guidance: 面向运营者 / agent 的修复指引，随失败结果一起渲染。
    """

    SCRIPT_MISSING = "script_missing"
    BROWSER_RUNTIME_MISSING = "browser_runtime_missing"
    APP_NOT_READY = "app_not_ready"
    SCRIPT_FAILED = "script_failed"
    SCRIPT_TIMEOUT = "script_timeout"
    ARTIFACT_UNHEALTHY = "artifact_unhealthy"

    @property
    def token(self) -> str:
        """标记里使用的分类 token（即枚举值）。"""
        return self.value

    @property
    def guidance(self) -> str:
        """该分类的修复指引文案。"""
        return _CATEGORY_GUIDANCE[self]


_CATEGORY_GUIDANCE: dict[E2EFailureCategory, str] = {
    E2EFailureCategory.SCRIPT_MISSING: (
        "Fix: point `script` to a file that exists inside the worktree (path "
        "relative to the worktree root) and is runnable: the shell executes the "
        "path directly, so it needs a shebang plus the executable bit "
        "(`chmod +x`). RV/evidence scripts belong under the evidence scripts "
        "directory (`<evidence_dir>/scripts/...`); make sure the script is "
        "present in this worktree before verification runs."
    ),
    E2EFailureCategory.BROWSER_RUNTIME_MISSING: (
        "Fix: install a headless browser runtime on the runner machine — for "
        "Playwright repos typically `just e2e-install` or `pnpm exec playwright "
        "install chromium` inside the repo's E2E package. Non-standard setups: "
        "set the entry's `probe` to a command that exits 0 exactly when the "
        "browser runtime is available. CI container images are a documented "
        "follow-up; first release only guarantees the local machine."
    ),
    E2EFailureCategory.APP_NOT_READY: (
        "Fix: check that `app_start` really serves `ready_url` — the tail of "
        "the app log is included above. For apps without an HTTP readiness "
        "surface, drop `ready_url` and set `startup_wait_seconds` (fixed wait; "
        "the script then self-checks readiness)."
    ),
    E2EFailureCategory.SCRIPT_FAILED: (
        "Fix: the browser script exited non-zero — the page flow it asserts "
        "against is the real failure (wrong selector, broken UI, unexpected "
        "behavior). keda deliberately does not interpret the assertions; read "
        "the script output above."
    ),
    E2EFailureCategory.SCRIPT_TIMEOUT: (
        "Fix: the E2E run exceeded its `timeout_seconds`; its whole process "
        "group (browser children and the backgrounded app) was killed, so no "
        "zombie processes remain. Raise `timeout_seconds` if the suite is "
        "legitimately slower, or fix the hang."
    ),
    E2EFailureCategory.ARTIFACT_UNHEALTHY: (
        "Fix: the script exited 0 but a declared artifact failed the FR-11a "
        "hard layer (missing / 0-byte / mime mismatch / below min_size / "
        "stale). Correct the artifact `path` / `mime` / `min_size` "
        "declarations, or make the script produce the file fresh in this run."
    ),
}


def run_browser_e2e_verification_command(
    e2e_command: BrowserE2EVerificationCommand,
    worktree_path: Path,
    process_runner: IProcessRunner,
) -> CommandResult:
    """以受控方式执行一条浏览器 E2E 验证命令并返回结果（FR-1..FR-5）。

    控制流：脚本入口预检 → 浏览器运行时预检 → 在同一个受控子进程内
    （后台启动应用 → HTTP 就绪探测或固定等待 → 执行脚本 → 回收应用进程树）
    → 脚本通过后再对声明产物跑 artifact health 硬层。所有子进程都经
    child_env 白名单档（``E2E_CHILD_ENV_PROFILE``）过滤环境变量并带
    wall-clock 超时，超时击杀覆盖整个进程组。

    Args:
        e2e_command: 结构化 E2E 验证命令条目。
        worktree_path: worktree 根目录。
        process_runner: 子进程执行端口。

    Returns:
        CommandResult: 非零 ``return_code`` 即验证失败（进入既有
        VERIFICATION_FAILED 通道），stderr 携带分类标记与修复指引；
        零返回码表示脚本通过且全部声明产物通过硬层。
    """
    started_at = datetime.now(timezone.utc)
    monotonic_start = time.monotonic()

    script_missing_result = _preflight_script_entry(e2e_command, worktree_path)
    if script_missing_result is not None:
        return script_missing_result

    runtime_missing_result = _preflight_browser_runtime(e2e_command, worktree_path, process_runner)
    if runtime_missing_result is not None:
        return runtime_missing_result

    composed_shell = _compose_e2e_shell(e2e_command)
    try:
        execution_result = process_runner.run(
            ["bash", "-lc", composed_shell],
            cwd=worktree_path,
            check=False,
            capture_output=True,
            timeout=e2e_command.timeout_seconds,
            inactivity_timeout=e2e_command.inactivity_timeout_seconds,
            label=f"browser-e2e:{e2e_command.script}",
            env_profile=E2E_CHILD_ENV_PROFILE,
            env_allow_extra=e2e_command.env_allow,
        )
    except subprocess.TimeoutExpired as timeout_error:
        return _failure_result(
            e2e_command,
            E2EFailureCategory.SCRIPT_TIMEOUT,
            (
                f"E2E run timed out after {e2e_command.timeout_seconds}s and its "
                f"process group was terminated: {timeout_error}"
            ),
            captured_output=_timeout_partial_output(timeout_error),
            duration_seconds=time.monotonic() - monotonic_start,
        )

    if execution_result.return_code != 0:
        failure_category = (
            _category_from_output(execution_result) or E2EFailureCategory.SCRIPT_FAILED
        )
        detail = (
            f"E2E script `{e2e_command.script}` exited "
            f"{execution_result.return_code} (wall {execution_result.duration_seconds:.1f}s)."
        )
        return _failure_result(
            e2e_command,
            failure_category,
            detail,
            captured_output=_merge_streams(execution_result),
            duration_seconds=time.monotonic() - monotonic_start,
        )

    artifact_failure = _gate_declared_artifacts(
        e2e_command, worktree_path, process_runner, started_at
    )
    if artifact_failure is not None:
        return artifact_failure

    artifacts_summary = "\n".join(
        f"verified artifact: {artifact.path} (mime={artifact.mime})"
        for artifact in e2e_command.artifacts
    )
    return CommandResult(
        command=("browser_e2e", e2e_command.script),
        return_code=0,
        stdout=(execution_result.stdout + "\n" + artifacts_summary).strip()
        if artifacts_summary
        else execution_result.stdout,
        stderr=execution_result.stderr,
        duration_seconds=time.monotonic() - monotonic_start,
    )


def extract_e2e_failure_category(result: CommandResult) -> E2EFailureCategory | None:
    """从验证命令结果的 stderr/stdout 中提取 E2E 失败分类。

    Args:
        result: 任一验证命令结果（非 E2E 结果返回 ``None``）。

    Returns:
        命中的失败分类；结果不含 E2E 分类标记时为 ``None``。
    """
    return _category_from_output(result)


def format_e2e_failure_classification(result: CommandResult) -> str:
    """把一条验证结果渲染为单行 E2E 失败分类摘要（无标记时为空串）。

    渲染层用它把"环境缺失 / 脚本失败 / 产物不健全"的子分类显式带到失败
    评论与 recovery prompt 里，而不是让三类失败坍缩成同一条裸
    VERIFICATION_FAILED。
    """
    category = extract_e2e_failure_category(result)
    if category is None:
        return ""
    return (
        f"Browser E2E verification failed with sub-category "
        f"`{category.token}`. Guidance: {category.guidance}"
    )


def _preflight_script_entry(
    e2e_command: BrowserE2EVerificationCommand, worktree_path: Path
) -> CommandResult | None:
    """预检脚本入口存在性与可执行性（FR-2）；缺失时给出分类失败而非跑到一半才炸。

    脚本以 ``bash -lc '<script>'`` 交给 shell 执行，由脚本自身的 shebang 选择
    解释器（keda 不绑定 E2E 框架，D-01）。缺少执行位时 bash 会把脚本内容当
    shell 语法回退解释——预检直接拦下，避免产生不可读的伪失败。
    """
    script_path = worktree_path / e2e_command.script
    if not script_path.is_file():
        return _failure_result(
            e2e_command,
            E2EFailureCategory.SCRIPT_MISSING,
            f"script entry `{e2e_command.script}` does not exist under {worktree_path}.",
        )
    try:
        is_executable = bool(script_path.stat().st_mode & 0o111)
    except OSError:
        is_executable = False
    if not is_executable:
        return _failure_result(
            e2e_command,
            E2EFailureCategory.SCRIPT_MISSING,
            f"script entry `{e2e_command.script}` exists but is not executable; "
            "give it a shebang and `chmod +x` so the shell can run it.",
        )
    return None


def _preflight_browser_runtime(
    e2e_command: BrowserE2EVerificationCommand,
    worktree_path: Path,
    process_runner: IProcessRunner,
) -> CommandResult | None:
    """预检浏览器运行时可用性（FR-2）；探测命令同样走白名单档。"""
    probe_command = e2e_command.probe or _DEFAULT_BROWSER_PROBE_COMMAND
    try:
        probe_result = process_runner.run(
            ["bash", "-lc", probe_command],
            cwd=worktree_path,
            check=False,
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            label=f"browser-e2e-probe:{e2e_command.script}",
            env_profile=E2E_CHILD_ENV_PROFILE,
            env_allow_extra=e2e_command.env_allow,
        )
    except subprocess.TimeoutExpired:
        return _failure_result(
            e2e_command,
            E2EFailureCategory.BROWSER_RUNTIME_MISSING,
            f"browser runtime probe timed out after {_PROBE_TIMEOUT_SECONDS}s: "
            f"`{probe_command}`.",
        )
    if probe_result.return_code == 0:
        return None
    return _failure_result(
        e2e_command,
        E2EFailureCategory.BROWSER_RUNTIME_MISSING,
        (
            f"browser runtime probe exited {probe_result.return_code}: "
            f"`{probe_command}`. Probe output tail:\n"
            f"{_tail(_merge_streams(probe_result))}"
        ),
    )


def _compose_e2e_shell(e2e_command: BrowserE2EVerificationCommand) -> str:
    """拼装"应用后台启动 → 就绪探测 → 执行脚本 → 进程树回收"的单一 bash 脚本。

    整段脚本作为一条 ``bash -lc`` 命令交给 IProcessRunner：子进程被放进独立
    进程组，wall-clock 超时时由执行层向整组发信号，浏览器派生进程与后台应用
    一并回收；正常结束时脚本自己的 EXIT trap 回收应用进程树。
    """
    script_lines: list[str] = [
        "set -u",
        "e2e_kill_descendants() {",
        '  for e2e_child_pid in $(pgrep -P "$1" 2>/dev/null || true); do',
        '    pgrep -P "$e2e_child_pid" >/dev/null 2>&1 && e2e_kill_descendants "$e2e_child_pid"',
        '    kill "$e2e_child_pid" 2>/dev/null || true',
        "  done",
        '  kill "$1" 2>/dev/null || true',
        "}",
        'e2e_app_pid=""',
        "e2e_cleanup() {",
        '  if [ -n "$e2e_app_pid" ]; then e2e_kill_descendants "$e2e_app_pid"; fi',
        "}",
        "trap e2e_cleanup EXIT",
    ]

    for artifact in e2e_command.artifacts:
        artifact_parent = str(Path(artifact.path).parent)
        if artifact_parent not in ("", "."):
            script_lines.append(f"mkdir -p {shlex.quote(artifact_parent)}")

    if e2e_command.app_start:
        script_lines.append('e2e_app_log=$(mktemp "${TMPDIR:-/tmp}/iar-e2e-app.XXXXXX") || exit 3')
        script_lines.append(
            f"bash -lc {shlex.quote(e2e_command.app_start)} "
            '>"$e2e_app_log" 2>&1 < /dev/null & e2e_app_pid=$!'
        )
        script_lines.extend(_compose_readiness_lines(e2e_command))
    elif e2e_command.ready_url:
        # 配置层已拦截（ready_url 无 app_start 报错）；这里双保险不留死路径。
        script_lines.append(
            f"printf '{E2E_FAILURE_MARKER_PREFIX} [category=app_not_ready] "
            "ready_url declared without app_start\\n' >&2; exit 4"
        )

    script_lines.append(f"bash -lc {shlex.quote(e2e_command.script)}")
    script_lines.append("e2e_script_rc=$?")
    script_lines.append("exit $e2e_script_rc")
    return "\n".join(script_lines) + "\n"


def _compose_readiness_lines(e2e_command: BrowserE2EVerificationCommand) -> list[str]:
    """生成应用就绪等待语句：HTTP 探测优先，否则固定等待（首版承诺见 §12）。"""
    if e2e_command.ready_url:
        quoted_url = shlex.quote(e2e_command.ready_url)

        def _emit_app_not_ready(reason: str) -> str:
            return (
                f"printf '{E2E_FAILURE_MARKER_PREFIX} [category=app_not_ready] "
                f'{reason}\\n\' >&2; tail -n {_APP_LOG_TAIL_LINES} "$e2e_app_log" '
                ">&2; exit 5"
            )

        return [
            f"e2e_ready_deadline=$(( $(date +%s) + {int(e2e_command.ready_timeout_seconds)} ))",
            "while :; do",
            f"  if curl -fsS -o /dev/null --max-time 2 {quoted_url}; then break; fi",
            f"  if ! kill -0 \"$e2e_app_pid\" 2>/dev/null; then {_emit_app_not_ready('app process exited before readiness')}; fi",
            '  if [ "$(date +%s)" -ge "$e2e_ready_deadline" ]; then',
            f"    {_emit_app_not_ready('readiness probe timed out')}",
            "  fi",
            f"  sleep {_READINESS_POLL_INTERVAL_SECONDS}",
            "done",
        ]
    wait_seconds = (
        e2e_command.startup_wait_seconds
        if e2e_command.startup_wait_seconds is not None
        else _DEFAULT_STARTUP_WAIT_SECONDS
    )
    return [f"sleep {int(wait_seconds)}"]


def _gate_declared_artifacts(
    e2e_command: BrowserE2EVerificationCommand,
    worktree_path: Path,
    process_runner: IProcessRunner,
    started_at: datetime,
) -> CommandResult | None:
    """对声明产物跑 FR-11a artifact health 硬层（FR-5），失败即分类失败。"""
    for artifact in e2e_command.artifacts:
        artifact_spec = ArtifactSpec(
            path=artifact.path,
            mime=artifact.mime,
            min_size=artifact.min_size,
            key_claim=artifact.key_claim,
        )
        try:
            validate_evidence_artifact(
                worktree_path, artifact_spec, process_runner, since=started_at
            )
        except ValidationEvidenceError as artifact_error:
            return _failure_result(
                e2e_command,
                E2EFailureCategory.ARTIFACT_UNHEALTHY,
                str(artifact_error),
            )
    return None


def _failure_result(
    e2e_command: BrowserE2EVerificationCommand,
    category: E2EFailureCategory,
    detail: str,
    captured_output: str = "",
    duration_seconds: float = 0.0,
) -> CommandResult:
    """构建携带分类标记与修复指引的非零 CommandResult。"""
    stderr_text = (
        f"{E2E_FAILURE_MARKER_PREFIX} [category={category.token}] {detail}\n"
        f"Guidance: {category.guidance}"
    )
    if captured_output:
        stderr_text += f"\n--- captured output tail ---\n{_tail(captured_output)}"
    return CommandResult(
        command=("browser_e2e", e2e_command.script),
        return_code=1,
        stdout="",
        stderr=stderr_text,
        duration_seconds=duration_seconds,
    )


def _category_from_output(result: CommandResult) -> E2EFailureCategory | None:
    """解析脚本输出中的分类标记；未知 token 视为无标记。"""
    match = _MARKER_PATTERN.search(result.stderr or "") or _MARKER_PATTERN.search(
        result.stdout or ""
    )
    if match is None:
        return None
    return _CATEGORY_BY_TOKEN.get(match.group(1))


_CATEGORY_BY_TOKEN: dict[str, E2EFailureCategory] = {
    category.token: category for category in E2EFailureCategory
}


def _merge_streams(result: CommandResult) -> str:
    """把 stdout/stderr 合成一段供失败详情引用。"""
    return f"{result.stdout}\n{result.stderr}".strip()


def _timeout_partial_output(timeout_error: subprocess.TimeoutExpired) -> str:
    """提取超时前已捕获的部分输出（字节流按 utf-8 宽松解码）。"""
    partial_chunks: list[str] = []
    for stream in (timeout_error.output, timeout_error.stderr):
        if isinstance(stream, bytes):
            partial_chunks.append(stream.decode("utf-8", errors="replace"))
        elif isinstance(stream, str):
            partial_chunks.append(stream)
    return "\n".join(partial_chunks).strip()


def _tail(output_text: str) -> str:
    """截断过长输出，保留尾部。"""
    if len(output_text) <= _MAX_CAPTURED_TAIL_CHARS:
        return output_text
    return "[truncated; showing tail]\n" + output_text[-_MAX_CAPTURED_TAIL_CHARS:]

"""Subprocess runner implementation."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.shared.interfaces.agent_output_protocol import (
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
    PLAIN_PROTOCOL_ID,
)
from backend.core.shared.interfaces.agent_runner import (
    E2E_CHILD_ENV_PROFILE,
)
from backend.core.shared.interfaces.output_timestamps import (
    TimestampedStreamFormatter as TimestampedStreamFormatter,
    format_timestamped_line,
)
from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_runner import TokenUsage
from backend.core.shared.models.agent_stall import AttemptOwnership, StallCancelOutcome
from backend.infrastructure.agent_stream_usage import (
    StreamUsageCollector,
    _attach_stream_observations,
    parse_reported_model_from_plain_stdout,
    parse_usage_from_plain_stdout,
)
from backend.infrastructure.attempt_process_registry import (
    probe_attempt_ownership,
    register_live_attempt,
    terminate_attempt,
    unregister_live_attempt,
)
from backend.infrastructure.child_env import build_e2e_child_env, build_sanitized_child_env
from backend.infrastructure.logging.logger import logger
from backend.infrastructure.process_identity import process_start_time
from backend.infrastructure.process_runner_streams import (
    ClaudeStreamRenderer as ClaudeStreamRenderer,
    _run_pty_stream as _run_pty_stream,
    run_filtered_claude_stream as run_filtered_claude_stream,
)


def _resolve_profiled_child_env(
    env_profile: str,
    env_allow_extra: Sequence[str],
    *,
    capture_output: bool,
    timeout: int | None,
    output_protocol: str | None,
) -> dict[str, str]:
    """按档名构造受控子进程环境，并校验该档的安全前提。

    E2E 白名单档的前提是：输出被捕获、带 wall-clock 超时（超时才能走
    进程组击杀兜底），且不混用 agent 流式协议。前提不成立时报错，
    绝不静默回退到全量环境继承——那正是本档要防的凭据泄漏路径。

    Args:
        env_profile: 请求的档名，目前仅支持 :data:`E2E_CHILD_ENV_PROFILE`。
        env_allow_extra: 追加放行的变量名。
        capture_output: 调用是否捕获输出。
        timeout: 调用传入的 wall-clock 超时。
        output_protocol: 调用选择的输出协议。

    Returns:
        过滤后的子进程环境 dict。

    Raises:
        ValueError: 档名未知，或白名单档的安全前提不成立。
    """
    if env_profile != E2E_CHILD_ENV_PROFILE:
        raise ValueError(f"Unknown process env profile: {env_profile!r}")
    if not capture_output or timeout is None:
        raise ValueError(
            "env_profile='browser_e2e' requires capture_output=True and a "
            "wall-clock timeout so hung browser/app processes are killed with "
            "their whole process group; refusing to run with an unfiltered, "
            "unbounded subprocess environment."
        )
    if output_protocol not in (None, PLAIN_PROTOCOL_ID):
        raise ValueError(
            f"env_profile='browser_e2e' cannot combine with output protocol "
            f"{output_protocol!r}; the sanitized-env guarantee only holds on "
            "the captured plain path."
        )
    return build_e2e_child_env(env_allow_extra)


try:
    import pty
except ImportError:  # pragma: no cover - pty is POSIX-only (absent on Windows).
    pty = None  # type: ignore[assignment]

# Plain-output agents (such as kimi) block-buffer stdout when it is a pipe, hiding
# their progress until exit. A pseudo-terminal makes them line-buffer again.
_PTY_AVAILABLE = pty is not None and hasattr(pty, "openpty")

_MAX_BUFFER_SIZE = 4096
_MAX_ERROR_DETAIL_LEN = 4096
_COMMAND_HEARTBEAT_SECONDS = 60

# 带超时的子进程都放进**自己的进程组**，超时时才能整组回收（见
# :func:`_terminate_process_tree`）。用 ``process_group=0``（setpgid）而不是
# ``start_new_session=True``（setsid）：只换进程组、保留控制终端，避免改变
# kimi/codex 这类依赖 tty 行为的 agent 的运行环境。
_OWN_PROCESS_GROUP_KWARGS: dict[str, Any] = {"process_group": 0} if hasattr(os, "setpgid") else {}


def _terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    expected_process_group: int | None = None,
    expected_process_started_at: float | None = None,
) -> bool:
    """终止超时子进程**及其整个进程组**，而不是只终止直接子进程。

    ``Popen.kill()`` 只向直接子进程发信号。对 ``bash -lc "script.sh | tee log"``
    这类命令，管道里的 ``tee`` 和脚本自己拉起的后台进程会活下来，并继续持有它们
    继承到的 stdout 写端；于是 :func:`_run_captured_process` 的 ``communicate()``
    永远读不到 EOF，超时后整个 runner 无限期阻塞——被 kill 的子进程连僵尸都没被
    回收，daemon 也不再轮询任何 Issue。

    子进程由 ``_OWN_PROCESS_GROUP_KWARGS`` 放进独立进程组（组 id 等于其 pid），
    向组发信号即可覆盖所有派生进程。若平台不支持而子进程仍留在 runner 自己的组
    里，则退回只杀直接子进程，避免把 runner 自己一起杀掉。

    0 号和 1 号组同样一律不碰：``killpg(0, ...)`` 按语义就是"杀调用者自己所在的
    组"，1 号组属于 init。两者都不可能是子进程独立建出来的组（那个组 id 等于子
    进程 pid），所以排除它们不会漏掉任何真实场景，却能挡住 pid 取到异常值时把整台
    机器打穿——CI 上就出现过：某个测试传进来的 ``pid`` 会被 :func:`os.getpgid`
    当成 1，于是这里真的向 1 号组发了 SIGKILL，直接把 runner 打没，表现为任务永远
    不结束且日志完全拿不到。

    Args:
        process: 需要终止的子进程句柄。

    Returns:
        是否已发出终止信号。监督器取消时，实时 PID、PGID 与创建时刻任一对不上
        都拒绝信号；普通超时调用忽略返回值并沿用既有失败处理。
    """
    killable_group_id: int | None = None
    has_expected_identity = expected_process_group is not None
    if has_expected_identity and not hasattr(os, "killpg"):
        return False
    if has_expected_identity:
        if expected_process_started_at is None or process.poll() is not None:
            return False
        try:
            current_group_id = os.getpgid(process.pid)
        except (OSError, AttributeError):
            return False
        if (
            current_group_id != expected_process_group
            or process_start_time(process.pid) != expected_process_started_at
        ):
            return False
        killable_group_id = expected_process_group
    if hasattr(os, "killpg") and not has_expected_identity:
        try:
            child_group_id = os.getpgid(process.pid)
        except OSError:  # 子进程已退出或已被回收。
            child_group_id = None
        if child_group_id is not None and child_group_id > 1 and child_group_id != os.getpgid(0):
            killable_group_id = child_group_id
    if killable_group_id is not None:
        try:
            os.killpg(killable_group_id, signal.SIGKILL)
            return True
        except OSError:  # 组已消失，退回直接终止。
            if has_expected_identity:
                return False
    try:
        process.kill()
        return True
    except OSError:  # 子进程已退出。
        return False


@dataclass(frozen=True)
class CommandResult:
    """Captured subprocess result.

    ``duration_seconds`` 让调用方能把"这一步花了多久"记进 attempt 历史 /
    日志——没有它，卡住的到底是哪条命令只能靠翻日志时间戳倒推。字段与
    ``backend.core.shared.models.agent_runner.CommandResult`` 保持一致。
    """

    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0
    output_protocol: str = PLAIN_PROTOCOL_ID
    token_usage: TokenUsage | None = None
    session_id: str | None = None
    reported_model: str | None = None


class CommandFailedError(subprocess.CalledProcessError):
    """CalledProcessError with captured stderr/stdout included in the message."""

    def __str__(self) -> str:
        base = super().__str__()
        detail = self.stderr or self.output or ""
        if not detail:
            return base
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        detail = detail.strip()
        if not detail:
            return base
        if len(detail) > _MAX_ERROR_DETAIL_LEN:
            detail = detail[:_MAX_ERROR_DETAIL_LEN] + "\n... (truncated)"
        return f"{base}\n\n--- stderr/stdout ---\n{detail}"


def _with_available_own_command(command: Sequence[str]) -> Sequence[str]:
    """自有命令名在当前 PATH 上不存在时，换成一个可用的自有名字。

    覆盖「改动已合并、可编辑安装尚未重装」的窗口：仓库配置里写的是 ``kc``，
    虚拟环境里却还只有 ``iar``。只替换 argv[0]，其余参数逐字不动；argv[0]
    不是自有名字（``git`` / ``gh`` / agent CLI），或本来就能找到时原样返回。

    Args:
        command: 待执行的命令与参数。

    Returns:
        Sequence[str]: 可能被换名的命令列表。
    """
    command_parts = list(command)
    if not command_parts or not product_identity.is_own_command_name(command_parts[0]):
        return command_parts
    if shutil.which(str(command_parts[0])) is not None:
        return command_parts
    for own_command_name in product_identity.OWN_COMMAND_NAMES:
        if shutil.which(own_command_name) is not None:
            command_parts[0] = own_command_name
            return command_parts
    return command_parts


def _observe_exception_output(
    exc: BaseException,
    usage_collector: StreamUsageCollector | None,
) -> None:
    """从 subprocess 超时/失败异常里尽力采集尚未返回的原始输出。"""
    if usage_collector is None:
        return
    for attribute_name in ("output", "stdout", "stderr", "partial_stdout", "partial_stderr"):
        output_value = getattr(exc, attribute_name, None)
        if isinstance(output_value, bytes):
            output_value = output_value.decode("utf-8", errors="replace")
        if isinstance(output_value, str):
            usage_collector.observe_text(output_value)


class SubprocessRunner:
    """Run commands using the subprocess module.

    Implements the ``IProcessRunner`` interface from
    ``backend.core.shared.interfaces.agent_runner`` via duck typing.
    """

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        timeout: int | None = None,
        inactivity_timeout: int | None = None,
        capture_output: bool = True,
        input_text: str | None = None,
        label: str | None = None,
        output_sink: Callable[[str], None] | None = None,
        output_protocol: str | None = None,
        env_profile: str | None = None,
        env_allow_extra: Sequence[str] = (),
        attempt_key: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> CommandResult:
        """Run a subprocess and capture output.

        Args:
            command: Command and arguments to execute.
            cwd: Working directory for the subprocess.
            check: Raise CommandFailedError when return code is non-zero.
            timeout: Optional wall-clock timeout in seconds.
            inactivity_timeout: Optional timeout in seconds since the last
                stdout/stderr output. Useful for detecting hung agents that
                keep the process alive without producing data.
            capture_output: Capture stdout/stderr instead of streaming.
            input_text: Optional text to feed via stdin.
            label: Optional label for heartbeat/timeout logs.
            output_sink: Optional callback for streamed output chunks. When
                provided for a streaming command (Claude ``stream-json`` or any
                non-captured command), rendered text is routed to the sink
                instead of the shared stdout, so parallel Issue runs can keep
                each agent's output in its own panel/log without interleaving.
            output_protocol: agent 调用路径专用的输出协议 id。非 plain 协议
                路由到对应的结构化流式
                渲染执行器，取代旧版对命令行内容的嗅探；``None`` /
                ``"plain"`` 走通用路径。
            env_profile: 子进程环境变量档名。``None``（默认）走 denylist
                净化档：以 :func:`build_sanitized_child_env` 组装子进程
                环境——剔除会话私有变量（如 ``SERVER__PORT``），其余变量
                原样透传；默认档不存在「全量继承 os.environ」的语义
                （Issue #230：内容生成路径曾因全量继承被会话私有变量中毒）。
                ``E2E_CHILD_ENV_PROFILE`` 时按 child_env 白名单构造子进程
                环境（runner 凭据不可见），并要求 ``capture_output=True``
                且 ``timeout`` 非空、``output_protocol`` 为 plain——否则
                白名单+进程树击杀的安全前提不成立，直接报错而非静默降级。
            env_allow_extra: E2E 档下追加放行的变量名（配置 ``env_allow``）。
            attempt_key: 活跃 attempt 登记键。非 ``None`` 时把这次 ``Popen`` 登记进
                :mod:`backend.infrastructure.attempt_process_registry`，供停滞监督
                当场复核归属并精确取消（Issue #256 FR-6/FR-7）；``None``（默认）
                不登记，行为与本特性之前一致。
            cancel_check: 外部取消探针（Issue #278 人工接管）。非 ``None`` 时在
                watchdog 的秒级 tick 上轮询，返回 ``True`` 即终止该子进程自己的
                进程组并抛 ``InterruptedError``；要求 ``capture_output=True`` 且带
                ``timeout``，否则报错、不静默降级。探针抛错按"未取消"继续。
        """
        command = _with_available_own_command(command)
        started_mono: float = time.monotonic()
        if env_profile is not None:
            child_env: dict[str, str] = _resolve_profiled_child_env(
                env_profile,
                env_allow_extra,
                capture_output=capture_output,
                timeout=timeout,
                output_protocol=output_protocol,
            )
        else:
            # 默认档也净化：env 构造收敛在本方法一处，新增 denylist 变量
            # 无需改动任何调用点。
            child_env = build_sanitized_child_env()
        if cancel_check is not None and not (capture_output and timeout is not None):
            raise ValueError(
                "cancel_check requires capture_output=True and a wall-clock timeout: "
                "cancellation is enforced by the watchdog that only exists on the "
                "captured-with-timeout path. Refusing to silently ignore the probe "
                "(a takeover request would otherwise never reach the process)."
            )
        usage_collector: StreamUsageCollector | None = None
        if output_protocol is not None:
            usage_collector = StreamUsageCollector()
        if output_protocol == CLAUDE_STREAM_JSON_PROTOCOL_ID:
            completed = run_filtered_claude_stream(
                command,
                cwd=cwd,
                timeout=timeout,
                inactivity_timeout=inactivity_timeout,
                collect_stdout=True,
                label=label,
                output_sink=output_sink,
                usage_collector=usage_collector,
                env=child_env,
                attempt_key=attempt_key,
            )
            stdout = completed.stdout
            stderr = completed.stderr
        elif input_text is not None:
            # stdin 投递也走 Popen：只有拿到真实句柄，停滞监督才有"当场可复核的
            # 进程归属"可言。语义与原先的 ``subprocess.run`` 保持一致——捕获式输出、
            # 只有 wall-clock 超时，不新增无输出超时（那会误杀长时间静默思考的 agent）。
            completed = _run_captured_process(
                command,
                cwd=cwd,
                timeout=timeout,
                label=label,
                env=child_env,
                attempt_key=attempt_key,
                input_text=input_text,
                usage_collector=usage_collector,
                cancel_check=cancel_check,
            )
            stdout = completed.stdout
            stderr = completed.stderr
        elif capture_output and timeout is not None:
            completed = _run_captured_process(
                command,
                cwd=cwd,
                timeout=timeout,
                inactivity_timeout=inactivity_timeout,
                label=label,
                env=child_env,
                attempt_key=attempt_key,
                usage_collector=usage_collector,
                cancel_check=cancel_check,
            )
            stdout = completed.stdout
            stderr = completed.stderr
        elif capture_output:
            try:
                completed = subprocess.run(
                    list(command),
                    cwd=cwd,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    env=child_env,
                )
            except BaseException as exc:
                _observe_exception_output(exc, usage_collector)
                _attach_stream_observations(exc, usage_collector)
                raise
            stdout = completed.stdout
            stderr = completed.stderr
        elif _PTY_AVAILABLE:
            # Stream a non-Claude command (kimi / codex) under a PTY so it
            # line-buffers and shows live progress instead of going silent.
            completed = _run_pty_stream(
                command,
                cwd=cwd,
                timeout=timeout,
                inactivity_timeout=inactivity_timeout,
                label=label,
                output_sink=output_sink,
                env=child_env,
                attempt_key=attempt_key,
                usage_collector=usage_collector,
            )
            stdout = completed.stdout
            stderr = completed.stderr
        else:
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_OWN_PROCESS_GROUP_KWARGS,
            )
            watchdog = _ProcessWatchdog(
                process,
                command,
                timeout=timeout,
                inactivity_timeout_seconds=inactivity_timeout,
                heartbeat_seconds=_COMMAND_HEARTBEAT_SECONDS,
                base_label="Command",
                context_label=label,
                attempt_key=attempt_key,
            )
            watchdog.start()
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []
            try:
                if process.stdout is not None:
                    for line in process.stdout:
                        watchdog.note_output()
                        if usage_collector is not None:
                            usage_collector.observe_line(line)
                        if output_sink is not None:
                            output_sink(line)
                        else:
                            timestamped = format_timestamped_line(line)
                            print(timestamped, end="", flush=True)
                        logger.info("%s", line.rstrip("\n"))
                        stdout_lines.append(line)
                if process.stderr is not None:
                    for line in process.stderr:
                        watchdog.note_output()
                        if usage_collector is not None:
                            usage_collector.observe_line(line)
                        if output_sink is not None:
                            output_sink(line)
                        else:
                            timestamped = format_timestamped_line(line)
                            print(timestamped, end="", file=sys.stderr, flush=True)
                        logger.warning("%s", line.rstrip("\n"))
                        stderr_lines.append(line)
                return_code = process.wait(timeout=timeout)
                watchdog.raise_if_timed_out(
                    partial_stdout="".join(stdout_lines),
                    partial_stderr="".join(stderr_lines),
                )
            except BaseException as exc:
                # BaseException 而不是 Exception：Ctrl-C（KeyboardInterrupt）也必须
                # 拆掉整个进程组，否则子进程会变成孤儿继续跑。
                _attach_stream_observations(exc, usage_collector)
                _terminate_process_tree(process)
                process.wait()
                raise
            finally:
                watchdog.stop()
            stdout = "".join(stdout_lines)
            stderr = "".join(stderr_lines)
            completed = subprocess.CompletedProcess(
                args=list(command),
                returncode=return_code,
                stdout=stdout,
                stderr=stderr,
            )
        if usage_collector is not None and output_protocol != CLAUDE_STREAM_JSON_PROTOCOL_ID:
            usage_collector.observe_text(stdout)
            usage_collector.observe_text(stderr)
        token_usage = usage_collector.usage if usage_collector is not None else None
        reported_model = usage_collector.reported_model if usage_collector is not None else None
        if (
            output_protocol is not None
            and output_protocol != CLAUDE_STREAM_JSON_PROTOCOL_ID
            and (token_usage is None or reported_model is None)
        ):
            # agent 调用走通用执行路径（kimi / codex 等 plain / PTY）：stdout
            # 未被渲染改写，事后容错解析用量与执行器自报模型；普通命令
            # （output_protocol=None，git / gh / 验证命令）不解析，避免无谓扫描。
            if token_usage is None:
                token_usage = parse_usage_from_plain_stdout(stdout)
            if reported_model is None:
                reported_model = parse_reported_model_from_plain_stdout(stdout)
        result = CommandResult(
            command=tuple(command),
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(time.monotonic() - started_mono, 3),
            output_protocol=output_protocol or PLAIN_PROTOCOL_ID,
            token_usage=token_usage,
            session_id=usage_collector.session_id if usage_collector is not None else None,
            reported_model=reported_model,
        )
        if check and completed.returncode != 0:
            failure = CommandFailedError(
                completed.returncode,
                list(command),
                output=stdout,
                stderr=stderr,
            )
            # 非零退出的 agent 调用同样可能已经聊出了一段会话（跑了一半才失败）。
            # 把击杀/失败前的最后一个会话 id 与自报模型一并挂在异常上，恢复轮次
            # 才有得可续，调用观测也才能诚实记录"执行器报告了什么"。
            _attach_stream_observations(failure, usage_collector)
            raise failure
        return result

    def probe_live_attempt(self, attempt_key: str) -> AttemptOwnership:
        """读取一次活跃 attempt 的进程归属证据（只读，绝不触碰任何进程）。

        证据口径见 :mod:`backend.infrastructure.attempt_process_registry`：在册、
        仍存活、实时进程组与登记值一致三项全过才 ``confirmed=True``。未走 ``Popen``
        的调用（例如普通 ``subprocess.run`` 命令）不在册，一律是"归属不可证实"。
        """
        return probe_attempt_ownership(attempt_key=attempt_key, host_label=socket.gethostname())

    def cancel_live_attempt(
        self, attempt_key: str, expected: AttemptOwnership
    ) -> StallCancelOutcome:
        """按登记身份精确终止一个活跃 attempt 的进程组。

        Args:
            attempt_key: 待终止的 attempt 键。
            expected: 调用方在动手前**重新读取**的所有权证据。

        Returns:
            :class:`StallCancelOutcome`：身份不符时未触碰任何进程并说明原因。
        """
        return terminate_attempt(attempt_key=attempt_key, expected=expected)


def _run_captured_process(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int | None,
    inactivity_timeout: int | None = None,
    label: str | None = None,
    env: dict[str, str] | None = None,
    attempt_key: str | None = None,
    input_text: str | None = None,
    usage_collector: StreamUsageCollector | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a captured subprocess with heartbeat and optional inactivity logging.

    Args:
        command: Command and arguments to execute.
        cwd: Working directory for the subprocess.
        timeout: Wall-clock timeout in seconds；``None`` 表示不设 wall-clock 上限
            （与 ``subprocess.run(timeout=None)`` 同语义）。
        inactivity_timeout: Optional no-output timeout in seconds.
        label: Optional label for heartbeat/timeout logs.
        env: 已构造好的子进程环境，正常由 :meth:`SubprocessRunner.run` 的
            默认净化档或 E2E 白名单档传入（避免同一环境重复构造）；``None``
            时沿用 ``subprocess`` 的父环境继承语义，绕过 ``run()`` 直接调用
            本函数的调用方需自行保证环境已净化。
        attempt_key: 活跃 attempt 登记键（停滞监督的所有权证据来源）。
        input_text: 经 stdin 投递的文本。传入时走 ``communicate(input=...)``
            ——与 :meth:`SubprocessRunner.run` 原先用 ``subprocess.run`` 投递
            stdin 的语义一致（捕获式、无输出活动跟踪）。
        usage_collector: 可选 agent 输出旁路观测器；捕获输出在返回或超时前解析。
        cancel_check: 人工接管取消探针，由 watchdog 秒级 tick 轮询；命中即
            终止进程组并在捕获返回后抛 InterruptedError（Issue #278）。
    """
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **_OWN_PROCESS_GROUP_KWARGS,
    )
    watchdog = _ProcessWatchdog(
        process,
        command,
        timeout=timeout,
        inactivity_timeout_seconds=inactivity_timeout,
        heartbeat_seconds=_COMMAND_HEARTBEAT_SECONDS,
        base_label="Command",
        context_label=label,
        attempt_key=attempt_key,
        cancel_check=cancel_check,
    )
    watchdog.start()
    try:
        if input_text is not None or inactivity_timeout is None:
            stdout, stderr = process.communicate(input=input_text)
        else:
            stdout, stderr = _communicate_with_activity_tracking(process, watchdog)
            process.wait()
        if usage_collector is not None:
            usage_collector.observe_text(stdout)
            usage_collector.observe_text(stderr)
        watchdog.raise_if_timed_out(partial_stdout=stdout, partial_stderr=stderr)
        watchdog.raise_if_cancelled(partial_stdout=stdout, partial_stderr=stderr)
    except BaseException as exc:
        _observe_exception_output(exc, usage_collector)
        _attach_stream_observations(exc, usage_collector)
        _terminate_process_tree(process)
        process.wait()
        raise
    finally:
        watchdog.stop()
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _communicate_with_activity_tracking(
    process: subprocess.Popen[str],
    watchdog: _ProcessWatchdog,
) -> tuple[str, str]:
    """Read stdout/stderr while resetting the inactivity timeout on each chunk."""
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _pump_stdout() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            watchdog.note_output()
            stdout_lines.append(line)

    def _pump_stderr() -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            watchdog.note_output()
            stderr_lines.append(line)

    stdout_thread = threading.Thread(target=_pump_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_pump_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    return "".join(stdout_lines), "".join(stderr_lines)


class _ProcessWatchdog:
    """Log long-running subprocess heartbeats and enforce timeouts.

    Supports both a wall-clock timeout and an inactivity (no-output)
    timeout. The inactivity timeout resets whenever the watched process
    produces stdout or stderr data.
    """

    def __init__(
        self,
        process: subprocess.Popen[str],
        command: Sequence[str],
        *,
        timeout: int | None,
        inactivity_timeout_seconds: int | None = None,
        heartbeat_seconds: int,
        base_label: str,
        context_label: str | None = None,
        attempt_key: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self._process = process
        self._command = tuple(command)
        self._timeout = timeout
        self._inactivity_timeout = inactivity_timeout_seconds
        self._effective_timeout: int | None = timeout
        self._heartbeat_seconds = heartbeat_seconds
        self._base_label = base_label
        self._context_label = context_label
        self._attempt_key = attempt_key
        self._cancel_check = cancel_check
        self._cancelled = False
        self._cancel_check_error_logged = False
        self._started_at = time.monotonic()
        self._last_output_at = self._started_at
        self._output_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._timed_out = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Start the watchdog background thread."""
        if self._attempt_key is not None:
            # 活跃 attempt 的进程句柄进登记簿，停滞监督才能"当场再读一次归属"。
            # 标签只取命令名：完整 argv 里可能带提示词，绝不进任何日志与证据。
            register_live_attempt(
                attempt_key=self._attempt_key,
                process=self._process,
                label=str(self._command[0]) if self._command else "command",
                terminator=lambda expected_group, expected_started_at: _terminate_process_tree(
                    self._process,
                    expected_process_group=expected_group,
                    expected_process_started_at=expected_started_at,
                ),
            )
        self._thread.start()

    def stop(self) -> None:
        """Stop the watchdog and wait briefly for it to exit."""
        self._stop_event.set()
        self._thread.join(timeout=1)
        if self._attempt_key is not None:
            unregister_live_attempt(
                attempt_key=self._attempt_key,
                pid=self._process.pid,
            )

    def note_output(self) -> None:
        """Reset the inactivity timeout clock after observing output."""
        with self._output_lock:
            self._last_output_at = time.monotonic()

    def raise_if_timed_out(
        self,
        *,
        partial_stdout: str | None = None,
        partial_stderr: str | None = None,
    ) -> None:
        """Raise TimeoutExpired when the watchdog killed the process.

        杀进程之前读到的输出必须跟着异常一起往上走。捕获模式下调用方拿不到
        任何流式输出，如果这里把已收集的 stdout/stderr 丢掉，一次超时就等于
        "什么都没发生"——比如 verifier agent 跑了半小时被杀，操作者只能看到
        一条 "timed out"，看不到它当时判到哪一步。
        """
        if self._timed_out:
            raise subprocess.TimeoutExpired(
                cmd=list(self._command),
                timeout=self._effective_timeout,
                output=partial_stdout,
                stderr=partial_stderr,
            )

    def raise_if_cancelled(
        self,
        *,
        partial_stdout: str | None = None,
        partial_stderr: str | None = None,
    ) -> None:
        """人工接管取消时抛 InterruptedError（Issue #278）。

        绝不复用 TimeoutExpired：超时会被上层归类为 verifier 超时并触发
        候选回退/重试，而"人被接管"必须原样上抛、不重试、不换 agent。
        """
        if self._cancelled:
            raise InterruptedError(
                f"Terminated by takeover cancel request: {_summarize_command(self._command)}"
                + (f"\npartial stdout: {partial_stdout}" if partial_stdout else "")
                + (f"\npartial stderr: {partial_stderr}" if partial_stderr else "")
            )

    def _format_label(self) -> str:
        """Return the log label, optionally appending the context label."""
        if self._context_label:
            return f"{self._base_label} ({self._context_label})"
        return self._base_label

    def _check_timeouts(self, elapsed_seconds: int) -> bool:
        """Return True if a timeout fired and the process was killed."""
        if self._timeout is not None and elapsed_seconds >= self._timeout:
            self._timed_out = True
            self._effective_timeout = self._timeout
            label = self._format_label()
            logger.error(
                "%s timed out after %ds; terminating: %s",
                label,
                elapsed_seconds,
                _summarize_command(self._command),
            )
            _terminate_process_tree(self._process)
            return True
        if self._inactivity_timeout is not None:
            with self._output_lock:
                inactive_seconds = int(time.monotonic() - self._last_output_at)
            if inactive_seconds >= self._inactivity_timeout:
                self._timed_out = True
                self._effective_timeout = self._inactivity_timeout
                label = self._format_label()
                logger.error(
                    "%s inactive for %ds; terminating: %s",
                    label,
                    inactive_seconds,
                    _summarize_command(self._command),
                )
                _terminate_process_tree(self._process)
                return True
        return False

    def _check_takeover_cancel(self) -> bool:
        """轮询人工接管探针；命中则终止整个进程组并返回 True（Issue #278）。

        探针本身抛错（如旁路 SQLite 瞬时锁失败）按"未取消"继续跑：取消是
        高代价动作，绝不能因一次读库失败就杀掉正在工作的 verifier 进程。
        抛错只记一次日志，避免每秒刷屏。
        """
        if self._cancel_check is None or self._cancelled:
            return False
        try:
            if not self._cancel_check():
                return False
        except Exception as exc:  # noqa: BLE001 - probe failure must not kill the process.
            if not self._cancel_check_error_logged:
                self._cancel_check_error_logged = True
                logger.warning(
                    "%s cancel_check probe failed; treating as not cancelled: %s",
                    self._format_label(),
                    exc,
                )
            return False
        self._cancelled = True
        logger.error(
            "%s takeover cancel requested; terminating: %s",
            self._format_label(),
            _summarize_command(self._command),
        )
        _terminate_process_tree(self._process)
        return True

    def _run(self) -> None:
        next_heartbeat_at = self._heartbeat_seconds
        while not self._stop_event.wait(timeout=1):
            if self._process.poll() is not None:
                return
            elapsed_seconds = int(time.monotonic() - self._started_at)
            if elapsed_seconds >= next_heartbeat_at:
                label = self._format_label()
                logger.info(
                    "%s still running after %ds: %s",
                    label,
                    elapsed_seconds,
                    _summarize_command(self._command),
                )
                next_heartbeat_at += self._heartbeat_seconds
            if self._check_timeouts(elapsed_seconds):
                return
            if self._check_takeover_cancel():
                return


def _summarize_command(command: Sequence[str]) -> str:
    """Return a compact command string safe for logs."""
    command_text = " ".join(str(part) for part in command)
    if len(command_text) <= 240:
        return command_text
    return f"{command_text[:237]}..."

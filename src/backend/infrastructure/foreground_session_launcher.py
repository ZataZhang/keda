"""原生执行器会话的前台启动实现（infrastructure 层）。

:class:`backend.core.shared.interfaces.agent_session.IForegroundSessionLauncher`
的实现端。这里承担三份只有真正碰进程才需要处理的细节：

1. **终端归属**：子进程与 KC 处在同一进程组，因此终端的前台信号（Ctrl-C /
   Ctrl-\\）直接送到 provider，KC 不抢先处理；KC 在等待期间忽略这两个信号，
   否则 provider 的全屏界面会被半路拆掉。
2. **信号转发**：SIGTERM / SIGHUP 不是终端前台信号，KC 收到后原样转给
   provider，再按 shell 约定以 ``128 + signal`` 结束。
3. **环境组装**：复用 runner 侧同一份净化档（剔除交互式会话私有的
   ``SERVER__PORT`` 等变量），provider 因此和无人值守调用看到一致的环境。

输出**不捕获**：stdin/stdout/stderr 全部继承，provider 因此拿到真实 TTY。
"""

from __future__ import annotations

import signal
import subprocess

from backend.core.shared.interfaces.agent_session import (
    IForegroundSessionLauncher,
)
from backend.core.shared.models.agent_session import NativeSessionPlan, NativeSessionResult
from backend.infrastructure.child_env import build_sanitized_child_env
from backend.infrastructure.logging.logger import logger

#: provider 被信号杀死时，退出码按 shell 约定折算的偏移量。
_SIGNAL_EXIT_CODE_OFFSET = 128


class SubprocessForegroundSessionLauncher(IForegroundSessionLauncher):
    """以继承当前终端的方式启动 provider 原生界面。"""

    def launch(self, session_plan: NativeSessionPlan) -> NativeSessionResult:
        """前台启动一次原生会话，阻塞到 provider 退出并交回其退出码。

        Args:
            session_plan: 由声明式 interactive profile 组装的启动计划。

        Returns:
            NativeSessionResult: 退出码逐字节反映 provider 的结束状态。

        Raises:
            RuntimeError: 可执行文件缺失或无法启动。错误消息指名二进制与目录，
                调用方据此提示安装或改配置，绝不回退到非交互执行路径。
        """
        executable = session_plan.argv[0] if session_plan.argv else ""
        if not executable:
            raise RuntimeError(
                f"Agent '{session_plan.agent_name}' resolved to an empty argv; "
                "nothing can be launched. Check [agent_runner.agents."
                f"{session_plan.agent_name}] configuration."
            )
        try:
            child_process = subprocess.Popen(
                list(session_plan.argv),
                cwd=str(session_plan.cwd),
                env=build_sanitized_child_env(),
            )
        except FileNotFoundError as file_not_found_exc:
            raise RuntimeError(
                f"Agent '{session_plan.agent_name}' binary {executable!r} was not found "
                f"on PATH (cwd={session_plan.cwd}). Install it, or point "
                f"[agent_runner.agents.{session_plan.agent_name}] at the real executable; "
                "`kc agent doctor --agent <name>` shows the resolved command line."
            ) from file_not_found_exc
        except OSError as os_exc:
            raise RuntimeError(
                f"Failed to launch agent '{session_plan.agent_name}' "
                f"({executable!r}) in {session_plan.cwd}: {os_exc}"
            ) from os_exc
        exit_code = self._wait_with_signal_policy(child_process)
        return NativeSessionResult(
            agent_name=session_plan.agent_name,
            argv=tuple(session_plan.argv),
            exit_code=exit_code,
        )

    def _wait_with_signal_policy(self, child_process: subprocess.Popen) -> int:
        """等待 provider 退出，期间按终端语义处理信号。

        Args:
            child_process: 已启动的 provider 进程。

        Returns:
            交给 ``kc`` 自身的退出码：provider 正常退出为其返回码，被信号杀死为
            ``128 + signal``，父进程收到转发信号时同样按该约定结束。
        """
        forwarded_signals: list[int] = []
        original_handlers = {
            signal.SIGINT: signal.signal(signal.SIGINT, signal.SIG_IGN),
            signal.SIGQUIT: signal.signal(signal.SIGQUIT, signal.SIG_IGN),
        }

        def _forward_parent_signal(signal_number: int, _frame: object) -> None:
            forwarded_signals.append(signal_number)
            try:
                child_process.send_signal(signal_number)
            except ProcessLookupError:
                # provider 已经没了：不需要再做任何事，等待循环会立刻返回。
                return

        for terminal_signal in (signal.SIGTERM, signal.SIGHUP):
            try:
                original_handlers[terminal_signal] = signal.signal(
                    terminal_signal, _forward_parent_signal
                )
            except (OSError, ValueError):
                # 非主线程（例如被 console/测试宿主调用）无法安装信号处理器：
                # 保持 provider 自行结束，不因此拒绝启动。
                logger.debug(
                    "signal handler for %s unavailable while launching native session",
                    terminal_signal,
                )
        try:
            returncode = child_process.wait()
        finally:
            for installed_signal, previous_handler in original_handlers.items():
                try:
                    signal.signal(installed_signal, previous_handler)
                except (OSError, ValueError):
                    continue
        if returncode < 0:
            return _SIGNAL_EXIT_CODE_OFFSET + -returncode
        if forwarded_signals:
            # 父进程收到的信号最终由 provider 消化：按 shell 约定折算，
            # 让调用方（脚本 / CI）看到「被信号中断」而不是成功。
            return _SIGNAL_EXIT_CODE_OFFSET + forwarded_signals[-1]
        return returncode


__all__ = ["SubprocessForegroundSessionLauncher"]

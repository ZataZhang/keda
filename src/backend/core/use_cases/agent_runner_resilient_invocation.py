"""Agent 调用的会话续传与瞬时错误重试策略。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import AppConfig, CommandResult, IssueSummary


def run_agent_with_prompt_resilient(
    agent_name: str,
    prompt: str,
    worktree_path: Path,
    process_runner: IProcessRunner,
    *,
    transient_retry_attempts: int = 2,
    transient_retry_delay_seconds: int = 10,
    **agent_call_options: object,
) -> CommandResult:
    """Run an agent, retrying transient network/transport failures in place.

    Agent process timeouts first use the configured per-session budget to resume
    the same CLI session with one more phase-sized time window. Only
    :func:`is_transient_failure` transport errors (dropped sockets, connection
    resets, gateway timeouts, 5xx) use the separate in-place retry budget. A
    missing agent CLI is surfaced as :class:`AgentUnavailableError` so
    orchestration can skip to the next agent; every other error propagates
    unchanged to recovery or cross-agent fallback.

    ``model_selection``（若传入）先经 :func:`drop_model_selection_for_agent`
    校验：跨 agent 回退或显式 ``--agent`` 换人时丢弃绑定并记日志，本函数内
    的原地瞬态重试不换 agent，绑定保持有效。

    Args:
        agent_name: Agent to invoke (claude / codex / kimi).
        prompt: Prepared prompt text.
        worktree_path: Worktree the agent runs in.
        process_runner: Command executor.
        transient_retry_attempts: Extra retries granted to transient failures.
        transient_retry_delay_seconds: Backoff between transient retries.
        agent_call_options: 原样透传给 :func:`run_agent_with_prompt` 的关键字参数
            （`config` / `capture_output` / `timeout_seconds` /
            `inactivity_timeout_seconds` / `issue` / `profile` /
            `model_selection` / `cancel_check`），两个入口共用同一份参数契约，
            避免逐字段重复声明而漂移。

    Returns:
        The successful :class:`CommandResult`.

    Raises:
        AgentUnavailableError: The agent CLI could not be launched.
        Exception: The original error when it is not transient or retries are
            exhausted.
    """
    from backend.core.use_cases import run_agent_once as run_agent_once_module

    max_retries = max(0, transient_retry_attempts)
    forwarded_options = dict(agent_call_options)
    raw_model_selection = forwarded_options.get("model_selection")
    if raw_model_selection is not None:
        forwarded_options["model_selection"] = run_agent_once_module.drop_model_selection_for_agent(
            agent_name,
            raw_model_selection if isinstance(raw_model_selection, ModelSelection) else None,
        )
    agent_call_issue = forwarded_options.get("issue")
    issue_number = agent_call_issue.number if isinstance(agent_call_issue, IssueSummary) else 0
    agent_config = forwarded_options.get("config")
    if not isinstance(agent_config, AppConfig):
        agent_config = AppConfig()
    raw_phase = forwarded_options.get("invocation_phase", run_agent_once_module.PHASE_UNSPECIFIED)
    session_scope = run_agent_once_module.AgentSessionScope(
        issue_number=(
            agent_call_issue.number if isinstance(agent_call_issue, IssueSummary) else None
        ),
        phase=raw_phase if isinstance(raw_phase, str) else run_agent_once_module.PHASE_UNSPECIFIED,
    )
    current_prompt = prompt
    for retry_index in range(max_retries + 1):
        if retry_index > 0:
            # 原地瞬态重试是**新的一次实际进程调用**：新建 invocation 并用 retry_of
            # 关联上一次，绝不合并成一条记录（否则"重试了三次才成功"会消失）。
            run_agent_once_module.link_next_invocation(run_agent_once_module.RETRY_REASON_TRANSIENT)
        while True:
            try:
                return run_agent_once_module.run_agent_with_prompt(
                    agent_name,
                    current_prompt,
                    worktree_path,
                    process_runner,
                    **forwarded_options,
                )
            except FileNotFoundError as exc:
                raise run_agent_once_module.AgentUnavailableError(agent_name) from exc
            except subprocess.TimeoutExpired as exc:
                requested_session_id = forwarded_options.get("resume_session_id")
                timeout_session_id = run_agent_once_module._session_id_from_exception(exc)
                if timeout_session_id is None and isinstance(requested_session_id, str):
                    # 已续传的进程可能在再次上报 session id 前超时，仍可接着请求中的 id 续跑。
                    timeout_session_id = requested_session_id
                consumed_attempts = (
                    run_agent_once_module.consume_agent_timeout_resume_attempt(
                        worktree_path,
                        config=agent_config,
                        agent_name=agent_name,
                        session_id=timeout_session_id,
                        scope=session_scope,
                        max_attempts=agent_config.runner.timeout_resume_attempts,
                    )
                    if timeout_session_id is not None
                    else None
                )
                if consumed_attempts is None:
                    raise
                run_agent_once_module._logger.warning(
                    "Agent '%s' timed out for Issue #%d; resuming session with an extra "
                    "time window (%d/%d).",
                    agent_name,
                    issue_number,
                    consumed_attempts,
                    agent_config.runner.timeout_resume_attempts,
                )
                run_agent_once_module.link_next_invocation(
                    run_agent_once_module.RETRY_REASON_TIMEOUT_RESUME
                )
                current_prompt = run_agent_once_module._TIMEOUT_CONTINUATION_PROMPT
                forwarded_options["resume_session_id"] = timeout_session_id
            except InterruptedError:
                # 人工接管取消（Issue #278）：InterruptedError 是 OSError 子类，
                # 绝不进瞬时重试——人被接管后原地重试会把刚杀的 verifier 又拉起来。
                raise
            except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
                if retry_index >= max_retries or not run_agent_once_module.is_transient_failure(
                    exc
                ):
                    raise
                run_agent_once_module._logger.warning(
                    "Transient error from agent '%s' for Issue #%d; retrying (%d/%d): %s",
                    agent_name,
                    issue_number,
                    retry_index + 1,
                    max_retries,
                    exc,
                )
                run_agent_once_module.wait_before_recovery_attempt(
                    issue_number,
                    recovery_attempt=retry_index + 1,
                    max_recovery_attempts=max_retries,
                    delay_seconds=transient_retry_delay_seconds,
                )
                break
    raise RuntimeError("unreachable: resilient agent retry loop exited")

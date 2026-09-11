"""Agent Runner content generators.

Holds :class:`SubprocessContentGenerator`,
:class:`SafePlannerContentGenerator`. Extracted out of
:mod:`backend.engines.agent_runner.factory` so the content-side and
repository-side concerns can live in separate files.

命令构造已全部收敛到 :func:`build_agent_invocation`：只读用途映射到
profile ``"generate"``，REPL 用途映射到 profile ``"repl"``；planner 的
只读门禁读注册表 spec 的 ``read_only`` 字段，不再枚举 agent 名。
"""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.interfaces.agent_output_protocol import (
    PLAIN_PROTOCOL_ID,
    IAgentOutputProtocolRegistry,
    OutputRelayRequest,
)
from backend.core.shared.interfaces.agent_runner import IContentGenerator
from backend.core.shared.models.agent_runner import AppConfig, CommandResult
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_GENERATE,
    AGENT_PROFILE_REPL,
    PROMPT_DELIVERY_STDIN,
)
from backend.core.use_cases.agent_invocation import (
    build_agent_invocation,
    resolve_profile_spec,
)
from backend.infrastructure.process_runner import SubprocessRunner


class SubprocessContentGenerator(IContentGenerator):
    """Generate content via a read-only local agent subprocess.

    Implements ``IContentGenerator`` via duck typing.
    """

    def __init__(
        self,
        process_runner: SubprocessRunner,
        *,
        read_only: bool = True,
        config: AppConfig | None = None,
        protocol_registry: IAgentOutputProtocolRegistry | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._read_only = read_only
        self._config = config or AppConfig()
        if protocol_registry is None:
            from backend.engines.agent_runner.output_protocols import (
                get_output_protocol_registry,
            )

            protocol_registry = get_output_protocol_registry()
        self._protocol_registry = protocol_registry

    def generate(
        self,
        agent_name: str,
        prompt: str,
        *,
        cwd: Path,
        timeout: int | None = None,
    ) -> CommandResult:
        """Run a content generator and return its output.

        When the instance was constructed with ``read_only=True`` (the
        default) the invocation uses the agent's ``generate`` profile
        (declared read-only). The REPL entrypoint constructs the
        generator with ``read_only=False`` so the ``repl`` profile is
        used, allowing file mutations inside the user's confirmation
        model.
        """
        profile = AGENT_PROFILE_REPL if not self._read_only else AGENT_PROFILE_GENERATE
        invocation = build_agent_invocation(agent_name, profile, prompt, cwd, self._config)
        if invocation.output_protocol != PLAIN_PROTOCOL_ID:
            protocol = self._protocol_registry.resolve(invocation.output_protocol)
            return protocol.relay(
                OutputRelayRequest(
                    argv=invocation.argv,
                    cwd=cwd,
                    prompt_text=prompt
                    if invocation.prompt_delivery == PROMPT_DELIVERY_STDIN
                    else None,
                    prompt_delivery=invocation.prompt_delivery,
                    timeout=timeout,
                    collect_stdout=True,
                )
            )
        return self._process_runner.run(
            list(invocation.argv),
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
            input_text=prompt if invocation.prompt_delivery == PROMPT_DELIVERY_STDIN else None,
            output_protocol=invocation.output_protocol,
        )


class SafePlannerContentGenerator(IContentGenerator):
    """Generate decision plans via a local agent subprocess.

    The planner runs the agent's ``generate`` profile, which the registry
    must declare ``read_only``——这是只读决策入口（planner / ``iar ask``）
    的 fail-fast 门禁：spec 未声明只读时拒绝启动 agent。Callers are
    responsible for validating and sandboxing the resulting plan.
    """

    def __init__(
        self,
        process_runner: SubprocessRunner,
        *,
        config: AppConfig | None = None,
        protocol_registry: IAgentOutputProtocolRegistry | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._config = config or AppConfig()
        if protocol_registry is None:
            from backend.engines.agent_runner.output_protocols import (
                get_output_protocol_registry,
            )

            protocol_registry = get_output_protocol_registry()
        self._protocol_registry = protocol_registry

    def generate(
        self,
        agent_name: str,
        prompt: str,
        *,
        cwd: Path,
        timeout: int | None = None,
    ) -> CommandResult:
        """Run a planner agent and return its output.

        Raises:
            ValueError: 该 agent 的 ``generate`` profile 未声明
                ``read_only``，不允许作为只读 planner 启动。
        """
        profile_spec = resolve_profile_spec(agent_name, AGENT_PROFILE_GENERATE, self._config)
        if not profile_spec.read_only:
            raise ValueError(
                f"Agent '{agent_name}' profile '{AGENT_PROFILE_GENERATE}' is not declared "
                f"read_only in the agent registry; refusing to start it for read-only "
                f"decision planning. Declare read_only = true for this profile to allow it."
            )
        invocation = build_agent_invocation(
            agent_name, AGENT_PROFILE_GENERATE, prompt, cwd, self._config
        )
        if invocation.output_protocol != PLAIN_PROTOCOL_ID:
            protocol = self._protocol_registry.resolve(invocation.output_protocol)
            return protocol.relay(
                OutputRelayRequest(
                    argv=invocation.argv,
                    cwd=cwd,
                    prompt_text=prompt
                    if invocation.prompt_delivery == PROMPT_DELIVERY_STDIN
                    else None,
                    prompt_delivery=invocation.prompt_delivery,
                    timeout=timeout,
                    collect_stdout=True,
                )
            )
        return self._process_runner.run(
            list(invocation.argv),
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
            input_text=prompt if invocation.prompt_delivery == PROMPT_DELIVERY_STDIN else None,
            output_protocol=invocation.output_protocol,
        )


def create_planner_runner(
    process_runner: SubprocessRunner | None = None,
    *,
    config: AppConfig | None = None,
    protocol_registry: IAgentOutputProtocolRegistry | None = None,
) -> SafePlannerContentGenerator:
    """Create a safe planner runner instance."""
    return SafePlannerContentGenerator(
        process_runner or SubprocessRunner(),
        config=config,
        protocol_registry=protocol_registry,
    )


def create_content_generator(
    process_runner: SubprocessRunner | None = None,
    *,
    read_only: bool = True,
    config: AppConfig | None = None,
    protocol_registry: IAgentOutputProtocolRegistry | None = None,
) -> SubprocessContentGenerator:
    """Create a content generator instance."""
    return SubprocessContentGenerator(
        process_runner or SubprocessRunner(),
        read_only=read_only,
        config=config,
        protocol_registry=protocol_registry,
    )


__all__ = [
    "SafePlannerContentGenerator",
    "SubprocessContentGenerator",
    "create_content_generator",
    "create_planner_runner",
]

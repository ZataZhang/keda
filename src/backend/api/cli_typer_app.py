"""Typer app definition and shared option types.

Owns the :data:`app` instance, every sub-application (``labels_app``,
``issue_app``, ``registry_app``, ``daemon_app``, ``worktree_app``,
``workflow_app``, ``loop_app``, ``completion_app``), the shared
``Annotated[...]`` option types, and the small ``_run_typer_*`` /
``_typer_selector_options`` helpers used by every Typer command module
under :mod:`backend.api.cli_typer_*`.

The command implementations live in the per-domain modules
(:mod:`backend.api.cli_typer_init`, :mod:`backend.api.cli_typer_runner`,
…) and are imported for side effects at the bottom of this file so the
Typer decorators register against :data:`app` before :func:`main` is
called.
"""

from __future__ import annotations

import argparse
import os
import sys
from enum import Enum
from typing import Annotated, Any

import typer
from typer import _click as typer_click

from backend.api.cli import _run_parsed_command, error_console
from backend.api.cli_completion import register_completion_commands
from backend.api.cli_exit_codes import EXIT_CODE_HELP, ExitCode
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    CliError,
    OutputFormat,
    render_cli_error,
)
from backend.api.version_info import resolve_keda_version
from backend.core.shared.models import product_identity

__all__ = [
    "AllRepositoriesOption",
    "ConfigOption",
    "ConcurrencyOption",
    "DaemonIntervalOption",
    "IssueAgentChoice",
    "IssueTypeChoice",
    "JsonOutputOption",
    "LogsKindChoice",
    "MaxIssuesOption",
    "OutputFormat",
    "OutputOption",
    "RepoIdOption",
    "ModelIdOption",
    "ModelPresetOption",
    "ReasoningEffortOption",
    "RepoOption",
    "RunAgentChoice",
    "RunAgentOption",
    "_HELP_CONTEXT",
    "_app_callback",
    "_enum_value",
    "_run_typer_command",
    "_typer_preset_options",
    "_run_typer_repository_command",
    "_typer_selector_options",
    "app",
    "agent_app",
    "auth_app",
    "completion_app",
    "config_app",
    "container_app",
    "console_app",
    "daemon_app",
    "issue_app",
    "labels_app",
    "loop_app",
    "main",
    "preview_app",
    "registry_app",
    "backlog_app",
    "skill_app",
    "worktree_app",
    "workflow_app",
]


def _registered_agent_names() -> tuple[str, ...]:
    """已注册 agent 名（Typer 枚举的取值来源），配置加载失败回落内置默认。"""
    from backend.api.cli_parser_options import registered_agent_names

    return registered_agent_names()


def _build_agent_choice_enum(
    enum_name: str,
    *,
    prefix: tuple[str, ...],
    suffix: tuple[str, ...] = (),
) -> type[Enum]:
    """从 agent 注册表动态构建 ``--agent`` choices 枚举。

    取值集合 = 路由别名（auto / none）+ 注册表 agent 名；配置注册的
    新 agent 无需改代码即可出现在 ``--agent`` 的合法值里。
    """
    members = {name: name for name in (*prefix, *_registered_agent_names(), *suffix)}
    return Enum(enum_name, members, type=str)  # type: ignore[no-any-return]


RunAgentChoice = _build_agent_choice_enum("RunAgentChoice", prefix=("auto",))
"""Agent choices accepted by runner commands（注册表派生）."""

IssueAgentChoice = _build_agent_choice_enum("IssueAgentChoice", prefix=("auto",), suffix=("none",))
"""Agent labels accepted by issue creation commands（注册表派生）."""


class IssueTypeChoice(str, Enum):
    """Issue type labels accepted by PRD issue creation."""

    feature = "feature"
    refactor = "refactor"
    bug = "bug"


class LogsKindChoice(str, Enum):
    """Kind selector for ``kc logs``."""

    daemon = "daemon"
    review_daemon = "review_daemon"


_HELP_CONTEXT = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    name=product_identity.PRIMARY_COMMAND_NAME,
    help=f"{product_identity.PRODUCT_DISPLAY_NAME} CLI.\n\n{EXIT_CODE_HELP}",
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings=_HELP_CONTEXT,
)
labels_app = typer.Typer(
    help="Manage GitHub labels.", no_args_is_help=True, context_settings=_HELP_CONTEXT
)
issue_app = typer.Typer(
    help="Create and manage GitHub Issues.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
completion_app = typer.Typer(
    help="Manage shell completion.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
worktree_app = typer.Typer(
    help="Manage KedaCode-owned Git worktrees for the current repository.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
registry_app = typer.Typer(
    help="Manage the repository registry in config.toml.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
daemon_app = typer.Typer(
    help="Run the agent runner continuously or inspect daemon status.",
    no_args_is_help=False,
    context_settings=_HELP_CONTEXT,
)
workflow_app = typer.Typer(
    help="Install and manage bundled workflow templates.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
loop_app = typer.Typer(
    help="Register and manage recurring task generators.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
backlog_app = typer.Typer(
    help="Drive the backlog scheduler manually.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
config_app = typer.Typer(
    help="Maintain local state: move ~/.iar, rename .iar.toml.",  # legacy-alias
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
container_app = typer.Typer(
    help="Manage the KedaCode runner container (auth import, up, down, logs).",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
console_app = typer.Typer(
    help="Serve the bundled Agent Runner web console (API + panel).",
    no_args_is_help=False,
    context_settings=_HELP_CONTEXT,
)
agent_app = typer.Typer(
    help="Inspect registered agents and output protocols (read-only).",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
auth_app = typer.Typer(
    help="Manage the container-side authentication snapshot.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
skill_app = typer.Typer(
    help="Install and refresh the packaged and remote-template user-level Skills.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
preview_app = typer.Typer(
    help="Manage the on-demand local preview server for the current repository.",
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT,
)
container_app.add_typer(auth_app, name="auth")
app.add_typer(labels_app, name="labels")
app.add_typer(issue_app, name="issue")
app.add_typer(completion_app, name="completion")
register_completion_commands(completion_app)
app.add_typer(worktree_app, name="worktree")
app.add_typer(registry_app, name="registry")
app.add_typer(daemon_app, name="daemon")
app.add_typer(workflow_app, name="workflow")
app.add_typer(loop_app, name="loop")
app.add_typer(backlog_app, name="backlog")
app.add_typer(config_app, name="config")
app.add_typer(container_app, name="container")
app.add_typer(console_app, name="console")
app.add_typer(agent_app, name="agent")
app.add_typer(skill_app, name="skill")
app.add_typer(preview_app, name="preview")

RepoOption = Annotated[str | None, typer.Option("--repo", help="Target repository path.")]
RepoIdOption = Annotated[
    str | None, typer.Option("--repo-id", help="Target configured repository ID.")
]
#: 机读契约共享旗标：``--output {table,json}`` 为主形态，``--json`` 为等价别名。
#: 默认 ``table`` —— 机器模式必须显式声明（不做非 TTY 自动切 JSON）。
OutputOption = Annotated[
    OutputFormat,
    typer.Option("--output", help="Output format: table|json."),
]
JsonOutputOption = Annotated[
    bool,
    typer.Option("--json", help="Alias of --output json (stdout carries data only)."),
]
ConfigOption = Annotated[
    str | None,
    typer.Option(
        "--config",
        help="Deprecated: config is loaded from config.toml and env vars.",
    ),
]
AllRepositoriesOption = Annotated[
    bool,
    typer.Option(
        "--all",
        help="Process all enabled configured repositories.",
    ),
]
RunAgentOption = Annotated[
    RunAgentChoice,
    typer.Option("--agent", help="Agent runner to use."),
]
#: 生命周期锚定入口共享的模型预设一次性覆盖旗标（--preset/--model/--reasoning-effort）。
ModelPresetOption = Annotated[
    str | None,
    typer.Option(
        "--preset",
        help="Anchor this command's lifecycle stage to a named model preset (one-shot).",
    ),
]
ModelIdOption = Annotated[
    str | None,
    typer.Option("--model", help="One-shot model id override for the preset / binding."),
]
ReasoningEffortOption = Annotated[
    str | None,
    typer.Option(
        "--reasoning-effort",
        help="One-shot reasoning effort override for the preset / binding.",
    ),
]


def _typer_preset_options(
    *,
    preset: str | None,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, str | None]:
    """把模型预设覆盖旗标组装成 dispatch kwargs（未传时全 ``None``）。"""
    return {"preset": preset, "model": model, "reasoning_effort": reasoning_effort}


MaxIssuesOption = Annotated[
    int | None,
    typer.Option("--max-issues", help="Maximum number of issues to process."),
]
DaemonIntervalOption = Annotated[
    int | None,
    typer.Option("--interval", help="Polling interval."),
]
ConcurrencyOption = Annotated[
    int | None,
    typer.Option(
        "--concurrency",
        help=(
            "Issues to process in parallel per pass (default: "
            "[agent_runner.runner].max_concurrent_issues; 1 = sequential)."
        ),
    ),
]


def _enum_value(value: str | Enum) -> str:
    """Return a plain string for Typer enum values."""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _typer_selector_options(
    ctx: typer.Context,
    *,
    repo: str | None,
    repo_id: str | None,
    config: str | None,
) -> dict[str, str | None]:
    """Merge command-level repository selectors with top-level selectors."""
    context_values = ctx.obj or {}
    return {
        "repo": repo if repo is not None else context_values.get("repo"),
        "repo_id": repo_id if repo_id is not None else context_values.get("repo_id"),
        "config": config if config is not None else context_values.get("config"),
    }


def _run_typer_command(command: str, **kwargs: Any) -> int:
    """Convert Typer command arguments to the dispatch namespace."""
    namespace_kwargs = {"config": None, **kwargs}
    return _run_parsed_command(argparse.Namespace(command=command, **namespace_kwargs))


def _run_typer_repository_command(
    ctx: typer.Context,
    command: str,
    *,
    repo: str | None,
    repo_id: str | None,
    config: str | None,
    **kwargs: Any,
) -> int:
    """Run a command that accepts repository selector options."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(command, **selector_options, **kwargs)


@app.callback(invoke_without_command=True)
def _app_callback(
    ctx: typer.Context,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    agent: Annotated[
        RunAgentChoice | None,
        typer.Option(
            "--agent",
            help="Override the native executor for the bare `kc` entrypoint. "
            "Accepts any registered agent name (see `kc agent list`); "
            "'auto' falls back to [agent_session].default_agent.",
        ),
    ] = None,
) -> int | None:
    """Store top-level options and dispatch the no-arg native executor entrypoint."""
    ctx.obj = {"repo": repo, "repo_id": repo_id, "config": config}
    if ctx.invoked_subcommand is not None:
        return None
    # No subcommand and the user did not pass --help. The native executor entry
    # only makes sense inside an interactive terminal (the provider owns the
    # TTY); otherwise fall back to Typer's standard help-and-exit behaviour so
    # CI / pipe-driven scripts keep working. The legacy Keda REPL stays at
    # `kc repl`.
    agent_override = _enum_value(agent) if agent is not None else None
    if sys.stdin.isatty():
        # 执行器退出码必须原样交回：裸 kc 就是那个进程，包装层重新解释等于吞掉失败。
        return _run_typer_command(
            "session",
            repo=repo,
            repo_id=repo_id,
            config=config,
            agent=agent_override,
        )
    typer.echo(ctx.get_help())
    raise typer.Exit(code=1)


# Side-effect imports: register every Typer command against ``app`` and the
# sub-apps defined above. Keep them after the ``_app_callback`` definition so
# the decorators can reach the registry, options, and helpers.
#
# Import order matters: Typer preserves the order in which commands register
# on a Typer app. The original ``cli_typer`` module defined commands in this
# order: init → registry → labels → issue → run/review/review-daemon/loop-daemon
# → logs → recover/blocked-continue → ask/repl/deliberate → worktree/workflow
# → takeover → loop. The modules below are imported in that exact order so
# ``kc --help`` byte-output stays stable. ``cli_typer_schema`` is appended
# last: the read-only introspection command only adds a trailing entry and must
# not reorder the existing ones.
from backend.api import (  # noqa: E402,F401
    cli_typer_init,
    cli_typer_registry,
    cli_typer_labels,
    cli_typer_issue,
    cli_typer_runner,
    cli_typer_recover,
    cli_typer_agent,
    cli_typer_worktree,
    cli_typer_workflow,
    cli_typer_container,
    cli_typer_takeover,
    cli_typer_loop,
    cli_typer_backlog,
    cli_typer_config,
    cli_typer_console,
    cli_typer_tokens,
    cli_typer_skill,
    cli_typer_preview,
    cli_typer_schema,
)


def _machine_output_requested(args: list[str]) -> bool:
    """从原始参数探测是否声明了机器模式（解析失败时拿不到 Namespace）。

    解析失败的命令无法走
    :func:`backend.api.cli_output.resolve_output_format` 的正规判定，
    只能扫描原始 token：出现 ``--json``、``--output json`` 或
    ``--output=json``（取值大小写不敏感）即视为机器模式声明。

    Args:
        args: 传给 :func:`main` 的原始参数序列。

    Returns:
        真值表示调用方显式请求了机器输出。
    """
    for index, token in enumerate(args):
        if token == "--json":
            return True
        if token.startswith("--output="):
            if token.removeprefix("--output=").strip().lower() == OUTPUT_FORMAT_JSON:
                return True
        elif token == "--output" and index + 1 < len(args):
            if args[index + 1].strip().lower() == OUTPUT_FORMAT_JSON:
                return True
    return False


def _render_click_exception(exc: typer_click.exceptions.ClickException) -> int:
    """把 click 解析/用法错误落成 FR-4 的结构化 envelope（机器模式）。

    PRD oracle「任意命令在 JSON 模式下的失败」覆盖解析期失败：agent 把旗标
    写错时恰恰最需要结构化错误。错误名按 click 的 ``exit_code`` 归位
    （``UsageError`` 为 ``2`` → ``usage_error``，其余 click 错误未分类为
    ``1`` → ``error``）；``UsageError`` 携带 ``ctx``，据此给出
    ``kc <命令路径> --help`` 建议。

    Args:
        exc: click 抛出的异常（未知旗标、枚举拒绝、缺必填参数等）。

    Returns:
        click 自带的进程退出码，envelope 的 ``exit_code`` 与其一致。
    """
    exit_code = exc.exit_code if isinstance(exc.exit_code, int) else int(ExitCode.GENERAL)
    code = ExitCode.USAGE if exit_code == int(ExitCode.USAGE) else ExitCode.GENERAL
    context = getattr(exc, "ctx", None)
    help_command = (
        context.command_path if context is not None else product_identity.PRIMARY_COMMAND_NAME
    )
    return render_cli_error(
        CliError(
            exc.format_message(),
            code=code,
            suggestion=f"{help_command} --help",
            retryable=False,
        ),
        fmt=OUTPUT_FORMAT_JSON,
    )


def _emit_legacy_command_notice(raw_args: list[str]) -> None:
    """以弃用别名直接启动时，往 stderr 给一次改名提醒。

    三个入口指向同一实现，只有旧名 ``iar`` 需要提醒，``kc`` 与 ``kedacode`` 保持
    安静。两道静音闸门让非人类路径逐字节不变（PRD FR-2）：已安装补全脚本触发的
    补全协议（补全环境变量存在），以及机器模式（``--json`` / ``--output json``）。
    提醒必须走 stderr：人类模式的应用日志把 stdout 处理器挂在 root logger 上，
    而配置加载早于机器模式的日志改绑，用 logger 会让提醒混进 stdout。

    Args:
        raw_args: 传给 :func:`main` 的原始参数，用于判定机器模式。
    """
    invocation_name = sys.argv[0] if sys.argv else ""
    if not product_identity.is_legacy_command_name(invocation_name):
        return
    if product_identity.COMPLETION_ENV_VAR_NAME in os.environ:
        return
    if _machine_output_requested(raw_args):
        return
    product_identity.emit_notice_once(product_identity.LEGACY_COMMAND_HINT)


def main(argv: list[str] | None = None) -> int:
    """Run the Typer-powered CLI (``kc``; also installed as ``kedacode`` / ``iar``)."""
    args = list(sys.argv[1:] if argv is None else argv)
    _emit_legacy_command_notice(args)
    # Typer/Click 会在调用根回调前把空参数处理成成功的帮助输出；保留裸命令既有的
    # no-TTY 非零退出语义，TTY 下直接交给原生会话。
    if not args and product_identity.COMPLETION_ENV_VAR_NAME not in os.environ:
        if sys.stdin.isatty():
            return _run_typer_command(
                "session",
                repo=None,
                repo_id=None,
                config=None,
                agent=None,
            )
        app(
            args=["--help"],
            prog_name=product_identity.PRIMARY_COMMAND_NAME,
            complete_var=product_identity.COMPLETION_ENV_VAR_NAME,
            standalone_mode=False,
        )
        return 1
    if "--version" in args or "-V" in args:
        typer.echo(f"{product_identity.PRIMARY_COMMAND_NAME} {resolve_keda_version()}")
        return 0
    try:
        result = app(
            args=args,
            prog_name=product_identity.PRIMARY_COMMAND_NAME,
            complete_var=product_identity.COMPLETION_ENV_VAR_NAME,
            standalone_mode=False,
        )
    except typer_click.exceptions.NoArgsIsHelpError:
        return 0
    except typer_click.exceptions.ClickException as exc:
        # 解析期失败在机器模式下也必须落 FR-4 envelope（PRD「任意命令在
        # JSON 模式下的失败」oracle）；人类模式保持 click 原文逐字节不变。
        if _machine_output_requested(args):
            return _render_click_exception(exc)
        exc.show()
        return exc.exit_code
    # Abort 必须走 typer 公开 API：typer 0.27 起私有 `_click.exceptions` 不再导出 Abort，
    # 而 typer.Abort 在新旧版本都与实际抛出的类保持同一身份。
    except typer.Abort:
        error_console.print("[red]Aborted.[/]")
        return 1
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1
    return int(result or 0)

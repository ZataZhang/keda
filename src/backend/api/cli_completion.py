"""Shell completion helpers and command registration for the Typer CLI.

Keeping completion logic in a dedicated module prevents ``cli_typer.py`` from
 growing with installation utilities that are only exercised once per shell.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from enum import Enum
from importlib.metadata import entry_points
from pathlib import Path

import click
import typer
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from typer.completion import get_completion_script

from backend.api.cli_console import error_console
from backend.api.cli_output import machine_output_requested
from backend.core.shared.models import product_identity

# 补全协议环境变量名显式绑定当前 CLI 名称，避免随调用方的 prog_name 漂移。
_PRIMARY_COMMAND_NAME = product_identity.PRIMARY_COMMAND_NAME
_COMPLETION_ENV_VAR = product_identity.COMPLETION_ENV_VAR_NAME
# bash 补全脚本的 XDG 配置目录名沿用长命令名（与 PyPI 发行名一致）。
_USER_CONFIG_DIR_NAME = product_identity.LONG_COMMAND_NAME
# 与主命令同 target 的 console script 即别名入口，单一事实源是 pyproject [project.scripts]。
_CLI_ENTRY_POINT_TARGET = "backend.api.cli:main"
_COMPLETION_HINT_MARKER_PREFIX = ".completion-setup-prompt-shown"


class CompletionShellChoice(str, Enum):
    """Shells supported by the explicit completion installer."""

    bash = "bash"
    zsh = "zsh"
    fish = "fish"


def alias_command_names() -> tuple[str, ...]:
    """按安装元数据返回当前支持的非主命令入口，按字母序去重。

    元数据缺失时返回空元组，此时只生成主命令补全。未知或旧命令名不会生成补全。

    Returns:
        tuple[str, ...]: 当前支持的非主命令入口，例如 ``("kedacode",)``。
    """
    supported_alias_names = set(product_identity.OWN_COMMAND_NAMES)
    supported_alias_names.discard(_PRIMARY_COMMAND_NAME)
    alias_name_set = {
        entry_point.name
        for entry_point in entry_points(group="console_scripts")
        if entry_point.value == _CLI_ENTRY_POINT_TARGET
        and entry_point.name in supported_alias_names
    }
    alias_name_set.discard(_PRIMARY_COMMAND_NAME)
    return tuple(sorted(alias_name_set))


def _completion_script(shell: CompletionShellChoice) -> str:
    """Return the shell completion script for the kc command and its aliases."""
    script_sections = [
        get_completion_script(
            prog_name=command_name,
            complete_var=_COMPLETION_ENV_VAR,
            shell=shell.value,
        )
        for command_name in (_PRIMARY_COMMAND_NAME, *alias_command_names())
    ]
    # 每个命令名一段独立脚本，函数与 compdef/complete 注册行均由 typer 模板生成；
    # zsh 段落中间的 #compdef 行只作注释（compinit 仅解析文件首行），别名注册依赖
    # 安装契约里 .zshrc 显式 source 该文件这一行为。
    return "\n\n".join(script_sections)


def _append_unique_line(file_path: Path, line: str) -> bool:
    """Append a shell profile line when it is not already present."""
    existing_text = ""
    if file_path.exists():
        existing_text = file_path.read_text(encoding="utf-8")
        if line in existing_text.splitlines():
            return False
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as profile_file:
        if existing_text and not existing_text.endswith("\n"):
            profile_file.write("\n")
        profile_file.write(f"{line}\n")
    return True


def _completion_script_path(shell: CompletionShellChoice, home_path: Path) -> Path:
    """返回指定 shell 的用户级补全脚本路径。"""
    if shell is CompletionShellChoice.zsh:
        return home_path / ".zsh" / "completions" / f"_{_PRIMARY_COMMAND_NAME}"
    if shell is CompletionShellChoice.bash:
        return (
            home_path
            / ".config"
            / _USER_CONFIG_DIR_NAME
            / f"{_PRIMARY_COMMAND_NAME}_completion.bash"
        )
    return home_path / ".config" / "fish" / "completions" / f"{_PRIMARY_COMMAND_NAME}.fish"


def _completion_profile_path(
    shell: CompletionShellChoice,
    home_path: Path,
) -> Path | None:
    """返回需要 source 补全脚本的 shell profile；fish 会自动发现脚本。"""
    if shell is CompletionShellChoice.zsh:
        return home_path / ".zshrc"
    if shell is CompletionShellChoice.bash:
        return home_path / ".bashrc"
    return None


def _completion_source_line(completion_path: Path) -> str:
    """生成 bash / zsh profile 中加载补全脚本的配置行。"""
    return f'[ -f "{completion_path}" ] && source "{completion_path}"'


def _completion_installation_exists(
    shell: CompletionShellChoice,
    home_path: Path,
) -> bool:
    """检查补全脚本及其 shell 加载配置是否已由 kc 安装。"""
    completion_path = _completion_script_path(shell, home_path)
    if not completion_path.is_file():
        return False
    profile_path = _completion_profile_path(shell, home_path)
    if profile_path is None:
        return True
    try:
        profile_lines = profile_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return _completion_source_line(completion_path) in profile_lines


def _choose_completion_setup(shell: CompletionShellChoice) -> bool:
    """用方向键选择是否启用补全，默认选择安装。"""
    selected_index = 0
    with Live(console=error_console, transient=True, auto_refresh=False) as live_menu:
        while True:
            menu_text = Text()
            menu_text.append(f"Enable {shell.value} shell completion?\n\n", style="bold")
            menu_choices = ("Install completion (default)", "Not now")
            for choice_index, choice_text in enumerate(menu_choices):
                is_selected = choice_index == selected_index
                cursor = "❯ " if is_selected else "  "
                menu_text.append(
                    f"{cursor}{choice_text}\n",
                    style="bold cyan" if is_selected else "dim",
                )
            menu_text.append("\n↑/↓ move · Enter select · q skip", style="dim")
            live_menu.update(Panel(menu_text, border_style="cyan", expand=False), refresh=True)

            key_buffer = click.getchar()
            while key_buffer:
                if key_buffer.startswith(("\x1b[", "\x1bO")):
                    sequence_end = next(
                        (
                            index
                            for index, sequence_character in enumerate(key_buffer[2:], start=2)
                            if sequence_character.isalpha()
                        ),
                        None,
                    )
                    if sequence_end is None:
                        key_buffer = ""
                        continue
                    pressed_key = {"A": "up", "B": "down"}.get(key_buffer[sequence_end], "ignore")
                    key_buffer = key_buffer[sequence_end + 1 :]
                elif key_buffer.startswith(("\x00", "\xe0")) and len(key_buffer) > 1:
                    pressed_key = {"H": "up", "P": "down"}.get(key_buffer[1], "ignore")
                    key_buffer = key_buffer[2:]
                else:
                    key_character = key_buffer[0]
                    key_buffer = key_buffer[1:]
                    if key_character in {"\r", "\n"}:
                        pressed_key = "select"
                    elif key_character.lower() == "q":
                        pressed_key = "skip"
                    else:
                        pressed_key = "ignore"

                if pressed_key == "up":
                    selected_index = max(0, selected_index - 1)
                elif pressed_key == "down":
                    selected_index = min(1, selected_index + 1)
                elif pressed_key == "select":
                    return selected_index == 0
                elif pressed_key == "skip":
                    return False


def _mark_completion_setup_prompt_shown(marker_path: Path) -> None:
    """持久化提示状态；写入失败不能阻断 CLI。"""
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("shown\n", encoding="utf-8")
    except OSError:
        return


def _install_completion_and_report(
    shell: CompletionShellChoice,
    *,
    to_stderr: bool = False,
) -> None:
    """安装补全并输出路径及 shell 重载方式。"""
    completion_path, profile_path = _install_completion_script(shell)
    covered_command_names = ", ".join((_PRIMARY_COMMAND_NAME, *alias_command_names()))
    typer.echo(f"Installed {shell.value} completion: {completion_path}", err=to_stderr)
    typer.echo(
        f"Completion registered for commands: {covered_command_names}",
        err=to_stderr,
    )
    if profile_path is not None:
        typer.echo(f"Reload your shell with: source {profile_path}", err=to_stderr)
    else:
        typer.echo("Open a new terminal session to activate completion.", err=to_stderr)


def maybe_prompt_for_completion_setup(raw_cli_arguments: Sequence[str]) -> bool:
    """首次交互式使用时询问是否安装 shell 补全。

    Args:
        raw_cli_arguments: 用户传给 CLI 的原始参数。

    Returns:
        bool: 本次是否成功安装补全；成功时主命令应退出，让用户重载 shell 后重试。
    """
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        return False
    if _COMPLETION_ENV_VAR in os.environ or machine_output_requested(raw_cli_arguments):
        return False
    if any(argument in {"-h", "--help", "-V", "--version"} for argument in raw_cli_arguments):
        return False
    if raw_cli_arguments and raw_cli_arguments[0] == "completion":
        return False

    shell_name = Path(os.environ.get("SHELL", "")).name.lower()
    try:
        shell_choice = CompletionShellChoice(shell_name)
    except ValueError:
        shell_choice = None

    try:
        home_path = Path.home()
    except (OSError, RuntimeError):
        return False
    if shell_choice is not None and _completion_installation_exists(shell_choice, home_path):
        return False

    marker_shell_name = shell_choice.value if shell_choice is not None else "other"
    try:
        marker_path = product_identity.state_home(home_path) / (
            f"{_COMPLETION_HINT_MARKER_PREFIX}-{marker_shell_name}"
        )
        if marker_path.exists():
            return False
    except (OSError, RuntimeError):
        return False

    if shell_choice is None:
        product_identity.emit_notice_once(
            "note: kc shell completion supports bash, zsh, and fish. "
            "Run `kc completion install --help` to choose your shell and enable it."
        )
        _mark_completion_setup_prompt_shown(marker_path)
        return False

    if _choose_completion_setup(shell_choice):
        try:
            _install_completion_and_report(shell_choice, to_stderr=True)
            typer.echo(
                "Completion is installed. This command will exit now so you can activate it; "
                "follow the shell activation instruction above, then rerun your `kc` command.",
                err=True,
            )
            return True
        except OSError as install_error:
            typer.echo(f"Could not install shell completion: {install_error}", err=True)
        return False

    typer.echo(
        f"Skipped. Run `kc completion install --shell {shell_choice.value}` later to enable.",
        err=True,
    )
    _mark_completion_setup_prompt_shown(marker_path)
    return False


def _install_completion_script(
    shell: CompletionShellChoice,
) -> tuple[Path, Path | None]:
    """Install KedaCode shell completion and return the script/profile paths."""
    script_content = _completion_script(shell)
    home_path = Path.home()
    completion_path = _completion_script_path(shell, home_path)
    completion_path.parent.mkdir(parents=True, exist_ok=True)
    completion_path.write_text(f"{script_content}\n", encoding="utf-8")
    profile_path = _completion_profile_path(shell, home_path)
    if profile_path is None:
        return completion_path, None
    if shell is CompletionShellChoice.zsh:
        _append_unique_line(profile_path, "autoload -Uz compinit && compinit")
    _append_unique_line(profile_path, _completion_source_line(completion_path))
    return completion_path, profile_path


def register_completion_commands(completion_app: typer.Typer) -> None:
    """Register ``completion show`` and ``completion install`` commands."""

    @completion_app.command("show")
    def completion_show_command(
        shell: CompletionShellChoice = CompletionShellChoice.zsh,
    ) -> int:
        """Print a shell completion script."""
        typer.echo(_completion_script(shell))
        return 0

    @completion_app.command("install")
    def completion_install_command(
        shell: CompletionShellChoice = CompletionShellChoice.zsh,
    ) -> int:
        """Install shell completion for the current user."""
        _install_completion_and_report(shell)
        return 0

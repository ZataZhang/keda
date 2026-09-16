"""Shell completion helpers and command registration for the Typer CLI.

Keeping completion logic in a dedicated module prevents ``cli_typer.py`` from
 growing with installation utilities that are only exercised once per shell.
"""

from __future__ import annotations

from enum import Enum
from importlib.metadata import entry_points
from pathlib import Path

import typer
from typer.completion import get_completion_script

# CLI 在 cli_typer_app 中固定以 prog_name="iar" 运行，补全协议环境变量随之固定；
# 别名入口共享同一协议（协议解析会丢弃首词命令名，命令名不影响补全结果）。
_PRIMARY_COMMAND_NAME = "iar"
_COMPLETION_ENV_VAR = "_IAR_COMPLETE"
# 与主命令同 target 的 console script 即别名入口，单一事实源是 pyproject [project.scripts]。
_CLI_ENTRY_POINT_TARGET = "backend.api.cli:main"


class CompletionShellChoice(str, Enum):
    """Shells supported by the explicit completion installer."""

    bash = "bash"
    zsh = "zsh"
    fish = "fish"


def alias_command_names() -> tuple[str, ...]:
    """按安装元数据返回与主命令共享入口的别名命令名，按字母序去重。

    元数据缺失时返回空元组，此时只生成主命令补全；改名前的旧发行版会重复
    声明同一入口，按命令名去重后不受影响。

    Returns:
        tuple[str, ...]: 别名命令名，例如 ``("kedacode",)``。
    """
    alias_name_set = {
        entry_point.name
        for entry_point in entry_points(group="console_scripts")
        if entry_point.value == _CLI_ENTRY_POINT_TARGET
    }
    alias_name_set.discard(_PRIMARY_COMMAND_NAME)
    return tuple(sorted(alias_name_set))


def _completion_script(shell: CompletionShellChoice) -> str:
    """Return the shell completion script for the iAR command and its aliases."""
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


def _install_completion_script(
    shell: CompletionShellChoice,
) -> tuple[Path, Path | None]:
    """Install iAR shell completion and return the script/profile paths."""
    script_content = _completion_script(shell)
    home_path = Path.home()
    if shell is CompletionShellChoice.zsh:
        completion_dir = home_path / ".zsh" / "completions"
        completion_path = completion_dir / "_iar"
        completion_path.parent.mkdir(parents=True, exist_ok=True)
        completion_path.write_text(f"{script_content}\n", encoding="utf-8")
        zshrc_path = home_path / ".zshrc"
        _append_unique_line(zshrc_path, "autoload -Uz compinit && compinit")
        source_line = f'[ -f "{completion_path}" ] && source "{completion_path}"'
        _append_unique_line(zshrc_path, source_line)
        return completion_path, zshrc_path
    if shell is CompletionShellChoice.bash:
        completion_path = home_path / ".config" / "iar" / "iar_completion.bash"
        completion_path.parent.mkdir(parents=True, exist_ok=True)
        completion_path.write_text(f"{script_content}\n", encoding="utf-8")
        bashrc_path = home_path / ".bashrc"
        source_line = f'[ -f "{completion_path}" ] && source "{completion_path}"'
        _append_unique_line(bashrc_path, source_line)
        return completion_path, bashrc_path
    completion_path = home_path / ".config" / "fish" / "completions" / "iar.fish"
    completion_path.parent.mkdir(parents=True, exist_ok=True)
    completion_path.write_text(f"{script_content}\n", encoding="utf-8")
    return completion_path, None


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
        completion_path, profile_path = _install_completion_script(shell)
        covered_command_names = ", ".join((_PRIMARY_COMMAND_NAME, *alias_command_names()))
        typer.echo(f"Installed {shell.value} completion: {completion_path}")
        typer.echo(f"Completion registered for commands: {covered_command_names}")
        if profile_path is not None:
            typer.echo(f"Reload your shell with: source {profile_path}")
        else:
            typer.echo("Open a new terminal session to activate completion.")
        return 0

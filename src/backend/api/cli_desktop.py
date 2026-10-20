"""macOS 菜单栏 App 的 CLI 命令与首次使用提示。"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

import typer

from backend.api.cli_output import machine_output_requested
from backend.api.cli_setup_prompt import choose_setup_option
from backend.api.version_info import resolve_keda_version
from backend.core.shared.models import product_identity
from backend.core.use_cases.desktop_companion import (
    DesktopCompanionInstallError,
    install_desktop_companion,
    is_desktop_companion_installed,
)

_DESKTOP_SETUP_MARKER = ".desktop-app-setup-prompt-shown"
_SETUP_PROMPT_SKIP_COMMANDS = {"agent", "completion", "desktop", "schema"}


def _should_skip_desktop_setup_prompt(raw_cli_arguments: Sequence[str]) -> bool:
    """判定本次调用是否不应弹出桌面 App 的首次使用提示。"""
    if sys.platform != "darwin":
        return True
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        return True
    if product_identity.COMPLETION_ENV_VAR_NAME in os.environ:
        return True
    if machine_output_requested(list(raw_cli_arguments)):
        return True
    if any(argument in {"-h", "--help", "-V", "--version"} for argument in raw_cli_arguments):
        return True
    if raw_cli_arguments and raw_cli_arguments[0] in _SETUP_PROMPT_SKIP_COMMANDS:
        return True
    return False


def _desktop_setup_marker_path() -> Path | None:
    """返回菜单栏 App 首次提示的本机状态文件路径。"""
    try:
        return product_identity.state_home() / _DESKTOP_SETUP_MARKER
    except (OSError, RuntimeError):
        return None


def _mark_desktop_setup_prompt_shown(marker_path: Path | None) -> None:
    """记录用户已选择是否安装；写入失败不阻断 CLI。"""
    if marker_path is None:
        return
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("shown\n", encoding="utf-8")
    except OSError:
        return


def _install_desktop_app_and_report(*, fail_on_error: bool) -> bool:
    """安装当前版本对应的菜单栏 App，必要时把失败转换成 CLI 错误。"""
    try:
        installed_app_path = install_desktop_companion(version=resolve_keda_version())
    except DesktopCompanionInstallError as install_error:
        typer.echo(
            f"Could not install the KedaCode menu bar app: {install_error}\n"
            "Retry later with `kc desktop install`.",
            err=True,
        )
        if fail_on_error:
            raise typer.Exit(code=1) from install_error
        return False
    typer.echo(
        f"Installed the KedaCode menu bar app: {installed_app_path}\n"
        "Open it from Finder to show the menu bar icon."
    )
    return True


def maybe_prompt_for_desktop_app_setup(raw_cli_arguments: Sequence[str]) -> None:
    """首次交互式使用时询问是否安装 macOS 菜单栏 App。

    Args:
        raw_cli_arguments: 用户传给 CLI 的原始参数。
    """
    if _should_skip_desktop_setup_prompt(raw_cli_arguments):
        return
    if is_desktop_companion_installed():
        return
    marker_path = _desktop_setup_marker_path()
    if marker_path is None:
        return
    try:
        if marker_path.exists():
            return
    except OSError:
        return

    user_accepted_install = choose_setup_option(
        question="Install the KedaCode macOS menu bar app?",
        install_label="Install menu bar app (default)",
        skip_label="Not now",
    )
    _mark_desktop_setup_prompt_shown(marker_path)
    if user_accepted_install:
        _install_desktop_app_and_report(fail_on_error=False)
        return
    typer.echo(
        "Skipped. Run `kc desktop install` later to install the menu bar app.",
        err=True,
    )


def register_desktop_commands(desktop_app: typer.Typer) -> None:
    """注册菜单栏 App 管理命令。

    Args:
        desktop_app: 用于注册命令的 Typer 子应用。
    """

    @desktop_app.command("install")
    def desktop_install_command() -> None:
        """安装与当前 kedacode 版本匹配的 macOS 菜单栏 App。"""
        _install_desktop_app_and_report(fail_on_error=True)


__all__ = ["maybe_prompt_for_desktop_app_setup", "register_desktop_commands"]

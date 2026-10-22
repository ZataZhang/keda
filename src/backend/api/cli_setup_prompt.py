"""交互式首次使用设置菜单。"""

from __future__ import annotations

import click
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from backend.api.cli_console import error_console


def choose_setup_option(
    *,
    question: str,
    install_label: str,
    skip_label: str,
) -> bool:
    """展示方向键菜单并返回用户是否选择安装。

    Args:
        question: 菜单标题。
        install_label: 默认选中的安装项。
        skip_label: 暂不安装项。

    Returns:
        用户选择安装时返回 ``True``。
    """
    selected_index = 0
    with Live(console=error_console, transient=True, auto_refresh=False) as live_menu:
        while True:
            menu_text = Text()
            menu_text.append(f"{question}\n\n", style="bold")
            for choice_index, choice_text in enumerate((install_label, skip_label)):
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

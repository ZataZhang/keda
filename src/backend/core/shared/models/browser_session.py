"""Agent 浏览器通道的动作边界与使用说明。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

BROWSER_GUIDANCE = """
Browser access is available on demand through `kc browser` in this invocation.
Use `kc browser open <url>`, then `kc browser snapshot` to read element references,
`kc browser click <ref>`, `kc browser fill <ref> <text>`, `kc browser press Enter`,
and `kc browser screenshot --filename <worktree-relative evidence path>`.
Run `kc browser --help` for the supported actions. The runner starts the browser
outside the agent sandbox only when requested, isolates this invocation's session,
and closes it when the invocation ends. Use this entry for interactive browser
validation and screenshots. Record reproducible tests separately when required.
""".strip()

_ACTION_ARITIES = {
    "open": (0, 1),
    "goto": (1, 1),
    "snapshot": (0, 1),
    "find": (1, 1),
    "click": (1, 2),
    "dblclick": (1, 2),
    "fill": (2, 2),
    "type": (1, 1),
    "press": (1, 1),
    "hover": (1, 1),
    "check": (1, 1),
    "uncheck": (1, 1),
    "select": (2, 2),
    "resize": (2, 2),
    "go-back": (0, 0),
    "go-forward": (0, 0),
    "reload": (0, 0),
    "tab-list": (0, 0),
    "tab-new": (0, 1),
    "tab-select": (1, 1),
    "tab-close": (0, 1),
    "dialog-accept": (0, 1),
    "dialog-dismiss": (0, 0),
    "close": (0, 0),
    "status": (0, 0),
}


def validate_browser_arguments(arguments: list[str], worktree: Path) -> list[str]:
    """校验浏览器动作，禁止透传宿主执行、配置或会话覆盖参数。

    Args:
        arguments: Agent 提交的动作与参数。
        worktree: 截图允许写入的工作树根目录。

    Returns:
        校验并规范化后的 Playwright CLI 参数。

    Raises:
        ValueError: 动作、参数或截图路径超出浏览器通道边界。
    """
    if not arguments or not all(isinstance(value, str) for value in arguments):
        raise ValueError("Expected a browser action and string arguments.")
    action, *values = arguments
    if action == "screenshot":
        return _validate_screenshot_arguments(values, worktree)
    if action not in _ACTION_ARITIES:
        raise ValueError(f"Unsupported browser action: {action}. Run kc browser --help.")
    minimum, maximum = _ACTION_ARITIES[action]
    option_candidates = (
        values[:-1] if action in {"fill", "type", "find", "select", "dialog-accept"} else values
    )
    if not minimum <= len(values) <= maximum or any(
        value.startswith("-") for value in option_candidates
    ):
        raise ValueError(f"Invalid arguments for browser action {action}.")
    if action in {"open", "goto", "tab-new"} and values:
        parsed_url = urlsplit(values[0])
        if values[0] != "about:blank" and (
            parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname
        ):
            raise ValueError("Browser navigation requires an http(s) URL or about:blank.")
    return arguments.copy()


def _validate_screenshot_arguments(values: list[str], worktree: Path) -> list[str]:
    """解析截图参数，并把输出路径限制到本次工作树。"""
    normalized = ["screenshot"]
    position = 0
    while position < len(values):
        value = values[position]
        if value == "--filename":
            position += 1
            if position >= len(values):
                raise ValueError("--filename requires a worktree-relative path.")
            value = "--filename=" + values[position]
        if value.startswith("--filename="):
            output_path = (worktree / value.split("=", 1)[1]).resolve()
            if not output_path.is_relative_to(worktree.resolve()):
                raise ValueError("Screenshot output must stay inside this worktree.")
            normalized.append(f"--filename={output_path}")
        elif value == "--full-page" or not value.startswith("-"):
            normalized.append(value)
        else:
            raise ValueError(f"Unsupported screenshot option: {value}")
        position += 1
    if sum(not value.startswith("--") for value in normalized[1:]) > 1:
        raise ValueError("Screenshot accepts at most one element reference.")
    if sum(value.startswith("--filename=") for value in normalized) > 1:
        raise ValueError("Screenshot accepts one output path.")
    return normalized

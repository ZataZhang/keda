"""解析 ``worktree.path_command`` 在 stdout 上输出的 worktree 路径。

``kc worktree path`` 会在最后一行打印绝对路径，但同一进程可能在路径之前把
日志记录（WARNING/INFO）写到 stdout：应用日志器固定写 stdout，而配置加载发生
在命令主体之前。若调用方直接 ``Path(stdout.strip())``，就会把日志噪声与路径
拼成一条非法路径，导致路径「不存在」，进而把 Issue 误判为失败。这里统一取
最后一行非空文本，即命令最后打印的路径。
"""

from __future__ import annotations

from pathlib import Path


def parse_worktree_path_stdout(stdout: str) -> Path:
    """从 ``path_command`` 的 stdout 中解析 worktree 路径。

    取最后一行非空文本作为路径，并去掉成对包裹的引号，从而对「路径之前夹带了
    日志行」的被污染 stdout 保持健壮。

    Args:
        stdout: ``path_command`` 进程的完整标准输出。

    Returns:
        解析出的 worktree 路径（不做 ``resolve``；相对路径由调用方自行锚定）。

    Raises:
        ValueError: 当 stdout 不含任何非空行时抛出。此时无法得到路径，宁可显式
            失败，也不能把空字符串误当成当前目录。
    """
    last_line = ""
    for raw_line in reversed(stdout.splitlines()):
        stripped_line = raw_line.strip()
        if stripped_line:
            last_line = stripped_line
            break
    if not last_line:
        raise ValueError(
            f"worktree path_command produced no non-empty stdout line; raw stdout={stdout!r}."
        )
    if len(last_line) >= 2 and last_line[0] == last_line[-1] and last_line[0] in {"'", '"'}:
        last_line = last_line[1:-1]
    return Path(last_line)


__all__ = ["parse_worktree_path_stdout"]

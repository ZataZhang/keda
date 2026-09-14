"""PRD 原文读取用例：只读白名单目录内的 Markdown 文件。

该用例把「路径是否合法」与「文件是否可读」都收敛为 :class:`PrdContentError`，
让接入层能稳定地把失败映射为 4xx，而不是把 ``OSError`` 泄漏成 500。
"""

from __future__ import annotations

from pathlib import Path

from backend.core.use_cases.roadmap_prd_scanner import _DEFAULT_PRD_DIRS

#: 只允许读取 Markdown 原文，避免把端点扩成任意文件读取。
_ALLOWED_PRD_SUFFIX = ".md"


class PrdContentError(Exception):
    """PRD 原文读取失败：路径越界、后缀非法或文件不存在/不可读。"""


def resolve_prd_content_path(repo_path: Path, relative_prd_path: str) -> Path:
    """把列表接口给出的相对路径解析为白名单目录内的真实文件路径。

    白名单目录锚定在**解析后的仓库根**之下（只解析仓库根，不再解析白名单目录
    自身），候选路径则完整 ``resolve()`` 后再比对：这样 ``../``、文件符号链接
    逃逸，以及白名单目录整体被替换为符号链接三种情况都会落到白名单之外。
    若改成对原始字符串做前缀匹配，前两类都会漏过。

    Args:
        repo_path: 仓库根目录。
        relative_prd_path: 相对仓库根目录的 PRD 路径，取自列表响应。

    Returns:
        位于 ``tasks/pending/`` 或 ``tasks/archive/`` 内的已解析绝对路径。

    Raises:
        PrdContentError: 传入绝对路径、后缀不是 ``.md``、解析后落在白名单目录
            之外，或目标文件不存在。
    """
    requested_path = Path(relative_prd_path)
    if requested_path.is_absolute():
        raise PrdContentError("PRD 路径必须是相对仓库根目录的相对路径。")
    if requested_path.suffix != _ALLOWED_PRD_SUFFIX:
        raise PrdContentError("只允许读取 .md 后缀的 PRD 文件。")

    resolved_repo_root = repo_path.resolve()
    allowed_directories = [
        resolved_repo_root / relative_directory for relative_directory in _DEFAULT_PRD_DIRS
    ]
    resolved_path = (repo_path / requested_path).resolve()
    if not any(resolved_path.is_relative_to(allowed) for allowed in allowed_directories):
        raise PrdContentError("PRD 路径必须位于 tasks/pending/ 或 tasks/archive/ 内。")
    if not resolved_path.is_file():
        raise PrdContentError("PRD 文件不存在。")
    return resolved_path


def read_prd_content(repo_path: Path, relative_prd_path: str) -> str:
    """读取白名单目录内的 PRD 原文。

    Args:
        repo_path: 仓库根目录。
        relative_prd_path: 相对仓库根目录的 PRD 路径，取自列表响应。

    Returns:
        以 UTF-8 解码的 Markdown 原文，与磁盘文件逐字节一致。

    Raises:
        PrdContentError: 路径校验失败，或文件存在但无法按 UTF-8 读出。
    """
    resolved_path = resolve_prd_content_path(repo_path, relative_prd_path)
    try:
        return resolved_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PrdContentError(f"PRD 文件读取失败: {exc}") from exc

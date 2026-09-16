"""Post-PR supervisor 的 diff 分层注入（补丁 3）。

从 ``pr_supervisor.py`` 拆出：补丁 3 让整文件超过 1000 非空行硬上限
（CI ``check_max_file_lines.py --max-lines 1000``），而 allowlist 明确禁止新增豁免，
故按职责切成独立模块。这里只放与 diff 文本切分/截断相关的纯函数。"""

from __future__ import annotations

import logging
import re

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 补丁 3：supervisor diff 分层注入（关键路径全量 + 其余按字符预算截断）
# ---------------------------------------------------------------------------

_DIFF_FILE_HEADER_PREFIX = "diff --git "
_DIFF_FILE_PATH_PATTERN = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
_DIFF_TRUNCATION_MARKER = "...(diff truncated)"


def _normalize_diff_path(raw_path: str) -> str:
    """归一化 diff 路径与 ``key_paths`` 前缀，避免 ``./`` 或反斜杠写法漏匹配。"""
    return raw_path.replace("\\", "/").removeprefix("./").strip()


def _diff_file_path(header_line: str) -> str:
    """从 ``diff --git a/<path> b/<path>`` 行取出变更文件路径。"""
    path_match = _DIFF_FILE_PATH_PATTERN.match(header_line.strip())
    if path_match is None:
        return ""
    return _normalize_diff_path(path_match.group(2))


def _split_diff_by_file(diff_text: str) -> tuple[tuple[str, str], ...]:
    """把 unified diff 切成 ``(文件路径, 该文件 diff 文本)`` 序列。

    分不出文件边界时（例如 ``(diff unavailable)`` 或非标准格式）返回空元组，
    由调用方退回整体截断，保持补丁前的降级行为。
    """
    file_chunks: list[tuple[str, str]] = []
    current_path = ""
    current_lines: list[str] = []
    for raw_line in diff_text.splitlines():
        if raw_line.startswith(_DIFF_FILE_HEADER_PREFIX):
            if current_lines:
                file_chunks.append((current_path, "\n".join(current_lines)))
            current_path = _diff_file_path(raw_line)
            current_lines = [raw_line]
            continue
        if current_path:
            current_lines.append(raw_line)
    if current_lines:
        file_chunks.append((current_path, "\n".join(current_lines)))
    return tuple(file_chunks)


def _is_key_diff_path(file_path: str, key_paths: tuple[str, ...]) -> bool:
    """判断某文件的 diff 是否落在配置声明的关键路径下。"""
    normalized_path = _normalize_diff_path(file_path)
    return any(
        normalized_path.startswith(_normalize_diff_path(key_path))
        for key_path in key_paths
        if key_path.strip()
    )


def _truncate_diff_text(diff_text: str, max_diff_chars: int) -> str:
    """按字符预算截断 diff，并显式留下截断标记。"""
    if max_diff_chars <= 0 or len(diff_text) <= max_diff_chars:
        return diff_text
    return f"{diff_text[:max_diff_chars]}\n{_DIFF_TRUNCATION_MARKER}"


def _build_layered_diff(
    diff_text: str,
    key_paths: tuple[str, ...],
    max_diff_chars: int,
) -> str:
    """构建分层 diff：文件清单 + 关键文件全量 + 其余按预算截断。

    补丁前整个 diff 被截断到 6000 字符，长 PR 的关键变更常常整个落在窗口外，
    supervisor 只看得到前半截就 approve。这里按 ``key_paths`` 前缀把关键文件
    的 diff 完整保留，非关键文件共享剩余字符预算。

    Args:
        diff_text: ``git diff`` 原始输出。
        key_paths: 关键路径前缀；为空时退化为整体截断（补丁前行为）。
        max_diff_chars: 非关键文件 diff 的字符预算。

    Returns:
        str: 供 prompt 注入的分层 diff 文本。
    """
    file_chunks = _split_diff_by_file(diff_text)
    if not file_chunks:
        return _truncate_diff_text(diff_text, max_diff_chars)

    key_chunks = [chunk for chunk in file_chunks if _is_key_diff_path(chunk[0], key_paths)]
    other_chunks = [chunk for chunk in file_chunks if not _is_key_diff_path(chunk[0], key_paths)]
    if key_paths and not key_chunks:
        _logger.warning(
            "Supervisor key_paths %s matched no file in the PR diff; "
            "check for a wrong path prefix.",
            key_paths,
        )

    sections = [
        f"Changed files ({len(file_chunks)}):",
        "\n".join(f"- {file_path}" for file_path, _ in file_chunks),
    ]
    if key_chunks:
        sections.extend(
            [
                "",
                "--- Key files (full diff) ---",
                "\n".join(chunk_text for _, chunk_text in key_chunks),
            ]
        )
    if other_chunks:
        other_diff_text = "\n".join(chunk_text for _, chunk_text in other_chunks)
        sections.extend(
            [
                "",
                f"--- Other files (truncated to {max_diff_chars} chars) ---",
                _truncate_diff_text(other_diff_text, max_diff_chars),
            ]
        )
    return "\n".join(sections)

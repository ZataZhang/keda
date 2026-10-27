"""Markdown 围栏代码块的判定与剥离（跨解析面的唯一口径）。

runner 解析的几种正文都要求"只读真实声明、不读示例"：PRD 的 Delivery
Dependencies 小节、PR 正文的 ``Closes #N`` 锚点、PR 正文的签收清单 marker。
GitHub 与 Markdown 渲染器都不把围栏内容当成正文——写在 ``` 块里的 ``Closes #N``
不会建立 Issue 关联，围栏里的示例清单也不会被人勾选。把这类示例当成真实声明，
产出的就是没有任何信号的静默误判；反过来，把示例当成待迁移的旧 marker 拦下来，
又是另一类假阳性。因此"什么算围栏内"必须只有一份实现。
"""

from __future__ import annotations

import re

_CODE_FENCE_LINE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>```+|~~~+)")
"""围栏定界行：行首至多 3 个空格缩进 + 三个以上连续反引号或波浪号（CommonMark）。"""


def _extract_code_fence_delimiter(line: str) -> str | None:
    """返回该行的围栏定界符（``` / ~~~ 系列）；不是围栏行时返回 ``None``。"""
    fence_match = _CODE_FENCE_LINE_RE.match(line)
    return fence_match.group("fence") if fence_match else None


def _closes_open_fence(candidate_fence: str, open_fence: str) -> bool:
    """候选定界符是否能关闭 ``open_fence`` 开启的代码块（同字符且不短于开围栏）。"""
    return candidate_fence[0] == open_fence[0] and len(candidate_fence) >= len(open_fence)


def strip_code_fences(markdown_text: str) -> str:
    """把围栏代码块（含定界行）替换成等长空白，保持行号与字符偏移不变。

    未闭合的开围栏会一直吞到文本结尾，与 Markdown 渲染器的行为一致。调用方因此
    可以拿剥离后的文本继续做正则搜索或按偏移切片，而不必担心围栏里的示例被当成
    正文声明。

    Args:
        markdown_text: 原始 Markdown 文本。

    Returns:
        围栏行与围栏内内容变成空白的同长度文本。
    """
    stripped_lines: list[str] = []
    open_fence: str | None = None
    for original_line in markdown_text.splitlines(keepends=True):
        fence_delimiter = _extract_code_fence_delimiter(original_line)
        if open_fence is None:
            if fence_delimiter is None:
                stripped_lines.append(original_line)
                continue
            open_fence = fence_delimiter
        elif fence_delimiter is not None and _closes_open_fence(fence_delimiter, open_fence):
            open_fence = None
        stripped_lines.append(re.sub(r"\S", " ", original_line))
    return "".join(stripped_lines)

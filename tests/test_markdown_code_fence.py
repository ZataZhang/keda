"""Markdown 围栏剥离的单元测试：正文解析只认真实声明，不认 ``` 块里的示例。"""

from __future__ import annotations

import re

from backend.core.shared.markdown_code_fence import strip_code_fences


def test_strip_code_fences_blanks_block_but_keeps_offsets() -> None:
    """围栏行与块内内容变成空白，其余行原样保留且偏移量不变。"""
    markdown_text = (
        "## 8. Delivery Dependencies\n\n"
        "```yaml\n"
        "  - prd: example\n"
        "```\n"
        "\n"
        "- Gate type: hard\n"
    )

    scannable_text = strip_code_fences(markdown_text)

    assert len(scannable_text) == len(markdown_text)
    assert re.search(r"- prd:", scannable_text) is None
    assert "- Gate type: hard" in scannable_text
    assert scannable_text.index("- Gate type: hard") == markdown_text.index("- Gate type: hard")


def test_strip_code_fences_requires_matching_fence_character() -> None:
    """~~~ 关闭不了 ``` 开的块：块内直到文末都不算正文。"""
    markdown_text = "```\n~~~\n- Gate type: hard\n"

    assert "- Gate type: hard" not in strip_code_fences(markdown_text)


def test_strip_code_fences_keeps_shorter_fence_as_content() -> None:
    """比开围栏更短的同类围栏是块内容，关不掉它；未闭合时吞到文末。"""
    markdown_text = "````java\n- Notes: sample\n```\n- Notes: real\n"

    assert "- Notes: real" not in strip_code_fences(markdown_text)

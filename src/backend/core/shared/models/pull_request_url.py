"""GitHub 与 Gitea Pull Request URL 的共享解析规则。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_PULL_REQUEST_NUMBER_PATTERN = re.compile(r"/pulls?/(\d+)(?:/|$)", re.IGNORECASE)


def parse_pull_request_number(url: str) -> int | None:
    """从 GitHub ``/pull/<n>`` 或 Gitea ``/pulls/<n>`` 链接解析编号。

    Args:
        url: Pull Request URL 或路径。

    Returns:
        正整数编号；链接中没有受支持的 Pull Request 路径时返回 ``None``。
    """
    if not isinstance(url, str) or not url:
        return None
    request_path = urlsplit(url).path
    number_match = _PULL_REQUEST_NUMBER_PATTERN.search(request_path)
    if number_match is None:
        return None
    return int(number_match.group(1))


__all__ = ["parse_pull_request_number"]

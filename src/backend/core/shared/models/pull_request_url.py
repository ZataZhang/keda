"""GitHub 与 Gitea Pull Request URL 的共享解析规则。"""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit, urlunsplit

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


def build_evidence_blob_url(pr_url: str, branch: str, file_name: str) -> str | None:
    """生成 GitHub 或 Gitea 仓库证据分支的文件链接。"""
    if parse_pull_request_number(pr_url) is None:
        return None
    parsed_pr_url = urlsplit(pr_url)
    path_parts = [part for part in parsed_pr_url.path.split("/") if part]
    pull_segment_index = next(
        (
            index
            for index, path_part in enumerate(path_parts)
            if path_part.lower() in {"pull", "pulls"}
            and index > 1
            and index + 1 < len(path_parts)
            and path_parts[index + 1].isdigit()
        ),
        None,
    )
    if pull_segment_index is None:
        return None
    pull_segment = path_parts[pull_segment_index].lower()
    blob_path_segments = (
        ["src", "branch", *branch.split("/")]
        if pull_segment == "pulls"
        else ["blob", *branch.split("/")]
    )
    link_prefix_parts = path_parts[:pull_segment_index] + blob_path_segments + file_name.split("/")
    encoded_path = "/" + "/".join(quote(path_part, safe="") for path_part in link_prefix_parts)
    return urlunsplit((parsed_pr_url.scheme, parsed_pr_url.netloc, encoded_path, "", ""))


__all__ = ["build_evidence_blob_url", "parse_pull_request_number"]

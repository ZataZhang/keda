"""GitHub 与 Gitea Pull Request URL 解析测试。"""

from __future__ import annotations

import pytest

from backend.core.shared.models.pull_request_url import parse_pull_request_number


@pytest.mark.parametrize(
    ("pull_request_url", "expected_number"),
    [
        ("https://github.com/owner/repo/pull/17", 17),
        ("https://git.example.com/owner/repo/pulls/42", 42),
        ("https://git.example.com/owner/repo/pulls/42/files", 42),
        ("/pull/9", 9),
        ("/pulls/8/", 8),
    ],
)
def test_parse_pull_request_number_accepts_github_and_gitea_urls(
    pull_request_url: str,
    expected_number: int,
) -> None:
    """GitHub 单数与 Gitea 复数路径都解析为 PR 编号。"""
    assert parse_pull_request_number(pull_request_url) == expected_number


@pytest.mark.parametrize(
    "pull_request_url",
    [
        "",
        "https://github.com/owner/repo/issues/17",
        "https://git.example.com/owner/repo/pulls/not-a-number",
        "https://git.example.com/owner/repo/pulls/17-extra",
    ],
)
def test_parse_pull_request_number_rejects_non_pull_request_urls(
    pull_request_url: str,
) -> None:
    """Issue、空值与格式错误链接不应误判为 Pull Request。"""
    assert parse_pull_request_number(pull_request_url) is None

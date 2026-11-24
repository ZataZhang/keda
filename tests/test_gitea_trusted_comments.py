"""Gitea 控制评论必须按作者权限过滤。"""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.infrastructure.gitea_client import GiteaApiClient, GiteaRepositoryBinding


def test_trusted_comments_check_each_author() -> None:
    """运行者有写权限时，外部作者仍不能伪造控制评论。"""
    client = GiteaApiClient(
        GiteaRepositoryBinding(Path("."), "https://git.example", "git.example", "o/r"), "secret"
    )
    comments = [
        {"id": 1, "body": "claim", "user": {"login": "runner"}},
        {"id": 2, "body": "claim", "user": {"login": "outsider"}},
        {"id": 3, "body": "claim", "user": {"login": "maintainer"}},
    ]
    with (
        patch.object(client, "_list_pages", return_value=comments),
        patch.object(client, "_get_authenticated_login", return_value="runner"),
        patch.object(client, "_request_json", side_effect=[None, {"permission": "write"}]),
    ):
        assert client.list_issue_comment_entries(1, trusted_only=True) == [
            (1, "claim"),
            (3, "claim"),
        ]


def test_trusted_comments_reject_unknown_permission() -> None:
    """权限响应异常不能被当作可信作者。"""
    client = GiteaApiClient(
        GiteaRepositoryBinding(Path("."), "https://git.example", "git.example", "o/r"), "secret"
    )
    with (
        patch.object(
            client,
            "_list_pages",
            return_value=[{"id": 1, "body": "claim", "user": {"login": "other"}}],
        ),
        patch.object(client, "_get_authenticated_login", return_value="runner"),
        patch.object(client, "_request_json", return_value={"permission": "unexpected"}),
    ):
        with pytest.raises(RuntimeError, match="permissions"):
            client.list_issue_comment_entries(1, trusted_only=True)

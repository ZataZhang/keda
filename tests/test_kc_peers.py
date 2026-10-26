"""``kc peers`` 对没有状态板的旧认领也保持可见。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import backend.api.cli  # noqa: F401 - 先完成 CLI facade 初始化以避免包内循环导入。
from backend.api.cli_parsed_commands import peers as peers_command
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary, RepositoryRunContext
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimBid, format_claim_comment
from tests.conftest import FakeGitHubClient


class PeersGitHubClient(FakeGitHubClient):
    """Fake that returns Issue lists for their requested label."""

    def __init__(self, *, running_issue: IssueSummary) -> None:
        super().__init__()
        self._running_issue = running_issue

    def list_issues_by_label(self, label: str, limit: int, state: str = "all"):
        self.calls.append(
            {"method": "list_issues_by_label", "label": label, "limit": limit, "state": state}
        )
        if label == "agent/running":
            return [self._running_issue]
        return []


def test_peers_lists_legacy_claim_without_presence_issue(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """没有状态 Issue 时仍报告可能来自旧版的 running claim。"""
    context = RepositoryRunContext(
        repo_id="keda-main",
        display_name="Keda Main",
        repo_path=tmp_path,
        config=AppConfig(),
    )
    running_issue = IssueSummary(
        number=43,
        title="Legacy task",
        url="https://github.com/example/repo/issues/43",
        body="",
        labels=(context.config.labels.running,),
    )
    github_client = PeersGitHubClient(running_issue=running_issue)
    github_client.comment_issue(
        43,
        format_claim_comment(
            ClaimBid(
                comment_id=0,
                host="legacy-host",
                pid=4321,
                started_at=datetime.now(timezone.utc),
                agent="claude",
            )
        ),
    )
    monkeypatch.setattr(
        "backend.api.cli_parsed_commands.runner._resolve_initialized_repository_contexts",
        lambda _ctx: [context],
    )
    command_context = SimpleNamespace(
        github_client_factory=lambda _repo_path: github_client,
        output_format="json",
    )

    exit_code = peers_command.run_peers_command(command_context)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["presence_issue"] is None
    assert payload["machines"] == []
    assert payload["message"] == "该仓库还没有机器上报状态"
    assert len(payload["unreported_claims"]) == 1
    unreported_claim = payload["unreported_claims"][0]
    assert unreported_claim["issue_number"] == 43
    assert unreported_claim["hostname"] == "legacy-host"
    assert unreported_claim["machine_id"] is None
    assert unreported_claim["agent"] == "claude"
    assert unreported_claim["status"] == "持有者未上报状态（可能是旧版本）"
    assert datetime.fromisoformat(unreported_claim["claim_updated_at"]).tzinfo is not None

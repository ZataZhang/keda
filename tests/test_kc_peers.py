"""``kc peers`` 的可见性与输出安全：没有状态板的旧认领仍列出，评论带来的文本按字面展示。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.api.cli  # noqa: F401 - 先完成 CLI facade 初始化以避免包内循环导入。
from backend.api.cli_parsed_commands import peers as peers_command
from backend.core.shared.models.agent_labels import KEDACODE_PRESENCE_LABEL
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    RepositoryRunContext,
)
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimBid, format_claim_comment
from backend.core.use_cases.machine_presence import render_presence_comment
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


class PresenceBoardGitHubClient(PeersGitHubClient):
    """带一块状态板的 fake：状态评论由测试自己写进去。"""

    def __init__(self, *, running_issue: IssueSummary, presence_issue: IssueSummary) -> None:
        super().__init__(running_issue=running_issue)
        self._presence_issue = presence_issue

    def list_issues_by_label(self, label: str, limit: int, state: str = "all"):
        if label == KEDACODE_PRESENCE_LABEL:
            self.calls.append(
                {"method": "list_issues_by_label", "label": label, "limit": limit, "state": state}
            )
            return [self._presence_issue]
        return super().list_issues_by_label(label, limit, state=state)


def test_display_field_strips_control_sequences_and_markup() -> None:
    """评论带来的自由文本被压成安全的单元格内容。"""
    display_text = peers_command._display_field(  # noqa: SLF001 - 渲染护栏本身就是要测的行为
        "[red]pwn[/red]\x1b]0;evil\x07" + "x" * 200
    )

    assert "\x1b" not in display_text
    assert display_text.startswith("\\[red]pwn")
    assert display_text.endswith("…")


def test_peers_human_table_never_emits_raw_terminal_controls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """状态评论里的 markup / 控制字符按字面展示，不操纵终端。"""
    context = RepositoryRunContext(
        repo_id="keda-main",
        display_name="Keda Main",
        repo_path=tmp_path,
        config=AppConfig(),
    )
    presence_issue = IssueSummary(
        number=100,
        title="KedaCode 机器状态",
        url="https://github.com/example/repo/issues/100",
        body="",
        labels=(KEDACODE_PRESENCE_LABEL,),
    )
    github_client = PresenceBoardGitHubClient(
        running_issue=IssueSummary(
            number=42,
            title="Task",
            url="https://github.com/example/repo/issues/42",
            body="",
            labels=(context.config.labels.running,),
        ),
        presence_issue=presence_issue,
    )
    github_client.comment_issue(
        100,
        render_presence_comment(
            machine_id="machine-a",
            slot="111@1730000000.000000",
            hostname="pwn\x1b]0;evil\x07",
            version="1.2.3",
            role="daemon",
            process_state="online",
            issues=({"number": 42, "stage": "agent/running", "agent": "[bold]codex[/bold]"},),
            reported_at=datetime.now(timezone.utc),
        ),
    )
    monkeypatch.setattr(
        "backend.api.cli_parsed_commands.runner._resolve_initialized_repository_contexts",
        lambda _ctx: [context],
    )
    command_context = SimpleNamespace(
        github_client_factory=lambda _repo_path: github_client,
        output_format="table",
    )

    exit_code = peers_command.run_peers_command(command_context)

    rendered = capsys.readouterr().out
    assert exit_code == 0
    assert "\x1b" not in rendered
    assert "pwn" in rendered


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

"""通过真实落盘失败验证 GitHub API 账本降级。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from backend.core.shared.interfaces.agent_runner import GitHubApiRateLimitError
from backend.engines.agent_runner.persistence.github_api_budget_json import JsonGitHubApiBudget
from backend.infrastructure.github_client import GitHubCliClient
from tests.test_github_api_budget import _NOW, _GhRunner, _budget


@pytest.fixture
def unwritable_ledger(tmp_path: Path) -> JsonGitHubApiBudget:
    (tmp_path / ".kedacode.toml").write_text(
        '[agent_runner.repository]\ngithub_repo = "example/local-fixture"\n',
        encoding="utf-8",
    )
    budget = _budget(tmp_path, flush_every_calls=1)
    # 将账本文件的位置占为目录，原子写入必定失败，无需伪造权限系统行为。
    budget.state_path.mkdir()
    return budget


def _warned_about_shared_state(caplog: pytest.LogCaptureFixture) -> bool:
    warnings = [
        record.getMessage().lower() for record in caplog.records if record.levelname == "WARNING"
    ]
    return any(
        ("ledger" in warning or "budget" in warning) and "process" in warning
        for warning in warnings
    )


def test_successful_issue_query_survives_ledger_error(
    tmp_path: Path,
    unwritable_ledger: JsonGitHubApiBudget,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = GitHubCliClient(tmp_path, _GhRunner(), api_budget=unwritable_ledger)
    assert client.list_ready_issues("agent/ready", 5) == []
    assert _warned_about_shared_state(caplog)


def test_rate_limit_takes_precedence_over_ledger_write_error(
    tmp_path: Path,
    unwritable_ledger: JsonGitHubApiBudget,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = _GhRunner(fail_issue_with="GraphQL: API rate limit already exceeded for user ID 1.")
    client = GitHubCliClient(tmp_path, runner, api_budget=unwritable_ledger)
    with pytest.raises(GitHubApiRateLimitError):
        client.list_ready_issues("agent/ready", 5)
    assert _warned_about_shared_state(caplog)


def test_direct_ledger_recording_is_best_effort(
    unwritable_ledger: JsonGitHubApiBudget,
    caplog: pytest.LogCaptureFixture,
) -> None:
    unwritable_ledger.record_call("graphql", "issue view")
    unwritable_ledger.record_rate_limited("graphql", _NOW + timedelta(minutes=5))
    unwritable_ledger.record_probe("graphql", remaining=100, limit=5000, reset_at=_NOW)
    assert _warned_about_shared_state(caplog)

"""监控用例保持真实聚合路径，只替换 GitHub 传输。"""

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_monitor import build_repository_overview
from tests.conftest import FakeGitHubClient, FakeProcessRunner


class MissingHistoryClient(FakeGitHubClient):
    def get_issue(self, issue_number):
        if issue_number == 141:
            raise RuntimeError("Not Found (HTTP 404)")
        return super().get_issue(issue_number)


def test_one_missing_issue_does_not_discard_other_current_states(tmp_path):
    client = MissingHistoryClient()
    client.close_issue(42)
    overview = build_repository_overview(
        repo_id="fixture",
        display_name="Fixture",
        enabled=True,
        config=AppConfig(),
        github_client=client,
        process_runner=FakeProcessRunner(),
        repo_path=tmp_path,
        tracked_issue_numbers=[141, 42],
    )
    assert overview.sync_error is None
    assert [issue.number for issue in overview.tracked_issues] == [42]
    assert overview.tracked_issues[0].state.lower() == "closed"


def test_all_history_missing_still_leaves_a_valid_queue_scan(tmp_path):
    overview = build_repository_overview(
        repo_id="fixture",
        display_name="Fixture",
        enabled=True,
        config=AppConfig(),
        github_client=MissingHistoryClient(),
        process_runner=FakeProcessRunner(),
        repo_path=tmp_path,
        tracked_issue_numbers=[141],
    )
    assert overview.sync_error is None
    assert not overview.tracked_issues

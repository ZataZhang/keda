"""使用冻结的旧测试夹具，验证 Console 动作到执行器选择的实际结果。"""

from backend.core.shared.models.agent_model_preset import AgentModelPreset
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary, RepositoryRunContext
from backend.core.shared.models.lifecycle_agent import LifecyclePresetsConfig
from backend.core.use_cases.agent_runner_agent_selection import (
    choose_agent,
    resolve_agent_fallback_candidate_specs,
)
from backend.core.use_cases.console_actions import execute_issue_action
from backend.core.use_cases.issue_retry_overrides import (
    IssueRetryOverrideRequest,
    prepare_issue_retry_override,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeProcessRunner
from tests.eval_support import (
    FakeGitHubClient,
    _make_supervisor,
    _fake_runner_command,
    _seed_failed_executor_run,
)


def test_console_choice_survives_preset_and_is_consumed_once(tmp_path):
    config = AppConfig(
        agent_presets={"bound": AgentModelPreset(agent="codex", model="gpt-6-luna")},
        lifecycle_presets=LifecyclePresetsConfig(repository_layer={"implementation": "bound"}),
    )
    issue = IssueSummary(123, "Broken", "https://example.test/123", "", (config.labels.failed,))
    client = FakeGitHubClient(issue)
    store = SqliteConsoleStore(tmp_path / "console.db")
    _seed_failed_executor_run(store, issue_number=123)
    request = dict(
        action="retry_failed_with_executor",
        repo_id="keda-main",
        issue_number=123,
        contexts=[
            RepositoryRunContext(
                repo_id="keda-main", display_name="Fixture", repo_path=tmp_path, config=config
            )
        ],
        github_client_factory=lambda _: client,
        supervisor=_make_supervisor(tmp_path),
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
        lifecycle_stage="implementation",
        executor="mcode",
    )
    assert execute_issue_action(**request).result == "accepted"
    assert choose_agent(issue, config, "auto") == "codex"
    other_connection = SqliteConsoleStore(tmp_path / "console.db")
    prepared, claim = prepare_issue_retry_override(
        IssueRetryOverrideRequest(
            issue=issue,
            issue_kind="ready",
            repo_id="keda-main",
            config=config,
            run_history_store=other_connection,
        )
    )
    assert choose_agent(prepared, config, "auto") == "mcode"
    assert choose_agent(prepared, config, "claude") == "claude"
    assert resolve_agent_fallback_candidate_specs(prepared, config, "claude")[0].agent == "claude"
    assert claim is not None
    claim.consume()
    assert store.get_issue_retry_override(repo_id="keda-main", issue_number=123) is None


def test_repeat_submission_is_accepted_after_first_queue_transition(tmp_path):
    config = AppConfig()
    issue = IssueSummary(123, "Broken", "https://example.test/123", "", (config.labels.ready,))
    client = FakeGitHubClient(issue)
    store = SqliteConsoleStore(tmp_path / "console.db")
    _seed_failed_executor_run(store, issue_number=123)
    store.set_issue_retry_override(
        repo_id="keda-main", issue_number=123, stage="implementation", executor="codex"
    )
    outcome = execute_issue_action(
        action="retry_failed_with_executor",
        repo_id="keda-main",
        issue_number=123,
        contexts=[
            RepositoryRunContext(
                repo_id="keda-main", display_name="Fixture", repo_path=tmp_path, config=config
            )
        ],
        github_client_factory=lambda _: client,
        supervisor=_make_supervisor(tmp_path),
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
        lifecycle_stage="implementation",
        executor="mcode",
    )
    assert outcome.result == "accepted"
    assert client.label_edits == []
    assert store.get_issue_retry_override(repo_id="keda-main", issue_number=123) == (
        "implementation",
        "mcode",
    )

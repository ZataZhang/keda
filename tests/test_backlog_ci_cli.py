"""``iar backlog ci status|policy|repair`` 的 CLI 表面测试。

三条命令都被要求"只是既有 core 用例的薄封装"，所以这里的断言全部围绕**共享事实源**
展开，而不是重复验证投影逻辑本身（那部分在
:mod:`tests.test_backlog_ci_delivery`）：

- 走真实入口：``backend.api.cli.main`` → Typer 命令树 → ``_run_parsed_command`` →
  ``dispatch_parsed_command``，不直接调处理函数；
- 仓库 registry 解析、PRD 扫描、TOML 读写、marker 解析、门禁判定全是真的；
- 只有 GitHub 与 process runner 用记录副作用的 fake（被观察的边界）；
- ``--json`` 必须与 Console 的 ``ci_delivery`` DTO 同构：同一份输入下，CLI 的 stdout
  等于 ``serialize_value(build_prd_ci_delivery(...))``，且 stdout 只有 JSON。

所有 CLI 调用都显式带 ``--repo-id``：不带选择器时仓库解析会回落到 cwd，那会碰到真实
工作区。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import backend.api.cli as cli_module
from backend.api.cli_parser import build_parser
from backend.api.serialization import serialize_value
from backend.core.shared.models.agent_runner import (
    CommandResult,
    PullRequestContext,
    RepositoryRunContext,
)
from backend.core.use_cases.agent_runner_events import format_event_marker
from backend.core.use_cases.backlog_ci_delivery import (
    build_ci_failure_key,
    build_prd_ci_delivery,
    resolve_stored_ci_repair_policy,
)
from backend.engines.agent_runner.factory_repository_resolver import resolve_repository_targets
from backend.infrastructure.config.settings import (
    AgentRunnerRepositorySettings,
    AgentRunnerSettings,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner

_REPO_ID = "cli-ci"
_ISSUE_NUMBER = 7
_PRD_PATH = "tasks/pending/P1-FEAT-20260101-ci.md"
_PR_BRANCH = f"issue-{_ISSUE_NUMBER}"
_HEAD_A = "a" * 40
_HEAD_B = "b" * 40
_FAILURE_SUMMARY = (
    "unit-tests (status=COMPLETED, conclusion=FAILURE) https://github.com/example/repo/runs/1",
)
_IAR_TOML_TEMPLATE = """[agent_runner.autopilot]
enabled = false
merge_method = "squash"

[agent_runner.safety]
auto_merge = false

[agent_runner.post_pr_supervisor]
enabled = true
max_repair_attempts = 2
"""


def _git(repo_path: Path, *git_args: str) -> None:
    subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _pr_context(**overrides: object) -> PullRequestContext:
    defaults: dict[str, object] = {
        "pr_url": f"https://github.com/example/repo/pull/{_ISSUE_NUMBER}",
        "branch": _PR_BRANCH,
        "head_sha": _HEAD_A,
        "base_sha": "d" * 40,
        "checks_state": "FAILURE",
        "checks_summary": _FAILURE_SUMMARY,
        "mergeable": True,
        "number": _ISSUE_NUMBER,
    }
    defaults.update(overrides)
    return PullRequestContext(**defaults)  # type: ignore[arg-type]


def _repair_intent(head_sha: str) -> str:
    return format_event_marker(
        phase="post_pr_rework_requested",
        cycle=1,
        head_sha=head_sha,
        pr_branch=_PR_BRANCH,
        action="repair_pr_branch",
        failure_digest="deadbeef",
    )


def _written_comments(github_client: FakeGitHubClient) -> list[str]:
    return [
        call["body"]
        for call in github_client.calls
        if call["method"] == "comment_issue" and call["issue_number"] == _ISSUE_NUMBER
    ]


#: 只读命令一条都不允许出现：观察投影不得有任何写副作用。
_WRITE_METHODS = frozenset(
    {"comment_issue", "edit_issue_labels", "edit_issue_body", "create_issue", "create_pull_request"}
)


def _mutating_calls(github_client: FakeGitHubClient) -> list[dict]:
    return [call for call in github_client.calls if call["method"] in _WRITE_METHODS]


@pytest.fixture
def cli_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """把 CLI 接到一个真实 git 仓库：真实 registry 解析 + 真实 PRD 扫描 + fake GitHub。"""
    isolated_config_path = tmp_path / "isolated-config.toml"
    isolated_config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv("IAR_CONFIG", str(isolated_config_path))

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git(repo_dir, "init", "-b", "main")
    (repo_dir / _PRD_PATH).parent.mkdir(parents=True)
    (repo_dir / _PRD_PATH).write_text(
        "# PRD: CI Feature\n\n"
        f"- GitHub Issue: https://github.com/example/repo/issues/{_ISSUE_NUMBER}\n\n"
        "## Acceptance Checklist\n\n- [ ] item\n",
        encoding="utf-8",
    )
    config_path = repo_dir / ".iar.toml"
    config_path.write_text(_IAR_TOML_TEMPLATE, encoding="utf-8")

    settings = AgentRunnerSettings(
        repositories={_REPO_ID: AgentRunnerRepositorySettings(path=str(repo_dir), id=_REPO_ID)}
    )

    github_client = FakeGitHubClient()
    github_client._issue_comments[_ISSUE_NUMBER] = [
        format_event_marker(phase="draft_pr_created", cycle=1, pr_branch=_PR_BRANCH)
    ]
    github_client.set_pr_context(_PR_BRANCH, _pr_context())

    process_runner = FakeProcessRunner(
        responses={
            ("iar", "worktree", "path", "--branch", _PR_BRANCH): CommandResult(
                command=("iar", "worktree", "path", "--branch", _PR_BRANCH),
                return_code=0,
                stdout=".\n",
                stderr="",
            )
        }
    )

    monkeypatch.setattr(cli_module, "get_agent_runner_settings", lambda: settings)
    # 写后校验必须重新解析磁盘上的配置，内存值不能当作成功判据。
    monkeypatch.setattr(cli_module, "load_fresh_agent_runner_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "create_process_runner", lambda: process_runner)
    monkeypatch.setattr(
        cli_module,
        "create_github_client",
        lambda repo_path, runner: github_client,
    )

    def run_ci_command(*args: str, repo_flag: bool = True) -> tuple[int, str, str]:
        """真实入口跑一次，返回 (退出码, stdout, stderr)；stdout 只应有数据。"""
        argv = ["backlog", "ci", *args]
        if repo_flag:
            argv += ["--repo-id", _REPO_ID]
        capsys.readouterr()
        exit_code = cli_module.main(argv)
        captured = capsys.readouterr()
        return exit_code, captured.out, captured.err

    def current_delivery():
        """用产品自己的路径取到生效上下文与投影，供同构断言复用。"""
        context = resolve_repository_targets(settings, repo_id=_REPO_ID)[0]
        delivery = build_prd_ci_delivery(
            prd_path=_PRD_PATH,
            issue_number=_ISSUE_NUMBER,
            github_client=github_client,
            config=context.config,
        )
        return context, delivery

    return {
        "repo_dir": repo_dir,
        "config_path": config_path,
        "github_client": github_client,
        "settings": settings,
        "run": run_ci_command,
        "delivery": current_delivery,
    }


# ── 参数表面：三条子命令 + 不接受客户端伪造 head/轮次 ──────────────────────


def test_parser_accepts_the_three_ci_subcommands() -> None:
    """legacy parser 认得三条子命令，且 dest 与 Typer 侧一致。"""
    parsed = build_parser().parse_args(
        ["backlog", "ci", "status", "--prd", _PRD_PATH, "--json", "--repo-id", _REPO_ID]
    )
    assert parsed.command == "backlog ci status"
    assert parsed.ci_prd_path == _PRD_PATH
    assert parsed.ci_json is True

    parsed_policy = build_parser().parse_args(["backlog", "ci", "policy", "--prd", _PRD_PATH, "on"])
    assert parsed_policy.command == "backlog ci policy"
    assert parsed_policy.ci_policy_value == "on"
    assert parsed_policy.ci_global_policy is None

    parsed_repair = build_parser().parse_args(["backlog", "ci", "repair", "--prd", _PRD_PATH])
    assert parsed_repair.command == "backlog ci repair"
    assert parsed_repair.dry_run is False


@pytest.mark.parametrize(
    ("args", "forged_flag"),
    [
        (("status", "--json"), "--head-sha"),
        (("status", "--json"), "--round"),
        (("policy", "--global", "on"), "--head-sha"),
        (("repair", "--prd", "x.md"), "--head-sha"),
        (("repair", "--prd", "x.md"), "--round"),
    ],
)
def test_cli_surface_rejects_client_forged_head_or_round(
    args: tuple[str, ...], forged_flag: str
) -> None:
    """head SHA 与轮次一律服务端判定：CLI 表面不存在能传它们的旗标。"""
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["backlog", "ci", *args, forged_flag, "deadbeef"])
    assert exit_info.value.code == 2

    assert (
        cli_module.main(["backlog", "ci", *args, forged_flag, "deadbeef", "--repo-id", _REPO_ID])
        == 2
    )


# ── FR-15 status：只读、纯 JSON、与 Console DTO 同构 ────────────────────────


def test_ci_status_json_is_isomorphic_to_the_console_dto(cli_environment) -> None:
    """``--json`` 与 Console ci_delivery 逐字段相等，且 stdout 只有 JSON。"""
    exit_code, stdout_text, _ = cli_environment["run"]("status", "--prd", _PRD_PATH, "--json")
    assert exit_code == 0

    _, delivery = cli_environment["delivery"]()
    assert delivery is not None
    # 整体相等，而不是挑几个字段：两个表面共用同一个序列化入口。
    assert json.loads(stdout_text) == serialize_value(delivery)
    assert json.loads(stdout_text)["status"] == "failing"
    assert json.loads(stdout_text)["stored_policy"] == "inherit"


def test_ci_status_json_carries_the_failures_without_any_side_effects(cli_environment) -> None:
    """只读观察不得写评论或改标签——即使 checks 处于失败态。"""
    exit_code, stdout_text, _ = cli_environment["run"]("status", "--prd", _PRD_PATH, "--json")
    assert exit_code == 0

    payload = json.loads(stdout_text)
    assert payload["checks_state"] == "FAILURE"
    assert payload["problems"][0]["name"] == "unit-tests"
    assert payload["repair_rounds"] == 0
    assert _mutating_calls(cli_environment["github_client"]) == []


def test_ci_status_without_json_is_human_readable(cli_environment) -> None:
    """人读输出把原始 checks 与三态策略结论分开呈现，且不混进 JSON。"""
    exit_code, stdout_text, _ = cli_environment["run"]("status", "--prd", _PRD_PATH)
    assert exit_code == 0

    assert "stored_policy" not in stdout_text
    assert "stored=inherit" in stdout_text
    assert "effective=off" in stdout_text
    assert "unit-tests" in stdout_text
    assert "{" not in stdout_text


def test_ci_status_lists_every_published_prd_with_the_repository_header(cli_environment) -> None:
    """不带 ``--prd`` 时给出仓库级生效值 + 该仓所有已发布 PR 的 PRD。"""
    exit_code, stdout_text, _ = cli_environment["run"]("status")
    assert exit_code == 0

    assert "auto_repair_ci=off" in stdout_text
    assert "max_repair_attempts=2" in stdout_text
    assert _PRD_PATH in stdout_text


def test_ci_status_json_on_unlinked_prd_emits_null_on_stdout(cli_environment) -> None:
    """未关联 Issue 时 JSON 仍走 stdout（``null``），提示走 stderr，退出码 0。"""
    exit_code, stdout_text, stderr_text = cli_environment["run"](
        "status", "--prd", "tasks/pending/ghost.md", "--json"
    )
    assert exit_code == 0
    assert stdout_text.strip() == "null"
    assert "还没有关联 Issue" in stderr_text


# ── FR-16 policy：两个互斥目标，各自复用 Console 的事实源 ───────────────────


def test_ci_policy_global_writes_only_the_auto_repair_key(cli_environment) -> None:
    """仓库级 ``--global on`` 只新增一个键，不牵连 autopilot / auto_merge。"""
    before_text = cli_environment["config_path"].read_text(encoding="utf-8")

    exit_code, stdout_text, _ = cli_environment["run"]("policy", "--global", "on")
    assert exit_code == 0

    after_text = cli_environment["config_path"].read_text(encoding="utf-8")
    added = [line for line in after_text.splitlines() if line not in before_text.splitlines()]
    removed = [line for line in before_text.splitlines() if line not in after_text.splitlines()]
    assert added == ["auto_repair_ci = true"]
    assert removed == []
    assert "enabled = false" in after_text
    assert "auto_merge = false" in after_text
    assert "auto_repair_ci" in stdout_text

    # 写后重新读一次：全局值立刻成为 inherit PRD 的生效值。
    _, status_stdout = cli_environment["run"]("status", "--prd", _PRD_PATH, "--json")[:2]
    payload = json.loads(status_stdout)
    assert payload["global_auto_repair"] is True
    assert payload["effective_auto_repair"] is True


def test_ci_policy_global_off_is_persisted_not_deleted(cli_environment) -> None:
    """显式关与从没设过在持久层可区分：写入 false，而不是删键。"""
    exit_code, _, _ = cli_environment["run"]("policy", "--global", "off")
    assert exit_code == 0

    assert "auto_repair_ci = false" in cli_environment["config_path"].read_text(encoding="utf-8")
    _, status_stdout = cli_environment["run"]("status", "--prd", _PRD_PATH)[:2]
    assert "global=off" in status_stdout


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--prd", _PRD_PATH),
        ("--global", "on", "--prd", _PRD_PATH, "on"),
    ],
    ids=["no-target", "prd-without-value", "both-targets"],
)
def test_ci_policy_requires_exactly_one_target(cli_environment, args: tuple[str, ...]) -> None:
    """全局值与单 PRD 覆盖互斥、且必须有一个；被拒时不写任何东西。"""
    exit_code, _, stderr_text = cli_environment["run"]("policy", *args)
    assert exit_code == 1

    assert "需要且只需要一个目标" in stderr_text
    assert "auto_repair_ci" not in cli_environment["config_path"].read_text(encoding="utf-8")
    assert _mutating_calls(cli_environment["github_client"]) == []


def test_ci_policy_rejects_an_invalid_value_without_writing(cli_environment) -> None:
    """非法策略值由共享处理层拦住：退出码 1，Issue 上没有新 marker。"""
    exit_code, _, stderr_text = cli_environment["run"]("policy", "--prd", _PRD_PATH, "maybe")
    assert exit_code == 1

    assert "只接受 inherit / on / off" in stderr_text
    assert _mutating_calls(cli_environment["github_client"]) == []


def test_ci_policy_prd_writes_the_shared_marker_and_flips_effective_value(cli_environment) -> None:
    """单 PRD 覆盖写的是 Console 用的同一种 latest-wins marker，不是另一套状态。"""
    assert cli_environment["run"]("policy", "--global", "on")[0] == 0
    assert cli_environment["run"]("policy", "--prd", _PRD_PATH, "off")[0] == 0

    comments = cli_environment["github_client"].list_issue_comments(_ISSUE_NUMBER)
    policy_writes = [
        body
        for body in _written_comments(cli_environment["github_client"])
        if "iar:ci-auto-repair-policy" in body
    ]
    assert len(policy_writes) == 1
    assert "value=off" in policy_writes[0]
    assert resolve_stored_ci_repair_policy(comments).value == "off"

    payload = json.loads(cli_environment["run"]("status", "--prd", _PRD_PATH, "--json")[1])
    assert payload["stored_policy"] == "off"
    assert payload["global_auto_repair"] is True
    assert payload["effective_auto_repair"] is False

    # inherit 回到全局值：latest-wins 靠追加新 marker，不编辑历史评论。
    assert cli_environment["run"]("policy", "--prd", _PRD_PATH, "inherit")[0] == 0
    payload = json.loads(cli_environment["run"]("status", "--prd", _PRD_PATH, "--json")[1])
    assert payload["stored_policy"] == "inherit"
    assert payload["effective_auto_repair"] is True


def test_ci_policy_prd_requires_a_linked_issue(cli_environment) -> None:
    """策略挂在 Issue 上：PRD 还没关联 Issue 时拒绝写入，不另存一份路径态。"""
    exit_code, _, stderr_text = cli_environment["run"](
        "policy", "--prd", "tasks/pending/ghost.md", "on"
    )
    assert exit_code == 1

    assert "还没有关联 Issue" in stderr_text
    assert _mutating_calls(cli_environment["github_client"]) == []


# ── FR-17 repair：与问题卡同一用例 ─────────────────────────────────────────


def test_ci_repair_dry_run_reports_the_decision_without_writing(cli_environment) -> None:
    """``--dry-run`` 给出结论与 head/digest，零副作用。"""
    exit_code, stdout_text, _ = cli_environment["run"]("repair", "--prd", _PRD_PATH, "--dry-run")
    assert exit_code == 0

    assert "dry-run" in stdout_text
    assert "decision=ci_repair_allowed" in stdout_text
    assert _HEAD_A in stdout_text
    assert "未写任何" in stdout_text
    assert _mutating_calls(cli_environment["github_client"]) == []


def test_ci_repair_writes_one_intent_then_refuses_the_same_failure(cli_environment) -> None:
    """真实修复只写一条 rework intent；同一失败重复请求被幂等拒绝。"""
    exit_code, stdout_text, _ = cli_environment["run"]("repair", "--prd", _PRD_PATH)
    assert exit_code == 0
    assert "已排队" in stdout_text

    first_writes = _written_comments(cli_environment["github_client"])
    assert len(first_writes) == 1
    assert "action=repair_pr_branch" in first_writes[0]
    assert _HEAD_A in first_writes[0]
    # 服务端自算的 digest 就是产品自己的 failure key，不是客户端传进来的值。
    assert (
        build_ci_failure_key(
            pr_number=_ISSUE_NUMBER, head_sha=_HEAD_A, checks_summary=_FAILURE_SUMMARY
        )
        in first_writes[0]
    )

    exit_code, stdout_text, _ = cli_environment["run"]("repair", "--prd", _PRD_PATH)
    assert exit_code == 1
    assert "被拒绝" in stdout_text
    assert "decision=ci_repair_duplicate" in stdout_text
    assert len(_written_comments(cli_environment["github_client"])) == 1


def test_ci_repair_refuses_when_the_attempt_budget_is_exhausted(cli_environment) -> None:
    """手动修复同样受 max_repair_attempts 约束：用尽后拒绝且零副作用。"""
    cli_environment["github_client"]._issue_comments[_ISSUE_NUMBER].extend(
        [_repair_intent(_HEAD_A), _repair_intent(_HEAD_B)]
    )

    exit_code, stdout_text, _ = cli_environment["run"]("repair", "--prd", _PRD_PATH)
    assert exit_code == 1

    assert "被拒绝" in stdout_text
    assert "decision=ci_repair_exhausted" in stdout_text
    assert "max_repair_attempts=2" in stdout_text
    assert _written_comments(cli_environment["github_client"]) == []


def test_ci_repair_requires_a_prd_target(cli_environment) -> None:
    """repair 必须指明 PRD；缺 ``--prd`` 时不落到"所有 PRD"这种模糊语义。"""
    assert cli_module.main(["backlog", "ci", "repair", "--repo-id", _REPO_ID]) == 2
    assert _mutating_calls(cli_environment["github_client"]) == []


def test_ci_status_and_repair_share_the_same_round_state(cli_environment) -> None:
    """status 投影读到的轮次就是 repair 写下去的那一条——两条命令共用同一状态。"""
    assert cli_environment["run"]("repair", "--prd", _PRD_PATH)[0] == 0

    payload = json.loads(cli_environment["run"]("status", "--prd", _PRD_PATH, "--json")[1])
    assert payload["repair_rounds"] == 1
    assert payload["last_decision"] == "ci_repair_duplicate"


def test_ci_commands_refuse_when_the_selector_resolves_to_several_repositories(
    cli_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI 命令一律针对单仓：解析出多个目标时拒绝，不跨仓库批量写。"""
    extra_context = RepositoryRunContext(
        repo_id="other",
        display_name="Other",
        repo_path=cli_environment["repo_dir"],
        config=cli_environment["delivery"]()[0].config,
    )
    monkeypatch.setattr(
        "backend.api.cli_parsed_commands.backlog._resolve_cli_repository_targets",
        lambda **kwargs: [cli_environment["delivery"]()[0], extra_context],
    )

    exit_code, _, stderr_text = cli_environment["run"]("policy", "--global", "on")
    assert exit_code == 1
    assert "requires exactly one target repository" in stderr_text
    assert "auto_repair_ci" not in cli_environment["config_path"].read_text(encoding="utf-8")
    assert _mutating_calls(cli_environment["github_client"]) == []

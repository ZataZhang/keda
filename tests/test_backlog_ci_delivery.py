"""Backlog CI/CD 投影、自动修复策略与 repair 门禁的用例测试。

覆盖链条：

- 投影与门禁（:mod:`backend.core.use_cases.backlog_ci_delivery`）是纯函数，直接断言
  三态策略合成、failure key 稳定性与轮次计数；
- 仓库级开关走真实临时 ``.iar.toml`` + 真实 TOML writer（不 mock 配置读写）；
- 单 PRD 策略与修复轮次走 Issue 评论里的真实 marker 文本，由产品自身的解析器读出；
- 自动路径的 repair 门禁跑产品自己的 ``_process_review_candidate`` 分支。

GitHub 与 process runner 按测试目标使用记录副作用的 fake：它们是被观察的边界，
不是被测逻辑。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
    PostPrSupervisorConfig,
    PullRequestContext,
    RepositoryRunContext,
)
from backend.core.shared.models.backlog import (
    CI_PROBLEM_KIND_AGGREGATE,
    CI_PROBLEM_KIND_CHECK_FAILURE,
    CI_PROBLEM_KIND_CHECK_PENDING,
    CI_PROBLEM_KIND_NOT_RUN,
    CI_PROBLEM_KIND_UNAVAILABLE,
    BacklogCiRepairPolicy,
    CiDeliveryStatus,
    CiRepairGateDecision,
)
from backend.core.use_cases.agent_runner_events import (
    build_ci_auto_repair_policy_comment,
    format_event_marker,
    parse_event_markers,
)
from backend.core.use_cases.backlog_ci_delivery import (
    REPAIR_ACTION,
    BacklogCiError,
    build_ci_delivery,
    build_ci_failure_key,
    build_prd_ci_delivery,
    compute_effective_auto_repair,
    count_ci_repair_rounds,
    evaluate_ci_repair_gate,
    load_ci_auto_repair_state,
    repair_requested_for_failure,
    request_manual_ci_repair,
    resolve_prd_issue_number,
    resolve_stored_ci_repair_policy,
    set_ci_auto_repair_enabled,
    set_prd_ci_repair_policy,
)
from backend.core.use_cases.pr_supervisor import (
    SupervisorActionResult,
    build_rework_intent_comment,
)
from backend.core.use_cases.review_once import _process_review_candidate
from backend.infrastructure.config.repository_settings_editor import (
    TomlRepositoryAutopilotSettingsEditor,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner

REPO_ID = "keda-main"
PR_BRANCH = "issue-7"
PR_NUMBER = 7
HEAD_A = "a" * 40
HEAD_B = "b" * 40
BASE_SHA = "d" * 40
FAILED_SUMMARY = (
    "unit-tests (status=COMPLETED, conclusion=FAILURE) https://github.com/example/repo/runs/1",
)
PRD_PATH = "tasks/pending/P1-FEAT-20260916-134008-example.md"


def _config(*, auto_repair: bool = False, max_attempts: int = 2) -> AppConfig:
    """构造生效配置：只动本功能相关的两个键，其余沿用产品默认值。"""
    return replace(
        AppConfig(),
        post_pr_supervisor=PostPrSupervisorConfig(
            auto_repair_ci=auto_repair,
            max_repair_attempts=max_attempts,
        ),
    )


def _draft_pr_comment() -> str:
    """让 PR 分支可被监控层解析出来（真实 marker，不是手工注入的字段）。"""
    return format_event_marker(phase="draft_pr_created", cycle=1, pr_branch=PR_BRANCH)


def _supervisor_comment(
    *,
    head_sha: str = HEAD_A,
    action: str = REPAIR_ACTION,
    checks_state: str = "FAILURE",
    issue_comments_count: int = 1,
) -> str:
    """Supervisor 观察结论 marker（其 ``action=`` 就是 Agent 的决定）。"""
    return format_event_marker(
        phase="post_pr_supervisor",
        cycle=1,
        head_sha=head_sha,
        base_sha=BASE_SHA,
        pr_branch=PR_BRANCH,
        action=action,
        checks_state=checks_state,
        mergeable=True,
        issue_comments_count=issue_comments_count,
        pr_comments_count=0,
    )


def _repair_intent_comment(*, head_sha: str, failure_digest: str | None = None) -> str:
    """修复意图评论：自动与手动两条路径写的是同一份文本。"""
    return build_rework_intent_comment(
        action=REPAIR_ACTION,
        pr_branch=PR_BRANCH,
        head_sha=head_sha,
        failure_digest=failure_digest,
    )


def _pr_context(**overrides: object) -> PullRequestContext:
    defaults: dict[str, object] = {
        "pr_url": f"https://github.com/example/repo/pull/{PR_NUMBER}",
        "branch": PR_BRANCH,
        "head_sha": HEAD_A,
        "base_sha": BASE_SHA,
        "checks_state": "FAILURE",
        "checks_summary": FAILED_SUMMARY,
        "mergeable": True,
        "number": PR_NUMBER,
    }
    defaults.update(overrides)
    return PullRequestContext(**defaults)  # type: ignore[arg-type]


class _BodyAwareGitHubClient(FakeGitHubClient):
    """Issue 正文可注入的 fake：让 PRD 路径解析走产品自己的提取逻辑。"""

    def __init__(self, issue_body: str = f"PRD path: `{PRD_PATH}`") -> None:
        super().__init__()
        self._issue_body = issue_body

    def get_issue(self, issue_number: int) -> IssueSummary:
        issue = super().get_issue(issue_number)
        return replace(issue, body=self._issue_body)


class _DropCommentsGitHubClient(FakeGitHubClient):
    """写入被吞掉的 fake：用于验证「读不回一致就必须报错」。"""

    def list_issue_comments(self, issue_number: int) -> list[str]:
        return []


def _markers(comments: list[str]) -> list:
    return parse_event_markers(comments)


def _gate(
    *,
    config: AppConfig | None = None,
    comments: list[str] | None = None,
    pr_context: PullRequestContext | None = None,
    manual: bool = False,
):
    """按产品自己的解析链做一次放行判定（不手工拼 marker 字段）。"""
    active_context = pr_context if pr_context is not None else _pr_context()
    comment_list = list(comments or [_draft_pr_comment()])
    return evaluate_ci_repair_gate(
        pr_number=active_context.number,
        head_sha=active_context.head_sha,
        checks_summary=active_context.checks_summary,
        markers=_markers(comment_list),
        stored_policy=resolve_stored_ci_repair_policy(comment_list),
        global_auto_repair=bool((config or _config()).post_pr_supervisor.auto_repair_ci),
        max_repair_attempts=int((config or _config()).post_pr_supervisor.max_repair_attempts),
        manual=manual,
    )


# ── 策略 marker 与生效值 ────────────────────────────────────────────────────


def test_policy_marker_round_trip_is_latest_wins() -> None:
    """策略以 Issue 上最新 marker 为准，清除覆盖写 inherit 而不是删除评论。"""
    comments = [
        build_ci_auto_repair_policy_comment(BacklogCiRepairPolicy.ON),
        "无关评论",
        build_ci_auto_repair_policy_comment(BacklogCiRepairPolicy.OFF),
    ]
    assert resolve_stored_ci_repair_policy(comments) is BacklogCiRepairPolicy.OFF
    assert resolve_stored_ci_repair_policy([]) is BacklogCiRepairPolicy.INHERIT
    assert (
        resolve_stored_ci_repair_policy(["<!-- iar:ci-auto-repair-policy value=nope -->"])
        is BacklogCiRepairPolicy.INHERIT
    )


@pytest.mark.parametrize(
    ("stored_policy", "global_value", "expected"),
    [
        (BacklogCiRepairPolicy.INHERIT, False, False),
        (BacklogCiRepairPolicy.INHERIT, True, True),
        (BacklogCiRepairPolicy.ON, False, True),
        (BacklogCiRepairPolicy.ON, True, True),
        (BacklogCiRepairPolicy.OFF, False, False),
        (BacklogCiRepairPolicy.OFF, True, False),
    ],
)
def test_effective_policy_six_combinations(
    stored_policy: BacklogCiRepairPolicy, global_value: bool, expected: bool
) -> None:
    """六种组合的生效值唯一由 core 计算，inherit 不等于关闭。"""
    assert (
        compute_effective_auto_repair(stored_policy=stored_policy, global_auto_repair=global_value)
        is expected
    )


# ── failure key 稳定性与轮次 ────────────────────────────────────────────────


def test_failure_key_is_stable_across_daemon_restart() -> None:
    """同一 PR/head/失败摘要必须得到同一指纹：重启后去重依赖它。"""
    first = build_ci_failure_key(
        pr_number=PR_NUMBER, head_sha=HEAD_A, checks_summary=FAILED_SUMMARY
    )
    second = build_ci_failure_key(
        pr_number=PR_NUMBER, head_sha=HEAD_A, checks_summary=FAILED_SUMMARY
    )
    assert first == second
    assert first != build_ci_failure_key(
        pr_number=PR_NUMBER, head_sha=HEAD_B, checks_summary=FAILED_SUMMARY
    )
    assert first != build_ci_failure_key(pr_number=PR_NUMBER, head_sha=HEAD_A, checks_summary=())
    assert (
        build_ci_failure_key(pr_number=PR_NUMBER, head_sha="", checks_summary=FAILED_SUMMARY) == ""
    )


def test_repair_rounds_count_distinct_heads_not_comments() -> None:
    """轮次按不同 head 计数：同一轮重入产生的重复意图不额外消耗上限。"""
    comments = [
        _repair_intent_comment(head_sha=HEAD_A),
        _repair_intent_comment(head_sha=HEAD_A),
        _repair_intent_comment(head_sha=HEAD_B),
    ]
    assert count_ci_repair_rounds(_markers(comments)) == 2
    assert count_ci_repair_rounds(_markers([_draft_pr_comment()])) == 0


def test_duplicate_repair_detected_from_failure_digest_after_restart() -> None:
    """带 failure_digest 的历史意图按指纹匹配：重启后同一轮失败不再重复修复。"""
    digest = build_ci_failure_key(
        pr_number=PR_NUMBER, head_sha=HEAD_A, checks_summary=FAILED_SUMMARY
    )
    comments = [_repair_intent_comment(head_sha=HEAD_A, failure_digest=digest)]
    markers = _markers(comments)
    assert repair_requested_for_failure(markers, failure_key=digest, head_sha=HEAD_A) is True
    # 同一 head 上出现了新的失败摘要（指纹不同）时不能被判成重复
    assert (
        repair_requested_for_failure(
            markers,
            failure_key=build_ci_failure_key(
                pr_number=PR_NUMBER,
                head_sha=HEAD_A,
                checks_summary=("other (status=COMPLETED, conclusion=FAILURE)",),
            ),
            head_sha=HEAD_A,
        )
        is False
    )


def test_legacy_intent_without_digest_falls_back_to_head_sha() -> None:
    """本功能之前写的意图没有 failure_digest：同一 head 宁可少修一次也不重复推送。"""
    comments = [_repair_intent_comment(head_sha=HEAD_A)]
    markers = _markers(comments)
    assert (
        repair_requested_for_failure(
            markers,
            failure_key=build_ci_failure_key(
                pr_number=PR_NUMBER, head_sha=HEAD_A, checks_summary=FAILED_SUMMARY
            ),
            head_sha=HEAD_A,
        )
        is True
    )
    assert repair_requested_for_failure(markers, failure_key="whatever", head_sha=HEAD_B) is False


# ── 放行门禁 ────────────────────────────────────────────────────────────────


def test_gate_denies_auto_repair_when_policy_off() -> None:
    """生效策略关闭时自动路径必须被拒，且拒绝码可区分「交人工」。"""
    gate = _gate(config=_config(auto_repair=False))
    assert gate.decision is CiRepairGateDecision.POLICY_OFF
    assert gate.allowed is False
    assert gate.effective_auto_repair is False


def test_gate_allows_manual_repair_regardless_of_policy() -> None:
    """手动请求跳过策略门禁（人已显式授权这一次），其余门禁不变。"""
    assert _gate(config=_config(auto_repair=False), manual=True).decision is (
        CiRepairGateDecision.ALLOWED
    )


def test_gate_denies_repair_when_budget_exhausted() -> None:
    """达到 max_repair_attempts 后自动与手动都停止副作用，不靠前端按钮兜底。"""
    comments = [
        _draft_pr_comment(),
        _repair_intent_comment(head_sha=HEAD_B, failure_digest="digest-1"),
        _repair_intent_comment(head_sha="c" * 40, failure_digest="digest-2"),
    ]
    exhausted = _gate(
        config=_config(auto_repair=True, max_attempts=2),
        comments=comments,
    )
    assert exhausted.repair_rounds == 2
    assert exhausted.decision is CiRepairGateDecision.BUDGET_EXHAUSTED
    assert _gate(
        config=_config(auto_repair=True, max_attempts=2), comments=comments, manual=True
    ).decision is (CiRepairGateDecision.BUDGET_EXHAUSTED)


def test_gate_denies_when_head_unresolvable() -> None:
    """读不到当前 head 时不得按过期状态修复。"""
    assert _gate(pr_context=_pr_context(head_sha="")).decision is CiRepairGateDecision.UNRESOLVED


def test_gate_denies_duplicate_failure_key() -> None:
    """同一 failure key 只放行一次：重复点击/重启不再产生副作用。"""
    digest = build_ci_failure_key(
        pr_number=PR_NUMBER, head_sha=HEAD_A, checks_summary=FAILED_SUMMARY
    )
    gate = _gate(
        config=_config(auto_repair=True),
        comments=[_repair_intent_comment(head_sha=HEAD_A, failure_digest=digest)],
    )
    assert gate.decision is CiRepairGateDecision.DUPLICATE


# ── CI 投影 ─────────────────────────────────────────────────────────────────


def test_delivery_absent_when_prd_has_no_pr() -> None:
    """没有关联 PR 时返回 None：本功能不适用，不伪造一个空状态。"""
    assert (
        build_ci_delivery(
            prd_path=PRD_PATH,
            pr_context=None,
            comments=[],
            config=_config(),
        )
        is None
    )


def test_delivery_marks_unavailable_context_without_starting_repair() -> None:
    """有 PR 分支但读不到 context：如实标不可读，既不视为通过也不启动修复。"""
    delivery = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=None,
        comments=[],
        config=_config(auto_repair=True),
        pr_branch=PR_BRANCH,
    )
    assert delivery is not None
    assert delivery.status is CiDeliveryStatus.UNAVAILABLE
    assert delivery.effective_auto_repair is True
    assert delivery.problems[0].kind == CI_PROBLEM_KIND_UNAVAILABLE
    assert "不视为通过" in delivery.detail
    # 真实读取路径（GitHub 读失败）把原因原样透出，不美化成通过
    github_client = FakeGitHubClient()
    github_client._issue_comments[PR_NUMBER] = [_draft_pr_comment()]
    github_client.set_pr_context(PR_BRANCH, None)
    fresh = build_prd_ci_delivery(
        prd_path=PRD_PATH,
        issue_number=PR_NUMBER,
        github_client=github_client,
        config=_config(auto_repair=True),
    )
    assert fresh is not None
    assert fresh.status is CiDeliveryStatus.UNAVAILABLE
    assert "不视为通过" in fresh.detail


@pytest.mark.parametrize(
    ("checks_state", "expected_status"),
    [
        ("SUCCESS", CiDeliveryStatus.PASSING),
        ("FAILURE", CiDeliveryStatus.FAILING),
        ("PENDING", CiDeliveryStatus.PENDING),
        (None, CiDeliveryStatus.NOT_RUN),
    ],
)
def test_delivery_maps_raw_checks_state(
    checks_state: str | None, expected_status: CiDeliveryStatus
) -> None:
    """原始 checks 状态与结论分开映射，零 job 不得说成代码失败。"""
    delivery = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(
            checks_state=checks_state,
            checks_summary=FAILED_SUMMARY if checks_state in ("FAILURE", "PENDING") else (),
        ),
        comments=[_draft_pr_comment()],
        config=_config(),
    )
    assert delivery is not None
    assert delivery.status is expected_status
    if expected_status is CiDeliveryStatus.NOT_RUN:
        assert delivery.problems[0].kind == CI_PROBLEM_KIND_NOT_RUN
        assert "不得当作代码失败或通过" in delivery.detail
    if expected_status is CiDeliveryStatus.FAILING:
        assert delivery.problems[0].kind == CI_PROBLEM_KIND_CHECK_FAILURE
        assert delivery.problems[0].url.endswith("/runs/1")


def test_delivery_keeps_aggregate_failure_without_inventing_jobs() -> None:
    """GitHub 只给聚合 FAILURE 时不编造 job 名。"""
    delivery = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(checks_summary=()),
        comments=[_draft_pr_comment()],
        config=_config(),
    )
    assert delivery is not None
    assert delivery.problems[0].kind == CI_PROBLEM_KIND_AGGREGATE


def test_delivery_parses_pending_check_line_as_pending_problem() -> None:
    """进行中的 check 归为等待类问题，不混进失败列表的语义里。"""
    delivery = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(
            checks_state="PENDING",
            checks_summary=("build (status=IN_PROGRESS, conclusion=None)",),
        ),
        comments=[_draft_pr_comment()],
        config=_config(),
    )
    assert delivery is not None
    assert delivery.problems[0].kind == CI_PROBLEM_KIND_CHECK_PENDING
    assert delivery.problems[0].name == "build"


def test_delivery_reports_stored_global_and_effective_separately() -> None:
    """三个值一起返回，前端与 CLI 都不再自行推断生效值。"""
    comments = [
        _draft_pr_comment(),
        build_ci_auto_repair_policy_comment(BacklogCiRepairPolicy.ON),
    ]
    delivery = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(),
        comments=comments,
        config=_config(auto_repair=False),
    )
    assert delivery is not None
    assert delivery.stored_policy is BacklogCiRepairPolicy.ON
    assert delivery.global_auto_repair is False
    assert delivery.effective_auto_repair is True
    assert delivery.policy_source == "on"
    # 反向组合：全局开 + PRD 强制关
    off_delivery = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(),
        comments=[
            _draft_pr_comment(),
            build_ci_auto_repair_policy_comment(BacklogCiRepairPolicy.OFF),
        ],
        config=_config(auto_repair=True),
    )
    assert off_delivery is not None
    assert off_delivery.effective_auto_repair is False
    inherit = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(),
        comments=[_draft_pr_comment()],
        config=_config(auto_repair=True),
    )
    assert inherit is not None
    assert inherit.policy_source == "inherit"
    assert inherit.effective_auto_repair is True


def test_delivery_reconstructs_last_decision_without_new_events() -> None:
    """最近一次放行结论从 markers 重建：读路径不制造新结论。"""
    # Agent 选了 repair 且策略关闭 → 它会被拦下
    off = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(),
        comments=[_supervisor_comment()],
        config=_config(auto_repair=False),
    )
    assert off is not None
    assert off.last_decision == CiRepairGateDecision.POLICY_OFF.value
    assert off.supervisor_action == REPAIR_ACTION
    # 已写过意图 → 同一 failure key 判为重复
    digest = off.failure_key
    dup = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(),
        comments=[
            _draft_pr_comment(),
            _supervisor_comment(),
            _repair_intent_comment(head_sha=HEAD_A, failure_digest=digest or ""),
        ],
        config=_config(auto_repair=True),
    )
    assert dup is not None
    assert dup.last_decision == CiRepairGateDecision.DUPLICATE.value
    # approve 结论不产生任何 repair 判定
    approve = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(checks_state="SUCCESS", checks_summary=()),
        comments=[_supervisor_comment(action="approve_for_human_review", checks_state="SUCCESS")],
        config=_config(auto_repair=True),
    )
    assert approve is not None
    assert approve.last_decision is None


def test_delivery_flags_exhausted_rounds_and_keeps_problems() -> None:
    """耗尽后仍然展示问题并显式说明上限，不把失败态抹成正常。"""
    comments = [
        _draft_pr_comment(),
        _repair_intent_comment(head_sha=HEAD_B, failure_digest="digest-1"),
        _repair_intent_comment(head_sha="c" * 40, failure_digest="digest-2"),
    ]
    delivery = build_ci_delivery(
        prd_path=PRD_PATH,
        pr_context=_pr_context(),
        comments=comments,
        config=_config(auto_repair=True, max_attempts=2),
    )
    assert delivery is not None
    assert delivery.repair_rounds == 2
    assert delivery.repair_exhausted is True
    assert "max_repair_attempts=2" in delivery.detail
    assert delivery.problems[0].kind == CI_PROBLEM_KIND_CHECK_FAILURE


def test_build_prd_ci_delivery_reads_fresh_issue_state() -> None:
    """单 PRD fresh 投影：PR 分支由 Issue markers 解析，不读缓存。"""
    github_client = FakeGitHubClient()
    github_client._issue_comments[7] = [
        _draft_pr_comment(),
        build_ci_auto_repair_policy_comment(BacklogCiRepairPolicy.ON),
    ]
    github_client.set_pr_context(PR_BRANCH, _pr_context())
    delivery = build_prd_ci_delivery(
        prd_path=PRD_PATH,
        issue_number=7,
        github_client=github_client,
        config=_config(auto_repair=False),
    )
    assert delivery is not None
    assert delivery.stored_policy is BacklogCiRepairPolicy.ON
    assert delivery.effective_auto_repair is True
    assert delivery.pr_branch == PR_BRANCH
    assert delivery.head_sha == HEAD_A


def test_resolve_prd_issue_number_finds_issue_from_prd_file(tmp_path: Path) -> None:
    """PRD 路径只是入口标识，Issue 号从文件里的链接解析（归档后路径会变）。"""
    pending = tmp_path / "tasks" / "pending"
    pending.mkdir(parents=True)
    (pending / "P1-FEAT-20260101-x.md").write_text(
        "# PRD: X\n\n- GitHub Issue: https://github.com/example/repo/issues/42\n\n"
        "## Acceptance Checklist\n\n- [ ] item\n",
        encoding="utf-8",
    )
    assert resolve_prd_issue_number(tmp_path, "tasks/pending/P1-FEAT-20260101-x.md") == 42
    assert resolve_prd_issue_number(tmp_path, "tasks/pending/nope.md") is None


# ── 仓库级开关：真实 TOML writer ────────────────────────────────────────────

_CONFIG_TEMPLATE = """# tmp repo config
[agent_runner.autopilot]
enabled = false
merge_method = "squash"
require_verifier_pass = true
auto_sign_off = false
merge_check_timeout_seconds = 1800

[agent_runner.safety]
auto_merge = false

[agent_runner.post_pr_supervisor]
enabled = true
max_repair_attempts = 2
"""


def _contexts_for(repo_root: Path, *, auto_repair: bool) -> list[RepositoryRunContext]:
    """把磁盘上的 auto_repair_ci 值装进生效配置，模拟 fresh loader 的结果。"""
    config = replace(
        AppConfig(),
        post_pr_supervisor=PostPrSupervisorConfig(auto_repair_ci=auto_repair),
    )
    return [
        RepositoryRunContext(
            repo_id=REPO_ID,
            display_name="tmp repo",
            repo_path=repo_root,
            config=config,
        )
    ]


def test_load_ci_state_distinguishes_unpersisted_from_effective(tmp_path: Path) -> None:
    """没有该键时持久值为 None，生效值来自配置默认：页面必须能区分两者。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".iar.toml").write_text(_CONFIG_TEMPLATE, encoding="utf-8")

    state = load_ci_auto_repair_state(
        repo_id=REPO_ID,
        contexts=_contexts_for(repo_root, auto_repair=False),
        editor=TomlRepositoryAutopilotSettingsEditor(),
    )
    assert state.auto_repair_ci is False
    assert state.persisted_auto_repair is None
    assert state.max_repair_attempts == 2
    assert state.config_source == ".iar.toml"


def test_set_ci_auto_repair_changes_only_that_key(tmp_path: Path) -> None:
    """写回只动 auto_repair_ci：不得联动 autopilot / auto_merge / fix_agent。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config_path = repo_root / ".iar.toml"
    config_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
    before = config_path.read_text(encoding="utf-8").splitlines()

    def loader():
        text = config_path.read_text(encoding="utf-8")
        return _contexts_for(repo_root, auto_repair="auto_repair_ci = true" in text)

    state = set_ci_auto_repair_enabled(
        repo_id=REPO_ID,
        enabled=True,
        editor=TomlRepositoryAutopilotSettingsEditor(),
        contexts_loader=loader,
    )
    after = config_path.read_text(encoding="utf-8").splitlines()
    added = [line for line in after if line not in before]
    removed = [line for line in before if line not in after]
    assert state.auto_repair_ci is True
    assert state.persisted_auto_repair is True
    # 本次写入只新增 auto_repair_ci 一行，没有任何既有键被改写
    assert added == ["auto_repair_ci = true"]
    assert removed == []
    assert "enabled = false" in after  # autopilot.enabled 未被连带打开
    assert "auto_merge = false" in after  # safety.auto_merge 未被连带打开


def test_set_ci_auto_repair_rejects_stale_fresh_loader(tmp_path: Path) -> None:
    """写后 fresh load 与请求值不一致必须报错，不能 200 冒充成功。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".iar.toml").write_text(_CONFIG_TEMPLATE, encoding="utf-8")

    with pytest.raises(BacklogCiError):
        set_ci_auto_repair_enabled(
            repo_id=REPO_ID,
            enabled=True,
            editor=TomlRepositoryAutopilotSettingsEditor(),
            contexts_loader=lambda: _contexts_for(repo_root, auto_repair=False),
        )


def test_set_ci_auto_repair_requires_existing_config(tmp_path: Path) -> None:
    """目标仓没有本地配置时不得凭空创建文件。"""
    repo_root = tmp_path / "repo-without-config"
    repo_root.mkdir()

    with pytest.raises(BacklogCiError):
        set_ci_auto_repair_enabled(
            repo_id=REPO_ID,
            enabled=True,
            editor=TomlRepositoryAutopilotSettingsEditor(),
            contexts_loader=lambda: _contexts_for(repo_root, auto_repair=False),
        )
    assert not (repo_root / ".iar.toml").exists()


def test_set_ci_auto_repair_rejects_unknown_repo(tmp_path: Path) -> None:
    """未知仓库标识直接报错，不落到默认仓库。"""
    with pytest.raises(BacklogCiError):
        set_ci_auto_repair_enabled(
            repo_id="nope",
            enabled=True,
            editor=TomlRepositoryAutopilotSettingsEditor(),
            contexts_loader=lambda: _contexts_for(tmp_path, auto_repair=False),
        )


def test_set_prd_ci_policy_writes_marker_and_reads_it_back() -> None:
    """策略写入的真实产物是 Issue 上的 marker，响应来自读回而不是请求回显。"""
    github_client = FakeGitHubClient()
    stored = set_prd_ci_repair_policy(
        github_client=github_client,
        issue_number=PR_NUMBER,
        policy=BacklogCiRepairPolicy.OFF,
    )
    assert stored is BacklogCiRepairPolicy.OFF
    comments = github_client.list_issue_comments(PR_NUMBER)
    assert resolve_stored_ci_repair_policy(comments) is BacklogCiRepairPolicy.OFF
    assert len(comments) == 1


def test_set_prd_ci_policy_raises_when_read_back_differs() -> None:
    """写入成功但读不回同一策略时按失败处理。"""
    with pytest.raises(BacklogCiError):
        set_prd_ci_repair_policy(
            github_client=_DropCommentsGitHubClient(),
            issue_number=PR_NUMBER,
            policy=BacklogCiRepairPolicy.ON,
        )


# ── 单次手动修复（rv-4：manual_repair / idempotent / exhausted） ────────────


def _repair_client(comments: list[str]) -> _BodyAwareGitHubClient:
    github_client = _BodyAwareGitHubClient()
    github_client._issue_comments[PR_NUMBER] = comments
    github_client.set_pr_context(PR_BRANCH, _pr_context())
    return github_client


def _repair_runner() -> FakeProcessRunner:
    return FakeProcessRunner(
        responses={
            ("iar", "worktree", "path", "--branch", f"issue-{PR_NUMBER}"): CommandResult(
                command=("iar", "worktree", "path", "--branch", f"issue-{PR_NUMBER}"),
                return_code=0,
                stdout=".\n",
                stderr="",
            )
        }
    )


def test_manual_repair_writes_single_intent_when_allowed(tmp_path: Path) -> None:
    """manual_repair：首次合法请求只写一条修复意图 + running 标签，判定在服务端完成。"""
    github_client = _repair_client([_draft_pr_comment()])
    result = request_manual_ci_repair(
        issue_number=PR_NUMBER,
        repo_path=tmp_path,
        config=_config(auto_repair=False),
        github_client=github_client,
        process_runner=_repair_runner(),
    )
    assert result.accepted is True
    assert result.decision == CiRepairGateDecision.ALLOWED.value
    assert result.head_sha == HEAD_A
    assert result.prd_path == PRD_PATH
    intents = [call for call in github_client.calls if call["method"] == "comment_issue"]
    assert len(intents) == 1
    assert f"failure_digest={result.failure_key}" in intents[0]["body"]
    assert REPAIR_ACTION in intents[0]["body"]


def test_manual_repair_is_idempotent_for_same_failure_key(tmp_path: Path) -> None:
    """idempotent：第二次相同请求零新增副作用，重复点击不重复消耗轮次。"""
    github_client = _repair_client([_draft_pr_comment()])
    runner = _repair_runner()
    first = request_manual_ci_repair(
        issue_number=PR_NUMBER,
        repo_path=tmp_path,
        config=_config(auto_repair=True),
        github_client=github_client,
        process_runner=runner,
    )
    comment_count_after_first = len(github_client._issue_comments[PR_NUMBER])
    second = request_manual_ci_repair(
        issue_number=PR_NUMBER,
        repo_path=tmp_path,
        config=_config(auto_repair=True),
        github_client=github_client,
        process_runner=runner,
    )
    assert first.accepted is True
    assert second.accepted is False
    assert second.decision == CiRepairGateDecision.DUPLICATE.value
    assert len(github_client._issue_comments[PR_NUMBER]) == comment_count_after_first


def test_manual_repair_bypasses_policy_but_not_the_other_gates(tmp_path: Path) -> None:
    """manual_repair：关闭态下人工仍可请求一次，但上限依旧生效。"""
    github_client = _repair_client([_draft_pr_comment()])
    result = request_manual_ci_repair(
        issue_number=PR_NUMBER,
        repo_path=tmp_path,
        config=_config(auto_repair=False),
        github_client=github_client,
        process_runner=_repair_runner(),
    )
    assert result.accepted is True

    comments = [
        _draft_pr_comment(),
        _repair_intent_comment(head_sha=HEAD_B, failure_digest="digest-1"),
        _repair_intent_comment(head_sha="c" * 40, failure_digest="digest-2"),
    ]
    exhausted_client = _repair_client(comments)
    exhausted = request_manual_ci_repair(
        issue_number=PR_NUMBER,
        repo_path=tmp_path,
        config=_config(auto_repair=True, max_attempts=2),
        github_client=exhausted_client,
        process_runner=_repair_runner(),
    )
    assert exhausted.accepted is False
    assert exhausted.decision == CiRepairGateDecision.BUDGET_EXHAUSTED.value
    assert not [call for call in exhausted_client.calls if call["method"] == "comment_issue"]


def test_manual_repair_exhausted_reports_zero_side_effects(tmp_path: Path) -> None:
    """exhausted：被拒时既没有评论也没有 label 变更，问题保留在投影里。"""
    comments = [
        _draft_pr_comment(),
        _repair_intent_comment(head_sha=HEAD_B, failure_digest="digest-1"),
        _repair_intent_comment(head_sha="c" * 40, failure_digest="digest-2"),
    ]
    github_client = _repair_client(comments)
    result = request_manual_ci_repair(
        issue_number=PR_NUMBER,
        repo_path=tmp_path,
        config=_config(auto_repair=True, max_attempts=2),
        github_client=github_client,
        process_runner=_repair_runner(),
    )
    assert result.accepted is False
    assert [call for call in github_client.calls if call["method"] == "comment_issue"] == []
    assert [call for call in github_client.calls if call["method"] == "edit_issue_labels"] == []


def test_manual_repair_dry_run_writes_nothing(tmp_path: Path) -> None:
    """dry-run 走同一条判定链路，但结论之外零副作用。"""
    github_client = _repair_client([_draft_pr_comment()])
    result = request_manual_ci_repair(
        issue_number=PR_NUMBER,
        repo_path=tmp_path,
        config=_config(auto_repair=True),
        github_client=github_client,
        process_runner=_repair_runner(),
        dry_run=True,
    )
    assert result.accepted is True
    assert "dry-run" in result.detail
    assert [call for call in github_client.calls if call["method"] == "comment_issue"] == []
    assert [call for call in github_client.calls if call["method"] == "edit_issue_labels"] == []


def test_manual_repair_requires_existing_worktree(tmp_path: Path) -> None:
    """没有可用 worktree 时拒绝修复，且不产生任何 Issue 副作用。"""
    github_client = _repair_client([_draft_pr_comment()])
    with pytest.raises(BacklogCiError):
        request_manual_ci_repair(
            issue_number=PR_NUMBER,
            repo_path=tmp_path / "missing-worktree",
            config=_config(auto_repair=True),
            github_client=github_client,
            process_runner=FakeProcessRunner(),
        )
    assert [call for call in github_client.calls if call["method"] == "comment_issue"] == []


def test_manual_repair_requires_fresh_pr_context(tmp_path: Path) -> None:
    """读不到当前 PR context 时不得按过期状态修复。"""
    github_client = _repair_client([_draft_pr_comment()])
    github_client.set_pr_context(PR_BRANCH, None)
    with pytest.raises(BacklogCiError):
        request_manual_ci_repair(
            issue_number=PR_NUMBER,
            repo_path=tmp_path,
            config=_config(auto_repair=True),
            github_client=github_client,
            process_runner=_repair_runner(),
        )


def test_manual_repair_requires_resolved_pr_branch(tmp_path: Path) -> None:
    """Issue 上还没有 PR 分支时给出明确错误而不是空修复。"""
    github_client = _BodyAwareGitHubClient()
    with pytest.raises(BacklogCiError):
        request_manual_ci_repair(
            issue_number=PR_NUMBER,
            repo_path=tmp_path,
            config=_config(auto_repair=True),
            github_client=github_client,
            process_runner=_repair_runner(),
        )


# ── 自动路径：review pass 的 repair 门禁 ────────────────────────────────────


def _review_issue() -> IssueSummary:
    return IssueSummary(
        number=PR_NUMBER,
        title="T",
        url="U",
        body=f"PRD path: `{PRD_PATH}`",
        labels=("agent/supervising",),
    )


def _repair_action_result() -> SupervisorActionResult:
    return SupervisorActionResult(
        action=REPAIR_ACTION,
        summary="CI 失败证据指向本 PRD 的实现",
    )


def _run_review_pass(
    *,
    config: AppConfig,
    comments: list[str],
    pr_context: PullRequestContext | None = None,
):
    """跑产品自己的 review 分支，返回 outcome 与记录到的 GitHub 副作用。"""
    github_client = FakeGitHubClient()
    github_client._remote_base_sha = BASE_SHA
    github_client._issue_comments[PR_NUMBER] = comments
    github_client.set_pr_context(PR_BRANCH, pr_context or _pr_context())
    runner = FakeProcessRunner(
        responses={
            ("git", "rev-parse", "HEAD"): CommandResult(
                command=("git", "rev-parse", "HEAD"),
                return_code=0,
                stdout=f"{HEAD_A}\n",
                stderr="",
            )
        }
    )
    with (
        patch(
            "backend.core.use_cases.review_once.create_or_reuse_worktree",
            return_value=Path("."),
        ),
        patch(
            "backend.core.use_cases.review_once.resolve_supervisor_agent",
            return_value="codex",
        ),
        patch(
            "backend.core.use_cases.review_once.run_post_pr_supervisor_cycle",
            return_value=_repair_action_result(),
        ),
    ):
        outcome = _process_review_candidate(
            issue=_review_issue(),
            repo_path=Path("."),
            config=config,
            agent="auto",
            github_client=github_client,
            process_runner=runner,
        )
    return outcome, github_client


def _supervised_comments() -> list[str]:
    """让 context 判定为「已变化」，从而真正进入 Agent 决策分支。"""
    return [
        _draft_pr_comment(),
        _supervisor_comment(checks_state="PENDING", head_sha="e" * 40, issue_comments_count=1),
    ]


def test_review_pass_repairs_when_auto_repair_enabled() -> None:
    """策略开启时 Agent 的 repair 决定照常进入既有修复路径，并带上 failure digest。"""
    outcome, github_client = _run_review_pass(
        config=_config(auto_repair=True),
        comments=_supervised_comments(),
    )
    assert outcome == f"queued_{REPAIR_ACTION}"
    intents = [call for call in github_client.calls if call["method"] == "comment_issue"]
    assert len(intents) == 1
    assert "failure_digest=" in intents[0]["body"]


def test_review_pass_denies_repair_with_zero_side_effects_when_policy_off() -> None:
    """关闭态：可以运行 Supervisor 判断，但不执行自动 repair/label/提交。"""
    outcome, github_client = _run_review_pass(
        config=_config(auto_repair=False),
        comments=_supervised_comments(),
    )
    assert outcome == f"skipped_{CiRepairGateDecision.POLICY_OFF.value}"
    assert [call for call in github_client.calls if call["method"] == "comment_issue"] == []
    assert [call for call in github_client.calls if call["method"] == "edit_issue_labels"] == []


def test_review_pass_stops_repair_when_rounds_exhausted() -> None:
    """耗尽后停止自动副作用并保持问题状态，不另起一轮。"""
    comments = _supervised_comments() + [
        _repair_intent_comment(head_sha=HEAD_A, failure_digest="digest-1"),
        _repair_intent_comment(head_sha="f" * 40, failure_digest="digest-2"),
    ]
    outcome, github_client = _run_review_pass(
        config=_config(auto_repair=True, max_attempts=2),
        comments=comments,
    )
    assert outcome == f"skipped_{CiRepairGateDecision.BUDGET_EXHAUSTED.value}"
    assert [call for call in github_client.calls if call["method"] == "comment_issue"] == []


def test_review_pass_does_not_requeue_the_same_failure_key() -> None:
    """同一 failure key 在重启或重入后不再修复：意图评论已存在即视为处理过。"""
    digest = build_ci_failure_key(
        pr_number=PR_NUMBER, head_sha=HEAD_A, checks_summary=FAILED_SUMMARY
    )
    comments = _supervised_comments() + [
        _repair_intent_comment(head_sha=HEAD_A, failure_digest=digest)
    ]
    outcome, github_client = _run_review_pass(
        config=_config(auto_repair=True),
        comments=comments,
    )
    assert outcome == f"skipped_{CiRepairGateDecision.DUPLICATE.value}"
    assert [call for call in github_client.calls if call["method"] == "comment_issue"] == []


def test_review_pass_leaves_non_repair_actions_ungated() -> None:
    """门禁只约束 repair：Agent 选择等待 checks 时，关闭态不产生任何干预。"""
    github_client = FakeGitHubClient()
    github_client._remote_base_sha = BASE_SHA
    github_client._issue_comments[PR_NUMBER] = _supervised_comments()
    github_client.set_pr_context(PR_BRANCH, _pr_context())
    runner = FakeProcessRunner(
        responses={
            ("git", "rev-parse", "HEAD"): CommandResult(
                command=("git", "rev-parse", "HEAD"),
                return_code=0,
                stdout=f"{HEAD_A}\n",
                stderr="",
            )
        }
    )
    with (
        patch(
            "backend.core.use_cases.review_once.create_or_reuse_worktree",
            return_value=Path("."),
        ),
        patch(
            "backend.core.use_cases.review_once.resolve_supervisor_agent",
            return_value="codex",
        ),
        patch(
            "backend.core.use_cases.review_once.run_post_pr_supervisor_cycle",
            return_value=SupervisorActionResult(action="wait_for_checks", summary="still running"),
        ),
    ):
        outcome = _process_review_candidate(
            issue=_review_issue(),
            repo_path=Path("."),
            config=_config(auto_repair=False),
            agent="auto",
            github_client=github_client,
            process_runner=runner,
        )
    assert outcome == "waiting_for_checks"
    assert [call for call in github_client.calls if call["method"] == "comment_issue"] == []


def test_review_pass_leaves_rebase_ungated_when_policy_off() -> None:
    """关闭态不得连带阻塞 rebase：门禁只约束 Agent 选出的 repair 动作。

    ``mergeable=False`` 时产品自身的守卫会把等待类动作改写成 rebase，这条链路
    与 ``auto_repair_ci`` 无关，必须照常排队。
    """
    github_client = FakeGitHubClient()
    github_client._remote_base_sha = BASE_SHA
    github_client._issue_comments[PR_NUMBER] = _supervised_comments()
    github_client.set_pr_context(PR_BRANCH, _pr_context(mergeable=False))
    runner = FakeProcessRunner(
        responses={
            ("git", "rev-parse", "HEAD"): CommandResult(
                command=("git", "rev-parse", "HEAD"),
                return_code=0,
                stdout=f"{HEAD_A}\n",
                stderr="",
            )
        }
    )
    with (
        patch(
            "backend.core.use_cases.review_once.create_or_reuse_worktree",
            return_value=Path("."),
        ),
        patch(
            "backend.core.use_cases.review_once.resolve_supervisor_agent",
            return_value="codex",
        ),
        patch(
            "backend.core.use_cases.review_once.run_post_pr_supervisor_cycle",
            return_value=SupervisorActionResult(action="wait_for_checks", summary="still running"),
        ),
    ):
        outcome = _process_review_candidate(
            issue=_review_issue(),
            repo_path=Path("."),
            config=_config(auto_repair=False),
            agent="auto",
            github_client=github_client,
            process_runner=runner,
        )
    assert outcome == "queued_rebase_pr_branch"
    intents = [call for call in github_client.calls if call["method"] == "comment_issue"]
    assert len(intents) == 1
    # rebase 意图不携带 failure digest：它不属于本门禁统计的修复轮次
    assert "failure_digest=" not in intents[0]["body"]

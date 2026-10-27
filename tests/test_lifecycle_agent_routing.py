"""生命周期 Agent 消费点接线的测试。

覆盖：

- 各阶段既有解析函数（``choose_agent`` / ``resolve_reviewer_agent`` /
  ``resolve_supervisor_agent`` / ``_choose_verifier_agent``）显式声明时改用矩阵值，
  声明 ``auto`` 或未声明时保持既有语义；
- rv-2：执行循环把矩阵解析出的 agent 真正传给 ``run_fix_agent`` /
  ``run_closeout_agent``（含默认跟随实现者的对照）。
- daemon 的 blocked 续作路径：认领后的新鲜快照仍带着 PRD 级覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
from backend.core.shared.models.agent_runner import (
    AppConfig,
    DeliveryGateFailureKind,
    IssueSummary,
    RunnerConfig,
)
from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
from backend.core.use_cases import agent_review as agent_review_module
from backend.core.use_cases import agent_runner_delivery_closeout as delivery_closeout_module
from backend.core.use_cases import (
    agent_runner_orchestration_runtime as orchestration_runtime_module,
)
from backend.core.use_cases import run_agent_execution_loop as execution_loop_module
from backend.core.use_cases.agent_runner_events import format_event_marker
from backend.core.use_cases.agent_runner_feedback import (
    PrdDeliveryError,
    VerificationFailedError,
)
from backend.core.use_cases.run_agent_once import (
    choose_agent,
    resolve_reviewer_agent,
    resolve_supervisor_agent,
)
from backend.core.use_cases.run_verifier_agent import _choose_verifier_agent
from tests.conftest import FakeGitHubClient, FakeProcessRunner

_ISSUE = IssueSummary(
    number=7,
    title="t",
    url="https://example/7",
    body="Body",
    labels=(),
)


def _matrix_config(**lifecycle_values: str) -> AppConfig:
    return AppConfig(lifecycle_agents=LifecycleAgentsConfig(global_layer=dict(lifecycle_values)))


# ── 函数级接线 ───────────────────────────────────────────────────────────────


def test_resolve_reviewer_agent_honors_matrix_value() -> None:
    """矩阵显式声明 review 时用它，而不是 ``pre_pr_review.review_agent``。"""
    config = _matrix_config(review="kimi")
    assert resolve_reviewer_agent(_ISSUE, config, "codex") == "kimi"


def test_resolve_reviewer_agent_keeps_auto_semantics_when_auto_declared() -> None:
    """矩阵声明 auto 时保持既有语义（allow_same_agent 为真沿用实现者）。"""
    config = _matrix_config(review="auto")
    assert resolve_reviewer_agent(_ISSUE, config, "codex") == "codex"


def test_resolve_reviewer_agent_honors_prd_override() -> None:
    """PRD 覆盖比矩阵更优先。"""
    config = _matrix_config(review="kimi")
    overrides = {"review": "claude"}
    assert resolve_reviewer_agent(_ISSUE, config, "codex", prd_overrides=overrides) == "claude"


def test_resolve_supervisor_agent_honors_matrix_value() -> None:
    """矩阵显式声明 supervisor 时用它。"""
    config = _matrix_config(supervisor="pi")
    assert resolve_supervisor_agent(_ISSUE, config, "auto", fallback_agent="codex") == "pi"


def test_resolve_supervisor_agent_cli_override_still_wins() -> None:
    """命令行 ``--agent`` 仍高于矩阵。"""
    config = _matrix_config(supervisor="pi")
    assert resolve_supervisor_agent(_ISSUE, config, "claude", fallback_agent="codex") == "claude"


def test_choose_verifier_agent_honors_matrix_value() -> None:
    """矩阵显式声明 verifier 时用它，即使 manifest 给出了 builder。"""
    config = _matrix_config(verifier="kimi")
    assert _choose_verifier_agent(config, "codex") == "kimi"


def test_choose_verifier_agent_auto_prefers_different_agent() -> None:
    """矩阵声明 auto 时保持"挑第一个 ≠ 实现者"的既有语义。"""
    config = _matrix_config(verifier="auto")
    assert _choose_verifier_agent(config, "claude") == "kimi"


def test_choose_agent_honors_matrix_implementation_value() -> None:
    """矩阵 implementation 替换 default_agent 回落层。"""
    config = _matrix_config(implementation="pi")
    assert choose_agent(_ISSUE, config, "auto") == "pi"


def test_choose_agent_label_route_still_wins_over_matrix() -> None:
    """Issue 上的 agent 标签路由高于矩阵 implementation。"""
    config = _matrix_config(implementation="pi")
    labelled_issue = IssueSummary(
        number=7,
        title="t",
        url="https://example/7",
        body="Body",
        labels=("agent/kimi",),
    )
    assert choose_agent(labelled_issue, config, "auto") == "kimi"


def test_choose_agent_cli_override_still_wins_over_matrix() -> None:
    """CLI / loop recipe 的显式 override 高于矩阵。"""
    config = _matrix_config(implementation="pi")
    assert choose_agent(_ISSUE, config, "claude") == "claude"


# ── rv-2：执行循环真正切换 fix / closeout 的 agent ──────────────────────────


class _CapturingFixAgent:
    """捕获 ``run_fix_agent`` 的 agent_name 后抛出验证失败异常。"""

    def __init__(self) -> None:
        self.captured_agent: str | None = None

    def __call__(
        self,
        agent_name,
        issue,
        worktree_path,
        config,
        process_runner,
        verification_results,
        model_selection=None,
    ):
        self.captured_agent = agent_name
        raise VerificationFailedError([])


class _CapturingCloseoutAgent:
    """捕获 ``run_closeout_agent`` 的 agent_name。"""

    def __init__(self) -> None:
        self.captured_agent: str | None = None

    def __call__(self, agent_name, config, process_runner, *, prompt_context, model_selection=None):
        self.captured_agent = agent_name

        class _Result:
            return_code = 0
            stdout = ""
            stderr = ""

        return _Result()


def _patch_loop_passthrough_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """把执行循环里与本次断言无关的门禁替身成 no-op。"""
    monkeypatch.setattr(
        execution_loop_module, "run_verification", lambda worktree, config, runner: []
    )
    monkeypatch.setattr(execution_loop_module, "has_changes", lambda worktree, runner: True)
    monkeypatch.setattr(execution_loop_module, "ensure_prd_delivery_ready", lambda *a, **k: None)
    monkeypatch.setattr(
        execution_loop_module, "ensure_validation_evidence_ready", lambda *a, **k: None
    )
    monkeypatch.setattr(
        execution_loop_module, "ensure_no_misplaced_evidence_helpers", lambda *a, **k: None
    )
    monkeypatch.setattr(
        execution_loop_module, "ensure_validation_commands_pass", lambda *a, **k: None
    )
    monkeypatch.setattr(execution_loop_module, "warn_legacy_evidence_helpers", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.core.use_cases.run_verifier_agent.run_verifier_gate", lambda *a, **k: None
    )


def _run_loop_with_fix_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    capture: _CapturingFixAgent,
) -> None:
    """驱动执行循环走到"staged 验证失败 -> Fix Agent"分支。"""

    def _raising_commit(*args, **kwargs):
        raise VerificationFailedError([])

    monkeypatch.setattr(execution_loop_module, "commit_requested_changes", _raising_commit)
    monkeypatch.setattr(execution_loop_module, "run_fix_agent", capture)
    monkeypatch.setattr(execution_loop_module, "unstage_changes", lambda *a, **k: None)
    _patch_loop_passthrough_gates(monkeypatch)

    with pytest.raises(Exception):
        execution_loop_module.run_agent_until_committed(
            execution_loop_module.AgentExecutionRequest(
                selected_agent="codex",
                issue=_ISSUE,
                worktree_path=tmp_path,
                config=config,
                process_runner=FakeProcessRunner(),
                before_sha="0" * 40,
                expected_branch="issue-7",
                github_client=FakeGitHubClient(),
            )
        )


def test_choose_agent_honors_issue_carried_prd_override() -> None:
    """R1 回归：Issue 携带的 PRD 覆盖能改变实现阶段 agent（行为样例表第 3 行）。"""
    config = _matrix_config()
    issue_with_override = IssueSummary(
        number=7,
        title="t",
        url="https://example/7",
        body="Body",
        labels=(),
        lifecycle_overrides=(("implementation", "claude"),),
    )
    assert choose_agent(issue_with_override, config, "auto") == "claude"


def test_label_route_still_wins_over_issue_carried_prd_override() -> None:
    """实现阶段的 Issue 标签路由仍高于 PRD 覆盖（PRD §6：label/loop 语义不变）。

    PRD 覆盖作用于"无标签路由时的实现阶段选择"；一旦 Issue 上挂了 agent 标签，
    标签路由（以及 loop recipe / CLI override）仍然是最高优先级。
    """
    config = _matrix_config()
    issue_with_override = IssueSummary(
        number=7,
        title="t",
        url="https://example/7",
        body="Body",
        labels=("agent/kimi",),
        lifecycle_overrides=(("implementation", "claude"),),
    )
    assert choose_agent(issue_with_override, config, "auto") == "kimi"


def test_issue_carried_prd_override_applies_without_label_route() -> None:
    """无标签路由时，PRD 覆盖决定实现阶段 agent（行为样例表第 3 行的实际路径）。"""
    config = _matrix_config()
    issue_with_override = IssueSummary(
        number=7,
        title="t",
        url="https://example/7",
        body="Body",
        labels=(),
        lifecycle_overrides=(("implementation", "claude"),),
    )
    assert choose_agent(issue_with_override, config, "auto") == "claude"


def test_resolve_reviewer_agent_honors_issue_carried_prd_override() -> None:
    """R1 回归：同一 PRD 头部声明 review 覆盖后审核阶段生效（行为样例表第 4 行）。"""
    config = _matrix_config()
    issue_with_override = IssueSummary(
        number=7,
        title="t",
        url="https://example/7",
        body="Body",
        labels=(),
        lifecycle_overrides=(("review", "kimi"),),
    )
    assert resolve_reviewer_agent(issue_with_override, config, "codex") == "kimi"


def test_resolve_supervisor_agent_honors_issue_carried_prd_override() -> None:
    """R1 回归：监督阶段的 PRD 覆盖同样生效。"""
    config = _matrix_config()
    issue_with_override = IssueSummary(
        number=7,
        title="t",
        url="https://example/7",
        body="Body",
        labels=(),
        lifecycle_overrides=(("supervisor", "kimi"),),
    )
    assert (
        resolve_supervisor_agent(issue_with_override, config, "auto", fallback_agent="codex")
        == "kimi"
    )


def test_attach_prd_lifecycle_overrides_reads_prd_header(tmp_path: Path) -> None:
    """编排入口回填：从该 Issue 引用的 PRD 文件头部解析覆盖。"""
    from backend.core.use_cases.lifecycle_agent_resolution import (
        attach_prd_lifecycle_overrides,
    )

    repo_path = tmp_path / "repo"
    prd_path = repo_path / "tasks" / "pending" / "P1-FEAT-20260101-demo.md"
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text(
        "# PRD: Demo\n\n- GitHub Issue: x\n\n"
        "- lifecycle_agents:\n  - implementation: claude\n  - review: kimi\n\n"
        "## 1. Intro\n正文\n",
        encoding="utf-8",
    )
    issue = IssueSummary(
        number=9,
        title="t",
        url="https://example/9",
        body="Body\n\n- PRD path: `tasks/pending/P1-FEAT-20260101-demo.md`\n",
        labels=(),
    )
    enriched = attach_prd_lifecycle_overrides(issue, repo_path)
    assert dict(enriched.lifecycle_overrides) == {
        "implementation": "claude",
        "review": "kimi",
    }
    # 解析失败/文件缺失时原样返回，不打断流水线。
    missing_prd_issue = IssueSummary(
        number=10,
        title="t",
        url="https://example/10",
        body="Body\n\n- PRD path: `tasks/pending/does-not-exist.md`\n",
        labels=(),
    )
    assert attach_prd_lifecycle_overrides(missing_prd_issue, repo_path).lifecycle_overrides == ()


def test_fix_agent_defaults_to_selected_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rv-2 对照：未声明矩阵时 Fix Agent 用实现者。"""
    capture = _CapturingFixAgent()
    config = AppConfig(runner=RunnerConfig(max_recovery_attempts=0, fix_agent_enabled=True))
    _run_loop_with_fix_failure(tmp_path, monkeypatch, config, capture)
    assert capture.captured_agent == "codex"


def test_fix_agent_uses_matrix_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rv-2：矩阵 fix=kimi 时 Fix Agent 用 kimi。"""
    capture = _CapturingFixAgent()
    config = AppConfig(
        runner=RunnerConfig(max_recovery_attempts=0, fix_agent_enabled=True),
        lifecycle_agents=LifecycleAgentsConfig(global_layer={"fix": "kimi"}),
    )
    _run_loop_with_fix_failure(tmp_path, monkeypatch, config, capture)
    assert capture.captured_agent == "kimi"


def test_closeout_agent_uses_matrix_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rv-2：矩阵 closeout=pi 时收尾阶段用 pi。"""
    capture = _CapturingCloseoutAgent()
    config = AppConfig(
        runner=RunnerConfig(max_recovery_attempts=0),
        lifecycle_agents=LifecycleAgentsConfig(global_layer={"closeout": "pi"}),
    )
    call_count = {"value": 0}

    def _prd_gate(*args, **kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            # 第一次失败 -> 触发收尾层；收尾里的重跑通过。
            raise PrdDeliveryError(
                "delivery gate failed for test",
                kind=DeliveryGateFailureKind.CHECKLIST_UNCHECKED,
            )
        return None

    # 交付门禁与证据门禁在执行循环与收尾层各有一份模块引用：两处都要打桩，
    # 否则跨模块拆分后会有一侧仍走真实实现。
    for module in (execution_loop_module, delivery_closeout_module):
        monkeypatch.setattr(module, "ensure_prd_delivery_ready", _prd_gate)
        monkeypatch.setattr(module, "ensure_validation_evidence_ready", lambda *a, **k: None)
        monkeypatch.setattr(module, "ensure_no_misplaced_evidence_helpers", lambda *a, **k: None)
        monkeypatch.setattr(module, "ensure_validation_commands_pass", lambda *a, **k: None)
    # 收尾专属的协作对象搬到了 agent_runner_delivery_closeout，桩点随之转移。
    monkeypatch.setattr(delivery_closeout_module, "run_closeout_agent", capture)
    monkeypatch.setattr(
        delivery_closeout_module, "capture_closeout_snapshot", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        delivery_closeout_module, "find_closeout_scope_violations", lambda *a, **k: ()
    )
    monkeypatch.setattr(
        delivery_closeout_module, "summarize_closeout_changes", lambda *a, **k: "summary"
    )
    monkeypatch.setattr(
        delivery_closeout_module, "build_closeout_allowed_scope", lambda *a, **k: ()
    )
    monkeypatch.setattr(execution_loop_module, "run_verification", lambda *a, **k: [])
    monkeypatch.setattr(execution_loop_module, "has_changes", lambda *a, **k: True)

    def _raising_commit(*args, **kwargs):
        raise VerificationFailedError([])

    monkeypatch.setattr(execution_loop_module, "commit_requested_changes", _raising_commit)
    monkeypatch.setattr(execution_loop_module, "run_fix_agent", lambda *a, **k: None)
    monkeypatch.setattr(execution_loop_module, "unstage_changes", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.core.use_cases.run_verifier_agent.run_verifier_gate", lambda *a, **k: None
    )

    with pytest.raises(Exception):
        execution_loop_module.run_agent_until_committed(
            execution_loop_module.AgentExecutionRequest(
                selected_agent="codex",
                issue=_ISSUE,
                worktree_path=tmp_path,
                config=config,
                process_runner=FakeProcessRunner(),
                before_sha="0" * 40,
                expected_branch="issue-7",
                github_client=FakeGitHubClient(),
            )
        )
    assert capture.captured_agent == "pi"


# ── 消费点防漏守卫（AST） ────────────────────────────────────────────────────


def test_fix_and_closeout_call_sites_resolve_through_lifecycle_function() -> None:
    """AST 守卫：fix / closeout 的 agent 入参必须来自 ``resolve_lifecycle_agent``。

    扫描整个 ``core/use_cases/`` 目录（不只执行循环一个文件），任何新增的
    ``run_fix_agent`` / ``run_closeout_agent`` 调用点若绕过解析函数都会失败。
    这是"新增阶段消费点必须走解析函数"的防漏守卫，与
    ``test_agent_config_consistency.test_every_agent_invocation_call_site_passes_config``
    同型（放在普通测试文件而非 ``tests/guards/``，沿用既有先例）。

    Raises:
        AssertionError: 出现绕过解析函数直接传 ``selected_agent`` 的新调用点。
    """
    import ast

    use_cases_directory = Path(execution_loop_module.__file__).parent
    guarded_callees = {"run_fix_agent", "run_closeout_agent"}
    call_sites: list[tuple[Path, ast.Call]] = []
    for module_path in sorted(use_cases_directory.glob("*.py")):
        module_tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(module_tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = node.func.id if isinstance(node.func, ast.Name) else None
            if function_name in guarded_callees:
                call_sites.append((module_path, node))

    assert call_sites, "AST 守卫没有找到任何 fix / closeout 调用点，会静默通过。"

    for module_path, call_site in call_sites:
        first_argument = call_site.args[0] if call_site.args else None
        assert isinstance(first_argument, ast.Call), (
            f"{module_path}:{call_site.lineno} 的 agent 入参不是一次调用——"
            "fix / closeout 必须经 resolve_lifecycle_agent 解析后再传入。"
        )
        called_name = (
            first_argument.func.id
            if isinstance(first_argument.func, ast.Name)
            else getattr(first_argument.func, "attr", None)
        )
        assert called_name == "resolve_lifecycle_agent", (
            f"{module_path}:{call_site.lineno} 的 agent 入参来自 '{called_name}'，"
            "必须先经 resolve_lifecycle_agent 解析。"
        )


def test_agent_review_uses_matrix_aware_reviewer_resolution() -> None:
    """review 阶段消费点仍走 ``resolve_reviewer_agent``（矩阵/PRD 覆盖集中在那里）。"""
    source_text = Path(agent_review_module.__file__).read_text(encoding="utf-8")
    assert "resolve_reviewer_agent" in source_text


def test_orchestration_entry_attaches_prd_overrides() -> None:
    """接线守卫：编排入口必须把 PRD 覆盖回填到 Issue。

    实现 / 审核 / 监督三个阶段依赖 Issue 上回填的 ``lifecycle_overrides``；若
    ``_process_single_issue`` 不再调用 ``attach_prd_lifecycle_overrides``，这三个阶段
    的 PRD 级覆盖会静默失效（round-2 复核的 R4：此前只有注入字段的测试，没有接线测试）。
    """
    import ast
    import importlib

    runtime_module = importlib.import_module(
        "backend.core.use_cases.agent_runner_orchestration_runtime"
    )
    module_tree = ast.parse(Path(runtime_module.__file__).read_text(encoding="utf-8"))
    target_function = next(
        node
        for node in ast.walk(module_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_process_single_issue"
    )
    called_names = {
        call.func.id
        for call in ast.walk(target_function)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }
    assert "attach_prd_lifecycle_overrides" in called_names
    assert "choose_agent" in called_names


def test_blocked_resolution_claim_keeps_prd_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行为护点：blocked 续作接管认领后的新鲜快照时，PRD 级覆盖必须留在 Issue 上。

    上面的 AST 守卫只看得到「入口调过 attach」，看不出快照替换把回填字段抹掉：
    ``claim_blocked_issue`` 返回的是 ``get_issue`` 的结果，两个 lifecycle 字段恒为默认
    空值。实现（``_process_blocked_resolution`` 用交接的 Issue 重跑 ``choose_agent``）、
    review（``resolve_reviewer_agent(issue, ...)``）与监督（
    ``resolve_lifecycle_agent("supervisor", ..., issue=issue)``）都只读 Issue 上回填的
    字段、没有显式 prd_overrides 入参，被抹掉后是静默回落到默认 agent 而不是报错。
    """
    repo_path = tmp_path / "repo"
    prd_relative_path = "tasks/pending/P1-FEAT-20260101-blocked-demo.md"
    prd_path = repo_path / prd_relative_path
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text(
        "# PRD: Blocked demo\n\n- GitHub Issue: x\n\n"
        "- lifecycle_agents:\n  - implementation: claude\n  - review: kimi\n"
        "  - supervisor: kimi\n\n"
        "- lifecycle_presets:\n  - review: strict\n\n"
        "## 1. Intro\n正文\n",
        encoding="utf-8",
    )
    config = AppConfig()
    client = FakeGitHubClient()
    client.set_issue_labels(9, (config.labels.blocked,))
    client.set_issue_body(9, f"Body\n\n- PRD path: `{prd_relative_path}`\n")
    client.comment_issue(
        9,
        format_event_marker(
            phase="blocked_resolution_requested",
            cycle=1,
            blocked_paths=(".env.local",),
        ),
    )
    discovered_issue = IssueSummary(
        number=9,
        title="Issue #9",
        url="https://example/9",
        body=f"Body\n\n- PRD path: `{prd_relative_path}`\n",
        labels=(config.labels.blocked,),
    )
    handed_off_issues: list[IssueSummary] = []

    def _capture_blocked_resolution(**kwargs: object) -> None:
        handed_off_issues.append(kwargs["issue"])

    def _run_first_candidate(*, process_for_agent, **_kwargs: object) -> str:
        process_for_agent(agent="claude")
        return "claude"

    monkeypatch.setattr(
        orchestration_runtime_module, "_process_blocked_resolution", _capture_blocked_resolution
    )
    monkeypatch.setattr(
        orchestration_runtime_module, "run_issue_with_agent_fallback", _run_first_candidate
    )

    assert (
        orchestration_runtime_module._process_single_issue(
            discovered_issue,
            "blocked_resolution",
            repo_path=repo_path,
            config=config,
            agent="auto",
            github_client=client,
            process_runner=FakeProcessRunner(),
            content_generator=None,
            run_history_store=None,
            run_trigger="cli_run",
            effective_repo_id="repo",
            output_view=NoOpRunnerLiveView(),
        )
        == 0
    )

    assert handed_off_issues, "blocked_resolution 没有把 Issue 交给续作处理器"
    (handled_issue,) = handed_off_issues
    assert dict(handled_issue.lifecycle_overrides) == {
        "implementation": "claude",
        "review": "kimi",
        "supervisor": "kimi",
    }
    assert dict(handled_issue.lifecycle_preset_overrides) == {"review": "strict"}
    # 交接基线仍是认领后的新鲜状态：blocked 已翻成 running，不会把旧标签当成基线。
    assert config.labels.running in handled_issue.labels
    assert config.labels.blocked not in handled_issue.labels

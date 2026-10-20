"""启动前可用性门禁的行为测试（PRD rv-2 的自动化部分）。

门禁的契约有两面：**可用选择绝不被拦截**，**缺失选择必须在领取 Issue 之前被拦
截**——门禁的意义在于"没装"绝不能变成悄悄换人或一条 Issue 认领痕迹。因此这里的
断言成对出现：一次调用是否抛错，以及抛错时真实入口有没有留下副作用（GitHub 客
户端调用、worktree、认领标记、billed attempt）。PATH 判定换成受控集合，测试自己
声明这台机器装了哪些 CLI。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from backend.api.cli_exit_codes import ExitCode, translate_exit_code
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.shared.models.agent_spec import AGENT_PROFILE_RUN
from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
from backend.core.use_cases import agent_onboarding
from backend.core.use_cases.agent_invocation_tracing import (
    FAILURE_AGENT_UNAVAILABLE,
    PHASE_IMPLEMENTATION,
    classify_invocation_failure,
)
from backend.core.use_cases.agent_onboarding import (
    PrimaryAgentNotInstalledError,
    assert_run_gate,
    resolve_run_gate_block,
)
from backend.core.use_cases.agent_runner_failure import AgentUnavailableError
from backend.core.use_cases.agent_runner_orchestrate import run_issue_with_agent_fallback
from backend.core.use_cases.run_agent_once import run_agent_with_prompt
from backend.core.use_cases.run_agent_repositories_once import run_agent_repositories_once
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_availability import install_bins, make_config as _config, make_spec
from tests.support.agent_runner import config_with_review_disabled, make_prd_issue

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def path_lookup(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """受控 PATH：测试自己声明这台机器装了哪些 bin。"""
    return install_bins(monkeypatch)


def _config_binding_implementation(agent_name: str) -> AppConfig:
    """把实现阶段绑到指定 agent 的生效配置（内存构造，不读磁盘）。"""
    return _config(lifecycle_agents={"implementation": agent_name})


class _ClaimSentinel(Exception):
    """标记"流程已经走到认领这一步"的哨兵异常。"""


# ---------------------------------------------------------------------------
# 门禁判定：预设、矩阵、覆盖的解析口径
# ---------------------------------------------------------------------------


def test_gate_passes_for_default_config(path_lookup: set[str]) -> None:
    """未声明任何具体 agent（default auto）时放行：门禁不得拦截可运行的默认配置。"""
    assert resolve_run_gate_block(_config()) is None
    assert_run_gate(_config())


def test_gate_passes_when_declared_agent_is_installed(path_lookup: set[str]) -> None:
    """可用 agent 不被拦截——这是 rv-2 的正向对照。"""
    path_lookup.add("claude")
    config = _config(lifecycle_agents={"implementation": "claude"})
    assert resolve_run_gate_block(config) is None
    assert_run_gate(config)


def test_gate_blocks_missing_agent_before_any_work(path_lookup: set[str]) -> None:
    """矩阵指向没装的 CLI：门禁点名阶段、agent、可执行文件与 setup 入口。"""
    config = _config_binding_implementation("kimi")
    assert resolve_run_gate_block(config) == ("implementation", None, "kimi", "kimi")

    with pytest.raises(PrimaryAgentNotInstalledError) as exc_info:
        assert_run_gate(config)
    message = str(exc_info.value)
    assert "stage 'implementation'" in message
    assert "kimi" in message
    assert "kc agent setup" in message


def test_gate_names_the_binding_preset_when_preset_shadows_matrix(path_lookup: set[str]) -> None:
    """预设遮蔽矩阵时点名预设：用户要改的是预设或装回 CLI。"""
    from backend.core.shared.models.agent_model_preset import AgentModelPreset

    config = _config(
        presets={"fast": AgentModelPreset(agent="kimi")},
        lifecycle_presets={"review": "fast"},
    )
    assert resolve_run_gate_block(config) == ("review", "fast", "kimi", "kimi")

    with pytest.raises(PrimaryAgentNotInstalledError) as exc_info:
        assert_run_gate(config)
    assert "preset 'fast'" in str(exc_info.value)


def test_gate_reports_unregistered_agent_without_a_bin(path_lookup: set[str]) -> None:
    """矩阵指向根本没注册的 agent：bin 为 None，消息给出注册清单口径。"""
    config = _config(lifecycle_agents={"verifier": "nobody"})
    assert resolve_run_gate_block(config) == ("verifier", None, "nobody", None)

    with pytest.raises(PrimaryAgentNotInstalledError) as exc_info:
        assert_run_gate(config)
    assert "not registered" in str(exc_info.value)
    assert "kc agent add" in str(exc_info.value)


def test_gate_falls_back_to_runner_default_agent(path_lookup: set[str]) -> None:
    """矩阵未声明时实现阶段回落 ``runner.default_agent``，缺失照样拦截。"""
    config = _config(default_agent="kimi")
    assert resolve_run_gate_block(config) == ("implementation", None, "kimi", "kimi")


def test_gate_is_not_rescued_by_fallback_candidates(path_lookup: set[str]) -> None:
    """回退链不参与门禁：主选没装是配置错误，换人必须显式 ``--agent``。"""
    config = _config_binding_implementation("kimi")
    assert "claude" in config.runner.agent_fallback_order
    path_lookup.add("claude")  # 回退候选可用，但主选缺失
    assert resolve_run_gate_block(config) is not None


def test_gate_applies_agent_override_only_to_implementation_stage(
    path_lookup: set[str],
) -> None:
    """``--agent`` 只换实现阶段的判定；其余阶段仍按配置解析。"""
    path_lookup.add("claude")
    config = _config(lifecycle_agents={"implementation": "claude", "verifier": "kimi"})

    # 显式换成没装的 kimi：实现阶段按换进来的人判定。
    assert resolve_run_gate_block(config, agent_override="kimi") == (
        "implementation",
        None,
        "kimi",
        "kimi",
    )
    # 显式换成可用的 claude：实现阶段放行，卡点落在配置里缺失的校验阶段。
    assert resolve_run_gate_block(config, agent_override="claude") == (
        "verifier",
        None,
        "kimi",
        "kimi",
    )


def test_gate_ignores_binding_to_a_deleted_preset(path_lookup: set[str]) -> None:
    """绑定指向已删除的预设时不制造第二套报错：解析链自己按回落层走。"""
    config = _config(lifecycle_presets={"implementation": "gone"})
    assert resolve_run_gate_block(config) is None


def test_gate_error_stays_an_agent_unavailable_failure(path_lookup: set[str]) -> None:
    """门禁异常继承既有的"不可用"分类，让观测与回退链继续认识它。"""
    config = _config_binding_implementation("kimi")
    with pytest.raises(AgentUnavailableError) as exc_info:
        assert_run_gate(config)
    assert isinstance(exc_info.value, PrimaryAgentNotInstalledError)


def test_gate_error_maps_to_not_found_exit_code(path_lookup: set[str]) -> None:
    """门禁失败是"查不到可执行文件"：语义退出码固定 ``NOT_FOUND(3)``。"""
    config = _config_binding_implementation("kimi")
    with pytest.raises(PrimaryAgentNotInstalledError) as exc_info:
        assert_run_gate(config)
    assert translate_exit_code(exc_info.value) == int(ExitCode.NOT_FOUND)


def test_gate_block_is_classified_as_agent_unavailable(path_lookup: set[str]) -> None:
    """观测口径不变：门禁与运行时缺失都归 ``agent_unavailable``。"""
    config = _config_binding_implementation("kimi")
    with pytest.raises(PrimaryAgentNotInstalledError) as exc_info:
        assert_run_gate(config)
    assert classify_invocation_failure(exc_info.value) == FAILURE_AGENT_UNAVAILABLE
    assert classify_invocation_failure(FileNotFoundError("claude")) == FAILURE_AGENT_UNAVAILABLE


# ---------------------------------------------------------------------------
# 真实入口：阻断发生在任何副作用之前
# ---------------------------------------------------------------------------


def test_repositories_entry_blocks_before_touching_github(path_lookup: set[str]) -> None:
    """``kc run`` 的仓库轮询入口在取 GitHub 客户端之前停下（dry-run 同样检查）。"""
    factory_calls: list[Path] = []

    def _factory(repo_path: Path) -> FakeGitHubClient:
        factory_calls.append(repo_path)
        return FakeGitHubClient()

    blocked = _entry_config(_config_binding_implementation("kimi"))
    contexts = [
        RepositoryRunContext(
            repo_id="repo-a",
            display_name="Repo A",
            repo_path=Path("."),
            config=blocked,
        )
    ]

    with pytest.raises(PrimaryAgentNotInstalledError):
        run_agent_repositories_once(
            contexts=contexts,
            dry_run=True,
            agent="auto",
            max_issues=1,
            process_runner=FakeProcessRunner(),
            github_client_factory=_factory,
        )

    assert factory_calls == []


@pytest.mark.parametrize("installed", [False, True])
def test_ready_issue_entry_blocks_before_claim(
    installed: bool, path_lookup: set[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """真实 claim 路径：主选没装时零副作用；装上后照常走到认领。

    Args:
        installed: 绑定的实现 agent 是否在本机可执行（受控 PATH）。
    """
    from backend.core.use_cases import agent_runner_issue_handlers as handlers

    binding = "claude" if installed else "kimi"
    if installed:
        path_lookup.add(binding)
    config = _entry_config(_config_binding_implementation(binding))

    issue = make_prd_issue()
    fake_client = FakeGitHubClient()
    worktree_calls: list[str] = []
    claim_calls: list[str] = []

    def _create_worktree(*_args: Any, **_kwargs: Any) -> Path:
        worktree_calls.append("worktree")
        return tmp_path / "issue-123"

    def _claim(*_args: Any, **_kwargs: Any) -> None:
        claim_calls.append("claim")
        raise _ClaimSentinel

    monkeypatch.setattr(handlers, "create_or_reuse_worktree", _create_worktree)
    monkeypatch.setattr(handlers, "arbitrate_first_claim", _claim)

    def _run_entry() -> None:
        handlers._process_ready_issue(
            issue=issue,
            repo_path=Path("."),
            config=config,
            agent="auto",
            github_client=fake_client,
            process_runner=FakeProcessRunner(),
        )

    if installed:
        with pytest.raises(_ClaimSentinel):
            _run_entry()
        # 正向对照：可用选择走到认领，没有被门禁拦在配置层。
        assert claim_calls == ["claim"]
    else:
        with pytest.raises(PrimaryAgentNotInstalledError) as exc_info:
            _run_entry()
        assert "stage 'implementation'" in str(exc_info.value)
        # 无副作用：没有认领标记 / 标签 / 评论，也没有 worktree。
        assert fake_client.calls == []
        assert claim_calls == []
        assert worktree_calls == []


def _entry_config(binder: AppConfig) -> AppConfig:
    """把替身配置里的注册表与阶段绑定搬到"runner 入口"形态的配置上。

    Args:
        binder: 只带可用性判定所需段落的替身配置。

    Returns:
        关闭评审相关门禁、其余保持入口默认值的生效配置。
    """
    return dataclasses.replace(
        config_with_review_disabled(),
        agents=binder.agents,
        agent_presets=binder.agent_presets,
        lifecycle_agents=binder.lifecycle_agents,
        lifecycle_presets=binder.lifecycle_presets,
    )


# ---------------------------------------------------------------------------
# 回退阶梯：换人是运行时失败，不是配置错误
# ---------------------------------------------------------------------------


def test_ladder_still_switches_on_runtime_unavailable() -> None:
    """候选**起跑后**报不可用：阶梯照常换人，并点名失败的那个 agent。"""
    calls: list[str] = []

    def _process_for_agent(**kwargs: Any) -> None:
        agent = str(kwargs["agent"])
        calls.append(agent)
        if agent == "claude":
            raise AgentUnavailableError("claude", context="launching bin 'claude'")

    config = AppConfig()
    used = run_issue_with_agent_fallback(
        issue=make_prd_issue(),
        config=config,
        agent="claude",
        process_for_agent=_process_for_agent,
    )

    assert calls == ["claude", "kimi"]
    assert used == "kimi"


def test_ladder_refuses_to_switch_on_gate_block() -> None:
    """门禁拦下的是配置错误：阶梯不得把它当成"这个 agent 跑挂了"偷偷换人。"""
    calls: list[str] = []

    def _process_for_agent(**kwargs: Any) -> None:
        calls.append(str(kwargs["agent"]))
        raise PrimaryAgentNotInstalledError("kimi", "stage 'implementation' resolves to kimi")

    with pytest.raises(PrimaryAgentNotInstalledError):
        run_issue_with_agent_fallback(
            issue=make_prd_issue(),
            config=AppConfig(),
            agent="kimi",
            process_for_agent=_process_for_agent,
        )

    assert calls == ["kimi"]


def test_spawn_boundary_names_phase_profile_and_bin(tmp_path: Path) -> None:
    """真实起跑失败时把阶段 / 用途 / 可执行文件补进异常，日志不再只剩 command not found。"""
    config = dataclasses.replace(
        AppConfig(),
        agents={**AppConfig().agents, "myagent": make_spec("myagent")},
    )

    class _MissingBinRunner(FakeProcessRunner):
        def run(self, command, **kwargs):  # noqa: ANN001, ANN201 - 替身签名与协议一致
            raise FileNotFoundError(command[0])

    with pytest.raises(AgentUnavailableError) as exc_info:
        run_agent_with_prompt(
            "myagent",
            "Do the work.",
            tmp_path,
            _MissingBinRunner(),
            config=config,
            profile=AGENT_PROFILE_RUN,
            invocation_phase=PHASE_IMPLEMENTATION,
        )

    context = exc_info.value.context
    assert context is not None
    assert "bin 'myagent'" in context
    assert "phase 'implementation'" in context
    assert "profile 'run'" in context
    assert exc_info.value.agent == "myagent"


def test_gate_reads_only_the_effective_registry(path_lookup: set[str]) -> None:
    """门禁与可用性判定同一份事实：仓库层覆盖进来的自定义 agent 同样被认识。"""
    base = _config()
    config = dataclasses.replace(
        base,
        agents={**base.agents, "myagent": make_spec("myagentbin")},
        lifecycle_agents=LifecycleAgentsConfig(global_layer={"implementation": "myagent"}),
    )

    assert resolve_run_gate_block(config) == ("implementation", None, "myagent", "myagentbin")
    path_lookup.add("myagentbin")
    assert resolve_run_gate_block(config) is None


def test_gate_module_uses_the_single_path_lookup(path_lookup: set[str]) -> None:
    """可用性只有一处判定：门禁不引入第二套 PATH 逻辑或内置名单。"""
    assert agent_onboarding.shutil.which("kimi") is None
    path_lookup.add("kimi")
    assert agent_onboarding.shutil.which("kimi") == "/fake/bin/kimi"

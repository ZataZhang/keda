"""首启提示（FR-4）的呈现与一次性测试。

提示只在**交互式终端**出现一次，绝不进入 agent 消费的机器输出流，也绝不因为
配置读不起来而阻断本轮命令。判定复用 core 的可用性事实源，本模块只测呈现门禁
与 marker 的一次性语义。

TTY 门禁不去伪造真实的 ``sys.stderr``：pytest 的捕获会在 fixture 之后重新装上
自己那份流对象，补丁打在它身上读到的仍是"不是终端"。这里把模块内的 ``sys``
换成受控替身（只回答是不是终端），提示文本由 ``emit_notice_once`` 的 recorder
直接接住——写 stderr 这件事本身由身份模块的测试负责。
"""

from __future__ import annotations

import dataclasses
import types
from pathlib import Path

import pytest

from backend.api import cli_agent_hint
from backend.api.cli_agent_hint import maybe_prompt_for_agent_setup
from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import AGENT_PROFILE_RUN, AgentProfileSpec, AgentSpec
from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
from backend.core.use_cases import agent_onboarding

_MARKER_NAME = ".agent-setup-hint-shown"


class _Terminal:
    """终端判定读到的最小接口。"""

    def __init__(self, *, tty: bool = True) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@dataclasses.dataclass
class HintHarness:
    """一轮提示测试的受控环境。

    Attributes:
        state_dir: 替代的状态目录（marker 落点）。
        installed_bins: 这台"机器"装了哪些 bin。
        notices: 提示文本的接收处，按实际输出顺序累积。
    """

    state_dir: Path
    installed_bins: set[str]
    notices: list[str]
    _terminals: dict[str, _Terminal]

    def as_non_terminal(self) -> None:
        """把 stdin 换成"不是终端"：管道、cron、runner 自己调用的那一侧。"""
        self._terminals["stdin"].tty = False

    @property
    def marker_shown(self) -> bool:
        return (self.state_dir / _MARKER_NAME).is_file()


@pytest.fixture()
def hint_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> HintHarness:
    """把状态目录、TTY、PATH 与提示输出全部换成受控替身。"""
    state_dir = tmp_path / ".kedacode"
    monkeypatch.setattr(product_identity, "state_home", lambda: state_dir)
    terminals = {"stdin": _Terminal(), "stderr": _Terminal()}
    monkeypatch.setattr(cli_agent_hint, "sys", types.SimpleNamespace(**terminals))
    monkeypatch.delenv(product_identity.COMPLETION_ENV_VAR_NAME, raising=False)

    notices: list[str] = []
    monkeypatch.setattr(product_identity, "emit_notice_once", notices.append)

    installed_bins: set[str] = set()
    monkeypatch.setattr(
        agent_onboarding.shutil,
        "which",
        lambda bin_name: f"/fake/bin/{bin_name}" if bin_name in installed_bins else None,
    )
    return HintHarness(state_dir, installed_bins, notices, terminals)


def _stub_effective_config(monkeypatch: pytest.MonkeyPatch, config: AppConfig) -> None:
    """把提示判定读到的生效配置换成替身（不读磁盘上的 ``config.toml``）。"""
    monkeypatch.setattr(
        "backend.core.use_cases.agent_runner_factory.build_app_config", lambda: config
    )


def _config_with_matrix(agent_name: str) -> AppConfig:
    """构造"实现阶段显式指向某个 agent"的配置，agent 的 bin 与注册名同名。"""
    spec = AgentSpec(
        bin=agent_name,
        label=f"agent/{agent_name}",
        label_color="5319E7",
        label_description="",
        profiles={AGENT_PROFILE_RUN: AgentProfileSpec()},
    )
    return dataclasses.replace(
        AppConfig(),
        agents={agent_name: spec},
        lifecycle_agents=LifecycleAgentsConfig(global_layer={"implementation": agent_name}),
    )


def test_hint_prompts_once_and_leaves_a_marker(
    hint_environment: HintHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有任何显式选择：提示一次并落 marker，同机不再重复。"""
    _stub_effective_config(monkeypatch, AppConfig())

    maybe_prompt_for_agent_setup(["run", "issue-1"])
    assert len(hint_environment.notices) == 1
    assert "kc agent setup" in hint_environment.notices[0]
    assert hint_environment.marker_shown

    maybe_prompt_for_agent_setup(["run", "issue-2"])
    assert len(hint_environment.notices) == 1


def test_hint_names_the_configured_but_missing_agent(
    hint_environment: HintHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置指向没装的 CLI：提示点名 agent 与 bin，并指向 list / setup。"""
    _stub_effective_config(monkeypatch, _config_with_matrix("kimi"))
    maybe_prompt_for_agent_setup(["run"])
    assert len(hint_environment.notices) == 1
    notice = hint_environment.notices[0]
    assert "kimi" in notice
    assert "bin 'kimi'" in notice
    assert "not installed on this machine" in notice
    assert "kc agent list" in notice


def test_hint_names_the_configured_but_unregistered_agent(
    hint_environment: HintHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """矩阵指向注册表里不存在的名字：照样提示，但不编造 bin。"""
    config = dataclasses.replace(
        AppConfig(),
        lifecycle_agents=LifecycleAgentsConfig(global_layer={"implementation": "ghost"}),
    )
    _stub_effective_config(monkeypatch, config)
    maybe_prompt_for_agent_setup(["run"])
    assert len(hint_environment.notices) == 1
    assert "ghost" in hint_environment.notices[0]
    assert "bin" not in hint_environment.notices[0]


def test_hint_is_silent_when_the_selection_is_installed(
    hint_environment: HintHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """选择可用时不提示，也不写 marker（marker 只属于"提示过"）。"""
    _stub_effective_config(monkeypatch, _config_with_matrix("claude"))
    hint_environment.installed_bins.add("claude")

    maybe_prompt_for_agent_setup(["run"])
    assert hint_environment.notices == []
    assert not hint_environment.marker_shown


@pytest.mark.parametrize(
    ("arguments", "terminal"),
    [
        (["run"], False),  # 没有可以回答的人：管道、cron、runner 自己
        (["run", "--json"], True),  # 机器输出
        (["run", "--output", "json"], True),
        (["run", "--output=json"], True),
        (["run", "--help"], True),
        (["run", "--version"], True),
        (["agent", "list"], True),  # agent 子命令本身就在处理这件事
        (["completion", "bash"], True),
    ],
)
def test_hint_stays_out_of_non_interactive_and_self_explanatory_calls(
    hint_environment: HintHarness,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    terminal: bool,
) -> None:
    """非 TTY / 机器输出 / --help / agent 子命令：一律不提示，也不留 marker。"""
    if not terminal:
        hint_environment.as_non_terminal()
    _stub_effective_config(monkeypatch, AppConfig())

    maybe_prompt_for_agent_setup(arguments)
    assert hint_environment.notices == []
    assert not hint_environment.marker_shown


def test_hint_stays_out_while_completing(
    hint_environment: HintHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """click 正在跑补全时不能往 stdout/stderr side 写任何东西。"""
    monkeypatch.setenv(product_identity.COMPLETION_ENV_VAR_NAME, "run:")
    _stub_effective_config(monkeypatch, AppConfig())
    maybe_prompt_for_agent_setup(["run"])
    assert hint_environment.notices == []


def test_hint_is_skipped_when_the_marker_already_exists(
    hint_environment: HintHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """marker 是"这台机器提示过"的唯一凭据：存在即不再提示。"""
    hint_environment.state_dir.mkdir(parents=True, exist_ok=True)
    (hint_environment.state_dir / _MARKER_NAME).write_text("shown\n", encoding="utf-8")
    _stub_effective_config(monkeypatch, AppConfig())

    maybe_prompt_for_agent_setup(["run"])
    assert hint_environment.notices == []


def test_hint_never_blocks_the_command_when_config_is_broken(
    hint_environment: HintHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置读不起来时，提示这条附加路径必须静默让位，不吞掉本轮命令。"""

    def _boom() -> AppConfig:
        raise RuntimeError("bad config.toml")

    monkeypatch.setattr("backend.core.use_cases.agent_runner_factory.build_app_config", _boom)
    maybe_prompt_for_agent_setup(["run"])
    assert hint_environment.notices == []
    assert not hint_environment.marker_shown

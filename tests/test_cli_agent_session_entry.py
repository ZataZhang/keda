"""裸 ``kc`` 原生执行器入口的契约（Issue #256 / FR-1、FR-2、FR-4，rv-1 自动化侧）。

分层刻意贴着风险走：

- **argv 决策**用 engines 的公开入口 :func:`prepare_native_session_plan` 直接断言
  （PRD 要求「生成 argv 不含无人值守 skip-permission 参数」是可机器核验的）。
- **进程语义**用真实子进程验证：cwd、退出码、stdio 继承、信号折算都不 mock。
- **路由**用真实 ``main()``：Typer/parser/schema 走生产装配，provider 换成临时脚本。

Human 侧（真实 Codex/Claude TUI 里的一轮对话）由 rv-1 的终端证据承担，本文件不冒充它。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_session import AgentSessionConfig, NativeSessionPlan
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_RUN,
    INTERACTIVE_PROFILE_ID,
    AgentProfileSpec,
    AgentSpec,
)
from backend.core.use_cases.agent_invocation import (
    UnknownAgentError,
    UnknownProfileError,
)
from backend.engines.agent_runner import interactive_agent_session
from backend.engines.agent_runner.interactive_agent_session import (
    build_operator_bootstrap,
    prepare_native_session_plan,
)
from backend.engines.agent_runner.remote_template_skills import RemoteTemplateSkillInstallError
from backend.infrastructure.foreground_session_launcher import (
    SubprocessForegroundSessionLauncher,
)
from backend.infrastructure.preview_process_manager import read_preview_record

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

#: 无人值守 runner 的自动批准/非交互旗标：交互入口里出现任何一条都算违规。
_UNATTENDED_FLAGS = (
    "--dangerously-skip-permissions",
    "--ask-for-approval",
    "--sandbox",
    "-p",
    "--print",
    "--output-format",
    "exec",
)


@pytest.fixture
def isolated_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``~/.kedacode`` 指向临时目录（skill 安装与预览注册表都不落本机）。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """目标仓库根（执行器的 cwd 必须是它）。"""
    repo_path = tmp_path / "provider-repo"
    repo_path.mkdir()
    return repo_path


def _session_config(**overrides: object) -> AppConfig:
    """只关心入口能力的最小生效配置（默认不校验 skill、不投递 bootstrap）。"""
    session_defaults: dict[str, object] = {
        "default_agent": "claude",
        "skill_install_check_enabled": False,
        "bootstrap_enabled": False,
    }
    session_defaults.update(overrides)
    return AppConfig(agent_session=AgentSessionConfig(**session_defaults))  # type: ignore[arg-type]


def _config_with_fake_agents(
    *,
    default_agent: str,
    agents: dict[str, AgentSpec],
    bootstrap_enabled: bool = False,
    skill_check_enabled: bool = False,
) -> AppConfig:
    """注册假 provider 的配置（真实 bin 路径 + 只声明 interactive 能力）。"""
    return AppConfig(
        agents=agents,
        agent_session=AgentSessionConfig(
            default_agent=default_agent,
            skill_install_check_enabled=skill_check_enabled,
            bootstrap_enabled=bootstrap_enabled,
        ),
    )


def _fake_agent_spec(
    script_path: Path,
    *,
    interactive_args: tuple[str, ...] = (),
    interactive: bool = True,
) -> AgentSpec:
    """一个假 provider：默认只声明交互能力，``interactive=False`` 用于 fail-fast 负控。"""
    return AgentSpec(
        bin=str(script_path),
        label="agent/fake-provider",
        label_color="000000",
        label_description="Terminal-native fake provider used by tests.",
        profiles={AGENT_PROFILE_RUN: AgentProfileSpec(args=("--unattended",))},
        interactive=AgentProfileSpec(args=interactive_args) if interactive else None,
    )


# ---------------------------------------------------------------------------
# argv 决策：能力来自声明，权限来自 provider 自己
# ---------------------------------------------------------------------------


def test_claude_interactive_plan_delivers_bootstrap_as_system_prompt(
    repo_root: Path,
) -> None:
    """claude 的交互 argv 走 ``--append-system-prompt``，cwd 是目标仓库。"""
    config = _session_config(bootstrap_enabled=True)

    preparation = prepare_native_session_plan(config=config, repo_root=repo_root)

    plan = preparation.plan
    assert plan.agent_name == "claude"
    assert plan.argv[0] == "claude"
    assert "--append-system-prompt" in plan.argv
    assert plan.cwd == repo_root
    assert plan.bootstrap_text
    assert INTERACTIVE_PROFILE_ID == "interactive"


def test_interactive_argv_never_carries_unattended_permission_flags(
    repo_root: Path,
) -> None:
    """原生入口不得复用无人值守形态（PRD §2 决定三 / 决定四的自动门禁）。"""
    config = _session_config(bootstrap_enabled=True)

    claude_plan = prepare_native_session_plan(config=config, repo_root=repo_root).plan
    codex_plan = prepare_native_session_plan(
        config=config, repo_root=repo_root, agent_override="codex"
    ).plan

    for plan in (claude_plan, codex_plan):
        assert not [flag for flag in _UNATTENDED_FLAGS if flag in plan.argv], plan.argv
    #: codex 的位置参数会被当成"用户提的任务"直接开跑，不是使用说明，
    #: 因此该 provider 诚实声明为无投递通道（bootstrap 不投）。
    assert codex_plan.bootstrap_text == ""
    assert codex_plan.argv == ("codex", "--cd", str(repo_root))


def test_agent_flag_overrides_default_executor(repo_root: Path) -> None:
    """``--agent`` 选择其他已声明交互能力的执行器，默认值不生效。"""
    config = _session_config(default_agent="claude")

    plan = prepare_native_session_plan(
        config=config, repo_root=repo_root, agent_override="codex"
    ).plan

    assert plan.agent_name == "codex"
    assert plan.argv[0] == "codex"


def test_auto_agent_placeholder_falls_back_to_configured_default(
    repo_root: Path,
) -> None:
    """``--agent auto`` 不是执行器名：回落默认值并把事实说清楚。"""
    config = _session_config(default_agent="claude")

    preparation = prepare_native_session_plan(
        config=config, repo_root=repo_root, agent_override="auto"
    )

    assert preparation.plan.agent_name == "claude"
    assert any("does not name an executor" in notice for notice in preparation.notices)


def test_agent_without_interactive_declaration_fails_before_any_launch(
    repo_root: Path,
) -> None:
    """只声明非交互 profile 的 agent 一律 fail-fast，绝不冒充交互会话。"""
    agents = {"no-tui": _fake_agent_spec(Path("/bin/echo"), interactive=False)}
    config = _config_with_fake_agents(default_agent="no-tui", agents=agents)

    with pytest.raises(UnknownProfileError) as refusal:
        prepare_native_session_plan(config=config, repo_root=repo_root)

    message = str(refusal.value)
    assert "does not declare an interactive profile" in message
    assert "[agent_runner.agents.no-tui.interactive]" in message


def test_unknown_agent_name_fails_before_any_launch(repo_root: Path) -> None:
    """未注册的执行器名报错，不静默换默认值。"""
    config = _session_config()

    with pytest.raises(UnknownAgentError):
        prepare_native_session_plan(config=config, repo_root=repo_root, agent_override="nope")


def test_bootstrap_text_states_the_preview_and_no_chat_boundary() -> None:
    """bootstrap 必须写清：KC 不跑聊天/项目服务，预览只按 ``kc preview start``。"""
    bootstrap_text = build_operator_bootstrap(
        repo_root=Path("/repo/demo"), skills_dir=Path("/home/u/.claude/skills")
    )

    assert "kc preview start" in bootstrap_text
    assert "no project dev server" in bootstrap_text
    assert "KedaCode injects none" in bootstrap_text
    assert "/repo/demo" in bootstrap_text
    assert "kedacode-operator" in bootstrap_text


# ---------------------------------------------------------------------------
# operator skill：复用 fail-closed 安装器，冲突不覆盖，拒绝就不启动
# ---------------------------------------------------------------------------


class _InstallPlan:
    """``install_packaged_operator_skill`` 返回值的测试替身。"""

    def __init__(self, action: str, target_path: Path) -> None:
        self.action = action
        self.target_path = target_path


def test_skill_check_installs_only_when_missing_and_never_force(
    repo_root: Path, isolated_state_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入口用同一个安装器、默认不 force，因此不会改写用户内容。"""
    calls: list[dict[str, object]] = []
    target = isolated_state_home / ".fake-provider" / "skills" / "kedacode-operator"

    def _fake_install(*, target_skills_root: Path, dry_run: bool, force: bool) -> _InstallPlan:
        calls.append({"dry_run": dry_run, "force": force, "root": target_skills_root})
        return _InstallPlan("install" if dry_run else "installed", target)

    monkeypatch.setattr(interactive_agent_session, "install_packaged_operator_skill", _fake_install)
    agent = _fake_agent_spec(Path("/bin/echo"))
    object.__setattr__(agent, "auth_home", "~/.fake-provider")
    config = _config_with_fake_agents(
        default_agent="fake", agents={"fake": agent}, skill_check_enabled=True
    )

    preparation = prepare_native_session_plan(
        config=config, repo_root=repo_root, agent_override="fake"
    )

    assert [call["dry_run"] for call in calls] == [True, False]
    assert all(call["force"] is False for call in calls)
    assert calls[0]["root"] == isolated_state_home / ".fake-provider" / "skills"
    assert any(
        "Installed the packaged" in notice for notice in preparation.notices
    ), preparation.notices


def test_user_edited_skill_is_preserved_and_blocks_session(
    repo_root: Path, isolated_state_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户改过的随包 skill 保留，冲突未解决前入口不启动 provider。"""
    target = isolated_state_home / ".fake-provider" / "skills" / "kedacode-operator"
    monkeypatch.setattr(
        interactive_agent_session,
        "install_packaged_operator_skill",
        lambda **kwargs: _InstallPlan("preserve-conflict", target),
    )
    agent = _fake_agent_spec(Path("/bin/echo"))
    object.__setattr__(agent, "auth_home", "~/.fake-provider")
    config = _config_with_fake_agents(
        default_agent="fake", agents={"fake": agent}, skill_check_enabled=True
    )

    with pytest.raises(RuntimeError, match="Refusing to start the native session") as refusal:
        prepare_native_session_plan(config=config, repo_root=repo_root, agent_override="fake")

    assert "Your edits were kept and nothing was overwritten" in str(refusal.value)


def test_skill_installer_refusal_blocks_the_session(
    repo_root: Path, isolated_state_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """安装器 fail-closed（目标目录不可信）时本次会话不启动，只指名原因。"""
    agent = _fake_agent_spec(Path("/bin/echo"))
    object.__setattr__(agent, "auth_home", "~/.fake-provider")
    config = _config_with_fake_agents(
        default_agent="fake", agents={"fake": agent}, skill_check_enabled=True
    )
    monkeypatch.setattr(
        interactive_agent_session,
        "install_packaged_operator_skill",
        lambda **kwargs: (_ for _ in ()).throw(
            RemoteTemplateSkillInstallError("skills root is a symlink pointing elsewhere")
        ),
    )

    with pytest.raises(RuntimeError, match="Refusing to start the native session"):
        prepare_native_session_plan(config=config, repo_root=repo_root, agent_override="fake")


# ---------------------------------------------------------------------------
# 真实前台进程语义：cwd / 退出码 / stdio 继承 / 信号折算
# ---------------------------------------------------------------------------


def test_foreground_launcher_forwards_cwd_and_exit_code_and_inherits_stdout(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """provider 继承真实 stdio（不捕获）、cwd 为仓库、退出码原样交回。"""
    marker = tmp_path / "provider-marker"
    script = tmp_path / "fake_provider.py"
    script.write_text(
        "import json, os, sys, pathlib\n"
        f"pathlib.Path({json.dumps(str(marker))}).write_text(json.dumps("
        '{"cwd": os.getcwd(), "argv": sys.argv[1:], "ppid": os.getppid()}))\n'
        "print('provider-owns-the-terminal')\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    launcher = SubprocessForegroundSessionLauncher()
    plan = NativeSessionPlan(
        agent_name="fake",
        argv=(sys.executable, str(script), "--cd", str(tmp_path)),
        cwd=tmp_path,
    )

    result = launcher.launch(plan)

    recorded = json.loads(marker.read_text(encoding="utf-8"))
    assert result.exit_code == 7
    assert Path(recorded["cwd"]) == tmp_path
    assert recorded["ppid"] == os.getpid()
    #: stdout 没被 KC 接管：provider 打印的内容直接落在 KC 自己的终端流上，
    #: 这正是原生 TUI 能画全屏界面的前提。
    assert "provider-owns-the-terminal" in capfd.readouterr().out


def test_foreground_launcher_translates_signal_death_to_shell_exit_code(
    tmp_path: Path,
) -> None:
    """provider 被信号杀死时按 shell 约定折算成 ``128 + signal``，不报成功。"""
    script = tmp_path / "self_kill.py"
    script.write_text("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n", encoding="utf-8")

    result = SubprocessForegroundSessionLauncher().launch(
        NativeSessionPlan(agent_name="fake", argv=(sys.executable, str(script)), cwd=tmp_path)
    )

    assert result.exit_code == 128 + int(signal.SIGKILL.value)


def test_foreground_launcher_reports_missing_binary_without_fallback(
    tmp_path: Path,
) -> None:
    """二进制缺失时报指名二进制的可行动错误，绝不回退到别的跑法。"""
    with pytest.raises(RuntimeError) as refusal:
        SubprocessForegroundSessionLauncher().launch(
            NativeSessionPlan(
                agent_name="fake",
                argv=("/nonexistent/keda-fake-cli", "--tui"),
                cwd=tmp_path,
            )
        )

    message = str(refusal.value)
    assert "/nonexistent/keda-fake-cli" in message
    assert "kc agent doctor" in message


def test_foreground_launcher_rejects_empty_argv(tmp_path: Path) -> None:
    """空 argv 无法启动，报错指名要修的配置段，不静默返回 0。"""
    with pytest.raises(RuntimeError, match="empty argv"):
        SubprocessForegroundSessionLauncher().launch(
            NativeSessionPlan(agent_name="fake", argv=(), cwd=tmp_path)
        )


# ---------------------------------------------------------------------------
# 真实 CLI 路由（生产 Typer 装配 + 临时 provider 脚本）
# ---------------------------------------------------------------------------


def _init_repo_with_agents(
    tmp_path: Path,
    *,
    default_agent: str,
    agents_toml: str,
    extra_session_toml: str = "",
) -> Path:
    """写一个带 ``[agent_session]`` 与假 provider 注册表的仓库配置。

    要靠 ``--agent`` 选中的那个名字必须取自内置注册表（``--agent`` 的合法取值来自
    注册表，仓库层只把它的 ``bin`` 换成临时脚本）；只作为 ``default_agent`` 出现的
    名字不受此限制。
    """
    from tests.test_agent_runner_cli import _init_iar_repo

    repo_path = _init_iar_repo(tmp_path)
    config_text = (
        (repo_path / ".iar.toml").read_text(encoding="utf-8")
        + "\n[agent_session]\n"
        + f'default_agent = "{default_agent}"\n'
        + "skill_install_check_enabled = false\n"
        + "bootstrap_enabled = false\n"
        + extra_session_toml
        + agents_toml
    )
    (repo_path / ".iar.toml").write_text(config_text, encoding="utf-8")
    return repo_path


def _provider_script(tmp_path: Path, name: str, *, exit_code: int = 0) -> Path:
    """写出一个可执行的假 provider（配置里的 ``bin`` 直接指向它）。"""
    script = tmp_path / name
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        f"pathlib.Path({json.dumps(str(tmp_path / (name + '.marker')))}).write_text("
        "json.dumps({\"cwd\": os.getcwd(), \"argv\": sys.argv[1:]}))\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_bare_kc_in_tty_launches_provider_and_forwards_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_state_home: Path
) -> None:
    """TTY 裸 ``kc`` = 配置的 provider 原生界面：cwd 是仓库，退出码原样交回。"""
    from backend.api.cli import main

    provider = _provider_script(tmp_path, "claude-native", exit_code=9)
    repo_path = _init_repo_with_agents(
        tmp_path,
        default_agent="claude",
        agents_toml=(
            "[agent_runner.agents.claude]\n"
            f'bin = "{provider}"\n'
            'label = "agent/claude"\n'
            "[agent_runner.agents.claude.interactive]\n"
            'args = ["--tui"]\n'
        ),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    exit_code = main(["--repo", str(repo_path)])

    assert exit_code == 9
    recorded = json.loads((tmp_path / "claude-native.marker").read_text(encoding="utf-8"))
    assert Path(recorded["cwd"]) == repo_path
    #: bootstrap_enabled = false 时不投递说明，也不该凭空多出一个空位置参数
    #: （provider 会把 "" 当成用户提的一条消息）。
    assert recorded["argv"] == ["--tui"]
    #: 裸启动不运行任何项目 dev server（FR-1 / rv-1）。
    assert read_preview_record(repo_path) is None


def test_agent_flag_selects_another_declared_executor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_state_home: Path
) -> None:
    """``kc --agent <name>`` 覆盖默认执行器，走另一个已声明交互能力的 provider。"""
    from backend.api.cli import main

    default_provider = _provider_script(tmp_path, "default-provider")
    override_provider = _provider_script(tmp_path, "override-provider", exit_code=3)
    repo_path = _init_repo_with_agents(
        tmp_path,
        default_agent="claude",
        agents_toml=(
            "[agent_runner.agents.claude]\n"
            f'bin = "{default_provider}"\n'
            'label = "agent/claude"\n'
            "[agent_runner.agents.claude.interactive]\n\n"
            "[agent_runner.agents.codex]\n"
            f'bin = "{override_provider}"\n'
            'label = "agent/codex"\n'
            "[agent_runner.agents.codex.interactive]\n"
        ),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    exit_code = main(["--agent", "codex", "--repo", str(repo_path)])

    assert exit_code == 3
    assert (tmp_path / "override-provider.marker").exists()
    assert not (tmp_path / "default-provider.marker").exists()


def test_bare_kc_without_tty_keeps_help_and_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_state_home: Path
) -> None:
    """非 TTY（CI / 管道）裸 ``kc`` 仍打印帮助并保持既有非零状态（FR-8）。"""
    from backend.api.cli import main

    repo_path = _init_repo_with_agents(
        tmp_path,
        default_agent="fake",
        agents_toml=(
            '[agent_runner.agents.fake]\nbin = "/bin/echo"\nlabel = "agent/fake"\n'
            "[agent_runner.agents.fake.interactive]\n"
        ),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    exit_code = main(["--repo", str(repo_path)])

    assert exit_code == 1
    assert not (tmp_path / "fake.marker").exists()


def test_bare_kc_without_options_and_tty_dispatches_to_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实空 argv 的 TTY 分支进入原生会话并原样返回 provider 退出码。"""
    from backend.api.cli import main

    observed_calls: list[tuple[str, dict[str, object]]] = []

    def _record_native_session(command_name: str, **command_options: object) -> int:
        observed_calls.append((command_name, command_options))
        return 17

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "backend.api.cli_typer_app._run_typer_command",
        _record_native_session,
    )

    exit_code = main([])

    assert exit_code == 17
    assert observed_calls == [
        (
            "session",
            {"repo": None, "repo_id": None, "config": None, "agent": None},
        )
    ]


def test_bare_kc_console_entrypoint_returns_nonzero_for_no_tty() -> None:
    """已安装的裸 ``kc`` 在管道输入下显示帮助并保留非零退出状态。"""
    console_script = Path(sys.executable).with_name("kc")
    completed_process = subprocess.run(
        [str(console_script)],
        input="issue256 real-entry probe\n",
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed_process.returncode == 1
    assert "Usage: kc" in completed_process.stdout


def test_session_subcommand_requires_a_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_state_home: Path
) -> None:
    """``kc session`` 在无 TTY 下以用法错误退出，不启动 provider。"""
    from backend.api.cli import main

    provider = _provider_script(tmp_path, "tty-required")
    repo_path = _init_repo_with_agents(
        tmp_path,
        default_agent="fake",
        agents_toml=(
            f'[agent_runner.agents.fake]\nbin = "{provider}"\nlabel = "agent/fake"\n'
            "[agent_runner.agents.fake.interactive]\n"
        ),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    exit_code = main(["session", "--repo", str(repo_path)])

    assert exit_code == 2
    assert not (tmp_path / "tty-required.marker").exists()


def test_session_with_unsupported_executor_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_state_home: Path
) -> None:
    """未声明交互能力的执行器在真实 CLI 里给出可行动错误，而不是 traceback。"""
    from backend.api.cli import main

    repo_path = _init_repo_with_agents(
        tmp_path,
        default_agent="codex",
        agents_toml=(
            "[agent_runner.agents.kimi]\n"
            'bin = "/usr/bin/true"\n'
            'label = "agent/kimi"\n'
            '[agent_runner.agents.kimi.profiles.run]\nargs = ["--headless"]\n'
        ),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    exit_code = main(["session", "--agent", "kimi", "--repo", str(repo_path)])

    assert exit_code == 2
    #: 失败发生在启动任何进程之前：既没有预览记录，也没有 provider marker。
    assert read_preview_record(repo_path) is None
    assert not (tmp_path / "kimi.marker").exists()


def test_session_with_missing_provider_binary_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_state_home: Path, capsys
) -> None:
    """provider 没装时退出码非零且报错可行动（PRD 行为样例「未安装…明确失败」）。"""
    from backend.api.cli import main

    repo_path = _init_repo_with_agents(
        tmp_path,
        default_agent="absent",
        agents_toml=(
            '[agent_runner.agents.absent]\nbin = "/nonexistent/keda-absent-cli"\n'
            'label = "agent/absent"\n'
            "[agent_runner.agents.absent.interactive]\n"
        ),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    exit_code = main(["session", "--repo", str(repo_path)])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "Traceback" not in captured.err + captured.out
    assert "was not found on PATH" in captured.err + captured.out

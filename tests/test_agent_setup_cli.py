"""``kc agent setup`` / ``kc agent add`` 的 CLI 正负路径与退出码测试。

引导的两个入口都必须满足同一条契约：**校验失败不触达配置文件**，退出码稳定
（用法错误 ``USAGE(2)``，选了本机没装的 agent ``NOT_FOUND(3)``），``--dry-run``
只读。测试把配置加载与写回编辑器换成受控替身，因此不会碰到真实
``~/.kedacode/config.toml``；PATH 判定同样换成受控集合。
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    OUTPUT_FORMAT_TABLE,
    CliError,
    render_cli_error,
)
from backend.api.cli_parsed_commands import agent as agent_cli
from backend.api.cli_parsed_commands import agent_onboarding_commands as onboarding_cli
from backend.api.cli_parsed_commands.agent import (
    run_agent_add_command,
    run_agent_list_command,
    run_agent_presets_command,
    run_agent_setup_command,
)
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.core.shared.models.agent_model_preset import AgentModelPreset
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import AGENT_PROFILE_RUN, AgentProfileSpec, AgentSpec
from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
from backend.core.use_cases import agent_onboarding
from backend.core.use_cases.agent_onboarding import (
    agent_availability_entries,
    build_fix_plan,
    build_setup_plan,
)

# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def path_lookup(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """受控 PATH：测试自己声明这台机器装了哪些 bin。"""
    installed: set[str] = set()
    monkeypatch.setattr(
        agent_onboarding.shutil,
        "which",
        lambda bin_name: f"/fake/bin/{bin_name}" if bin_name in installed else None,
    )
    return installed


class RecordingEditor:
    """记录写回序列的假 lifecycle 编辑器（配置文件的替身）。"""

    def __init__(self, config_path: Path | None = None) -> None:
        self.writes: list[tuple[Any, ...]] = []
        self.transactions = 0
        self._config_path = config_path or Path("/fake/config.toml")

    @property
    def config_path(self) -> Path:
        return self._config_path

    @contextlib.contextmanager
    def write_transaction(self):
        self.transactions += 1
        yield

    def update_lifecycle_agents(self, values: Any) -> None:
        self.writes.append(("lifecycle_agents", dict(values)))

    def update_lifecycle_presets(self, values: Any) -> None:
        self.writes.append(("lifecycle_presets", dict(values)))

    def update_agent_registration(
        self, agent_name: str, fields: Any, profile_run_fields: Any
    ) -> None:
        self.writes.append(("registration", agent_name, dict(fields), dict(profile_run_fields)))


def _spec(bin_name: str) -> AgentSpec:
    """最小注册声明：bin 与注册名同名，便于用受控 PATH 精确控制可用态。"""
    return AgentSpec(
        bin=bin_name,
        label=f"agent/{bin_name}",
        label_color="5319E7",
        label_description="",
        profiles={AGENT_PROFILE_RUN: AgentProfileSpec()},
    )


def _config(
    *,
    presets: dict[str, AgentModelPreset] | None = None,
    lifecycle_agents: dict[str, str] | None = None,
    lifecycle_presets: dict[str, str] | None = None,
) -> AppConfig:
    """构造全局层替身：claude / kimi 两个 agent，其余段落保持内置默认。"""
    config = AppConfig()
    overrides: dict[str, Any] = {
        "agents": {"claude": _spec("claude"), "kimi": _spec("kimi")},
    }
    if presets is not None:
        overrides["agent_presets"] = presets
    if lifecycle_agents is not None:
        overrides["lifecycle_agents"] = LifecycleAgentsConfig(global_layer=dict(lifecycle_agents))
    if lifecycle_presets is not None:
        overrides["lifecycle_presets"] = LifecycleAgentsConfig(global_layer=dict(lifecycle_presets))
    return dataclasses.replace(config, **overrides)


@pytest.fixture()
def installed_config(monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """把 setup 读到的全局配置换成替身（不读磁盘上的 ``config.toml``）。"""
    config = _config()
    monkeypatch.setattr(onboarding_cli, "load_fresh_agent_runner_settings", lambda: object())
    monkeypatch.setattr(onboarding_cli, "build_app_config_from_settings", lambda _: config)
    return config


@pytest.fixture()
def recording_editor(monkeypatch: pytest.MonkeyPatch) -> RecordingEditor:
    """setup / add 的写回落点换成替身编辑器。"""
    editor = RecordingEditor()
    monkeypatch.setattr(onboarding_cli, "create_lifecycle_settings_editor", lambda *_: editor)
    return editor


def _make_context(**parsed_kwargs: Any) -> ParsedCommandContext:
    """构造 setup / add 命令的最小上下文（其余依赖不触达）。

    ``as_json`` 是 CLI 旗标名，同时决定中央 dispatcher 注入的 ``output_format``。
    """
    defaults: dict[str, Any] = {
        "dry_run": False,
        "fix": False,
        "stage": None,
        "agent": None,
        "preset": None,
        "yes": True,
        # add 的旗标（setup 用例不读）
        "name": "myagent",
        "bin": "myagent",
        "arg": (),
        "prompt_delivery": "argv_tail",
        "prompt_flag": None,
        "output_protocol": "plain",
        "auth_home": None,
        "label": None,
        "write": False,
    }
    defaults.update(parsed_kwargs)
    as_json = bool(defaults.pop("as_json", False))
    return ParsedCommandContext(
        parsed=argparse.Namespace(**defaults),
        process_runner=None,
        runner_settings=None,
        repo_id=None,
        repo_override=None,
        github_client_factory=None,
        output_format=OUTPUT_FORMAT_JSON if as_json else OUTPUT_FORMAT_TABLE,
    )


def _run(
    context: ParsedCommandContext,
    handler,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, Any, str, str]:
    """执行命令并返回 ``(退出码, stdout JSON 或 None, stdout 原文, stderr 原文)``。

    失败按真实进程的方式走完：handler 只抛带类别的 :class:`CliError`，落码与
    stderr 归属交给 ``render_cli_error``，因此断言的是进程真正会给出的码。
    """
    capsys.readouterr()
    try:
        code = handler(context)
    except CliError as error:
        code = render_cli_error(error, fmt=context.output_format)
    captured = capsys.readouterr()
    stdout_text = captured.out
    document = json.loads(stdout_text) if stdout_text.strip().startswith(("{", "[")) else None
    return int(code), document, stdout_text, captured.err


# ---------------------------------------------------------------------------
# kc agent setup
# ---------------------------------------------------------------------------


def test_setup_writes_available_agent_choice(
    installed_config: AppConfig,
    recording_editor: RecordingEditor,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """装了的名字：一次全局层矩阵写入，JSON 文档点名落点。"""
    path_lookup.add("claude")
    code, document, _, _ = _run(
        _make_context(agent="claude", as_json=True), run_agent_setup_command, capsys
    )
    assert code == 0
    assert recording_editor.writes == [("lifecycle_agents", {"implementation": "claude"})]
    assert document["stage"] == "implementation"
    assert document["agent"] == "claude"
    assert document["cancelled"] is False and document["dry_run"] is False


def test_setup_missing_agent_exits_not_found_without_writing(
    installed_config: AppConfig,
    recording_editor: RecordingEditor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """没装的 agent 必被拒绝（NOT_FOUND=3），且配置文件一次锁都没取。"""
    code, _, _, stderr_text = _run(
        _make_context(agent="kimi", as_json=True), run_agent_setup_command, capsys
    )
    assert code == int(ExitCode.NOT_FOUND)
    assert recording_editor.transactions == 0 and recording_editor.writes == []
    assert "not available on PATH" in stderr_text


def test_setup_unregistered_agent_exits_usage(
    installed_config: AppConfig,
    recording_editor: RecordingEditor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """没注册的 agent 是用法错误（USAGE=2），并指向 ``kc agent add``。"""
    code, _, _, stderr_text = _run(
        _make_context(agent="nobody", as_json=True), run_agent_setup_command, capsys
    )
    assert code == int(ExitCode.USAGE)
    assert recording_editor.transactions == 0
    assert "kc agent add" in stderr_text


def test_setup_unknown_stage_exits_usage(
    installed_config: AppConfig,
    recording_editor: RecordingEditor,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """阶段名不在九键闭集里：用法错误，不写文件。"""
    path_lookup.add("claude")
    code, _, _, _ = _run(
        _make_context(stage="vibes", agent="claude", as_json=True),
        run_agent_setup_command,
        capsys,
    )
    assert code == int(ExitCode.USAGE)
    assert recording_editor.transactions == 0


def test_setup_dry_run_reads_without_writing(
    installed_config: AppConfig,
    recording_editor: RecordingEditor,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--dry-run`` 只预览：即使目标未满足也不取写锁。"""
    path_lookup.add("claude")
    code, document, _, _ = _run(
        _make_context(agent="claude", dry_run=True, as_json=True),
        run_agent_setup_command,
        capsys,
    )
    assert code == 0
    assert document["dry_run"] is True and document["already_satisfied"] is False
    assert recording_editor.transactions == 0 and recording_editor.writes == []


def test_setup_skips_write_when_the_global_layer_already_matches(
    recording_editor: RecordingEditor,
    path_lookup: set[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """重复执行同一选择：already_satisfied 为真，磁盘一次都不动。"""
    path_lookup.add("claude")
    config = _config(lifecycle_agents={"implementation": "claude"})
    assert build_setup_plan(config, stage="implementation", agent="claude").already_satisfied
    monkeypatch.setattr(onboarding_cli, "build_app_config_from_settings", lambda _: config)

    code, document, _, _ = _run(
        _make_context(agent="claude", as_json=True), run_agent_setup_command, capsys
    )
    assert code == 0
    assert document["already_satisfied"] is True
    assert recording_editor.transactions == 0 and recording_editor.writes == []


def test_setup_unbinds_the_masking_stage_preset(
    recording_editor: RecordingEditor,
    path_lookup: set[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """预设遮蔽矩阵时，写矩阵必须先解绑预设，否则选择永不生效。"""
    path_lookup.add("claude")
    config = _config(
        presets={"fast": AgentModelPreset(agent="claude")},
        lifecycle_presets={"implementation": "fast"},
    )
    monkeypatch.setattr(onboarding_cli, "build_app_config_from_settings", lambda _: config)
    code, document, _, _ = _run(
        _make_context(agent="claude", as_json=True), run_agent_setup_command, capsys
    )
    assert code == 0
    assert document["unbind_stage_preset"] is True
    assert recording_editor.writes == [
        ("lifecycle_presets", {"implementation": None}),
        ("lifecycle_agents", {"implementation": "claude"}),
    ]


def test_setup_without_selection_or_terminal_exits_usage(
    installed_config: AppConfig,
    recording_editor: RecordingEditor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """非 TTY 且没有任何选择：拒绝，而不是猜一个 agent。"""
    # pytest 的替身 stdin 不是 TTY，正是"没有可以回答的人"的那一侧。
    assert not sys.stdin.isatty()
    code, _, _, stderr_text = _run(_make_context(as_json=False), run_agent_setup_command, capsys)
    assert code == int(ExitCode.USAGE)
    assert "interactive terminal" in stderr_text
    assert recording_editor.transactions == 0


def test_setup_readonly_listing_shares_availability_facts(
    installed_config: AppConfig,
    recording_editor: RecordingEditor,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--dry-run`` 的可用性清单与 Console 视图共用 core 的同一份序列化。"""
    path_lookup.add("claude")
    code, document, _, _ = _run(
        _make_context(dry_run=True, as_json=True), run_agent_setup_command, capsys
    )
    assert code == 0
    assert document["availability"] == agent_availability_entries(installed_config.agents)
    assert document["available_agents"] == ["claude"]
    assert recording_editor.transactions == 0


def test_setup_fix_removes_only_global_unavailable_bindings(
    path_lookup: set[str],
    recording_editor: RecordingEditor,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--fix`` 批删全局层指向不可用 agent 的绑定；可用的不动。"""
    path_lookup.add("claude")
    config = _config(
        presets={"fast": AgentModelPreset(agent="kimi")},
        lifecycle_agents={"implementation": "claude", "verifier": "kimi"},
        lifecycle_presets={"review": "fast"},
    )
    monkeypatch.setattr(onboarding_cli, "build_app_config_from_settings", lambda _: config)

    code, document, _, _ = _run(
        _make_context(fix=True, as_json=True), run_agent_setup_command, capsys
    )
    assert code == 0
    assert {(entry["stage"], entry["layer"]) for entry in document["removed"]} == {
        ("verifier", "lifecycle_agents"),
        ("review", "lifecycle_presets"),
    }
    assert recording_editor.transactions == 1
    assert recording_editor.writes == [
        ("lifecycle_presets", {"review": None}),
        ("lifecycle_agents", {"verifier": None}),
    ]

    dry_code, dry_document, _, _ = _run(
        _make_context(fix=True, dry_run=True, as_json=True), run_agent_setup_command, capsys
    )
    assert dry_code == 0
    assert dry_document["dry_run"] is True
    assert len(dry_document["removed"]) == len(document["removed"])
    assert recording_editor.transactions == 1  # 只读形态没有再写一次


def test_setup_fix_with_clean_config_reports_nothing(
    installed_config: AppConfig,
    recording_editor: RecordingEditor,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """没有坏绑定时报告"没有"，退出码 0，且不取写锁。"""
    path_lookup.add("claude")
    code, document, _, _ = _run(
        _make_context(fix=True, as_json=True), run_agent_setup_command, capsys
    )
    assert code == 0
    assert document["removed"] == []
    assert build_fix_plan(installed_config) == ()
    assert recording_editor.transactions == 0


# ---------------------------------------------------------------------------
# kc agent add
# ---------------------------------------------------------------------------


def test_add_previews_registration_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    recording_editor: RecordingEditor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """默认只预览 TOML：注册校验通过但磁盘不动。"""
    monkeypatch.setattr(onboarding_cli, "build_app_config", lambda: _config())
    code, document, _, _ = _run(
        _make_context(name="myagent", bin="myagent", as_json=True),
        run_agent_add_command,
        capsys,
    )
    assert code == 0
    assert document["written"] is False
    assert "[agent_runner.agents.myagent.profiles.run]" in document["toml"]
    assert recording_editor.transactions == 0


def test_add_write_persists_the_validated_block(
    monkeypatch: pytest.MonkeyPatch,
    recording_editor: RecordingEditor,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--write`` 才落盘；bin 还没装时给出提醒而不是假装可用。"""
    monkeypatch.setattr(onboarding_cli, "build_app_config", lambda: _config())
    code, document, _, stderr_text = _run(
        _make_context(name="myagent", bin="myagent", arg=("--print",), write=True, as_json=True),
        run_agent_add_command,
        capsys,
    )
    assert code == 0
    assert document["written"] is True
    assert recording_editor.writes[0][0] == "registration"
    assert recording_editor.writes[0][1] == "myagent"
    assert recording_editor.writes[0][3]["args"] == ["--print"]
    assert "not on PATH" in stderr_text

    path_lookup.add("other")
    ok_code, _, _, ok_stderr = _run(
        _make_context(name="other", bin="other", write=True, as_json=True),
        run_agent_add_command,
        capsys,
    )
    assert ok_code == 0
    assert "not on PATH" not in ok_stderr


def test_add_invalid_registration_exits_usage_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    recording_editor: RecordingEditor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """非法协议 / 重名 / 缺 prompt_flag 一律 USAGE(2)，配置文件逐字节不动。"""
    monkeypatch.setattr(onboarding_cli, "build_app_config", lambda: _config())
    cases: list[dict[str, Any]] = [
        {"name": "claude", "bin": "claude"},
        {"name": "myagent", "bin": "myagent", "output_protocol": "nope"},
        {"name": "myagent", "bin": "myagent", "prompt_delivery": "flag"},
        {"name": "9agent", "bin": "myagent"},
    ]
    for kwargs in cases:
        code, _, _, _ = _run(
            _make_context(write=True, as_json=True, **kwargs), run_agent_add_command, capsys
        )
        assert code == int(ExitCode.USAGE), kwargs
    assert recording_editor.transactions == 0


# ---------------------------------------------------------------------------
# kc agent list / presets 的可用性字段
# ---------------------------------------------------------------------------


def test_agent_list_reports_availability_per_agent(
    monkeypatch: pytest.MonkeyPatch,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """list 的 ``available`` / ``bin_path`` 直接来自 PATH 判定。"""
    config = _config()
    monkeypatch.setattr(agent_cli, "build_app_config", lambda: config)
    code, document, _, _ = _run(_make_context(as_json=True), run_agent_list_command, capsys)
    assert code == 0
    by_agent = {entry["agent"]: entry for entry in document}
    assert by_agent["claude"]["available"] is False
    assert by_agent["claude"]["bin_path"] is None

    path_lookup.add("claude")
    code, document, _, _ = _run(_make_context(as_json=True), run_agent_list_command, capsys)
    assert code == 0
    by_agent = {entry["agent"]: entry for entry in document}
    assert by_agent["claude"]["bin_path"] == "/fake/bin/claude"
    assert by_agent["kimi"]["available"] is False


def test_agent_list_human_output_marks_missing_agent(
    monkeypatch: pytest.MonkeyPatch,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人类输出把未安装标出来，让"配了但没装"肉眼可分。"""
    monkeypatch.setattr(agent_cli, "build_app_config", lambda: _config())
    code, _, stdout_text, _ = _run(_make_context(), run_agent_list_command, capsys)
    assert code == 0
    assert "missing" in stdout_text and "kimi" in stdout_text

    path_lookup.add("claude")
    code, _, stdout_text, _ = _run(_make_context(), run_agent_list_command, capsys)
    assert code == 0
    assert "available" in stdout_text and "/fake/bin/claude" in stdout_text


def test_agent_presets_reports_bound_agent_availability(
    monkeypatch: pytest.MonkeyPatch,
    path_lookup: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """预设清单点名它绑定的 agent 装没装。"""
    config = _config(presets={"fast": AgentModelPreset(agent="kimi", model="m1")})
    monkeypatch.setattr(agent_cli, "build_app_config", lambda: config)
    code, document, _, _ = _run(_make_context(as_json=True), run_agent_presets_command, capsys)
    assert code == 0
    assert document == [
        {
            "preset": "fast",
            "agent": "kimi",
            "model": "m1",
            "reasoning_effort": None,
            "agent_available": False,
        }
    ]

    path_lookup.add("kimi")
    code, document, _, _ = _run(_make_context(as_json=True), run_agent_presets_command, capsys)
    assert code == 0
    assert document[0]["agent_available"] is True

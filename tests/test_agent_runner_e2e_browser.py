"""浏览器 E2E 验证命令链路的针对性测试（P1-FEAT-20260930-225500）。

覆盖 PRD 的 FR-1..FR-7 逻辑层：配置契约解析、工厂转换、执行层分类失败、
child_env 白名单、进程执行档安全前提、run_verification 分发，以及
"纯 shell 条目行为逐字段不变"（FR-7）的回归特征化。真实入口保真度由
RV 证据（rv-1..rv-5）另行覆盖，本文件不替代之。
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.core.shared.interfaces.agent_runner import (
    E2E_CHILD_ENV_PROFILE,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    BrowserE2EVerificationCommand,
    CommandResult,
    E2EVerificationArtifact,
    RunnerConfig,
    describe_verification_command,
)
from backend.core.use_cases.agent_runner_e2e_browser import (
    E2EFailureCategory,
    extract_e2e_failure_category,
    format_e2e_failure_classification,
    run_browser_e2e_verification_command,
)
from backend.core.use_cases.agent_runner_feedback import (
    format_result_for_recovery,
    format_verification_failure,
)
from backend.core.use_cases.agent_runner_git import run_verification
from backend.engines.agent_runner.factory_config_builder import (
    _build_verification_commands,
)
from backend.infrastructure.child_env import build_e2e_child_env
from backend.infrastructure.config.agent_runner_settings import (
    AgentRunnerE2eVerificationCommandSettings,
    AgentRunnerLocalSettings,
)
from backend.infrastructure.process_runner import SubprocessRunner

_E2E_ENTRY = BrowserE2EVerificationCommand(
    script="tests/e2e/smoke.sh",
    app_start="just run frontend",
    ready_url="http://localhost:5173/sign-in",
)


class _RecordingProcessRunner:
    """记录调用参数的假 IProcessRunner；按队列返回预设结果。"""

    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._results = list(results or [])

    def run(self, command, **kwargs):  # noqa: ANN001, ANN201
        self.calls.append({"command": tuple(command), **kwargs})
        if self._results:
            return self._results.pop(0)
        return CommandResult(command=tuple(command), return_code=0, stdout="", stderr="")

    def last(self) -> dict[str, object]:
        return self.calls[-1]


def _success_result(command: tuple[str, ...] = ("bash",)) -> CommandResult:
    return CommandResult(command=command, return_code=0, stdout="", stderr="")


def _failure_result(command: tuple[str, ...], stderr: str) -> CommandResult:
    return CommandResult(command=command, return_code=1, stdout="", stderr=stderr)


def _make_executable_script(worktree_path: Path, relpath: str = "tests/e2e/smoke.sh") -> Path:
    script_path = worktree_path / relpath
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    script_path.chmod(0o755)
    return script_path


def _e2e_failure_result(category: E2EFailureCategory) -> CommandResult:
    return CommandResult(
        command=("browser_e2e", "tests/e2e/smoke.sh"),
        return_code=1,
        stdout="",
        stderr=f"BROWSER_E2E_FAILURE [category={category.token}] boom",
    )


# ---------------------------------------------------------------------------
# FR-1 配置契约：解析与诊断
# ---------------------------------------------------------------------------


def test_settings_accept_mixed_string_and_e2e_entries() -> None:
    """同一数组里混排纯 shell 字符串与 E2E 结构化条目均可解析。"""
    settings = AgentRunnerLocalSettings(
        runner={
            "verification_commands": [
                "git diff --check",
                {
                    "kind": "browser_e2e",
                    "script": "tests/e2e/smoke.sh",
                    "app_start": "just run frontend",
                    "ready_url": "http://localhost:5173",
                    "env_allow": ["MY_PORT"],
                    "artifacts": [{"path": ".iar/evidence/shot.png", "mime": "image/png"}],
                },
            ]
        }
    )
    entries = settings.runner.verification_commands
    assert entries[0] == "git diff --check"
    e2e_entry = entries[1]
    assert isinstance(e2e_entry, AgentRunnerE2eVerificationCommandSettings)
    assert e2e_entry.script == "tests/e2e/smoke.sh"
    assert e2e_entry.artifacts[0].mime == "image/png"
    assert e2e_entry.timeout_seconds == 900


def test_settings_reject_unknown_field_with_field_level_diagnostic() -> None:
    """``extra="forbid"``：拼错/多余的字段在加载期就点名报错。"""
    with pytest.raises(ValidationError) as exc_info:
        AgentRunnerE2eVerificationCommandSettings.model_validate(
            {"kind": "browser_e2e", "script": "a.sh", "ap_start": "typo"}
        )
    assert "ap_start" in str(exc_info.value)


def test_settings_reject_ready_url_without_app_start() -> None:
    """``ready_url`` / ``startup_wait_seconds`` 没有 ``app_start`` 时定向报错。"""
    with pytest.raises(ValidationError) as exc_info:
        AgentRunnerE2eVerificationCommandSettings.model_validate(
            {"kind": "browser_e2e", "script": "a.sh", "ready_url": "http://localhost:3000"}
        )
    assert "ready_url" in str(exc_info.value) and "app_start" in str(exc_info.value)
    with pytest.raises(ValidationError) as exc_info:
        AgentRunnerE2eVerificationCommandSettings.model_validate(
            {"kind": "browser_e2e", "script": "a.sh", "startup_wait_seconds": 5}
        )
    assert "startup_wait_seconds" in str(exc_info.value)


def test_factory_converts_e2e_entries_to_frozen_core_view() -> None:
    """工厂把 settings 条目转成 frozen core dataclass，列表转元组。"""
    settings_entry = AgentRunnerE2eVerificationCommandSettings.model_validate(
        {
            "kind": "browser_e2e",
            "script": "tests/e2e/smoke.sh",
            "app_start": "just run frontend",
            "env_allow": ["MY_PORT"],
            "artifacts": [{"path": "out/trace.zip", "mime": "application/zip"}],
        }
    )
    built = _build_verification_commands(["git diff --check", settings_entry])
    assert built[0] == "git diff --check"
    core_entry = built[1]
    assert isinstance(core_entry, BrowserE2EVerificationCommand)
    assert core_entry.env_allow == ("MY_PORT",)
    assert isinstance(core_entry.artifacts[0], E2EVerificationArtifact)
    assert core_entry.artifacts[0].path == "out/trace.zip"


# ---------------------------------------------------------------------------
# FR-2 可用性预检与分类失败
# ---------------------------------------------------------------------------


def test_script_missing_fails_preflight_without_spawning_process(tmp_path: Path) -> None:
    """脚本入口不存在：预检即得 script_missing 分类，不启动任何子进程。"""
    runner = _RecordingProcessRunner()
    result = run_browser_e2e_verification_command(_E2E_ENTRY, tmp_path, runner)
    assert result.return_code != 0
    assert extract_e2e_failure_category(result) is E2EFailureCategory.SCRIPT_MISSING
    assert runner.calls == []


def test_script_without_exec_bit_is_caught_by_preflight(tmp_path: Path) -> None:
    """缺执行位同样预检失败，避免 bash 把脚本内容当 shell 回退解释。"""
    script_path = tmp_path / "tests" / "e2e" / "smoke.sh"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    script_path.chmod(0o644)
    result = run_browser_e2e_verification_command(_E2E_ENTRY, tmp_path, _RecordingProcessRunner())
    assert extract_e2e_failure_category(result) is E2EFailureCategory.SCRIPT_MISSING
    assert "not executable" in result.stderr


def test_browser_runtime_probe_failure_is_classified_with_guidance(tmp_path: Path) -> None:
    """浏览器运行时探测失败：browser_runtime_missing + 安装指引，不进入执行段。"""
    _make_executable_script(tmp_path)
    runner = _RecordingProcessRunner(
        results=[_failure_result(("bash", "-lc", "probe"), "no browsers installed")]
    )
    result = run_browser_e2e_verification_command(_E2E_ENTRY, tmp_path, runner)
    assert extract_e2e_failure_category(result) is E2EFailureCategory.BROWSER_RUNTIME_MISSING
    assert "playwright install chromium" in result.stderr
    assert len(runner.calls) == 1  # 只有探测，没有真正执行


# ---------------------------------------------------------------------------
# FR-3 / FR-4 执行段：白名单档、超时、分类透传
# ---------------------------------------------------------------------------


def test_execution_runs_under_e2e_env_profile_with_allow_list(tmp_path: Path) -> None:
    """预检与执行段的子进程都必须走 E2E 白名单档并携带 env_allow。"""
    _make_executable_script(tmp_path)
    entry = BrowserE2EVerificationCommand(script="tests/e2e/smoke.sh", env_allow=("E2E_PORT",))
    runner = _RecordingProcessRunner(results=[_success_result(), _success_result()])
    result = run_browser_e2e_verification_command(entry, tmp_path, runner)
    assert result.return_code == 0
    assert result.command == ("browser_e2e", "tests/e2e/smoke.sh")
    assert len(runner.calls) == 2
    for call in runner.calls:
        assert call["env_profile"] == E2E_CHILD_ENV_PROFILE
        assert call["env_allow_extra"] == ("E2E_PORT",)
        assert call["capture_output"] is True
    assert runner.last()["timeout"] == entry.timeout_seconds


def test_script_failure_without_marker_classifies_as_script_failed(tmp_path: Path) -> None:
    """脚本非零且无标记 → script_failed；stderr 保留分类标记与指引。"""
    _make_executable_script(tmp_path)
    runner = _RecordingProcessRunner(
        results=[_success_result(), _failure_result(("bash", "-lc", "x"), "selector not found")]
    )
    result = run_browser_e2e_verification_command(
        BrowserE2EVerificationCommand(script="tests/e2e/smoke.sh"), tmp_path, runner
    )
    assert extract_e2e_failure_category(result) is E2EFailureCategory.SCRIPT_FAILED
    assert "selector not found" in result.stderr


def test_script_emitted_app_not_ready_marker_is_passed_through(tmp_path: Path) -> None:
    """组合 shell 自己发出的 app_not_ready 标记必须被解析为同名分类。"""
    _make_executable_script(tmp_path)
    runner = _RecordingProcessRunner(
        results=[
            _success_result(),
            _failure_result(
                ("bash", "-lc", "x"),
                "BROWSER_E2E_FAILURE [category=app_not_ready] readiness probe timed out",
            ),
        ]
    )
    result = run_browser_e2e_verification_command(_E2E_ENTRY, tmp_path, runner)
    assert extract_e2e_failure_category(result) is E2EFailureCategory.APP_NOT_READY


def test_wall_clock_timeout_classifies_as_script_timeout(tmp_path: Path) -> None:
    """wall-clock 超时 → script_timeout，标记与指引齐全。"""
    _make_executable_script(tmp_path)
    runner = _RecordingProcessRunner()

    def _timeout_run(command, **kwargs):  # noqa: ANN001, ANN002, ANN202
        runner.calls.append({"command": tuple(command), **kwargs})
        if kwargs.get("label", "").startswith("browser-e2e-probe"):
            return _success_result()
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 1))

    runner.run = _timeout_run
    entry = BrowserE2EVerificationCommand(script="tests/e2e/smoke.sh", timeout_seconds=5)
    result = run_browser_e2e_verification_command(entry, tmp_path, runner)
    assert extract_e2e_failure_category(result) is E2EFailureCategory.SCRIPT_TIMEOUT
    assert "process group" in result.stderr


def test_declared_artifact_missing_classifies_as_artifact_unhealthy(tmp_path: Path) -> None:
    """脚本 exit 0 但声明产物不存在 → artifact_unhealthy（FR-11a 硬层）。"""
    _make_executable_script(tmp_path)
    entry = BrowserE2EVerificationCommand(
        script="tests/e2e/smoke.sh",
        artifacts=(E2EVerificationArtifact(path="out/missing.png", mime="image/png"),),
    )
    runner = _RecordingProcessRunner(results=[_success_result(), _success_result()])
    result = run_browser_e2e_verification_command(entry, tmp_path, runner)
    assert extract_e2e_failure_category(result) is E2EFailureCategory.ARTIFACT_UNHEALTHY


# ---------------------------------------------------------------------------
# FR-3 组合 shell：语法有效、进程树回收、就绪分支
# ---------------------------------------------------------------------------


def test_composed_shell_is_syntactically_valid_bash() -> None:
    """组合出的 bash 脚本必须通过 ``bash -n`` 语法检查（真实 shell 解析）。"""
    from backend.core.use_cases.agent_runner_e2e_browser import _compose_e2e_shell

    composed = _compose_e2e_shell(_E2E_ENTRY)
    subprocess.run(["bash", "-n"], input=composed, text=True, check=True, timeout=30)


def test_composed_shell_contains_tree_teardown_and_readiness_markers() -> None:
    """回收 trap、就绪超时的 app_not_ready 标记、注入安全的引号包裹都在脚本里。"""
    from backend.core.use_cases.agent_runner_e2e_browser import _compose_e2e_shell

    entry = BrowserE2EVerificationCommand(
        script="tests/e 2e/smoke's.sh",
        app_start='just run frontend && echo "it\'s up"',
        ready_url="http://localhost:5173/sign-in",
        ready_timeout_seconds=42,
    )
    composed = _compose_e2e_shell(entry)
    assert "trap e2e_cleanup EXIT" in composed
    assert "pgrep -P" in composed
    assert "[category=app_not_ready] readiness probe timed out" in composed
    assert "[category=app_not_ready] app process exited before readiness" in composed
    assert "e2e_ready_deadline=$(( $(date +%s) + 42 ))" in composed
    assert "'tests/e 2e/smoke'\"'\"'s.sh'" in composed  # shlex.quote 生效


def test_composed_shell_without_app_start_skips_readiness_block() -> None:
    """无 app_start 时不生成应用管理层，只保留固定等待分支之外的脚本执行。"""
    from backend.core.use_cases.agent_runner_e2e_browser import _compose_e2e_shell

    composed = _compose_e2e_shell(BrowserE2EVerificationCommand(script="s.sh"))
    assert 'e2e_app_pid=""' in composed
    assert "curl" not in composed
    assert "mktemp" not in composed


# ---------------------------------------------------------------------------
# FR-4 child_env 白名单档
# ---------------------------------------------------------------------------


def test_e2e_child_env_drops_credentials_keeps_browser_runtime_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认关闭白名单：runner 凭据不可见，浏览器运行时所需变量保留。"""
    fake_env = {
        "PATH": "/usr/bin",
        "HOME": "/home/runner",
        "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
        "E2E_APP_PORT": "5173",
        "GITHUB_TOKEN": "ghp_secret",
        "OPENAI_API_KEY": "sk-secret",
        "AWS_SECRET_ACCESS_KEY": "zzz",
        "SERVER_ADMIN_PASSWORD": "hunter2",
    }
    monkeypatch.setattr(os, "environ", fake_env)
    child_env = build_e2e_child_env(extra_allowed=("CUSTOM_OK",))
    assert child_env["PATH"] == "/usr/bin"
    assert child_env["PLAYWRIGHT_BROWSERS_PATH"] == "/ms-playwright"
    assert child_env["E2E_APP_PORT"] == "5173"
    for secret_key in (
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "SERVER_ADMIN_PASSWORD",
    ):
        assert secret_key not in child_env
    assert "CUSTOM_OK" not in child_env  # 未出现在父环境时无副作用

    env_with_extra = dict(fake_env)
    env_with_extra["CUSTOM_OK"] = "yes"
    monkeypatch.setattr(os, "environ", env_with_extra)
    assert build_e2e_child_env(extra_allowed=("CUSTOM_OK",))["CUSTOM_OK"] == "yes"
    assert "CUSTOM_OK" not in build_e2e_child_env()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"cwd": Path("."), "env_profile": "no_such_profile"},
            "Unknown process env profile",
        ),
        (
            {"cwd": Path("."), "env_profile": E2E_CHILD_ENV_PROFILE, "timeout": None},
            "wall-clock timeout",
        ),
        (
            {
                "cwd": Path("."),
                "env_profile": E2E_CHILD_ENV_PROFILE,
                "timeout": 60,
                "capture_output": False,
            },
            "capture",
        ),
    ],
)
def test_env_profile_refuses_unsafe_prerequisites(kwargs: dict[str, object], message: str) -> None:
    """白名单档的安全前提不成立时必须响亮报错，绝不静默回退到继承全量环境。"""
    with pytest.raises(ValueError, match=message):
        SubprocessRunner().run(["true"], **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# run_verification 分发与 FR-7 shell 路径不变性
# ---------------------------------------------------------------------------


def _app_config(commands: tuple[object, ...]) -> AppConfig:
    return AppConfig(runner=RunnerConfig(verification_commands=commands))  # type: ignore[arg-type]


def test_run_verification_passes_shell_entry_field_for_field(tmp_path: Path) -> None:
    """纯 shell 条目的调用形态逐字段不变：bash -lc、无 env_profile。"""
    runner = _RecordingProcessRunner(results=[_success_result()])
    run_verification(tmp_path, _app_config(("git diff --check",)), runner)
    assert runner.calls == [
        {
            "command": ("bash", "-lc", "git diff --check"),
            "cwd": tmp_path,
            "check": False,
        }
    ]


def test_run_verification_dispatches_e2e_entry_and_short_circuits(tmp_path: Path) -> None:
    """E2E 条目分发到浏览器执行路径；其失败同样短路后续命令。"""
    _make_executable_script(tmp_path)
    sentinel = tmp_path / "sentinel"
    runner = _RecordingProcessRunner(
        results=[
            _success_result(),  # probe
            _failure_result(("bash", "-lc", "x"), "boom: selector not found"),  # e2e run
        ]
    )
    entry = BrowserE2EVerificationCommand(script="tests/e2e/smoke.sh")
    results = run_verification(tmp_path, _app_config((entry, f"touch {sentinel}")), runner)
    assert len(results) == 1
    assert extract_e2e_failure_category(results[0]) is E2EFailureCategory.SCRIPT_FAILED
    assert not sentinel.exists()


def test_run_verification_shell_then_e2e_both_pass(tmp_path: Path) -> None:
    """两类条目在同一队列按序执行，全过则全部记录为成功。"""
    _make_executable_script(tmp_path)
    runner = _RecordingProcessRunner(
        results=[_success_result(), _success_result(), _success_result()]
    )
    results = run_verification(tmp_path, _app_config(("git diff --check", _E2E_ENTRY)), runner)
    assert all(result.return_code == 0 for result in results)
    assert len(results) == 2
    assert runner.calls[0]["command"] == ("bash", "-lc", "git diff --check")
    assert runner.calls[2]["env_profile"] == E2E_CHILD_ENV_PROFILE


# ---------------------------------------------------------------------------
# 渲染层：分类进入 recovery prompt / Issue 评论（D-03）
# ---------------------------------------------------------------------------


def test_describe_verification_command_renders_both_shapes() -> None:
    """纯 shell 原样透传；E2E 条目渲染为带形态标记的单行描述。"""
    assert describe_verification_command("git diff --check") == "git diff --check"
    described = describe_verification_command(_E2E_ENTRY)
    assert described == "[browser_e2e] tests/e2e/smoke.sh (app: just run frontend)"


def test_recovery_and_comment_rendering_carry_subcategory() -> None:
    """E2E 失败结果在 recovery prompt 与失败评论里都带子分类行。"""
    failed = _e2e_failure_result(E2EFailureCategory.BROWSER_RUNTIME_MISSING)
    recovery_text = format_result_for_recovery(failed)
    assert "sub-category `browser_runtime_missing`" in recovery_text
    comment_text = format_verification_failure([failed])
    assert "browser_runtime_missing" in comment_text


def test_plain_shell_failure_renders_unchanged_without_marker() -> None:
    """非 E2E 失败结果的渲染不出现分类行（逐字段不变回归）。"""
    plain = CommandResult(
        command=("bash", "-lc", "just test"), return_code=1, stdout="out", stderr="err"
    )
    assert format_e2e_failure_classification(plain) == ""
    recovery_text = format_result_for_recovery(plain)
    assert "sub-category" not in recovery_text
    assert recovery_text.startswith("Command: `bash -lc 'just test'`\nExit code: 1\nstdout:")


def test_success_result_has_no_classification() -> None:
    """无标记时 extract 返回 None；成功结果正常。"""
    result = CommandResult(command=("bash", "-lc", "true"), return_code=0, stdout="", stderr="")
    assert extract_e2e_failure_category(result) is None
    assert format_e2e_failure_classification(result) == ""


def test_artifact_gate_rejects_stale_file(tmp_path: Path) -> None:
    """产物门禁使用 started_at 做新鲜度下限：陈旧文件判 artifact_unhealthy。"""
    _make_executable_script(tmp_path)
    artifact = tmp_path / "out" / "shot.png"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"\x89PNG fake-bytes")
    old_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(artifact, (old_timestamp, old_timestamp))

    entry = BrowserE2EVerificationCommand(
        script="tests/e2e/smoke.sh",
        artifacts=(E2EVerificationArtifact(path="out/shot.png", mime="image/png"),),
    )

    # 预检与执行段返回成功；mime 探测返回 image/png，让陈旧判定落在
    # freshness 分支而非 mime 分支。
    def _routing_run(command, **kwargs):  # noqa: ANN001, ANN002, ANN202
        if str(command[0]) == "file":
            return CommandResult(
                command=tuple(command), return_code=0, stdout="image/png\n", stderr=""
            )
        return _success_result()

    runner = _RecordingProcessRunner()
    runner.run = _routing_run
    result = run_browser_e2e_verification_command(entry, tmp_path, runner)
    assert extract_e2e_failure_category(result) is E2EFailureCategory.ARTIFACT_UNHEALTHY

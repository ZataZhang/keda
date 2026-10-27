"""内置恢复巡检的真实 CLI、文件与 shell 边界验证。"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from backend.api import cli, cli_loop, cli_loop_template
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError
from backend.api.cli_parser import build_parser
from backend.api.cli_typer import app
from backend.core.shared.models.agent_runner import AppConfig, LabelConfig, RepositoryRunContext
from backend.core.shared.models.agent_runner_config import RepositoryIdentity
from backend.core.shared.models.loop import LoopSchedule
from backend.core.use_cases.loop_builtin import (
    BuiltinLoopRequest,
    create_builtin_loop,
    render_blocker_monitor_recipe,
)
from backend.core.use_cases.loop_recipe import (
    parse_loop_recipe,
    parse_loop_recipe_text,
    parse_pre_command_output,
    render_loop_recipe,
)
from backend.engines.agent_runner.persistence.loop_state_json import JsonLoopStateStore
from backend.infrastructure.config.settings import AgentRunnerSettings


@pytest.fixture
def repository_context(tmp_path: Path) -> RepositoryRunContext:
    """构造不同于 keda 的仓库与标签，避免模板硬编码被遗漏。"""
    repo_path = tmp_path / "other repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo_path)], check=True)
    (repo_path / ".kedacode.toml").write_text(
        '[agent_runner.repository]\nid = "product"\ngithub_repo = "example/product"\n'
        '[agent_runner.labels]\nblocked = "custom/blocked"\nfailed = "custom/failed"\n',
        encoding="utf-8",
    )
    identity = RepositoryIdentity("product", str(repo_path), "Product", "example/product")
    return RepositoryRunContext(
        repo_id="product",
        display_name="Product",
        repo_path=repo_path,
        config=AppConfig(
            repositories={"product": identity},
            labels=replace(LabelConfig(), blocked="custom/blocked", failed="custom/failed"),
        ),
    )


def test_cli_template_registers_without_firing_or_overwriting(
    repository_context: RepositoryRunContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonLoopStateStore(tmp_path / "state.json")
    monkeypatch.setattr(cli, "create_loop_state_store", lambda: store)
    settings = AgentRunnerSettings(
        repositories={"product": {"path": str(repository_context.repo_path)}},
        labels={"blocked": "custom/blocked", "failed": "custom/failed"},
    )
    monkeypatch.setattr(
        cli_loop_template,
        "get_agent_runner_settings",
        lambda: settings,
    )
    cli_args = [
        "loop",
        "create",
        "product-blockers",
        "--template",
        "blocker-monitor",
        "--repo-id",
        "product",
        "--every",
        "1h",
    ]
    cli_result = CliRunner().invoke(app, cli_args, standalone_mode=False)
    assert cli_result.exit_code == 0 and cli_result.return_value == 0, cli_result.output
    store.load()
    task = store.get_task("product-blockers")
    assert task is not None
    assert task.fire_count == 0
    assert task.schedule.expression == "1h"
    assert task.managed_by == "agent-daemon"
    assert task.output == "issue"
    assert task.skip_when == "needs_attention=0"
    recipe = parse_loop_recipe(task.recipe_path)
    assert recipe.repo_id == "product"
    assert recipe.schedule.expression == "30m"
    assert "example/product" in recipe.body_template
    assert "custom/blocked" in recipe.body_template
    assert "ZataZhang" not in recipe.body_template
    assert "technical-blocker" in cli_result.output
    original_text = task.recipe_path.read_text(encoding="utf-8")
    repeated = CliRunner().invoke(app, [*cli_args, "--force"], standalone_mode=False)
    assert repeated.return_value == int(ExitCode.CONFLICT), repeated.output
    assert task.recipe_path.read_text(encoding="utf-8") == original_text
    assert list(repository_context.repo_path.rglob("*.md")) == [task.recipe_path]


@pytest.mark.parametrize("arguments", [[], ["--recipe", "x", "--template", "blocker-monitor"]])
def test_template_source_is_exclusive(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["loop", "create", "demo", *arguments])
    cli_result = CliRunner().invoke(
        app, ["loop", "create", "demo", *arguments], standalone_mode=False
    )
    assert cli_result.return_value == int(ExitCode.USAGE)


def test_template_requires_explicit_registered_repo(tmp_path: Path) -> None:
    with pytest.raises(CliError) as raised:
        cli_loop.run_loop_create_command(
            argparse.Namespace(loop_id="demo", template="blocker-monitor"),
            state_store_factory=lambda: JsonLoopStateStore(tmp_path / "state.json"),
        )
    assert raised.value.code == ExitCode.USAGE
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("failure", ["template", "schedule", "slug", "github"])
def test_invalid_template_creates_no_files(
    repository_context: RepositoryRunContext, tmp_path: Path, failure: str
) -> None:
    request = BuiltinLoopRequest(
        loop_id="../escape" if failure == "slug" else "product-blockers",
        template_name="missing" if failure == "template" else "blocker-monitor",
        schedule=LoopSchedule.from_expression("0 0 31 2 *") if failure == "schedule" else None,
    )
    if failure == "github":
        (repository_context.repo_path / ".kedacode.toml").unlink()
    context = (
        replace(repository_context, config=AppConfig())
        if failure == "github"
        else repository_context
    )
    with pytest.raises(ValueError):
        create_builtin_loop(
            request, context=context, state_store=JsonLoopStateStore(tmp_path / "state.json")
        )
    assert not (repository_context.repo_path / "tasks").exists()
    assert not (tmp_path / "state.json").exists()


def test_failed_state_write_removes_new_recipe(
    repository_context: RepositoryRunContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonLoopStateStore(tmp_path / "state.json")

    def fail_write(task) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "upsert_task", fail_write)
    with pytest.raises(OSError, match="disk unavailable"):
        create_builtin_loop(
            BuiltinLoopRequest("product-blockers", "blocker-monitor"),
            context=repository_context,
            state_store=store,
        )
    assert not list(repository_context.repo_path.rglob("*.md"))


@pytest.mark.parametrize(
    "scenario",
    [
        ("0", "0", "0", "none"),
        ("2", "1", "3", "none"),
        ("FAIL", "0", "unknown", "HTTP 401"),
        ("0", "bad", "unknown", "invalid count"),
    ],
)
def test_generated_pre_command_runs_in_real_shell(
    repository_context: RepositoryRunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: tuple[str, str, str, str],
) -> None:
    blocked_output, failed_output, expected_attention, expected_error = scenario
    # 仅替换外部 gh 传输；YAML 解析、shell 执行和模板渲染保持真实。
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh_script = fake_bin / "gh"
    gh_script.write_text(
        '#!/bin/sh\ncase "$*" in *custom/blocked*) answer="$BLOCKED_OUTPUT";; '
        '*) answer="$FAILED_OUTPUT";; esac\n'
        'if [ "$answer" = FAIL ]; then printf "HTTP 401\\nlogin required\\n" >&2; exit 1; fi\n'
        'printf "%s\\n" "$answer"\n',
        encoding="utf-8",
    )
    gh_script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("BLOCKED_OUTPUT", blocked_output)
    monkeypatch.setenv("FAILED_OUTPUT", failed_output)
    recipe = parse_loop_recipe_text(
        render_blocker_monitor_recipe(repository_context, loop_id="product-blockers"),
        source_path=tmp_path / "recipe.md",
    )
    shell_result = subprocess.run(
        ["/bin/sh", "-c", recipe.pre_command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    variables = parse_pre_command_output(shell_result.stdout)
    assert variables["needs_attention"] == expected_attention
    assert expected_error in variables["state_read_error"]
    assert len(shell_result.stdout.splitlines()) == 4
    rendered = render_loop_recipe(
        recipe, fire_at=datetime.now(timezone.utc), extra_variables=variables
    )
    assert "{{" not in rendered
    assert variables["state_read_error"] in rendered

"""真实 Codex 与 Keda 生命周期执行，外部 GitHub 写入替换为本地夹具。"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from unittest.mock import patch

from backend.core.shared.models import product_identity
from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_model_preset import ModelSelection, resolve_model_selection
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.lifecycle_agent import LIFECYCLE_AGENT_KEYS
from backend.core.use_cases import agent_runner_orchestrate
from backend.engines.agent_runner.factory import (
    build_app_config_from_settings,
    create_process_runner,
    load_fresh_agent_runner_settings,
)
from backend.engines.agent_runner.factory_config_merge import merge_repository_config
from backend.engines.model_evaluation.local_github import ISSUE_NUMBER, LocalEvaluationGitHub
from backend.infrastructure.config.agent_runner_repository_settings import (
    load_agent_runner_local_settings,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from backend.core.use_cases.model_benchmark.common import (
    ALTERNATE_REVIEW_ARM,
    CellSpec,
    git,
    run_logged_process,
    save_json,
)


def with_call_budget(prompt: str, seconds: int) -> str:
    """为模型标明本次调用实际可用的共享预算。

    Args:
        prompt: 原任务或阶段上下文。
        seconds: 当前总预算内剩余的完整秒数。

    Returns:
        带运行时预算说明的模型输入，不修改题目正文记录。
    """
    deadline = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return (
        f"[Evaluation runtime budget: {seconds} seconds remaining for this invocation.]\n"
        f"UTC deadline: {deadline.isoformat(timespec='seconds')}.\n"
        "This includes tools and the final response. The task's total limit is shared "
        "across phases; it is not a fresh allowance for this call. Prioritize the requested "
        "behavior and affected contracts, and report unfinished verification honestly.\n\n" + prompt
    )


def benchmark_model(preset_name: str, config: AppConfig) -> ModelSelection:
    """确认预设能作为同一 Codex 协议下的执行或审查模型。

    Args:
        preset_name: 当前使用的命名预设。
        config: 仓库生效配置。

    Returns:
        含模型和推理档的已解析配置。

    Raises:
        ValueError: 预设不存在或不是显式配置的 Codex 预设。
    """
    selection = resolve_model_selection(preset_name, config)
    if selection.agent != "codex" or not selection.model or not selection.reasoning_effort:
        raise ValueError(f"评测预设 {preset_name!r} 必须指定 Codex 模型和推理档")
    return selection


def build_config(spec: CellSpec) -> AppConfig:
    """冻结本机预设并仅按实验组切换审查，保留两组一致的验证步骤。

    Args:
        spec: 实验组及总时间预算。

    Returns:
        用于本地隔离生命周期的 AppConfig。

    Raises:
        ValueError: 预设未指向有效的 Codex 模型和推理档。
    """
    config = build_app_config_from_settings(load_fresh_agent_runner_settings())
    repository_settings = load_agent_runner_local_settings(spec.run.root)
    if repository_settings is not None:
        config = merge_repository_config(config, repository_settings)
    implementation = benchmark_model(spec.run.preset, config)
    if (
        spec.run.implementation_model is not None
        and implementation != spec.run.implementation_model
    ):
        raise ValueError("实现预设已改变，请另建评测")
    review_preset = spec.run.review_preset if spec.arm == ALTERNATE_REVIEW_ARM else spec.run.preset
    if review_preset is None:
        raise ValueError("替代审查组需要一个审查预设")
    review_selection = benchmark_model(review_preset, config)
    if (
        spec.arm == ALTERNATE_REVIEW_ARM
        and spec.run.alternate_review_model is not None
        and review_selection != spec.run.alternate_review_model
    ):
        raise ValueError("替代审查预设已改变，请另建评测")
    return replace(
        config,
        lifecycle_presets=replace(
            config.lifecycle_presets,
            repository_layer={
                key: review_preset if key == "review" else spec.run.preset
                for key in LIFECYCLE_AGENT_KEYS
            },
        ),
        lifecycle_config_loader=None,
        git=replace(config.git, remote="origin", base_branch="main"),
        worktree=replace(
            config.worktree,
            create_command="git switch -c issue-{issue_number} {base_branch}",
            reuse_command="pwd",
            path_command="pwd",
            provision_database=False,
            base_branch="main",
        ),
        runner=replace(
            config.runner,
            verification_commands=(
                "git diff --check",
                f"{shlex.quote(sys.executable)} -m compileall -q src/backend",
            ),
            pre_commit_verification_command=None,
            agent_fallback_order=(),
            agent_fallback_candidates=(),
            max_agent_switches=0,
            max_recovery_attempts=1,
            transient_retry_attempts=0,
            timeout_resume_attempts=0,
            recovery_retry_delay_seconds=0,
            timeout_seconds=spec.run.seconds,
        ),
        memory=replace(config.memory, enabled=False),
        autopilot=replace(config.autopilot, enabled=False),
        generated_content=replace(config.generated_content, enabled=False),
        safety=replace(config.safety, auto_merge=False),
        stall_supervisor=replace(config.stall_supervisor, enabled=False),
        post_pr_supervisor=replace(config.post_pr_supervisor, enabled=False),
        pre_pr_review=replace(
            config.pre_pr_review,
            enabled=spec.arm in ("keda-review", ALTERNATE_REVIEW_ARM),
            allow_same_agent=True,
            max_attempts=1,
            repair_agent="self",
        ),
    )


class ObservedRunner:
    """对生产 ProcessRunner 加入相同总预算、实际模型核验与审查前提交记录。"""

    def __init__(
        self,
        spec: CellSpec,
        deadline: float,
        *,
        model_selection: ModelSelection | None = None,
        review_selection: ModelSelection | None = None,
    ) -> None:
        """保存单格运行上下文，内部调用保持生产实现。"""
        self.spec = spec
        self.deadline = deadline
        self.model_selection = model_selection or ModelSelection(
            agent="codex", model="gpt-6-luna", reasoning_effort="max"
        )
        self.review_selection = review_selection or self.model_selection
        self.inner = create_process_runner()
        self.invocations: list[dict] = []
        self.before_review: str | None = None

    def __getattr__(self, name: str):
        """委托生产 runner 的其他能力。"""
        return getattr(self.inner, name)

    @wraps(IProcessRunner.run)
    def run(self, command, **options):
        """调用真实进程并留下模型、耗时和用量证据。"""
        executable = Path(command[0]).name
        if executable == "gh":
            raise RuntimeError("评测只允许本地 GitHub 客户端")
        if executable == "git" and command[1:2] == ["push"]:
            # 候选可能改过 remote；发布前按 Git 实际解析后的 push URL 核对。
            expected_origin = str(self.spec.directory / "origin.git")
            if "origin" not in command[2:] or git(
                Path(options["cwd"]), "remote", "get-url", "--push", "--all", "origin"
            ).splitlines() != [expected_origin]:
                raise RuntimeError("评测只允许向本格的本地 Git 远端推送")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("三组共享的总执行时间已用尽")
        options["timeout"] = min(remaining, options.get("timeout") or remaining)
        if executable != "codex":
            return self.inner.run(command, **options)
        label = options.get("label") or ""
        execution_log = self.spec.directory / "execution.log"
        if self.before_review is None and execution_log.is_file():
            # 两个阶段共用 Issue 标签；生产日志提供明确的审查前提交。
            review_heads = re.findall(
                r"^INFO:backend.core.use_cases.agent_review:Starting pre-PR review "
                r".*head=([0-9a-f]{40})\)\.$",
                execution_log.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
            if review_heads:
                self.before_review = review_heads[0]
        expected = self.review_selection if self.before_review else self.model_selection
        actual_model = command[command.index("--model") + 1] if "--model" in command else None
        if (
            executable != expected.agent
            or actual_model != expected.model
            or f"model_reasoning_effort={expected.reasoning_effort}" not in command
        ):
            raise ValueError("实际模型命令偏离本格冻结的 Codex 模型与推理档")
        original_prompt = options.get("input_text")
        uses_stdin = isinstance(original_prompt, str)
        if not uses_stdin:
            # 生产 Codex run profile 使用 argv_tail，deliberate 则使用 stdin。
            original_prompt = command[-1]
        call_budget = int(remaining)
        model_prompt = with_call_budget(original_prompt, call_budget)
        if uses_stdin:
            options["input_text"] = model_prompt
        else:
            command = [*command[:-1], model_prompt]
        (self.spec.directory / f"invocation-{len(self.invocations) + 1}-prompt.txt").write_text(
            model_prompt, encoding="utf-8"
        )
        invocation = {
            "label": label,
            "model": actual_model,
            "effort": expected.reasoning_effort,
            "provided_budget_seconds": call_budget,
        }
        self.invocations.append(invocation)
        started = time.monotonic()
        try:
            outcome = self.inner.run(command, **options)
            invocation["exit_code"] = outcome.return_code
            invocation["usage"] = (
                asdict(outcome.token_usage) if outcome.token_usage is not None else None
            )
            return outcome
        except Exception as error:  # noqa: BLE001 - 执行证据边界记录后原样抛出
            invocation["error"] = type(error).__name__
            raise
        finally:
            invocation["seconds"] = round(time.monotonic() - started, 2)
            save_json(self.spec.directory / "invocations.json", self.invocations)


def run_keda(spec: CellSpec, prompt: str) -> dict:
    """通过生产 Issue 生命周期运行一次，Git 和状态写入仅落本格。

    Args:
        spec: 实验配置。
        prompt: 三组共享的任务正文。

    Returns:
        正常交付、模型调用及审查前 checkpoint 的记录。
    """
    started = time.monotonic()
    config = build_config(spec)
    repo = spec.directory / "repo"
    remote = spec.directory / "origin.git"
    state_dir = spec.directory / "state"
    state_dir.mkdir()
    save_json(
        spec.directory / "workflow-config.json",
        {
            "preset": spec.run.preset,
            "seconds": spec.run.seconds,
            "review": asdict(config.pre_pr_review),
            "runner": asdict(config.runner),
            "lifecycle_bindings": dict(config.lifecycle_presets.repository_layer),
            "github_boundary": "local fixture",
            "memory_enabled": False,
            "post_pr_supervisor_enabled": False,
        },
    )
    git(spec.directory, "init", "--bare", str(remote))
    git(repo, "branch", "-M", "main")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    client = LocalEvaluationGitHub(
        title=spec.case_id.replace("-", " "),
        prompt=prompt,
        ready_label=config.labels.ready,
        base_sha=git(repo, "rev-parse", "HEAD").strip(),
    )
    implementation = benchmark_model(spec.run.preset, config)
    review_preset = config.lifecycle_presets.repository_layer["review"]
    runner = ObservedRunner(
        spec,
        started + spec.run.seconds,
        model_selection=implementation,
        review_selection=benchmark_model(review_preset, config),
    )
    isolated_config = state_dir / "config.toml"
    selected_presets = {
        implementation.preset_name: implementation,
        review_preset: runner.review_selection,
    }
    preset_blocks = [
        f"\n[agent_runner.presets.{json.dumps(name)}]\n"
        f"agent = {json.dumps(selection.agent)}\nmodel = {json.dumps(selection.model)}\n"
        f"reasoning_effort = {json.dumps(selection.reasoning_effort)}\n"
        for name, selection in selected_presets.items()
    ]
    isolated_config.write_text(
        "[agent_runner.console]\nhistory_db_path = "
        + json.dumps(str(state_dir / "console.db"))
        + "\n"
        + "".join(preset_blocks),
        encoding="utf-8",
    )
    execution_error = None
    exit_code = None
    try:
        with (
            patch.dict(
                os.environ,
                {"KEDACODE_CONFIG": str(isolated_config), "KEDACODE_NO_UPDATE_CHECK": "1"},
            ),
            patch.object(product_identity, "state_home", lambda home_path=None: state_dir),
        ):
            exit_code = agent_runner_orchestrate.run_once(
                repo_path=repo,
                config=config,
                dry_run=False,
                agent="auto",
                max_issues=1,
                github_client=client,
                process_runner=runner,
                target_issue=ISSUE_NUMBER,
                run_history_store=SqliteConsoleStore(state_dir / "console.db"),
                run_trigger="local_evaluation",
                repo_id=f"eval-{spec.case_id}",
                log_base=spec.directory / "logs",
            )
    except Exception as error:  # noqa: BLE001 - 实验单格失败也必须保留补丁并评分
        execution_error = f"{type(error).__name__}: {error}"
    published = client.draft is not None
    if published:
        save_json(spec.directory / "local-draft-pr.json", client.draft)
    return {
        "seconds": round(time.monotonic() - started, 2),
        "exit_code": exit_code,
        "delivered": exit_code == 0 and published,
        "local_draft_created": published,
        "execution_error": execution_error,
        "agent_calls": runner.invocations,
        "before_review": runner.before_review,
    }


def run_native(spec: CellSpec, prompt: str) -> dict:
    """真实调用一次原生 Codex，遵守与 Keda 相同的总时间上限。

    Args:
        spec: 实验配置。
        prompt: 三组共享的任务正文。

    Returns:
        退出状态、用量和实际命令元数据。
    """
    repo = spec.directory / "repo"
    config = build_config(spec)
    selection = benchmark_model(spec.run.preset, config)
    model_prompt = with_call_budget(prompt, spec.run.seconds)
    (spec.directory / "model-prompt.txt").write_text(model_prompt, encoding="utf-8")
    command = [
        config.agents["codex"].bin,
        "--cd",
        str(repo),
        "--sandbox",
        "workspace-write",
        "--config",
        "sandbox_workspace_write.network_access=true",
        "--ask-for-approval",
        "never",
        "--model",
        str(selection.model),
        "--config",
        f"model_reasoning_effort={selection.reasoning_effort}",
        "exec",
        "--json",
        model_prompt,
    ]
    started = time.monotonic()
    output_path = spec.directory / "agent.jsonl"
    with (
        output_path.open("w", encoding="utf-8") as stdout,
        (spec.directory / "agent.stderr").open("w", encoding="utf-8") as stderr,
    ):
        return_code, timed_out = run_logged_process(
            command,
            cwd=repo,
            timeout=spec.run.seconds,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
    turn_usages = []
    for line in output_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            turn_usages.append(event.get("usage", {}))
    elapsed = round(time.monotonic() - started, 2)
    return {
        "seconds": elapsed,
        "exit_code": return_code,
        "timeout": timed_out,
        "delivered": return_code == 0 and bool(turn_usages),
        "agent_calls": [
            {
                "model": selection.model,
                "effort": selection.reasoning_effort,
                "seconds": elapsed,
                "provided_budget_seconds": spec.run.seconds,
                "turn_usages": turn_usages,
            }
        ],
    }

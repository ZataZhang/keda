#!/usr/bin/env python3
"""通过真实 ``kc run --issue`` 验证 Issue #290 的依赖准备。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ISSUE_NUMBER = 290
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
PRD_SKILL_PATH = Path(
    os.environ.get(
        "KEDACODE_PRD_SKILL_PATH",
        str(Path.home() / ".codex" / "skills" / "prd" / "SKILL.md"),
    )
)


def _write_text(file_path: Path, content: str, *, mode: int | None = None) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    if mode is not None:
        file_path.chmod(mode)


def _run_git(repo_path: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _make_fake_gh(bin_path: Path, state_path: Path, python_path: str) -> None:
    """安装仅接受本验证所需 GitHub REST 路由的文件型 gh 边界替身。"""
    gh_program = f"#!{python_path}\n" + r'''
import json
import sys
from pathlib import Path

state_path = Path(__STATE_PATH__)
arguments = sys.argv[1:]
state = json.loads(state_path.read_text(encoding="utf-8"))
state.setdefault("calls", []).append(arguments)
state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

if arguments[:2] == ["pr", "create"]:
    pull_request_url = "https://github.com/example/issue-290-rv/pull/291"
    state["created_pr_url"] = pull_request_url
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(pull_request_url)
    raise SystemExit(0)

def emit(value):
    print(json.dumps(value, ensure_ascii=False))

if arguments[:2] == ["api", "rate_limit"]:
    emit({"resources": {"core": {"limit": 5000, "remaining": 5000}}})
    raise SystemExit(0)

if arguments[:2] == ["api", "user"]:
    emit({"login": "rv-fixture"})
    raise SystemExit(0)

if len(arguments) < 2 or arguments[0] != "api":
    print("unrecognized gh argv: " + repr(arguments), file=sys.stderr)
    raise SystemExit(2)

method = "GET"
endpoint = None
field_values = []
index = 1
while index < len(arguments):
    argument = arguments[index]
    if argument in ("-X", "--method"):
        method = arguments[index + 1].upper()
        index += 2
        continue
    if argument in ("-f", "-F", "--field"):
        field_values.append(arguments[index + 1])
        index += 2
        continue
    if argument == "--paginate":
        index += 1
        continue
    if argument.startswith("repos/"):
        endpoint = argument
        index += 1
        continue
    if argument.startswith("--"):
        index += 1
        continue
    if endpoint is None and argument != "api":
        endpoint = argument
    index += 1

if endpoint is None:
    print("unrecognized gh api argv: " + repr(arguments), file=sys.stderr)
    raise SystemExit(2)

if endpoint.endswith("/issues/290") and method == "GET":
    issue = state["issue"]
    emit({**issue, "labels": [{"name": name} for name in issue["label_names"]]})
    raise SystemExit(0)

if endpoint.endswith("/issues") and method == "GET":
    requested_labels = next(
        (value.split("=", 1)[1] for value in field_values if value.startswith("labels=")),
        "",
    )
    if requested_labels == "agent/ready":
        issue = state["issue"]
        emit([{**issue, "labels": [{"name": name} for name in issue["label_names"]]}])
    else:
        emit([])
    raise SystemExit(0)

if endpoint.endswith("/issues/290/labels") and method == "POST":
    for field in field_values:
        if field.startswith("labels[]="):
            label_name = field.split("=", 1)[1]
            if label_name not in state["issue"]["label_names"]:
                state["issue"]["label_names"].append(label_name)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    emit([{"name": label_name} for label_name in state["issue"]["label_names"]])
    raise SystemExit(0)

if endpoint.startswith("repos/example/issue-290-rv/issues/290/labels/") and method == "DELETE":
    label_name = endpoint.rsplit("/", 1)[1].replace("%2F", "/")
    state["issue"]["label_names"] = [
        label for label in state["issue"]["label_names"] if label != label_name
    ]
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    emit([{"name": label_name} for label_name in state["issue"]["label_names"]])
    raise SystemExit(0)

if endpoint.endswith("/issues/290/comments") and method == "GET":
    emit([state["comments"]])
    raise SystemExit(0)

if endpoint.endswith("/issues/290/comments") and method == "POST":
    body_value = next((value.split("=", 1)[1] for value in field_values if value.startswith("body=")), "")
    if body_value.startswith("@"):
        body_value = Path(body_value[1:]).read_text(encoding="utf-8")
    comment = {"id": len(state["comments"]) + 1, "body": body_value, "user": {"login": "rv-fixture"}}
    state["comments"].append(comment)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    emit(comment)
    raise SystemExit(0)

if endpoint.startswith("repos/example/issue-290-rv/issues/comments/") and method == "PATCH":
    comment_id = int(endpoint.rsplit("/", 1)[1])
    body_value = next((value.split("=", 1)[1] for value in field_values if value.startswith("body=")), "")
    if body_value.startswith("@"):
        body_value = Path(body_value[1:]).read_text(encoding="utf-8")
    comment = next((entry for entry in state["comments"] if entry["id"] == comment_id), None)
    if comment is None:
        print("unknown comment id: " + str(comment_id), file=sys.stderr)
        raise SystemExit(2)
    comment["body"] = body_value
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    emit(comment)
    raise SystemExit(0)

if "/pulls" in endpoint and method == "GET":
    emit([])
    raise SystemExit(0)

print("unrecognized gh route: " + method + " " + endpoint, file=sys.stderr)
print("argv: " + repr(arguments), file=sys.stderr)
raise SystemExit(2)
'''.replace("__STATE_PATH__", repr(str(state_path)))
    _write_text(bin_path / "gh", gh_program, mode=0o755)


def _make_fake_codex(bin_path: Path, python_path: str) -> None:
    """Create a plan-driven executor fixture that records each real invocation."""
    codex_program = f"#!{python_path}\n" + r'''
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

worktree = Path.cwd()
prompt_text = " ".join(sys.argv[1:])
report_path = worktree / ".agent-runner" / "dependency-preparation.json"
probe_path = worktree / ".agent-runner" / "rv-agent-probe.jsonl"
plan_path = Path(os.environ["RV_AGENT_PLAN_PATH"])
plan = json.loads(plan_path.read_text(encoding="utf-8"))
records = []
if report_path.is_file():
    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = report.get("projects", [])
record_map = {record.get("project_path"): record for record in records}
has_dependency_context = "Worktree dependency preparation context" in prompt_text
role = "implementer" if has_dependency_context else "skill-distillation"
plan_key = "codex:" + role
step_queue = plan.get("steps", {}).get(plan_key)
if not isinstance(step_queue, list) or not step_queue:
    print("RV_EXECUTOR_PLAN_FAILED: missing plan steps for " + plan_key, file=sys.stderr)
    raise SystemExit(92)
plan_cursors = plan.setdefault("cursors", {})
step_index = int(plan_cursors.get(plan_key, 0))
plan_step = step_queue[min(step_index, len(step_queue) - 1)]
plan_cursors[plan_key] = step_index + 1
plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if role == "skill-distillation":
    skill_prompt_marker = "提炼成可复用的操作 skill"
    if skill_prompt_marker not in prompt_text:
        print("RV_EXECUTOR_PLAN_FAILED: unrecognized auxiliary prompt", file=sys.stderr)
        raise SystemExit(92)
    auxiliary_probe = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "executor": "codex",
        "role": role,
        "scenario": plan_step.get("scenario"),
        "action": plan_step.get("action"),
        "argv": sys.argv[1:],
        "cwd": str(worktree),
        "prompt_head": prompt_text[:500],
        "checks": {"recognized_skill_distillation_prompt": True},
        "record_count": len(records),
    }
    with probe_path.open("a", encoding="utf-8") as probe_file:
        probe_file.write(json.dumps(auxiliary_probe, ensure_ascii=False) + "\n")
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Fixture completed a non-implementation agent stage."}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    raise SystemExit(0)

scenario = plan_step.get("scenario")
action = plan_step.get("action")
if scenario not in (
    "missing",
    "dangling-node-modules",
    "workspace-no-root-manifest",
    "workspace-exclusion",
    "privilege",
    "credentials",
    "reuse",
    "reuse-negative",
):
    print("RV_EXECUTOR_PLAN_FAILED: unrecognized scenario " + repr(scenario), file=sys.stderr)
    raise SystemExit(92)

def has_record(path, reason=None, manager=None, outcome=None, action=None):
    record = record_map.get(path, {})
    return (
        (reason is None or record.get("reason") == reason)
        and (manager is None or record.get("package_manager") == manager)
        and (outcome is None or record.get("outcome") == outcome)
        and (action is None or record.get("requires_user_action") == action)
    )

checks = {
    "prompt_has_dependency_context": has_dependency_context,
    "prompt_says_diagnose_not_fail": "A dependency gap is not a task failure" in prompt_text,
    "report_is_readable": report_path.is_file(),
    "agent_invocation_has_prompt": bool(prompt_text),
}
if scenario in ("missing", "dangling-node-modules"):
    config_text = Path(".kedacode.toml").read_text(encoding="utf-8")
    checks.update({
        "workspace_root_pnpm_missing": has_record(".", "tool_missing", "pnpm"),
        "workspace_member_skipped": has_record("packages/client", "workspace_covered", "pnpm", "skipped"),
        "nested_package_not_covered_by_single_level_glob": has_record(
            "packages/client/nested", "tool_missing", "pnpm", "not_prepared"
        ),
        "nested_package_diagnostic_reaches_prompt": (
            "`packages/client/nested` - NOT ready "
            "(outcome: not_prepared, reason: tool_missing, package manager: pnpm)" in prompt_text
        ),
        "workspace_manager_conflict_preserved": has_record("packages/conflict", "manager_conflict"),
        "package_manager_npm_used": has_record("tools/declared", "tool_missing", "npm"),
        "lockfile_conflict_preserved": has_record("tools/conflict", "manager_conflict"),
        "pnpm_workspace_npm_root_conflict_preserved": has_record(
            "tools/yaml-conflict", "manager_conflict", outcome="not_prepared"
        ),
        "pnpm_workspace_npm_member_conflict_preserved": has_record(
            "tools/yaml-conflict/packages/app", "manager_conflict", outcome="not_prepared"
        ),
        "planned_command_in_prompt": "pnpm install --ignore-scripts" in prompt_text,
        "missing_tool_detail_in_prompt": "'pnpm' is not on PATH" in prompt_text,
        "package_manager_selection_evidence_in_prompt": "package.json packageManager declares 'pnpm'" in prompt_text,
        "workspace_conflict_evidence_in_prompt": "workspace root '.' package.json packageManager declares 'pnpm'" in prompt_text,
        "missing_tools_are_not_upfront_human_actions": all(
            record.get("requires_user_action") is None
            for record in records
            if record.get("reason") == "tool_missing"
        ) and "host_toolchain" not in prompt_text,
        "safe_local_or_alternative_guidance_present": "repository-local" in prompt_text,
        "unrelated_check_needs_no_frontend_tool": 'verification_commands = ["git diff --check"]' in config_text,
        "plan_continues_only_unrelated_work": action == "continue_non_dependency_work",
    })
elif scenario == "workspace-no-root-manifest":
    root_record = record_map.get(".", {})
    checks.update({
        "manifestless_pnpm_workspace_root_is_discovered": has_record(
            ".", "tool_missing", "pnpm", "not_prepared"
        ),
        "workspace_root_lockfile_selects_pnpm": any(
            "lockfile(s): pnpm-lock.yaml" in evidence_line
            for evidence_line in root_record.get("selection_evidence", [])
        ),
        "workspace_root_install_command_is_planned": (
            "pnpm install --ignore-scripts" in root_record.get("attempted_commands", [])
        ),
        "workspace_root_missing_tool_detail_is_recorded": (
            "pnpm' is not on PATH" in root_record.get("detail", "")
            and any(
                phrase in root_record.get("detail", "")
                for phrase in ("was not run", "was never run")
            )
            and root_record.get("exit_code") is None
            and shutil.which("pnpm") is None
        ),
        "workspace_member_is_covered_by_root": has_record(
            "packages/app", "workspace_covered", "pnpm", "skipped"
        ),
        "workspace_root_diagnostic_reaches_prompt": (
            f"`{'.'}` - NOT ready (outcome: not_prepared, reason: tool_missing, package manager: pnpm)"
            in prompt_text
            and "pnpm install --ignore-scripts" in prompt_text
            and "'pnpm' is not on PATH" in prompt_text
        ),
        "missing_tool_is_not_human_prerequisite": (
            root_record.get("requires_user_action") is None
            and "host_toolchain" not in prompt_text
        ),
        "agent_chooses_safe_unrelated_work": action == "continue_non_dependency_work",
    })
elif scenario == "workspace-exclusion":
    checks.update({
        "included_neighbor_remains_workspace_member": has_record(
            "packages/client", "workspace_covered", "pnpm", "skipped"
        ),
        "excluded_nested_package_is_not_workspace_member": has_record(
            "packages/client/nested", "tool_missing", "pnpm", "not_prepared"
        ),
        "excluded_nested_diagnostic_reaches_prompt": (
            "`packages/client/nested` - NOT ready "
            "(outcome: not_prepared, reason: tool_missing, package manager: pnpm)" in prompt_text
        ),
        "excluded_nested_install_command_reaches_prompt": (
            "pnpm install --ignore-scripts --ignore-workspace" in prompt_text
        ),
        "excluded_nested_missing_tool_is_not_human_prerequisite": all(
            record.get("requires_user_action") is None
            for record in records
            if record.get("project_path") == "packages/client/nested"
        ),
        "agent_chooses_safe_unrelated_work": action == "continue_non_dependency_work",
    })
elif scenario == "privilege":
    checks.update({
        "install_failure_not_success": has_record("tools/declared", "install_failed", "npm", "not_prepared", "privilege"),
        "permission_detail_present": any("EACCES" in record.get("detail", "") for record in records),
        "agent_chooses_permission_blocker": action == "request_privilege_decision",
    })
elif scenario == "credentials":
    checks.update({
        "install_failure_not_success": has_record("tools/declared", "install_failed", "npm", "not_prepared", "credentials"),
        "credential_detail_present": any("E401" in record.get("detail", "") for record in records),
        "agent_chooses_credential_blocker": action == "request_credentials",
    })
elif scenario == "reuse":
    reuse_record = record_map.get("tools/declared", {})
    config_text = Path(".kedacode.toml").read_text(encoding="utf-8")
    checks.update({
        "matching_main_checkout_dependencies_reused": has_record(
            "tools/declared", "no_action_needed", "npm", "reused"
        ) and reuse_record.get("attempted_commands") == [],
        "reuse_status_visible_in_prompt": (
            "`tools/declared` - ready (outcome: reused, reason: no_action_needed" in prompt_text
        ),
        "reuse_reason_visible_in_prompt": (
            "matching main checkout dependencies were reused before installation" in prompt_text
        ),
        "npm_is_available_but_not_needed": shutil.which("npm") is not None,
        "verification_requires_reused_symlink": (
            'verification_commands = ["test -L tools/declared/node_modules && '
            'test -f tools/declared/node_modules/.rv-ready"]' in config_text
        ),
        "agent_chooses_to_use_reused_dependencies": action == "continue_with_reused_dependencies",
    })
elif scenario == "reuse-negative":
    install_record = record_map.get("tools/declared", {})
    worktree_modules_path = worktree / "tools/declared/node_modules"
    checks.update({
        "baseline_installed_instead_of_reusing": (
            install_record.get("outcome") == "installed"
            and "npm ci --ignore-scripts" in install_record.get("attempted_commands", [])
        ),
        "baseline_worktree_modules_are_not_reuse_link": not worktree_modules_path.is_symlink(),
        "agent_can_continue_after_install": action == "continue_after_install",
    })
checks["all_prompt_checks_pass"] = all(checks.values())
probe = {
    "timestamp_utc": datetime.now(UTC).isoformat(),
    "executor": "codex",
    "role": role,
    "scenario": scenario,
    "action": action,
    "argv": sys.argv[1:],
    "cwd": str(worktree),
    "prompt_head": prompt_text[:500],
    "checks": checks,
    "record_count": len(records),
}
with probe_path.open("a", encoding="utf-8") as probe_file:
    probe_file.write(json.dumps(probe, ensure_ascii=False) + "\n")
if not checks["all_prompt_checks_pass"]:
    print("RV_AGENT_ORACLE_FAILED: " + json.dumps(checks, sort_keys=True))
    raise SystemExit(91)

processed_line = f"Agent decision for {scenario}: {action}."
if action == "continue_non_dependency_work":
    processed_line += " Frontend dependencies are not needed for the configured check."
elif action == "continue_with_reused_dependencies":
    processed_line += " Use the matching main-checkout node_modules link."
elif action == "continue_after_install":
    processed_line += " Let the configured symlink oracle distinguish install from reuse."
elif action in ("request_privilege_decision", "request_credentials"):
    processed_line += " Stop before changing host permissions or using unprovided credentials."
readme_path = worktree / "README.md"
readme_text = readme_path.read_text(encoding="utf-8")
readme_path.write_text(readme_text.rstrip() + "\n\n" + processed_line + "\n", encoding="utf-8")
commit_request_path = worktree / ".agent-runner" / "commit-request.json"
commit_request_path.write_text(
    json.dumps({"commit_message": "docs: record dependency preparation result"}, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": processed_line}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
'''
    _write_text(bin_path / "codex", codex_program, mode=0o755)


def _make_repository(
    scene_path: Path,
    scenario: str,
    *,
    verification_command: str,
    project_setup,
    real_npm: bool = False,
    force_tracked_paths: tuple[str, ...] = (),
) -> tuple[Path, Path, Path]:
    repo_path = scene_path / scenario / "repository"
    bin_path = scene_path / scenario / "bin"
    state_path = scene_path / scenario / "github-state.json"
    bin_path.mkdir(parents=True)
    repo_path.mkdir(parents=True)
    _make_fake_gh(bin_path, state_path, sys.executable)
    action_by_scenario = {
        "missing": "continue_non_dependency_work",
        "dangling-node-modules": "continue_non_dependency_work",
        "workspace-no-root-manifest": "continue_non_dependency_work",
        "workspace-exclusion": "continue_non_dependency_work",
        "privilege": "request_privilege_decision",
        "credentials": "request_credentials",
        "reuse": "continue_with_reused_dependencies",
        "reuse-negative": "continue_after_install",
        "pre-fix": "unexpected_agent_launch",
    }
    plan_path = scene_path / scenario / "agent-plan.json"
    _write_text(
        plan_path,
        json.dumps(
            {
                "steps": {
                    "codex:implementer": [
                        {
                            "scenario": scenario,
                            "action": action_by_scenario[scenario],
                        }
                    ],
                    "codex:skill-distillation": [
                        {
                            "scenario": "skill-distillation",
                            "action": "complete_auxiliary_stage",
                        }
                    ],
                },
                "cursors": {},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    _make_fake_codex(bin_path, sys.executable)
    if real_npm:
        _make_real_node_toolchain(bin_path)

    _write_text(
        repo_path / ".gitignore",
        ".iar-worktrees/\n.agent-runner/\nnode_modules/\n",
    )
    _write_text(
        repo_path / ".kedacode.toml",
        "[agent_runner.repository]\n"
        'id = "issue-290-rv"\n'
        'github_repo = "example/issue-290-rv"\n'
        "\n[agent_runner.runner]\n"
        "agent_fallback_order = []\n"
        "max_agent_switches = 0\n"
        "max_recovery_attempts = 0\n"
        "recovery_retry_delay_seconds = 0\n"
        f"verification_commands = [{json.dumps(verification_command)}]\n",
    )
    with (repo_path / ".kedacode.toml").open("a", encoding="utf-8") as repository_config:
        repository_config.write(
            "\n[agent_runner.worktree]\n"
            "provision_database = false\n"
            "\n[agent_runner.generated_content]\n"
            "enabled = false\n"
            "\n[agent_runner.pre_pr_review]\n"
            "enabled = false\n"
        )
    _write_text(repo_path / "README.md", "# Isolated Issue 290 validation\n")
    project_setup(repo_path)
    _run_git(repo_path, "init", "--initial-branch=main")
    _run_git(repo_path, "config", "user.email", "rv@example.invalid")
    _run_git(repo_path, "config", "user.name", "Issue 290 RV")
    _run_git(repo_path, "add", ".")
    for force_tracked_path in force_tracked_paths:
        _run_git(repo_path, "add", "-f", force_tracked_path)
    _run_git(repo_path, "commit", "-m", "fixture")
    remote_path = scene_path / scenario / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote_path)],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _run_git(repo_path, "remote", "add", "origin", str(remote_path))
    _run_git(repo_path, "push", "--set-upstream", "origin", "main")
    state = {
        "issue": {
            "number": ISSUE_NUMBER,
            "title": "Issue 290 isolated validation",
            "body": "Isolated local fixture; no PRD anchor.",
            "state": "open",
            "html_url": "https://github.com/example/issue-290-rv/issues/290",
            "label_names": ["agent/ready"],
        },
        "comments": [],
        "calls": [],
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return repo_path, bin_path, state_path


def _root_workspace(repo_path: Path) -> None:
    _write_text(
        repo_path / "package.json",
        json.dumps(
            {
                "name": "rv-root",
                "packageManager": "pnpm@10.2.0",
                "workspaces": ["packages/*"],
            }
        )
        + "\n",
    )
    _write_text(repo_path / "pnpm-workspace.yaml", 'packages:\n  - "packages/*"\n')
    _write_text(repo_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write_text(repo_path / "packages/client/package.json", '{"name":"client"}\n')
    _write_text(
        repo_path / "packages/client/nested/package.json",
        '{"name":"nested-client"}\n',
    )
    _write_text(
        repo_path / "packages/client/nested/pnpm-lock.yaml",
        "lockfileVersion: '9.0'\n",
    )
    _write_text(
        repo_path / "packages/conflict/package.json",
        '{"name":"conflict","packageManager":"npm@10.0.0"}\n',
    )
    _write_text(repo_path / "tools/declared/package.json", '{"name":"declared","packageManager":"npm@10.0.0"}\n')
    _write_text(repo_path / "tools/declared/package-lock.json", '{"lockfileVersion":3}\n')
    _write_text(repo_path / "tools/conflict/package.json", '{"name":"lock-conflict","packageManager":"npm@10.0.0"}\n')
    _write_text(repo_path / "tools/conflict/package-lock.json", '{"lockfileVersion":3}\n')
    _write_text(repo_path / "tools/conflict/pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write_text(
        repo_path / "tools/yaml-conflict/package.json",
        '{"name":"yaml-conflict","packageManager":"npm@10.0.0"}\n',
    )
    _write_text(repo_path / "tools/yaml-conflict/package-lock.json", '{"lockfileVersion":3}\n')
    _write_text(
        repo_path / "tools/yaml-conflict/pnpm-workspace.yaml",
        'packages:\n  - "packages/*"\n',
    )
    _write_text(
        repo_path / "tools/yaml-conflict/packages/app/package.json", '{"name":"yaml-child"}\n'
    )


def _pnpm_workspace_without_root_manifest(repo_path: Path) -> None:
    """pnpm workspace 根只含 workspace 配置和锁文件，没有 package.json。"""
    _write_text(repo_path / "pnpm-workspace.yaml", 'packages:\n  - "packages/*"\n')
    _write_text(repo_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write_text(repo_path / "packages/app/package.json", '{"name":"app"}\n')


def _root_workspace_with_excluded_nested_package(repo_path: Path) -> None:
    """在递归 workspace 中显式排除已有的嵌套独立包。"""
    _root_workspace(repo_path)
    _write_text(
        repo_path / "pnpm-workspace.yaml",
        'packages:\n  - "packages/**"\n  - "!packages/client/nested"\n',
    )


def _root_workspace_with_dangling_node_modules(repo_path: Path) -> None:
    """给 npm 项目放入一条指向不存在目录的既有链接。"""
    _root_workspace(repo_path)
    (repo_path / "tools/declared/node_modules").symlink_to(
        repo_path / ".rv-missing-node-modules-target",
        target_is_directory=True,
    )


def _single_npm_project(repo_path: Path) -> None:
    project_path = repo_path / "tools/declared"
    dependency_path = repo_path / "tools/dependency"
    _write_text(
        project_path / "package.json",
        json.dumps(
            {
                "name": "rv-npm",
                "version": "1.0.0",
                "packageManager": "npm@10.0.0",
                "dependencies": {"rv-dependency": "file:../dependency"},
            }
        )
        + "\n",
    )
    _write_text(dependency_path / "package.json", '{"name":"rv-dependency","version":"1.0.0"}\n')
    npm_path = shutil.which("npm")
    if npm_path is None:
        raise RuntimeError("host npm is needed only to prepare the offline lockfile fixture")
    lock_result = subprocess.run(
        [npm_path, "install", "--package-lock-only", "--ignore-scripts", "--offline"],
        cwd=project_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    if lock_result.returncode != 0:
        raise RuntimeError("offline npm lockfile fixture failed: " + lock_result.stderr[-1000:])


def _single_npm_project_with_reusable_modules(repo_path: Path) -> None:
    """准备真实 npm 锁文件和主检出可复用的依赖标记。"""
    _single_npm_project(repo_path)
    _write_text(repo_path / "tools/declared/node_modules/.rv-ready", "reusable\n")


def _credential_npm_project(repo_path: Path, registry_url: str) -> None:
    dependency_name = "rv-private-package"
    package_version = "1.0.0"
    package_project = repo_path / "tools/declared"
    package_body = {
        "name": "rv-npm",
        "version": "1.0.0",
        "packageManager": "npm@10.0.0",
        "dependencies": {dependency_name: package_version},
    }
    lock_body = {
        "name": "rv-npm",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {
                "name": "rv-npm",
                "version": "1.0.0",
                "dependencies": {dependency_name: package_version},
            },
            f"node_modules/{dependency_name}": {
                "version": package_version,
                "resolved": (
                    f"{registry_url}/{dependency_name}/-/{dependency_name}-{package_version}.tgz"
                ),
            },
        },
    }
    _write_text(package_project / "package.json", json.dumps(package_body) + "\n")
    _write_text(package_project / "package-lock.json", json.dumps(lock_body) + "\n")


def _make_real_node_toolchain(bin_path: Path) -> None:
    """暴露宿主真实 node/npm 可执行文件，供隔离场景运行实际 npm ci。"""
    for executable_name in ("node", "npm"):
        source_path = shutil.which(executable_name)
        if source_path is None:
            raise RuntimeError(f"host {executable_name} is required for real npm failure scenarios")
        (bin_path / executable_name).symlink_to(source_path)


def _start_unauthorized_registry() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    class UnauthorizedRegistryHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            response_body = b'{"error":"unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("WWW-Authenticate", 'Basic realm="isolated-rv-registry"')
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, format_string: str, *args: object) -> None:
            return

    registry_server = ThreadingHTTPServer(("127.0.0.1", 0), UnauthorizedRegistryHandler)
    registry_thread = threading.Thread(target=registry_server.serve_forever, daemon=True)
    registry_thread.start()
    registry_url = f"http://127.0.0.1:{registry_server.server_port}"
    return registry_server, registry_thread, registry_url


def _fresh_environment(
    scene_path: Path,
    config_path: Path,
    bin_path: Path,
    *,
    repo_path: Path,
    source_path: Path,
    expected_scenario: str,
) -> dict[str, str]:
    home_path = scene_path / "home"
    home_path.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home_path),
        "LANG": "C.UTF-8",
        "PATH": os.pathsep.join((str(bin_path), BASE_PATH)),
        "KEDACODE_CONFIG": str(config_path),
        "KEDACODE_SKIP_GH_AUTH_CHECK": "1",
        "KEDACODE_NO_UPDATE_CHECK": "1",
        "KEDACODE_PRD_SKILL_PATH": str(PRD_SKILL_PATH),
        "PYTHONPATH": str(source_path),
        "RV_EXPECTED_SCENARIO": expected_scenario,
        "RV_AGENT_PLAN_PATH": str(repo_path.parent / "agent-plan.json"),
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", "/private/tmp/issue-290-uv-cache"),
        "UV_OFFLINE": "1",
    }


def _run_kc(
    *,
    repo_root: Path,
    repo_path: Path,
    scene_path: Path,
    config_path: Path,
    bin_path: Path,
    source_path: Path,
    expected_scenario: str,
) -> subprocess.CompletedProcess[str]:
    uv_path = shutil.which("uv")
    if uv_path is None:
        raise RuntimeError("uv executable is unavailable")
    command = [
        uv_path,
        "run",
        "--project",
        str(repo_root),
        "kc",
        "run",
        "--issue",
        str(ISSUE_NUMBER),
        "--repo",
        str(repo_path),
        "--agent",
        "codex",
    ]
    permission_fault_applied = threading.Event()
    stop_permission_watcher = threading.Event()
    permission_project_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}" / "tools/declared"

    def apply_permission_fault() -> None:
        while not stop_permission_watcher.is_set():
            worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
            if (worktree_path / ".git").exists() and permission_project_path.is_dir():
                permission_project_path.chmod(0o555)
                permission_fault_applied.set()
                return
            stop_permission_watcher.wait(0.01)

    permission_watcher = None
    if expected_scenario == "privilege":
        permission_watcher = threading.Thread(target=apply_permission_fault, daemon=True)
        permission_watcher.start()
    try:
        run_result = subprocess.run(
            command,
            cwd=repo_path,
            env=_fresh_environment(
                scene_path,
                config_path,
                bin_path,
                repo_path=repo_path,
                source_path=source_path,
                expected_scenario=expected_scenario,
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired as timeout_error:
        stdout = timeout_error.stdout or ""
        stderr = timeout_error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        run_result = subprocess.CompletedProcess(
            command,
            124,
            stdout=stdout,
            stderr=stderr + "\nRV_TIMEOUT: real kc run --issue exceeded 90 seconds",
        )
    finally:
        stop_permission_watcher.set()
        if permission_watcher is not None:
            permission_watcher.join(timeout=1)
        if permission_fault_applied.is_set() and permission_project_path.exists():
            permission_project_path.chmod(0o755)
    setattr(run_result, "rv_permission_fault_applied", permission_fault_applied.is_set())
    return run_result


def _load_json(file_path: Path) -> dict[str, Any]:
    return json.loads(file_path.read_text(encoding="utf-8"))


def _assert(condition: bool, label: str, observed: object) -> None:
    print(f"[CHECK] {label}: {'PASS' if condition else 'FAIL'}")
    if not condition:
        print(f"  observed: {observed}")
        raise AssertionError(label)


def _export_source_tree(repo_root: Path, source_ref: str, archive_path: Path) -> Path:
    """从 Git 引用导出隔离入口运行所需的 ``src`` 树。"""
    archive_path.mkdir(parents=True)
    archive_bytes = subprocess.run(
        ["git", "archive", source_ref, "src"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    subprocess.run(
        ["tar", "-x", "-C", str(archive_path)],
        input=archive_bytes,
        check=True,
    )
    return archive_path / "src"


def _baseline_negative_control(repo_root: Path, scene_path: Path) -> None:
    print("== negative control: pre-fix source through real kc run --issue")
    # 固定为 #295 合并前的真实主线；当前 main 已含基础修复，不能再作为原始缺陷负控。
    main_ref = os.environ.get("RV_PRE_FIX_REF", "e687f44b987a9404def52dc42a7df81725ba3fb2")
    baseline_src_path = _export_source_tree(
        repo_root,
        main_ref,
        scene_path / "pre-fix-source",
    )
    _assert((baseline_src_path / "backend/core/use_cases/worktree_frontend.py").is_file(), "修复前源码已导出", main_ref)

    def setup_missing_npm_project(repo_path: Path) -> None:
        _single_npm_project(repo_path)

    config_path = scene_path / "pre-fix-config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    repo_path, bin_path, state_path = _make_repository(
        scene_path,
        "pre-fix",
        verification_command="git diff --check",
        project_setup=setup_missing_npm_project,
    )
    # 负对照把 npm 从 PATH 中移出；旧准备代码会直接调用 npm 并在 Agent 前崩溃。
    result = _run_kc(
        repo_root=repo_root,
        repo_path=repo_path,
        scene_path=scene_path,
        config_path=config_path,
        bin_path=bin_path,
        source_path=baseline_src_path,
        expected_scenario="missing",
    )
    worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
    agent_probe_path = worktree_path / ".agent-runner" / "rv-agent-probe.jsonl"
    output_text = result.stdout + "\n" + result.stderr
    print("BASELINE_KC_OUTPUT_BEGIN")
    print(output_text[-12000:])
    print("BASELINE_KC_OUTPUT_END")
    file_not_found_line = next(
        (line.strip() for line in output_text.splitlines() if "No such file or directory" in line),
        "missing executable diagnostic not printed",
    )
    state = _load_json(state_path)
    _assert(result.returncode != 0, "修复前真实 kc run 返回失败", result.returncode)
    _assert(
        "No such file or directory" in output_text and "npm" in output_text,
        "修复前复现 npm 缺失异常",
        file_not_found_line,
    )
    _assert(not agent_probe_path.exists(), "修复前 Agent 未启动", str(agent_probe_path.exists()))
    _assert("agent/failed" in state["issue"]["label_names"], "修复前被标记 agent/failed", state["issue"]["label_names"])
    print(f"BASELINE_EXIT: {result.returncode}")
    print(f"BASELINE_FAILURE: {file_not_found_line}")
    print(f"BASELINE_AGENT_STARTED: {agent_probe_path.exists()}")
    print(f"BASELINE_LABELS: {state['issue']['label_names']}")
    print("RESULT: RED AS EXPECTED (dependency preparation failed before agent start)")


def _reuse_order_negative_control(repo_root: Path, scene_path: Path) -> None:
    """注入跳过安全复用的故障，真实 npm ci 和复用 oracle 必须让它变红。"""
    print("== negative control: injected reuse miss runs npm before the reuse oracle")
    control_root = scene_path / "reuse-order-control"
    control_root.mkdir()
    install_first_source_path = _source_with_reuse_miss(repo_root, control_root)
    config_path = control_root / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    verification_command = (
        "test -L tools/declared/node_modules && "
        "test -f tools/declared/node_modules/.rv-ready"
    )
    repo_path, bin_path, state_path = _make_repository(
        control_root,
        "reuse-negative",
        verification_command=verification_command,
        project_setup=_single_npm_project_with_reusable_modules,
        real_npm=True,
    )
    run_result = _run_kc(
        repo_root=repo_root,
        repo_path=repo_path,
        scene_path=control_root,
        config_path=config_path,
        bin_path=bin_path,
        source_path=install_first_source_path,
        expected_scenario="reuse-negative",
    )
    worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
    dependency_report = _load_json(
        worktree_path / ".agent-runner" / "dependency-preparation.json"
    )
    npm_record = next(
        project_record
        for project_record in dependency_report["projects"]
        if project_record.get("project_path") == "tools/declared"
    )
    source_modules_path = repo_path / "tools/declared/node_modules"
    worktree_modules_path = worktree_path / "tools/declared/node_modules"
    state = _load_json(state_path)
    print(f"REUSE_NEGATIVE_EXIT: {run_result.returncode}")
    print(f"REUSE_NEGATIVE_OUTCOME: {npm_record.get('outcome')}")
    print(f"REUSE_NEGATIVE_ATTEMPTED_COMMANDS: {npm_record.get('attempted_commands')}")
    print(f"REUSE_NEGATIVE_MAIN_MARKER_EXISTS: {(source_modules_path / '.rv-ready').is_file()}")
    print(f"REUSE_NEGATIVE_WORKTREE_IS_REUSE_LINK: {worktree_modules_path.is_symlink()}")
    print(f"REUSE_NEGATIVE_LABELS: {state['issue']['label_names']}")
    _assert(run_result.returncode != 0, "注入复用失败后的真实依赖复用 oracle 返回失败", run_result.returncode)
    _assert(
        npm_record.get("outcome") == "installed"
        and "npm ci --ignore-scripts" in npm_record.get("attempted_commands", []),
        "复用失败时真实 npm ci 运行且未复用现有依赖",
        npm_record,
    )
    _assert((source_modules_path / ".rv-ready").is_file(), "主检出存在可复用依赖标记", source_modules_path)
    _assert(
        worktree_modules_path.is_dir() and not worktree_modules_path.is_symlink(),
        "负对照 worktree 由 npm 新装而非复用链接产生",
        worktree_modules_path,
    )
    _assert(
        "agent/failed" in state["issue"]["label_names"],
        "安装优先流程未通过复用 oracle",
        state["issue"]["label_names"],
    )
    print("RESULT: RED AS EXPECTED (injected reuse miss ran npm ci instead of reusing matching node_modules)")


def _source_with_cross_segment_workspace_glob(repo_root: Path, scene_path: Path) -> Path:
    """只在临时源码副本中恢复跨路径段 glob 匹配故障。"""
    fault_source_path = scene_path / "cross-segment-workspace-glob-fault" / "src"
    shutil.copytree(repo_root / "src", fault_source_path)
    package_manager_source_path = (
        fault_source_path / "backend/core/use_cases/worktree_package_manager.py"
    )
    package_manager_source_text = package_manager_source_path.read_text(encoding="utf-8")
    function_start = package_manager_source_text.index("def _matches_workspace_pattern(")
    function_end = package_manager_source_text.index(
        "\n\ndef _matching_workspace_member_pattern", function_start
    )
    injected_matcher = '''def _matches_workspace_pattern(relative_posix_path: str, pattern: str) -> bool:
    """负对照：fnmatch 的星号会错误地跨越斜线。"""
    normalized_pattern = pattern.strip().rstrip("/")
    if not normalized_pattern:
        return False
    candidate_path = relative_posix_path.rstrip("/")
    if fnmatch.fnmatch(candidate_path, normalized_pattern):
        return True
    return fnmatch.fnmatch(f"{candidate_path}/", normalized_pattern)'''
    package_manager_source_path.write_text(
        package_manager_source_text[:function_start]
        + injected_matcher
        + package_manager_source_text[function_end:],
        encoding="utf-8",
    )
    return fault_source_path


def _workspace_glob_negative_control(repo_root: Path, scene_path: Path) -> None:
    """确认恢复跨层级 ``*`` 故障时，真实 kc run 的嵌套项目 oracle 变红。"""
    print("== negative control: inject cross-directory matching for workspace '*'")
    control_root = scene_path / "cross-segment-workspace-glob-control"
    control_root.mkdir()
    fault_source_path = _source_with_cross_segment_workspace_glob(repo_root, control_root)
    config_path = control_root / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    repo_path, bin_path, state_path = _make_repository(
        control_root,
        "missing",
        verification_command="git diff --check",
        project_setup=_root_workspace,
    )
    run_result = _run_kc(
        repo_root=repo_root,
        repo_path=repo_path,
        scene_path=control_root,
        config_path=config_path,
        bin_path=bin_path,
        source_path=fault_source_path,
        expected_scenario="missing",
    )
    worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
    report = _load_json(worktree_path / ".agent-runner" / "dependency-preparation.json")
    nested_record = next(
        record for record in report["projects"]
        if record.get("project_path") == "packages/client/nested"
    )
    probe_path = worktree_path / ".agent-runner" / "rv-agent-probe.jsonl"
    probes = [
        json.loads(line)
        for line in probe_path.read_text(encoding="utf-8").splitlines()
    ]
    implementation_probe = next(probe for probe in probes if probe.get("role") == "implementer")
    nested_match_check = implementation_probe["checks"].get(
        "nested_package_not_covered_by_single_level_glob"
    )
    state = _load_json(state_path)
    print(f"WORKSPACE_GLOB_NEGATIVE_EXIT: {run_result.returncode}")
    print(f"WORKSPACE_GLOB_NEGATIVE_NESTED_OUTCOME: {nested_record.get('outcome')}")
    print(f"WORKSPACE_GLOB_NEGATIVE_NESTED_REASON: {nested_record.get('reason')}")
    print(f"WORKSPACE_GLOB_NEGATIVE_AGENT_CHECK: {nested_match_check}")
    print(f"WORKSPACE_GLOB_NEGATIVE_LABELS: {state['issue']['label_names']}")
    _assert(
        nested_record.get("outcome") == "skipped"
        and nested_record.get("reason") == "workspace_covered",
        "故障注入使 packages/* 错误覆盖嵌套项目",
        nested_record,
    )
    _assert(nested_match_check is False, "真实 Agent prompt oracle 检出被错误跳过的嵌套项目", implementation_probe["checks"])
    _assert(run_result.returncode != 0, "跨层级 glob 故障使真实 kc run 失败", run_result.returncode)
    _assert("agent/failed" in state["issue"]["label_names"], "负对照被真实入口标为 agent/failed", state["issue"]["label_names"])
    print("RESULT: RED AS EXPECTED (workspace '*' incorrectly skipped a nested standalone project)")


def _source_skipping_manifestless_pnpm_workspace_root(
    repo_root: Path,
    scene_path: Path,
) -> Path:
    """只在临时源码副本恢复仅扫描 package.json 的旧逻辑。"""
    fault_source_path = scene_path / "manifestless-workspace-root-fault" / "src"
    shutil.copytree(repo_root / "src", fault_source_path)
    frontend_source_path = fault_source_path / "backend/core/use_cases/worktree_frontend.py"
    frontend_source_text = frontend_source_path.read_text(encoding="utf-8")
    current_scan_condition = """        if (
            PACKAGE_MANIFEST_FILE_NAME in child_file_names
            or PNPM_WORKSPACE_FILE_NAME in child_file_names
        ):
            project_dir_paths.append(Path(current_dir_text))"""
    old_scan_condition = """        if PACKAGE_MANIFEST_FILE_NAME in child_file_names:
            project_dir_paths.append(Path(current_dir_text))"""
    if current_scan_condition not in frontend_source_text:
        raise RuntimeError("manifestless workspace negative-control injection point changed")
    frontend_source_path.write_text(
        frontend_source_text.replace(current_scan_condition, old_scan_condition, 1),
        encoding="utf-8",
    )
    return fault_source_path


def _manifestless_workspace_root_negative_control(repo_root: Path, scene_path: Path) -> None:
    """恢复 package.json-only 扫描时，真实 kc run 必须漏掉 workspace 根并失败。"""
    print("== negative control: omit a pnpm workspace root without package.json")
    control_root = scene_path / "manifestless-workspace-root-control"
    control_root.mkdir()
    fault_source_path = _source_skipping_manifestless_pnpm_workspace_root(
        repo_root,
        control_root,
    )
    config_path = control_root / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    repo_path, bin_path, state_path = _make_repository(
        control_root,
        "workspace-no-root-manifest",
        verification_command="git diff --check",
        project_setup=_pnpm_workspace_without_root_manifest,
    )
    run_result = _run_kc(
        repo_root=repo_root,
        repo_path=repo_path,
        scene_path=control_root,
        config_path=config_path,
        bin_path=bin_path,
        source_path=fault_source_path,
        expected_scenario="workspace-no-root-manifest",
    )
    worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
    dependency_report = _load_json(
        worktree_path / ".agent-runner" / "dependency-preparation.json"
    )
    root_record = next(
        (
            record
            for record in dependency_report["projects"]
            if record.get("project_path") == "."
        ),
        None,
    )
    probe_path = worktree_path / ".agent-runner" / "rv-agent-probe.jsonl"
    probe_lines = probe_path.read_text(encoding="utf-8").splitlines()
    implementation_probe = next(
        json.loads(line)
        for line in probe_lines
        if json.loads(line).get("role") == "implementer"
    )
    state = _load_json(state_path)
    print(f"MANIFESTLESS_WORKSPACE_NEGATIVE_EXIT: {run_result.returncode}")
    print(f"MANIFESTLESS_WORKSPACE_NEGATIVE_ROOT_RECORD: {root_record}")
    print(
        "MANIFESTLESS_WORKSPACE_NEGATIVE_AGENT_CHECK: "
        f"{implementation_probe['checks'].get('manifestless_pnpm_workspace_root_is_discovered')}"
    )
    print(f"MANIFESTLESS_WORKSPACE_NEGATIVE_LABELS: {state['issue']['label_names']}")
    _assert(root_record is None, "旧扫描逻辑遗漏无 package.json 的 workspace 根", root_record)
    _assert(
        implementation_probe["checks"].get("manifestless_pnpm_workspace_root_is_discovered")
        is False,
        "真实 Agent prompt oracle 拒绝缺失 workspace 根诊断",
        implementation_probe["checks"],
    )
    _assert(run_result.returncode != 0, "缺失 workspace 根上下文使真实 kc run 失败", run_result.returncode)
    _assert(
        "agent/failed" in state["issue"]["label_names"],
        "workspace 根缺工具诊断遗漏被 Agent oracle 标记失败",
        state["issue"]["label_names"],
    )
    print("RESULT: RED AS EXPECTED (manifestless pnpm workspace root was omitted)")


def _source_with_workspace_exclusions_ignored(repo_root: Path, scene_path: Path) -> Path:
    """只在临时源码副本中注入忽略 workspace 排除 pattern 的旧逻辑。"""
    fault_source_path = scene_path / "workspace-exclusion-fault" / "src"
    shutil.copytree(repo_root / "src", fault_source_path)
    package_manager_source_path = (
        fault_source_path / "backend/core/use_cases/worktree_package_manager.py"
    )
    package_manager_source_text = package_manager_source_path.read_text(encoding="utf-8")
    original_lookup = """        matched_pattern = _matching_workspace_member_pattern(
            member_relative_posix_path,
            _workspace_member_patterns(parent_dir_path),
        )"""
    faulty_lookup = """        matched_pattern = next(
            (
                member_pattern
                for member_pattern in _workspace_member_patterns(parent_dir_path)
                if not member_pattern.startswith("!")
                and _matches_workspace_pattern(member_relative_posix_path, member_pattern)
            ),
            None,
        )"""
    if original_lookup not in package_manager_source_text:
        raise RuntimeError("workspace exclusion lookup anchor was not found in the current source")
    package_manager_source_path.write_text(
        package_manager_source_text.replace(original_lookup, faulty_lookup, 1),
        encoding="utf-8",
    )
    return fault_source_path


def _workspace_exclusion_negative_control(repo_root: Path, scene_path: Path) -> None:
    """确认忽略 pnpm ``!`` pattern 时，真实 kc run oracle 会拒绝错误 skip。"""
    print("== negative control: ignore a matching pnpm workspace exclusion")
    control_root = scene_path / "workspace-exclusion-control"
    control_root.mkdir()
    faulty_source_path = _source_with_workspace_exclusions_ignored(repo_root, control_root)
    config_path = control_root / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    repo_path, bin_path, state_path = _make_repository(
        control_root,
        "workspace-exclusion",
        verification_command="git diff --check",
        project_setup=_root_workspace_with_excluded_nested_package,
    )
    run_result = _run_kc(
        repo_root=repo_root,
        repo_path=repo_path,
        scene_path=control_root,
        config_path=config_path,
        bin_path=bin_path,
        source_path=faulty_source_path,
        expected_scenario="workspace-exclusion",
    )
    worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
    report = _load_json(worktree_path / ".agent-runner" / "dependency-preparation.json")
    nested_record = next(
        record for record in report["projects"]
        if record.get("project_path") == "packages/client/nested"
    )
    probe_lines = (worktree_path / ".agent-runner" / "rv-agent-probe.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    implementation_probe = next(
        json.loads(line) for line in probe_lines
        if json.loads(line).get("role") == "implementer"
    )
    exclusion_check = implementation_probe["checks"].get(
        "excluded_nested_package_is_not_workspace_member"
    )
    state = _load_json(state_path)
    print(f"WORKSPACE_EXCLUSION_NEGATIVE_EXIT: {run_result.returncode}")
    print(f"WORKSPACE_EXCLUSION_NEGATIVE_OUTCOME: {nested_record.get('outcome')}")
    print(f"WORKSPACE_EXCLUSION_NEGATIVE_REASON: {nested_record.get('reason')}")
    print(f"WORKSPACE_EXCLUSION_NEGATIVE_AGENT_CHECK: {exclusion_check}")
    print(f"WORKSPACE_EXCLUSION_NEGATIVE_LABELS: {state['issue']['label_names']}")
    _assert(
        nested_record.get("outcome") == "skipped"
        and nested_record.get("reason") == "workspace_covered",
        "忽略 pnpm 排除 pattern 时嵌套独立项目被错误跳过",
        nested_record,
    )
    _assert(exclusion_check is False, "真实 Agent prompt oracle 检出被忽略的排除规则", implementation_probe["checks"])
    _assert(run_result.returncode != 0, "忽略 workspace 排除规则使真实 kc run 失败", run_result.returncode)
    _assert("agent/failed" in state["issue"]["label_names"], "负对照被真实入口标为 agent/failed", state["issue"]["label_names"])
    print("RESULT: RED AS EXPECTED (pnpm workspace exclusion was ignored)")


def _source_treating_dangling_node_modules_as_ready(repo_root: Path, scene_path: Path) -> Path:
    """只在临时源码副本中注入把悬空 node_modules 误判为 ready 的旧逻辑。"""
    fault_source_path = scene_path / "dangling-node-modules-fault" / "src"
    shutil.copytree(repo_root / "src", fault_source_path)
    frontend_source_path = fault_source_path / "backend/core/use_cases/worktree_frontend.py"
    frontend_source_text = frontend_source_path.read_text(encoding="utf-8")
    current_directory_check = "    if target_node_modules_path.is_dir():\n"
    faulty_existing_path_check = (
        "    if target_node_modules_path.exists() or target_node_modules_path.is_symlink():\n"
    )
    if frontend_source_text.count(current_directory_check) != 1:
        raise RuntimeError("dangling node_modules negative-control injection point changed")
    frontend_source_path.write_text(
        frontend_source_text.replace(current_directory_check, faulty_existing_path_check, 1),
        encoding="utf-8",
    )
    return fault_source_path


def _dangling_node_modules_negative_control(repo_root: Path, scene_path: Path) -> None:
    """确认把断链误判为 ready 时，真实 kc run 的 Agent oracle 会变红。"""
    print("== negative control: treat a dangling node_modules link as ready")
    control_root = scene_path / "dangling-node-modules-control"
    control_root.mkdir()
    faulty_source_path = _source_treating_dangling_node_modules_as_ready(
        repo_root,
        control_root,
    )
    config_path = control_root / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    repo_path, bin_path, state_path = _make_repository(
        control_root,
        "dangling-node-modules",
        verification_command="git diff --check",
        project_setup=_root_workspace_with_dangling_node_modules,
        force_tracked_paths=("tools/declared/node_modules",),
    )
    run_result = _run_kc(
        repo_root=repo_root,
        repo_path=repo_path,
        scene_path=control_root,
        config_path=config_path,
        bin_path=bin_path,
        source_path=faulty_source_path,
        expected_scenario="dangling-node-modules",
    )
    worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
    report = _load_json(worktree_path / ".agent-runner" / "dependency-preparation.json")
    npm_record = next(
        record for record in report["projects"] if record.get("project_path") == "tools/declared"
    )
    probe_lines = (worktree_path / ".agent-runner" / "rv-agent-probe.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    implementation_probe = next(
        json.loads(probe_line)
        for probe_line in probe_lines
        if json.loads(probe_line).get("role") == "implementer"
    )
    state = _load_json(state_path)
    node_modules_path = worktree_path / "tools/declared/node_modules"
    npm_missing_check = implementation_probe["checks"].get("package_manager_npm_used")
    print(f"DANGLING_NEGATIVE_EXIT: {run_result.returncode}")
    print(f"DANGLING_NEGATIVE_OUTCOME: {npm_record.get('outcome')}")
    print(f"DANGLING_NEGATIVE_REASON: {npm_record.get('reason')}")
    print(f"DANGLING_NEGATIVE_AGENT_NPM_CHECK: {npm_missing_check}")
    print(f"DANGLING_NEGATIVE_SYMLINK_PRESERVED: {node_modules_path.is_symlink()}")
    print(f"DANGLING_NEGATIVE_LABELS: {state['issue']['label_names']}")
    _assert(
        npm_record.get("outcome") == "present"
        and npm_record.get("reason") == "no_action_needed",
        "负对照复现断链被误报 ready",
        npm_record,
    )
    _assert(
        npm_missing_check is False,
        "Agent oracle 拒绝缺失的 npm 诊断",
        implementation_probe["checks"],
    )
    _assert(run_result.returncode != 0, "断链误报 ready 使真实 kc run 失败", run_result.returncode)
    _assert(
        "agent/failed" in state["issue"]["label_names"],
        "负对照以 agent/failed 收尾",
        state["issue"]["label_names"],
    )
    print("RESULT: RED AS EXPECTED (dangling node_modules was reported ready)")


def _source_with_reuse_miss(repo_root: Path, scene_path: Path) -> Path:
    """只在临时源码副本中注入复用失败，稳定复现安装优先的回归。"""
    fault_source_path = scene_path / "reuse-miss-fault" / "src"
    shutil.copytree(repo_root / "src", fault_source_path)
    frontend_source_path = fault_source_path / "backend/core/use_cases/worktree_frontend.py"
    frontend_source_text = frontend_source_path.read_text(encoding="utf-8")
    original_reuse_block = (
        "        reuse_attempt = _try_reuse_main_checkout_dependencies(\n"
        "            _ReuseTarget(\n"
        "                repo_root_path=preparation_request.repo_root_path,\n"
        "                project_dir_path=preparation_request.project_dir_path,\n"
        "                relative_project_path=preparation_request.relative_project_path,\n"
        "            )\n"
        "        )\n"
    )
    injected_reuse_block = (
        "        reuse_attempt = _ReuseAttempt(\n"
        "            succeeded=False,\n"
        "            reason=REASON_REUSABLE_ABSENT,\n"
        '            detail="injected negative-control reuse miss",\n'
        "        )\n"
    )
    if frontend_source_text.count(original_reuse_block) != 1:
        raise RuntimeError("reuse miss negative-control injection point changed")
    frontend_source_path.write_text(
        frontend_source_text.replace(original_reuse_block, injected_reuse_block),
        encoding="utf-8",
    )
    return fault_source_path


def _source_with_upfront_toolchain_demand(repo_root: Path, scene_path: Path) -> Path:
    """只在临时源码副本中注入「缺工具就要求人决定」的回归故障。"""
    fault_source_path = scene_path / "upfront-toolchain-fault" / "src"
    shutil.copytree(repo_root / "src", fault_source_path)
    context_source_path = fault_source_path / "backend/core/use_cases/worktree_dependency_context.py"
    context_source_text = context_source_path.read_text(encoding="utf-8")
    original_prompt_block = (
        "        if record.requires_user_action:\n"
        "            lines.append(\n"
        '                f"  - needs a human decision ({record.requires_user_action}): see the "\n'
        '                "handling rules below."\n'
        "            )\n"
    )
    injected_prompt_block = (
        '        action_label = record.requires_user_action or "host_toolchain"\n'
        "        if record.requires_user_action or record.reason == REASON_TOOL_MISSING:\n"
        "            lines.append(\n"
        '                f"  - needs a human decision ({action_label}): see the handling rules below."\n'
        "            )\n"
    )
    if context_source_text.count(original_prompt_block) != 1:
        raise RuntimeError("upfront toolchain negative-control injection point changed")
    context_source_path.write_text(
        context_source_text.replace(original_prompt_block, injected_prompt_block),
        encoding="utf-8",
    )
    return fault_source_path


def _upfront_toolchain_negative_control(repo_root: Path, scene_path: Path) -> None:
    """确认缺工具被错误标成人工决定时，真实入口 oracle 会变红。"""
    print("== negative control: inject upfront host-toolchain demand into the Agent prompt")
    control_root = scene_path / "upfront-toolchain-control"
    control_root.mkdir()
    fault_source_path = _source_with_upfront_toolchain_demand(repo_root, control_root)
    config_path = control_root / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    repo_path, bin_path, state_path = _make_repository(
        control_root,
        "missing",
        verification_command="git diff --check",
        project_setup=_root_workspace,
    )
    run_result = _run_kc(
        repo_root=repo_root,
        repo_path=repo_path,
        scene_path=control_root,
        config_path=config_path,
        bin_path=bin_path,
        source_path=fault_source_path,
        expected_scenario="missing",
    )
    worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
    probe_path = worktree_path / ".agent-runner" / "rv-agent-probe.jsonl"
    probe_lines = probe_path.read_text(encoding="utf-8").splitlines()
    implementation_probe = next(
        json.loads(probe_line)
        for probe_line in probe_lines
        if json.loads(probe_line).get("role") == "implementer"
    )
    prompt_text = " ".join(implementation_probe["argv"])
    state = _load_json(state_path)
    rejected_action = implementation_probe["checks"].get(
        "missing_tools_are_not_upfront_human_actions"
    ) is False
    print(f"UPFRONT_TOOLCHAIN_NEGATIVE_EXIT: {run_result.returncode}")
    print(f"UPFRONT_TOOLCHAIN_PROMPT_MARKER_PRESENT: {'needs a human decision (host_toolchain)' in prompt_text}")
    print(f"UPFRONT_TOOLCHAIN_ORACLE_REJECTED: {rejected_action}")
    print(f"UPFRONT_TOOLCHAIN_FINAL_LABELS: {state['issue']['label_names']}")
    _assert(run_result.returncode != 0, "人工作为缺工具前置条件时真实 kc run 失败", run_result.returncode)
    _assert(
        "needs a human decision (host_toolchain)" in prompt_text,
        "故障注入使 Agent 收到错误的人工作为缺工具前置条件",
        prompt_text,
    )
    _assert(rejected_action, "真实入口 oracle 拒绝预先要求用户配置工具链", implementation_probe["checks"])
    _assert("agent/failed" in state["issue"]["label_names"], "错误上下文未通过 Agent oracle", state["issue"]["label_names"])
    print("RESULT: RED AS EXPECTED (Agent prompt oracle rejects upfront host-toolchain demand)")


def _green_run(
    *,
    repo_root: Path,
    scene_path: Path,
    scenario: str,
    project_setup,
    verification_template: str,
    expected_scenario: str,
    real_npm: bool = False,
    force_tracked_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    bin_path = scene_path / scenario / "bin"
    verification_command = verification_template.format(bin_path=bin_path)
    config_path = scene_path / f"{scenario}-config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    repo_path, bin_path, state_path = _make_repository(
        scene_path,
        scenario,
        verification_command=verification_command,
        project_setup=project_setup,
        real_npm=real_npm,
        force_tracked_paths=force_tracked_paths,
    )
    result = _run_kc(
        repo_root=repo_root,
        repo_path=repo_path,
        scene_path=scene_path,
        config_path=config_path,
        bin_path=bin_path,
        source_path=repo_root / "src",
        expected_scenario=expected_scenario,
    )
    worktree_path = repo_path / ".iar-worktrees" / f"issue-{ISSUE_NUMBER}"
    agent_probe_path = worktree_path / ".agent-runner" / "rv-agent-probe.jsonl"
    report_path = worktree_path / ".agent-runner" / "dependency-preparation.json"
    state = _load_json(state_path)
    probe_lines = (
        agent_probe_path.read_text(encoding="utf-8").splitlines()
        if agent_probe_path.is_file()
        else []
    )
    all_agent_probes = [json.loads(probe_line) for probe_line in probe_lines]
    implementation_probes = [
        agent_probe for agent_probe in all_agent_probes if agent_probe.get("role") == "implementer"
    ]
    probe = implementation_probes[-1] if implementation_probes else {}
    report = _load_json(report_path) if report_path.is_file() else {}
    comments_text = "\n".join(comment.get("body", "") for comment in state["comments"])
    print(f"== real kc run: {scenario}")
    print(f"KC_RUN_EXIT: {result.returncode}")
    print(f"AGENT_STARTED: {bool(implementation_probes)}")
    print(f"AGENT_INVOCATION_COUNT: {len(implementation_probes)}")
    print(f"AUXILIARY_EXECUTOR_INVOCATION_COUNT: {len(all_agent_probes) - len(implementation_probes)}")
    print(f"AGENT_PROCESSED_CONTEXT: {probe.get('checks', {}).get('all_prompt_checks_pass', False)}")
    print(f"AGENT_DECISION: {probe.get('action', 'none')}")
    print(f"AGENT_RECORD_COUNT: {probe.get('record_count', 0)}")
    print(f"FINAL_LABELS: {state['issue']['label_names']}")
    print(f"BLOCK_COMMENT_HAS_REQUIRED_ACTION: {'Required human action(s):' in comments_text}")
    print(f"PRIVILEGE_FAULT_APPLIED: {getattr(result, 'rv_permission_fault_applied', False)}")
    for project_record in report.get("projects", []):
        if project_record.get("reason") == "install_failed":
            print(f"INSTALL_FAILURE: {project_record.get('detail', '')}")
    if result.returncode != 0:
        print("KC_RUN_OUTPUT_BEGIN")
        print((result.stdout + "\n" + result.stderr)[-16000:])
        print("KC_RUN_OUTPUT_END")
        details = [
            line.strip()
            for line in (result.stdout + "\n" + result.stderr).splitlines()
            if any(marker in line for marker in ("unrecognized gh", "Traceback", "ERROR", "Blocked Issue"))
        ]
        print("KC_RUN_DIAGNOSTICS: " + " | ".join(details[-5:]))
    return {
        "return_code": result.returncode,
        "agent_probe": probe,
        "report": report,
        "labels": state["issue"]["label_names"],
        "comments": comments_text,
        "agent_started": bool(implementation_probes),
        "agent_processed": probe.get("checks", {}).get("all_prompt_checks_pass", False),
        "agent_decision": probe.get("action"),
        "permission_fault_applied": getattr(result, "rv_permission_fault_applied", False),
        "repo_path": str(repo_path),
        "worktree_path": str(worktree_path),
        "state": state,
    }


def _run_positive_cases(repo_root: Path, scene_path: Path, registry_url: str) -> None:
    print("== green: real kc run prepares a pnpm workspace without root package.json")
    manifestless_workspace_result = _green_run(
        repo_root=repo_root,
        scene_path=scene_path,
        scenario="workspace-no-root-manifest",
        project_setup=_pnpm_workspace_without_root_manifest,
        verification_template="git diff --check",
        expected_scenario="workspace-no-root-manifest",
    )
    manifestless_root_record = next(
        record
        for record in manifestless_workspace_result["report"].get("projects", [])
        if record.get("project_path") == "."
    )
    _assert(
        manifestless_workspace_result["return_code"] == 0,
        "无根 package.json 的 pnpm workspace 缺工具不阻断真实 kc run",
        manifestless_workspace_result["return_code"],
    )
    _assert(
        manifestless_workspace_result["agent_started"]
        and manifestless_workspace_result["agent_processed"],
        "Agent 启动并处理无根 package.json 的 workspace 缺工具上下文",
        manifestless_workspace_result["agent_probe"],
    )
    _assert(
        manifestless_root_record.get("outcome") == "not_prepared"
        and manifestless_root_record.get("reason") == "tool_missing"
        and manifestless_root_record.get("package_manager") == "pnpm"
        and "pnpm install --ignore-scripts"
        in manifestless_root_record.get("attempted_commands", []),
        "无根 package.json 的 workspace 根保留 pnpm 缺失诊断与计划命令",
        manifestless_root_record,
    )
    _assert(
        manifestless_workspace_result["agent_probe"]["checks"].get(
            "workspace_root_diagnostic_reaches_prompt"
        ) is True,
        "Agent prompt 收到无根 package.json 的 workspace 根诊断",
        manifestless_workspace_result["agent_probe"],
    )
    _assert(
        "agent/failed" not in manifestless_workspace_result["labels"],
        "workspace 根缺少 pnpm 不会误标 agent/failed",
        manifestless_workspace_result["labels"],
    )

    print("== green: real kc run --issue with missing tools and manager conflicts")
    missing_result = _green_run(
        repo_root=repo_root,
        scene_path=scene_path,
        scenario="missing",
        project_setup=_root_workspace,
        verification_template="git diff --check",
        expected_scenario="missing",
    )
    _assert(missing_result["return_code"] == 0, "缺工具诊断被处理后真实 kc run 成功收尾", missing_result["return_code"])
    _assert(missing_result["agent_started"], "缺 pnpm/npm 后真实 kc run 仍启动 Agent", missing_result["labels"])
    _assert(missing_result["agent_processed"], "Agent 处理了包管理器缺失与冲突上下文", missing_result["agent_probe"])
    _assert(
        missing_result["agent_probe"].get("checks", {}).get(
            "missing_tools_are_not_upfront_human_actions"
        ) is True,
        "缺失包管理器只作为诊断，不要求用户预先配置工具链",
        missing_result["agent_probe"],
    )
    _assert(
        missing_result["agent_decision"] == "continue_non_dependency_work",
        "Agent 选择安全继续不依赖前端包的检查",
        missing_result["agent_decision"],
    )
    _assert("agent/failed" not in missing_result["labels"], "环境缺口没有被标记为 agent/failed", missing_result["labels"])
    _assert(
        missing_result["agent_probe"]["checks"].get("package_manager_npm_used") is True,
        "packageManager 字段参与包管理器选择",
        missing_result["agent_probe"],
    )
    _assert(
        missing_result["agent_probe"]["checks"].get("workspace_member_skipped") is True
        and missing_result["agent_probe"]["checks"].get("workspace_manager_conflict_preserved") is True,
        "workspace 根/成员与冲突声明均交给 Agent",
        missing_result["agent_probe"],
    )
    nested_record = next(
        record for record in missing_result["report"].get("projects", [])
        if record.get("project_path") == "packages/client/nested"
    )
    _assert(
        nested_record.get("outcome") == "not_prepared"
        and nested_record.get("reason") == "tool_missing"
        and nested_record.get("package_manager") == "pnpm"
        and "pnpm install --ignore-scripts --ignore-workspace"
        in nested_record.get("attempted_commands", []),
        "packages/* 未覆盖的嵌套项目保留 pnpm 缺失诊断与计划命令",
        nested_record,
    )
    _assert(
        missing_result["agent_probe"]["checks"].get(
            "nested_package_diagnostic_reaches_prompt"
        ) is True,
        "Agent prompt 收到嵌套项目的可恢复缺工具诊断",
        missing_result["agent_probe"],
    )
    _assert(
        missing_result["agent_probe"]["checks"].get("lockfile_conflict_preserved") is True,
        "冲突锁文件没有被静默选定",
        missing_result["agent_probe"],
    )

    print("== green: real kc run diagnoses a dangling node_modules link")
    dangling_result = _green_run(
        repo_root=repo_root,
        scene_path=scene_path,
        scenario="dangling-node-modules",
        project_setup=_root_workspace_with_dangling_node_modules,
        verification_template="git diff --check",
        expected_scenario="dangling-node-modules",
        force_tracked_paths=("tools/declared/node_modules",),
    )
    dangling_npm_record = next(
        record for record in dangling_result["report"].get("projects", [])
        if record.get("project_path") == "tools/declared"
    )
    dangling_node_modules_path = (
        Path(dangling_result["worktree_path"]) / "tools/declared/node_modules"
    )
    _assert(
        dangling_result["return_code"] == 0,
        "断链依赖诊断不阻断真实 kc run",
        dangling_result["return_code"],
    )
    _assert(dangling_result["agent_started"], "断链依赖场景仍启动 Agent", dangling_result["labels"])
    _assert(
        dangling_result["agent_processed"],
        "Agent 收到并处理断链与缺失 npm 上下文",
        dangling_result["agent_probe"],
    )
    _assert(
        dangling_npm_record.get("outcome") == "not_prepared"
        and dangling_npm_record.get("reason") == "tool_missing"
        and dangling_npm_record.get("package_manager") == "npm"
        and "npm ci --ignore-scripts" in dangling_npm_record.get("attempted_commands", [])
        and "dangling symbolic link" in dangling_npm_record.get("detail", "")
        and "npm' is not on PATH" in dangling_npm_record.get("detail", ""),
        "断链 node_modules 保留 npm 缺失诊断和计划命令",
        dangling_npm_record,
    )
    _assert(
        dangling_node_modules_path.is_symlink() and not dangling_node_modules_path.exists(),
        "断链符号链接保持原状，准备器未覆盖 worktree 状态",
        dangling_node_modules_path,
    )
    _assert(
        "agent/failed" not in dangling_result["labels"],
        "断链导致的环境诊断没有标记 agent/failed",
        dangling_result["labels"],
    )

    print("== green: real kc run respects a pnpm workspace exclusion")
    exclusion_result = _green_run(
        repo_root=repo_root,
        scene_path=scene_path,
        scenario="workspace-exclusion",
        project_setup=_root_workspace_with_excluded_nested_package,
        verification_template="git diff --check",
        expected_scenario="workspace-exclusion",
    )
    excluded_nested_record = next(
        record for record in exclusion_result["report"].get("projects", [])
        if record.get("project_path") == "packages/client/nested"
    )
    _assert(
        exclusion_result["return_code"] == 0,
        "排除的嵌套项目不阻断真实 kc run",
        exclusion_result["return_code"],
    )
    _assert(exclusion_result["agent_started"], "排除 pattern 场景仍启动 Agent", exclusion_result["labels"])
    _assert(
        exclusion_result["agent_probe"].get("checks", {}).get(
            "excluded_nested_package_is_not_workspace_member"
        ) is True,
        "pnpm ! 排除 pattern 不把嵌套独立包判作 workspace 成员",
        exclusion_result["agent_probe"],
    )
    _assert(
        excluded_nested_record.get("outcome") == "not_prepared"
        and excluded_nested_record.get("reason") == "tool_missing"
        and excluded_nested_record.get("package_manager") == "pnpm",
        "被排除的独立包保留 pnpm 缺工具诊断",
        excluded_nested_record,
    )

    print("== green: reuse matching main-checkout node_modules before invoking real npm")
    reuse_result = _green_run(
        repo_root=repo_root,
        scene_path=scene_path,
        scenario="reuse",
        project_setup=_single_npm_project_with_reusable_modules,
        verification_template=(
            "test -L tools/declared/node_modules && "
            "test -f tools/declared/node_modules/.rv-ready"
        ),
        expected_scenario="reuse",
        real_npm=True,
    )
    reuse_record = next(
        record for record in reuse_result["report"].get("projects", [])
        if record.get("project_path") == "tools/declared"
    )
    reuse_modules_path = Path(reuse_result["worktree_path"]) / "tools/declared/node_modules"
    _assert(reuse_result["return_code"] == 0, "复用现有依赖后真实 kc run 验证通过", reuse_result["return_code"])
    _assert(reuse_result["agent_started"], "安全复用场景仍启动 Agent", reuse_result["labels"])
    _assert(reuse_result["agent_processed"], "Agent 收到复用状态和原因", reuse_result["agent_probe"])
    _assert(
        reuse_result["agent_decision"] == "continue_with_reused_dependencies",
        "Agent 选择使用已复用依赖",
        reuse_result["agent_decision"],
    )
    _assert(
        reuse_record.get("outcome") == "reused"
        and reuse_record.get("attempted_commands") == [],
        "存在可复用 node_modules 时未运行安装命令",
        reuse_record,
    )
    _assert(reuse_modules_path.is_symlink(), "worktree node_modules 是主检出的安全复用链接", reuse_modules_path)

    print("== green: actual npm install failure with privilege blocker")
    privilege_result = _green_run(
        repo_root=repo_root,
        scene_path=scene_path,
        scenario="privilege",
        project_setup=_single_npm_project,
        verification_template="npm ci --prefix tools/declared --ignore-scripts",
        expected_scenario="privilege",
        real_npm=True,
    )
    privilege_record = next(
        record for record in privilege_result["report"].get("projects", [])
        if record.get("project_path") == "tools/declared"
    )
    _assert(privilege_result["agent_started"], "真实安装权限失败后仍启动 Agent", privilege_result["labels"])
    _assert(privilege_result["agent_processed"], "Agent 收到权限失败诊断", privilege_result["agent_probe"])
    _assert(
        privilege_result["agent_decision"] == "request_privilege_decision",
        "Agent 选择请求权限决定而非擅自提权",
        privilege_result["agent_decision"],
    )
    _assert(privilege_result["permission_fault_applied"], "权限负控只读故障注入已作用于临时项目", privilege_result["permission_fault_applied"])
    _assert(privilege_record.get("outcome") == "not_prepared" and privilege_record.get("reason") == "install_failed", "安装失败保留原因且不伪报成功", privilege_record)
    _assert(privilege_record.get("requires_user_action") == "privilege", "权限失败归类为需要人处理", privilege_record)
    _assert("EACCES" in privilege_record.get("detail", ""), "真实 npm ci 输出保留 EACCES", privilege_record.get("detail"))
    _assert("agent/blocked" in privilege_result["labels"], "权限验证失败转为可恢复 blocked", privilege_result["labels"])
    _assert("Required human action(s): privilege." in privilege_result["comments"], "Issue 评论给出权限阻塞和选择", privilege_result["comments"])

    print("== green: actual npm registry credential failure")
    credentials_result = _green_run(
        repo_root=repo_root,
        scene_path=scene_path,
        scenario="credentials",
        project_setup=lambda repo_path: _credential_npm_project(repo_path, registry_url),
        verification_template="npm ci --prefix tools/declared --ignore-scripts",
        expected_scenario="credentials",
        real_npm=True,
    )
    credentials_record = next(
        record for record in credentials_result["report"].get("projects", [])
        if record.get("project_path") == "tools/declared"
    )
    _assert(credentials_result["agent_started"], "真实安装凭据失败后仍启动 Agent", credentials_result["labels"])
    _assert(credentials_result["agent_processed"], "Agent 收到凭据失败诊断", credentials_result["agent_probe"])
    _assert(
        credentials_result["agent_decision"] == "request_credentials",
        "Agent 选择报告凭据阻塞而非使用未提供的密钥",
        credentials_result["agent_decision"],
    )
    _assert(credentials_record.get("outcome") == "not_prepared" and credentials_record.get("reason") == "install_failed", "凭据失败保留原因且不伪报成功", credentials_record)
    _assert(credentials_record.get("requires_user_action") == "credentials", "凭据错误归类为需要人处理", credentials_record)
    _assert("E401" in credentials_record.get("detail", ""), "真实 npm ci 401 诊断得到保留", credentials_record.get("detail"))
    _assert("agent/blocked" in credentials_result["labels"], "凭据验证失败转为可恢复 blocked", credentials_result["labels"])
    _assert("Required human action(s): credentials." in credentials_result["comments"], "Issue 评论说明所需凭据选择", credentials_result["comments"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    parsed_args = parser.parse_args()
    repo_root = parsed_args.repo_root.resolve()
    scene_path = Path(tempfile.mkdtemp(prefix="rv290-real-entry-"))
    registry_server, registry_thread, registry_url = _start_unauthorized_registry()
    run_completed = False
    try:
        _assert(PRD_SKILL_PATH.is_file(), "真实 PRD skill 可供运行器预检", str(PRD_SKILL_PATH))
        _baseline_negative_control(repo_root, scene_path)
        _upfront_toolchain_negative_control(repo_root, scene_path)
        _reuse_order_negative_control(repo_root, scene_path)
        _workspace_glob_negative_control(repo_root, scene_path)
        _workspace_exclusion_negative_control(repo_root, scene_path)
        _dangling_node_modules_negative_control(repo_root, scene_path)
        _manifestless_workspace_root_negative_control(repo_root, scene_path)
        _run_positive_cases(repo_root, scene_path, registry_url)
        print("RESULT: ALL PASS (7 real kc run negative controls + 7 isolated green scenarios)")
        run_completed = True
        return 0
    except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as run_error:
        print(f"RESULT: FAILED ({type(run_error).__name__}: {run_error})")
        print(f"FAILED_SCENE_PRESERVED: {scene_path}")
        return 1
    finally:
        registry_server.shutdown()
        registry_server.server_close()
        registry_thread.join(timeout=2)
        if run_completed and os.environ.get("RV_KEEP_SCENES") != "1":
            shutil.rmtree(scene_path, ignore_errors=True)
        elif run_completed:
            print(f"SUCCESS_SCENE_PRESERVED: {scene_path}")


if __name__ == "__main__":
    sys.exit(main())

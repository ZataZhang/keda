#!/usr/bin/env python3
"""通过真实 kc 与 gh 子进程执行 Issue #457 的隔离恢复场景。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rv_harness import (
    EVIDENCE_DIR,
    ISSUE_NUMBER,
    OWNER_REPOSITORY,
    REPOSITORY_ROOT,
    RecoveryScene,
    _build_scene,
    _append_json_line,
    _git,
    _load_scene_state,
    _save_scene_state,
    _set_control,
)


def _run_kc(
    scene: RecoveryScene,
    command_arguments: list[str],
    *,
    timeout_seconds: int = 120,
) -> subprocess.CompletedProcess[str]:
    """通过真实 kc CLI 执行一条命令并保存终端转录。"""
    full_command = [scene.kc_command, *command_arguments]
    try:
        command_result = subprocess.run(
            full_command,
            cwd=scene.repository_path,
            env=scene.environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as timeout_error:
        stdout_output = timeout_error.stdout or ""
        stderr_output = timeout_error.stderr or ""
        if isinstance(stdout_output, bytes):
            stdout_output = stdout_output.decode("utf-8", errors="replace")
        if isinstance(stderr_output, bytes):
            stderr_output = stderr_output.decode("utf-8", errors="replace")
        raise TimeoutError(
            f"Real kc command timed out after {timeout_seconds}s in {scene.root}; "
            f"stdout tail: {stdout_output[-2000:]}; stderr tail: {stderr_output[-2000:]}"
        ) from timeout_error
    _append_json_line(
        scene.root / "kc-command-results.jsonl",
        {
            "argv": command_arguments,
            "exit_code": command_result.returncode,
            "stdout_tail": command_result.stdout[-1000:],
            "stderr_tail": command_result.stderr[-1000:],
            "at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    return command_result


def _line_count(path: Path) -> int:
    """返回 JSONL 探针文件中的实际记录数。"""
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _wait_until(predicate: Any, *, description: str, timeout_seconds: int = 90) -> None:
    """轮询已知状态，超时明确失败。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {description}.")


def _start_fresh_daemon(scene: RecoveryScene) -> subprocess.Popen[str]:
    """启动真实 daemon 子进程处理隔离仓库中的待恢复 Issue。"""
    return subprocess.Popen(
        [
            scene.kc_command,
            "daemon",
            "run",
            "--repo",
            str(scene.repository_path),
            "--interval",
            "1",
            "--no-autopilot",
            "--agent",
            "codex",
        ],
        cwd=scene.repository_path,
        env=scene.environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _stop_fresh_daemon(daemon_process: subprocess.Popen[str]) -> None:
    """结束真实 daemon 并收尽终端输出。"""
    os.killpg(daemon_process.pid, signal.SIGTERM)
    try:
        daemon_process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(daemon_process.pid, signal.SIGKILL)
        daemon_process.communicate(timeout=10)


def _fresh_database_probe(scene: RecoveryScene) -> dict[str, Any]:
    """在新 Python 进程与新 SQLite connection 中读取恢复事实。"""
    probe_source = """import json, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.row_factory = sqlite3.Row
table_names = {row[0] for row in connection.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}
table_rows = {}
for table_name in (\"runner_recovery_runs\", \"runner_recovery_stages\", \"runner_external_operations\"):
    if table_name in table_names:
        table_rows[table_name] = [dict(row) for row in connection.execute(f\"SELECT * FROM {table_name} ORDER BY 1\")]
print(json.dumps(table_rows, sort_keys=True))
connection.close()
"""
    probe_result = subprocess.run(
        [sys.executable, "-c", probe_source, str(scene.database_path)],
        cwd=scene.repository_path,
        env=scene.environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    parsed_probe = json.loads(probe_result.stdout)
    if not isinstance(parsed_probe, dict):
        raise RuntimeError("Fresh SQLite probe returned malformed output.")
    return parsed_probe


def _fresh_git_probe(scene: RecoveryScene) -> dict[str, str]:
    """在新 git 子进程中读取 worktree tree 与 bare remote ref。"""
    issue_worktree_path = scene.repository_path / ".kc-worktrees" / f"issue-{ISSUE_NUMBER}"
    tree_sha = _git(issue_worktree_path, "rev-parse", "HEAD^{tree}")
    remote_ref = subprocess.run(
        ["git", "--git-dir", str(scene.bare_remote_path), "rev-parse", f"refs/heads/issue-{ISSUE_NUMBER}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    return {"worktree_tree": tree_sha, "remote_issue_branch": remote_ref}


def _tree_fingerprint() -> dict[str, str]:
    """绑定 evidence 到当前 HEAD 与未提交 diff 指纹。"""
    head_tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    diff_result = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return {
        "head_tree": head_tree,
        "working_tree_diff_sha256": hashlib.sha256(diff_result.stdout).hexdigest(),
        "runner_harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_server_sha256": hashlib.sha256(Path(__file__).with_name("rv_harness.py").read_bytes()).hexdigest(),
        "fixture_setup_sha256": hashlib.sha256(Path(__file__).with_name("rv_setup.py").read_bytes()).hexdigest(),
    }


def _write_evidence(filename: str, lines: list[str]) -> Path:
    """写入单条 rv-id 命名的本地 raw evidence。"""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    evidence_path = EVIDENCE_DIR / filename
    evidence_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return evidence_path


def _read_forge_requests(scene: RecoveryScene) -> list[dict[str, Any]]:
    """读取服务端真实接收到的请求记录。"""
    if not scene.request_log_path.is_file():
        return []
    return [
        json.loads(line)
        for line in scene.request_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _find_operation_rows(database_probe: dict[str, Any]) -> list[dict[str, Any]]:
    """提取实际持久化的外部操作记录。"""
    operation_rows = database_probe.get("runner_external_operations", [])
    return operation_rows if isinstance(operation_rows, list) else []


def _assert_single_agent_invocation(invocation_count: int) -> None:
    """确认恢复没有重复启动实现 Agent。"""
    if invocation_count != 1:
        raise AssertionError(
            "The implementation Agent must run exactly once across recovery; "
            f"observed {invocation_count} invocations."
        )


def _assert_actionable_report(report_path: Path) -> str:
    """确认 runner 落盘的人工报告存在且包含可执行信息。"""
    if not report_path.is_file():
        raise AssertionError("The real runner did not persist its local recovery report.")
    report_text = report_path.read_text(encoding="utf-8")
    required_report_terms = (
        "失败步骤",
        "已完成成果",
        "未知或未完成的对外写入",
        "已尝试处理",
        "下一步",
        "恢复入口",
        "通知状态：待投递",
    )
    missing_terms = [term for term in required_report_terms if term not in report_text]
    if missing_terms:
        raise AssertionError(f"The saved report omitted actionable sections: {missing_terms}")
    return report_text


def _draft_create_summaries(operation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取 PR 创建操作的稳定键、载荷摘要和对账状态。"""
    summary_fields = (
        "operation_key",
        "payload_digest",
        "payload_revision",
        "state",
        "remote_reference",
        "last_error",
    )
    return [
        {field_name: operation_row.get(field_name) for field_name in summary_fields}
        for operation_row in operation_rows
        if operation_row.get("operation_kind") == "draft_pull_request_create"
    ]


def _stage_resume_scene(*, negative_control: bool = False) -> None:
    """真实 run 挂起后由新 daemon 继续发布，并验证成果变化会重跑验证。"""
    scene = _build_scene("stage-resume")
    try:
        _set_control(scene, drop_pull_read_after_agent=True)
        first_run = _run_kc(
            scene,
            ["run", "--issue", str(ISSUE_NUMBER), "--repo", str(scene.repository_path), "--agent", "codex"],
        )
        _assert_single_agent_invocation(_line_count(scene.agent_log_path))
        first_verification_count = _line_count(scene.verification_log_path)
        if first_verification_count < 1:
            raise AssertionError(
                "The first kc run must execute the real verification subprocess: "
                + json.dumps(
                    {
                        "exit_code": first_run.returncode,
                        "stdout_tail": first_run.stdout[-2200:],
                        "stderr_tail": first_run.stderr[-1200:],
                        "agent_invocations": _line_count(scene.agent_log_path),
                        "verification_invocations": first_verification_count,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        first_probe = _fresh_database_probe(scene)
        recovery_rows = first_probe.get("runner_recovery_runs", [])
        if not recovery_rows or recovery_rows[-1].get("recovery_state") != "waiting":
            raise AssertionError(
                "The publish read outage did not suspend kc run with a persisted waiting record: "
                + json.dumps(
                    {
                        "exit_code": first_run.returncode,
                        "stdout_tail": first_run.stdout[-1200:],
                        "stderr_tail": first_run.stderr[-1200:],
                        "recovery_rows": recovery_rows,
                        "requests": _read_forge_requests(scene)[-30:],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        if "recovery state=waiting" not in first_run.stdout:
            raise AssertionError("A zero exit code must still be presented as deferred, not successful.")
        issue_labels = {
            label_payload.get("name")
            for label_payload in _load_scene_state(scene)["issue"].get("labels", [])
            if isinstance(label_payload, dict)
        }
        if "agent/failed" in issue_labels:
            raise AssertionError("A publish network outage must not mark the issue as failed.")
        resume_at_text = recovery_rows[-1].get("resume_at")
        if not isinstance(resume_at_text, str):
            raise AssertionError("Waiting recovery did not persist resume_at.")
        persisted_resume_at = datetime.fromisoformat(resume_at_text.replace("Z", "+00:00"))
        accelerated_resume_at = datetime.now(timezone.utc) + timedelta(seconds=0.5)
        with sqlite3.connect(scene.database_path) as database_connection:
            database_connection.execute(
                "UPDATE runner_recovery_runs SET resume_at = ? WHERE issue_number = ?",
                (accelerated_resume_at.isoformat(), ISSUE_NUMBER),
            )
            database_connection.commit()
        wait_seconds = max(0.0, (accelerated_resume_at - datetime.now(timezone.utc)).total_seconds())
        time.sleep(wait_seconds + 0.25)
        _set_control(scene, drop_pull_read_after_agent=False)

        daemon = _start_fresh_daemon(scene)
        try:
            _wait_until(
                lambda: bool(_load_scene_state(scene).get("pull_requests"))
                and _line_count(scene.agent_log_path) == 1,
                description="the restarted daemon to publish the existing implementation",
                timeout_seconds=120,
            )
        finally:
            _stop_fresh_daemon(daemon)

        if _line_count(scene.verification_log_path) != first_verification_count:
            raise AssertionError("An unchanged tree and validation contract must reuse validation.")

        issue_worktree_path = scene.repository_path / ".kc-worktrees" / f"issue-{ISSUE_NUMBER}"
        repository_config_path = scene.repository_path / ".kedacode.toml"
        repository_config = repository_config_path.read_text(encoding="utf-8")
        changed_command = f"{sys.executable} {scene.root / 'rv_verify.py'} --contract-changed"
        repository_config = repository_config.replace(
            f"{sys.executable} {scene.root / 'rv_verify.py'}",
            changed_command,
        )
        repository_config_path.write_text(repository_config, encoding="utf-8")
        state_payload = _load_scene_state(scene)
        issue_payload = state_payload["issue"]
        issue_payload["labels"] = [{"name": "agent/running"}, {"name": "agent/codex"}]
        _save_scene_state(scene, state_payload)
        invalidated_run = _run_kc(
            scene,
            ["run", "--issue", str(ISSUE_NUMBER), "--repo", str(scene.repository_path), "--agent", "codex"],
        )
        if invalidated_run.returncode != 0:
            raise AssertionError(
                f"Validation-contract invalidation run failed: {invalidated_run.stderr[-1200:]}"
            )
        final_verification_count = _line_count(scene.verification_log_path)
        if final_verification_count <= first_verification_count:
            raise AssertionError("Changing the validation contract must execute verification again.")
        _assert_single_agent_invocation(_line_count(scene.agent_log_path))

        database_probe = _fresh_database_probe(scene)
        final_recovery_rows = database_probe.get("runner_recovery_runs", [])
        git_probe = _fresh_git_probe(scene)
        forge_state = _load_scene_state(scene)
        pull_rows = forge_state.get("pull_requests", [])
        socket_drop_rows = [
            request_row
            for request_row in _read_forge_requests(scene)
            if request_row.get("outcome") == "pull-read-socket-disconnected"
        ]
        if len(pull_rows) != 1:
            raise AssertionError(f"Expected one published PR, found {len(pull_rows)}.")
        evidence_tree = _tree_fingerprint()
        _write_evidence(
            "rv-1-stage-resume.txt",
            [
                "Scene: stage-resume",
                "Entry: real kc run --issue 73, followed by a fresh kc daemon run process.",
                "Fault: HTTPS forge dropped PR-list sockets after the real Agent process exited.",
                f"First kc run exit: {first_run.returncode}",
                f"Originally persisted resume_at: {persisted_resume_at.isoformat()}",
                f"Test-boundary accelerated resume_at: {accelerated_resume_at.isoformat()}",
                "Persisted recovery attempts after resume: "
                + str(final_recovery_rows[-1].get("recovery_attempts") if final_recovery_rows else None),
                f"Agent invocations: {_line_count(scene.agent_log_path)} (expected 1)",
                f"Verification invocations: {final_verification_count} (baseline before resume: {first_verification_count}; unchanged across resume; increased after contract change)",
                f"Remote PR count: {len(pull_rows)} (expected 1)",
                f"Fresh Git state: {json.dumps(git_probe, sort_keys=True)}",
                f"Fresh SQLite state: {json.dumps(database_probe, sort_keys=True)}",
                f"Forge pull-list reads: {forge_state.get('pull_list_reads')}",
                f"Actual TLS socket drops observed: {len(socket_drop_rows)} ({json.dumps(socket_drop_rows, sort_keys=True)})",
                f"Source identity: {json.dumps(evidence_tree, sort_keys=True)}",
                f"Harness request log: {scene.request_log_path}",
                "PASS: one Agent invocation; unchanged validation reused; changed contract revalidated.",
            ],
        )
        if negative_control:
            simulated_agent_invocations = _line_count(scene.agent_log_path) + 1
            try:
                _assert_single_agent_invocation(simulated_agent_invocations)
            except AssertionError as expected_error:
                _write_evidence(
                    "rv-1-stage-resume-negative-control.txt",
                    [
                        "Scene: stage-resume negative control",
                        "Boundary: test-only agent invocation observation; production source was not changed.",
                        f"Actual Agent invocation log: {_line_count(scene.agent_log_path)} entry.",
                        f"Injected duplicate observation: {simulated_agent_invocations} entries.",
                        f"Expected oracle failure: {expected_error}",
                        "Negative control result: FAILED as expected.",
                        f"Source identity: {json.dumps(evidence_tree, sort_keys=True)}",
                    ],
                )
                raise AssertionError(
                    "Expected negative-control failure observed: duplicate Agent invocation was rejected."
                ) from expected_error
            raise AssertionError("The duplicate-Agent negative control unexpectedly passed.")
    finally:
        scene.close()


def _unknown_write_subscene(
    subscene_name: str,
    *,
    create_action: str,
    query_error_status: int | None = None,
    delay_visible_reads: int = 0,
    drop_comment_response: bool = False,
    drop_reads_after_create: bool = False,
    refuse_in_flight_operation_kind: str | None = None,
    resume_after_unknown: bool = False,
) -> dict[str, Any]:
    """运行一个真实 PR 创建未知结果场景并新进程读取最终远端与账本。"""
    scene = _build_scene(subscene_name, seed_comment_count=100 if drop_comment_response else 0)
    try:
        _set_control(
            scene,
            pull_create_action=create_action,
            pull_query_error_status=query_error_status,
            pull_query_error_after_create=query_error_status is not None,
            pull_visibility_delay_reads=delay_visible_reads,
            drop_next_comment_response=drop_comment_response,
            drop_pull_reads_after_create=drop_reads_after_create,
            refuse_in_flight_operation_kind=refuse_in_flight_operation_kind,
        )
        initial_run = _run_kc(
            scene,
            ["run", "--issue", str(ISSUE_NUMBER), "--repo", str(scene.repository_path), "--agent", "codex"],
        )
        initial_database_probe = _fresh_database_probe(scene)
        initial_operation_rows = _find_operation_rows(initial_database_probe)
        initial_recovery_rows = initial_database_probe.get("runner_recovery_runs", [])
        original_budget_deadline = (
            initial_recovery_rows[-1].get("budget_deadline") if initial_recovery_rows else None
        )
        if resume_after_unknown:
            unknown_create_rows = [
                operation_row
                for operation_row in initial_operation_rows
                if operation_row.get("operation_kind") == "draft_pull_request_create"
            ]
            if (
                not initial_recovery_rows
                or initial_recovery_rows[-1].get("recovery_state") != "waiting"
                or not unknown_create_rows
                or unknown_create_rows[-1].get("state") != "unknown"
            ):
                raise AssertionError(
                    "The lost response must persist waiting recovery and an unknown create before restart: "
                    + json.dumps(
                        {
                            "recovery_rows": initial_recovery_rows,
                            "create_rows": unknown_create_rows,
                            "exit_code": initial_run.returncode,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            resume_at = datetime.now(timezone.utc) + timedelta(seconds=0.5)
            with sqlite3.connect(scene.database_path) as database_connection:
                database_connection.execute(
                    "UPDATE runner_recovery_runs SET resume_at = ? WHERE issue_number = ?",
                    (resume_at.isoformat(), ISSUE_NUMBER),
                )
                database_connection.commit()
            time.sleep(max(0.0, (resume_at - datetime.now(timezone.utc)).total_seconds()) + 0.25)
            _set_control(scene, drop_pull_reads_after_create=False)
            daemon = _start_fresh_daemon(scene)
            try:
                _wait_until(
                    lambda: any(
                        operation_row.get("operation_kind") == "draft_pull_request_create"
                        and operation_row.get("state") == "confirmed"
                        for operation_row in _find_operation_rows(_fresh_database_probe(scene))
                    ),
                    description="a fresh daemon to reconcile and confirm the saved PR create",
                    timeout_seconds=90,
                )
            finally:
                _stop_fresh_daemon(daemon)
            if _line_count(scene.agent_log_path) != 1:
                raise AssertionError("Fresh-process PR reconciliation must not rerun the Agent.")
            final_database_probe = _fresh_database_probe(scene)
            final_recovery_rows = final_database_probe.get("runner_recovery_runs", [])
            if final_recovery_rows and final_recovery_rows[-1].get("budget_deadline") != original_budget_deadline:
                raise AssertionError("Fresh-process PR reconciliation must not reset the recovery budget.")

        forge_state = _load_scene_state(scene)
        pull_rows = forge_state.get("pull_requests", [])
        comment_rows = forge_state.get("issue_comments", [])
        request_rows = _read_forge_requests(scene)
        database_probe = _fresh_database_probe(scene)
        operation_rows = _find_operation_rows(database_probe)
        pr_create_requests = [
            request_row
            for request_row in request_rows
            if request_row.get("method") == "POST"
            and (
                "/pulls" in str(request_row.get("path"))
                or str(request_row.get("path")) == "/graphql#createPullRequest"
            )
        ]
        return {
            "scene": subscene_name,
            "exit_code": initial_run.returncode,
            "stdout_tail": initial_run.stdout[-1600:],
            "stderr_tail": initial_run.stderr[-1600:],
            "pull_count": len(pull_rows) if isinstance(pull_rows, list) else -1,
            "pull_create_request_count": len(pr_create_requests),
            "agent_invocations": _line_count(scene.agent_log_path),
            "verification_invocations": _line_count(scene.verification_log_path),
            "recovery_state_before_resume": (
                initial_recovery_rows[-1].get("recovery_state") if initial_recovery_rows else None
            ),
            "initial_database": initial_database_probe,
            "resumed_in_fresh_daemon": resume_after_unknown,
            "budget_deadline_before_resume": original_budget_deadline,
            "operations": operation_rows,
            "database": database_probe,
            "requests": request_rows,
            "pulls": pull_rows,
            "comment_bodies": [
                str(comment_payload.get("body", ""))
                for comment_payload in comment_rows[-5:]
                if isinstance(comment_payload, dict)
            ],
            "request_log": str(scene.request_log_path),
            "source_identity": _tree_fingerprint(),
        }
    finally:
        scene.close()


def _unknown_write_scene(*, negative_control: bool) -> None:
    """验证创建后丢响应、无权威负向证明、查询故障与重复写负控。"""
    persisted = _unknown_write_subscene(
        "unknown-write-persisted",
        create_action="persist_and_drop",
        delay_visible_reads=1,
        drop_comment_response=True,
        drop_reads_after_create=True,
        resume_after_unknown=True,
    )
    if persisted["exit_code"] != 0 or persisted["pull_count"] != 1:
        raise AssertionError(
            "A persisted PR with a lost response must reconcile to one PR: "
            f"{json.dumps(persisted, ensure_ascii=False, sort_keys=True)}"
        )
    if persisted["pull_create_request_count"] != 1:
        raise AssertionError("A persisted PR response loss must not issue a second create request.")
    if persisted["recovery_state_before_resume"] != "waiting" or not persisted["resumed_in_fresh_daemon"]:
        raise AssertionError("The persisted PR unknown result must survive suspension and fresh-process recovery.")
    if _draft_create_summaries(persisted["operations"])[-1].get("state") != "confirmed":
        raise AssertionError("The fresh process did not confirm the previously unknown PR operation.")
    if persisted["agent_invocations"] != 1:
        raise AssertionError("The successful unknown-write path must run the Agent exactly once.")
    if persisted["verification_invocations"] < 1:
        raise AssertionError("The successful unknown-write path must execute verification.")
    comment_requests = [
        request_row
        for request_row in persisted["requests"]
        if request_row.get("outcome") == "persisted-comment-then-socket-drop"
    ]
    if len(comment_requests) != 1:
        raise AssertionError("The paginated comment reconciliation socket-drop branch was not observed.")

    connection_refused = _unknown_write_subscene(
        "unknown-write-connect-refused",
        create_action="accept",
        refuse_in_flight_operation_kind="draft_pull_request_create",
    )
    if connection_refused["exit_code"] != 0 or connection_refused["pull_count"] != 1:
        raise AssertionError("A proved pre-send connect refusal must safely retry and create one PR.")
    if connection_refused["pull_create_request_count"] != 1:
        raise AssertionError("Only the retry after pre-send refusal may reach the forge API.")
    if _draft_create_summaries(connection_refused["operations"])[-1].get("state") != "confirmed":
        raise AssertionError("The pre-send refusal retry did not persist confirmed operation state.")
    refused_connect_rows = [
        request_row
        for request_row in connection_refused["requests"]
        if request_row.get("outcome") == "origin-connect-refused-before-tls-and-api-request"
    ]
    if len(refused_connect_rows) != 1:
        raise AssertionError("The targeted create connection must be refused before TLS and API request bytes.")

    no_authority = _unknown_write_subscene(
        "unknown-write-no-authority",
        create_action="drop_without_create",
    )
    if no_authority["pull_count"] != 0 or no_authority["pull_create_request_count"] != 1:
        raise AssertionError("No-authority absence must remain unknown without a second PR write.")
    unknown_rows = [
        operation_row
        for operation_row in no_authority["operations"]
        if operation_row.get("operation_kind") == "draft_pull_request_create"
    ]
    if not unknown_rows or unknown_rows[-1].get("state") != "unknown":
        raise AssertionError("A server-side absence without authoritative proof must persist unknown.")

    unqueryable = _unknown_write_subscene(
        "unknown-write-unqueryable",
        create_action="drop_without_create",
        query_error_status=403,
    )
    if unqueryable["pull_count"] != 0 or unqueryable["pull_create_request_count"] != 1:
        raise AssertionError(
            "A failed reconciliation query must not resend the PR create request: "
            + json.dumps(
                {
                    key: unqueryable.get(key)
                    for key in (
                        "exit_code",
                        "pull_count",
                        "pull_create_request_count",
                        "stderr_tail",
                        "requests",
                        "operations",
                    )
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    if not any("http-403" == row.get("outcome") for row in unqueryable["requests"]):
        raise AssertionError("The permanent reconciliation query failure was not observed.")

    output_lines = [
        "Scene: unknown-write",
        "Entry: real kc run --issue 73, real gh CLI subprocesses, HTTPS forge and SQLite.",
        "Positive: persisted create + dropped response + delayed visibility ended with exactly one PR.",
        f"Positive PR count/create requests: {persisted['pull_count']}/{persisted['pull_create_request_count']}",
        f"Positive op before/after fresh daemon: waiting+unknown -> {_draft_create_summaries(persisted['operations'])[-1]}; budget deadline preserved: {persisted['budget_deadline_before_resume']}",
        f"Positive Agent/verification calls: {persisted['agent_invocations']}/{persisted['verification_invocations']}",
        "Pagination: claim comment marker was on page 2 after 100 pre-existing comments.",
        f"Connect refused before TLS and API bytes: {json.dumps(refused_connect_rows, sort_keys=True)}; final operation: {json.dumps(_draft_create_summaries(connection_refused['operations']), sort_keys=True)}; PR count/create requests: {connection_refused['pull_count']}/{connection_refused['pull_create_request_count']}",
        f"No-authority: PRs={no_authority['pull_count']}; creates={no_authority['pull_create_request_count']}; operation={json.dumps(no_authority['operations'], sort_keys=True)}",
        f"Unqueryable: PRs={unqueryable['pull_count']}; creates={unqueryable['pull_create_request_count']}; operation={json.dumps(unqueryable['operations'], sort_keys=True)}",
        f"Source identity: {json.dumps(_tree_fingerprint(), sort_keys=True)}",
        "PASS: unknown PR writes reconciled safely; one PR per successful create path.",
    ]
    negative_control_failed = False
    negative_control_lines: list[str] = []
    if negative_control:
        negative_control_lines, negative_control_failed = _run_duplicate_create_negative_control()
        negative_control_lines.append(
            f"Source identity: {json.dumps(_tree_fingerprint(), sort_keys=True)}"
        )
    _write_evidence("rv-2-unknown-write.txt", output_lines)
    if negative_control:
        _write_evidence("rv-2-unknown-write-negative-control.txt", negative_control_lines)
        if not negative_control_failed:
            raise AssertionError("The unsafe duplicate control unexpectedly passed the unique-object assertion.")
        raise AssertionError("Expected negative-control failure observed: unsafe duplicate created two PRs.")


def _run_duplicate_create_negative_control() -> tuple[list[str], bool]:
    """使用真实 gh 直接绕过账本重复创建，唯一对象断言必须失败。"""
    scene = _build_scene("unknown-write-negative-control")
    try:
        _set_control(scene, pull_create_action="accept")
        first_run = _run_kc(
            scene,
            ["run", "--issue", str(ISSUE_NUMBER), "--repo", str(scene.repository_path), "--agent", "codex"],
        )
        if first_run.returncode != 0:
            raise AssertionError("Negative control could not establish its one-object baseline.")
        gh_command = shutil.which("gh")
        if gh_command is None:
            raise RuntimeError("The real gh CLI is not available in PATH.")
        direct_duplicate = subprocess.run(
            [
                gh_command,
                "api",
                "-X",
                "POST",
                f"repos/{OWNER_REPOSITORY}/pulls",
                "-f",
                "title=Negative control duplicate",
                "-f",
                "head=issue-73",
                "-f",
                "base=main",
                "-f",
                "body=duplicate request bypassed the operation ledger",
            ],
            cwd=scene.repository_path,
            env=scene.environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if direct_duplicate.returncode != 0:
            raise AssertionError(f"Direct duplicate write did not reach the test forge: {direct_duplicate.stderr}")
        pull_rows = _load_scene_state(scene).get("pull_requests", [])
        unique_object_assertion_failed = not isinstance(pull_rows, list) or len(pull_rows) != 1
        return (
            [
                "Negative control unique-object assertion: "
                + ("FAILED as expected" if unique_object_assertion_failed else "UNEXPECTEDLY PASSED"),
                f"Direct gh exit: {direct_duplicate.returncode}",
                f"Remote object count after unsafe direct write: {len(pull_rows) if isinstance(pull_rows, list) else 'malformed'}",
            ],
            unique_object_assertion_failed,
        )
    finally:
        scene.close()


def _report_path(scene: RecoveryScene) -> Path:
    """返回真实 runner 固定报告路径。"""
    return scene.repository_path / ".agent-runner" / "recovery" / f"issue-{ISSUE_NUMBER}.md"


def _human_handoff_scene(*, negative_control: bool = False) -> None:
    """验证预算耗尽报告、通知待投递与恢复后的唯一补投。"""
    scene = _build_scene("human-handoff")
    try:
        _set_control(
            scene,
            pull_create_action="drop_without_create",
            deny_report_comment_once=True,
        )
        initial_run = _run_kc(
            scene,
            ["run", "--issue", str(ISSUE_NUMBER), "--repo", str(scene.repository_path), "--agent", "codex"],
        )
        state_probe = _fresh_database_probe(scene)
        recovery_rows = state_probe.get("runner_recovery_runs", [])
        if not recovery_rows:
            raise AssertionError("The first network failure did not persist its recovery budget.")
        if recovery_rows[-1].get("recovery_state") != "waiting":
            raise AssertionError("The first ambiguous write must persist as waiting, not completed.")
        if "recovery state=waiting" not in initial_run.stdout:
            raise AssertionError("The first ambiguous write must be reported as deferred.")
        if not any(
            operation_row.get("operation_kind") == "draft_pull_request_create"
            and operation_row.get("state") == "unknown"
            for operation_row in _find_operation_rows(state_probe)
        ):
            raise AssertionError("The first ambiguous PR create did not persist an unknown operation.")

        # 测试边界压缩累计预算；不改生产配置、时钟或恢复逻辑。
        expired_budget_deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(scene.database_path) as database_connection:
            database_connection.execute(
                "UPDATE runner_recovery_runs SET budget_deadline = ?, resume_at = ? WHERE issue_number = ?",
                (
                    expired_budget_deadline,
                    expired_budget_deadline,
                    ISSUE_NUMBER,
                ),
            )
            database_connection.commit()

        handoff_run = _run_kc(
            scene,
            ["run", "--issue", str(ISSUE_NUMBER), "--repo", str(scene.repository_path), "--agent", "codex"],
        )
        report_path = _report_path(scene)
        report_text = _assert_actionable_report(report_path)
        if handoff_run.returncode == 0:
            raise AssertionError("An unknown PR result past budget must not report success.")
        handoff_probe = _fresh_database_probe(scene)
        handoff_recovery_rows = handoff_probe.get("runner_recovery_runs", [])
        if not handoff_recovery_rows or handoff_recovery_rows[-1].get("recovery_state") != "needs_human":
            raise AssertionError("The expired recovery budget did not persist a human handoff state.")

        state_payload = _load_scene_state(scene)
        issue_payload = state_payload["issue"]
        issue_payload["labels"] = [{"name": "agent/running"}, {"name": "agent/codex"}]
        _save_scene_state(scene, state_payload)
        daemon = _start_fresh_daemon(scene)
        try:
            _wait_until(
                lambda: len(
                    [
                        comment_payload
                        for comment_payload in _load_scene_state(scene).get("issue_comments", [])
                        if "恢复报告" in str(comment_payload.get("body", ""))
                    ]
                ) == 1,
                description="the daemon to deliver the same saved report exactly once",
                timeout_seconds=60,
            )
        finally:
            _stop_fresh_daemon(daemon)

        delivered_reports = [
            comment_payload
            for comment_payload in _load_scene_state(scene).get("issue_comments", [])
            if "恢复报告" in str(comment_payload.get("body", ""))
        ]
        if len(delivered_reports) != 1:
            raise AssertionError(
                f"The resumed notification must deliver one report; found {len(delivered_reports)}."
            )
        delivered_report_body = str(delivered_reports[0].get("body", ""))
        report_operation_marker = delivered_report_body[len(report_text) :]
        if not delivered_report_body.startswith(report_text) or not re.fullmatch(
            r"\n\n<!-- kc:runner-operation id=[0-9a-f]{64} -->",
            report_operation_marker,
        ):
            raise AssertionError(
                "The resumed notification must deliver the saved report unchanged with only its stable ledger marker: "
                + json.dumps(
                    {
                        "saved_length": len(report_text),
                        "delivered_length": len(delivered_report_body),
                        "saved_tail": report_text[-500:],
                        "delivered_tail": delivered_report_body[-700:],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        database_probe = _fresh_database_probe(scene)
        final_recovery_rows = database_probe.get("runner_recovery_runs", [])
        if not final_recovery_rows or final_recovery_rows[-1].get("budget_deadline") != expired_budget_deadline:
            raise AssertionError("Notification retry must not reset the expired recovery budget.")
        all_requests = _read_forge_requests(scene)
        pull_create_count = sum(
            1
            for request_row in all_requests
            if request_row.get("method") == "POST"
            and request_row.get("path") == "/graphql#createPullRequest"
        )
        if pull_create_count != 1:
            raise AssertionError("Human notification retry must not resend the unknown PR create.")
        notification_rejections = [
            request_row
            for request_row in all_requests
            if request_row.get("outcome") == "http-429"
            and "/issues/73/comments" in str(request_row.get("path"))
        ]
        rejection_index = all_requests.index(notification_rejections[0]) if notification_rejections else -1
        notification_deliveries = [
            request_row
            for request_index, request_row in enumerate(all_requests)
            if request_index > rejection_index
            and request_row.get("method") == "POST"
            and "/issues/73/comments" in str(request_row.get("path"))
            and request_row.get("outcome") == "http-201"
        ]
        if len(notification_rejections) != 1 or len(notification_deliveries) != 1:
            raise AssertionError(
                "The test forge must observe one rejected and one successful report notification: "
                + json.dumps(
                    [
                        (row.get("method"), row.get("path"), row.get("outcome"))
                        for row in all_requests
                        if "/issues/73/comments" in str(row.get("path"))
                    ],
                    ensure_ascii=False,
                )
            )
        git_probe = _fresh_git_probe(scene)
        evidence_tree = _tree_fingerprint()
        _write_evidence(
            "rv-3-human-handoff.txt",
            [
                "Scene: human-handoff",
                "Entry: real kc run --issue 73, fresh kc daemon process and fresh SQLite probe.",
                "Fault: ambiguous PR creation, repeated failed reconciliation, test-boundary expired budget and one 429 report-notification response.",
                f"Initial kc run exit: {initial_run.returncode}; budget handoff run exit: {handoff_run.returncode}",
                f"Local report path: {report_path}",
                "----- saved report begins -----",
                report_text,
                "----- saved report ends -----",
                f"Delivered report count: {len(delivered_reports)}; saved body preserved with marker: {report_operation_marker.strip()}",
                f"Notification HTTP outcomes: rejected={len(notification_rejections)}, delivered={len(notification_deliveries)}",
                f"PR create request count across handoff and notification retry: {pull_create_count}",
                f"Fresh Git state: {json.dumps(git_probe, sort_keys=True)}",
                f"Fresh SQLite state: {json.dumps(database_probe, sort_keys=True)}",
                f"Source identity: {json.dumps(evidence_tree, sort_keys=True)}",
                f"Forge request log: {scene.request_log_path}",
                "PASS: local handoff saved; same report delivered once after notification recovery.",
            ],
        )
        if negative_control:
            negative_report_path = scene.root / "negative-control" / "issue-73.md"
            negative_report_path.parent.mkdir(parents=True, exist_ok=True)
            negative_report_path.write_text(report_text, encoding="utf-8")
            negative_report_path.unlink()
            try:
                _assert_actionable_report(negative_report_path)
            except AssertionError as expected_error:
                _write_evidence(
                    "rv-3-human-handoff-negative-control.txt",
                    [
                        "Scene: human-handoff negative control",
                        "Boundary: deleted a test copy of the actual runner-produced report; production source was not changed.",
                        "The positive run saved and validated the real local report before this test-only deletion.",
                        f"Expected oracle failure: {expected_error}",
                        "Negative control result: FAILED as expected.",
                        f"Source identity: {json.dumps(evidence_tree, sort_keys=True)}",
                    ],
                )
                raise AssertionError(
                    "Expected negative-control failure observed: missing local report was rejected."
                ) from expected_error
            raise AssertionError("The missing-report negative control unexpectedly passed.")
    finally:
        scene.close()


def _parse_arguments() -> argparse.Namespace:
    """解析真实入口验证场景。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scene",
        required=True,
        choices=("stage-resume", "unknown-write", "human-handoff"),
    )
    parser.add_argument("--negative-control", action="store_true")
    arguments = parser.parse_args()
    return arguments


def main() -> int:
    """执行一个完整真实入口场景并返回结果码。"""
    arguments = _parse_arguments()
    if arguments.scene == "stage-resume":
        _stage_resume_scene(negative_control=arguments.negative_control)
        print("PASS: one Agent invocation; unchanged validation reused; changed contract revalidated.")
    elif arguments.scene == "unknown-write":
        _unknown_write_scene(negative_control=arguments.negative_control)
        print("PASS: unknown PR writes reconciled safely; one PR per successful create path.")
    else:
        _human_handoff_scene(negative_control=arguments.negative_control)
        print("PASS: local handoff saved; same report delivered once after notification recovery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

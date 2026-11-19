"""Tests for ``kc evidence check``（本地证据自检入口）。

覆盖 FR-1～FR-5：真实 CLI 入口、退出码、``--json``、一次报全、超时标注入
以及 manifest 格式错误不执行任何证据命令。命令通过真实 ``bash -lc`` 执行，
判定走与交付门禁相同的 core 实现。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from backend.api import cli_typer_evidence
from backend.api.cli_typer_app import app
from backend.core.shared.models.agent_runner import AppConfig
from backend.infrastructure.process_runner import SubprocessRunner


def _item(number: int, command: str, *, assertions: list[dict] | None = None) -> dict:
    """构造一条最小合法 evidence block；断言字段按需附加。"""
    item_block = {
        "item_number": number,
        "item_name": f"checkpoint {number}",
        "command": command,
        "evidence_files": [f"rv-{number}-probe.txt"],
        "output_summary": f"command {number} output captured.",
        "explanation": "The command probes the real entry point.",
        "risks": "Local-only fixture.",
        "negative_control": "Break the probe and re-run.",
        "expected_fail": "The check reports a failed item.",
    }
    if assertions is not None:
        item_block["stdout_assertions"] = assertions
    return item_block


def _write_manifest(evidence_dir: Path, item_blocks: list[dict]) -> None:
    """把 evidence.json 写入目录（不做门禁式文件存在性校验）。"""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "language": "zh-CN", "items": item_blocks}
    (evidence_dir / "evidence.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture(autouse=True)
def _isolated_config_and_real_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离用户配置并注入真实子进程执行器（命令必须真的跑）。"""
    monkeypatch.setattr(
        cli_typer_evidence,
        "resolve_repository_targets",
        lambda settings, *, fallback_path: [SimpleNamespace(config=AppConfig())],
    )
    monkeypatch.setattr(cli_typer_evidence, "get_agent_runner_settings", lambda: object())
    monkeypatch.setattr(cli_typer_evidence, "create_process_runner", lambda: SubprocessRunner())


def _invoke(tmp_path: Path, *arguments: str):
    """以真实 Typer 入口执行 ``kc evidence check``。"""
    # standalone_mode=False 才能拿到命令回调的返回值：真实 kc 入口同样以
    # standalone_mode=False 调用 Typer 并把返回值作为进程退出码。
    return CliRunner().invoke(
        app,
        ["evidence", "check", "--worktree", str(tmp_path), *arguments],
        standalone_mode=False,
    )


def test_check_reports_passing_manifest_as_json(tmp_path: Path) -> None:
    """正确证据包通过自检；--json 可被标准 JSON 解析器读取（rv-5）。"""
    evidence_dir = tmp_path / "tasks" / "evidence" / "demo"
    _write_manifest(
        evidence_dir,
        [
            _item(
                1,
                "printf 'PROBE-1 OK\\n'",
                assertions=[{"pattern": "PROBE-1 OK"}],
            ),
            _item(
                2,
                "printf 'PROBE-2 OK\\n'",
                assertions=[{"pattern": "PROBE-2 OK"}],
            ),
        ],
    )

    invocation = _invoke(tmp_path, "--evidence", str(evidence_dir), "--json")

    assert invocation.return_value == 0, invocation.output
    payload = json.loads(invocation.stdout)
    assert payload["passed"] is True
    assert payload["summary"] == {
        "total": 2,
        "passed": 2,
        "failed": 0,
        "timed_out": 0,
        "skipped": 0,
    }
    assert [item_entry["status"] for item_entry in payload["items"]] == [
        "passed",
        "passed",
    ]
    assert payload["timeout_seconds"] == 300
    assert payload["gate_timeout_seconds"] == 300


def test_check_lists_all_assertion_failures_in_one_run(tmp_path: Path) -> None:
    """三个题目各写错一条断言：一次列出三条，退出码 1（rv-2）。"""
    evidence_dir = tmp_path / "tasks" / "evidence" / "demo"
    item_blocks = [
        _item(
            item_number,
            f"printf 'ACTUAL-{item_number} value\\n'",
            assertions=[{"pattern": f"EXPECTED-{item_number}"}],
        )
        for item_number in (1, 2, 3)
    ]
    _write_manifest(evidence_dir, item_blocks)

    invocation = _invoke(tmp_path, "--evidence", str(evidence_dir), "--json")

    assert invocation.return_value == 1
    payload = json.loads(invocation.stdout)
    failed_items = [
        item_entry for item_entry in payload["items"] if item_entry["status"] == "failed"
    ]
    assert [item_entry["item_number"] for item_entry in failed_items] == [1, 2, 3]
    for item_entry in failed_items:
        assertion_failure = item_entry["assertion_failures"][0]
        assert assertion_failure["pattern"] == f"EXPECTED-{item_entry['item_number']}"
        assert (
            assertion_failure["closest_output_line"] == f"ACTUAL-{item_entry['item_number']} value"
        )


def test_check_marks_local_timeout_and_continues_other_items(tmp_path: Path) -> None:
    """单题超时被标注且其余题目继续给出结论（rv-3）。"""
    evidence_dir = tmp_path / "tasks" / "evidence" / "demo"
    _write_manifest(
        evidence_dir,
        [
            _item(1, "sleep 5"),
            _item(2, "printf 'FAST OK\\n'", assertions=[{"pattern": "FAST OK"}]),
        ],
    )

    invocation = _invoke(tmp_path, "--evidence", str(evidence_dir), "--timeout", "1")

    assert invocation.return_value == 1
    human_output = invocation.output
    assert "TIMEOUT" in human_output
    assert "delivery gate limit is 300s" in human_output
    assert "PASSED" in human_output
    assert "Result: 1/2 passed, 1 timed out" in human_output


def test_check_manifest_error_executes_no_evidence_command(tmp_path: Path) -> None:
    """manifest 缺必填字段时逐条报错，探针无执行痕迹（rv-4）。"""
    evidence_dir = tmp_path / "tasks" / "evidence" / "demo"
    probe_marker = tmp_path / "probe-ran"
    broken_block = _item(1, f"touch {probe_marker}")
    del broken_block["risks"]
    _write_manifest(evidence_dir, [broken_block])

    invocation = _invoke(tmp_path, "--evidence", str(evidence_dir))

    assert invocation.return_value == 1
    assert "risks" in invocation.output
    assert "no evidence command was executed" in invocation.output
    assert not probe_marker.exists()


def test_check_requires_explicit_evidence_when_candidates_are_ambiguous(
    tmp_path: Path,
) -> None:
    """缺省解析遇到多个候选时报用法错误并列出候选，不猜。"""
    for candidate_name in ("alpha", "beta"):
        _write_manifest(
            tmp_path / "tasks" / "evidence" / candidate_name,
            [_item(1, "printf 'OK\\n'")],
        )

    invocation = _invoke(tmp_path)

    assert invocation.return_value == 2
    assert "alpha" in invocation.output
    assert "beta" in invocation.output
    assert "--evidence" in invocation.output


def test_check_resolves_unique_task_directory_by_default(tmp_path: Path) -> None:
    """缺省模式在配置证据根下找到唯一的 evidence.json 目录。"""
    evidence_dir = tmp_path / "tasks" / "evidence" / "unique-task"
    _write_manifest(evidence_dir, [_item(1, "printf 'OK\\n'")])

    invocation = _invoke(tmp_path)

    assert invocation.return_value == 0, invocation.output
    assert "unique-task" in invocation.output


def test_check_rejects_timeout_above_gate_limit(tmp_path: Path) -> None:
    """--timeout 不得大于门禁上限（决策一）；越界是用法错误。"""
    evidence_dir = tmp_path / "tasks" / "evidence" / "demo"
    _write_manifest(evidence_dir, [_item(1, "printf 'OK\\n'")])

    invocation = _invoke(tmp_path, "--evidence", str(evidence_dir), "--timeout", "301")

    assert invocation.return_value == 2
    assert "between 1 and 300" in invocation.output


def test_check_legacy_manifest_without_assertions_only_checks_exit_code(
    tmp_path: Path,
) -> None:
    """旧 manifest（无 stdout_assertions）行为不变：只看退出码。"""
    evidence_dir = tmp_path / "tasks" / "evidence" / "demo"
    _write_manifest(evidence_dir, [_item(1, "printf 'anything\\n'")])

    invocation = _invoke(tmp_path, "--evidence", str(evidence_dir), "--json")

    assert invocation.return_value == 0
    payload = json.loads(invocation.stdout)
    assert payload["items"][0]["status"] == "passed"
    assert payload["items"][0]["assertion_failures"] == []

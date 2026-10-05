"""会话续传全链路测试：自报会话落盘 → 下次领取以 ``--resume`` 形态起跑。

被测边界（真实）：假 agent 子进程按 claude stream-json 信封自报 ``session_id`` →
``SubprocessRunner`` 观测 → ``CommandResult.session_id`` → worktree 局部会话记录 →
领取侧 :func:`backend.core.use_cases.run_agent_once.run_agent_until_committed` 解析
首轮续传 → :func:`build_agent_invocation` 展开 ``resume_args`` → 第二次真实子进程
argv。被替代的只有 agent CLI 本身；执行循环的门禁按
``test_agent_token_usage_flow.py`` 的既有模式替身为 no-op。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    RunnerConfig,
)
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_RUN,
    AgentProfileSpec,
    AgentSpec,
    BUILTIN_AGENT_SPECS,
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
)
from backend.core.use_cases import run_agent_execution_loop as execution_loop_module
from backend.core.use_cases import run_agent_once as run_agent_once_module
from backend.core.use_cases.agent_runner_session_store import load_agent_session_record
from backend.infrastructure.process_runner import SubprocessRunner

_ISSUE = IssueSummary(
    number=7,
    title="resume flow",
    url="https://example.invalid/7",
    body="Body",
    labels=(),
)


def _write_fake_agent(worktree: Path, audit_path: Path) -> Path:
    """生成"把 argv 记进审计文件、按 claude 信封自报会话 id"的假 agent。"""
    script = worktree / "fake_resume_agent.py"
    script_body = (
        "import json, sys\n"
        f"argv_audit = {str(audit_path)!r}\n"
        "resumed = '--resume' in sys.argv\n"
        "resumed_session_id = sys.argv[sys.argv.index('--resume') + 1] if resumed else None\n"
        "session_id = resumed_session_id or 'sess-fresh'\n"
        "with open(argv_audit, 'a', encoding='utf-8') as audit_file:\n"
        "    audit_file.write(json.dumps("
        "{'argv': sys.argv, 'resumed': resumed, "
        "'resumed_session_id': resumed_session_id}) + '\\n')\n"
        "sys.stdout.write(json.dumps("
        "{'type': 'system', 'subtype': 'init', 'session_id': session_id}) + '\\n')\n"
        "sys.stdout.write(json.dumps("
        "{'type': 'result', 'result': 'done', 'session_id': session_id}) + '\\n')\n"
    )
    script.write_text(script_body, encoding="utf-8")
    return script


def _claude_like_spec(script_path: Path, *, supports_resume: bool) -> AgentSpec:
    """按内置 claude 的声明形状造假 agent spec（只把 bin 指向假脚本）。"""
    base_spec = BUILTIN_AGENT_SPECS["claude"]
    return AgentSpec(
        bin="uv",
        label=base_spec.label,
        label_color=base_spec.label_color,
        label_description=base_spec.label_description,
        supports_resume=supports_resume,
        resume_args=base_spec.resume_args,
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=("run", "python", str(script_path)),
                prompt_delivery="argv_tail",
                output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
            )
        },
    )


def _patch_loop_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """把与续传断言无关的门禁替身为 no-op（沿用 token 用量全链路测试的模式）。"""
    monkeypatch.setattr(
        execution_loop_module, "run_verification", lambda worktree, config, runner: []
    )
    monkeypatch.setattr(execution_loop_module, "has_changes", lambda worktree, runner: False)
    monkeypatch.setattr(execution_loop_module, "ensure_prd_delivery_ready", lambda *a, **k: None)
    monkeypatch.setattr(
        execution_loop_module, "ensure_validation_evidence_ready", lambda *a, **k: None
    )
    monkeypatch.setattr(
        execution_loop_module, "ensure_no_misplaced_evidence_helpers", lambda *a, **k: None
    )
    monkeypatch.setattr(
        execution_loop_module, "ensure_validation_commands_pass", lambda *a, **k: None
    )
    monkeypatch.setattr(execution_loop_module, "warn_legacy_evidence_helpers", lambda *a, **k: None)
    monkeypatch.setattr(execution_loop_module, "commit_requested_changes", lambda *a, **k: [])
    monkeypatch.setattr(
        "backend.core.use_cases.run_verifier_agent.run_verifier_gate", lambda *a, **k: None
    )


def _prepare_worktree(tmp_path: Path) -> Path:
    """造一个真 git worktree：执行循环会真实读取 ``git rev-parse HEAD``。"""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True, capture_output=True)
    (worktree / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=worktree, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    return worktree


def _run_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    supports_resume: bool,
) -> list[dict]:
    """连跑两次"领取 → 执行"，返回假 agent 记下的两次 argv 审计。"""
    _patch_loop_gates(monkeypatch)
    worktree = _prepare_worktree(tmp_path)
    audit_path = tmp_path / "argv_audit.jsonl"
    script_path = _write_fake_agent(worktree, audit_path)
    config = AppConfig(
        runner=RunnerConfig(max_recovery_attempts=0),
        agents={
            **BUILTIN_AGENT_SPECS,
            "claude": _claude_like_spec(script_path, supports_resume=supports_resume),
        },
    )

    for _ in range(2):
        run_agent_once_module.run_agent_until_committed(
            selected_agent="claude",
            issue=_ISSUE,
            worktree_path=worktree,
            config=config,
            process_runner=SubprocessRunner(),
            before_sha="0" * 40,
            expected_branch="issue-7",
        )

    return [
        json.loads(audit_line) for audit_line in audit_path.read_text(encoding="utf-8").splitlines()
    ]


class TestResumeAcrossClaims:
    """崩溃对账判定「可续传」后，下一次领取真的将原会话续上。"""

    def test_second_claim_resumes_recorded_session(self, tmp_path: Path, monkeypatch) -> None:
        """首轮全新会话 → 记录落盘；次轮领取的 argv 带上 ``--resume <首轮 id>``。"""
        audited_calls = _run_claims(tmp_path, monkeypatch, supports_resume=True)

        assert len(audited_calls) == 2
        first_call, second_call = audited_calls
        assert first_call["resumed"] is False

        record = load_agent_session_record(tmp_path / "wt", "claude")
        assert record is not None
        assert record.session_id == "sess-fresh"
        assert record.issue_number == _ISSUE.number

        assert second_call["resumed"] is True
        assert second_call["resumed_session_id"] == "sess-fresh"

    def test_agent_without_capability_never_gets_resume_args(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """负控：未声明续传能力的 agent 两轮都是全新会话，argv 不出现 ``--resume``。"""
        audited_calls = _run_claims(tmp_path, monkeypatch, supports_resume=False)

        assert len(audited_calls) == 2
        assert [call["resumed"] for call in audited_calls] == [False, False]


class TestAttemptResumeSelection:
    """执行循环内"这一轮该续传哪个会话"的选择规则。"""

    def _request(self, worktree: Path, *, resume_session_id: str | None = None):
        """构造只填必要字段的最小执行请求。"""
        return execution_loop_module.AgentExecutionRequest(
            selected_agent="claude",
            issue=_ISSUE,
            worktree_path=worktree,
            config=AppConfig(),
            process_runner=SubprocessRunner(),
            before_sha="0" * 40,
            expected_branch="issue-7",
            resume_session_id=resume_session_id,
        )

    def test_first_attempt_uses_injected_id_only(self, tmp_path: Path) -> None:
        """首轮只认领取侧注入的 id：有记录但没注入时仍按全新会话起跑。"""
        from backend.core.use_cases.agent_runner_session_store import (
            save_agent_session_record,
        )

        save_agent_session_record(
            tmp_path, agent_name="claude", session_id="sess-record", issue_number=7
        )

        request = self._request(tmp_path)
        assert (
            execution_loop_module._attempt_resume_session_id(  # noqa: SLF001
                request, attempt_index=0
            )
            is None
        )
        injected = self._request(tmp_path, resume_session_id="sess-injected")
        assert (
            execution_loop_module._attempt_resume_session_id(  # noqa: SLF001
                injected, attempt_index=0
            )
            == "sess-injected"
        )

    def test_recovery_round_reads_back_latest_record(self, tmp_path: Path) -> None:
        """recovery 轮次续的是"上一条自报的会话"，而不是领取时那个。"""
        from backend.core.use_cases.agent_runner_session_store import (
            save_agent_session_record,
        )

        save_agent_session_record(
            tmp_path, agent_name="claude", session_id="sess-latest", issue_number=7
        )
        request = self._request(tmp_path, resume_session_id="sess-original")

        assert (
            execution_loop_module._attempt_resume_session_id(  # noqa: SLF001
                request, attempt_index=1
            )
            == "sess-latest"
        )

    def test_missing_record_degrades_to_fresh_session(self, tmp_path: Path) -> None:
        """记录缺失（首轮就崩、或记录被清理）→ 全新会话，不报错。"""
        request = self._request(tmp_path)

        assert (
            execution_loop_module._attempt_resume_session_id(  # noqa: SLF001
                request, attempt_index=2
            )
            is None
        )

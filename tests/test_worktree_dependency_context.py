"""依赖准备结构化上下文的测试（Issue #290）。

覆盖四组事实：

- 落盘/读取往返保真，且旧 schema、非法 JSON、缺文件都静默降级成「无记录」而不是抛错。
- ``detail`` 脱敏：安装器 stderr 里的凭据形状不会进报告、prompt 或 Issue 评论。
- prompt 段落渲染：缺依赖的项目把证据、尝试过的命令与「不代做」规则交给 Agent。
- 失败归属：只有能对上记录里缺失工具的执行失败才改判成可恢复阻塞，其余原样返回，
  ``agent/failed`` 语义不被稀释。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    CommandResult,
    IssueSummary,
    PromptConfig,
)
from backend.core.use_cases.agent_runner_failure import DependencyEnvironmentBlockedError
from backend.core.use_cases.agent_runner_feedback import (
    VerificationFailedError,
    build_prompt,
)
from backend.core.use_cases.worktree_dependency_context import (
    DEPENDENCY_PREPARATION_RELATIVE_PATH,
    MAX_DETAIL_CHARS,
    OUTCOME_INSTALLED,
    OUTCOME_NOT_PREPARED,
    OUTCOME_REUSED,
    OUTCOME_SKIPPED,
    REASON_INSTALL_FAILED,
    REASON_MANAGER_CONFLICT,
    REASON_TOOL_MISSING,
    REASON_WORKSPACE_COVERED,
    USER_ACTION_HOST_TOOLCHAIN,
    USER_ACTION_CREDENTIALS,
    USER_ACTION_PRIVILEGE,
    DependencyPreparationReport,
    FrontendProjectRecord,
    build_environment_block_message,
    escalate_dependency_environment_failure,
    format_command_text,
    load_dependency_preparation_records,
    render_dependency_context_section,
    sanitize_detail_text,
    write_dependency_preparation_report,
)

_RECORDS = (
    FrontendProjectRecord(
        project_path="frontend",
        outcome=OUTCOME_NOT_PREPARED,
        reason=REASON_TOOL_MISSING,
        package_manager="pnpm",
        selection_evidence=("lockfile(s): pnpm-lock.yaml",),
        attempted_commands=("pnpm install --ignore-scripts",),
        exit_code=None,
        requires_user_action=USER_ACTION_HOST_TOOLCHAIN,
        detail="pnpm is not on PATH",
    ),
    FrontendProjectRecord(
        project_path="frontend-admin",
        outcome=OUTCOME_INSTALLED,
        reason="",
        package_manager="npm",
        selection_evidence=("manifest: parsed",),
        attempted_commands=("npm ci --ignore-scripts",),
        exit_code=0,
        requires_user_action=None,
        detail="",
    ),
)


def _write_report(worktree_path: Path, records: tuple[FrontendProjectRecord, ...]) -> Path:
    """按真实落盘路径写入记录，返回报告文件路径。"""
    report = DependencyPreparationReport(worktree_path=str(worktree_path), records=records)
    assert write_dependency_preparation_report(worktree_path, report) is True
    return worktree_path / DEPENDENCY_PREPARATION_RELATIVE_PATH


class TestWriteAndLoadRoundTrip:
    """落盘 schema 与读取端容错。"""

    def test_round_trip_preserves_every_field(self, tmp_path: Path) -> None:
        """写进去的记录读回来逐字段一致（元组仍是元组、None 不被编成字符串）。"""
        record_path = _write_report(tmp_path, _RECORDS)

        reloaded_records = load_dependency_preparation_records(tmp_path)

        assert reloaded_records == _RECORDS
        persisted_object = json.loads(record_path.read_text(encoding="utf-8"))
        assert persisted_object["version"] == 1
        assert persisted_object["projects"][0]["selection_evidence"] == [
            "lockfile(s): pnpm-lock.yaml"
        ]

    def test_missing_file_yields_no_records(self, tmp_path: Path) -> None:
        """没准备过的 worktree 读到空元组：prompt 段落因此与改动前逐字一致。"""
        assert load_dependency_preparation_records(tmp_path) == ()

    @pytest.mark.parametrize(
        "payload_text",
        [
            "not json at all",
            '{"version": 99, "projects": []}',
            '{"version": 1, "projects": "not a list"}',
            "[1, 2, 3]",
            "{}",
        ],
        ids=["invalid-json", "schema-mismatch", "projects-not-list", "array-root", "empty-object"],
    )
    def test_unreadable_payload_degrades_to_no_records(
        self, tmp_path: Path, payload_text: str
    ) -> None:
        """非法或版本不符的报告只当作「无记录」，绝不抛错打断交付。"""
        record_path = tmp_path / DEPENDENCY_PREPARATION_RELATIVE_PATH
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(payload_text, encoding="utf-8")

        assert load_dependency_preparation_records(tmp_path) == ()

    def test_entries_without_project_path_are_skipped(self, tmp_path: Path) -> None:
        """条目形状不完整时逐条跳过，其余记录仍可用。"""
        record_path = _write_report(tmp_path, _RECORDS)
        persisted_object = json.loads(record_path.read_text(encoding="utf-8"))
        persisted_object["projects"].append({"outcome": OUTCOME_INSTALLED})
        record_path.write_text(json.dumps(persisted_object), encoding="utf-8")

        assert len(load_dependency_preparation_records(tmp_path)) == 2

    def test_write_failure_is_swallowed(self, tmp_path: Path) -> None:
        """落盘失败（目标不可写）只返回 False，不升级成 Issue 的致命错误。"""
        blocked_parent = tmp_path / "locked"
        blocked_parent.write_text("not a directory", encoding="utf-8")

        report = DependencyPreparationReport(worktree_path=str(tmp_path), records=_RECORDS)

        assert write_dependency_preparation_report(blocked_parent, report) is False


class TestSanitizeDetailText:
    """诊断片段的凭据脱敏与截断。"""

    @pytest.mark.parametrize(
        ("raw_text", "leaked_fragment"),
        [
            ("ERR_PNPM 401 //registry.npmjs.org/:_authToken=abc123secret", "abc123secret"),
            ("npm error code E401 _authToken=abc123secret", "abc123secret"),
            ("Authorization: Bearer sk-abcdefghijklmnop", "sk-abcdefghijklmnop"),
            ("failed with https://user:hunter2@registry.internal/x", "hunter2"),
            ("npm_config__auth = supersecretvalue", "supersecretvalue"),
        ],
        ids=["pnpm-registry", "auth-token", "bearer", "url-credentials", "npm-config-auth"],
    )
    def test_credential_shapes_are_redacted(self, raw_text: str, leaked_fragment: str) -> None:
        """常见凭据形状被替换掉，原文不会进落盘报告或 Issue。"""
        sanitized_text = sanitize_detail_text(raw_text)

        assert leaked_fragment not in sanitized_text
        assert "[redacted]" in sanitized_text

    def test_whitespace_collapsed_and_long_text_truncated(self) -> None:
        """多行 stderr 压成一行片段，超长部分截断并留标记。"""
        long_text = "line\n  " + "x" * (MAX_DETAIL_CHARS + 200)

        sanitized_text = sanitize_detail_text(long_text)

        assert "\n" not in sanitized_text
        assert sanitized_text.endswith("…[truncated]")
        assert len(sanitized_text) <= MAX_DETAIL_CHARS + len("…[truncated]")

    def test_empty_input_returns_empty(self) -> None:
        """空输入保持空串（渲染时据此跳过该字段）。"""
        assert sanitize_detail_text("") == ""

    def test_format_command_text_is_shell_readable(self) -> None:
        """argv 渲染成可复制执行的命令文本。"""
        assert format_command_text(["pnpm", "install", "--ignore-scripts"]) == (
            "pnpm install --ignore-scripts"
        )
        assert format_command_text(["sh", "-c", "npm ci && exit 1"]) == ("sh -c 'npm ci && exit 1'")


class TestRenderDependencyContextSection:
    """给 Agent 的 prompt 段落。"""

    def test_empty_when_no_report(self, tmp_path: Path) -> None:
        """无记录时渲染空串，不凭空加段落。"""
        assert render_dependency_context_section(tmp_path) == ""

    def test_renders_diagnostics_and_rules(self, tmp_path: Path) -> None:
        """缺工具的项目带出计划命令、证据与人类决定标记，并附处理规则。"""
        _write_report(tmp_path, _RECORDS)

        section_text = render_dependency_context_section(tmp_path)

        assert "## Worktree dependency preparation context (runner diagnostics)" in section_text
        assert "`frontend` - NOT ready" in section_text
        assert "lockfile(s): pnpm-lock.yaml" in section_text
        assert "planned/attempted commands: pnpm install --ignore-scripts" in section_text
        assert "needs a human decision (host_toolchain)" in section_text
        assert "`frontend-admin` - ready" in section_text
        assert "  - exit code: 0" in section_text
        assert "npm ci --ignore-scripts" in section_text
        assert "Never run ``sudo``" in section_text
        assert "do not mark the task failed for it" in section_text

    def test_workspace_member_skip_is_visible(self, tmp_path: Path) -> None:
        """被根 workspace 覆盖的成员也要出现在段落里（Agent 需知道去哪装）。"""
        covered_record = FrontendProjectRecord(
            project_path="apps/web",
            outcome=OUTCOME_SKIPPED,
            reason=REASON_WORKSPACE_COVERED,
            package_manager="pnpm",
            selection_evidence=("workspace member covered by root '.' pattern 'apps/*' install",),
        )
        _write_report(tmp_path, (covered_record,))

        section_text = render_dependency_context_section(tmp_path)

        assert "`apps/web` - NOT ready" in section_text
        assert "covered by root '.' pattern 'apps/*'" in section_text


class TestBuildPromptInjection:
    """真实入口：build_prompt 把上下文段落注入默认模板。"""

    def test_prompt_carries_dependency_context(self, tmp_path: Path) -> None:
        """落盘报告存在时，Agent 收到的 prompt 里就有这段诊断。"""
        _write_report(tmp_path, _RECORDS)
        issue = IssueSummary(
            number=290,
            title="dependency preparation",
            url="https://github.com/example/repo/issues/290",
            body="Test body",
            labels=(),
        )

        prompt_text = build_prompt(issue, tmp_path, PromptConfig())

        assert "## Worktree dependency preparation context (runner diagnostics)" in prompt_text
        assert "`frontend` - NOT ready" in prompt_text

    def test_prompt_unchanged_without_report(self, tmp_path: Path) -> None:
        """没准备过依赖时，prompt 不含该段落（对现有行为零影响）。"""
        issue = IssueSummary(
            number=290,
            title="dependency preparation",
            url="https://github.com/example/repo/issues/290",
            body="Test body",
            labels=(),
        )

        prompt_text = build_prompt(issue, tmp_path, PromptConfig())

        assert "dependency preparation context" not in prompt_text


class TestEscalateDependencyEnvironmentFailure:
    """失败归属：环境缺口 → 可恢复阻塞，其余保持 agent/failed。"""

    def test_no_report_returns_original_error(self, tmp_path: Path) -> None:
        """没准备过依赖的 worktree 不做归属，原始失败语义完全不变。"""
        original_error = RuntimeError("verification failed")

        assert escalate_dependency_environment_failure(original_error, tmp_path) is original_error

    def test_unrelated_failure_returns_original_error(self, tmp_path: Path) -> None:
        """异常文本没点到记录里缺失的工具时不改判。"""
        _write_report(tmp_path, _RECORDS)
        original_error = RuntimeError("agent produced no commit")

        assert escalate_dependency_environment_failure(original_error, tmp_path) is original_error

    def test_missing_executable_error_is_reclassified(self, tmp_path: Path) -> None:
        """FileNotFoundError.filename 对上记录里的缺失工具 → 可恢复阻塞。"""
        _write_report(tmp_path, _RECORDS)
        original_error = FileNotFoundError(2, "No such file or directory")
        original_error.filename = "pnpm"

        escalated_error = escalate_dependency_environment_failure(original_error, tmp_path)

        assert isinstance(escalated_error, DependencyEnvironmentBlockedError)
        assert escalated_error.dependency_records == _RECORDS
        assert "Missing required executable(s): pnpm." in escalated_error.issue_comment_body
        assert "kc blocked-continue" in escalated_error.issue_comment_body

    def test_command_not_found_verification_is_reclassified(self, tmp_path: Path) -> None:
        """验证命令以 127 结束且 stderr 点名缺失工具时同样改判。"""
        _write_report(tmp_path, _RECORDS)
        verification_result = CommandResult(
            command=("pnpm", "test"),
            return_code=127,
            stdout="",
            stderr="bash: pnpm: command not found",
        )
        original_error = VerificationFailedError([verification_result])

        escalated_error = escalate_dependency_environment_failure(original_error, tmp_path)

        assert isinstance(escalated_error, DependencyEnvironmentBlockedError)

    @pytest.mark.parametrize(
        ("user_action", "failure_text"),
        [
            (USER_ACTION_CREDENTIALS, "npm ERR! code E401 _authToken=very-secret-token"),
            (USER_ACTION_PRIVILEGE, "npm ERR! EACCES: permission denied /usr/local/lib"),
        ],
        ids=("registry-credentials", "filesystem-permission"),
    )
    def test_human_action_failure_is_reclassified_without_leaking_secrets(
        self,
        tmp_path: Path,
        user_action: str,
        failure_text: str,
    ) -> None:
        """异常被 recovery 包装后仍能识别 npm 凭据/权限错误并脱敏阻塞说明。"""
        action_record = FrontendProjectRecord(
            project_path="frontend",
            outcome=OUTCOME_NOT_PREPARED,
            reason=REASON_INSTALL_FAILED,
            package_manager="npm",
            attempted_commands=("npm ci --ignore-scripts",),
            requires_user_action=user_action,
            detail=sanitize_detail_text(failure_text),
        )
        _write_report(tmp_path, (action_record,))
        verification_result = CommandResult(
            command=("npm", "ci", "--ignore-scripts"),
            return_code=1,
            stdout="",
            stderr=failure_text,
        )

        try:
            raise RuntimeError("Failed after 1 attempts.") from VerificationFailedError(
                [verification_result]
            )
        except RuntimeError as wrapped_failure:
            escalated_error = escalate_dependency_environment_failure(wrapped_failure, tmp_path)

        assert isinstance(escalated_error, DependencyEnvironmentBlockedError)
        assert f"Required human action(s): {user_action}." in escalated_error.issue_comment_body
        if "very-secret-token" in failure_text:
            assert "very-secret-token" not in escalated_error.issue_comment_body
            assert "[redacted]" in escalated_error.issue_comment_body

    def test_unrelated_command_does_not_trigger_recorded_credential_block(
        self, tmp_path: Path
    ) -> None:
        """其他命令报错时，frontend 的凭据记录不会把任务误判成环境阻塞。"""
        credential_record = FrontendProjectRecord(
            project_path="frontend",
            outcome=OUTCOME_NOT_PREPARED,
            reason=REASON_INSTALL_FAILED,
            package_manager="npm",
            requires_user_action=USER_ACTION_CREDENTIALS,
        )
        _write_report(tmp_path, (credential_record,))
        original_error = VerificationFailedError(
            [
                CommandResult(
                    command=("pytest", "tests"),
                    return_code=1,
                    stdout="",
                    stderr="401 unauthorized",
                )
            ]
        )

        assert escalate_dependency_environment_failure(original_error, tmp_path) is original_error

    def test_tool_that_got_usable_dependencies_is_not_blamed(self, tmp_path: Path) -> None:
        """工具虽曾缺失、但已复用主检出依赖时，失败仍是执行失败。"""
        resolved_record = FrontendProjectRecord(
            project_path="frontend",
            outcome=OUTCOME_REUSED,
            reason=REASON_TOOL_MISSING,
            package_manager="pnpm",
        )
        _write_report(tmp_path, (resolved_record,))
        original_error = FileNotFoundError(2, "No such file or directory")
        original_error.filename = "pnpm"

        assert escalate_dependency_environment_failure(original_error, tmp_path) is original_error

    def test_conflicting_manager_record_without_tool_is_not_blamed(self, tmp_path: Path) -> None:
        """识别不出包管理器的记录没有缺失工具可归因，保持原失败语义。"""
        conflict_record = FrontendProjectRecord(
            project_path="frontend",
            outcome=OUTCOME_NOT_PREPARED,
            reason=REASON_MANAGER_CONFLICT,
        )
        _write_report(tmp_path, (conflict_record,))
        original_error = RuntimeError("verification failed")

        assert escalate_dependency_environment_failure(original_error, tmp_path) is original_error


class TestBuildEnvironmentBlockMessage:
    """面向人的阻塞说明。"""

    def test_lists_only_records_needing_attention(self) -> None:
        """已就绪且无需人类决定的项目不出现在说明里。"""
        message_text = build_environment_block_message(
            ("pnpm",),
            _RECORDS,
            root_cause="FileNotFoundError: pnpm",
        )

        assert "Missing required executable(s): pnpm." in message_text
        assert "`frontend`" in message_text
        assert "`frontend-admin`" not in message_text
        assert "Options for the operator" in message_text
        assert "Root cause: FileNotFoundError: pnpm" in message_text

    def test_redacts_credentials_from_root_cause(self) -> None:
        """验证异常的原始输出也必须脱敏后才能写进 Issue 阻塞评论。"""
        message_text = build_environment_block_message(
            (),
            (
                FrontendProjectRecord(
                    project_path="frontend",
                    outcome=OUTCOME_NOT_PREPARED,
                    reason=REASON_INSTALL_FAILED,
                    package_manager="npm",
                    requires_user_action=USER_ACTION_CREDENTIALS,
                ),
            ),
            root_cause="VerificationFailedError: _authToken=very-secret-token",
        )

        assert "very-secret-token" not in message_text
        assert "Root cause: VerificationFailedError: _authToken=[redacted]" in message_text


def test_outcome_and_missing_executable_properties() -> None:
    """记录自身的两个判定属性口径正确。"""
    tool_missing_record = _RECORDS[0]
    installed_record = _RECORDS[1]

    assert tool_missing_record.has_usable_dependencies is False
    assert tool_missing_record.missing_executables == ("pnpm",)
    assert installed_record.has_usable_dependencies is True
    assert installed_record.missing_executables == ()

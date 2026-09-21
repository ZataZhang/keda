"""agent 命令行黄金快照测试（rv-1 的自动化部分）。

12 条既有命令行（codex / claude / kimi × run / deliberate / generate / repl）
与改造前逐字节一致是本 PRD 的硬验收线：任何 spec 数据、占位符展开或
提示词投递语义的回归都会在这里直接变红。pi 的四条命令行是**新增**
目标形态（无"改造前"可对照），一并锁定防止后续漂移。
codebuddy / qoder / opencode 共 11 条同样是**新增**目标形态：codebuddy 与
claude 同构；qoder 的可执行名是 qodercn、run 用 -o stream-json、deliberate
保留 -p 并走 stdin；opencode 只有 run / deliberate / repl（缺 generate 是刻意的）。

改动本文件的期望值前，先确认是有意的行为变更，并同步更新
`docs/guides/agent-runner.md` 与 PRD 中的目标形态表。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import (
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
    PLAIN_PROTOCOL_ID,
)
from backend.core.use_cases.agent_invocation import (
    UnknownAgentError,
    UnknownExpanderError,
    UnknownProfileError,
    build_agent_invocation,
)

PROMPT = "golden-prompt"


@pytest.fixture()
def app_config() -> AppConfig:
    """内置四个 agent 的默认注册表（config.toml 未覆盖时的等价形态）。"""
    return AppConfig()


@pytest.fixture()
def plain_worktree(tmp_path: Path) -> Path:
    """普通目录（`.git` 非指针文件）：``git_writable_roots`` 展开器返回空。"""
    return tmp_path


# ---------------------------------------------------------------------------
# codex：4 条黄金快照
# ---------------------------------------------------------------------------


def test_golden_codex_run(app_config: AppConfig, plain_worktree: Path) -> None:
    """codex 主执行：workspace-write 沙箱 + 网络开关 + tail exec + argv 尾提示词。"""
    invocation = build_agent_invocation("codex", "run", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "codex",
        "--cd",
        str(plain_worktree),
        "--sandbox",
        "workspace-write",
        "--config",
        "sandbox_workspace_write.network_access=true",
        "--ask-for-approval",
        "never",
        "exec",
        PROMPT,
    )
    assert invocation.prompt_delivery == "argv_tail"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is False


def test_golden_codex_deliberate(app_config: AppConfig, plain_worktree: Path) -> None:
    """codex 辩论：read-only 沙箱 + tail exec，提示词走 stdin。"""
    invocation = build_agent_invocation("codex", "deliberate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "codex",
        "--cd",
        str(plain_worktree),
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "exec",
    )
    assert invocation.prompt_delivery == "stdin"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is True


def test_golden_codex_generate(app_config: AppConfig, plain_worktree: Path) -> None:
    """codex 内容生成：read-only 沙箱 + tail exec + argv 尾提示词。"""
    invocation = build_agent_invocation("codex", "generate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "codex",
        "--cd",
        str(plain_worktree),
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "exec",
        PROMPT,
    )
    assert invocation.prompt_delivery == "argv_tail"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is True


def test_golden_codex_repl(app_config: AppConfig, plain_worktree: Path) -> None:
    """codex REPL：仅 --cd + tail exec，无沙箱参数（边界由 REPL 命令执行器把守）。"""
    invocation = build_agent_invocation("codex", "repl", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "codex",
        "--cd",
        str(plain_worktree),
        "exec",
        PROMPT,
    )
    assert invocation.prompt_delivery == "argv_tail"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is False


# ---------------------------------------------------------------------------
# claude：4 条黄金快照
# ---------------------------------------------------------------------------


def test_golden_claude_run(app_config: AppConfig, plain_worktree: Path) -> None:
    """claude 主执行：流式事件流 + argv 尾提示词，协议 claude-stream-json。"""
    invocation = build_agent_invocation("claude", "run", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "claude",
        "--dangerously-skip-permissions",
        "--verbose",
        "-p",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        PROMPT,
    )
    assert invocation.prompt_delivery == "argv_tail"
    assert invocation.output_protocol == "claude-stream-json"
    assert invocation.read_only is False


def test_golden_claude_deliberate(app_config: AppConfig, plain_worktree: Path) -> None:
    """claude 辩论：与主执行同形态（流式事件流）。"""
    invocation = build_agent_invocation("claude", "deliberate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "claude",
        "--dangerously-skip-permissions",
        "--verbose",
        "-p",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        PROMPT,
    )
    assert invocation.prompt_delivery == "argv_tail"
    assert invocation.output_protocol == "claude-stream-json"
    assert invocation.read_only is False


def test_golden_claude_generate(app_config: AppConfig, plain_worktree: Path) -> None:
    """claude 内容生成：纯文本路径（-p + argv 尾提示词）。"""
    invocation = build_agent_invocation("claude", "generate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "claude",
        "--dangerously-skip-permissions",
        "-p",
        PROMPT,
    )
    assert invocation.prompt_delivery == "argv_tail"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is True


def test_golden_claude_repl(app_config: AppConfig, plain_worktree: Path) -> None:
    """claude REPL：与内容生成同形态但可写。"""
    invocation = build_agent_invocation("claude", "repl", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "claude",
        "--dangerously-skip-permissions",
        "-p",
        PROMPT,
    )
    assert invocation.prompt_delivery == "argv_tail"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is False


# ---------------------------------------------------------------------------
# kimi：4 条黄金快照
# ---------------------------------------------------------------------------


def test_golden_kimi_run(app_config: AppConfig, plain_worktree: Path) -> None:
    """kimi 主执行：提示词跟在 --prompt 之后（flag 投递）。"""
    invocation = build_agent_invocation("kimi", "run", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("kimi", "--prompt", PROMPT)
    assert invocation.prompt_delivery == "flag"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is False


def test_golden_kimi_deliberate(app_config: AppConfig, plain_worktree: Path) -> None:
    """kimi 辩论：--input-format text，提示词走 stdin。"""
    invocation = build_agent_invocation("kimi", "deliberate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("kimi", "--input-format", "text")
    assert invocation.prompt_delivery == "stdin"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is False


def test_golden_kimi_generate(app_config: AppConfig, plain_worktree: Path) -> None:
    """kimi 内容生成：--prompt flag 投递，只读。"""
    invocation = build_agent_invocation("kimi", "generate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("kimi", "--prompt", PROMPT)
    assert invocation.prompt_delivery == "flag"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is True


def test_golden_kimi_repl(app_config: AppConfig, plain_worktree: Path) -> None:
    """kimi REPL：--prompt flag 投递，可写。"""
    invocation = build_agent_invocation("kimi", "repl", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("kimi", "--prompt", PROMPT)
    assert invocation.prompt_delivery == "flag"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is False


# ---------------------------------------------------------------------------
# pi：新增目标形态锁定（无改造前对照，按 PRD 注册块断言）
# ---------------------------------------------------------------------------


def test_golden_pi_run(app_config: AppConfig, plain_worktree: Path) -> None:
    """pi 主执行：--approve 信任项目文件 + --mode json 事件流，提示词走 stdin。"""
    invocation = build_agent_invocation("pi", "run", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("pi", "--approve", "--mode", "json")
    assert invocation.prompt_delivery == "stdin"
    assert invocation.output_protocol == "pi-json-lines"
    assert invocation.read_only is False


def test_golden_pi_deliberate(app_config: AppConfig, plain_worktree: Path) -> None:
    """pi 辩论：--no-tools 表达可验证只读（工具白名单），提示词走 stdin。"""
    invocation = build_agent_invocation("pi", "deliberate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("pi", "--approve", "--no-tools", "--print")
    assert invocation.prompt_delivery == "stdin"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is True


def test_golden_pi_generate(app_config: AppConfig, plain_worktree: Path) -> None:
    """pi 内容生成：--no-tools --print，只读。"""
    invocation = build_agent_invocation("pi", "generate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("pi", "--no-tools", "--print")
    assert invocation.prompt_delivery == "stdin"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is True


def test_golden_pi_repl(app_config: AppConfig, plain_worktree: Path) -> None:
    """pi REPL：--approve --print，可写。"""
    invocation = build_agent_invocation("pi", "repl", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("pi", "--approve", "--print")
    assert invocation.prompt_delivery == "stdin"
    assert invocation.output_protocol == "plain"
    assert invocation.read_only is False


# ---------------------------------------------------------------------------
# git_writable_roots 展开器（codex run 专属的既有行为）
# ---------------------------------------------------------------------------


def test_golden_codex_run_expands_git_writable_roots(app_config: AppConfig, tmp_path: Path) -> None:
    """linked worktree（.git 是指针文件）时 codex run 追加 --add-dir 放行 git 元数据。"""
    worktree_path = tmp_path / "wt"
    worktree_path.mkdir()
    main_git_dir = tmp_path / "main" / ".git" / "worktrees" / "wt"
    main_git_dir.mkdir(parents=True)
    (worktree_path / ".git").write_text(f"gitdir: {main_git_dir}\n", encoding="utf-8")

    invocation = build_agent_invocation("codex", "run", PROMPT, worktree_path, app_config)
    assert invocation.argv == (
        "codex",
        "--cd",
        str(worktree_path),
        "--sandbox",
        "workspace-write",
        "--config",
        "sandbox_workspace_write.network_access=true",
        "--ask-for-approval",
        "never",
        "--add-dir",
        str(main_git_dir.resolve()),
        "exec",
        PROMPT,
    )


def test_golden_codex_run_expands_commondir(app_config: AppConfig, tmp_path: Path) -> None:
    """指针文件带 commondir 时追加第二个 --add-dir（与旧实现一致的解析顺序）。"""
    worktree_path = tmp_path / "wt"
    worktree_path.mkdir()
    main_git_dir = tmp_path / "main" / ".git" / "worktrees" / "wt"
    main_git_dir.mkdir(parents=True)
    common_dir = tmp_path / "main" / ".git"
    (main_git_dir / "commondir").write_text(f"{common_dir}\n", encoding="utf-8")
    (worktree_path / ".git").write_text(f"gitdir: {main_git_dir}\n", encoding="utf-8")

    invocation = build_agent_invocation("codex", "run", PROMPT, worktree_path, app_config)
    assert invocation.argv[-6:-4] == (
        "--add-dir",
        str(main_git_dir.resolve()),
    )
    assert invocation.argv[-4:-2] == (
        "--add-dir",
        str(common_dir.resolve()),
    )


# ---------------------------------------------------------------------------
# codebuddy / qoder / opencode：新增目标形态（无"改造前"可对照）
#
# 三者均为**新增**注册项，一并锁定防止后续漂移。codebuddy 与 claude 同构；
# qoder 的可执行名是 qodercn、run 用 -o stream-json、deliberate 走 stdin；
# opencode 只有 run / deliberate / repl 三个用途（缺 generate 是刻意的，
# 它没有可验证的只读机制）。
# ---------------------------------------------------------------------------


def test_golden_codebuddy_run(app_config: AppConfig, plain_worktree: Path) -> None:
    """codebuddy 主执行：与 claude 同构的流式形态。"""
    invocation = build_agent_invocation("codebuddy", "run", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "codebuddy",
        "--dangerously-skip-permissions",
        "--verbose",
        "-p",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        PROMPT,
    )
    assert invocation.output_protocol == CLAUDE_STREAM_JSON_PROTOCOL_ID


def test_golden_codebuddy_deliberate(app_config: AppConfig, plain_worktree: Path) -> None:
    """codebuddy 辩论：同一套流式形态。"""
    invocation = build_agent_invocation(
        "codebuddy", "deliberate", PROMPT, plain_worktree, app_config
    )
    assert invocation.argv == (
        "codebuddy",
        "--dangerously-skip-permissions",
        "--verbose",
        "-p",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        PROMPT,
    )


def test_golden_codebuddy_generate(app_config: AppConfig, plain_worktree: Path) -> None:
    """codebuddy 生成：只读用途，提示词在 argv 尾部。"""
    invocation = build_agent_invocation("codebuddy", "generate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("codebuddy", "--dangerously-skip-permissions", "-p", PROMPT)
    assert invocation.read_only is True


def test_golden_codebuddy_repl(app_config: AppConfig, plain_worktree: Path) -> None:
    """codebuddy REPL：可写形态。"""
    invocation = build_agent_invocation("codebuddy", "repl", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("codebuddy", "--dangerously-skip-permissions", "-p", PROMPT)


def test_golden_qoder_run(app_config: AppConfig, plain_worktree: Path) -> None:
    """qoder 主执行：bin 是 qodercn，用 -o stream-json 走流式协议。"""
    invocation = build_agent_invocation("qoder", "run", PROMPT, plain_worktree, app_config)
    assert invocation.argv == (
        "qodercn",
        "--dangerously-skip-permissions",
        "-p",
        "-o",
        "stream-json",
        PROMPT,
    )
    assert invocation.output_protocol == CLAUDE_STREAM_JSON_PROTOCOL_ID


def test_golden_qoder_deliberate(app_config: AppConfig, plain_worktree: Path) -> None:
    """qoder 辩论：保留 -p、提示词走 stdin（不依赖 qoder 缺 -p 时的行为）。"""
    invocation = build_agent_invocation("qoder", "deliberate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("qodercn", "--dangerously-skip-permissions", "-p")
    assert invocation.prompt_delivery == "stdin"
    assert invocation.output_protocol == PLAIN_PROTOCOL_ID


def test_golden_qoder_generate(app_config: AppConfig, plain_worktree: Path) -> None:
    """qoder 生成：只读用途。"""
    invocation = build_agent_invocation("qoder", "generate", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("qodercn", "--dangerously-skip-permissions", "-p", PROMPT)
    assert invocation.read_only is True


def test_golden_qoder_repl(app_config: AppConfig, plain_worktree: Path) -> None:
    """qoder REPL：可写形态。"""
    invocation = build_agent_invocation("qoder", "repl", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("qodercn", "--dangerously-skip-permissions", "-p", PROMPT)


def test_golden_opencode_run(app_config: AppConfig, plain_worktree: Path) -> None:
    """opencode 主执行：run 子命令 + 逐行文本输出。"""
    invocation = build_agent_invocation("opencode", "run", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("opencode", "run", "--dangerously-skip-permissions", PROMPT)
    assert invocation.output_protocol == PLAIN_PROTOCOL_ID


def test_golden_opencode_deliberate(app_config: AppConfig, plain_worktree: Path) -> None:
    """opencode 辩论：同一形态。"""
    invocation = build_agent_invocation(
        "opencode", "deliberate", PROMPT, plain_worktree, app_config
    )
    assert invocation.argv == ("opencode", "run", "--dangerously-skip-permissions", PROMPT)


def test_golden_opencode_repl(app_config: AppConfig, plain_worktree: Path) -> None:
    """opencode REPL：同一形态。"""
    invocation = build_agent_invocation("opencode", "repl", PROMPT, plain_worktree, app_config)
    assert invocation.argv == ("opencode", "run", "--dangerously-skip-permissions", PROMPT)


def test_opencode_has_no_generate_profile(app_config: AppConfig, plain_worktree: Path) -> None:
    """opencode 刻意不声明 generate：它没有可验证的只读机制，不放假只读声明。"""
    with pytest.raises(UnknownProfileError) as exc_info:
        build_agent_invocation("opencode", "generate", PROMPT, plain_worktree, app_config)
    message = str(exc_info.value)
    assert "opencode" in message
    assert "generate" in message
    # 报错必须列出已声明的用途，且不得回落到别的用途
    assert "run" in message


# ---------------------------------------------------------------------------
# 失败路径：不降级、带上下文
# ---------------------------------------------------------------------------


def test_unknown_agent_lists_registered(app_config: AppConfig, plain_worktree: Path) -> None:
    """未注册 agent 报错必须列出全部已注册名，绝不静默回落默认 agent。"""
    with pytest.raises(UnknownAgentError) as exc_info:
        build_agent_invocation("does-not-exist", "run", PROMPT, plain_worktree, app_config)
    message = str(exc_info.value)
    for registered_name in ("codex", "claude", "kimi", "pi", "codebuddy", "qoder", "opencode"):
        assert registered_name in message


def test_unknown_profile_raises(app_config: AppConfig, plain_worktree: Path) -> None:
    """已注册 agent 缺指定用途时报错并列出已声明用途。"""
    from dataclasses import replace

    config = AppConfig()
    claude_spec = config.agents["claude"]
    # replace 出独立副本：直接 pop 会污染共享的 BUILTIN_AGENT_SPECS。
    config.agents["partial"] = replace(
        claude_spec,
        profiles={
            name: spec for name, spec in claude_spec.profiles.items() if name != "deliberate"
        },
    )
    with pytest.raises(UnknownProfileError) as exc_info:
        build_agent_invocation("partial", "deliberate", PROMPT, plain_worktree, config)
    assert "run" in str(exc_info.value)


def test_unknown_expander_raises(app_config: AppConfig, plain_worktree: Path) -> None:
    """配置引用未命名展开器时 fail fast，不猜测语义。"""
    from dataclasses import replace

    from backend.core.shared.models.agent_spec import AgentProfileSpec

    config = AppConfig()
    codex_spec = config.agents["codex"]
    broken_run_profile = AgentProfileSpec(
        expand=("no_such_expander:--flag",),
        tail_args=("exec",),
        prompt_delivery="argv_tail",
    )
    # replace 出独立副本：直接改 profiles 会污染共享的 BUILTIN_AGENT_SPECS。
    config.agents["custom"] = replace(
        codex_spec,
        profiles={**codex_spec.profiles, "run": broken_run_profile},
    )
    with pytest.raises(UnknownExpanderError):
        build_agent_invocation("custom", "run", PROMPT, plain_worktree, config)


def test_flag_delivery_without_prompt_flag_raises(
    app_config: AppConfig, plain_worktree: Path
) -> None:
    """prompt_delivery='flag' 但缺 prompt_flag 时报错，不产出残缺命令。"""
    from dataclasses import replace

    from backend.core.shared.models.agent_spec import AgentProfileSpec

    config = AppConfig()
    claude_spec = config.agents["claude"]
    broken_run_profile = AgentProfileSpec(prompt_delivery="flag")
    # replace 出独立副本：直接改 profiles 会污染共享的 BUILTIN_AGENT_SPECS。
    config.agents["custom"] = replace(
        claude_spec,
        profiles={**claude_spec.profiles, "run": broken_run_profile},
    )
    with pytest.raises(ValueError, match="prompt_flag"):
        build_agent_invocation("custom", "run", PROMPT, plain_worktree, config)

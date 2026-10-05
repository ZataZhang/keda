"""IAR 自带 operator Skill 的发行资源与用户目录冲突保护。"""

from __future__ import annotations

import re
from pathlib import Path

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_schema import build_command_schema
from backend.api.cli_typer_app import app
from backend.engines.agent_runner.remote_template_skills import install_packaged_operator_skill

#: 随包发行的 SKILL.md 源（与安装产物同一份文件）。
_PACKAGED_SKILL = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "backend"
    / "engines"
    / "agent_runner"
    / "templates"
    / "skills"
    / "iar-operator"
    / "SKILL.md"
)


def test_packaged_operator_skill_dry_run_reports_install_and_writes_nothing(tmp_path: Path) -> None:
    """干净 home 的 init dry-run 应发现随包 Skill 而不落盘。"""
    skills_root = tmp_path / ".codex" / "skills"

    result = install_packaged_operator_skill(
        target_skills_root=skills_root,
        dry_run=True,
        force=False,
    )

    assert result.action == "install"
    assert result.target_path == skills_root / "iar-operator"
    assert not skills_root.exists()


def test_packaged_operator_skill_preserves_user_conflict_by_default(tmp_path: Path) -> None:
    """已有同名用户 Skill 默认保留，dry-run 与实际安装都报告冲突。"""
    skills_root = tmp_path / "skills"
    user_skill = skills_root / "iar-operator" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("user content\n", encoding="utf-8")

    preview = install_packaged_operator_skill(
        target_skills_root=skills_root,
        dry_run=True,
        force=False,
    )
    result = install_packaged_operator_skill(
        target_skills_root=skills_root,
        dry_run=False,
        force=False,
    )

    assert preview.action == result.action == "preserve-conflict"
    assert user_skill.read_text(encoding="utf-8") == "user content\n"


# ── SKILL.md 内容：四条查看路径、只读/执行分流、命令与 --help 一致 ────────────


def _skill_text() -> str:
    return _PACKAGED_SKILL.read_text(encoding="utf-8")


def test_packaged_skill_documents_issue_output_paths() -> None:
    """发行 Skill 必须覆盖按 Issue 查看、/ps 边界与网页查看三条路径。"""
    text = _skill_text()
    assert "iar logs --repo <path> --issue <N> --follow" in text
    # 一次性查看（不带 --follow）是独立路径，不能用「其它行也含 --issue <N>」蒙混。
    assert "iar logs --repo <path> --issue <N>`" in text
    assert "/ps" in text
    # /ps 的同会话后台终端边界必须写清，不得暗示外部任务自动可见。
    assert "same Codex session" in text
    assert "never appears in `/ps`" in text
    # 网页查看路径（Backlog PRD 详情的实时输出标签）。
    assert "实时输出" in text


_PLACEHOLDER = re.compile(r"<[^>]+>")


def _flags_of(tokens: list[str]) -> set[str]:
    """提取命令 token 序列里的 flag 名（去掉 ``=值`` 部分）。"""
    return {token.split("=")[0] for token in tokens if token.startswith("-")}


#: Skill 命令示例允许出现的旗标精选集。覆盖面以「能蒙住假阳性」为准，不与
#: ``--help`` 全集对齐；但每个条目都必须真实存在于当前命令树（由
#: ``test_packaged_skill_whitelist_matches_runtime_schema`` 对 ``iar schema --json``
#: 的运行时派生结果断言），CLI 删掉 Skill 仍在用的旗标必须让守卫红。
_ALLOWED_FLAGS: dict[tuple[str, ...], set[str]] = {
    ("run",): {"--dry-run", "--max-issues", "--repo", "--repo-id", "--agent", "--all"},
    ("logs",): {"--repo", "--repo-id", "--issue", "--follow", "--lines", "-n", "-f", "--kind"},
    ("issue", "list"): {"--repo", "--repo-id", "--state", "--label", "--limit"},
    ("issue", "create"): set(),
    ("init",): {"--dry-run", "--force"},
    ("registry", "start"): set(),
    ("registry", "stop"): set(),
    ("registry", "list"): set(),
    ("daemon", "status"): set(),
    ("recover",): {"--issue", "--branch", "--repo", "--repo-id"},
    ("blocked-continue",): {"--issue", "--agent", "--repo", "--repo-id"},
    ("worktree", "path"): {"--branch"},
    ("agent", "presets"): set(),
    # ask / deliberate 的 --output 是输出目录，不是格式；取值集合取自
    # ``iar schema --json`` 的运行时派生结果，而非人工记忆。
    ("ask",): {
        "--agent",
        "--plan-only",
        "--execute",
        "--yes",
        "--output",
        "--preset",
        "--model",
        "--reasoning-effort",
        "--repo",
        "--repo-id",
        "--config",
    },
    ("deliberate",): {
        "--agents",
        "--rounds",
        "--synthesizer",
        "--output",
        "--session-id",
        "--strict",
        "--repo",
        "--repo-id",
        "--config",
    },
    ("schema",): {"--json", "--output"},
    # `iar backlog ci` 子组（P1-FEAT-20260916-134008）：policy 的 --global 与
    # --prd 互斥；status 的 --json 复用 Console ci_delivery DTO。
    ("backlog", "ci", "status"): {"--prd", "--json", "--repo", "--repo-id"},
    ("backlog", "ci", "policy"): {"--global", "--prd", "--repo", "--repo-id"},
    ("backlog", "ci", "repair"): {"--prd", "--dry-run", "--repo", "--repo-id"},
    ("agent", "doctor"): {
        "--all-profiles",
        "--json",
        "--protocols",
        "--prompt",
        "--preset",
        "--model",
        "--reasoning-effort",
        "--lifecycle",
    },
}


def _real_flags_by_path() -> dict[tuple[str, ...], set[str]]:
    """从真实 Typer 命令树派生「命令路径 → 全部旗标名（含短旗标与 secondary）」。"""
    schema = build_command_schema(app)
    return {
        tuple(command["path"]): {name for option in command["options"] for name in option["names"]}
        for command in schema["commands"]
    }


def test_packaged_skill_command_examples_match_cli_help() -> None:
    """Skill 中的命令示例所用的 flag 必须真实存在于当前 CLI。"""
    text = _skill_text()
    # 提取反引号内的 iar 命令示例。
    examples = re.findall(r"`(iar [^`]+)`", text)
    assert examples, "SKILL.md 应包含 iar 命令示例"

    # 每个子命令的合法 flag 集合见模块级 ``_ALLOWED_FLAGS``：覆盖面是精选集，
    # 示例中出现集合之外的 flag 即视为与 CLI 漂移。
    allowed_flags = _ALLOWED_FLAGS
    for example in examples:
        tokens = example.split()
        subcommand = tuple(
            token
            for token in tokens[1:]
            if not token.startswith("-") and not _PLACEHOLDER.fullmatch(token)
        )
        # ``iar --help`` 这类纯 flag 形式没有子命令，跳过；``start|stop``
        # 这类紧凑写法先按分隔符拆开再逐个匹配。
        if not subcommand:
            continue
        subcommand = tuple(part for token in subcommand for part in token.split("|"))
        key = next(
            (
                candidate
                for candidate in allowed_flags
                if list(subcommand[: len(candidate)]) == list(candidate)
            ),
            None,
        )
        assert key is not None, f"未收录的命令示例：{example}"
        # ``--help`` 是所有子命令都支持的元 flag，不参与漂移判定。
        unknown = _flags_of(tokens) - allowed_flags[key] - {"--help", "-h"}
        assert not unknown, f"{example} 使用了 --help 中不存在的 flag：{unknown}"


def test_packaged_skill_whitelist_matches_runtime_schema() -> None:
    """白名单每个旗标都必须真实存在于当前命令树（防 skill ↔ CLI 漂移）。"""
    real_flags_by_path = _real_flags_by_path()
    for subcommand, whitelist in _ALLOWED_FLAGS.items():
        assert subcommand in real_flags_by_path, f"schema 中不存在命令 {subcommand}"
        unknown = whitelist - real_flags_by_path[subcommand]
        assert not unknown, f"{subcommand} 白名单旗标在真实命令树中不存在：{unknown}"


def test_packaged_skill_separates_read_only_from_execution() -> None:
    """只读查看路径不得引导启动新任务；执行路径必须显式标注写副作用。"""
    text = _skill_text()
    # 只读意图的明确保护。
    assert "never start" in text.lower() or "never starts" in text.lower()
    # 执行路径仍然如实声明副作用。
    assert "Runs configured Agents" in text
    # 旧语义兼容：不带 --issue 时仍是托管进程日志。
    assert "managed process" in text
    assert "--issue` and `--kind` are mutually exclusive" in text


def test_packaged_skill_keeps_conflict_protection_guidance() -> None:
    """安装冲突保护语义仍在 Skill 中：默认保留用户自有 Skill。"""
    text = _skill_text()
    assert "preserved by default" in text
    assert "--force" in text


def test_packaged_skill_documents_machine_output_contract() -> None:
    """机读契约（FR-1/FR-3/FR-5/FR-6）必须写进随包 Skill，agent 才不必猜。

    断言的是「文档里有哪些可执行事实」：显式 ``--json``、默认仍是人类表格、
    stdout 只承载数据、stderr envelope 字段、完整退出码表、``iar schema --json``
    自省入口，以及 ``ask``/``deliberate`` 的 ``--output`` 目录例外。
    """
    text = _skill_text()
    assert "`--json` is an alias of `--output json`" in text
    assert "stdout carries data only" in text
    for envelope_field in ('"error"', '"message"', '"suggestion"', '"retryable"', '"exit_code"'):
        assert envelope_field in text, f"SKILL.md 未列出 envelope 字段 {envelope_field}"
    # 表里的 Name 列必须是 envelope 的 ``error`` 取值本身：agent 靠它把 $? 和 stderr 对上。
    for error_token in (
        "`ok`",
        "`usage_error`",
        "`not_found`",
        "`permission_denied`",
        "`conflict`",
        "`dry_run_ok`",
    ):
        assert error_token in text, f"SKILL.md 退出码表缺少 envelope 取值 {error_token}"
    assert "exit_codes.values" in text
    for code in ("`0`", "`1`", "`2`", "`3`", "`4`", "`5`", "`10`"):
        assert code in text, f"SKILL.md 退出码表缺少 {code}"
    assert "iar schema --json" in text
    assert "output **directory**" in text


def test_packaged_skill_documents_triage_paths() -> None:
    """Triage 章节必须覆盖标签语义、卡住时的判读与恢复动作。"""
    text = _skill_text()
    # 标签语义：运维据此决定下一步，缺一个就会靠猜。
    for label in (
        "agent/ready",
        "agent/waiting",
        "agent/running",
        "agent/supervising",
        "agent/review",
        "agent/failed",
        "agent/blocked",
        "validation/verifier-passed",
    ):
        assert label in text, f"SKILL.md 未说明 {label} 的含义"
    # 无输出/卡住时必须能读出「重试 → 换 agent」这条链，而不是直接报结论。
    assert "Attempt History" in text
    assert "max_recovery_attempts" in text
    assert "agent_fallback_order" in text
    # 恢复动作：发布失败用 recover，回队列用标签，代码位置用 worktree path。
    assert "iar recover --issue <N>" in text
    assert "agent/ready --remove-label agent/failed" in text
    assert "iar worktree path --branch issue-<N>" in text

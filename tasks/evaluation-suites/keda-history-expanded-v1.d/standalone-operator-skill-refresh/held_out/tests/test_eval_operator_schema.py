"""从历史判分抽取的冻结契约检查；不覆盖候选已有回归文件。"""

from __future__ import annotations


from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入


from backend.api.cli_schema import build_command_schema


from backend.api.cli_typer_app import app


_ALLOWED_FLAGS: dict[tuple[str, ...], set[str]] = {
    ("run",): {
        "--dry-run",
        "--max-issues",
        "--repo",
        "--repo-id",
        "--agent",
        "--all",
        # 目标必填契约（run-daemon-autopilot-control-surface）：--issue / PRD
        # 路径 / --all-ready 三选一；--takeover 显式接管（--yes 免确认）。
        # --fast-merge 为一次性快速通道旁路（issue-207），单次目标专用。
        # --direct-pr 为无 PRD 锚点 Issue 的直发档（issue-215），同样单次目标专用。
        "--issue",
        "--all-ready",
        "--fast-merge",
        "--direct-pr",
        "--takeover",
        "--yes",
    },
    ("daemon", "run"): {
        "--autopilot",
        "--no-autopilot",
        "--interval",
        "--agent",
        "--max-issues",
        "--concurrency",
        "--repo",
        "--repo-id",
        "--all",
    },
    ("logs",): {"--repo", "--repo-id", "--issue", "--follow", "--lines", "-n", "-f", "--kind"},
    ("issue", "list"): {"--repo", "--repo-id", "--state", "--label", "--limit"},
    # issue-215：--from-prompt 与 PRD 路径互斥，--require-validation 是验收小节的
    # 显式 opt-in（默认正文不带 PRD 锚点也不带验收清单）。
    # issue-234：建 issue 子命令说明写明「是否进队列」，示例用到 --ready；--repo
    # 是仓库歧义时的显式目标。均已在真实命令树核对。
    ("issue", "create"): {"--from-prompt", "--require-validation", "--ready", "--repo"},
    # issue-235：随包 Skill 写明 direct-pr 标签由 `kc labels sync` 建立/改名，
    # 旗标集合取自 ``kc schema --json`` 的真实命令树。
    ("labels", "sync"): {"--repo", "--repo-id", "--config", "--all"},
    ("init",): {"--dry-run", "--force"},
    # issue-245：随包 Skill 写明 skill 重装/刷新的独立入口；它不带仓库 selector。
    ("skill", "install"): {"--dry-run", "--force"},
    ("registry", "start"): set(),
    ("registry", "stop"): {"--repo-id", "--all"},
    ("backlog", "advance"): {"--dry-run", "--repo", "--repo-id", "--config"},
    ("registry", "list"): set(),
    ("daemon", "status"): set(),
    ("recover",): {"--issue", "--branch", "--repo", "--repo-id"},
    ("blocked-continue",): {"--issue", "--agent", "--repo", "--repo-id"},
    ("worktree", "path"): {"--branch"},
    ("agent", "presets"): set(),
    # ask / deliberate 的 --output 是输出目录，不是格式；取值集合取自
    # ``kc schema --json`` 的运行时派生结果，而非人工记忆。
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
    # `kc backlog ci` 子组（P1-FEAT-20260916-134008）：policy 的 --global 与
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


def test_packaged_skill_whitelist_matches_runtime_schema() -> None:
    """白名单每个旗标都必须真实存在于当前命令树（防 skill ↔ CLI 漂移）。"""
    real_flags_by_path = _real_flags_by_path()
    for subcommand, whitelist in _ALLOWED_FLAGS.items():
        assert subcommand in real_flags_by_path, f"schema 中不存在命令 {subcommand}"
        unknown = whitelist - real_flags_by_path[subcommand]
        assert not unknown, f"{subcommand} 白名单旗标在真实命令树中不存在：{unknown}"

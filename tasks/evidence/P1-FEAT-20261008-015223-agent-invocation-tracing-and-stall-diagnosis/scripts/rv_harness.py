"""Issue #242 Realistic Validation 共用夹具。

装配一个**完全隔离**的真实运行现场，然后用真实的 ``kc`` console script 驱动它：

- 真 Git：临时 bare ``origin`` + 真工作克隆 + runner 自己创建的 worktree。
- 真 SQLite：``HOME`` 指向临时目录，账本落在 ``<HOME>/.kedacode/console.db``。
- 真 CLI：``<worktree>/.venv/bin/kc``（本 worktree 源码安装的 console script）。
- GitHub 边界：PATH 上的 ``gh`` 替身（``fake_gh.py``），状态落 JSON，不联网。
- Agent 边界：PATH 上的 ``claude`` / ``kimi`` 替身（``fixture_agent.py``），
  行为由 JSON 计划决定，确定性可复现。

披露：已发布的 ``~/.local/bin/kc``（0.2.1）不含本次改动，因此这里驱动的是本
worktree 的 ``kc`` console script；两者是同一个打包入口，差别只在解析到的源码。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parents[3]
KC_BINARY = REPO_ROOT / ".venv" / "bin" / "kc"
PYTHON_BINARY = Path(sys.executable)

REPO_ID = "rv-harness"
GITHUB_REPO = "rv-harness/demo-repo"
BASE_BRANCH = "main"


def _run_git(args: Sequence[str], cwd: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(  # noqa: S603 - 夹具内部的真实 git 调用。
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _write_shim(bin_dir: Path, name: str, script: Path, agent_env: dict[str, str]) -> Path:
    """写一个可执行 shim：把调用转发给夹具脚本并带上身份环境变量。"""
    exports = "\n".join(f'export {key}="{value}"' for key, value in agent_env.items())
    shim_path = bin_dir / name
    shim_path.write_text(
        "#!/bin/sh\n"
        f"{exports}\n"
        f'exec "{PYTHON_BINARY}" "{script}" "$@"\n',
        encoding="utf-8",
    )
    shim_path.chmod(0o755)
    return shim_path


@dataclass
class Harness:
    """一次隔离运行现场的全部落点。"""

    root: Path
    origin: Path
    repo: Path
    bin_dir: Path
    home: Path
    config_path: Path
    gh_state_path: Path
    plan_path: Path
    probe_path: Path
    counter_dir: Path
    env: dict[str, str] = field(default_factory=dict)

    @property
    def state_home(self) -> Path:
        """``kc`` 的状态目录（``console.db`` 落在这里）。"""
        return self.home / ".kedacode"

    @property
    def console_db(self) -> Path:
        """旁路账本数据库路径。"""
        return self.state_home / "console.db"

    @property
    def issue_log_dir(self) -> Path:
        """per-Issue 日志目录（既有 ``kc logs --issue`` 读的就是这里）。"""
        return self.repo / "logs" / "agent-runner" / "issues" / REPO_ID

    # -- 真实入口 ---------------------------------------------------------

    def run_kc(self, args: Sequence[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
        """用真实 ``kc`` console script 跑一条命令（新进程、隔离环境）。"""
        return subprocess.run(  # noqa: S603 - 这正是本夹具要验证的真实入口。
            [str(KC_BINARY), *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def run_kc_detached(self, args: Sequence[str]) -> subprocess.Popen:
        """起一个可控生命周期的 ``kc`` 进程（rv-3 的中断场景要能杀掉它）。"""
        return subprocess.Popen(  # noqa: S603 - 同上。
            [str(KC_BINARY), *args],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    # -- 读取现场 ---------------------------------------------------------

    def issue_log_paths(self, issue_number: int) -> list[Path]:
        """列出该 Issue 的全部尝试日志（按名字排序 = 按时间排序）。"""
        if not self.issue_log_dir.exists():
            return []
        return sorted(self.issue_log_dir.glob(f"issue-{issue_number}-*.log"))

    def read_issue_logs(self, issue_number: int) -> str:
        """拼接该 Issue 全部尝试日志的原文。"""
        return "\n".join(
            path.read_text(encoding="utf-8") for path in self.issue_log_paths(issue_number)
        )

    def invocation_rows(self, issue_number: int) -> list[dict[str, Any]]:
        """用**新的** SQLite 连接直接读账本，核对事件与详情字段。"""
        if not self.console_db.exists():
            return []
        with sqlite3.connect(self.console_db) as connection:
            connection.row_factory = sqlite3.Row
            try:
                cursor = connection.execute(
                    "SELECT run_id, event_type, invocation_id, repo_id, issue_number, "
                    "phase, role, agent, occurred_at, detail_json "
                    "FROM agent_invocation_events WHERE issue_number = ? ORDER BY id ASC",
                    (issue_number,),
                )
                rows = cursor.fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            {**dict(row), "detail": json.loads(row["detail_json"])}  # noqa: B039
            for row in rows
        ]

    def probe_lines(self) -> list[dict[str, Any]]:
        """读取 fixture 执行器探针（证明真实进程边界确实起了子进程）。"""
        if not self.probe_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.probe_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def gh_state(self) -> dict[str, Any]:
        """读取 fake gh 的终态（标签、评论、PR）。"""
        with self.gh_state_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def fixture_fingerprint(self) -> str:
        """夹具脚本内容的 SHA-256 短哈希（final tree evidence 用）。"""
        digest = hashlib.sha256()
        for script in sorted(SCRIPTS_DIR.glob("*.py")):
            digest.update(script.name.encode("utf-8"))
            digest.update(script.read_bytes())
        return digest.hexdigest()[:16]

    def implementation_tree(self) -> str:
        """实现树的 ``git rev-parse HEAD:src`` 短哈希（final tree evidence 用）。"""
        return _run_git(["rev-parse", "HEAD:src"], REPO_ROOT, self.env)[:12]


def _machine_config(registry_entry: dict[str, Any]) -> str:
    """渲染机器级 config.toml（只放 registry，其余全部走仓库本地覆盖）。"""
    lines = [
        "[agent_runner]",
        "",
        f"[agent_runner.repositories.{REPO_ID}]",
    ]
    for key, value in registry_entry.items():
        rendered = f'"{value}"' if isinstance(value, str) else json.dumps(value)
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def _render_toml_value(value: Any) -> str:
    """把一个 Python 值渲染成 TOML 字面量（夹具只用到标量、列表与内联表）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        inner = ", ".join(f"{key} = {_render_toml_value(item)}" for key, item in value.items())
        return "{" + inner + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_render_toml_value(item) for item in value) + "]"
    return str(value)


def _repo_config(*, overrides: dict[str, Any]) -> str:
    """渲染仓库本地 ``.kedacode.toml``。

    ``overrides`` 形如 ``{"runner": {"max_recovery_attempts": 0}, ...}``，
    每个键对应一个 ``[agent_runner.<key>]`` 表；表内的 dict 值渲染成内联表，
    因此 ``{"presets": {"rv-claude": {...}}}`` 这类嵌套配置也能直接给出。
    """
    lines = [
        "[agent_runner]",
        "",
        "[agent_runner.repository]",
        f'id = "{REPO_ID}"',
        "enabled = true",
    ]
    for table_name, table_values in overrides.items():
        lines.append("")
        lines.append(f"[agent_runner.{table_name}]")
        for key, value in table_values.items():
            lines.append(f"{key} = {_render_toml_value(value)}")
    return "\n".join(lines) + "\n"


def build_harness(
    root: Path,
    *,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    issue_labels: Sequence[str] = ("agent/ready",),
    plan: dict[str, Any],
    repo_config_overrides: dict[str, Any] | None = None,
    extra_path_bins: Sequence[str] = (),
    seed_files: dict[str, str] | None = None,
) -> Harness:
    """在 ``root`` 下装配一个隔离运行现场并返回句柄。"""
    root.mkdir(parents=True, exist_ok=True)
    origin = root / "origin.git"
    repo = root / "demo-repo"
    bin_dir = root / "bin"
    home = root / "home"
    bin_dir.mkdir()
    home.mkdir()

    base_env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(root / "gitconfig"),
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "RV Harness",
        "GIT_AUTHOR_EMAIL": "rv-harness@example.invalid",
        "GIT_COMMITTER_NAME": "RV Harness",
        "GIT_COMMITTER_EMAIL": "rv-harness@example.invalid",
    }
    (root / "gitconfig").write_text("[user]\n\tname = RV Harness\n", encoding="utf-8")

    _run_git(["init", "--bare", "--initial-branch", BASE_BRANCH, str(origin)], root, base_env)
    _run_git(["clone", str(origin), str(repo)], root, base_env)
    (repo / "README.md").write_text("# rv harness demo repo\n", encoding="utf-8")
    # 与真实 keda 仓库一致地忽略 runner 自己的元数据目录：会话记录
    # （`.iar/agent-runner/sessions/<agent>.json`）是续传用的旁路落盘，不是 agent
    # 交付内容；不忽略就会被 checkpoint 当成"在途改动"提交进 Issue 分支。
    (repo / ".gitignore").write_text(".iar/\n", encoding="utf-8")
    for relative_path, content in (seed_files or {}).items():
        target = repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _run_git(["add", "-A"], repo, base_env)
    _run_git(["commit", "-m", "chore: seed rv harness demo repo"], repo, base_env)
    _run_git(["push", "origin", BASE_BRANCH], repo, base_env)

    gh_state_path = root / "gh-state.json"
    issue_url = f"https://github.example.invalid/{GITHUB_REPO}/issues/{issue_number}"
    gh_state_path.write_text(
        json.dumps(
            {
                "repo_full_name": GITHUB_REPO,
                "repo_html_url": f"https://github.example.invalid/{GITHUB_REPO}",
                "issues": {
                    str(issue_number): {
                        "number": issue_number,
                        "title": issue_title,
                        "url": issue_url,
                        "body": issue_body,
                        "state": "open",
                        "labels": list(issue_labels),
                        "comments": [],
                    }
                },
                "pulls": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    plan_path = root / "fixture-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    probe_path = root / "fixture-probe.jsonl"
    counter_dir = root / "fixture-counters"
    counter_dir.mkdir()

    agent_env = {
        "RV_FIXTURE_PLAN": str(plan_path),
        "RV_FIXTURE_PROBE": str(probe_path),
        "RV_FIXTURE_COUNTER_DIR": str(counter_dir),
    }
    _write_shim(bin_dir, "gh", SCRIPTS_DIR / "fake_gh.py", {"RV_GH_STATE": str(gh_state_path)})
    for agent_name in ("claude", "kimi", *extra_path_bins):
        _write_shim(
            bin_dir,
            agent_name,
            SCRIPTS_DIR / "fixture_agent.py",
            {**agent_env, "RV_FIXTURE_AGENT": agent_name},
        )

    config_path = root / "kedacode-config.toml"
    config_path.write_text(
        _machine_config({"path": str(repo), "enabled": True, "github_repo": GITHUB_REPO}),
        encoding="utf-8",
    )
    (repo / ".kedacode.toml").write_text(
        _repo_config(
            overrides={
                "git": {"remote": "origin", "base_branch": BASE_BRANCH},
                "worktree": {"provision_database": False},
                "memory": {"enabled": False},
                "post_pr_supervisor": {"enabled": False},
                **(repo_config_overrides or {}),
            }
        ),
        encoding="utf-8",
    )
    _run_git(["add", "-A"], repo, base_env)
    _run_git(["commit", "-m", "chore: add rv harness runner config"], repo, base_env)
    _run_git(["push", "origin", BASE_BRANCH], repo, base_env)

    env = {
        **base_env,
        "PATH": f"{bin_dir}{os.pathsep}{base_env['PATH']}",
        "KEDACODE_CONFIG": str(config_path),
        "IAR_CONFIG": str(config_path),
        "KEDACODE_SKIP_GH_AUTH_CHECK": "1",
        "IAR_SKIP_GH_AUTH_CHECK": "1",
        "RV_GH_STATE": str(gh_state_path),
        **agent_env,
    }
    prd_skill_path = _resolve_prd_skill_path()
    if prd_skill_path is not None:
        env["KEDACODE_PRD_SKILL_PATH"] = str(prd_skill_path)
        env["IAR_PRD_SKILL_PATH"] = str(prd_skill_path)

    harness = Harness(
        root=root,
        origin=origin,
        repo=repo,
        bin_dir=bin_dir,
        home=home,
        config_path=config_path,
        gh_state_path=gh_state_path,
        plan_path=plan_path,
        probe_path=probe_path,
        counter_dir=counter_dir,
        env=env,
    )
    _update_head_sha(harness, issue_number)
    return harness


def _resolve_prd_skill_path() -> Path | None:
    """解析真实安装的 prd skill（发布预检需要它的 Machine Contract）。"""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from backend.core.shared.prd_skill_location import resolve_prd_skill_path
    except ImportError:
        return None
    try:
        return resolve_prd_skill_path()
    except Exception:  # noqa: BLE001 - 解析不到就交给预检自己报错。
        return None


def _update_head_sha(harness: Harness, issue_number: int) -> None:
    """把当前 HEAD 写进 fake gh 状态，供 ``gh pr create`` 回填 PR 头。"""
    head_sha = _run_git(["rev-parse", "HEAD"], harness.repo, harness.env)
    state = json.loads(harness.gh_state_path.read_text(encoding="utf-8"))
    state["head_sha"] = head_sha
    state["head_branch"] = f"issue-{issue_number}"
    harness.gh_state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cleanup_harness(harness: Harness) -> None:
    """删除临时现场（保留证据文件，它们已经写在证据目录里）。

    设 ``RV_KEEP_HARNESS=1`` 可保留现场供排障；证据产物不受影响。
    """
    if os.environ.get("RV_KEEP_HARNESS"):
        print(f"[harness] kept at {harness.root}", file=sys.stderr)
        return
    shutil.rmtree(harness.root, ignore_errors=True)


__all__ = [
    "BASE_BRANCH",
    "GITHUB_REPO",
    "KC_BINARY",
    "REPO_ID",
    "Harness",
    "build_harness",
    "cleanup_harness",
]

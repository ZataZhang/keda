"""RV 证据脚本的公共 fixture 构造器（只服务证据采集，绝不进代码 diff）。

隔离口径：
- 独立 ``HOME`` 状态目录：console.db / processes.json / 日志全部落临时目录；
- ``IAR_CONFIG`` 指向 fixture config.toml（注册表 + console 路径/端口）；
- ``PATH`` 前置 fake ``gh`` 与 fake agent 可执行文件（PRD mock_boundary：只
  fake 外部 gh / agent 边界，kc CLI、daemon 主循环、SQLite、认领选择真实）；
- fake gh 的仓库状态是临时目录里的 JSON（flock 串行化读写），每次调用记 argv。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = SCRIPTS_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parents[3]  # scripts → <dir> → evidence → tasks → repo root
KC = REPO_ROOT / ".venv" / "bin" / "kc"

FAKE_GH = SCRIPTS_DIR / "gh"
FAKE_AGENT = SCRIPTS_DIR / "fake_agent"
CONTRACT_SKILL = SCRIPTS_DIR / "prd_skill_contract"

# 同进程内已分配过的端口；顺序 fixture 复用同一端口会撞上上一个 console
# 尚未消散的 TIME_WAIT socket（kc console 预检直接拒绝绑定）。
_RESERVED_PORTS: set[int] = set()


def _free_port() -> int:
    """向内核探测一个空闲端口（bind 0），避开固定端口段的哈希碰撞与 TIME_WAIT。"""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in _RESERVED_PORTS:
            _RESERVED_PORTS.add(port)
            return port


class Fixture:
    """一个隔离 fixture：仓库、状态目录、fake 边界与子进程环境。"""

    def __init__(
        self, name: str, *, capacity: int = 10, auto_advance: bool = False, port: int = 0
    ) -> None:
        base = Path(os.environ.get("RV_FIXTURE_BASE", SCRIPTS_DIR / "work"))
        self._ensure_base_ignored(base)
        root = base / name
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        self.name = name
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.repo_path = root / "repo"
        self.state_dir = root / "gh-state"
        self.state_dir.mkdir()
        self.console_db = self.home / ".kedacode" / "console.db"
        self.agent_probe = self.state_dir / "agent-probes.jsonl"
        self.gh_calls = self.state_dir / "gh_calls.jsonl"
        self.capacity = capacity
        # 每个 fixture 用内核探测出的空闲端口；顺序 fixture 之间互不重复，
        # 也不与上一个 console 的 TIME_WAIT socket 相撞。
        self.port = port or _free_port()

        self._init_git_repo(auto_advance)
        self._write_configs()
        self._init_gh_state()

    # ── 构造 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _ensure_base_ignored(base: Path) -> None:
        """让 scratch fixture 目录永不进入 ``git status``。

        fixture 会建真实 git 仓库（含 README.md），根 ``.gitignore`` 的
        ``!tasks/evidence/**/*.md`` 会把其中 ``*.md`` 重新纳入跟踪，使工作区显示为脏，
        从而触发独立 verifier「requires a clean committed worktree」门禁（复跑 RV 脚本会
        在 verifier 之前重建这些 fixture）。默认把 base 落在 ``scripts/``（其
        ``.gitignore`` 已是 ``*``）下；无论 base 指向何处，都再写一份自忽略
        ``.gitignore``，因为更深目录的 ``.gitignore`` 优先于根白名单——这与
        ``scripts/.gitignore`` 屏蔽 prd skill 桩里 ``SKILL.md`` 是同一生效机制。
        """
        base.mkdir(parents=True, exist_ok=True)
        ignore_file = base / ".gitignore"
        if not ignore_file.is_file():
            ignore_file.write_text("*\n", encoding="utf-8")

    def _init_git_repo(self, auto_advance: bool) -> None:
        repo = self.repo_path
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "rv@fixture.local"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "RV Fixture"], check=True)
        # 发布远端预检与 push 使用本机 bare 仓库，验证不访问外部 GitHub。
        self.remote_path = self.root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote_path)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "remote",
                "add",
                "origin",
                str(self.remote_path),
            ],
            check=True,
        )
        kedacode_toml = [
            '[agent_runner.repository]',
            'id = "rv-fixture"',
            "enabled = true",
            'display_name = "RV Fixture"',
            "",
            "[agent_runner.runner]",
            'default_agent = "claude"',
            'agent_fallback_order = ["claude"]',
            f"max_concurrent_issues = {self.capacity}",
            "max_recovery_attempts = 0",
            'verification_commands = ["echo rv-ok"]',
            "",
            "[agent_runner.backlog]",
            f"auto_advance = {'true' if auto_advance else 'false'}",
            "",
            "[agent_runner.worktree]",
            "provision_database = false",
            "",
            "[agent_runner.validation]",
            "verifier_enabled = false",
            "reexecute_commands = false",
            "",
            "[agent_runner.pre_pr_review]",
            "enabled = false",
            "",
            "[agent_runner.post_pr_supervisor]",
            "enabled = false",
            "",
            "[agent_runner.safety]",
            "auto_merge = false",
            "",
        ]
        (repo / ".kedacode.toml").write_text("\n".join(kedacode_toml), encoding="utf-8")
        tasks_dir = repo / "tasks" / "pending"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "README.md").write_text("# pending\n", encoding="utf-8")
        (repo / "README.md").write_text("# rv fixture repo\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "init fixture repo"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "--set-upstream", "origin", "main"],
            check=True,
        )

    def _write_configs(self) -> None:
        config = self.home / "config.toml"
        config.write_text(
            "\n".join(
                [
                    "[agent_runner.console]",
                    f'history_db_path = "{self.console_db}"',
                    f'process_registry_path = "{self.home / ".kedacode" / "processes.json"}"',
                    f'process_log_dir = "{self.home / "process-logs"}"',
                    f"port = {self.port}",
                    "",
                    "[agent_runner.runner]",
                    'runner_command = ' + json.dumps([str(KC)]) + "",
                    "",
                    "[agent_runner.repositories.rv-fixture]",
                    f'path = "{self.repo_path}"',
                    "enabled = true",
                    'display_name = "RV Fixture"',
                    "",
                    # 只 fake 外部 agent 可执行器：claude 的 bin 指向 fake_agent，
                    # run 档改用 plain 协议（fake_agent 不产 stream-json）。
                    "[agent_runner.agents.claude]",
                    f'bin = "{FAKE_AGENT}"',
                    'label = "agent/claude"',
                    "",
                    "[agent_runner.agents.claude.profiles.run]",
                    'args = ["--rv-fake-agent"]',
                    'output_protocol = "plain"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.config_path = config

    def _init_gh_state(self) -> None:
        (self.state_dir / "issues.json").write_text(
            json.dumps({"next_issue": 100, "next_comment_id": 1, "issues": {}}), encoding="utf-8"
        )

    # ── 环境 / 子进程 ─────────────────────────────────────────────────────

    def env(self, **overrides: str | None) -> dict[str, str]:
        child = dict(os.environ)
        child.update(
            {
                "HOME": str(self.home),
                "IAR_CONFIG": str(self.config_path),
                "IAR_SKIP_GH_AUTH_CHECK": "1",
                "IAR_PRD_SKILL_PATH": str(CONTRACT_SKILL / "SKILL.md"),
                "PATH": f"{SCRIPTS_DIR}{os.pathsep}{child['PATH']}",
                "FAKE_GH_STATE": str(self.state_dir),
                "FAKE_AGENT_PROBE": str(self.agent_probe),
            }
        )
        for key, value in overrides.items():
            if value is None:
                child.pop(key, None)
            else:
                child[key] = value
        return child

    def run_kc(self, args: list[str], *, timeout: float = 120, **env_kw) -> subprocess.CompletedProcess:
        env = self.env(**env_kw)
        return subprocess.run(
            [str(KC), *args],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    def spawn(self, args: list[str], log_path: Path, **env_kw) -> subprocess.Popen:
        env = self.env(**env_kw)
        handle = log_path.open("w", encoding="utf-8")
        return subprocess.Popen(
            [str(KC), *args],
            cwd=str(self.repo_path),
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    # ── fake gh store ─────────────────────────────────────────────────────

    def gh_state(self) -> dict:
        return json.loads((self.state_dir / "issues.json").read_text(encoding="utf-8"))

    def save_gh_state(self, state: dict) -> None:
        (self.state_dir / "issues.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def seed_issue(
        self, number: int, *, title: str, body: str = "", labels: list[str] | None = None
    ) -> None:
        state = self.gh_state()
        state["issues"][str(number)] = {
            "number": number,
            "title": title,
            "url": f"https://github.com/fake-owner/fake-repo/issues/{number}",
            "body": body,
            "labels": labels or [],
            "state": "OPEN",
            "comments": [],
        }
        state["next_issue"] = max(state["next_issue"], number + 1)
        self.save_gh_state(state)

    # ── console / HTTP ────────────────────────────────────────────────────

    def _rebind_port(self, port: int) -> None:
        """换端口并同步 config.toml，供 start_console 重试使用。"""
        self.port = port
        config_text = self.config_path.read_text(encoding="utf-8")
        self.config_path.write_text(
            re.sub(r"(?m)^port = \d+$", f"port = {port}", config_text), encoding="utf-8"
        )

    def start_console(
        self, log_path: Path, env_overrides: dict[str, str | None] | None = None
    ) -> subprocess.Popen:
        """启动 kc console 并等待就绪；绑定失败（如端口撞 TIME_WAIT）时换端口重试。"""
        attempts = 3
        ready_timeout = 40
        child_env_overrides = env_overrides or {}
        last_failure = ""
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                self._rebind_port(_free_port())
            proc = self.spawn(
                ["console", "--port", str(self.port), "--no-browser"],
                log_path,
                **child_env_overrides,
            )
            deadline = time.time() + ready_timeout
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/api/v1/agent-runner/backlog/prds?repo_id=rv-fixture",
                        timeout=2,
                    ) as response:
                        if response.status < 500:
                            return proc
                except urllib.error.HTTPError:
                    return proc
                except OSError:
                    time.sleep(0.5)
            try:
                Fixture.stop(proc)
            except Exception:
                pass
            try:
                last_failure = (
                    f"attempt {attempt}: exit={proc.returncode} "
                    + "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-5:])
                )
            except OSError:
                last_failure = f"attempt {attempt}: exit={proc.returncode}"
        raise RuntimeError(
            f"console did not come up on port {self.port} after {attempts} attempts; "
            f"last: {last_failure}"
        )

    @staticmethod
    def stop(proc: subprocess.Popen) -> None:
        # 进程组一起终止：kc console 会派生 uvicorn 子进程，仅 terminate 直接子进程
        # 会让监听端口迟迟不释放。
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait(timeout=5)

    def http(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
                return int(response.status), payload
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return int(exc.code), json.loads(raw)
            except ValueError:
                return int(exc.code), {"raw": raw}

    # ── SQLite 只读核对 ───────────────────────────────────────────────────

    def read_settings_row(self) -> dict | None:
        if not self.console_db.exists():
            return None
        connection = sqlite3.connect(f"file:{self.console_db}?mode=ro", uri=True)
        try:
            cursor = connection.execute(
                "SELECT repo_id, max_parallel, default_view FROM backlog_settings WHERE repo_id = ?",
                ("rv-fixture",),
            )
            row = cursor.fetchone()
        finally:
            connection.close()
        return None if row is None else {"repo_id": row[0], "max_parallel": row[1], "default_view": row[2]}

    # ── 探针 / 调用日志解析 ───────────────────────────────────────────────

    def probe_events(self) -> list[dict]:
        if not self.agent_probe.exists():
            return []
        return [
            json.loads(line)
            for line in self.agent_probe.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def wait_for_agent_starts(self, expected_count: int, *, timeout: float = 45) -> list[int]:
        """等待真实 CLI 子进程探针达到预期启动数，零启动场景至少观察三秒。"""
        started_at = time.monotonic()
        deadline = started_at + timeout
        while time.monotonic() < deadline:
            started_issues = sorted(
                {
                    event["issue"]
                    for event in self.probe_events()
                    if event.get("event") == "start"
                }
            )
            if len(started_issues) >= expected_count and (
                expected_count > 0 or time.monotonic() - started_at >= 3
            ):
                return started_issues
            time.sleep(0.25)
        raise TimeoutError(
            f"agent start probes {len(started_issues)} did not reach expected "
            f"{expected_count} within {timeout}s"
        )

    def max_concurrent_agents(self) -> int:
        events = self.probe_events()
        timeline = sorted(
            [(event["ts"], 1 if event["event"] == "start" else -1) for event in events]
        )
        running = peak = 0
        for _ts, delta in timeline:
            running += delta
            peak = max(peak, running)
        return peak

    def gh_call_lines(self) -> list[dict]:
        if not self.gh_calls.exists():
            return []
        return [
            json.loads(line)
            for line in self.gh_calls.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def write_pending_prd(fixture: Fixture, relative_path: str, title: str, *, issue_number: int | None = None) -> Path:
    prd_path = fixture.repo_path / relative_path
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    issue_line = (
        f"- GitHub Issue: https://github.com/fake-owner/fake-repo/issues/{issue_number}"
        if issue_number
        else "- GitHub Issue: (to be created)"
    )
    prd_path.write_text(
        f"# PRD: {title}\n\n{issue_line}\n\n## Acceptance Checklist\n\n- [ ] demo item\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(fixture.repo_path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(fixture.repo_path), "commit", "-q", "-m", f"prd {title}"], check=True
    )
    return prd_path


def evidence_path(filename: str) -> Path:
    return EVIDENCE_DIR / filename


def write_text(filename: str, text: str) -> Path:
    path = evidence_path(filename)
    path.write_text(text, encoding="utf-8")
    return path


def python_bin() -> str:
    return sys.executable

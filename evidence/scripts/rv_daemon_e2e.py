#!/usr/bin/env python3
"""rv-1 / rv-2 / rv-3 的真实入口证据采集。

真实 ``kc daemon run`` 子进程 + 真实配置加载 + 真实认领仲裁 + 真实对账/补位调度；
只把 GitHub 换成 PATH 上的文件型假 ``gh``，agent CLI 换成可控时长的确定性假 agent。

用法：uv run python tasks/evidence/<prd-stem>/scripts/rv_daemon_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
PRD_DIR = HERE.parents[1]
REPO_ROOT = HERE.parents[4]
EVIDENCE_DIR = PRD_DIR
WORK_ROOT = Path(os.environ.get("RV_WORK_ROOT", "/tmp/kc-rv-parallel-limit"))
KC = REPO_ROOT / ".venv" / "bin" / "kc"
FAKE_GH = HERE.parent / "fake_gh.py"
TREE = subprocess.run(
    ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD^{tree}"],
    capture_output=True, text=True, check=True,
).stdout.strip()
COMMIT = subprocess.run(
    ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
    capture_output=True, text=True, check=True,
).stdout.strip()

RESULTS: list[tuple[str, str, bool, str]] = []
LINES: list[str] = []


def record(rv: str, name: str, ok: bool, detail: str) -> None:
    """记录一条断言结果并立刻打印。"""
    RESULTS.append((rv, name, ok, detail))
    line = f"[{'PASS' if ok else 'FAIL'}] {rv} {name}: {detail}"
    print(line)
    LINES.append(line)


def log(message: str) -> None:
    """打印并留档过程说明。"""
    print(message)
    LINES.append(message)


def free_port() -> int:
    """取一个空闲本地端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write(path: Path, content: str, mode: int | None = None) -> None:
    """写文件，必要时补执行位。"""
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def prepare(
    name: str,
    *,
    limit: int,
    max_issues: int | None = None,
    interval: int = 4,
    seeds: list[int] | None = None,
) -> dict:
    """搭一个隔离工作根：home / config / git 仓库 / 假 gh / 假 agent / 初始 Issue。"""
    work = WORK_ROOT / name
    if work.exists():
        shutil.rmtree(work)
    for sub in ("home/.kedacode", "state", "bin", "repo"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    real_skills = Path.home() / ".kedacode" / "skills" / "prd"
    if real_skills.is_dir():
        shutil.copytree(real_skills, work / "home" / ".kedacode" / "skills" / "prd")

    write(work / "bin" / "gh", f'#!/bin/sh\nexec {sys.executable} "{FAKE_GH}" "$@"\n', 0o755)
    write(
        work / "bin" / "rve2e-agent",
        """#!/bin/sh
echo "invoked: $*" >> "$RV_AGENT_LOG"
number=$(printf '%s' "$*" | grep -o 'ISSUE-MARK-[0-9]*' | head -1 | sed 's/ISSUE-MARK-//')
[ -z "$number" ] && number=0
deadline=$(( $(date +%s) + 300 ))
while [ ! -f "$RV_RELEASE_DIR/release-$number" ]; do
  [ "$(date +%s)" -gt "$deadline" ] && break
  sleep 0.2
done
echo '{"type":"system","subtype":"init","session_id":"fake-session"}'
echo '{"type":"result","subtype":"success","is_error":false,"duration_ms":10,"result":"done"}'
exit 0
""",
        0o755,
    )

    runner_extra = "max_recovery_attempts = 0\nagent_fallback_order = []\nmax_agent_switches = 0\n"
    if max_issues is not None:
        runner_extra += f"max_issues = {max_issues}\n"
    write(
        work / "config.toml",
        f"""[agent_runner.repositories.rve2e]
path = "{work / 'repo'}"
enabled = true
github_repo = "fake/rve2e"

[agent_runner.runner]
max_concurrent_issues = {limit}
{runner_extra}
[agent_runner.agents.claude]
bin = "rve2e-agent"
""",
    )

    repo = work / "repo"
    write(
        repo / ".kedacode.toml",
        """[agent_runner.repository]
id = "rve2e"
enabled = true
github_repo = "fake/rve2e"

[agent_runner.git]
remote = "origin"
base_branch = "main"

[agent_runner.runner]
default_agent = "claude"
""",
    )
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=e2e@example.invalid",
         "-c", "user.name=e2e", "add", "-A"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=e2e@example.invalid",
         "-c", "user.name=e2e", "commit", "-q", "-m", "init"], check=True
    )
    origin = work / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run([*git, "remote", "add", "origin", str(origin)], check=True)
    subprocess.run([*git, "push", "-q", "origin", "main"], check=True)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    issues = [
        {"number": number, "title": f"Task {number}", "body": f"ISSUE-MARK-{number}",
         "state": "open", "labels": ["agent/ready", "type/refactor"],
         "created_at": now, "updated_at": now}
        for number in (seeds or [101])
    ]
    write(
        work / "state" / "gh_store.json",
        json.dumps({"issues": issues, "labels": [], "comments": []}, ensure_ascii=False, indent=2),
    )

    env = dict(os.environ)
    env.update({
        "HOME": str(work / "home"),
        "KEDACODE_CONFIG": str(work / "config.toml"),
        "FAKE_GH_STATE": str(work / "state"),
        "FAKE_GH_REPO": "fake/rve2e",
        "PATH": f"{work / 'bin'}{os.pathsep}{env['PATH']}",
        "RV_AGENT_LOG": str(work / "state" / "agent-calls.txt"),
        "RV_RELEASE_DIR": str(work / "state"),
        "PYTHONUNBUFFERED": "1",
    })
    return {"work": work, "repo": repo, "env": env, "interval": interval,
            "log": work / "state" / "daemon.log"}


def store(env: dict) -> dict:
    """读假 GitHub 存储。"""
    return json.loads((Path(env["env"]["FAKE_GH_STATE"]) / "gh_store.json").read_text(encoding="utf-8"))


def mutate(env: dict, callback) -> None:
    """就地修改假 GitHub 存储并落盘。"""
    path = Path(env["env"]["FAKE_GH_STATE"]) / "gh_store.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    callback(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_ready(env: dict, *numbers: int) -> None:
    """把若干 Issue 以 agent/ready 状态放进队列。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def callback(data: dict) -> None:
        for number in numbers:
            if any(issue["number"] == number for issue in data["issues"]):
                continue
            data["issues"].append({
                "number": number, "title": f"Task {number}", "body": f"ISSUE-MARK-{number}",
                "state": "open", "labels": ["agent/ready", "type/refactor"],
                "created_at": now, "updated_at": now,
            })

    mutate(env, callback)


def add_label(env: dict, number: int, label: str) -> None:
    """给 Issue 加标签。"""
    def callback(data: dict) -> None:
        for issue in data["issues"]:
            if issue["number"] == number and label not in issue["labels"]:
                issue["labels"].append(label)
    mutate(env, callback)


def claim_count(env: dict, number: int) -> int:
    """统计 Issue 上的认领评论条数。"""
    return sum(
        1 for comment in store(env)["comments"]
        if comment["issue"] == number and "Agent Runner Claimed" in comment["body"]
    )


def labels_of(env: dict, number: int) -> list[str]:
    """读 Issue 当前标签。"""
    for issue in store(env)["issues"]:
        if issue["number"] == number:
            return list(issue["labels"])
    return []


def running_numbers(env: dict) -> list[int]:
    """当前带 agent/running 的本机 Issue 编号。"""
    return [issue["number"] for issue in store(env)["issues"] if "agent/running" in issue["labels"]]


def wait_until(predicate, timeout: float, description: str, poll: float = 0.4) -> bool:
    """轮询等待条件成立。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    log(f"TIMEOUT waiting for {description}")
    return False


class Daemon:
    """真实 daemon 子进程。"""

    def __init__(self, env: dict, *, extra_args: list[str] | None = None) -> None:
        self.env = env
        self.extra_args = extra_args or []
        self.process: subprocess.Popen | None = None

    def __enter__(self) -> "Daemon":
        log_path = Path(self.env["log"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(KC), "daemon", "run", "--interval", str(self.env["interval"]),
             "--agent", "claude", *self.extra_args],
            cwd=self.env["repo"], env=self.env["env"], stdout=self.handle,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        return self

    def text(self) -> str:
        """读当前 daemon 日志。"""
        return Path(self.env["log"]).read_text(encoding="utf-8", errors="replace")

    def __exit__(self, *_exc) -> None:
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.handle.close()


def release(env: dict, number: int) -> None:
    """让某个 Issue 的假 agent 结束。"""
    (Path(env["env"]["RV_RELEASE_DIR"]) / f"release-{number}").write_text("go", encoding="utf-8")


def scenario_rv1() -> None:
    """rv-1：A 在跑时 B 在一个轮询间隔内被领取，C 等空位；各 Issue 只被认领一次。"""
    env = prepare("rv1", limit=2, interval=4)
    with Daemon(env) as daemon:
        if not wait_until(lambda: "agent/running" in labels_of(env, 101), 40, "A claimed"):
            record("rv-1", "A 被领取", False, "40s 内 A 未进入 agent/running")
            return
        log(f"rv-1 A claimed at {time.strftime('%H:%M:%S')}")
        ready_at = time.time()
        add_ready(env, 103, 104)
        b_claimed = wait_until(lambda: "agent/running" in labels_of(env, 103), 20, "B claimed")
        delay = time.time() - ready_at
        record("rv-1", "B 在一个轮询间隔内被领取", b_claimed and delay <= env["interval"] + 10,
               f"B 就绪后 {delay:.1f}s 被领取（上限 4s 轮询 + 处理开销）")
        record("rv-1", "A 仍在执行", "agent/running" in labels_of(env, 101),
               f"A 标签={labels_of(env, 101)}")
        record("rv-1", "C 在空位出现前保持就绪",
               "agent/running" not in labels_of(env, 104) and claim_count(env, 104) == 0,
               f"C 标签={labels_of(env, 104)} 认领评论={claim_count(env, 104)}")
        release(env, 101)
        c_claimed = wait_until(lambda: "agent/running" in labels_of(env, 104), 60, "C claimed")
        record("rv-1", "A 结束后的下一次轮询领取 C", c_claimed,
               f"C 标签={labels_of(env, 104)}")
        log(f"rv-1 C claimed at {time.strftime('%H:%M:%S')}")
        counts = {number: claim_count(env, number) for number in (101, 103, 104)}
        record("rv-1", "每个 Issue 认领评论各 1 条", set(counts.values()) == {1}, f"{counts}")
        text = daemon.text()
        passes = text.count("Daemon pass for repository")
        limits = text.count("Parallel limit for")
        record("rv-1", "每次轮询都有 Parallel limit 行", passes > 0 and limits >= passes,
               f"pass 行 {passes} 条，Parallel limit 行 {limits} 条")
        record("rv-1", "补位期间没有重复处理",
               text.count("Issue #101 finished") <= 1 and "claim arbitration lost" not in text,
               "无重复认领 / 仲裁失败记录")


def scenario_rv2a() -> None:
    """rv-2(a)：本机在执行的 Issue 不被返工通道或对账重复处理。"""
    env = prepare("rv2a", limit=2, interval=4)
    with Daemon(env) as daemon:
        if not wait_until(lambda: "agent/running" in labels_of(env, 101), 40, "A claimed"):
            record("rv-2", "返工通道跳过在执行 Issue", False, "A 未被领取，无法验证")
            return
        add_label(env, 101, "agent/rework-prd")
        time.sleep(env["interval"] * 2 + 3)
        text = daemon.text()
        record("rv-2", "返工标记不触发重复认领", claim_count(env, 101) == 1,
               f"A 认领评论={claim_count(env, 101)}")
        record("rv-2", "对账不把在执行的 A 当僵尸",
               "Stale Attempt Reconciled" not in text and "Agent Runner Failed" not in text,
               "无对账/失败评论，日志无 stale-attempt 处理记录")
        release(env, 101)


def scenario_rv2b() -> None:
    """rv-2(b)：统计运行中数量失败时本轮不领取新 Issue，已在执行的不受影响。"""
    env = prepare("rv2b", limit=2, interval=4)
    with Daemon(env) as daemon:
        if not wait_until(lambda: "agent/running" in labels_of(env, 101), 40, "A claimed"):
            record("rv-2", "计数失败时本轮不领取", False, "A 未被领取，无法验证")
            return
        faults = Path(env["env"]["FAKE_GH_STATE"]) / "faults.json"
        faults.write_text(json.dumps({"fail_running_list": 1}), encoding="utf-8")
        add_ready(env, 105)
        failed = wait_until(
            lambda: "claiming zero new Issues this pass" in daemon.text(), 30, "fail-closed log"
        )
        record("rv-2", "计数失败时本轮不领取", failed and claim_count(env, 105) == 0,
               f"fail-closed 日志={failed} 新 Issue 认领评论={claim_count(env, 105)}")
        record("rv-2", "已在执行的 Issue 不受影响", "agent/running" in labels_of(env, 101),
               f"A 标签={labels_of(env, 101)}")
        recovered = wait_until(lambda: "agent/running" in labels_of(env, 105), 40, "105 claimed")
        record("rv-2", "下一次正常轮询恢复领取", recovered, f"105 标签={labels_of(env, 105)}")
        release(env, 101)
        release(env, 105)


def scenario_rv2c() -> None:
    """rv-2(c)：旧 max_issues 只提示一次且不改变同时运行数（上限仍取 max_concurrent_issues）。"""
    env = prepare("rv2c", limit=1, max_issues=5, interval=3, seeds=[201, 202])
    with Daemon(env) as daemon:
        samples: list[int] = []
        deadline = time.time() + 16
        while time.time() < deadline:
            samples.append(len(running_numbers(env)))
            time.sleep(1)
        text = daemon.text()
        record("rv-2", "旧 max_issues 只提示一次", text.count("max_issues 已退役") == 1,
               f"弃用提示 {text.count('max_issues 已退役')} 条")
        record("rv-2", "旧配置下同时运行数不超过 1", max(samples) <= 1,
               f"采样最大并发={max(samples)} 样本={samples}")
        record("rv-2", "生效上限取 max_concurrent_issues",
               "limit=1 source=config" in text and "limit=5" not in text,
               "日志 limit=1 source=config")
        for number in (201, 202):
            release(env, number)


def scenario_rv2d() -> None:
    """rv-2(d)：daemon 拒绝 --max-issues，退出码 2 并提示改用 --concurrency。"""
    env = prepare("rv2d", limit=2)
    completed = subprocess.run(
        [str(KC), "daemon", "run", "--max-issues", "3"],
        cwd=env["repo"], env=env["env"], capture_output=True, text=True, timeout=60,
    )
    output = completed.stdout + completed.stderr
    record("rv-2", "daemon 拒绝 --max-issues",
           completed.returncode == 2 and "--concurrency" in output,
           f"exit={completed.returncode} 输出片段={output.strip().splitlines()[-1][:80] if output.strip() else ''}")


def http_json(url: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    """对隔离管理终端发一次 JSON 请求。"""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def scenario_rv3log() -> None:
    """rv-3 的 daemon 侧闭环：页面保存的上限成为真实生效值（limit=4 source=policy）。"""
    env = prepare("rv3log", limit=2, interval=4)
    port = free_port()
    console_log = Path(env["work"]) / "state" / "console.log"
    handle = console_log.open("w", encoding="utf-8")
    console = subprocess.Popen(
        [str(KC), "console", "--port", str(port), "--no-browser"],
        cwd=env["repo"], env=env["env"], stdout=handle, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        base = f"http://127.0.0.1:{port}/api/v1/agent-runner/backlog/settings?repo_id=rve2e"
        if not wait_until(lambda: _http_ok(base), 90, "console ready"):
            record("rv-3", "页面设置成为 daemon 生效值", False, "管理终端未在 90s 内就绪")
            return
        before = http_json(base)
        saved = http_json(base, method="PATCH", payload={"max_parallel": 4})
        record("rv-3", "页面保存后接口报告页面设置",
               saved.get("effective_max_parallel") == 4 and saved.get("ceiling_source") == "policy",
               f"保存前 {before.get('effective_max_parallel')}/{before.get('ceiling_source')} → "
               f"保存后 {saved.get('effective_max_parallel')}/{saved.get('ceiling_source')}")
        with Daemon(env) as daemon:
            ok = wait_until(lambda: "limit=4 source=policy" in daemon.text(), 40, "daemon uses policy")
            record("rv-3", "daemon 按下一次轮询采用页面设置", ok,
                   "日志出现 limit=4 source=policy" if ok else daemon.text()[-400:])
        restored = http_json(base, method="PATCH", payload={"max_parallel": None})
        record("rv-3", "恢复为配置文件值后回到 2（配置文件）",
               restored.get("effective_max_parallel") == 2 and restored.get("ceiling_source") == "config",
               f"恢复后 {restored.get('effective_max_parallel')}/{restored.get('ceiling_source')}")
    finally:
        try:
            os.killpg(console.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            console.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(console.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        handle.close()


def _http_ok(url: str) -> bool:
    try:
        http_json(url)
        return True
    except Exception:  # noqa: BLE001 - 就绪探测，任何异常都当作未就绪
        return False


SCENARIOS = {
    "rv1": (scenario_rv1, "rv-1-backfill.txt", "rv-1"),
    "rv2a": (scenario_rv2a, "rv-2-safety.txt", "rv-2"),
    "rv2b": (scenario_rv2b, "rv-2-safety.txt", "rv-2"),
    "rv2c": (scenario_rv2c, "rv-2-safety.txt", "rv-2"),
    "rv2d": (scenario_rv2d, "rv-2-safety.txt", "rv-2"),
    "rv3": (scenario_rv3log, "rv-3-daemon-log.txt", "rv-3"),
}


def main() -> int:
    """跑选定场景（默认全部）并写证据文件。"""
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    chosen = sys.argv[1:] or list(SCENARIOS)
    for filename in {SCENARIOS[key][1] for key in chosen}:
        (EVIDENCE_DIR / filename).unlink(missing_ok=True)
    for key in chosen:
        scenario, filename, rv_id = SCENARIOS[key]
        LINES.clear()
        scenario()
        _emit(filename, rv_id)
    failures = [item for item in RESULTS if not item[2]]
    print(f"\n==={len(RESULTS) - len(failures)}/{len(RESULTS)} PASS, tree={TREE}===")
    return 1 if failures else 0


def _emit(filename: str, rv: str) -> None:
    """把一个 rv 的记录追加进证据文件，文件头带 tree 与生成时间。"""
    path = EVIDENCE_DIR / filename
    header = [
        f"# {filename}",
        f"# git tree: {TREE} (HEAD {COMMIT})",
        f"# generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
        f"## {rv}",
    ]
    body = header + LINES + [""]
    with path.open("a" if path.exists() else "w", encoding="utf-8") as handle:
        handle.write("\n".join(body) + "\n")


if __name__ == "__main__":
    sys.exit(main())


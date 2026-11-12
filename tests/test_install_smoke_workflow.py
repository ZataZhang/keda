"""install-smoke 工作流里 console 探活步骤的行为契约测试。

这些探活步骤都在后台拉起 `kc console`，然后轮询健康检查端点。后台进程会继承
步骤的 stdout 管道：如果步骤结束时不回收它，runner 必须等管道关闭才认为步骤
结束，于是「面板起得来」也会把 job 拖到超时——而超时/取消的 job 结论是
`cancelled` 且日志一律不落盘，等于把一次成功的验证变成不可诊断的失败。

本测试直接把工作流 YAML 里的真实脚本抽出来，配上桩 `kc` / `curl` 执行，
断言脚本既快速结束、又确实回收了后台进程，而不是只做字符串匹配。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "install-smoke.yml"
PROBE_STEP_NAME = "Probe the installed console"
PROBE_LINGER_SECONDS = 30

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.name != "posix",
    reason="探活脚本是 bash + POSIX 进程语义，仅在类 Unix 环境执行",
)


def _load_probe_script() -> str:
    """取出 stock-python 档探活步骤的真实 shell 脚本。"""
    workflow_document = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow_document["jobs"]["stock-python"]["steps"]
    probe_steps = [step for step in steps if step.get("name") == PROBE_STEP_NAME]
    assert len(probe_steps) == 1, f"stock-python 档应当恰有一个 {PROBE_STEP_NAME!r} 步骤"
    return probe_steps[0]["run"]


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    """在桩目录里放一个可执行脚本。"""
    stub_path = bin_dir / name
    stub_path.write_text(body, encoding="utf-8")
    stub_path.chmod(0o755)


def _process_is_alive(process_id: int) -> bool:
    """判断进程是否仍然存在（不发送实际信号）。"""
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until_gone(process_id: int, timeout_seconds: float = 5.0) -> bool:
    """在有界时间内等待进程消失，返回最终是否消失。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _process_is_alive(process_id):
            return True
        time.sleep(0.05)
    return not _process_is_alive(process_id)


def test_console_probe_reaps_background_process(tmp_path: Path) -> None:
    """探活步骤必须在结束时回收后台 console，否则 runner 会一直等 stdout 关闭。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pid_file = tmp_path / "console.pid"

    # 桩 kc：`--version` 正常返回；`console` 记录自身 pid 后用 exec 变成长时间
    # 存活、且持续占用 stdout 的进程，模拟真实的 `kc console` 服务。
    _write_stub(
        bin_dir,
        "kc",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                'if [ "${1:-}" = "--version" ]; then',
                '  echo "kc 0.0.0"',
                "  exit 0",
                "fi",
                'echo $$ > "${KEDA_PROBE_PID_FILE}"',
                f"exec sleep {PROBE_LINGER_SECONDS}",
                "",
            ]
        ),
    )
    # 桩 curl：健康检查立即成功，脚本会走到 exit 0 的成功分支。
    _write_stub(bin_dir, "curl", "#!/usr/bin/env bash\nexit 0\n")

    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "KEDA_PROBE_PID_FILE": str(pid_file),
    }

    try:
        probe_result = subprocess.run(
            ["bash", "-c", _load_probe_script()],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=PROBE_LINGER_SECONDS / 2,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "console 探活步骤在后台进程存活期间没有结束："
            "后台 kc console 未被回收，runner 会一直等待 stdout 关闭直到 job 超时。"
        )

    assert probe_result.returncode == 0, probe_result.stdout + probe_result.stderr
    assert "console reachable" in probe_result.stdout

    console_pid = int(pid_file.read_text(encoding="utf-8").strip())
    console_reaped = _wait_until_gone(console_pid)
    # 兜底清理必须放在断言之前：断言失败恰恰是子进程还活着的时候，把清理写在
    # 断言之后会让这条路径永远执行不到，反过来给测试自己留下孤儿进程。
    if not console_reaped:
        os.kill(console_pid, signal.SIGKILL)
    orphan_message = f"后台 console 进程 {console_pid} 结束后仍然存活：缺少 EXIT trap 回收"
    assert console_reaped, orphan_message

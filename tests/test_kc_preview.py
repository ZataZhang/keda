"""`kc preview` 的按需项目预览契约（Issue #256 / FR-3，rv-1 自动化侧）。

这些用例跑**真实子进程与真实进程组**：假 dev server 是本机起的 Python 进程，
它绑定回环端口、自报地址、并 fork 出一个孙进程用来验证「整组退出」。
只有 mock 掉的对象是 provider 与 GitHub，预览的 argv 精确性、回环主机校验、
ready 超时后的自杀式回收、以及「身份不可证实就拒绝发信号」都走真实路径。

对应 PRD §7.6 rv-1 的 negative_control（未确认唯一候选、地址非回环、进程未就绪）
与 Architecture Acceptance「停止动作只影响 KC 持有的 process group」。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.core.shared.interfaces.agent_session import PreviewStartOutcome, PreviewStartRequest
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_session import (
    AgentSessionConfig,
    PreviewProfile,
    PreviewProcessRecord,
    PreviewState,
    require_loopback_preview_url,
)
from backend.core.use_cases.agent_session_preview import (
    PreviewCommandResolution,
    discover_preview_candidates,
    run_preview_start,
    run_preview_status,
    run_preview_stop,
)
from backend.infrastructure import preview_process_manager
from backend.infrastructure.process_identity import process_start_time
from backend.infrastructure.preview_process_manager import (
    SubprocessPreviewProcessManager,
    clear_preview_record,
    read_preview_record,
    write_preview_record,
)

# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------

#: 假 dev server 脚本：绑定回环端口、自报地址、拉起一个同组孙进程后长睡。
_FAKE_DEV_SERVER = """
import socket, subprocess, sys, time

port = int(sys.argv[1])
mode = sys.argv[2] if len(sys.argv) > 2 else "loopback"
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
print(f"grandchild_pid={grandchild.pid}", flush=True)

if mode == "silent":
    # 不监听、不报地址：模拟启动即挂住或需要额外配置的 dev 命令。
    time.sleep(120)

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", port))
server.listen(8)
if mode == "lan":
    # 只报可路由地址（很多真 dev server 会同时打印局域网地址）。
    print(f"Network: http://10.20.30.40:{port}/", flush=True)
else:
    print(f"  - Local:   http://127.0.0.1:{port}/", flush=True)
while True:
    time.sleep(0.2)
"""


@pytest.fixture
def isolated_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``~/.kedacode`` 指向临时目录，注册表与日志不污染本机状态。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """一个目标仓库根（预览只按 cwd 认身份，无需真实 git）。"""
    repo_path = tmp_path / "target-repo"
    repo_path.mkdir()
    return repo_path


@pytest.fixture
def dev_server_argv(tmp_path: Path) -> tuple[str, ...]:
    """写出假 dev server 脚本并返回精确 argv（脚本 + 端口 + 模式）。"""
    script_path = tmp_path / "fake_dev_server.py"
    script_path.write_text(_FAKE_DEV_SERVER, encoding="utf-8")
    return (sys.executable, str(script_path), str(_free_loopback_port()))


def _free_loopback_port() -> int:
    """取一个当前空闲的回环端口（探测与释放之间可能有竞争，测试里可接受）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        return int(probe_socket.getsockname()[1])


def _config_with_preview(preview: PreviewProfile) -> AppConfig:
    """构造只带预览 profile 的生效配置（其余走默认）。"""
    return AppConfig(agent_session=AgentSessionConfig(preview=preview))


class RecordingPreviewManager(SubprocessPreviewProcessManager):
    """记录调用参数的预览管理器（用于断言「未确认时端口一次都没被调用」）。"""

    def __init__(self) -> None:
        self.start_requests: list[tuple[str, ...]] = []

    def start_preview(self, request) -> PreviewStartOutcome:  # type: ignore[override]
        self.start_requests.append(tuple(request.argv))
        return super().start_preview(request)


def _pid_alive(process_pid: int) -> bool:
    """信号 0 探活（不回收、不发消息）。"""
    try:
        os.kill(process_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_alive(process_group: int) -> bool:
    """进程组里是否还有活着的成员。"""
    try:
        os.killpg(process_group, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _grandchild_pid_from_log(log_path: Path) -> int | None:
    """从预览日志里读出孙进程 pid（证明整组被回收的证据）。"""
    for _attempt in range(40):
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            for line in log_text.splitlines():
                if line.startswith("grandchild_pid="):
                    return int(line.split("=", 1)[1])
        time.sleep(0.1)
    return None


# ---------------------------------------------------------------------------
# 精确 argv：配置声明 or 用户逐字确认，其余一律不启动
# ---------------------------------------------------------------------------


def test_declared_preview_profile_argv_is_used_verbatim(
    isolated_state_home: Path, repo_root: Path, dev_server_argv: tuple[str, ...]
) -> None:
    """仓库声明了 argv 时，启动的就是那条精确 argv，不经 shell 拆分。"""
    manager = RecordingPreviewManager()
    config = _config_with_preview(PreviewProfile(argv=dev_server_argv, ready_timeout_seconds=30))

    outcome = run_preview_start(
        config=config, repo_root=repo_root, confirmed_argv=None, preview_manager=manager
    )

    try:
        assert outcome.exit_ok is True
        assert outcome.state == PreviewState.READY
        assert outcome.url == f"http://127.0.0.1:{dev_server_argv[2]}/"
        assert manager.start_requests == [tuple(dev_server_argv)]
    finally:
        manager.stop_preview(repo_root=repo_root)


def test_unconfirmed_unique_candidate_starts_no_process(
    isolated_state_home: Path, repo_root: Path
) -> None:
    """无 profile 且只发现一个候选时，必须先交用户确认，一次进程都不启动。"""
    (repo_root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (repo_root / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8"
    )
    manager = RecordingPreviewManager()
    config = _config_with_preview(PreviewProfile())

    outcome = run_preview_start(
        config=config, repo_root=repo_root, confirmed_argv=None, preview_manager=manager
    )

    assert outcome.exit_ok is False
    assert outcome.state == PreviewState.NONE
    assert outcome.url is None
    assert "No process was started" in outcome.message
    assert manager.start_requests == []
    assert read_preview_record(repo_root) is None
    #: 下一步命令必须可直接执行，agent 才能把确认动作原样转给用户。
    assert outcome.next_command == 'kc preview start --confirm "pnpm run dev"'


def test_candidate_must_be_confirmed_verbatim(isolated_state_home: Path, repo_root: Path) -> None:
    """确认文本与发现的候选不等价时拒绝启动，防止「确认 A 实跑 B」。"""
    (repo_root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (repo_root / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8"
    )
    manager = RecordingPreviewManager()
    config = _config_with_preview(PreviewProfile())

    mismatched = run_preview_start(
        config=config,
        repo_root=repo_root,
        confirmed_argv="pnpm run dev -- --host 0.0.0.0",
        preview_manager=manager,
    )

    assert mismatched.exit_ok is False
    assert "Refusing to start anything" in mismatched.message
    assert manager.start_requests == []

    confirmed = run_preview_start(
        config=config,
        repo_root=repo_root,
        confirmed_argv="pnpm run dev",
        preview_manager=manager,
    )

    #: 逐字相等才会把候选 argv 交给端口层（这里 pnpm 不存在，进程根本起不来，
    #: 但断言的是 argv 决策，不是 pnpm 是否安装）。
    assert manager.start_requests == [("pnpm", "run", "dev")]
    assert confirmed.exit_ok is False


def test_ambiguous_candidates_never_start_a_process(
    isolated_state_home: Path, repo_root: Path
) -> None:
    """候选不唯一（dev + start 都在）时报数量并拒启动，绝不猜默认。"""
    (repo_root / "package-lock.json").write_text("{}", encoding="utf-8")
    (repo_root / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite", "start": "node server.js"}}),
        encoding="utf-8",
    )
    manager = RecordingPreviewManager()
    config = _config_with_preview(PreviewProfile())

    outcome = run_preview_start(
        config=config, repo_root=repo_root, confirmed_argv=None, preview_manager=manager
    )

    assert outcome.exit_ok is False
    assert manager.start_requests == []
    resolution: PreviewCommandResolution | None = outcome.resolution
    assert resolution is not None and resolution.candidates == 2
    assert "Declare [agent_session.preview].argv" in outcome.message


def test_candidate_discovery_requires_a_lockfile(
    isolated_state_home: Path, repo_root: Path
) -> None:
    """没有锁文件时不猜包管理器（宁可不给候选，也不凭空造一条命令）。"""
    (repo_root / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8"
    )

    assert discover_preview_candidates(repo_root) == ()


# ---------------------------------------------------------------------------
# 回环边界与 ready 超时：非本机地址不作为就绪证据
# ---------------------------------------------------------------------------


def test_lan_only_address_is_not_ready_and_group_is_reclaimed(
    isolated_state_home: Path, repo_root: Path, tmp_path: Path
) -> None:
    """进程只自报可路由地址时不算就绪：超时后 KC 终止**自己**刚启动的那个组。"""
    script_path = tmp_path / "lan_dev_server.py"
    script_path.write_text(_FAKE_DEV_SERVER, encoding="utf-8")
    port = _free_loopback_port()
    manager = SubprocessPreviewProcessManager()
    config = _config_with_preview(
        PreviewProfile(
            argv=(sys.executable, str(script_path), str(port), "lan"),
            ready_timeout_seconds=3,
        )
    )

    outcome = run_preview_start(
        config=config, repo_root=repo_root, confirmed_argv=None, preview_manager=manager
    )

    assert outcome.exit_ok is False
    assert "loopback" in outcome.message or "did not report" in outcome.message
    assert read_preview_record(repo_root) is None
    log_files = list((isolated_state_home / ".kedacode" / "preview").glob("*.log"))
    grandchild_pid = _grandchild_pid_from_log(log_files[0]) if log_files else None
    assert grandchild_pid is not None
    assert not _pid_alive(grandchild_pid), "孙进程必须随整组退出，不能留下孤儿"


def test_silent_dev_command_times_out_without_url(
    isolated_state_home: Path, repo_root: Path, tmp_path: Path
) -> None:
    """dev 命令不报地址也不监听时按超时处理，不回复任何 URL。"""
    script_path = tmp_path / "silent_dev_server.py"
    script_path.write_text(_FAKE_DEV_SERVER, encoding="utf-8")
    port = _free_loopback_port()
    manager = SubprocessPreviewProcessManager()
    config = _config_with_preview(
        PreviewProfile(
            argv=(sys.executable, str(script_path), str(port), "silent"),
            ready_timeout_seconds=2,
        )
    )

    outcome = run_preview_start(
        config=config, repo_root=repo_root, confirmed_argv=None, preview_manager=manager
    )

    assert outcome.exit_ok is False
    assert outcome.url is None
    assert "ready_timeout_seconds" in outcome.message


def test_non_loopback_ready_url_is_rejected_before_any_spawn(
    isolated_state_home: Path, repo_root: Path, dev_server_argv: tuple[str, ...]
) -> None:
    """配置里的 ready_url 指向可路由主机时，端口层在启动进程前就拒绝。"""
    from backend.core.shared.interfaces.agent_session import PreviewStartRequest

    manager = SubprocessPreviewProcessManager()
    outcome = manager.start_preview(
        PreviewStartRequest(
            argv=tuple(dev_server_argv),
            cwd=repo_root,
            ready_url="http://10.20.30.40:3000/",
            ready_timeout_seconds=5,
        )
    )

    assert outcome.started is False
    assert "loopback" in outcome.message
    assert read_preview_record(repo_root) is None
    #: 未启动任何进程的证据：状态目录里没有该仓库的日志/注册表产物。
    state_dir = isolated_state_home / ".kedacode" / "preview"
    assert not state_dir.exists() or list(state_dir.iterdir()) == []


@pytest.mark.parametrize(
    "url_text",
    (
        "http://user@127.0.0.1:3000/",
        "http://user:secret@localhost:3000/",
    ),
)
def test_preview_url_rejects_embedded_credentials(url_text: str) -> None:
    """loopback 地址也不能把 URL 凭据回显到执行器对话中。"""
    with pytest.raises(ValueError, match="must not contain user information or credentials"):
        require_loopback_preview_url(url_text, source="test")


# ---------------------------------------------------------------------------
# 所有权：只停自己登记且身份可证实的进程组
# ---------------------------------------------------------------------------


def test_stop_terminates_the_whole_owned_group(
    isolated_state_home: Path, repo_root: Path, dev_server_argv: tuple[str, ...]
) -> None:
    """start → status(ready) → stop 闭环：整组退出、记录清除、端口不再监听。"""
    manager = SubprocessPreviewProcessManager()
    config = _config_with_preview(PreviewProfile(argv=dev_server_argv, ready_timeout_seconds=30))
    started = run_preview_start(
        config=config, repo_root=repo_root, confirmed_argv=None, preview_manager=manager
    )
    assert started.exit_ok is True
    record = read_preview_record(repo_root)
    assert record is not None
    log_files = list((isolated_state_home / ".kedacode" / "preview").glob("*.log"))
    grandchild_pid = _grandchild_pid_from_log(log_files[0])
    process_group = record.process_group

    status = run_preview_status(repo_root=repo_root, preview_manager=manager)
    assert status.exit_ok is True
    assert status.state == PreviewState.READY
    assert status.url == f"http://127.0.0.1:{dev_server_argv[2]}/"

    stopped = run_preview_stop(repo_root=repo_root, preview_manager=manager)

    assert stopped.exit_ok is True
    assert stopped.state == PreviewState.NONE
    assert read_preview_record(repo_root) is None
    assert not _process_group_alive(process_group)
    assert not _pid_alive(grandchild_pid)


def test_second_start_reuses_slot_and_does_not_spawn_a_rival(
    isolated_state_home: Path, repo_root: Path, dev_server_argv: tuple[str, ...]
) -> None:
    """同一仓库同时只允许一个 KC 持有的预览：第二次 start 不新起进程。"""
    manager = SubprocessPreviewProcessManager()
    config = _config_with_preview(PreviewProfile(argv=dev_server_argv, ready_timeout_seconds=30))
    first = run_preview_start(
        config=config, repo_root=repo_root, confirmed_argv=None, preview_manager=manager
    )
    assert first.exit_ok is True
    first_pid = read_preview_record(repo_root).pid

    second = run_preview_start(
        config=config, repo_root=repo_root, confirmed_argv=None, preview_manager=manager
    )

    assert second.exit_ok is False
    assert "already running" in second.message
    assert read_preview_record(repo_root).pid == first_pid
    run_preview_stop(repo_root=repo_root, preview_manager=manager)


def test_stop_refuses_when_recorded_process_group_does_not_match(
    isolated_state_home: Path, repo_root: Path
) -> None:
    """pid 复用/记录被改写时拒绝发信号：现场组与记录不符就不许杀。"""
    manager = SubprocessPreviewProcessManager()
    foreign_process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        # 不加 start_new_session：该进程仍在 pytest 的组里，与记录声称的组不符。
    )
    stale_record = PreviewProcessRecord(
        key="current",
        pid=foreign_process.pid,
        process_group=foreign_process.pid,
        argv=("npm", "run", "dev"),
        cwd=repo_root,
        log_path=isolated_state_home / "unrelated.log",
        url="http://127.0.0.1:3999/",
        owner_pid=os.getpid(),
    )
    write_preview_record(repo_root, stale_record)

    status = manager.inspect_preview(repo_root=repo_root)
    stopped = manager.stop_preview(repo_root=repo_root)

    assert status.state == PreviewState.FOREIGN
    assert stopped.stopped is False
    assert stopped.killed is False
    assert "not verifiable as KedaCode-owned" in stopped.message
    assert foreign_process.poll() is None, "被拒的停止绝不能碰到那个陌生进程"
    assert read_preview_record(repo_root) is not None, "拒绝时保留记录供人诊断"
    foreign_process.kill()
    foreign_process.wait(timeout=10)


def test_live_record_with_reused_process_identity_cannot_be_stopped_or_replaced(
    isolated_state_home: Path,
    repo_root: Path,
    dev_server_argv: tuple[str, ...],
) -> None:
    """创建时刻不符的存活 pid 按 foreign 处理，既不杀也不启动第二份。"""
    manager = SubprocessPreviewProcessManager()
    foreign_process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        actual_started_at = process_start_time(foreign_process.pid)
        assert actual_started_at is not None
        stale_record = PreviewProcessRecord(
            key="current",
            pid=foreign_process.pid,
            process_group=foreign_process.pid,
            argv=("npm", "run", "dev"),
            cwd=repo_root,
            log_path=isolated_state_home / "unrelated.log",
            url="http://127.0.0.1:3999/",
            owner_pid=os.getpid(),
            process_started_at=actual_started_at + 1,
        )
        write_preview_record(repo_root, stale_record)

        status = manager.inspect_preview(repo_root=repo_root)
        start = manager.start_preview(
            PreviewStartRequest(
                argv=dev_server_argv,
                cwd=repo_root,
                ready_url=None,
                ready_timeout_seconds=1,
            )
        )
        stopped = manager.stop_preview(repo_root=repo_root)

        assert status.state == PreviewState.FOREIGN
        assert start.started is False
        assert "refusing to start a second preview" in start.message
        assert stopped.stopped is False
        assert stopped.killed is False
        assert "not verifiable as KedaCode-owned" in stopped.message
        assert foreign_process.poll() is None, "旧进程身份不符时不得触碰该 pid"
        assert read_preview_record(repo_root).pid == foreign_process.pid
    finally:
        foreign_process.kill()
        foreign_process.wait(timeout=10)


def test_stop_clears_stale_record_without_signalling(
    isolated_state_home: Path, repo_root: Path
) -> None:
    """记录里的进程早已退出时只清记录，不发任何信号（pid 可能已被复用）。"""
    manager = SubprocessPreviewProcessManager()
    dead_process = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_process.wait(timeout=30)
    write_preview_record(
        repo_root,
        PreviewProcessRecord(
            key="current",
            pid=dead_process.pid,
            process_group=dead_process.pid,
            argv=("npm", "run", "dev"),
            cwd=repo_root,
            log_path=isolated_state_home / "unrelated.log",
            url=None,
            owner_pid=os.getpid(),
        ),
    )

    status = manager.inspect_preview(repo_root=repo_root)
    stopped = manager.stop_preview(repo_root=repo_root)

    assert status.state == PreviewState.EXITED
    assert stopped.stopped is True
    assert stopped.killed is False
    assert "no signal was sent" in stopped.message
    assert read_preview_record(repo_root) is None


def test_status_without_record_is_read_only_and_ok(
    isolated_state_home: Path, repo_root: Path
) -> None:
    """没启动过时 status 仍成功返回 none（只读查询不是失败）。"""
    status = run_preview_status(
        repo_root=repo_root, preview_manager=SubprocessPreviewProcessManager()
    )

    assert status.exit_ok is True
    assert status.state == PreviewState.NONE
    assert status.url is None


def test_malformed_registry_version_is_not_trusted(
    isolated_state_home: Path, repo_root: Path
) -> None:
    """注册表版本或形状不认识时按「无记录」处理，不猜字段、不据此杀进程。"""
    registry_dir = isolated_state_home / ".kedacode" / "preview"
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_path = registry_dir / (preview_process_manager.preview_repo_slug(repo_root) + ".json")
    registry_path.write_text(
        json.dumps({"version": 999, "record": {"pid": 1234}}), encoding="utf-8"
    )

    assert read_preview_record(repo_root) is None
    outcome = SubprocessPreviewProcessManager().stop_preview(repo_root=repo_root)
    assert outcome.stopped is True
    assert outcome.killed is False


# ---------------------------------------------------------------------------
# CLI 真实入口（Typer → 用例 → 端口），含机器输出契约
# ---------------------------------------------------------------------------


def _initialized_repo(tmp_path: Path) -> Path:
    """构造带仓库配置的最小目标仓库（预览只需要 id 与 cwd）。"""
    from tests.test_agent_runner_cli import _init_iar_repo

    return _init_iar_repo(tmp_path)


def _repo_with_preview_profile(repo_path: Path, argv: tuple[str, ...]) -> None:
    """把 ``[agent_session.preview]`` 写进仓库配置文件。"""
    config_path = repo_path / ".iar.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text
        + "\n[agent_session.preview]\n"
        + f"argv = {json.dumps(list(argv))}\n"
        + "ready_timeout_seconds = 30\n",
        encoding="utf-8",
    )


def test_cli_preview_start_and_stop_json_roundtrip(
    isolated_state_home: Path, tmp_path: Path, dev_server_argv: tuple[str, ...]
) -> None:
    """真 CLI 进程走 start → status → stop：机器输出里带 state/url/argv。"""
    from backend.api.cli import main

    repo_path = _initialized_repo(tmp_path)
    _repo_with_preview_profile(repo_path, dev_server_argv)

    start_code = main(["preview", "start", "--repo", str(repo_path), "--json"])
    assert start_code == 0

    try:
        status_code = main(["preview", "status", "--repo", str(repo_path), "--json"])
        assert status_code == 0
        record = read_preview_record(repo_path)
        assert record is not None
        assert record.url == f"http://127.0.0.1:{dev_server_argv[2]}/"
        assert record.argv == tuple(dev_server_argv)
    finally:
        stop_code = main(["preview", "stop", "--repo", str(repo_path), "--json"])
    assert stop_code == 0
    assert read_preview_record(repo_path) is None


def test_cli_preview_start_without_confirmation_exits_usage_code(
    isolated_state_home: Path, tmp_path: Path
) -> None:
    """未确认唯一候选时 CLI 以用法错误退出（2），并给出可执行的重跑命令。"""
    from backend.api.cli import main

    repo_path = _initialized_repo(tmp_path)
    (repo_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (repo_path / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8"
    )

    exit_code = main(["preview", "start", "--repo", str(repo_path)])

    assert exit_code == 2
    assert read_preview_record(repo_path) is None


def test_preview_record_lives_outside_the_target_repository(
    isolated_state_home: Path, repo_root: Path, dev_server_argv: tuple[str, ...]
) -> None:
    """预览状态是本机操作态，不写进被执行的仓库。"""
    manager = SubprocessPreviewProcessManager()
    run_preview_start(
        config=_config_with_preview(PreviewProfile(argv=dev_server_argv, ready_timeout_seconds=30)),
        repo_root=repo_root,
        confirmed_argv=None,
        preview_manager=manager,
    )
    try:
        assert list(repo_root.iterdir()) == []
    finally:
        manager.stop_preview(repo_root=repo_root)
        clear_preview_record(repo_root)

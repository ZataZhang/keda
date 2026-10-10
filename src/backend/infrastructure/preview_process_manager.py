"""按需项目预览的受管进程实现（infrastructure 层）。

:class:`backend.core.shared.interfaces.agent_session.IPreviewProcessManager`
的实现端。它把「在目标仓库跑一条已批准的 dev 命令」压缩成三条硬约束：

- **精确 argv**：只按 argv 数组启动，永不经过 shell，因此配置里不可能塞进
  拼接后的 shell 文本。
- **回环地址**：ready 判定只接受进程自报或配置声明的 loopback 地址；进程打印的
  局域网地址被忽略（既不作为就绪证据，也绝不回复给终端）。地址不可确认时按
  超时处理并终止自己刚启动的进程组。
- **身份可证实才动手**：注册表同时记 pid、进程组 id、argv 与启动时刻；stop 前
  重新读取 pid 的实际进程组，任一不符就拒绝发信号。pid 复用（老进程已死、
  新进程占了同一个 pid）因此不会变成误杀。

进程组归 KC 自己持有：子进程以 ``start_new_session=True`` 启动，成为新会话的
组长，dev server 的 npm/vite 子进程都落在同一组里，停止时整组退出。

记录与日志落在本机状态目录 ``~/.kedacode/preview/`` 下，按仓库路径摘要分文件，
**不写进目标仓库**——预览是操作者本机的临时状态，不该污染被执行的工程。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

from backend.core.shared.interfaces.agent_session import (
    IPreviewProcessManager,
    PreviewStartOutcome,
    PreviewStartRequest,
    PreviewStatusOutcome,
    PreviewStopOutcome,
)
from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_session import (
    LOOPBACK_PREVIEW_HOSTS,
    PreviewProcessRecord,
    PreviewState,
    parse_preview_url,
    require_loopback_preview_url,
)
from backend.infrastructure.child_env import build_sanitized_child_env
from backend.infrastructure.logging.logger import logger
from backend.infrastructure.process_identity import process_start_time

#: 预览状态子目录名（位于本机状态目录下）。
PREVIEW_STATE_SUBDIR = "preview"
#: 注册表单槽位键：同一仓库同时只允许一个 KC 持有的预览进程。
PREVIEW_RECORD_KEY = "current"
#: 注册表文件格式版本（读到时不匹配即视为无法解析，不猜测字段）。
PREVIEW_REGISTRY_VERSION = 1

_READY_POLL_INTERVAL_SECONDS = 0.25
_READY_CONNECT_TIMEOUT_SECONDS = 1.0
_TERMINATE_GRACE_SECONDS = 5.0
_TERMINATE_POLL_INTERVAL_SECONDS = 0.1
_LOG_SCAN_TAIL_BYTES = 65536
#: 自报地址里允许出现的回环主机文本（IPv6 用 URL 里的方括号写法）。
_LOOPBACK_URL_PATTERN = re.compile(
    r"https?://(?:"
    + "|".join(re.escape(host) for host in LOOPBACK_PREVIEW_HOSTS)
    + r"|\[::1\])(?::\d{1,5})?(?:/[^\s\"'<>]*)?",
    re.IGNORECASE,
)
#: URL 结尾常被终端排版字符带住，这些不算路径的标点先剥掉。
_TRAILING_URL_PUNCTUATION = ".,;:)]}\"'"


def preview_state_dir() -> Path:
    """预览记录与日志所在目录（``~/.kedacode/preview``）。"""
    return product_identity.state_home() / PREVIEW_STATE_SUBDIR


def preview_repo_slug(repo_root: Path) -> str:
    """给目标仓库生成文件名可用的短标识（目录名 + 解析后路径的摘要）。"""
    digest = hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:8]
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", repo_root.name) or "repo"
    return f"{safe_name}-{digest}"


class SubprocessPreviewProcessManager(IPreviewProcessManager):
    """用独立进程组托管预览 dev server，并把记录落在本机状态目录。"""

    def start_preview(self, request: PreviewStartRequest) -> PreviewStartOutcome:
        """启动已批准的 dev argv 并等待其报告回环地址。

        Args:
            request: 受控启动输入。

        Returns:
            PreviewStartOutcome：已有存活预览、启动即退出、ready 超时都作为结果
            返回；配置地址非回环时在**启动任何进程之前**拒绝。
        """
        if request.ready_url:
            try:
                require_loopback_preview_url(
                    request.ready_url, source="agent_session.preview.ready_url"
                )
            except ValueError as value_exc:
                return PreviewStartOutcome(started=False, record=None, message=str(value_exc))
        repo_root = request.cwd
        existing_record = read_preview_record(repo_root)
        if existing_record is not None:
            if _pid_is_alive(existing_record.pid):
                identity_reason = _identity_mismatch_reason(existing_record)
                if identity_reason is not None:
                    return PreviewStartOutcome(
                        started=False,
                        record=existing_record,
                        message=(
                            "An existing preview record is present but its process owner "
                            f"cannot be verified ({identity_reason}); refusing to start a "
                            "second preview. Inspect it with `kc preview status`."
                        ),
                    )
                return PreviewStartOutcome(
                    started=False,
                    record=existing_record,
                    message=(
                        f"A KedaCode-owned preview is already running for this repository "
                        f"(pid {existing_record.pid}, process group {existing_record.process_group}, "
                        f"url {existing_record.url or 'not ready yet'}). "
                        "Run `kc preview stop` before starting another one."
                    ),
                )
            # 陈旧记录：进程已不在，清理后按新启动处理（不因此发任何信号）。
            clear_preview_record(repo_root)

        state_dir = preview_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        log_path = state_dir / f"{preview_repo_slug(repo_root)}.log"
        child_process = _spawn_preview_process(request.argv, repo_root, log_path)
        if isinstance(child_process, str):
            return PreviewStartOutcome(started=False, record=None, message=child_process)

        process_group = _process_group_of(child_process.pid)
        process_started_at = process_start_time(child_process.pid)
        if process_group != child_process.pid or process_started_at is None:
            _terminate_fresh_preview_child(child_process)
            return PreviewStartOutcome(
                started=False,
                record=None,
                message=(
                    "Preview process identity could not be confirmed after startup; "
                    "the new process was stopped where its process group was verifiable."
                ),
            )

        record = PreviewProcessRecord(
            key=PREVIEW_RECORD_KEY,
            pid=child_process.pid,
            process_group=process_group,
            argv=tuple(request.argv),
            cwd=repo_root,
            log_path=log_path,
            url=None,
            started_at_iso=datetime.now().isoformat(timespec="seconds"),
            started_at_mono=time.monotonic(),
            owner_pid=os.getpid(),
            process_started_at=process_started_at,
        )
        write_preview_record(repo_root, record)
        return _await_ready(
            record=record,
            child_process=child_process,
            ready_url=request.ready_url,
            ready_timeout_seconds=request.ready_timeout_seconds,
        )

    def inspect_preview(self, *, repo_root: Path) -> PreviewStatusOutcome:
        """读取注册表并判定当前预览状态，不启动也不终止任何进程。"""
        record = read_preview_record(repo_root)
        if record is None:
            return PreviewStatusOutcome(
                state=PreviewState.NONE,
                record=None,
                message="No KedaCode-owned preview process is registered for this repository.",
            )
        if not _pid_is_alive(record.pid):
            # 存活判定必须在身份核对**之前**：pid 已消失时 getpgid 必然读不到组，
            # 那属于「陈旧记录」而不是「陌生进程」，否则 EXITED 分支永远走不到，
            # 记录也永远清不掉（对一个不存在的进程喊"手动去停"是无解的指引）。
            return PreviewStatusOutcome(
                state=PreviewState.EXITED,
                record=record,
                message=(
                    f"Registered preview (pid {record.pid}) is no longer running; the record "
                    "is stale and no process was signalled. `kc preview stop` clears it."
                ),
            )
        identity_reason = _identity_mismatch_reason(record)
        if identity_reason is not None:
            return PreviewStatusOutcome(
                state=PreviewState.FOREIGN,
                record=record,
                message=(
                    f"Preview record for pid {record.pid} cannot be verified as "
                    f"KedaCode-owned: {identity_reason}. Refusing to signal it."
                ),
            )
        if record.url:
            return PreviewStatusOutcome(
                state=PreviewState.READY,
                record=record,
                url=record.url,
                message=f"Preview ready at {record.url} (process group {record.process_group}).",
            )
        return PreviewStatusOutcome(
            state=PreviewState.STARTING,
            record=record,
            message=(
                f"Preview process (pid {record.pid}) is running but has not reported a "
                f"loopback address yet. Log: {record.log_path}"
            ),
        )

    def stop_preview(self, *, repo_root: Path) -> PreviewStopOutcome:
        """只停止注册表里那个身份可证实的进程组。"""
        record = read_preview_record(repo_root)
        if record is None:
            return PreviewStopOutcome(
                stopped=True,
                killed=False,
                record=None,
                message="Nothing to stop: no KedaCode-owned preview process is registered.",
            )
        if not _pid_is_alive(record.pid):
            # 与 inspect 同序：进程已不在时只清记录、不发信号（见上文的理由）。
            clear_preview_record(repo_root)
            return PreviewStopOutcome(
                stopped=True,
                killed=False,
                record=record,
                message=(
                    f"Preview pid {record.pid} had already exited; the stale record was "
                    "removed and no signal was sent."
                ),
            )
        identity_reason = _identity_mismatch_reason(record)
        if identity_reason is not None:
            return PreviewStopOutcome(
                stopped=False,
                killed=False,
                record=record,
                message=(
                    f"Refusing to stop pid {record.pid}: the record is not verifiable as "
                    f"KedaCode-owned ({identity_reason}). Stop it manually if you own it."
                ),
            )
        if record.process_group == os.getpgid(0):
            # 正常预览绝不会是 KC 自己所在的组；命中说明记录被改写或进程被
            # 重新归组——盲杀会连带杀掉调用方终端会话，因此拒绝。
            return PreviewStopOutcome(
                stopped=False,
                killed=False,
                record=record,
                message=(
                    f"Refusing to stop process group {record.process_group}: it is KedaCode's "
                    "own group, which means the record does not describe a detached preview."
                ),
            )
        # 跨进程 stop 拿不到原 Popen 对象，因此按 pgid 终止整组；上面的身份核对
        # 已确认该组仍由记录的组长持有。
        termination = _terminate_process_group(record.process_group)
        clear_preview_record(repo_root)
        return PreviewStopOutcome(
            stopped=True,
            killed=True,
            record=record,
            message=(
                f"Stopped preview process group {record.process_group} (pid {record.pid}): "
                f"{termination}."
            ),
        )


def _spawn_preview_process(
    argv: tuple[str, ...], repo_root: Path, log_path: Path
) -> subprocess.Popen | str:
    """以独立进程组启动 dev 命令；失败时返回人读原因而非抛异常。

    Returns:
        成功返回 Popen 句柄，失败返回说明文本（调用方据此构造 outcome）。
    """
    if not argv:
        return "Preview argv is empty; nothing can be started."
    with log_path.open("ab") as log_handle:
        try:
            return subprocess.Popen(
                list(argv),
                cwd=str(repo_root),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=build_sanitized_child_env(),
            )
        except FileNotFoundError:
            return f"Preview command {argv[0]!r} was not found on PATH; nothing was started."
        except OSError as os_exc:
            return f"Failed to start preview process: {os_exc}"


def _await_ready(
    *,
    record: PreviewProcessRecord,
    child_process: subprocess.Popen,
    ready_url: str | None,
    ready_timeout_seconds: int,
) -> PreviewStartOutcome:
    """轮询就绪：配置地址走连通性探测，否则解析进程自报的回环地址。"""
    deadline = time.monotonic() + max(1, int(ready_timeout_seconds))
    while time.monotonic() < deadline:
        if child_process.poll() is not None:
            clear_preview_record(record.cwd)
            return PreviewStartOutcome(
                started=False,
                record=None,
                message=(
                    f"Preview command exited with code {child_process.returncode} before "
                    f"reporting a ready address. Log: {record.log_path}"
                ),
            )
        ready_url_candidate = _resolve_ready_url(log_path=record.log_path, expected_url=ready_url)
        if ready_url_candidate is not None:
            ready_record = _record_with_url(record, ready_url_candidate)
            write_preview_record(record.cwd, ready_record)
            return PreviewStartOutcome(
                started=True,
                record=ready_record,
                message=(
                    f"Preview is ready at {ready_url_candidate} (pid {record.pid}, "
                    f"process group {record.process_group}). Open it yourself; KedaCode "
                    "does not launch a browser. Stop it with `kc preview stop`."
                ),
            )
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    termination = _terminate_record_group(child_process=child_process, record=record)
    clear_preview_record(record.cwd)
    return PreviewStartOutcome(
        started=False,
        record=None,
        timed_out=True,
        message=(
            f"Preview did not report a reachable loopback address within "
            f"{ready_timeout_seconds}s; the process group was stopped ({termination}). "
            f"Log: {record.log_path}. Set [agent_session.preview].ready_url / "
            "ready_timeout_seconds, or confirm the dev command prints a localhost URL."
        ),
    )


def read_preview_record(repo_root: Path) -> PreviewProcessRecord | None:
    """读取该仓库的预览记录；无记录或记录不可解析时返回 ``None``。"""
    registry_path = _preview_registry_path(repo_root)
    if not registry_path.exists():
        return None
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as read_exc:
        logger.warning("preview registry %s unreadable: %s", registry_path, read_exc)
        return None
    if not isinstance(payload, dict) or payload.get("version") != PREVIEW_REGISTRY_VERSION:
        logger.warning("preview registry %s has an unsupported version", registry_path)
        return None
    record_payload = payload.get("record")
    if not isinstance(record_payload, dict):
        return None
    try:
        return PreviewProcessRecord(
            key=str(record_payload["key"]),
            pid=int(record_payload["pid"]),
            process_group=int(record_payload["process_group"]),
            argv=tuple(str(argument) for argument in record_payload["argv"]),
            cwd=Path(record_payload["cwd"]),
            log_path=Path(record_payload["log_path"]),
            url=None if record_payload.get("url") in (None, "") else str(record_payload["url"]),
            started_at_iso=str(record_payload.get("started_at_iso", "")),
            started_at_mono=float(record_payload.get("started_at_mono", 0.0)),
            owner_pid=int(record_payload.get("owner_pid", 0)),
            process_started_at=(
                None
                if record_payload.get("process_started_at") in (None, "")
                else float(record_payload["process_started_at"])
            ),
        )
    except (KeyError, TypeError, ValueError) as parse_exc:
        logger.warning("preview registry %s is malformed: %s", registry_path, parse_exc)
        return None


def write_preview_record(repo_root: Path, record: PreviewProcessRecord) -> None:
    """原子写入预览记录（先写临时文件再 replace，避免半截 JSON）。"""
    registry_path = _preview_registry_path(repo_root)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PREVIEW_REGISTRY_VERSION,
        "record": {
            "key": record.key,
            "pid": record.pid,
            "process_group": record.process_group,
            "argv": list(record.argv),
            "cwd": str(record.cwd),
            "log_path": str(record.log_path),
            "url": record.url,
            "started_at_iso": record.started_at_iso,
            "started_at_mono": record.started_at_mono,
            "owner_pid": record.owner_pid,
            "process_started_at": record.process_started_at,
        },
    }
    temp_path = registry_path.with_name(registry_path.name + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, registry_path)


def clear_preview_record(repo_root: Path) -> None:
    """删除该仓库的预览记录（幂等）。"""
    try:
        _preview_registry_path(repo_root).unlink()
    except FileNotFoundError:
        return


def _preview_registry_path(repo_root: Path) -> Path:
    """该仓库的注册表文件路径（目录与命名规则由本模块唯一决定）。"""
    return preview_state_dir() / f"{preview_repo_slug(repo_root)}.json"


def _record_with_url(record: PreviewProcessRecord, url_text: str) -> PreviewProcessRecord:
    """返回补上就绪地址的记录副本（dataclass 不可变，只能重建）。"""
    return PreviewProcessRecord(
        key=record.key,
        pid=record.pid,
        process_group=record.process_group,
        argv=record.argv,
        cwd=record.cwd,
        log_path=record.log_path,
        url=url_text,
        started_at_iso=record.started_at_iso,
        started_at_mono=record.started_at_mono,
        owner_pid=record.owner_pid,
        process_started_at=record.process_started_at,
    )


def _process_group_of(process_pid: int) -> int | None:
    """读取 pid 的实际进程组 id；进程已消失时返回 ``None``。"""
    try:
        return os.getpgid(process_pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None


def _pid_is_alive(process_pid: int) -> bool:
    """信号 0 探活：只在「进程不存在」时为假，权限受限按存在处理。"""
    try:
        os.kill(process_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _record_is_alive(record: PreviewProcessRecord) -> bool:
    """记录描述的进程是否仍由原组长持有（可安全认定为同一个预览进程）。"""
    if not _pid_is_alive(record.pid):
        return False
    return _identity_mismatch_reason(record) is None


def _identity_mismatch_reason(record: PreviewProcessRecord) -> str | None:
    """核对记录与实时进程组；无法证实时返回指名原因，可证实时返回 ``None``。"""
    if record.process_started_at is None:
        return "the record has no process creation identity"
    actual_started_at = process_start_time(record.pid)
    if actual_started_at is None:
        return "the process creation identity cannot be read"
    if actual_started_at != record.process_started_at:
        return (
            f"pid {record.pid} was created at {actual_started_at}, "
            f"not the recorded {record.process_started_at}"
        )
    actual_group = _process_group_of(record.pid)
    if actual_group is None:
        return "the pid no longer resolves to a process group"
    if actual_group != record.process_group:
        return (
            f"pid {record.pid} now belongs to process group {actual_group}, "
            f"not the recorded {record.process_group}"
        )
    if record.process_group != record.pid:
        return (
            f"recorded process group {record.process_group} is not led by the recorded "
            f"pid {record.pid}"
        )
    return None


def _terminate_fresh_preview_child(child_process: subprocess.Popen) -> None:
    """清理刚启动但无法登记身份的预览进程，不触碰已变更的进程组。"""
    if child_process.poll() is not None:
        return
    actual_group = _process_group_of(child_process.pid)
    if actual_group == child_process.pid:
        _terminate_process_group(actual_group)
        try:
            child_process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            child_process.kill()
            child_process.wait()
        return
    try:
        child_process.terminate()
        child_process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        try:
            child_process.kill()
        except OSError:
            return
        child_process.wait()


def _resolve_ready_url(*, log_path: Path, expected_url: str | None) -> str | None:
    """给出可回复终端的就绪地址：期望地址与自报地址都要求当前可连通。"""
    if expected_url:
        return expected_url if _url_is_reachable(expected_url) else None
    reported_url = _scan_reported_loopback_url(log_path)
    if reported_url is None:
        return None
    return reported_url if _url_is_reachable(reported_url) else None


def _scan_reported_loopback_url(log_path: Path) -> str | None:
    """从进程自己的输出里取第一个回环地址；局域网/远程地址一律忽略。"""
    tail_text = _read_log_tail(log_path)
    if not tail_text:
        return None
    for match in _LOOPBACK_URL_PATTERN.finditer(tail_text):
        candidate = match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)
        try:
            return require_loopback_preview_url(candidate, source="preview process output")
        except ValueError:
            continue
    return None


def _read_log_tail(log_path: Path) -> str:
    """读日志尾部（二进制读再解码，避免文本模式下的负偏移 seek 限制）。"""
    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            log_file.seek(max(0, log_file.tell() - _LOG_SCAN_TAIL_BYTES))
            raw_bytes = log_file.read()
    except OSError as read_exc:
        logger.debug("preview log %s unreadable: %s", log_path, read_exc)
        return ""
    return raw_bytes.decode("utf-8", errors="replace")


def _url_is_reachable(url_text: str) -> bool:
    """该回环地址当前是否可建立 TCP 连接（dev server 真的在监听）。"""
    try:
        host, port, _path = parse_preview_url(url_text)
    except ValueError:
        return False
    if host not in LOOPBACK_PREVIEW_HOSTS:
        return False
    return _can_connect(host, port if port is not None else _default_port(url_text))


def _default_port(url_text: str) -> int:
    """URL 未显式带端口时按 scheme 取默认端口。"""
    return 443 if url_text.lower().startswith("https://") else 80


def _can_connect(connect_host: str, connect_port: int) -> bool:
    """短超时 TCP 探测，失败不抛异常。"""
    address_family = socket.AF_INET6 if ":" in connect_host else socket.AF_INET
    try:
        with socket.socket(address_family, socket.SOCK_STREAM) as probe_socket:
            probe_socket.settimeout(_READY_CONNECT_TIMEOUT_SECONDS)
            return probe_socket.connect_ex((connect_host, connect_port)) == 0
    except OSError as connect_exc:
        logger.debug("preview readiness probe failed: %s", connect_exc)
        return False


def _terminate_process_group(process_group: int) -> str:
    """先 SIGTERM 再限时 SIGKILL 地终止一个进程组，返回人读结论。"""
    os.killpg(process_group, signal.SIGTERM)
    deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _process_group_is_running(process_group):
            return "SIGTERM drained the group"
        time.sleep(_TERMINATE_POLL_INTERVAL_SECONDS)
    os.killpg(process_group, signal.SIGKILL)
    return "SIGKILL after the SIGTERM grace period"


def _process_group_is_running(process_group: int) -> bool:
    """信号 0 探测进程组里是否还有活着的成员。"""
    try:
        os.killpg(process_group, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _terminate_record_group(
    *, child_process: subprocess.Popen, record: PreviewProcessRecord
) -> str:
    """超时后终止自己刚启动的那个组，并回收直接子进程。"""
    outcome = _terminate_process_group(record.process_group)
    try:
        child_process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        child_process.kill()
        child_process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    return outcome


__all__ = [
    "PREVIEW_RECORD_KEY",
    "PREVIEW_REGISTRY_VERSION",
    "PREVIEW_STATE_SUBDIR",
    "SubprocessPreviewProcessManager",
    "clear_preview_record",
    "preview_repo_slug",
    "preview_state_dir",
    "read_preview_record",
    "write_preview_record",
]

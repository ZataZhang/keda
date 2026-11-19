"""跨进程文件锁：POSIX ``flock`` → Windows ``msvcrt.locking`` → 无锁回退。

本模块是仓库内**唯一**的跨进程建议锁实现。``config.toml`` / ``.kedacode.toml``
写回、本机机器身份、loop 状态、GitHub 额度账本与预览注册表原先各自手写了一份
``fcntl``（其中三处直接 ``import fcntl``，在 Windows 上要么让整个模块无法导入，
要么静默退化成无锁）。这里把平台探测与抢锁重试收敛到一处：

- POSIX 走 ``fcntl.flock``；
- Windows 走 ``msvcrt.locking``（锁首个字节，Windows 没有 flock 语义）；
- 两者都没有时退化为无锁，并用 :func:`lock_backend_name` 暴露 ``"none"``，
  让守卫测试在缺锁平台上**跳过**而不是假装通过。

锁文件是零字节的 rendezvous 点，不写内容；``os.replace`` 换掉目标文件的 inode
也不影响它。进程退出时内核自动释放，崩溃不留死锁。

同线程重入直接放行：``flock`` 记在 open file description 上，同进程再开一个 fd
去锁同一个文件会等自己；把整套读-改-写套进另一个持锁调用时，重入必须在进入内核
之前拦下来。抢锁默认无限等待（``timeout_seconds=None``），需要"超时失败而不是
挂死"的调用方传有限秒数并捕获 :class:`TimeoutError`。
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: 锁文件权限：rendezvous 点不含数据，只该被属主读写（默认 0666&~umask 太宽）。
LOCK_FILE_MODE = 0o600
#: 抢锁重试间隔：持锁写入本身是毫秒级，短间隔足够让并发写者在超时内轮到手。
LOCK_RETRY_INTERVAL_SECONDS = 0.05

_LOCK_BACKEND: str
try:
    import fcntl

    def try_acquire_file_lock(file_descriptor: int) -> bool:
        """非阻塞尝试独占锁，被别的进程持有则返回 ``False``。"""
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def release_file_lock(file_descriptor: int) -> None:
        """释放 :func:`try_acquire_file_lock` 拿到的锁。"""
        fcntl.flock(file_descriptor, fcntl.LOCK_UN)

    _LOCK_BACKEND = "fcntl"
except ImportError:  # pragma: no cover - 仅 Windows 走这一支
    try:
        import msvcrt

        def try_acquire_file_lock(file_descriptor: int) -> bool:
            """非阻塞锁住首字节；已被占用则返回 ``False``。"""
            try:
                os.lseek(file_descriptor, 0, os.SEEK_SET)
                msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True

        def release_file_lock(file_descriptor: int) -> None:
            """解锁首字节（须与加锁时同一偏移）。"""
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)

        _LOCK_BACKEND = "msvcrt"
    except ImportError:
        # 两个后端都没有：跨进程串行化不成立，只剩 os.replace 保证单次写入不撕裂。
        # 显式命名成 none，让依赖锁的守卫测试在本平台跳过而不是"静默通过"。

        def try_acquire_file_lock(file_descriptor: int) -> bool:
            """无锁后端：视为立即拿到锁。"""
            return True

        def release_file_lock(file_descriptor: int) -> None:
            """无锁后端：没有需要释放的东西。"""

        _LOCK_BACKEND = "none"


_held_lock_paths = threading.local()


def lock_backend_name() -> str:
    """返回当前平台的锁后端：``"fcntl"`` / ``"msvcrt"`` / ``"none"``。"""
    return _LOCK_BACKEND


def _thread_held_lock_paths() -> set[str]:
    """取本线程当前持有的锁键集合（首次使用时建）。"""
    held_paths: set[str] | None = getattr(_held_lock_paths, "paths", None)
    if held_paths is None:
        held_paths = set()
        _held_lock_paths.paths = held_paths
    return held_paths


def open_lock_file(lock_path: Path) -> int:
    """打开（必要时以 :data:`LOCK_FILE_MODE` 新建）锁文件，返回其 fd。

    先 ``O_EXCL`` 再回落 ``O_CREAT``：只有"确实是本进程建出来的"那次才 fchmod，避免
    把别人建好的锁文件权限改来改去；``O_EXCL`` 分支的 mode 会被 umask 削，所以补一次
    显式 ``fchmod`` 保证是 0600。Windows 没有 ``os.fchmod``，跳过权限收紧。

    Args:
        lock_path: 锁文件路径；父目录须已存在。

    Returns:
        已打开的锁文件描述符，调用方负责 ``os.close``。
    """
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    try:
        created_descriptor = os.open(str(lock_path), flags | os.O_EXCL, LOCK_FILE_MODE)
    except FileExistsError:
        return os.open(str(lock_path), flags)
    if hasattr(os, "fchmod"):
        os.fchmod(created_descriptor, LOCK_FILE_MODE)
    return created_descriptor


def acquire_file_lock(
    file_descriptor: int,
    *,
    lock_path: Path,
    timeout_seconds: float | None = None,
) -> None:
    """在给定等待时间内抢到锁；``timeout_seconds=None`` 时无限等待。

    Args:
        file_descriptor: :func:`open_lock_file` 打开的 fd。
        lock_path: 锁文件路径，仅用于错误信息。
        timeout_seconds: 最长等待秒数；``None`` 表示一直等到拿到为止。

    Raises:
        TimeoutError: 有限等待内仍被他人持有。
    """
    if try_acquire_file_lock(file_descriptor):
        return
    if timeout_seconds is None:
        while not try_acquire_file_lock(file_descriptor):
            time.sleep(LOCK_RETRY_INTERVAL_SECONDS)
        return
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"{lock_path} 的跨进程锁被另一个写入者持有超过 {timeout_seconds:g}s。"
            )
        time.sleep(min(LOCK_RETRY_INTERVAL_SECONDS, remaining))
        if try_acquire_file_lock(file_descriptor):
            return


@contextmanager
def file_lock(
    lock_path: Path,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    """持有 ``lock_path`` 的跨进程排他锁，覆盖整个 ``with`` 代码块。

    Args:
        lock_path: 锁文件路径；父目录不存在时自动创建。
        timeout_seconds: 最长等待秒数；``None`` 表示一直等到拿到为止。

    Raises:
        TimeoutError: 有限等待内仍被他人持有。
    """
    lock_key = os.path.realpath(str(lock_path))
    held_paths = _thread_held_lock_paths()
    if lock_key in held_paths:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = open_lock_file(lock_path)
    try:
        acquire_file_lock(
            file_descriptor,
            lock_path=lock_path,
            timeout_seconds=timeout_seconds,
        )
        held_paths.add(lock_key)
        try:
            yield
        finally:
            held_paths.discard(lock_key)
            release_file_lock(file_descriptor)
    finally:
        os.close(file_descriptor)


__all__ = [
    "LOCK_FILE_MODE",
    "acquire_file_lock",
    "file_lock",
    "lock_backend_name",
    "open_lock_file",
    "release_file_lock",
    "try_acquire_file_lock",
]

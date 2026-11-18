"""跨进程文件锁与平台能力判定的单元测试。

锁的跨进程互斥语义由 ``tests/test_toml_section_editor.py`` 的真实子进程用例覆盖；
本文件只钉住共享实现自己的契约：锁文件权限、同线程重入、有界超时，以及
Windows 能力闸门的失败信息。
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from backend.core.shared.models import platform_support
from backend.core.shared.models.platform_support import (
    UnsupportedPlatformFeatureError,
    require_posix_process_groups,
    supports_posix_process_groups,
)
from backend.infrastructure.file_lock import file_lock, lock_backend_name


def test_lock_backend_is_one_of_the_known_backends() -> None:
    """锁后端只能是 fcntl / msvcrt / none 三者之一。"""
    assert lock_backend_name() in {"fcntl", "msvcrt", "none"}


def test_file_lock_creates_private_lock_file(tmp_path: Path) -> None:
    """锁文件是零字节 rendezvous 点；POSIX 上权限收紧到 0600。"""
    lock_path = tmp_path / "state.lock"
    with file_lock(lock_path):
        assert lock_path.is_file()
        if hasattr(os, "fchmod"):
            assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_file_lock_is_reentrant_within_one_thread(tmp_path: Path) -> None:
    """同线程嵌套取同一把锁直接放行，不自死锁。"""
    lock_path = tmp_path / "state.lock"
    with file_lock(lock_path):
        with file_lock(lock_path):
            pass


@pytest.mark.skipif(
    lock_backend_name() == "none",
    reason="本平台没有跨进程锁后端，超时语义不可验证",
)
def test_file_lock_times_out_when_another_process_holds_it(tmp_path: Path) -> None:
    """另一个进程持锁时，有限等待以 TimeoutError 失败而不是挂死。"""
    lock_path = tmp_path / "state.lock"
    holder_script = textwrap.dedent(
        f"""
        import sys
        import time
        from pathlib import Path

        from backend.infrastructure.file_lock import file_lock

        with file_lock(Path({str(lock_path)!r})):
            print("locked", flush=True)
            time.sleep(30)
        """
    )
    holder_process = subprocess.Popen(
        [sys.executable, "-c", holder_script],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert holder_process.stdout is not None
        assert holder_process.stdout.readline().strip() == b"locked"
        with pytest.raises(TimeoutError, match="跨进程锁"):
            with file_lock(lock_path, timeout_seconds=0.2):
                pass
    finally:
        holder_process.kill()
        holder_process.wait(timeout=10)


def test_require_posix_process_groups_rejects_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows 上依赖进程组的功能入口必须给出可读的拒绝信息。"""
    monkeypatch.setattr(platform_support.os, "name", "nt")
    with pytest.raises(UnsupportedPlatformFeatureError, match="POSIX 进程组"):
        require_posix_process_groups("kc daemon")


def test_require_posix_process_groups_allows_supported_host() -> None:
    """支持的宿主上闸门放行；判定与实际 ``os`` 属性一致。"""
    expected_supported = os.name != "nt" and hasattr(os, "killpg") and hasattr(os, "getsid")
    assert supports_posix_process_groups() is expected_supported
    if expected_supported:
        require_posix_process_groups("kc daemon")

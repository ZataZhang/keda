"""通过 runner 的公开进程入口验证失败诊断与已有调用兼容。"""

import subprocess
import sys
from pathlib import Path

import pytest

from backend.infrastructure.process_runner import SubprocessRunner


def _run_failure(tmp_path: Path, code: str) -> subprocess.CalledProcessError:
    with pytest.raises(subprocess.CalledProcessError) as captured:
        SubprocessRunner().run([sys.executable, "-c", code], cwd=tmp_path)
    return captured.value


def test_process_error_includes_stderr_and_exit_code(tmp_path: Path) -> None:
    """保留标准异常类型，错误信息优先展示 stderr。"""
    code = (
        "import sys; print('STDOUT_' + 'IGNORE'); "
        "print('ERR_' + 'missing-validation', file=sys.stderr); sys.exit(17)"
    )
    error = _run_failure(tmp_path, code)
    assert error.returncode == 17
    assert error.cmd == [sys.executable, "-c", code]
    assert "17" in str(error)
    assert "ERR_missing-validation" in str(error)
    assert "STDOUT_IGNORE" not in str(error)


def test_process_error_uses_stdout_when_stderr_empty(tmp_path: Path) -> None:
    """无 stderr 时使用实际收集的 stdout，不依赖异常类名。"""
    error = _run_failure(tmp_path, "print('DIAGNOSTIC_' + 'stdout'); raise SystemExit(7)")
    assert "DIAGNOSTIC_stdout" in str(error)


def test_process_error_bounds_large_diagnostic(tmp_path: Path) -> None:
    """长错误附有截断标记，避免把过大的输出塞进日志。"""
    error = _run_failure(tmp_path, "import sys; print('x' * 20000, file=sys.stderr); sys.exit(1)")
    message = str(error)
    assert "x" * 1000 in message
    assert "truncat" in message.lower()
    assert len(message) < 5000


def test_nonzero_without_check_retains_command_result(tmp_path: Path) -> None:
    """不启用 check 时仍能从 CommandResult 读取失败及输出。"""
    result = SubprocessRunner().run(
        [sys.executable, "-c", "print('still captured'); raise SystemExit(11)"],
        cwd=tmp_path,
        check=False,
    )
    assert result.return_code == 11
    assert result.stdout.strip() == "still captured"

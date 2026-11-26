"""通过独立进程观察应用日志的启动、轮转和文件输出。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_log_probe(log_dir: Path, program: str, *, level: str = "INFO") -> None:
    """从干净的进程启动日志入口，使用隔离目录承接配置和产物。"""
    probe = subprocess.run(
        [sys.executable, "-c", program],
        cwd=log_dir,
        env={
            **os.environ,
            "LOG_FILE": str(log_dir / "app.log"),
            "LOG_LEVEL": level,
            "LOG_RETENTION_DAYS": "14",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr


def _read_daily_log(log_dir: Path) -> str:
    daily_logs = sorted(log_dir.glob("app-*.log"))
    assert len(daily_logs) == 1, daily_logs
    return daily_logs[0].read_text(encoding="utf-8")


@pytest.mark.parametrize(("level", "writes_info"), [("not-a-log-level", True), ("WARNING", False)])
def test_log_startup_respects_configured_level(
    tmp_path: Path, level: str, writes_info: bool
) -> None:
    """配置错误时仍写入日志，正常级别继续过滤低级别消息。"""
    _run_log_probe(
        tmp_path,
        """
from backend.infrastructure.logging.logger import logger

logger.info("eval-info-record")
logger.warning("eval-warning-record")
""",
        level=level,
    )
    log_text = _read_daily_log(tmp_path)
    assert "eval-warning-record" in log_text
    assert ("eval-info-record" in log_text) == writes_info


def test_rollover_writes_new_day_and_removes_only_expired_logs(tmp_path: Path) -> None:
    """同一进程到达次日时切换写入目标，并清理轮转时存在的旧日志。"""
    _run_log_probe(
        tmp_path,
        """
import os
from datetime import datetime
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

# 先加载配置，让时钟替身只影响日志初始化及后续写入。
import_module("backend.infrastructure.config.settings")

current_time = datetime(2026, 9, 1, 23, 59, 59)


class ProbeDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return current_time
        return datetime.fromtimestamp(current_time.timestamp(), tz)


with patch("datetime.datetime", ProbeDateTime), patch("time.time", lambda: current_time.timestamp()):
    from backend.infrastructure.logging.logger import logger

    logger.info("eval-before-midnight")
    # 在初始化之后补入旧文件，避免启动清理掩盖轮转时没有清理的问题。
    for name, filed_at in (
        ("app-2026-08-01.log", datetime(2026, 8, 1)),
        ("app-2026-08-31.log", datetime(2026, 8, 31)),
    ):
        dated_file = Path(name)
        dated_file.write_text("existing record", encoding="utf-8")
        os.utime(dated_file, (filed_at.timestamp(), filed_at.timestamp()))
    current_time = datetime(2026, 9, 2, 0, 0, 1)
    logger.info("eval-after-midnight")
""",
    )
    previous_day = tmp_path / "app-2026-09-01.log"
    next_day = tmp_path / "app-2026-09-02.log"
    assert previous_day.is_file() and next_day.is_file()
    previous_text = previous_day.read_text(encoding="utf-8")
    next_text = next_day.read_text(encoding="utf-8")
    assert "eval-before-midnight" in previous_text
    assert "eval-after-midnight" not in previous_text
    assert "eval-after-midnight" in next_text
    assert "eval-before-midnight" not in next_text
    assert not (tmp_path / "app-2026-08-01.log").exists()
    assert (tmp_path / "app-2026-08-31.log").read_text(encoding="utf-8") == "existing record"


def test_preexisting_root_handler_shares_log_delivery(tmp_path: Path) -> None:
    """第三方 handler 预装时，两路都要收到同一条记录。"""
    _run_log_probe(
        tmp_path,
        """
import logging

third_party = logging.FileHandler("third-party.log", encoding="utf-8")
logging.getLogger().addHandler(third_party)
from backend.infrastructure.logging.logger import logger

logger.warning("eval-shared-record")
""",
    )
    assert "eval-shared-record" in _read_daily_log(tmp_path)
    assert "eval-shared-record" in (tmp_path / "third-party.log").read_text(encoding="utf-8")

"""Tests for logger configuration."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from backend.api import cli_registry
from backend.core.use_cases import agent_runner_factory
from backend.engines.agent_runner import factory as engines_factory
from backend.infrastructure.config.settings import AppSettings
from backend.infrastructure.logging.logger import Logger, daily_log_path

#: ``backend.infrastructure.logging`` 包把 ``logger`` 属性重绑成了 ``Logger`` 单例，
#: 因此需要模块对象时必须显式取模块，不能按属性链解析。
logger_impl_module = import_module("backend.infrastructure.logging.logger")

#: 改动前后都必须保持的日志行格式（FR-6 正常路径零变化的比对基准）
_EXPECTED_LOG_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
)
_EXPECTED_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_DAILY_LOG_NAME_PATTERN = re.compile(r"^app-\d{4}-\d{2}-\d{2}\.log$")


class _ShiftedClockDatetime:
    """测试内使用的 datetime 替身，只提供 logger 模块调用的 ``now`` / ``strptime``。

    生产代码里没有时钟开关；跨天行为只能在测试里把时钟拨到下一天来模拟。
    """

    def __init__(self, current: datetime) -> None:
        self._current = current

    def advance_days(self, days: int) -> None:
        self._current += timedelta(days=days)

    def now(self) -> datetime:
        return self._current

    def strptime(self, date_string: str, date_format: str) -> datetime:
        return datetime.strptime(date_string, date_format)


def _reset_logging_state() -> None:
    """摘掉并关闭 root 上所有 handler（含第三方与普通 FileHandler），再重置 Logger 单例。"""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)
    Logger._instance = None
    Logger._logger = None


@contextmanager
def _logger_with_config(
    log_dir: Path,
    *,
    app_name: str,
    log_level: str = "INFO",
    retention_days: int = 14,
) -> Iterator[logging.Logger]:
    """以指定配置真实执行一次 ``Logger._setup_logger``，交出 root logger。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    with patch.object(logger_impl_module, "config") as mock_config:
        mock_config.app_name = app_name
        mock_config.log_level = log_level
        mock_config.log_file = str(log_dir / "app.log")
        mock_config.log_retention_days = retention_days
        _reset_logging_state()
        try:
            Logger().get_logger()
            yield logging.getLogger()
        finally:
            _reset_logging_state()


def _file_handlers_of(root: logging.Logger) -> list[logging.FileHandler]:
    return [handler for handler in root.handlers if isinstance(handler, logging.FileHandler)]


def _keda_handler_count(root: logging.Logger) -> int:
    return sum(1 for handler in root.handlers if getattr(handler, "_keda_handler", False))


def _daily_log_text(log_dir: Path) -> str:
    """读取"当天"日志文件内容（文件不存在时返回空串）。"""
    daily_file = daily_log_path(log_dir)
    return daily_file.read_text(encoding="utf-8") if daily_file.exists() else ""


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("app", logging.INFO, __file__, 1, message, (), None)


def _write_dated_log(log_dir: Path, date: datetime, content: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"app-{date.strftime('%Y-%m-%d')}.log"
    log_file.write_text(content, encoding="utf-8")
    return log_file


def test_logger_uses_daily_file_handler(tmp_path: Path) -> None:
    """Root logger should use daily-named FileHandler."""
    with _logger_with_config(tmp_path, app_name="test_daily_fh") as root:
        assert any(isinstance(handler, logging.FileHandler) for handler in root.handlers)
        assert any(isinstance(handler, logging.StreamHandler) for handler in root.handlers)
        file_handlers = _file_handlers_of(root)
        assert len(file_handlers) == 1, "只应有一个日文件写入者"
        assert all(
            isinstance(handler, logger_impl_module._DailyFileHandler)  # noqa: SLF001
            for handler in file_handlers
        ), "文件 handler 必须是按日自切的子类"


def test_logger_file_handler_uses_daily_filename(tmp_path: Path) -> None:
    """FileHandler should write to app-YYYY-MM-DD.log."""
    with _logger_with_config(tmp_path / "logs", app_name="test_daily_name") as root:
        file_handlers = _file_handlers_of(root)
        assert len(file_handlers) == 1, "只应有一个日文件写入者"
        today = datetime.now().strftime("%Y-%m-%d")
        assert f"app-{today}.log" in file_handlers[0].baseFilename


def test_logger_handlers_on_root(tmp_path: Path) -> None:
    """Handlers should be attached to root logger so all module loggers work."""
    with _logger_with_config(tmp_path, app_name="test_root_handlers") as root:
        assert root.handlers, "Root logger should have handlers"
        assert any(isinstance(handler, logging.StreamHandler) for handler in root.handlers)


def test_cleanup_old_logs(tmp_path: Path) -> None:
    """_cleanup_old_logs should remove log files older than keep_days."""
    log_dir = tmp_path / "logs"

    old_file = _write_dated_log(log_dir, datetime.now() - timedelta(days=20), "old log content")
    recent_file = _write_dated_log(
        log_dir, datetime.now() - timedelta(days=1), "recent log content"
    )

    with _logger_with_config(log_dir, app_name="test_cleanup", retention_days=14):
        # Old file should be cleaned up, recent file should remain
        assert not old_file.exists()
        assert recent_file.exists()


def test_normal_path_keeps_format_and_default_level(tmp_path: Path) -> None:
    """FR-6：合法级别、root 为空时，级别、日志行格式与文件命名与改动前一致。"""
    assert AppSettings().log_level == "INFO"
    probe_record = logging.LogRecord("app", logging.INFO, __file__, 12, "format probe", (), None)
    expected_line = logging.Formatter(_EXPECTED_LOG_FORMAT, datefmt=_EXPECTED_DATE_FORMAT).format(
        probe_record
    )

    with _logger_with_config(tmp_path / "logs", app_name="test_unchanged") as root:
        assert root.level == logging.INFO
        console_handler = next(
            handler for handler in root.handlers if type(handler) is logging.StreamHandler
        )
        file_handler = _file_handlers_of(root)[0]
        assert console_handler.level == logging.INFO
        assert file_handler.level == logging.INFO
        assert console_handler.format(probe_record) == expected_line
        assert file_handler.format(probe_record) == expected_line
        assert _DAILY_LOG_NAME_PATTERN.match(Path(file_handler.baseFilename).name)


@pytest.mark.parametrize(
    ("configured_level", "expected_level"),
    [
        ("INFO", logging.INFO),
        ("DEBUG", logging.DEBUG),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    ],
)
def test_legal_levels_resolve_unchanged(
    tmp_path: Path,
    configured_level: str,
    expected_level: int,
) -> None:
    """FR-6：五个合法级别名的解析结果与改动前一致。"""
    with _logger_with_config(
        tmp_path, app_name="test_legal_level", log_level=configured_level
    ) as root:
        assert root.level == expected_level
        assert _file_handlers_of(root)[0].level == expected_level


def test_lowercase_level_resolves_with_normalization_notice(tmp_path: Path) -> None:
    """FR-1：小写 ``info`` 这类手误不再崩溃，级别照常生效并留下一条可见提示。"""
    log_dir = tmp_path / "logs"
    with _logger_with_config(log_dir, app_name="test_lowercase", log_level="info") as root:
        assert root.level == logging.INFO
    notice_text = _daily_log_text(log_dir)
    assert "不是 logging 预定义名，已按 INFO 解析" in notice_text
    assert "无效日志级别" not in notice_text


@pytest.mark.parametrize("configured_level", ["bogus", "infoo", ""])
def test_invalid_log_level_degrades_to_info_with_warning(
    tmp_path: Path,
    configured_level: str,
) -> None:
    """FR-1：非法级别降级为 INFO 并留下明确警告，不抛异常、不丢文件日志。"""
    log_dir = tmp_path / "logs"
    with _logger_with_config(log_dir, app_name="test_invalid", log_level=configured_level) as root:
        assert root.level == logging.INFO
        assert _file_handlers_of(root), "降级路径仍应挂上文件 handler"
    warning_text = _daily_log_text(log_dir)
    assert "无效日志级别" in warning_text
    assert "已降级为 INFO" in warning_text


@pytest.mark.parametrize("configured_level", ["error", "critical"])
def test_normalization_notice_survives_the_level_it_just_applied(
    tmp_path: Path,
    configured_level: str,
) -> None:
    """FR-1：提示不能被子级别门槛吃掉——生效级别是 ERROR 时也得看得见那条手误。"""
    log_dir = tmp_path / "logs"
    with _logger_with_config(
        log_dir, app_name="test_notice_above_warning", log_level=configured_level
    ) as root:
        assert root.level == getattr(logging, configured_level.upper())
    notice_text = _daily_log_text(log_dir)
    assert "不是 logging 预定义名" in notice_text
    assert configured_level in notice_text


def test_padded_level_is_reported_as_normalized(tmp_path: Path) -> None:
    """FR-1：空格写错和大小写写错同属"需要归一化"，级别生效但必须同样出声。"""
    log_dir = tmp_path / "logs"
    with _logger_with_config(log_dir, app_name="test_padded_level", log_level="  INFO  ") as root:
        assert root.level == logging.INFO
    assert "不是 logging 预定义名" in _daily_log_text(log_dir)


def test_daily_handler_switches_file_on_date_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-2：跨天后新日志写进当天文件，两个文件都保持 app-YYYY-MM-DD.log 命名。"""
    log_dir = tmp_path / "logs"
    clock = _ShiftedClockDatetime(datetime(2026, 5, 24, 23, 59))
    monkeypatch.setattr(logger_impl_module, "datetime", clock)

    with _logger_with_config(log_dir, app_name="test_rotation") as root:
        file_handler = _file_handlers_of(root)[0]
        file_handler.emit(_record("before midnight"))
        clock.advance_days(1)
        file_handler.emit(_record("after midnight"))

        day_one_text = (log_dir / "app-2026-05-24.log").read_text(encoding="utf-8")
        day_two_text = (log_dir / "app-2026-05-25.log").read_text(encoding="utf-8")
        assert "before midnight" in day_one_text
        assert "after midnight" not in day_one_text
        assert "after midnight" in day_two_text
        assert file_handler.baseFilename == str(log_dir / "app-2026-05-25.log")
        written_names = sorted(path.name for path in log_dir.glob("app*.log"))
        assert written_names == ["app-2026-05-24.log", "app-2026-05-25.log"]
        assert all(_DAILY_LOG_NAME_PATTERN.match(name) for name in written_names)


def test_rollover_stays_anchored_when_cwd_moves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-2：``LOG_FILE`` 写成相对路径时，日切也必须落回配置那个目录。

    长驻进程（daemon / runner）常会 ``chdir()`` 到目标仓库；若日目录不在 setup 时锚定成
    绝对路径，午夜后的新文件会被开到当时 cwd 下，保留清理也会跟着去错的目录里删文件。
    """
    log_dir = tmp_path / "logs"
    clock = _ShiftedClockDatetime(datetime(2026, 5, 24, 23, 59))
    monkeypatch.setattr(logger_impl_module, "datetime", clock)
    monkeypatch.chdir(tmp_path)

    with patch.object(logger_impl_module, "config") as mock_config:
        mock_config.app_name = "test_relative_anchor"
        mock_config.log_level = "INFO"
        mock_config.log_file = "logs/app.log"
        mock_config.log_retention_days = 14
        _reset_logging_state()
        try:
            Logger().get_logger()
            file_handler = _file_handlers_of(logging.getLogger())[0]
            file_handler.emit(_record("before chdir"))

            moved_cwd = tmp_path / "moved"
            moved_cwd.mkdir()
            monkeypatch.chdir(moved_cwd)
            clock.advance_days(1)
            file_handler.emit(_record("after chdir"))

            assert Path(file_handler.baseFilename).is_absolute()
            assert Path(file_handler.baseFilename) == log_dir / "app-2026-05-25.log"
            assert not list(moved_cwd.glob("app-*.log")), "日切不得把文件开到新的 cwd 下"
        finally:
            _reset_logging_state()


def test_rollover_sweeps_logs_beyond_configured_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-3：保留期内的文件活过启动清理，却在日切时被按配置清掉。"""
    log_dir = tmp_path / "logs"
    clock = _ShiftedClockDatetime(datetime(2026, 5, 24, 0, 30))
    yesterday_file = _write_dated_log(log_dir, datetime(2026, 5, 23), "yesterday")
    monkeypatch.setattr(logger_impl_module, "datetime", clock)

    with _logger_with_config(log_dir, app_name="test_retention", retention_days=2) as root:
        file_handler = _file_handlers_of(root)[0]
        assert yesterday_file.exists(), "保留期 2 天时昨天的日志不该在启动时被删"
        clock.advance_days(1)
        file_handler.emit(_record("next day"))
        assert not yesterday_file.exists(), "日切后应按 log_retention_days 清理过期日志"
        assert (log_dir / "app-2026-05-24.log").exists()
        assert (log_dir / "app-2026-05-25.log").exists()


def test_rollover_keeps_logs_within_longer_configured_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-3：同一份旧文件在保留期更长时不被清理，证明保留天数取自配置而非硬编码。"""
    log_dir = tmp_path / "logs"
    clock = _ShiftedClockDatetime(datetime(2026, 5, 24, 0, 30))
    yesterday_file = _write_dated_log(log_dir, datetime(2026, 5, 23), "yesterday")
    monkeypatch.setattr(logger_impl_module, "datetime", clock)

    with _logger_with_config(log_dir, app_name="test_retention_3", retention_days=3) as root:
        file_handler = _file_handlers_of(root)[0]
        clock.advance_days(1)
        file_handler.emit(_record("next day"))
        assert yesterday_file.exists(), "保留期 3 天时不该删掉 2 天前的日志"


def test_retention_days_one_clears_two_day_old_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-3：``log_retention_days=1`` 时 2 天前的日文件在日切后消失。"""
    log_dir = tmp_path / "logs"
    clock = _ShiftedClockDatetime(datetime(2026, 5, 24, 0, 30))
    stale_file = _write_dated_log(log_dir, datetime(2026, 5, 22), "two days ago")
    monkeypatch.setattr(logger_impl_module, "datetime", clock)

    with _logger_with_config(log_dir, app_name="test_retention_1", retention_days=1) as root:
        file_handler = _file_handlers_of(root)[0]
        clock.advance_days(1)
        file_handler.emit(_record("next day"))
        assert not stale_file.exists()
        assert (log_dir / "app-2026-05-25.log").exists()


def test_rollover_failure_does_not_raise_into_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR-1 同一类失败：日切开不出新文件时，日志调用绝不能把异常抛回业务代码。"""
    log_dir = tmp_path / "logs"
    clock = _ShiftedClockDatetime(datetime(2026, 5, 24, 23, 59))
    monkeypatch.setattr(logger_impl_module, "datetime", clock)

    with _logger_with_config(log_dir, app_name="test_roll_fail") as root:
        file_handler = _file_handlers_of(root)[0]
        file_handler.emit(_record("before rollover"))
        clock.advance_days(1)
        # 目录改成只读：新文件开不出来，旧文件仍可写
        log_dir.chmod(0o500)
        try:
            file_handler.emit(_record("still writing"))
            file_handler.emit(_record("still writing again"))
        finally:
            log_dir.chmod(0o700)

        day_one_text = (log_dir / "app-2026-05-24.log").read_text(encoding="utf-8")
        assert "still writing" in day_one_text, "日切失败时记录必须仍写进旧文件，不能抛给调用方"
        assert (log_dir / "app-2026-05-25.log").exists() is False
        assert file_handler.baseFilename == str(log_dir / "app-2026-05-24.log")
        warning_lines = [
            line for line in capsys.readouterr().out.splitlines() if "日志跨天切换失败" in line
        ]
        assert len(warning_lines) == 1, f"同一天的切换失败只该提示一次：{warning_lines}"

        # 目录恢复后，下一条记录自动补上真正的跨天
        file_handler.emit(_record("recovered"))
        assert "recovered" in (log_dir / "app-2026-05-25.log").read_text(encoding="utf-8")


class _FlushFailingStream:
    """旧日志流的替身：写入可以成功，但 flush / close 抛 OSError（模拟 ENOSPC / EIO）。"""

    def __init__(self) -> None:
        self.buffer: list[str] = []
        self.closed = False

    def write(self, message: str) -> int:
        self.buffer.append(message)
        return len(message)

    def flush(self) -> None:
        raise OSError("No space left on device")

    def close(self) -> None:
        self.closed = True


def test_rollover_survives_old_stream_flush_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-2 兜底的另一半：旧流收尾失败也不能把异常抛回业务代码，且切换照常完成。"""
    log_dir = tmp_path / "logs"
    clock = _ShiftedClockDatetime(datetime(2026, 5, 24, 23, 59))
    monkeypatch.setattr(logger_impl_module, "datetime", clock)

    with _logger_with_config(log_dir, app_name="test_flush_fail") as root:
        file_handler = _file_handlers_of(root)[0]
        file_handler.emit(_record("before rollover"))
        file_handler.stream = _FlushFailingStream()
        clock.advance_days(1)

        file_handler.emit(_record("after rollover"))

        next_day_file = log_dir / "app-2026-05-25.log"
        assert "after rollover" in next_day_file.read_text(encoding="utf-8")
        assert file_handler.baseFilename == str(next_day_file)
        assert file_handler._current_date == "2026-05-25"  # noqa: SLF001


def test_log_retention_days_field_default_and_clamp() -> None:
    """FR-3：默认 14，可配置；非正数与无法解析的值都回退，且不让进程起不来。"""
    assert AppSettings().log_retention_days == 14
    assert AppSettings(log_retention_days=7).log_retention_days == 7
    assert AppSettings(log_retention_days="7").log_retention_days == 7
    assert AppSettings(log_retention_days=0).log_retention_days == 14
    assert AppSettings(log_retention_days=-3).log_retention_days == 14
    assert AppSettings(log_retention_days="abc").log_retention_days == 14
    assert AppSettings(log_retention_days="").log_retention_days == 14


def test_retention_days_env_name_feeds_the_daily_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-3：env 名是 ``LOG_RETENTION_DAYS``，且该值会交给负责跨天清理的 handler。

    清理本身按配置值删文件由 :func:`test_rollover_sweeps_logs_beyond_configured_retention`
    覆盖；这里只补它没有的那一段——配置键的 env 名字与它到 handler 的接线。
    """
    monkeypatch.setenv("LOG_RETENTION_DAYS", "3")
    assert AppSettings().log_retention_days == 3

    with _logger_with_config(
        tmp_path / "logs", app_name="test_env_retention", retention_days=3
    ) as root:
        assert _file_handlers_of(root)[0]._retention_days == 3  # noqa: SLF001


def test_setup_is_idempotent(tmp_path: Path) -> None:
    """FR-5：连续两次初始化不叠加 handler。"""
    with _logger_with_config(tmp_path, app_name="test_idempotent") as root:
        handlers_after_first_setup = list(root.handlers)
        Logger()._setup_logger()
        assert len(root.handlers) == len(handlers_after_first_setup)
        assert _keda_handler_count(root) == 2
        assert _file_handlers_of(root)


def test_rebuilt_logger_closes_handlers_no_longer_visible_on_root(tmp_path: Path) -> None:
    """FR-5：root 被外部清空过（旧 handler 不再挂在 root 上）时，重建 Logger 不留下第二个写入者。"""
    with _logger_with_config(tmp_path / "logs", app_name="test_rebuild") as root:
        stale_handlers = list(root.handlers)
        stale_file_handlers = [
            handler for handler in stale_handlers if isinstance(handler, logging.FileHandler)
        ]
        assert stale_file_handlers, "首次 setup 应挂上日文件 handler"
        for handler in stale_handlers:
            root.removeHandler(handler)  # 只摘不关：模拟旧单例引用复活的状态
        Logger._instance = None
        Logger._logger = None
        Logger().get_logger()
        assert _keda_handler_count(root) == 2, "重建后 root 上仍只应有一套 keda handler"
        assert len(_file_handlers_of(root)) == 1, "不应出现两个日文件写入者"
        assert all(
            handler.stream is None for handler in stale_file_handlers
        ), "旧的日文件句柄必须被关闭，否则每次重建都漏一个 fd"


def test_existing_handler_root_still_attaches_keda_handlers(tmp_path: Path) -> None:
    """FR-5：root 上已有第三方 handler 时仍挂 keda handler，且不移除第三方。"""
    with _logger_with_config(tmp_path / "logs", app_name="test_existing_handler") as root:
        third_party_handler = logging.StreamHandler()
        root.addHandler(third_party_handler)
        # 模拟"第三方先碰过 root，keda 才首次初始化"：清掉单例引用，让
        # _setup_logger 在 root 非空的状态下重新执行。
        Logger._logger = None
        Logger().get_logger()
        assert _file_handlers_of(root), "root 非空时 keda 文件 handler 仍应挂载"
        assert third_party_handler in root.handlers, "不应移除别人的 handler"
        assert _keda_handler_count(root) == 2


def test_daily_log_path_is_single_source(tmp_path: Path) -> None:
    """FR-4：日文件的目录与文件名都只有一个产出点，各层消费同一对象。"""
    assert engines_factory.daily_log_path is daily_log_path
    assert agent_runner_factory.daily_log_path is daily_log_path
    assert cli_registry.daily_log_path is daily_log_path

    log_dir = tmp_path / "logs"
    today = datetime.now().strftime("%Y-%m-%d")
    assert daily_log_path(log_dir) == log_dir / f"app-{today}.log"
    assert daily_log_path() == logger_impl_module.daily_log_dir() / f"app-{today}.log"


class _ConsoleCollector:
    """替掉 ``rich`` console，收集打印内容。"""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.chunks.append(" ".join(str(arg) for arg in args))


def _capture_fallback_hint() -> str:
    """调用真实的 ``_print_logs_fallback``，取回它打印的日文件路径。

    只有"托管进程记录存储"被打桩成没有任何记录（与本次改动无关的依赖），用来确定性地
    走到回退分支；被调用的是真实函数与真实的 ``daily_log_path``。
    """
    collector = _ConsoleCollector()
    stub_supervisor = MagicMock()
    stub_supervisor.list_processes.return_value = []
    with (
        patch.object(cli_registry, "console", collector),
        patch.object(cli_registry, "create_process_supervisor", return_value=stub_supervisor),
    ):
        cli_registry._print_logs_fallback("evidence-repo", "daemon")
    printed_text = "\n".join(collector.chunks)
    match = re.search(r"Global app log: \[cyan\](.+?)\[/\]", printed_text)
    assert match is not None, f"回退提示里没有 Global app log 行: {printed_text!r}"
    return match.group(1)


def test_cli_registry_fallback_reuses_shared_daily_path(tmp_path: Path) -> None:
    """FR-4：CLI 回退提示不再自行拼日文件名，与日志模块逐字符一致。"""
    registry_source = Path(cli_registry.__file__).read_text(encoding="utf-8")
    assert 'f"app-' not in registry_source, "cli_registry 不应再自行拼日文件名"

    log_dir = tmp_path / "logs"
    assert str(daily_log_path(log_dir)) == str(cli_registry.daily_log_path(log_dir))


def test_cli_registry_fallback_follows_configured_log_dir(tmp_path: Path) -> None:
    """FR-4：``LOG_FILE`` 把日志目录指到别处时，回退提示跟着走，不再指向没人写的文件。"""
    custom_dir = tmp_path / "elsewhere"
    with _logger_with_config(custom_dir, app_name="test_hint_follows_config") as root:
        file_handler = _file_handlers_of(root)[0]
        assert str(file_handler.baseFilename).startswith(str(custom_dir))
        assert _capture_fallback_hint() == file_handler.baseFilename

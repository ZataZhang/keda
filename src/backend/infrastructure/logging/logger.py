"""Application logging configuration."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.infrastructure.config.settings import config

#: 日文件命名约定 ``app-YYYY-MM-DD.log`` 的日期部分，被 ``kc logs`` 回退提示消费
_DAILY_LOG_DATE_FORMAT = "%Y-%m-%d"
#: keda 自挂载 handler 的私有标记属性名：幂等只认自己，不误伤第三方 handler
_KEDA_HANDLER_ATTR = "_keda_handler"
#: 配置值非法时的降级目标级别，与默认级别一致以保持既有可见性
_FALLBACK_LOG_LEVEL_NAME = "INFO"
#: `_resolve_log_level` 的三种解析结局
_LOG_LEVEL_EXACT = "exact"
_LOG_LEVEL_NORMALIZED = "normalized"
_LOG_LEVEL_INVALID = "invalid"


def daily_log_dir() -> Path:
    """返回日文件真正写入的目录。

    Returns:
        由 ``config.log_file`` 推出的目录，即日志目录的唯一来源：消费方一律取用它，
        不再自行拼 ``<项目根>/logs``。这里刻意取绝对路径——长驻进程跑过一次
        ``os.chdir()`` 后，相对目录会让日切把新文件开到别处，保留清理也会跟着误删。
    """
    return Path(os.path.abspath(config.log_file)).parent


def daily_log_path(log_dir: Path | None = None) -> Path:
    """返回当天日文件的路径。

    Args:
        log_dir: 存放日文件的目录。缺省时取配置里日志模块真正在写的目录。

    Returns:
        对应当前本地日期的 ``app-YYYY-MM-DD.log`` 路径。
    """
    target_dir = daily_log_dir() if log_dir is None else log_dir
    return target_dir / f"app-{_current_date()}.log"


def _current_date() -> str:
    return datetime.now().strftime(_DAILY_LOG_DATE_FORMAT)


def _resolve_log_level(configured_level: str | None) -> tuple[int, str]:
    """解析配置的日志级别，任何输入都不抛异常。

    Args:
        configured_level: 来自 env 或配置文件的原始 ``log_level`` 值。

    Returns:
        ``(level, resolution)`` 二元组。``resolution`` 为 ``"exact"`` 表示原样就是
        ``logging`` 预定义名；``"normalized"`` 表示只是大小写或空格写错、需要归一化；
        ``"invalid"`` 表示完全无法解析，改用 ``logging.INFO``。不抛异常，所以一个写错
        的级别值不再能让进程启动失败。
    """
    raw_level_name = str(configured_level or "")
    normalized_name = raw_level_name.strip().upper()
    resolved_level = getattr(logging, normalized_name, None) if normalized_name else None
    if not isinstance(resolved_level, int) or isinstance(resolved_level, bool):
        return getattr(logging, _FALLBACK_LOG_LEVEL_NAME), _LOG_LEVEL_INVALID
    if raw_level_name == normalized_name:
        return resolved_level, _LOG_LEVEL_EXACT
    return resolved_level, _LOG_LEVEL_NORMALIZED


def _log_level_resolution_notice(configured_level: str | None, resolution: str, level: int) -> None:
    """把手误写错的日志级别显式说出来，但不改变操作者要的那一档。

    Args:
        configured_level: 配置里的原始值。
        resolution: :func:`_resolve_log_level` 给出的解析结局。
        level: 实际生效的级别。

    ``"normalized"`` 保留操作者要的那一档（只是大小写或空格被归一化），但仍然出声：
    FR-1 承诺写错的级别要留下痕迹，默默接受就会让人无从查起。提示里显示生效级别的
    规范名，因此 ``warn`` 这类别名报成 ``WARNING``，而不是把别名原样吐回去。

    提示按 ``max(level, WARNING)`` 发出：这条提示是给配置写错的人看的，如果固定用
    WARNING 而生效级别是 ERROR/CRITICAL，它会被刚设的门槛过滤掉，缺陷就又变成静默。
    """
    module_logger = logging.getLogger(__name__)
    notice_level = max(level, logging.WARNING)
    if resolution == _LOG_LEVEL_INVALID:
        module_logger.log(
            notice_level, "无效日志级别 %r，已降级为 %s", configured_level, _FALLBACK_LOG_LEVEL_NAME
        )
    elif resolution == _LOG_LEVEL_NORMALIZED:
        module_logger.log(
            notice_level,
            "日志级别 %r 不是 logging 预定义名，已按 %s 解析",
            configured_level,
            logging.getLevelName(level),
        )


#: 本进程里 keda 自己创建过、还没交接出去的 handler。重复 setup 时按这份清单收尾，
#: 而不是只看 root 上现在挂着什么——否则一旦 root 的 handler 列表被外部清过（旧的
#: 单例引用复活、或别人 removeHandler 但没 close），日文件句柄会泄漏并再叠一个 writer。
_installed_keda_handlers: list[logging.Handler] = []


def _remove_keda_handlers(root_logger: logging.Logger) -> None:
    """移除本进程此前挂上的 keda handler，使重复 setup 不会叠加。

    Args:
        root_logger: 待清理的 root logger；第三方 handler 一律保留。
    """
    for handler in list(root_logger.handlers):
        if not getattr(handler, _KEDA_HANDLER_ATTR, False):
            continue
        root_logger.removeHandler(handler)
        handler.close()
    for handler in _installed_keda_handlers:
        if handler not in root_logger.handlers:
            handler.close()
    del _installed_keda_handlers[:]


def _cleanup_old_logs(log_dir: Path, keep_days: int) -> None:
    """删除早于 ``keep_days`` 保留期的日文件。

    Args:
        log_dir: 存放日文件的目录。
        keep_days: 保留多少天的日志。
    """
    cutoff = datetime.now() - timedelta(days=keep_days)
    for path in log_dir.glob("app-*.log"):
        try:
            date_str = path.stem.replace("app-", "")
            file_date = datetime.strptime(date_str, _DAILY_LOG_DATE_FORMAT)
            if file_date < cutoff:
                os.remove(path)
        except (ValueError, OSError):
            pass


class _DailyFileHandler(logging.FileHandler):
    """日期变化时重开当天日文件的 FileHandler。

    长驻进程不能一直往昨天的文件里写，保留清理也必须跟着日期推进，而不是只在启动
    时跑一次。文件名继续是 ``app-YYYY-MM-DD.log``（不是 ``TimedRotatingFileHandler``
    默认的 ``app.log.<date>`` 后缀），这样 ``kc logs`` 与 registry 的回退提示始终
    指向真正在被写的那个文件。
    """

    def __init__(self, log_dir: Path, retention_days: int) -> None:
        """打开 ``log_dir`` 下的当天日文件。

        Args:
            log_dir: 存放日文件的目录。
            retention_days: 每次跨天时保留多少天的日文件。
        """
        super().__init__(filename=str(daily_log_path(log_dir)), encoding="utf-8")
        self._log_dir = log_dir
        self._retention_days = retention_days
        self._current_date = _current_date()
        self._failed_rollover_date: str | None = None
        setattr(self, _KEDA_HANDLER_ATTR, True)

    def emit(self, record: logging.LogRecord) -> None:
        """写入 ``record``，日期已经跨天时先切到当天的文件。"""
        today = _current_date()
        if today != self._current_date:
            self._roll_to_new_day(today)
        super().emit(record)

    def _roll_to_new_day(self, new_date: str) -> None:
        """重开 ``new_date`` 对应的文件，并顺带清理过期的日文件。

        日切可能因为权限、只读挂载或 fd 耗尽而失败。这时保留旧 stream 继续写，并只按
        目标日期提示一次：不把异常抛回调用 ``logger.info()`` 的业务代码（那是本 PRD
        要消除的失败类别），也不走 ``handleError``——后者会让一个写日志密集的进程
        每条记录都吐一坨 traceback。

        Args:
            new_date: handler 需要跟到的 ``YYYY-MM-DD`` 日期串。
        """
        previous_base_filename = self.baseFilename
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self.baseFilename = os.fspath(daily_log_path(self._log_dir))
            new_stream = self._open()
        except OSError as error:
            self.baseFilename = previous_base_filename
            if self._failed_rollover_date != new_date:
                self._failed_rollover_date = new_date
                print(f"Warning: 日志跨天切换失败，继续写入 {previous_base_filename}: {error}")
            return
        if self.stream:
            try:
                self.flush()
                self.stream.close()
            except OSError:
                # 旧流收尾失败（写缓冲落盘时的 ENOSPC / EIO）不该挡住已经开好的新文件，
                # 也不该抛回业务代码；新流照常接管，最坏是丢掉旧文件里最后没刷出去的一段。
                pass
            self.stream = None
        self.stream = new_stream
        self._current_date = new_date
        self._failed_rollover_date = None
        _cleanup_old_logs(self._log_dir, keep_days=self._retention_days)


class Logger:
    """Singleton logger manager with daily log files."""

    _instance: Logger | None = None
    _logger: logging.Logger | None = None

    def __new__(cls) -> Logger:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._logger is None:
            self._setup_logger()

    def _setup_logger(self) -> None:
        log_level, level_resolution = _resolve_log_level(config.log_level)

        self._logger = logging.getLogger(config.app_name)
        self._logger.setLevel(log_level)

        root = logging.getLogger()
        _remove_keda_handlers(root)

        root.setLevel(log_level)

        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        if hasattr(console_handler.stream, "reconfigure"):
            try:
                console_handler.stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        setattr(console_handler, _KEDA_HANDLER_ATTR, True)
        root.addHandler(console_handler)
        _installed_keda_handlers.append(console_handler)

        try:
            log_dir = daily_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)

            file_handler = _DailyFileHandler(log_dir, retention_days=config.log_retention_days)
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
            _installed_keda_handlers.append(file_handler)

            _cleanup_old_logs(log_dir, keep_days=config.log_retention_days)
        except (OSError, PermissionError) as error:
            print(f"Warning: 无法创建日志文件处理器: {error}")

        _log_level_resolution_notice(config.log_level, level_resolution, log_level)

    def get_logger(self) -> logging.Logger:
        """Return the underlying ``logging.Logger`` instance."""
        if self._logger is None:
            self._setup_logger()
        return self._logger

    def __getattr__(self, name: str) -> Any:
        if self._logger is None:
            self._setup_logger()
        return getattr(self._logger, name)


logger = Logger()

__all__ = ["Logger", "daily_log_dir", "daily_log_path", "logger"]

"""kc 启动更新检查：对照 PyPI 最新版，仅在交互式终端询问是否升级。

行为边界（Issue #264）：

- 更新检查永远不得让原命令失败：网络短超时、离线静默、最外层兜底吞异常。
- 机器路径整段跳过，逐字节保持原样：机器模式（``--json`` / ``--output json``）、
  shell 补全协议（补全环境变量存在）、``--help`` / ``-h``、``--version``（入口已
  先行短路）、非交互终端（stdin 或 stderr 不是 TTY），以及显式关闭
  ``KEDACODE_NO_UPDATE_CHECK=1``（旧名 ``IAR_NO_UPDATE_CHECK`` 双读）。
- 结果缓存到 ``<状态目录>/update-check.json``，默认 24 小时内不再访问 PyPI；
  缓存记录绑定了当时的已安装版本，升级后自动失效。
- 询问后仅对「可确认的 PyPI 托管安装」（uv tool / pipx / Homebrew / venv pip /
  user-site pip）直接执行对应升级命令；来源不是 PyPI（源码、editable、tarball
  直链）或识别不出安装方式时，只打印可复制的命令，绝不猜测执行。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import site
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Callable, Mapping, Sequence

import typer
from packaging.version import InvalidVersion, Version

from backend.api.cli_console import error_console
from backend.api.cli_output import json_literal, machine_output_requested
from backend.api.version_info import _DISTRIBUTION_NAME, _UNKNOWN_VERSION, resolve_keda_version
from backend.core.shared.models import product_identity

__all__ = [
    "UpdateNotice",
    "check_for_update",
    "detect_upgrade_command",
    "fetch_latest_pypi_version",
    "startup_update_check",
]

#: PyPI 项目 JSON 端点；``info.version`` 即最新稳定版（发布时勾选 pre-release
#: 的版本不会成为该字段，除非项目只有预发布）。
_PYPI_LATEST_URL = f"https://pypi.org/pypi/{_DISTRIBUTION_NAME}/json"

#: 单次网络访问的硬超时（秒），宁可漏报也不拖慢任何一条 kc 命令。
_HTTP_TIMEOUT_SECONDS = 2.0

#: 两次 PyPI 访问之间的最小间隔（秒）。
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

#: 检查结果缓存文件名，落在 :func:`product_identity.state_home` 下。
_CACHE_FILENAME = "update-check.json"

#: 关闭开关后缀：``KEDACODE_NO_UPDATE_CHECK=1``（经产品双读含旧名 ``IAR_*``）。
_DISABLE_ENV_SUFFIX = "NO_UPDATE_CHECK"

#: 识别不出安装方式时给出的可复制命令清单（覆盖安装文档里的全部路径）。
_FALLBACK_UPGRADE_COMMANDS: tuple[str, ...] = (
    "uv tool install --force kedacode",
    "pipx install --force kedacode",
    "brew upgrade kedacode",
    "pip install --upgrade kedacode",
)


@dataclass(frozen=True)
class UpdateNotice:
    """一次「发现新版本」的结论。

    Attributes:
        current_version: 当前安装的 ``kedacode`` 版本。
        latest_version: PyPI 上的最新版本。
    """

    current_version: str
    latest_version: str


@dataclass(frozen=True)
class UpgradeCommand:
    """可按当前安装方式直接执行的升级命令。

    Attributes:
        argv: 传给 :func:`subprocess.run` 的参数序列。
    """

    argv: tuple[str, ...]

    @property
    def display(self) -> str:
        """可复制粘贴的命令行文本。"""
        return shlex.join(self.argv)


def _truthy_env_flag(environ: Mapping[str, str] | None = None) -> bool:
    """``NO_UPDATE_CHECK`` 是否被显式打开（值 ``1`` 视为关闭更新检查）。"""
    value = product_identity.read_product_env_value(_DISABLE_ENV_SUFFIX, environ)
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _is_interactive_terminal(stdin: object = None, stderr: object = None) -> bool:
    """stdin 与 stderr 都是 TTY 才认为「人在终端前」。

    提示与询问全部走 stderr（stdout 留给命令数据），所以只要求 stderr 为 TTY、
    不要求 stdout：管道场景（``kc run | tee``）下人仍然看得到问得到。
    """
    stdin_stream = sys.stdin if stdin is None else stdin
    stderr_stream = sys.stderr if stderr is None else stderr
    try:
        return bool(stdin_stream.isatty() and stderr_stream.isatty())
    except (AttributeError, ValueError):
        return False


def _should_skip_startup_update_check(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """判定本次调用是否属于「更新检查必须整段不发生」的路径。

    Args:
        argv: 传给 :func:`backend.api.cli_typer_app.main` 的原始参数。
        environ: 环境变量映射；省略时取 :data:`os.environ`。

    Returns:
        真值表示跳过检查（不访问网络、不提示、不询问）。
    """
    source_environ = os.environ if environ is None else environ
    if source_environ.get(product_identity.COMPLETION_ENV_VAR_NAME):
        return True
    if _truthy_env_flag(source_environ):
        return True
    if machine_output_requested(list(argv)):
        return True
    if "--help" in argv or "-h" in argv or "--version" in argv or "-V" in argv:
        return True
    return not _is_interactive_terminal()


def _cache_path() -> Path:
    """更新检查缓存文件的绝对路径（不创建目录）。"""
    return product_identity.state_home() / _CACHE_FILENAME


def _read_cache_record() -> dict[str, object] | None:
    """读取缓存记录；文件缺失或内容不可解析时返回 ``None``（视为无缓存）。"""
    try:
        raw_text = _cache_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        record = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def _write_cache_record(
    *,
    checked_at: float,
    current_version: str,
    latest_version: str | None,
) -> None:
    """把本次检查结论落盘；缓存目录不可写时静默放弃（下次照常检查）。"""
    record = {
        "checked_at": checked_at,
        "current_version": current_version,
        "latest_version": latest_version,
    }
    cache_path = _cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json_literal(record), encoding="utf-8")
    except OSError:
        return


def _is_newer(latest_version: str, current_version: str) -> bool:
    """按 PEP 440 判断 PyPI 版本是否比已安装版本新；解析不了的组合一律不升级。"""
    try:
        return Version(latest_version) > Version(current_version)
    except InvalidVersion:
        return False


def fetch_latest_pypi_version(timeout_seconds: float = _HTTP_TIMEOUT_SECONDS) -> str | None:
    """从 PyPI JSON 端点取 ``kedacode`` 最新发布版本；任何失败返回 ``None``。

    Args:
        timeout_seconds: 单次请求超时（秒）。

    Returns:
        版本字符串；离线、超时、响应异常时为 ``None``，调用方据此静默继续。
    """
    try:
        request = urllib.request.Request(
            _PYPI_LATEST_URL,
            headers={"User-Agent": f"{_DISTRIBUTION_NAME}-cli-update-check"},
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 更新检查不得因网络失败影响命令执行。
        return None
    version_text = payload.get("info", {}).get("version") if isinstance(payload, dict) else None
    return str(version_text) if version_text else None


def check_for_update(
    *,
    now_epoch: float | None = None,
    fetch_latest: Callable[[], str | None] = fetch_latest_pypi_version,
) -> UpdateNotice | None:
    """查一次「是否有新版本」，带 TTL 缓存；离线或版本不旧于当前时返回 ``None``。

    缓存命中条件：记录未过期、且记录里绑定的已安装版本与当前一致。升级后
    版本变化会让旧缓存自动失效，避免刚升完还被追问。

    Args:
        now_epoch: 判定缓存是否过期的时间戳；省略时取当前时间。
        fetch_latest: 取 PyPI 最新版的函数（测试注入口）。

    Returns:
        :class:`UpdateNotice` 表示确实有更新的版本；否则 ``None``。
    """
    current_version = resolve_keda_version()
    if current_version == _UNKNOWN_VERSION:
        return None
    checked_at = time.time() if now_epoch is None else now_epoch

    cached = _read_cache_record()
    if (
        isinstance(cached, dict)
        and cached.get("current_version") == current_version
        and checked_at - float(cached.get("checked_at") or 0.0) < _CHECK_INTERVAL_SECONDS
    ):
        latest_value = cached.get("latest_version")
        if latest_value is None:
            return None
        latest_version = str(latest_value)
    else:
        latest_version_opt = fetch_latest()
        _write_cache_record(
            checked_at=checked_at,
            current_version=current_version,
            latest_version=latest_version_opt,
        )
        if latest_version_opt is None:
            return None
        latest_version = latest_version_opt

    if not _is_newer(latest_version, current_version):
        return None
    return UpdateNotice(current_version=current_version, latest_version=latest_version)


def _installed_from_non_pypi_source() -> bool:
    """当前发行版是否带着 ``direct_url.json``（本地路径 / URL / editable 安装）。

    带该记录的版本不受 PyPI 托管，``upgrade`` 类命令会原地重装同一个旧来源，
    因此只能给可复制命令、不能自动执行。
    """
    try:
        distribution = importlib_metadata.distribution(_DISTRIBUTION_NAME)
    except importlib_metadata.PackageNotFoundError:
        return True
    try:
        return distribution.read_text("direct_url.json") is not None
    except OSError:
        return True


def detect_upgrade_command() -> UpgradeCommand | None:
    """按当前安装痕迹识别可自动执行的升级命令；识别不出返回 ``None``。

    识别顺序：uv tool 隔离环境 → pipx 隔离环境 → Homebrew prefix → venv 内
    pip → user-site pip。任何一支都要求对应安装器可执行文件存在，避免给出
    跑不起来的命令。

    Returns:
        :class:`UpgradeCommand` 表示可以放心执行；``None`` 时调用方打印
        :data:`_FALLBACK_UPGRADE_COMMANDS` 供用户自行选择。
    """
    if _installed_from_non_pypi_source():
        return None
    prefix_path = sys.prefix
    if "/uv/tools/" in prefix_path and shutil.which("uv"):
        return UpgradeCommand(("uv", "tool", "upgrade", _DISTRIBUTION_NAME))
    if "/pipx/venvs/" in prefix_path and shutil.which("pipx"):
        return UpgradeCommand(("pipx", "upgrade", _DISTRIBUTION_NAME))
    if "/Cellar/" in prefix_path and shutil.which("brew"):
        return UpgradeCommand(("brew", "upgrade", _DISTRIBUTION_NAME))
    if sys.prefix != sys.base_prefix:
        pip_spec = importlib.util.find_spec("pip")
        if pip_spec is not None:
            return UpgradeCommand(
                (sys.executable, "-m", "pip", "install", "--upgrade", _DISTRIBUTION_NAME)
            )
        return None
    if site.USER_SITE and any(
        Path(entry).expanduser() == Path(site.USER_SITE) for entry in sys.path if entry
    ):
        return UpgradeCommand(
            (sys.executable, "-m", "pip", "install", "--user", "--upgrade", _DISTRIBUTION_NAME)
        )
    return None


def _print_available_notice(notice: UpdateNotice, upgrade_hint: str | None) -> None:
    """把「发现新版本」的结论写到 stderr（stdout 保持给命令数据）。"""
    headline = (
        f"{product_identity.PRODUCT_DISPLAY_NAME} {notice.latest_version} is available "
        f"(you have {notice.current_version})."
    )
    if upgrade_hint is None:
        error_console.print(headline, markup=False, soft_wrap=True)
        error_console.print(
            "Could not auto-detect how kc was installed. Upgrade with the command "
            "matching your install method:",
            markup=False,
            soft_wrap=True,
        )
        for command_text in _FALLBACK_UPGRADE_COMMANDS:
            error_console.print(f"  {command_text}", markup=False, soft_wrap=True)
        error_console.print(
            "Docs: docs/getting-started/installation.md", markup=False, soft_wrap=True
        )
    else:
        error_console.print(
            f"{headline} Upgrade with: {upgrade_hint}", markup=False, soft_wrap=True
        )


def _handle_update_notice(notice: UpdateNotice) -> None:
    """呈现新版本并按安装方式询问、执行或给出命令。"""
    plan = detect_upgrade_command()
    if plan is None:
        _print_available_notice(notice, upgrade_hint=None)
        return
    _print_available_notice(notice, upgrade_hint=plan.display)
    confirmed = typer.confirm(f"Run `{plan.display}` now?", default=False, err=True)
    if not confirmed:
        error_console.print(
            "Not upgraded. Re-run the command above whenever you like.",
            markup=False,
            soft_wrap=True,
        )
        return
    result = subprocess.run(list(plan.argv))
    if result.returncode == 0:
        error_console.print(
            f"Upgraded {_DISTRIBUTION_NAME} to {notice.latest_version}; "
            "restart kc to use the new version.",
            markup=False,
            soft_wrap=True,
        )
    else:
        error_console.print(
            f"Upgrade command failed with exit code {result.returncode}. "
            f"Run it manually: {plan.display}",
            markup=False,
            soft_wrap=True,
        )


def startup_update_check(argv: Sequence[str]) -> None:
    """CLI 启动钩子：满足条件时检查 PyPI 更新并在交互终端询问是否升级。

    本函数**永不抛出**：任何未预期异常都被兜底吞掉，保证更新检查不影响
    真正的命令执行（Issue #264 的「离线时静默继续」扩展到一切失败）。

    Args:
        argv: 传给 :func:`backend.api.cli_typer_app.main` 的原始参数。
    """
    try:
        if _should_skip_startup_update_check(argv):
            return
        notice = check_for_update()
        if notice is None:
            return
        _handle_update_notice(notice)
    except Exception:  # noqa: BLE001 - 更新检查的任何失败不得影响命令执行。
        return

"""kc 启动时自动同步随包 operator Skill：升级发行包后无需再手动 ``kc skill install``。

动机：``kc`` 经 pip / uv / pipx 安装或升级后，随包的 ``kedacode-operator`` Skill 只落在
wheel 里，用户级安装根（``~/.claude`` 等）中的副本仍是旧版，过去只能靠手动跑
``kc skill install`` 同步。本钩子在 CLI 启动时按**已安装包版本**与上次同步记录比对，
版本变化才把随包 operator Skill 刷新到全部安装根，之后同一版本不再重复触碰文件系统。

行为边界（与 :mod:`backend.api.cli_update_check` 的启动检查保持一致）：

- 自动同步**永不阻断命令**：任何未预期异常都被兜底吞掉。
- 机器路径整段跳过：复用 ``_should_skip_startup_update_check``，即机器模式
  （``--json``）、shell 补全协议、``--help`` / ``--version``、无参数、非交互终端、
  以及 ``KEDACODE_NO_UPDATE_CHECK=1`` 全部原样不发生；另支持专用关闭开关
  ``KEDACODE_NO_SKILL_SYNC=1``。
- 只刷新**随包 operator Skill**，绝不下载或覆盖 ``prd`` / ``code-reviewer`` 等远程模板，
  用户改过的 operator 副本按 ``force=False`` 保留（``preserve-conflict``），不会被破坏。
- 结果记录到 ``<状态目录>/skill-sync.json``，仅缓存「上次已同步的包版本」；离线无关，
  本钩子不访问网络。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping, Sequence

from backend.api.cli_console import error_console
from backend.api.cli_skill import sync_packaged_operator_skills
from backend.api.cli_update_check import _should_skip_startup_update_check
from backend.api.version_info import _UNKNOWN_VERSION, resolve_keda_version
from backend.core.shared.models import product_identity

__all__ = ["startup_skill_sync"]

#: 同步记录缓存文件名，落在 :func:`product_identity.state_home` 下。
_CACHE_FILENAME = "skill-sync.json"

#: 专用关闭开关后缀：``KEDACODE_NO_SKILL_SYNC=1``。
_DISABLE_ENV_SUFFIX = "NO_SKILL_SYNC"

#: 实际改动了文件系统的安装动作；这些动作才值得向人提示。
_MUTATING_ACTIONS = frozenset({"install", "refresh", "overwrite"})


def _cache_path() -> Path:
    """同步记录缓存文件的绝对路径（不创建目录）。"""
    return product_identity.state_home() / _CACHE_FILENAME


def _read_cached_synced_version() -> str | None:
    """读取上次已同步版本；文件缺失或内容不可解析时返回 ``None``（视为从未同步）。"""
    try:
        record = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    synced_version = record.get("synced_version")
    return synced_version if isinstance(synced_version, str) else None


def _write_cached_synced_version(*, synced_version: str) -> None:
    """把本次同步到的包版本落盘；缓存目录不可写时静默放弃（下次照常同步）。"""
    cache_path = _cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"synced_version": synced_version}),
            encoding="utf-8",
        )
    except OSError:
        return


def _truthy_disable_flag(environ: Mapping[str, str] | None = None) -> bool:
    """``NO_SKILL_SYNC`` 是否被显式打开（值 ``1`` 视为关闭自动同步）。"""
    value = product_identity.read_product_env_value(_DISABLE_ENV_SUFFIX, environ)
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _needs_sync(
    *, argv: Sequence[str], current_version: str, environ: Mapping[str, str] | None
) -> bool:
    """判定本次调用是否应执行随包 Skill 同步。

    Args:
        argv: 传给 :func:`backend.api.cli_typer_app.main` 的原始参数。
        current_version: 当前安装的 ``kedacode`` 版本。
        environ: 环境变量映射；省略时取 :data:`os.environ`。

    Returns:
        真值表示需要同步（机器/帮助/非交互路径、显式关闭、版本未变化均为 ``False``）。
    """
    source_environ = os.environ if environ is None else environ
    if _should_skip_startup_update_check(list(argv), environ=source_environ):
        return False
    if _truthy_disable_flag(source_environ):
        return False
    if current_version == _UNKNOWN_VERSION:
        return False
    return _read_cached_synced_version() != current_version


def _print_refresh_notice(*, updated_count: int, synced_version: str) -> None:
    """把「随包 operator Skill 已自动更新」的结论写到 stderr（stdout 留给命令数据）。"""
    error_console.print(
        f"Auto-synced the packaged KedaCode operator skill ({updated_count} install "
        f"root(s)) to kedacode {synced_version}; no need to run `kc skill install`.",
        markup=False,
        soft_wrap=True,
    )


def startup_skill_sync(argv: Sequence[str]) -> None:
    """CLI 启动钩子：包版本变化时把随包 operator Skill 静默刷新到全部安装根。

    本函数**永不抛出**：任何未预期异常都被兜底吞掉，保证自动同步不影响真正的命令
    执行（对齐 :func:`backend.api.cli_update_check.startup_update_check` 的失败策略）。

    Args:
        argv: 传给 :func:`backend.api.cli_typer_app.main` 的原始参数。
    """
    try:
        current_version = resolve_keda_version()
        if not _needs_sync(argv=argv, current_version=current_version, environ=None):
            return
        results = sync_packaged_operator_skills()
        _write_cached_synced_version(synced_version=current_version)
        updated_count = sum(1 for result in results if result.action in _MUTATING_ACTIONS)
        if updated_count:
            _print_refresh_notice(updated_count=updated_count, synced_version=current_version)
    except Exception:  # noqa: BLE001 - 自动同步的任何失败不得影响命令执行。
        return

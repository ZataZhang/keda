"""本机状态目录迁移：把旧 ``~/.iar``（legacy-alias）搬到 ``~/.kedacode``，旧位置留一个相对链接。

改名后程序**双读**两套位置（新名优先），所以不迁移也能继续用；本模块负责用户
显式请求的那次搬迁。搬迁会动到管理终端的历史库、仓库注册表与锁文件，因此硬规则
是「有任何自有进程在跑就拒绝动手」：锁文件里存活的 PID、``processes.json`` 里存活
的受管记录、psutil 扫到的以三个自有名字之一启动的进程，任一命中即零改动退出。

预演（``dry_run=True``）与正式执行共用同一套预检与结论，预演永不写盘；因此预演
在被拒绝的场景里返回与正式执行相同的结论，而不是永远报成功。

依赖方向：本模块在 ``engines/``，只引用 ``core.shared.models.product_identity``
的状态目录解析与标准库/三方库，不反向依赖 ``api`` 的退出码——对外结论以
:data:`StateHomeOutcome` 表达，由 api 层映射成 :class:`ExitCode`。
"""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomlkit

from backend.core.shared.models import product_identity

#: 状态目录下的默认文件名，与 ``[agent_runner.console]`` 的默认取值一致；
#: 迁移只按默认位置找，用户把登记表挪到别处时由 psutil 扫描兜底。
_PROCESS_REGISTRY_FILE_NAME = "processes.json"
#: 每个仓库一份 daemon 单实例锁，锁文件内容只有一个 PID。
_DAEMON_LOCK_DIR_NAME = "daemon-locks"

StateHomeOutcome = Literal[
    "migrated",
    "already_migrated",
    "nothing_to_do",
    "occupied",
    "separate_directories",
    "legacy_link_in_way",
    "cross_device",
    "move_failed",
    "symlink_failed",
    "scanner_unavailable",
]
"""迁移结论：``migrated`` / ``already_migrated`` / ``nothing_to_do`` 之外都是拒绝或失败。"""

#: 这些结论意味着新旧位置互斥或数据可能被并发写坏，属于「冲突」类。
CONFLICT_OUTCOMES: frozenset[StateHomeOutcome] = frozenset(
    {"occupied", "separate_directories", "legacy_link_in_way"}
)
"""拒绝动手、需要用户先解冲突的结论集合。"""

#: 这些结论意味着搬迁中途失败，磁盘状态已经不确定（或扫描能力缺失）。
FAILURE_OUTCOMES: frozenset[StateHomeOutcome] = frozenset(
    {"cross_device", "move_failed", "symlink_failed", "scanner_unavailable"}
)
"""已执行到一半或无法安全判定、必须报失败的结论集合。"""


@dataclass(frozen=True)
class StateHomeMigrationResult:
    """一次状态目录迁移的结论与报告素材。

    Attributes:
        outcome: 结论，见 :data:`StateHomeOutcome`。
        home_path: 本次判定所用的家目录。
        new_state_path: 新状态目录路径（``~/.kedacode``）。
        legacy_state_path: 旧状态目录路径（``~/.iar``，legacy-alias）。
        effective_source_path: 实际被搬走的路径；无需搬迁时为 ``None``。
        blocking_pid_set: 占用中的进程号（按升序），仅 ``occupied`` 时非空。
        plan_lines: 预演或报告用的一行式计划描述。
        rewritten_config_path: 被改写路径值的全局配置文件；未改写时为 ``None``。
        rewritten_value_count: 改写掉的旧路径取值个数。
        legacy_path_file_count: 其他仍写着旧状态路径的文件数（只报告、不改动）。
        manual_symlink_command: 建链失败时给用户手工补建的命令。
        message: 面向用户的结论说明。
    """

    outcome: StateHomeOutcome
    home_path: Path
    new_state_path: Path
    legacy_state_path: Path
    effective_source_path: Path | None = None
    blocking_pid_set: tuple[int, ...] = ()
    plan_lines: tuple[str, ...] = ()
    rewritten_config_path: Path | None = None
    rewritten_value_count: int = 0
    legacy_path_file_count: int = 0
    manual_symlink_command: str | None = None
    message: str = ""


def conflict_outcomes() -> frozenset[str]:
    """需要用户先解冲突、因此拒绝动手的结论集合（见 :data:`CONFLICT_OUTCOMES`）。"""
    return CONFLICT_OUTCOMES


def failure_outcomes() -> frozenset[str]:
    """搬迁无法安全完成或中途失败的结论集合（见 :data:`FAILURE_OUTCOMES`）。"""
    return FAILURE_OUTCOMES


@dataclass(frozen=True)
class OccupancyReport:
    """占用检测结果。

    Attributes:
        scanner_available: psutil 是否可用（不可用时无法保证安全，直接拒绝）。
        pid_set: 命中存活的进程号集合。
    """

    scanner_available: bool
    pid_set: frozenset[int]


def _is_pid_alive(pid: int) -> bool:
    """进程号是否存活；``pid <= 0`` 视为不存在。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但不属于当前用户：迁移仍应拒绝，宁可不动。
        return True
    except OSError:
        return False
    return True


def _pids_from_lock_files(state_dir_path: Path) -> frozenset[int]:
    """收集状态目录 ``daemon-locks/`` 下仍然存活的锁属主 PID。"""
    live_pid_set: set[int] = set()
    lock_dir_path = state_dir_path / _DAEMON_LOCK_DIR_NAME
    if not lock_dir_path.is_dir():
        return frozenset()
    for lock_path in sorted(lock_dir_path.glob("*.lock")):
        try:
            owner_text = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not owner_text.isdigit():
            continue
        owner_pid = int(owner_text)
        if _is_pid_alive(owner_pid):
            live_pid_set.add(owner_pid)
    return frozenset(live_pid_set)


def _pids_from_process_registry(state_dir_path: Path) -> frozenset[int]:
    """收集 ``processes.json`` 里状态仍存活的受管进程 PID。"""
    registry_path = state_dir_path / _PROCESS_REGISTRY_FILE_NAME
    if not registry_path.is_file():
        return frozenset()
    try:
        registry_document = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(registry_document, dict):
        return frozenset()
    live_pid_set: set[int] = set()
    for registry_entry in registry_document.values():
        if not isinstance(registry_entry, dict):
            continue
        raw_pid = registry_entry.get("pid")
        if not isinstance(raw_pid, (int, str)):
            continue
        try:
            entry_pid = int(raw_pid)
        except (TypeError, ValueError):
            continue
        if _is_pid_alive(entry_pid):
            live_pid_set.add(entry_pid)
    return frozenset(live_pid_set)


def _ancestor_pid_set() -> frozenset[int]:
    """当前进程及其全部祖先进程的 PID（迁移命令自身不该把自己算成占用）。"""
    self_and_ancestor_pid_set = {os.getpid()}
    current_pid = os.getpid()
    while current_pid > 1:
        parent_pid = _parent_pid_of(current_pid)
        if parent_pid is None or parent_pid in self_and_ancestor_pid_set:
            break
        self_and_ancestor_pid_set.add(parent_pid)
        current_pid = parent_pid
    return frozenset(self_and_ancestor_pid_set)


def _parent_pid_of(pid: int) -> int | None:
    """返回直接父进程 PID，读不到时给 ``None``。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        return int(psutil.Process(pid).ppid())
    except Exception:  # noqa: BLE001 - 进程已退出或权限不足时按无父进程处理
        return None


def _process_home_path(process_handle: Any) -> Path | None:
    """读取候选进程的家目录；读不到（权限不足、进程已退出）时返回 ``None``。"""
    try:
        home_text = process_handle.environ().get("HOME")
    except Exception:  # noqa: BLE001 - macOS/Linux 上都可能因权限或竞态失败
        return None
    if not home_text:
        return None
    return Path(home_text)


def _launches_own_command(cmdline: list[str] | tuple[str, ...]) -> bool:
    """命令行是否真的以自有可执行文件启动（argv[0] 或带路径分隔符的自有名字）。"""
    for index, raw_token in enumerate(cmdline):
        token_text = str(raw_token)
        if not product_identity.is_own_command_name(token_text):
            continue
        if index == 0 or "/" in token_text or "\\" in token_text:
            return True
    return False


def _pids_from_command_scan(target_home_path: Path) -> frozenset[int] | None:
    """扫描以自有命令名启动、且服务于本次待迁移家目录的进程；扫不动时返回 ``None``。

    命中条件是 argv[0] 就是自有名字，或命令行里出现了**带路径分隔符**的自有可执行文件
    （控制台脚本 ``/…/bin/python /…/bin/iar run …`` 这种形态，旧命令名是 legacy-alias）。裸词匹配会让 ``vim kc``
    这类无关进程误判为占用，因此不做。自身与祖先进程排除。另一个家目录（临时 HOME 沙
    箱、别的账号）里的自有进程写不到本次要搬的状态目录，因此不阻止搬迁；家目录读不到
    时按同家目录处理，宁可拒绝动手。

    Args:
        target_home_path: 本次迁移针对的家目录。

    Returns:
        frozenset[int] | None: 命中存活的进程号；psutil 不可用，或遍历过程中抛出意外错误
        （macOS 上进程高频生灭时 ``proc_cmdline`` 会抛）时为 ``None``。两种情况都按
        「无法确认占用」处理，调用方据此拒绝动手，而不是带着不确定的结论去搬状态目录。
    """
    try:
        import psutil
    except ImportError:
        return None
    excluded_pid_set = _ancestor_pid_set()
    target_home_text = os.path.realpath(str(target_home_path))
    live_pid_set: set[int] = set()
    try:
        for process_handle in psutil.process_iter(["pid", "cmdline"]):
            try:
                info = process_handle.info
            except Exception:  # noqa: BLE001 - 扫描期间进程退出，跳过该进程
                continue
            pid = info.get("pid")
            cmdline = info.get("cmdline")
            if not isinstance(pid, int) or pid in excluded_pid_set:
                continue
            if not isinstance(cmdline, (list, tuple)) or not cmdline:
                continue
            if not _launches_own_command(cmdline):
                continue
            process_home_path = _process_home_path(process_handle)
            if (
                process_home_path is not None
                and os.path.realpath(str(process_home_path)) != target_home_text
            ):
                continue
            live_pid_set.add(pid)
    except Exception:  # noqa: BLE001 - 遍历本身失败（macOS 上进程高频生灭时 psutil 会抛出内部错误）
        return None
    return frozenset(live_pid_set)


def _detect_occupancy(
    state_dir_candidates: tuple[Path, ...],
    target_home_path: Path | None = None,
) -> OccupancyReport:
    """在候选状态目录里合并占用检测。

    Args:
        state_dir_candidates: 需要检查的状态目录（新旧两处，存在几个查几个）。
        target_home_path: 本次迁移针对的家目录；省略时取当前进程的家目录。

    Returns:
        OccupancyReport: 扫描可用性与存活 PID 集合。
    """
    live_pid_set: set[int] = set()
    for state_dir_path in state_dir_candidates:
        live_pid_set |= _pids_from_lock_files(state_dir_path)
        live_pid_set |= _pids_from_process_registry(state_dir_path)
    resolved_target_home_path = Path.home() if target_home_path is None else target_home_path
    scanned_pid_set = _pids_from_command_scan(resolved_target_home_path)
    if scanned_pid_set is None:
        return OccupancyReport(scanner_available=False, pid_set=frozenset(live_pid_set))
    live_pid_set |= scanned_pid_set
    return OccupancyReport(scanner_available=True, pid_set=frozenset(live_pid_set))


def _legacy_state_path_prefix_list(home_path: Path) -> tuple[tuple[str, str], ...]:
    """返回 ``(旧前缀, 新前缀)`` 配对，覆盖配置里可能出现的家目录写法。"""
    home_text = str(home_path)
    legacy_dir_name = product_identity.LEGACY_STATE_DIR_NAME
    new_dir_name = product_identity.STATE_DIR_NAME
    return tuple(
        (f"{placeholder}/{legacy_dir_name}", f"{placeholder}/{new_dir_name}")
        for placeholder in ("~", "$HOME", "${HOME}", home_text)
    )


def rewrite_legacy_state_path_text(value_text: str, home_path: Path) -> str | None:
    """把写着旧状态目录的路径取值改写到新状态目录，未命中时返回 ``None``。

    只处理以旧状态目录为**前缀**的路径（``~/.iar/…``、``$HOME/.iar``，均为 legacy-alias、
    ``${HOME}/.iar/…``（legacy-alias）与家目录绝对形式，避免误改路径中间恰好含有 ``.iar``
    的取值。

    Args:
        value_text: 配置里的原始字符串取值。
        home_path: 家目录。

    Returns:
        str | None: 改写后的取值；不含旧状态目录前缀时为 ``None``。
    """
    for legacy_prefix, new_prefix in _legacy_state_path_prefix_list(home_path):
        if value_text == legacy_prefix:
            return new_prefix
        if value_text.startswith(f"{legacy_prefix}/"):
            return f"{new_prefix}/{value_text[len(legacy_prefix) + 1 :]}"
    return None


def _rewrite_container(container: Any, home_path: Path, *, mutate: bool) -> int:
    """统计（并在 ``mutate`` 时改写）容器里写着旧状态目录的字符串取值。"""
    rewritten_count = 0
    for key, item in list(container.items()):
        if isinstance(item, str):
            rewritten_text = rewrite_legacy_state_path_text(str(item), home_path)
            if rewritten_text is None:
                continue
            rewritten_count += 1
            if mutate:
                container[key] = rewritten_text
            continue
        if hasattr(item, "items"):
            rewritten_count += _rewrite_container(item, home_path, mutate=mutate)
            continue
        if isinstance(item, (list, tuple)):
            for index, element in enumerate(list(item)):
                if not isinstance(element, str):
                    continue
                rewritten_element = rewrite_legacy_state_path_text(str(element), home_path)
                if rewritten_element is None:
                    continue
                rewritten_count += 1
                if mutate:
                    item[index] = rewritten_element
    return rewritten_count


def _load_global_config_document(
    new_state_path: Path,
) -> tuple[Path | None, Any]:
    """读出状态目录里可解析的 ``config.toml``；缺失或损坏时返回 ``(None, None)``。"""
    config_path = new_state_path / "config.toml"
    if not config_path.is_file():
        return None, None
    try:
        return config_path, tomlkit.parse(config_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 配置文件损坏时不阻断搬迁，只在报告里说明
        return None, None


def count_legacy_state_path_values(new_state_path: Path, home_path: Path) -> int:
    """统计 ``config.toml`` 里写着旧状态目录的取值条数（只读，不改动）。

    Args:
        new_state_path: 迁移后 ``config.toml`` 所在的状态目录。
        home_path: 家目录。

    Returns:
        int: 命中的旧路径取值条数；文件缺失或不可解析时为 ``0``。
    """
    _, config_document = _load_global_config_document(new_state_path)
    if config_document is None:
        return 0
    return _rewrite_container(config_document, home_path, mutate=False)


def _rewrite_global_config_paths(new_state_path: Path, home_path: Path) -> tuple[Path | None, int]:
    """改写新状态目录里 ``config.toml`` 的旧路径取值。

    Returns:
        tuple[Path | None, int]: 被改写的文件与改写条数；无需改写时文件为 ``None``。
    """
    config_path, config_document = _load_global_config_document(new_state_path)
    if config_document is None:
        return None, 0
    rewritten_count = _rewrite_container(config_document, home_path, mutate=True)
    if not rewritten_count:
        return None, 0
    config_path.write_text(config_document.as_string(), encoding="utf-8")
    return config_path, rewritten_count


def _count_other_files_with_legacy_paths(
    state_dir_path: Path,
    home_path: Path,
    *,
    skip_relative_path: str | None = None,
    max_scan_file_count: int = 200,
) -> int:
    """统计状态目录里其他仍写着旧状态路径的文本文件数（只报告，不改动）。

    跳过数据库、日志目录与锁目录；单文件超过 1 MiB 时跳过，避免为大文件付出无谓
    的读取成本。
    """
    if not state_dir_path.is_dir():
        return 0
    legacy_text_list = [
        f"{placeholder}/{product_identity.LEGACY_STATE_DIR_NAME}"
        for placeholder in ("~", "$HOME", "${HOME}")
    ] + [str(home_path / product_identity.LEGACY_STATE_DIR_NAME)]
    hit_file_count = 0
    scanned_file_count = 0
    for candidate_path in sorted(state_dir_path.rglob("*")):
        if not candidate_path.is_file():
            continue
        relative_path = candidate_path.relative_to(state_dir_path)
        top_directory_name = relative_path.parts[0] if relative_path.parts else ""
        if top_directory_name in {_DAEMON_LOCK_DIR_NAME, "logs"}:
            continue
        if skip_relative_path is not None and str(relative_path) == skip_relative_path:
            continue
        if candidate_path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pyc", ".log"}:
            continue
        try:
            if candidate_path.stat().st_size > 1_048_576:
                continue
        except OSError:
            continue
        scanned_file_count += 1
        if scanned_file_count > max_scan_file_count:
            break
        try:
            file_text = candidate_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(legacy_text in file_text for legacy_text in legacy_text_list):
            hit_file_count += 1
    return hit_file_count


def _state_dir_shape(
    home_path: Path,
) -> tuple[Literal["migrate", "already", "absent", "separate", "link_in_way"], Path, Path]:
    """判定新旧状态目录的形状，返回 ``(形状, 新路径, 旧路径)``。"""
    new_state_path = home_path / product_identity.STATE_DIR_NAME
    legacy_state_path = home_path / product_identity.LEGACY_STATE_DIR_NAME
    new_is_dir = new_state_path.is_dir()
    legacy_is_symlink = legacy_state_path.is_symlink()
    legacy_is_dir = legacy_state_path.is_dir()
    if new_is_dir:
        if legacy_is_symlink and product_identity.resolve_state_home(home_path).source == "new":
            return "already", new_state_path, legacy_state_path
        if legacy_is_dir:
            return "separate", new_state_path, legacy_state_path
        return "already", new_state_path, legacy_state_path
    if legacy_is_symlink:
        return "link_in_way", new_state_path, legacy_state_path
    if legacy_is_dir:
        return "migrate", new_state_path, legacy_state_path
    return "absent", new_state_path, legacy_state_path


def migrate_state_home(
    *,
    home_path: Path | None = None,
    dry_run: bool = False,
    occupancy_probe: Callable[[tuple[Path, ...], Path], OccupancyReport] | None = None,
) -> StateHomeMigrationResult:
    """把旧状态目录搬到新位置并在旧位置留相对链接（预演时只给计划）。

    Args:
        home_path: 家目录；省略时取 :meth:`pathlib.Path.home`。
        dry_run: 为 True 时只判定与返回计划，不写盘。
        occupancy_probe: 占用检测函数 ``(候选状态目录, 家目录) -> OccupancyReport``，
        单元测试可注入；省略时用内置实现。

    Returns:
        StateHomeMigrationResult: 结论与报告素材（拒绝与失败也返回结果，不抛异常）。
    """
    resolved_home_path = Path.home() if home_path is None else Path(home_path)
    shape, new_state_path, legacy_state_path = _state_dir_shape(resolved_home_path)
    candidate_state_dir_tuple = (
        (new_state_path, legacy_state_path) if shape == "separate" else (legacy_state_path,)
    )
    probe = _detect_occupancy if occupancy_probe is None else occupancy_probe
    occupancy_report = probe(candidate_state_dir_tuple, resolved_home_path)
    if not occupancy_report.scanner_available:
        return StateHomeMigrationResult(
            outcome="scanner_unavailable",
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            message=(
                "cannot verify that no KedaCode process is running (process scanner "
                "unavailable); nothing was changed."
            ),
        )
    if occupancy_report.pid_set:
        sorted_pid_text = ", ".join(str(pid) for pid in sorted(occupancy_report.pid_set))
        return StateHomeMigrationResult(
            outcome="occupied",
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            blocking_pid_set=tuple(sorted(occupancy_report.pid_set)),
            message=(
                f"KedaCode process(es) still running (PID {sorted_pid_text}); stop the daemon "
                "and close the console before migrating. Nothing was changed."
            ),
        )

    if shape == "already":
        return StateHomeMigrationResult(
            outcome="already_migrated",
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            message=f"already migrated: state lives in {new_state_path}.",
        )
    if shape == "absent":
        return StateHomeMigrationResult(
            outcome="nothing_to_do",
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            message=(
                f"no local state directory yet; {new_state_path} will be created on first write."
            ),
        )
    if shape == "separate":
        return StateHomeMigrationResult(
            outcome="separate_directories",
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            message=(
                f"both {legacy_state_path} and {new_state_path} are separate directories; "
                "refusing to merge them. Remove or merge whichever one you do not want."
            ),
        )
    if shape == "link_in_way":
        return StateHomeMigrationResult(
            outcome="legacy_link_in_way",
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            message=(
                f"{legacy_state_path} is a link to somewhere else, not a real directory; "
                "refusing to move it. Nothing was changed."
            ),
        )

    plan_line_tuple = (
        f"move {legacy_state_path} -> {new_state_path}",
        f"link {legacy_state_path} -> {product_identity.STATE_DIR_NAME} (relative)",
    )
    if dry_run:
        # 搬迁前 config.toml 还在旧目录里，预演按旧目录的位置统计。
        rewritten_value_count = count_legacy_state_path_values(
            legacy_state_path, resolved_home_path
        )
        legacy_path_file_count = _count_other_files_with_legacy_paths(
            legacy_state_path, resolved_home_path, skip_relative_path="config.toml"
        )
        return StateHomeMigrationResult(
            outcome="migrated",
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            effective_source_path=legacy_state_path,
            plan_lines=plan_line_tuple,
            rewritten_config_path=(
                new_state_path / "config.toml" if rewritten_value_count else None
            ),
            rewritten_value_count=rewritten_value_count,
            legacy_path_file_count=legacy_path_file_count,
            message="dry run: the plan above was computed, nothing was written.",
        )

    try:
        os.rename(legacy_state_path, new_state_path)
    except OSError as exc:
        is_cross_device = exc.errno == errno.EXDEV
        failure_outcome: StateHomeOutcome = "cross_device" if is_cross_device else "move_failed"
        failure_message = (
            f"{legacy_state_path} and {new_state_path} are on different filesystems; "
            "refusing to copy across devices. Nothing was changed."
            if is_cross_device
            else f"could not move {legacy_state_path} to {new_state_path}: {exc}"
        )
        return StateHomeMigrationResult(
            outcome=failure_outcome,
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            effective_source_path=legacy_state_path,
            message=failure_message,
        )

    symlink_error: OSError | None = None
    try:
        os.symlink(product_identity.STATE_DIR_NAME, legacy_state_path)
    except OSError as exc:
        symlink_error = exc

    rewritten_config_path, rewritten_value_count = _rewrite_global_config_paths(
        new_state_path, resolved_home_path
    )
    legacy_path_file_count = _count_other_files_with_legacy_paths(
        new_state_path, resolved_home_path, skip_relative_path="config.toml"
    )

    if symlink_error is not None:
        manual_command = f"ln -s {product_identity.STATE_DIR_NAME} {legacy_state_path}"
        return StateHomeMigrationResult(
            outcome="symlink_failed",
            home_path=resolved_home_path,
            new_state_path=new_state_path,
            legacy_state_path=legacy_state_path,
            effective_source_path=legacy_state_path,
            rewritten_config_path=rewritten_config_path,
            rewritten_value_count=rewritten_value_count,
            legacy_path_file_count=legacy_path_file_count,
            manual_symlink_command=manual_command,
            message=(
                f"moved {legacy_state_path} to {new_state_path} but could not create the "
                f"compatibility link ({symlink_error}). The move is not rolled back; create "
                f"the link yourself with: {manual_command}"
            ),
        )

    return StateHomeMigrationResult(
        outcome="migrated",
        home_path=resolved_home_path,
        new_state_path=new_state_path,
        legacy_state_path=legacy_state_path,
        effective_source_path=legacy_state_path,
        plan_lines=plan_line_tuple,
        rewritten_config_path=rewritten_config_path,
        rewritten_value_count=rewritten_value_count,
        legacy_path_file_count=legacy_path_file_count,
        message=f"moved {legacy_state_path} to {new_state_path} and linked the old path to it.",
    )


__all__ = [
    "CONFLICT_OUTCOMES",
    "OccupancyReport",
    "count_legacy_state_path_values",
    "conflict_outcomes",
    "FAILURE_OUTCOMES",
    "failure_outcomes",
    "StateHomeMigrationResult",
    "StateHomeOutcome",
    "migrate_state_home",
    "rewrite_legacy_state_path_text",
]

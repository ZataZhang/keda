"""保留式 TOML 表键写回。

用 ``tomlkit`` 做 round-trip 编辑：保留文件中的全部注释、空行与格式，只触碰
调用方点名的键。三条落盘规则是这个模块的全部承诺：

1. **原子且不改归属**：写经「同目录唯一临时文件 + ``os.replace``」完成。固定临时名
   在并发写入时会互相截断（后写者在前者 replace 之前截断同一文件，原子替换反而可能
   把写了一半的内容换成正式配置），故取名必须唯一；``tempfile.mkstemp`` 建的是 0600
   文件且属于当前进程，直接替换会把目标配置从 0644 降级成只有属主可读、并把属主悄悄
   换成写入者，因此 replace 前必须把目标原有的权限位与 uid/gid 写回临时文件。
2. **同文件串行**：一次读-改-写整体持有一把跨进程文件锁（``<name>.lock``）。``os.replace``
   只保证单次写入不撕裂，不保证两个写入者不互相丢键——机器级 ``config.toml`` 同时被
   Console 保存、registry 写入与 CLI 调用改动，仓库级 ``.kedacode.toml`` 同理。锁按目标
   路径派生，故本模块与 :class:`backend.infrastructure.config.registry_editor.TomlRegistryEditor`
   等不同写入器之间同样互斥。这是**建议锁**：只经 :func:`config_write_lock` 直接改
   ``config.toml`` 的写入者（模板 seed、状态目录搬迁、``kc init``）同样要取它，否则
   能与持锁写者交错。抢锁是有界非阻塞重试（超时抛
   :class:`~backend.core.shared.models.config_edit.ConfigLockBusyError`，不挂死），
   同线程重入放行（否则嵌套调用会在自己的锁上自死锁）。
3. **一批一次落盘**：一次用户操作涉及的多个段由 :func:`apply_toml_updates` 在**同一次**
   读-改-写里完成；任一段的点名校验不过（含"要删的键本层根本没声明"）就整批不落盘，
   调用方拿到一次明确失败而不是半写状态。

``config.toml``（机器级）与 ``.kedacode.toml``（仓库级）共用本模块——两处写回语义必须
一致：**只写请求里显式给出的键**（值为 ``None`` 表示删除该键），文件其余内容一字不动。

现有文件解析失败时抛 :class:`~backend.core.shared.models.config_edit.ConfigDocumentInvalidError`
而不是覆盖：把一份坏 TOML 静默重写成"能解析的"新文件，会连带丢掉用户手写的注释与
其余段落。
"""

from __future__ import annotations

import os
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT, Table

from backend.core.shared.models.config_edit import (
    ConfigDocumentInvalidError,
    ConfigKeyMissingError,
    ConfigLockBusyError,
)

#: 锁后端：POSIX ``fcntl.flock`` → Windows ``msvcrt.locking`` → 两者皆无时不加跨进程锁。
_LOCK_BACKEND: str
try:
    import fcntl

    def _try_lock(file_descriptor: int) -> bool:
        """非阻塞尝试独占锁，被别的进程持有则返回 ``False``。"""
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(file_descriptor: int) -> None:
        """释放 :func:`_try_lock` 拿到的锁。"""
        fcntl.flock(file_descriptor, fcntl.LOCK_UN)

    _LOCK_BACKEND = "fcntl"
except ImportError:  # pragma: no cover - 仅 Windows 走这一支
    try:
        import msvcrt

        def _try_lock(file_descriptor: int) -> bool:
            """非阻塞锁住首字节；已被占用则返回 ``False``。"""
            try:
                os.lseek(file_descriptor, 0, os.SEEK_SET)
                msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True

        def _unlock(file_descriptor: int) -> None:
            """解锁首字节（须与加锁时同一偏移）。"""
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)

        _LOCK_BACKEND = "msvcrt"
    except ImportError:
        # 两个后端都没有：跨进程串行化不成立，只剩 os.replace 保证单次写入不撕裂。
        # 显式命名成 none，让依赖锁的守卫测试在本平台跳过而不是"静默通过"。

        def _try_lock(file_descriptor: int) -> bool:
            """无锁后端：视为立即拿到锁。"""
            return True

        def _unlock(file_descriptor: int) -> None:
            """无锁后端：没有需要释放的东西。"""

        _LOCK_BACKEND = "none"


#: 目标文件不存在时新建配置所用的权限位（与 ``mkstemp`` 的 0600 无关）。
_DEFAULT_NEW_CONFIG_MODE = 0o644
#: 跨进程锁文件后缀：与目标配置同目录、同前缀，保证同一配置名的写入者排队。
_LOCK_FILE_SUFFIX = ".lock"
#: 锁文件权限：rendezvous 点不含数据，只该被属主读写（默认 0666&~umask 太宽）。
_LOCK_FILE_MODE = 0o600
#: 抢锁的最长等待：拿不到就失败退出，而不是无限阻塞把 CLI 挂死、
#: 或在 FastAPI 的 threadpool 里长期占住一个 worker。
_LOCK_WAIT_TIMEOUT_SECONDS = 10.0
#: 抢锁重试间隔：写入本身是毫秒级，短间隔足够让并发写者在超时内轮到手。
_LOCK_RETRY_INTERVAL_SECONDS = 0.05

_PresenceRequirement = Literal["none", "table", "keys"]
"""删除操作对"目标层已存在"的要求：``none`` 允许静默 no-op；``table`` 要求叶子表
已存在（删除一个预设）；``keys`` 在此基础上还要求每个待删键已存在（解绑一个阶段）。"""


@dataclass(frozen=True)
class TableKeyUpdate:
    """一次表内点名键的期望改动。

    Attributes:
        table_path: 表路径，如 ``("agent_runner", "lifecycle_agents")``。
        values: 键 -> 新值；``None`` 表示删除该键。
        presence: 该更新里**删除**部分的存在性要求，见 :data:`_PresenceRequirement`。
            默认 ``"none"`` 保持"删不存在的键即 no-op"的兼容语义。
    """

    table_path: tuple[str, ...]
    values: Mapping[str, Any]
    presence: _PresenceRequirement = "none"


@dataclass(frozen=True)
class ArrayTableUpdate:
    """一次数组表整体替换（顺序即 ``entries`` 顺序）。

    Attributes:
        table_path: 数组表所属父表路径，如 ``("agent_runner", "runner")``。
        key: 数组表键名，如 ``"agent_fallback_candidates"``。
        entries: 有序条目列表，每项是 ``字段名 -> 值``（值为 ``None`` 表示省略）；
            空列表表示删除该键。
    """

    table_path: tuple[str, ...]
    key: str
    entries: Sequence[Mapping[str, Any]]


_TomlUpdate = TableKeyUpdate | ArrayTableUpdate

#: 本线程已持有的锁路径：flock 记在 open file description 上，同进程再开一个 fd 去锁
#: 同一个文件会等自己，所以重入必须在进入内核之前拦下来。
_held_lock_paths = threading.local()


def _thread_held_locks() -> set[str]:
    """取本线程当前持有的配置锁集合（首次使用时建）。"""
    held_paths: set[str] | None = getattr(_held_lock_paths, "paths", None)
    if held_paths is None:
        held_paths = set()
        _held_lock_paths.paths = held_paths
    return held_paths


def _open_lock_file(lock_path: Path) -> int:
    """打开（必要时以 :data:`_LOCK_FILE_MODE` 新建）锁文件，返回其 fd。

    先 ``O_EXCL`` 再回落 ``O_CREAT``：只有"确实是本进程建出来的"那次才 fchmod，避免
    把别人建好的锁文件权限改来改去；``O_EXCL`` 分支的 mode 会被 umask 削，所以补一次
    显式 ``fchmod`` 保证是 0600。
    """
    try:
        created_fd = os.open(
            str(lock_path),
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            _LOCK_FILE_MODE,
        )
    except FileExistsError:
        return os.open(str(lock_path), os.O_RDWR | os.O_CREAT)
    os.fchmod(created_fd, _LOCK_FILE_MODE)
    return created_fd


def _acquire_lock_or_fail(file_descriptor: int, resolved_path: Path) -> None:
    """在有界等待内抢到锁，否则抛 :class:`ConfigLockBusyError`（不无限阻塞）。

    Raises:
        ConfigLockBusyError: 等待超过 :data:`_LOCK_WAIT_TIMEOUT_SECONDS` 仍被占用。
    """
    if _try_lock(file_descriptor):
        return
    deadline = time.monotonic() + _LOCK_WAIT_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConfigLockBusyError(
                f"{resolved_path} 的写锁被另一个写入者持有超过 "
                f"{_LOCK_WAIT_TIMEOUT_SECONDS:g}s，本次写回未改动任何配置："
                "请确认没有并发的 kc 写入或 Console 保存，然后重试。"
            )
        time.sleep(min(_LOCK_RETRY_INTERVAL_SECONDS, remaining))
        if _try_lock(file_descriptor):
            return


@contextmanager
def _config_file_lock(resolved_path: Path) -> Iterator[None]:
    """持有一把与目标配置同目录的写锁，覆盖整个读-改-写。

    锁文件是零字节的 rendezvous 点，不写内容；``os.replace`` 换掉配置文件的 inode
    也不影响它，因此锁不会被一次成功写入"换手"。进程退出时内核自动释放，崩溃不留死锁。

    两条防护让"有锁"不等于"会挂死"：抢锁走 :func:`_acquire_lock_or_fail` 的**有界**
    非阻塞重试（超时报 :class:`~backend.core.shared.models.config_edit.ConfigLockBusyError`
    而不是无限等），并且**同线程重入直接放行**——否则把 :func:`apply_toml_updates`
    套进 :func:`mutate_toml_document` 的回调里，同一进程会在自己的锁上自死锁。

    Args:
        resolved_path: 目标配置文件路径（其父目录须已存在）。
    """
    lock_key = os.path.realpath(str(resolved_path))
    held_paths = _thread_held_locks()
    if lock_key in held_paths:
        yield
        return
    lock_path = resolved_path.with_name(resolved_path.name + _LOCK_FILE_SUFFIX)
    file_descriptor = _open_lock_file(lock_path)
    try:
        _acquire_lock_or_fail(file_descriptor, resolved_path)
        held_paths.add(lock_key)
        try:
            yield
        finally:
            held_paths.discard(lock_key)
            _unlock(file_descriptor)
    finally:
        os.close(file_descriptor)


@contextmanager
def config_write_lock(config_path: str | Path) -> Iterator[None]:
    """对**不走本模块写回路径**的调用方暴露同一把写锁。

    flock 是建议锁：只有都取同一把锁的写入者之间才互斥。``kc`` 的读-改-写在加锁前
    还要"读基线 + 校验"（候选数组这类整段替换尤其如此），把这段也圈进锁里才不会丢掉
    并发写入者的改动；同一线程内后续经 :func:`apply_toml_updates` 落盘时重入放行。

    Args:
        config_path: 目标配置文件路径。

    Raises:
        ConfigLockBusyError: 有界等待内没拿到锁（目标文件未被改动）。
        OSError: 锁文件无法创建或打开。
    """
    with _config_file_lock(Path(config_path).expanduser()):
        yield


def _table_label(table_path: Sequence[str]) -> str:
    """把表路径渲染成 TOML 段名（错误信息里指认位置用）。"""
    return ".".join(table_path)


def _parse_roundtrip_document(
    resolved_path: Path,
    *,
    create_if_missing: bool,
) -> tomlkit.TOMLDocument:
    """读取 round-trip 文档；文件缺失时按 ``create_if_missing`` 建或抛错。

    Args:
        resolved_path: 目标配置文件路径。
        create_if_missing: ``True`` 时以空文档起步（写回可新建文件）；``False`` 时
            缺失文件按 ``OSError`` 原样抛出（读取端不该凭空看到一份空配置）。

    Returns:
        解析后的 :class:`tomlkit.TOMLDocument`。

    Raises:
        OSError: 文件缺失且 ``create_if_missing=False``，或读取失败。
        ConfigDocumentInvalidError: 现有内容不是合法 TOML 或非 UTF-8 文本。
    """
    if create_if_missing and not resolved_path.is_file():
        return tomlkit.document()
    raw_text = resolved_path.read_text(encoding="utf-8")
    try:
        return tomlkit.parse(raw_text)
    except (TOMLKitError, UnicodeDecodeError) as exc:
        raise ConfigDocumentInvalidError(
            f"{resolved_path} 不是合法的 TOML，本次写回未改动任何配置；请先修复该文件再重试：{exc}"
        ) from exc


def _ensure_table(document: tomlkit.TOMLDocument, table_path: Sequence[str]) -> Table:
    """沿 ``table_path`` 逐级取（或建）表，返回叶子表。

    Args:
        document: 已解析的 TOML 文档。
        table_path: 表路径，如 ``("agent_runner", "lifecycle_agents")``。

    Returns:
        叶子 :class:`tomlkit.items.Table`。
    """
    current: Any = document
    for index, key in enumerate(table_path):
        existing = current.get(key)
        if existing is None:
            is_leaf = index == len(table_path) - 1
            # 非叶子用 super table，保证后续子段能正确渲染成 `[a.b]` 而非内联表。
            new_table = tomlkit.table(is_super_table=not is_leaf)
            current[key] = new_table
            existing = new_table
        current = existing
    return current


def _existing_table(document: tomlkit.TOMLDocument, table_path: Sequence[str]) -> Table | None:
    """沿 ``table_path`` 取**已存在**的叶子表；任一中间层缺失时返回 ``None``（不创建）。"""
    current: Any = document
    for key in table_path:
        current = current.get(key)
        if current is None:
            return None
    return current if isinstance(current, Table) else None


def _replace_file_atomically(resolved_path: Path, rendered_document: str) -> None:
    """经同目录**唯一**临时文件写回，失败时不留下残 file、原文件一字不动。

    权限位与属主/属组都沿用目标文件现状：``mkstemp`` 建出的临时文件是 0600 且属于当前
    进程，不补这两步就会把机器级 ``config.toml`` 从 0644 降成只有属主可读，或把一份
    他人/其他用户持有的配置（共享机器、root 属主的机器级配置）静默改成当前用户所有。
    """
    temp_fd, temp_name = tempfile.mkstemp(
        dir=str(resolved_path.parent),
        prefix=f"{resolved_path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_handle:
            temp_handle.write(rendered_document)
            temp_handle.flush()
            os.fsync(temp_handle.fileno())
        identity = _target_file_identity(resolved_path)
        os.chmod(temp_path, identity.mode)
        _preserve_file_ownership(temp_path, identity)
        os.replace(temp_path, resolved_path)
    finally:
        temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class _FileIdentity:
    """目标文件写前的归属现状（文件尚不存在时属主/属组为 ``None``）。"""

    mode: int
    owner_uid: int | None
    owner_gid: int | None


def _target_file_identity(resolved_path: Path) -> _FileIdentity:
    """取目标文件的权限位与属主/属组；文件尚不存在时用 :data:`_DEFAULT_NEW_CONFIG_MODE`。

    Raises:
        OSError: ``stat`` 失败且原因不是"文件不存在"。
    """
    try:
        target_stat = resolved_path.stat()
    except FileNotFoundError:
        return _FileIdentity(_DEFAULT_NEW_CONFIG_MODE, None, None)
    return _FileIdentity(
        stat.S_IMODE(target_stat.st_mode),
        target_stat.st_uid,
        target_stat.st_gid,
    )


def _preserve_file_ownership(temp_path: Path, identity: _FileIdentity) -> None:
    """把目标文件原本的属主/属组搬到临时文件上，``os.replace`` 后 ownership 不变。

    尽力而为：POSIX 只允许特权进程将文件 chown 给别人，非属主的共享写者做不到这一步，
    此时保持"权限位不变、属主随写入进程"并继续完成写入，不把一次本可成功的保存改成失败。
    """
    if identity.owner_uid is None or identity.owner_gid is None:
        return
    try:
        os.chown(temp_path, identity.owner_uid, identity.owner_gid)
    except OSError:  # pragma: no cover - 非特权进程改不动属主
        pass


def _atomic_dump(resolved_path: Path, document: tomlkit.TOMLDocument) -> None:
    """原子写回文档；渲染文本与磁盘现状一致时**不落盘**。

    ``os.replace`` 只保证单次写入的原子性，不保证"共写但没变"的键不产生 diff：
    同值写回在这里被识别为 no-op，文件（含被替换段的位置与行内注释）保持原样。
    """
    rendered_document = tomlkit.dumps(document)
    if resolved_path.is_file() and resolved_path.read_text(encoding="utf-8") == rendered_document:
        return
    _replace_file_atomically(resolved_path, rendered_document)


def _prune_empty_table(document: tomlkit.TOMLDocument, table_path: Sequence[str]) -> None:
    """若 ``table_path`` 叶子表已空则删除它，避免留下空段头。"""
    if not table_path:
        return
    parent: Any = document
    for key in table_path[:-1]:
        parent = parent.get(key)
        if parent is None:
            return
    leaf_key = table_path[-1]
    leaf_table = parent.get(leaf_key)
    if isinstance(leaf_table, Table) and len(leaf_table) == 0:
        del parent[leaf_key]


def _apply_table_key_update(
    resolved_path: Path,
    document: tomlkit.TOMLDocument,
    update: TableKeyUpdate,
) -> bool:
    """把一次表内点名改动就地应用到 ``document``，返回是否产生改动。

    Raises:
        ValueError: ``table_path`` 为空。
        ConfigKeyMissingError: ``update.presence`` 要求的目标表 / 待删键不在本层。
    """
    if not update.table_path:
        raise ValueError("table_path must not be empty.")
    delete_keys = [key for key, value in update.values.items() if value is None]
    if update.presence == "none" or not delete_keys:
        # 纯 upsert 不受存在性要求约束：本层还没这个表时新建它正是期望行为。
        table = _ensure_table(document, update.table_path)
    else:
        table = _existing_table(document, update.table_path)
        if table is None:
            raise ConfigKeyMissingError(_missing_table_message(resolved_path, update.table_path))
        if update.presence == "keys":
            _require_present_deletes(resolved_path, update.table_path, update.values, table)
    changed = False
    for key, value in update.values.items():
        if value is None:
            if key in table:
                del table[key]
                changed = True
        else:
            table[key] = value
            changed = True
    if changed:
        _prune_empty_table(document, update.table_path)
    return changed


def _missing_table_message(resolved_path: Path, table_path: Sequence[str]) -> str:
    """ "本层没有这个表"的可操作错误文案（指认文件与段名、点明该改哪一层）。"""
    return (
        f"{resolved_path} 里没有 [{_table_label(table_path)}]，"
        "本次删除不会改动任何配置：请改在声明它的配置范围里操作。"
    )


def _require_present_deletes(
    resolved_path: Path,
    table_path: Sequence[str],
    values: Mapping[str, Any],
    table: Table,
) -> None:
    """确认每个"要删"的键都在本层表里，否则抛出无效删除错误。

    Raises:
        ConfigKeyMissingError: 存在本层未声明的待删键（如解绑一个当前层没有绑定的阶段）。
    """
    unbound_keys = sorted(
        key for key, value in values.items() if value is None and key not in table
    )
    if not unbound_keys:
        return
    raise ConfigKeyMissingError(
        f"{resolved_path} 的 [{_table_label(table_path)}] 未声明 "
        f"{', '.join(unbound_keys)}，本次操作不会改动任何配置：请改在声明它的配置范围里操作。"
    )


def _apply_array_table_update(
    document: tomlkit.TOMLDocument,
    update: ArrayTableUpdate,
) -> bool:
    """把一次数组表整体替换就地应用到 ``document``，返回是否产生改动。

    Raises:
        ValueError: ``table_path`` 为空。
    """
    if not update.table_path:
        raise ValueError("table_path must not be empty.")
    table = _ensure_table(document, update.table_path)
    if not update.entries:
        # 空列表 = 清空 / 保持不存在；本就无此键则没有任何改动。
        if update.key not in table:
            return False
        del table[update.key]
        return True
    existing = table.get(update.key)
    if isinstance(existing, AoT):
        _apply_entries_in_place(existing, update.entries)
    else:
        table[update.key] = _render_entries_as_array_table(update.entries)
    return True


def _apply_updates(
    resolved_path: Path,
    document: tomlkit.TOMLDocument,
    updates: Sequence[_TomlUpdate],
) -> bool:
    """按顺序把一批改动应用到文档，返回整批是否产生改动。

    存在性校验在应用前完成，因此任何一个更新失败都不会留下半改的文档。
    """
    changed = False
    for update in updates:
        if isinstance(update, ArrayTableUpdate):
            changed |= _apply_array_table_update(document, update)
        else:
            changed |= _apply_table_key_update(resolved_path, document, update)
    return changed


def apply_toml_updates(config_path: str | Path, updates: Sequence[_TomlUpdate]) -> None:
    """锁内一次读-改-写落盘 ``updates``：**要么全落，要么文件一字不动**。

    一次用户操作常常同时改多个段（预设定义 + 阶段绑定，或候选数组 + 切换预算）。
    逐段各写一次时，第二次写入失败会留下半套配置；收敛成一次读-改-写后，任何一步
    校验不过（含 :class:`~backend.core.shared.models.config_edit.ConfigKeyMissingError`）
    都在落盘之前抛出。渲染结果与磁盘现状相同时不落盘。

    Args:
        config_path: 目标 TOML 文件路径（``config.toml`` 或 ``.kedacode.toml``）。
        updates: 有序的 :class:`TableKeyUpdate` / :class:`ArrayTableUpdate` 列表。

    Raises:
        ValueError: ``updates`` 为空，或某个更新的 ``table_path`` 为空。
        OSError: 读取或替换目标文件失败。
        ConfigDocumentInvalidError: 现有文件不是合法 TOML。
        ConfigKeyMissingError: 某个更新要求存在的表 / 待删键不在本层。
        ConfigLockBusyError: 有界等待内没拿到写锁（另一个写入者正持有该文件）。
    """
    if not updates:
        raise ValueError("updates must not be empty.")
    resolved_path = Path(config_path).expanduser()
    with _config_file_lock(resolved_path):
        document = _parse_roundtrip_document(resolved_path, create_if_missing=True)
        if not _apply_updates(resolved_path, document, updates):
            # 整批没有任何改动（纯删除且目标键本就不存在）：不落盘，避免 round-trip
            # 重排产生 diff，也避免凭空建出一个配置文件。
            return
        _atomic_dump(resolved_path, document)


def mutate_toml_document(
    config_path: str | Path,
    mutate: Callable[[tomlkit.TOMLDocument], None],
    *,
    create_if_missing: bool = False,
) -> None:
    """锁内读一份 round-trip 文档、交给 ``mutate`` 就地改、再原子写回。

    给"段结构由调用方自己决定"的写入器（如 registry 的 ``repositories.<repo_id>``
    子树）复用同一套锁 / 唯一临时名 / 权限保持，全仓因此只有一份 tomlkit 写回实现。
    ``mutate`` 抛异常时不落盘——前置校验写在 ``mutate`` 里即可保证失败不动文件。

    Args:
        config_path: 目标 TOML 文件路径。
        mutate: 就地修改文档的回调；签名只有文档本身，不返回内容。
        create_if_missing: 目标文件不存在时是否以空文档起步并允许新建。

    Raises:
        OSError: 文件缺失且 ``create_if_missing=False``，或读取/替换失败。
        ConfigDocumentInvalidError: 现有文件不是合法 TOML。
        ConfigLockBusyError: 有界等待内没拿到写锁（另一个写入者正持有该文件）。
    """
    resolved_path = Path(config_path).expanduser()
    with _config_file_lock(resolved_path):
        document = _parse_roundtrip_document(resolved_path, create_if_missing=create_if_missing)
        mutate(document)
        _atomic_dump(resolved_path, document)


def update_toml_table_keys(
    config_path: str | Path,
    table_path: Sequence[str],
    values: Mapping[str, Any],
) -> None:
    """保留式更新 ``table_path`` 下点名的键。

    值为 ``None`` 时删除该键（用于"跟随上一层 / 删除本键"语义）；其余值直接写入。
    只修改签名为 ``values`` 的键，文件其余内容与格式保持不变。文件不存在时新建，
    并按需创建中间表。**全部取值都是删除且目标键本就不存在时不落盘**（无改动、
    不产生 diff、也不凭空创建文件）；取值与磁盘现状相同同样不落盘（no-op 写回
    不产生文件抖动）。需要"删不掉就必须失败"的语义时用 :func:`apply_toml_updates`
    并给 ``TableKeyUpdate.presence`` 传 ``"table"`` / ``"keys"``。

    Args:
        config_path: 目标 TOML 文件路径（``config.toml`` 或 ``.kedacode.toml``）。
        table_path: 表路径，如 ``("agent_runner", "lifecycle_agents")``。
        values: 键 -> 新值；``None`` 表示删除该键。

    Raises:
        ValueError: ``table_path`` 为空。
        OSError: 读取或替换目标文件失败。
        ConfigDocumentInvalidError: 现有文件不是合法 TOML。
    """
    apply_toml_updates(config_path, [TableKeyUpdate(tuple(table_path), values)])


def _render_entries_as_array_table(entries: Sequence[Mapping[str, Any]]) -> AoT:
    """把 ``entries`` 渲染成全新的数组表（每条一个 ``[[...]]`` 子表）。"""
    array_table = tomlkit.aot()
    for entry in entries:
        array_table.append(_entry_to_table(entry))
    return array_table


def _entry_to_table(entry: Mapping[str, Any]) -> Table:
    """把一条 entry 转成子表；值为 ``None`` 的字段省略。"""
    element = tomlkit.table()
    for field_name, field_value in entry.items():
        if field_value is not None:
            element[field_name] = field_value
    return element


def _apply_entries_in_place(existing: AoT, entries: Sequence[Mapping[str, Any]]) -> None:
    """把 ``entries`` 逐条原地写回已存在的数组表，长度差用追加 / 弹出收尾。

    原地改写保住数组表在文件里的**位置**与段前 / 段内**注释**；整体删除再追加会
    把 ``[[...]]`` 段挪到父表末尾并抹掉手写注释。

    Args:
        existing: 文件里已存在的数组表（会被就地修改）。
        entries: 期望内容，顺序即写后的候选顺序。
    """
    for index, entry in enumerate(entries):
        if index < len(existing):
            element = existing[index]
        else:
            element = _entry_to_table(entry)
            existing.append(element)
        for field_name, field_value in entry.items():
            if field_value is None:
                if field_name in element:
                    del element[field_name]
            else:
                element[field_name] = field_value
        for stale_field_name in [name for name in element if name not in entry]:
            del element[stale_field_name]
    while len(existing) > len(entries):
        existing.pop()


def update_toml_array_of_tables(
    config_path: str | Path,
    table_path: Sequence[str],
    key: str,
    entries: Sequence[Mapping[str, Any]],
) -> None:
    """整体替换 ``table_path`` 下的数组表 ``key``（如 ``agent_fallback_candidates``）。

    ``update_toml_table_keys`` 只能写标量 / 内联值，无法产出规范的
    ``[[a.b.key]]`` 数组表形态；本函数专为此形态：写后内容**整体等于** ``entries``
    （顺序即 ``entries`` 顺序）。每条 ``entry`` 里值为 ``None`` 的字段直接省略
    （回读时由 Pydantic 默认值补回，与"该候选不设此字段"语义一致）。``entries``
    为空时删除该键；键本就不存在且无内容可写时**不落盘**（不产生 diff、不凭空建
    文件）。已存在的数组表按条**原地改写**（保住位置与手写注释）；渲染结果与磁盘
    现状相同（如重复提交同一条候选链）时同样不落盘。文件其余内容与格式保持不变。

    Args:
        config_path: 目标 TOML 文件路径（``config.toml`` 或 ``.kedacode.toml``）。
        table_path: 数组表所属父表路径，如 ``("agent_runner", "runner")``。
        key: 数组表键名，如 ``"agent_fallback_candidates"``。
        entries: 有序条目列表，每项是 ``字段名 -> 值``（值为 ``None`` 表示省略）。

    Raises:
        ValueError: ``table_path`` 为空。
        OSError: 读取或替换目标文件失败。
        ConfigDocumentInvalidError: 现有文件不是合法 TOML。
    """
    apply_toml_updates(config_path, [ArrayTableUpdate(tuple(table_path), key, entries)])


__all__ = [
    "ArrayTableUpdate",
    "TableKeyUpdate",
    "apply_toml_updates",
    "config_write_lock",
    "mutate_toml_document",
    "update_toml_array_of_tables",
    "update_toml_table_keys",
]

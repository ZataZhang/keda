#!/usr/bin/env python3
"""PRD 执行锁：防止同一条 PRD 被多个会话重复开工。

锁是主仓库 ``tasks/evidence/<prd-stem>/active.lock`` 下的本机 JSON 文件，
被 ``.gitignore`` 的 ``tasks/evidence/**`` 规则覆盖，天然不进版本库。
在任意 linked worktree 内运行时，仓库根通过 ``git rev-parse --git-common-dir``
反推主仓库，保证所有 worktree 共享同一份锁视图。

用法::

    python3 scripts/shared/just/prd_lock.py claim <prd-file> [--tool <名称>] [--branch <名称>]
    python3 scripts/shared/just/prd_lock.py heartbeat <prd-file>
    python3 scripts/shared/just/prd_lock.py release <prd-file> [--force]

互斥语义：

- ``claim`` 原子排他创建（``os.link`` 的 ``FileExistsError`` 承担 ``O_EXCL`` 语义）；
  他人新鲜锁拒绝并输出持锁者信息（退出 1）；过期锁（心跳超 30 分钟）自动接管
  并把旧锁改名留档为 ``active.lock.<时间戳>.stale``；同归属（worktree 相对路径
  一致）重复领锁幂等刷新。
- **过期判定只看心跳**。锁脚本自身是命令结束即退出的短命进程，且 agent 工具
  调用通常给每条命令开新会话——无论记自身 pid 还是会话首领 pid，"pid 已死"
  都会在领锁动作返回后立刻成立，所有锁瞬间过期、互斥失效（实测证实）。
  ``pid`` / ``hostname`` 字段仍记录在锁 JSON 里，仅供排查展示，不参与判定。
- 归属判定只看锁里的 ``worktree`` 字段；``ai_tool`` / ``branch`` 是纯展示元数据，
  不参与互斥判定。主仓库领取的锁被同一仓库 linked worktree 再次领取时视为开工
  入口到执行器的移交（``just implement`` 先领锁、executor 进 worktree 后自检），
  刷新心跳并把归属更新为当前 worktree。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOCK_FILENAME = "active.lock"
STALE_AFTER = timedelta(minutes=30)
UNKNOWN_TOOL_LABEL = "unknown"


@dataclass
class LockSnapshot:
    """某条 PRD 执行锁的查询快照。

    Attributes:
        state (str): 锁状态，``none`` / ``fresh`` / ``stale``。
        metadata (dict): 锁 JSON 原文；无锁或锁文件损坏时为 ``{}``。
        lock_path (Path): 锁文件应在的主仓库路径。
    """

    state: str
    metadata: dict = field(default_factory=dict)
    lock_path: Path = field(default_factory=Path)


def resolve_main_repo_root() -> Path:
    """定位主仓库根目录（linked worktree 内也解析到主仓库）。

    Returns:
        Path: 主仓库根；不在 git 仓库内时向上查找含 ``tasks/`` 的目录，
        仍找不到时回退到当前工作目录。
    """
    try:
        raw_common_dir_text = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if raw_common_dir_text:
            common_dir_path = Path(raw_common_dir_text)
            if not common_dir_path.is_absolute():
                common_dir_path = (Path.cwd() / common_dir_path).resolve()
            if common_dir_path.name == ".git":
                return common_dir_path.parent
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    for candidate_root_path in [Path.cwd(), *Path.cwd().parents]:
        if (candidate_root_path / "tasks").is_dir():
            return candidate_root_path
    return Path.cwd()


def resolve_current_worktree_label(main_repo_root: Path) -> str:
    """返回当前工作树相对主仓库根的归属标签。

    Args:
        main_repo_root (Path): 主仓库根目录。

    Returns:
        str: 主仓库内为 ``""``；linked worktree 内为相对主仓库根的路径。
    """
    try:
        raw_toplevel_text = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    if not raw_toplevel_text:
        return ""
    current_root_path = Path(raw_toplevel_text).resolve()
    main_root_path = main_repo_root.resolve()
    if current_root_path == main_root_path:
        return ""
    return os.path.relpath(current_root_path, main_root_path)


def lock_path_for(main_repo_root: Path, prd_stem: str) -> Path:
    """返回某条 PRD 锁文件在主仓库内的路径。"""
    return main_repo_root / "tasks" / "evidence" / prd_stem / LOCK_FILENAME


def parse_lock_timestamp(raw_timestamp_value: object) -> datetime | None:
    """解析锁 JSON 里的 ISO 时间戳。

    无法解析、非字符串或不带时区（naive）时返回 ``None``——调用方按
    "时间不可信即视为过期"处理，不得让异常逃逸到 CLI。

    Args:
        raw_timestamp_value (object): 锁 JSON 字段原值。

    Returns:
        datetime | None: 带时区的解析结果；不可信时为 ``None``。
    """
    if not isinstance(raw_timestamp_value, str) or not raw_timestamp_value:
        return None
    try:
        parsed_moment = datetime.fromisoformat(raw_timestamp_value)
    except ValueError:
        return None
    if parsed_moment.tzinfo is None:
        return None
    return parsed_moment


def is_lock_stale(lock_metadata: dict, now_moment: datetime) -> bool:
    """判定锁是否已过期。

    过期条件只有一个：心跳时间无法解析（含 naive 时间戳），或心跳距今超过
    ``STALE_AFTER``。不做 pid 存活探测——锁脚本与 agent 工具调用的会话都是
    短命的，pid 已死不代表持锁会话已死（详见模块 docstring）。

    Args:
        lock_metadata (dict): 锁 JSON 内容。
        now_moment (datetime): 当前时间（带时区）。

    Returns:
        bool: 锁已过期时为 ``True``。
    """
    heartbeat_moment = parse_lock_timestamp(lock_metadata.get("heartbeat_at"))
    if heartbeat_moment is None:
        return True
    return now_moment - heartbeat_moment > STALE_AFTER


def _read_lock_text(lock_path: Path) -> str | None:
    """读取锁文件原文；不存在或不可读时返回 ``None``。"""
    try:
        return lock_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _parse_lock_text(raw_lock_text: str) -> dict:
    """把锁文件原文解析为字典；损坏时返回空字典（调用方按过期锁处理）。"""
    try:
        parsed_metadata = json.loads(raw_lock_text)
    except json.JSONDecodeError:
        return {}
    return parsed_metadata if isinstance(parsed_metadata, dict) else {}


def _read_lock_metadata(lock_path: Path) -> dict:
    """读取锁 JSON；文件损坏时返回空字典（调用方按过期锁处理）。"""
    raw_lock_text = _read_lock_text(lock_path)
    if raw_lock_text is None:
        return {}
    return _parse_lock_text(raw_lock_text)


def inspect_prd_lock(
    main_repo_root: Path, prd_stem: str, now_moment: datetime | None = None
) -> LockSnapshot:
    """查询某条 PRD 的执行锁状态，供看板与提交钩子复用。

    Args:
        main_repo_root (Path): 主仓库根目录。
        prd_stem (str): PRD 文件名去掉 ``.md`` 的短标识。
        now_moment (datetime | None): 判定用当前时间，缺省取系统时间。

    Returns:
        LockSnapshot: ``state`` 为 ``none`` / ``fresh`` / ``stale``。
    """
    lock_path = lock_path_for(main_repo_root, prd_stem)
    if not lock_path.is_file():
        return LockSnapshot(state="none", lock_path=lock_path)
    lock_metadata = _read_lock_metadata(lock_path)
    current_moment = now_moment or datetime.now(timezone.utc)
    lock_state = "stale" if is_lock_stale(lock_metadata, current_moment) else "fresh"
    return LockSnapshot(state=lock_state, metadata=lock_metadata, lock_path=lock_path)


def _build_lock_metadata(worktree_label: str, ai_tool: str, branch_name: str) -> dict:
    """构造一份新锁的 JSON 内容。

    ``pid`` 记录会话首领 pid（``os.getsid(0)``），仅供排查展示，不参与过期判定。
    """
    now_iso_text = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "pid": os.getsid(0),
        "hostname": socket.gethostname(),
        "worktree": worktree_label,
        "started_at": now_iso_text,
        "heartbeat_at": now_iso_text,
        "ai_tool": ai_tool,
        "branch": branch_name,
    }


def _write_lock_file(lock_path: Path, lock_metadata: dict, exclusive: bool) -> bool:
    """把锁 JSON 原子地写入磁盘。

    先写同目录临时文件再 ``os.link`` / ``os.replace`` 到位，保证并发读者看到的
    要么是完整 JSON、要么文件不存在；``O_EXCL`` 语义由 ``os.link`` 的
    ``FileExistsError`` 承担。

    Args:
        lock_path (Path): 锁文件路径。
        lock_metadata (dict): 锁内容。
        exclusive (bool): 为真时仅在锁不存在时创建，已存在则返回 ``False``。

    Returns:
        bool: 是否写入成功。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = lock_path.with_name(f"{lock_path.name}.tmp.{os.getpid()}")
    staging_path.write_text(
        json.dumps(lock_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        if exclusive:
            try:
                os.link(staging_path, lock_path)
            except FileExistsError:
                return False
            return True
        os.replace(staging_path, lock_path)
        return True
    finally:
        staging_path.unlink(missing_ok=True)


def _describe_worktree(worktree_label: str) -> str:
    """把归属标签渲染成可读文本。"""
    return worktree_label if worktree_label else "主仓库"


def _print_holder_info(lock_metadata: dict, prd_file_name: str) -> None:
    """打印持锁者信息与显式释放提示（冲突拒绝时使用）。"""
    print("❌ 该 PRD 正被其他会话执行：")
    print(f"   工具: {lock_metadata.get('ai_tool') or UNKNOWN_TOOL_LABEL}")
    print(f"   分支: {lock_metadata.get('branch') or 'unknown'}")
    print(f"   Worktree: {_describe_worktree(str(lock_metadata.get('worktree') or ''))}")
    print(f"   开始时间: {lock_metadata.get('started_at') or 'unknown'}")
    print(f"   最后心跳: {lock_metadata.get('heartbeat_at') or 'unknown'}")
    print(f"确认对方已停止后，显式释放再重试: just prd release {prd_file_name}")


def _print_checklist_progress(prd_file_path: Path) -> None:
    """顺带输出该 PRD 的验收清单进度，提供开工上下文。"""
    try:
        from prd_status import count_checklist_items  # 延迟 import，避免与看板脚本循环依赖
    except ImportError:
        return
    try:
        raw_prd_text = prd_file_path.read_text(encoding="utf-8")
    except OSError:
        return
    checked_item_count, checklist_item_count = count_checklist_items(raw_prd_text)
    if checklist_item_count > 0:
        print(f"   清单进度: {checked_item_count}/{checklist_item_count}")


def _resolve_branch_label(raw_branch_argument: str | None) -> str:
    """确定锁的分支展示字段：显式传入优先，缺省取当前 git 分支。"""
    if raw_branch_argument:
        return raw_branch_argument
    try:
        return subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


_MAX_CLAIM_ATTEMPTS = 5


def claim_lock(prd_file_name: str, ai_tool: str | None, branch_name: str | None) -> int:
    """原子领取某条 PRD 的执行锁。

    并发安全核心：过期锁接管采用"留档改名 → 排他创建新锁"两段式，改名后先核对
    被归档文件与此前判定的过期锁逐字节一致（不一致说明归档到的是别人刚写入的
    新锁，立即放回去重排），随后用排他创建决出唯一接管者；任何并发组合下恰
    一方成功，失败方在重试循环中重读锁并重新走完整判定。

    Args:
        prd_file_name (str): PRD 文件路径（必须存在）。
        ai_tool (str | None): 自报的 agent 工具名，缺省记 ``unknown``。
        branch_name (str | None): 分支展示字段，缺省取当前 git 分支。

    Returns:
        int: 领锁成功（含幂等刷新与过期接管）为 0；他人新鲜锁冲突为 1。
    """
    prd_file_path = Path(prd_file_name)
    if not prd_file_path.is_file():
        print(f"❌ PRD 文件不存在: {prd_file_name}")
        return 1

    main_repo_root = resolve_main_repo_root()
    worktree_label = resolve_current_worktree_label(main_repo_root)
    prd_stem = prd_file_path.stem
    lock_path = lock_path_for(main_repo_root, prd_stem)
    resolved_branch_name = _resolve_branch_label(branch_name)
    new_lock_metadata = _build_lock_metadata(
        worktree_label, ai_tool or UNKNOWN_TOOL_LABEL, resolved_branch_name
    )
    takeover_archive_path: Path | None = None

    for _claim_attempt in range(_MAX_CLAIM_ATTEMPTS):
        if _write_lock_file(lock_path, new_lock_metadata, exclusive=True):
            if takeover_archive_path is not None:
                print(f"⚠️ 旧锁已过期，已留档为 {takeover_archive_path.name} 并接管。")
            print(f"✅ 已领取 PRD 执行锁: {prd_stem}")
            print(f"   锁文件: {lock_path}")
            _print_checklist_progress(prd_file_path)
            return 0

        raw_existing_text = _read_lock_text(lock_path)
        if raw_existing_text is None:
            # 锁被并发释放或接管，重排后再判定。
            continue
        existing_metadata = _parse_lock_text(raw_existing_text)
        existing_worktree_label = str(existing_metadata.get("worktree") or "")

        if existing_metadata and existing_worktree_label == worktree_label:
            existing_metadata["heartbeat_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            _write_lock_file(lock_path, existing_metadata, exclusive=False)
            print(f"ℹ️ 锁已由当前会话持有，已刷新心跳: {prd_stem}")
            return 0

        if existing_metadata and not existing_worktree_label and worktree_label:
            # just implement 在主仓库领锁后 executor 进入 worktree：开工自检把归属
            # 移交到当前 worktree，并把分支展示字段更新为 worktree 的实际分支，
            # 避免开工入口自我阻塞、看板误显示 @main。
            existing_metadata["worktree"] = worktree_label
            existing_metadata["branch"] = resolved_branch_name
            existing_metadata["heartbeat_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            if ai_tool:
                existing_metadata["ai_tool"] = ai_tool
            _write_lock_file(lock_path, existing_metadata, exclusive=False)
            print(f"🤝 锁由主仓库开工入口领取，已移交到当前 worktree: {worktree_label}")
            return 0

        if is_lock_stale(existing_metadata, datetime.now(timezone.utc)):
            archive_suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
            stale_archive_path = lock_path.with_name(
                f"{LOCK_FILENAME}.{archive_suffix}.{os.getpid()}.stale"
            )
            try:
                os.rename(lock_path, stale_archive_path)
            except OSError:
                # 其他进程已抢先留档或锁状态变化，重排后再判定。
                continue
            if _read_lock_text(stale_archive_path) != raw_existing_text:
                # 归档到的是其他进程刚写入的新锁，而非我们判定的过期锁：
                # 立即放回去并重新走完整判定，绝不能覆盖别人的新锁。
                try:
                    os.rename(stale_archive_path, lock_path)
                except OSError:
                    pass
                continue
            takeover_archive_path = stale_archive_path
            continue

        _print_holder_info(existing_metadata, prd_file_name)
        return 1

    print(f"❌ 锁竞争在 {_MAX_CLAIM_ATTEMPTS} 次尝试内未收敛，请稍后重试: {prd_stem}")
    return 1


def heartbeat_lock(prd_file_name: str) -> int:
    """续期当前会话持有的锁。

    Args:
        prd_file_name (str): PRD 文件路径（只需文件名可解析，文件可已被移动）。

    Returns:
        int: 续期成功为 0；锁不存在或归属不符为 1。
    """
    main_repo_root = resolve_main_repo_root()
    worktree_label = resolve_current_worktree_label(main_repo_root)
    lock_path = lock_path_for(main_repo_root, Path(prd_file_name).stem)

    if not lock_path.is_file():
        print(f"⚠️ 执行锁不存在（可能已被释放或接管）: {prd_file_name}")
        return 1
    existing_metadata = _read_lock_metadata(lock_path)
    lock_holder_worktree_label = str(existing_metadata.get("worktree") or "")
    if lock_holder_worktree_label != worktree_label:
        print(f"⚠️ 执行锁归属不符，当前持有方: {_describe_worktree(lock_holder_worktree_label)}")
        return 1

    existing_metadata["heartbeat_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_lock_file(lock_path, existing_metadata, exclusive=False)
    print(f"💓 心跳已续期: {Path(prd_file_name).stem}")
    return 0


def release_lock(prd_file_name: str, force_release: bool) -> int:
    """释放某条 PRD 的执行锁。

    Args:
        prd_file_name (str): PRD 文件路径。
        force_release (bool): 归属不符时是否强制释放。

    Returns:
        int: 释放成功（或本就无锁）为 0；归属不符且未强制为 1。
    """
    main_repo_root = resolve_main_repo_root()
    worktree_label = resolve_current_worktree_label(main_repo_root)
    lock_path = lock_path_for(main_repo_root, Path(prd_file_name).stem)

    if not lock_path.is_file():
        print(f"ℹ️ 无执行锁可释放: {Path(prd_file_name).stem}")
        return 0
    existing_metadata = _read_lock_metadata(lock_path)
    existing_worktree_label = str(existing_metadata.get("worktree") or "")
    if existing_worktree_label != worktree_label and not force_release:
        print(f"❌ 锁由 {_describe_worktree(existing_worktree_label)} 持有，归属不符。")
        print(f"   确认对方已停止后加 --force 释放: just prd release {prd_file_name} --force")
        return 1

    lock_path.unlink()
    if existing_worktree_label != worktree_label:
        print(f"⚠️ 已强制释放 {_describe_worktree(existing_worktree_label)} 持有的锁。")
    else:
        print(f"✅ 已释放执行锁: {Path(prd_file_name).stem}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Returns:
        int: 进程退出码。
    """
    raw_argument_parser = argparse.ArgumentParser(
        description="PRD 执行锁：claim / heartbeat / release，锁落主仓库证据目录。"
    )
    subparsers = raw_argument_parser.add_subparsers(dest="action", required=True)

    claim_parser = subparsers.add_parser("claim", help="原子领取执行锁")
    claim_parser.add_argument("prd_file", help="PRD 文件路径")
    claim_parser.add_argument("--tool", default=None, help="自报的 agent 工具名（自由文本）")
    claim_parser.add_argument("--branch", default=None, help="分支展示字段，缺省取当前分支")

    heartbeat_parser = subparsers.add_parser("heartbeat", help="续期自己持有的锁")
    heartbeat_parser.add_argument("prd_file", help="PRD 文件路径")

    release_parser = subparsers.add_parser("release", help="释放执行锁")
    release_parser.add_argument("prd_file", help="PRD 文件路径")
    release_parser.add_argument("--force", action="store_true", help="归属不符时强制释放")

    raw_parsed_arguments = raw_argument_parser.parse_args(argv)
    if raw_parsed_arguments.action == "claim":
        return claim_lock(
            raw_parsed_arguments.prd_file, raw_parsed_arguments.tool, raw_parsed_arguments.branch
        )
    if raw_parsed_arguments.action == "heartbeat":
        return heartbeat_lock(raw_parsed_arguments.prd_file)
    return release_lock(raw_parsed_arguments.prd_file, raw_parsed_arguments.force)


if __name__ == "__main__":
    sys.exit(main())

"""按需项目预览的用例编排：解析 profile、发现唯一候选、驱动受管进程。

Issue #256 的 FR-3。触发方式决定了这里的形态：**调用方是执行器里的 agent，
它的 stdin 不是终端**，所以"交用户确认"不可能靠 CLI 弹提示完成。流程是：

1. 用户在执行器对话里明确要求预览项目；
2. agent 先跑 :func:`resolve_preview_command`——仓库声明了
   ``[agent_session.preview].argv`` 就直接用；没声明就只把**唯一**候选报回去，
   不启动任何进程；
3. 用户确认后，agent 用 ``--confirm "<候选原文>"`` 再跑一次；确认文本必须与
   发现的候选逐字相等，否则拒绝。

因此"未经确认不启动进程"是可机器核验的：缺 ``--confirm`` 且无 profile 时，
本用例根本不会拿到进程管理器的调用。第二条边界在端口实现层（回环主机 +
精确进程组），这里不重复设防。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.core.shared.interfaces.agent_session import (
    IPreviewProcessManager,
    PreviewStartRequest,
)
from backend.core.shared.models.agent_session import PreviewState

if TYPE_CHECKING:
    from backend.core.shared.models.agent_runner import AppConfig

#: package.json 里被视为"本地开发入口"的脚本名（按此顺序优先）。
_DEV_SCRIPT_NAMES: tuple[str, ...] = ("dev", "serve", "start")
#: 锁文件 -> 包管理器命令前缀（决定候选 argv 的第一段）。
_PACKAGE_MANAGER_BY_LOCKFILE: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("bun.lockb", "bun"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)
#: Makefile 里被视为开发入口的目标名。
_MAKE_DEV_TARGET = "dev"


@dataclass(frozen=True)
class PreviewCandidate:
    """一个可识别的本地开发入口候选。

    Attributes:
        argv: 若被确认将用于启动的精确 argv。
        source: 候选出处（如 ``package.json scripts.dev`` / ``Makefile dev``），
            用于向用户说明"这条命令是从哪儿读出来的"。
    """

    argv: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class PreviewCommandResolution:
    """预览命令的解析结果（不含任何进程动作）。

    Attributes:
        argv: 可直接启动的精确 argv；``None`` 表示本轮不该启动。
        source: argv 的来源说明（配置字段或发现的入口文件），用于终端回显。
        requires_confirmation: 是否需要用户显式确认后才会启动。
        message: 面向终端/agent 的一行结论（含"为什么没启动"）。
        candidates: 发现的候选数量（0 或多于 1 时无法唯一确定）。
        next_command: 未启动时**可直接执行**的下一步命令（如带 ``--confirm`` 的
            重跑）；无此类命令时为 ``None``。CLI 层的 ``suggestion`` 必须可执行，
            这里把它作为数据给出，避免调用方去 parse 自然语言。
    """

    argv: tuple[str, ...] | None
    source: str
    requires_confirmation: bool
    message: str
    candidates: int = 0
    next_command: str | None = None


@dataclass(frozen=True)
class PreviewCommandOutcome:
    """一次 ``kc preview`` 调用的最终呈现结果。

    Attributes:
        exit_ok: 命令是否达成调用方意图（供 CLI 决定退出码）。
        state: :class:`PreviewState` 取值；候选确认轮次为 ``none``。
        url: 已验证的回环地址；未就绪时 ``None``。
        message: 一行人读结论。
        resolution: 命令解析细节（``start`` 路径才有）。
        next_command: 未达成意图时可直接执行的下一步命令；意图达成时为 ``None``。
    """

    exit_ok: bool
    state: str
    url: str | None
    message: str
    resolution: PreviewCommandResolution | None = None
    next_command: str | None = None


def resolve_preview_command(
    *, config: AppConfig, repo_root: Path, confirmed_argv: str | None
) -> PreviewCommandResolution:
    """决定 ``kc preview start`` 应该启动什么，或为什么不该启动。

    Args:
        config: 目标仓库的生效配置（``[agent_session.preview]`` 已在配置层合并）。
        repo_root: 目标仓库根，候选发现的作用域。
        confirmed_argv: ``--confirm`` 文本；仅在无 profile 时用于确认唯一候选。

    Returns:
        PreviewCommandResolution：``argv`` 为 ``None`` 时调用方必须拒绝启动进程。
    """
    preview_profile = config.agent_session.preview
    if preview_profile.is_declared:
        return PreviewCommandResolution(
            argv=preview_profile.argv,
            source="[agent_session.preview].argv",
            requires_confirmation=False,
            message=(
                f"Using the preview command declared in the repository configuration: "
                f"{' '.join(preview_profile.argv)}"
            ),
        )
    candidates = discover_preview_candidates(repo_root)
    if len(candidates) != 1:
        return PreviewCommandResolution(
            argv=None,
            source="candidate discovery",
            requires_confirmation=False,
            message=(
                f"No [agent_session.preview] profile is declared and candidate discovery "
                f"found {len(candidates)} dev entr{'y' if len(candidates) == 1 else 'ies'} "
                f"in {repo_root}. Declare [agent_session.preview].argv (an exact argv array, "
                "never shell text) and rerun."
            ),
            candidates=len(candidates),
        )
    candidate = candidates[0]
    candidate_text = " ".join(candidate.argv)
    confirm_command = f'kc preview start --confirm "{candidate_text}"'
    if confirmed_argv is None:
        return PreviewCommandResolution(
            argv=None,
            source=candidate.source,
            requires_confirmation=True,
            message=(
                f"Found exactly one preview candidate ({candidate.source}): {candidate_text}. "
                f'Ask the user to confirm it, then rerun with --confirm "{candidate_text}". '
                "No process was started."
            ),
            candidates=1,
            next_command=confirm_command,
        )
    if confirmed_argv.strip() != candidate_text:
        return PreviewCommandResolution(
            argv=None,
            source=candidate.source,
            requires_confirmation=True,
            message=(
                f'--confirm "{confirmed_argv}" does not match the only discovered candidate '
                f'"{candidate_text}" (from {candidate.source}). Refusing to start anything; '
                "confirm the exact command or declare [agent_session.preview].argv."
            ),
            candidates=1,
            next_command=confirm_command,
        )
    return PreviewCommandResolution(
        argv=candidate.argv,
        source=candidate.source,
        requires_confirmation=True,
        message=(
            f"Starting the user-confirmed preview candidate {candidate_text} "
            f"(source: {candidate.source})."
        ),
    )


def discover_preview_candidates(repo_root: Path) -> tuple[PreviewCandidate, ...]:
    """在仓库里发现本地开发入口候选（只读，不执行任何项目脚本）。

    识别范围刻意狭窄：package.json 的 ``dev``/``serve``/``start`` 脚本（按包管理器
    锁文件决定命令前缀），以及 Makefile 的 ``dev`` 目标。其他框架各有各的跑法，
    与其猜，不如让仓库显式声明 ``[agent_session.preview].argv``。多个命中是好事：
    调用方因此知道"不唯一"，会拒绝启动并要求显式配置。

    Args:
        repo_root: 目标仓库根。

    Returns:
        候选列表（按 :data:`_DEV_SCRIPT_NAMES` 的顺序，去重）。
    """
    candidates: list[PreviewCandidate] = []
    package_manager = _resolve_package_manager(repo_root)
    scripts = _read_package_json_scripts(repo_root)
    if package_manager is not None:
        for script_name in _DEV_SCRIPT_NAMES:
            if script_name in scripts:
                candidates.append(
                    PreviewCandidate(
                        argv=(package_manager, "run", script_name),
                        source=f"package.json scripts.{script_name}",
                    )
                )
    if not candidates and _makefile_has_dev_target(repo_root):
        candidates.append(PreviewCandidate(argv=("make", _MAKE_DEV_TARGET), source="Makefile"))
    return tuple(candidates)


def run_preview_start(
    *,
    config: AppConfig,
    repo_root: Path,
    confirmed_argv: str | None,
    preview_manager: IPreviewProcessManager,
) -> PreviewCommandOutcome:
    """解析预览命令并通过受管进程组启动它。

    Args:
        config: 生效配置（提供 profile argv 与 ready 等待参数）。
        repo_root: 目标仓库根，作为 dev 进程的 cwd。
        confirmed_argv: ``--confirm`` 文本（无 profile 时的确认凭据）。
        preview_manager: 端口实现（负责精确进程组、回环校验、注册表）。

    Returns:
        PreviewCommandOutcome：未确认/候选不唯一时 ``exit_ok`` 为假且**没有**
            调用过端口实现。
    """
    resolution = resolve_preview_command(
        config=config, repo_root=repo_root, confirmed_argv=confirmed_argv
    )
    if resolution.argv is None:
        return PreviewCommandOutcome(
            exit_ok=False,
            state=PreviewState.NONE,
            url=None,
            message=resolution.message,
            resolution=resolution,
            next_command=resolution.next_command or "kc preview start",
        )
    preview_profile = config.agent_session.preview
    start_outcome = preview_manager.start_preview(
        PreviewStartRequest(
            argv=resolution.argv,
            cwd=repo_root,
            ready_url=preview_profile.ready_url,
            ready_timeout_seconds=preview_profile.ready_timeout_seconds,
        )
    )
    return PreviewCommandOutcome(
        exit_ok=start_outcome.started,
        state=PreviewState.READY if start_outcome.started else PreviewState.NONE,
        url=start_outcome.record.url if start_outcome.record is not None else None,
        message=f"{resolution.message} {start_outcome.message}",
        resolution=resolution,
        next_command=None if start_outcome.started else "kc preview status",
    )


def run_preview_status(
    *, repo_root: Path, preview_manager: IPreviewProcessManager
) -> PreviewCommandOutcome:
    """读取当前预览状态（只读，永不启动或终止进程）。"""
    status = preview_manager.inspect_preview(repo_root=repo_root)
    return PreviewCommandOutcome(
        # 状态查询本身总算成功：陈旧/外部记录是事实陈述，不是命令失败。
        exit_ok=True,
        state=status.state,
        url=status.url,
        message=status.message,
    )


def run_preview_stop(
    *, repo_root: Path, preview_manager: IPreviewProcessManager
) -> PreviewCommandOutcome:
    """停止 KC 持有且身份可证实的预览进程组。"""
    stop_outcome = preview_manager.stop_preview(repo_root=repo_root)
    return PreviewCommandOutcome(
        exit_ok=stop_outcome.stopped,
        state=PreviewState.NONE if stop_outcome.stopped else PreviewState.FOREIGN,
        url=None,
        message=stop_outcome.message,
        next_command=None if stop_outcome.stopped else "kc preview status",
    )


def _resolve_package_manager(repo_root: Path) -> str | None:
    """按锁文件判定包管理器；没有锁文件时不猜测（宁可不给候选）。"""
    for lockfile_name, package_manager_name in _PACKAGE_MANAGER_BY_LOCKFILE:
        if (repo_root / lockfile_name).is_file():
            return package_manager_name
    return None


def _read_package_json_scripts(repo_root: Path) -> dict[str, object]:
    """读取 ``package.json`` 的 scripts 表；读不到或形状不对时返回空表。"""
    package_json_path = repo_root / "package.json"
    if not package_json_path.is_file():
        return {}
    try:
        payload = json.loads(package_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    scripts = payload.get("scripts") if isinstance(payload, dict) else None
    if not isinstance(scripts, dict):
        return {}
    return {
        name: value
        for name, value in scripts.items()
        if isinstance(name, str) and isinstance(value, str) and value.strip()
    }


def _makefile_has_dev_target(repo_root: Path) -> bool:
    """Makefile（``Makefile`` / ``makefile``）里是否存在独立的 ``dev`` 目标。"""
    for makefile_name in ("Makefile", "makefile"):
        makefile_path = repo_root / makefile_name
        if not makefile_path.is_file():
            continue
        try:
            makefile_text = makefile_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in makefile_text.splitlines():
            if line.startswith(f"{_MAKE_DEV_TARGET}:"):
                return True
    return False


__all__ = [
    "PreviewCommandOutcome",
    "PreviewCommandResolution",
    "discover_preview_candidates",
    "resolve_preview_command",
    "run_preview_start",
    "run_preview_status",
    "run_preview_stop",
]

"""Backlog PRD 验收证据的受限只读访问用例。

事实源是**仓库当前仍保留**的证据目录（默认 ``tasks/evidence/<prd-stem>/``，
legacy 扁平目录沿用既有解析语义），而不是 Issue 关闭后会被清理的临时 orphan
分支。目录解析一律走 :func:`resolve_evidence_dir` 单一入口，禁止在本模块手写
``tasks/evidence/<stem>`` 拼接。

安全边界：

- PRD 路径沿用 ``prd_content_reader`` 的白名单 + containment 校验；
- artifact 只接受纯 basename（拒绝路径分隔符、``.``/``..`` 与隐藏文件）；
- 解析后的目标必须是**证据目录的直接子文件**，符号链接逃逸与其他目录冒充都
  会被拒绝；
- 单文件大小上限保守设定，超限文件返回 4xx 而不是读进内存。
"""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_validation import (
    evidence_dir_path,
    resolve_evidence_dir,
)
from backend.core.use_cases.prd_content_reader import (
    PrdContentError,
    resolve_prd_content_path,
)

#: 单个证据文件的读取上限（10 MiB）：保守值，避免把误放的大文件读进内存。
MAX_EVIDENCE_FILE_BYTES = 10 * 1024 * 1024

_ROLE_EVIDENCE_REPORT = "evidence_report"
_ROLE_VERIFIER_REPORT = "verifier_report"
_ROLE_VERIFICATION_PLAN = "verification_plan"
_ROLE_ARTIFACT = "artifact"

#: 已知报告文件名后缀 → 角色；其余允许文件一律为 ``artifact``。
_ROLE_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("evidence-report.md", _ROLE_EVIDENCE_REPORT),
    ("verifier-report.md", _ROLE_VERIFIER_REPORT),
    ("verification-plan.md", _ROLE_VERIFICATION_PLAN),
)


class BacklogPrdEvidenceError(ValueError):
    """PRD 证据访问被拒绝：路径越界、文件非法、超限或不可读。"""


@dataclass(frozen=True)
class BacklogEvidenceFile:
    """manifest 中的一个允许展示的证据文件。

    Attributes:
        name: 文件名（纯 basename，不含任何路径分隔符）。
        size_bytes: 文件字节数（真实 ``stat`` 值，不是验收勾选数）。
        media_type: 推断出的 MIME 类型，未知类型为 ``application/octet-stream``。
        role: ``evidence_report`` / ``verifier_report`` / ``verification_plan``
            / ``artifact``。
        artifact_token: 已 base64url 编码的文件名，供前端拼接 artifact URL。
        source: 文件来源标注——``worktree``（执行工作区、尚未合并）或 ``main``
            （主仓库）。无候选工作区时统一为 ``main``。
    """

    name: str
    size_bytes: int
    media_type: str
    role: str
    artifact_token: str
    source: str = "main"


@dataclass(frozen=True)
class BacklogPrdEvidenceManifest:
    """某个 PRD 的验收证据清单。

    Attributes:
        prd_path: PRD 的仓库相对路径（与列表接口一致）。
        prd_stem: PRD 文件名 stem，用于定位证据子目录。
        evidence_dir: 解析出的证据目录（仓库相对 POSIX 路径），用于空态提示。
        exists: 证据目录是否存在（任一有效候选目录存在即为真）。
        files: 一层允许展示的文件清单，按名称排序。
        status: 读取状态——``complete`` 表示候选目录成功枚举；``unavailable`` 表示
            工作区发现失败或多重匹配，无法确定证据来源。此时不得把结果当成「确认无文件」
            的空态呈现（``exists`` 与 ``files`` 只是无可用来源，不代表磁盘为空）。
    """

    prd_path: str
    prd_stem: str
    evidence_dir: str
    exists: bool
    files: list[BacklogEvidenceFile]
    status: str = "complete"


def encode_artifact_token(file_name: str) -> str:
    """把文件名编码为 URL 安全片段（与 PRD 路径同一套 base64url 约定）。"""
    return base64.urlsafe_b64encode(file_name.encode("utf-8")).decode("ascii")


def decode_artifact_token(token: str) -> str:
    """解码 artifact token；非法编码时抛 :class:`BacklogPrdEvidenceError`。"""
    try:
        return base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 解码失败即为非法输入
        raise BacklogPrdEvidenceError("非法的证据文件名编码。") from exc


def _classify_role(file_name: str) -> str:
    """按既有命名约定判定文件角色，未知文件为 ``artifact``。"""
    for suffix, role in _ROLE_SUFFIXES:
        if file_name.endswith(suffix):
            return role
    return _ROLE_ARTIFACT


def _guess_media_type(file_name: str) -> str:
    """按文件名推断 MIME 类型；Markdown 显式归为 ``text/markdown``。"""
    if file_name.lower().endswith(".md"):
        return "text/markdown"
    guessed_type, _encoding = mimetypes.guess_type(file_name)
    return guessed_type or "application/octet-stream"


def resolve_prd_evidence_dir(
    repo_path: Path, config: AppConfig, prd_path: str
) -> tuple[Path, str, str]:
    """解析 PRD 对应的证据目录，并把它约束在配置证据根之内。

    Args:
        repo_path: 仓库根目录。
        config: 生效配置，提供 ``validation.evidence_dir``。
        prd_path: PRD 的仓库相对路径（来自列表接口）。

    Returns:
        ``(evidence_dir_abs, evidence_relative_dir, prd_stem)``。

    Raises:
        BacklogPrdEvidenceError: PRD 路径非法，或解析出的目录越出证据根。
    """
    try:
        resolve_prd_content_path(repo_path, prd_path)
    except PrdContentError as exc:
        raise BacklogPrdEvidenceError(str(exc)) from exc

    prd_stem = Path(prd_path).stem
    evidence_root = evidence_dir_path(repo_path, config)
    evidence_dir = resolve_evidence_dir(repo_path, config, prd_stem=prd_stem)
    resolved_root = evidence_root.resolve()
    resolved_dir = evidence_dir.resolve()
    if not (resolved_dir == resolved_root or resolved_dir.is_relative_to(resolved_root)):
        raise BacklogPrdEvidenceError("解析出的证据目录越出了配置的证据根目录。")
    try:
        relative_dir = resolved_dir.relative_to(Path(repo_path).resolve()).as_posix()
    except ValueError:
        relative_dir = str(resolved_dir)
    return evidence_dir, relative_dir, prd_stem


def resolve_evidence_dir_for_root(base_path: Path, config: AppConfig, prd_stem: str) -> Path:
    """在任意仓库/工作区根下解析该 PRD 的证据目录，并 containment 到该根本身的证据根。

    worktree 候选与主仓库候选共用这一条规则（同一 config、同一 legacy/子目录语义），
    避免各拼各的 ``tasks/evidence/<stem>``。裸文件名/遍历产生的越界目录一律拒绝。

    Args:
        base_path: 主仓库根或某个工作区根。
        config: 生效配置，提供 ``validation.evidence_dir``。
        prd_stem: canonical PRD 的文件名 stem。

    Returns:
        containment 校验通过的证据目录绝对路径（可能不存在，由调用方判存在性）。

    Raises:
        BacklogPrdEvidenceError: 解析出的目录越出了该根的证据根目录。
    """
    evidence_root = evidence_dir_path(base_path, config).resolve()
    resolved_dir = resolve_evidence_dir(base_path, config, prd_stem=prd_stem).resolve()
    if not (resolved_dir == evidence_root or resolved_dir.is_relative_to(evidence_root)):
        raise BacklogPrdEvidenceError("解析出的证据目录越出了配置的证据根目录。")
    return resolved_dir


def _scan_evidence_dir(evidence_dir: Path, source_tag: str) -> list[BacklogEvidenceFile]:
    """列出一个候选证据目录内一层允许展示的文件（隐藏/子目录/符号链接/超限跳过）。"""
    if not evidence_dir.is_dir():
        return []
    scanned_files: list[BacklogEvidenceFile] = []
    for candidate_path in sorted(evidence_dir.iterdir(), key=lambda path: path.name):
        if candidate_path.name.startswith("."):
            continue
        if candidate_path.is_symlink() or not candidate_path.is_file():
            continue
        try:
            size_bytes = candidate_path.stat().st_size
        except OSError:  # noqa: BLE001 - 单个文件不可 stat 时跳过而非整体失败
            continue
        if size_bytes > MAX_EVIDENCE_FILE_BYTES:
            # artifact 端点对超限文件必然 4xx，列出来只会给出一个必然失败的下载
            # 动作；manifest 与 artifact 的「允许集合」必须一致。
            continue
        scanned_files.append(
            BacklogEvidenceFile(
                name=candidate_path.name,
                size_bytes=size_bytes,
                media_type=_guess_media_type(candidate_path.name),
                role=_classify_role(candidate_path.name),
                artifact_token=encode_artifact_token(candidate_path.name),
                source=source_tag,
            )
        )
    return scanned_files


def build_evidence_manifest(
    *,
    repo_path: Path,
    config: AppConfig,
    prd_path: str,
    candidates: Sequence[tuple[Path, str]] | None = None,
    status: str = "complete",
) -> BacklogPrdEvidenceManifest:
    """生成 PRD 的证据 manifest（每次调用都重新读盘，不复用任何缓存）。

    单层语义刻意与既有实现保持一致：隐藏文件、子目录与符号链接指向的目录都被
    跳过——递归会把 ``scripts/`` 下的 oracle 源码混作验收产物。

    当给定 ``candidates``（``(绝对证据目录, source 标签)`` 有序序列，工作区在前、
    主仓库兜底）时，按顺序扫描并在候选间合并去重：同名文件以先出现（即工作区）为准并
    标注其来源，主仓库独有文件保留并标 ``main``。不提供 ``candidates`` 时退化为只读
    主仓库目录，行为与既有调用方逐字段一致。

    Args:
        repo_path: 主仓库根目录。
        config: 生效配置。
        prd_path: PRD 的仓库相对路径。
        candidates: 有序候选证据目录及其来源标签；为 ``None`` 时只用主仓库目录。
        status: 读取状态透传（工作区发现失败/歧义时由调用方给 ``unavailable``），
            默认 ``complete``；候选为空且来源不可用时不把结果伪装成确认空态。

    Returns:
        与磁盘一致的证据清单；``evidence_dir`` 恒为主仓库相对路径（用于空态提示）。
    """
    main_evidence_dir, relative_dir, prd_stem = resolve_prd_evidence_dir(
        repo_path, config, prd_path
    )
    candidate_sequence = (
        list(candidates) if candidates is not None else [(main_evidence_dir, "main")]
    )

    merged_files: dict[str, BacklogEvidenceFile] = {}
    exists_any = False
    for candidate_dir, source_tag in candidate_sequence:
        if candidate_dir.is_dir():
            exists_any = True
        for manifest_file in _scan_evidence_dir(candidate_dir, source_tag):
            # 工作区优先：先出现的目录胜出，后续目录的同名文件被忽略。
            merged_files.setdefault(manifest_file.name, manifest_file)

    return BacklogPrdEvidenceManifest(
        prd_path=prd_path,
        prd_stem=prd_stem,
        evidence_dir=relative_dir,
        exists=exists_any,
        files=sorted(merged_files.values(), key=lambda manifest_file: manifest_file.name),
        status=status,
    )


def _validate_artifact_name(artifact_name: str) -> None:
    """校验 artifact 名称是纯 basename：拒绝空、``.``/``..``、路径分隔符与隐藏文件。"""
    if not artifact_name or artifact_name in {".", ".."}:
        raise BacklogPrdEvidenceError("证据文件名非法。")
    if "/" in artifact_name or "\\" in artifact_name or "\x00" in artifact_name:
        raise BacklogPrdEvidenceError("证据文件名不得包含路径分隔符。")
    if artifact_name.startswith("."):
        raise BacklogPrdEvidenceError("隐藏文件不可作为验收证据访问。")


def resolve_evidence_artifact_path(
    repo_path: Path,
    config: AppConfig,
    prd_path: str,
    artifact_name: str,
    candidates: Sequence[tuple[Path, str]] | None = None,
) -> Path:
    """校验并解析单个证据 artifact 的真实路径（分支候选优先、主仓库兜底）。

    允许的 artifact 必须是某个候选证据目录的**直接子文件**：名称不得含路径分隔符、
    不得是 ``.``/``..``、不得是隐藏文件；解析后的真实路径的父目录必须等于该候选目录
    的真实路径，从而挡住符号链接逃逸。给定 ``candidates``（工作区在前）时按顺序命中，
    第一个「存在且合法」的文件即返回；某候选目录里出现逃逸链接立即拒绝、不再回落
    主仓库。不提供 ``candidates`` 时只查主仓库目录，行为与既有调用方逐字节一致。

    Args:
        repo_path: 主仓库根目录。
        config: 生效配置。
        prd_path: PRD 的仓库相对路径。
        artifact_name: 解码后的文件名。
        candidates: 有序候选证据目录（含来源标签）；为 ``None`` 时只用主仓库目录。

    Returns:
        允许读取的绝对文件路径。

    Raises:
        BacklogPrdEvidenceError: 任一安全校验失败、目录缺失或文件不存在。
    """
    _validate_artifact_name(artifact_name)

    main_evidence_dir, _relative_dir, _prd_stem = resolve_prd_evidence_dir(
        repo_path, config, prd_path
    )
    if candidates is None:
        candidate_dirs = [main_evidence_dir]
    else:
        candidate_dirs = [candidate_dir for candidate_dir, _tag in candidates]

    any_dir_exists = False
    for candidate_dir in candidate_dirs:
        if not candidate_dir.is_dir():
            continue
        any_dir_exists = True
        resolved_candidate_dir = candidate_dir.resolve()
        resolved_target = (candidate_dir / artifact_name).resolve()
        if resolved_target.parent != resolved_candidate_dir:
            raise BacklogPrdEvidenceError("证据文件必须直接位于该 PRD 的证据目录内。")
        if not resolved_target.is_file():
            continue
        try:
            if resolved_target.stat().st_size > MAX_EVIDENCE_FILE_BYTES:
                raise BacklogPrdEvidenceError(
                    f"证据文件超过 {MAX_EVIDENCE_FILE_BYTES} 字节上限，请直接下载查看。"
                )
        except OSError as exc:
            raise BacklogPrdEvidenceError(f"证据文件读取失败: {exc}") from exc
        return resolved_target

    if not any_dir_exists:
        raise BacklogPrdEvidenceError("该 PRD 没有可用的证据目录。")
    raise BacklogPrdEvidenceError("证据文件不存在或不是普通文件。")


def read_evidence_artifact(
    repo_path: Path,
    config: AppConfig,
    prd_path: str,
    artifact_name: str,
    candidates: Sequence[tuple[Path, str]] | None = None,
) -> tuple[bytes, str, str]:
    """读取单个证据文件的字节内容与媒体类型（分支候选优先）。

    Returns:
        ``(content_bytes, media_type, file_name)``。

    Raises:
        BacklogPrdEvidenceError: 校验失败或文件不可读。
    """
    target_path = resolve_evidence_artifact_path(
        repo_path, config, prd_path, artifact_name, candidates
    )
    try:
        return target_path.read_bytes(), _guess_media_type(target_path.name), target_path.name
    except OSError as exc:
        raise BacklogPrdEvidenceError(f"证据文件读取失败: {exc}") from exc


def read_evidence_artifact_text(
    repo_path: Path,
    config: AppConfig,
    prd_path: str,
    artifact_name: str,
    candidates: Sequence[tuple[Path, str]] | None = None,
) -> str:
    """以显式 UTF-8 读取文本类证据；解码失败给出清晰错误而不是 500（分支候选优先）。"""
    target_path = resolve_evidence_artifact_path(
        repo_path, config, prd_path, artifact_name, candidates
    )
    try:
        return target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BacklogPrdEvidenceError(
            f"证据文件不是有效的 UTF-8 文本，无法内联预览: {exc}"
        ) from exc
    except OSError as exc:
        raise BacklogPrdEvidenceError(f"证据文件读取失败: {exc}") from exc

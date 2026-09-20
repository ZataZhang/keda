"""Roadmap PRD 验收证据的受限只读访问用例。

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


class RoadmapPrdEvidenceError(ValueError):
    """PRD 证据访问被拒绝：路径越界、文件非法、超限或不可读。"""


@dataclass(frozen=True)
class RoadmapEvidenceFile:
    """manifest 中的一个允许展示的证据文件。

    Attributes:
        name: 文件名（纯 basename，不含任何路径分隔符）。
        size_bytes: 文件字节数（真实 ``stat`` 值，不是验收勾选数）。
        media_type: 推断出的 MIME 类型，未知类型为 ``application/octet-stream``。
        role: ``evidence_report`` / ``verifier_report`` / ``verification_plan``
            / ``artifact``。
        artifact_token: 已 base64url 编码的文件名，供前端拼接 artifact URL。
    """

    name: str
    size_bytes: int
    media_type: str
    role: str
    artifact_token: str


@dataclass(frozen=True)
class RoadmapPrdEvidenceManifest:
    """某个 PRD 的验收证据清单。

    Attributes:
        prd_path: PRD 的仓库相对路径（与列表接口一致）。
        prd_stem: PRD 文件名 stem，用于定位证据子目录。
        evidence_dir: 解析出的证据目录（仓库相对 POSIX 路径），用于空态提示。
        exists: 证据目录是否存在。
        files: 一层允许展示的文件清单，按名称排序。
    """

    prd_path: str
    prd_stem: str
    evidence_dir: str
    exists: bool
    files: list[RoadmapEvidenceFile]


def encode_artifact_token(file_name: str) -> str:
    """把文件名编码为 URL 安全片段（与 PRD 路径同一套 base64url 约定）。"""
    return base64.urlsafe_b64encode(file_name.encode("utf-8")).decode("ascii")


def decode_artifact_token(token: str) -> str:
    """解码 artifact token；非法编码时抛 :class:`RoadmapPrdEvidenceError`。"""
    try:
        return base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 解码失败即为非法输入
        raise RoadmapPrdEvidenceError("非法的证据文件名编码。") from exc


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
        RoadmapPrdEvidenceError: PRD 路径非法，或解析出的目录越出证据根。
    """
    try:
        resolve_prd_content_path(repo_path, prd_path)
    except PrdContentError as exc:
        raise RoadmapPrdEvidenceError(str(exc)) from exc

    prd_stem = Path(prd_path).stem
    evidence_root = evidence_dir_path(repo_path, config)
    evidence_dir = resolve_evidence_dir(repo_path, config, prd_stem=prd_stem)
    resolved_root = evidence_root.resolve()
    resolved_dir = evidence_dir.resolve()
    if not (resolved_dir == resolved_root or resolved_dir.is_relative_to(resolved_root)):
        raise RoadmapPrdEvidenceError("解析出的证据目录越出了配置的证据根目录。")
    try:
        relative_dir = resolved_dir.relative_to(Path(repo_path).resolve()).as_posix()
    except ValueError:
        relative_dir = str(resolved_dir)
    return evidence_dir, relative_dir, prd_stem


def build_evidence_manifest(
    *, repo_path: Path, config: AppConfig, prd_path: str
) -> RoadmapPrdEvidenceManifest:
    """生成 PRD 的证据 manifest。

    单层语义刻意与 :func:`list_evidence_files` 保持一致：隐藏文件、子目录与
    符号链接指向的目录都被跳过——递归会把 ``scripts/`` 下的 oracle 源码混作
    验收产物。无目录时返回 ``exists=False`` 的空清单，由页面呈现明确空态。

    Args:
        repo_path: 仓库根目录。
        config: 生效配置。
        prd_path: PRD 的仓库相对路径。

    Returns:
        与磁盘一致的证据清单（每次调用都重新读盘，不复用任何缓存）。
    """
    evidence_dir, relative_dir, prd_stem = resolve_prd_evidence_dir(repo_path, config, prd_path)
    if not evidence_dir.is_dir():
        return RoadmapPrdEvidenceManifest(
            prd_path=prd_path,
            prd_stem=prd_stem,
            evidence_dir=relative_dir,
            exists=False,
            files=[],
        )

    manifest_files: list[RoadmapEvidenceFile] = []
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
        manifest_files.append(
            RoadmapEvidenceFile(
                name=candidate_path.name,
                size_bytes=size_bytes,
                media_type=_guess_media_type(candidate_path.name),
                role=_classify_role(candidate_path.name),
                artifact_token=encode_artifact_token(candidate_path.name),
            )
        )
    return RoadmapPrdEvidenceManifest(
        prd_path=prd_path,
        prd_stem=prd_stem,
        evidence_dir=relative_dir,
        exists=True,
        files=manifest_files,
    )


def resolve_evidence_artifact_path(
    repo_path: Path, config: AppConfig, prd_path: str, artifact_name: str
) -> Path:
    """校验并解析单个证据 artifact 的真实路径。

    允许的 artifact 必须是证据目录的**直接子文件**：名称不得含路径分隔符、
    不得是 ``.``/``..``、不得是隐藏文件；解析后的真实路径的父目录必须等于
    证据目录的真实路径，从而挡住符号链接逃逸。

    Args:
        repo_path: 仓库根目录。
        config: 生效配置。
        prd_path: PRD 的仓库相对路径。
        artifact_name: 解码后的文件名。

    Returns:
        允许读取的绝对文件路径。

    Raises:
        RoadmapPrdEvidenceError: 任一安全校验失败。
    """
    if not artifact_name or artifact_name in {".", ".."}:
        raise RoadmapPrdEvidenceError("证据文件名非法。")
    if "/" in artifact_name or "\\" in artifact_name or "\x00" in artifact_name:
        raise RoadmapPrdEvidenceError("证据文件名不得包含路径分隔符。")
    if artifact_name.startswith("."):
        raise RoadmapPrdEvidenceError("隐藏文件不可作为验收证据访问。")

    evidence_dir, _relative_dir, _prd_stem = resolve_prd_evidence_dir(repo_path, config, prd_path)
    if not evidence_dir.is_dir():
        raise RoadmapPrdEvidenceError("该 PRD 没有可用的证据目录。")

    resolved_evidence_dir = evidence_dir.resolve()
    resolved_target = (evidence_dir / artifact_name).resolve()
    if resolved_target.parent != resolved_evidence_dir:
        raise RoadmapPrdEvidenceError("证据文件必须直接位于该 PRD 的证据目录内。")
    if not resolved_target.is_file():
        raise RoadmapPrdEvidenceError("证据文件不存在或不是普通文件。")

    try:
        if resolved_target.stat().st_size > MAX_EVIDENCE_FILE_BYTES:
            raise RoadmapPrdEvidenceError(
                f"证据文件超过 {MAX_EVIDENCE_FILE_BYTES} 字节上限，请直接下载查看。"
            )
    except OSError as exc:
        raise RoadmapPrdEvidenceError(f"证据文件读取失败: {exc}") from exc
    return resolved_target


def read_evidence_artifact(
    repo_path: Path, config: AppConfig, prd_path: str, artifact_name: str
) -> tuple[bytes, str, str]:
    """读取单个证据文件的字节内容与媒体类型。

    Returns:
        ``(content_bytes, media_type, file_name)``。

    Raises:
        RoadmapPrdEvidenceError: 校验失败或文件不可读。
    """
    target_path = resolve_evidence_artifact_path(repo_path, config, prd_path, artifact_name)
    try:
        return target_path.read_bytes(), _guess_media_type(target_path.name), target_path.name
    except OSError as exc:
        raise RoadmapPrdEvidenceError(f"证据文件读取失败: {exc}") from exc


def read_evidence_artifact_text(
    repo_path: Path, config: AppConfig, prd_path: str, artifact_name: str
) -> str:
    """以显式 UTF-8 读取文本类证据；解码失败给出清晰错误而不是 500。"""
    target_path = resolve_evidence_artifact_path(repo_path, config, prd_path, artifact_name)
    try:
        return target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RoadmapPrdEvidenceError(
            f"证据文件不是有效的 UTF-8 文本，无法内联预览: {exc}"
        ) from exc
    except OSError as exc:
        raise RoadmapPrdEvidenceError(f"证据文件读取失败: {exc}") from exc

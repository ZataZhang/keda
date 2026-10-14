"""渲染 PR 中按清单分组的 Realistic Validation 证据。"""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_structured_evidence import (
    EvidenceUpload,
    StructuredEvidenceReport,
    _label,
    _render_evidence_file_lines,
)
from backend.core.use_cases.agent_runner_validation_identity import (
    EvidenceMarker,
    EvidenceMarkerV2,
    format_validation_evidence_marker,
)


def render_structured_evidence_comment(
    report: StructuredEvidenceReport,
    upload: EvidenceUpload,
    worktree_path: Path,
    config: AppConfig,
    pr_url: str,
    head_sha: str,
    *,
    evidence_dir: Path | None = None,
    base_sha: str | None = None,
    patch_id: str | None = None,
) -> str:
    """Render the structured PR evidence comment grouped by checklist item.

    Args:
        report: 已校验并按 checklist 分组的证据报告。
        upload: 上传到 orphan evidence branch 的文件清单。
        worktree_path: 生成和读取证据的仓库工作树。
        config: Agent Runner 配置。
        pr_url: 关联 Pull Request 的页面地址。
        head_sha: 发布证据时 PR 的 head。
        evidence_dir: 解析后的按任务证据目录；缺省时使用配置根目录。
        base_sha: v2 marker 的 PR base 快照；缺省时保留 v1 marker。
        patch_id: v2 marker 的 PR 改动指纹；缺省时保留 v1 marker。

    Returns:
        按 checklist 分组的 PR 证据评论。
    """
    language = report.language
    marker_fields = {
        "head_sha": head_sha,
        "branch": upload.branch,
        "count": len(upload.file_names),
    }
    if base_sha is not None and patch_id is not None:
        evidence_marker = EvidenceMarkerV2(**marker_fields, base_sha=base_sha, patch_id=patch_id)
    else:
        evidence_marker = EvidenceMarker(**marker_fields)
    comment_lines = [
        format_validation_evidence_marker(evidence_marker),
        "",
        f"## {_label(language, 'title')}",
        "",
        f"- {_label(language, 'branch')}: `{upload.branch}`",
        f"- {_label(language, 'head')}: `{head_sha}`",
        *([f"- {_label(language, 'patch')}: `{patch_id}`"] if patch_id else []),
        f"- {_label(language, 'language')}: `{language}`",
        "",
        _label(language, "sign_off"),
    ]

    for item_report in report.items:
        block = item_report.block
        comment_lines.extend(
            [
                "",
                f"### RV-{block.item_number} {block.item_name}",
                "",
                f"**{_label(language, 'reproducible_command')}**",
                f"`{block.command}`",
                "",
                f"**{_label(language, 'evidence_files')}**",
            ]
        )
        for file_info in item_report.files:
            comment_lines.extend(
                _render_evidence_file_lines(
                    file_info=file_info,
                    branch=upload.branch,
                    pr_url=pr_url,
                    worktree_path=worktree_path,
                    config=config,
                    language=language,
                    evidence_dir=evidence_dir,
                )
            )
        comment_lines.extend(
            [
                "",
                f"**{_label(language, 'output_summary')}**",
                block.output_summary,
                "",
                f"**{_label(language, 'explanation')}**",
                block.explanation,
                "",
                f"**{_label(language, 'risks')}**",
                block.risks,
            ]
        )
        if block.negative_control:
            comment_lines.extend(
                [
                    "",
                    f"**{_label(language, 'negative_control')}**",
                    f"`{block.negative_control}`",
                ]
            )
            if block.expected_fail:
                comment_lines.append(
                    f"- {_label(language, 'expected_fail')}: {block.expected_fail}"
                )

    return "\n".join(comment_lines)

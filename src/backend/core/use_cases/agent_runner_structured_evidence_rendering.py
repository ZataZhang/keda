"""渲染 PR 中按清单分组的 Realistic Validation 证据。"""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_primary_demo import PrimaryDemo
from backend.core.use_cases.agent_runner_structured_evidence import (
    EvidenceUpload,
    StructuredEvidenceReport,
    _label,
    _render_evidence_file_lines,
    build_evidence_blob_url,
)
from backend.core.use_cases.agent_runner_validation_identity import (
    EvidenceMarker,
    EvidenceMarkerV2,
    format_validation_evidence_marker,
)

# 主演示块专用文案；其余固定文案沿用 ``_label`` 的共享表
_DEMO_LABELS: dict[str, dict[str, str]] = {
    "zh-CN": {
        "primary_demo": "主演示",
        "demo_recording": "录屏",
        "mock_warning": "⚠️ mock，未验证",
        "rv_details": "逐项验证证据（RV）",
    },
    "en-US": {
        "primary_demo": "Primary demo",
        "demo_recording": "Recording",
        "mock_warning": "⚠️ mock, not verified",
        "rv_details": "Per-item validation evidence (RV)",
    },
}


def _demo_label(language: str, key: str) -> str:
    """返回主演示块的本地化文案，未知语言回退英文。"""
    return _DEMO_LABELS.get(language, _DEMO_LABELS["en-US"])[key]


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

    primary_demo = report.primary_demo
    if primary_demo is not None:
        # 主演示是给人看的第一屏；rv 逐项证据折叠，留给 verifier 与下钻
        comment_lines.extend(_render_primary_demo_lines(primary_demo, upload, pr_url, language))
        comment_lines.extend(
            ["", "<details>", f"<summary>{_demo_label(language, 'rv_details')}</summary>"]
        )

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

    if primary_demo is not None:
        comment_lines.extend(["", "</details>"])

    return "\n".join(comment_lines)


def _render_primary_demo_lines(
    primary_demo: PrimaryDemo, upload: EvidenceUpload, pr_url: str, language: str
) -> list[str]:
    """渲染主演示块：录屏链接 + 按顺序的每步结论，mock 步带警示。"""
    blob_url = build_evidence_blob_url(pr_url, upload.branch, primary_demo.file)
    recording_text = (
        f"[`{primary_demo.file}`]({blob_url})" if blob_url else f"`{primary_demo.file}`"
    )
    demo_lines = [
        "",
        f"### {_demo_label(language, 'primary_demo')}",
        "",
        f"**{_demo_label(language, 'demo_recording')}**: {recording_text}",
        "",
    ]
    for step_number, demo_step in enumerate(primary_demo.steps, start=1):
        mock_suffix = f" — {_demo_label(language, 'mock_warning')}" if demo_step.mock else ""
        demo_lines.append(
            f"{step_number}. **{demo_step.caption}**：{demo_step.result}{mock_suffix}"
        )
    return demo_lines

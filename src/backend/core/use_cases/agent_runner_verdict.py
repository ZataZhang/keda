"""独立 verifier 的 verdict 协议：结论模型、隐藏 marker 与复验 prompt 组装。

本模块只承载**协议本身**——确定性解析与 prompt 组装，不含 agent 调用与编排
（跑 verifier 见 ``run_verifier_agent``，跑门禁见 ``run_verifier_gate``）。协议
解析遵循 fail-safe：没有可解析的 verdict（verifier 没产出 / 产出畸形）一律按
``red``（阻断）处理，绝不让"无判定"静默放行。marker 与 ``agent_runner_events``
的 ``kc:`` hidden marker 同型。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.core.shared.models.agent_runner import IssueSummary, TokenUsage
from backend.core.use_cases.agent_runner_structured_evidence import EvidenceManifest

_VALID_RISKS: tuple[str, ...] = ("green", "yellow", "red")
_VERDICT_MARKER_PATTERN = re.compile(
    r"<!--\s*kc:verifier-verdict\s+risk=(?P<risk>green|yellow|red)\s*-->"
)
_NO_VERDICT_FINDINGS = "No parseable verifier verdict marker found; treating as blocked (red)."


@dataclass(frozen=True)
class ValidationVerdict:
    """Independent verifier's structured verdict.

    ``risk`` 取值 {``green``, ``yellow``, ``red``}:green/yellow 放行(yellow
    附警告评论、不阻断),red 阻断发布并打回 builder。``findings`` 是人读的
    发现说明(对抗验证中发现的缝隙 / 风险)。

    ``marker_found`` 区分"verifier 真的判了 red"与"verifier 没吐出 verdict
    marker、被 fail-safe 当成 red"。两者都阻断,但成因完全不同:后者是 verifier
    侧的协议/可靠性故障,不代表 builder 的改动有缺陷。

    ``agent`` 与 ``token_usage`` 由实际运行 verifier 的调用点回填：候选回退后
    ``agent`` 是真正跑完的 agent；agent 未运行（parse-only 路径）时保持默认值。
    ``verifier_input_sha`` 与 ``verifier_run_id`` 将真实 verdict 绑定到运行控制行，
    供发布前 fresh-read、过期隔离和同一运行的 hold 清理使用。
    """

    risk: str
    findings: str = ""
    marker_found: bool = True
    agent: str = ""
    token_usage: "TokenUsage | None" = None
    verifier_input_sha: str | None = None
    verifier_run_id: str | None = None

    @property
    def passed(self) -> bool:
        """green 或 yellow 视为放行(yellow 仅警告,不阻断发布)。"""
        return self.risk in ("green", "yellow")

    @property
    def blocks(self) -> bool:
        """red 阻断发布,走 repair 循环打回 builder。"""
        return self.risk == "red"

    @property
    def missing_marker(self) -> bool:
        """verifier 没有产出可解析的 verdict marker(fail-safe 记为 red)。"""
        return not self.marker_found


class VerifierUnavailableError(RuntimeError):
    """候选 verifier 全部不可用或未能给出可解析结论，停止而不让 builder 盲修。"""


def format_verifier_verdict_marker(risk: str) -> str:
    """Format the hidden ``kc:verifier-verdict`` marker for a PR/Issue comment."""
    if risk not in _VALID_RISKS:
        raise ValueError(f"invalid verifier risk: {risk!r}; expected one of {_VALID_RISKS}")
    return f"<!-- kc:verifier-verdict risk={risk} -->"


def parse_verifier_verdict(text: str, *, findings: str = "") -> ValidationVerdict:
    """Deterministically parse the latest verifier-verdict marker from ``text``.

    Fail-safe:没有可解析的 marker 一律返回 ``red``——绝不把"无判定"当成
    通过。纯确定性解析,不引入 LLM。当文本里有多个 marker(例如 repair 后
    重新裁决),取最后一个。

    Args:
        text: verifier agent 的输出 / PR 评论文本。
        findings: 可选的人读发现说明,原样带入 verdict。

    Returns:
        解析出的 ``ValidationVerdict``;无 / 畸形 marker 时为 red。
    """
    latest_match = None
    for latest_match in _VERDICT_MARKER_PATTERN.finditer(text):
        pass
    if latest_match is None:
        # findings 仍带回原始输出以便排查,但 marker_found=False 才是"没判定"的
        # 唯一可靠信号——以前只靠 findings 是否等于哨兵字符串来区分,而调用方
        # 总会填入响应文本,于是哨兵永远走不到,红判与漏 marker 无法分辨。
        return ValidationVerdict(
            risk="red",
            findings=findings or _NO_VERDICT_FINDINGS,
            marker_found=False,
        )
    return ValidationVerdict(risk=latest_match.group("risk"), findings=findings)


def build_verifier_prompt(
    issue: IssueSummary,
    builder_sha: str,
    manifest: EvidenceManifest,
) -> str:
    """Assemble the independent-verifier prompt.

    给 verifier 注入需求意图(issue 正文)与验收 oracle(manifest 各项的真实
    入口命令 / 负控 / 期望失败),并下达"从意图独立出题、亲自跑真实入口、对抗
    证伪、跑负控、给 verdict"的指令;明令不许信 builder 的绿,确认不了即 red。
    """
    oracle_lines: list[str] = []
    for block in manifest.items:
        oracle_lines.append(f"- Item {block.item_number} ({block.item_name}):")
        oracle_lines.append(f"    real entry / command: {block.command}")
        if block.negative_control:
            oracle_lines.append(f"    negative control (must go RED): {block.negative_control}")
        if block.expected_fail:
            oracle_lines.append(f"    expected failure: {block.expected_fail}")
        if block.evidence_files:
            files = ", ".join(block.evidence_files)
            oracle_lines.append(f"    evidence artifacts on disk: {files}")
        for spec in block.expected_artifacts:
            claim = f" (key claim: {spec.key_claim!r})" if spec.key_claim else ""
            oracle_lines.append(
                f"    expected artifact: {spec.path} mime={spec.mime}"
                f" min_size={spec.min_size} min_duration={spec.min_duration_seconds}"
                f"{claim}"
            )
    oracle_block = "\n".join(oracle_lines) or "(no structured oracle items)"

    return "\n".join(
        [
            f"You are an INDEPENDENT verifier for issue #{issue.number}: "
            f'"{issue.title}". A different agent (the builder) already implemented',
            f"it at commit {builder_sha} in this worktree. Your job is NOT to trust",
            "their work — it is to independently decide whether the feature actually",
            "does what was asked, and to try to prove it does NOT.",
            "",
            "What it is supposed to do (derive your OWN checks from this; do not just",
            "re-run the builder's tests):",
            issue.body.strip(),
            "",
            "The acceptance oracle it was meant to satisfy:",
            oracle_block,
            "",
            "Do this:",
            '1. From the intent above, independently decide what "working" means.',
            "   Do not assume the builder tested the right thing.",
            "2. Run the real entry point yourself in this worktree and observe the",
            "   result with your own eyes. Do not mock or stub the thing under test.",
            '3. Adversarially probe the gap between "tests pass" and "a real user runs',
            '   it and it works": edge cases, empty/invalid input, the real end-to-end',
            "   path, anything the happy-path tests would miss.",
            "4. Run each negative control to confirm the check actually goes RED when",
            "   the feature is broken — a test that cannot fail proves nothing.",
            "5. Verdict: green = you independently confirmed it (incl. adversarial",
            "   probes); yellow = main path works but a real, non-blocking gap/risk",
            "   (state it); red = does NOT match the intent, OR you found a break, OR",
            "   you could not independently confirm it.",
            "",
            "Multimodal evidence: the manifest's `evidence artifacts` may include",
            "images (.png/.jpg), videos (.webm/.mp4), audio, or other non-text",
            "files. Handle them with whatever your model natively supports — read",
            "the image directly if you can, use `ffmpeg` to extract a frame from",
            "a video if you can, listen to audio if you can. If you cannot read",
            "the artifact yourself, fall back to shell metadata: `stat <path>` for",
            "size, `file --mime <path>` for type, `ffprobe <path>` for duration /",
            "resolution. Never trust the file's existence or size alone — a 0-byte",
            "screenshot or a video with no frames is not evidence. State in your",
            "findings which artifacts you actually inspected and how.",
            "",
            "When the oracle includes `key_claim` assertions on an artifact, apply",
            "the D-14c fairness rule: IF you are a multimodal model that CAN read",
            "the file directly, verify the key_claim and report what you actually",
            "saw (green if confirmed, red if contradicted). IF you are a TEXT-ONLY",
            "model that CANNOT read images/video/audio, state 'I am text-only and",
            "cannot visually verify the key_claim; only file metadata was checked'.",
            "Encourage a self-downgrade to yellow in that case — but NEVER return",
            "red solely because you cannot read a non-text artifact. Red is for",
            "proven breaks only, not model-capability gaps.",
            "",
            "Be a skeptic. If you cannot independently confirm it works, the verdict",
            "is RED — do not give the builder the benefit of the doubt.",
            "",
            "End your report with concrete findings (what you ran, what you observed,",
            "any gap between intent and behavior), then exactly one final line:",
            "<!-- kc:verifier-verdict risk=green -->   (or yellow, or red)",
        ]
    )

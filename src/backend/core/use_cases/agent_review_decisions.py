"""Pre-PR 审查结果解析与多来源决策合并。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from backend.core.shared.models.agent_runner import ReviewFinding

_VALID_REVIEW_VERDICTS = {"approved", "changes_requested"}
_logger = logging.getLogger("backend.core.use_cases.agent_review")


@dataclass(frozen=True)
class ReviewerDecision:
    """Parsed pre-PR reviewer decision."""

    verdict: str
    summary: str = ""
    findings: tuple[ReviewFinding, ...] = ()
    findings_critical: int = 0
    findings_high: int = 0
    findings_medium: int = 0
    findings_low: int = 0
    parseable: bool = True

    @property
    def has_findings(self) -> bool:
        """Return True when at least one structured finding was captured."""
        return bool(self.findings)

    def recomputed_counts(self) -> tuple[int, int, int, int]:
        """Return ``(critical, high, medium, low)`` counts derived from findings."""
        critical = high = medium = low = 0
        for finding in self.findings:
            severity = finding.severity.strip().lower()
            if severity == "critical":
                critical += 1
            elif severity == "high":
                high += 1
            elif severity == "medium":
                medium += 1
            elif severity == "low":
                low += 1
        return critical, high, medium, low


def parse_reviewer_decision(text: str) -> ReviewerDecision:
    """Parse reviewer verdict and findings from agent output."""
    payload = _extract_json_payload(text)
    if payload is not None:
        verdict = _normalize_review_verdict(payload.get("verdict"))
        if verdict in _VALID_REVIEW_VERDICTS:
            findings = _parse_findings_array(payload.get("findings"))
            decision = ReviewerDecision(
                verdict=verdict,
                summary=str(payload.get("summary", "")),
                findings=findings,
                findings_critical=0,
                findings_high=_int_field(payload, "findings_high"),
                findings_medium=_int_field(payload, "findings_medium"),
                findings_low=_int_field(payload, "findings_low"),
            )
            return _finalize_decision_counts(decision)

    fallback_verdict = _parse_text_verdict(text)
    if fallback_verdict is not None:
        return ReviewerDecision(verdict=fallback_verdict, summary=text.strip())

    return ReviewerDecision(
        verdict="changes_requested",
        summary="Reviewer did not return a parseable verdict.",
        parseable=False,
    )


def _merge_reviewer_decisions(
    stdout_decision: ReviewerDecision,
    commit_request_decision: ReviewerDecision | None,
) -> ReviewerDecision:
    """Prefer stdout verdicts, falling back to commit-request metadata.

    The stdout decision is the authoritative source for findings when it is
    parseable; the commit-request file is only used to recover a missing
    verdict, never to override structured findings.
    """
    if stdout_decision.parseable or commit_request_decision is None:
        return stdout_decision
    # When stdout is not parseable, fall back to the commit-request verdict
    # wholesale so reviewers that only encode their decision in the request
    # file still drive the gate correctly.
    return commit_request_decision


def _parse_findings_array(raw: object) -> tuple[ReviewFinding, ...]:
    """Convert a JSON ``findings`` array into a tuple of ``ReviewFinding``.

    Missing or malformed entries fall back to defaults so partial output from
    the reviewer still produces usable comment data instead of breaking the
    gate.
    """
    if not isinstance(raw, list):
        return ()
    findings: list[ReviewFinding] = []
    for entry in raw:
        if isinstance(entry, dict):
            findings.append(
                ReviewFinding(
                    category=str(entry.get("category", "")),
                    severity=str(entry.get("severity", "")),
                    title=str(entry.get("title", "")),
                    description=str(entry.get("description", "")),
                    file=str(entry.get("file", "")),
                    line=_safe_int(entry.get("line")),
                    recommendation=str(entry.get("recommendation", "")),
                )
            )
        elif isinstance(entry, str) and entry.strip():
            # Allow legacy string-array findings ("first finding") by treating
            # the string as a description on a medium-severity finding.
            findings.append(
                ReviewFinding(
                    severity="medium",
                    title=entry.strip()[:120],
                    description=entry.strip(),
                )
            )
    return tuple(findings)


def _safe_int(value: object) -> int:
    """Best-effort coercion to ``int``; returns 0 for any non-numeric input."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _finalize_decision_counts(
    decision: ReviewerDecision,
    fallback_counts: tuple[int, int, int, int] | None = None,
) -> ReviewerDecision:
    """Recompute severity counts from findings and reconcile verdict.

    The reviewer-supplied counts (or any provided fallback) are ignored when
    findings are present, since the parser is the only authoritative source.
    When findings exist alongside ``verdict == "approved"``, the verdict is
    downgraded to ``changes_requested`` so the gate does not silently
    approve unreviewed issues.
    """
    if decision.has_findings:
        critical, high, medium, low = decision.recomputed_counts()
    elif fallback_counts is not None:
        critical, high, medium, low = fallback_counts
    else:
        critical = decision.findings_critical
        high = decision.findings_high
        medium = decision.findings_medium
        low = decision.findings_low

    new_verdict = decision.verdict
    new_summary = decision.summary
    if decision.has_findings and decision.verdict == "approved":
        _logger.warning(
            "Reviewer returned verdict=approved with %d finding(s); "
            "downgrading to changes_requested.",
            len(decision.findings),
        )
        new_verdict = "changes_requested"
        if not new_summary:
            new_summary = "Findings reported without explicit changes_requested."

    if (
        new_verdict == decision.verdict
        and new_summary == decision.summary
        and critical == decision.findings_critical
        and high == decision.findings_high
        and medium == decision.findings_medium
        and low == decision.findings_low
    ):
        return decision

    return ReviewerDecision(
        verdict=new_verdict,
        summary=new_summary,
        findings=decision.findings,
        findings_critical=critical,
        findings_high=high,
        findings_medium=medium,
        findings_low=low,
        parseable=decision.parseable,
    )


def _extract_json_payload(text: str) -> dict[str, object] | None:
    r"""Recover a JSON object from reviewer output, tolerating nested objects.

    Prefers fenced ``\`\`\`json\`\`\`` blocks, then tries the outermost balanced
    brace pair following ``"verdict"`` so reviews that emit nested finding
    objects (which break the greedy regex fallback) still parse.
    """
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        json_text = match.group(1)
    else:
        candidate = _extract_outermost_json_object(text)
        if candidate is None:
            return None
        json_text = candidate
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        payload = _loads_with_trailing_comma_fix(json_text)
        if payload is None:
            return None
    return payload if isinstance(payload, dict) else None


def _loads_with_trailing_comma_fix(json_text: str) -> dict[str, object] | None:
    """Retry ``json.loads`` after stripping trailing commas from objects/arrays.

    The reviewer is allowed to emit slightly malformed JSON (trailing commas
    inside arrays). Fixing them in place lets the parser recover the verdict
    and findings without forcing the runner to hard-fail on cosmetic issues.
    """
    cleaned = re.sub(r",(\s*[}\]])", r"\1", json_text)
    if cleaned == json_text:
        return None
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_outermost_json_object(text: str) -> str | None:
    """Return the substring of the outermost ``{...}`` containing ``"verdict"``."""
    verdict_match = re.search(r'"verdict"\s*:\s*"', text)
    if verdict_match is None:
        return None
    start = text.rfind("{", 0, verdict_match.start())
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _normalize_review_verdict(raw_verdict: object) -> str:
    verdict_text = str(raw_verdict or "").strip().lower().replace("-", "_")
    verdict_text = re.sub(r"\s+", "_", verdict_text)
    if verdict_text in {"approve", "approved", "pass", "passed"}:
        return "approved"
    if verdict_text in {
        "changes_requested",
        "change_requested",
        "request_changes",
        "requested_changes",
        "needs_changes",
        "not_approved",
    }:
        return "changes_requested"
    return verdict_text


def _parse_text_verdict(text: str) -> str | None:
    # 引号可选：兼容纯文本（verdict: approved）与残缺 JSON（"verdict": "approved"）。
    normalized_text = text.strip().lower()
    if re.search(r"[\"']?verdict[\"']?\s*[:=-]\s*[\"']?approved\b", normalized_text):
        return "approved"
    if re.search(
        r"[\"']?verdict[\"']?\s*[:=-]\s*[\"']?changes?[_ -]requested\b",
        normalized_text,
    ):
        return "changes_requested"
    return None


def _int_field(payload: dict[str, object], key: str) -> int:
    try:
        return int(payload.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0

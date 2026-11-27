"""提交前代码审查的默认提示词，与审查执行逻辑分离。"""

REPAIR_VALIDATION_GUIDANCE = (
    "Prepare the code patch even when you cannot run a platform-specific check. "
    "Run available checks, report which required checks are still unverified, "
    "and follow the Issue and repository rules for requesting a commit."
)

SELF_REPAIR_MODE_LINES = (
    "",
    "Complete the review and repairs in this same session before returning:",
    "- Collect all confirmed problems, then fix every one in this worktree. Continue "
    "through the remaining problems after each patch and verify your own repairs.",
    "- Return only after all confirmed problems are fixed and checked, or you have "
    "exhausted feasible approaches to a concrete blocker. For each blocked problem, "
    "describe what you tried, the observed obstacle, and what is needed to continue.",
    "- A partial patch or a failed validation command is a reason to keep diagnosing "
    "and repairing. Continue other repairs while an external dependency is blocked.",
    f"- {REPAIR_VALIDATION_GUIDANCE}",
    "- When requesting a commit for the actual patch, write "
    "`.agent-runner/commit-request.json` with a descriptive `commit_message`.",
    "",
    "Final response: one JSON object in a markdown code block with `summary` and "
    "`repairs`. Record every confirmed problem in `repairs`, including those you fixed. "
    "Use an empty array only when the review found no problems.",
    "Each repair uses the problem fields above plus:",
    '- "status": "fixed" or "blocked".',
    '- "fix": actual changes made, including affected files; for a blocked problem, '
    "include attempted approaches and any partial repair.",
    '- "verification": commands/checks and observed results for the final code tree.',
    '- "blocker": concrete obstacle and what is needed to proceed; empty when fixed.',
    "Mark a repair fixed after verifying it. If a required check remains impossible, "
    "report that problem as blocked and retain the completed code patch.",
)

READ_ONLY_REVIEW_MODE_LINES = (
    "",
    "Read-only review mode:",
    "- Do NOT modify any file in the worktree.",
    "- Do NOT write `.agent-runner/commit-request.json`; a separate repair agent "
    "applies the fixes and any commit request you write is discarded.",
    "- Only report findings (a structured JSON verdict with findings).",
    "Final response: one JSON object in a markdown code block with `summary`, "
    "`verdict` (approved or changes_requested), and `findings` (problem fields above). "
    "Use approved only when there are no unresolved findings.",
    "Each finding also includes `recommendation`, describing how to fix it.",
)

# 默认规则调用 code-reviewer skill，并将结论交给 runner 的结构化输出契约。
DEFAULT_REVIEW_PROMPT_TEMPLATE: tuple[str, ...] = (
    "Before completing your report, call the `code-reviewer` skill using the Skill tool "
    "with the diff and PRD context above.",
    "Use the skill's findings as the confirmed problems to address in this session.",
    (
        "Complete the review of the current diff and requirement/evidence boundaries "
        "before starting a repair. Collect all actionable findings "
        "in this pass and consolidate duplicate root causes."
    ),
    (
        "Trace the requested behavior through its normal entry point. If the requirement "
        "also applies to an alternate command, configuration, or existing state, use a "
        "focused check to verify the affected route before finishing."
    ),
    (
        "Ground each actionable finding in an explicit requirement or an existing contract "
        "you can identify in code, documentation, or tests. Preserve documented precedence, "
        "defaults, and compatibility unless the task requests changing them. Explain "
        "uncertain requirements in the summary; do not invent a new policy from ambiguous "
        "wording and then treat the current behavior as a defect."
    ),
    (
        "If a patch is rejected because its context no longer matches, reread the affected "
        "file and revise against its current contents before trying again."
    ),
    (
        "On a follow-up review, focus on the repair diff, previously reported findings, "
        "and affected boundaries. Report newly discovered regressions, but do not reopen "
        "resolved findings without evidence or introduce unrelated cleanup."
    ),
    (
        "Focused review does not waive final delivery gates: check that the required "
        "verification and independent evidence apply to the final code tree; never "
        "reuse a verdict or test flag for a different tree."
    ),
    "After using the skill, produce the final JSON report for the review mode below.",
    "",
    "Problem fields for each entry in the report:",
    "```json",
    "[",
    "  {",
    '    "category": "requirement|code|validation|docs",',
    '    "severity": "critical|high|medium|low",',
    '    "file": "path/to/file.py",',
    '    "line": 42,',
    '    "title": "short title",',
    '    "description": "why this is a problem"',
    "  }",
    "]",
    "```",
)

"""提交前代码审查的默认提示词，与审查执行逻辑分离。"""

# 默认规则调用 code-reviewer skill，并将结论交给 runner 的结构化输出契约。
DEFAULT_REVIEW_PROMPT_TEMPLATE: tuple[str, ...] = (
    "Before writing your verdict, call the `code-reviewer` skill using the Skill tool "
    "with the diff and PRD context above.",
    "Use the skill's findings to populate the `findings` array in your response.",
    (
        "Complete the review of the current diff and requirement/evidence boundaries "
        "before editing; collect all actionable findings in this pass, consolidate "
        "duplicate root causes, and then repair the complete set together."
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
    "If the skill reports no findings, verdict must be `approved`.",
    "If findings exist, apply fixes in the worktree and write "
    "`.agent-runner/commit-request.json` with a descriptive `commit_message`.",
    "Do not leave findings unaddressed while returning `approved`.",
    "",
    "CRITICAL: The `code-reviewer` skill's Chinese text report is input for your "
    "judgment, NOT your final answer to the runner. After calling the skill, you "
    "MUST still produce a final ```json code block with the verdict/summary/findings "
    "schema below. The runner parses only that JSON block; without it the review "
    "fails with 'no parseable verdict'.",
    "",
    "Findings JSON schema:",
    "```json",
    "[",
    "  {",
    '    "category": "requirement|code|validation|docs",',
    '    "severity": "critical|high|medium|low",',
    '    "file": "path/to/file.py",',
    '    "line": 42,',
    '    "title": "short title",',
    '    "description": "why this is a problem",',
    '    "recommendation": "how to fix"',
    "  }",
    "]",
    "```",
    "",
    "Final response must be a single JSON object in a markdown code block with:",
    "- verdict: one of `approved`, `changes_requested`.",
    "- summary: short rationale.",
    "- findings: array of objects matching the schema above (may be empty).",
)

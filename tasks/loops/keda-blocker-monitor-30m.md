---
id: keda-blocker-monitor-30m
schedule: "30m"
repo_id: keda
managed_by: agent-daemon
issue_type: bug
agent: auto
queue_ready: true
output: issue
timezone: Asia/Shanghai
priority: P1
slug: keda-blocker-monitor
skip_when: needs_attention=0
pre_command: 'blocked="$(gh issue list --repo ZataZhang/keda --state open --label agent/blocked --limit 1000 --json number --jq length 2>/dev/null)" || blocked=unknown; failed="$(gh issue list --repo ZataZhang/keda --state open --label agent/failed --limit 1000 --json number --jq length 2>/dev/null)" || failed=unknown; case "$blocked:$failed" in *[!0-9:]*|:*|*:) printf "blocked_count=unknown\nfailed_count=unknown\nneeds_attention=unknown\n";; *) total=$((blocked + failed)); printf "blocked_count=%s\nfailed_count=%s\nneeds_attention=%s\n" "$blocked" "$failed" "$total";; esac'
---

# Keda blocker and failure patrol — {{date}}

This is the Keda task-recovery patrol that fires every 30 minutes. The counts read before the trigger:

- `agent/blocked`: {{blocked_count}}
- `agent/failed`: {{failed_count}}
- Total: {{needs_attention}}

If reading the GitHub state fails, the counts show `unknown`; diagnose the access failure first and do not treat it as an empty queue.

## Steps

1. Re-read every open `agent/blocked` and `agent/failed` Issue in the current repository so you do not act on stale counts. `gh issue list` queries must pass `--limit 1000` explicitly (the default returns only 30). Read each Issue body, the latest Attempt History and the related PR state one by one, and use `kc logs --repo-id keda --issue <N>` to read this attempt's log.
2. For `agent/failed`, classify the root cause first. Publishing or status-finalisation failures use `kc recover --issue <N>`; for execution failures, fix the confirmed code, configuration or local-environment cause first, then requeue through the recovery flow. Do not retry blindly while the root cause is unknown.
3. For `agent/blocked`, the repository maintainer has authorised automatic handling of well-scoped, verifiable technical blockers. Only after the root cause is fixed, run `kc blocked-continue --issue <N>` and confirm the Issue returned to the correct state.
4. Do not guess product trade-offs, access permissions, secrets, protected paths or external approvals. When a blocker needs a human decision, keep the original Issue blocked, comment the evidence, the missing decision and the minimal options, then put this patrol Issue into blocked and wait for the maintainer.
5. Do not take over Issues that are still `agent/running`, and do not kill a process or start a second execution just because the log went quiet for a while. Running tasks are recovered by the daemon's stall supervisor and the original execution flow.
6. After handling, re-read the target Issue state and logs to confirm the recovery actually took effect. If nothing is left to handle on re-check, close this patrol Issue and state that the round left no blockers; if this round produced a fix PR or still needs a human decision, keep this patrol Issue and its links, and never claim it is resolved when it is not.

## Patrol result and PR description

- If this round produces a code-fix PR, append `## Round summary (for PR description generation)` to the end of this patrol Issue body, listing for each target Issue: the related PR, the symptom and root cause, this round's fix, the actual verification commands and results, the target Issue's current state, and the remaining human decisions. Do not put this information only in an Issue comment: PR description generation reads the Issue body, the commits and the diff, not the comments.
- Only ever append at the very **end** of the body: read the current body back before writing, and after writing `## Steps`, `## Patrol result and PR description`, `## Constraints` and `## Delivery Notes` must all still be present verbatim. Do not locate a section by heading text and replace everything after it — an earlier round matched a section title quoted in backticks inside the instruction above and swallowed the body after `## Constraints`; this patrol Issue body is the single source of the requirement, and losing a section means nobody knows the constraints.
- The fix commit's title or body should carry the target Issue number and the concrete change, so that if PR description generation falls back, the commit history still explains what changed.
- The draft PR body must state the target Issue, the symptom/root cause, what was fixed, the verification results and what is left. Keep `Closes #<this patrol Issue>` so merging closes this patrol and lets the next round schedule; when the target Issue still has unfinished acceptance or pending human decisions, use only `Addresses #<target Issue>` / `Related to #<target Issue>` and never a keyword that would close the target Issue automatically.
- PR metadata does not fix itself just because the generator was fixed: when `create_draft_pr` finds an existing open PR for the branch it **returns that URL without regenerating** the title or body (see `docs/guides/agent-runner.md`). After every fix cycle, verify and refresh it yourself with `gh pr edit --title` / `--body-file`: the title is a Conventional Commits subject of ≤ 72 characters; `Closes #<this patrol Issue>` must be the bare text of the **first line** of the body — inside a ``` fence GitHub does not honour it (empty `closingIssuesReferences`), so this Issue will not close when the PR merges and the loop's "pause when the previous round is still open" rule blocks the next round; rewrite the body from the **current commit set and current verification results**, leaving no previous round's commit SHAs or "not run" conclusions behind.

## Constraints

- Only handle `agent/blocked` and `agent/failed` Issues in the Keda repository; do not widen to ordinary todos or Issues awaiting human review.
- Code fixes follow `docs/ai-standards/`, use the highest-fidelity real entry point available for verification, and submit a draft PR through the normal runner flow.
- Restore the original Issue only after the root cause is fixed and verified; when verification fails, keep the blocker and record the failure evidence.

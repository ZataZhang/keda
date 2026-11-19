# Human Review Checklist — Issue #406

## Decision

No second recovery should start while Issues #366 and #400 have live runner leases. Keep patrol #406 open while they run. Issue #366's current log also records that publishing its raw `rv-1` output through the GitHub connector requires approval, which this session cannot grant; a maintainer must resolve that approval path if the active runner cannot publish it.

## Issue and PR status

- Patrol [Issue #406](https://github.com/ZataZhang/keda/issues/406) is open on branch `issue-406` (`2059d4f` when checked). No PR existed for this branch before this handoff commit request.
- Target [Issue #366](https://github.com/ZataZhang/keda/issues/366) is open and still labeled `agent/failed` by GitHub. Its claim lease renewed at `2026-11-19T17:42:35Z`; `kc peers` reports it as `agent/running`.
- [PR #404](https://github.com/ZataZhang/keda/pull/404) is an open, draft PR for #366 at `6379a08` with merge state `UNSTABLE`. CI and Validation Gate passed; [two macOS Install smoke jobs were cancelled](https://github.com/ZataZhang/keda/actions/runs/44572250803), while the other reported checks passed. The latest supervisor event asks the active runner to publish raw `rv-1` output and refresh the title and stale review materials. The PRD's human-confirmed item remains unchecked.
- Target [Issue #400](https://github.com/ZataZhang/keda/issues/400) is open and still labeled `agent/failed`; its lease renewed at `2026-11-19T17:41:34Z`, and `kc peers` reports it as `agent/running`.
- [PR #405](https://github.com/ZataZhang/keda/pull/405) for #400 is open, not draft, and blocked at head `74ba5cb`. [Validate Template failed the file-length gate](https://github.com/ZataZhang/keda/actions/runs/44573954300/job/135736252232); the latest supervisor rework is splitting the overlong orchestration module. The #400 PRD's implementation checks are complete, but its human confirmation of #398 remains pending.

## What changed and evidence

This patrol adds this status handoff only. No product code or GitHub Issue/PR state was changed. The #366 target branch's [PRD](https://github.com/ZataZhang/keda/blob/issue-366/tasks/archive/P1-FEAT-20261113-121113-nightly-cleanup-keda.md) and [verifier report](https://github.com/ZataZhang/keda/blob/issue-366/tasks/evidence/P1-FEAT-20261113-121113-nightly-cleanup-keda/P1-FEAT-20261113-121113-nightly-cleanup-keda.verifier-report.md) record `rv-1` passing at implementation SHA `525a3549`. Its PRD header says awaiting human acceptance, but its checklist still contains a validation-blocked sentence. The latest supervisor event requests refreshed review material and raw-output publication; the active agent log says the GitHub connector rejected that write because approval is required and this session's policy is `never`.

The #400 target branch's [PRD](https://github.com/ZataZhang/keda/blob/issue-400/tasks/archive/P1-FIX-20261119-180000-prd-publication-on-protected-main.md) records all four implementation validations as checked, with human confirmation of #398 still pending. Its active supervisor is repairing the new `Validate Template` line-count failure.

Commands and results:

- `gh issue list --state open --label agent/blocked --limit 1000 --json number,title,url` — no Issues.
- `gh issue list --state open --label agent/failed --limit 1000 --json number,title,url` — #400 and #366.
- `gh issue view 366 --json number,title,state,labels,updatedAt,comments` — open, labeled `agent/failed`, with a freshly renewed lease.
- `kc peers --repo-id keda` — #366 reported `agent/running`.
- `kc logs --repo-id keda --issue 366 --lines 8` — read successfully; the active agent reported an approval-gated GitHub write for raw-output publication and is updating local records. Full log: `kc logs --repo-id keda --issue 366`.
- `gh pr view 404 --json number,title,state,isDraft,url,headRefName,headRefOid,baseRefName,mergeStateStatus,reviewDecision,closingIssuesReferences,statusCheckRollup` — open draft at `6379a08`, merge state `UNSTABLE`; two macOS Install smoke jobs are `CANCELLED`.
- `gh issue view 400 --json number,title,state,url,body,labels,updatedAt,comments` — open, labeled `agent/failed`; latest supervisor event requests repair of PR #405.
- `kc logs --repo-id keda --issue 400 --lines 30` — read successfully; the active agent is inspecting the oversized orchestration module and existing tests. Full log: `kc logs --repo-id keda --issue 400`.
- `kc peers --repo-id keda` — #366 and #400 both reported `agent/running`.
- `gh pr view 405 --json number,title,state,isDraft,url,headRefName,headRefOid,baseRefName,mergeStateStatus,reviewDecision,closingIssuesReferences,statusCheckRollup` — open, non-draft, blocked at `74ba5cb`; `Validate Template` failed.
- `git show 74ba5cb248dcf8ae73f5106ba4c46df4f2cc9afd:tasks/archive/P1-FIX-20261119-180000-prd-publication-on-protected-main.md` — the target PRD records implementation checks complete and #398 human confirmation pending.
- `gh pr list --state all --head issue-406 --limit 1000 --json number,title,state,url` — no existing PR before this handoff commit request.
- `python3 -m json.tool .agent-runner/commit-request.json` and `jq -e 'type == "object" and (.commit_message | type == "string" and length > 0)' .agent-runner/commit-request.json` — both passed.
- `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" uv run mkdocs build --strict` — passed; documentation built in 8.44 seconds.
- `git diff --no-index --check /dev/null tasks/evidence/issue-406/human-review-checklist.md` — no whitespace findings (the wrapper accepted exit 1 for the expected new-file diff).
- Product tests — not run; this handoff changes no product code.

## Remaining work and next action

It is unverified whether either live runner will finish and update GitHub state. The #366 raw-output comment remains blocked by an approval requirement; the #400 Validate Template fix has not yet been verified. Let both runners and the daemon's stall supervisor continue. Re-read both Issues, logs, and PRs after the runs settle; do not start parallel recoveries while the leases are active. This is backend/CI workflow work; screenshots and video do not apply.

## Patrol closing condition

Keep #406 open while either target remains in the blocked/failed queue, either PR needs more automated repair, or the #366 publication approval remains unresolved. Close #406 only after a fresh recheck finds no unresolved targets and no pending human decisions.

# Human Review Checklist — Issue #406

## Decision

Keep patrol #406 open. Issues #366 and #400 still have active runner leases, so do not start duplicate recovery. GitHub labels #406 agent/failed, while kc peers still reports its runner as agent/running; preserve this mismatch for the next patrol.

The #366 publication decision remains unresolved. The required raw rv-1 outputs and PR evidence comment have not been published. A prior GitHub connector write was rejected with MCP tool call requires approval, but approval policy is never. A maintainer must authorize connector publication or identify an approved alternate route. Do not bypass the approval gate or requeue #366 while its runner lease remains active.

## Current issue and PR state

Snapshot checked at 2026-11-20 03:34 Asia/Shanghai (2026-11-19 19:34 UTC).

- Patrol Issue #406 is open and labeled agent/failed in GitHub. kc peers --repo-id keda reported an active agent/running lease. PR #409 is open draft at 7d6a701e6f9ae3fdeb3b60e88b4fab4abe2cb3ee, merge state CLEAN. Current CI, Validation Gate, and Install smoke checks passed; the optional public-PyPI install probe was skipped. Its first body line is Closes #406, and its closing reference includes #406.
- Target Issue #366 is open and labeled agent/failed; kc peers reports its lease as active/agent/running. PR #404 is open draft at 2c1042485b0980cfc33a2ab315ea0fd8969737a4, merge state CLEAN; current CI, Validation Gate, and Install smoke checks passed. Its PR comment timeline is empty. The latest supervisor note still says raw rv-1 outputs are unpublished and requests publication before human review.
- Target Issue #400 is open and labeled agent/failed; kc peers reports its lease as active/agent/running. PR #405 is open and non-draft at 5676be1f90dc931ffe616b7b369d58daad0dcf4f, merge state DIRTY, with no checks in its current status rollup. Human confirmation of #398 remains pending.

## PR #409 metadata refresh

Prepared title: test(issue-406): widen active-output test timeout

The GitHub connector rejected the requested title/body update with MCP tool call requires approval, but approval policy is never. The remote PR metadata therefore remains unchanged. No CLI or alternate write path was attempted. This is the exact proposed body, prepared for review:

> Closes #406
>
> ## Patrol round summary
>
> This PR contains the handoff record and a subprocess test timeout fix in two commits:
>
> - docs(issue-406): record #366 and #400 handoff records patrol evidence and target decisions.
> - test(issue-406): widen active-output test timeout raises the wall timeout for test_captured_output_includes_stdout_written_when_process_exits from 20 to 60 seconds. The old limit could expire while the child was still producing output; the test still checks captured output and retains its 3-second inactivity timeout. This changes the test allowance, not product behavior.
>
> ## Current snapshot — 2026-11-20 03:34 Asia/Shanghai
>
> - #366 is open and labeled agent/failed; kc peers reports its lease as active/agent/running. PR #404 is open draft at 2c1042485b0980cfc33a2ab315ea0fd8969737a4, merge state CLEAN; CI, Validation Gate, and Install smoke checks passed. The required PR evidence comment is absent (PR #404 has no comments). Its latest supervisor note says raw rv-1 outputs remain unpublished. The #366 publication permission decision is unresolved: a maintainer must authorize connector publication or identify an approved alternate path. A prior connector comment write was rejected for approval, and no bypass was used.
> - #400 is open and labeled agent/failed; kc peers reports its lease as active/agent/running. PR #405 is open and non-draft at 5676be1f90dc931ffe616b7b369d58daad0dcf4f, merge state DIRTY, with no checks reported. Human confirmation of #398 remains pending.
> - #406 is open and labeled agent/failed in GitHub, while kc peers still reports an active/agent/running lease. PR #409 is open draft at 7d6a701e6f9ae3fdeb3b60e88b4fab4abe2cb3ee, merge state CLEAN; current CI, Validation Gate, and Install smoke checks passed. The optional public-PyPI install probe was skipped, and the closing reference still points to #406.
>
> ## Verification
>
> At source head 7d6a701e6f9ae3fdeb3b60e88b4fab4abe2cb3ee: targeted subprocess tests passed (2 passed in 27.60s); just test all passed with full lint, 5,520 passed, and 1 skipped in 412.90s. Final source-evidence checks passed: git diff --check, uv run mkdocs build --strict, and all-file pre-commit with check-test-flag explicitly skipped. No independent verifier ran; Issue #406 has no PRD.
>
> ## Remaining human decision
>
> The #366 raw-output publication decision remains with the maintainer. Merging this PR does not authorize that publication or resolve the decision.
>
> <!-- kc:merge-acceptance version=1 -->
> Merging this PR accepts the recorded patrol outcome and subprocess test timeout adjustment, and authorizes post-merge acceptance recording on Issue #406. No PRD file exists for this patrol; the #366 publication permission decision remains unresolved and outside this PR acceptance.
>
> - PRD: none — Issue #406 has no PRD file; the review surface is tasks/evidence/issue-406/human-review-checklist.md.

## Verification and source evidence

The reviewed branch HEAD is 7d6a701e6f9ae3fdeb3b60e88b4fab4abe2cb3ee. The PR has two commits: docs(issue-406): record #366 and #400 handoff and test(issue-406): widen active-output test timeout. The test change raises the subprocess wall timeout from 20 to 60 seconds while keeping the 3-second inactivity timeout and stdout assertion.

Recorded verification at this source head:

- Targeted subprocess tests passed: 2 passed in 27.60s.
- just test all passed with full lint, 5,520 passed, and 1 skipped in 412.90s.
- Prior final source-evidence checks passed: git diff --check; uv run mkdocs build --strict; all-file pre-commit with check-test-flag explicitly skipped. These will be rerun against this refreshed checklist.
- GitHub check rollups: PR #409 and #404 CI, Validation Gate, and Install smoke passed. PR #405 had no checks reported.

Read-only commands and evidence:

- git rev-parse HEAD, git log -5 --oneline --decorate, and git show HEAD:tests/test_process_runner.py confirmed source head and 60-second timeout.
- gh pr view 409 --json number,title,body,state,isDraft,url,headRefName,headRefOid,baseRefName,mergeStateStatus,closingIssuesReferences,statusCheckRollup returned PR #409 metadata and checks.
- gh pr view 404 and gh pr view 405 returned target PR heads and checks.
- GitHub connector issue snapshots for #366, #400, and #406; current #366 issue comments and #404 PR comments read. PR #404 has no comments; latest #366 activity records the rebase repair completion and the preceding supervisor note still requires raw outputs and a PR evidence comment.
- kc peers --repo-id keda reported #366, #400, and #406 active/agent/running leases. It returned a snapshot with warnings that local logs and the API budget ledger outside this worktree could not be written.
- GitHub connector update request for PR #409 title/body was rejected with MCP tool call requires approval, but approval policy is never; no alternate write path was attempted.
- Final local checks for this handoff refresh: git diff --check, uv run mkdocs build --strict, and all-file pre-commit with check-test-flag skipped — passed.

## Finding disposition

- Fixed locally: the checklist reflects current commit, verification, and fresh Issue/PR states, including the #406 label/lease mismatch and unresolved #366 permission decision.
- Blocked externally: PR #409 title/body refresh was prepared but the GitHub connector rejected the write under the configured approval policy. The remote metadata remains stale.
- No independent verifier or visual evidence applies to this patrol record.

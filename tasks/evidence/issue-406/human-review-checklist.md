# Human Review Checklist — Issue #406

## Decision and next human action

Keep patrol #406 open. The required state transition is to move both #366 and #406 from `agent/failed` to `agent/blocked`. The reason is the unresolved maintainer decision on publishing #366's required raw `rv-1` outputs and evidence comment. The GitHub write connector rejected the label, Issue-body, comment, and PR-body updates because approval is required while the configured approval policy is `never`; readback confirms that no external write took effect. The current GitHub labels therefore remain `agent/failed` until those updates can be applied.

**Next action for the maintainer:** choose whether to authorize publication of the required raw `rv-1` outputs and evidence comment for PR #404, then apply the prepared Issue labels/comments/body append from `.agent-runner/github-write-drafts.md` and PR description from `.agent-runner/pr-409-body.md`.

- **Approve publication:** authorize publishing the required outputs and comment, then let the normal recovery/verification flow continue.
- **Decline publication:** keep #366 and #406 blocked; specify an acceptable alternative evidence path if one should be used.

Issue #400 remains open with `agent/failed`. PR #405 is still not mergeable, so its rebase-repair completion comment is not proof that the PR is integrated or its current head is checked.

**Close #406 only after a final patrol re-read** confirms that #366's publication decision is recorded, the required evidence and verification are complete, #366 is no longer blocked/failed, #400's PR conflict is resolved and its required checks and #398 human confirmation are complete, and the #409 fix PR has been handled. If any target or human action remains unresolved, keep #406 open and record the remaining blocker.

## Current Issue and PR snapshot

Checked at 2026-11-20 05:37 Asia/Shanghai (2026-11-19 21:37 UTC).

- **#406:** open, currently labeled `agent/failed`; the requested blocked transition was not applied. PR #409 is open draft, mergeable, and `CLEAN` at `c9f3de8d283dd6e8d2cddb9300aa044af0643cce`. It has five commits at this snapshot. Its 15 check runs show 13 successes and 2 skips (`Report main CI failure` and the optional public-PyPI probe); no check failed. The latest known lease renewal is `2026-11-19T20:53:21Z`, expired at 21:13:21Z.
- **#366:** open, currently labeled `agent/failed`; the requested blocked transition was not applied. Its last known lease renewal was `2026-11-19T20:29:23Z`, expired at 20:49:23Z. PR #404 is open draft, mergeable, and `CLEAN` at `2c1042485b0980cfc33a2ab315ea0fd8969737a4`. Required raw `rv-1` output and its evidence comment are still absent. The latest failure comment records a non-fast-forward push rejection. The missing decision is whether the maintainer authorizes publication; no choice has been made by this patrol.
- **#400:** open, `agent/failed`; PR #405 is open and non-draft at `1153018ec124f6318c4a9d9a1702054cb9f17a4c`, and GitHub reports `mergeable=false`. The current head has zero check runs. A prior supervisor found a merge-tree conflict at `e87337f`; the later rebase-repair comment says local verification passed, but the current PR metadata still reports it unmergeable. Human confirmation of #398 also remains pending.

## PR #409 metadata handoff

The patrol Issue requires the PR title and body to follow the current branch history and verification after each fix cycle. The current remote PR body is still stale: it retains the 20:53 UTC snapshot and old target states. The PR-body update was rejected by the GitHub connector, and the readback confirms the old body remains. A current replacement with `Closes #406` as its first line is prepared in `.agent-runner/pr-409-body.md`.

- Title: `docs(issue-406): refresh current review handoff`.
- The first body line is bare `Closes #406`.
- The prepared body snapshot includes the current #366, #400, #406 and PR states, verification, the maintainer decision, and the patrol close condition. It refers to target Issues without closing keywords.
- Existing branch commits are listed by subject; the PR header remains the authoritative head SHA. The runner will create this repair's commit as `docs(issue-406): record blocked patrol handoff`; no future SHA is guessed.
- PR #409's latest published checks were fetched from the GitHub check-runs API for `c9f3de8d283dd6e8d2cddb9300aa044af0643cce`. PR #405's current head has zero check runs.

The remote body still begins with `Closes #406`, but it has not been refreshed and the Issue body has not received the required appended round summary. The prepared replacement preserves `Closes #406` as its first line and states that #406 stays open/blocked pending the #366 decision. It does not imply that passing checks on #409 resolve #366 or #400.

## Verification and source evidence

- On PR #409 head `c9f3de8d283dd6e8d2cddb9300aa044af0643cce`, GitHub reports 15 completed check runs: 13 `success`, 2 `skipped`. CI, Validation Gate (`Validate Template` and `Realistic Validation sign-off`), and install smoke checks passed. The skipped checks are `Report main CI failure` and the optional public-PyPI probe.
- The focused captured-output test passed: 1 test in 20.84s. `just test all` passed: 5,520 passed, 1 skipped in 412.90s. Both are historical evidence from the test-fix commit; the later source commits before this repair changed documentation only.
- Earlier all-file pre-commit hooks passed with `check-test-flag` skipped. The prior handoff's `git diff --check`, guidelines-consistency check, and `mkdocs build --strict` passed. Current documentation-only repair checks are recorded below after execution.
- No product code or test code changes are part of this repair. Do not describe the historical full suite as rerun for this handoff.
- Issue state, labels, latest relevant comments and lease markers were read from GitHub Issues #366, #400 and #406. PR state, head and mergeability were read for #404, #405 and #409. Check runs were fetched for the exact current PR heads.
- Issue #406 has no PRD, so no independent PRD verifier applies. Human decisions for #366 and #400 remain outside the implementation change in PR #409.

## Final-tree evidence

- Source head when this repair began: `c9f3de8d283dd6e8d2cddb9300aa044af0643cce`.
- The runner will create the requested commit after this repair. The prepared PR body lists its subject without inventing a SHA; the remote body has not changed.
- This repair updates this local checklist, prepares the Issue writes in `.agent-runner/github-write-drafts.md`, prepares the PR body in `.agent-runner/pr-409-body.md`, and writes `.agent-runner/commit-request.json`. The requested remote label changes, Issue #406 round-summary append, comments, and PR #409 body update were rejected and remain unapplied. No product code changed.
- `git diff --check` passed. `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" uv run python hooks/shared/check_guidelines_consistency.py` passed. `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" uv run mkdocs build --strict` passed (exit 0; MkDocs emitted informational unlisted-page and optional Black/Ruff signature-formatting notices). `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" uv run python -m json.tool .agent-runner/commit-request.json` passed.
- GitHub readback after the rejected writes confirms #366 and #406 are still open/`agent/failed` with unchanged comment counts (21 and 18); #406 has no appended round summary at the end of its body; PR #409 still has its old 20:53 UTC body. No remote label, Issue body, comment, or PR body update succeeded.
- Final `git status --short --branch` shows `## issue-406...zata/issue-406` and one tracked change: `tasks/evidence/issue-406/human-review-checklist.md`. `git diff --name-only` lists only that path; `git rev-parse HEAD` remains `c9f3de8d283dd6e8d2cddb9300aa044af0643cce`.
- `.agent-runner/commit-request.json`, `.agent-runner/github-write-drafts.md`, and `.agent-runner/pr-409-body.md` are present in the worktree; the runner draft files are not listed in the tracked diff. No staging, commit, reset, checkout, merge, or push is performed.

## Finding disposition

- Partially fixed: refreshed this local checklist to the current `c9f3de8` head and five-commit branch and recorded #405's current `mergeable=false` and zero checks. The replacement PR body is prepared locally, but GitHub rejected the external update, so the remote PR body remains stale.
- Confirmed and prepared, but not applied remotely: #366 and #406 need `agent/blocked`, with the missing publication decision and yes/no options recorded on both Issues. The connector rejected the label and comment writes; readback confirms both Issues remain open/`agent/failed`. The explicit close condition is recorded above and in the prepared drafts.
- No finding is disputed. The required external updates were blocked by the tool's approval policy, not by disagreement with the findings.

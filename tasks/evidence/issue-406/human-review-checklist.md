# Human Review Checklist — Issue #406

## Decision

Keep patrol #406 open. Issues #366 and #400 remain open with `agent/failed`; neither target has been recovered. At the snapshot below, #366's last lease has expired; confirm live ownership before any recovery. #400 and #406 have fresh lease markers. Issue #366 still needs maintainer direction on publishing its required raw `rv-1` outputs. Do not treat the PR metadata refresh as target recovery or close this patrol.

## Current Issue and PR snapshot

Checked at 2026-11-20 04:53 Asia/Shanghai (2026-11-19 20:53 UTC).

- Issue #406 is open and labeled `agent/failed`; its latest lease renewal is `2026-11-19T20:53:21Z` with a 1,200-second TTL. PR #409 is open draft, mergeable, and `CLEAN`. The latest pushed head before this docs-only repair is `b148cd5a5f5ebfd275c6f1d52da82379029f9114`. Its 15 GitHub check runs show 13 successes and 2 skips: `Report main CI failure` and the optional public-PyPI probe. No check failed.
- Issue #366 is open and labeled `agent/failed`; its latest lease renewal was `2026-11-19T20:29:23Z` with a 1,200-second TTL, expired at 20:49:23Z. Confirm live ownership before any recovery. PR #404 remains open draft, mergeable, and `CLEAN` at `2c1042485b0980cfc33a2ab315ea0fd8969737a4`; its 15 check runs show 13 successes and 2 skips. The latest Issue comment reports a non-fast-forward push failure. The issue still requires the raw `rv-1` outputs and an evidence comment before human review; publication permission remains a maintainer decision.
- Issue #400 is open and labeled `agent/failed`; its lease was renewed at `2026-11-19T20:52:44Z` with a 1,200-second TTL. PR #405 is open, non-draft, and `DIRTY` at `e87337f5ca8479632f8433b7d01109b2f859ad68`; its current head has zero GitHub check runs. The latest Issue comment says the rebase repair completed with verification passed; no remote checks are reported. Human confirmation of #398 remains pending.

## PR #409 metadata refresh

The patrol Issue requires the title and body to be refreshed from the current branch history and verification after every fix cycle. This repair used:

- Title: `docs(issue-406): refresh current review handoff` (Conventional Commits subject, under 72 characters).
- First body line: plain `Closes #406`.
- Body: a timestamped snapshot of the current #366, #400, #406 and PR states, verification results, remaining human decisions, and all branch changes in commit-subject order. It contains no copied commit SHAs; the PR header is the authoritative head reference. The commit-request written for this review cycle uses the same title.
- The latest pushed head before this docs-only repair is recorded above as `b148cd5`; the runner will create this review's additional commit. The PR description lists that change by subject and does not invent a SHA before the runner assigns one.
- `gh pr edit 409 --title 'docs(issue-406): refresh current review handoff' --body-file .agent-runner/pr-409-body.md` completed successfully. `gh pr view 409 --json title,body,state,isDraft,headRefOid,mergeStateStatus,closingIssuesReferences` read the saved title and full body back: the first line is plain `Closes #406`, the PR is open draft and `CLEAN`, and `closingIssuesReferences` contains #406. The PR header still points to the last pushed head because the runner has not yet created this repair's commit.

The four commits already on PR #409 at the snapshot are:

1. `51ce2be` — `docs(issue-406): record #366 and #400 handoff` records the original patrol outcome.
2. `7d6a701` — `test(issue-406): widen active-output test timeout` raises the subprocess wall timeout from 20 to 60 seconds while preserving the 3-second inactivity timeout and captured-output assertion. This changes test allowance, not product behavior.
3. `4c4c734` — `docs(issue-406): refresh handoff review evidence` records the prior review snapshot and verification.
4. `b148cd5` — `docs(issue-406): align handoff with current PR state` records the last pushed handoff state.

## Verification and source evidence

- `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" uv run pytest tests/test_process_runner.py::test_captured_output_includes_stdout_written_when_process_exits -q` — passed, 1 test in 20.84s. This was run against the test-fix source; subsequent commits before this repair only changed documentation.
- `just test all` — passed at the test-fix commit: 5,520 passed, 1 skipped in 412.90s. This is historical full-suite evidence, not a claim that the full suite was rerun for this documentation-only repair.
- Previous all-file hooks: `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" SKIP=check-test-flag uv run pre-commit run --all-files --show-diff-on-failure` — passed; only `check-test-flag` was skipped.
- Current documentation refresh checks: `git diff --check` — passed; `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" uv run python hooks/shared/check_guidelines_consistency.py` — passed; `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" uv run mkdocs build --strict` — passed (exit 0; MkDocs emitted informational notices for unlisted pages and optional signature formatting).
- `.agent-runner/commit-request.json` parses with `UV_CACHE_DIR="$PWD/.agent-runner/uv-cache" uv run python -m json.tool .agent-runner/commit-request.json`. The first `python` attempt failed because no `python` executable is installed; the first `uv` attempt used a cache outside the writable worktree and was denied. The worktree-local cache command above passed.
- Current PR #409 check-run source: `GET /repos/ZataZhang/keda/commits/b148cd5a5f5ebfd275c6f1d52da82379029f9114/check-runs`; PR #404 check runs were read from its current head. PR #405 returned zero check runs after its rebase repair.
- Issue state, labels, latest lease markers, and latest comments were read from the GitHub Issues API for #366, #400, and #406. PR state, title, head, base, and merge state were read from the Pull Requests API for #404, #405, and #409.
- No independent PRD verifier applies: Issue #406 has no PRD. The remaining human decisions belong to #366 and #400 and are outside this patrol PR's acceptance.

## Final-tree evidence

- Source head before the runner creates this repair commit: `b148cd5a5f5ebfd275c6f1d52da82379029f9114`.
- The committed PR history at the snapshot is the four-entry list above. This repair changes the handoff checklist, refreshes PR #409 metadata, and writes `.agent-runner/commit-request.json`; it does not change product code.
- The only tracked working-tree change is `tasks/evidence/issue-406/human-review-checklist.md`. `.agent-runner/commit-request.json` is present and valid for the runner; the temporary PR body file was removed after the successful PR update. `git status --short`, `git diff --name-only`, and `git rev-parse HEAD` were checked after editing. No Git staging, commit, reset, checkout, merge, or push command is part of this repair.

## Finding disposition

- Fixed: refreshed the checklist to identify the latest pushed PR #409 head and all four existing commits, refreshed the PR title/body with the current issue/PR snapshot and verification, and confirmed the saved body begins with `Closes #406` and retains the closing reference. The PR body lists changes by subject so this review's runner-created commit is represented without a guessed SHA.
- No finding is disputed.

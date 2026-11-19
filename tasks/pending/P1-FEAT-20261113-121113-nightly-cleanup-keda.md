
# Nightly cleanup (CI / duplicated code / docs / dependencies) — 2026-11-13

- GitHub Issue: https://github.com/ZataZhang/keda/issues/366

> Fires at 02:00 every night for the keda repository. This PRD body gives the
> priority order and decision guidance for four scopes (CI failure / duplicated
> code merge / docs sync / dependency upgrades) as a triage tree. The agent picks
> the single highest-ROI scope for the night; if all four are not actionable, it
> closes the Issue with a comment and does not open a PR.

- Loop id: `nightly-cleanup-keda`
- Target repository: `keda`
- Trigger date: `2026-11-13`
- Last CI status: `success`
- Outdated dependency count: `78`

## 1. CI failure

- **Trigger**: `last_ci_status` parses to `failure` / `timed_out` / `cancelled` / `action_required` — any value other than `success` / `none`.
- **Steps**:
  1. Pull the failing log with `gh run view <id> --log-failed`;
  2. Reproduce the failure inside the worktree (`uv run pytest <path> -v`);
  3. Fix the code / tests / configuration, then get `just test` fully green;
  4. Open a draft PR with the `cleanup(ci):` title prefix.
- **Not actionable when**: `last_ci_status` is `success` / `none`, or one fix attempt still fails regression tests for reasons unrelated to the failure.

## 2. Duplicated code

- **Trigger**: a similarity scan of helper-style functions under `src/backend/` (`pylint --disable=all --enable=duplicate-code` or `jscpd`) finds ≥ 2 duplicated blocks / near-identical functions.
- **Steps**:
  1. Pick the highest-ROI duplicate (prefer many call sites plus recent maintainer churn);
  2. Extract a shared helper / merge into a single function and replace every call site;
  3. Run `just test` to confirm no regression;
  4. Open a draft PR with the `cleanup(refactor):` title prefix.
- **Not actionable when**: the scan is empty, or every duplicate has low ROI (< 5 lines / 1–2 call sites).

## 3. Docs sync

The goal is filling gaps: feature changes must still update docs, so the night only follows recent diffs to check affected explanations and does not rewrite the whole docs tree.
A passing build only proves the build layer works; it does not prove the described behaviour matches the code.

### Scope and progress

1. At the start of this round, record the full commit SHA of the target base branch in this Issue's comment as the fixed upper bound `head_sha`.
   Read the doc-check record of the loop's most recently closed Issue to find the latest valid `checked_through_sha`;
   it must be an ancestor of `head_sha`. Do not treat closing the Issue or merging the PR as completing the doc check.
2. With a valid checkpoint, check forward from it in the base branch's first-parent order up to the fixed upper bound;
   use commit diffs that include merge results (relative to the first parent) so changes brought in by merge commits are not missed.
   On the first run with no checkpoint, use the last 7 days as the initial range and disclose that older history is not covered.
   If the record cannot be read or the checkpoint is no longer an ancestor, mark the range as unconfirmed instead of silently resetting progress as a first run.
3. Check at most the earliest 20 pending commits per round. Inspect each diff, then follow the changed symbols, entry points, config keys and direct callers to the relevant docs; a large commit may be checked feature by feature, but the checkpoint must not advance past it before it is finished.
   Do not assume the docs are in sync just because code and docs landed in the same commit, and do not decide no update is needed just because no doc shares the name.
4. Advance `checked_through_sha` only to the last consecutively fully checked commit; record the remaining range and continue next round.
   When findings exist but are not fixed yet, you may record the checked progress, but must carry the unresolved items and their Issues/PRs, and verify first next round whether they were fixed or are tracked by an open task, so problems are neither lost nor fixed twice in duplicate PRs.

### Checking and fixing

- **Trigger**: a source-backed behavioural omission or contradiction is found — for example entry points, parameters and defaults, config sources, state transitions, persistence, request scope, cache and refresh cadence, permissions or failure handling are described incorrectly; or the related docs have dead paths, symbols, links, or a formal page missing from navigation.
- **Steps**:
  1. Trace the current implementation from the production entry point. Maintain existing guides or feature maps the way feature-docs does; when the skill is unavailable, still follow this section and never treat historical PRDs, old docs or test doubles as implementation facts.
  2. For each finding, write "commit/changed symbol → doc location → current description or gap → implementation evidence → impact", prioritising issues that mislead usage, operations or code changes; do not manufacture edits for wording preference.
  3. Fix the authoritative docs and references; update `mkdocs.yml` only when a formal page is added, moved, or genuinely missing from navigation.
     Do not change production code to match old docs, and do not expand into unrelated doc tidying.
  4. Run `git diff --check` and `uv run mkdocs build --strict`, then re-read the changed behaviour descriptions against the source; a docs-only change does not claim product-behaviour verification. Open a draft PR with the `cleanup(docs):` title prefix.
- **Not actionable when**: the declared range has been behaviourally checked, no doc difference holds that is not already tracked by another task, and the applicable link, navigation and build checks are done. A passing build alone, a skipped check or unavailable dependencies do not mean there is no difference.

### Per-round record

Whether or not anything is found, leave a doc-check record on this Issue: `head_sha`, the starting point and first-run coverage limits,
`checked_through_sha` (not advanced when unchecked), commits checked and related docs, findings with source evidence, unresolved items and
tracking links, remaining range, actual verification results, and time spent. If no doc applies, still summarise the basis for that judgement.
Distinguish "checked, no difference", "found, pending fix", "partially checked" and "not executed"; never fabricate coverage.

## 4. Dependency upgrades

- **Trigger**: `outdated_count` > 5 (inclusive), or minor (patch) versions in 0.0 ≤ X < 1.0 accumulate to ≥ 10.
- **Steps**:
  1. Upgrade the whole lockfile with `uv lock --upgrade`, or hand-pick a few high-ROI direct dependencies (heavily imported here, tight security advisories);
  2. Run `just test` to confirm compatibility;
  3. On failure, narrow to a safe range (patch only) and retry;
  4. Open a draft PR with the `cleanup(deps):` title prefix.
- **Not actionable when**: the `outdated_count` threshold is not met; or the last upgrade was within 7 days (avoid nightly PR churn).

## Triage priority

- **Only one scope per night**, priority: `CI > refactor > docs > deps`.
- **Decision flow**:
  1. Judge each trigger above in order (CI via `last_ci_status` → refactor via the scan → docs via the recent-diff behaviour check and build → deps via `outdated_count`).
  2. The highest-priority hit is the night's target; skip the lower ones. When docs is skipped, still record "not executed" and why,
     do not advance the doc checkpoint, and keep accumulating the range next round instead of dropping backlog by a date window.
  3. No hit in any of the four → `gh issue close <N> --comment "no actionable cleanup tonight"` and **do not** open a PR.
- **Scope label**: once a scope is chosen, run inside the worktree:
  ```bash
  gh issue edit <N> --add-label scope/<x>
  ```
  where `x ∈ {ci, refactor, docs, deps}`. The four `scope/<x>` labels must first be created in the repository with
  `kc labels sync` (see `docs/guides/loop.md`).

## Delivery Notes

- Recommended branch: `task/<issue-number>-prd-nightly-cleanup-keda-2026-11-13`
- Worktree command: `iar worktree create --branch issue-<issue-number> --base-branch main`
- PR should include: `Closes #<issue-number>`
- Draft PR title prefix: `cleanup(<scope>): <one-line description>`

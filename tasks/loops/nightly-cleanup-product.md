---
id: nightly-cleanup-product
schedule: "30 2 * * *"
repo_id: "<product-repo-id>"
priority: P1
issue_type: feature
agent: auto
labels:
  - loop/cleanup
publish_prd: true
queue_ready: true
run_now: true
timezone: Asia/Shanghai
slug: nightly-cleanup-product
# pre_command uses a single-line shell command; the simple-YAML frontmatter parser
# does not support `|` block scalars. Use `;` to chain so all KEY=value lines
# land on one logical line in stdout.
pre_command: "gh run list --limit 1 --json conclusion -q '.[0].conclusion // \"none\"' | awk '{ print \"last_ci_status=\" $0 }'; if [ -f uv.lock ]; then uv pip list --outdated --format json 2>/dev/null | jq 'length' | awk '{ print \"outdated_count=\" $0 }'; elif [ -f package-lock.json ]; then npm outdated --json 2>/dev/null | jq 'keys | length' | awk '{ print \"outdated_count=\" $0 }'; elif [ -f pnpm-lock.yaml ]; then pnpm outdated --format json 2>/dev/null | jq 'keys | length' | awk '{ print \"outdated_count=\" $0 }'; else echo outdated_count=0; fi"
---

# Nightly cleanup (CI / duplicated code / docs / dependencies) — {{date}}

> Fires at 02:30 every night for the product repository (30 minutes apart from the
> keda loop, to avoid GitHub API quota spikes and `~/.iar/loop-state.json` upsert
> contention). This PRD body gives the priority order and decision guidance for
> four scopes (CI failure / duplicated code merge / docs sync / dependency
> upgrades) as a triage tree. The agent picks the single highest-ROI scope for the
> night; if all four are not actionable, it closes the Issue with a comment and
> does not open a PR.

- Loop id: `{{loop_id}}`
- Target repository: `{{repo_id}}`
- Trigger date: `{{date}}`
- Last CI status: `{{last_ci_status}}`
- Outdated dependency count: `{{outdated_count}}`

> **First-run reminder**: replace `repo_id: "<product-repo-id>"` in the frontmatter
> with the actual product repository id from `config.toml`. This recipe reuses the
> same four-scope triage tree as the keda repository, but `pre_command` and the
> docs/dependency commands are adapted to the common product tech stacks
> (one of uv / npm / pnpm).

## 1. CI failure

- **Trigger**: `last_ci_status` parses to `failure` / `timed_out` / `cancelled` / `action_required` — any value other than `success` / `none`.
- **Steps**:
  1. Pull the failing log with `gh run view <id> --log-failed`;
  2. Reproduce the failure inside the worktree (by stack: `pnpm test` / `npm test` / `uv run pytest`);
  3. Fix the code / tests / configuration, then get `just test` (or the product repo's equivalent) fully green;
  4. Open a draft PR with the `cleanup(ci):` title prefix.
- **Not actionable when**: `last_ci_status` is `success` / `none`, or one fix attempt still fails regression tests for reasons unrelated to the failure.

## 2. Duplicated code

- **Trigger**: a similarity scan of the front-end / back-end source (`jscpd` or the linter's built-in duplicate-code check) finds ≥ 2 duplicated blocks / near-identical functions / near-identical components.
- **Steps**:
  1. Pick the highest-ROI duplicate (prefer many call sites plus recent maintainer churn);
  2. Extract a shared helper / component / composable and replace every call site;
  3. Run the full tests / lint to confirm no regression;
  4. Open a draft PR with the `cleanup(refactor):` title prefix.
- **Not actionable when**: the scan is empty, or every duplicate has low ROI (< 5 lines / 1–2 call sites).

## 3. Docs sync

- **Trigger**: `docs/` and `mkdocs.yml` (or README) reference import paths / class names / modules / routes that do not exist in the source; newly added `docs/*.md` entries are missing from `mkdocs.yml`.
- **Steps**:
  1. Run the docs build (`pnpm docs:build` / `npm run docs:build` / `uv run mkdocs build --strict`) and read the broken link / missing file report;
  2. Fix `docs/guides/*.md` and bring `mkdocs.yml` back in sync;
  3. Run the build again to confirm there are no warnings;
  4. Open a draft PR with the `cleanup(docs):` title prefix.
- **Not actionable when**: the build is fully green with no missing / broken report.

## 4. Dependency upgrades

- **Trigger**: `outdated_count` > 5 (inclusive), or minor (patch) versions in 0.0 ≤ X < 1.0 accumulate to ≥ 10.
- **Steps**:
  1. Upgrade by stack lockfile:
     - uv: `uv lock --upgrade`
     - pnpm: `pnpm update --latest`
     - npm: `npm update`
  2. Run the full tests / lint to confirm compatibility;
  3. On failure, narrow to a safe range (patch only) and retry;
  4. Open a draft PR with the `cleanup(deps):` title prefix.
- **Not actionable when**: the `outdated_count` threshold is not met; or the last upgrade was within 7 days (avoid nightly PR churn).

## Triage priority

- **Only one scope per night**, priority: `CI > refactor > docs > deps`.
- **Decision flow**:
  1. Judge each trigger above in order (CI via `last_ci_status` → refactor via the scan → docs via the docs build → deps via `outdated_count`).
  2. The highest-priority hit is the night's target; skip the lower ones.
  3. No hit in any of the four → `gh issue close <N> --comment "no actionable cleanup tonight"` and **do not** open a PR.
- **Scope label**: once a scope is chosen, run inside the worktree:
  ```bash
  gh issue edit <N> --add-label scope/<x>
  ```
  where `x ∈ {ci, refactor, docs, deps}`. The four `scope/<x>` labels must first be created in the repository with
  `kc labels sync` (see `docs/guides/loop.md`).

## Delivery Notes

- Recommended branch: `task/<issue-number>-prd-nightly-cleanup-product-{{date}}`
- Worktree command: `iar worktree create --branch issue-<issue-number> --base-branch main`
- PR should include: `Closes #<issue-number>`
- Draft PR title prefix: `cleanup(<scope>): <one-line description>`

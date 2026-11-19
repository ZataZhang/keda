---
id: nightly-main-ci
schedule: "0 1 * * *"
repo_id: keda
managed_by: agent-daemon
issue_type: bug
agent: auto
labels:
  - scope/ci
output: issue
queue_ready: true
run_now: false
timezone: Asia/Shanghai
priority: P1
slug: nightly-main-ci
skip_when: last_ci_status=success,none
pre_command: 'status="$(gh run list --branch main --status completed --limit 1 --json conclusion -q ".[] | .conclusion" 2>/dev/null)" && if [ -n "$status" ]; then printf "last_ci_status=%s\n" "$status"; else printf "last_ci_status=none\n"; fi || printf "last_ci_status=unavailable\n"'
---

# Main CI nightly check — {{date}}

This is the daily main CI inspection task for the `keda` repository. The most recent main
workflow concluded with: `{{last_ci_status}}`.

## Goal

Confirm whether the latest GitHub Actions workflow on main failed; if it did, find the root
cause and open a minimal fix PR. If there is nothing actionable, do not manufacture a PR just
to produce a change.

## Rules

1. Start with `gh run list --branch main --limit 5`, then read the failing job's logs with
   `gh run view <run-id> --log-failed`.
2. For `failure`, `timed_out`, `cancelled` or `action_required`, reproduce the real failing
   entry point, fix the source / tests / configuration, and run the verification that matches
   the change; this repository prefers `just test` for day-to-day verification, with the
   corresponding lint or docs build as needed.
3. The known hard gate is a single Python file exceeding 1000 non-blank lines; if it still
   trips, split the offending file rather than adding it to an allowlist.
4. When the latest main run is `success` or `none`, the scheduler already skips this task via
   `skip_when` and no task is created; seeing this Issue means investigation is needed
   (including `unavailable`: the CI status could not be read). Do not widen the scope into
   refactors or dependency upgrades.

## Done when

- There is a failure: fix it, verify it, and open a draft PR that references this Issue and the
  failing run.
- Re-checking shows the latest run has already recovered: leave the check result on the Issue
  and close it, without opening a PR.
- External GitHub permissions or workflow state cannot be confirmed: keep the Issue blocked and
  state the human action needed; do not guess at a conclusion.

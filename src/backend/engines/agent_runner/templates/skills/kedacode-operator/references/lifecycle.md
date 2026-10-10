# 查生命周期 — inspect Issue lifecycle history (read-only)

## When to use

Use this route when the user asks which lifecycle phase an Issue is in, which Issues have reached a lifecycle milestone, or asks for one Issue's ordered lifecycle events. This is different from `kc issue list` (GitHub Issue / linked PR state) and `kc logs --issue` (raw live Agent output).

## Query

- `kc lifecycle` — list every Issue lifecycle currently recorded in the local console ledger.
- `kc lifecycle --reached validation_passed` — filter by a milestone event in history. The Issue remains in the result if it later moved into review, blocked, failed, or completed.
- `kc lifecycle --phase reviewing` — filter by the exact current phase.
- `kc lifecycle --repo-id <id> --issue <N>` — show one Issue's current phase and ordered lifecycle timeline.
- Add `--json` for structured output; use `kc lifecycle --help` or `kc schema --json` for the accepted event and phase values.

## Boundaries

- Read-only: this command never claims, starts, resumes, or re-queues an Issue.
- The source is the local SQLite lifecycle ledger configured by `history_db_path`; it does not query GitHub or aggregate another machine's ledger.
- An empty result means this local ledger has no matching lifecycle record. It does not prove that an Issue never ran elsewhere or predates lifecycle tracking.
- `--reached` checks whether the event was recorded, not whether validation succeeded. For a passing gate use `validation_passed`; `validation_failed` is a distinct milestone.
- `history_complete=false` means the observation ledger has a known gap. Preserve that warning when reporting the current phase.

## Recovery

If the target is missing from the ledger, use `kc issue list --state all` to confirm the repository and Issue, then use `kc logs --repo-id <id> --issue <N>` for available per-Issue output. Do not infer lifecycle state from an open or Draft PR alone.

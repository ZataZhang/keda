# Verification Plan · Issue #314

## Acceptance scope

1. Two isolated KedaCode homes share presence through one repository board; `kc peers` and `--json` identify both machines and B's active Issue.
2. A declared remote lease stays live before expiry, returns to the queue after expiry, and an old claim without a lease remains untouched. The baseline claim parser continues to read the unchanged claim marker.
3. A holder stops its agent at half-TTL after renewal failure, and ownership reads before push and PR creation stop delivery after takeover.
4. Presence board creation converges under duplicate and concurrent starts; presence-off suppresses board writes while lease renewal continues; identical hostnames remain distinct by machine ID.

## Realistic validation

Run each oracle through real `kc` CLI and daemon processes, local Git remotes, and deterministic agent subprocesses. Replace only `gh` and the agent CLI with isolated, no-credential fixtures. For each item, run its negative control first and observe the acceptance assertion turn red; remove the injected failure and observe the same real entry point turn green. Save only that item's output to its `rv-<n>-*.txt` file immediately after success.

| Item | Real entry point | Negative control | Green oracle | Evidence |
|---|---|---|---|---|
| rv-1 | `kc daemon`, `kc peers`, `kc peers --json` | Hide B's status comment; require two machines | Two online machines; B reports #42 and codex; one board has two comments | `rv-1-kc-peers.txt` |
| rv-2 | `kc run --issue 42`, `kc daemon`, fresh `gh` reads | Return a fresh server timestamp for an already expired lease | 570s does not recover; 630s produces one `remote_lease_expired` and `agent/ready`; no-lease #43 remains running | `rv-2-remote-lease.txt` |
| rv-3 | `kc run --issue 42`, agent subprocess registry, push and PR publication | Hide the competing claim so the unprotected run pushes and attempts PR creation | Lost ownership blocks push and pre-PR creation; failed renewal kills the agent at half of a 120s TTL and does not deliver | `rv-3-self-fence.txt` |
| rv-4 | Concurrent `kc daemon` startup and `kc run` | Reject duplicate-board label removal | Retry/concurrent startup converges to one board; presence-off preserves status while lease renews; same hostname with distinct IDs does not reclaim a live remote lease | `rv-4-presence.txt` |

The executable command, detailed negative-control injection, expected red state, output assertions, explanation, risks, and associated raw file are recorded per item in `evidence.json`. The four final outputs all identify candidate implementation tree `1915539127430f1cb15f1dadd8c9bc955457d801`; this tree excludes the PRD and its delivery-evidence directory.

## Automated validation

- Focused regression suite without testmon filtering: 96 passed across the seven claim, heartbeat, presence, reconciliation, admission, arbitration, and reclaim modules.
- `SKIP=check-test-flag just lint --full`: passed, including Ruff, architecture, docs-guideline consistency, and file-length checks.
- `just lint --reuse`: passed duplicate-code and reuse gates.
- `uv run mkdocs build --strict`: passed. MkDocs printed existing unnav-page and docstring formatter notices.
- `just test`: testmon reported no tests pending in this worktree; the full focused command above was run without testmon and passed.
- PRD archive-readiness checker: passed after all executor checklist items were resolved and the banner/Final Reconciliation were synchronized.

## Handoff gates and limits

The independent verifier is a runner-owned gate and has not been represented as passed by the executor. The interactive human-review HTML is copied from the PRD skill template, its fixed script matches the template, and its three decision cards align with the Markdown checklist. Static parsing confirms four cards, three radio groups, three textareas, and the result card. The warning-level skill checker reports a false mismatch because its raw `<h2>` regex sees an `<h2>` example inside the unchanged template comment and consumes the first real heading. The CUA environment reported no browser sessions and denied access to installed Edge and Chrome-for-Testing, so zero browser `pageerror`, first-card visibility, pagination, and final Markdown generation remain `REVIEW_INCIDENT / INCONCLUSIVE` for the runner to retry in an enabled browser environment.
